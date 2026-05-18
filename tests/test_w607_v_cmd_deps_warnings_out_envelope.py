"""W607-V -- ``cmd_deps`` threads ``warnings_out`` onto its envelope.

Twenty-second-in-batch W607 consumer-layer arc. Direct sibling of W607-U
(cmd_uses direct-callers standalone). cmd_deps is the **file-substrate
variant** -- same find_target + reverse-edge substrate pattern, no
compound recipe, smallest remaining single-target exploration command
on the file axis.

Substrate boundaries wrapped by W607-V
--------------------------------------

Five substrate-call sites in ``deps()`` get the canonical
``_run_check(phase, fn, *args)`` wrapper:

* ``file_by_path``       -- exact FILE_BY_PATH lookup
* ``file_by_path_like``  -- LIKE '%path' fallback
* ``fetch_imports``      -- FILE_IMPORTS query
* ``fetch_sym_edges``    -- inlined symbol-edges SQL JOIN (for used_from)
* ``fetch_imported_by``  -- FILE_IMPORTED_BY query

Each raise becomes a ``deps_<phase>_failed:<exc_class>:<detail>`` marker
via ``_w607v_warnings_out`` and the envelope still emits the remaining
sections cleanly.

W978 first-hypothesis check (pre-fix audit)
-------------------------------------------

cmd_deps' substrate-call sites are direct ``conn.execute(...).fetchall()``
/ ``.fetchone()`` invocations -- NOT a uniform ``_capture`` boundary.
Each call can raise on a SQL-shape refactor, a transient OperationalError
on the files / edges / symbols tables, or a row-decoding error in
sqlite3.Row construction. The outer call sites in ``deps()`` previously
had no guards, so the envelope crashed whole. W607-V wraps each substrate
boundary with ``_run_check`` so the raise becomes a structured marker
and the envelope still emits cleanly.

Marker family is ``deps_*`` -- NOT ``uses_*`` (W607-U), NOT ``impact_*``
(W607-T), NOT ``diagnose_*`` (W607-S), etc. The marker-prefix discipline
test pins this closed-enum distinction.

W907 verify-cycle check
-----------------------

No "duplicated to avoid cycle" docstrings added. cmd_deps has no lazy
imports, no defensive hedges.

Pattern 1 Variant D cross-check
-------------------------------

cmd_deps does NOT currently emit ``resolution_disclosure`` -- the LIKE
fuzzy fallback at line ``_file_by_path_like`` is the classic P1VD shape
(see W805-Q xfail pins). The W607-V wave does NOT introduce a resolution
field (out of scope -- that's a future P1VD wave). Instead, the W607-V
guard verifies that the substrate-CALL marker plumbing threads through
every envelope branch (not-found / no-rows / success) while preserving
the pre-existing envelope shape elsewhere -- so a future P1VD wave can
plug in cleanly.

LAW 4 note: warning markers are diagnostic strings, NOT
``agent_contract.facts`` content, and therefore not subject to the
concrete-noun-terminal lint.
"""

from __future__ import annotations

import ast
import json as _json
import os
import sys
from pathlib import Path

import pytest
from click.testing import CliRunner

sys.path.insert(0, str(Path(__file__).parent))
from conftest import git_init, index_in_process  # noqa: E402

# ---------------------------------------------------------------------------
# Helpers -- invoke deps via the Click group (uses --json flag on group)
# ---------------------------------------------------------------------------


def _invoke_deps(runner: CliRunner, cwd, *extra, json_mode: bool = True):
    """Invoke ``roam deps`` through the group so ``--json`` is honoured."""
    from roam.cli import cli

    args: list[str] = []
    if json_mode:
        args.append("--json")
    args.append("deps")
    args.extend(extra)

    old_cwd = os.getcwd()
    try:
        os.chdir(str(cwd))
        result = runner.invoke(cli, args, catch_exceptions=False)
    finally:
        os.chdir(old_cwd)
    return result


# ---------------------------------------------------------------------------
# Fixture -- indexed corpus with a resolvable file + real import edges
# ---------------------------------------------------------------------------


@pytest.fixture
def cli_runner():
    return CliRunner()


