"""Regression tripwire for auth-gaps, not a precision proof.

The labelled pair locks an unprotected route and the detector's auth-group
suppression against refactors; it does not claim a precision number.
"""

from pathlib import Path

from roam.commands.cmd_auth_gaps import _analyze_route_file

FIXTURES = Path(__file__).parent / "fixtures" / "detector_eval" / "auth-gaps"


def test_auth_gap_tp_fires_and_authenticated_tn_is_clean():
    tp = FIXTURES / "tp_unprotected_route.php"
    tn = FIXTURES / "tn_authenticated_route.php"
    tp_findings, _ = _analyze_route_file(str(tp), tp.read_text())
    tn_findings, _ = _analyze_route_file(str(tn), tn.read_text())
    assert any(f["type"] == "route" for f in tp_findings)
    assert tn_findings == []
