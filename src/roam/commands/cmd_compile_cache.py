"""`roam compile-cache build|stats|clear|evict|vanilla-stats` —
manage the W56 persistent envelope cache that backs `roam compile`.

SARIF is deliberately NOT emitted: output is cache-management
operations (row counts / evictions / build summaries), not file-located
code findings — there's nothing for SARIF locations[] to point at.

The persistent cache (`.roam/compile-envelope-cache.sqlite`) holds
pre-computed envelopes keyed by `sha256(task + repo_head + cwd)`. Hot
tasks resolve in ~5ms instead of the cold ~500ms compile pipeline.

Subcommands:
  build         — pre-compile a corpus of likely user-shape tasks
                  (warms the cache for future workloads).
  stats         — show cache row count, size, age distribution.
  clear         — drop rows mismatching the current HEAD or all rows.
  evict         — W78 git-aware eviction by diff against a ref.
  vanilla-stats — W68 stats on the bench vanilla-result cache.
"""

from __future__ import annotations

import json
import os
import sqlite3
import stat
import time
from pathlib import Path

import click

from roam.capability import roam_capability
from roam.output.formatter import json_envelope, to_json

_CACHE_FILENAME = "compile-envelope-cache.sqlite"
_TELEMETRY_TASK_PREFIX_LIMIT = 80
_MAX_BUILD_TASKS = 10_000
_MAX_CORPUS_BYTES = 2 * 1024 * 1024
_MAX_CORPUS_LINE_BYTES = 16 * 1024
_MAX_CORPUS_TASKS = 10_000
_MAX_GIT_OUTPUT_BYTES = 2 * 1024 * 1024
_MAX_ALL_FILE_PATHS = 3_000
_GIT_ENUMERATION_TIMEOUT_SECONDS = 10.0


def _cache_path(root: str) -> Path:
    return Path(root) / ".roam" / _CACHE_FILENAME


def _open_db(root: str) -> sqlite3.Connection | None:
    p = _cache_path(root)
    if not p.parent.is_dir():
        return None
    conn = sqlite3.connect(str(p), timeout=1.0)
    conn.execute(
        "CREATE TABLE IF NOT EXISTS env_cache "
        "(key TEXT PRIMARY KEY, repo_head TEXT, art_label TEXT, "
        "envelope_json TEXT, ts REAL)"
    )
    return conn


@click.group(name="compile-cache")
@roam_capability(
    name="compile-cache",
    category="planning",
    summary="Manage the persistent envelope cache that backs `roam compile`.",
    inputs=("subcommand",),
    outputs=("summary_envelope",),
    examples=(
        "roam compile-cache stats",
        "roam compile-cache build",
        "roam compile-cache clear --stale",
    ),
    tags=("planning", "compiler", "cache"),
    side_effect=True,
)
def compile_cache() -> None:
    """Manage the persistent envelope cache."""


def _vanilla_missing_summary() -> dict:
    return {
        "verdict": "no vanilla cache yet — run `roam bench-compile` to populate",
        "row_count": 0,
        "partial_success": True,
    }


def _vanilla_present_summary(cache_dir: Path, cells: list[Path]) -> dict:
    total_bytes = sum(c.stat().st_size for c in cells)
    return {
        "verdict": f"{len(cells)} vanilla cells cached ({total_bytes / 1024:.1f} KB)",
        "row_count": len(cells),
        "size_bytes": total_bytes,
        "cache_dir": str(cache_dir),
        "partial_success": False,
    }


def _emit_vanilla_missing(summary: dict, json_mode: bool, cache_dir: Path) -> None:
    if json_mode:
        click.echo(
            to_json(
                json_envelope(
                    "compile-cache-vanilla-stats",
                    summary=summary,
                    agent_contract={
                        "facts": ["0 vanilla records", "vanilla cache checked"],
                        "next_commands": ["roam bench-compile"],
                        "risks": [],
                        "confidence": None,
                    },
                )
            )
        )
        return
    click.echo("VERDICT: no vanilla cache yet")
    click.echo(f"  (populated by `roam bench-compile` runs; would live at {cache_dir})")


def _emit_vanilla_present(summary: dict, json_mode: bool, cache_dir: Path, cells: list[Path]) -> None:
    if json_mode:
        click.echo(to_json(json_envelope("compile-cache-vanilla-stats", summary=summary)))
        return
    click.echo(f"VERDICT: {summary['verdict']}")
    click.echo(f"cache_dir:  {cache_dir}")
    click.echo(f"row_count:  {len(cells)}")
    click.echo(f"size:       {int(summary['size_bytes']) / 1024:.1f} KB")
    # Show task-hash prefixes so a user can correlate
    click.echo("")
    click.echo("Cached task hashes (top 10):")
    for c in sorted(cells, key=lambda p: -p.stat().st_size)[:10]:
        click.echo(f"  {c.stem}  ({c.stat().st_size} bytes)")


