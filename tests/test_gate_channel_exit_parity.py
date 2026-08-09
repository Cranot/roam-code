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

THREE LAWS, ALL DRIVEN OFF THE REGISTRY
---------------------------------------
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

3. A PROBE THAT NEVER RAN IS NOT A PASS. If the harness cannot get argv past
   Click -- a missing positional, a missing required option -- the process
   dies at exit 2 in every channel and Law 1 holds TRIVIALLY while the gate
   under test never executes. Silently counting that as coverage is the same
   shape as the defect: a measurement that did not happen, reported green.
   Those pairs are pinned in ``_ARGV_UNREACHABLE`` with a reason, and the set
   may not grow without an edit here.

4. AUTHORIZING IN AN UNANALYZABLE WORKSPACE IS AN INVENTORIED FACT (W1470).
   Law 2 is only as wide as ``scan_incomplete``. The paragraph below headed
   "Law 2 can only hold a command to a claim it actually publishes" recorded
   the resulting hole and called sorting it out "a separate audit"; the audit
   was then done by hand, one command at a time, across ~48 defects. This law
   is that audit turned into a fixture so it does not have to be redone.

   In the ``unanalyzable`` workspace -- no git, no index, one text file --
   every gate flag that exits 0 must appear in
   ``_AUTHORIZES_IN_AN_UNANALYZABLE_WORKSPACE`` with a CLASS and a reason
   measured on the day it was added. A pair that starts authorizing is a new
   entry and fails; a pair that stops authorizing is a stale entry and fails,
   so the inventory can only shrink. It is not an allowlist: ANSWERED entries
   are closed, DISCLOSED entries are open questions with their evidence
   attached, and the difference is written down rather than implied by
   silence.

   Proved against the pre-fix tree by restoring ``changed_files.py`` and
   ``cmd_adversarial.py`` to ``f059e077^``::

     $ roam adversarial --fail-on-critical     VERDICT: No changes detected   0
     $ roam --json adversarial --fail-on-critical                             0

   -- a new, uninventoried entry, so Law 3 goes red. On the shipped tree the
   same command answers ``diff unavailable: git_error -- cannot gate`` and
   exits 5.

All three are enumerated from ``roam.cli._COMMANDS`` at collection time rather
than from a hand-maintained list, which would drift; this repo has shipped
that failure before.

DISCOVERY IS A HEURISTIC, AND THE EARLIER CLAIM HERE WAS TOO STRONG
-------------------------------------------------------------------
This docstring used to say a newly added gate command is covered "without
touching this file". It is not, and the sentence was refuted by construction:
a synthetic ``--deny-on-issue`` whose help read "Refuse when problems are
found." -- no vocabulary word, no documented exit code -- carrying the exact
text/``--json`` divergence of the seed defect was collected ZERO times. It was
invisible.

What is true is narrower: a flag is discovered when its stem carries a known
gate word OR its help documents a non-zero exit. Measured 2026-08-08 over the
real surface, the arms split stem-only 11, both 32, help-only 2
(``envelope-diff --regression``, ``proof-bundle --validate``), so the
vocabulary carries 43 of 45 and the help arm is a thin backstop, not a
catch-all. A new flag that avoids both drops out silently. The floor in
``test_gate_discovery_is_not_vacuous`` is the ratchet that makes a COLLAPSE
loud; nothing here makes an omission loud, and writing gate help that names
its exit code is the only thing that does.

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
  and are not covered. Neither are the env-gated ``_EXPERIMENTAL_COMMANDS``
  (``roam.cli``): they are off by default, so a gate flag on one of them would
  be as unmeasured here as a plugin's.
* Law 2 reads every ``summary`` object the command printed, not the last JSON
  line. The positional form let a command that publishes ``scan_incomplete``
  and authorizes anyway pass silently, as long as its stdout ENDED with some
  other JSON object; a hint line was enough. Selecting by position is how a
  fail-open hides, so the pairs that publish nothing parseable are pinned in
  ``_NO_SUMMARY_PUBLISHED`` rather than left as a floating skip count.
