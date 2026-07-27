"""Tests for `roam cycles` — the import/call cycle (SCC) command.

Sibling of `roam clusters` / `roam layers`; the focused view of the cycle
analysis `roam health` bundles.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from conftest import (  # noqa: E402
    index_in_process,
    invoke_cli,
    parse_json_output,
)


def test_cycles_finds_cross_file_cycle(cli_runner, tmp_path, monkeypatch):
    proj = tmp_path / "cyc"
    proj.mkdir()
    # Anchor project-root detection here so find_project_root can't walk up to a
    # polluted /tmp ancestor (lesson from the brief-test /tmp pollution dig).
    (proj / ".git").mkdir()
    (proj / "a.py").write_text("from b import foo\n\n\ndef bar():\n    return foo()\n")
    (proj / "b.py").write_text("from a import bar\n\n\ndef foo():\n    return bar()\n")
    monkeypatch.chdir(proj)
    out, rc = index_in_process(proj, "--force")
    assert rc == 0, out

    result = invoke_cli(cli_runner, ["cycles"], cwd=proj, json_mode=True)
    assert result.exit_code == 0, result.output
    data = parse_json_output(result, command="cycles")
    assert data["command"] == "cycles"
    assert data["summary"]["cycle_count"] >= 1
    assert data["summary"]["actionable_count"] >= 1  # 2 distinct non-test files


# ---------------------------------------------------------------------------
# Token-budget survival / disclosure (Task #49)
# ---------------------------------------------------------------------------
#
# THE DEFECT: on a repo whose ``cycles`` envelope exceeds the JSON token
# budget, ``budget_truncate_json``'s ``_drop_fields_to_budget`` deletes the
# whole non-preserved ``cycles`` payload key (it is the only payload field
# this command emits, so there is nothing smaller to drop instead) while
# ``summary.cycle_count`` / ``summary.verdict`` still report the true,
# pre-truncation count. Before this fix ``summary.partial_success`` stayed
# ``False`` — the default ``_prepare_envelope_summary`` stamps — because
# ``_annotate_truncation`` (the function ``budget_truncate_json`` calls to
# stamp ``truncated`` / ``omitted_low_importance_nodes``) never touched
# ``partial_success``. A consumer reading only ``summary`` saw a complete,
# successful result over a payload that had just been discarded; a consumer
# reading ``data.get("cycles", [])`` could not tell "0 cycles found" from
# "cycles found but dropped".
#
# This drives the REAL budget path end-to-end (``ROAM_DEFAULT_JSON_BUDGET``,
# the same env lever production reads -- see ``formatter._default_json_budget``)
# on a genuine two-file cross-import cycle, exactly mirroring the
# ``TestAttestSurvivesTokenBudget`` regression added in dfe5966 for the
# sibling ``roam attest`` defect (attestation/agent_contract dropped ahead
# of the oversized ``evidence`` blob). No mocking of ``budget_truncate_json``
# itself -- the tiny env-var cap forces the genuine truncation branch.


def _build_cycle_project(tmp_path):
    proj = tmp_path / "cyc_budget"
    proj.mkdir()
    (proj / ".git").mkdir()
    (proj / "a.py").write_text("from b import foo\n\n\ndef bar():\n    return foo()\n")
    (proj / "b.py").write_text("from a import bar\n\n\ndef foo():\n    return bar()\n")
    return proj


class TestCyclesSurvivesTokenBudget:
    """W1327 regression: a budget-truncated `cycles` envelope must disclose
    partial_success=True, never assert completeness over a dropped payload."""

    def test_dropped_cycles_payload_flips_partial_success(self, cli_runner, tmp_path, monkeypatch):
        proj = _build_cycle_project(tmp_path)
        monkeypatch.chdir(proj)
        out, rc = index_in_process(proj, "--force")
        assert rc == 0, out

        # Force the JSON budget down far enough that the whole `cycles`
        # payload key gets dropped by `_drop_fields_to_budget` -- the exact
        # branch that silently discarded the payload pre-fix.
        monkeypatch.setenv("ROAM_DEFAULT_JSON_BUDGET", "100")

        result = invoke_cli(cli_runner, ["cycles"], cwd=proj, json_mode=True)
        assert result.exit_code == 0, result.output
        data = parse_json_output(result, command="cycles")

        summary = data["summary"]
        # Guard the guard: if the budget gate did not fire, this test proves
        # nothing about the disclosure it's meant to verify.
        assert summary.get("truncated") is True, (
            f"budget gate did not fire -- fixture outgrew the cap? summary={summary!r}"
        )
        # Confirm the actual defect condition: the cycles payload really was
        # dropped even though summary still reports a nonzero count.
        assert "cycles" not in data, (
            f"expected the whole 'cycles' payload key to be dropped by the tiny "
            f"budget; it survived -- test no longer exercises the reported defect. "
            f"data keys={sorted(data.keys())!r}"
        )
        assert summary.get("cycle_count", 0) >= 1, (
            "expected summary.cycle_count to still report the true count even "
            "though the payload was dropped -- that mismatch IS the defect"
        )
        # THE regression assertion.
        assert summary.get("partial_success") is True, (
            f"budget-truncated cycles envelope must disclose partial_success=True "
            f"-- a consumer cannot otherwise distinguish 'no cycles found' from "
            f"'cycles found but discarded'; got summary={summary!r}"
        )

    def test_untruncated_cycles_keeps_partial_success_false(self, cli_runner, tmp_path, monkeypatch):
        """Sanity anchor: a normal-sized envelope must NOT be flipped to
        partial_success=True just because the fix exists -- only a genuine
        truncation event should raise it."""
        proj = _build_cycle_project(tmp_path)
        monkeypatch.chdir(proj)
        out, rc = index_in_process(proj, "--force")
        assert rc == 0, out

        result = invoke_cli(cli_runner, ["cycles"], cwd=proj, json_mode=True)
        assert result.exit_code == 0, result.output
        data = parse_json_output(result, command="cycles")

        summary = data["summary"]
        assert summary.get("truncated") is not True, f"unexpected truncation on untouched budget: {summary!r}"
        assert "cycles" in data
        assert summary.get("partial_success") is False


# ---------------------------------------------------------------------------
# Shadow-artifact classification (mark_shadow_artifacts) — label-only.
# Unit tests build the index DB directly so the graph shape (phantom edges,
# non-exported bindings, import linkage) is exact and deterministic.
# ---------------------------------------------------------------------------


def _shadow_test_db(files, symbols, file_edges, edges):
    import sqlite3

    from roam.db.schema import SCHEMA_SQL

    conn = sqlite3.connect(":memory:")
    conn.executescript(SCHEMA_SQL)
    conn.executemany("INSERT INTO files (id, path) VALUES (?, ?)", files)
    conn.executemany(
        "INSERT INTO symbols (id, file_id, name, kind, is_exported) VALUES (?, ?, ?, ?, ?)",
        symbols,
    )
    conn.executemany(
        "INSERT INTO file_edges (source_file_id, target_file_id, kind) VALUES (?, ?, ?)",
        file_edges,
    )
    conn.executemany("INSERT INTO edges (source_id, target_id, kind) VALUES (?, ?, ?)", edges)
    conn.commit()
    return conn


def test_shadow_artifact_false_for_genuine_cycle_with_unrelated_name_collision():
    """REGRESSION: a corpus-wide name collision is NOT proof of shadowing.

    Genuine cross-file cycle ``a.helper <-> b.config`` whose closing edge
    targets a non-exported const ``config`` that merely name-collides with
    an UNRELATED exported symbol in a third module that neither cycle file
    imports. The prior attempt classified this as a shadow artifact and
    suppressed a genuine cycle — it must assert ``shadow_artifact is False``
    and report unchanged.
    """
    import networkx as nx

    from roam.graph.cycles import (
        find_cycles,
        format_cycles,
        mark_actionable_cycles,
        mark_shadow_artifacts,
    )

    conn = _shadow_test_db(
        files=[(1, "a.py"), (2, "b.py"), (3, "unrelated.py")],
        symbols=[
            (1, 1, "helper", "function", 1),
            (2, 2, "config", "constant", 0),  # non-exported closing-edge target
            (3, 3, "config", "constant", 1),  # unrelated exported name collision
        ],
        # a.py <-> b.py genuinely import each other; NOBODY imports unrelated.py
        file_edges=[(1, 2, "imports"), (2, 1, "imports")],
        edges=[(2, 1, "calls"), (1, 2, "references")],
    )
    G = nx.DiGraph()
    G.add_edges_from([(2, 1), (1, 2)])

    formatted = format_cycles(find_cycles(G), conn)
    mark_actionable_cycles(formatted)
    before = [(c["size"], c["files"], c["actionable"]) for c in formatted]

    mark_shadow_artifacts(formatted, G, conn)

    assert len(formatted) == 1
    assert formatted[0]["shadow_artifact"] is False
    assert "shadow_evidence" not in formatted[0]
    # HARD CONSTRAINT: label-only — the genuine cycle still reports unchanged.
    assert [(c["size"], c["files"], c["actionable"]) for c in formatted] == before
    conn.close()


def test_shadow_artifact_true_for_destructured_consumer_phantom():
    """POSITIVE: the destructured-consumer mislink shape IS labelled.

    Consumer does ``const { total } = useCart()`` — a non-exported local
    ``total``. The composable module (which the consumer imports) exports a
    genuine ``total``; the resolver mislinks a reference inside the
    composable's own file to the consumer's local binding, closing a
    phantom cycle. Must label ``shadow_artifact: True`` (never suppress).
    """
    import networkx as nx

    from roam.graph.cycles import (
        find_cycles,
        format_cycles,
        mark_actionable_cycles,
        mark_shadow_artifacts,
    )

    conn = _shadow_test_db(
        files=[(1, "src/composables/cart.js"), (2, "src/components/Consumer.vue")],
        symbols=[
            (1, 1, "useCart", "function", 1),
            (2, 1, "total", "constant", 1),  # genuine sibling export (destructured source)
            (3, 2, "total", "constant", 0),  # destructured local binding in consumer
        ],
        file_edges=[(2, 1, "imports")],  # consumer imports the composable module
        edges=[(3, 1, "calls"), (1, 3, "references")],  # (1, 3) is the phantom mislink
    )
    G = nx.DiGraph()
    G.add_edges_from([(3, 1), (1, 3)])

    formatted = format_cycles(find_cycles(G), conn)
    mark_actionable_cycles(formatted)
    mark_shadow_artifacts(formatted, G, conn)

    assert len(formatted) == 1
    assert formatted[0]["shadow_artifact"] is True
    evidence = formatted[0]["shadow_evidence"]
    assert evidence["shadowed_name"] == "total"
    assert evidence["genuine_sibling_file"] == "src/composables/cart.js"
    assert evidence["edge"] == [1, 3]
    # Label-only: the cycle is still present and still counted.
    assert formatted[0]["size"] == 2


def test_shadow_artifact_false_when_sibling_is_same_file_as_binding():
    """NEGATIVE: an exported same-name symbol in the binding's OWN file is
    not a destructure sibling — the genuine sibling must be cross-file."""
    import networkx as nx

    from roam.graph.cycles import (
        find_cycles,
        format_cycles,
        mark_actionable_cycles,
        mark_shadow_artifacts,
    )

    conn = _shadow_test_db(
        files=[(1, "a.py"), (2, "b.py")],
        symbols=[
            (1, 1, "helper", "function", 1),
            (2, 2, "limit", "constant", 0),  # non-exported closing-edge target
            (3, 2, "limit", "constant", 1),  # exported, but SAME file as the binding
        ],
        file_edges=[(1, 2, "imports"), (2, 1, "imports")],
        edges=[(2, 1, "calls"), (1, 2, "references")],
    )
    G = nx.DiGraph()
    G.add_edges_from([(2, 1), (1, 2)])

    formatted = format_cycles(find_cycles(G), conn)
    mark_actionable_cycles(formatted)
    mark_shadow_artifacts(formatted, G, conn)

    assert len(formatted) == 1
    assert formatted[0]["shadow_artifact"] is False
    conn.close()


def test_cycles_clean_repo_reports_none(cli_runner, tmp_path, monkeypatch):
    proj = tmp_path / "clean"
    proj.mkdir()
    (proj / ".git").mkdir()
    (proj / "a.py").write_text("def foo():\n    return 1\n")
    (proj / "b.py").write_text("def bar():\n    return 2\n")
    monkeypatch.chdir(proj)
    out, rc = index_in_process(proj, "--force")
    assert rc == 0, out

    result = invoke_cli(cli_runner, ["cycles"], cwd=proj, json_mode=True)
    assert result.exit_code == 0
    data = parse_json_output(result, command="cycles")
    assert data["summary"]["cycle_count"] == 0
