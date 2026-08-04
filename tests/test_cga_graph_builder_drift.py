"""CGA must not report a graph-builder upgrade as evidence tampering.

Measured defect: an attestation emitted before a case-fold fix in the symbol
resolver failed ``roam cga verify`` against a post-fix index at the IDENTICAL
commit on a CLEAN tree, exit 5, with::

    CGA mismatch: 2 error(s)
      - edge_bundle_digest mismatch — edges changed since signing
      - edge_count mismatch: got 99668, live=93215

The symbol merkle root was unchanged; only the edge bundle moved, because the
resolver stopped fabricating ~6,400 edges. "Edges changed since signing" is
what a verifier prints when someone has altered the evidence, so a tool
upgrade and an attack were rendered identically.

The security property under test is NOT "drift is tolerated". It is that the
three cases are told apart while all three keep the verdict they deserve:

* same builder + changed graph  → MISMATCH, tamper wording  (NEGATIVE CONTROL)
* changed builder + changed graph → BUILDER_DRIFT, still fails
* same builder + same graph      → VERIFIED                 (POSITIVE CONTROL)

The negative control is load-bearing: a "fix" that reclassified every
mismatch as drift would satisfy any test that only checks the drift path.
"""

from __future__ import annotations

import json
import sqlite3

import pytest

from roam.attest.cga import (
    GRAPH_RESOLVER_VERSION,
    VERIFY_STATE_BUILDER_DRIFT,
    VERIFY_STATE_MISMATCH,
    VERIFY_STATE_VERIFIED,
    build_cga_statement,
    graph_builder_identity,
    verify_cga_statement,
    verify_cga_statement_state,
)
from tests.conftest import make_src_project

_MANIFEST_DDL = """
CREATE TABLE index_manifest (
    id INTEGER PRIMARY KEY,
    indexed_at INTEGER NOT NULL,
    roam_version TEXT NOT NULL,
    schema_version INTEGER NOT NULL,
    parser_versions TEXT NOT NULL,
    grammar_versions TEXT,
    config_hash TEXT NOT NULL,
    git_head TEXT,
    git_dirty_hash TEXT,
    enabled_extras TEXT NOT NULL,
    index_profile TEXT DEFAULT 'all',
    notes TEXT,
    steps_status TEXT,
    component_versions TEXT
);
"""


def _make_db(*, schema_version: int = 18, extractor_version: str = "1.0.0", with_manifest: bool = True):
    """Minimal index DB shaped like the real one, with a manifest row."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE files (id INTEGER PRIMARY KEY, path TEXT, language TEXT);
        CREATE TABLE symbols (
            id INTEGER PRIMARY KEY, file_id INTEGER, name TEXT,
            qualified_name TEXT, kind TEXT, signature TEXT
        );
        CREATE TABLE edges (
            id INTEGER PRIMARY KEY, source_id INTEGER, target_id INTEGER, kind TEXT
        );
        """
    )
    conn.executemany(
        "INSERT INTO files(id, path, language) VALUES (?, ?, ?)",
        [(1, "src/a.py", "python"), (2, "src/b.py", "python")],
    )
    conn.executemany(
        "INSERT INTO symbols(id, file_id, name, qualified_name, kind, signature) VALUES (?,?,?,?,?,?)",
        [
            (1, 1, "alpha", "a.alpha", "function", "()"),
            (2, 1, "beta", "a.beta", "function", "()"),
            (3, 2, "gamma", "b.gamma", "function", "()"),
        ],
    )
    conn.executemany(
        "INSERT INTO edges(id, source_id, target_id, kind) VALUES (?,?,?,?)",
        [(1, 1, 2, "calls"), (2, 3, 1, "calls"), (3, 3, 2, "imports")],
    )
    if with_manifest:
        conn.executescript(_MANIFEST_DDL)
        conn.execute(
            "INSERT INTO index_manifest(id, indexed_at, roam_version, schema_version, "
            "parser_versions, grammar_versions, config_hash, enabled_extras, component_versions) "
            "VALUES (1, 1785840967, '13.10.0', ?, ?, NULL, 'cfg', '[]', ?)",
            (
                schema_version,
                json.dumps({"tree_sitter": "0.25.2"}),
                json.dumps({"extractors": {"python": extractor_version}, "bridges": {"django": "1.1.0"}}),
            ),
        )
    conn.commit()
    return conn


def _drop_an_edge(conn):
    """Simulate the measured upgrade: the resolver stops fabricating an edge.

    Same commit, same source, clean tree — only the graph builder's output
    moved. This is the exact shape of the reported false tamper alarm.
    """
    conn.execute("DELETE FROM edges WHERE id = (SELECT MIN(id) FROM edges)")
    conn.commit()


