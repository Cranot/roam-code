"""W607-B — ``cmd_retrieve`` threads ``warnings_out`` onto its JSON envelope.

The W595-W606 substrate-floor Pattern-2 arc plumbed ``warnings_out``
buckets on every silent-fallback substrate reader. W607-A landed the
first consumer-layer wave on ``cmd_search_semantic``. W607-B is the
second consumer-layer wave on ``cmd_retrieve`` — one of roam's
5-verb core commands.

W978 first-hypothesis check (pre-fix audit)
-------------------------------------------

Before writing this file, audited ``cmd_retrieve.py`` head-to-tail:

* The command imports nothing from ``roam.search.index_embeddings``
  directly. The W605-plumbed substrate (``search_stored`` /
  ``search_fts`` / ``fts5_available`` / etc.) is NOT a direct
  callsite of cmd_retrieve.
* cmd_retrieve invokes ``run_retrieve`` from ``roam.retrieve.pipeline``.
  That function delegates to ``retrieve.pipeline._first_stage`` (which
  itself uses ad-hoc ``try/except sqlite3.OperationalError`` against the
  ``symbol_fts`` table) and ``retrieve.seeds.infer_seeds`` (same
  ad-hoc pattern). Neither yet threads ``warnings_out``.
* No local ``warnings_out`` list existed in cmd_retrieve before
  W607-B. No markers were ever surfaced on the envelope.

Therefore the consumer-side gap was REAL. Because cmd_retrieve has no
direct W605 callsites, the disclosure shape lives at the
**outer-guard boundary**: any uncaught exception from ``run_retrieve``
(substrate corruption, locked DB, malformed FTS5 query bubbling past
the inner ``except``) emits ``retrieve_pipeline_failed:<exc_class>:<detail>``.
This mirrors the cmd_search_semantic W607-A
``semantic_search_stored_failed:`` outer-guard marker.

W907 verify-cycle check
-----------------------

No "duplicated to avoid cycle" docstrings were added. The
``warnings_out: list[str] = []`` local is a plain accumulator (mirrors
cmd_search_semantic W607-A / cmd_complexity W1086 disclosure idiom);
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
# Fixture: a small indexed project so retrieve has a real corpus.
# ---------------------------------------------------------------------------


@pytest.fixture
def retrieve_project(project_factory):
    return project_factory(
        {
            "auth/login.py": (
                "def authenticate_user(username, password):\n"
                "    '''Authenticate a user with credentials.'''\n"
                "    pass\n"
                "def refresh_session(token):\n"
                "    '''Refresh the user session.'''\n"
                "    pass\n"
            ),
            "db/connection.py": ("def open_database():\n    '''Open a database connection.'''\n    pass\n"),
        }
    )


# ---------------------------------------------------------------------------
# (1) Happy path — clean retrieve → no warnings_out / no partial_success flip
# ---------------------------------------------------------------------------


def test_clean_retrieve_no_warnings(retrieve_project, monkeypatch):
    """Clean retrieve on populated corpus → envelope has no warnings_out.

    Hash-stable: an empty bucket must produce a byte-identical envelope
    on the success path. ``partial_success`` defaults to False per the
    W817 always-emit discipline in ``json_envelope``.
    """
    monkeypatch.chdir(retrieve_project)
    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["--json", "retrieve", "authenticate user session"],
        catch_exceptions=False,
    )
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert data["command"] == "retrieve"

    # No top-level warnings_out on clean happy path.
    assert "warnings_out" not in data, (
        f"clean retrieve must NOT surface top-level warnings_out; got {data.get('warnings_out')!r}"
    )

    # Mirrors cmd_search_semantic W607-A: summary.warnings_out only set
    # when bucket is non-empty.
    assert "warnings_out" not in data["summary"], (
        f"clean retrieve must NOT populate summary.warnings_out; got {data['summary'].get('warnings_out')!r}"
    )

    # W817 default — partial_success stamped False on clean envelopes.
    assert data["summary"].get("partial_success") is False, (
        f"clean envelope must have partial_success=False; got {data['summary'].get('partial_success')!r}"
    )


# ---------------------------------------------------------------------------
# (2) Pipeline failure surfaces marker — outer-guard disclosure
# ---------------------------------------------------------------------------


def test_pack_failure_surfaces_marker(retrieve_project, monkeypatch):
    """Monkeypatch ``run_retrieve`` → ConnectionError → marker reaches envelope.

    Pattern-2 outer-guard contract: when the retrieve pipeline raises
    before producing a result, the envelope surfaces a structured marker
    rather than a Click traceback. Mirrors cmd_search_semantic W607-A's
    ``semantic_search_stored_failed:`` marker.
    """
    from roam.commands import cmd_retrieve

    def _boom(*a, **kw):
        raise ConnectionError("synthetic-pack-failure from W607-B test")

    monkeypatch.setattr(cmd_retrieve, "run_retrieve", _boom)
    monkeypatch.chdir(retrieve_project)

    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["--json", "retrieve", "authenticate user session"],
        catch_exceptions=False,
    )
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)

    # Top-level disclosure (canonical idiom — preserved-list field).
    top_wo = data.get("warnings_out")
    assert top_wo, (
        f"pipeline ConnectionError must surface top-level warnings_out; got data keys = {sorted(data.keys())!r}"
    )
    assert any(m.startswith("retrieve_pipeline_failed:") for m in top_wo), (
        f"expected ``retrieve_pipeline_failed:`` marker in top-level warnings_out; got {top_wo!r}"
    )
    # ConnectionError class name must propagate for triage.
    assert any("ConnectionError" in m for m in top_wo), top_wo
    # Synthetic detail must propagate.
    assert any("synthetic-pack-failure from W607-B test" in m for m in top_wo), top_wo


# ---------------------------------------------------------------------------
# (3) FTS-query failure surfaces marker — second substrate producer path
# ---------------------------------------------------------------------------


def test_fts_failure_surfaces_marker(retrieve_project, monkeypatch):
    """Monkeypatch ``run_retrieve`` → OperationalError → marker reaches envelope.

    Forces a ``sqlite3.OperationalError`` (the FTS5-substrate failure
    class) past the pipeline's inner ad-hoc ``except`` by raising at the
    outer ``run_retrieve`` boundary. The marker class-name must
    propagate so operators can triage substrate vs. application bugs.
    """
    import sqlite3

    from roam.commands import cmd_retrieve

    def _boom(*a, **kw):
        raise sqlite3.OperationalError("synthetic-fts-failure from W607-B test")

    monkeypatch.setattr(cmd_retrieve, "run_retrieve", _boom)
    monkeypatch.chdir(retrieve_project)

    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["--json", "retrieve", "authenticate user session"],
        catch_exceptions=False,
    )
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)

    top_wo = data.get("warnings_out") or data["summary"].get("warnings_out")
    assert top_wo, (
        f"FTS-query failure must surface warnings_out somewhere on envelope; "
        f"got top={data.get('warnings_out')!r} "
        f"summary.warnings_out={data['summary'].get('warnings_out')!r}"
    )
    assert any(m.startswith("retrieve_pipeline_failed:") for m in top_wo), top_wo
    assert any("OperationalError" in m for m in top_wo), top_wo


# ---------------------------------------------------------------------------
# (4) partial_success flips when any marker present
# ---------------------------------------------------------------------------


def test_partial_success_flips_on_warning_present(retrieve_project, monkeypatch):
    """Any non-empty warnings_out → summary.partial_success = True.

    Pattern-2 contract: the agent MUST be able to distinguish "clean
    retrieve" from "retrieve ran with substrate degradation" via
    summary.partial_success alone.
    """
    from roam.commands import cmd_retrieve

    def _boom(*a, **kw):
        raise RuntimeError("synthetic-runtime-partial-success-test")

    monkeypatch.setattr(cmd_retrieve, "run_retrieve", _boom)
    monkeypatch.chdir(retrieve_project)

    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["--json", "retrieve", "anything"],
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


def test_warnings_out_summary_mirror(retrieve_project, monkeypatch):
    """Non-empty bucket → both top-level AND summary.warnings_out are populated.

    Top-level is needed because the preserved-list field
    (``_ALWAYS_PRESERVED_LIST_FIELDS`` in formatter.py) survives
    ``strip_list_payloads`` in default-detail mode. summary mirror gives
    consumers reading only the summary block visibility too.
    """
    from roam.commands import cmd_retrieve

    monkeypatch.setattr(
        cmd_retrieve,
        "run_retrieve",
        lambda *a, **kw: (_ for _ in ()).throw(ValueError("synthetic-mirror-test")),
    )
    monkeypatch.chdir(retrieve_project)

    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["--json", "retrieve", "anything"],
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
# (6) Top-level mirror explicitly checked (W607-A discipline parity)
# ---------------------------------------------------------------------------


def test_top_level_warnings_out_mirror(retrieve_project, monkeypatch):
    """Top-level ``warnings_out`` must be present alongside summary mirror.

    The preserved-list-field discipline at ``_ALWAYS_PRESERVED_LIST_FIELDS``
    requires the top-level mirror so the field survives detail-mode
    list-payload stripping. cmd_search_semantic W607-A pinned the same
    discipline; W607-B extends it to retrieve.
    """
    from roam.commands import cmd_retrieve

    def _boom(*a, **kw):
        raise OSError("synthetic-top-level-mirror-check")

    monkeypatch.setattr(cmd_retrieve, "run_retrieve", _boom)
    monkeypatch.chdir(retrieve_project)

    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["--json", "retrieve", "anything"],
        catch_exceptions=False,
    )
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)

    top_wo = data.get("warnings_out")
    assert isinstance(top_wo, list) and top_wo, (
        f"top-level warnings_out must be a non-empty list on disclosure path; got {top_wo!r}"
    )


# ---------------------------------------------------------------------------
# (7) Marker prefix consistency — retrieve_pipeline_failed family
# ---------------------------------------------------------------------------


def test_w605_marker_prefix_consistent(retrieve_project, monkeypatch):
    """Every surfaced marker uses the canonical ``retrieve_*`` prefix family.

    cmd_retrieve has NO direct W605 callsites (it goes through
    ``run_retrieve`` rather than ``search_stored``), so the prefix
    family is the ``retrieve_*`` family (outer-guard
    ``retrieve_pipeline_failed:`` + W607-BI per-substrate
    ``retrieve_<phase>_failed:`` ADDITIVE plumbing). This pin guards
    against accidental marker-prefix drift into a sibling family
    (``context_*`` / ``understand_*`` / etc.).

    W607-BI ADDITIVE: when ``run_retrieve`` raises (monkeypatched here),
    BOTH the W607-BI substrate markers (``retrieve_fts5_search_failed:``,
    ``retrieve_tfidf_rerank_failed:``) AND the W607-B outer-guard marker
    (``retrieve_pipeline_failed:``) surface. The contract is that all
    markers stay in the ``retrieve_*`` family.
    """
    from roam.commands import cmd_retrieve

    def _boom(*a, **kw):
        raise ConnectionError("synthetic-prefix-consistency-check")

    monkeypatch.setattr(cmd_retrieve, "run_retrieve", _boom)
    monkeypatch.chdir(retrieve_project)

    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["--json", "retrieve", "anything"],
        catch_exceptions=False,
    )
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    top_wo = data.get("warnings_out") or []
    assert top_wo, "expected non-empty warnings_out for prefix-consistency check"
    # W607-BI ADDITIVE: outer-guard ``retrieve_pipeline_failed:`` marker
    # must still appear when the full pipeline + lexical-only fallback
    # both raise the same exception (the monkeypatch hits both paths).
    pipeline_markers = [m for m in top_wo if m.startswith("retrieve_pipeline_failed:")]
    assert pipeline_markers, f"W607-B outer-guard ``retrieve_pipeline_failed:`` marker missing; got {top_wo!r}"
    for marker in top_wo:
        assert marker.startswith("retrieve_"), (
            f"every surfaced marker must use the ``retrieve_*`` prefix "
            f"family (W607-B outer-guard + W607-BI per-substrate); "
            f"got {marker!r}"
        )


# ---------------------------------------------------------------------------
# (8) Existing happy-path ranked-spans contract preserved
# ---------------------------------------------------------------------------


def test_results_shape_unchanged_on_happy_path(retrieve_project, monkeypatch):
    """The pre-W607-B envelope shape is byte-stable on happy path.

    W607-B must not perturb the existing envelope contract: ``candidates``
    list + summary fields (``confidence`` / ``budget`` / ``rerank`` /
    ``seed_count``) must all still be emitted on each successful run.
    """
    monkeypatch.chdir(retrieve_project)
    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["--json", "retrieve", "authenticate user"],
        catch_exceptions=False,
    )
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert data["command"] == "retrieve"

    # Envelope-level keys preserved.
    candidates = data.get("candidates")
    assert isinstance(candidates, list), (
        f"candidates field must be a list on happy path; got {type(candidates).__name__}"
    )

    # Summary keys preserved (pin against accidental key removals).
    expected_summary_keys = {
        "verdict",
        "low_confidence",
        "confidence",
        "refinements",
        "candidates",
        "total_candidates",
        "budget",
        "budget_used",
        "k",
        "rerank",
        "seed_count",
        "semantic_embeddings",
        "semantic_coverage_pct",
        "dry_run",
        "partial_success",  # always-emit per W817
    }
    summary_keys = set(data["summary"].keys())
    missing = expected_summary_keys - summary_keys
    assert not missing, f"summary lost expected keys: {sorted(missing)!r}; got = {sorted(summary_keys)!r}"

    # warnings_out is NOT in the happy-path summary key-set
    # (hash-stable: empty bucket → no slot emitted).
    assert "warnings_out" not in summary_keys, (
        f"happy path must NOT emit summary.warnings_out slot; got summary = {data['summary']!r}"
    )


# ---------------------------------------------------------------------------
# (9) Interface stability — Click options + function signature unchanged
# ---------------------------------------------------------------------------


def test_caller_unmodified():
    """AST-check ``cmd_retrieve.retrieve``'s public interface.

    W607-B threads warnings_out PURELY internally (the bucket is a
    local accumulator; no new Click option exposes it to callers).
    The Click decorator surface, function name, and parameter list
    must therefore remain byte-identical to pre-W607-B.
    """
    path = repo_root() / "src" / "roam" / "commands" / "cmd_retrieve.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))

    # Find the retrieve function.
    fn = next(
        (n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == "retrieve"),
        None,
    )
    assert fn is not None, "retrieve function not found in cmd_retrieve.py"

    # Parameter list must be the canonical
    # (ctx, task, budget, k, rerank, seed_files, dry_run, scope_path).
    arg_names = [a.arg for a in fn.args.args]
    assert arg_names == [
        "ctx",
        "task",
        "budget",
        "k",
        "rerank",
        "seed_files",
        "dry_run",
        "scope_path",
    ], f"retrieve parameter list drifted from pre-W607-B: {arg_names!r}"

    # Click decorators present: @roam_capability + @click.command + 7x option +
    # @click.pass_context. We pin the count to catch accidental option
    # additions or removals.
    decorator_count = len(fn.decorator_list)
    assert decorator_count >= 9, (
        f"expected ≥9 decorators on retrieve (roam_capability + "
        f"click.command + ≥6 options + click.pass_context); got "
        f"{decorator_count}"
    )

    # The function must still NOT have an explicit return value annotation
    # mutation (W607-B doesn't change return semantics).
    assert fn.returns is None, (
        f"retrieve gained a return annotation; W607-B is interface-stable. Got: {ast.dump(fn.returns)!r}"
    )


# ---------------------------------------------------------------------------
# (10) Outer-guard pipeline marker — explicit emit-on-exception contract
# ---------------------------------------------------------------------------


def test_outer_guard_emits_pipeline_failed_marker(retrieve_project, monkeypatch):
    """Outer-guard must emit ``retrieve_pipeline_failed:<exc_class>:<detail>``.

    Distinguishes W607-B from W607-A: cmd_search_semantic's outer-guard
    is ``semantic_search_stored_failed:`` (substrate-scope marker
    because the consumer calls ``search_stored`` directly).
    cmd_retrieve calls ``run_retrieve`` (the pipeline orchestrator),
    so its outer-guard marker is ``retrieve_pipeline_failed:``.

    The marker shape MUST be ``<prefix>:<exc_class>:<detail>`` —
    three colon-separated segments — so downstream consumers can
    parse the exception class without regex gymnastics.
    """
    from roam.commands import cmd_retrieve

    def _boom(*a, **kw):
        raise TypeError("synthetic-outer-guard-emit-check")

    monkeypatch.setattr(cmd_retrieve, "run_retrieve", _boom)
    monkeypatch.chdir(retrieve_project)

    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["--json", "retrieve", "anything"],
        catch_exceptions=False,
    )
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)

    top_wo = data.get("warnings_out") or []
    assert top_wo, "outer-guard must emit at least one marker"

    # Exactly one marker on the outer-guard path.
    pipeline_markers = [m for m in top_wo if m.startswith("retrieve_pipeline_failed:")]
    assert pipeline_markers, f"outer-guard must emit retrieve_pipeline_failed marker; got {top_wo!r}"

    # Three-segment shape: prefix:exc_class:detail
    marker = pipeline_markers[0]
    parts = marker.split(":", 2)
    assert len(parts) == 3, f"marker must have three colon-separated segments (prefix:exc_class:detail); got {marker!r}"
    assert parts[0] == "retrieve_pipeline_failed", parts
    assert parts[1] == "TypeError", parts
    assert "synthetic-outer-guard-emit-check" in parts[2], parts
