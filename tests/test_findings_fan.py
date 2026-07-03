"""Tests for the W152 migration: fan detector emits to the central
findings registry.

The fan detector is the fifth detector migrating onto the A4 findings
registry (after ``clones`` in W95, ``dead`` in W99, ``complexity`` in
W102, and ``smells`` in W109). It continues to return its in-memory
items list to the caller and ALSO emits one row per cross-file
architectural flag (``HIGH-RISK`` / ``hub`` / ``spreader``) into
``findings`` when invoked with ``--persist``.

Per the W150 audit, fan ships under a **dual-source-detector** design:
``source_detector='fan-symbol'`` for symbol-mode hits (``subject_kind='symbol'``)
and ``source_detector='fan-file'`` for file-mode hits (``subject_kind='file'``).
Local-only flags (``local-hub`` / ``local-spreader``) are intentionally
skipped — they are single-file-by-design rather than architectural.

End-to-end fixtures cover the symbol-mode emit through the actual
``graph_metrics`` and ``edges`` tables; file-mode rows / per-flag tier
assertions / vocabulary-discipline checks lean on direct
``_emit_fan_findings`` invocations on synthetic items so the test set
stays small and deterministic.
"""

from __future__ import annotations

import json
import os
import sqlite3

from click.testing import CliRunner

from roam.cli import cli
from roam.commands.cmd_fan import (
    _FAN_FLAG_TO_CONFIDENCE,
    _FAN_FLAG_TO_KIND,
    FAN_DETECTOR_VERSION,
    _emit_fan_findings,
    _fan_finding_id,
)
from roam.db.connection import open_db
from tests._findings_helpers import assert_detector_visible_in_findings_count
from tests.conftest import make_src_project as _make_project

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _hubby_project(tmp_path):
    """Tiny multi-file project where ``hub_fn`` is imported / called from
    many distinct files. Designed to push the symbol's in-degree and
    cross-file fan_in_files above the hub threshold so the natural
    detector path (``roam fan --persist``) emits a real registry row.

    Six caller files reach into ``core.py:hub_fn`` so:

    * ``in_degree`` >= 11 (well above the >10 hub threshold)
    * ``fan_in_files`` >= 6 (well above ``_CROSS_FILE_HUB_THRESHOLD = 3``)
    """
    callers = {
        f"caller_{i}.py": f"""
        from .core import hub_fn

        def caller_{i}_a():
            hub_fn()

        def caller_{i}_b():
            hub_fn()
        """
        for i in range(6)
    }
    files = {
        "core.py": """
        def hub_fn():
            return 1
        """,
        "__init__.py": "",
        **callers,
    }
    return _make_project(tmp_path, files)


def _persist_fan(mode="symbol"):
    """Index and run ``roam fan <mode> --persist``."""
    runner = CliRunner()
    assert runner.invoke(cli, ["index"]).exit_code == 0
    result = runner.invoke(cli, ["fan", mode, "--persist"])
    assert result.exit_code == 0, result.output
    return result


def _seed_for_emit_helper(tmp_path):
    """Open a writable connection on a fresh repo with the findings table.

    Used to exercise ``_emit_fan_findings`` directly on synthetic items
    so the per-flag tier mapping + vocabulary discipline are verified
    independently of which symbols / files the detector happens to
    trigger on a given fixture.
    """
    proj = tmp_path / "proj"
    proj.mkdir()
    return open_db(readonly=False, project_root=proj)


# ---------------------------------------------------------------------------
# Determinism + id stability
# ---------------------------------------------------------------------------


def test_fan_finding_id_is_deterministic():
    """_fan_finding_id returns the same id for the same inputs."""
    a = _fan_finding_id("fan-symbol", "hub", "src/a.py:hub_fn:10")
    b = _fan_finding_id("fan-symbol", "hub", "src/a.py:hub_fn:10")
    assert a == b
    assert a.startswith("fan-symbol:hub:")
    # Different flag → different id.
    assert _fan_finding_id("fan-symbol", "spreader", "src/a.py:hub_fn:10") != a
    # Different subject → different id.
    assert _fan_finding_id("fan-symbol", "hub", "src/a.py:hub_fn:11") != a
    # Different detector → different id (dual-detector disambiguation).
    assert _fan_finding_id("fan-file", "hub", "src/a.py:hub_fn:10") != a


