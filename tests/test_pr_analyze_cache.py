"""Tests for the pr-analyze envelope cache (--cache flag)."""

from __future__ import annotations

from roam.commands.cmd_pr_analyze import (
    _cache_key,
    _cache_path,
    _load_cache,
    _save_cache,
)


def test_cache_key_stable_for_same_inputs(tmp_path):
    rules = tmp_path / "rules.yml"
    rules.write_text("rules: []\n")
    k1 = _cache_key("diff text", rules, 85, None)
    k2 = _cache_key("diff text", rules, 85, None)
    assert k1 == k2


def test_cache_key_changes_with_diff(tmp_path):
    rules = tmp_path / "rules.yml"
    rules.write_text("rules: []\n")
    k1 = _cache_key("diff a", rules, 85, None)
    k2 = _cache_key("diff b", rules, 85, None)
    assert k1 != k2


def test_cache_key_changes_with_rules_content(tmp_path):
    rules = tmp_path / "rules.yml"
    rules.write_text("rules: []\n")
    k1 = _cache_key("diff", rules, 85, None)
    rules.write_text("rules:\n  - id: x\n    pattern: import_from\n    forbidden_target_glob: y\n")
    k2 = _cache_key("diff", rules, 85, None)
    assert k1 != k2


def test_cache_key_changes_with_threshold(tmp_path):
    rules = tmp_path / "rules.yml"
    rules.write_text("rules: []\n")
    k1 = _cache_key("diff", rules, 85, None)
    k2 = _cache_key("diff", rules, 90, None)
    assert k1 != k2


def test_cache_key_changes_with_language(tmp_path):
    rules = tmp_path / "rules.yml"
    rules.write_text("rules: []\n")
    k1 = _cache_key("diff", rules, 85, "python")
    k2 = _cache_key("diff", rules, 85, "typescript")
    assert k1 != k2


def test_cache_key_handles_missing_rules_file(tmp_path):
    """Missing rules file shouldn't crash key derivation."""
    k = _cache_key("diff", tmp_path / "missing.yml", 85, None)
    assert isinstance(k, str)
    assert len(k) == 64  # sha256 hex


def test_save_and_load_cache_roundtrip(tmp_path):
    cache_dir = tmp_path / "cache"
    bundle = {"summary": {"verdict": "SAFE", "blast_radius": 12}, "extra": [1, 2, 3]}
    _save_cache(cache_dir, "abc123", bundle)
    out = _load_cache(cache_dir, "abc123")
    assert out == bundle


def test_load_cache_returns_none_on_miss(tmp_path):
    assert _load_cache(tmp_path / "cache", "nope") is None


def test_load_cache_returns_none_on_corrupted_json(tmp_path):
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    (cache_dir / "key.json").write_text("not valid json {")
    assert _load_cache(cache_dir, "key") is None


def test_cache_path_includes_key_in_filename(tmp_path):
    p = _cache_path(tmp_path / "cache", "myhash")
    assert p.name == "myhash.json"
