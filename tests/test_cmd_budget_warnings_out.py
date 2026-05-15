"""W1019c — Pattern 2 (silent fallback) tests for `_load_budgets`.

Drives the `warnings_out` accumulator plumbed in W1019c through the
budget-loader. Mirrors the W706 / W1009 disciplines: every
silent-fallback path surfaces a structured warning when an accumulator
is supplied; when no accumulator is supplied, behaviour is
byte-identical to pre-W1019c (silent empty list).

Cross-links:
- W706 — canonical ``warnings_out`` plumb-through for
  ``_load_ignore_findings_file``.
- W1018 / W1019 — YAML-loader consolidation: file-read + parse +
  root-type check delegated to
  ``roam.commands._yaml_loader.load_yaml_with_warnings``.
- ``(internal memo)`` — the survey + rationale.
- CLAUDE.md "Six systemic anti-patterns" / Pattern 2 "Silent fallback".
"""

from __future__ import annotations

import json as _json
from pathlib import Path

import pytest

from roam.commands.cmd_budget import _load_budgets


# ---------------------------------------------------------------------------
# _load_budgets — direct loader behaviour
# ---------------------------------------------------------------------------


def test_load_missing_file_no_warning(tmp_path: Path) -> None:
    """Absent file is the default state — never warn (would spam every run)."""
    warnings_out: list[str] = []
    budgets = _load_budgets(tmp_path / "missing.yml", warnings_out=warnings_out)
    assert budgets == []
    assert warnings_out == []


def test_load_none_path_no_warning() -> None:
    """``None`` path short-circuits to ``[]`` without touching the helper."""
    warnings_out: list[str] = []
    budgets = _load_budgets(None, warnings_out=warnings_out)
    assert budgets == []
    assert warnings_out == []


def test_load_valid_yaml_no_warning(tmp_path: Path) -> None:
    """Happy path: well-formed file, no warnings emitted, all budgets returned."""
    body = (
        'version: "1"\n'
        "budgets:\n"
        '  - name: "Health score floor"\n'
        "    metric: health_score\n"
        "    max_decrease: 5\n"
        '  - name: "No new cycles"\n'
        "    metric: cycles\n"
        "    max_increase: 0\n"
    )
    p = tmp_path / "budget.yaml"
    p.write_text(body, encoding="utf-8")
    warnings_out: list[str] = []
    budgets = _load_budgets(p, warnings_out=warnings_out)
    assert warnings_out == []
    assert len(budgets) == 2
    assert budgets[0]["name"] == "Health score floor"
    assert budgets[0]["metric"] == "health_score"
    assert budgets[1]["metric"] == "cycles"


def test_load_malformed_yaml_root_warns(tmp_path: Path) -> None:
    """Root is a list — caller must see the shape problem."""
    p = tmp_path / "budget.yaml"
    p.write_text("- just\n- a\n- list\n", encoding="utf-8")
    warnings_out: list[str] = []
    budgets = _load_budgets(p, warnings_out=warnings_out)
    assert budgets == []
    assert len(warnings_out) == 1
    msg = warnings_out[0]
    assert "budget" in msg
    assert "expected a mapping" in msg


def test_load_missing_budgets_key_warns(tmp_path: Path) -> None:
    """Top-level dict without `budgets:` — surface the missing key, not empty silence."""
    p = tmp_path / "budget.yaml"
    p.write_text('version: "1"\nnotes: "no budgets here"\n', encoding="utf-8")
    warnings_out: list[str] = []
    budgets = _load_budgets(p, warnings_out=warnings_out)
    assert budgets == []
    assert len(warnings_out) == 1
    assert "no `budgets:` key" in warnings_out[0]


def test_load_budgets_not_a_list_warns(tmp_path: Path) -> None:
    """`budgets:` set to a scalar — surface the type mismatch."""
    p = tmp_path / "budget.yaml"
    p.write_text("budgets: not-a-list\n", encoding="utf-8")
    warnings_out: list[str] = []
    budgets = _load_budgets(p, warnings_out=warnings_out)
    assert budgets == []
    assert len(warnings_out) == 1
    assert "expected a list" in warnings_out[0]


def test_load_non_dict_entry_warns_and_skips(tmp_path: Path) -> None:
    """An entry that's a scalar (typo) surfaces the index + type and is skipped."""
    p = tmp_path / "budget.yaml"
    p.write_text(
        "budgets:\n"
        "  - just-a-string\n"
        '  - name: "No new cycles"\n'
        "    metric: cycles\n"
        "    max_increase: 0\n",
        encoding="utf-8",
    )
    warnings_out: list[str] = []
    budgets = _load_budgets(p, warnings_out=warnings_out)
    assert len(budgets) == 1
    assert budgets[0]["name"] == "No new cycles"
    assert len(warnings_out) == 1
    assert "budgets[0]" in warnings_out[0]


def test_load_warnings_out_none_is_byte_identical_silent(tmp_path: Path) -> None:
    """When the caller doesn't pass an accumulator, behaviour is silent (pre-W1019c)."""
    p = tmp_path / "budget.yaml"
    p.write_text("- not a mapping\n", encoding="utf-8")
    # Should not raise, should not print, should return []
    budgets = _load_budgets(p)
    assert budgets == []


def test_load_warnings_out_none_byte_identical_happy_path(tmp_path: Path) -> None:
    """Happy path with no accumulator returns the exact same budgets list as with one."""
    body = (
        "budgets:\n"
        '  - name: "Health score floor"\n'
        "    metric: health_score\n"
        "    max_decrease: 5\n"
    )
    p = tmp_path / "budget.yaml"
    p.write_text(body, encoding="utf-8")

    silent = _load_budgets(p)
    warnings_out: list[str] = []
    instrumented = _load_budgets(p, warnings_out=warnings_out)
    assert silent == instrumented
    assert warnings_out == []


def test_load_no_pyyaml_falls_back_cleanly(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """If PyYAML import fails, JSON-shaped file still loads cleanly with no warnings."""
    import builtins

    real_import = builtins.__import__

    def _fake_import(name, *args, **kwargs):
        if name == "yaml":
            raise ImportError("simulated PyYAML absence")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", _fake_import)
    p = tmp_path / "budget.yaml"
    p.write_text(
        _json.dumps(
            {
                "budgets": [
                    {"name": "Health score floor", "metric": "health_score", "max_decrease": 5},
                ]
            }
        ),
        encoding="utf-8",
    )
    warnings_out: list[str] = []
    budgets = _load_budgets(p, warnings_out=warnings_out)
    assert warnings_out == []
    assert len(budgets) == 1
    assert budgets[0]["name"] == "Health score floor"
