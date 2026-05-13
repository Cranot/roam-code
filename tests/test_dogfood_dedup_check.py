"""Tests for dev/dogfood_dedup_check.py — the pre-dispatch dedup helper."""
from __future__ import annotations

import sys
from pathlib import Path

# Make dev/ importable for these tests
_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT / "dev"))


def test_known_fixed_commands_detected():
    """Commands with W18.* eval docs should report verdict: fixed."""
    from dogfood_dedup_check import check_commands
    rows = check_commands(["sbom", "stale-refs"])
    # Both should have at least one eval with 'fixed' status today
    assert all(r["evals_found"] >= 1 for r in rows)
    assert all(r["verdict"] == "fixed" for r in rows)


def test_unknown_command_returns_no_evals():
    from dogfood_dedup_check import check_commands
    rows = check_commands(["nonexistent-command-xyz"])
    assert rows[0]["verdict"] == "no_evals"


def test_from_md_extraction(tmp_path):
    """--from-md extracts `roam <cmd>` references."""
    md = tmp_path / "report.md"
    md.write_text("Run `roam sbom` then `roam dead` to check.\n")
    from dogfood_dedup_check import _parse_commands_from_md
    commands = _parse_commands_from_md(md)
    assert "sbom" in commands
    assert "dead" in commands


def test_fix_ref_alone_classifies_as_fixed():
    """W37.4 fix: an eval with fix_ref but non-'fixed' status is still fixed."""
    from dogfood_dedup_check import check_commands
    # 'dead' has the W18.3 fix_ref with status:unverifiable-on-this-repo
    rows = check_commands(["dead"])
    assert rows[0]["verdict"] == "fixed", (
        f"dead has W18.3 fix_ref; should classify as fixed regardless of status label"
    )


def test_classify_verdict_unit_cases():
    """Unit-level coverage of the _classify_verdict helper."""
    from dogfood_dedup_check import _classify_verdict
    assert _classify_verdict("fixed-in-W18", None) == "fixed"
    assert _classify_verdict("fixed-in-W18", "some ref") == "fixed"
    assert _classify_verdict("unverifiable-on-this-repo", "W18.3 — ...") == "fixed"
    assert _classify_verdict("open", None) == "open"
    assert _classify_verdict("needs-investigation", None) == "open"
    assert _classify_verdict(None, None) == "unknown"
