"""Tests for ``roam agent-opt`` — the envelope-contract super-optimizer (P1).

Covers: the three starter detectors (pure-function unit level), the family
registry + closed-enum validation, the CLI surface (--list-tasks /
--list-detectors / --only), empty-input envelope discipline (Pattern 1),
agent_contract LAW-4 anchoring + CONSTRAINT-12 executable next_commands, the
A4 persistence wiring, and an MCP-level check of the internal chain.

Module-cache hygiene (AGENTS.md § Testing): this file imports
``roam.mcp_server`` at function scope to read ``_TOOL_METADATA`` but NEVER pops
it from ``sys.modules`` — no restoring fixture is needed.
"""

from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

import pytest
from click.testing import CliRunner

sys.path.insert(0, str(Path(__file__).parent))
from test_law4_lint import _is_concrete_anchored  # noqa: E402 — reuse canonical anchor logic

from roam import agent_opt as ao  # noqa: E402
from roam.cli import cli  # noqa: E402


def _parse_env(output: str) -> dict:
    return json.loads(output[output.index("{") :])


# ---------------------------------------------------------------------------
# Detector unit tests (pure functions — deterministic, no live harvest)
# ---------------------------------------------------------------------------
class TestDeclarativeToolDescription:
    def test_flags_declarative_openers_only(self):
        descs = {
            "roam_bad1": "This command shows callers.",
            "roam_bad2": "Returns the dependency list.",
            "roam_bad3": "Provides a health score.",
            "roam_good1": "Detect cycles in the import graph.",
            "roam_good2": "Pre-change safety gate: blast radius + tests.",  # identity-first noun
            "roam_good3": "Score the agent on a 0-100 composite.",
        }
        out = ao.detect_declarative_tool_description(descs)
        flagged = {f["subject"] for f in out}
        assert flagged == {"roam_bad1", "roam_bad2", "roam_bad3"}
        for f in out:
            assert f["task_id"] == "tool-description-declarative"
            assert f["detected_way"] == "declarative-opening"
            assert f["suggested_way"] == "imperative-identity"
            assert f["confidence_basis"] == "heuristic"

    def test_empty_or_blank_description_skipped(self):
        assert ao.detect_declarative_tool_description({"a": "", "b": "   "}) == []

    def test_live_description_surface_is_law2_clean(self):
        """roam's own ``description=`` fields are imperative/identity-first
        (the description-rewrite waves cleaned them). agent-opt confirms it +
        guards against regression — empty output is the signal here."""
        descs = ao.iter_tool_descriptions("full")
        out = ao.detect_declarative_tool_description(descs)
        assert out == [], f"declarative tool descriptions regressed: {[f['subject'] for f in out]}"


class TestWeakVerdict:
    @pytest.mark.parametrize(
        "verdict,weakness",
        [
            ("", "empty"),
            ("   ", "empty"),
            ("completed", "boilerplate"),
            ("Done", "boilerplate"),
            ("ok", "boilerplate"),
            ("see details", "boilerplate"),
            ("See the full report", "deferral"),
            ("Analysis complete:", "dangling-colon"),
            ("all good", "too-terse"),
            (None, "missing"),
        ],
    )
    def test_weak_verdicts_flagged(self, verdict, weakness):
        out = ao.detect_weak_verdict([("cmd", {"summary": {"verdict": verdict}})])
        assert len(out) == 1
        assert out[0]["evidence"]["weakness"] == weakness
        assert out[0]["confidence"] == "high"
        assert out[0]["confidence_basis"] == "structural"

    @pytest.mark.parametrize(
        "verdict",
        [
            "Healthy 82/100 with 3 cycles",
            "5 critical findings across 12 files",
            "0 agent-contract improvements found — 229 tool descriptions scanned",
            "3 cycles",  # short but carries a number
        ],
    )
    def test_strong_verdicts_pass(self, verdict):
        assert ao.detect_weak_verdict([("cmd", {"summary": {"verdict": verdict}})]) == []

    def test_verdict_weakness_pure_unit(self):
        assert ao._verdict_weakness("Healthy 82/100") is None
        assert ao._verdict_weakness("ok") == "boilerplate"
        assert ao._verdict_weakness("") == "empty"
        assert ao._verdict_weakness(None) == "missing"


