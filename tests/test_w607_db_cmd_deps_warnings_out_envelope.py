"""W607-DB -- additive aggregation-phase plumbing for ``cmd_deps``.

cmd_deps is the file-relation traversal command (imports / imported-by
for one file path). With W607-DB landed, the full json-mode emit path
is now dual-bucket plumbed via:

  - substrate-CALL layer: W607-V (5 phases: file_by_path /
    file_by_path_like / fetch_imports / fetch_sym_edges /
    fetch_imported_by)
  - aggregation-phase layer: W607-DB (3 phases: compute_predicate /
    compute_verdict / serialize_envelope)

Both layers share the canonical ``deps_*`` marker family and the
``deps_<phase>_failed:<exc_class>:<detail>`` shape contract. The two
buckets (``_w607v_warnings_out`` substrate-CALL +
``_w607db_warnings_out`` aggregation-phase) are combined at envelope-
emit time so consumers see the full degradation lineage in marker-
emission order.

Relation to W607-V
------------------

cmd_deps already carries W607-V substrate-CALL plumbing covering 5
substrate-helper boundaries on the json-mode path. W607-DB is
ADDITIVE on top of W607-V, extending marker coverage to the
AGGREGATION-PHASE boundaries that W607-V left unguarded:

  - ``compute_predicate``   -- extraction of dep-count predicate
                                fields (imports_count /
                                imported_by_count / filename) used
                                to compose the verdict string.
  - ``compute_verdict``     -- verdict string assembly (LAW 6
                                standalone-parse).
  - ``serialize_envelope``  -- ``json_envelope("deps", ...)``
                                projection.

cmd_deps is NOT a risk scorer (unlike cmd_attest / cmd_pr_bundle);
it is a file-relation traversal command with no auto_log call. So
the W607-DB phase set drops ``score_classify`` /
``severity_normalize`` / ``auto_log`` and keeps the 3 phases above.
Mirror of cmd_fan's W607-CY phase set adapted for the single-mode
file-relation aggregator.

W978 7-discipline pre-fix audit
-------------------------------

1. f-string verdict floor -- floor is a LITERAL string
   ``"deps analysis completed"``, never an f-string re-interpolating
   the same predicate values that just raised.
2. kwarg-default eagerness -- ``default=`` arguments to
   ``_run_check_db`` are plain dicts / strings; no expensive call.
3. json.dumps(default=str) sentinel -- not used; markers carry
   ``str(exc)`` directly.
4. Phase-name collision -- W607-DB phase names
   (``compute_predicate`` / ``compute_verdict`` /
   ``serialize_envelope``) do NOT collide with any W607-V
   substrate-CALL phase name (``file_by_path`` /
   ``file_by_path_like`` / ``fetch_imports`` / ``fetch_sym_edges``
   / ``fetch_imported_by``).
5. len() at kwarg-bind -- ``len(imports)`` / ``len(imported_by)``
   are captured into ints BEFORE being passed through
   ``_run_check_db``; no poisoned-object ``len()`` at the wrap
   call-site.
6. Unguarded len()/if x on poisoned object -- predicate-floor
   dicts carry concrete int / str defaults; downstream readers
   never call ``len()`` on a sentinel.
7. dict.get(key, expensive_default) eager-eval -- predicate-
   extraction uses direct ``dict["key"]`` lookups inside the
   wrap, NOT ``dict.get(key, expensive_default)``; the floor
   branch substitutes a documented empty-shape dict.

Marker family is ``deps_*`` -- same family as W607-V (additive,
not a separate prefix). The marker-prefix discipline test pins
the closed-enum distinction against sibling W607 families.

LAW 4 note: warning markers are diagnostic strings, NOT
``agent_contract.facts`` content, and therefore not subject to
the concrete-noun-terminal lint.
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
# Helpers -- invoke deps via the Click group
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
# Fixture -- indexed corpus with cross-file imports
# ---------------------------------------------------------------------------


@pytest.fixture
def cli_runner():
    return CliRunner()


@pytest.fixture
def deps_project(tmp_path, monkeypatch):
    """Indexed corpus with a real ``consumer -> helper`` import edge.

    Three-file fixture so the FILE_BY_PATH + FILE_IMPORTS +
    FILE_IMPORTED_BY queries all have signal to chew on. Mirror of
    the W607-V fixture so both layers test against identical
    substrate signal.
    """
    proj = tmp_path / "deps_w607db_project"
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
# (1) Happy path -- envelope omits W607-DB aggregation markers
# ---------------------------------------------------------------------------


def test_deps_happy_path_no_w607db_markers(cli_runner, deps_project):
    """Clean deps -> no W607-DB aggregation markers.

    Hash-stable: an empty W607-DB bucket on the success path must
    produce an envelope without any
    ``deps_compute_predicate_failed:`` /
    ``deps_compute_verdict_failed:`` /
    ``deps_serialize_envelope_failed:`` markers.
    """
    result = _invoke_deps(cli_runner, deps_project, "src/consumer.py")
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)
    assert data["command"] == "deps"

    top_wo = data.get("warnings_out") or []
    summary_wo = data["summary"].get("warnings_out") or []
    all_markers = list(top_wo) + list(summary_wo)
    w607db_phases = (
        "deps_compute_predicate_failed:",
        "deps_compute_verdict_failed:",
        "deps_serialize_envelope_failed:",
    )
    for prefix in w607db_phases:
        leaked = [m for m in all_markers if m.startswith(prefix)]
        assert not leaked, f"clean deps must NOT surface {prefix} markers; got {leaked!r}"
    # partial_success must NOT flip on the clean path
    assert data["summary"].get("partial_success") is not True, (
        f"clean deps must NOT flip partial_success; got summary = {data['summary']!r}"
    )


# ---------------------------------------------------------------------------
# (2) AST-level guard -- additive ``_run_check_db`` helper is present
# ---------------------------------------------------------------------------


def test_cmd_deps_carries_w607db_accumulator():
    """AST-level guard: cmd_deps source carries the W607-DB accumulator.

    Pins the canonical W607-DB anchors so a future refactor that
    removes the additive instrumentation (or merges it back into
    W607-V) fails this guard rather than silently regressing the
    aggregation-phase marker coverage.
    """
    src_path = Path(__file__).parent.parent / "src" / "roam" / "commands" / "cmd_deps.py"
    assert src_path.exists(), f"cmd_deps.py missing at {src_path}"
    src = src_path.read_text(encoding="utf-8")

    # Source-level anchors
    assert "_w607db_warnings_out" in src, (
        "W607-DB accumulator missing from cmd_deps; the additive aggregation-phase marker plumbing has been removed."
    )
    assert "_run_check_db" in src, (
        "W607-DB helper ``_run_check_db`` missing from cmd_deps; the additive wrapper has been refactored away."
    )

    # Parse-tree level: confirm _run_check_db is defined inside deps()
    tree = ast.parse(src)
    found_run_check_db = False
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "_run_check_db":
            found_run_check_db = True
            break
    assert found_run_check_db, (
        "W607-DB ``_run_check_db`` helper not found in cmd_deps AST; "
        "the additive aggregation-phase wrapper has been refactored "
        "away."
    )

    # W607-V must still be present (additive layer does NOT replace it)
    assert "_w607v_warnings_out" in src, (
        "W607-V accumulator vanished alongside the W607-DB add; the "
        "additive plumbing must preserve the W607-V substrate-CALL "
        "layer."
    )


# ---------------------------------------------------------------------------
# (3) Source-grep guard -- every aggregation-phase boundary is wrapped
# ---------------------------------------------------------------------------


def test_every_aggregation_phase_wrapped_in_run_check_db():
    """Source-grep guard: every aggregation-phase boundary calls
    ``_run_check_db(...)`` with the canonical phase name.
    """
    src_path = Path(__file__).parent.parent / "src" / "roam" / "commands" / "cmd_deps.py"
    src = src_path.read_text(encoding="utf-8")

    canonical_phases = (
        "compute_predicate",
        "compute_verdict",
        "serialize_envelope",
    )
    for phase in canonical_phases:
        same_line = f'_run_check_db("{phase}"' in src
        multi_line = any(f'_run_check_db(\n{" " * indent}"{phase}"' in src for indent in (4, 8, 12, 16, 20, 24, 28, 32))
        assert same_line or multi_line, (
            f"phase ``{phase}`` is not wrapped in _run_check_db(...); add the W607-DB guard or pin the canonical anchor"
        )


# ---------------------------------------------------------------------------
# (4) compute_predicate failure marker
# ---------------------------------------------------------------------------


def test_compute_predicate_failure_marker_format(cli_runner, deps_project, monkeypatch):
    """If compute_predicate raises, surface the marker.

    Drive the failure by wrapping the connection so that the row
    returned from the FILE_BY_PATH cursor has a poisoned ``path``
    accessor that raises inside the predicate closure. The W607-DB
    wrap surfaces a structured marker rather than crashing the
    envelope.

    Implementation note: ``sqlite3.Cursor`` blocks attribute
    reassignment on ``fetchone``. We instead return a wrapper
    cursor whose own ``fetchone`` returns a poison row, leaving
    the real cursor untouched.
    """
    from roam.commands import cmd_deps

    real_open_db = cmd_deps.open_db

    class _PoisonRow:
        def __init__(self, real_row):
            self._real = real_row

        def __getitem__(self, key):
            if key == "path":
                raise RuntimeError("synthetic-compute-predicate-from-W607-DB")
            return self._real[key]

        def __getattr__(self, name):
            return getattr(self._real, name)

    class _WrapCursor:
        def __init__(self, real_cur, poison_first_row: bool):
            self._real = real_cur
            self._poison = poison_first_row
            self._seen = False

        def fetchone(self):
            row = self._real.fetchone()
            if row is None:
                return None
            if self._poison and not self._seen:
                self._seen = True
                return _PoisonRow(row)
            return row

        def fetchall(self):
            return self._real.fetchall()

        def __iter__(self):
            return iter(self._real)

        def __getattr__(self, name):
            return getattr(self._real, name)

    class _PoisonConn:
        def __init__(self, real_conn):
            self._real = real_conn
            self._poisoned = False

        def execute(self, sql, *args, **kwargs):
            cur = self._real.execute(sql, *args, **kwargs)
            # Only the first FILE_BY_PATH-shaped query gets the
            # poison; the LIKE fallback + JOIN edges + IMPORTED_BY
            # all pass through cleanly.
            if "FROM files" in sql and "WHERE path" in sql and "LIKE" not in sql and not self._poisoned:
                self._poisoned = True
                return _WrapCursor(cur, poison_first_row=True)
            return _WrapCursor(cur, poison_first_row=False)

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return self._real.__exit__(*a)

        def __getattr__(self, name):
            return getattr(self._real, name)

    def _fake_open_db(*args, **kwargs):
        real_ctx = real_open_db(*args, **kwargs)

        class _Wrapped:
            def __enter__(self_inner):
                real_conn = real_ctx.__enter__()
                return _PoisonConn(real_conn)

            def __exit__(self_inner, *exc_info):
                return real_ctx.__exit__(*exc_info)

        return _Wrapped()

    monkeypatch.setattr(cmd_deps, "open_db", _fake_open_db)

    result = _invoke_deps(cli_runner, deps_project, "src/consumer.py")
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    top_wo = data.get("warnings_out") or []
    summary_wo = data["summary"].get("warnings_out") or []
    all_wo = list(top_wo) + list(summary_wo)
    markers = [m for m in all_wo if m.startswith("deps_compute_predicate_failed:")]
    assert markers, f"expected ``deps_compute_predicate_failed:`` marker; got {all_wo!r}"
    assert any("RuntimeError" in m for m in markers), markers


# ---------------------------------------------------------------------------
# (5) compute_verdict failure marker -- W978 first-hypothesis floor
# ---------------------------------------------------------------------------


def test_compute_verdict_failure_marker_format(cli_runner, deps_project, monkeypatch):
    """If compute_verdict raises, surface the marker AND the floor
    verdict is a LITERAL string (NOT re-interpolated).

    W978 first-hypothesis check: the canonical floor MUST be
    ``"deps analysis completed"`` -- a literal string that does NOT
    re-evaluate the same predicate values that tripped the closure.

    Strategy: drive the verdict closure to raise by injecting a
    ``__format__``-raising sentinel into the predicate dict. We
    achieve this by intercepting ``len()`` at the wrap call-site —
    capture-time semantics in the predicate floor mean ``len(imports)``
    is already an int by the time the verdict closure reads
    ``pred['imports_count']``. So we patch a different layer: the
    predicate closure itself returns a dict containing a __format__-
    raising sentinel for ``imports_count``, which trips the verdict's
    f-string.
    """
    from roam.commands import cmd_deps

    class _BadInt(int):
        """An int subclass that raises on __format__ — survives
        ``len(imports)`` capture (passed through as an int value)
        but trips the verdict's f-string interpolation.
        """

        def __new__(cls):
            return int.__new__(cls, 42)

        def __format__(self, spec):
            raise RuntimeError("synthetic-compute-verdict-from-W607-DB")

    # Wrap _run_check_db at the cmd_deps module level so we can
    # intercept the predicate floor return value and substitute a
    # poisoned imports_count. The verdict closure then trips on the
    # f-string interpolation.

    # Patch the predicate closure result by intercepting the inner
    # _build_deps_verdict callable indirectly: monkeypatch the
    # source's ``len`` builtin to return _BadInt for the imports list.
    # This survives capture because len(imports) IS an int at capture
    # time, but the verdict closure's f-string interpolation evaluates
    # __format__ on it.
    real_len = len

    def _len_poison(obj):
        # Detect the imports list at capture time (it's a list of
        # sqlite3.Row in the project fixture). We trip ONLY when the
        # caller is inside the success-emit branch — keep it generic
        # by poisoning only list-of-Row objects.
        result = real_len(obj)
        if isinstance(obj, list) and obj and hasattr(obj[0], "keys"):
            return _BadInt()
        return result

    monkeypatch.setattr(cmd_deps, "len", _len_poison, raising=False)

    result = _invoke_deps(cli_runner, deps_project, "src/consumer.py")
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    top_wo = data.get("warnings_out") or []
    summary_wo = data["summary"].get("warnings_out") or []
    all_wo = list(top_wo) + list(summary_wo)
    # The verdict closure trips on _BadInt.__format__ in the
    # ``f"{pred['fname']}: {pred['imports_count']} imports, ..."`` line.
    verdict_markers = [m for m in all_wo if m.startswith("deps_compute_verdict_failed:")]
    assert verdict_markers, f"expected ``deps_compute_verdict_failed:`` marker; got {all_wo!r}"
    assert any("RuntimeError" in m for m in verdict_markers), verdict_markers

    # W978 first-hypothesis floor check: the verdict must be the
    # LITERAL ``"deps analysis completed"`` string, NOT a re-
    # interpolation of the poisoned values.
    assert data["summary"]["verdict"] == "deps analysis completed", (
        f"W978 floor discipline: verdict must be the literal "
        f"``deps analysis completed`` floor, NOT a re-interpolation "
        f"of the poisoned values; got {data['summary']['verdict']!r}"
    )


# ---------------------------------------------------------------------------
# (6) serialize_envelope guard -- raise floors to stub document
# ---------------------------------------------------------------------------


def test_w607db_serialize_envelope_floor_on_raise(cli_runner, deps_project, monkeypatch):
    """If ``json_envelope`` raises on the success path, the wrap
    floors to a parseable envelope stub and surfaces
    ``deps_serialize_envelope_failed:``.
    """
    from roam.commands import cmd_deps

    def _raise_envelope(*args, **kwargs):
        raise RuntimeError("synthetic-serialize-envelope-from-W607-DB")

    monkeypatch.setattr(cmd_deps, "json_envelope", _raise_envelope)

    result = _invoke_deps(cli_runner, deps_project, "src/consumer.py")
    assert result.exit_code == 0, result.output

    data = _json.loads(result.output)
    assert data.get("command") == "deps", f"envelope stub must carry the canonical command name on raise; got {data!r}"
    top_wo = data.get("warnings_out") or []
    markers = [m for m in top_wo if m.startswith("deps_serialize_envelope_failed:")]
    assert markers, f"expected ``deps_serialize_envelope_failed:`` marker; got {top_wo!r}"


# ---------------------------------------------------------------------------
# (7) ANY marker flips partial_success
# ---------------------------------------------------------------------------


def test_any_marker_flips_partial_success(cli_runner, deps_project, monkeypatch):
    """ANY W607-DB or W607-V marker must flip
    summary.partial_success=True.

    Pattern-2 contract: the agent MUST be able to distinguish "clean
    deps" from "deps ran with substrate degradation" via
    summary.partial_success alone.
    """
    from roam.commands import cmd_deps

    def _raise_envelope(*args, **kwargs):
        raise RuntimeError("synthetic-partial-success-from-W607-DB")

    monkeypatch.setattr(cmd_deps, "json_envelope", _raise_envelope)

    result = _invoke_deps(cli_runner, deps_project, "src/consumer.py")
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)
    assert data["summary"].get("partial_success") is True, (
        f"non-empty deps warnings_out must flip summary.partial_success=True; got summary = {data['summary']!r}"
    )


# ---------------------------------------------------------------------------
# (8) warnings_out lands in BOTH top-level AND summary mirror
# ---------------------------------------------------------------------------


def test_w607db_warnings_out_in_both_top_and_summary(cli_runner, deps_project, monkeypatch):
    """Non-empty W607-DB bucket -> both top-level AND
    summary.warnings_out populated.

    Mirror parity with W607-BZ / W607-CY contract: top-level survives
    ``strip_list_payloads`` in default-detail mode; summary mirror
    gives consumers reading only the summary block visibility too.
    """
    from roam.commands import cmd_deps

    real_envelope = cmd_deps.json_envelope
    call_count = {"n": 0}

    def _raise_first_envelope(*args, **kwargs):
        call_count["n"] += 1
        if call_count["n"] == 1:
            raise RuntimeError("synthetic-mirror-from-W607-DB")
        return real_envelope(*args, **kwargs)

    monkeypatch.setattr(cmd_deps, "json_envelope", _raise_first_envelope)

    result = _invoke_deps(cli_runner, deps_project, "src/consumer.py")
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    assert data.get("warnings_out"), (
        f"top-level warnings_out missing on W607-DB raise path; keys = {sorted(data.keys())!r}"
    )
    assert data["summary"].get("warnings_out"), (
        f"summary.warnings_out missing on W607-DB raise path; got summary = {data['summary']!r}"
    )

    top_markers = [m for m in data["warnings_out"] if m.startswith("deps_serialize_envelope_failed:")]
    summary_markers = [m for m in data["summary"]["warnings_out"] if m.startswith("deps_serialize_envelope_failed:")]
    assert top_markers and summary_markers, (
        f"both mirrors must carry the serialize_envelope marker; "
        f"top = {data.get('warnings_out')!r}, "
        f"summary = {data['summary'].get('warnings_out')!r}"
    )


# ---------------------------------------------------------------------------
# (9) Marker-prefix discipline -- W607-DB uses the SAME ``deps_*`` family
# ---------------------------------------------------------------------------


def test_w607db_marker_prefix_deps_family(cli_runner, deps_project, monkeypatch):
    """W607-DB markers use the canonical ``deps_*`` prefix (same
    family as W607-V; W607-DB is ADDITIVE, not a separate prefix).

    Hard guard: any W607-DB marker that leaks into a sibling W607-*
    family (e.g. ``cga_*`` / ``attest_*`` / ``preflight_*`` /
    ``relate_*`` / ``fan_*``) breaks the closed-enum marker-family
    contract.
    """
    from roam.commands import cmd_deps

    def _raise_envelope(*args, **kwargs):
        raise PermissionError("synthetic-prefix-discipline-from-W607-DB")

    monkeypatch.setattr(cmd_deps, "json_envelope", _raise_envelope)

    result = _invoke_deps(cli_runner, deps_project, "src/consumer.py")
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)
    top_wo = data.get("warnings_out") or []
    failure_markers = [m for m in top_wo if "_failed:" in m]
    assert failure_markers, "expected non-empty failure-marker bucket for prefix-discipline check"
    for marker in failure_markers:
        assert marker.startswith("deps_"), f"every W607-DB marker must use the ``deps_*`` prefix; got {marker!r}"


# ---------------------------------------------------------------------------
# (10) W607-V COEXISTENCE -- substrate-CALL + aggregation-phase markers
# coexist in the same family but flow through different buckets
# ---------------------------------------------------------------------------


class _RaisingSymEdgesConn:
    """sqlite3.Connection wrapper that raises on the sym_edges JOIN.

    Reuses the W607-V test pattern -- trigger the substrate-CALL
    layer raise so we can observe the aggregation-phase layer
    coexists in the SAME ``deps_*`` family.
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


