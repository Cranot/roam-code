"""W681 — Positive smoke for the YAML-rule-driven taint engine.

W661 drive-by surfaced that ``src/roam/security/taint_engine.py`` had only
negative-path tests (``test_inter_unused_return``,
``test_qualified_only_loads_from_yaml``) and the source-as-sanitizer
overlap regression. The engine deserves a clean POSITIVE smoke proving
``run_taint`` actually flags a real taint flow end-to-end.

This test:

* Builds an in-memory SQLite DB with ``files`` / ``symbols`` / ``edges``
  populated for a tiny SSTI-style scenario:
  ``request.args.get`` -> ``render`` -> ``flask.render_template_string``.
* Defines the rule inline (rather than relying on
  ``python_ssti.yaml`` being present, which it isn't in this baseline)
  so the test stays resilient across worktrees per W681 constraints.
* Also exercises the YAML-rule path with the shipped
  ``python-command-injection`` pack rule to keep ``load_rules`` covered.
* Asserts at least one finding is returned with the expected
  ``rule_id``, source symbol, and sink symbol — the two engine passes
  (forward BFS and intraprocedural co-call) are both exercised.

LAW 4 anchors: this docstring terminates on ``taints`` / ``flows`` /
``findings`` / ``rules``; assertions read in the same vocabulary.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from roam.security.taint_engine import (
    TaintRule,
    load_rules,
    run_taint,
)

# ---------------------------------------------------------------------------
# In-memory schema helpers (mirror tests/test_taint_intraprocedural.py +
# tests/test_taint_analysis.py — narrowest schema the engine reads from).
# ---------------------------------------------------------------------------


def _make_conn() -> sqlite3.Connection:
    """Tiny schema: only the columns ``run_taint`` actually reads.

    The engine queries ``files`` (path, language), ``symbols``
    (id, name, qualified_name, line_start, file_id), and ``edges``
    (source_id, target_id, kind). Anything else is irrelevant for the
    smoke.
    """
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE files (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            path TEXT NOT NULL,
            language TEXT
        );
        CREATE TABLE symbols (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            file_id INTEGER NOT NULL,
            name TEXT NOT NULL,
            qualified_name TEXT,
            kind TEXT,
            signature TEXT,
            line_start INTEGER,
            line_end INTEGER
        );
        CREATE TABLE edges (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_id INTEGER NOT NULL,
            target_id INTEGER NOT NULL,
            kind TEXT NOT NULL
        );
        """
    )
    return conn


def _add_file(conn: sqlite3.Connection, path: str, language: str = "python") -> int:
    conn.execute("INSERT INTO files (path, language) VALUES (?, ?)", (path, language))
    return int(conn.execute("SELECT last_insert_rowid()").fetchone()[0])


def _add_symbol(
    conn: sqlite3.Connection,
    file_id: int,
    name: str,
    qualified_name: str | None = None,
    line_start: int = 1,
) -> int:
    conn.execute(
        "INSERT INTO symbols (file_id, name, qualified_name, line_start) VALUES (?, ?, ?, ?)",
        (file_id, name, qualified_name or name, line_start),
    )
    return int(conn.execute("SELECT last_insert_rowid()").fetchone()[0])


def _add_edge(conn: sqlite3.Connection, src: int, tgt: int, kind: str = "calls") -> None:
    conn.execute(
        "INSERT INTO edges (source_id, target_id, kind) VALUES (?, ?, ?)",
        (src, tgt, kind),
    )


# ---------------------------------------------------------------------------
# The positive smoke
# ---------------------------------------------------------------------------


