"""Every gate flag must answer the same question in every output channel.

THE DEFECT SHAPE THIS FILE EXISTS TO CATCH
------------------------------------------
A gated command decides its exit code once per OUTPUT CHANNEL, by hand::

    if json_mode:
        click.echo(to_json(envelope))
        if fail_on_found and (violations or scan_incomplete):   # copy 1
            raise GateFailureError(verdict)
        return
    ...
    if fail_on_found and violations:                            # copy 2
        raise GateFailureError(verdict)

The two copies are held equal by author discipline and nothing else. In
``roam ignore-drift`` copy 2 lost the ``or scan_incomplete`` term, so a repo
where git could not answer printed

    VERDICT: UNANALYZABLE: ... This is NOT a clean result.
      Nothing was measured, so nothing is proven clean.

and then exited 0. Both of this repo's wired callers of that gate
(``.github/workflows/secret-scan.yml`` and ``scripts/prepush_check.py``) read
the TEXT channel, and the only test that asserted the gate refuses on
UNANALYZABLE invoked the ``--json`` channel -- so the assertion lived on the
one channel nothing in production uses, and the suite was green.

TWO LAWS, BOTH DRIVEN OFF THE REGISTRY
--------------------------------------
1. CHANNEL PARITY. For identical argv, ``roam <cmd> <gate-flag>`` and
   ``roam --json <cmd> <gate-flag>`` (and ``--sarif`` where supported) must
   return the SAME exit code. A gate whose answer depends on how you asked
   for the answer is not a gate.

2. AN UNMEASURED RESULT NEVER AUTHORIZES. When a command's own envelope
   reports ``scan_incomplete``, the command is saying the analysis had no
   input. A gate flag must then refuse. Sharing exit 5 with a measured
   VIOLATION is correct; sharing exit 0 with a measured CLEAN is the defect.

   The signal is ``scan_incomplete`` and deliberately NOT ``partial_success``.
   Measured 2026-08-08: ``partial_success`` is overloaded. ``boundary`` sets
   it for a clean run that was legitimately scoped to a changed range, and
   ``taint`` sets it on a fully populated corpus with 638 findings. Both of
   those ARE measurements of what the caller asked for, and gating on them
   would refuse every scoped run. ``scan_incomplete`` is the narrow claim --
   "the analysis had no input at all" -- and it is the only one that must
   deny.

Both laws are enumerated from ``roam.cli._COMMANDS`` at collection time, so a
NEWLY ADDED gate command is covered without touching this file. A
hand-maintained list would drift, and this repo has shipped that failure
before.

WHAT THIS FILE DOES **NOT** PROVE
---------------------------------
* It drives three ENVIRONMENT classes (measurable-and-clean,
  measurable-with-a-real-finding, unmeasurable), not three per-command
  verdicts. For a given command the seeded repo may not reach VIOLATION --
  parity is still asserted, but the failing branch of that particular gate is
  then unexercised. Reaching VIOLATION for all 37 gate commands needs a
  per-command fixture and is not attempted here.
* It enumerates BOOLEAN gate flags only. Value-bearing thresholds
  (``--fail-under N``, ``--min-coverage N``) are not probed, so the gates that
  only fail via a threshold are covered for parity at their default value and
  nowhere else.
* Commands the registry marks ``destructive`` are excluded (they would mutate
  the fixture); see ``_DESTRUCTIVE_EXCLUSIONS``, which the meta-test pins so
  the exclusion set cannot grow silently.
* Plugin-registered commands live in ``_PLUGIN_COMMANDS``, not ``_COMMANDS``,
  and are not covered.
* Law 2 can only hold a command to a claim it actually publishes. A command
  that is blind to its own degradation -- that never sets ``scan_incomplete``
  no matter what it failed to read -- passes vacuously. Measured 2026-08-08:
  ``pr-analyze --gate`` reports ``state: "no_changes"`` with no
  ``scan_incomplete``, and it is deliberately NOT changed here, because "there
  is no diff to gate" is a real answer rather than an absent measurement.
  Deciding which of the remaining gated commands are blind rather than clean
  is a separate audit.
"""

from __future__ import annotations

import importlib
import json
import os
import re
import subprocess
import sys
from pathlib import Path

import click
import pytest

from roam.cli import _COMMANDS, _SARIF_CONSUMERS

# ---------------------------------------------------------------------------
# Registry-driven discovery of the gate surface
# ---------------------------------------------------------------------------