@compile_cache.command(name="vanilla-stats")
@click.pass_context
@roam_capability(
    name="compile-cache-vanilla-stats",
    category="planning",
    summary="W68 — show how many vanilla baseline cells are cached and reusable.",
    inputs=("",),
    outputs=("summary_envelope",),
    examples=("roam compile-cache vanilla-stats",),
    tags=("planning", "compiler", "cache", "bench"),
)
def vanilla_stats(ctx: click.Context) -> None:
    """Show stats on the vanilla-result reuse cache (W65)."""
    json_mode = ctx.obj.get("json") if ctx.obj else False
    cache_dir = Path.home() / ".cache" / "roam-bench-vanilla"
    if not cache_dir.exists():
        _emit_vanilla_missing(_vanilla_missing_summary(), json_mode, cache_dir)
        return
    cells = list(cache_dir.glob("*.json"))
    _emit_vanilla_present(_vanilla_present_summary(cache_dir, cells), json_mode, cache_dir, cells)


def _stats_agent_contract(summary: dict) -> dict:
    row_count = int(summary.get("row_count", 0) or 0)
    facts = [f"{row_count} cache records"]
    if "size_bytes" in summary:
        facts.append(f"{int(summary.get('size_bytes') or 0)} bytes")
    else:
        facts.append("cache stats checked")
    next_commands = ["roam compile-cache clear --stale"] if row_count else ["roam compile-cache build"]
    return {
        "facts": facts,
        "next_commands": next_commands,
        "risks": [],
        "confidence": None,
    }


@compile_cache.command(name="stats")
@click.option("--root", default=".", show_default=True)
@click.pass_context
@roam_capability(
    name="compile-cache-stats",
    category="planning",
    summary="Show row count, size, and age distribution of the W56 envelope cache.",
    inputs=("--root",),
    outputs=("summary_envelope",),
    examples=("roam compile-cache stats",),
    tags=("planning", "telemetry", "compiler", "cache"),
)
def cache_stats(ctx: click.Context, root: str) -> None:
    """Summarize the persistent envelope cache."""
    json_mode = ctx.obj.get("json") if ctx.obj else False
    path = _cache_path(root)
    if not path.exists():
        msg = "no envelope cache yet — `roam compile <task>` populates it"
        summary = {"verdict": msg, "row_count": 0, "partial_success": True}
        if json_mode:
            click.echo(
                to_json(
                    json_envelope(
                        "compile-cache-stats",
                        summary=summary,
                        agent_contract=_stats_agent_contract(summary),
                    )
                )
            )
        else:
            click.echo(f"VERDICT: {msg}")
        return
    conn = _open_db(root)
    rows = conn.execute("SELECT repo_head, art_label, ts FROM env_cache").fetchall()
    # W57.5 — surface sibling caches (plan + symbol-resolution) if present.
    # Tables are created lazily by the compiler; tolerate missing tables.
    plan_rows = 0
    sym_rows = 0
    try:
        (plan_rows,) = conn.execute("SELECT COUNT(*) FROM plan_cache").fetchone()
    except sqlite3.DatabaseError:
        plan_rows = 0
    try:
        (sym_rows,) = conn.execute("SELECT COUNT(*) FROM symbol_resolution_cache").fetchone()
    except sqlite3.DatabaseError:
        sym_rows = 0
    conn.close()
    n = len(rows)
    from collections import Counter

    heads = Counter(r[0] for r in rows)
    labels = Counter(r[1] for r in rows)
    if rows:
        oldest = min(r[2] for r in rows)
        newest = max(r[2] for r in rows)
        age_sec = int(time.time() - oldest)
    else:
        oldest = newest = 0
        age_sec = 0
    size_bytes = path.stat().st_size
    summary = {
        "verdict": f"{n} cached envelopes ({size_bytes / 1024:.1f} KB, age {age_sec}s)",
        "row_count": n,
        "plan_cache_rows": plan_rows,
        "symbol_resolution_cache_rows": sym_rows,
        "size_bytes": size_bytes,
        "oldest_ts": oldest,
        "newest_ts": newest,
        "age_seconds": age_sec,
        "head_distribution": dict(heads.most_common(5)),
        "label_distribution": dict(labels.most_common()),
        "partial_success": False,
    }
    if json_mode:
        click.echo(
            to_json(
                json_envelope(
                    "compile-cache-stats",
                    summary=summary,
                    agent_contract=_stats_agent_contract(summary),
                )
            )
        )
        return
    click.echo(f"VERDICT: {summary['verdict']}")
    click.echo(f"path:                  {path}")
    click.echo(f"env_cache rows:        {n}")
    click.echo(f"plan_cache rows:       {plan_rows}")
    click.echo(f"symbol_resolution:     {sym_rows}")
    click.echo(f"size:                  {size_bytes / 1024:.1f} KB")
    click.echo(f"age (oldest):          {age_sec}s")
    click.echo("")
    click.echo("Artifact label distribution:")
    for label, count in labels.most_common():
        click.echo(f"  {label:<14s} {count:>5d}")
    click.echo("")
    click.echo("Top repo heads (cached against):")
    for head, count in heads.most_common(5):
        click.echo(f"  {head[:12]:<14s} {count:>5d}")


