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
   ``truncation_reason``, ``scan_incomplete``), or the semantic
   ``degradation_state`` seeded by a ``partial_success`` value that can be true,
   that reaches the output of one supported mode but not another;
2. a *non-zero exit* — a CI gate — reachable in one mode but not another.
   Rule 2 is the ``py-types`` defect stated as an invariant, and
   ``test_scanner_catches_the_prefix_py_types_defect`` below pins it
   against the real pre-fix source from git rather than a mock.

Raw ``partial_success`` and ``failed_checks`` KEY-NAME spelling remains
reporting-only: text and SARIF legitimately disclose those facts in prose.
The guard does gate the semantic fact when it sees a potentially true
``partial_success`` value and accepts equivalent prose such as "degraded",
"truncated", "no symbols", or a SARIF runtime notification.

ZERO-BASELINE INVARIANT
-----------------------
The scanner enumerates every ``cmd_*.py`` module and every Click command
function it contains.  Disclosure violations have no allowlist: one live
site fails this test.  Exit-code parity is tested separately because a
structured JSON error envelope may intentionally exit zero to survive the
MCP bridge; that transport rule must never exempt a missing disclosure.

ZERO IS ONLY MEANINGFUL BESIDE ITS DENOMINATOR (W1455)
------------------------------------------------------
``violations == []`` is asserted here, but it proves nothing on its own: a
scanner that parsed nothing reports the same empty list.  So the numerator
and the denominator are SEPARATE assertions —
``test_no_disclosure_asymmetry_in_any_enumerated_command`` for the
violations, ``test_scan_publishes_a_trustworthy_denominator`` for
``files_parsed`` / ``files_unanalyzable`` / the positive control.  A parse
regression must fail loudly instead of shrinking the corpus in silence;
``tests/test_w1455_scanner_fails_closed.py`` pins that behaviour directly.
"""

from __future__ import annotations

import pathlib
import subprocess
import sys

import pytest

from tests._helpers.repo_root import repo_root

REPO_ROOT = repo_root()
SCANNER = REPO_ROOT / "scripts" / "scan_disclosure_asymmetry.py"
COMMANDS = REPO_ROOT / "src" / "roam" / "commands"

_SCRIPTS = str(REPO_ROOT / "scripts")
if _SCRIPTS not in sys.path:
    sys.path.insert(0, _SCRIPTS)

import scan_disclosure_asymmetry as scanner  # noqa: E402


@pytest.fixture(scope="module")
def current() -> scanner.ScanReport:
    return scanner.scan(COMMANDS)


# ---------------------------------------------------------------------------
# The zero-baseline guard
# ---------------------------------------------------------------------------


def test_no_disclosure_asymmetry_in_any_enumerated_command(current: scanner.ScanReport) -> None:
    """Every computed degradation signal must reach every supported mode.

    Exit-code reachability is a separate transport invariant with its own
    regression below.  This assertion has no command allowlist and no
    shrinking baseline: a single disclosure violation fails immediately.
    The established fix is to route the signal into the branch that is blind —
    for ``warnings_out`` the established template is ``cmd_understand.py``:
    echo ``# warning: <marker>`` to STDERR from the non-JSON tails, which
    leaves stdout byte-identical and therefore breaks no golden output.
    """
    disclosure_violations = [v for v in current.violations if v["token"] != scanner.GATE_SIGNAL]
    assert not disclosure_violations, (
        f"{len(disclosure_violations)} output-branch disclosure asymmetry(ies).\n"
        "A signal that says 'this result is degraded' now reaches one output\n"
        "branch and not another, so a consumer of the other branch reads a\n"
        "degraded run as a clean one.\n\n"
        + "\n".join(
            f"  {v['module']}:{v['line']}::{v['command']}::{v['token']}: "
            f"seen by {v['observed_by']}, BLIND: {v['blind']}"
            for v in disclosure_violations
        )
    )


def test_scan_publishes_a_trustworthy_denominator(current: scanner.ScanReport) -> None:
    """The zero above is only worth what its denominator is worth.

    Three assertions, deliberately separate from ``violations == []``:

    * ``files_parsed`` is the whole command surface — zero parsed files is
      zero COVERAGE, not zero violations;
    * ``files_unanalyzable == 0`` — before W1455 a single ``def broken(:``
      made a module return ``[]``, indistinguishable from clean, and dropped
      it out of the coverage enumeration at the same time;
    * the positive control fired — otherwise "0 violations" and "the
      detector stopped working" are the same output.
    """
    assert current.files_parsed > 0, f"the scan parsed NO command modules at all: {current.summary()}"
    assert current.files_unanalyzable == 0, (
        f"{current.files_unanalyzable} command module(s) could not be analysed, so the "
        "zero-baseline assertion above was measured against a silently shrunken corpus:\n"
        + "\n".join(f"  {e['module']}: {e['reason']}" for e in current.unanalyzable)
    )
    assert current.positive_control.ok, (
        "the disclosure scanner failed its own positive control, so every verdict it "
        f"produced in this run is uninterpretable: {current.positive_control.detail}"
    )


def test_enumeration_covers_every_registered_command_module() -> None:
    """The guard discovers the registry; adding a command needs no test edit."""
    from roam.cli import _COMMANDS

    report = scanner.enumerate_command_sites(COMMANDS)
    assert report.files_unanalyzable == 0, (
        "a module that cannot be parsed vanishes from the enumeration, which is "
        "exactly how a coverage proof comes to under-report its own coverage:\n"
        + "\n".join(f"  {e['module']}: {e['reason']}" for e in report.unanalyzable)
    )
    scanned_modules = {f"roam.commands.{str(site['module'])[:-3]}" for site in report.sites}
    registered_modules = {module for module, _attr in _COMMANDS.values()}
    missing = sorted(registered_modules - scanned_modules)
    assert not missing, "Registered command modules omitted from disclosure enumeration:\n" + "\n".join(
        f"  {module}" for module in missing
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
    gates = [v for v in scanner.scan(tmp_path).violations if v["token"] == scanner.GATE_SIGNAL]
    assert gates, "the scanner did not flag the pre-fix py-types CI-gate asymmetry"
    assert gates[0]["observed_by"] == ["text"]
    assert set(gates[0]["blind"]) == {"json", "sarif"}, (
        "the pre-fix defect is precisely that --json and --sarif could not reach the gate"
    )

    result = scanner.scan_file(COMMANDS / "cmd_py_types.py")
    assert result.status != scanner.UNANALYZABLE, f"cmd_py_types.py is unanalyzable: {result.reason}"
    shipped = [v for v in result.violations if v["token"] == scanner.GATE_SIGNAL]
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

    result = scanner.scan_file(COMMANDS / "cmd_understand.py")
    assert result.status != scanner.UNANALYZABLE, f"cmd_understand.py is unanalyzable: {result.reason}"
    clean = [v for v in result.violations if v["token"] == "warnings_out"]
    assert not clean, f"cmd_understand was expected to be symmetric, got {clean}"

    (tmp_path / "cmd_understand.py").write_text(source.replace(disclosure, ""), encoding="utf-8")
    broken = [v for v in scanner.scan(tmp_path).violations if v["token"] == "warnings_out"]
    assert broken, "removing the only text-mode warnings disclosure did NOT trip the lint"
    assert "text" in broken[0]["blind"]


# ---------------------------------------------------------------------------
# Scanner sanity — the analysis itself
# ---------------------------------------------------------------------------


def _analyze(src: str, tmp_path: pathlib.Path, name: str = "cmd_probe.py") -> list[dict]:
    """Violations for a synthetic module — and never for one that failed to parse.

    The denominator is asserted here rather than in every caller: a probe that
    silently failed to parse would otherwise satisfy every ``== []`` below.
    """
    (tmp_path / name).write_text(src, encoding="utf-8")
    report = scanner.scan(tmp_path)
    assert report.files_unanalyzable == 0, f"probe module did not parse: {report.unanalyzable}"
    assert report.files_parsed > 0, "probe module was not picked up by the scan glob"
    return report.violations


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
    assert found[0]["line"] == 10


def test_json_only_disclosure_is_blind_in_text_and_sarif(tmp_path: pathlib.Path) -> None:
    """A supported SARIF branch is part of the same zero-baseline invariant."""
    src = """
import click

@click.command()
@click.pass_context
def probe(ctx):
    warnings_out = []
    json_mode = ctx.obj.get("json")
    sarif_mode = ctx.obj.get("sarif")
    if json_mode:
        click.echo(to_json({"warnings_out": warnings_out}))
    elif sarif_mode:
        click.echo(to_sarif([]))
    else:
        click.echo("VERDICT: ok")
"""
    found = _analyze(src, tmp_path)
    assert [v["token"] for v in found] == ["warnings_out"]
    assert found[0]["observed_by"] == ["json"]
    assert found[0]["blind"] == ["sarif", "text"]


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


def test_partial_success_true_is_gated_semantically(tmp_path: pathlib.Path) -> None:
    """An explicit partial run must not be presented as an unqualified OK."""
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
    found = scanner.scan(tmp_path).violations
    assert [v["token"] for v in found] == ["degradation_state"]
    assert found[0]["blind"] == ["text"]

    fixed = src.replace('click.echo("VERDICT: ok")', 'click.echo("VERDICT: degraded — no symbols analyzed")')
    (tmp_path / "cmd_probe.py").write_text(fixed, encoding="utf-8")
    assert scanner.scan(tmp_path).violations == []

    # Raw key-name spelling is still available for audits, but it is not
    # what the semantic guard requires text/SARIF to reproduce.
    opted_in = scanner.scan(tmp_path, scanner.DISCLOSURE_TOKENS + scanner.SCHEMA_TOKENS).violations
    assert [v["token"] for v in opted_in] == ["partial_success"]


@pytest.mark.parametrize(
    "emitter",
    [
        pytest.param("laws_to_sarif", id="laws"),
        pytest.param("llm_smells_to_sarif", id="llm-smells"),
        pytest.param("orphan_routes_to_sarif", id="orphan-routes"),
    ],
)
def test_partial_sarif_run_contains_runtime_notification(emitter: str) -> None:
    """A zero-result SARIF document must say when the producer was degraded."""
    from roam.output import sarif

    disclosure = "partial result: no analyzable input was available"
    doc = getattr(sarif, emitter)([], disclosures=[disclosure])
    invocation = doc["runs"][0]["invocations"][0]
    notifications = invocation["toolExecutionNotifications"]

    assert invocation["executionSuccessful"] is True
    assert [note["message"]["text"] for note in notifications] == [disclosure]
    assert notifications[0]["level"] == "warning"


def test_sarif_boundary_adapter_discloses_a_floored_document() -> None:
    """A projector failure represented by ``{}`` still yields honest SARIF."""
    from roam.output.sarif import with_sarif_disclosures

    disclosure = "partial result: SARIF projection failed"
    doc = with_sarif_disclosures({}, [disclosure])

    run = doc["runs"][0]
    assert run["results"] == []
    assert run["invocations"][0]["toolExecutionNotifications"][0]["message"]["text"] == disclosure