#: A long option is gate-shaped when its stem carries one of these words as a
#: whole hyphen-delimited part (``--fail-on-found``, ``--ci``, ``--gate``,
#: ``--strict``, ``--require-coverage``), OR when its help text documents a
#: non-zero exit. The second arm is what catches a gate named something this
#: vocabulary never anticipated -- the vocabulary is a convenience, the
#: documented exit is the contract.
_GATE_STEM = re.compile(r"(^|-)(fail|strict|gate|ci|enforce|requires?d?|check)($|-)", re.I)
_GATE_HELP = re.compile(r"exit\s+(with\s+)?(code\s+)?(non-?zero|[1-9])", re.I)

#: Excluded because running them would mutate or delete the fixture the other
#: cases depend on. Their channel parity is UNKNOWN, not clean. Pinned by
#: ``test_exclusions_are_exactly_the_declared_destructive_commands`` so that a
#: future command cannot drop out of coverage by quietly acquiring the flag.
_DESTRUCTIVE_EXCLUSIONS: frozenset[str] = frozenset({"reset", "stale-refs"})

#: A subprocess that never returns is an ABSENT measurement, and an absent
#: measurement is UNKNOWN. The timeout is generous enough that hitting it is
#: a finding, and the test reports it as a failure rather than a skip.
_TIMEOUT_SECONDS = 240


def _gate_surface() -> dict[str, list[str]]:
    """Map ``command name -> sorted gate-shaped boolean long options``."""
    surface: dict[str, list[str]] = {}
    for name, (module_path, attr) in sorted(_COMMANDS.items()):
        command = getattr(importlib.import_module(module_path), attr)
        flags = set()
        for param in getattr(command, "params", []):
            if not isinstance(param, click.Option) or not param.is_flag:
                continue
            longs = [opt for opt in param.opts if opt.startswith("--")]
            if not longs:
                continue
            longest = max(longs, key=len)
            if _GATE_STEM.search(longest[2:]) or _GATE_HELP.search(param.help or ""):
                flags.add(longest)
        if flags:
            surface[name] = sorted(flags)
    return surface


def _is_destructive(name: str) -> bool:
    module_path, attr = _COMMANDS[name]
    command = getattr(importlib.import_module(module_path), attr)
    capability = getattr(command, "__roam_capability__", None)
    return bool(getattr(capability, "destructive", False))


GATE_SURFACE: dict[str, list[str]] = _gate_surface()
EXCLUDED: frozenset[str] = frozenset(n for n in GATE_SURFACE if _is_destructive(n))
PROBED: dict[str, list[str]] = {n: f for n, f in GATE_SURFACE.items() if n not in EXCLUDED}

#: One case per (command, flag). Scenario is a separate parametrize axis so a
#: failure names the environment that produced it.
CASES: list[tuple[str, str]] = [(name, flag) for name, flags in PROBED.items() for flag in flags]

SCENARIOS: tuple[str, ...] = ("clean", "violation", "unanalyzable")


# ---------------------------------------------------------------------------
# Fixture workspaces -- three environment classes
# ---------------------------------------------------------------------------


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(
        ["git", *args],
        cwd=cwd,
        check=True,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
    )


def _seed_sources(root: Path) -> None:
    (root / "src").mkdir(parents=True, exist_ok=True)
    (root / "src" / "a.py").write_text("def alpha(x):\n    return x + 1\n", encoding="utf-8")
    (root / "src" / "b.py").write_text(
        "from src.a import alpha\n\n\ndef beta(y):\n    return alpha(y) * 2\n", encoding="utf-8"
    )
    (root / "test_beta.py").write_text(
        "from src.b import beta\n\n\ndef test_beta():\n    assert beta(1) == 4\n", encoding="utf-8"
    )
    (root / "README.md").write_text("# Fixture\n\nSee [a](src/a.py).\n", encoding="utf-8")
    (root / ".gitignore").write_text("build/\n*.log\n", encoding="utf-8")


def _build_repo(root: Path) -> None:
    root.mkdir(parents=True, exist_ok=True)
    _git(root, "init", "-q", ".")
    _git(root, "config", "user.email", "fixture@example.invalid")
    _git(root, "config", "user.name", "fixture")
    _seed_sources(root)
    _git(root, "add", ".gitignore", "src", "test_beta.py", "README.md")
    _git(root, "commit", "-q", "-m", "fixture")


def _roam_init(root: Path) -> None:
    subprocess.run(
        [sys.executable, "-m", "roam", "init"],
        cwd=root,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=_TIMEOUT_SECONDS,
    )