* Law 2 can only hold a command to a claim it actually publishes. A command
  that is blind to its own degradation -- that never sets ``scan_incomplete``
  no matter what it failed to read -- passes vacuously. Law 3 does not fix
  that; it makes the vacuum COUNTABLE. Measured 2026-08-09 over a pristine
  no-git / no-index directory, 14 of the 39 reachable pairs exit 0: five
  ANSWERED, one NOT-A-GATE, seven DISCLOSED and one BLIND. The seven
  DISCLOSED ones are the audit's result rather than a substitute for it --
  each publishes a private word for "I could not evaluate this"
  (``gate_evaluated: false``, ``coverage_pct_computable: false``,
  ``config_state: "missing"``, ``snapshots: 0``) that Law 2's single
  ``scan_incomplete`` key cannot see. Unifying that vocabulary is the fix;
  the inventory is what stops the list growing while it is pending.

  CORRECTION (W1468). This paragraph used to exempt ``pr-analyze --gate`` on
  the grounds that its ``state: "no_changes"`` was "a real answer rather than
  an absent measurement". That exemption rested on a property the command did
  not have: measured in a repo with a modified tracked file and a corrupt
  ``GIT_INDEX_FILE``, ``pr-analyze --gate`` published exactly the same
  ``state: "no_changes"`` / ``reasons: ["no changes to analyze"]`` /
  ``verdict: "NOCHANGES"`` and exited 0 -- so the state did not distinguish
  the real answer from the failure. ``cmd_diff`` now emits
  ``state: "diff_unavailable"`` with ``git_error`` when git could not answer,
  which routes pr-analyze to ``diff_failed`` and makes ``--gate`` refuse. A
  genuinely empty diff still reports NOCHANGES and still exits 0, which is
  what the original paragraph was reaching for.
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
#: non-zero exit.
#:
#: The second arm is NOT a catch-all, and this comment used to claim it was
#: ("what catches a gate named something this vocabulary never anticipated").
#: It only fires when the help text names an exit code, so a flag that avoids
#: BOTH the vocabulary and the documented exit -- ``--deny-on-issue``, help
#: "Refuse when problems are found." -- is invisible to this file. Measured
#: arm split over the real surface: stem-only 11, both 32, help-only 2.
_GATE_STEM = re.compile(r"(^|-)(fail|strict|gate|ci|enforce|requires?d?|check)($|-)", re.I)
_GATE_HELP = re.compile(r"exit\s+(with\s+)?(code\s+)?(non-?zero|[1-9])", re.I)

#: Excluded because running them would mutate or delete the fixture the other
#: cases depend on. Their channel parity is UNKNOWN, not clean. Pinned by
#: ``test_exclusions_are_exactly_the_declared_destructive_commands`` so that a
#: future command cannot drop out of coverage by quietly acquiring the flag.
#: Measured 2026-08-08: only ``stale-refs`` is actually in ``GATE_SURFACE``, so
#: the effective exclusion is a set of one; ``reset`` is a forward declaration
#: that costs nothing and stops a future gate flag on it from being a surprise.
#: The meta-test intersects with the live surface, so the inert entry cannot
#: mask a command that quietly left coverage.
_DESTRUCTIVE_EXCLUSIONS: frozenset[str] = frozenset({"reset", "stale-refs"})

#: A subprocess that never returns is an ABSENT measurement, and an absent
#: measurement is UNKNOWN. The timeout is generous enough that hitting it is
#: a finding, and the test reports it as a failure rather than a skip.
_TIMEOUT_SECONDS = 240

#: Click's exit code for "I could not parse your argv".
_EXIT_USAGE = 2

#: (command, flag) pairs whose gate body this harness never reaches: Click
#: rejects the argv first, so the process dies at exit 2 in every channel.
#: Parity then holds because every channel is equally unmeasured, which is
#: coverage-shaped and proves nothing. Measured 2026-08-08, identically in all
#: three workspaces; each needs argv this fixture does not synthesise:
#:
#:   dict-consistency --check-consistency  -- positional PATH (a Python file)
#:   envelope-diff    --regression         -- positional A [B] (compile envelopes)
#:   review-verify    --required           -- required --phase and --artifact
#:
#: ``test_every_probed_pair_actually_reaches_its_gate`` fails if this set
#: grows, so a command acquiring a required argument cannot drop out of
#: coverage quietly. Shrinking it is the fix; the entries are UNKNOWN, not
#: clean.
_ARGV_UNREACHABLE: frozenset[tuple[str, str]] = frozenset(
    {
        ("dict-consistency", "--check-consistency"),
        ("envelope-diff", "--regression"),
        ("review-verify", "--required"),
    }
)