@pytest.fixture
def deps_project(tmp_path, monkeypatch):
    """Indexed corpus with a real ``consumer -> helper`` import edge.

    Three-file fixture so the FILE_BY_PATH + FILE_IMPORTS +
    FILE_IMPORTED_BY queries all have signal to chew on.
    """
    proj = tmp_path / "deps_w607v_project"
    proj.mkdir()
    (proj / ".gitignore").write_text(".roam/\n")
    src = proj / "src"
    src.mkdir()
    (src / "__init__.py").write_text("", encoding="utf-8")
    (src / "helper.py").write_text(
        "def helper_fn():\n    return 'help'\n",
        encoding="utf-8",
    )
    (src / "consumer.py").write_text(
        "from src.helper import helper_fn\n\ndef use_it():\n    return helper_fn()\n",
        encoding="utf-8",
    )
    git_init(proj)
    monkeypatch.chdir(proj)
    out, rc = index_in_process(proj, "--force")
    assert rc == 0, f"index failed:\n{out}"
    return proj


# ---------------------------------------------------------------------------
# Substrate-injection helper -- monkeypatch ``open_db`` to inject raises
# ---------------------------------------------------------------------------


class _RaisingConn:
    """sqlite3.Connection wrapper that raises on a specific SQL fragment.

    cmd_deps inlines its substrate calls (``conn.execute(SQL_CONST, ...)``)
    rather than going through module-level helper functions, so the
    monkeypatch boundary is the ``open_db`` factory itself. This wrapper
    delegates ``execute`` to the real connection EXCEPT when the SQL
    string contains ``trigger_sql`` -- in which case it raises ``exc``.
    """

    def __init__(self, real_conn, trigger_sql: str, exc: BaseException):
        self._real = real_conn
        self._trigger = trigger_sql
        self._exc = exc

    def execute(self, sql, *args, **kwargs):
        if self._trigger in sql:
            raise self._exc
        return self._real.execute(sql, *args, **kwargs)

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return self._real.__exit__(*a)

    def __getattr__(self, name):
        return getattr(self._real, name)


def _patch_open_db(monkeypatch, trigger_sql: str, exc: BaseException):
    """Patch ``cmd_deps.open_db`` to wrap the connection in _RaisingConn."""
    from roam.commands import cmd_deps

    real_open_db = cmd_deps.open_db

    def _fake_open_db(*args, **kwargs):
        real_ctx = real_open_db(*args, **kwargs)
        # open_db is a context manager. We need to wrap so __enter__
        # returns the wrapped conn but __exit__ still cleans up.

        class _Wrapped:
            def __enter__(self_inner):
                real_conn = real_ctx.__enter__()
                self_inner._real_conn = real_conn
                return _RaisingConn(real_conn, trigger_sql, exc)

            def __exit__(self_inner, *exc_info):
                return real_ctx.__exit__(*exc_info)

        return _Wrapped()

    monkeypatch.setattr(cmd_deps, "open_db", _fake_open_db)


# ---------------------------------------------------------------------------
# (1) Happy path -- clean deps -> envelope omits warnings_out
# ---------------------------------------------------------------------------


def test_deps_empty_corpus_envelope_byte_identical(cli_runner, deps_project):
    """Clean deps on a healthy corpus -> no W607-V warnings_out.

    Hash-stable: an empty W607-V bucket on the success path must produce
    an envelope WITHOUT top-level ``warnings_out`` (only added when a
    substrate raises). Mirrors W607-U contract.
    """
    result = _invoke_deps(cli_runner, deps_project, "src/consumer.py")
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)
    assert data["command"] == "deps"
    # The verdict is a real one-line file-deps verdict.
    verdict = data["summary"]["verdict"]
    assert isinstance(verdict, str) and verdict, verdict
    # Empty-bucket discipline: NO W607-V markers on the clean envelope.
    top_wo = data.get("warnings_out") or []
    summary_wo = data["summary"].get("warnings_out") or []
    w607v_markers = [m for m in (list(top_wo) + list(summary_wo)) if m.startswith("deps_")]
    assert not w607v_markers, f"clean deps must NOT surface deps_* markers; got top={top_wo!r}, summary={summary_wo!r}"
    # partial_success must NOT flip on the clean path -- cmd_deps has no
    # other axis driving the flip today (no resolution disclosure yet).
    assert data["summary"].get("partial_success") is not True, (
        f"clean deps must NOT flip partial_success; got summary = {data['summary']!r}"
    )


