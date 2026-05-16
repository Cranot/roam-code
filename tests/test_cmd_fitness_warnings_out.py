"""W1051 — Pattern 2 (silent fallback) tests for `_load_rules`.

Drives the `warnings_out` accumulator plumbed in W1051 through the
fitness-loader. Mirrors the W706 / W1009 / W1019c disciplines: every
silent-fallback path surfaces a structured warning when an accumulator
is supplied; when no accumulator is supplied, behaviour is
byte-identical to pre-W1051 (silent empty list).

Cross-links:
- W706 — canonical ``warnings_out`` plumb-through for
  ``_load_ignore_findings_file``.
- W1019c — sibling migration for ``cmd_budget._load_budgets``.
- W1018 / W1019 — YAML-loader consolidation: file-read + parse +
  root-type check delegated to
  ``roam.commands._yaml_loader.load_yaml_with_warnings``.
- ``(internal memo)`` — the survey + rationale.
- CLAUDE.md "Six systemic anti-patterns" / Pattern 2 "Silent fallback".
"""

from __future__ import annotations

import json as _json
from pathlib import Path

from roam.commands.cmd_fitness import _load_rules

# ---------------------------------------------------------------------------
# _load_rules — direct loader behaviour
# ---------------------------------------------------------------------------


def _seed_roam_dir(tmp_path: Path) -> Path:
    """Create .roam/ subdir so _load_rules finds the canonical path."""
    (tmp_path / ".roam").mkdir(parents=True, exist_ok=True)
    return tmp_path


def test_load_missing_file_no_warning(tmp_path: Path) -> None:
    """Absent file is the default state — never warn (would spam every run)."""
    _seed_roam_dir(tmp_path)
    warnings_out: list[str] = []
    rules = _load_rules(tmp_path, warnings_out=warnings_out)
    assert rules == []
    assert warnings_out == []


def test_load_valid_yaml_no_warning(tmp_path: Path) -> None:
    """Happy path: well-formed file, no warnings emitted, all rules returned."""
    _seed_roam_dir(tmp_path)
    body = (
        "rules:\n"
        '  - name: "No cycles"\n'
        "    type: metric\n"
        "    metric: cycles\n"
        "    max: 0\n"
        '  - name: "Health min"\n'
        "    type: metric\n"
        "    metric: health_score\n"
        "    min: 60\n"
    )
    p = tmp_path / ".roam" / "fitness.yaml"
    p.write_text(body, encoding="utf-8")
    warnings_out: list[str] = []
    rules = _load_rules(tmp_path, warnings_out=warnings_out)
    assert warnings_out == []
    assert len(rules) == 2
    assert rules[0]["name"] == "No cycles"
    assert rules[0]["metric"] == "cycles"
    assert rules[1]["metric"] == "health_score"


def test_load_yml_extension_walks(tmp_path: Path) -> None:
    """The loader walks .yaml then .yml — confirm .yml is picked up."""
    _seed_roam_dir(tmp_path)
    body = 'rules:\n  - name: "No cycles"\n    type: metric\n    metric: cycles\n    max: 0\n'
    p = tmp_path / ".roam" / "fitness.yml"
    p.write_text(body, encoding="utf-8")
    warnings_out: list[str] = []
    rules = _load_rules(tmp_path, warnings_out=warnings_out)
    assert warnings_out == []
    assert len(rules) == 1
    assert rules[0]["name"] == "No cycles"


def test_load_malformed_yaml_root_warns(tmp_path: Path) -> None:
    """Root is a list — caller must see the shape problem."""
    _seed_roam_dir(tmp_path)
    p = tmp_path / ".roam" / "fitness.yaml"
    p.write_text("- just\n- a\n- list\n", encoding="utf-8")
    warnings_out: list[str] = []
    rules = _load_rules(tmp_path, warnings_out=warnings_out)
    assert rules == []
    assert len(warnings_out) == 1
    msg = warnings_out[0]
    assert "fitness" in msg
    assert "expected a mapping" in msg


def test_load_missing_rules_key_warns(tmp_path: Path) -> None:
    """Top-level dict without `rules:` — surface the missing key, not empty silence."""
    _seed_roam_dir(tmp_path)
    p = tmp_path / ".roam" / "fitness.yaml"
    p.write_text('version: "1"\nnotes: "no rules here"\n', encoding="utf-8")
    warnings_out: list[str] = []
    rules = _load_rules(tmp_path, warnings_out=warnings_out)
    assert rules == []
    assert len(warnings_out) == 1
    assert "no `rules:` key" in warnings_out[0]


def test_load_rules_not_a_list_warns(tmp_path: Path) -> None:
    """`rules:` set to a scalar — surface the type mismatch."""
    _seed_roam_dir(tmp_path)
    p = tmp_path / ".roam" / "fitness.yaml"
    p.write_text("rules: not-a-list\n", encoding="utf-8")
    warnings_out: list[str] = []
    rules = _load_rules(tmp_path, warnings_out=warnings_out)
    assert rules == []
    assert len(warnings_out) == 1
    assert "expected a list" in warnings_out[0]


def test_load_non_dict_entry_warns_and_skips(tmp_path: Path) -> None:
    """An entry that's a scalar (typo) surfaces the index + type and is skipped."""
    _seed_roam_dir(tmp_path)
    p = tmp_path / ".roam" / "fitness.yaml"
    p.write_text(
        'rules:\n  - just-a-string\n  - name: "No cycles"\n    type: metric\n    metric: cycles\n    max: 0\n',
        encoding="utf-8",
    )
    warnings_out: list[str] = []
    rules = _load_rules(tmp_path, warnings_out=warnings_out)
    assert len(rules) == 1
    assert rules[0]["name"] == "No cycles"
    assert len(warnings_out) == 1
    assert "rules[0]" in warnings_out[0]


def test_load_warnings_out_none_is_byte_identical_silent(tmp_path: Path) -> None:
    """When the caller doesn't pass an accumulator, behaviour is silent (pre-W1051)."""
    _seed_roam_dir(tmp_path)
    p = tmp_path / ".roam" / "fitness.yaml"
    p.write_text("- not a mapping\n", encoding="utf-8")
    # Should not raise, should not print, should return []
    rules = _load_rules(tmp_path)
    assert rules == []


def test_load_warnings_out_none_byte_identical_happy_path(tmp_path: Path) -> None:
    """Happy path with no accumulator returns the exact same rules list as with one."""
    _seed_roam_dir(tmp_path)
    body = 'rules:\n  - name: "No cycles"\n    type: metric\n    metric: cycles\n    max: 0\n'
    p = tmp_path / ".roam" / "fitness.yaml"
    p.write_text(body, encoding="utf-8")

    silent = _load_rules(tmp_path)
    warnings_out: list[str] = []
    instrumented = _load_rules(tmp_path, warnings_out=warnings_out)
    assert silent == instrumented
    assert warnings_out == []


def test_load_no_pyyaml_falls_back_cleanly(
    tmp_path: Path,
    no_pyyaml: None,
) -> None:
    """If PyYAML import fails, JSON-shaped file still loads cleanly with no warnings."""
    _seed_roam_dir(tmp_path)
    p = tmp_path / ".roam" / "fitness.yaml"
    p.write_text(
        _json.dumps(
            {
                "rules": [
                    {"name": "No cycles", "type": "metric", "metric": "cycles", "max": 0},
                ]
            }
        ),
        encoding="utf-8",
    )
    warnings_out: list[str] = []
    rules = _load_rules(tmp_path, warnings_out=warnings_out)
    assert warnings_out == []
    assert len(rules) == 1
    assert rules[0]["name"] == "No cycles"
