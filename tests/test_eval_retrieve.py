"""Tests for the retrieval eval harness (A.0.4)."""

from __future__ import annotations

import json
import os
import textwrap

import pytest
from click.testing import CliRunner

from roam.cli import cli
from roam.eval.harness import (
    EvalTask,
    aggregate_results,
    evaluate_task,
    load_tasks,
    render_markdown_report,
)
from tests.conftest import make_src_project as _make_project

# ---------------------------------------------------------------------------
# Task loading
# ---------------------------------------------------------------------------


class TestLoadTasks:
    def test_basic_jsonl(self, tmp_path):
        path = tmp_path / "tasks.jsonl"
        path.write_text(
            textwrap.dedent(
                """\
                {"task_id": "t1", "task": "find UserSession", "expected_files": ["src/auth.py"]}
                {"task": "find Invoice", "expected_files": ["src/billing.py"]}
                """
            ),
            encoding="utf-8",
        )
        tasks = load_tasks(path)
        assert len(tasks) == 2
        assert tasks[0].task_id == "t1"
        # Auto-generated id from task text when omitted
        assert tasks[1].task_id

    def test_blank_and_comment_lines_skipped(self, tmp_path):
        path = tmp_path / "tasks.jsonl"
        path.write_text(
            textwrap.dedent(
                """\
                # comment line
                {"task": "x", "expected_files": ["a"]}

                // c-style comment
                {"task": "y", "expected_files": ["b"]}
                """
            ),
            encoding="utf-8",
        )
        tasks = load_tasks(path)
        assert len(tasks) == 2

    def test_missing_task_field_raises(self, tmp_path):
        path = tmp_path / "tasks.jsonl"
        path.write_text(
            '{"expected_files": ["a"]}\n',
            encoding="utf-8",
        )
        with pytest.raises(ValueError, match="missing 'task'"):
            load_tasks(path)

    def test_empty_expected_files_raises(self, tmp_path):
        path = tmp_path / "tasks.jsonl"
        path.write_text(
            '{"task": "x", "expected_files": []}\n',
            encoding="utf-8",
        )
        with pytest.raises(ValueError, match="non-empty"):
            load_tasks(path)

    def test_invalid_json_raises_with_line_number(self, tmp_path):
        path = tmp_path / "tasks.jsonl"
        path.write_text(
            '{"task": "x", "expected_files": ["a"]}\nnot json at all\n',
            encoding="utf-8",
        )
        with pytest.raises(ValueError, match=":2"):
            load_tasks(path)

    def test_paths_normalised(self, tmp_path):
        path = tmp_path / "tasks.jsonl"
        path.write_text(
            '{"task": "x", "expected_files": ["src\\\\a.py", "./src/b.py"]}\n',
            encoding="utf-8",
        )
        tasks = load_tasks(path)
        assert tasks[0].expected_files == ("src/a.py", "src/b.py")


# ---------------------------------------------------------------------------
# Aggregate
# ---------------------------------------------------------------------------


class TestAggregateResults:
    def test_empty_returns_zero(self):
        agg = aggregate_results([])
        assert agg["task_count"] == 0
        assert agg["recall_at_5"] == 0.0
        assert agg["recall_at_20"] == 0.0

    def test_mean_across_tasks(self):
        from roam.eval.harness import TaskResult

        results = [
            TaskResult(
                task_id="a",
                task="t1",
                expected_files=("x",),
                retrieved_files=("x",),
                recall_at={5: 1.0, 10: 1.0, 20: 1.0},
            ),
            TaskResult(
                task_id="b",
                task="t2",
                expected_files=("y",),
                retrieved_files=("z",),
                recall_at={5: 0.0, 10: 0.0, 20: 0.0},
            ),
        ]
        agg = aggregate_results(results)
        assert agg["task_count"] == 2
        assert agg["recall_at_20"] == 0.5


class TestRenderMarkdownReport:
    def test_renders_table(self):
        from roam.eval.harness import TaskResult

        per = [
            TaskResult(
                task_id="t1",
                task="task one",
                expected_files=("a",),
                retrieved_files=("a",),
                recall_at={5: 1.0, 10: 1.0, 20: 1.0},
            )
        ]
        agg = aggregate_results(per)
        md = render_markdown_report(per, agg)
        assert "# roam retrieve eval report" in md
        assert "t1" in md
        assert "1.00" in md


# ---------------------------------------------------------------------------
# End-to-end against an indexed project
# ---------------------------------------------------------------------------