#: (command, flag) pairs that run but print no ``summary`` object on stdout
#: under ``--json``. Law 2 can only hold a command to a claim it publishes, so
#: these are skipped -- but pinned, because a floating skip count under CI's
#: ``-q`` is exactly how coverage evaporates unnoticed.
_NO_SUMMARY_PUBLISHED: frozenset[tuple[str, str]] = frozenset(
    {
        ("pr-analyze", "--rules-strict"),
    }
)

# ---------------------------------------------------------------------------
# Law 3 -- the inventory of authorizations granted in an unanalyzable workspace
# ---------------------------------------------------------------------------

#: An entry's CLASS. Not severity: it records WHAT the exit 0 rests on, which
#: is the only thing that separates a closed question from an open one.
#:
#:   ANSWERED  -- the command observed something real and published it. The
#:                observation does not need the workspace (a shipped catalogue,
#:                a version comparison, the absence of a config file), so
#:                "nothing to analyse here" is a complete answer and exit 0 is
#:                earned. Nothing to do.
#:   DISCLOSED -- the command SAYS, in its own envelope, that it could not
#:                evaluate the thing the flag gates on -- and exits 0 anyway.
#:                It is not blind and it is not lying; it is authorizing on a
#:                non-measurement in a vocabulary Law 2 cannot read. OPEN.
#:   BLIND     -- exit 0, a clean verdict, and NOTHING in the envelope that
#:                distinguishes "I looked and it was clean" from "there was
#:                nothing to look at". This is the defect in its pure form.
#:                OPEN, and the worst of the four.
#:   NOT-A-GATE -- discovery matched a flag that has no failing exit path at
#:                all, so its exit 0 carries no information either way. The
#:                finding is in the flag's NAME, not its behaviour. OPEN.
_INVENTORY_CLASSES = frozenset({"ANSWERED", "DISCLOSED", "BLIND", "NOT-A-GATE"})