class TestMissingNextCommand:
    def test_findings_but_no_next_commands(self):
        env = {"summary": {"verdict": "5 issues found", "count": 5}, "findings": [1, 2]}
        out = ao.detect_missing_next_command([("cmd", env)], known_commands={"health"})
        assert len(out) == 1
        assert out[0]["detected_way"] == "no-next-command"
        assert out[0]["confidence"] == "medium"

    def test_no_findings_no_next_commands_is_ok(self):
        env = {"summary": {"verdict": "0 issues found", "count": 0}}
        assert ao.detect_missing_next_command([("cmd", env)], known_commands={"health"}) == []

    def test_non_roam_next_command_flagged(self):
        env = {
            "summary": {"count": 1},
            "findings": [1],
            "agent_contract": {"next_commands": ["Run `roam debt` to see effort"]},
        }
        out = ao.detect_missing_next_command([("cmd", env)], known_commands={"debt"})
        assert len(out) == 1
        assert "not a runnable" in out[0]["reason"]

    def test_unresolved_subcommand_flagged(self):
        env = {
            "summary": {"count": 1},
            "findings": [1],
            "agent_contract": {"next_commands": ["roam frobnicate --all"]},
        }
        out = ao.detect_missing_next_command([("cmd", env)], known_commands={"health", "debt"})
        assert len(out) == 1
        assert out[0]["evidence"]["unresolved_subcommand"] == "frobnicate"
        assert out[0]["confidence"] == "high"

    def test_good_next_commands_pass(self):
        env = {
            "summary": {"count": 1},
            "findings": [1],
            "agent_contract": {"next_commands": ["roam impact handleSave", "roam context src/x.py"]},
        }
        assert ao.detect_missing_next_command([("cmd", env)], known_commands={"impact", "context"}) == []


# ---------------------------------------------------------------------------
# Second slice: silent-degraded-state / large-envelope-no-handle /
# abstract-fact / parameter-name-drift
# ---------------------------------------------------------------------------
class TestSilentDegradedState:
    def test_failure_count_without_partial_success_flagged(self):
        env = {"summary": {"verdict": "SAFE", "detectors_failed": 2}}
        out = ao.detect_silent_degraded_state([("c", env)])
        assert len(out) == 1 and out[0]["detected_way"] == "silent-fallback"

    def test_warnings_without_partial_success_flagged(self):
        env = {"summary": {"verdict": "x"}, "warnings_out": ["bad config"]}
        assert len(ao.detect_silent_degraded_state([("c", env)])) == 1

    def test_partial_success_true_not_flagged(self):
        env = {"summary": {"verdict": "x", "detectors_failed": 2, "partial_success": True}}
        assert ao.detect_silent_degraded_state([("c", env)]) == []

    def test_clean_absent_state_not_flagged(self):
        # `state: not_initialized` is a CORRECT Pattern-2 disclosure, not a failure.
        env = {"summary": {"verdict": "chain not initialized", "state": "not_initialized"}}
        assert ao.detect_silent_degraded_state([("c", env)]) == []

    def test_clean_envelope_not_flagged(self):
        assert ao.detect_silent_degraded_state([("c", {"summary": {"verdict": "Healthy 90/100"}})]) == []


class TestLargeEnvelopeNoHandle:
    def test_large_inline_payload_flagged(self):
        env = {"summary": {"verdict": "x"}, "blob": "A" * 90_000}
        out = ao.detect_large_envelope_no_handle([("c", env)])
        assert len(out) == 1 and out[0]["evidence"]["bytes"] > 80_000

    def test_large_with_handle_not_flagged(self):
        env = {"summary": {"verdict": "x"}, "blob": "A" * 90_000, "handle_id": "h1"}
        assert ao.detect_large_envelope_no_handle([("c", env)]) == []

    def test_small_envelope_not_flagged(self):
        assert ao.detect_large_envelope_no_handle([("c", {"summary": {"verdict": "x"}})]) == []