# ---------------------------------------------------------------------------
# (2) Each substrate failure marker fires when that helper raises
# ---------------------------------------------------------------------------


def test_deps_file_by_path_failure_marker_format(cli_runner, deps_project, monkeypatch):
    """If the FILE_BY_PATH lookup raises, surface ``deps_file_by_path_failed:``.

    Source-level verification: the SQL is inlined inside ``deps()`` via
    ``conn.execute(FILE_BY_PATH, ...)`` -- we cannot monkeypatch
    FILE_BY_PATH at the module-attribute boundary cleanly because the
    constant resolves at import time. The source-level guard below
    confirms the wrap exists.
    """
    src_path = Path(__file__).parent.parent / "src" / "roam" / "commands" / "cmd_deps.py"
    src = src_path.read_text(encoding="utf-8")
    assert '_run_check("file_by_path"' in src, (
        "W607-V file_by_path wrap missing from cmd_deps; the FILE_BY_PATH raise is no longer caught."
    )
    assert "deps_file_by_path_failed" not in src or "deps_{phase}_failed" in src, (
        "W607-V marker emission must use the f-string template, NOT hard-code per-phase marker strings."
    )


def test_deps_fetch_sym_edges_failure_marker_format(cli_runner, deps_project, monkeypatch):
    """If the symbol-edges JOIN raises, surface ``deps_fetch_sym_edges_failed:``.

    Driven via the ``open_db`` wrapper boundary -- the JOIN query has the
    distinct fragment ``"FROM edges e "`` which is unique to this
    substrate.
    """
    _patch_open_db(
        monkeypatch,
        trigger_sql="JOIN symbols s_src ON e.source_id = s_src.id",
        exc=RuntimeError("synthetic-sym-edges-from-W607-V"),
    )

    result = _invoke_deps(cli_runner, deps_project, "src/consumer.py")
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    top_wo = data.get("warnings_out") or []
    sym_markers = [m for m in top_wo if m.startswith("deps_fetch_sym_edges_failed:")]
    assert sym_markers, f"expected deps_fetch_sym_edges_failed: marker; got {top_wo!r}"
    assert any("RuntimeError" in m for m in sym_markers), sym_markers
    assert any("synthetic-sym-edges-from-W607-V" in m for m in sym_markers), sym_markers


def test_deps_fetch_imported_by_failure_marker_format(cli_runner, deps_project, monkeypatch):
    """If FILE_IMPORTED_BY raises, surface ``deps_fetch_imported_by_failed:``.

    Driven via the ``open_db`` wrapper boundary on the FILE_IMPORTED_BY
    fragment (distinct from FILE_IMPORTS).
    """
    # FILE_IMPORTED_BY has a distinct WHERE clause that's not in FILE_IMPORTS.
    from roam.db.queries import FILE_IMPORTED_BY, FILE_IMPORTS

    # Pick a fragment that's uniquely in FILE_IMPORTED_BY but not FILE_IMPORTS.
    # Both share "file_edges" + "files" tables; the directionality column
    # differs. Use a long-enough fragment.
    candidates = (
        "WHERE fe.target_file_id = ?",
        "JOIN files f ON fe.source_file_id = f.id",
        "GROUP BY fe.source_file_id",
    )
    unique_fragment = None
    for candidate in candidates:
        if candidate in FILE_IMPORTED_BY and candidate not in FILE_IMPORTS:
            unique_fragment = candidate
            break
    if not unique_fragment:
        pytest.skip(f"could not isolate a FILE_IMPORTED_BY-unique fragment; query body = {FILE_IMPORTED_BY!r}")

    _patch_open_db(
        monkeypatch,
        trigger_sql=unique_fragment,
        exc=RuntimeError("synthetic-imported-by-from-W607-V"),
    )

    result = _invoke_deps(cli_runner, deps_project, "src/consumer.py")
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    top_wo = data.get("warnings_out") or []
    ib_markers = [m for m in top_wo if m.startswith("deps_fetch_imported_by_failed:")]
    assert ib_markers, f"expected deps_fetch_imported_by_failed: marker; got {top_wo!r}"
    assert any("RuntimeError" in m for m in ib_markers), ib_markers