#: (command, flag) -> (class, reason measured on the date in the reason).
#:
#: SHRINK-ONLY. ``test_no_gate_newly_authorizes_an_unanalyzable_workspace``
#: fails on an addition; ``test_authorization_inventory_has_no_stale_entries``
#: fails on an entry that no longer reproduces, which is the ratchet working
#: and means the entry should be DELETED in the same commit as the fix. Every
#: reason quotes the envelope it was taken from, so an entry cannot degrade
#: into "we agreed not to look".
_AUTHORIZES_IN_AN_UNANALYZABLE_WORKSPACE: dict[tuple[str, str], tuple[str, str]] = {
    ("compatibility", "--ci"): (
        "ANSWERED",
        "2026-08-09: verdict 'no regressions' with covered_dimension_count 7, "
        "uncovered_dimension_count 6, surface_coverage_scope 'covered_dimensions'. "
        "The comparison is against the shipped compatibility matrix, not the "
        "workspace, and the part it does NOT cover is published beside the verdict.",
    ),
    ("config", "--check"): (
        "ANSWERED",
        "2026-08-09: verdict 'no .roam/config.json - using defaults', issues 0. "
        "The absence of a config file IS the observation: there is no document to "
        "validate, and the defaults the command falls back to are its own shipped "
        "values, so no part of this answer depends on the workspace being readable.",
    ),
    ("evidence-oscal", "--strict"): (
        "ANSWERED",
        "2026-08-09: verdict 'emitted OSCAL v1.2 control-mapping with 23 controls "
        "across 9 frameworks', control_count 23, framework_count 9. The document is "
        "projected from roam's own control catalogue; an empty workspace changes "
        "nothing about it, so the emission genuinely succeeded.",
    ),
    ("db-check", "--ci"): (
        "ANSWERED",
        "2026-08-09: verdict 'OK', checks_run 7, checks_complete true, errors 0. roam "
        "auto-indexes, so the command builds an index for the empty corpus and then runs "
        "all 7 integrity checks against the database it built. The subject of db-check is "
        "the DATABASE, not the corpus, and the database it checked really exists.",
    ),
    ("version", "--check"): (
        "NOT-A-GATE",
        "2026-08-09: verdict 'installed: 14.0.0, latest on PyPI: 14.0.0'. cmd_version "
        "contains no ctx.exit and no gate at all -- `--check` means 'also query PyPI', "
        "and _GATE_STEM matches the word 'check'. It cannot return non-zero on any "
        "input, including a failed network call, so its exit 0 proves nothing here.",
    ),
    ("patterns", "--strict-factory"): (
        "BLIND",
        "2026-08-09: verdict 'no patterns detected', total_patterns 0, pattern_types 0, "
        "types_found [], partial_success false -- over an auto-indexed corpus of one text "
        "file with no symbols in it. The summary carries no denominator of any kind, so "
        "this envelope is byte-identical in shape to a real repository with no factories.",
    ),
    ("py-types", "--ci"): (
        "DISCLOSED",
        "2026-08-09: publishes coverage_pct_computable false, state 'no_python_files', "
        "python_files 0, total_public 0 -- and exits 0 under a flag whose help promises "
        "a CI gate. W1320 made the uncomputable state visible in every channel; it did "
        "not decide whether an uncomputable coverage may authorize. It still does.",
    ),
    ("reachability-triage", "--gate-on-new-reachable"): (
        "DISCLOSED",
        "2026-08-09: publishes gate_evaluated false, changed_files null, "
        "reachable_paths 0 -- the envelope states in as many words that the gate was "
        "never evaluated, and the process still exits 0. The signal is present and "
        "correctly named; nothing reads it.",
    ),
    ("rules", "--ci"): (
        "DISCLOSED",
        "2026-08-09: verdict 'no rules directory found', config_state 'missing', "
        "total 0, passed 0, failed 0. Zero rules passing out of zero rules loaded is "
        "not evidence that the repository satisfies its rules -- the same shape the "
        "taint --rule fix closed for a filter that matched nothing.",
    ),
}

#: High-water mark for the inventory, measured 2026-08-09. A RATCHET: the
#: stale-entry test forces it down as gates are fixed, and this pins the raw
#: count so that swapping one entry for another is still a visible edit of a
#: number rather than a silent trade.
#:
#: 14 -> 9 on 2026-08-09. Four of the five deletions are the `--fail-on-anomaly`
#: aliases (digest / snapshot / trend / trends -- four registry names for one
#: callback in cmd_trends.py), closed by the W1524 fix: a gate over a history
#: that does not exist is now UNANALYZABLE and refuses. The fifth,
#: `compatibility --require-coverage`, was already stale when W1524 was written
#: -- e2f55a4c fixed the gate and did not delete its inventory entry, so the
#: ratchet had been failing on it since. Deleted here with the others rather
#: than left red.
_AUTHORIZATION_HIGH_WATER = 9


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


def _json_objects(stdout: str) -> list[dict]:
    """Every top-level JSON object anywhere on stdout, in order.

    Scanned with ``raw_decode`` from each ``{`` rather than parsed positionally,
    because POSITION IS NOT IDENTITY and picking by position is how a fail-open
    hides. The previous form took the last single-line object on stdout; a
    command that printed a pretty envelope carrying ``scan_incomplete: true``
    and then one trailing compact hint line therefore had Law 2 read the hint,
    find no ``scan_incomplete``, and PASS -- a silent authorization, not even a
    skip. Reading every object and holding the command to ANY of them fails
    closed under the same input.
    """
    decoder = json.JSONDecoder()
    found: list[dict] = []
    i = stdout.find("{")
    while i >= 0:
        try:
            obj, end = decoder.raw_decode(stdout, i)
        except ValueError:
            i = stdout.find("{", i + 1)
            continue
        if isinstance(obj, dict):
            found.append(obj)
            i = stdout.find("{", end)
        else:
            i = stdout.find("{", i + 1)
    return found


