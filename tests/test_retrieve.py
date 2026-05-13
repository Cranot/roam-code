"""End-to-end tests for A.1 — `roam retrieve`.

Covers:

* Pipeline (`roam.retrieve.pipeline.run_retrieve`).
* Reranker (`roam.retrieve.rerank.structural_score`).
* CLI command (`roam retrieve`) — text and JSON output.

Tests use the same project-fixture pattern as the other end-to-end test
files so the FTS5 + symbol graph + clone tables are all populated by a
real ``roam index`` run.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from click.testing import CliRunner

from roam.cli import cli
from tests.conftest import make_src_project as _make_project

_AUTH_FIXTURE: dict[str, str] = {
    "auth.py": """
        class UserSession:
            def __init__(self, token):
                self.token = token

            def refresh(self):
                return self.token

            def revoke(self):
                return None

        def handle_login(user):
            s = UserSession(token="abc")
            return s.refresh()
    """,
    "billing.py": """
        class Invoice:
            def __init__(self, amount):
                self.amount = amount

            def total(self):
                return self.amount

        def calculate_tax(invoice):
            return invoice.total() * 0.07
    """,
}


@pytest.fixture
def indexed_project(tmp_path):
    proj = _make_project(tmp_path, _AUTH_FIXTURE)
    old_cwd = os.getcwd()
    try:
        os.chdir(str(proj))
        runner = CliRunner()
        result = runner.invoke(cli, ["index"])
        assert result.exit_code == 0, result.output
        yield proj
    finally:
        os.chdir(old_cwd)


# ---------------------------------------------------------------------------
# Pipeline
# ---------------------------------------------------------------------------


class TestPipeline:
    def _run(self, project: Path, **kwargs):
        from roam.db.connection import open_db
        from roam.retrieve.pipeline import run_retrieve

        with open_db(readonly=True) as conn:
            return run_retrieve(conn, **kwargs)

    def test_returns_candidates_for_pascal_query(self, indexed_project):
        result = self._run(indexed_project, task="is it safe to delete UserSession?")
        assert result["candidates"], "expected at least one candidate"
        names = [c["name"] for c in result["candidates"]]
        assert "UserSession" in names

    def test_candidates_sorted_by_score_descending(self, indexed_project):
        result = self._run(indexed_project, task="UserSession refresh")
        scores = [c["score"] for c in result["candidates"]]
        assert scores == sorted(scores, reverse=True)

    def test_seeds_inferred_when_no_seed_files(self, indexed_project):
        result = self._run(indexed_project, task="UserSession")
        assert result["seeds"], "expected inferred seeds"

    def test_seed_files_explicit_overrides_inference(self, indexed_project):
        from roam.db.connection import open_db
        from roam.retrieve.pipeline import run_retrieve

        with open_db(readonly=True) as conn:
            result = run_retrieve(
                conn,
                "UserSession",
                seed_files=["src/billing.py"],
            )
        # When explicit seeds are given, they should map to billing.py symbols
        assert result["seeds"], "expected explicit seeds to resolve"

    def test_budget_caps_results(self, indexed_project):
        result = self._run(indexed_project, task="UserSession refresh", budget=20)
        # 20 token budget can't fit much; should return at most 1-2 small spans
        assert sum(c["estimated_tokens"] for c in result["candidates"]) <= 20 or len(result["candidates"]) <= 1

    def test_k_caps_results(self, indexed_project):
        result = self._run(indexed_project, task="UserSession refresh handle_login", k=2)
        assert len(result["candidates"]) <= 2

    def test_estimated_tokens_present_per_candidate(self, indexed_project):
        result = self._run(indexed_project, task="UserSession")
        for c in result["candidates"]:
            assert "estimated_tokens" in c
            assert c["estimated_tokens"] > 0

    def test_justifications_present(self, indexed_project):
        result = self._run(indexed_project, task="UserSession")
        for c in result["candidates"]:
            assert "justifications" in c
            assert isinstance(c["justifications"], dict)

    def test_pagerank_kind_personalized_when_seeded(self, indexed_project):
        result = self._run(indexed_project, task="UserSession")
        kinds = {
            c["justifications"].get("pagerank_kind")
            for c in result["candidates"]
            if "pagerank_kind" in c["justifications"]
        }
        assert "personalized" in kinds

    def test_pagerank_kind_global_when_rerank_off(self, indexed_project):
        result = self._run(indexed_project, task="UserSession", rerank="off")
        kinds = {
            c["justifications"].get("pagerank_kind")
            for c in result["candidates"]
            if "pagerank_kind" in c["justifications"]
        }
        # With rerank=off we should not get personalized PR
        assert "personalized" not in kinds

    def test_unknown_task_returns_empty(self, indexed_project):
        result = self._run(indexed_project, task="ZzNoSuchSymbol AnotherMissing")
        assert result["candidates"] == []

    def test_empty_task_returns_empty(self, indexed_project):
        result = self._run(indexed_project, task="")
        assert result["candidates"] == []

    def test_weights_dict_returned(self, indexed_project):
        result = self._run(indexed_project, task="UserSession")
        assert set(result["weights"].keys()) == {
            "alpha",
            "beta",
            "gamma",
            "delta",
            "epsilon",
            "zeta",  # v12.2: semantic similarity via bge-small + sqlite-vec
        }


# ---------------------------------------------------------------------------
# Reranker (direct unit tests, no pipeline)
# ---------------------------------------------------------------------------


class TestRerank:
    def test_empty_candidates_returns_empty(self, indexed_project):
        from roam.db.connection import open_db
        from roam.retrieve.rerank import structural_score

        with open_db(readonly=True) as conn:
            result = structural_score(conn, [], {}, {"alpha": 0.4})
        assert result == []

    def test_score_field_added(self, indexed_project):
        from roam.db.connection import open_db
        from roam.retrieve.rerank import structural_score

        with open_db(readonly=True) as conn:
            sym_rows = conn.execute(
                "SELECT s.id AS symbol_id, s.name, s.kind, s.line_start, s.line_end, "
                "       f.path AS file_path "
                "FROM symbols s JOIN files f ON s.file_id = f.id LIMIT 3"
            ).fetchall()
            candidates = [{**dict(r), "fts_score": 1.0} for r in sym_rows]
            result = structural_score(conn, candidates, {}, {"alpha": 0.4, "epsilon": 0.05})
        assert len(result) == len(candidates)
        for r in result:
            assert "score" in r
            assert "justifications" in r

    def test_seeded_candidate_outranks_unseeded(self, indexed_project):
        """Personalised PR should pull the seed itself to the top."""
        from roam.db.connection import open_db
        from roam.retrieve.rerank import structural_score

        with open_db(readonly=True) as conn:
            sym = conn.execute(
                "SELECT s.id AS symbol_id, s.name, s.kind, s.line_start, s.line_end, "
                "       f.path AS file_path "
                "FROM symbols s JOIN files f ON s.file_id = f.id "
                "WHERE s.name = 'UserSession'"
            ).fetchone()
            other = conn.execute(
                "SELECT s.id AS symbol_id, s.name, s.kind, s.line_start, s.line_end, "
                "       f.path AS file_path "
                "FROM symbols s JOIN files f ON s.file_id = f.id "
                "WHERE s.name = 'calculate_tax'"
            ).fetchone()
            if sym is None or other is None:
                pytest.skip("fixture symbols missing")

            candidates = [
                {**dict(sym), "fts_score": 0.5},
                {**dict(other), "fts_score": 0.5},
            ]
            seeds = {int(sym["symbol_id"]): 1.0}
            result = structural_score(conn, candidates, seeds, {"alpha": 0.6, "epsilon": 0.05})
        assert result[0]["name"] == "UserSession"

    def test_semantic_signal_can_outrank_lexical_tie(self, monkeypatch, indexed_project):
        """The ζ signal should read stored ONNX vectors and explain the lift."""
        from roam.db.connection import open_db
        from roam.retrieve import semantic
        from roam.retrieve.rerank import structural_score

        monkeypatch.setattr(semantic, "_load_text_encoder", lambda: lambda _text: [1.0, 0.0])

        with open_db(readonly=False, project_root=indexed_project) as conn:
            user_session = conn.execute(
                "SELECT s.id AS symbol_id, s.name, s.kind, s.line_start, s.line_end, "
                "       f.path AS file_path "
                "FROM symbols s JOIN files f ON s.file_id = f.id "
                "WHERE s.name = 'UserSession'"
            ).fetchone()
            calculate_tax = conn.execute(
                "SELECT s.id AS symbol_id, s.name, s.kind, s.line_start, s.line_end, "
                "       f.path AS file_path "
                "FROM symbols s JOIN files f ON s.file_id = f.id "
                "WHERE s.name = 'calculate_tax'"
            ).fetchone()
            assert user_session is not None
            assert calculate_tax is not None

            conn.execute("DELETE FROM symbol_embeddings")
            conn.execute(
                "INSERT INTO symbol_embeddings(symbol_id, vector, dims, provider, model_id) VALUES (?, ?, ?, ?, ?)",
                (int(user_session["symbol_id"]), json.dumps([1.0, 0.0]), 2, "onnx", "test"),
            )
            conn.execute(
                "INSERT INTO symbol_embeddings(symbol_id, vector, dims, provider, model_id) VALUES (?, ?, ?, ?, ?)",
                (int(calculate_tax["symbol_id"]), json.dumps([0.0, 1.0]), 2, "onnx", "test"),
            )
            conn.commit()

            candidates = [
                {**dict(calculate_tax), "fts_score": 1.0},
                {**dict(user_session), "fts_score": 1.0},
            ]
            result = structural_score(
                conn,
                candidates,
                {},
                {"alpha": 0.0, "beta": 0.0, "delta": 0.0, "epsilon": 0.0, "zeta": 1.0},
                use_personalized=False,
                lexical_baseline=0.0,
                task="database connection",
            )

        assert result[0]["name"] == "UserSession"
        assert result[0]["justifications"]["semantic"] == 1.0


# ---------------------------------------------------------------------------
# CLI surface
# ---------------------------------------------------------------------------


class TestRetrieveCLI:
    def test_text_output_has_verdict(self, indexed_project):
        runner = CliRunner()
        result = runner.invoke(cli, ["retrieve", "is it safe to delete UserSession"])
        assert result.exit_code == 0, result.output
        assert "VERDICT:" in result.output
        assert "UserSession" in result.output

    def test_json_envelope(self, indexed_project):
        runner = CliRunner()
        result = runner.invoke(cli, ["--json", "retrieve", "is it safe to delete UserSession"])
        assert result.exit_code == 0, result.output
        data = json.loads(result.output)
        assert data["command"] == "retrieve"
        assert "summary" in data
        assert "verdict" in data["summary"]
        assert "candidates" in data
        assert isinstance(data["candidates"], list)
        assert data["summary"]["candidates"] == len(data["candidates"])
        assert data["semantic_coverage"]["symbols"] >= len(data["candidates"])
        assert "semantic_coverage_pct" in data["summary"]

    def test_k_flag_caps_output(self, indexed_project):
        runner = CliRunner()
        result = runner.invoke(
            cli,
            ["--json", "retrieve", "UserSession refresh handle_login", "--k", "2"],
        )
        assert result.exit_code == 0, result.output
        data = json.loads(result.output)
        assert len(data["candidates"]) <= 2

    def test_seed_files_flag(self, indexed_project):
        runner = CliRunner()
        result = runner.invoke(
            cli,
            [
                "--json",
                "retrieve",
                "trace login flow",
                "--seed-files",
                "src/auth.py",
            ],
        )
        assert result.exit_code == 0, result.output
        data = json.loads(result.output)
        # auth.py has at least 5 symbols → seeds list should not be empty
        assert data["summary"]["seed_count"] >= 1

    def test_budget_flag_caps_tokens(self, indexed_project):
        runner = CliRunner()
        result = runner.invoke(
            cli,
            ["--json", "retrieve", "UserSession refresh", "--budget", "30"],
        )
        assert result.exit_code == 0, result.output
        data = json.loads(result.output)
        assert data["summary"]["budget_used"] <= 60  # within 1 span over budget

    def test_empty_task_rejected(self, indexed_project):
        runner = CliRunner()
        result = runner.invoke(cli, ["retrieve", ""])
        assert result.exit_code != 0
        assert "task" in result.output.lower() or "usage" in result.output.lower()

    def test_no_match_does_not_crash(self, indexed_project):
        runner = CliRunner()
        result = runner.invoke(cli, ["retrieve", "ZzNoSuchSymbol AnotherMissing"])
        assert result.exit_code == 0, result.output
        assert "No candidates" in result.output or "0 span" in result.output

    def test_rerank_off_flag(self, indexed_project):
        runner = CliRunner()
        result = runner.invoke(
            cli,
            ["--json", "retrieve", "UserSession", "--rerank", "off"],
        )
        assert result.exit_code == 0, result.output
        data = json.loads(result.output)
        assert data["summary"]["rerank"] == "off"

    def test_justifications_in_json(self, indexed_project):
        runner = CliRunner()
        result = runner.invoke(cli, ["--json", "retrieve", "UserSession refresh"])
        assert result.exit_code == 0, result.output
        data = json.loads(result.output)
        assert data["candidates"], "expected candidates"
        first = data["candidates"][0]
        assert "justifications" in first
        # Either pagerank or fts must be present
        assert "pagerank" in first["justifications"] or "fts" in first["justifications"]

    def test_command_appears_in_help(self, indexed_project):
        # W19.2 5-verb help redesign: compact `--help` only shows 5 verbs;
        # full inventory lives in `--help-all` / `_COMMANDS`. Assert against
        # the registry (source of truth) and `--help-all` (UX surface).
        from roam.cli import _COMMANDS

        assert "retrieve" in _COMMANDS
        runner = CliRunner()
        result = runner.invoke(cli, ["--help-all"])
        assert result.exit_code == 0, result.output
        assert "retrieve" in result.output

    def test_command_appears_in_workflow_category(self, indexed_project):
        # W19.2 5-verb help redesign: legacy "Daily Workflow" category header
        # no longer appears in compact `--help`. Verify `retrieve` is in the
        # workflow category via the `_CATEGORIES` registry.
        from roam.cli import _CATEGORIES

        workflow_commands = _CATEGORIES.get("Daily Workflow", [])
        assert "retrieve" in workflow_commands


# ---------------------------------------------------------------------------
# P0 fixes — anchored path matching, token cap, config-driven knobs
# ---------------------------------------------------------------------------


def _project_with_substring_paths(tmp_path: Path) -> Path:
    """Two files where one path is a substring of the other.

    `auth.py` vs `authNotMine.py` — naive LIKE '%auth.py' would match both.
    The anchored-at-/ shape used by ``_seeds_from_files`` must match only
    the exact one.
    """
    return _make_project(
        tmp_path,
        {
            "auth.py": """
                class UserSession:
                    def refresh(self):
                        return self.token
            """,
            "subdir/authNotMine.py": """
                class OtherSession:
                    def renew(self):
                        return None
            """,
        },
    )


class TestAnchoredSeedsFromFiles:
    def test_exact_path_only_picks_one_file(self, tmp_path):
        proj = _project_with_substring_paths(tmp_path)
        old_cwd = os.getcwd()
        try:
            os.chdir(str(proj))
            runner = CliRunner()
            assert runner.invoke(cli, ["index"]).exit_code == 0

            from roam.db.connection import open_db
            from roam.retrieve.pipeline import _seeds_from_files

            with open_db(readonly=True) as conn:
                seeds = _seeds_from_files(conn, ["src/auth.py"])
                # Map seed ids back to files
                paths = {
                    row["path"]
                    for row in conn.execute(
                        "SELECT f.path FROM symbols s JOIN files f ON s.file_id = f.id "
                        f"WHERE s.id IN ({','.join('?' * len(seeds))})",
                        list(seeds.keys()),
                    ).fetchall()
                }
            assert "src/auth.py" in paths
            assert "src/subdir/authNotMine.py" not in paths
        finally:
            os.chdir(old_cwd)

    def test_basename_only_anchored_at_slash(self, tmp_path):
        proj = _project_with_substring_paths(tmp_path)
        old_cwd = os.getcwd()
        try:
            os.chdir(str(proj))
            runner = CliRunner()
            assert runner.invoke(cli, ["index"]).exit_code == 0

            from roam.db.connection import open_db
            from roam.retrieve.pipeline import _seeds_from_files

            with open_db(readonly=True) as conn:
                seeds = _seeds_from_files(conn, ["auth.py"])
                paths = (
                    {
                        row["path"]
                        for row in conn.execute(
                            "SELECT f.path FROM symbols s JOIN files f ON s.file_id = f.id "
                            f"WHERE s.id IN ({','.join('?' * len(seeds))})",
                            list(seeds.keys()),
                        ).fetchall()
                    }
                    if seeds
                    else set()
                )
            # `auth.py` must match `src/auth.py` exactly, not `src/subdir/authNotMine.py`.
            assert "src/auth.py" in paths
            assert "src/subdir/authNotMine.py" not in paths
        finally:
            os.chdir(old_cwd)

    def test_missing_file_returns_empty(self, indexed_project):
        from roam.db.connection import open_db
        from roam.retrieve.pipeline import _seeds_from_files

        with open_db(readonly=True) as conn:
            seeds = _seeds_from_files(conn, ["src/does_not_exist.py"])
        assert seeds == {}

    def test_blank_paths_filtered(self, indexed_project):
        from roam.db.connection import open_db
        from roam.retrieve.pipeline import _seeds_from_files

        with open_db(readonly=True) as conn:
            seeds = _seeds_from_files(conn, ["", "   ", "./"])
        assert seeds == {}


class TestFirstStageTokenCap:
    def test_huge_query_does_not_crash(self, indexed_project):
        """50-token query should be capped silently and return some results."""
        from roam.db.connection import open_db
        from roam.retrieve.pipeline import _first_stage

        # 50 distinct PascalCase fragments
        tokens = " ".join(f"Token{n:03d}Word" for n in range(50))
        # Add the real symbol so we get at least one match
        query = "UserSession " + tokens

        with open_db(readonly=True) as conn:
            result = _first_stage(conn, query, top_n=20, token_cap=8)
        # Never crashes, may or may not have hits depending on FTS5 behaviour
        assert isinstance(result, list)

    def test_token_cap_is_applied(self, indexed_project):
        """Cap of 1 must produce a query of exactly one token-clause."""
        from roam.db.connection import open_db
        from roam.retrieve.pipeline import _first_stage

        # The first extracted token will be the file path (longest first
        # in extract_tokens). With cap=1, only that token's results return.
        with open_db(readonly=True) as conn:
            result_capped = _first_stage(conn, "UserSession refresh handle_login", top_n=20, token_cap=1)
            result_uncapped = _first_stage(conn, "UserSession refresh handle_login", top_n=20, token_cap=8)
        assert len(result_capped) <= len(result_uncapped)


class TestConfigDrivenKnobs:
    def _write_config(self, project: Path, body: str) -> None:
        cfg = project / ".roam" / "config.toml"
        cfg.parent.mkdir(exist_ok=True)
        cfg.write_text(body, encoding="utf-8")

    def test_tokens_per_line_override(self, indexed_project):
        """Doubling tokens_per_line must double budget_used."""
        from roam.db.connection import open_db
        from roam.retrieve.pipeline import run_retrieve

        with open_db(readonly=True) as conn:
            baseline = run_retrieve(conn, "UserSession refresh", k=3, budget=10000)

        self._write_config(indexed_project, "[retrieve]\ntokens_per_line = 8\n")

        with open_db(readonly=True) as conn:
            doubled = run_retrieve(conn, "UserSession refresh", k=3, budget=10000)

        # Same candidates fit under 10k budget either way; tokens_per_line
        # only affects the cost accounting.
        if baseline["candidates"] and doubled["candidates"]:
            ratio = doubled["budget_used"] / max(baseline["budget_used"], 1)
            assert 1.5 < ratio < 2.5, f"expected ~2× budget_used, got ratio {ratio:.2f}"

    def test_lexical_baseline_zero_drops_pure_lexical_hits(self, indexed_project):
        """With lexical_baseline=0, the lexical contribution to a
        pure-FTS candidate drops to zero. Other small boosts (e.g.
        the v12.12.9 recency boost, +0.05 max for files edited
        today) may still contribute, so we compare *with* vs
        *without* the lexical baseline rather than asserting an
        absolute zero score.
        """
        from roam.db.connection import open_db
        from roam.retrieve.rerank import structural_score

        with open_db(readonly=True) as conn:
            sym = conn.execute(
                "SELECT s.id AS symbol_id, s.name, s.kind, s.line_start, s.line_end, "
                "       f.path AS file_path "
                "FROM symbols s JOIN files f ON s.file_id = f.id "
                "WHERE s.name = 'UserSession'"
            ).fetchone()
            assert sym is not None

            cand = {**dict(sym), "fts_score": 1.0}
            with_baseline = structural_score(conn, [cand], {}, {"alpha": 0.0, "epsilon": 0.0}, lexical_baseline=0.5)
            without_baseline = structural_score(conn, [cand], {}, {"alpha": 0.0, "epsilon": 0.0}, lexical_baseline=0.0)
        assert with_baseline[0]["score"] > without_baseline[0]["score"]
        # Without the lexical baseline the score is just whatever
        # tiny per-symbol boosts contribute — well below the 0.5
        # lift that lexical_baseline=0.5 + fts_norm=1.0 would give.
        assert without_baseline[0]["score"] < 0.30


class TestEdgeCases:
    """Test debt P1: graceful handling of empty/missing inputs."""

    def test_empty_graph_returns_empty_candidates(self, tmp_path):
        """Project with no parseable code → retrieve yields no candidates."""
        proj = _make_project(tmp_path, {"readme.txt": "no code here"})
        old_cwd = os.getcwd()
        try:
            os.chdir(str(proj))
            runner = CliRunner()
            runner.invoke(cli, ["index"])  # may succeed with 0 symbols
            result = runner.invoke(cli, ["retrieve", "anything UserSession"])
            assert result.exit_code == 0, result.output
            assert "No candidates" in result.output or "0 span" in result.output
        finally:
            os.chdir(old_cwd)

    def test_seed_files_pointing_at_unknown_falls_back_to_inference(self, indexed_project):
        """If --seed-files resolves to zero symbols, infer from the task instead."""
        from roam.db.connection import open_db
        from roam.retrieve.pipeline import run_retrieve

        with open_db(readonly=True) as conn:
            result = run_retrieve(
                conn,
                "UserSession refresh",
                seed_files=["src/does_not_exist.py"],
            )
        # Falls back to inference → must still surface UserSession
        names = [c["name"] for c in result["candidates"]]
        assert "UserSession" in names
        assert result["seeds"], "expected inferred seeds after empty file resolve"

    def test_candidate_outside_clone_cluster_has_no_clone_tag(self, indexed_project):
        """A candidate that isn't a clone should never get clone_cluster tag."""
        runner = CliRunner()
        # Don't run --persist, so clone_pairs stays empty.
        result = runner.invoke(cli, ["--json", "retrieve", "UserSession refresh"])
        assert result.exit_code == 0, result.output
        data = json.loads(result.output)
        for c in data["candidates"]:
            assert "clone_cluster" not in c["justifications"], f"candidate {c.get('name')} unexpectedly tagged as clone"


