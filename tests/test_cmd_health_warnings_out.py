"""W1052 — Pattern 2 (silent fallback) tests for `_load_gate_config`.

Drives the `warnings_out` accumulator plumbed in W1052 through the
``.roam-gates.yml`` loader. Mirrors the W706 / W1009 / W1019c
disciplines: every silent-fallback path surfaces a structured warning
when an accumulator is supplied; when no accumulator is supplied,
behaviour is byte-identical to pre-W1052 (silent default thresholds).

``health`` is a flagship CI-gate command (W834 sealed its silent-Healthy
bug on empty corpus). The W1052 plumbing here exposes the loader's
silent-empty fallback path the same way W834 exposed the score-collapse
path.

Cross-links:
- W706 — canonical ``warnings_out`` plumb-through for
  ``_load_ignore_findings_file``.
- W1019c — sibling migration for ``cmd_budget._load_budgets``.
- W1051 — sibling migration for ``cmd_fitness._load_rules``.
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

from roam.commands.cmd_health import _load_gate_config

# ---------------------------------------------------------------------------
# _load_gate_config — direct loader behaviour
# ---------------------------------------------------------------------------


def test_load_missing_file_returns_defaults_no_warning(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Absent file is the default state — never warn (would spam every run)."""
    monkeypatch.chdir(tmp_path)
    warnings_out: list[str] = []
    cfg = _load_gate_config(warnings_out=warnings_out)
    assert cfg == {"health_min": 60}
    assert warnings_out == []


def test_load_valid_yaml_overrides_defaults_no_warning(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Happy path: well-formed file, no warnings emitted, thresholds merged."""
    monkeypatch.chdir(tmp_path)
    body = "health:\n  health_min: 75\n  complexity_max: 30\n  cycle_max: 0\n  tangle_max: 0.2\n"
    (tmp_path / ".roam-gates.yml").write_text(body, encoding="utf-8")
    warnings_out: list[str] = []
    cfg = _load_gate_config(warnings_out=warnings_out)
    assert warnings_out == []
    assert cfg["health_min"] == 75
    assert cfg["complexity_max"] == 30
    assert cfg["cycle_max"] == 0
    assert cfg["tangle_max"] == 0.2


def test_load_malformed_yaml_root_warns(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Root is a list — caller must see the shape problem."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".roam-gates.yml").write_text("- not\n- a\n- mapping\n", encoding="utf-8")
    warnings_out: list[str] = []
    cfg = _load_gate_config(warnings_out=warnings_out)
    assert cfg == {"health_min": 60}
    assert len(warnings_out) == 1
    msg = warnings_out[0]
    assert "health-gate" in msg
    assert "expected a mapping" in msg


def test_load_missing_health_key_warns(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Top-level dict without `health:` — surface the missing key, not empty silence."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".roam-gates.yml").write_text('version: "1"\nnotes: "no health here"\n', encoding="utf-8")
    warnings_out: list[str] = []
    cfg = _load_gate_config(warnings_out=warnings_out)
    assert cfg == {"health_min": 60}
    assert len(warnings_out) == 1
    assert "no `health:` key" in warnings_out[0]


def test_load_health_not_a_mapping_warns(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """`health:` set to a scalar — surface the type mismatch."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".roam-gates.yml").write_text("health: not-a-mapping\n", encoding="utf-8")
    warnings_out: list[str] = []
    cfg = _load_gate_config(warnings_out=warnings_out)
    assert cfg == {"health_min": 60}
    assert len(warnings_out) == 1
    assert "expected a mapping" in warnings_out[0]


def test_load_warnings_out_none_is_byte_identical_silent(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """When the caller doesn't pass an accumulator, behaviour is silent (pre-W1052)."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".roam-gates.yml").write_text("- not a mapping\n", encoding="utf-8")
    # Should not raise, should not print, should return defaults
    cfg = _load_gate_config()
    assert cfg == {"health_min": 60}


def test_load_warnings_out_none_byte_identical_happy_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Happy path with no accumulator returns the exact same cfg as with one."""
    monkeypatch.chdir(tmp_path)
    body = "health:\n  health_min: 80\n  complexity_max: 25\n"
    (tmp_path / ".roam-gates.yml").write_text(body, encoding="utf-8")

    silent = _load_gate_config()
    warnings_out: list[str] = []
    instrumented = _load_gate_config(warnings_out=warnings_out)
    assert silent == instrumented
    assert warnings_out == []


def test_load_no_pyyaml_falls_back_cleanly(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    no_pyyaml: None,
) -> None:
    """If PyYAML import fails, JSON-shaped file still loads cleanly with no warnings."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".roam-gates.yml").write_text(
        _json.dumps({"health": {"health_min": 75, "complexity_max": 30}}),
        encoding="utf-8",
    )
    warnings_out: list[str] = []
    cfg = _load_gate_config(warnings_out=warnings_out)
    assert warnings_out == []
    assert cfg["health_min"] == 75
    assert cfg["complexity_max"] == 30
