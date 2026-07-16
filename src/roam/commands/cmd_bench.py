"""`roam bench compile` — A/B harness for compiler vs vanilla vs static prompt.

SARIF is deliberately NOT emitted: output is benchmark aggregate
statistics, not file-located findings.

Promotes the one-off `internal/benchmarks/cvc_harness.py` (used to
generate the 2026-05-30 production numbers) to a first-class CLI
command. Dispatches `claude -p --output-format json` calls per cell
and aggregates duration / cost / turns / output_tokens.

Use cases:
  * Validate that the static prompt swap actually helps before rolling out
  * Track compile-mode regression as the corpus grows
  * Sanity-check after editing the classifier or routing logic

Defaults are intentionally cheap: 1 task × 3 conditions × n=1. Scale up
explicitly with `--tasks-file` + `--runs`.
"""

from __future__ import annotations

import json
import os
import statistics
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import click

from roam.capability import roam_capability
from roam.output.formatter import json_envelope, to_json

# The same static prompt an agent host ships in its agent-prompt selector.
_STATIC_PROMPT = (
    "roam-code Python project. Be FAST and TERSE. "
    "Single-symbol lookup -> roam_search_symbol. "
    "Multi-symbol (3+) -> roam_batch_search ONE call. "
    "Callers -> roam_uses. Coupling/deps -> roam_coupling+roam_deps PARALLEL. "
    "Dead/unused -> roam_dead_code. Impact -> roam_impact. "
    "File role -> roam_file_info. Semantic/conceptual -> roam_search_semantic. "
    "Targeted line-read of a known file -> Read directly. "
    "Synthesis (write test/code/diff) -> SKIP roam, Read+Edit directly. "
    "Parallel-call independent tools in ONE tool_use block."
)


def _build_prompt(condition: str, task: str, compile_out: str) -> str:
    if condition == "vanilla":
        return f"TASK: {task}\n\nAnswer the task now."
    if condition == "static":
        return (
            f"ROUTING HINTS (use these to choose tools, then ANSWER):\n"
            f"{_STATIC_PROMPT}\n\n"
            f"TASK: {task}\n\n"
            f"Answer the task now."
        )
    if condition == "compile":
        return (
            f"PRE-COMPUTED PLAN (use to choose tools, then ANSWER):\n"
            f"{compile_out}\n\n"
            f"TASK: {task}\n\n"
            f"Answer the task now."
        )
    raise click.ClickException(f"unknown condition: {condition}")


def _compile_envelope(task: str, cwd: str) -> str:
    """Run `roam compile <task>` and return its text envelope."""
    from roam.plan.agent_mode import ENV_VAR, MODE_BENCH

    try:
        # stamp the child's telemetry row as bench (measurement integrity — these
        # rows must not land in the production L1-rate/latency KPIs). Passed via
        # an explicit env so it holds regardless of the parent's own mode.
        env = {**os.environ, ENV_VAR: MODE_BENCH}
        proc = subprocess.run(
            ["roam", "compile", task, "--artifact", "auto"],
            capture_output=True,
            text=True,
            timeout=10,
            cwd=cwd,
            env=env,
        )
        return proc.stdout
    except (OSError, subprocess.TimeoutExpired):
        return ""


def _run_claude_p(prompt: str, out_path: Path, timeout_sec: int, model: str | None = None) -> dict:
    """Invoke `claude -p` once. Write raw JSON to out_path. Return metadata.

    W64 — optional `model` arg appends `--model <model>` to the CLI
    invocation. Accepts an alias (`opus`, `sonnet`) or a full id
    (`claude-opus-4-8`).
    """
    started = time.time()
    cmd = ["claude", "-p", prompt, "--output-format", "json"]
    if model:
        cmd.extend(["--model", model])
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout_sec,
            stdin=subprocess.DEVNULL,
        )
    except subprocess.TimeoutExpired:
        out_path.write_text(json.dumps({"type": "error", "reason": "timeout"}))
        return {"error": "timeout", "elapsed": time.time() - started}
    if proc.returncode != 0 or not proc.stdout.strip():
        out_path.write_text(
            json.dumps(
                {
                    "type": "error",
                    "stderr": proc.stderr[:500],
                    "returncode": proc.returncode,
                }
            )
        )
        return {"error": "no_output", "elapsed": time.time() - started}
    out_path.write_text(proc.stdout)
    return {"ok": True, "elapsed": time.time() - started}


