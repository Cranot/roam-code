"""Tests for quality rule profiles with inheritance (#138)."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest
from click.testing import CliRunner


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def tmp_project(tmp_path: Path):
    """Create a minimal project directory with .roam dir and index DB."""
    (tmp_path / ".roam").mkdir()
    db_path = tmp_path / ".roam" / "index.db"
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS files (
            id INTEGER PRIMARY KEY, path TEXT NOT NULL,
            loc INTEGER, file_role TEXT DEFAULT 'source', language TEXT
        );
        CREATE TABLE IF NOT EXISTS symbols (
            id INTEGER PRIMARY KEY, file_id INTEGER, name TEXT,
            qualified_name TEXT, kind TEXT, line_start INTEGER,
            line_end INTEGER, is_exported INTEGER DEFAULT 0,
            cognitive_complexity REAL, parent_id INTEGER,
            FOREIGN KEY(file_id) REFERENCES files(id)
        );
        CREATE TABLE IF NOT EXISTS edges (
            id INTEGER PRIMARY KEY, source_id INTEGER,
            target_id INTEGER, kind TEXT DEFAULT 'calls',
            FOREIGN KEY(source_id) REFERENCES symbols(id),
            FOREIGN KEY(target_id) REFERENCES symbols(id)
        );
        CREATE TABLE IF NOT EXISTS graph_metrics (
            symbol_id INTEGER PRIMARY KEY,
            in_degree INTEGER DEFAULT 0,
            out_degree INTEGER DEFAULT 0,
            betweenness REAL DEFAULT 0,
            pagerank REAL DEFAULT 0
        );
    """)
    conn.execute("INSERT INTO files (id, path, loc) VALUES (1, 'src/app.py', 100)")
    conn.execute(
        "INSERT INTO symbols (id, file_id, name, kind, cognitive_complexity) "
        "VALUES (1, 1, 'main', 'function', 5)"
    )
    conn.commit()
    conn.close()
    return tmp_path


# ---------------------------------------------------------------------------
# BUILTIN_PROFILES registry
# ---------------------------------------------------------------------------


class TestBuiltinProfiles:
    """Tests for the BUILTIN_PROFILES dict in builtin.py."""

    def test_profiles_exist(self):
        from roam.rules.builtin import BUILTIN_PROFILES
        assert isinstance(BUILTIN_PROFILES, dict)
        expected = {"default", "strict-security", "ai-code-review", "legacy-maintenance", "minimal"}
        assert expected == set(BUILTIN_PROFILES.keys())

    def test_all_profiles_have_description(self):
        from roam.rules.builtin import BUILTIN_PROFILES
        for name, prof in BUILTIN_PROFILES.items():
            assert "description" in prof, f"Profile {name} missing description"
            assert prof["description"], f"Profile {name} has empty description"

    def test_all_profiles_have_rules(self):
        from roam.rules.builtin import BUILTIN_PROFILES
        for name, prof in BUILTIN_PROFILES.items():
            assert "rules" in prof, f"Profile {name} missing rules"
            assert isinstance(prof["rules"], dict)

    def test_default_profile_has_all_rules(self):
        from roam.rules.builtin import BUILTIN_PROFILES, BUILTIN_RULES
        default = BUILTIN_PROFILES["default"]
        for rule in BUILTIN_RULES:
            assert rule.id in default["rules"], (
                f"Default profile missing rule: {rule.id}"
            )

    def test_strict_security_extends_default(self):
        from roam.rules.builtin import BUILTIN_PROFILES
        prof = BUILTIN_PROFILES["strict-security"]
        assert prof.get("extends") == "default"

    def test_ai_code_review_extends_default(self):
        from roam.rules.builtin import BUILTIN_PROFILES
        prof = BUILTIN_PROFILES["ai-code-review"]
        assert prof.get("extends") == "default"

    def test_legacy_maintenance_extends_default(self):
        from roam.rules.builtin import BUILTIN_PROFILES
        prof = BUILTIN_PROFILES["legacy-maintenance"]
        assert prof.get("extends") == "default"

    def test_minimal_does_not_extend(self):
        from roam.rules.builtin import BUILTIN_PROFILES
        prof = BUILTIN_PROFILES["minimal"]
        assert prof.get("extends") is None