class TestAbstractFact:
    def test_abstract_terminal_flagged_not_anchored(self):
        env = {"agent_contract": {"facts": ["5 critical", "3 findings"]}}
        out = ao.detect_abstract_fact([("c", env)])
        assert [f["evidence"]["fact"] for f in out] == ["5 critical"]

    def test_no_facts_not_flagged(self):
        assert ao.detect_abstract_fact([("c", {"summary": {"verdict": "x"}})]) == []

    def test_anchor_parity_with_law4_lint(self):
        """Production anchor sets MUST equal the lint's, or agent-opt and the
        CI lint would diverge on what 'weak fact' means (Pattern 3a)."""
        import test_law4_lint as t

        assert ao._CONCRETE_NOUN_ANCHORS == t._CONCRETE_NOUN_ANCHORS
        assert ao._ANALYTICAL_VERBS == t._ANALYTICAL_VERBS
        assert ao._MEASUREMENT_SUFFIXES == t._MEASUREMENT_SUFFIXES

    @pytest.mark.parametrize(
        "fact",
        [
            "5 critical findings",
            "3722 total files",
            "useThemeClasses classified hot",
            "Run roam preflight handleSave before editing",
            "health score 75",
            "225 registered capabilities (213 AI-safe)",
            "5 critical",
            "ok",
            "see details",
            "no data",
            "",
        ],
    )
    def test_predicate_agrees_with_law4(self, fact):
        import test_law4_lint as t

        assert ao._fact_is_abstract(fact) == (not t._is_concrete_anchored(fact)), fact


class TestParameterNameDrift:
    def test_legacy_param_flagged_low_confidence(self):
        out = ao.detect_parameter_name_drift([("roam_x", ("file", "root"))], legacy_map={"file": "path"})
        assert len(out) == 1
        assert out[0]["confidence"] == "low"  # advisory / grandfathered — not a hard gate
        assert out[0]["evidence"]["canonical"] == "path"

    def test_canonical_param_not_flagged(self):
        out = ao.detect_parameter_name_drift([("roam_x", ("path", "root"))], legacy_map={"file": "path"})
        assert out == []

    def test_legacy_map_derived_from_param_aliases(self):
        assert ao._legacy_param_map().get("file") == "path"

    def test_discover_tool_params_finds_wrappers(self):
        tps = ao.discover_tool_params()
        assert len(tps) >= 50, f"AST discovery found {len(tps)} wrappers; expected >=50"
        assert "roam_agent_opt" in {n for n, _ in tps}


# ---------------------------------------------------------------------------
# Family registry + closed-enum validation
# ---------------------------------------------------------------------------
class TestRegistry:
    def test_all_seven_detectors_registered(self):
        names = {d["name"] for d in ao.list_agent_opt_detectors()}
        assert names == {
            "detect_declarative_tool_description",
            "detect_weak_verdict",
            "detect_missing_next_command",
            "detect_silent_degraded_state",
            "detect_large_envelope_no_handle",
            "detect_abstract_fact",
            "detect_parameter_name_drift",
        }

    def test_entries_have_valid_closed_enums(self):
        from roam.catalog.detectors import QUERY_COST_HIGH, QUERY_COST_LOW, QUERY_COST_MEDIUM
        from roam.db.findings import (
            CONFIDENCE_HEURISTIC,
            CONFIDENCE_RUNTIME,
            CONFIDENCE_STATIC_ANALYSIS,
            CONFIDENCE_STRUCTURAL,
        )

        bases = {CONFIDENCE_HEURISTIC, CONFIDENCE_STRUCTURAL, CONFIDENCE_STATIC_ANALYSIS, CONFIDENCE_RUNTIME}
        costs = {QUERY_COST_LOW, QUERY_COST_MEDIUM, QUERY_COST_HIGH}
        for d in ao.list_agent_opt_detectors():
            assert d["confidence_basis"] in bases
            assert d["query_cost"] in costs
            assert d["family"] == "agent-opt"

    def test_bad_enum_rejected_at_decoration(self):
        with pytest.raises(ValueError):

            @ao.agent_opt_detector(task_id="weak-verdict", confidence_basis="probabilistic")
            def _bad(envelopes):  # pragma: no cover
                return []

    def test_decorator_rejects_non_family_task(self):
        with pytest.raises(ValueError):

            @ao.agent_opt_detector(task_id="sorting")  # algorithm-family, not agent-opt
            def _bad(x):  # pragma: no cover
                return []

    def test_tasks_tagged_in_shared_catalog(self):
        from roam.catalog.tasks import CATALOG

        ids = ao.agent_opt_task_ids()
        assert set(ids) == {
            "tool-description-declarative",
            "weak-verdict",
            "missing-next-command",
            "silent-degraded-state",
            "large-envelope-no-handle",
            "abstract-fact",
            "parameter-name-drift",
        }
        for tid in ids:
            assert CATALOG[tid]["family"] == "agent-opt"


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------
class TestRunAgentOpt:
    def test_injected_sources_deterministic(self):
        descs = {"roam_x": "This shows X."}
        envs = [("cmd", {"summary": {"verdict": "ok"}, "findings": [1]})]
        # Inject tool_params=[] so the param-tier runs without the live AST scan.
        findings, meta = ao.run_agent_opt(tool_descriptions=descs, envelopes=envs, tool_params=[])
        by_task = {f["task_id"] for f in findings}
        # Only the three tasks with violations in this crafted input fire; the
        # other four run cleanly (0 findings) — all 7 detectors execute.
        assert by_task == {"tool-description-declarative", "weak-verdict", "missing-next-command"}
        assert meta["detectors_executed"] == 7
        assert meta["partial_success"] is False

    def test_only_filter_skips_harvest(self):
        # --only tool-description-declarative must NOT need envelopes.
        findings, meta = ao.run_agent_opt(only=["tool-description-declarative"], tool_descriptions={"a": "Detect Y."})
        assert meta["active_tasks"] == ["tool-description-declarative"]
        assert "envelopes_scanned" not in meta["sources"]

    def test_partial_success_when_no_envelope_source(self):
        # Active envelope task but zero harvested envelopes -> disclose (Pattern 2).
        _findings, meta = ao.run_agent_opt(only=["weak-verdict"], envelopes=[])
        assert meta["partial_success"] is True

    def test_unknown_only_task_surfaced(self):
        _findings, meta = ao.run_agent_opt(only=["nope"], tool_descriptions={})
        assert meta["only_unknown"] == ["nope"]


