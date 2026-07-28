"""W1331 — a disclosure must reach EVERY output branch, not just ``--json``.

WHAT THIS GUARD PROTECTS
------------------------
W1320 proved four false-clean defects in one sweep, and three of the four
shared a single mechanism: the disclosure logic was written into the
``if json_mode:`` branch — the branch the tests exercise — and never
mirrored into the text branch a human reads or the SARIF branch a CI gate
consumes. The JSON envelope correctly said "this signal could not be
computed"; the other two modes silently reported clean.

The proven instances:

* ``py-types --ci --min-coverage 90`` on a corpus with no public Python
  functions: the gate sat AFTER the json/sarif early returns, so text
  exited 5 while ``--json`` and ``--sarif`` — the two modes a CI job
  actually consumes — both exited 0 on the identical number.
* ``delete-check``: ``matches = []`` on search-engine failure, threaded
  into ``warnings_out`` and rendered in the JSON envelope; the ``else:``
  branch printed the verdict alone.
* ``auth-gaps`` with ``--routes-only``: a whole scan phase skipped, but
  the text emitter printed "(none found)" for the scope that never ran.

The recommendation those fixes ended with is what this file implements:
the asymmetry is MECHANICALLY GREPPABLE, so build the check instead of
running another manual sweep.

WHAT IT CHECKS
--------------
``scripts/scan_disclosure_asymmetry.py`` partitions each click command
into output-mode regions (json / sarif / text), following early returns
and recursing into in-module helpers — including shared emitters that take
``json_mode`` as a parameter and branch on it internally. It then reports
two kinds of asymmetry:

1. a *disclosure token* (``warnings_out``, ``empty_corpus_state``,
   ``truncation_reason``, ``scan_incomplete``) that reaches the output of
   one supported mode but not another;
2. a *non-zero exit* — a CI gate — reachable in one mode but not another.
   Rule 2 is the ``py-types`` defect stated as an invariant, and
   ``test_scanner_catches_the_prefix_py_types_defect`` below pins it
   against the real pre-fix source from git rather than a mock.

``partial_success`` and ``failed_checks`` are deliberately NOT gated: both
are envelope KEY NAMES rather than computed signals, so a JSON branch
carries them by construction and a text branch never spells them, and the
check degenerates into "text is not JSON". ``partial_success`` had 164
such hits; ``failed_checks`` had two, and running both proved the text
branch discloses the identical list under a different name (``adversarial``
appends it to the VERDICT line, ``doctor`` renders one ``[WARN]``/``[FAIL]``
row per entry). Measure them with ``--include-schema-keys`` for reporting.

RATCHET
-------
The current tree is not clean, so this is a shrinking baseline, not a
tripwire: ``tests/data/disclosure_asymmetry_baseline.json``. The test
fails on a NEW violation *and* on a STALE entry, so the file can only get
smaller. Every entry names a reason code defined in the same file — an
allowlist whose entries do not say why is a shrug, not a policy, and
growing it is a reviewable diff by construction.
"""

from __future__ import annotations

import json
import pathlib
import subprocess
import sys

import pytest

from tests._helpers.repo_root import repo_root

REPO_ROOT = repo_root()
SCANNER = REPO_ROOT / "scripts" / "scan_disclosure_asymmetry.py"
BASELINE = REPO_ROOT / "tests" / "data" / "disclosure_asymmetry_baseline.json"
COMMANDS = REPO_ROOT / "src" / "roam" / "commands"

_SCRIPTS = str(REPO_ROOT / "scripts")
if _SCRIPTS not in sys.path:
    sys.path.insert(0, _SCRIPTS)

import scan_disclosure_asymmetry as scanner  # noqa: E402


def _load_baseline() -> dict:
    return json.loads(BASELINE.read_text(encoding="utf-8"))


def _shape(v: dict) -> tuple[str, tuple[str, ...], tuple[str, ...]]:
    return (
        scanner.violation_key(v),
        tuple(v["observed_by"]),
        tuple(v["blind"]),
    )


@pytest.fixture(scope="module")
def current() -> list[dict]:
    return scanner.scan(COMMANDS)


