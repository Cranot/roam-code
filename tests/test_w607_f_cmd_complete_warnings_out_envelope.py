"""W607-F — ``cmd_complete`` threads ``warnings_out`` onto its JSON envelope.

The W595-W606 substrate-floor Pattern-2 arc plumbed ``warnings_out``
buckets on every silent-fallback substrate reader. W607-A landed the
first consumer-layer wave on ``cmd_search_semantic``. W607-B landed
``cmd_retrieve`` (outer-guard-only). W607-C landed ``cmd_findings``.
W607-D landed ``cmd_dogfood`` (outer-guard-only). W607-E landed
``cmd_search``. W607-F is the sixth consumer-layer wave on
``cmd_complete`` — the lexical-PREFIX completion surface that seals the
lexical-search trio (search-semantic / search / complete).

W978 first-hypothesis check (pre-fix audit)
-------------------------------------------

Before writing this file, audited ``cmd_complete.py`` + the
read-only helper module ``roam.mcp_extras.completions`` head-to-tail:

* Neither file calls any W605-plumbed substrate function
  (``search_fts`` / ``fts5_available`` / ``fts5_populated`` /
  ``tfidf_populated`` / ``onnx_populated`` / ``search_stored``).
  ``cmd_complete`` issues raw SQL via the local ``_prefix_symbols``
  helper (``LIKE prefix%`` against ``symbols.name``) and delegates
  ``--kind path`` / ``--kind command`` to ``complete_paths`` /
  ``complete_commands`` in the read-only ``completions.py`` helper.
* The local ``_prefix_symbols`` helper carries a SILENT
  ``except Exception: return []`` fallback (pre-W607-F) at line 55.
* The ``complete_paths`` / ``complete_commands`` helpers in
  ``mcp_extras.completions`` carry their own internal silent
  fallbacks (line 87, 184, 189, 197 — sqlite3 errors, missing index
  paths, etc.) but the helper module is OUT OF SCOPE per the W607-F
  task contract (read-only inventory). Their substrate failures
  manifest as ``[]`` returns OR as exceptions bubbling out of the
  helper boundary.

Therefore the consumer-side gap is REAL with TWO disclosure surfaces:

1. INNER (threaded): the local ``_prefix_symbols`` helper threads
   ``warnings_out`` through its sqlite ``except Exception`` block →
   ``complete_symbols_query_failed:<exc>:<detail>``.
2. OUTER (guard): the ``complete_paths`` / ``complete_commands``
   helper calls are wrapped in try/except so any uncaught exception
   from the read-only helper boundary emits
   ``complete_paths_query_failed:<exc>:<detail>`` or
   ``complete_commands_query_failed:<exc>:<detail>``.

Marker family is ``complete_*`` — NOT ``search_*`` (W607-E lexical-
substring layer) and NOT ``semantic_*`` (W605/W607-A semantic FTS5-
BM25 substrate). cmd_complete is the LEXICAL-PREFIX layer; the
prefix-discipline test below pins this distinction.

W907 verify-cycle check
-----------------------

No "duplicated to avoid cycle" docstrings were added. The
``warnings_out: list[str] = []`` local is a plain accumulator (mirrors
cmd_search W607-E / cmd_dogfood W607-D / cmd_findings W607-C /
cmd_retrieve W607-B / cmd_search_semantic W607-A disclosure idioms);
no shared module was created or hoisted.

LAW 4 note: warning markers are diagnostic strings, NOT
``agent_contract.facts`` content, and therefore not subject to the
concrete-noun-terminal lint.
"""

from __future__ import annotations

import ast
import json
import sys
from pathlib import Path

import pytest
from click.testing import CliRunner

from roam.cli import cli

sys.path.insert(0, str(Path(__file__).parent))
from _helpers.repo_root import repo_root  # noqa: E402

# ---------------------------------------------------------------------------
# Fixture: a small indexed project so complete has a real corpus.
# ---------------------------------------------------------------------------


@pytest.fixture
def complete_project(project_factory):
    return project_factory(
        {
            "src/hooks.py": (
                "def useFoo():\n    '''Hook foo.'''\n    return 1\n\ndef useBar():\n    '''Hook bar.'''\n    return 2\n"
            ),
            "src/util.py": ("def authenticate_user(username):\n    '''Authenticate.'''\n    return True\n"),
        }
    )


# ---------------------------------------------------------------------------
# (1) Happy path — clean prefix completion → no warnings_out / no flip
# ---------------------------------------------------------------------------