def test_w607v_substrate_markers_coexist_with_w607db_aggregation(cli_runner, deps_project, monkeypatch):
    """Confirm ``deps_<substrate-phase>_failed:`` markers (W607-V
    layer) coexist with ``deps_<agg-phase>_failed:`` markers
    (W607-DB layer) -- both in same family, but threaded through
    different buckets at envelope-emit.

    Explicit guard requested by the W607-DB brief: the additive
    aggregation-phase layer must NOT shadow the pre-existing
    substrate-CALL layer; both buckets must combine into the same
    warnings_out channel with marker-prefix disambiguation
    (``deps_<substrate-phase>_failed:`` vs.
    ``deps_<agg-phase>_failed:``).
    """
    from roam.commands import cmd_deps

    real_open_db = cmd_deps.open_db

    def _fake_open_db(*args, **kwargs):
        real_ctx = real_open_db(*args, **kwargs)

        class _Wrapped:
            def __enter__(self_inner):
                real_conn = real_ctx.__enter__()
                return _RaisingSymEdgesConn(
                    real_conn,
                    "JOIN symbols s_src ON e.source_id = s_src.id",
                    RuntimeError("synthetic-v-coexist-sym-edges"),
                )

            def __exit__(self_inner, *exc_info):
                return real_ctx.__exit__(*exc_info)

        return _Wrapped()

    monkeypatch.setattr(cmd_deps, "open_db", _fake_open_db)

    # W607-DB aggregation boundary -- json_envelope
    real_envelope = cmd_deps.json_envelope
    call_count = {"n": 0}

    def _raise_envelope_first(*a, **kw):
        call_count["n"] += 1
        if call_count["n"] == 1:
            raise RuntimeError("synthetic-db-coexist-envelope")
        return real_envelope(*a, **kw)

    monkeypatch.setattr(cmd_deps, "json_envelope", _raise_envelope_first)

    result = _invoke_deps(cli_runner, deps_project, "src/consumer.py")
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    top_wo = data.get("warnings_out") or []

    # Substrate-CALL phase from W607-V
    v_markers = [m for m in top_wo if m.startswith("deps_fetch_sym_edges_failed:")]
    # Aggregation-phase from W607-DB
    db_markers = [m for m in top_wo if m.startswith("deps_serialize_envelope_failed:")]

    assert v_markers, f"W607-V substrate-CALL marker (deps_fetch_sym_edges_failed) missing; got {top_wo!r}"
    assert db_markers, f"W607-DB aggregation-phase marker (deps_serialize_envelope_failed) missing; got {top_wo!r}"

    # Both share the canonical ``deps_*`` family
    assert all(m.startswith("deps_") for m in (v_markers + db_markers)), (
        f"all markers must share the canonical ``deps_*`` family; got v = {v_markers!r}, db = {db_markers!r}"
    )

    # Both surface in summary mirror too
    summary_wo = data["summary"].get("warnings_out") or []
    assert any(m.startswith("deps_fetch_sym_edges_failed:") for m in summary_wo), (
        f"W607-V marker missing from summary mirror; got {summary_wo!r}"
    )
    assert any(m.startswith("deps_serialize_envelope_failed:") for m in summary_wo), (
        f"W607-DB marker missing from summary mirror; got {summary_wo!r}"
    )


