"""Tests for framework gate presets (Phase 3).

Covers:
- ALL_PRESETS contents and count
- get_preset() lookup
- detect_preset() auto-detection from file lists
- GateRule dataclass defaults
- GatePreset structure validation
- Each preset has at least one rule
"""

from __future__ import annotations

from roam.commands.gate_presets import (
    ALL_PRESETS,
    GatePreset,
    GateRule,
    detect_preset,
    get_preset,
    load_gates_config,
)


class TestAllPresets:
    def test_all_presets_has_at_least_five(self):
        assert len(ALL_PRESETS) >= 5

    def test_all_preset_names_unique(self):
        names = [p.name for p in ALL_PRESETS]
        assert len(names) == len(set(names))

    def test_each_preset_has_rules(self):
        for preset in ALL_PRESETS:
            assert len(preset.rules) >= 1, f"Preset {preset.name!r} has no rules"

    def test_each_preset_has_detect_files(self):
        for preset in ALL_PRESETS:
            assert len(preset.detect_files) >= 1, f"Preset {preset.name!r} has no detect_files"


class TestGetPreset:
    def test_get_python_preset(self):
        p = get_preset("python")
        assert p is not None
        assert p.name == "python"
        assert len(p.rules) >= 1

    def test_get_go_preset(self):
        p = get_preset("go")
        assert p is not None
        assert p.name == "go"

    def test_get_nonexistent_preset(self):
        assert get_preset("nonexistent") is None


class TestDetectPreset:
    def test_detect_python_from_pyproject(self):
        p = detect_preset(["src/main.py", "pyproject.toml", "README.md"])
        assert p is not None
        assert p.name == "python"

    def test_detect_js_from_package_json(self):
        p = detect_preset(["src/index.js", "package.json"])
        assert p is not None
        assert p.name == "javascript"

    def test_detect_go_from_go_mod(self):
        p = detect_preset(["main.go", "go.mod"])
        assert p is not None
        assert p.name == "go"

    def test_detect_java_from_pom(self):
        p = detect_preset(["src/main/java/App.java", "pom.xml"])
        assert p is not None
        assert p.name == "java-maven"

    def test_detect_rust_from_cargo(self):
        p = detect_preset(["src/lib.rs", "Cargo.toml"])
        assert p is not None
        assert p.name == "rust"

    def test_detect_returns_none_for_no_match(self):
        assert detect_preset(["random.txt", "data.csv"]) is None

    def test_detect_uses_basename_only(self):
        """Paths with directories should still match on basename."""
        p = detect_preset(["some/deep/path/pyproject.toml"])
        assert p is not None
        assert p.name == "python"


class TestGateRuleDefaults:
    def test_default_min_test_count(self):
        rule = GateRule(name="test", description="desc")
        assert rule.min_test_count == 1

    def test_default_severity(self):
        rule = GateRule(name="test", description="desc")
        assert rule.severity == "warning"

    def test_default_patterns_empty(self):
        rule = GateRule(name="test", description="desc")
        assert rule.include_patterns == []
        assert rule.exclude_patterns == []

    def test_custom_values(self):
        rule = GateRule(
            name="critical",
            description="critical rule",
            include_patterns=["src/**/*.py"],
            exclude_patterns=["tests/**"],
            min_test_count=5,
            severity="error",
        )
        assert rule.name == "critical"
        assert rule.min_test_count == 5
        assert rule.severity == "error"
        assert rule.include_patterns == ["src/**/*.py"]


class TestGatePresetStructure:
    def test_preset_is_dataclass(self):
        p = GatePreset(name="test", description="test preset")
        assert p.name == "test"
        assert p.rules == []
        assert p.detect_files == []
        assert p.languages == []

    def test_python_preset_has_languages(self):
        p = get_preset("python")
        assert "python" in p.languages

    def test_python_preset_rules_have_patterns(self):
        p = get_preset("python")
        for rule in p.rules:
            assert len(rule.include_patterns) >= 1, f"Rule {rule.name!r} in python preset has no include_patterns"


class TestLoadGatesConfig:
    """W706-family: load_gates_config must return [] on every broken-input
    path rather than raising. Loud-fallback semantics: ambiguous => empty."""

    def test_missing_file_returns_empty(self, tmp_path):
        assert load_gates_config(str(tmp_path / "does-not-exist.yml")) == []

    def test_malformed_yaml_returns_empty(self, tmp_path):
        cfg = tmp_path / "bad.yml"
        cfg.write_text("rules:\n  - name: x\n  bad-indent-here\n", encoding="utf-8")
        assert load_gates_config(str(cfg)) == []

    def test_top_level_list_returns_empty(self, tmp_path):
        cfg = tmp_path / "list.yml"
        cfg.write_text("- name: foo\n- name: bar\n", encoding="utf-8")
        assert load_gates_config(str(cfg)) == []

    def test_missing_rules_key_returns_empty(self, tmp_path):
        cfg = tmp_path / "norules.yml"
        cfg.write_text("other_key: value\n", encoding="utf-8")
        assert load_gates_config(str(cfg)) == []

    def test_rules_not_a_list_returns_empty(self, tmp_path):
        cfg = tmp_path / "rules-dict.yml"
        cfg.write_text("rules:\n  name: foo\n", encoding="utf-8")
        assert load_gates_config(str(cfg)) == []

    def test_non_dict_rule_entries_skipped(self, tmp_path):
        cfg = tmp_path / "mixed.yml"
        cfg.write_text(
            "rules:\n  - just_a_string\n  - name: valid\n    include: ['src/**/*.py']\n",
            encoding="utf-8",
        )
        rules = load_gates_config(str(cfg))
        assert len(rules) == 1
        assert rules[0].name == "valid"

    def test_invalid_severity_falls_back_to_warning(self, tmp_path):
        cfg = tmp_path / "sev.yml"
        cfg.write_text(
            "rules:\n  - name: x\n    severity: catastrophic\n",
            encoding="utf-8",
        )
        rules = load_gates_config(str(cfg))
        assert len(rules) == 1
        assert rules[0].severity == "warning"

    def test_non_integer_min_tests_falls_back(self, tmp_path):
        cfg = tmp_path / "min.yml"
        cfg.write_text(
            "rules:\n  - name: x\n    min_tests: not-a-number\n",
            encoding="utf-8",
        )
        rules = load_gates_config(str(cfg))
        assert len(rules) == 1
        assert rules[0].min_test_count == 1

    def test_happy_path(self, tmp_path):
        cfg = tmp_path / "ok.yml"
        cfg.write_text(
            "rules:\n"
            "  - name: api\n"
            "    description: API modules\n"
            "    include: ['src/api/**/*.py']\n"
            "    exclude: ['**/__init__.py']\n"
            "    min_tests: 3\n"
            "    severity: error\n",
            encoding="utf-8",
        )
        rules = load_gates_config(str(cfg))
        assert len(rules) == 1
        r = rules[0]
        assert r.name == "api"
        assert r.min_test_count == 3
        assert r.severity == "error"
        assert r.include_patterns == ["src/api/**/*.py"]
        assert r.exclude_patterns == ["**/__init__.py"]