def _published_summaries(stdout: str) -> list[dict]:
    """Every ``summary`` object the command published on stdout."""
    return [obj["summary"] for obj in _json_objects(stdout) if isinstance(obj.get("summary"), dict)]


def _died_in_argv_parsing(run: _Run) -> bool:
    """True when Click rejected the argv, so the gate body never executed.

    Distinguished from the several commands that legitimately CHOOSE exit 2
    after running (``doctor --strict``, ``guard-pr`` with no bundle on disk,
    ``verdict`` on an unloadable bundle): those reached their own code and
    made a decision, and their parity is a real measurement.
    """
    if run.returncode != _EXIT_USAGE:
        return False
    return run.stderr.lstrip().startswith("Usage:")


# ---------------------------------------------------------------------------
# Meta-guards: a discovery bug must not silently empty this file
# ---------------------------------------------------------------------------


#: High-water mark for discovery, measured 2026-08-08: 36 probed commands /
#: 42 (command, flag) pairs. A RATCHET, not a target -- growth needs no edit,
#: any fall is a discovery failure until proven otherwise. The previous floor
#: was a round 30 against an actual 36, which tolerated a silent 17% collapse:
#: six commands could stop being probed and this file would still report green.
_DISCOVERY_FLOOR_COMMANDS = 36
_DISCOVERY_FLOOR_PAIRS = 42


