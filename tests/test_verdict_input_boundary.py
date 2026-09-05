"""A saved proof is untrusted input: malformed evidence must not earn a pass."""

from __future__ import annotations

import json

import pytest
from click.testing import CliRunner

from roam.cli import cli


def _invoke(text):
    return CliRunner().invoke(cli, ["--json", "verdict", "--bundle", "-"], input=text)


@pytest.mark.parametrize(
    "text",
    [
        "[]",
        "null",
        "42",
        '"proof"',
        '{"verification_contract": {}, "review_evidence": []}',
        '{"verification_contract": {}, "orchestration_contract": false}',
        '{"verification_contract": {}, "change_set_unanalyzable": false}',
        '{"verification_contract": [], "changed_files": []}',
        '{"verification_contract": {"required": [null]}}',
        '{"verification_contract": {"required": null}}',
        '{"verification_contract": {}, "orchestration_contract": {"obligations": false}}',
        '{"verification_contract": {"required": [{"command": []}]}}',
        '{"verification_contract": {"required": [{}]}, "executed_checks": [{"status": "pass"}]}',
        '{"verification_contract": {"required": [{"command": ""}]}, "executed_checks": [{"command": "", "status": "pass"}]}',
        '{"verification_contract": {}, "executed_checks": [{"command": null, "status": "pass"}]}',
        '{"body": {"tests_run": [{"status": "pass"}]}}',
        '{"verification_contract": {}, "executed_checks": ["pass"]}',
        '{"verification_contract": {}, "executed_checks": [{"command": [], "status": "pass"}]}',
        '{"verification_contract": {}, "executed_checks": [{"command": "pytest", "status": []}]}',
        '{"verification_contract": {}, "risk": []}',
        '{"verification_contract": {}, "ledger": {"verified": "false"}}',
        '{"verification_contract": {}, "scope_findings": [null]}',
        '{"verification_contract": {}, "review_evidence": {"1b_plan_critique": {"status": []}}}',
        '{"body": []}',
        '{"bundle": false}',
        '{"body": {"tests_run": ["pytest"]}}',
        '{"body": {"tests_run": [{"name": [], "status": "pass"}]}}',
        '{"body": {"review_evidence": false}}',
        '{"verification_contract": {"_meta": {"rule_pack": []}}}',
        '{"verification_contract": {}, "changed_files": "README.md"}',
        '{"verification_contract": {}, "change_set_unanalyzable": "failed", "change_set_unanalyzable": null}',
        '{"verification_contract": {}, "review_evidence": {"1b_plan_critique": {"status": "rejected", "status": "declared_accepted"}}}',
        '{"verification_contract": {}, "extra": NaN}',
        '{"verification_contract": {}, "extra": Infinity}',
        '{"verification_contract": {}, "extra": -Infinity}',
        '{"verification_contract": {}, "extra": 1e999}',
    ],
)
def test_invalid_evidence_is_a_structured_refusal(text):
    result = _invoke(text)
    assert result.exit_code == 2, result.output
    data = json.loads(result.output)
    assert data["summary"]["partial_success"] is True
    assert data["summary"]["error_code"] == "bundle_parse_error"
    assert data["error"]["code"] == "bundle_parse_error"
    assert "Traceback" not in result.output


@pytest.mark.parametrize("wrapper", [None, "body", "bundle"])
def test_valid_legacy_and_empty_optional_fields_still_work(wrapper):
    payload = {
        "verification_contract": {"required": []},
        "changed_files": [],
        "review_evidence": None,
        "orchestration_contract": None,
    }
    result = _invoke(json.dumps({wrapper: payload} if wrapper else payload))
    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["verdict"]["value"] == "pass"


@pytest.mark.parametrize("field", ["command", "name", "test"])
def test_legacy_named_check_aliases_still_satisfy_requirements(field):
    result = _invoke(
        json.dumps(
            {
                "body": {
                    "verification_contract": {"required": [{"command": "pytest"}]},
                    "tests_run": [{field: "pytest", "status": "pass"}],
                    "changed_files": [],
                }
            }
        )
    )
    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["verdict"]["value"] == "pass"


def test_utf8_file_is_read_without_platform_default_codec(tmp_path, monkeypatch):
    from pathlib import Path

    path = tmp_path / "proof.json"
    path.write_text('{"verification_contract": {}, "changed_files": ["caf\u00e9.py"]}', encoding="utf-8")
    original = Path.read_text

    def checked_read(self, *args, **kwargs):
        if self == path:
            assert kwargs.get("encoding") == "utf-8"
        return original(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", checked_read)
    result = CliRunner().invoke(cli, ["--json", "verdict", "--bundle", str(path)])
    assert result.exit_code == 0, result.output


@pytest.mark.parametrize("failure", [PermissionError("denied"), UnicodeDecodeError("utf-8", b"\xff", 0, 1, "bad byte")])
def test_unreadable_bundle_is_structured(monkeypatch, failure):
    def fail(_):
        raise failure

    monkeypatch.setattr("roam.commands.cmd_verdict._load_bundle", fail)
    result = CliRunner().invoke(cli, ["--json", "verdict", "--bundle", "proof.json"])
    assert result.exit_code == 2, result.output
    data = json.loads(result.output)
    assert data["summary"]["partial_success"] is True
    assert data["error"]["code"] in {"bundle_load_failed", "bundle_parse_error"}


def test_unknown_review_status_uses_one_registered_error_code():
    from roam.guard_errors import GUARD_ERROR_CODES, exit_code_for_guard_error

    result = _invoke(
        json.dumps(
            {
                "verification_contract": {},
                "review_evidence": {"1b_plan_critique": {"status": "future_status"}},
            }
        )
    )
    data = json.loads(result.output)
    code = data["summary"]["error_code"]
    assert code == data["error"]["code"] == "unmapped_review_status"
    assert code in GUARD_ERROR_CODES
    assert result.exit_code == exit_code_for_guard_error(code) == 2