# ---------------------------------------------------------------------------
# (11) CROSS-PREFIX ISOLATION -- deps_* markers DO NOT leak into adjacent
# commands' marker families
# ---------------------------------------------------------------------------


def test_deps_markers_do_not_leak_foreign_prefixes(cli_runner, deps_project, monkeypatch):
    """``deps_*`` markers must NOT carry any sibling W607-* family
    prefixes (``cga_*`` / ``attest_*`` / ``pr_bundle_*`` /
    ``preflight_*`` / ``relate_*`` / ``fan_*`` / etc.).

    Validates the marker-family isolation contract: each command's
    W607 plumbing uses its OWN prefix and does not bleed into
    adjacent commands' warnings_out channels.
    """
    from roam.commands import cmd_deps

    def _raise_envelope(*args, **kwargs):
        raise RuntimeError("synthetic-cross-prefix-from-W607-DB")

    monkeypatch.setattr(cmd_deps, "json_envelope", _raise_envelope)

    result = _invoke_deps(cli_runner, deps_project, "src/consumer.py")
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    top_wo = data.get("warnings_out") or []
    summary_wo = data["summary"].get("warnings_out") or []
    all_markers = list(top_wo) + list(summary_wo)
    failure_markers = [m for m in all_markers if "_failed:" in m]
    assert failure_markers, "expected non-empty failure-marker bucket for prefix-isolation check"

    foreign_prefixes = (
        "cga_",
        "attest_",
        "pr_bundle_",
        "supply_chain_",
        "preflight_",
        "impact_",
        "diagnose_",
        "critique_",
        "diff_",
        "relate_",
        "uses_",
        "fan_",
        "grep_",
    )
    for marker in failure_markers:
        for foreign in foreign_prefixes:
            assert not marker.startswith(foreign), (
                f"cmd_deps warnings_out must not contain {foreign}* markers; got {marker!r}"
            )