def test_fan_flag_to_kind_covers_three_architectural_flags():
    """The flag-to-kind map carries exactly the 3 cross-file flags.

    Drift guard: if a new flag value lands in ``_scope_flag`` without a
    matching entry here, emitting would silently skip it. The W150
    audit froze the architectural set at three — hub / spreader /
    HIGH-RISK — so adding another implicitly requires re-running that
    audit and bumping ``FAN_DETECTOR_VERSION``.
    """
    assert set(_FAN_FLAG_TO_KIND.keys()) == {"hub", "spreader", "HIGH-RISK"}
    assert set(_FAN_FLAG_TO_CONFIDENCE.keys()) == {"hub", "spreader", "HIGH-RISK"}
    assert _FAN_FLAG_TO_KIND["hub"] == "arch.fan_hub"
    assert _FAN_FLAG_TO_KIND["spreader"] == "arch.fan_spreader"
    assert _FAN_FLAG_TO_KIND["HIGH-RISK"] == "arch.fan_high_risk"


# ---------------------------------------------------------------------------
# Per-flag tier mapping (all structural)
# ---------------------------------------------------------------------------


def test_fan_flag_tier_mapping_all_structural(tmp_path):
    """All three architectural flags land at structural confidence.

    Per the W150 audit, the flags ride on graph-edge evidence (edges +
    file_edges) — not regex, not runtime — so every kind maps to
    ``structural``.
    """
    with _seed_for_emit_helper(tmp_path) as conn:
        items = [
            {
                "name": "hubby",
                "kind": "function",
                "fan_in": 12,
                "fan_out": 2,
                "total": 14,
                "location": "src/a.py:10",
                "fan_in_files": 5,
                "fan_out_files": 1,
                "flag": "hub",
            },
            {
                "name": "spready",
                "kind": "function",
                "fan_in": 1,
                "fan_out": 12,
                "total": 13,
                "location": "src/b.py:20",
                "fan_in_files": 1,
                "fan_out_files": 5,
                "flag": "spreader",
            },
            {
                "name": "risky",
                "kind": "function",
                "fan_in": 12,
                "fan_out": 12,
                "total": 24,
                "location": "src/c.py:30",
                "fan_in_files": 5,
                "fan_out_files": 5,
                "flag": "HIGH-RISK",
            },
        ]
        written = _emit_fan_findings(
            conn,
            {
                "summary": {"caller_metric_definition": "direct_in_degree"},
                "items": items,
            },
            mode="symbol",
            source_version=FAN_DETECTOR_VERSION,
        )
        assert written == 3
        rows = conn.execute(
            "SELECT confidence, claim FROM findings WHERE source_detector = 'fan-symbol' ORDER BY id ASC"
        ).fetchall()
        assert len(rows) == 3
        for r in rows:
            assert r["confidence"] == "structural", f"expected structural for {r['claim']!r}, got {r['confidence']!r}"


# ---------------------------------------------------------------------------
# Dual source_detector differentiation (W150 audit recommendation)
# ---------------------------------------------------------------------------


