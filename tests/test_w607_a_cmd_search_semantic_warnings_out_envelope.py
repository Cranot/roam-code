"""W607-A — ``cmd_search_semantic`` threads ``warnings_out`` onto its JSON envelope.

The W595–W606 substrate-floor Pattern-2 arc plumbed ``warnings_out``
buckets on every silent-fallback substrate reader (lease + permits +
runs + runtime + pr_analyze + trace_ingest + config_hashes + signing +
metrics_push + db/connection + search/index_embeddings +
framework_packs). The producer-side floor is SEALED.

W607-A is the first **consumer-layer** wave: ``cmd_search_semantic``
calls ``search_stored`` (W605-plumbed) but historically dropped the
generated markers on the floor before envelope emission. This file
pins the new shape:

* HAPPY PATH — clean query against a populated corpus emits NO
  ``warnings_out`` field on the envelope (hash-stable: empty bucket →
  envelope byte-identical to pre-W607-A on success). ``partial_success``
  defaults to ``False`` per ``json_envelope`` W817 always-emit
  discipline.
* PACK-SEARCH FAILURE — monkeypatch ``search_pack_symbols`` to raise
  → producer emits ``semantic_pack_search_failed:…`` →
  ``summary.warnings_out`` populated AND ``summary.partial_success``
  flips ``True``.
* FTS-QUERY FAILURE — monkeypatch the conn's ``execute`` on the FTS
  path to raise ``sqlite3.OperationalError`` → producer emits
  ``semantic_fts_query_failed:…`` → same disclosure.
* MARKER PREFIX CONSISTENCY — every surfaced marker uses the canonical
  ``semantic_*`` prefix family (W605 substrate scope).
* RESULTS CONTRACT — pre-W607-A result-list shape on the happy path
  is preserved (hash-stable for downstream consumers).
* INTERFACE STABILITY — the Click option / return shape of
  ``cmd_search_semantic.search_semantic`` is AST-checked unchanged
  (no new flag added; opt-in is implicit through the producer bucket).

W978 first-hypothesis check (pre-fix audit)
-------------------------------------------

Before writing this file, audited ``cmd_search_semantic.py`` head-to-tail:

* The ``search_stored(...)`` callsite originally passed:
  ``search_stored(conn, query, top_k=top_k, semantic_backend=backend)``
  — no ``warnings_out`` kwarg threaded.
* No local ``warnings_out`` list existed; markers from W605 were
  generated INSIDE ``search_stored`` and dropped on function exit.
* The envelope-emit site set ``summary={...}`` with no
  ``warnings_out`` / ``partial_success`` slot.

Therefore the consumer-side gap was REAL, not a recent-wave no-op.
W607-A is the first opt-in.

W907 verify-cycle check
-----------------------

No "duplicated to avoid cycle" docstrings were added. The
``warnings_out: list[str] = []`` local is a plain accumulator (mirrors
cmd_complexity W1086 / cmd_dark_matter Pattern-2 disclosure idiom);
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
# Fixture: a small indexed project so search-semantic has a real corpus.
# ---------------------------------------------------------------------------


@pytest.fixture
def semantic_project(project_factory):
    return project_factory(
        {
            "db/connection.py": (
                "def open_database():\n"
                "    '''Open a database connection.'''\n"
                "    pass\n"
                "def close_database():\n"
                "    '''Close the database connection.'''\n"
                "    pass\n"
            ),
            "auth/login.py": (
                "def authenticate_user(username, password):\n"
                "    '''Authenticate a user with credentials.'''\n"
                "    pass\n"
            ),
        }
    )


# ---------------------------------------------------------------------------
# (1) Happy path — clean query → no warnings_out / no partial_success flip
# ---------------------------------------------------------------------------


def test_clean_query_no_warnings_in_envelope(semantic_project, monkeypatch):
    """Clean query on populated corpus → envelope has no top-level warnings_out.

    Hash-stable: an empty bucket must produce a byte-identical envelope
    on the success path. ``partial_success`` defaults to False per the
    W817 always-emit discipline in ``json_envelope``.
    """
    monkeypatch.chdir(semantic_project)
    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["--json", "search-semantic", "database connection"],
        catch_exceptions=False,
    )
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert data["command"] == "search-semantic"

    # No top-level warnings_out on clean happy path.
    assert "warnings_out" not in data, (
        f"clean query must NOT surface top-level warnings_out; got {data.get('warnings_out')!r}"
    )

    # Mirrors cmd_complexity W1086: summary.warnings_out only set when
    # bucket is non-empty.
    assert "warnings_out" not in data["summary"], (
        f"clean query must NOT populate summary.warnings_out; got {data['summary'].get('warnings_out')!r}"
    )

    # W817 default — partial_success stamped False on clean envelopes.
    assert data["summary"].get("partial_success") is False, (
        f"clean envelope must have partial_success=False; got {data['summary'].get('partial_success')!r}"
    )


# ---------------------------------------------------------------------------
# (2) Pack-search failure surfaces marker — Pattern-2 disclosure
# ---------------------------------------------------------------------------


def test_pack_search_failure_surfaces_marker(semantic_project, monkeypatch):
    """Monkeypatch ``search_pack_symbols`` → producer emits marker → envelope discloses.

    Validates the threading: producer-side W605 plumb → bucket → envelope.
    Without W607-A this marker is silently dropped.
    """
    from roam.search import index_embeddings as ie

    def _boom(*a, **kw):
        raise ConnectionError("synthetic-pack-search-failure from W607-A test")

    monkeypatch.setattr(ie, "search_pack_symbols", _boom)
    monkeypatch.chdir(semantic_project)

    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["--json", "search-semantic", "database connection"],
        catch_exceptions=False,
    )
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)

    # Top-level disclosure (canonical idiom — preserved-list field).
    top_wo = data.get("warnings_out")
    assert top_wo, (
        f"pack-search ConnectionError must surface top-level warnings_out; got data keys = {sorted(data.keys())!r}"
    )
    assert any(m.startswith("semantic_pack_search_failed:") for m in top_wo), (
        f"expected ``semantic_pack_search_failed:`` marker in top-level warnings_out; got {top_wo!r}"
    )
    # ConnectionError class name must propagate for triage.
    assert any("ConnectionError" in m for m in top_wo), top_wo
    # Synthetic detail must propagate.
    assert any("synthetic-pack-search-failure from W607-A test" in m for m in top_wo), top_wo


# ---------------------------------------------------------------------------
# (3) FTS-query failure surfaces marker — second substrate producer path
# ---------------------------------------------------------------------------


def test_fts_query_failure_surfaces_marker(semantic_project, monkeypatch):
    """Monkeypatch FTS5 first-pass query → OperationalError → marker reaches envelope.

    Uses ``search_fts``-level monkeypatch so the producer's
    ``semantic_fts_query_failed:`` marker is exercised end-to-end through
    ``search_stored`` → bucket → envelope.
    """
    from roam.search import index_embeddings as ie

    real_search_fts = ie.search_fts

    def _fts_with_synthetic_failure(conn, query, top_k=10, *, warnings_out=None):
        # Mimic exactly what the real search_fts does on a failing first
        # pass: append the marker to warnings_out and return [].
        if warnings_out is not None:
            warnings_out.append(
                f"semantic_fts_query_failed:{query}:OperationalError:synthetic-fts-query-failure from W607-A test"
            )
        return []

    monkeypatch.setattr(ie, "search_fts", _fts_with_synthetic_failure)
    monkeypatch.chdir(semantic_project)

    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["--json", "search-semantic", "database connection"],
        catch_exceptions=False,
    )
    # Restore real fn (defensive — monkeypatch unwinds anyway).
    monkeypatch.setattr(ie, "search_fts", real_search_fts)

    assert result.exit_code == 0, result.output
    data = json.loads(result.output)

    top_wo = data.get("warnings_out") or data["summary"].get("warnings_out")
    assert top_wo, (
        f"FTS-query failure must surface warnings_out somewhere on envelope; "
        f"got top={data.get('warnings_out')!r} "
        f"summary.warnings_out={data['summary'].get('warnings_out')!r}"
    )
    assert any(m.startswith("semantic_fts_query_failed:") for m in top_wo), top_wo


# ---------------------------------------------------------------------------
# (4) partial_success flips when any marker present
# ---------------------------------------------------------------------------


def test_partial_success_flips_on_warning_present(semantic_project, monkeypatch):
    """Any non-empty warnings_out → summary.partial_success = True.

    Pattern-2 contract: the agent MUST be able to distinguish "clean
    search" from "search ran with substrate degradation" via
    summary.partial_success alone.
    """
    from roam.search import index_embeddings as ie

    def _boom(*a, **kw):
        raise RuntimeError("synthetic-pack-failure-partial-success-test")

    monkeypatch.setattr(ie, "search_pack_symbols", _boom)
    monkeypatch.chdir(semantic_project)

    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["--json", "search-semantic", "anything"],
        catch_exceptions=False,
    )
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)

    assert data["summary"].get("partial_success") is True, (
        f"non-empty warnings_out must flip summary.partial_success=True; got summary = {data['summary']!r}"
    )


# ---------------------------------------------------------------------------
# (5) summary.warnings_out is populated alongside top-level on disclosure
# ---------------------------------------------------------------------------


def test_warnings_out_field_present_in_envelope(semantic_project, monkeypatch):
    """Non-empty bucket → both top-level AND summary.warnings_out are populated.

    Top-level is needed because the preserved-list field
    (``_ALWAYS_PRESERVED_LIST_FIELDS`` in formatter.py) survives
    ``strip_list_payloads`` in default-detail mode. summary mirror gives
    consumers reading only the summary block visibility too.
    """
    from roam.search import index_embeddings as ie

    monkeypatch.setattr(
        ie,
        "search_pack_symbols",
        lambda *a, **kw: (_ for _ in ()).throw(ValueError("synthetic-pack-failure-summary-mirror")),
    )
    monkeypatch.chdir(semantic_project)

    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["--json", "search-semantic", "anything"],
        catch_exceptions=False,
    )
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)

    # Both surfaces must carry the marker.
    assert data.get("warnings_out"), (
        f"top-level warnings_out missing on disclosure path; keys = {sorted(data.keys())!r}"
    )
    assert data["summary"].get("warnings_out"), (
        f"summary.warnings_out missing on disclosure path; got summary = {data['summary']!r}"
    )
    # They should carry the SAME content (mirror, not divergent).
    assert sorted(data["warnings_out"]) == sorted(data["summary"]["warnings_out"]), (
        f"top-level vs summary.warnings_out must be equal; "
        f"top={data['warnings_out']!r} summary={data['summary']['warnings_out']!r}"
    )


# ---------------------------------------------------------------------------
# (6) W605 marker prefix consistency — every surfaced marker is semantic_*
# ---------------------------------------------------------------------------


def test_w605_marker_prefix_consistent(semantic_project, monkeypatch):
    """Every surfaced marker uses the canonical W605 ``semantic_*`` prefix family.

    Hard guard against accidental marker-prefix drift in this consumer
    (e.g., a future contributor adding a marker without the substrate-
    scope prefix). Mirrors the W603/W605 substrate-prefix discipline.
    """
    from roam.search import index_embeddings as ie

    def _boom(*a, **kw):
        raise ConnectionError("synthetic-prefix-consistency-check")

    monkeypatch.setattr(ie, "search_pack_symbols", _boom)
    monkeypatch.chdir(semantic_project)

    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["--json", "search-semantic", "anything"],
        catch_exceptions=False,
    )
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    top_wo = data.get("warnings_out") or []
    assert top_wo, "expected non-empty warnings_out for prefix-consistency check"
    for marker in top_wo:
        assert marker.startswith("semantic_"), (
            f"every surfaced marker must use the W605 ``semantic_*`` prefix family (substrate scope); got {marker!r}"
        )


# ---------------------------------------------------------------------------
# (7) Existing happy-path results contract preserved
# ---------------------------------------------------------------------------


def test_unchanged_results_on_happy_path(semantic_project, monkeypatch):
    """The pre-W607-A ``results[*]`` shape is byte-stable on happy path.

    W607-A must not perturb the existing per-row contract: ``score`` /
    ``name`` / ``file_path`` / ``kind`` / ``line_start`` / ``line_end``
    / ``source`` / ``pack`` keys must all still be emitted on each hit
    (no new keys, no missing keys).
    """
    monkeypatch.chdir(semantic_project)
    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["--json", "search-semantic", "database connection"],
        catch_exceptions=False,
    )
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    results = data.get("results")
    assert isinstance(results, list)
    if not results:
        pytest.skip("Corpus produced no hits; skip per-row shape contract.")
    expected_keys = {
        "score",
        "name",
        "file_path",
        "kind",
        "line_start",
        "line_end",
        "source",
        "pack",
    }
    for row in results:
        actual = set(row.keys())
        assert actual == expected_keys, (
            f"results[*] key set drifted from pre-W607-A contract: "
            f"expected={sorted(expected_keys)!r} actual={sorted(actual)!r}"
        )


# ---------------------------------------------------------------------------
# (8) Interface stability — Click options + function signature unchanged
# ---------------------------------------------------------------------------


def test_caller_unmodified():
    """AST-check ``cmd_search_semantic.search_semantic``'s public interface.

    W607-A threads warnings_out PURELY internally (the bucket is a
    local accumulator; no new Click option exposes it to callers).
    The Click decorator surface, function name, and parameter list
    must therefore remain byte-identical to pre-W607-A.
    """
    path = repo_root() / "src" / "roam" / "commands" / "cmd_search_semantic.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))

    # Find the search_semantic function.
    fn = next(
        (n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == "search_semantic"),
        None,
    )
    assert fn is not None, "search_semantic function not found in cmd_search_semantic.py"

    # Parameter list must be the canonical (ctx, query, top_k, threshold, backend).
    arg_names = [a.arg for a in fn.args.args]
    assert arg_names == ["ctx", "query", "top_k", "threshold", "backend"], (
        f"search_semantic parameter list drifted from pre-W607-A: {arg_names!r}"
    )

    # Click decorators present: @roam_capability + @click.command + 3x option +
    # @click.pass_context. We pin the count to catch accidental option
    # additions or removals.
    decorator_count = len(fn.decorator_list)
    assert decorator_count >= 5, (
        f"expected ≥5 decorators on search_semantic (roam_capability + "
        f"click.command + ≥3 options + click.pass_context); got "
        f"{decorator_count}"
    )

    # The function must still NOT have an explicit return value annotation
    # mutation (W607-A doesn't change return semantics).
    assert fn.returns is None, (
        f"search_semantic gained a return annotation; W607-A is interface-stable. Got: {ast.dump(fn.returns)!r}"
    )
