"""Tests for ``bench/retrieve/check_published_recall.py``.

That script is a **CI-blocking gate** on a public credibility claim: the
recall numbers `bench/retrieve/{README,SUBMISSION}.md` publish, next to an
invitation to "run this command". It decides whether the build goes red.

Until 2026-08-12 it had no tests at all::

    $ grep -rl check_published_recall tests/ dev/ scripts/
    (no output)

That is worth stating plainly, because the gate's own reason for existing is
that a published number nothing re-derives will drift. The same argument
applies to the checker: an unexercised gate is a claim nothing re-derives.

What is pinned here is the decision boundary in both directions, the
near-miss band, and — most importantly — every path where the check *cannot*
run. Those must refuse (exit 2), never quietly pass. An absent measurement is
not a green build.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

from tests._helpers.repo_root import repo_root

# W572/W588: ask git for the toplevel rather than walking up from __file__. A
# nested worktree has tests/ but not the project markers, so parents[1] lands
# somewhere that exists and is wrong -- and for a gate test that means loading
# a DIFFERENT check_published_recall.py, or none, and passing either way.
REPO_ROOT = repo_root()
GATE_PATH = REPO_ROOT / "bench" / "retrieve" / "check_published_recall.py"


def _load_gate():
    """Import the gate by path — it lives in ``bench/``, not an importable package."""
    spec = importlib.util.spec_from_file_location("_check_published_recall", GATE_PATH)
    assert spec and spec.loader, f"cannot load {GATE_PATH}"
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


gate = _load_gate()


def _write_doc(path: Path, recalls: dict[int, float]) -> None:
    body = "\n".join(f"recall@{k}  = {v:.3f}" for k, v in sorted(recalls.items()))
    path.write_text(
        "# doc\n\n"
        "<!-- canonical-recall:begin -- parsed by check_published_recall.py -->\n"
        f"```\n{body}\n```\n"
        "<!-- canonical-recall:end -->\n",
        encoding="utf-8",
    )


def _write_eval(path: Path, recalls: dict[int, float], *, tasks: int = 30, partial: bool = False) -> None:
    summary: dict[str, object] = {"task_count": tasks, "partial_success": partial}
    for k, v in recalls.items():
        summary[f"recall_at_{k}"] = v
    path.write_text(
        json.dumps({"command": "eval-retrieve", "summary": summary}),
        encoding="utf-8",
    )


@pytest.fixture
def bench(tmp_path: Path) -> Path:
    """A bench dir carrying both publishing docs at the same numbers."""
    published = {5: 0.642, 10: 0.772, 20: 0.914}
    for name in gate.PUBLISHING_DOCS:
        _write_doc(tmp_path / name, published)
    return tmp_path


def _run(bench_dir: Path, eval_path: Path, *extra: str) -> int:
    return gate.main(["--eval-json", str(eval_path), "--bench-dir", str(bench_dir), *extra])


# --------------------------------------------------------------------------
# the decision boundary
# --------------------------------------------------------------------------


def test_matching_numbers_pass(bench: Path, tmp_path: Path) -> None:
    ev = tmp_path / "eval.json"
    _write_eval(ev, {5: 0.6611, 10: 0.7722, 20: 0.9139})
    assert _run(bench, ev) == 0


def test_regression_beyond_tolerance_fails(bench: Path, tmp_path: Path, capsys) -> None:
    """The exact failure that went red on 74520ca5: 0.914 published, 0.850 measured."""
    ev = tmp_path / "eval.json"
    _write_eval(ev, {5: 0.647, 10: 0.769, 20: 0.850})
    assert _run(bench, ev) == 1
    out = capsys.readouterr().out
    assert "REGRESSED" in out
    assert "recall@20" in out


def test_improvement_beyond_tolerance_also_fails(bench: Path, tmp_path: Path, capsys) -> None:
    """Both directions. A retriever that improved while the docs stood still
    is the drift this repo actually shipped — a one-sided floor is blind to it."""
    ev = tmp_path / "eval.json"
    _write_eval(ev, {5: 0.642, 10: 0.772, 20: 0.990})
    assert _run(bench, ev) == 1
    assert "IMPROVED (docs are stale)" in capsys.readouterr().out


def test_drift_exactly_at_tolerance_passes(bench: Path, tmp_path: Path) -> None:
    """``abs(drift) > tolerance`` fails, so the boundary itself is a pass.
    Pinned so the comparison cannot silently flip to ``>=``."""
    ev = tmp_path / "eval.json"
    _write_eval(ev, {5: 0.642, 10: 0.772, 20: 0.914 - gate.DEFAULT_TOLERANCE})
    assert _run(bench, ev) == 0


def test_docs_disagreeing_with_each_other_fails(tmp_path: Path, capsys) -> None:
    """If the two docs publish different numbers, "which is real" is
    undecidable no matter what the harness measured."""
    _write_doc(tmp_path / gate.PUBLISHING_DOCS[0], {20: 0.914})
    _write_doc(tmp_path / gate.PUBLISHING_DOCS[1], {20: 0.880})
    ev = tmp_path / "eval.json"
    _write_eval(ev, {20: 0.914})
    assert _run(tmp_path, ev) == 1
    assert "disagree with each other" in capsys.readouterr().out


# --------------------------------------------------------------------------
# the near-miss band
# --------------------------------------------------------------------------


def test_near_miss_warns_but_still_passes(bench: Path, tmp_path: Path, capsys) -> None:
    """The five days of silence that preceded the red build.

    recall@20 sat at 0.867 measured against 0.914 published — a -0.047 drift,
    78% of the +/-0.06 budget — across at least five green main commits, and
    the gate printed the same "OK" it prints at zero drift.
    """
    ev = tmp_path / "eval.json"
    _write_eval(ev, {5: 0.647, 10: 0.769, 20: 0.867})
    assert _run(bench, ev) == 0, "a near miss must not change the exit code"
    out = capsys.readouterr().out
    assert "NEAR" in out
    assert "78% of it" in out
    assert "::warning::" in out


def test_small_drift_does_not_warn(bench: Path, tmp_path: Path, capsys) -> None:
    """Negative control: the warning must not fire on a healthy build, or it
    is noise and will be ignored the one time it matters."""
    ev = tmp_path / "eval.json"
    _write_eval(ev, {5: 0.6611, 10: 0.7722, 20: 0.9139})
    assert _run(bench, ev) == 0
    out = capsys.readouterr().out
    assert "NEAR" not in out
    assert "::warning::" not in out


def test_near_miss_and_failure_are_disjoint(bench: Path) -> None:
    """A K past tolerance is a FAIL and must not also be reported as NEAR."""
    published = {20: 0.914}
    measured = {20: 0.914 - 0.10}
    assert gate.compare(measured, published, "d", gate.DEFAULT_TOLERANCE)
    assert not gate.near_misses(measured, published, "d", gate.DEFAULT_TOLERANCE)


# --------------------------------------------------------------------------
# refusal: every path where the check cannot run must exit 2, never 0
# --------------------------------------------------------------------------


def test_missing_eval_json_refuses(bench: Path, tmp_path: Path) -> None:
    assert _run(bench, tmp_path / "absent.json") == 2


def test_partial_success_refuses(bench: Path, tmp_path: Path) -> None:
    """A degraded harness run cannot confirm OR refute the published numbers."""
    ev = tmp_path / "eval.json"
    _write_eval(ev, {5: 0.642, 10: 0.772, 20: 0.914}, partial=True)
    assert _run(bench, ev) == 2


def test_zero_task_count_refuses(bench: Path, tmp_path: Path) -> None:
    """An empty run agrees with anything. That is not a pass."""
    ev = tmp_path / "eval.json"
    _write_eval(ev, {5: 0.642, 10: 0.772, 20: 0.914}, tasks=0)
    assert _run(bench, ev) == 2


def test_doc_without_canonical_block_refuses(tmp_path: Path) -> None:
    """A doc that lost its block stopped publishing numbers — that needs a
    human, so it is an error rather than a skip."""
    (tmp_path / gate.PUBLISHING_DOCS[0]).write_text("# no block here\n", encoding="utf-8")
    _write_doc(tmp_path / gate.PUBLISHING_DOCS[1], {20: 0.914})
    ev = tmp_path / "eval.json"
    _write_eval(ev, {20: 0.914})
    assert _run(tmp_path, ev) == 2


def test_wrong_envelope_command_refuses(bench: Path, tmp_path: Path) -> None:
    ev = tmp_path / "eval.json"
    ev.write_text(json.dumps({"command": "audit", "summary": {}}), encoding="utf-8")
    assert _run(bench, ev) == 2


def test_negative_tolerance_refuses(bench: Path, tmp_path: Path) -> None:
    ev = tmp_path / "eval.json"
    _write_eval(ev, {20: 0.914})
    assert _run(bench, ev, "--tolerance", "-0.1") == 2


# --------------------------------------------------------------------------
# the workflow must measure the environment the docs publish
# --------------------------------------------------------------------------


def test_dogfood_checks_out_full_history() -> None:
    """``fetch-depth`` is part of this gate's correctness, not just plumbing.

    ``git_cochange`` is a ranking signal built from whatever history the
    checkout has. Measured 2026-08-12 at 74520ca5, same binary, same corpus,
    byte-identical index (5001 files / 46783 symbols / 97304 edges):

        full history (2575 commits)  recall@20 = 0.9139
        git_cochange emptied         recall@20 = 0.8778
        fetch-depth: 50              recall@20 = 0.8500

    A shallow clone scores *below* an empty table — a partial, recency-biased
    signal mis-ranks where an absent one falls back cleanly. At depth 50 the
    published-vs-measured gap was 0.064, i.e. the entire tolerance, so the
    gate could not have detected a real regression of any size.

    Asserted against the parsed YAML, not the file text: the first draft of
    this test grepped for the string and failed on the *comment* explaining
    the change. A substring check here would also pass on a workflow that
    merely mentions the right depth in prose while configuring another.
    """
    yaml = pytest.importorskip("yaml", reason="pyyaml is a dev-extra")
    workflow = yaml.safe_load((REPO_ROOT / ".github" / "workflows" / "dogfood.yml").read_text(encoding="utf-8"))

    guarded = [
        job
        for job in workflow["jobs"].values()
        if any("check_published_recall.py" in str(step.get("run", "")) for step in job.get("steps", []))
    ]
    assert guarded, (
        "this test guards the checkout depth ON BEHALF of the recall gate; no "
        "job in dogfood.yml runs check_published_recall.py any more, so move "
        "this assertion to wherever the gate went"
    )

    for job in guarded:
        checkouts = [step for step in job["steps"] if str(step.get("uses", "")).startswith("actions/checkout@")]
        assert checkouts, "the job running the recall gate has no checkout step"
        for step in checkouts:
            depth = step.get("with", {}).get("fetch-depth")
            assert depth == 0, (
                f"fetch-depth is {depth!r}; the job running the recall gate must "
                "clone FULL history (fetch-depth: 0). The gate compares against "
                "numbers published from a normal (full) clone, and a shallow one "
                "biases recall@20 by ~0.064 -- the entire tolerance budget"
            )