# ---- W65 — vanilla-result reuse cache ----
#
# Vanilla is the baseline. Its result for a given (task, model) tuple is
# INDEPENDENT of our compile changes. Re-running it on every A/B wastes
# money. Cache vanilla cells per (sha256(task), model) under
# ~/.cache/roam-bench-vanilla/ so subsequent --reuse-vanilla bench runs
# skip the claude -p call and just copy the prior cell file.

_VANILLA_CACHE_DIR = Path.home() / ".cache" / "roam-bench-vanilla"


def _vanilla_cache_key(task: str, model: str | None) -> str:
    import hashlib

    h = hashlib.sha256()
    h.update(task.encode("utf-8", "replace"))
    h.update(b"\x00")
    h.update((model or "default").encode("utf-8"))
    return h.hexdigest()[:32]


def _vanilla_cache_lookup(task: str, model: str | None) -> str | None:
    """Return path to cached vanilla cell JSON, or None on miss."""
    path = _VANILLA_CACHE_DIR / f"{_vanilla_cache_key(task, model)}.json"
    return str(path) if path.exists() else None


def _vanilla_cache_store(task: str, model: str | None, source_path: Path) -> None:
    """Copy source_path into the vanilla cache. Best-effort."""
    try:
        _VANILLA_CACHE_DIR.mkdir(parents=True, exist_ok=True)
        dest = _VANILLA_CACHE_DIR / f"{_vanilla_cache_key(task, model)}.json"
        import shutil

        shutil.copyfile(source_path, dest)
    except (OSError, ValueError):
        pass  # best-effort cache


def _parse_cell(path: Path) -> dict | None:
    text = path.read_text()
    j = text[text.find("{") :]
    try:
        d = json.loads(j)
    except json.JSONDecodeError:
        return None
    if d.get("type") == "error":
        return None
    u = d.get("usage", {})
    return {
        "num_turns": d.get("num_turns"),
        "duration_ms": d.get("duration_ms"),
        "cost_usd": d.get("total_cost_usd"),
        "input_tokens": u.get("input_tokens", 0)
        + u.get("cache_read_input_tokens", 0)
        + u.get("cache_creation_input_tokens", 0),
        "output_tokens": u.get("output_tokens", 0),
        "result_len": len(d.get("result", "")),
        # W62 fix — include the result text so --judge can score it.
        "result": d.get("result", ""),
    }


def _agg(values: list[float]) -> dict | None:
    if not values:
        return None
    return {
        "mean": round(statistics.mean(values), 2),
        "median": round(statistics.median(values), 2),
        "min": round(min(values), 2),
        "max": round(max(values), 2),
        "n": len(values),
    }


# ---- Ground-truth oracle integration ----
#
# When `--ground-truth` is set, each cell's result text is inspected for a
# shape-appropriate artifact (a pytest test source for `write_pytest` tasks,
# or a unified diff for `stack_trace_fix` / `fix_bug` tasks). If found, the
# artifact is routed through the corresponding oracle in
# `internal/benchmarks/`. Score semantics:
#   * write_pytest:    1 if oracle exit_code == 0 else 0
#   * fix_bug/stack:   transitioned_to_passing (int count, may be > 1)
#   * any other shape: "" (empty string for TSV join-ability)