# ---------------------------------------------------------------------------
# resolve_profile
# ---------------------------------------------------------------------------


class TestResolveProfile:
    """Tests for the resolve_profile function."""

    def test_resolve_default(self):
        from roam.rules.builtin import resolve_profile, BUILTIN_RULES
        overrides = resolve_profile("default")
        assert isinstance(overrides, list)
        # Default should have an override for every built-in rule
        ids = {ov["id"] for ov in overrides}
        for rule in BUILTIN_RULES:
            assert rule.id in ids

    def test_resolve_strict_security_inherits_default(self):
        from roam.rules.builtin import resolve_profile
        overrides = resolve_profile("strict-security")
        ov_map = {ov["id"]: ov for ov in overrides}
        # Strict security overrides max-fan-out threshold to 10
        assert ov_map["max-fan-out"]["threshold"] == 10
        # But inherits rules it doesn't override
        assert "no-circular-imports" in ov_map

    def test_resolve_legacy_relaxed_thresholds(self):
        from roam.rules.builtin import resolve_profile
        overrides = resolve_profile("legacy-maintenance")
        ov_map = {ov["id"]: ov for ov in overrides}
        assert ov_map["max-file-complexity"]["threshold"] == 80
        assert ov_map["max-file-length"]["threshold"] == 800
        assert ov_map["no-god-classes"]["threshold"] == 30

    def test_resolve_minimal_disables_most_rules(self):
        from roam.rules.builtin import resolve_profile, BUILTIN_RULES
        overrides = resolve_profile("minimal")
        ov_map = {ov["id"]: ov for ov in overrides}
        # Minimal only enables no-circular-imports and layer-violation
        enabled_ids = {
            ov["id"] for ov in overrides
            if ov.get("enabled", True) is True
        }
        assert "no-circular-imports" in enabled_ids
        assert "layer-violation" in enabled_ids
        # Others should be disabled
        disabled_ids = {
            ov["id"] for ov in overrides
            if ov.get("enabled") is False
        }
        assert "max-fan-out" in disabled_ids

    def test_resolve_unknown_profile_raises(self):
        from roam.rules.builtin import resolve_profile
        with pytest.raises(ValueError, match="Unknown profile"):
            resolve_profile("nonexistent-profile")

    def test_resolve_ai_code_review_thresholds(self):
        from roam.rules.builtin import resolve_profile
        overrides = resolve_profile("ai-code-review")
        ov_map = {ov["id"]: ov for ov in overrides}
        assert ov_map["max-file-length"]["threshold"] == 300
        assert ov_map["max-fan-out"]["threshold"] == 10


# ---------------------------------------------------------------------------
# list_profiles
# ---------------------------------------------------------------------------


class TestListProfiles:
    def test_list_profiles_returns_all(self):
        from roam.rules.builtin import list_profiles, BUILTIN_PROFILES
        profiles = list_profiles()
        assert len(profiles) == len(BUILTIN_PROFILES)
        names = {p["name"] for p in profiles}
        assert names == set(BUILTIN_PROFILES.keys())

    def test_list_profiles_structure(self):
        from roam.rules.builtin import list_profiles
        for p in list_profiles():
            assert "name" in p
            assert "description" in p
            assert "extends" in p
            assert "rule_count" in p


# ---------------------------------------------------------------------------
# CLI: --profile flag
# ---------------------------------------------------------------------------


