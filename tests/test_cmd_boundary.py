"""Tests for the W1295 ``roam boundary`` command.

Two finding kinds are exercised:

* ``public_by_accident`` — underscore-prefixed names appearing in
  ``__all__``. Deterministic AST scan, full-corpus.
* ``wrong_direction_import`` — changed-range layer violations. PARTIAL
  by design: the layer-numbering is derived (CLAUDE.md doesn't pin a
  strict layer DAG) so the kind requires a non-trivial layer jump
  before flagging.

The synthetic projects below cover:

(a) a ``_private_helper`` in ``__all__`` → triggers public_by_accident
(b) a clean module → zero findings
(c) a benign import from one module to another in the same direction
    (no wrong_direction_import — confirms the polarity)
(d) the registry-persistence contract (--persist writes rows
    discoverable via the central findings table)
"""

from __future__ import annotations

import json
import os

from click.testing import CliRunner

from roam.cli import cli
from roam.commands.cmd_boundary import (
    _BOUNDARY_DETECTOR_VERSION,
    _BOUNDARY_KINDS,
    _boundary_finding_id,
    _extract_all_exports,
)
from roam.db.connection import open_db
from tests.conftest import make_src_project as _make_project

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _public_by_accident_project(tmp_path):
    """Project with a ``_private_helper`` exported via ``__all__``."""
    return _make_project(
        tmp_path,
        {
            "leaky.py": """
            __all__ = ["public_fn", "_private_helper"]

            def public_fn():
                return 1

            def _private_helper():
                return 2
            """,
        },
    )


def _clean_project(tmp_path):
    """Project with a well-formed ``__all__`` — no findings expected."""
    return _make_project(
        tmp_path,
        {
            "clean.py": """
            __all__ = ["public_fn", "another_fn"]

            def public_fn():
                return 1

            def another_fn():
                return 2
            """,
        },
    )


def _benign_import_project(tmp_path):
    """Two modules importing in the conventional direction (caller -> callee).

    ``app.py`` calls ``util.py`` — the standard layering. The detector
    must NOT report a wrong_direction_import here.
    """
    return _make_project(
        tmp_path,
        {
            "app.py": """
            from .util import helper

            def run():
                return helper()
            """,
            "util.py": """
            def helper():
                return 1
            """,
        },
    )


# ---------------------------------------------------------------------------
# AST helper unit tests
# ---------------------------------------------------------------------------


def test_extract_all_exports_returns_entries():
    source = '__all__ = ["foo", "_bar", "baz"]\n'
    out = _extract_all_exports(source)
    assert out is not None
    names = [name for name, _line in out]
    assert names == ["foo", "_bar", "baz"]


def test_extract_all_exports_returns_none_without_all():
    """Modules without ``__all__`` return ``None`` (no findings to emit)."""
    out = _extract_all_exports("def foo(): pass\n")
    assert out is None


def test_extract_all_exports_skips_dynamic_all():
    """Non-literal ``__all__`` (e.g. ``list(...)``) returns ``None``."""
    source = "import _PUBLIC\n__all__ = list(_PUBLIC.keys())\n"
    out = _extract_all_exports(source)
    # Dynamic shape — the scanner returns None rather than guessing.
    assert out is None


def test_extract_all_exports_returns_none_on_syntax_error():
    out = _extract_all_exports("def foo( pass\n")
    assert out is None


def test_boundary_finding_id_is_deterministic():
    """The finding id is stable across runs and unique per finding."""
    f1 = {
        "file": "src/leaky.py",
        "line": 1,
        "kind": "public_by_accident",
        "evidence": {"exported_name": "_helper"},
    }
    f1_alt = dict(f1)
    assert _boundary_finding_id(f1) == _boundary_finding_id(f1_alt)
    assert _boundary_finding_id(f1).startswith("boundary:public_by_accident:")

    # Different exported name -> different id.
    f2 = dict(f1)
    f2["evidence"] = {"exported_name": "_other"}
    assert _boundary_finding_id(f2) != _boundary_finding_id(f1)


def test_boundary_kinds_is_closed_enum():
    """The closed-enum membership is stable; agents key off these strings."""
    assert _BOUNDARY_KINDS == ("public_by_accident", "wrong_direction_import")


# ---------------------------------------------------------------------------
# End-to-end CLI tests
# ---------------------------------------------------------------------------


def _index_and_run_boundary(project, *args):
    """Index the project then invoke ``roam boundary`` with ``args``."""
    runner = CliRunner()
    old_cwd = os.getcwd()
    try:
        os.chdir(str(project))
        idx = runner.invoke(cli, ["index"])
        assert idx.exit_code == 0, idx.output
        result = runner.invoke(cli, ["boundary", *args])
        return result
    finally:
        os.chdir(old_cwd)