def _classify_task_shape(task: str) -> str:
    """Heuristic shape classifier for ground-truth dispatch.

    Mirrors the compiler's task classification at a much coarser
    granularity — we only need to distinguish three buckets here.
    """
    t = (task or "").lower()
    # Bug-fix shapes: stack traces, "fix the bug", "patch", "failing test".
    if any(
        k in t
        for k in (
            "stack trace",
            "traceback",
            "fix the bug",
            "fix bug",
            "failing test",
            "patch the",
            "make the test pass",
            "make tests pass",
            "stack_trace",
        )
    ):
        return "stack_trace_fix"
    # Pytest production.
    if any(
        k in t
        for k in (
            "write a pytest",
            "write a test",
            "write pytest",
            "produce a test",
            "produce a pytest",
            "add a test",
            "write tests for",
        )
    ):
        return "write_pytest"
    return "other"


def _extract_pytest_source(text: str) -> str | None:
    """Pull a pytest-recognizable source block from result text.

    Prefers a fenced ```python block; falls back to any fenced block whose
    body looks like a pytest test. Returns None when nothing pytest-like
    is found.
    """
    if not text:
        return None
    import re as _re

    fence_re = _re.compile(r"```(?P<lang>[\w+-]*)\n(?P<body>.*?)```", _re.DOTALL)
    candidates: list[str] = []
    for m in fence_re.finditer(text):
        lang = (m.group("lang") or "").lower()
        body = m.group("body") or ""
        if lang in ("python", "py", "pytest", ""):
            candidates.append(body)
    pytest_markers = ("def test_", "import pytest", "from pytest", "@pytest")
    for body in candidates:
        if any(mk in body for mk in pytest_markers):
            return body
    # No fenced block matched; scan raw text for an inline test_ function.
    if "def test_" in text and ("assert " in text or "pytest" in text):
        # Return from the first `def test_` to end of text — caller sandboxes.
        idx = text.find("def test_")
        # Try to also pull a preceding import line if present.
        head_start = max(0, text.rfind("\nimport ", 0, idx))
        return text[head_start:].lstrip("\n") if head_start else text[idx:]
    return None


def _extract_patch(text: str) -> str | None:
    """Pull a unified-diff patch from result text.

    Looks for fenced ```diff / ```patch blocks first; falls back to a raw
    `--- ` / `+++ ` git-diff section. Returns None when nothing diff-like
    is found.
    """
    if not text:
        return None
    import re as _re

    fence_re = _re.compile(r"```(?P<lang>[\w+-]*)\n(?P<body>.*?)```", _re.DOTALL)
    for m in fence_re.finditer(text):
        lang = (m.group("lang") or "").lower()
        body = m.group("body") or ""
        if lang in ("diff", "patch"):
            return body
        if body.lstrip().startswith(("--- ", "diff --git ")):
            return body
    # Raw scan — find the first `diff --git` or `--- a/` marker.
    for marker in ("diff --git ", "\n--- a/", "\n--- "):
        idx = text.find(marker)
        if idx == -1 and marker.startswith("\n"):
            # Allow start-of-string match.
            if text.startswith(marker.lstrip("\n")):
                idx = 0
        if idx != -1:
            return text[idx:].lstrip("\n")
    return None


def _ensure_internal_importable() -> bool:
    """Put the roam dev-repo root on sys.path so `internal.benchmarks.*`
    resolves regardless of the agent's CWD.

    The oracle modules live under the repo's gitignored `internal/` (NOT shipped
    with the installed package). When bench-compile grades an EXTERNAL repo (e.g.
    a SWE-bench instance), the agent runs in THAT repo's dir, so a bare
    `import internal...` ImportErrors and the score silently empties. Deriving
    the repo root from `roam.__file__` (src/roam/__init__.py -> parents[2]) makes
    the import work from any cwd.
    """
    try:
        import pathlib as _pl
        import sys as _sys

        import roam

        repo_root = _pl.Path(roam.__file__).resolve().parents[2]
        if (repo_root / "internal" / "benchmarks").is_dir():
            if str(repo_root) not in _sys.path:
                _sys.path.insert(0, str(repo_root))
            return True
    except Exception:  # noqa: BLE001
        pass
    return False