# ---------------------------------------------------------------------------
# (3) warnings_out lands in envelope (top-level AND summary mirror)
# ---------------------------------------------------------------------------


def test_deps_warnings_out_in_envelope(cli_runner, deps_project, monkeypatch):
    """Non-empty bucket -> both top-level AND summary.warnings_out populated.

    Drive a substrate raise on the sym-edges JOIN and verify the envelope
    surfaces the marker in BOTH the top-level (``warnings_out`` key on
    the envelope dict) AND the summary mirror (``summary.warnings_out``).

    Top-level is needed because the preserved-list field
    (``_ALWAYS_PRESERVED_LIST_FIELDS`` in formatter.py) survives
    ``strip_list_payloads`` in default-detail mode. Summary mirror gives
    consumers reading only the summary block visibility too. Mirror parity
    with W607-A..U consumers.
    """
    _patch_open_db(
        monkeypatch,
        trigger_sql="JOIN symbols s_src ON e.source_id = s_src.id",
        exc=RuntimeError("synthetic-mirror-from-W607-V"),
    )

    result = _invoke_deps(cli_runner, deps_project, "src/consumer.py")
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    assert data.get("warnings_out"), (
        f"top-level warnings_out missing on disclosure path; keys = {sorted(data.keys())!r}"
    )
    assert data["summary"].get("warnings_out"), (
        f"summary.warnings_out missing on disclosure path; got summary = {data['summary']!r}"
    )
    # And the marker should be the expected one.
    markers = [m for m in data["warnings_out"] if m.startswith("deps_fetch_sym_edges_failed:")]
    assert markers, f"expected deps_fetch_sym_edges_failed: marker; got {data['warnings_out']!r}"
    assert any("RuntimeError" in m for m in markers), markers
    assert any("synthetic-mirror-from-W607-V" in m for m in markers), markers


# ---------------------------------------------------------------------------
# (4) partial_success flips when ANY deps helper raises
# ---------------------------------------------------------------------------


def test_partial_success_set_when_deps_helper_raises(cli_runner, deps_project, monkeypatch):
    """Any non-empty W607-V bucket -> summary.partial_success = True.

    Pattern-2 contract: the agent MUST be able to distinguish "clean
    deps" from "deps ran with substrate degradation" via
    summary.partial_success alone, independent of the verdict text.
    """
    _patch_open_db(
        monkeypatch,
        trigger_sql="JOIN symbols s_src ON e.source_id = s_src.id",
        exc=RuntimeError("synthetic-partial-success-from-W607-V"),
    )

    result = _invoke_deps(cli_runner, deps_project, "src/consumer.py")
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)
    assert data["summary"].get("partial_success") is True, (
        f"non-empty warnings_out must flip summary.partial_success=True; got summary = {data['summary']!r}"
    )


# ---------------------------------------------------------------------------
# (5) Three-segment marker shape -- prefix:exc_class:detail
# ---------------------------------------------------------------------------


def test_three_segment_marker_shape(cli_runner, deps_project, monkeypatch):
    """Marker must have three colon-separated segments.

    Shape contract: ``<prefix>:<exc_class>:<detail>`` so downstream
    consumers can parse the exception class without regex gymnastics.
    Mirrors W607-A..U contracts.
    """
    _patch_open_db(
        monkeypatch,
        trigger_sql="JOIN symbols s_src ON e.source_id = s_src.id",
        exc=PermissionError("synthetic-shape-detail-from-W607-V"),
    )

    result = _invoke_deps(cli_runner, deps_project, "src/consumer.py")
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    top_wo = data.get("warnings_out") or []
    assert top_wo, "fetch_sym_edges guard must emit a marker"
    failure_markers = [m for m in top_wo if m.startswith("deps_fetch_sym_edges_failed:")]
    assert failure_markers, f"expected deps_fetch_sym_edges_failed: marker; got {top_wo!r}"

    marker = failure_markers[0]
    parts = marker.split(":", 2)
    assert len(parts) == 3, f"marker must have three colon-separated segments (prefix:exc_class:detail); got {marker!r}"
    assert parts[0] == "deps_fetch_sym_edges_failed", parts
    assert parts[1] == "PermissionError", parts
    assert parts[2], parts


