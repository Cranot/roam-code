"""Regression tripwire for the secrets detector, not a precision proof.

The labelled pair locks one advertised secret shape and its nearest
environment-backed suppression against refactors; it does not claim a
precision number or corpus-level accuracy.
"""

from pathlib import Path

from roam.commands.cmd_secrets import _line_is_allowlisted, scan_file

FIXTURES = Path(__file__).parent / "fixtures" / "detector_eval" / "secrets"


def _scan_fixture(tmp_path: Path, fixture_name: str) -> list[dict]:
    target = tmp_path / fixture_name
    target.write_text((FIXTURES / fixture_name).read_text(encoding="utf-8"), encoding="utf-8")
    return scan_file(str(target))


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


def test_redaction_connection_rule_is_suppressed_and_literal_dsn_fires(tmp_path):
    """A pattern/replacement pair is metadata; a literal credential is not."""
    assert _scan_fixture(tmp_path, "tn_redaction_connection_rule.ts") == []

    findings = _scan_fixture(tmp_path, "tp_connection_string.ts")
    assert any(finding["pattern_name"] == "Database Connection String" for finding in findings)


def test_path_values_are_suppressed_and_opaque_slash_token_fires(tmp_path):
    """Absolute/interpolated paths differ from opaque slash-bearing tokens."""
    assert _scan_fixture(tmp_path, "tn_token_paths.py") == []

    findings = _scan_fixture(tmp_path, "tp_token_with_slash.py")
    assert any(finding["pattern_name"] == "Generic Secret Assignment" for finding in findings)


def test_documented_assignment_is_suppressed_and_adjacent_code_fires(tmp_path):
    """Quoted scanner syntax is prose; an adjacent code assignment is code."""
    assert _scan_fixture(tmp_path, "tn_documented_assignment.py") == []

    findings = _scan_fixture(tmp_path, "tp_code_next_to_docstring.py")
    assert findings
    assert {finding["line"] for finding in findings} == {6}


def test_explicit_fixture_marker_suppresses_but_fixture_prose_does_not(tmp_path):
    """Only the exact allowlist marker, not adjacent fixture prose, suppresses."""
    assert _scan_fixture(tmp_path, "tn_allowlisted_hex_fixture.ts") == []

    findings = _scan_fixture(tmp_path, "tp_unannotated_hex_fixture.ts")
    assert any(finding["pattern_name"] == "Generic Secret Assignment" for finding in findings)


def test_explicit_fixture_marker_uses_the_established_exact_syntax():
    """The pre-push scanner's three end-of-line comment forms stay aligned."""
    assert _line_is_allowlisted('TOKEN = "invented-value"  # secretsallow')  # secretsallow
    assert _line_is_allowlisted('token: "invented-value"  // SECRETSALLOW')  # secretsallow
    assert _line_is_allowlisted('token = "invented-value"  ; secretsallow')  # secretsallow
    assert not _line_is_allowlisted('TOKEN = "invented-value"  # secretsallow later')  # secretsallow


def test_low_entropy_tags_are_suppressed_and_mixed_entropy_token_fires(tmp_path):
    """Short semantic tags differ from long opaque credential values."""
    assert _scan_fixture(tmp_path, "tn_low_entropy_tags.py") == []

    findings = _scan_fixture(tmp_path, "tp_mixed_entropy_token.py")
    assert any(finding["pattern_name"] == "Generic Secret Assignment" for finding in findings)