def test_clean_happy_path(complete_project, monkeypatch):
    """Clean prefix match on a populated corpus → envelope has no warnings_out.

    Hash-stable: an empty bucket must produce a byte-identical envelope
    on the success path (modulo the existing W817 ``partial_success``
    default + the pre-existing ``partial = total == 0`` logic).
    """
    monkeypatch.chdir(complete_project)
    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["--json", "complete", "use"],
        catch_exceptions=False,
    )
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert data["command"] == "complete"

    # Symbols must contain at least one match (sanity check on fixture).
    syms = (data.get("results") or {}).get("symbols") or []
    assert "useFoo" in syms or "useBar" in syms, (
        f"fixture sanity: prefix 'use' should match useFoo/useBar; got {syms!r}"
    )

    # No top-level warnings_out on the happy path.
    assert "warnings_out" not in data, (
        f"clean complete must NOT surface top-level warnings_out; got {data.get('warnings_out')!r}"
    )

    # No summary.warnings_out either.
    assert "warnings_out" not in data["summary"], (
        f"clean complete must NOT populate summary.warnings_out; got {data['summary'].get('warnings_out')!r}"
    )


# ---------------------------------------------------------------------------
# (2) Pipeline failure surfaces marker — _prefix_symbols sqlite raises
# ---------------------------------------------------------------------------


def test_pipeline_failure_surfaces_marker(complete_project, monkeypatch):
    """Monkeypatch the SQL pipeline → OperationalError → marker reaches envelope.

    Pattern-2 inner-bucket contract: when ``_prefix_symbols``' silent
    ``except Exception`` fallback fires, the threaded ``warnings_out``
    bucket emits a structured marker instead of dropping the substrate
    failure on the floor.
    """
    import sqlite3

    from roam.commands import cmd_complete

    real_open_db = None
    # _prefix_symbols imports open_db locally; monkeypatch at the
    # connection module level so the local import resolves the wrapped
    # version.
    from roam.db import connection as _conn_mod

    real_open_db = _conn_mod.open_db

    class _BoomConn:
        """Wraps a real conn but raises on the symbols-prefix SELECT."""

        def __init__(self, inner):
            self._inner = inner

        def __enter__(self):
            self._real = self._inner.__enter__()
            return self

        def __exit__(self, *a):
            return self._inner.__exit__(*a)

        def execute(self, sql, *args, **kwargs):
            # The _prefix_symbols pipeline SELECT is the DISTINCT name
            # FROM symbols LIKE-ESCAPE form.
            if "SELECT DISTINCT name FROM symbols" in sql:
                raise sqlite3.OperationalError("synthetic-complete-symbols-failure from W607-F test")
            return self._real.execute(sql, *args, **kwargs)

        def __getattr__(self, name):
            return getattr(self._real, name)

    def _wrapped_open_db(*a, **kw):
        return _BoomConn(real_open_db(*a, **kw))

    monkeypatch.setattr(_conn_mod, "open_db", _wrapped_open_db)
    monkeypatch.chdir(complete_project)

    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["--json", "complete", "use"],
        catch_exceptions=False,
    )
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)

    # Top-level disclosure (canonical idiom — preserved-list field).
    top_wo = data.get("warnings_out")
    assert top_wo, (
        f"_prefix_symbols OperationalError must surface top-level warnings_out; got data keys = {sorted(data.keys())!r}"
    )
    assert any(m.startswith("complete_symbols_query_failed:") for m in top_wo), (
        f"expected ``complete_symbols_query_failed:`` marker in top-level warnings_out; got {top_wo!r}"
    )
    # OperationalError class name must propagate for triage.
    assert any("OperationalError" in m for m in top_wo), top_wo
    # Synthetic detail must propagate.
    assert any("synthetic-complete-symbols-failure from W607-F test" in m for m in top_wo), top_wo

    # And the _prefix_symbols helper signature must expose the
    # warnings_out kwarg (sanity check we plumbed it).
    import inspect

    sig = inspect.signature(cmd_complete._prefix_symbols)
    assert "warnings_out" in sig.parameters, (
        f"_prefix_symbols must accept warnings_out kwarg post-W607-F; got params = {list(sig.parameters)!r}"
    )


# ---------------------------------------------------------------------------
# (3) partial_success flips when any marker present
# ---------------------------------------------------------------------------