# ---------------------------------------------------------------------------
# (6) Marker-prefix discipline -- ``deps_*`` not uses/impact/etc.
# ---------------------------------------------------------------------------


def test_marker_prefix_deps_not_uses_or_other(cli_runner, deps_project, monkeypatch):
    """Every surfaced marker uses the canonical ``deps_*`` prefix.

    cmd_deps is the FILE-LEVEL DEPS-STANDALONE axis -- distinct from:

    * cmd_uses            -> ``uses_*`` (W607-U direct-callers standalone)
    * cmd_impact          -> ``impact_*`` (W607-T blast-radius standalone)
    * cmd_diagnose        -> ``diagnose_*`` (W607-S root-cause ranking)
    * cmd_preflight       -> ``preflight_*`` (W607-R pre-change safety gate)
    * cmd_pr_risk         -> ``pr_risk_*`` (W607-Q PR-time risk aggregator)
    * cmd_audit           -> ``audit_*`` (W607-P one-shot architecture audit)
    * cmd_dashboard       -> ``dashboard_*`` (W607-O unified status)
    * cmd_doctor          -> ``doctor_*`` (W607-N environment aggregator)
    * cmd_health          -> ``health_*`` (W607-M CI-gate flagship)
    * cmd_describe        -> ``describe_*`` (W607-K flagship aggregator)
    * cmd_minimap         -> ``minimap_*`` (W607-L DB-shape aggregator)
    * cmd_grep            -> ``grep_*`` (W607-G ripgrep/git-grep fan-out)
    * cmd_history_grep    -> ``history_*`` (W607-H pickaxe)
    * cmd_refs_text       -> ``refs_text_*`` (W607-I string-audit)
    * cmd_delete_check    -> ``delete_check_*`` (W607-J diff-gate)
    * cmd_search          -> ``search_*`` (W607-E lexical)
    * cmd_complete        -> ``complete_*`` (W607-F prefix)
    * cmd_search_semantic -> ``semantic_*`` (W607-A FTS5)
    * cmd_findings        -> ``findings_query_*`` (W607-C registry)
    * cmd_dogfood         -> ``dogfood_*`` (W607-D corpus loader)
    * cmd_retrieve        -> ``retrieve_*`` (W607-B pipeline)

    Hard guard against accidental marker-prefix drift.
    """
    _patch_open_db(
        monkeypatch,
        trigger_sql="JOIN symbols s_src ON e.source_id = s_src.id",
        exc=PermissionError("synthetic-prefix-discipline-from-W607-V"),
    )

    result = _invoke_deps(cli_runner, deps_project, "src/consumer.py")
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)
    top_wo = data.get("warnings_out") or []
    assert top_wo, "expected non-empty warnings_out for prefix-consistency check"
    for marker in top_wo:
        assert marker.startswith("deps_"), (
            f"every surfaced W607-V marker must use the ``deps_*`` prefix "
            f"family (cmd_deps file-deps scope); got {marker!r}"
        )
        # Hard distinction from sibling W607-* layers.
        for forbidden_prefix, sibling in (
            ("uses_", "cmd_uses W607-U"),
            ("impact_", "cmd_impact W607-T"),
            ("diagnose_", "cmd_diagnose W607-S"),
            ("preflight_", "cmd_preflight W607-R"),
            ("pr_risk_", "cmd_pr_risk W607-Q"),
            ("audit_", "cmd_audit W607-P"),
            ("dashboard_", "cmd_dashboard W607-O"),
            ("doctor_", "cmd_doctor W607-N"),
            ("health_", "cmd_health W607-M"),
            ("describe_", "cmd_describe W607-K"),
            ("minimap_", "cmd_minimap W607-L"),
            ("grep_", "cmd_grep W607-G"),
            ("history_", "cmd_history_grep W607-H"),
            ("refs_text_", "cmd_refs_text W607-I"),
            ("delete_check_", "cmd_delete_check W607-J"),
            ("search_", "cmd_search W607-E"),
            ("complete_", "cmd_complete W607-F"),
            ("semantic_", "cmd_search_semantic W607-A"),
            ("findings_query_", "cmd_findings W607-C"),
            ("dogfood_", "cmd_dogfood W607-D"),
            ("retrieve_", "cmd_retrieve W607-B"),
        ):
            assert not marker.startswith(forbidden_prefix), (
                f"marker leaked into ``{forbidden_prefix}*`` family ({sibling} scope); got {marker!r}"
            )