def test_fan_symbol_vs_fan_file_source_detector(tmp_path):
    """Symbol mode emits under fan-symbol; file mode under fan-file.

    The dual-detector design is the W150 audit's recommendation —
    consumers query each surface independently
    (``roam findings list --detector fan-symbol`` vs ``fan-file``)
    rather than filtering on a nested mode field.
    """
    with _seed_for_emit_helper(tmp_path) as conn:
        symbol_items = [
            {
                "name": "hubby",
                "kind": "function",
                "fan_in": 12,
                "fan_out": 2,
                "total": 14,
                "location": "src/a.py:10",
                "fan_in_files": 5,
                "fan_out_files": 1,
                "flag": "hub",
            },
        ]
        file_items = [
            {
                "path": "src/hub_file.py",
                "fan_in": 10,
                "fan_out": 2,
                "total": 12,
                "flag": "hub",
            },
        ]
        _emit_fan_findings(
            conn,
            {"summary": {"caller_metric_definition": "direct_in_degree"}, "items": symbol_items},
            mode="symbol",
            source_version=FAN_DETECTOR_VERSION,
        )
        _emit_fan_findings(
            conn,
            {
                "summary": {"caller_metric_definition": "direct_in_degree (file-level: distinct source files)"},
                "items": file_items,
            },
            mode="file",
            source_version=FAN_DETECTOR_VERSION,
        )

        rows = conn.execute(
            "SELECT source_detector, subject_kind FROM findings WHERE source_detector LIKE 'fan-%' ORDER BY id ASC"
        ).fetchall()
        assert len(rows) == 2
        by_detector = {r["source_detector"]: r["subject_kind"] for r in rows}
        assert by_detector == {
            "fan-symbol": "symbol",
            "fan-file": "file",
        }


# ---------------------------------------------------------------------------
# Vocabulary discipline (Pattern 3): caller_metric_definition preserved
# ---------------------------------------------------------------------------


def test_fan_evidence_preserves_caller_metric_definition(tmp_path):
    """The persisted evidence carries ``caller_metric_definition``.

    The W150 audit highlighted this as Pattern 3 vocabulary discipline —
    fan's `direct_in_degree` definition needs to survive into the
    registry so downstream consumers can tell this fan_in apart from
    ``impact``'s fan_in or ``cmd_describe``'s caller count.
    """
    with _seed_for_emit_helper(tmp_path) as conn:
        _emit_fan_findings(
            conn,
            {
                "summary": {"caller_metric_definition": "direct_in_degree"},
                "items": [
                    {
                        "name": "hubby",
                        "kind": "function",
                        "fan_in": 12,
                        "fan_out": 2,
                        "total": 14,
                        "location": "src/a.py:10",
                        "fan_in_files": 5,
                        "fan_out_files": 1,
                        "flag": "hub",
                    },
                ],
            },
            mode="symbol",
            source_version=FAN_DETECTOR_VERSION,
        )
        _emit_fan_findings(
            conn,
            {
                "summary": {"caller_metric_definition": "direct_in_degree (file-level: distinct source files)"},
                "items": [
                    {
                        "path": "src/hub_file.py",
                        "fan_in": 10,
                        "fan_out": 2,
                        "total": 12,
                        "flag": "hub",
                    },
                ],
            },
            mode="file",
            source_version=FAN_DETECTOR_VERSION,
        )

        rows = conn.execute(
            "SELECT source_detector, evidence_json FROM findings WHERE source_detector LIKE 'fan-%' ORDER BY id ASC"
        ).fetchall()
        assert len(rows) == 2
        defs_by_detector: dict[str, str] = {}
        for r in rows:
            ev = json.loads(r["evidence_json"])
            assert "caller_metric_definition" in ev, (
                f"{r['source_detector']} evidence missing caller_metric_definition (Pattern 3 regression)"
            )
            defs_by_detector[r["source_detector"]] = ev["caller_metric_definition"]
        assert defs_by_detector["fan-symbol"] == "direct_in_degree"
        assert defs_by_detector["fan-file"] == ("direct_in_degree (file-level: distinct source files)")


# ---------------------------------------------------------------------------
# Skip local-hub / local-spreader (W150 audit)
# ---------------------------------------------------------------------------


