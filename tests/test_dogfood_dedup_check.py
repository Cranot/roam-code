"""Tests for dev/dogfood_dedup_check.py — the pre-dispatch dedup helper.

These tests depend on ``internal/dogfood/`` which is intentionally gitignored
(private corpus). They pass on local dev (Cranot has the dir) but fail on
public clones / CI runners because the data isn't present. Skip the
data-dependent tests when the dogfood dir is missing; keep the data-free
unit tests (``_classify_verdict``, ``_parse_commands_from_md``, etc.) running.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

# Make dev/ importable for these tests
_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT / "dev"))

_DOGFOOD_DIR_PRESENT = (_REPO_ROOT / "internal" / "dogfood").is_dir()
_skip_no_dogfood = pytest.mark.skipif(
    not _DOGFOOD_DIR_PRESENT,
    reason="internal/dogfood/ is gitignored — not available on CI / public clones",
)


@_skip_no_dogfood
def test_known_fixed_commands_detected():
    """Commands with W18.* eval docs should report verdict: fixed."""
    from dogfood_dedup_check import check_commands

    rows = check_commands(["sbom", "stale-refs"])
    # Both should have at least one eval with 'fixed' status today
    assert all(r["evals_found"] >= 1 for r in rows)
    assert all(r["verdict"] == "fixed" for r in rows)


@_skip_no_dogfood
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


@_skip_no_dogfood
def test_fix_ref_alone_classifies_as_fixed():
    """W37.4 fix: an eval with fix_ref but non-'fixed' status is still fixed."""
    from dogfood_dedup_check import check_commands

    # 'dead' has the W18.3 fix_ref with status:unverifiable-on-this-repo
    rows = check_commands(["dead"])
    assert rows[0]["verdict"] == "fixed", "dead has W18.3 fix_ref; should classify as fixed regardless of status label"


def test_classify_verdict_unit_cases():
    """Unit-level coverage of the _classify_verdict helper."""
    from dogfood_dedup_check import _classify_verdict

    assert _classify_verdict("fixed-in-W18", None) == "fixed"
    assert _classify_verdict("fixed-in-W18", "some ref") == "fixed"
    assert _classify_verdict("unverifiable-on-this-repo", "W18.3 — ...") == "fixed"
    assert _classify_verdict("open", None) == "open"
    assert _classify_verdict("needs-investigation", None) == "open"
    assert _classify_verdict(None, None) == "unknown"