@pytest.fixture(scope="session")
def workspaces(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Path]:
    """Three environment classes, built once per xdist worker.

    ``tmp_path_factory`` is worker-unique, which is load-bearing: roam
    auto-indexes, and two channels racing on ONE directory produce
    "Another indexing process owns the workspace" and a FALSE divergence.
    """
    base = tmp_path_factory.mktemp("gate-parity")

    clean = base / "clean"
    _build_repo(clean)
    _roam_init(clean)

    violation = base / "violation"
    _build_repo(violation)
    # A tracked-but-ignored file: the one violation seed that needs no secret
    # material, no malformed source and no network. `.gitignore` says `*.log`;
    # git tracks it anyway, which is exactly the defect `ignore-drift` reports.
    (violation / "leak.log").write_text("tracked despite an ignore rule\n", encoding="utf-8")
    _git(violation, "add", "-f", "leak.log")
    # A file that does not parse, so the syntax/quality gates have a finding.
    (violation / "src" / "broken.py").write_text("def broken(:\n", encoding="utf-8")
    _git(violation, "add", "-f", "src/broken.py")
    _git(violation, "commit", "-q", "-m", "seed violations")
    _roam_init(violation)

    # Not a git worktree, no roam index: git answers `fatal: not a git
    # repository` and rc 128, so any check that needs git cannot run. This is
    # the shape a pre-push gate meets on a degraded machine, and the shape the
    # seed defect passed as clean.
    unanalyzable = base / "unanalyzable"
    unanalyzable.mkdir()
    (unanalyzable / "notes.txt").write_text("no git, no index\n", encoding="utf-8")

    return {"clean": clean, "violation": violation, "unanalyzable": unanalyzable}


# ---------------------------------------------------------------------------
# Invocation
# ---------------------------------------------------------------------------


class _Run:
    __slots__ = ("argv", "returncode", "stdout", "stderr")

    def __init__(self, argv: list[str], returncode: int, stdout: str, stderr: str) -> None:
        self.argv = argv
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def _invoke(cwd: Path, argv: list[str]) -> _Run:
    """Run roam as a SUBPROCESS and read the exit code directly.

    Subprocess, not ``CliRunner``: the wired callers of these gates are
    subprocesses reading a process exit status, and that is the thing under
    test. ``stdin=DEVNULL`` so a command that reads stdin fails fast instead
    of wedging the suite.
    """
    env = dict(os.environ, PYTHONIOENCODING="utf-8", NO_COLOR="1")
    full = [sys.executable, "-m", "roam", *argv]
    try:
        completed = subprocess.run(
            full,
            cwd=cwd,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=_TIMEOUT_SECONDS,
            env=env,
        )
    except subprocess.TimeoutExpired:
        pytest.fail(
            f"`roam {' '.join(argv)}` did not return within {_TIMEOUT_SECONDS}s in {cwd}. "
            "An absent measurement is UNKNOWN, never a benign default: this is a "
            "failure, not a skip."
        )
    return _Run(full, completed.returncode, completed.stdout, completed.stderr)


def _last_envelope(stdout: str) -> dict | None:
    """Return the JSON object printed on stdout, or None.

    Three shapes, in order, because getting this wrong makes Law 2 skip
    silently rather than fail: a single-line envelope; the whole of stdout
    when it is one pretty-printed object; and a pretty-printed object
    preceded by plain-text lines (a staleness WARNING on stdout is enough to
    defeat the naive form, and a skipped case proves nothing).
    """
    for line in reversed(stdout.splitlines()):
        stripped = line.strip()
        if stripped.startswith("{") and stripped.endswith("}"):
            try:
                parsed = json.loads(stripped)
            except json.JSONDecodeError:
                continue
            if isinstance(parsed, dict):
                return parsed

    stripped = stdout.strip()
    for start in (0, stripped.find("{")):
        if start < 0:
            continue
        try:
            parsed = json.loads(stripped[start:])
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            return parsed
    return None


# ---------------------------------------------------------------------------
# Meta-guards: a discovery bug must not silently empty this file
# ---------------------------------------------------------------------------


def test_gate_discovery_is_not_vacuous() -> None:
    """A regex that stops matching turns every test below into a no-op pass.

    This is the failure mode that makes a structural guard worse than none:
    it keeps reporting green while measuring nothing. Pin the floor and pin
    the one case whose breakage started this file.
    """
    assert len(PROBED) >= 30, f"gate discovery collapsed to {len(PROBED)} commands: {sorted(PROBED)}"
    assert "ignore-drift" in PROBED
    assert "--fail-on-found" in PROBED["ignore-drift"]
    assert "secrets" in PROBED
    assert "--fail-on-found" in PROBED["secrets"]