@pytest.fixture
def root(tmp_path):
    """A clean, committed git repo: keeps commit-SHA / dirty-tree checks out of
    the way so these tests isolate the graph-fingerprint verdict.

    This used to be a bare ``tmp_path`` with no repo in it, which achieved the
    same thing for the wrong reason: ``_git_dirty_hash`` returned ``None`` both
    for "I looked, the tree is clean" and for "I could not look", so a
    directory git had never heard of read as clean. W1462 split those apart, so
    "out of the way" now has to be earned with a tree that is genuinely clean
    rather than merely unobserved — which is also what these tests meant all
    along. The graph-fingerprint verdict under test is unaffected either way.
    """
    return make_src_project(tmp_path, {"seed.py": "SEED = 1\n"})


def _errs(errors):
    return " | ".join(errors)


# ---------------------------------------------------------------------------
# POSITIVE CONTROL — the guard must not fail everything
# ---------------------------------------------------------------------------


def test_same_builder_same_graph_still_verifies(root):
    """Untouched index + untouched attestation → VERIFIED, zero errors."""
    conn = _make_db()
    stmt = build_cga_statement(conn, project_root=root)
    state, errors = verify_cga_statement_state(stmt, conn, project_root=root)

    assert errors == [], _errs(errors)
    assert state == VERIFY_STATE_VERIFIED
    assert verify_cga_statement(stmt, conn, project_root=root)[0] is True


# ---------------------------------------------------------------------------
# NEGATIVE CONTROL — the actual security property
# ---------------------------------------------------------------------------


def test_same_builder_changed_edges_still_reads_as_tampering(root):
    """LOAD-BEARING. Identical builder identity + a changed edge set is the
    tamper signal and MUST keep the tamper wording and the failing verdict."""
    conn = _make_db()
    stmt = build_cga_statement(conn, project_root=root)
    _drop_an_edge(conn)

    state, errors = verify_cga_statement_state(stmt, conn, project_root=root)
    joined = _errs(errors)

    assert state == VERIFY_STATE_MISMATCH, joined
    assert verify_cga_statement(stmt, conn, project_root=root)[0] is False
    assert "edge_bundle_digest mismatch — edges changed since signing" in joined
    assert "edge_count mismatch" in joined
    assert "graph_builder_drift" not in joined, "a fixed builder must never be excused as drift"


def test_same_builder_edited_predicate_digest_still_reads_as_tampering(root):
    """Hand-editing the signed digest, builder untouched → tamper, not drift."""
    conn = _make_db()
    stmt = build_cga_statement(conn, project_root=root)
    stmt["predicate"]["merkle_root"] = "deadbeef" * 8

    state, errors = verify_cga_statement_state(stmt, conn, project_root=root)
    joined = _errs(errors)

    assert state == VERIFY_STATE_MISMATCH, joined
    assert "merkle_root mismatch — symbols changed since signing" in joined
    assert "graph_builder_drift" not in joined


# ---------------------------------------------------------------------------
# THE FIX — a builder change is reported distinctly
# ---------------------------------------------------------------------------


def test_changed_builder_changed_edges_reports_drift_not_tampering(root):
    """The measured case: same commit, clean tree, upgraded graph builder."""
    conn = _make_db(extractor_version="1.0.0")
    stmt = build_cga_statement(conn, project_root=root)

    # The upgrade: resolver stops fabricating an edge AND stamps a new
    # component version, exactly as a re-index after a resolver fix would.
    _drop_an_edge(conn)
    conn.execute(
        "UPDATE index_manifest SET component_versions = ?",
        (json.dumps({"extractors": {"python": "1.1.0"}, "bridges": {"django": "1.1.0"}}),),
    )
    conn.commit()

    state, errors = verify_cga_statement_state(stmt, conn, project_root=root)
    joined = _errs(errors)

    assert state == VERIFY_STATE_BUILDER_DRIFT, joined
    assert "graph_builder_drift" in joined
    assert "NOT evidence of tampering" in joined
    assert "edges changed since signing" not in joined, "must not accuse the user of tampering"
    # The numbers are still disclosed, not swallowed.
    assert "edge_count" in joined and "live=" in joined


def test_resolver_version_bump_alone_reports_drift(root):
    """The hand-owned half of the identity works on its own — a core resolver
    fix bumps GRAPH_RESOLVER_VERSION without any component/schema change."""
    conn = _make_db()
    stmt = build_cga_statement(conn, project_root=root)
    stmt["predicate"]["graph_builder"]["resolver_version"] = GRAPH_RESOLVER_VERSION + "-prev"
    _drop_an_edge(conn)

    state, errors = verify_cga_statement_state(stmt, conn, project_root=root)
    assert state == VERIFY_STATE_BUILDER_DRIFT, _errs(errors)
    assert "resolver_version" in _errs(errors)


def test_index_schema_bump_reports_drift(root):
    conn = _make_db(schema_version=18)
    stmt = build_cga_statement(conn, project_root=root)
    _drop_an_edge(conn)
    conn.execute("UPDATE index_manifest SET schema_version = 19")
    conn.commit()

    state, errors = verify_cga_statement_state(stmt, conn, project_root=root)
    assert state == VERIFY_STATE_BUILDER_DRIFT, _errs(errors)
    assert "index_schema_version 18 → 19" in _errs(errors)