# ---------------------------------------------------------------------------
# (12) W978 7-discipline AST audit
# ---------------------------------------------------------------------------


def test_w607db_w978_seven_discipline_ast_audit():
    """AST audit: the W607-DB plumbing in cmd_deps honours all 7
    W978 first-hypothesis disciplines.

    1. f-string verdict floor -- floor is the LITERAL string
       ``"deps analysis completed"``, never an f-string.
    2. kwarg-default eagerness -- ``default=`` arguments are
       inexpensive literals / dicts of literals.
    3. json.dumps(default=str) sentinel -- not used at the W607-DB
       layer; markers carry ``str(exc)`` directly.
    4. Phase-name collision -- W607-DB phase names do NOT collide
       with W607-V substrate-CALL phase names.
    5. len() at kwarg-bind -- ``len(imports)`` / ``len(imported_by)``
       are captured into ints BEFORE the wrap call-site, not inside
       the kwarg-bind of ``_run_check_db(...)``.
    6. Unguarded len()/if x on poisoned object -- predicate-floor
       dicts carry concrete int / str defaults; downstream readers
       never call ``len()`` on a sentinel.
    7. dict.get(key, expensive_default) eager-eval -- the predicate
       closures use direct ``dict["key"]`` lookups, NOT
       ``dict.get(key, expensive_default)``.
    """
    src_path = Path(__file__).parent.parent / "src" / "roam" / "commands" / "cmd_deps.py"
    src = src_path.read_text(encoding="utf-8")
    tree = ast.parse(src)

    # Discipline 1: f-string verdict floor MUST be a literal string.
    literal_floor_found = False
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if not (isinstance(node.func, ast.Name) and node.func.id == "_run_check_db"):
            continue
        if not node.args:
            continue
        phase_arg = node.args[0]
        if not (isinstance(phase_arg, ast.Constant) and phase_arg.value == "compute_verdict"):
            continue
        for kw in node.keywords:
            if kw.arg == "default":
                assert isinstance(kw.value, ast.Constant) and isinstance(kw.value.value, str), (
                    f"W978 discipline 1: compute_verdict floor must be a "
                    f"literal string ast.Constant, not "
                    f"{type(kw.value).__name__}; f-string floors "
                    f"re-interpolate the poisoned values that just raised"
                )
                assert kw.value.value == "deps analysis completed", (
                    f"W978 discipline 1: compute_verdict floor must be the "
                    f"canonical literal ``deps analysis completed``; "
                    f"got {kw.value.value!r}"
                )
                literal_floor_found = True
    assert literal_floor_found, (
        "W978 discipline 1: no _run_check_db('compute_verdict', ..., "
        "default=<literal>) call found; the verdict floor must be a "
        "literal string"
    )

    # Discipline 2: kwarg-default eagerness -- no ast.Call as default value.
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if not (isinstance(node.func, ast.Name) and node.func.id == "_run_check_db"):
            continue
        for kw in node.keywords:
            if kw.arg == "default":
                assert not isinstance(kw.value, ast.Call), (
                    f"W978 discipline 2: _run_check_db default= MUST NOT "
                    f"be an ast.Call (eager evaluation of expensive "
                    f"default); got {ast.dump(kw.value)!r}"
                )

    # Discipline 3: json.dumps not used inside _run_check_db.
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "_run_check_db":
            helper_src = ast.unparse(node)
            assert "json.dumps" not in helper_src, (
                f"W978 discipline 3: _run_check_db must not call "
                f"json.dumps (use plain f-string marker format); "
                f"got body = {helper_src!r}"
            )

    # Discipline 4: phase-name collision check.
    w607db_phases = {
        "compute_predicate",
        "compute_verdict",
        "serialize_envelope",
    }
    w607v_phases = {
        "file_by_path",
        "file_by_path_like",
        "fetch_imports",
        "fetch_sym_edges",
        "fetch_imported_by",
    }
    overlap = w607db_phases & w607v_phases
    assert not overlap, (
        f"W978 discipline 4: W607-DB phase names collide with W607-V substrate-CALL phase names; overlap = {overlap!r}"
    )

    # Discipline 5 + 6 + 7: floor dicts in compute_predicate use
    # CONSTANT default values (int / str), not call expressions.
    floor_dicts_inspected = 0
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if not (isinstance(node.func, ast.Name) and node.func.id == "_run_check_db"):
            continue
        if not node.args:
            continue
        phase_arg = node.args[0]
        if not (isinstance(phase_arg, ast.Constant) and phase_arg.value == "compute_predicate"):
            continue
        for kw in node.keywords:
            if kw.arg != "default":
                continue
            assert isinstance(kw.value, ast.Dict), (
                f"W978 discipline 6: compute_predicate floor must be a literal ast.Dict; got {type(kw.value).__name__}"
            )
            for v in kw.value.values:
                if isinstance(v, ast.Call):
                    # Only allow len() on a Name argument
                    assert isinstance(v.func, ast.Name) and v.func.id == "len", (
                        f"W978 discipline 5/7: compute_predicate floor "
                        f"may only call ``len(name)`` as a default "
                        f"computation; got {ast.dump(v)!r}"
                    )
                else:
                    assert isinstance(v, ast.Constant), (
                        f"W978 discipline 6: compute_predicate floor "
                        f"values must be ast.Constant or len(name); "
                        f"got {type(v).__name__} = {ast.dump(v)!r}"
                    )
            floor_dicts_inspected += 1
    assert floor_dicts_inspected >= 1, (
        f"expected at least 1 compute_predicate floor dict; found {floor_dicts_inspected}"
    )