def _ground_truth_score(task: str, result_text: str, project_root: str) -> str:
    """Dispatch to the appropriate oracle for the task's classified shape.

    Returns a string (for TSV join-ability) — either an integer score
    rendered as text, or "" when the shape is unsupported or extraction
    failed.
    """
    shape = _classify_task_shape(task)
    if shape == "write_pytest":
        source = _extract_pytest_source(result_text or "")
        if not source:
            return ""
        _ensure_internal_importable()
        try:
            from internal.benchmarks.oracle_pytest import run_produced_test
        except ImportError as exc:
            click.echo(
                f"[ground-truth] oracle_pytest import failed (cwd-coupled internal/ not on path): {exc!r}", err=True
            )
            return "NOORACLE"
        try:
            import pathlib as _pl

            r = run_produced_test(source, _pl.Path(project_root))
            return "1" if int(r.get("exit_code", -1)) == 0 else "0"
        except Exception:  # noqa: BLE001
            return ""
    if shape == "stack_trace_fix":
        patch = _extract_patch(result_text or "")
        if not patch:
            return ""
        # Need a test selector to drive the bug-fix oracle. Try to pull
        # one from the task text; fall back to "" which short-circuits the
        # oracle (returns failing_before=0).
        selector = _extract_test_selector(task)
        _ensure_internal_importable()
        try:
            from internal.benchmarks.oracle_fix_bug import apply_and_measure
        except ImportError as exc:
            click.echo(
                f"[ground-truth] oracle_fix_bug import failed (cwd-coupled internal/ not on path): {exc!r}", err=True
            )
            return "NOORACLE"
        try:
            import pathlib as _pl

            r = apply_and_measure(
                _pl.Path(project_root),
                patch,
                selector,
            )
        except Exception as exc:  # noqa: BLE001
            # Pattern-2 discipline: an oracle that RAISED is not a 0 score and
            # not "unsupported" — surface it so a broken grading run can't pose
            # as a clean one (this exact swallow silently emptied a Tier-0 run).
            click.echo(
                f"[ground-truth] oracle raised for selector {selector!r} at root {project_root}: {exc!r}", err=True
            )
            return "ERR"
        if not r.get("patch_applied", False):
            click.echo(
                f"[ground-truth] patch did NOT apply for selector {selector!r} "
                f"at root {project_root} (scored 0, not a real fix attempt)",
                err=True,
            )
        return str(int(r.get("transitioned_to_passing", 0)))
    return ""


def _extract_test_selector(task: str) -> str:
    """Best-effort pull of a pytest selector (path::id) from task text."""
    if not task:
        return ""
    import re as _re

    # path::id form
    m = _re.search(r"([\w./-]+\.py(?:::[\w:-]+)?)", task)
    if m:
        return m.group(1)
    return ""


def _structure_judge_score(result_text: str, task: str) -> dict:
    """W62 — heuristic offline rubric (0..1 score). NOT a quality judge.
    Measures whether the result has the STRUCTURAL SHAPE of a useful
    answer: file:line citations, code blocks, no apology language,
    non-trivial length, mentions key nouns from the task.
    """
    if not result_text:
        return {"score": 0.0, "signals": ["empty_result"]}
    txt = result_text
    signals: list[str] = []
    score = 0.0
    import re as _re

    if _re.search(r"\b[\w./-]+\.(py|js|ts|tsx|jsx|go|rs|java|rb|php):\d+\b", txt):
        score += 0.25
        signals.append("file_line_citation")
    if "```" in txt or txt.count("    ") >= 4:
        score += 0.20
        signals.append("code_block")
    apology = ("i cannot", "i don't have", "unable to", "i'm not sure", "i can't find")
    if not any(a in txt.lower() for a in apology):
        score += 0.15
        signals.append("no_apology")
    task_nouns = [
        w
        for w in _re.findall(r"\b\w{4,}\b", task)
        if w.lower()
        not in (
            "what",
            "where",
            "which",
            "files",
            "from",
            "this",
            "that",
            "with",
            "have",
            "does",
            "fix",
            "compare",
            "trace",
            "find",
        )
    ]
    if task_nouns:
        hits = sum(1 for n in task_nouns[:5] if n.lower() in txt.lower())
        if hits >= 1:
            score += 0.20 * min(1.0, hits / 3)
            signals.append(f"task_noun_overlap:{hits}")
    if 80 <= len(txt) <= 8000:
        score += 0.20
        signals.append("non_trivial_length")
    return {"score": round(min(1.0, score), 2), "signals": signals, "char_count": len(txt)}