def test_fan_skips_local_hub_and_local_spreader(tmp_path):
    """Local-only flags are not mirrored to the findings registry.

    The W150 audit classifies ``local-hub`` and ``local-spreader`` as
    single-file by design (one large SFC, generated module) rather
    than architectural. Persisting them would bloat the registry with
    non-actionable rows.
    """
    with _seed_for_emit_helper(tmp_path) as conn:
        items = [
            {
                "name": "intra_hub",
                "kind": "function",
                "fan_in": 30,
                "fan_out": 0,
                "total": 30,
                "location": "src/big.py:10",
                "fan_in_files": 1,
                "fan_out_files": 0,
                "flag": "local-hub",
            },
            {
                "name": "intra_spread",
                "kind": "function",
                "fan_in": 0,
                "fan_out": 30,
                "total": 30,
                "location": "src/big.py:20",
                "fan_in_files": 0,
                "fan_out_files": 1,
                "flag": "local-spreader",
            },
            {
                "name": "neutral",
                "kind": "function",
                "fan_in": 1,
                "fan_out": 1,
                "total": 2,
                "location": "src/big.py:30",
                "fan_in_files": 1,
                "fan_out_files": 1,
                "flag": "",
            },
            {
                "name": "real_hub",
                "kind": "function",
                "fan_in": 12,
                "fan_out": 2,
                "total": 14,
                "location": "src/c.py:30",
                "fan_in_files": 5,
                "fan_out_files": 1,
                "flag": "hub",
            },
        ]
        written = _emit_fan_findings(
            conn,
            {"summary": {"caller_metric_definition": "direct_in_degree"}, "items": items},
            mode="symbol",
            source_version=FAN_DETECTOR_VERSION,
        )
        # Only the cross-file `hub` flag should land in the registry.
        assert written == 1
        rows = conn.execute("SELECT evidence_json FROM findings WHERE source_detector = 'fan-symbol'").fetchall()
        assert len(rows) == 1
        ev = json.loads(rows[0]["evidence_json"])
        assert ev["flag"] == "hub"
        assert ev["symbol_name"] == "real_hub"


# ---------------------------------------------------------------------------
# Batched subject_id pre-resolution (one query keyed by file/name/line,
# with an in-memory nearest-line fallback)
# ---------------------------------------------------------------------------


def _seed_symbol(conn, path, name, line_start):
    """Insert a (file, symbol) pair and return the new symbols.id."""
    fid_row = conn.execute(
        "INSERT INTO files (path) VALUES (?) ON CONFLICT(path) DO UPDATE SET path=path RETURNING id",
        (path,),
    ).fetchone()
    file_id = int(fid_row[0])
    sid = conn.execute(
        "INSERT INTO symbols (file_id, name, kind, line_start) VALUES (?, ?, 'function', ?)",
        (file_id, name, line_start),
    ).lastrowid
    return int(sid)


def test_fan_subject_id_resolves_exact_and_nearest_line(tmp_path):
    """Batched pre-resolution links each finding to its symbols.id.

    Two paths exercised in one persist call: an exact (path, name, line)
    match, and a nearest-line fallback when the item's line drifts off the
    real symbol's line_start (decorator / parser drift). Both must resolve
    via the single batched query rather than the old two-lookup-per-item
    path — proven by subject_id pointing at the seeded rows.
    """
    with _seed_for_emit_helper(tmp_path) as conn:
        exact_id = _seed_symbol(conn, "src/a.py", "hubby", 10)
        # Real symbol sits at line 42 but the ranked item reports line 40
        # (off by a couple of lines) — only the nearest-line fallback links it.
        drift_id = _seed_symbol(conn, "src/b.py", "spready", 42)
        conn.commit()

        items = [
            {
                "name": "hubby",
                "kind": "function",
                "fan_in": 12,
                "fan_out": 2,
                "total": 14,
                "location": "src/a.py:10",
                "fan_in_files": 5,
                "fan_out_files": 1,
                "flag": "hub",
            },
            {
                "name": "spready",
                "kind": "function",
                "fan_in": 1,
                "fan_out": 12,
                "total": 13,
                "location": "src/b.py:40",
                "fan_in_files": 1,
                "fan_out_files": 5,
                "flag": "spreader",
            },
        ]
        written = _emit_fan_findings(
            conn,
            {"summary": {"caller_metric_definition": "direct_in_degree"}, "items": items},
            mode="symbol",
            source_version=FAN_DETECTOR_VERSION,
        )
        assert written == 2
        rows = conn.execute(
            "SELECT subject_id, subject_kind, json_extract(evidence_json, '$.symbol_name') AS name "
            "FROM findings WHERE source_detector = 'fan-symbol'"
        ).fetchall()
        by_name = {r["name"]: r for r in rows}
        assert by_name["hubby"]["subject_kind"] == "symbol"
        assert by_name["hubby"]["subject_id"] == exact_id
        # Nearest-line fallback links the drifted item to the real symbol.
        assert by_name["spready"]["subject_id"] == drift_id