class TestProfileCLI:
    def test_profile_flag_strict_security(self, tmp_project, monkeypatch):
        from roam.cli import cli
        monkeypatch.chdir(tmp_project)
        runner = CliRunner()
        result = runner.invoke(
            cli, ["check-rules", "--profile", "strict-security"],
            catch_exceptions=False,
        )
        assert result.exit_code in (0, 1)
        assert "VERDICT" in result.output

    def test_profile_flag_minimal(self, tmp_project, monkeypatch):
        from roam.cli import cli
        monkeypatch.chdir(tmp_project)
        runner = CliRunner()
        result = runner.invoke(
            cli, ["check-rules", "--profile", "minimal"],
            catch_exceptions=False,
        )
        assert result.exit_code in (0, 1)
        assert "VERDICT" in result.output

    def test_profile_flag_unknown_fails(self, tmp_project, monkeypatch):
        from roam.cli import cli
        monkeypatch.chdir(tmp_project)
        runner = CliRunner()
        result = runner.invoke(
            cli, ["check-rules", "--profile", "nonexistent"],
            catch_exceptions=False,
        )
        assert result.exit_code != 0

    def test_list_profiles_flag(self, monkeypatch, tmp_path):
        from roam.cli import cli
        monkeypatch.chdir(tmp_path)
        runner = CliRunner()
        result = runner.invoke(
            cli, ["check-rules", "--list-profiles"],
            catch_exceptions=False,
        )
        assert result.exit_code == 0
        assert "strict-security" in result.output
        assert "minimal" in result.output

    def test_list_profiles_json(self, monkeypatch, tmp_path):
        from roam.cli import cli
        monkeypatch.chdir(tmp_path)
        runner = CliRunner()
        result = runner.invoke(
            cli, ["--json", "check-rules", "--list-profiles"],
            catch_exceptions=False,
        )
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert "profiles" in data
        names = {p["name"] for p in data["profiles"]}
        assert "strict-security" in names


# ---------------------------------------------------------------------------
# YAML config: profile: key
# ---------------------------------------------------------------------------


class TestYAMLProfileConfig:
    def test_load_config_profile_from_yaml(self, tmp_path):
        from roam.commands.cmd_check_rules import _load_config_profile
        cfg = tmp_path / ".roam-rules.yml"
        cfg.write_text("profile: strict-security\n", encoding="utf-8")
        result = _load_config_profile(str(cfg))
        assert result == "strict-security"

    def test_load_config_profile_none_when_missing(self, tmp_path):
        from roam.commands.cmd_check_rules import _load_config_profile
        cfg = tmp_path / ".roam-rules.yml"
        cfg.write_text("rules:\n  - id: max-fan-out\n    threshold: 5\n", encoding="utf-8")
        result = _load_config_profile(str(cfg))
        assert result is None

    def test_load_config_profile_none_when_no_file(self):
        from roam.commands.cmd_check_rules import _load_config_profile
        result = _load_config_profile("/nonexistent/path/rules.yml")
        assert result is None

    def test_profile_from_yaml_used_by_cli(self, tmp_project, monkeypatch):
        from roam.cli import cli
        monkeypatch.chdir(tmp_project)
        # Write a config with profile: minimal
        cfg = tmp_project / ".roam-rules.yml"
        cfg.write_text("profile: minimal\n", encoding="utf-8")
        runner = CliRunner()
        result = runner.invoke(cli, ["check-rules"], catch_exceptions=False)
        assert result.exit_code in (0, 1)
        assert "VERDICT" in result.output

    def test_cli_profile_overrides_yaml_profile(self, tmp_project, monkeypatch):
        from roam.cli import cli
        monkeypatch.chdir(tmp_project)
        # Config says minimal but CLI says strict-security
        cfg = tmp_project / ".roam-rules.yml"
        cfg.write_text("profile: minimal\n", encoding="utf-8")
        runner = CliRunner()
        result = runner.invoke(
            cli, ["check-rules", "--profile", "strict-security"],
            catch_exceptions=False,
        )
        # Should use strict-security (more rules than minimal)
        assert result.exit_code in (0, 1)
        assert "VERDICT" in result.output
