"""Text-anchor evidence must demand AST work only where its result is consumed."""

from __future__ import annotations

import pytest

from roam.db.connection import open_db
from roam.security import taint_engine
from roam.security.taint_engine import TaintRule


@pytest.mark.parametrize(
    "source,has_source,has_sink,expected_evidence",
    [
        ("def f():\n    return 42\n", False, False, []),
        ("def f():\n    return request.args.get('q')\n", True, False, []),
        ("def f():\n    os.system('fixed')\n", False, True, []),
        (
            "def source():\n    return request.args.get('q')\ndef sink():\n    os.system('fixed')\n",
            True,
            True,
            [],
        ),
        (
            "def f():\n    value = request.args.get('q')\n    os.system(value)\n",
            True,
            True,
            ["dataflow"],
        ),
        (
            "def f():\n    value = request.args.get('q')\n    os.system('fixed')\n",
            True,
            True,
            ["co_occurrence"],
        ),
        (
            "def f():\n    value = request.args.get('q')\n    os.system(value)\n    os.system(value)\n",
            True,
            True,
            ["dataflow", "dataflow"],
        ),
    ],
)
def test_argument_analysis_only_runs_for_consumed_anchor_pairs(
    project_factory, monkeypatch, source, has_source, has_sink, expected_evidence
):
    project = project_factory({"app.py": source})
    monkeypatch.chdir(project)
    calls = []
    original = taint_engine._python_argument_dataflow_pairs

    def measure(text, rule):
        calls.append((text, rule.rule_id))
        return original(text, rule)

    monkeypatch.setattr(taint_engine, "_python_argument_dataflow_pairs", measure)
    rule = TaintRule(
        rule_id="bounded-python-command",
        description="Keep dataflow and co-occurrence separate",
        severity="error",
        languages=("python",),
        sources=("request.args",),
        sinks=("os.system",),
    )
    with open_db(readonly=True) as conn:
        scan = taint_engine._text_scan_rule_anchors(conn, str(project), rule, {})
    assert len(calls) == (1 if expected_evidence else 0)
    assert bool(scan["sources"]) is has_source
    assert bool(scan["sinks"]) is has_sink
    findings = scan["co_occurrence_findings"]
    assert [finding.evidence for finding in findings] == expected_evidence
    assert [finding.severity for finding in findings] == [
        "error" if evidence == "dataflow" else "note" for evidence in expected_evidence
    ]
