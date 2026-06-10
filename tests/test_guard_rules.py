"""Tests for the guard_rules module (Phase 5 pluggable rule packs)."""

from __future__ import annotations

import pytest

from roam.guard_rules import (
    FilePatternRule,
    RulePack,
    get_active_rules,
    load_rule_pack,
    parse_rule_pack_dict,
)

# ---- default pack ----


def test_default_pack_has_expected_rules():
    pack = RulePack.default()
    assert pack.name == "default"
    ids = {r.id for r in pack.file_patterns}
    # Built-in rules from pre-Phase-5 hard-coded list
    assert "auth_file_changed" in ids
    assert "migration_file_changed" in ids
    assert "public_api_changed" in ids
    assert "config_file_changed" in ids
    assert "test_file_changed" in ids


def test_default_pack_auth_pattern_matches():
    pack = RulePack.default()
    auth_rule = next(r for r in pack.file_patterns if r.id == "auth_file_changed")
    assert auth_rule.regex.search("src/auth/session.py")
    assert auth_rule.regex.search("lib/login.rb")
    assert not auth_rule.regex.search("src/util/foo.py")


def test_default_pack_round_trips_to_dict():
    pack = RulePack.default()
    d = pack.to_dict()
    assert d["name"] == "default"
    assert len(d["file_patterns"]) == 5


# ---- YAML parsing ----


def test_parse_minimal_pack():
    data = {
        "name": "test-pack",
        "version": "0.1",
        "file_patterns": [
            {"id": "foo", "regex": r"^src/foo\.py$", "applies_to_kinds": ["test"]},
        ],
    }
    pack = parse_rule_pack_dict(data)
    assert pack.name == "test-pack"
    assert pack.version == "0.1"
    assert len(pack.file_patterns) == 1
    rule = pack.file_patterns[0]
    assert rule.id == "foo"
    assert rule.applies_to_kinds == frozenset({"test"})
    assert rule.regex.search("src/foo.py")


def test_parse_multiple_kinds_per_rule():
    data = {
        "name": "multi",
        "file_patterns": [
            {"id": "x", "regex": r"y", "applies_to_kinds": ["test", "lint", "build"]},
        ],
    }
    pack = parse_rule_pack_dict(data)
    assert pack.file_patterns[0].applies_to_kinds == frozenset({"test", "lint", "build"})


# ---- validation errors ----


def test_missing_name_raises():
    with pytest.raises(ValueError, match="missing required field `name`"):
        parse_rule_pack_dict({"file_patterns": []})


def test_missing_regex_raises():
    with pytest.raises(ValueError, match="regex missing"):
        parse_rule_pack_dict(
            {
                "name": "x",
                "file_patterns": [{"id": "foo", "applies_to_kinds": ["test"]}],
            }
        )


def test_invalid_regex_raises():
    with pytest.raises(ValueError, match="regex is invalid"):
        parse_rule_pack_dict(
            {
                "name": "x",
                "file_patterns": [{"id": "foo", "regex": "[unclosed", "applies_to_kinds": ["test"]}],
            }
        )


def test_empty_applies_to_kinds_raises():
    with pytest.raises(ValueError, match="applies_to_kinds must be a non-empty list"):
        parse_rule_pack_dict(
            {
                "name": "x",
                "file_patterns": [{"id": "foo", "regex": "y", "applies_to_kinds": []}],
            }
        )


def test_non_dict_root_raises():
    with pytest.raises(ValueError, match="must be a mapping"):
        parse_rule_pack_dict([])  # type: ignore[arg-type]


# ---- file loading ----


def test_load_rule_pack_from_yaml_file(tmp_path):
    yml = tmp_path / "pack.yml"
    yml.write_text(
        """
name: filepack
version: 1.0
file_patterns:
  - id: rails_controller
    regex: 'app/controllers/.*_controller\\.rb$'
    applies_to_kinds: [test]
""".strip()
    )
    pack = load_rule_pack(yml)
    assert pack.name == "filepack"
    assert pack.file_patterns[0].id == "rails_controller"
    assert pack.file_patterns[0].regex.search("app/controllers/users_controller.rb")