def test_fan_subject_id_none_when_symbol_absent(tmp_path):
    """A flagged item with no matching symbol resolves subject_id to NULL.

    The batched query simply omits the key; the row still persists (the
    finding is real) but carries no subject linkage — never a wrong id.
    """
    with _seed_for_emit_helper(tmp_path) as conn:
        items = [
            {
                "name": "ghost",
                "kind": "function",
                "fan_in": 12,
                "fan_out": 2,
                "total": 14,
                "location": "src/missing.py:10",
                "fan_in_files": 5,
                "fan_out_files": 1,
                "flag": "hub",
            },
        ]
        written = _emit_fan_findings(
            conn,
            {"summary": {"caller_metric_definition": "direct_in_degree"}, "items": items},
            mode="symbol",
            source_version=FAN_DETECTOR_VERSION,
        )
        assert written == 1
        row = conn.execute("SELECT subject_id FROM findings WHERE source_detector = 'fan-symbol'").fetchone()
        assert row["subject_id"] is None


# ---------------------------------------------------------------------------
# Upsert determinism on rerun
# ---------------------------------------------------------------------------


def test_fan_rerun_upserts_not_duplicates(tmp_path):
    """Re-emitting the same items produces the same id set and row count."""
    with _seed_for_emit_helper(tmp_path) as conn:
        items = [
            {
                "name": "hubby",
                "kind": "function",
                "fan_in": 12,
                "fan_out": 2,
                "total": 14,
                "location": "src/a.py:10",
                "fan_in_files": 5,
                "fan_out_files": 1,
                "flag": "hub",
            },
        ]
        payload = {
            "summary": {"caller_metric_definition": "direct_in_degree"},
            "items": items,
        }
        _emit_fan_findings(conn, payload, mode="symbol", source_version=FAN_DETECTOR_VERSION)
        first_ids = {
            r[0]
            for r in conn.execute("SELECT finding_id_str FROM findings WHERE source_detector = 'fan-symbol'").fetchall()
        }
        first_count = conn.execute("SELECT COUNT(*) FROM findings WHERE source_detector = 'fan-symbol'").fetchone()[0]
        assert first_count == 1

        # Re-run — same payload, same ids, same row count.
        _emit_fan_findings(conn, payload, mode="symbol", source_version=FAN_DETECTOR_VERSION)
        second_ids = {
            r[0]
            for r in conn.execute("SELECT finding_id_str FROM findings WHERE source_detector = 'fan-symbol'").fetchall()
        }
        second_count = conn.execute("SELECT COUNT(*) FROM findings WHERE source_detector = 'fan-symbol'").fetchone()[0]
        assert second_count == first_count
        assert second_ids == first_ids


# ---------------------------------------------------------------------------
# End-to-end emit through the indexer + roam fan --persist
# ---------------------------------------------------------------------------


def test_fan_persist_e2e_emits_hub_symbol(tmp_path):
    """`roam fan symbol --persist` writes a hub finding when one exists.

    The hubby project pushes ``core.hub_fn`` over the cross-file hub
    threshold (5+ caller files), so the natural detector path should
    emit a ``fan-symbol`` row tagged with the ``hub`` flag.
    """
    proj = _hubby_project(tmp_path)
    old_cwd = os.getcwd()
    try:
        os.chdir(str(proj))
        _persist_fan(mode="symbol")

        with open_db(readonly=True) as conn:
            rows = conn.execute(
                "SELECT finding_id_str, evidence_json, confidence, "
                "       source_detector, source_version, subject_kind "
                "FROM findings WHERE source_detector = 'fan-symbol'"
            ).fetchall()
        assert len(rows) >= 1, "expected at least one fan-symbol finding for the cross-file hub fixture; got 0"
        flags_seen = set()
        for r in rows:
            assert r["source_detector"] == "fan-symbol"
            assert r["source_version"] == FAN_DETECTOR_VERSION
            assert r["confidence"] == "structural"
            assert r["subject_kind"] == "symbol"
            assert r["finding_id_str"].startswith("fan-symbol:")
            ev = json.loads(r["evidence_json"])
            flags_seen.add(ev.get("flag"))
            # Evidence must carry the metric provenance field.
            assert ev.get("caller_metric_definition") == "direct_in_degree"
        # `hub` is the flag fired by 6 cross-file callers + zero outbound.
        assert "hub" in flags_seen, f"expected a 'hub' flag among the persisted findings; got {flags_seen}"
    finally:
        os.chdir(old_cwd)