def _clear_agent_contract(summary: dict) -> dict:
    facts = [f"{summary.get('rows_dropped', 0)} dropped records"]
    verdict = str(summary.get("verdict") or "")
    if "cannot determine HEAD" in verdict:
        facts.append("HEAD resolution failed")
    elif "pass --all or --stale" in verdict:
        facts.append("cache selector failed")
    elif summary.get("partial_success"):
        facts.append("cache clear failed")
    else:
        facts.append("cache clear passed")
    return {
        "facts": facts,
        "next_commands": ["roam compile-cache stats"],
        "risks": [],
        "confidence": None,
    }


def _evict_agent_contract(summary: dict) -> dict:
    facts = [f"{summary.get('rows_evicted', 0)} evicted records"]
    if summary.get("partial_success"):
        facts.append("git diff failed")
    elif "files_changed" in summary:
        facts.append(f"{summary.get('files_changed', 0)} changed files")
    else:
        facts.append("cache evict passed")
    return {
        "facts": facts,
        "next_commands": ["roam compile-cache stats"],
        "risks": [],
        "confidence": None,
    }


def _emit_evict_summary(
    summary: dict,
    json_mode: bool,
    *,
    err: bool = False,
    detail_lines: list[str] | None = None,
) -> None:
    if json_mode:
        click.echo(
            to_json(
                json_envelope(
                    "compile-cache-evict",
                    summary=summary,
                    agent_contract=_evict_agent_contract(summary),
                )
            )
        )
        return
    click.echo(f"VERDICT: {summary['verdict']}", err=err)
    for line in detail_lines or []:
        click.echo(line)


def _changed_files_since(root: str, diff_ref: str) -> set[str]:
    import subprocess as _sp

    try:
        result = _sp.run(
            ["git", "diff", "--name-only", f"{diff_ref}..HEAD"],
            capture_output=True,
            text=True,
            timeout=8.0,
            cwd=root,
        )
    except (OSError, _sp.SubprocessError) as exc:
        raise RuntimeError(str(exc)) from exc
    if result.returncode != 0:
        err = (result.stderr or "").strip() or f"git diff exited {result.returncode}"
        raise RuntimeError(err)
    return {ln.strip() for ln in result.stdout.splitlines() if ln.strip()}


def _dep_json_touches_changed_file(dep_json: str, changed: set[str]) -> bool:
    try:
        deps = json.loads(dep_json or "{}")
    except (TypeError, ValueError):
        return False
    return any(dep in changed for dep in deps.keys())


def _evict_keys_touching_changed_files(rows: list[tuple[str, str]], changed: set[str]) -> list[str]:
    return [key for key, dep_json in rows if _dep_json_touches_changed_file(dep_json, changed)]


def _evict_changed_dep_rows(root: str, changed: set[str]) -> tuple[int, int]:
    conn = _open_db(root)
    if conn is None:
        return 0, 0
    try:
        rows = conn.execute("SELECT key, dep_mtimes_json FROM env_cache WHERE dep_mtimes_json IS NOT NULL").fetchall()
        evict_keys = _evict_keys_touching_changed_files(rows, changed)
        if evict_keys:
            conn.executemany("DELETE FROM env_cache WHERE key=?", [(k,) for k in evict_keys])
            conn.commit()
        return len(evict_keys), len(rows)
    finally:
        conn.close()


def _build_agent_contract(summary: dict) -> dict:
    facts = [
        f"{summary.get('built', 0)} warmed records",
        f"{summary.get('skipped', 0)} skipped records",
    ]
    if summary.get("top_misses"):
        facts.append(f"{summary.get('top_miss_tasks_added', 0)} telemetry records")
    return {
        "facts": facts,
        "next_commands": ["roam compile-cache stats"],
        "risks": [],
        "confidence": None,
    }


