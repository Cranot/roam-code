"""Cross-channel memory: verify's persisted findings ride into compile envelopes.

`roam verify --report --persist` writes the whole-repo findings to
`.roam/verify-report.json`; the compiler now embeds the named file's OPEN
findings (category counts + top items + report age) as `known_findings`.
Context, not an answer — deliberately absent from the L1 promotion keys.
"""

from __future__ import annotations

import json
from pathlib import Path

from roam.plan.compiler import _probe_known_findings_for_task

REPORT = {
    "violations": [
        {
            "category": "complexity",
            "severity": "WARN",
            "file": "src/loader.py",
            "line": 10,
            "symbol": "load_items",
            "message": "fn `load_items` cognitive complexity 21 (threshold 15)",
        },
        {
            "category": "error_handling",
            "severity": "FAIL",
            "file": "src/loader.py",
            "line": 14,
            "symbol": "load_items",
            "message": "broad except swallows",
        },
        {
            "category": "complexity",
            "severity": "WARN",
            "file": "src/other.py",
            "line": 3,
            "symbol": "f",
            "message": "x",
        },
    ]
}


def _project(tmp_path: Path) -> Path:
    (tmp_path / ".roam").mkdir()
    (tmp_path / ".roam" / "verify-report.json").write_text(json.dumps(REPORT))
    return tmp_path


def test_named_file_findings_embed(tmp_path):
    proj = _project(tmp_path)
    facts = _probe_known_findings_for_task(["src/loader.py"], str(proj))
    assert facts is not None
    kf = facts["known_findings"]
    assert kf["total"] == 2
    assert kf["by_category"] == {"complexity": 1, "error_handling": 1}
    assert kf["top"][0]["symbol"] == "load_items"
    assert kf["report_age_hours"] >= 0.0
    assert "do not re-run a whole-repo scan" in facts["known_findings_definition"]


def test_silent_when_file_has_no_findings(tmp_path):
    proj = _project(tmp_path)
    assert _probe_known_findings_for_task(["src/clean.py"], str(proj)) is None


def test_silent_without_report(tmp_path):
    assert _probe_known_findings_for_task(["src/loader.py"], str(tmp_path)) is None


def test_silent_without_named_paths(tmp_path):
    proj = _project(tmp_path)
    assert _probe_known_findings_for_task([], str(proj)) is None


def test_not_in_promotion_keys():
    """known_findings is context, not an answer — it must never promote a
    facts envelope to L1 on its own."""
    from roam.plan.compiler import _L1_PROCEDURE_KEYS

    for proc, keys in _L1_PROCEDURE_KEYS.items():
        assert "known_findings" not in keys, proc


def test_extender_label_registered():
    from roam.plan.compiler import _L1_ALWAYS_ON_PROBES

    assert any(label == "known_findings" for label, _ in _L1_ALWAYS_ON_PROBES)
