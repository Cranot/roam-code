"""Regression tripwire for the secrets detector, not a precision proof.

The labelled pair locks one advertised secret shape and its nearest
environment-backed suppression against refactors; it does not claim a
precision number or corpus-level accuracy.
"""

from pathlib import Path

from roam.commands.cmd_secrets import scan_file

FIXTURES = Path(__file__).parent / "fixtures" / "detector_eval" / "secrets"


def test_secrets_tp_fires_and_environment_tn_is_suppressed(tmp_path):
    tp = tmp_path / "tp_hardcoded_key.py"
    tn = tmp_path / "tn_environment_key.py"
    tp.write_text((FIXTURES / tp.name).read_text(), encoding="utf-8")
    tn.write_text((FIXTURES / tn.name).read_text(), encoding="utf-8")

    assert scan_file(str(tp))
    assert scan_file(str(tn)) == []