@pytest.fixture
def eval_project(tmp_path):
    proj = _make_project(
        tmp_path,
        {
            "auth.py": """
                class UserSession:
                    def refresh(self):
                        return self.token
                def handle_login(user):
                    return UserSession()
            """,
            "billing.py": """
                class Invoice:
                    def total(self):
                        return self.amount
            """,
        },
    )
    old_cwd = os.getcwd()
    try:
        os.chdir(str(proj))
        runner = CliRunner()
        assert runner.invoke(cli, ["index"]).exit_code == 0
        yield proj
    finally:
        os.chdir(old_cwd)


class TestEvaluateTask:
    def test_perfect_recall_when_query_finds_expected(self, eval_project):
        from roam.db.connection import open_db

        task = EvalTask(
            task_id="t1",
            task="trace UserSession refresh flow",
            expected_files=("src/auth.py",),
        )
        with open_db(readonly=True) as conn:
            result = evaluate_task(conn, task)
        # UserSession is in src/auth.py — must show up at some K.
        assert "src/auth.py" in result.retrieved_files or result.recall_at[20] > 0

    def test_zero_recall_when_query_misses(self, eval_project):
        from roam.db.connection import open_db

        task = EvalTask(
            task_id="impossible",
            task="ZzNoSuchThing AnotherImpossible",
            expected_files=("src/auth.py",),
        )
        with open_db(readonly=True) as conn:
            result = evaluate_task(conn, task)
        assert result.recall_at[5] == 0.0


class TestEvalCLI:
    def test_smoke_runs_against_the_self_test_set(self, eval_project, tmp_path):
        # Build a tiny task set tailored to the fixture
        tasks_path = tmp_path / "tasks.jsonl"
        tasks_path.write_text(
            json.dumps(
                {
                    "task": "trace UserSession refresh",
                    "expected_files": ["src/auth.py"],
                }
            )
            + "\n",
            encoding="utf-8",
        )
        runner = CliRunner()
        result = runner.invoke(cli, ["eval-retrieve", "--tasks", str(tasks_path)])
        assert result.exit_code == 0, result.output
        assert "recall@20" in result.output

    def test_json_envelope(self, eval_project, tmp_path):
        tasks_path = tmp_path / "tasks.jsonl"
        tasks_path.write_text(
            json.dumps(
                {
                    "task": "trace UserSession",
                    "expected_files": ["src/auth.py"],
                }
            )
            + "\n",
            encoding="utf-8",
        )
        runner = CliRunner()
        result = runner.invoke(cli, ["--json", "eval-retrieve", "--tasks", str(tasks_path)])
        assert result.exit_code == 0, result.output
        data = json.loads(result.output)
        assert data["command"] == "eval-retrieve"
        assert data["summary"]["task_count"] == 1
        assert "recall_at_5" in data["summary"]

    def test_min_recall_gate_passes_when_clean(self, eval_project, tmp_path):
        tasks_path = tmp_path / "tasks.jsonl"
        tasks_path.write_text(
            json.dumps({"task": "UserSession refresh", "expected_files": ["src/auth.py"]}) + "\n",
            encoding="utf-8",
        )
        runner = CliRunner()
        result = runner.invoke(
            cli,
            [
                "eval-retrieve",
                "--tasks",
                str(tasks_path),
                "--min-recall-at-20",
                "0.0",  # always passes
            ],
        )
        assert result.exit_code == 0

    def test_min_recall_gate_fails_with_unreachable_threshold(self, eval_project, tmp_path):
        tasks_path = tmp_path / "tasks.jsonl"
        # Unsolvable task — recall must be 0.
        tasks_path.write_text(
            json.dumps(
                {
                    "task": "ZzNoSuchThing Impossible",
                    "expected_files": ["src/auth.py"],
                }
            )
            + "\n",
            encoding="utf-8",
        )
        runner = CliRunner()
        result = runner.invoke(
            cli,
            [
                "eval-retrieve",
                "--tasks",
                str(tasks_path),
                "--min-recall-at-20",
                "0.99",
            ],
        )
        assert result.exit_code == 5

    def test_report_path_writes_markdown(self, eval_project, tmp_path):
        tasks_path = tmp_path / "tasks.jsonl"
        tasks_path.write_text(
            json.dumps({"task": "x", "expected_files": ["src/auth.py"]}) + "\n",
            encoding="utf-8",
        )
        report_path = tmp_path / "report.md"
        runner = CliRunner()
        result = runner.invoke(
            cli,
            [
                "eval-retrieve",
                "--tasks",
                str(tasks_path),
                "--report",
                str(report_path),
            ],
        )
        assert result.exit_code == 0, result.output
        assert report_path.exists()
        assert "roam retrieve eval report" in report_path.read_text(encoding="utf-8")

    def test_unknown_tasks_path_rejected(self, eval_project, tmp_path):
        runner = CliRunner()
        result = runner.invoke(cli, ["eval-retrieve", "--tasks", str(tmp_path / "nope.jsonl")])
        assert result.exit_code != 0


