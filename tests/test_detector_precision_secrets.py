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


def test_secret_pattern_definitions_are_suppressed(tmp_path):
    """Regex catalogues describe credential shapes; they contain no credential."""
    pattern_table = tmp_path / "redaction_patterns.ts"
    pattern_table.write_text(
        (FIXTURES / "tn_redaction_pattern_table.ts").read_text(encoding="utf-8"),
        encoding="utf-8",
    )

    assert scan_file(str(pattern_table)) == []


def test_hardcoded_credential_remains_detectable(tmp_path):
    """Conservation control: regex-definition suppression must not hide values."""
    credential = tmp_path / "hardcoded_credential.ts"
    credential.write_text(
        (FIXTURES / "tp_hardcoded_credential.ts").read_text(encoding="utf-8"),
        encoding="utf-8",
    )

    control_findings = scan_file(str(credential))
    assert control_findings, "conservation control: a hardcoded credential must remain detectable"
    assert any(finding["pattern_name"] == "Generic Secret Assignment" for finding in control_findings)