# ---------------------------------------------------------------------------
# CLI surface
# ---------------------------------------------------------------------------
class TestCli:
    def test_list_tasks_json(self):
        r = CliRunner().invoke(cli, ["--json", "agent-opt", "--list-tasks"])
        assert r.exit_code == 0, r.output
        env = _parse_env(r.output)
        assert env["summary"]["task_count"] == 7
        assert {t["task_id"] for t in env["tasks"]} >= {
            "tool-description-declarative",
            "weak-verdict",
            "missing-next-command",
            "silent-degraded-state",
            "large-envelope-no-handle",
            "abstract-fact",
            "parameter-name-drift",
        }

    def test_list_detectors_json(self):
        r = CliRunner().invoke(cli, ["--json", "agent-opt", "--list-detectors"])
        assert r.exit_code == 0, r.output
        env = _parse_env(r.output)
        assert env["summary"]["detector_count"] == 7

    def test_only_declarative_envelope_shape(self):
        """Empty-or-not, the envelope is well-formed (Pattern 1: never empty
        stdout) and the agent_contract is LAW-4 anchored + CONSTRAINT-12
        executable. This is the agent-opt command dogfooding itself."""
        r = CliRunner().invoke(cli, ["--json", "agent-opt", "--only", "tool-description-declarative"])
        assert r.exit_code == 0, r.output
        env = _parse_env(r.output)
        assert "verdict" in env["summary"] and env["summary"]["verdict"]
        # LAW 4: every fact is concrete-noun-anchored.
        facts = env["agent_contract"]["facts"]
        assert facts, "no facts emitted"
        weak = [f for f in facts if not _is_concrete_anchored(f)]
        assert not weak, f"agent-opt emitted weak facts (LAW 4): {weak}"
        # CONSTRAINT 12: every next_command is a literal copy-paste `roam <cmd>`.
        ncs = env["agent_contract"]["next_commands"]
        assert ncs
        known = ao.known_command_names()
        for nc in ncs:
            assert nc.startswith("roam "), nc
            sub = [t for t in nc.split()[1:] if not t.startswith("-")]
            assert sub and sub[0] in known, f"unresolved next_command: {nc}"

    def test_empty_input_emits_nonempty_envelope(self):
        """Pattern 1: a scan that finds nothing still emits a structured
        envelope with a standalone verdict, never empty stdout."""
        r = CliRunner().invoke(cli, ["--json", "agent-opt", "--only", "weak-verdict"])
        assert r.exit_code == 0, r.output
        env = _parse_env(r.output)
        assert env["command"] == "agent-opt"
        assert "verdict" in env["summary"]
        assert isinstance(env["summary"]["total"], int)