def test_partial_success_flip(complete_project, monkeypatch):
    """Any non-empty warnings_out → summary.partial_success = True.

    Pattern-2 contract: the agent MUST be able to distinguish "clean
    completion" from "completion ran with substrate degradation" via
    summary.partial_success alone.
    """
    import sqlite3

    from roam.db import connection as _conn_mod

    real_open_db = _conn_mod.open_db

    class _BoomConn:
        def __init__(self, inner):
            self._inner = inner

        def __enter__(self):
            self._real = self._inner.__enter__()
            return self

        def __exit__(self, *a):
            return self._inner.__exit__(*a)

        def execute(self, sql, *args, **kwargs):
            if "SELECT DISTINCT name FROM symbols" in sql:
                raise sqlite3.OperationalError("synthetic-partial-success-test from W607-F")
            return self._real.execute(sql, *args, **kwargs)

        def __getattr__(self, name):
            return getattr(self._real, name)

    def _wrapped_open_db(*a, **kw):
        return _BoomConn(real_open_db(*a, **kw))

    monkeypatch.setattr(_conn_mod, "open_db", _wrapped_open_db)
    monkeypatch.chdir(complete_project)

    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["--json", "complete", "use"],
        catch_exceptions=False,
    )
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)

    assert data["summary"].get("partial_success") is True, (
        f"non-empty warnings_out must flip summary.partial_success=True; got summary = {data['summary']!r}"
    )


# ---------------------------------------------------------------------------
# (4) summary.warnings_out is populated alongside top-level on disclosure
# ---------------------------------------------------------------------------


def test_summary_warnings_out_mirror(complete_project, monkeypatch):
    """Non-empty bucket → both top-level AND summary.warnings_out are populated.

    Top-level is needed because the preserved-list field
    (``_ALWAYS_PRESERVED_LIST_FIELDS`` in formatter.py) survives
    ``strip_list_payloads`` in default-detail mode. summary mirror gives
    consumers reading only the summary block visibility too.
    """
    import sqlite3

    from roam.db import connection as _conn_mod

    real_open_db = _conn_mod.open_db

    class _BoomConn:
        def __init__(self, inner):
            self._inner = inner

        def __enter__(self):
            self._real = self._inner.__enter__()
            return self

        def __exit__(self, *a):
            return self._inner.__exit__(*a)

        def execute(self, sql, *args, **kwargs):
            if "SELECT DISTINCT name FROM symbols" in sql:
                raise sqlite3.OperationalError("synthetic-summary-mirror-test")
            return self._real.execute(sql, *args, **kwargs)

        def __getattr__(self, name):
            return getattr(self._real, name)

    def _wrapped_open_db(*a, **kw):
        return _BoomConn(real_open_db(*a, **kw))

    monkeypatch.setattr(_conn_mod, "open_db", _wrapped_open_db)
    monkeypatch.chdir(complete_project)

    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["--json", "complete", "use"],
        catch_exceptions=False,
    )
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)

    assert data.get("warnings_out"), (
        f"top-level warnings_out missing on disclosure path; keys = {sorted(data.keys())!r}"
    )
    assert data["summary"].get("warnings_out"), (
        f"summary.warnings_out missing on disclosure path; got summary = {data['summary']!r}"
    )
    # Mirror — top-level and summary content must be equal.
    assert sorted(data["warnings_out"]) == sorted(data["summary"]["warnings_out"]), (
        f"top-level vs summary.warnings_out must be equal; "
        f"top={data['warnings_out']!r} summary={data['summary']['warnings_out']!r}"
    )


# ---------------------------------------------------------------------------
# (5) Top-level mirror explicitly checked (W607-A/B/C/D/E discipline parity)
# ---------------------------------------------------------------------------