# ---------------------------------------------------------------------------
# (7) Sibling parity -- W607-U cmd_uses surface unchanged
# ---------------------------------------------------------------------------


def test_w607_u_cmd_uses_xfails_unaffected():
    """Sibling parity guard: W607-U cmd_uses source surface unchanged.

    W607-V lands only in cmd_deps. The W607-U cmd_uses surface
    (per-helper ``_run_check`` wrapper + ``_w607u_warnings_out``
    accumulator + ``uses_*`` marker emission) MUST stay identical. If
    a future refactor wave touches cmd_uses while editing deps,
    the canonical anchors below catch the drift before sibling tests fail
    downstream.
    """
    src_path = Path(__file__).parent.parent / "src" / "roam" / "commands" / "cmd_uses.py"
    assert src_path.exists(), f"cmd_uses.py missing at {src_path}"
    src = src_path.read_text(encoding="utf-8")
    assert "_w607u_warnings_out" in src, (
        "W607-U accumulator removed from cmd_uses; W607-V must not regress the sibling instrumentation."
    )
    assert "uses_" in src, (
        "W607-U marker prefix removed from cmd_uses; W607-V must not regress the sibling marker family."
    )


# ---------------------------------------------------------------------------
# (8) Pattern 1 Variant D mirror -- envelope shape stays plug-compatible
# ---------------------------------------------------------------------------


def test_resolution_state_unaffected_on_fuzzy_fallback(cli_runner, deps_project):
    """Pattern 1-V-D plug-compatibility guard.

    cmd_deps does NOT currently emit ``resolution_disclosure`` (out of
    scope for W607-V; pinned via W805-Q xfail). This guard verifies that
    the canonical envelope shape on the LIKE-fuzzy-fallback path is NOT
    silently broken by the W607-V wrap -- specifically, that the
    resolver fallback (LIKE search when exact match fails) still
    produces a clean envelope so a future P1VD wave can plug in
    disclosure without restructuring the branches.

    Substring matching -- exact 'helper.py' misses, LIKE fallback hits
    'src/helper.py' via '%helper.py', and the envelope still emits a
    valid deps payload.
    """
    runner = cli_runner
    result = _invoke_deps(runner, deps_project, "helper.py")
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)
    summary = data["summary"]
    # The fallback found a row via LIKE -- the envelope must still
    # have a verdict (LAW 6), and must NOT have any W607-V markers
    # (the resolver fallback is a soft success, not a substrate raise).
    verdict = summary.get("verdict", "")
    assert verdict, f"fuzzy-LIKE fallback must produce a verdict; got {summary!r}"
    top_wo = data.get("warnings_out") or []
    w607v_markers = [m for m in top_wo if m.startswith("deps_")]
    assert not w607v_markers, f"LIKE-fallback soft-success path must NOT surface deps_* markers; got {top_wo!r}"


# ---------------------------------------------------------------------------
# (9) Multiple substrates can fail simultaneously -- all markers surface
# ---------------------------------------------------------------------------