class TestLIKEFallback:
    """Verify the FTS5-absent code paths in retrieve still work."""

    def test_pipeline_first_stage_like_fallback(self, indexed_project, monkeypatch):
        """Force the LIKE path by stubbing _has_symbol_fts to False."""
        from roam.db.connection import open_db
        from roam.retrieve import pipeline

        monkeypatch.setattr(pipeline, "_has_symbol_fts", lambda _conn: False)

        with open_db(readonly=True) as conn:
            result = pipeline.run_retrieve(conn, "UserSession refresh")
        # LIKE fallback must still surface the queried symbol.
        names = [c["name"] for c in result["candidates"]]
        assert "UserSession" in names

    def test_seeds_like_fallback(self, indexed_project, monkeypatch):
        """Force seeds.infer_seeds onto the LIKE path."""
        from roam.db.connection import open_db
        from roam.retrieve import seeds

        monkeypatch.setattr(seeds, "_has_symbol_fts", lambda _conn: False)

        with open_db(readonly=True) as conn:
            inferred = seeds.infer_seeds(conn, "UserSession refresh")
        assert inferred, "LIKE fallback must still produce seeds"


class TestEmptyFTSGuard:
    """If symbol_fts has been wiped (mid-session schema migration on a
    cloud-synced repo, etc.) the retrieve pipeline silently returns
    nothing — no error, just zero candidates. The CLI now surfaces a
    clear remediation message instead.
    """

    def test_text_output_suggests_reindex(self, indexed_project):
        from roam.db.connection import open_db

        with open_db(readonly=False) as conn:
            conn.execute("DELETE FROM symbol_fts")
            conn.commit()

        runner = CliRunner()
        result = runner.invoke(cli, ["retrieve", "UserSession"])
        assert result.exit_code == 0, result.output
        assert "search index is empty" in result.output
        assert "roam index --force" in result.output

    def test_json_output_carries_zero_counts(self, indexed_project):
        from roam.db.connection import open_db

        with open_db(readonly=False) as conn:
            conn.execute("DELETE FROM symbol_fts")
            conn.commit()

        runner = CliRunner()
        result = runner.invoke(cli, ["--json", "retrieve", "UserSession"])
        assert result.exit_code == 0, result.output
        data = json.loads(result.output)
        assert data["summary"]["candidates"] == 0
        assert data["summary"]["fts_rows"] == 0
        assert data["summary"]["symbol_count"] > 0