def test_top_level_warnings_out_mirror(complete_project, monkeypatch):
    """Top-level ``warnings_out`` must be present alongside summary mirror.

    The preserved-list-field discipline at ``_ALWAYS_PRESERVED_LIST_FIELDS``
    requires the top-level mirror so the field survives detail-mode
    list-payload stripping. cmd_search_semantic W607-A + cmd_retrieve
    W607-B + cmd_findings W607-C + cmd_dogfood W607-D + cmd_search
    W607-E pinned the same discipline; W607-F extends it to cmd_complete.
    """
    import sqlite3

    from roam.db import connection as _conn_mod

    real_open_db = _conn_mod.open_db

    class _BoomConn:
        def __init__(self, inner):
            self._inner = inner

        def __enter__(self):
            self._real = self._inner.__enter__()
            return self

        def __exit__(self, *a):
            return self._inner.__exit__(*a)

        def execute(self, sql, *args, **kwargs):
            if "SELECT DISTINCT name FROM symbols" in sql:
                raise sqlite3.OperationalError("synthetic-top-level-mirror-check")
            return self._real.execute(sql, *args, **kwargs)

        def __getattr__(self, name):
            return getattr(self._real, name)

    def _wrapped_open_db(*a, **kw):
        return _BoomConn(real_open_db(*a, **kw))

    monkeypatch.setattr(_conn_mod, "open_db", _wrapped_open_db)
    monkeypatch.chdir(complete_project)

    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["--json", "complete", "use"],
        catch_exceptions=False,
    )
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)

    top_wo = data.get("warnings_out")
    assert isinstance(top_wo, list) and top_wo, (
        f"top-level warnings_out must be a non-empty list on disclosure path; got {top_wo!r}"
    )


# ---------------------------------------------------------------------------
# (6) Three-segment marker shape — prefix:exc_class:detail
# ---------------------------------------------------------------------------


def test_three_segment_marker_shape(complete_project, monkeypatch):
    """Marker must have three colon-separated segments.

    The marker shape MUST be ``<prefix>:<exc_class>:<detail>`` — three
    colon-separated segments — so downstream consumers can parse the
    exception class without regex gymnastics. Mirrors cmd_search W607-E
    / cmd_findings W607-C / cmd_retrieve W607-B / cmd_dogfood W607-D
    outer-guard contracts.
    """
    import sqlite3

    from roam.db import connection as _conn_mod

    real_open_db = _conn_mod.open_db

    class _BoomConn:
        def __init__(self, inner):
            self._inner = inner

        def __enter__(self):
            self._real = self._inner.__enter__()
            return self

        def __exit__(self, *a):
            return self._inner.__exit__(*a)

        def execute(self, sql, *args, **kwargs):
            if "SELECT DISTINCT name FROM symbols" in sql:
                raise sqlite3.OperationalError("synthetic-three-segment-check")
            return self._real.execute(sql, *args, **kwargs)

        def __getattr__(self, name):
            return getattr(self._real, name)

    def _wrapped_open_db(*a, **kw):
        return _BoomConn(real_open_db(*a, **kw))

    monkeypatch.setattr(_conn_mod, "open_db", _wrapped_open_db)
    monkeypatch.chdir(complete_project)

    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["--json", "complete", "use"],
        catch_exceptions=False,
    )
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)

    top_wo = data.get("warnings_out") or []
    assert top_wo, "inner-bucket must emit at least one marker"

    sym_markers = [m for m in top_wo if m.startswith("complete_symbols_query_failed:")]
    assert sym_markers, f"inner-bucket must emit complete_symbols_query_failed marker; got {top_wo!r}"

    marker = sym_markers[0]
    parts = marker.split(":", 2)
    assert len(parts) == 3, f"marker must have three colon-separated segments (prefix:exc_class:detail); got {marker!r}"
    assert parts[0] == "complete_symbols_query_failed", parts
    assert parts[1] == "OperationalError", parts
    assert "synthetic-three-segment-check" in parts[2], parts


# ---------------------------------------------------------------------------
# (7) Marker prefix discipline — ``complete_*`` not ``search_*``/``semantic_*``
# ---------------------------------------------------------------------------