class TestTaintEnginePositiveSmoke:
    """One realistic SSTI-style flow, plus a fallback YAML-rule check.

    Flow modelled: ``handle_request`` (the request handler) calls BOTH
    ``request.args.get`` (taint source) and
    ``flask.render_template_string`` (taint sink). The intraprocedural
    co-call pass MUST flag this — it's the canonical
    ``user_input = source(); sink(user_input)`` shape that pure forward
    BFS misses.
    """

    def test_inline_ssti_rule_produces_finding(self):
        # Realistic Python file path so the rule's language filter binds.
        conn = _make_conn()
        fid = _add_file(conn, "app/views.py", language="python")

        # Source (taint origin): request.args.get.
        src_id = _add_symbol(
            conn,
            fid,
            "get",
            qualified_name="flask.request.args.get",
            line_start=5,
        )
        # Sink: render_template_string.
        sink_id = _add_symbol(
            conn,
            fid,
            "render_template_string",
            qualified_name="flask.render_template_string",
            line_start=10,
        )
        # Enclosing handler that co-calls both — the intraprocedural shape.
        handler_id = _add_symbol(
            conn,
            fid,
            "handle_request",
            qualified_name="app.views.handle_request",
            line_start=4,
        )
        _add_edge(conn, handler_id, src_id, "calls")
        _add_edge(conn, handler_id, sink_id, "calls")

        # Inline rule — resilient regardless of whether python_ssti.yaml
        # ships in this worktree's taint_rules/ directory.
        rule = TaintRule(
            rule_id="w681-python-ssti",
            description="Untrusted input rendered as Jinja template (SSTI).",
            severity="error",
            cwe="CWE-94",
            languages=("python",),
            sources=("request.args.get",),
            sinks=("render_template_string",),
            sanitizers=(),
        )

        findings = run_taint(conn, [rule])

        # At least one finding from the co-call pass.
        assert findings, "run_taint produced zero findings on a real SSTI flow"
        assert any(f.rule_id == "w681-python-ssti" for f in findings), (
            "expected w681-python-ssti finding among returned taint findings; "
            f"got rule_ids={[f.rule_id for f in findings]}"
        )

        # Pick the first matching finding and assert source/sink identity.
        finding = next(f for f in findings if f.rule_id == "w681-python-ssti")
        assert finding.source_symbol["id"] == src_id, (
            f"source_symbol id mismatch: got {finding.source_symbol}, expected id={src_id}"
        )
        assert finding.sink_symbol["id"] == sink_id, (
            f"sink_symbol id mismatch: got {finding.sink_symbol}, expected id={sink_id}"
        )
        # The co-call pass emits a 3-hop path: [source, enclosing, sink].
        path_ids = [p.get("id") for p in finding.path_symbols]
        assert src_id in path_ids and sink_id in path_ids, (
            f"taint path should contain both source and sink ids; got {path_ids}"
        )
        # No sanitizer registered — must report sanitizer_in_path=False so
        # downstream OpenVEX doesn't falsely claim inline_mitigations.
        assert finding.sanitizer_in_path is False

    def test_forward_bfs_chain_produces_finding(self):
        """Cross-procedural forward BFS pass: source -> middle -> sink."""
        conn = _make_conn()
        fid = _add_file(conn, "app/pipeline.py", language="python")
        src_id = _add_symbol(conn, fid, "get", qualified_name="flask.request.args.get", line_start=3)
        middle_id = _add_symbol(conn, fid, "build_query", qualified_name="app.build_query", line_start=10)
        sink_id = _add_symbol(conn, fid, "execute", qualified_name="cursor.execute", line_start=20)
        # Forward chain: source -> middle -> sink (no co-caller).
        _add_edge(conn, src_id, middle_id, "calls")
        _add_edge(conn, middle_id, sink_id, "calls")

        rule = TaintRule(
            rule_id="w681-python-sqli",
            description="Untrusted input concatenated into raw SQL.",
            severity="error",
            cwe="CWE-89",
            languages=("python",),
            sources=("request.args.get",),
            sinks=("cursor.execute",),
            sanitizers=(),
        )
        findings = run_taint(conn, [rule])
        assert findings, "forward BFS should flag source->middle->sink taint flows"
        bfs_finds = [f for f in findings if f.rule_id == "w681-python-sqli"]
        assert bfs_finds, f"expected w681-python-sqli forward-BFS finding; got {[f.rule_id for f in findings]}"
        # BFS path must include the middle hop — proves the forward pass fired,
        # not the co-call pass (no shared enclosing exists in this fixture).
        path_ids = [p.get("id") for p in bfs_finds[0].path_symbols]
        assert middle_id in path_ids, f"BFS taint path should traverse middle hop {middle_id}; got {path_ids}"

    def test_shipped_yaml_pack_loads_and_runs(self):
        """Resilience: when the shipped YAML pack is present, load + run it.

        Doesn't assert findings — the goal is to prove the load path works
        and ``run_taint`` consumes pack rules without raising. Findings
        depend on which pack rules ship in the worktree.
        """
        pack_dir = Path(__file__).resolve().parents[1] / "src" / "roam" / "security" / "taint_rules"
        if not pack_dir.is_dir():
            pytest.skip("no shipped taint_rules/ directory in this worktree")
        rules = load_rules(pack_dir)
        if not rules:
            pytest.skip("taint_rules/ directory present but empty")

        conn = _make_conn()
        fid = _add_file(conn, "app/cmd.py", language="python")
        src_id = _add_symbol(conn, fid, "args", qualified_name="flask.request.args", line_start=2)
        sink_id = _add_symbol(conn, fid, "system", qualified_name="os.system", line_start=6)
        handler_id = _add_symbol(conn, fid, "handle", qualified_name="app.cmd.handle", line_start=1)
        _add_edge(conn, handler_id, src_id, "calls")
        _add_edge(conn, handler_id, sink_id, "calls")

        # Should not raise; may or may not return findings depending on
        # which rules' sources/sinks the fixture matches.
        findings = run_taint(conn, rules)
        assert isinstance(findings, list)
        # When python-command-injection is in the pack and matches, we get
        # at least one finding. This is the "common case" — it provides
        # extra signal without making the test brittle.
        cmd_inj_rules = [r for r in rules if r.rule_id == "python-command-injection"]
        if cmd_inj_rules:
            cmd_finds = [f for f in findings if f.rule_id == "python-command-injection"]
            assert cmd_finds, (
                "python-command-injection pack rule should flag the "
                "request.args -> os.system co-call fixture; "
                f"got rule_ids={[f.rule_id for f in findings]}"
            )