def test_gate_discovery_is_not_vacuous() -> None:
    """A regex that stops matching turns every test below into a no-op pass.

    This is the failure mode that makes a structural guard worse than none:
    it keeps reporting green while measuring nothing. Pin the floor at the
    measured count and pin the one case whose breakage started this file.
    """
    assert len(PROBED) >= _DISCOVERY_FLOOR_COMMANDS, (
        f"gate discovery fell to {len(PROBED)} commands, below the measured "
        f"high-water mark of {_DISCOVERY_FLOOR_COMMANDS}: {sorted(PROBED)}. "
        "Either _GATE_STEM/_GATE_HELP stopped matching something they used to "
        "match, or a gate command was removed. Say which, then move the mark."
    )
    assert len(CASES) >= _DISCOVERY_FLOOR_PAIRS, (
        f"gate discovery fell to {len(CASES)} (command, flag) pairs, below the "
        f"measured high-water mark of {_DISCOVERY_FLOOR_PAIRS}. A command can "
        "keep its name while losing a flag from coverage; that is the same "
        "collapse one level down."
    )
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
# Law 0 -- a probe that never ran is not a pass
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(("command", "flag"), CASES, ids=[f"{c}{f}" for c, f in CASES])
def test_every_probed_pair_actually_reaches_its_gate(workspaces: dict[str, Path], command: str, flag: str) -> None:
    """Law 1 must not be satisfied by a command that never executed.

    Three of the 42 pairs were reported as covered while dying in Click argv
    parsing -- ``Usage: ... Error: Missing argument 'PATH'`` -- in all three
    workspaces. Every channel exited 2, so parity held, Law 2 skipped, and the
    file reported a green PASS with no marker of any kind: the hard commands
    silently unmeasured, the count still reading covered. That is the defect
    this whole file exists to catch, committed by the file itself.

    ONE workspace, not three, and that is a measurement rather than a saving:
    argv rejection happens in Click before the command body touches the
    filesystem, and the three affected pairs were observed failing identically
    in all three environment classes. Running it once keeps this file's
    subprocess count -- already the reason it is not in the pre-push bundle --
    from growing by half.
    """
    run = _invoke(workspaces["clean"], [command, flag])
    if not _died_in_argv_parsing(run):
        assert (command, flag) not in _ARGV_UNREACHABLE, (
            f"`roam {command} {flag}` now reaches its gate. Remove it from "
            "_ARGV_UNREACHABLE -- the pin is a disclosure of a hole, and a stale "
            "pin re-opens it."
        )
        return

    assert (command, flag) in _ARGV_UNREACHABLE, (
        f"`roam {command} {flag}` never ran: Click rejected the argv "
        f"(exit {run.returncode}), so the gate under test did not execute and "
        "channel parity below is measuring nothing.\n"
        f"stderr: {run.stderr.strip()[:400]!r}\n"
        "Give the fixture the argv this command needs, or add the pair to "
        "_ARGV_UNREACHABLE with the reason. An absent measurement is UNKNOWN, "
        "never coverage."
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
    if (command, flag) in _ARGV_UNREACHABLE:
        pytest.skip(f"`roam {command} {flag}` never reaches its body here; see Law 0")

    cwd = workspaces[scenario]
    run = _invoke(cwd, ["--json", command, flag])

    # EVERY summary the command published, not the last JSON object printed.
    # Selecting by position let a trailing hint line stand in for the envelope
    # and turn this assertion into a silent pass.
    summaries = _published_summaries(run.stdout)
    if not summaries:
        assert (command, flag) in _NO_SUMMARY_PUBLISHED, (
            f"`roam --json {command} {flag}` published no summary object in the "
            f"{scenario} workspace, so Law 2 cannot hold it to anything. That is a "
            "coverage hole, not health. Make the command publish an envelope, or "
            "add the pair to _NO_SUMMARY_PUBLISHED so the hole is counted.\n"
            f"stdout: {run.stdout.strip()[:400]!r}"
        )
        pytest.skip(f"`roam --json {command} {flag}` publishes no summary (pinned)")

    incomplete = [s for s in summaries if s.get("scan_incomplete")]
    if not incomplete:
        return

    assert run.returncode != 0, (
        f"`roam --json {command} {flag}` in the {scenario} workspace published "
        f"scan_incomplete={incomplete[0].get('scan_incomplete')!r} and still exited 0. "
        "The command states the analysis had no input and then authorizes on that "
        "non-measurement. An absent measurement is UNKNOWN, never a benign CLEAN.\n"
        f"verdict: {incomplete[0].get('verdict')!r}"
    )


# ---------------------------------------------------------------------------
# Law 3 -- authorizing an unanalyzable workspace is an inventoried fact
# ---------------------------------------------------------------------------


def _pristine_unanalyzable(base: Path, name: str) -> Path:
    """A FRESH no-git / no-index directory, one per probe.

    Deliberately NOT the session-scoped ``unanalyzable`` workspace. roam
    auto-indexes, so the first command any test runs in that directory leaves
    a populated ``.roam/index.db`` behind and every later command sees a
    DIFFERENT environment than the docstring claims. Measured while building
    this law: ``db-check --ci`` exits 1 ("No roam index found") as the first
    command in the directory and 0 ("verdict: OK", checks_complete) once some
    earlier test has auto-indexed it -- so the inventory would record whichever
    order pytest-xdist happened to pick, and flip in CI.

    A per-probe directory makes the starting state identical for every pair:
    no git, no index, one text file, and whatever auto-indexing the command
    under test does to itself.
    """
    root = base / name
    root.mkdir(parents=True, exist_ok=True)
    (root / "notes.txt").write_text("no git, no index\n", encoding="utf-8")
    return root


def _authorization_evidence(run: _Run) -> str:
    """The verdict and the head of stdout, for a failure message that names it."""
    summaries = _published_summaries(run.stdout)
    verdict = next((s["verdict"] for s in summaries if "verdict" in s), None)
    keys = sorted({k for s in summaries for k in s}) if summaries else []
    return f"  verdict: {verdict!r}\n  summary keys: {keys}\n  stdout: {run.stdout.strip()[:400]!r}"


@pytest.mark.parametrize(("command", "flag"), CASES, ids=[f"{c}{f}" for c, f in CASES])
def test_no_gate_newly_authorizes_an_unanalyzable_workspace(tmp_path: Path, command: str, flag: str) -> None:
    """A gate that starts returning 0 over nothing must be a deliberate edit.

    ONE channel, because Law 1 has already asserted that every channel returns
    the same code for this argv -- probing all three here would re-measure
    parity and buy nothing.

    This is the law that fails on the pre-fix tree. Restore
    ``src/roam/commands/changed_files.py`` and ``src/roam/commands/
    cmd_adversarial.py`` to ``f059e077^`` and ``adversarial
    --fail-on-critical`` prints "VERDICT: No changes detected" and exits 0 in
    this workspace, because a git that answered ``fatal: not a git repository``
    and rc 128 reached the caller as ``[]`` -- indistinguishable from a clean
    tree. It is not in the inventory, so this test names it.
    """
    if (command, flag) in _ARGV_UNREACHABLE:
        pytest.skip(f"`roam {command} {flag}` never reaches its body here; see Law 0")

    run = _invoke(_pristine_unanalyzable(tmp_path, "probe"), ["--json", command, flag])
    if run.returncode != 0:
        return

    known = _AUTHORIZES_IN_AN_UNANALYZABLE_WORKSPACE.get((command, flag))
    assert known is not None, (
        f"`roam --json {command} {flag}` exited 0 in a workspace with no git, no index "
        "and no source files, and is not in the Law 3 inventory.\n"
        "Either it answered a question that needs none of those -- then add it as "
        "ANSWERED with the envelope you measured -- or it authorized on a "
        "non-measurement, which is the defect and is fixed in the command.\n"
        f"{_authorization_evidence(run)}"
    )


def test_authorization_inventory_has_no_stale_entries(tmp_path: Path) -> None:
    """An entry that now refuses must be deleted, so the inventory shrinks.

    Failing here is the ratchet working. It costs one subprocess per inventory
    entry rather than per case, so it stays cheap as the list gets shorter --
    which is the direction it is only allowed to move.
    """
    still_authorizing: list[tuple[str, str]] = []
    for index, (command, flag) in enumerate(sorted(_AUTHORIZES_IN_AN_UNANALYZABLE_WORKSPACE)):
        run = _invoke(_pristine_unanalyzable(tmp_path, f"probe{index}"), ["--json", command, flag])
        if run.returncode == 0:
            still_authorizing.append((command, flag))

    stale = sorted(set(_AUTHORIZES_IN_AN_UNANALYZABLE_WORKSPACE) - set(still_authorizing))
    assert not stale, (
        f"{len(stale)} inventory entry(ies) no longer authorize -- good. Delete them from "
        "_AUTHORIZES_IN_AN_UNANALYZABLE_WORKSPACE in the same commit as the fix, and lower "
        "_AUTHORIZATION_HIGH_WATER to match:\n"
        + "\n".join(f"  roam {c} {f}  ({_AUTHORIZES_IN_AN_UNANALYZABLE_WORKSPACE[c, f][0]})" for c, f in stale)
    )


def test_authorization_inventory_is_classified_and_bounded() -> None:
    """Every entry names a defined class and says what it measured.

    An inventory whose entries do not say why is a shrug, not a policy. The
    length check is a second, blunt guard: growth is already blocked per-pair
    above, and this makes a swap show up as an edit of a number.
    """
    bad: list[str] = []
    for (command, flag), entry in sorted(_AUTHORIZES_IN_AN_UNANALYZABLE_WORKSPACE.items()):
        klass, reason = entry
        if klass not in _INVENTORY_CLASSES:
            bad.append(f"roam {command} {flag}: undefined class {klass!r}")
        if len(reason) < 120 or "20" not in reason:
            bad.append(f"roam {command} {flag}: reason does not quote a dated measurement")
    assert not bad, "Law 3 inventory entries that do not explain themselves:\n" + "\n".join(f"  {b}" for b in bad)

    assert len(_AUTHORIZES_IN_AN_UNANALYZABLE_WORKSPACE) <= _AUTHORIZATION_HIGH_WATER, (
        f"the inventory grew to {len(_AUTHORIZES_IN_AN_UNANALYZABLE_WORKSPACE)} entries, above "
        f"the recorded high-water mark of {_AUTHORIZATION_HIGH_WATER}. Fix the gate instead; "
        "if the mark genuinely must move, move it in its own commit and say why."
    )

    unknown = sorted(set(_AUTHORIZES_IN_AN_UNANALYZABLE_WORKSPACE) - set(CASES))
    assert not unknown, (
        "inventory entries that are no longer part of the probed gate surface -- a flag was "
        f"renamed or removed, or discovery stopped seeing it: {unknown}"
    )