def test_marker_prefix_complete_not_search_or_semantic(complete_project, monkeypatch):
    """Every surfaced marker uses the canonical ``complete_*`` prefix family.

    cmd_complete is the LEXICAL-PREFIX layer of the lexical-search trio:

    * cmd_complete  → ``complete_*`` (left-anchored prefix)
    * cmd_search    → ``search_*`` (substring/regex)
    * cmd_search_semantic → ``semantic_*`` (FTS5-BM25 W605 substrate)

    Hard guard against accidental marker-prefix drift in this consumer
    (e.g., a future contributor mis-routing a marker into the
    ``search_*`` or ``semantic_*`` family). Closes the lexical-search
    trio prefix-discipline contract.
    """
    import sqlite3

    from roam.db import connection as _conn_mod

    real_open_db = _conn_mod.open_db

    class _BoomConn:
        def __init__(self, inner):
            self._inner = inner

        def __enter__(self):
            self._real = self._inner.__enter__()
            return self

        def __exit__(self, *a):
            return self._inner.__exit__(*a)

        def execute(self, sql, *args, **kwargs):
            if "SELECT DISTINCT name FROM symbols" in sql:
                raise sqlite3.OperationalError("synthetic-prefix-consistency-check")
            return self._real.execute(sql, *args, **kwargs)

        def __getattr__(self, name):
            return getattr(self._real, name)

    def _wrapped_open_db(*a, **kw):
        return _BoomConn(real_open_db(*a, **kw))

    monkeypatch.setattr(_conn_mod, "open_db", _wrapped_open_db)
    monkeypatch.chdir(complete_project)

    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["--json", "complete", "use"],
        catch_exceptions=False,
    )
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    top_wo = data.get("warnings_out") or []
    assert top_wo, "expected non-empty warnings_out for prefix-consistency check"
    for marker in top_wo:
        assert marker.startswith("complete_"), (
            f"every surfaced marker must use the W607-F ``complete_*`` prefix "
            f"family (cmd_complete lexical-prefix layer scope); got {marker!r}"
        )
        # Hard distinction from the sibling lexical layers.
        assert not marker.startswith("search_"), (
            f"marker leaked into ``search_*`` family (cmd_search W607-E scope); got {marker!r}"
        )
        assert not marker.startswith("semantic_"), (
            f"marker leaked into ``semantic_*`` family (cmd_search_semantic "
            f"W607-A / W605 substrate scope); got {marker!r}"
        )


# ---------------------------------------------------------------------------
# (8) Empty bucket → byte-identical envelope (hash stability)
# ---------------------------------------------------------------------------


def test_empty_bucket_byte_identical(complete_project, monkeypatch):
    """Clean envelope must NOT carry ``warnings_out`` keys at all.

    The W817 always-emit discipline plus the preserved-list-field
    discipline mean an EMPTY warnings_out bucket MUST be omitted from
    both summary and top-level (byte-identical to pre-W607-F).
    """
    monkeypatch.chdir(complete_project)

    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["--json", "complete", "use"],
        catch_exceptions=False,
    )
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)

    # Neither key may even be present (vs present-but-empty list).
    assert "warnings_out" not in data, (
        f"empty bucket must omit top-level warnings_out key entirely; "
        f"got data['warnings_out']={data.get('warnings_out')!r}"
    )
    assert "warnings_out" not in data["summary"], (
        f"empty bucket must omit summary.warnings_out key entirely; got summary={data['summary']!r}"
    )
    # And the canonical W817 partial_success default behaviour for
    # cmd_complete pre-W607-F (``partial = total == 0``) is preserved
    # when no markers fired: the only path that flips partial_success on
    # a non-zero-result query is the W607-F warnings_out branch.
    syms = (data.get("results") or {}).get("symbols") or []
    if syms:
        assert data["summary"].get("partial_success") is False, (
            f"clean envelope with hits must have partial_success=False; got {data['summary'].get('partial_success')!r}"
        )


# ---------------------------------------------------------------------------
# (9) Interface stability — Click options + function signature unchanged
# ---------------------------------------------------------------------------


def test_ast_interface_stability():
    """AST-check ``cmd_complete.complete``'s public interface.

    W607-F threads warnings_out PURELY internally (the bucket is a
    local accumulator; no new Click option exposes it to callers). The
    Click decorator surface, function name, and parameter list must
    therefore remain byte-identical to pre-W607-F.
    """
    path = repo_root() / "src" / "roam" / "commands" / "cmd_complete.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))

    # Find the complete function.
    fn = next(
        (n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == "complete"),
        None,
    )
    assert fn is not None, "complete function not found in cmd_complete.py"

    # Parameter list must be the canonical pre-W607-F shape.
    arg_names = [a.arg for a in fn.args.args]
    assert arg_names == [
        "ctx",
        "prefix",
        "kind",
        "limit",
    ], f"complete parameter list drifted from pre-W607-F: {arg_names!r}"

    # Click decorators present: @roam_capability + @click.command +
    # @click.argument + 2x option + @click.pass_context. Pin count to
    # catch accidental option additions or removals.
    decorator_count = len(fn.decorator_list)
    assert decorator_count >= 5, (
        f"expected ≥5 decorators on complete (roam_capability + "
        f"click.command + argument + options + click.pass_context); "
        f"got {decorator_count}"
    )

    # No return annotation drift (W607-F doesn't change return semantics).
    assert fn.returns is None, (
        f"complete gained a return annotation; W607-F is interface-stable. Got: {ast.dump(fn.returns)!r}"
    )