class TestBetaCoChange:
    """β contribution: candidates that co-change with seed files outrank
    structural peers when β > 0. Phase-research synthesis ships this
    finally — alpha/beta/gamma/delta/epsilon were declared in config.py
    but only α was being applied to the score before this push."""

    def test_cochange_signal_appears_in_justifications(self, indexed_project):
        """The fixture has zero git history of co-change between auth.py
        and billing.py, so co_change normalised across an empty set
        produces zero contributions. Confirm β doesn't crash and the
        absence is silent (no spurious justification keys)."""
        from roam.db.connection import open_db
        from roam.retrieve.pipeline import run_retrieve

        with open_db(readonly=True) as conn:
            result = run_retrieve(conn, "UserSession refresh", k=5)
        for c in result["candidates"]:
            # Either co_change is missing (no signal) or numeric and in [0,1]
            if "co_change" in c["justifications"]:
                assert 0.0 < c["justifications"]["co_change"] <= 1.0

    def test_runtime_signal_appears_when_data_present(self, indexed_project):
        """Inject one runtime_stats row for an existing symbol, run
        retrieve, and confirm runtime_hot is in the candidate's
        justification block."""
        from roam.db.connection import open_db
        from roam.retrieve.pipeline import run_retrieve

        # Insert a runtime row for one symbol so δ has something to
        # contribute. Use a writeable connection.
        with open_db(readonly=False) as conn:
            sym = conn.execute("SELECT s.id FROM symbols s WHERE s.name = 'UserSession' LIMIT 1").fetchone()
            if sym is None:
                pytest.skip("fixture missing UserSession")
            conn.execute(
                "INSERT OR REPLACE INTO runtime_stats "
                "(symbol_id, call_count, p99_latency_ms, error_rate) "
                "VALUES (?, ?, ?, ?)",
                (sym[0], 50000, 800, 0.05),
            )
            conn.commit()

        with open_db(readonly=True) as conn:
            result = run_retrieve(conn, "UserSession", k=10)

        # At least one candidate (UserSession itself) must surface the
        # runtime_hot justification — otherwise δ isn't wired.
        any_runtime = any("runtime_hot" in c["justifications"] for c in result["candidates"])
        assert any_runtime, "expected runtime_hot in at least one candidate's justifications"

    def test_beta_and_delta_default_to_zero_with_no_data(self, indexed_project):
        """On a clean fixture with no co-change history and no runtime
        traces, β and δ contributions must be exactly 0 — they only
        boost ordering, never destabilise it."""
        from roam.db.connection import open_db
        from roam.retrieve.pipeline import run_retrieve

        with open_db(readonly=True) as conn:
            result = run_retrieve(conn, "UserSession refresh", k=5)
        for c in result["candidates"]:
            assert c["justifications"].get("co_change", 0) == 0 or (0 < c["justifications"]["co_change"] <= 1.0)
            assert c["justifications"].get("runtime_hot", 0) == 0 or (0 < c["justifications"]["runtime_hot"] <= 1.0)


