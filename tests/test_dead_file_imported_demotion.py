"""File-imported-but-symbol-unused exports must NOT sit in the delete tier.

Band-1 defect (2026-08 re-triage, task #141): ``roam dead`` placed live
code in the top-confidence SAFE tier via the ``file_imported`` branch of
``_dead_action``, and ``_dead_reason`` claimed "this export has no
production consumers" — a per-symbol check that never ran.
``_dead_file_import_meta`` classifies importer FILES as
test/barrel/production; it never walks an importer to see whether it
references the specific symbol.

Measurement that killed the old claim:

- ROAM-FEEDBACK-2026-07-15 THEME 6: hand-verification found 2 of 14
  file-imported SAFE verdicts true (14% precision).
- Union feedback 2026-05-19 / 2026-07-07: 0 of 20 SAFE survived.

Estate laws pinned here: the claim must never exceed the measurement,
and a reason string may only name checks that RAN. The demotion sends
the branch to the existing REVIEW tier; the reason now discloses the
non-measurement. The sibling-import graph walk (THEME 6) is the future
fix that could re-earn a high tier — these tests do not block it, they
block re-promotion WITHOUT it.
"""

from __future__ import annotations

from pathlib import Path

from roam.commands.cmd_dead import _dead_action, _dead_reason, dead

# ---------------------------------------------------------------------------
# _dead_action: the demotion itself
# ---------------------------------------------------------------------------

# A name/path that dodges every earlier branch: not a test path, not
# scaffolding, not MCP, not cmd_*, not an ABC method, not an entry
# name/file, no API prefix, not a barrel, not underscore-private.
_PLAIN_ROW = {
    "name": "withholding_summary",
    "file_path": "src/pkg/tax_rules.py",
    "kind": "function",
}


def test_file_imported_symbol_is_demoted_out_of_the_safe_tier():
    """The 14%-precision branch (2 of 14 true) may not verdict SAFE."""
    action, confidence = _dead_action(dict(_PLAIN_ROW), file_imported=True)
    assert action == "REVIEW", (
        f"file_imported symbols measured 2/14 true in the SAFE tier "
        f"(THEME 6) — expected REVIEW, got ({action}, {confidence}). "
        f"Re-promotion requires the sibling-import graph walk to exist "
        f"and RUN first."
    )
    assert confidence < 80, (
        f"confidence {confidence} puts the branch back in a delete-grade band without new measurement"
    )


def test_not_imported_safe_bands_are_preserved():
    """The 95/90 bands were not indicted by the measurement — they stay."""
    action, confidence = _dead_action(dict(_PLAIN_ROW), file_imported=False)
    assert (action, confidence) == ("SAFE", 90)

    private_row = dict(_PLAIN_ROW, name="_quiet_helper")
    action, confidence = _dead_action(private_row, file_imported=False)
    assert (action, confidence) == ("SAFE", 95)


# ---------------------------------------------------------------------------
# _dead_reason: first-ever coverage — the string may only name checks
# that ran
# ---------------------------------------------------------------------------


def _fmeta(**overrides):
    meta = {
        "module_path_importers": 3,
        "production_module_path_importers": 2,
        "test_module_path_importers": 1,
        "barrel_module_path_importers": 1,
        "consumer_module_path_importers": 2,
    }
    meta.update(overrides)
    return {10: meta}


_REASON_ROW = {"id": 1, "file_id": 10}
_DISCLOSURE = "importers not checked for this specific export"


def test_reason_for_file_imported_branch_discloses_the_non_measurement():
    reason = _dead_reason(dict(_REASON_ROW), {}, _fmeta(), {})
    assert _DISCLOSURE in reason, (
        f"reason must disclose that importing files were never walked for "
        f"this symbol (no per-symbol check exists — THEME 6): {reason!r}"
    )
    assert "no production consumers" not in reason, (
        f"'no production consumers' claims a per-symbol importer check that never ran (measured 2/14 true): {reason!r}"
    )
    # The measured facts stay in the string.
    assert "file is imported by 3 place(s)" in reason


def test_reason_sibling_variant_carries_the_same_disclosure():
    sibling_meta = {10: {"production_referenced_siblings": 2}}
    reason = _dead_reason(dict(_REASON_ROW), {}, _fmeta(), sibling_meta)
    assert _DISCLOSURE in reason, reason
    assert "no production consumers" not in reason, reason
    assert "2 sibling export(s) are used" in reason


def test_reason_no_importer_branch_is_unchanged():
    """The uninidicted branch keeps its wording (it claims no check)."""
    reason = _dead_reason(dict(_REASON_ROW), {}, {}, {})
    assert "no module importers" in reason


# ---------------------------------------------------------------------------
# Docstring / help / source: claims must match checks that exist
# ---------------------------------------------------------------------------


def test_dead_action_docstring_claims_only_checks_that_exist():
    doc = _dead_action.__doc__
    assert "no dynamic usage possible" not in doc, (
        "no dynamic-usage scan exists in cmd_dead — the docstring may not claim one"
    )
    assert "no string-based references" not in doc, (
        "no string-reference scan exists in cmd_dead — the docstring may not claim one"
    )
    assert "NOT scanned" in doc, "the docstring must disclose what is not measured"
    # The THEME 6 future fix stays on record: honest claim now, graph walk later.
    assert "sibling-import graph walk" in doc


def test_reachable_only_help_does_not_promise_deletion_safety():
    opt = next(p for p in dead.params if p.name == "reachable_only")
    assert "safe to delete without further investigation" not in opt.help, (
        "--reachable-only intersects two STATIC call-graph checks; it may "
        "not promise deletion safety no static check can establish"
    )
    assert "not scanned" in opt.help


def test_cmd_dead_source_never_claims_the_unran_per_symbol_check():
    """Source-level guard: the over-claim phrase and the SAFE-80 verdict
    must not reappear anywhere in the module (reason strings, echo
    headers, docstrings)."""
    import roam.commands.cmd_dead as mod

    src = Path(mod.__file__).read_text(encoding="utf-8")
    assert "this export has no production consumers" not in src
    assert 'return "SAFE", 80' not in src