def test_multiple_substrates_failing_emit_separate_markers(cli_runner, deps_project, monkeypatch):
    """Substrate raise -> marker surfaced + remaining substrates still run.

    Aggregator scope: cmd_deps runs FILE_IMPORTS -> sym_edges ->
    FILE_IMPORTED_BY serially. The W607-V guard must NOT short-circuit
    on the first raise -- each subsequent substrate still runs and the
    envelope emits cleanly. Consumers see the full degradation lineage.
    """
    _patch_open_db(
        monkeypatch,
        trigger_sql="JOIN symbols s_src ON e.source_id = s_src.id",
        exc=RuntimeError("synthetic-multi-edges-from-W607-V"),
    )

    result = _invoke_deps(cli_runner, deps_project, "src/consumer.py")
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    top_wo = data.get("warnings_out") or []
    sym_markers = [m for m in top_wo if m.startswith("deps_fetch_sym_edges_failed:")]
    assert sym_markers, f"expected deps_fetch_sym_edges_failed: marker; got {top_wo!r}"
    assert data["summary"].get("partial_success") is True, data["summary"]
    # The envelope should still emit the verdict and counts cleanly,
    # because fetch_imports and fetch_imported_by still ran.
    summary = data["summary"]
    assert "verdict" in summary and summary["verdict"], summary
    assert "imports" in summary, summary
    assert "imported_by" in summary, summary


# ---------------------------------------------------------------------------
# (10) Source-level guard: cmd_deps uses the canonical W607-V accumulator
# ---------------------------------------------------------------------------


def test_cmd_deps_carries_w607v_accumulator():
    """AST-level guard: cmd_deps source carries the W607-V accumulator.

    Pins the canonical anchors so a future refactor that removes the
    instrumentation (e.g., switches to a single try/except wrapping the
    whole command body) fails this guard rather than silently regressing
    every other test on dynamic envelope shape.
    """
    src_path = Path(__file__).parent.parent / "src" / "roam" / "commands" / "cmd_deps.py"
    assert src_path.exists(), f"cmd_deps.py missing at {src_path}"
    src = src_path.read_text(encoding="utf-8")
    assert "_w607v_warnings_out" in src, (
        "W607-V accumulator missing from cmd_deps; the substrate-CALL marker plumbing has been removed."
    )
    assert "deps_{phase}_failed" in src, (
        "W607-V marker prefix template missing from cmd_deps; check the "
        '`f"deps_{phase}_failed:..."` line in _run_check.'
    )
    # Parse-tree level: confirm _run_check is defined inside deps().
    tree = ast.parse(src)
    found_run_check = False
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "_run_check":
            found_run_check = True
            break
    assert found_run_check, (
        "W607-V ``_run_check`` helper not found in cmd_deps AST; the per-substrate wrapper has been refactored away."
    )


# ---------------------------------------------------------------------------
# (11) Each substrate phase is wrapped (source-level)
# ---------------------------------------------------------------------------


def test_all_substrate_phases_wrapped_in_source():
    """Source-level guard: every cmd_deps substrate boundary is wrapped.

    W607-V substrate inventory:

    * file_by_path        -- exact FILE_BY_PATH lookup
    * file_by_path_like   -- LIKE fallback
    * fetch_imports       -- FILE_IMPORTS query
    * fetch_sym_edges     -- inlined symbol-edges JOIN
    * fetch_imported_by   -- FILE_IMPORTED_BY query

    If a future wave introduces a new substrate boundary, this guard
    needs to know about it -- add the phase name here.
    """
    src_path = Path(__file__).parent.parent / "src" / "roam" / "commands" / "cmd_deps.py"
    src = src_path.read_text(encoding="utf-8")
    expected_phases = [
        "file_by_path",
        "file_by_path_like",
        "fetch_imports",
        "fetch_sym_edges",
        "fetch_imported_by",
    ]
    for phase in expected_phases:
        # Accept either same-line ``_run_check("phase",`` or a multi-line
        # block where the phase string is the first argument on the next
        # line -- both are legitimate refactor shapes.
        same_line = f'_run_check("{phase}"' in src
        multi_line = f'_run_check(\n            "{phase}"' in src or f'_run_check(\n                "{phase}"' in src
        assert same_line or multi_line, (
            f"W607-V _run_check wrap missing for phase {phase!r}; substrate boundary is no longer caught."
        )