def test_load_rule_pack_missing_file_raises(tmp_path):
    with pytest.raises(ValueError, match="failed to load rule pack"):
        load_rule_pack(tmp_path / "nope.yml")


# ---- get_active_rules ----


def test_get_active_rules_none_returns_default():
    pack = get_active_rules(None)
    assert pack.name == "default"


def test_get_active_rules_with_path_loads_pack(tmp_path):
    yml = tmp_path / "p.yml"
    yml.write_text(
        """
name: custom
file_patterns:
  - {id: x, regex: y, applies_to_kinds: [test]}
""".strip()
    )
    pack = get_active_rules(yml)
    assert pack.name == "custom"


# ---- integration with build_verification_contract ----


def test_custom_pack_overrides_default_rules():
    """A custom pack with no rules → no required checks even on auth files."""
    from roam.verification_contract import build_verification_contract

    empty_pack = RulePack(name="empty", version="1.0", file_patterns=())
    graph = {"commands": [{"id": "test.x", "command": "x", "kind": "test"}]}
    contract = build_verification_contract(
        changed_files=["src/auth/session.py"],  # would trigger default's auth rule
        command_graph=graph,
        rule_pack=empty_pack,
    )
    # No matching rule → no required checks
    assert contract["required"] == []


def test_custom_pack_with_new_rule_triggers_required():
    """A custom pack with its own rule fires correctly."""
    from roam.verification_contract import build_verification_contract

    pack = RulePack(
        name="custom",
        version="1.0",
        file_patterns=(
            FilePatternRule(
                id="frontend_changed",
                regex=__import__("re").compile(r"^frontend/.*\.tsx?$"),
                applies_to_kinds=frozenset({"test"}),
            ),
        ),
    )
    graph = {"commands": [{"id": "test.x", "command": "x", "kind": "test"}]}
    contract = build_verification_contract(
        changed_files=["frontend/login.tsx"],
        command_graph=graph,
        rule_pack=pack,
    )
    assert len(contract["required"]) == 1
    assert contract["required"][0]["reason"] == "frontend_changed"


# ---- Phase 6: rule pack inheritance ----


def test_extends_default_keeps_all_default_rules():
    """A pack that extends default with no new rules == default."""
    pack = parse_rule_pack_dict(
        {
            "name": "extension",
            "extends": "default",
            "file_patterns": [],
        }
    )
    default_ids = {r.id for r in RulePack.default().file_patterns}
    extension_ids = {r.id for r in pack.file_patterns}
    assert extension_ids == default_ids


def test_extends_adds_new_rule_to_default():
    """Custom rule appends to inherited rules."""
    pack = parse_rule_pack_dict(
        {
            "name": "ext",
            "extends": "default",
            "file_patterns": [
                {"id": "rust_changed", "regex": r"\.rs$", "applies_to_kinds": ["test"]},
            ],
        }
    )
    ids = {r.id for r in pack.file_patterns}
    assert "rust_changed" in ids
    # Default rules still present
    assert "auth_file_changed" in ids
    assert "migration_file_changed" in ids


def test_extends_overrides_rule_with_same_id():
    """Custom rule with an existing id REPLACES the inherited one."""
    pack = parse_rule_pack_dict(
        {
            "name": "stricter",
            "extends": "default",
            "file_patterns": [
                {
                    "id": "auth_file_changed",
                    "regex": r"^src/auth/.*$",  # tighter than default
                    "applies_to_kinds": ["test", "lint", "build"],  # more kinds
                },
            ],
        }
    )
    auth_rule = next(r for r in pack.file_patterns if r.id == "auth_file_changed")
    assert auth_rule.applies_to_kinds == frozenset({"test", "lint", "build"})
    # Other defaults still inherited
    ids = {r.id for r in pack.file_patterns}
    assert "migration_file_changed" in ids


def test_extends_unknown_pack_raises():
    with pytest.raises(ValueError, match="no such built-in pack"):
        parse_rule_pack_dict(
            {
                "name": "x",
                "extends": "nonexistent",
                "file_patterns": [],
            }
        )