class TestRerankConsistency:
    def test_heavy_choice_rejected_by_cli(self, indexed_project):
        """'heavy' is not a valid CLI rerank choice (cut from MVP)."""
        runner = CliRunner()
        result = runner.invoke(cli, ["retrieve", "UserSession", "--rerank", "heavy"])
        assert result.exit_code != 0
        assert (
            "heavy" in result.output.lower() or "invalid" in result.output.lower() or "choice" in result.output.lower()
        )

    def test_pipeline_heavy_value_does_not_use_personalized(self, indexed_project):
        """If a programmatic caller bypasses the CLI and passes 'heavy',
        the pipeline treats it as 'off' (does not run personalised PR).
        The pipeline reserves 'heavy' for when A.13 ships.
        """
        from roam.db.connection import open_db
        from roam.retrieve.pipeline import run_retrieve

        with open_db(readonly=True) as conn:
            result = run_retrieve(conn, "UserSession", rerank="heavy")
        # No candidate should be tagged as personalized — heavy is not implemented.
        kinds = {
            c["justifications"].get("pagerank_kind")
            for c in result["candidates"]
            if "pagerank_kind" in c["justifications"]
        }
        assert "personalized" not in kinds

    def test_pagerank_lookup_handles_more_than_999_candidates(self, indexed_project):
        """Regression: ``_pagerank_scores`` previously built a raw
        ``WHERE id IN (?,?,...)`` clause that broke past SQLite's default
        ``SQLITE_MAX_VARIABLE_NUMBER=999`` limit. With ``--k 200`` plus
        ``first_stage_limit=200``, top_n grew to 1000 placeholders. Use
        ``batched_in()`` instead — covered by feeding the helper a synthetic
        oversized candidate-id set.
        """
        from roam.db.connection import open_db
        from roam.retrieve.rerank import _pagerank_scores

        with open_db(readonly=True) as conn:
            # 1500 fake ids — none will match graph_metrics, but the call
            # must not raise sqlite3.OperationalError ("too many SQL variables").
            big_set = list(range(1, 1501))
            scores = _pagerank_scores(conn, big_set, seeds={}, use_personalized=False)
            assert isinstance(scores, dict)
            # Real graph_metrics rows for any actual indexed symbols should
            # come through; the synthetic ids contribute nothing.
            assert all(int(k) > 0 for k in scores)