def test_exclusions_are_exactly_the_declared_destructive_commands() -> None:
    """Coverage may not shrink by accident.

    If a command acquires ``destructive=True`` it drops out of this file's
    coverage. That is a decision, so it has to be written down here.
    """
    assert EXCLUDED == _DESTRUCTIVE_EXCLUSIONS & set(GATE_SURFACE), (
        f"gate commands excluded as destructive changed: {sorted(EXCLUDED)}. "
        f"Declared: {sorted(_DESTRUCTIVE_EXCLUSIONS)}. Update _DESTRUCTIVE_EXCLUSIONS "
        "and say in the docstring what is no longer covered."
    )


# ---------------------------------------------------------------------------
# Law 1 -- channel parity
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("scenario", SCENARIOS)
@pytest.mark.parametrize(("command", "flag"), CASES, ids=[f"{c}{f}" for c, f in CASES])
def test_gate_exit_code_is_the_same_in_every_channel(
    workspaces: dict[str, Path], scenario: str, command: str, flag: str
) -> None:
    """Identical argv, different ``--json``/``--sarif`` prefix, same exit code.

    Pre-fix this failed on ``ignore-drift --fail-on-found`` in the
    ``unanalyzable`` scenario with ``text=0 json=5`` -- the text channel, the
    one both wired callers use, authorizing a push after printing that
    nothing had been measured.
    """
    cwd = workspaces[scenario]
    text = _invoke(cwd, [command, flag])
    js = _invoke(cwd, ["--json", command, flag])

    channels = {"text": text, "--json": js}
    if command in _SARIF_CONSUMERS:
        channels["--sarif"] = _invoke(cwd, ["--sarif", command, flag])

    codes = {label: run.returncode for label, run in channels.items()}
    if len(set(codes.values())) == 1:
        return

    detail = "\n".join(
        f"  {label:8} exit={run.returncode}\n"
        f"    stdout: {run.stdout.strip()[:400]!r}\n"
        f"    stderr: {run.stderr.strip()[:400]!r}"
        for label, run in channels.items()
    )
    pytest.fail(
        f"channel parity broken for `roam {command} {flag}` in the {scenario} workspace: "
        f"{codes}\n"
        "A gate whose answer depends on which output format you asked for is not a gate; "
        "every wired caller in this repo reads the TEXT channel.\n"
        f"{detail}"
    )


# ---------------------------------------------------------------------------
# Law 2 -- an unmeasured result never authorizes
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("scenario", SCENARIOS)
@pytest.mark.parametrize(("command", "flag"), CASES, ids=[f"{c}{f}" for c, f in CASES])
def test_gate_refuses_when_its_own_envelope_reports_an_unmeasured_scan(
    workspaces: dict[str, Path], scenario: str, command: str, flag: str
) -> None:
    """``scan_incomplete: true`` in the envelope forbids exit 0 under a gate flag.

    This is the half a parity test cannot see. ``ignore-drift`` was visible
    only because the ``or scan_incomplete`` correction had been applied to ONE
    channel; where the same error is symmetric, both channels agree and both
    are wrong, and no parity assertion can tell. Measured 2026-08-08 before
    the fix: ``boundary --ci`` ("no imports to analyze"), ``taint --ci``
    ("22 rules loaded but not run") and ``test-hermeticity --ci`` ("no Python
    test files indexed") each printed a verdict naming what they could not
    analyse and exited 0 in BOTH channels.

    The command decides what counts as unmeasured; this test only holds it to
    its own published claim.
    """
    cwd = workspaces[scenario]
    run = _invoke(cwd, ["--json", command, flag])
    envelope = _last_envelope(run.stdout)
    if envelope is None:
        # No envelope means this pair published no completeness signal at all.
        # That is a real coverage hole, recorded in the module docstring, not
        # evidence of health -- but it is not this test's assertion.
        pytest.skip(f"`roam --json {command} {flag}` emitted no parseable envelope")

    summary = envelope.get("summary")
    if not isinstance(summary, dict):
        pytest.skip(f"`roam --json {command} {flag}` envelope carries no summary object")

    if not summary.get("scan_incomplete"):
        return

    assert run.returncode != 0, (
        f"`roam --json {command} {flag}` in the {scenario} workspace published "
        f"scan_incomplete={summary.get('scan_incomplete')!r} and still exited 0. "
        "The command states the analysis had no input and then authorizes on that "
        "non-measurement. An absent measurement is UNKNOWN, never a benign CLEAN.\n"
        f"verdict: {summary.get('verdict')!r}"
    )