def test_public_by_accident_flagged(tmp_path):
    """A ``_private_helper`` in ``__all__`` is surfaced as a finding."""
    proj = _public_by_accident_project(tmp_path)
    result = _index_and_run_boundary(proj, "--changed-range", "all")
    assert result.exit_code == 0, result.output
    assert "public_by_accident" in result.output
    assert "_private_helper" in result.output


def test_clean_project_zero_findings(tmp_path):
    """A clean module produces no findings."""
    proj = _clean_project(tmp_path)
    result = _index_and_run_boundary(proj, "--changed-range", "all")
    assert result.exit_code == 0, result.output
    assert "0 boundary findings" in result.output


def test_benign_import_no_wrong_direction(tmp_path):
    """Caller -> callee (conventional direction) produces no wrong-direction finding."""
    proj = _benign_import_project(tmp_path)
    result = _index_and_run_boundary(proj, "--changed-range", "all")
    assert result.exit_code == 0, result.output
    # No wrong-direction signal — the only output should be the verdict.
    assert "wrong_direction_import" not in result.output or "0 wrong-direction" in result.output


def test_json_envelope_shape(tmp_path):
    """``--json`` envelope carries the closed-enum summary fields."""
    proj = _public_by_accident_project(tmp_path)
    runner = CliRunner()
    old_cwd = os.getcwd()
    try:
        os.chdir(str(proj))
        runner.invoke(cli, ["index"])
        result = runner.invoke(cli, ["--json", "boundary", "--changed-range", "all"])
        assert result.exit_code == 0, result.output
        envelope = json.loads(result.output)
    finally:
        os.chdir(old_cwd)
    assert envelope["command"] == "boundary"
    summary = envelope["summary"]
    # Closed-enum summary fields.
    for key in (
        "verdict",
        "total",
        "public_by_accident",
        "wrong_direction_import",
        "partial_success",
        "scope",
    ):
        assert key in summary, f"summary missing {key!r}"
    assert summary["public_by_accident"] >= 1
    assert summary["wrong_direction_import"] == 0
    # Findings are emitted.
    findings = envelope["findings"]
    assert any(f["kind"] == "public_by_accident" for f in findings)
    # Each finding carries the closed-enum shape.
    for f in findings:
        assert f["kind"] in _BOUNDARY_KINDS
        assert "file" in f and "line" in f and "evidence" in f
        assert "layer_from" in f and "layer_to" in f


def test_persist_writes_findings_registry(tmp_path):
    """``--persist`` mirrors each finding into the central findings registry."""
    proj = _public_by_accident_project(tmp_path)
    runner = CliRunner()
    old_cwd = os.getcwd()
    try:
        os.chdir(str(proj))
        idx = runner.invoke(cli, ["index"])
        assert idx.exit_code == 0, idx.output
        result = runner.invoke(cli, ["boundary", "--changed-range", "all", "--persist"])
        assert result.exit_code == 0, result.output

        with open_db(readonly=True) as conn:
            rows = conn.execute(
                "SELECT finding_id_str, claim, source_detector, source_version, "
                "       subject_kind, confidence "
                "FROM findings WHERE source_detector = 'boundary'"
            ).fetchall()
        assert len(rows) >= 1
        for r in rows:
            assert r["source_detector"] == "boundary"
            assert r["source_version"] == _BOUNDARY_DETECTOR_VERSION
            assert r["confidence"] in ("static_analysis", "structural")
            assert r["finding_id_str"].startswith("boundary:")
    finally:
        os.chdir(old_cwd)


def test_persist_is_idempotent(tmp_path):
    """Re-running ``--persist`` upserts the same rows rather than duplicating."""
    proj = _public_by_accident_project(tmp_path)
    runner = CliRunner()
    old_cwd = os.getcwd()
    try:
        os.chdir(str(proj))
        runner.invoke(cli, ["index"])
        runner.invoke(cli, ["boundary", "--changed-range", "all", "--persist"])
        with open_db(readonly=True) as conn:
            first_ids = {
                r[0]
                for r in conn.execute(
                    "SELECT finding_id_str FROM findings WHERE source_detector = 'boundary'"
                ).fetchall()
            }
        # Second run — same fixture, same detector → same ids, same count.
        runner.invoke(cli, ["boundary", "--changed-range", "all", "--persist"])
        with open_db(readonly=True) as conn:
            second_ids = {
                r[0]
                for r in conn.execute(
                    "SELECT finding_id_str FROM findings WHERE source_detector = 'boundary'"
                ).fetchall()
            }
            count = conn.execute("SELECT COUNT(*) FROM findings WHERE source_detector = 'boundary'").fetchone()[0]
    finally:
        os.chdir(old_cwd)
    assert first_ids == second_ids
    assert count == len(first_ids)
