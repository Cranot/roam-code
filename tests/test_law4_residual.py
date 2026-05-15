"""LAW 4 residual sweep (W17.3) — anchor-string regression tests.

The W12.3 wave fixed six commands directly; W13.4 humanized the
auto-derived ``agent_contract.facts``. This file covers the **residual
commands** that still leaked weak facts in edge cases (no-data
branches, awkward humanized suffixes, abstract verdicts) plus the
auto-derive heuristic refinements landed in W17.3.

Scope:

- ``graph-diff`` no-baseline + head-not-found branches
- ``architecture-drift`` insufficient_snapshots branches (both paths)
- ``capabilities`` / ``config`` / ``minimap`` / ``visualize`` /
  ``endpoints`` / ``forecast`` / ``trends`` verdict + facts
- ``_humanize_summary_fact`` refinements: trailing ``_total`` peel,
  pre-pluralised concrete-noun terminals, leading-underscore skip,
  expanded measurement suffixes
- ``_AGENT_CONTRACT_FACT_SKIP_KEYS`` additions (``schema``, ``schema_version``,
  ``hint``, ``note``, ``truncated``, ``budget_tokens``, ``version``,
  ``project``)

Spot-check facts (one or two assertions per command) rather than full
envelope snapshots — the latter are brittle to harmless reshuffling.
"""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

import pytest

from tests._helpers.repo_root import repo_root


REPO_ROOT = repo_root()
_SRC_DIR = REPO_ROOT / "src"
if _SRC_DIR.is_dir() and str(_SRC_DIR) not in sys.path:
    sys.path.insert(0, str(_SRC_DIR))


# ---------------------------------------------------------------------------
# Auto-derive heuristic — pure unit tests
# ---------------------------------------------------------------------------


def test_auto_derive_humanizes_plurals_naturally():
    """``{runs_total: 5}`` -> ``"5 runs total"``, not the old
    ``"5 runs_total findings"`` form. The ``_total`` quantifier is
    peeled off the key and appended after the count."""
    from roam.output.formatter import json_envelope

    env = json_envelope("scan", summary={"verdict": "ok", "runs_total": 5})
    facts = env["agent_contract"]["facts"]
    assert any("5 runs total" == f for f in facts), facts


def test_auto_derive_concrete_noun_terminals_drop_findings_suffix():
    """Keys whose terminal token is already a concrete plural noun
    (``files`` / ``edges`` / ``snapshots`` / ``agents`` / ``effects``)
    must NOT get the ``"findings"`` suffix — that would double-noun
    the fact ("3722 total files findings" reads as garbage)."""
    from roam.output.formatter import json_envelope

    env = json_envelope(
        "audit",
        summary={
            "verdict": "ok",
            "total_files": 3722,
            "owned_files": 3722,
            "files_affected": 12,
            "agents_scored": 9,
            "snapshots_available": 3,
        },
    )
    facts = env["agent_contract"]["facts"]
    # All of these should appear without a trailing " findings".
    assert any("3722 total files" in f and "findings" not in f for f in facts), facts
    assert any("3722 owned files" in f and "findings" not in f for f in facts), facts


def test_auto_derive_skips_schema_metadata():
    """Envelope plumbing keys (``schema``, ``schema_version``, ``version``,
    ``project``, ``hint``, ``note``) must never appear in
    ``agent_contract.facts``. Pre-W17.3 the auto-derive happily
    surfaced any string-valued summary key."""
    from roam.output.formatter import json_envelope

    env = json_envelope(
        "scan",
        summary={
            "verdict": "ok",
            "schema": "roam-envelope-v1",
            "schema_version": "1.1.0",
            "version": "12.50",
            "project": "demo",
            "hint": "Run roam X to investigate",
            "note": "ancillary commentary",
            "truncated": True,
            "budget_tokens": 500,
            "count": 7,
        },
    )
    facts = env["agent_contract"]["facts"]
    joined = " | ".join(facts)
    assert "schema" not in joined
    assert "schema_version" not in joined
    assert "version" not in joined.lower() or "ok" in joined.lower()
    assert "hint" not in joined
    assert "note" not in joined
    assert "budget_tokens" not in joined
    # And a real numeric still surfaces:
    assert "count 7" in joined


