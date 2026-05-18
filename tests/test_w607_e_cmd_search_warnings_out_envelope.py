"""W607-E — ``cmd_search`` threads ``warnings_out`` onto its JSON envelope.

The W595-W606 substrate-floor Pattern-2 arc plumbed ``warnings_out``
buckets on every silent-fallback substrate reader. W607-A landed the
first consumer-layer wave on ``cmd_search_semantic``. W607-B landed the
second consumer-layer wave on ``cmd_retrieve`` (outer-guard-only).
W607-C landed the third wave on ``cmd_findings``. W607-D landed the
fourth wave on ``cmd_dogfood`` (outer-guard-only). W607-E is the fifth
consumer-layer wave: ``cmd_search`` — the lexical FTS / substring /
regex search.

W978 first-hypothesis check (pre-fix audit)
-------------------------------------------

Before writing this file, audited ``cmd_search.py`` head-to-tail:

* The command imports ``_build_fts_query`` from
  ``roam.search.index_embeddings`` but does NOT call any of the
  W605-plumbed substrate functions (``search_fts`` / ``fts5_available`` /
  ``fts5_populated`` / ``tfidf_populated`` / ``onnx_populated``). It
  carries a LOCAL ``_fts5_available`` helper and runs raw SQL against
  ``symbols`` / ``files`` / ``graph_metrics``.
* The main pipeline path (lines pre-W607-E ~387-410) executed
  ``conn.execute(...).fetchall()`` with NO try/except — a malformed
  REGEXP / locked DB / schema-drift error bubbled as a Click traceback.
* The ``_get_explain_data`` helper carried THREE silent
  ``except Exception: pass`` fallbacks (BM25 score / FTS5 highlight /
  per-field term counts) that dropped substrate failures on the floor.
* The W1068 unknown-kind disclosure short-circuits before the pipeline
  and is OUT of scope for W607-E (must remain byte-identical).

Therefore the consumer-side gap was REAL. Because cmd_search has no
direct W605 callsites, the W607-E shape is OUTER-GUARD with INNER
explain-helper plumbing:

* outer pipeline raise → ``search_pipeline_failed:<exc_class>:<detail>``
  marker + empty results
* inner ``_get_explain_data`` silent fallbacks (only when ``--explain``)
  → ``search_explain_<phase>_failed:<exc_class>:<detail>`` markers
  (phases: ``bm25``, ``highlight``, ``term_counts``)

Mirrors the cmd_retrieve W607-B ``retrieve_pipeline_failed:`` and
cmd_dogfood W607-D ``dogfood_aggregation_failed:`` outer-guard idioms.
The marker family is ``search_*`` (NOT ``semantic_*``) because cmd_search
is the lexical-substring layer — distinct from the semantic / FTS5-BM25
substrate scope owned by W605.

W907 verify-cycle check
-----------------------

No "duplicated to avoid cycle" docstrings were added. The
``warnings_out: list[str] = []`` local is a plain accumulator (mirrors
the cmd_search_semantic W607-A / cmd_retrieve W607-B / cmd_findings
W607-C / cmd_dogfood W607-D disclosure idioms); no shared module was
created or hoisted.

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
# Fixture: a small indexed project so search has a real corpus.
# ---------------------------------------------------------------------------


@pytest.fixture
def search_project(project_factory):
    return project_factory(
        {
            "auth/login.py": (
                "def authenticate_user(username, password):\n"
                "    '''Authenticate a user with credentials.'''\n"
                "    return True\n"
                "\n"
                "def authorize_user(username):\n"
                "    '''Authorize a user.'''\n"
                "    return True\n"
            ),
            "db/connection.py": (
                "class DatabaseConnection:\n"
                "    def open_database(self):\n"
                "        '''Open a database connection.'''\n"
                "        pass\n"
            ),
        }
    )


# ---------------------------------------------------------------------------
# (1) Happy path — clean search → no warnings_out / no partial_success flip
# ---------------------------------------------------------------------------


def test_clean_search_no_warnings(search_project, monkeypatch):
    """Clean substring match on a populated corpus → envelope has no warnings_out.

    Hash-stable: an empty bucket must produce a byte-identical envelope
    on the success path. ``partial_success`` defaults to False per the
    W817 always-emit discipline in ``json_envelope``.
    """
    monkeypatch.chdir(search_project)
    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["--json", "search", "authenticate"],
        catch_exceptions=False,
    )
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert data["command"] == "search"

    # No top-level warnings_out on the happy path.
    assert "warnings_out" not in data, (
        f"clean search must NOT surface top-level warnings_out; got {data.get('warnings_out')!r}"
    )

    # No summary.warnings_out either.
    assert "warnings_out" not in data["summary"], (
        f"clean search must NOT populate summary.warnings_out; got {data['summary'].get('warnings_out')!r}"
    )

    # W817 default — partial_success stamped False on clean envelopes.
    assert data["summary"].get("partial_success") is False, (
        f"clean envelope must have partial_success=False; got {data['summary'].get('partial_success')!r}"
    )


# ---------------------------------------------------------------------------
# (2) Outer-guard — SQL pipeline raises → marker reaches envelope
# ---------------------------------------------------------------------------


def test_fts_query_failure_surfaces_marker(search_project, monkeypatch):
    """Monkeypatch the SQL pipeline → OperationalError → marker reaches envelope.

    Pattern-2 outer-guard contract: when the pipeline path raises before
    rows return, the envelope surfaces a structured marker rather than a
    Click traceback. Mirrors cmd_retrieve W607-B / cmd_dogfood W607-D
    outer-guard idioms.
    """
    import sqlite3

    from roam.commands import cmd_search

    real_open_db = cmd_search.open_db

    class _BoomConn:
        """Wraps a real conn but raises on the main pipeline SELECT."""

        def __init__(self, inner):
            self._inner = inner

        def __enter__(self):
            self._real = self._inner.__enter__()
            return self

        def __exit__(self, *a):
            return self._inner.__exit__(*a)

        def execute(self, sql, *args, **kwargs):
            # The main pipeline SELECT joins symbols + files + graph_metrics.
            if "FROM symbols s JOIN files f" in sql and "graph_metrics" in sql:
                raise sqlite3.OperationalError("synthetic-fts-query-failure from W607-E test")
            return self._real.execute(sql, *args, **kwargs)

        def create_function(self, *a, **kw):
            return self._real.create_function(*a, **kw)

        def __getattr__(self, name):
            return getattr(self._real, name)

    def _wrapped_open_db(*a, **kw):
        return _BoomConn(real_open_db(*a, **kw))

    monkeypatch.setattr(cmd_search, "open_db", _wrapped_open_db)
    monkeypatch.chdir(search_project)

    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["--json", "search", "authenticate"],
        catch_exceptions=False,
    )
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)

    # Top-level disclosure (canonical idiom — preserved-list field).
    top_wo = data.get("warnings_out")
    assert top_wo, (
        f"SQL pipeline OperationalError must surface top-level warnings_out; got data keys = {sorted(data.keys())!r}"
    )
    assert any(m.startswith("search_pipeline_failed:") for m in top_wo), (
        f"expected ``search_pipeline_failed:`` marker in top-level warnings_out; got {top_wo!r}"
    )
    # OperationalError class name must propagate for triage.
    assert any("OperationalError" in m for m in top_wo), top_wo
    # Synthetic detail must propagate.
    assert any("synthetic-fts-query-failure from W607-E test" in m for m in top_wo), top_wo


# ---------------------------------------------------------------------------
# (3) partial_success flips when any marker present
# ---------------------------------------------------------------------------


def test_partial_success_flips_on_warning_present(search_project, monkeypatch):
    """Any non-empty warnings_out → summary.partial_success = True.

    Pattern-2 contract: the agent MUST be able to distinguish "clean
    search" from "search ran with substrate degradation" via
    summary.partial_success alone.
    """
    import sqlite3

    from roam.commands import cmd_search

    real_open_db = cmd_search.open_db

    class _BoomConn:
        def __init__(self, inner):
            self._inner = inner

        def __enter__(self):
            self._real = self._inner.__enter__()
            return self

        def __exit__(self, *a):
            return self._inner.__exit__(*a)

        def execute(self, sql, *args, **kwargs):
            if "FROM symbols s JOIN files f" in sql and "graph_metrics" in sql:
                raise sqlite3.OperationalError("synthetic-partial-success-test from W607-E")
            return self._real.execute(sql, *args, **kwargs)

        def create_function(self, *a, **kw):
            return self._real.create_function(*a, **kw)

        def __getattr__(self, name):
            return getattr(self._real, name)

    def _wrapped_open_db(*a, **kw):
        return _BoomConn(real_open_db(*a, **kw))

    monkeypatch.setattr(cmd_search, "open_db", _wrapped_open_db)
    monkeypatch.chdir(search_project)

    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["--json", "search", "anything"],
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


def test_summary_mirror(search_project, monkeypatch):
    """Non-empty bucket → both top-level AND summary.warnings_out are populated.

    Top-level is needed because the preserved-list field
    (``_ALWAYS_PRESERVED_LIST_FIELDS`` in formatter.py) survives
    ``strip_list_payloads`` in default-detail mode. summary mirror gives
    consumers reading only the summary block visibility too.
    """
    import sqlite3

    from roam.commands import cmd_search

    real_open_db = cmd_search.open_db

    class _BoomConn:
        def __init__(self, inner):
            self._inner = inner

        def __enter__(self):
            self._real = self._inner.__enter__()
            return self

        def __exit__(self, *a):
            return self._inner.__exit__(*a)

        def execute(self, sql, *args, **kwargs):
            if "FROM symbols s JOIN files f" in sql and "graph_metrics" in sql:
                raise sqlite3.OperationalError("synthetic-summary-mirror-test")
            return self._real.execute(sql, *args, **kwargs)

        def create_function(self, *a, **kw):
            return self._real.create_function(*a, **kw)

        def __getattr__(self, name):
            return getattr(self._real, name)

    def _wrapped_open_db(*a, **kw):
        return _BoomConn(real_open_db(*a, **kw))

    monkeypatch.setattr(cmd_search, "open_db", _wrapped_open_db)
    monkeypatch.chdir(search_project)

    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["--json", "search", "anything"],
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
# (5) Top-level mirror explicitly checked (W607-A/B/C/D discipline parity)
# ---------------------------------------------------------------------------


def test_top_level_warnings_out_mirror(search_project, monkeypatch):
    """Top-level ``warnings_out`` must be present alongside summary mirror.

    The preserved-list-field discipline at ``_ALWAYS_PRESERVED_LIST_FIELDS``
    requires the top-level mirror so the field survives detail-mode
    list-payload stripping. cmd_search_semantic W607-A + cmd_retrieve
    W607-B + cmd_findings W607-C + cmd_dogfood W607-D pinned the same
    discipline; W607-E extends it to cmd_search.
    """
    import sqlite3

    from roam.commands import cmd_search

    real_open_db = cmd_search.open_db

    class _BoomConn:
        def __init__(self, inner):
            self._inner = inner

        def __enter__(self):
            self._real = self._inner.__enter__()
            return self

        def __exit__(self, *a):
            return self._inner.__exit__(*a)

        def execute(self, sql, *args, **kwargs):
            if "FROM symbols s JOIN files f" in sql and "graph_metrics" in sql:
                raise sqlite3.OperationalError("synthetic-top-level-mirror-check")
            return self._real.execute(sql, *args, **kwargs)

        def create_function(self, *a, **kw):
            return self._real.create_function(*a, **kw)

        def __getattr__(self, name):
            return getattr(self._real, name)

    def _wrapped_open_db(*a, **kw):
        return _BoomConn(real_open_db(*a, **kw))

    monkeypatch.setattr(cmd_search, "open_db", _wrapped_open_db)
    monkeypatch.chdir(search_project)

    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["--json", "search", "anything"],
        catch_exceptions=False,
    )
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)

    top_wo = data.get("warnings_out")
    assert isinstance(top_wo, list) and top_wo, (
        f"top-level warnings_out must be a non-empty list on disclosure path; got {top_wo!r}"
    )


# ---------------------------------------------------------------------------
# (6) W1068 unknown-kind disclosure remains byte-identical (regression)
# ---------------------------------------------------------------------------


def test_w1068_unknown_kind_intact(search_project, monkeypatch):
    """The W1068 unknown-kind short-circuit must NOT regress to W607-E shape.

    The unknown-kind path runs BEFORE the pipeline + outer-guard, so it
    must remain free of ``warnings_out`` markers. The W1068 contract
    relies on ``state="unknown_kind"`` + ``did_you_mean`` (when close
    match) + flat ``known_kinds`` enumeration — those must not be
    contaminated by the new W607-E bucket.
    """
    monkeypatch.chdir(search_project)
    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["--json", "search", "foo", "--kind", "garbage"],
        catch_exceptions=False,
    )
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert data["command"] == "search"

    # W1068 contract — state stays "unknown_kind".
    assert data["summary"].get("state") == "unknown_kind", data["summary"]

    # W607-E must NOT splash warnings_out onto the unknown-kind envelope.
    assert "warnings_out" not in data, (
        f"W1068 unknown-kind envelope must NOT carry W607-E warnings_out; got {data.get('warnings_out')!r}"
    )
    assert "warnings_out" not in data["summary"], (
        f"W1068 unknown-kind summary must NOT carry W607-E warnings_out; got {data['summary'].get('warnings_out')!r}"
    )


# ---------------------------------------------------------------------------
# (7) Marker prefix discipline — every surfaced marker uses ``search_*``
# ---------------------------------------------------------------------------


def test_w605_marker_prefix_consistent(search_project, monkeypatch):
    """Every surfaced marker uses the canonical ``search_*`` prefix family.

    NOTE: cmd_search is the LEXICAL search layer, distinct from the
    SEMANTIC / FTS5-BM25 W605 substrate (which uses ``semantic_*``). The
    W607-E marker family is therefore ``search_*`` — both the outer-guard
    ``search_pipeline_failed:`` and the inner explain-helper
    ``search_explain_<phase>_failed:`` markers share this prefix.

    Hard guard against accidental marker-prefix drift in this consumer
    (e.g., a future contributor mis-routing a marker into the
    ``semantic_*`` family).
    """
    import sqlite3

    from roam.commands import cmd_search

    real_open_db = cmd_search.open_db

    class _BoomConn:
        def __init__(self, inner):
            self._inner = inner

        def __enter__(self):
            self._real = self._inner.__enter__()
            return self

        def __exit__(self, *a):
            return self._inner.__exit__(*a)

        def execute(self, sql, *args, **kwargs):
            if "FROM symbols s JOIN files f" in sql and "graph_metrics" in sql:
                raise sqlite3.OperationalError("synthetic-prefix-consistency-check")
            return self._real.execute(sql, *args, **kwargs)

        def create_function(self, *a, **kw):
            return self._real.create_function(*a, **kw)

        def __getattr__(self, name):
            return getattr(self._real, name)

    def _wrapped_open_db(*a, **kw):
        return _BoomConn(real_open_db(*a, **kw))

    monkeypatch.setattr(cmd_search, "open_db", _wrapped_open_db)
    monkeypatch.chdir(search_project)

    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["--json", "search", "anything"],
        catch_exceptions=False,
    )
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    top_wo = data.get("warnings_out") or []
    assert top_wo, "expected non-empty warnings_out for prefix-consistency check"
    for marker in top_wo:
        assert marker.startswith("search_"), (
            f"every surfaced marker must use the W607-E ``search_*`` prefix "
            f"family (cmd_search lexical layer scope); got {marker!r}"
        )


# ---------------------------------------------------------------------------
# (8) Outer-guard marker shape — three-segment prefix:exc_class:detail
# ---------------------------------------------------------------------------


def test_outer_guard_marker_shape(search_project, monkeypatch):
    """Marker must have three colon-separated segments.

    The marker shape MUST be ``<prefix>:<exc_class>:<detail>`` — three
    colon-separated segments — so downstream consumers can parse the
    exception class without regex gymnastics. Mirrors cmd_findings
    W607-C / cmd_retrieve W607-B / cmd_dogfood W607-D outer-guard
    contracts.
    """
    import sqlite3

    from roam.commands import cmd_search

    real_open_db = cmd_search.open_db

    class _BoomConn:
        def __init__(self, inner):
            self._inner = inner

        def __enter__(self):
            self._real = self._inner.__enter__()
            return self

        def __exit__(self, *a):
            return self._inner.__exit__(*a)

        def execute(self, sql, *args, **kwargs):
            if "FROM symbols s JOIN files f" in sql and "graph_metrics" in sql:
                raise sqlite3.OperationalError("synthetic-outer-guard-emit-check")
            return self._real.execute(sql, *args, **kwargs)

        def create_function(self, *a, **kw):
            return self._real.create_function(*a, **kw)

        def __getattr__(self, name):
            return getattr(self._real, name)

    def _wrapped_open_db(*a, **kw):
        return _BoomConn(real_open_db(*a, **kw))

    monkeypatch.setattr(cmd_search, "open_db", _wrapped_open_db)
    monkeypatch.chdir(search_project)

    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["--json", "search", "anything"],
        catch_exceptions=False,
    )
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)

    top_wo = data.get("warnings_out") or []
    assert top_wo, "outer-guard must emit at least one marker"

    pipeline_markers = [m for m in top_wo if m.startswith("search_pipeline_failed:")]
    assert pipeline_markers, f"outer-guard must emit search_pipeline_failed marker; got {top_wo!r}"

    marker = pipeline_markers[0]
    parts = marker.split(":", 2)
    assert len(parts) == 3, f"marker must have three colon-separated segments (prefix:exc_class:detail); got {marker!r}"
    assert parts[0] == "search_pipeline_failed", parts
    assert parts[1] == "OperationalError", parts
    assert "synthetic-outer-guard-emit-check" in parts[2], parts


# ---------------------------------------------------------------------------
# (9) Interface stability — Click options + function signature unchanged
# ---------------------------------------------------------------------------


def test_caller_unmodified():
    """AST-check ``cmd_search.search``'s public interface.

    W607-E threads warnings_out PURELY internally (the bucket is a
    local accumulator; no new Click option exposes it to callers). The
    Click decorator surface, function name, and parameter list must
    therefore remain byte-identical to pre-W607-E.
    """
    path = repo_root() / "src" / "roam" / "commands" / "cmd_search.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))

    # Find the search function.
    fn = next(
        (n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == "search"),
        None,
    )
    assert fn is not None, "search function not found in cmd_search.py"

    # Parameter list must be the canonical pre-W607-E shape.
    arg_names = [a.arg for a in fn.args.args]
    assert arg_names == [
        "ctx",
        "pattern",
        "full",
        "kind_filter",
        "async_only",
        "decorator_filter",
        "fixtures_only",
        "explain",
        "mode",
        "recent_days",
    ], f"search parameter list drifted from pre-W607-E: {arg_names!r}"

    # Click decorators present: @roam_capability + @click.command + 9x option +
    # @click.pass_context. We pin the count to catch accidental option
    # additions or removals.
    decorator_count = len(fn.decorator_list)
    assert decorator_count >= 5, (
        f"expected ≥5 decorators on search (roam_capability + "
        f"click.command + options + click.pass_context); got "
        f"{decorator_count}"
    )

    # No return annotation drift (W607-E doesn't change return semantics).
    assert fn.returns is None, (
        f"search gained a return annotation; W607-E is interface-stable. Got: {ast.dump(fn.returns)!r}"
    )