def _dedupe_tasks(tasks: list[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for task in tasks:
        key = task.strip()
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(key)
    return out


def _task_has_cached_envelope(task: str, root_abs: str) -> bool:
    try:
        from roam.plan.compiler import (
            _envelope_cache_lookup,
            compile_plan,
        )

        plan = compile_plan(task, cwd=root_abs)
        return _envelope_cache_lookup(plan, root_abs) is not None
    except Exception:  # noqa: BLE001 -- cache probing is advisory only
        return False


def _top_miss_task_candidate(entry: dict, root_abs: str) -> tuple[str | None, str | None]:
    if not entry.get("active_miss"):
        return None, "stale_misses_seen"
    task = (entry.get("task_prefix") or "").strip()
    if not task:
        return None, "blank_prefixes_skipped"
    if len(task) >= _TELEMETRY_TASK_PREFIX_LIMIT:
        return None, "truncated_prefixes_skipped"
    if _task_has_cached_envelope(task, root_abs):
        return None, "already_cached_skipped"
    return task, None


def _active_top_miss_tasks(root_abs: str, limit: int) -> tuple[list[str], dict]:
    """Return reconstructable active cache-miss tasks from compile telemetry.

    Only legacy telemetry contains a reconstructable 80-character task prefix;
    privacy-safe v3+ rows carry a keyed fingerprint and therefore cannot be
    replayed as task text. Legacy prefixes at the limit may be truncated, so
    warming them would create cache rows for a different task string. Keep
    those out and surface the count instead.
    """
    from roam.commands.cmd_compile_stats import _read_telemetry, _top_cache_misses

    rows = _read_telemetry(root_abs)
    telemetry_read_state = getattr(rows, "read_state", "ok")
    invalid_telemetry_rows = getattr(rows, "invalid_rows", 0)
    requested = max(1, int(limit))
    # This local side-effecting command needs the legacy prefix to compile it,
    # but the public compile-stats boundary uses the privacy-safe projection.
    candidates = _top_cache_misses(
        rows,
        limit=max(requested * 4, requested + 20),
        include_sensitive_task_text=True,
    )
    tasks: list[str] = []
    counts = {
        "active_misses_seen": 0,
        "stale_misses_seen": 0,
        "truncated_prefixes_skipped": 0,
        "blank_prefixes_skipped": 0,
        "already_cached_skipped": 0,
    }
    for entry in candidates:
        task, skip_key = _top_miss_task_candidate(entry, root_abs)
        if skip_key:
            counts[skip_key] += 1
            if skip_key != "stale_misses_seen":
                counts["active_misses_seen"] += 1
            continue
        counts["active_misses_seen"] += 1
        tasks.append(task)
        if len(tasks) >= requested:
            break
    return tasks, counts | {
        "telemetry_rows": len(rows),
        "telemetry_read_state": telemetry_read_state,
        "invalid_telemetry_rows": invalid_telemetry_rows,
        "top_miss_candidates": len(candidates),
    }


def _top_miss_telemetry_degraded(top_misses: bool, top_miss_meta: dict) -> bool:
    """Return whether an explicitly requested top-miss input was incomplete."""

    return bool(top_misses and top_miss_meta.get("telemetry_read_state", "ok") != "ok")


def _build_input_degradations(top_misses: bool, input_meta: dict) -> list[str]:
    """Return closed, standalone reasons that requested build input was partial."""

    reasons: list[str] = []
    if _top_miss_telemetry_degraded(top_misses, input_meta):
        reasons.append(f"telemetry_{input_meta['telemetry_read_state']}")
    if input_meta.get("corpus_truncated"):
        reasons.append("corpus_truncated")
    if input_meta.get("corpus_invalid_lines"):
        reasons.append("corpus_invalid_lines")
    if input_meta.get("corpus_oversized_lines"):
        reasons.append("corpus_oversized_lines")
    all_files_state = input_meta.get("all_files_read_state")
    if all_files_state not in {None, "ok"}:
        reasons.append(f"all_files_{all_files_state}")
    if input_meta.get("all_files_invalid_paths"):
        reasons.append("all_files_invalid_paths")
    if input_meta.get("build_tasks_truncated"):
        reasons.append("build_tasks_truncated")
    return reasons


def _read_corpus_tasks(corpus_path: str) -> tuple[list[str], dict]:
    tasks: list[str] = []
    invalid_lines = 0
    oversized_lines = 0
    consumed = 0
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_BINARY", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(corpus_path, flags)
    try:
        opened = os.fstat(descriptor)
        if not stat.S_ISREG(opened.st_mode):
            raise click.ClickException("compile-cache corpus must be a regular file")
        budget = min(opened.st_size, _MAX_CORPUS_BYTES)
        fh = os.fdopen(descriptor, "rb")
        descriptor = -1
        with fh:
            while consumed < budget and len(tasks) < _MAX_CORPUS_TASKS:
                raw = fh.readline(min(_MAX_CORPUS_LINE_BYTES + 1, budget - consumed))
                if not raw:
                    raise click.ClickException("compile-cache corpus changed while it was read")
                consumed += len(raw)
                if len(raw) > _MAX_CORPUS_LINE_BYTES:
                    oversized_lines += 1
                    while consumed < budget and not raw.endswith(b"\n"):
                        raw = fh.readline(min(_MAX_CORPUS_LINE_BYTES + 1, budget - consumed))
                        if not raw:
                            raise click.ClickException("compile-cache corpus changed while it was read")
                        consumed += len(raw)
                    continue
                try:
                    line = raw.decode("utf-8").strip()
                except UnicodeDecodeError:
                    invalid_lines += 1
                    continue
                if line and not line.startswith("#"):
                    tasks.append(line)
            after = os.fstat(fh.fileno())
        stable_fields = ("st_dev", "st_ino", "st_mode", "st_nlink", "st_size", "st_mtime_ns")
        if os.name != "nt":
            stable_fields += ("st_ctime_ns",)
        if any(getattr(opened, field) != getattr(after, field) for field in stable_fields):
            raise click.ClickException("compile-cache corpus changed while it was read")
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    truncated = consumed < opened.st_size or len(tasks) >= _MAX_CORPUS_TASKS
    return tasks, {
        "corpus_bytes_read": consumed,
        "corpus_tasks_read": len(tasks),
        "corpus_invalid_lines": invalid_lines,
        "corpus_oversized_lines": oversized_lines,
        "corpus_truncated": truncated,
    }


def _all_file_tasks(root_abs: str) -> tuple[list[str], dict]:
    import concurrent.futures
    import subprocess as _sp

    try:
        process = _sp.Popen(
            ["git", "ls-files", "-z", "--", "*.py"],
            cwd=root_abs,
            stdin=_sp.DEVNULL,
            stdout=_sp.PIPE,
            stderr=_sp.DEVNULL,
        )
    except OSError:
        return [], {"all_files_read_state": "unavailable", "all_files_truncated": False}
    assert process.stdout is not None

    def read_bounded_stdout() -> bytes:
        chunks: list[bytes] = []
        remaining = _MAX_GIT_OUTPUT_BYTES + 1
        while remaining:
            chunk = process.stdout.read(min(64 * 1024, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        return b"".join(chunks)

    executor = concurrent.futures.ThreadPoolExecutor(max_workers=1)
    future = executor.submit(read_bounded_stdout)
    read_state = "ok"
    try:
        try:
            payload = future.result(timeout=_GIT_ENUMERATION_TIMEOUT_SECONDS)
        except TimeoutError:
            read_state = "timeout"
            process.kill()
            payload = future.result(timeout=2.0)
        output_truncated = len(payload) > _MAX_GIT_OUTPUT_BYTES
        if output_truncated:
            process.kill()
        try:
            returncode = process.wait(timeout=2.0)
        except _sp.TimeoutExpired:
            process.kill()
            process.wait(timeout=2.0)
            returncode = -1
            read_state = "timeout"
    finally:
        executor.shutdown(wait=True, cancel_futures=True)
        process.stdout.close()
    if returncode != 0 and read_state == "ok" and not output_truncated:
        return [], {"all_files_read_state": "git_failed", "all_files_truncated": False}

    files: list[str] = []
    invalid_paths = 0
    bounded_payload = payload[:_MAX_GIT_OUTPUT_BYTES]
    raw_paths = bounded_payload.split(b"\0")
    for raw in raw_paths:
        if not raw:
            continue
        if len(files) >= _MAX_ALL_FILE_PATHS:
            break
        try:
            path = raw.decode("utf-8")
        except UnicodeDecodeError:
            invalid_paths += 1
            continue
        if path.strip():
            files.append(path)
    path_truncated = len([raw for raw in raw_paths if raw]) > len(files) + invalid_paths
    truncated = output_truncated or path_truncated
    if truncated and read_state == "ok":
        read_state = "truncated"
    tasks: list[str] = []
    for f in files:
        tasks.append(f"what does {f} do")
        tasks.append(f"what files are coupled to {f}")
        tasks.append(f"what changed in {f} recently")
    return tasks, {
        "all_files_read_state": read_state,
        "all_files_paths_read": len(files),
        "all_files_invalid_paths": invalid_paths,
        "all_files_output_bytes": len(bounded_payload),
        "all_files_truncated": truncated,
    }


def _default_corpus_path(root_abs: str, corpus_path: str | None, top_misses: bool, all_files: bool) -> str | None:
    if corpus_path or all_files or top_misses:
        return corpus_path
    default = Path(root_abs) / "internal" / "benchmarks" / "pilot_ab_tasks.txt"
    return str(default) if default.exists() else None


def _gather_build_tasks(
    root_abs: str, corpus_path: str | None, top_misses: bool, miss_limit: int, all_files: bool
) -> tuple[list[str], dict, str | None, int, bool]:
    tasks: list[str] = []
    top_miss_meta: dict = {}
    top_miss_tasks_added = 0
    corpus_label: str | None = None
    all_files_empty = False

    corpus_path = _default_corpus_path(root_abs, corpus_path, top_misses, all_files)
    if corpus_path:
        corpus_label = corpus_path
        corpus_tasks, corpus_meta = _read_corpus_tasks(corpus_path)
        tasks.extend(corpus_tasks)
        top_miss_meta.update(corpus_meta)
    if top_misses:
        miss_tasks, miss_meta = _active_top_miss_tasks(root_abs, miss_limit)
        top_miss_meta.update(miss_meta)
        top_miss_tasks_added = len(miss_tasks)
        tasks.extend(miss_tasks)
    if all_files:
        file_tasks, file_meta = _all_file_tasks(root_abs)
        top_miss_meta.update(file_meta)
        all_files_empty = not file_tasks and file_meta["all_files_read_state"] == "ok"
        corpus_label = corpus_label or "(--all-files)"
        tasks.extend(file_tasks)
    if not corpus_label and top_misses:
        corpus_label = "(--top-misses)"
    deduped = _dedupe_tasks(tasks)
    build_tasks_truncated = max(0, len(deduped) - _MAX_BUILD_TASKS)
    top_miss_meta["build_task_limit"] = _MAX_BUILD_TASKS
    top_miss_meta["build_tasks_truncated"] = build_tasks_truncated
    return deduped[:_MAX_BUILD_TASKS], top_miss_meta, corpus_label, top_miss_tasks_added, all_files_empty


def _empty_build_summary(
    corpus_label: str | None,
    all_files: bool,
    top_misses: bool,
    miss_limit: int,
    top_miss_tasks_added: int,
    top_miss_meta: dict,
) -> dict:
    degradations = _build_input_degradations(top_misses, top_miss_meta)
    verdict = "empty corpus and no warmable telemetry tasks"
    if degradations:
        verdict = f"build inputs incomplete ({', '.join(degradations)}); no warmable tasks"
    return {
        "verdict": verdict,
        "built": 0,
        "skipped": 0,
        "corpus": corpus_label,
        "all_files": all_files,
        "top_misses": top_misses,
        "top_miss_limit": max(1, int(miss_limit)),
        "top_miss_tasks_added": top_miss_tasks_added,
        "partial_success": True,
        **top_miss_meta,
    }


def _enter_compile_cache_agent_mode() -> str | None:
    old_agent_mode = os.environ.get("ROAM_AGENT_MODE")
    os.environ["ROAM_AGENT_MODE"] = "compile_cache_build"
    return old_agent_mode


def _restore_agent_mode(old_agent_mode: str | None) -> None:
    if old_agent_mode is None:
        os.environ.pop("ROAM_AGENT_MODE", None)
        return
    os.environ["ROAM_AGENT_MODE"] = old_agent_mode


def _warm_single_task(task: str, root_abs: str, compile_plan, compile_for_artifact) -> bool:
    try:
        plan = compile_plan(task, cwd=root_abs)
        compile_for_artifact(plan, cwd=root_abs)
    except Exception:  # noqa: BLE001
        return False
    return True


def _warm_tasks(tasks: list[str], root_abs: str) -> tuple[int, int, float]:
    from roam.plan.compiler import compile_for_artifact, compile_plan

    t0 = time.perf_counter()
    built = 0
    skipped = 0
    old_agent_mode = _enter_compile_cache_agent_mode()
    try:
        for task in tasks:
            if _warm_single_task(task, root_abs, compile_plan, compile_for_artifact):
                built += 1
            else:
                skipped += 1
    finally:
        _restore_agent_mode(old_agent_mode)
    return built, skipped, time.perf_counter() - t0


def _build_success_summary(
    built: int,
    skipped: int,
    elapsed_s: float,
    corpus_label: str | None,
    all_files: bool,
    top_misses: bool,
    miss_limit: int,
    top_miss_tasks_added: int,
    top_miss_meta: dict,
) -> dict:
    degradations = _build_input_degradations(top_misses, top_miss_meta)
    verdict = f"warmed {built} envelopes in {elapsed_s:.1f}s ({skipped} skipped)"
    if degradations:
        verdict += f"; build inputs incomplete: {', '.join(degradations)}"
    return {
        "verdict": verdict,
        "built": built,
        "skipped": skipped,
        "elapsed_s": round(elapsed_s, 2),
        "corpus": corpus_label,
        "all_files": all_files,
        "top_misses": top_misses,
        "top_miss_limit": max(1, int(miss_limit)),
        "top_miss_tasks_added": top_miss_tasks_added,
        "partial_success": skipped > 0 or bool(degradations),
        **top_miss_meta,
    }


def _emit_build_summary(summary: dict, json_mode: bool) -> None:
    if json_mode:
        click.echo(
            to_json(
                json_envelope(
                    "compile-cache-build",
                    summary=summary,
                    agent_contract=_build_agent_contract(summary),
                )
            )
        )
        return
    click.echo(f"VERDICT: {summary['verdict']}")
    click.echo(f"corpus:   {summary['corpus']}")
    if summary.get("top_misses"):
        click.echo(f"top misses added: {summary['top_miss_tasks_added']}")
    click.echo(f"built:    {summary['built']}")
    click.echo(f"skipped:  {summary['skipped']}")
    if "elapsed_s" in summary:
        click.echo(f"wall:     {summary['elapsed_s']:.1f}s")


@compile_cache.command(name="clear")
@click.option("--root", default=".", show_default=True)
@click.option("--all", "drop_all", is_flag=True, default=False, help="Drop every row, not just stale ones.")
@click.option(
    "--stale", "drop_stale", is_flag=True, default=False, help="Drop rows whose repo_head differs from current HEAD."
)
@click.pass_context
@roam_capability(
    name="compile-cache-clear",
    category="planning",
    summary="Drop stale or all rows from the W56 envelope cache.",
    inputs=("--root", "--all", "--stale"),
    outputs=("summary_envelope",),
    examples=("roam compile-cache clear --stale",),
    tags=("planning", "compiler", "cache"),
    side_effect=True,
)
def cache_clear(ctx: click.Context, root: str, drop_all: bool, drop_stale: bool) -> None:
    """Drop rows from the cache."""
    json_mode = ctx.obj.get("json") if ctx.obj else False
    if not (drop_all or drop_stale):
        summary = {
            "verdict": "pass --all or --stale to specify which rows to drop",
            "rows_dropped": 0,
            "partial_success": True,
        }
        if json_mode:
            click.echo(
                to_json(
                    json_envelope(
                        "compile-cache-clear",
                        summary=summary,
                        agent_contract=_clear_agent_contract(summary),
                    )
                )
            )
        else:
            click.echo(summary["verdict"], err=True)
        ctx.exit(2)
        return
    path = _cache_path(root)
    if not path.exists():
        summary = {
            "verdict": "no cache to clear",
            "rows_dropped": 0,
            "partial_success": False,
        }
        if json_mode:
            click.echo(
                to_json(
                    json_envelope(
                        "compile-cache-clear",
                        summary=summary,
                        agent_contract=_clear_agent_contract(summary),
                    )
                )
            )
        else:
            click.echo(f"VERDICT: {summary['verdict']}")
        return
    conn = _open_db(root)
    if drop_all:
        # W12 follow-up (2026-06-02): --all must nuke ALL caches, not just
        # env_cache. The plan_cache was silently surviving and returning
        # stale procedures (mis-routed compile envelopes) after classifier
        # changes — confirmed during W11/W12/W13 routing bug investigation.
        deleted = conn.execute("DELETE FROM env_cache").rowcount
        for tbl in ("plan_cache", "symbol_resolution_cache", "probe_pos_cache", "probe_neg_cache", "run_roam_cache"):
            try:
                deleted += conn.execute(f"DELETE FROM {tbl}").rowcount
            except Exception:  # noqa: BLE001 — missing table on older schema is expected
                # Newer DB created against older schema may lack a table.
                pass
    else:
        # Need current HEAD to know what counts as "stale"
        import subprocess as _sp

        try:
            r = _sp.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True, timeout=2.0, cwd=root)
            head = r.stdout.strip() if r.returncode == 0 else ""
        except (OSError, _sp.SubprocessError):
            head = ""
        if not head:
            summary = {
                "verdict": "cannot determine HEAD; refusing to clear",
                "rows_dropped": 0,
                "partial_success": True,
            }
            if json_mode:
                click.echo(
                    to_json(
                        json_envelope(
                            "compile-cache-clear",
                            summary=summary,
                            agent_contract=_clear_agent_contract(summary),
                        )
                    )
                )
            else:
                click.echo(f"VERDICT: {summary['verdict']}", err=True)
            ctx.exit(2)
            return
        deleted = conn.execute("DELETE FROM env_cache WHERE repo_head != ?", (head,)).rowcount
    conn.commit()
    conn.close()
    summary = {"verdict": f"dropped {deleted} rows", "rows_dropped": deleted, "partial_success": False}
    if json_mode:
        click.echo(
            to_json(
                json_envelope(
                    "compile-cache-clear",
                    summary=summary,
                    agent_contract=_clear_agent_contract(summary),
                )
            )
        )
    else:
        click.echo(f"VERDICT: {summary['verdict']}")


@compile_cache.command(name="evict")
@click.option("--root", default=".", show_default=True)
@click.option(
    "--diff",
    "diff_ref",
    type=str,
    default="HEAD~1",
    help="Git ref to diff against (default HEAD~1). Evicts only envelopes "
    "whose dep files changed in `<ref>..HEAD`. Compounds with W70 "
    "per-file dep-mtime invalidation.",
)
@click.pass_context
@roam_capability(
    name="compile-cache-evict",
    category="planning",
    summary="W78 — evict env_cache rows whose deps changed since a git ref.",
    inputs=("--root", "--diff"),
    outputs=("summary_envelope",),
    examples=("roam compile-cache evict --diff HEAD~5", "roam compile-cache evict --diff main"),
    tags=("planning", "compiler", "cache", "diff-aware"),
    side_effect=True,
)
def cache_evict_diff(ctx: click.Context, root: str, diff_ref: str) -> None:
    """W78 — evict cache rows whose deps changed since a git ref."""
    json_mode = ctx.obj.get("json") if ctx.obj else False
    path = _cache_path(root)
    if not path.exists():
        summary = {
            "verdict": "no cache to evict",
            "rows_evicted": 0,
            "partial_success": False,
        }
        _emit_evict_summary(summary, json_mode)
        return
    try:
        changed = _changed_files_since(root, diff_ref)
    except RuntimeError as exc:
        summary = {
            "verdict": f"git diff failed: {exc}",
            "rows_evicted": 0,
            "partial_success": True,
        }
        _emit_evict_summary(summary, json_mode, err=True)
        ctx.exit(2)
        return
    if not changed:
        summary = {
            "verdict": f"no files changed in {diff_ref}..HEAD; nothing to evict",
            "rows_evicted": 0,
            "files_changed": 0,
            "diff_ref": diff_ref,
            "partial_success": False,
        }
        _emit_evict_summary(summary, json_mode)
        return
    rows_evicted, rows_scanned = _evict_changed_dep_rows(root, changed)
    summary = {
        "verdict": f"evicted {rows_evicted} rows touching {len(changed)} changed files",
        "rows_evicted": rows_evicted,
        "rows_scanned": rows_scanned,
        "files_changed": len(changed),
        "diff_ref": diff_ref,
        "partial_success": False,
    }
    _emit_evict_summary(
        summary,
        json_mode,
        detail_lines=[
            f"diff_ref:       {diff_ref}",
            f"files_changed:  {len(changed)}",
            f"rows_scanned:   {rows_scanned}",
            f"rows_evicted:   {rows_evicted}",
        ],
    )


@compile_cache.command(name="build")
@click.option("--root", default=".", show_default=True)
@click.option(
    "--corpus",
    "corpus_path",
    type=click.Path(exists=True, dir_okay=False),
    default=None,
    help="Tasks file (one task per line); defaults to the W37 readiness corpus.",
)
@click.option(
    "--top-misses",
    "top_misses",
    is_flag=True,
    default=False,
    help="Warm the top active cache-miss tasks from .roam/compile-runs.jsonl.",
)
@click.option(
    "--miss-limit",
    "miss_limit",
    type=int,
    default=10,
    show_default=True,
    help="Maximum telemetry miss tasks to add when --top-misses is set.",
)
@click.option(
    "--all-files",
    "all_files",
    is_flag=True,
    default=False,
    help="W59 — also pre-compile common task shapes for every tracked source file "
    "(what does X do, who calls X, files coupled to X). Big upfront cost; "
    "subsequent compiles for those shapes hit cache in ~5ms.",
)
@click.pass_context
@roam_capability(
    name="compile-cache-build",
    category="planning",
    summary="Pre-warm the envelope cache by compiling a task corpus.",
    inputs=("--root", "--corpus", "--top-misses"),
    outputs=("summary_envelope",),
    examples=(
        "roam compile-cache build",
        "roam compile-cache build --corpus internal/benchmarks/pilot_ab_tasks.txt",
        "roam compile-cache build --top-misses --miss-limit 20",
    ),
    tags=("planning", "compiler", "cache"),
    side_effect=True,
)
def cache_build(
    ctx: click.Context, root: str, corpus_path: str | None, top_misses: bool, miss_limit: int, all_files: bool
) -> None:
    """Warm the cache by compiling every task in a corpus."""
    json_mode = ctx.obj.get("json") if ctx.obj else False
    root_abs = str(Path(root).resolve())

    tasks, top_miss_meta, corpus_label, top_miss_tasks_added, all_files_empty = _gather_build_tasks(
        root_abs, corpus_path, top_misses, miss_limit, all_files
    )
    if all_files_empty:
        click.echo("VERDICT: --all-files: no tracked source files found", err=True)
        ctx.exit(2)
        return

    if not tasks:
        summary = _empty_build_summary(
            corpus_label,
            all_files,
            top_misses,
            miss_limit,
            top_miss_tasks_added,
            top_miss_meta,
        )
        _emit_build_summary(summary, json_mode)
        ctx.exit(2)
        return

    built, skipped, elapsed_s = _warm_tasks(tasks, root_abs)
    summary = _build_success_summary(
        built,
        skipped,
        elapsed_s,
        corpus_label,
        all_files,
        top_misses,
        miss_limit,
        top_miss_tasks_added,
        top_miss_meta,
    )
    _emit_build_summary(summary, json_mode)
