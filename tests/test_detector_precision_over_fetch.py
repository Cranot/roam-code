"""Regression tripwire for over-fetch, not a precision proof.

The labelled pair locks the unguarded-relation finding and explicit-column
suppression against refactors; it does not claim a precision number.
"""

from pathlib import Path

from roam.commands.cmd_over_fetch import _endpoint_state_for_body

FIXTURES = Path(__file__).parent / "fixtures" / "detector_eval" / "over-fetch"


def test_over_fetch_tp_fires_and_column_guard_tn_is_clean():
    tp = FIXTURES / "tp_unguarded_relation.php"
    tn = FIXTURES / "tn_guarded_relation.php"
    tp_state, _ = _endpoint_state_for_body(tp.read_text())
    tn_state, _ = _endpoint_state_for_body(tn.read_text())
    assert tp_state == "UNGUARDED_RELATION"
    assert tn_state == "GUARDED_RELATION"