@click.command(name="bench-compile")
@click.argument("task", type=str, required=False)
@click.option(
    "--tasks-file",
    "tasks_file",
    type=click.Path(exists=True, dir_okay=False),
    default=None,
    help="One task per line. Overrides positional TASK.",
)
@click.option("--conditions", default="vanilla,static,compile", help="Comma-separated conditions to compare.")
@click.option(
    "--runs", "n_runs", type=int, default=1, show_default=True, help="Repetitions per (task, condition) cell."
)
@click.option(
    "--workers",
    type=int,
    default=3,
    show_default=True,
    help="Parallel claude -p calls (respect your rate-limit headroom).",
)
@click.option("--timeout", "timeout_sec", type=int, default=180, show_default=True, help="Per-cell wall-time cap.")
@click.option(
    "--model",
    "model",
    type=str,
    default=None,
    help="W64 — claude model to dispatch (alias `opus`/`sonnet` or "
    "full id like `claude-opus-4-8`). Default: claude -p's default.",
)
@click.option(
    "--reuse-vanilla",
    "reuse_vanilla",
    is_flag=True,
    default=False,
    help="W65 — skip vanilla cells when a cached result exists for the "
    "(task, model) tuple. Use after a clean baseline run to avoid "
    "re-spending $ on a baseline that hasn't changed.",
)
@click.option(
    "--judge",
    "judge_enabled",
    is_flag=True,
    default=False,
    help="W62 — score each result with an OFFLINE structural rubric "
    "(file:line citations, code blocks, named-path mentions, "
    "non-empty body, no `I cannot` apology). Not a model judge; "
    "fast + free + reproducible. Aggregate score per condition.",
)
@click.option(
    "--ground-truth",
    "ground_truth",
    is_flag=True,
    default=False,
    help="Route each cell's output through a ground-truth oracle "
    "(`internal/benchmarks/oracle_pytest.py` for write_pytest "
    "shapes; `oracle_fix_bug.py` for stack_trace_fix/fix_bug). "
    "Records `ground_truth_score` per cell. Default OFF for "
    "back-compat with the nightly cron.",
)
@click.option("--out-dir", "out_dir", default=None, help="Where to write per-cell raw JSON (default: temp dir).")
@click.pass_context
@roam_capability(
    name="bench-compile",
    category="planning",
    summary="A/B harness for compiler vs vanilla vs static prompt via claude -p",
    inputs=("tasks",),
    outputs=("per_condition_stats", "per_task_stats"),
    examples=(
        'roam bench compile "Which files are coupled to src/roam/cli.py?"',
        "roam bench compile --tasks-file my-bench-tasks.txt --runs 3",
    ),
    tags=("planning", "benchmark", "compiler", "ab"),
)
def bench_compile(
    ctx: click.Context,
    task: str | None,
    tasks_file: str | None,
    conditions: str,
    n_runs: int,
    workers: int,
    timeout_sec: int,
    model: str | None,
    reuse_vanilla: bool,
    judge_enabled: bool,
    ground_truth: bool,
    out_dir: str | None,
) -> None:
    """Run a controlled A/B between vanilla / static / compile prompt modes."""
    json_mode = ctx.obj.get("json") if ctx.obj else False

    # Resolve task list.
    if tasks_file:
        tasks = [
            line.strip()
            for line in Path(tasks_file).read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.startswith("#")
        ]
    elif task:
        tasks = [task]
    else:
        msg = "Pass TASK or --tasks-file"
        if json_mode:
            click.echo(
                to_json(
                    json_envelope(
                        "bench-compile",
                        summary={
                            "verdict": "missing_input",
                            "partial_success": True,
                            "error": msg,
                        },
                    )
                )
            )
        else:
            click.echo(msg, err=True)
        ctx.exit(2)
        return

    cond_list = [c.strip() for c in conditions.split(",") if c.strip()]
    work_dir = Path(out_dir) if out_dir else Path(f"/tmp/cvc_{os.getpid()}")
    work_dir.mkdir(parents=True, exist_ok=True)

    cwd = str(Path.cwd())
    cells = []
    compile_outs: dict[str, str] = {}
    for ti, t in enumerate(tasks):
        if "compile" in cond_list:
            compile_outs[t] = _compile_envelope(t, cwd)
        for cond in cond_list:
            for run in range(1, n_runs + 1):
                tid = f"t{ti}"
                prompt = _build_prompt(cond, t, compile_outs.get(t, ""))
                out_path = work_dir / f"{tid}_{cond}_{run}.json"
                cells.append(
                    {
                        "task_id": tid,
                        "task": t,
                        "cond": cond,
                        "run": run,
                        "prompt": prompt,
                        "out_path": out_path,
                    }
                )

    # W65 — short-circuit vanilla cells when --reuse-vanilla AND cache hit.
    # Live cells = the ones we'll actually pay for via claude -p.
    live_cells: list[dict] = []
    reused_count = 0
    if reuse_vanilla:
        import shutil

        for c in cells:
            if c["cond"] == "vanilla":
                cached = _vanilla_cache_lookup(c["task"], model)
                if cached:
                    shutil.copyfile(cached, c["out_path"])
                    reused_count += 1
                    continue
            live_cells.append(c)
    else:
        live_cells = list(cells)

    if not json_mode:
        click.echo(
            f"VERDICT: dispatching {len(live_cells)} cells "
            f"({len(tasks)} tasks × {len(cond_list)} conditions × n={n_runs}) "
            f"with {workers}-way parallelism"
            + (f"; reused {reused_count} cached vanilla cells" if reused_count else "")
            + (f"; model={model}" if model else "")
        )
    started = time.time()
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(_run_claude_p, c["prompt"], c["out_path"], timeout_sec, model): c for c in live_cells}
        for f in as_completed(futures):
            cell = futures[f]
            try:
                r = f.result()
                # W65 — write fresh vanilla results to the cache for next time.
                if cell["cond"] == "vanilla" and r.get("ok"):
                    _vanilla_cache_store(cell["task"], model, cell["out_path"])
            except Exception as e:  # noqa: BLE001
                r = {"error": str(e)}
            if not json_mode:
                marker = "OK" if r.get("ok") else f"FAIL ({r.get('error', '?')})"
                click.echo(f"  {cell['task_id']}_{cell['cond']}_{cell['run']}: {marker} {r.get('elapsed', 0):.1f}s")
    total_elapsed = time.time() - started

    # Aggregate
    by_cond: dict[str, list[dict]] = {c: [] for c in cond_list}
    tsv_rows: list[dict] = []  # ordered per-cell rows for TSV emission
    for c in cells:
        parsed = _parse_cell(c["out_path"])
        gt_score: str = ""
        if parsed and ground_truth:
            gt_score = _ground_truth_score(
                c["task"],
                parsed.get("result") or "",
                cwd,
            )
        if parsed:
            row = parsed | {"task_id": c["task_id"]}
            # W62 — score the result text (structural rubric).
            if judge_enabled:
                row["judge"] = _structure_judge_score(parsed.get("result") or "", c["task"])
            if ground_truth:
                row["ground_truth_score"] = gt_score
            by_cond[c["cond"]].append(row)
        tsv_rows.append(
            {
                "task_id": c["task_id"],
                "cond": c["cond"],
                "run": c["run"],
                "ground_truth_score": gt_score,
            }
        )

    # Emit per-cell TSV with `ground_truth_score` APPENDED at end (back-compat
    # readers ignore extra cols). Written next to the raw JSON cells.
    tsv_path = work_dir / "cells.tsv"
    try:
        lines = ["task_id\tcond\trun\tground_truth_score"]
        for r in tsv_rows:
            lines.append(f"{r['task_id']}\t{r['cond']}\t{r['run']}\t{r['ground_truth_score']}")
        tsv_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    except OSError:
        pass  # best-effort

    per_condition = {}
    for cond, rows in by_cond.items():
        per_condition[cond] = {
            "n": len(rows),
            "turns": _agg([r["num_turns"] for r in rows if r["num_turns"] is not None]),
            "duration_ms": _agg([r["duration_ms"] for r in rows if r["duration_ms"] is not None]),
            "cost_usd": _agg([r["cost_usd"] for r in rows if r["cost_usd"] is not None]),
            "output_tokens": _agg([r["output_tokens"] for r in rows]),
            "input_tokens": _agg([r["input_tokens"] for r in rows]),
        }
        # W62 — judge score aggregate
        if judge_enabled:
            judge_scores = [r["judge"]["score"] for r in rows if r.get("judge")]
            per_condition[cond]["judge_score"] = _agg(judge_scores)

    summary = {
        "verdict": (f"{len(cells)} cells, {sum(len(r) for r in by_cond.values())} parsed, {total_elapsed:.0f}s wall"),
        "cells": len(cells),
        "parsed_cells": sum(len(r) for r in by_cond.values()),
        "elapsed_seconds": round(total_elapsed, 2),
        "out_dir": str(work_dir),
        "partial_success": sum(len(r) for r in by_cond.values()) < len(cells),
    }
    facts = [
        f"{len(cells)} cells dispatched",
        f"{summary['parsed_cells']} cells parsed",
        f"{round(total_elapsed, 1)} seconds wall",
        f"raw JSON in {work_dir}",
    ]

    if json_mode:
        click.echo(
            to_json(
                json_envelope(
                    "bench-compile",
                    summary=summary,
                    agent_contract={
                        "facts": facts,
                        "next_commands": [],
                        "risks": [],
                    },
                    per_condition=per_condition,
                    tasks=tasks,
                    conditions=cond_list,
                )
            )
        )
        return

    click.echo("")
    click.echo(summary["verdict"])
    click.echo("")
    header = f"{'cond':<10} {'n':<4} {'turns':<8} {'wall_ms':<10} {'tok_out':<10} {'cost_usd':<10}"
    if judge_enabled:
        header += f"  {'judge':<8}"
    click.echo(header)
    click.echo("-" * (60 + (10 if judge_enabled else 0)))
    for cond in cond_list:
        s = per_condition[cond]
        if s["n"] == 0:
            click.echo(f"{cond:<10} 0 (all cells failed to parse)")
            continue
        line = (
            f"{cond:<10} {s['n']:<4} "
            f"{s['turns']['mean']:<8} "
            f"{s['duration_ms']['mean']:<10.0f} "
            f"{s['output_tokens']['mean']:<10.0f} "
            f"${s['cost_usd']['mean']:.4f}"
        )
        if judge_enabled and s.get("judge_score") and s["judge_score"].get("mean") is not None:
            line += f"  {s['judge_score']['mean']:.2f}"
        click.echo(line)
    click.echo("")
    click.echo(f"raw JSON: {work_dir}")