# ---------------------------------------------------------------------------
# A4 persistence (explicit per-family wiring)
# ---------------------------------------------------------------------------
class TestPersistence:
    def test_build_finding_records_shape(self):
        findings = [
            {
                "task_id": "weak-verdict",
                "detected_way": "non-standalone-verdict",
                "suggested_way": "standalone-verdict",
                "subject": "health",
                "subject_kind": "command",
                "confidence": "high",
                "confidence_basis": "structural",
                "reason": "health verdict is boilerplate",
                "evidence": {"verdict": "ok"},
                "suggestion": "Make the verdict work alone",
            }
        ]
        recs = ao.build_finding_records(findings)
        assert len(recs) == 1
        rec = recs[0]
        assert rec.source_detector == "agent-opt.weak-verdict"
        assert rec.source_version == ao.AGENT_OPT_DETECTOR_VERSION
        assert rec.subject_id is None
        payload = json.loads(rec.evidence_json)
        assert payload["task_id"] == "weak-verdict"
        assert payload["recommended_way"] == "standalone-verdict"

    def test_emit_finding_persists_and_is_listable(self, tmp_path):
        """`roam findings list` reads the findings table; assert agent-opt rows
        land there with the family-prefixed source_detector."""
        from roam.db.connection import ensure_schema
        from roam.db.findings import emit_finding

        db = tmp_path / "f.db"
        conn = sqlite3.connect(str(db))
        conn.row_factory = sqlite3.Row
        ensure_schema(conn)
        recs = ao.build_finding_records(ao.detect_weak_verdict([("health", {"summary": {"verdict": "see details"}})]))
        assert recs
        for rec in recs:
            emit_finding(conn, rec)
        conn.commit()
        rows = conn.execute(
            "SELECT source_detector, claim FROM findings WHERE source_detector LIKE 'agent-opt.%'"
        ).fetchall()
        assert len(rows) == 1
        assert rows[0]["source_detector"] == "agent-opt.weak-verdict"
        conn.close()

    def test_multiple_findings_same_subject_persist_distinctly(self, tmp_path):
        """Regression: two distinct findings on ONE subject (e.g. two
        non-executable next_commands in one envelope) must NOT collapse to a
        single row. The finding_id digest folds in the evidence so the upsert
        keeps them separate — caught by the 2026-05-27 deep re-verification
        (`roam findings list` showed 1 of 2 persisted)."""
        from roam.db.connection import ensure_schema
        from roam.db.findings import emit_finding

        env = {
            "summary": {"count": 1},
            "findings": [1],
            "agent_contract": {"next_commands": ["Run `roam debt` first", "Run `roam trends` next"]},
        }
        findings = ao.detect_missing_next_command([("health", env)], known_commands={"debt", "trends"})
        assert len(findings) == 2, findings
        recs = ao.build_finding_records(findings)
        assert len({r.finding_id_str for r in recs}) == 2, "finding ids collided on same subject"

        db = tmp_path / "f.db"
        conn = sqlite3.connect(str(db))
        conn.row_factory = sqlite3.Row
        ensure_schema(conn)
        for rec in recs:
            emit_finding(conn, rec)
        conn.commit()
        n = conn.execute(
            "SELECT COUNT(*) AS c FROM findings WHERE source_detector = 'agent-opt.missing-next-command'"
        ).fetchone()["c"]
        conn.close()
        assert n == 2, f"expected 2 distinct persisted rows, got {n}"


# ---------------------------------------------------------------------------
# MCP-level: the wrapper exists, is read-only, dogfoods its own description,
# and the internal chain produces a well-formed envelope.
# ---------------------------------------------------------------------------
class TestMcp:
    def test_wrapper_registered_and_read_only(self):
        from roam.mcp_server import _TOOL_METADATA  # not popped — module-cache safe

        assert "roam_agent_opt" in _TOOL_METADATA
        meta = _TOOL_METADATA["roam_agent_opt"]
        assert meta["read_only"] is True
        assert meta["destructive"] is False

    def test_wrapper_description_passes_its_own_detector(self):
        """Dogfood: roam_agent_opt's OWN description must not be declarative."""
        from roam.mcp_server import _TOOL_METADATA

        desc = _TOOL_METADATA["roam_agent_opt"]["description"]
        out = ao.detect_declarative_tool_description({"roam_agent_opt": desc})
        assert out == [], f"agent-opt's own description is declarative: {desc!r}"

    def test_internal_chain_envelope_wellformed(self):
        """Exercise the chain the MCP wrapper drives (`roam --json agent-opt`)
        end-to-end against the real surface; assert a valid envelope, not a
        specific finding count."""
        findings, meta = ao.run_agent_opt(
            tool_descriptions={"roam_z": "Returns Z."},
            envelopes=[("c", {"summary": {"verdict": "done"}, "findings": [1]})],
            tool_params=[],
        )
        assert isinstance(findings, list) and findings
        assert meta["detectors_executed"] == 7
        assert "partial_success" in meta