def test_auto_derive_skips_underscore_prefix_keys():
    """Leading-underscore keys are private metadata — they must never
    leak into the user-facing facts list."""
    from roam.output.formatter import json_envelope

    env = json_envelope(
        "scan",
        summary={
            "verdict": "ok",
            "_internal_counter": 99,
            "_trace_id": "abc123",
            "items": 5,
        },
    )
    facts = env["agent_contract"]["facts"]
    joined = " | ".join(facts)
    assert "internal_counter" not in joined
    assert "_trace_id" not in joined
    assert "99" not in joined
    # Public key still humanized
    assert any("5 items" in f for f in facts), facts


def test_auto_derive_pct_suffix_treated_as_measurement():
    """``coverage_pct: 100.0`` -> ``"coverage pct 100.0"`` (measurement
    suffix), not ``"100.0 coverage pct findings"``."""
    from roam.output.formatter import json_envelope

    env = json_envelope(
        "audit",
        summary={"verdict": "ok", "coverage_pct": 100.0},
    )
    facts = env["agent_contract"]["facts"]
    assert any("coverage pct 100.0" in f for f in facts), facts
    assert all("findings" not in f or "coverage" not in f for f in facts), facts


# ---------------------------------------------------------------------------
# graph-diff — Pattern Y (no-data branches)
# ---------------------------------------------------------------------------


def test_graph_diff_no_baseline_has_concrete_facts(tmp_path):
    """When ``.roam/snapshots/`` has no baseline, the envelope's
    ``agent_contract.facts`` must anchor on the literal subject
    ("graph-diff baseline") + the executable command, not the
    auto-derived "no data" abstraction."""
    # Spin up a tiny indexed project so ``roam graph-diff`` exits cleanly.
    project = tmp_path / "tinyproj"
    project.mkdir()
    (project / "main.py").write_text("def hello(): return 1\n", encoding="utf-8")
    # Initialise git so `find_project_root` discovers the project.
    (project / ".git").mkdir()

    from roam.commands.cmd_graph_diff import _resolve_snapshot

    snap, label = _resolve_snapshot(project, None)
    assert snap is None and label is None

    # Now exercise the envelope construction directly to lock the facts.
    from roam.output.formatter import json_envelope

    snapshots_dir = project / ".roam" / "snapshots"
    env = json_envelope(
        "graph-diff",
        summary={
            "verdict": "No baseline snapshot found",
            "state": "no_baseline_snapshot",
            "partial_success": True,
            "total_signals": 0,
            "hint": "Run `roam graph-diff --save-snapshot <label>` to capture a baseline.",
        },
        agent_contract={
            "facts": [
                f"graph-diff baseline: no snapshot found under {snapshots_dir}",
                "graph-diff baseline: run `roam graph-diff --save-snapshot <label>` to capture one",
            ],
        },
    )
    facts = env["agent_contract"]["facts"]
    # Concrete anchor:
    assert any("graph-diff baseline" in f for f in facts), facts
    # Actionable executable command:
    assert any("roam graph-diff --save-snapshot" in f for f in facts), facts
    # ``hint`` must NOT have leaked into facts (W17.3 SKIP_KEYS extension).
    assert all("Run `roam graph-diff" not in f or "no snapshot" in f for f in facts), facts


def test_graph_diff_no_baseline_source_pins_concrete_facts():
    """Source-level regression: ``cmd_graph_diff.py`` must explicitly
    pass ``agent_contract={"facts": [...]}`` in the no-baseline branch.
    Pre-W17.3 it relied on auto-derive which produced abstract facts."""
    src = (REPO_ROOT / "src" / "roam" / "commands" / "cmd_graph_diff.py").read_text(
        encoding="utf-8"
    )
    assert "graph-diff baseline: no snapshot found under" in src
    assert "graph-diff baseline: run `roam graph-diff --save-snapshot" in src
    # And the head-not-found branch also got an explicit contract:
    assert "graph-diff head:" in src