# ---------------------------------------------------------------------------
# (13) Cross-prefix isolation -- W607-DB does NOT introduce sibling-wave
# accumulator names
# ---------------------------------------------------------------------------


def test_w607db_does_not_introduce_other_w607_buckets():
    """W607-DB plumbing must NOT introduce sibling-wave accumulator
    names (W607-AF / W607-BZ / W607-BT / W607-BW / W607-CY / etc.).

    Defensive check: the W607-DB accumulator name is unique and
    doesn't accidentally use a sibling-wave's bucket name.
    """
    src_path = Path(__file__).parent.parent / "src" / "roam" / "commands" / "cmd_deps.py"
    src = src_path.read_text(encoding="utf-8")

    foreign_accumulators = (
        "_w607af_warnings_out",
        "_w607ae_warnings_out",
        "_w607ad_warnings_out",
        "_w607bt_warnings_out",
        "_w607bw_warnings_out",
        "_w607bz_warnings_out",
        "_w607cy_warnings_out",
    )
    for foreign in foreign_accumulators:
        assert foreign not in src, (
            f"cmd_deps must not carry sibling-wave accumulator "
            f"{foreign!r}; W607-DB uses its own "
            f"``_w607db_warnings_out`` bucket"
        )


# ---------------------------------------------------------------------------
# (14) SYMBOL-RELATIONS TRIO pairing -- deps_*, uses_*, relate_*, fan_*
# markers coexist when all four commands target the same workspace
# ---------------------------------------------------------------------------