# ---------------------------------------------------------------------------
# Weight-sweep plumbing — verifies that weights actually rotate
# ---------------------------------------------------------------------------


class TestWeightSweepPlumbing:
    """Pre-Round-4, the sweep was a placebo: every iteration ran with
    config-default weights regardless of the grid. After plumbing
    `run_retrieve(weights=...)` through, the sweep results should
    actually vary as α/β/γ/δ/ε rotate.
    """

    def test_run_retrieve_accepts_weights_kwarg(self, eval_project):
        from roam.db.connection import open_db
        from roam.retrieve.pipeline import run_retrieve

        with open_db(readonly=True) as conn:
            override = {"alpha": 0.9, "beta": 0.05, "gamma": 0.0, "delta": 0.0, "epsilon": 0.0}
            result = run_retrieve(
                conn,
                "trace UserSession",
                weights=override,
                k=5,
            )
        # The returned weights dict must reflect the override.
        assert result["weights"]["alpha"] == 0.9
        assert result["weights"]["beta"] == 0.05

    def test_partial_weights_merged_with_config(self, eval_project):
        """Caller passing only `alpha` should still get other weights
        from config — never crash on missing keys."""
        from roam.db.connection import open_db
        from roam.retrieve.pipeline import run_retrieve

        with open_db(readonly=True) as conn:
            result = run_retrieve(
                conn,
                "trace UserSession",
                weights={"alpha": 0.99},
                k=5,
            )
        assert result["weights"]["alpha"] == 0.99
        # Other weights still present (from config defaults)
        assert "beta" in result["weights"]
        assert "epsilon" in result["weights"]

    def test_sweep_produces_different_results_per_vector(self, eval_project):
        """Two extreme weight vectors should produce different ranked
        outputs — proving the sweep actually rotates weights."""
        from roam.db.connection import open_db
        from roam.retrieve.pipeline import run_retrieve

        with open_db(readonly=True) as conn:
            r1 = run_retrieve(
                conn,
                "trace UserSession",
                weights={"alpha": 1.0, "beta": 0.0, "gamma": 0.0, "delta": 0.0, "epsilon": 0.0},
                k=20,
            )
            r2 = run_retrieve(
                conn,
                "trace UserSession",
                weights={"alpha": 0.0, "beta": 0.0, "gamma": 0.0, "delta": 0.0, "epsilon": 0.0},
                k=20,
            )
        # Score ordering should differ between "PageRank-only" and
        # "lexical-baseline-only" — at least one candidate's score must
        # change (since alpha=0 zeros out the PR contribution).
        scores_1 = [c["score"] for c in r1["candidates"]]
        scores_2 = [c["score"] for c in r2["candidates"]]
        assert scores_1 != scores_2, "extreme weight vectors must produce different scores"

    def test_sweep_returns_sorted_by_recall(self, eval_project):
        """The sweep output is sorted descending by recall@target_k."""
        from roam.db.connection import open_db
        from roam.eval.harness import EvalTask, sweep_weights

        tasks = [
            EvalTask(
                task_id="t1",
                task="UserSession refresh",
                expected_files=("src/auth.py",),
            )
        ]
        with open_db(readonly=True) as conn:
            results = sweep_weights(conn, tasks)

        recalls = [r["recall_at_20"] for r in results]
        assert recalls == sorted(recalls, reverse=True), "sweep results must be sorted by recall@20 descending"

    def test_evaluate_task_accepts_weights(self, eval_project):
        from roam.db.connection import open_db
        from roam.eval.harness import EvalTask, evaluate_task

        task = EvalTask(
            task_id="t1",
            task="trace UserSession",
            expected_files=("src/auth.py",),
        )
        with open_db(readonly=True) as conn:
            result = evaluate_task(
                conn,
                task,
                weights={"alpha": 0.5, "beta": 0.2},
            )
        # Smoke test: function returns a TaskResult with recall@K filled in.
        assert "src/auth.py" in result.retrieved_files or result.recall_at[20] >= 0.0