# ---------------------------------------------------------------------------
# The ratchet
# ---------------------------------------------------------------------------


def test_no_new_disclosure_asymmetry(current: list[dict]) -> None:
    """A command may not start disclosing in one output branch only.

    If this fails on a NEW entry, the fix is almost never to append to the
    baseline. It is to route the signal into the branch that is blind —
    for ``warnings_out`` the established template is ``cmd_understand.py``:
    echo ``# warning: <marker>`` to STDERR from the non-JSON tails, which
    leaves stdout byte-identical and therefore breaks no golden output.
    """
    baseline = _load_baseline()
    known = {_shape(v) for v in baseline["violations"]}
    new = sorted(_shape(v) for v in current if _shape(v) not in known)
    assert not new, (
        f"{len(new)} NEW output-branch disclosure asymmetry(ies).\n"
        "A signal that says 'this result is degraded' now reaches one output\n"
        "branch and not another, so a consumer of the other branch reads a\n"
        "degraded run as a clean one.\n\n"
        + "\n".join(f"  {key}: seen by {list(seen)}, BLIND: {list(blind)}" for key, seen, blind in new)
    )


def test_baseline_has_no_stale_entries(current: list[dict]) -> None:
    """A fixed asymmetry must be deleted from the baseline, so it can shrink."""
    baseline = _load_baseline()
    live = {_shape(v) for v in current}
    stale = sorted(_shape(v) for v in baseline["violations"] if _shape(v) not in live)
    assert not stale, (
        f"{len(stale)} baseline entry(ies) no longer reproduce — good. Delete them\n"
        f"from {BASELINE.relative_to(REPO_ROOT)} so the ratchet holds:\n"
        + "\n".join(f"  {key}: recorded seen={list(seen)} blind={list(blind)}" for key, seen, blind in stale)
    )


# ---------------------------------------------------------------------------
# The allowlist is DATA, and exempting a command is a visible act
# ---------------------------------------------------------------------------


def test_every_baseline_entry_names_a_reason() -> None:
    """No silent exemptions: every entry cites a defined, non-placeholder code."""
    baseline = _load_baseline()
    codes = baseline["_reason_codes"]
    bad: list[str] = []
    for v in baseline["violations"]:
        reason = v.get("reason")
        if not reason or reason == "TODO":
            bad.append(f"{scanner.violation_key(v)}: reason is {reason!r}")
        elif reason not in codes:
            bad.append(f"{scanner.violation_key(v)}: undefined reason code {reason!r}")
    assert not bad, "Baseline entries without a usable reason:\n" + "\n".join(f"  {b}" for b in bad)


def test_reason_codes_are_defined_and_used() -> None:
    """Codes are documented prose, and a code nobody cites is deleted."""
    baseline = _load_baseline()
    codes = baseline["_reason_codes"]
    assert codes == scanner.REASON_CODES, (
        "tests/data/disclosure_asymmetry_baseline.json's _reason_codes has drifted "
        "from scripts/scan_disclosure_asymmetry.py's REASON_CODES. Regenerate with "
        "`python scripts/scan_disclosure_asymmetry.py --baseline`."
    )
    for code, prose in codes.items():
        assert len(prose) > 80, f"reason code {code!r} does not explain itself"
    used = {v["reason"] for v in baseline["violations"]}
    unused = sorted(set(codes) - used)
    assert not unused, f"Reason codes defined but cited by no entry — delete them: {unused}"


def test_baseline_only_ever_shrinks() -> None:
    """A second, blunt guard: the recorded high-water mark may only fall.

    ``test_no_new_disclosure_asymmetry`` already blocks additions, but it
    compares SHAPES. This pins the raw count so that swapping one entry for
    another still shows up as a deliberate edit of a number.
    """
    baseline = _load_baseline()
    assert len(baseline["violations"]) <= baseline["_high_water_mark"], (
        f"The baseline grew to {len(baseline['violations'])} entries, above the recorded "
        f"high-water mark of {baseline['_high_water_mark']}. Fix the asymmetry instead; "
        "if the mark genuinely must move, move it in its own commit and say why."
    )