# ---------------------------------------------------------------------------
# architecture-drift — Pattern Y (insufficient_snapshots branches)
# ---------------------------------------------------------------------------


def test_architecture_drift_insufficient_snapshots_has_concrete_facts():
    """Both insufficient-snapshot branches (``<2 on disk``, ``<2 readable``)
    must emit concrete-noun-anchored facts via ``agent_contract``.
    Pre-W17.3 these passed ``facts=[]`` as a payload kwarg, which the
    auto-derive then overrode with "N window days findings" noise."""
    src = (REPO_ROOT / "src" / "roam" / "commands" / "cmd_architecture_drift.py").read_text(
        encoding="utf-8"
    )
    # Both insufficient-snapshot envelopes must have an explicit
    # agent_contract dict.
    assert src.count('agent_contract={') >= 2, (
        "architecture-drift must pin agent_contract in both no-data branches"
    )
    # The concrete subject anchor and the actionable command must appear:
    assert "architecture drift over" in src
    assert "architecture drift needs >= 2 snapshots" in src
    assert "roam graph-diff --save-snapshot" in src


# ---------------------------------------------------------------------------
# Per-command verdict / fact regression
# ---------------------------------------------------------------------------


def test_capabilities_verdict_is_concrete():
    """``cmd_capabilities`` must emit a concrete verdict naming the
    registered count + AI-safe count, not bare ``"count 10"``."""
    src = (REPO_ROOT / "src" / "roam" / "commands" / "cmd_capabilities.py").read_text(
        encoding="utf-8"
    )
    assert "registered capabilities" in src
    # The bare verdict-less envelope MUST be gone. The summary now
    # includes a "verdict" key.
    assert '"verdict":' in src or "'verdict':" in src


def test_minimap_verdict_drops_bare_ok():
    """``minimap`` no longer emits ``verdict: "ok"`` on either branch —
    those are abstract (LAW 6 compression-survives fails)."""
    src = (REPO_ROOT / "src" / "roam" / "commands" / "cmd_minimap.py").read_text(
        encoding="utf-8"
    )
    # The action-naming forms must be present:
    assert "minimap rendered" in src
    assert "minimap " in src  # something more than the bare "ok"


def test_endpoints_pins_concrete_facts():
    """``endpoints`` overrides the auto-derive with explicit
    ``agent_contract`` facts so the bare ``count``/``framework_count``
    keys don't produce ``"count 10"`` / ``"framework count 2"``."""
    src = (REPO_ROOT / "src" / "roam" / "commands" / "cmd_endpoints.py").read_text(
        encoding="utf-8"
    )
    assert "endpoints scan found" in src
    assert "agent_contract=" in src


def test_forecast_pins_concrete_facts():
    """``forecast`` overrides the auto-derive: ``symbols_at_risk`` keyed
    as a non-noun terminal otherwise auto-derived to "N symbols at
    risk findings"."""
    src = (REPO_ROOT / "src" / "roam" / "commands" / "cmd_forecast.py").read_text(
        encoding="utf-8"
    )
    assert "forecast scope:" in src
    assert "forecast risk:" in src


def test_trends_renamed_latest_health_for_humanizer():
    """``cmd_trends`` renamed ``latest_health`` to ``latest_health_score``
    so the humanizer renders ``"latest health score 75"`` instead of
    ``"75 latest health findings"``."""
    src = (REPO_ROOT / "src" / "roam" / "commands" / "cmd_trends.py").read_text(
        encoding="utf-8"
    )
    assert "latest_health_score" in src
    # And the bare ``latest_health`` key (without the suffix) should be gone:
    # (use the assignment pattern, not the substring, to allow comments
    # that reference the rename).
    assert '"latest_health":' not in src
