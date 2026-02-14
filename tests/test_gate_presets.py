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

import pytest

from roam.commands.gate_presets import (
    ALL_PRESETS,
    GatePreset,
    GateRule,
    get_preset,
    detect_preset,
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
            assert len(preset.detect_files) >= 1, (
                f"Preset {preset.name!r} has no detect_files"
            )


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
            assert len(rule.include_patterns) >= 1, (
                f"Rule {rule.name!r} in python preset has no include_patterns"
            )