# ---------------------------------------------------------------------------
# ANTI-ESCAPE-HATCH — drift must never buy a pass or launder provenance
# ---------------------------------------------------------------------------


def test_drift_is_never_a_pass(root):
    """Drift is a refusal, not an excuse: ok stays False so every existing
    CI gate (roam cga verify exits 5) keeps blocking."""
    conn = _make_db()
    stmt = build_cga_statement(conn, project_root=root)
    _drop_an_edge(conn)
    conn.execute("UPDATE index_manifest SET schema_version = 99")
    conn.commit()

    state, errors = verify_cga_statement_state(stmt, conn, project_root=root)
    ok, ok_errors = verify_cga_statement(stmt, conn, project_root=root)

    assert state == VERIFY_STATE_BUILDER_DRIFT
    assert ok is False, "builder drift must not verify"
    assert ok_errors, "builder drift must still enumerate its failures"
    assert state != VERIFY_STATE_VERIFIED


def test_forged_builder_version_cannot_launder_a_dirty_tree(root):
    """A builder bump explains digests; it explains nothing about provenance.
    One environmental failure and the verdict is MISMATCH again."""
    conn = _make_db()
    stmt = build_cga_statement(conn, project_root=root)
    _drop_an_edge(conn)
    conn.execute("UPDATE index_manifest SET schema_version = 99")
    conn.commit()
    # Predicate claims it was signed on a dirty tree; live tree (non-git) is
    # not dirty — an environmental mismatch orthogonal to the graph builder.
    stmt["predicate"]["git_dirty_hash"] = "ab" * 32

    state, errors = verify_cga_statement_state(stmt, conn, project_root=root)
    joined = _errs(errors)

    assert state == VERIFY_STATE_MISMATCH, joined
    assert "git_dirty_hash mismatch" in joined


def test_wiping_builder_identity_cannot_create_drift(root):
    """Deleting the identity block is the cheapest forgery available. It must
    yield the STRICT verdict, never the softer one."""
    conn = _make_db()
    stmt = build_cga_statement(conn, project_root=root)
    stmt["predicate"].pop("graph_builder")
    _drop_an_edge(conn)

    state, errors = verify_cga_statement_state(stmt, conn, project_root=root)
    joined = _errs(errors)

    assert state == VERIFY_STATE_MISMATCH, joined
    assert "edges changed since signing" in joined
    # ...but it says out loud that it cannot tell the two apart.
    assert "cannot" in joined and "graph_builder" in joined


def test_nulled_builder_fields_cannot_create_drift(root):
    """A field that is None on either side is UNKNOWN, and unknown never
    softens the verdict."""
    conn = _make_db()
    stmt = build_cga_statement(conn, project_root=root)
    stmt["predicate"]["graph_builder"] = {
        "resolver_version": None,
        "index_schema_version": None,
        "component_digest": None,
    }
    _drop_an_edge(conn)

    state, errors = verify_cga_statement_state(stmt, conn, project_root=root)
    assert state == VERIFY_STATE_MISMATCH, _errs(errors)
    assert "edges changed since signing" in _errs(errors)


def test_missing_manifest_table_cannot_create_drift(root):
    """Synthetic / pre-manifest DBs make the live identity unknown. Unknown
    must fail closed to the strict wording rather than excuse everything."""
    conn = _make_db(with_manifest=False)
    stmt = build_cga_statement(conn, project_root=root)
    _drop_an_edge(conn)

    state, errors = verify_cga_statement_state(stmt, conn, project_root=root)
    assert state == VERIFY_STATE_MISMATCH, _errs(errors)
    assert "edges changed since signing" in _errs(errors)


# ---------------------------------------------------------------------------
# No phantom drift
# ---------------------------------------------------------------------------


def test_identity_is_stable_across_json_key_order(root):
    """Re-serialising the same component map with different key order must not
    manufacture a builder change — that would poison the drift signal."""
    conn = _make_db()
    before = graph_builder_identity(conn)
    conn.execute(
        "UPDATE index_manifest SET component_versions = ?",
        (json.dumps({"bridges": {"django": "1.1.0"}, "extractors": {"python": "1.0.0"}}),),
    )
    conn.commit()
    after = graph_builder_identity(conn)

    assert before["component_digest"] == after["component_digest"]


def test_predicate_carries_builder_identity(root):
    conn = _make_db()
    stmt = build_cga_statement(conn, project_root=root)
    gb = stmt["predicate"]["graph_builder"]

    assert gb["resolver_version"] == GRAPH_RESOLVER_VERSION
    assert gb["index_schema_version"] == 18
    assert isinstance(gb["component_digest"], str) and len(gb["component_digest"]) == 64
