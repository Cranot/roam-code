"""Canonical envelope introspection (2026-06-02) — single source of truth so
diagnostic commands don't re-derive 'is this L1 / probe-empty' and re-drift
into the agent_contract.facts vs prefetched_facts bug."""

from __future__ import annotations

from roam.plan.envelope_introspect import introspect, probe_families


def test_l1_with_substantive_families_not_empty():
    env = {
        "summary": {"artifact_type": "l1_probe", "procedure": "symbol_defined_where", "classifier_confidence": 0.85},
        "plan": {
            "prefetched_facts": {"symbol_definitions": [{"file": "x"}], "symbol_definitions_definition": "annotation"}
        },
    }
    r = introspect(env)
    assert r["label"] == "l1_probe"
    assert r["procedure"] == "symbol_defined_where"
    assert r["classifier_confidence"] == 0.85
    assert r["probe_families"] == ["symbol_definitions"]  # annotation excluded
    assert r["probe_empty"] is False


def test_l1_with_only_annotation_keys_is_empty():
    env = {
        "summary": {"artifact_type": "l1_probe"},
        "plan": {"prefetched_facts": {"foo_definition": "x", "bar_unavailable": "y"}},
    }
    assert introspect(env)["probe_empty"] is True


def test_non_l1_never_empty():
    env = {"summary": {"artifact_type": "full"}, "plan": {"prefetched_facts": {}}}
    assert introspect(env)["probe_empty"] is False


def test_inner_artifact_shape_no_summary():
    # The inner artifact env has no `summary`/`agent_contract` — must still read plan.
    env = {
        "plan": {
            "procedure": "structural_coupling",
            "classifier_confidence": 0.9,
            "prefetched_facts": {"structural_imports": [1]},
        }
    }
    r = introspect(env)
    assert r["procedure"] == "structural_coupling"
    assert r["classifier_confidence"] == 0.9
    assert r["probe_families"] == ["structural_imports"]


def test_probe_families_direct_dict():
    env = {"prefetched_facts": {"callers": [1], "callers_definition": "x"}}
    assert probe_families(env) == ["callers"]


def test_garbage_input():
    assert introspect(None)["probe_empty"] is False
    assert probe_families("not a dict") == []