# ---------------------------------------------------------------------------
# Bench-portable emit formats — `--emit-format coderag` / `beir`
# ---------------------------------------------------------------------------


class TestBenchEmit:
    """Public-leaderboard submission shapes: CodeRAG-Bench + BEIR/trec_eval."""

    def _write_tasks(self, tmp_path, tasks):
        path = tmp_path / "tasks.jsonl"
        path.write_text("\n".join(json.dumps(t) for t in tasks) + "\n", encoding="utf-8")
        return path

    def test_coderag_emit_one_record_per_task(self, eval_project, tmp_path):
        tasks_path = self._write_tasks(
            tmp_path,
            [
                {"task_id": "t1", "task": "UserSession refresh", "expected_files": ["src/auth.py"]},
                {"task_id": "t2", "task": "Invoice total", "expected_files": ["src/billing.py"]},
            ],
        )
        out_path = tmp_path / "runs" / "coderag.jsonl"
        runner = CliRunner()
        result = runner.invoke(
            cli,
            [
                "eval-retrieve",
                "--tasks",
                str(tasks_path),
                "--emit-format",
                "coderag",
                "--emit-out",
                str(out_path),
                "--emit-k",
                "5",
            ],
        )
        assert result.exit_code == 0, result.output
        assert out_path.exists(), result.output
        lines = out_path.read_text(encoding="utf-8").strip().splitlines()
        assert len(lines) == 2  # one per task
        records = [json.loads(line) for line in lines]
        for rec in records:
            assert set(rec) >= {"task_id", "query", "ctxs"}
            assert isinstance(rec["ctxs"], list)
            for ctx in rec["ctxs"]:
                assert set(ctx) >= {"id", "title", "text", "score"}
                # id format: <file>:<line_start>-<line_end>
                assert ":" in ctx["id"] and "-" in ctx["id"]
                assert isinstance(ctx["score"], float)

    def test_beir_emit_one_record_per_doc(self, eval_project, tmp_path):
        tasks_path = self._write_tasks(
            tmp_path,
            [
                {"task_id": "qA", "task": "UserSession refresh", "expected_files": ["src/auth.py"]},
            ],
        )
        out_path = tmp_path / "beir.jsonl"
        runner = CliRunner()
        result = runner.invoke(
            cli,
            [
                "eval-retrieve",
                "--tasks",
                str(tasks_path),
                "--emit-format",
                "beir",
                "--emit-out",
                str(out_path),
                "--emit-k",
                "3",
            ],
        )
        assert result.exit_code == 0, result.output
        lines = out_path.read_text(encoding="utf-8").strip().splitlines()
        # Up to 3 docs per query × 1 query = up to 3 lines.
        assert 0 < len(lines) <= 3, f"expected 1-3 BEIR records, got {len(lines)}"
        for ln, raw in enumerate(lines, 1):
            rec = json.loads(raw)
            assert set(rec) >= {"query_id", "doc_id", "rank", "score", "run_name"}
            assert rec["query_id"] == "qA"
            assert rec["rank"] == ln  # rank is 1-based, monotonically increasing

    def test_emit_format_requires_emit_out(self, eval_project, tmp_path):
        tasks_path = self._write_tasks(
            tmp_path,
            [{"task_id": "t1", "task": "x", "expected_files": ["src/auth.py"]}],
        )
        runner = CliRunner()
        result = runner.invoke(
            cli,
            ["eval-retrieve", "--tasks", str(tasks_path), "--emit-format", "coderag"],
        )
        # Missing --emit-out should produce a UsageError (Click exit code 2).
        assert result.exit_code != 0
        assert "emit-out" in result.output.lower()

    def test_default_format_is_roam(self, eval_project, tmp_path):
        """Without --emit-format, behaviour matches v12.0 — the harness
        runs and prints VERDICT plus per-task table."""
        tasks_path = self._write_tasks(
            tmp_path,
            [{"task_id": "t1", "task": "UserSession", "expected_files": ["src/auth.py"]}],
        )
        runner = CliRunner()
        result = runner.invoke(cli, ["eval-retrieve", "--tasks", str(tasks_path)])
        assert result.exit_code == 0, result.output
        assert "VERDICT:" in result.output
        # Should NOT have written any extra runs file.
        assert "runs" not in result.output.lower() or "Wrote" not in result.output