# ---------------------------------------------------------------------------
# The lint has been seen to fail
# ---------------------------------------------------------------------------


def test_scanner_catches_the_prefix_py_types_defect(tmp_path: pathlib.Path) -> None:
    """The pre-fix ``cmd_py_types.py`` from git must trip the gate rule.

    A lint nobody has watched fail is not a lint. This reconstructs the
    exact source that shipped the W1320 ``py-types`` defect — the CI gate
    sitting after the json and sarif early returns — and asserts the
    scanner names it, then asserts the shipped file does not.
    """
    fix = subprocess.run(
        ["git", "log", "--format=%H", "-1", "--grep=collapsing an uncomputable signal"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    ).stdout.strip()
    if not fix:
        pytest.skip("W1320 fix commit not in this clone's history")
    before = subprocess.run(
        ["git", "show", f"{fix}^:src/roam/commands/cmd_py_types.py"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    if before.returncode != 0:
        pytest.skip("pre-fix blob unavailable (shallow clone)")

    (tmp_path / "cmd_py_types.py").write_text(before.stdout, encoding="utf-8")
    gates = [v for v in scanner.scan(tmp_path) if v["token"] == scanner.GATE_SIGNAL]
    assert gates, "the scanner did not flag the pre-fix py-types CI-gate asymmetry"
    assert gates[0]["observed_by"] == ["text"]
    assert set(gates[0]["blind"]) == {"json", "sarif"}, (
        "the pre-fix defect is precisely that --json and --sarif could not reach the gate"
    )

    shipped = [v for v in scanner.scan_file(COMMANDS / "cmd_py_types.py") if v["token"] == scanner.GATE_SIGNAL]
    assert not shipped, f"cmd_py_types.py regressed the W1320 gate fix: {shipped}"


def test_scanner_catches_a_reintroduced_text_blindness(tmp_path: pathlib.Path) -> None:
    """Deleting a live text-mode disclosure must flip a clean command dirty.

    ``cmd_understand`` is the reference implementation of the fix template:
    it echoes its ``warnings_out`` markers to stderr in the text tail. Strip
    that loop and the command must become a violation — this proves the scan
    is measuring the disclosure and not some incidental token.
    """
    source = (COMMANDS / "cmd_understand.py").read_text(encoding="utf-8")
    disclosure = (
        "        if _w607bc_warnings_out:\n"
        "            for marker in _w607bc_warnings_out:\n"
        '                click.echo(f"# warning: {marker}", err=True)\n'
    )
    assert disclosure in source, "cmd_understand's text-mode disclosure moved; update this test"

    clean = [v for v in scanner.scan_file(COMMANDS / "cmd_understand.py") if v["token"] == "warnings_out"]
    assert not clean, f"cmd_understand was expected to be symmetric, got {clean}"

    (tmp_path / "cmd_understand.py").write_text(source.replace(disclosure, ""), encoding="utf-8")
    broken = [v for v in scanner.scan(tmp_path) if v["token"] == "warnings_out"]
    assert broken, "removing the only text-mode warnings disclosure did NOT trip the lint"
    assert "text" in broken[0]["blind"]


# ---------------------------------------------------------------------------
# Scanner sanity — the analysis itself
# ---------------------------------------------------------------------------


def _analyze(src: str, tmp_path: pathlib.Path, name: str = "cmd_probe.py") -> list[dict]:
    (tmp_path / name).write_text(src, encoding="utf-8")
    return scanner.scan(tmp_path)


def test_shared_disclosure_is_not_a_violation(tmp_path: pathlib.Path) -> None:
    """One emitter used by every mode is symmetric — it must not be flagged."""
    src = """
import click

@click.command()
@click.pass_context
def probe(ctx):
    warnings_out = []
    json_mode = ctx.obj.get("json")
    if json_mode:
        click.echo(to_json({"warnings_out": warnings_out}))
    else:
        for marker in warnings_out:
            click.echo(f"# warning: {marker}", err=True)
        click.echo("VERDICT: ok")
"""
    assert _analyze(src, tmp_path) == []


def test_json_only_disclosure_is_a_violation(tmp_path: pathlib.Path) -> None:
    """The same command, with the text branch blind, must be flagged."""
    src = """
import click

@click.command()
@click.pass_context
def probe(ctx):
    warnings_out = []
    json_mode = ctx.obj.get("json")
    if json_mode:
        click.echo(to_json({"warnings_out": warnings_out}))
    else:
        click.echo("VERDICT: ok")
"""
    found = _analyze(src, tmp_path)
    assert [v["token"] for v in found] == ["warnings_out"]
    assert found[0]["blind"] == ["text"]


def test_appending_in_shared_code_credits_nobody(tmp_path: pathlib.Path) -> None:
    """Every branch can SEE the bucket; the defect is that one prints it.

    Attribution must be about reaching output, not about the variable being
    lexically in scope — otherwise the dominant real-world shape (compute
    the bucket up top, render it in the JSON tail only) scores as clean.
    """
    src = """
import click

@click.command()
@click.pass_context
def probe(ctx):
    warnings_out = []
    try:
        scan()
    except Exception as exc:
        warnings_out.append(f"probe_failed:{exc}")
    json_mode = ctx.obj.get("json")
    if json_mode:
        click.echo(to_json({"warnings_out": warnings_out}))
    else:
        click.echo("VERDICT: ok")
"""
    found = _analyze(src, tmp_path)
    assert [v["blind"] for v in found] == [["text"]]


def test_shared_emitter_that_branches_internally_is_not_a_free_pass(
    tmp_path: pathlib.Path,
) -> None:
    """A helper taking ``json_mode`` as a PARAMETER is a mode dispatcher too.

    Crediting the whole helper to every calling mode is exactly how this
    asymmetry stays invisible, so the scan re-partitions inside it.
    """
    src = """
import click

def _emit(json_mode, warnings_out):
    if json_mode:
        click.echo(to_json({"warnings_out": warnings_out}))
    else:
        click.echo("VERDICT: ok")

@click.command()
@click.pass_context
def probe(ctx):
    warnings_out = []
    _emit(ctx.obj.get("json"), warnings_out)
"""
    found = _analyze(src, tmp_path)
    assert [v["token"] for v in found] == ["warnings_out"]
    assert found[0]["blind"] == ["text"]


def test_gate_after_a_json_early_return_is_a_violation(tmp_path: pathlib.Path) -> None:
    """The py-types shape, minimised: a gate the JSON branch returns past."""
    src = """
import click

@click.command()
@click.pass_context
def probe(ctx, ci_mode=True, coverage=0):
    if ctx.obj.get("json"):
        click.echo(to_json({"coverage": coverage}))
        return
    click.echo(f"coverage {coverage}")
    if ci_mode and coverage < 90:
        click.echo("GATE FAILED")
        ctx.exit(5)
"""
    found = _analyze(src, tmp_path)
    assert [v["token"] for v in found] == [scanner.GATE_SIGNAL]
    assert found[0]["blind"] == ["json"]

    fixed = src.replace("        return\n", "").replace(
        '    click.echo(f"coverage {coverage}")', '    else:\n        click.echo(f"coverage {coverage}")'
    )
    assert not [v for v in _analyze(fixed, tmp_path, "cmd_fixed.py") if v["module"] == "cmd_fixed.py"]


def test_partial_success_is_not_gated(tmp_path: pathlib.Path) -> None:
    """The envelope schema key stays measurable but ungated, on purpose."""
    src = """
import click

@click.command()
@click.pass_context
def probe(ctx):
    if ctx.obj.get("json"):
        click.echo(to_json({"partial_success": True}))
    else:
        click.echo("VERDICT: ok")
"""
    (tmp_path / "cmd_probe.py").write_text(src, encoding="utf-8")
    assert scanner.scan(tmp_path) == []
    opted_in = scanner.scan(tmp_path, scanner.DISCLOSURE_TOKENS + scanner.SCHEMA_TOKENS)
    assert [v["token"] for v in opted_in] == ["partial_success"]
