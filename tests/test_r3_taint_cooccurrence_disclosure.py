"""R3 -- the co-occurrence disclosure must survive the rules-lint warning.

``cmd_taint`` appends the disclosure that carries the honest reading of
its own findings::

    "637 of 637 finding(s) rest on co-occurrence only -- no dataflow
     path was computed for them; they are excluded from risk_score and
     reported at confidence=low"

The very next branch, the ``qualified_only`` rules-lint disclosure,
assigned ``summary["warnings_out"]`` with a bare ``=`` instead of
appending, so any run that produced BOTH signals shipped the lint
warning and silently dropped the co-occurrence one. Every other
warning branch in the same block reads-then-appends
(``_rules_zero_anchors`` and the W607-AY/CJ substrate markers), which
is what makes the assignment a defect rather than a convention.

This repo measures ``rules_lint.qualified_only_violations: 0``, so the
clobbering branch is never entered on the dogfood corpus and the live
envelope kept its disclosure. Reachability is therefore a unit-test
question: the test below forces both signals to be present at once.

LAW 4 note: warning strings are diagnostic text, not
``agent_contract.facts`` content.
"""

from __future__ import annotations

import json as _json
import os

import pytest
from click.testing import CliRunner

_CO_OCCURRENCE_MARK = "rest on co-occurrence only"
_LINT_MARK = "qualified_only lint flagged"


@pytest.fixture
def cli_runner():
    return CliRunner()


@pytest.fixture
def taint_project(project_factory):
    """A project whose source and sink co-occur in one enclosing function.

    ``os.environ`` and a fixed-argv ``subprocess.run`` inside one function
    are the shape the bundled rule reports as unverified co-occurrence.
    """
    return project_factory(
        {
            "runner.py": (
                "import os\n"
                "import subprocess\n"
                "def launch():\n"
                "    target = os.environ['TARGET']\n"
                "    print(target)\n"
                "    return subprocess.run(['report-tool', '--summary'])\n"
            ),
        }
    )


def _invoke_taint(cli_runner, project_root, *args):
    from roam.cli import cli

    old_cwd = os.getcwd()
    try:
        os.chdir(str(project_root))
        return cli_runner.invoke(cli, ["--json", "taint", *args], catch_exceptions=False)
    finally:
        os.chdir(old_cwd)


def _warnings(data: dict) -> list[str]:
    return list(data["summary"].get("warnings_out") or [])


def test_co_occurrence_disclosure_survives_the_qualified_only_lint(cli_runner, taint_project, monkeypatch):
    """BOTH warnings ship when BOTH signals fire.

    Drives the real envelope-assembly path: the corpus supplies the
    co-occurrence findings, and the rules-lint capture is patched to
    return a violation so the second branch is entered. Before the fix
    the lint warning replaced the list outright and the co-occurrence
    disclosure never reached the consumer.
    """
    from roam.commands import cmd_taint as _mod

    real_capture = _mod._w489_a_capture_qualified_only_lint

    def _capture_with_violation(rules_path):
        rules, violations = real_capture(rules_path)
        return rules, list(violations) + [{"rule_id": "synthetic-r3-rule", "kind": "source", "name": "environ"}]

    monkeypatch.setattr(_mod, "_w489_a_capture_qualified_only_lint", _capture_with_violation)

    result = _invoke_taint(cli_runner, taint_project)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    # Guard the premise: this test proves nothing unless BOTH signals
    # are genuinely present in the run it just made.
    assert data["summary"]["evidence_mix"]["co_occurrence"] > 0, (
        f"premise failed -- no co-occurrence findings to disclose; evidence_mix = {data['summary']['evidence_mix']!r}"
    )
    assert data["summary"]["rules_lint"]["qualified_only_violations"] > 0, (
        f"premise failed -- lint branch not entered; rules_lint = {data['summary']['rules_lint']!r}"
    )

    warnings = _warnings(data)
    assert any(_LINT_MARK in w for w in warnings), f"the qualified_only lint warning must ship; got {warnings!r}"
    assert any(_CO_OCCURRENCE_MARK in w for w in warnings), (
        f"the co-occurrence disclosure was dropped by the lint branch; got {warnings!r}"
    )


def test_substrate_markers_still_land_last(cli_runner, taint_project, monkeypatch):
    """The W607-AY/CJ substrate markers keep the terminal position.

    They are appended last by design so a consumer scanning the tail of
    ``warnings_out`` finds the failure markers. Turning the lint branch
    from an assignment into an append must not reorder them.
    """
    from roam.commands import cmd_taint as _mod

    real_capture = _mod._w489_a_capture_qualified_only_lint

    def _capture_with_violation(rules_path):
        rules, violations = real_capture(rules_path)
        return rules, list(violations) + [{"rule_id": "synthetic-r3-rule", "kind": "sink", "name": "run"}]

    def _raise(*args, **kwargs):
        raise RuntimeError("synthetic-marker-from-R3")

    monkeypatch.setattr(_mod, "_w489_a_capture_qualified_only_lint", _capture_with_violation)
    monkeypatch.setattr(_mod, "wrap_findings", _raise)

    result = _invoke_taint(cli_runner, taint_project)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    warnings = _warnings(data)
    markers = [w for w in warnings if w.startswith("taint_") and "_failed:" in w]
    assert markers, f"premise failed -- no substrate marker in the run; got {warnings!r}"
    tail = warnings[-len(markers) :]
    assert tail == markers, f"substrate markers must stay last; got {warnings!r}"