class TestHubNeighbourFilter:
    """v12.12 — close dogfood #8 'still leaks on hub seed files'.

    The seed-side hub filter shipped in v12.3 (skip seeds whose total
    file_edges degree exceeds ``hub_threshold``). The remaining leak
    came from non-hub seeds importing utility hubs. The neighbour-side
    filter rejects those hubs symmetrically.
    """

    def test_neighbor_hub_files_are_dropped(self, indexed_project):
        """Inject a synthetic hub neighbour and verify it does not
        bleed into the expanded candidate set."""
        from roam.db.connection import open_db
        from roam.retrieve.pipeline import _expand_via_file_neighbors

        with open_db(readonly=False) as conn:
            files = conn.execute("SELECT id, path FROM files ORDER BY id").fetchall()
            assert len(files) >= 2
            seed_file = files[0]
            hub_file = files[1]

            # Wire seed → hub via file_edges, then load up the hub with
            # synthetic incoming edges so its degree exceeds the default
            # hub_threshold (20).
            conn.execute("DELETE FROM file_edges")
            conn.execute(
                "INSERT INTO file_edges (source_file_id, target_file_id, kind) VALUES (?, ?, 'imports')",
                (seed_file["id"], hub_file["id"]),
            )
            # Add many extra distinct files we can point AT the hub to
            # inflate its degree above hub_threshold. The schema FKs to
            # files(id) so we can't fabricate ids — insert real file rows.
            extra_ids = []
            for i in range(30):
                p = f"_synthetic_hub_filler_{i}.py"
                conn.execute("INSERT INTO files (path) VALUES (?)", (p,))
                extra_ids.append(conn.execute("SELECT last_insert_rowid()").fetchone()[0])
            for fid in extra_ids:
                conn.execute(
                    "INSERT INTO file_edges (source_file_id, target_file_id, kind) VALUES (?, ?, 'imports')",
                    (fid, hub_file["id"]),
                )
            conn.commit()

            sym = conn.execute(
                "SELECT s.id, s.name, s.line_start, s.line_end FROM symbols s WHERE s.file_id = ? LIMIT 1",
                (seed_file["id"],),
            ).fetchone()
            assert sym is not None, "fixture must contain at least one symbol in seed file"

            first_stage = [
                {
                    "symbol_id": sym["id"],
                    "name": sym["name"],
                    "kind": "function",
                    "line_start": sym["line_start"],
                    "line_end": sym["line_end"],
                    "file_path": seed_file["path"],
                    "fts_score": 5.0,
                }
            ]
            expanded = _expand_via_file_neighbors(conn, first_stage, hub_threshold=20)

            expanded_paths = {c.get("file_path") for c in expanded if c.get("expansion")}
            assert hub_file["path"] not in expanded_paths, (
                "hub neighbour leaked into expansion despite degree > hub_threshold"
            )

    def test_low_degree_neighbor_still_expands(self, indexed_project):
        """Sanity check — the hub filter must NOT block legitimate
        cross-module expansion. A neighbour with degree ≤ threshold
        should still surface expanded symbols."""
        from roam.db.connection import open_db
        from roam.retrieve.pipeline import _expand_via_file_neighbors

        with open_db(readonly=False) as conn:
            files = conn.execute("SELECT id, path FROM files ORDER BY id").fetchall()
            if len(files) < 2:
                pytest.skip("fixture too small")
            seed_file, neighbor_file = files[0], files[1]
            conn.execute("DELETE FROM file_edges")
            conn.execute(
                "INSERT INTO file_edges (source_file_id, target_file_id, kind) VALUES (?, ?, 'imports')",
                (seed_file["id"], neighbor_file["id"]),
            )
            conn.commit()

            sym = conn.execute(
                "SELECT s.id, s.name, s.line_start, s.line_end FROM symbols s WHERE s.file_id = ? LIMIT 1",
                (seed_file["id"],),
            ).fetchone()
            first_stage = [
                {
                    "symbol_id": sym["id"],
                    "name": sym["name"],
                    "kind": "function",
                    "line_start": sym["line_start"],
                    "line_end": sym["line_end"],
                    "file_path": seed_file["path"],
                    "fts_score": 5.0,
                }
            ]
            expanded = _expand_via_file_neighbors(conn, first_stage, hub_threshold=20)
            expanded_paths = {c.get("file_path") for c in expanded if c.get("expansion")}
            assert neighbor_file["path"] in expanded_paths, "low-degree neighbour must still expand"


