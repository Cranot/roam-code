"""Tests for `roam guard-rules` introspection subcommands."""

from __future__ import annotations

import json

from click.testing import CliRunner

from roam.cli import cli


def test_guard_rules_show_text_mode():
    runner = CliRunner()
    result = runner.invoke(cli, ["guard-rules", "show"])
    assert result.exit_code == 0
    # Default pack name appears.
    assert "default" in result.output
    # YAML structure visible.
    assert "file_patterns" in result.output


def test_guard_rules_show_json_envelope():
    runner = CliRunner()
    result = runner.invoke(cli, ["--json", "guard-rules", "show"])
    payload = json.loads(result.output)
    assert payload["command"] == "guard-rules-show"
    assert "pack" in payload
    assert payload["pack"]["name"] == "default"
    assert len(payload["pack"]["file_patterns"]) > 0


def test_guard_rules_validate_valid_pack(tmp_path):
    p = tmp_path / "p.yml"
    p.write_text(
        """
name: my-pack
version: 1.0
file_patterns:
  - id: x
    regex: '^src/.*\\.py$'
    applies_to_kinds: [test]
""".strip()
    )
    runner = CliRunner()
    result = runner.invoke(cli, ["guard-rules", "validate", str(p)])
    assert result.exit_code == 0
    assert "VALID" in result.output


def test_guard_rules_validate_invalid_pack(tmp_path):
    p = tmp_path / "bad.yml"
    p.write_text("name: x\nfile_patterns: [{id: foo, regex: '[unclosed', applies_to_kinds: [test]}]")
    runner = CliRunner()
    result = runner.invoke(cli, ["guard-rules", "validate", str(p)])
    assert result.exit_code == 2


def test_guard_rules_validate_json_envelope_invalid(tmp_path):
    p = tmp_path / "bad.yml"
    p.write_text("not even yaml: [")
    runner = CliRunner()
    result = runner.invoke(cli, ["--json", "guard-rules", "validate", str(p)])
    assert result.exit_code == 2
    payload = json.loads(result.output)
    assert payload["summary"]["verdict"] == "invalid"


def test_guard_rules_test_matches_default_auth_rule():
    runner = CliRunner()
    result = runner.invoke(cli, ["--json", "guard-rules", "test", "src/auth/session.py"])
    assert result.exit_code == 0
    payload = json.loads(result.output)
    matched_ids = {m["id"] for m in payload["matches"]}
    assert "auth_file_changed" in matched_ids


def test_guard_rules_test_no_match_when_none_apply():
    runner = CliRunner()
    result = runner.invoke(cli, ["--json", "guard-rules", "test", "random/foo.notrecognized"])
    payload = json.loads(result.output)
    assert payload["matches"] == []
    assert payload["summary"]["match_count"] == 0


def test_guard_rules_test_text_mode_no_match():
    runner = CliRunner()
    result = runner.invoke(cli, ["guard-rules", "test", "random/x.notrecognized"])
    assert result.exit_code == 0
    assert "NO MATCH" in result.output


def test_guard_rules_test_text_mode_with_match():
    runner = CliRunner()
    result = runner.invoke(cli, ["guard-rules", "test", "src/auth/session.py"])
    assert result.exit_code == 0
    assert "MATCHES" in result.output
    assert "auth_file_changed" in result.output


def test_guard_rules_test_with_custom_pack(tmp_path):
    p = tmp_path / "p.yml"
    p.write_text(
        """
name: custom
version: 1.0
file_patterns:
  - id: my_special
    regex: '^foo/bar/baz\\.py$'
    applies_to_kinds: [test]
""".strip()
    )
    runner = CliRunner()
    result = runner.invoke(
        cli,
        [
            "--json",
            "guard-rules",
            "test",
            "foo/bar/baz.py",
            "--rules",
            str(p),
        ],
    )
    payload = json.loads(result.output)
    assert any(m["id"] == "my_special" for m in payload["matches"])


def test_guard_rules_test_requires_arg_or_from_bundle():
    """No file_path AND no --from-bundle → missing_input error."""
    runner = CliRunner()
    result = runner.invoke(cli, ["--json", "guard-rules", "test"])
    assert result.exit_code == 2
    payload = json.loads(result.output)
    assert payload["summary"]["verdict"] == "missing_input"


def test_guard_rules_test_from_bundle_no_bundle(tmp_path, monkeypatch):
    """--from-bundle in a repo with no pr-bundle → no_bundle envelope."""
    monkeypatch.chdir(tmp_path)
    runner = CliRunner()
    result = runner.invoke(cli, ["--json", "guard-rules", "test", "--from-bundle"])
    assert result.exit_code == 2
    payload = json.loads(result.output)
    assert payload["summary"]["verdict"] == "no_bundle"


def test_guard_rules_test_from_bundle_with_bundle(tmp_path, monkeypatch):
    """--from-bundle iterates every changed_file."""
    from tests.helpers import make_pr_bundle

    monkeypatch.chdir(tmp_path)
    (tmp_path / ".roam" / "pr-bundles").mkdir(parents=True)
    bundle_path = tmp_path / ".roam" / "pr-bundles" / "main.json"
    bundle = make_pr_bundle(
        affected=[
            {"name": "refresh_token", "file": "src/auth/session.py"},
            {"name": "render", "file": "src/views/home.py"},
        ],
    )
    bundle_path.write_text(json.dumps(bundle))
    runner = CliRunner()
    result = runner.invoke(
        cli,
        [
            "--json",
            "guard-rules",
            "test",
            "--from-bundle",
            "--bundle",
            str(bundle_path),
        ],
    )
    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["summary"]["file_count"] >= 2
    files_in_output = [e["file"] for e in payload["per_file"]]
    assert "src/auth/session.py" in files_in_output
    # auth file should have at least one match in the default pack.
    auth_entry = next(e for e in payload["per_file"] if e["file"] == "src/auth/session.py")
    assert auth_entry["match_count"] >= 1