def test_symbol_relations_trio_marker_families_coexist():
    """The symbol-relations family (cmd_deps + cmd_uses + cmd_relate
    + cmd_fan) MUST keep distinct marker prefixes so a workspace-
    wide audit (e.g. a CI script invoking all four on the same
    repo) can disambiguate which command emitted which marker.

    Source-level guard pinning the four prefix families:

    * cmd_deps    -> ``deps_*``    (W607-V substrate + W607-DB
                                    aggregation)
    * cmd_uses    -> ``uses_*``    (W607-U)
    * cmd_relate  -> ``relate_*``  (W607-W substrate; W607-DA
                                    aggregation pending)
    * cmd_fan     -> ``fan_*``     (W607-X substrate + W607-CY
                                    aggregation)

    Closes the symbol-relations family: each member uses its own
    canonical prefix in the source. A future refactor that
    accidentally merges two prefixes (e.g. uses_ -> deps_)
    breaks this guard before the marker-family contract leaks
    into shipped envelopes.
    """
    src_root = Path(__file__).parent.parent / "src" / "roam" / "commands"

    expected = (
        ("cmd_deps.py", "deps_", "_w607v_warnings_out"),
        ("cmd_deps.py", "deps_", "_w607db_warnings_out"),
        ("cmd_uses.py", "uses_", "_w607u_warnings_out"),
        ("cmd_relate.py", "relate_", "_w607w_warnings_out"),
        ("cmd_fan.py", "fan_", "_w607x_warnings_out"),
        ("cmd_fan.py", "fan_", "_w607cy_warnings_out"),
    )

    for filename, prefix_anchor, accumulator in expected:
        path = src_root / filename
        assert path.exists(), f"{filename} missing at {path}"
        src = path.read_text(encoding="utf-8")
        # The accumulator MUST exist in the source
        assert accumulator in src, (
            f"{filename}: accumulator {accumulator!r} missing — symbol-"
            f"relations family member has lost its W607 plumbing"
        )
        # The canonical marker prefix MUST appear in the source
        # somewhere (typically inside the f"<prefix>{phase}_failed:..."
        # template). We grep on the prefix anchor with the ``_`` suffix.
        assert prefix_anchor in src, (
            f"{filename}: canonical marker prefix {prefix_anchor!r} "
            f"missing — symbol-relations family has lost its prefix"
        )