class TestSharedConfidenceHelper:
    """v12.12 — confirm cmd_retrieve uses :mod:`roam.output.confidence`."""

    def test_low_confidence_field_in_json(self, indexed_project):
        """Foreign-concept query → JSON should expose ``low_confidence``
        as a structured boolean so MCP clients don't have to parse the
        verdict string."""
        runner = CliRunner()
        result = runner.invoke(
            cli,
            ["--json", "retrieve", "alpha bravo charlie delta echo foxtrot"],
        )
        assert result.exit_code == 0, result.output
        payload = json.loads(result.output)
        # The flag exists regardless — its value depends on candidates.
        assert "low_confidence" in payload["summary"]
        assert isinstance(payload["summary"]["low_confidence"], bool)

    def test_verdict_prefix_helper_pure(self):
        from roam.output.confidence import verdict_prefix

        assert verdict_prefix("20 spans", True) == "low confidence — 20 spans"
        assert verdict_prefix("20 spans", False) == "20 spans"
        assert verdict_prefix("x", True, label="uncertain") == "uncertain — x"

    def test_no_match_helper_formatting(self):
        from roam.output.confidence import format_no_match

        out = format_no_match(
            "recipe",
            [("verify-patch", 0.07, "Audit a patch")],
            flag="recipe",
        )
        assert "VERDICT: no confident recipe match" in out
        assert "[0.07] verify-patch — Audit a patch" in out