def test_fan_persist_no_persist_does_not_emit(tmp_path):
    """Without --persist, the standard read path stays side-effect-free."""
    proj = _hubby_project(tmp_path)
    old_cwd = os.getcwd()
    try:
        os.chdir(str(proj))
        runner = CliRunner()
        assert runner.invoke(cli, ["index"]).exit_code == 0
        # No --persist.
        assert runner.invoke(cli, ["fan", "symbol"]).exit_code == 0

        with open_db(readonly=True) as conn:
            try:
                count = conn.execute(
                    "SELECT COUNT(*) FROM findings WHERE source_detector IN ('fan-symbol', 'fan-file')"
                ).fetchone()[0]
            except sqlite3.OperationalError:
                count = 0
        assert count == 0, "non-persist fan still wrote to findings"
    finally:
        os.chdir(old_cwd)


# ---------------------------------------------------------------------------
# Read-side visibility through `roam findings`
# ---------------------------------------------------------------------------


def test_fan_findings_visible_via_cmd_findings_list(tmp_path):
    """`roam findings list --detector fan-symbol` returns rows."""
    proj = _hubby_project(tmp_path)
    old_cwd = os.getcwd()
    try:
        os.chdir(str(proj))
        _persist_fan(mode="symbol")

        runner = CliRunner()
        result = runner.invoke(cli, ["--json", "findings", "list", "--detector", "fan-symbol"])
        assert result.exit_code == 0, result.output
        envelope = json.loads(result.output)
        assert envelope["command"] == "findings-list"
        assert envelope["summary"]["state"] == "populated"
        assert envelope["summary"]["total_findings"] >= 1
        assert "fan-symbol" in envelope["summary"]["detectors"]
        assert all(r["source_detector"] == "fan-symbol" for r in envelope["findings"])
    finally:
        os.chdir(old_cwd)


def test_fan_findings_visible_via_cmd_findings_count(tmp_path):
    """`roam findings count` includes a non-zero entry for fan-symbol."""
    proj = _hubby_project(tmp_path)
    old_cwd = os.getcwd()
    try:
        os.chdir(str(proj))
        _persist_fan(mode="symbol")
    finally:
        os.chdir(old_cwd)
    assert_detector_visible_in_findings_count(proj, "fan-symbol")


# ---------------------------------------------------------------------------
# Graceful degrade on pre-W89 schema
# ---------------------------------------------------------------------------


def test_fan_persist_no_findings_table_no_crash(tmp_path):
    """`fan --persist` degrades cleanly when the findings table is absent.

    Simulates the pre-W89 schema by DROP-ing ``findings`` after init
    but before the persist call. The standard text / JSON output path
    that legacy consumers depend on must keep working — the command
    exits 0 and writes no registry rows.
    """
    proj = _hubby_project(tmp_path)
    old_cwd = os.getcwd()
    try:
        os.chdir(str(proj))
        runner = CliRunner()
        assert runner.invoke(cli, ["index"]).exit_code == 0

        with open_db(readonly=False) as conn:
            conn.execute("DROP TABLE IF EXISTS findings")
            conn.commit()

        result = runner.invoke(cli, ["fan", "symbol", "--persist"])
        # Must succeed despite the missing findings table.
        assert result.exit_code == 0, result.output
    finally:
        os.chdir(old_cwd)