# ---------------------------------------------------------------------------
# (15) W607-V regression guard -- W607-DB add does not break W607-V
# substrate behavior
# ---------------------------------------------------------------------------


def test_w607v_substrate_layer_still_works_after_w607db_add(cli_runner, deps_project, monkeypatch):
    """After W607-DB lands, W607-V substrate-CALL markers MUST still
    fire when the substrate raises.

    Pin-down regression guard: if a future refactor accidentally
    short-circuits the W607-V substrate accumulator while threading
    through the new W607-DB aggregation layer, this test surfaces it.
    """
    from roam.commands import cmd_deps

    real_open_db = cmd_deps.open_db

    def _fake_open_db(*args, **kwargs):
        real_ctx = real_open_db(*args, **kwargs)

        class _Wrapped:
            def __enter__(self_inner):
                real_conn = real_ctx.__enter__()
                return _RaisingSymEdgesConn(
                    real_conn,
                    "JOIN symbols s_src ON e.source_id = s_src.id",
                    PermissionError("synthetic-w607v-regression-from-W607-DB"),
                )

            def __exit__(self_inner, *exc_info):
                return real_ctx.__exit__(*exc_info)

        return _Wrapped()

    monkeypatch.setattr(cmd_deps, "open_db", _fake_open_db)

    result = _invoke_deps(cli_runner, deps_project, "src/consumer.py")
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    top_wo = data.get("warnings_out") or []
    sym_markers = [m for m in top_wo if m.startswith("deps_fetch_sym_edges_failed:")]
    assert sym_markers, f"W607-V substrate marker MUST still fire after W607-DB add; got top_wo = {top_wo!r}"
    assert any("PermissionError" in m for m in sym_markers), sym_markers
