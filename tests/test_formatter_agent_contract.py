"""Tests for ``_derive_agent_contract`` LAW 4 humanizer (W13.4).

These tests pin the humanized behavior of the auto-derived
``agent_contract.facts`` list. Before W13.4 the auto-derive produced
abstract ``"key: value"`` strings like ``"critical: 5"``. That violates
LAW 4 (CLAUDE.md): facts must anchor on concrete nouns, not abstract
key:value pairs. After W13.4 the same input produces
``"5 critical findings"`` — number first, label humanized, generic
``"findings"`` noun anchor.

Regression coverage:

- humanizer produces ``"<N> <label> findings"`` for count-noun keys
- measurement-suffix keys (``health_score``, ``total_lines``) keep
  ``"<label> <N>"`` order
- state metadata keys (``verdict``, ``state``, ``partial_success``,
  ``deprecation_warning``) NEVER appear in facts
- ``_definition`` / ``_distribution`` suffix keys NEVER appear in facts
- explicit ``agent_contract`` kwarg still wins over auto-derive (regression
  for the W9.2 merge fix)
- verdict is always the FIRST fact entry when present (LAW 3 — operation
  order = section order, verdict-first)
- non-numeric values (dict / list / nested) never produce facts
"""

from __future__ import annotations

from roam.output.formatter import json_envelope


# ---------------------------------------------------------------------------
# Core humanizer behavior
# ---------------------------------------------------------------------------


def test_auto_derive_humanizes_count_keys():
    """``{"critical": 5}`` -> ``"5 critical findings"`` (count-noun path)."""
    env = json_envelope(
        "scan",
        summary={"verdict": "Found issues", "critical": 5, "warning": 12, "info": 3},
    )
    facts = env["agent_contract"]["facts"]
    assert "5 critical findings" in facts
    assert "12 warning findings" in facts
    assert "3 info findings" in facts


def test_auto_derive_measurement_suffix_keeps_label_first():
    """Keys whose LAST underscore-segment is a measurement word
    (``score``, ``count``, ``total``, ``size``, ``ratio``, ...) keep the
    ``"label value"`` order — they NAME a measurement, they aren't a
    noun to be counted. Keys whose last segment is a regular noun
    (``total_lines``, ``critical``) take the count-noun ``"N label
    findings"`` form."""
    env = json_envelope(
        "health",
        summary={
            "verdict": "Healthy 90/100",
            "health_score": 90,
            "symbol_count": 217,
            "error_ratio": 5,
        },
    )
    facts = env["agent_contract"]["facts"]
    assert any("health score 90" in f for f in facts)
    assert any("symbol count 217" in f for f in facts)
    assert any("error ratio 5" in f for f in facts)


def test_auto_derive_skips_non_numeric_keys():
    """Non-numeric summary values (strings, dicts, lists) never become facts —
    they aren't auto-summarizable."""
    env = json_envelope(
        "complex",
        summary={
            "verdict": "Done",
            "label": "some text",
            "nested": {"a": 1},
            "tags": ["x", "y"],
            "count": 7,
        },
    )
    facts = env["agent_contract"]["facts"]
    # "some text" / dict / list never show up
    assert all("some text" not in f for f in facts)
    assert all("nested" not in f for f in facts)
    assert all("tags" not in f for f in facts)
    # The numeric count does
    assert any("count 7" in f for f in facts)


def test_auto_derive_skips_partial_success_state():
    """``state``, ``partial_success``, ``deprecation_warning`` stay in
    ``summary`` but never pollute ``agent_contract.facts``."""
    env = json_envelope(
        "audit",
        summary={
            "verdict": "Partial",
            "state": "not_initialized",
            "partial_success": True,
            "deprecation_warning": "use foo instead",
            "errors_found": 3,
        },
    )
    facts = env["agent_contract"]["facts"]
    # State metadata absent from facts (no "state:", "partial_success:" leakage)
    assert all("state" not in f or "errors" in f for f in facts)
    assert all("partial_success" not in f for f in facts)
    assert all("deprecation_warning" not in f for f in facts)
    # But they stay in summary for full-envelope consumers
    assert env["summary"]["state"] == "not_initialized"
    assert env["summary"]["partial_success"] is True


def test_auto_derive_skips_definition_and_distribution_suffix_keys():
    """``<metric>_definition`` and ``<metric>_distribution`` are full prose /
    nested dicts by convention — they never compress to a useful fact."""
    env = json_envelope(
        "metrics",
        summary={
            "verdict": "OK",
            "caller_metric_definition": "raw_edge_rows",
            "kind_distribution": {"function": 100, "class": 20},
            "total": 7,
        },
    )
    facts = env["agent_contract"]["facts"]
    assert all("definition" not in f for f in facts)
    assert all("distribution" not in f for f in facts)
    # total still shows
    assert any("total 7" in f for f in facts)


def test_explicit_agent_contract_still_wins_over_auto():
    """W9.2 regression: caller-supplied ``agent_contract={"facts": [...]}``
    overrides the auto-derived facts. W13.4 must NOT change this — the
    humanizer is friendlier auto-derive, not stricter merge logic."""
    env = json_envelope(
        "custom",
        summary={"verdict": "x", "critical": 5},
        agent_contract={"facts": ["explicit fact"]},
    )
    facts = env["agent_contract"]["facts"]
    assert facts == ["explicit fact"]
    # Auto-derived "5 critical findings" must NOT be merged in
    assert "5 critical findings" not in facts


def test_facts_first_entry_is_verdict_when_present():
    """LAW 3 (CLAUDE.md): operation order = section order. When ``verdict``
    is present, it must be ``facts[0]`` so agents that read only the first
    fact still get the actionable conclusion."""
    env = json_envelope(
        "preflight",
        summary={
            "critical": 5,
            "warning": 12,
            "verdict": "BLOCK: 5 critical issues",
        },
    )
    facts = env["agent_contract"]["facts"]
    assert facts
    assert facts[0].startswith("BLOCK:")


def test_auto_derive_skips_bool_values():
    """Booleans are filtered (isinstance check) — they aren't ``int`` even
    though Python's type hierarchy says they are.

    W17.3: ``items`` is now in the concrete-plural-terminal list, so it
    renders as ``"4 items"`` (no ``findings`` suffix). Use a key whose
    terminal token is NOT pre-pluralised to exercise the default
    count-noun path.
    """
    env = json_envelope(
        "flags",
        summary={"verdict": "ok", "ready": True, "broken": False, "critical": 4},
    )
    facts = env["agent_contract"]["facts"]
    # No "ready 1" / "broken 0" leakage
    assert all("ready" not in f for f in facts)
    assert all("broken" not in f for f in facts)
    assert any("4 critical findings" in f for f in facts)
