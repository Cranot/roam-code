#!/usr/bin/env python3
"""Run roam quality snapshots across a local OSS repository corpus.

This is a reproducible harness for backlog item #37.
It expects repositories to exist locally (by default under bench-repos/).
"""

from __future__ import annotations

import argparse
import json
import subprocess
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


@dataclass
class CmdResult:
    returncode: int
    stdout: str
    stderr: str
    elapsed_s: float


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _run_command(cwd: Path, args: list[str], timeout_s: int) -> CmdResult:
    start = time.perf_counter()
    try:
        proc = subprocess.run(
            args,
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=timeout_s,
            encoding="utf-8",
        )
        elapsed = round(time.perf_counter() - start, 3)
        return CmdResult(
            returncode=proc.returncode,
            stdout=proc.stdout.strip(),
            stderr=proc.stderr.strip(),
            elapsed_s=elapsed,
        )
    except subprocess.TimeoutExpired:
        elapsed = round(time.perf_counter() - start, 3)
        return CmdResult(
            returncode=124,
            stdout="",
            stderr=f"timed out after {timeout_s}s",
            elapsed_s=elapsed,
        )
    except OSError as exc:
        elapsed = round(time.perf_counter() - start, 3)
        return CmdResult(returncode=127, stdout="", stderr=str(exc), elapsed_s=elapsed)


def _run_roam_json(cwd: Path, command: str, timeout_s: int) -> tuple[dict[str, Any] | None, CmdResult]:
    res = _run_command(cwd, ["roam", "--json", command], timeout_s=timeout_s)
    if res.returncode != 0:
        return None, res
    try:
        payload = json.loads(res.stdout)
        return payload, res
    except json.JSONDecodeError:
        return None, res


def _health_score(payload: dict[str, Any]) -> int | None:
    direct = payload.get("health_score")
    if isinstance(direct, (int, float)):
        return int(direct)
    summary = payload.get("summary", {})
    nested = summary.get("health_score")
    if isinstance(nested, (int, float)):
        return int(nested)
    return None


def _dead_symbols(payload: dict[str, Any]) -> int | None:
    summary = payload.get("summary", {})
    safe = summary.get("safe", 0)
    review = summary.get("review", 0)
    if isinstance(safe, (int, float)) and isinstance(review, (int, float)):
        return int(safe + review)
    return None


def _extract_metrics(
    health: dict[str, Any],
    dead: dict[str, Any] | None,
    complexity: dict[str, Any] | None,
    coupling: dict[str, Any] | None,
) -> dict[str, Any]:
    cx = complexity.get("summary", {}) if complexity else {}
    cp = coupling.get("summary", {}) if coupling else {}
    severity = health.get("severity", {})
    return {
        "health_score": _health_score(health),
        "tangle_ratio": health.get("tangle_ratio"),
        "critical_issues": severity.get("CRITICAL", 0),
        "warning_issues": severity.get("WARNING", 0),
        "dead_symbols": _dead_symbols(dead) if dead else None,
        "avg_complexity": cx.get("average_complexity"),
        "p90_complexity": cx.get("p90_complexity"),
        "hidden_coupling": cp.get("hidden_coupling") if coupling else None,
    }


def _render_markdown(summary: dict[str, Any]) -> str:
    lines: list[str] = []
    lines.append("# OSS Benchmark Snapshot")
    lines.append("")
    lines.append(f"- Generated: `{summary['generated_at']}`")
    lines.append(f"- Manifest: `{summary['manifest_path']}`")
    lines.append(
        f"- Evaluated: `{summary['counts']['evaluated']}` / `{summary['counts']['targets_total']}` targets"
    )
    lines.append(
        f"- Full vs partial: `{summary['counts']['evaluated_full']}` full, `{summary['counts']['evaluated_partial']}` partial"
    )
    lines.append(
        f"- Major targets evaluated: `{summary['counts']['major_evaluated']}` / `{summary['counts']['major_total']}`"
    )
    lines.append("")
    lines.append("## Aggregate")
    lines.append("")
    lines.append("| Metric | Value |")
    lines.append("|---|---:|")
    lines.append(f"| Average health score | {_fmt(summary['aggregate']['avg_health_score'])} |")
    lines.append(f"| Average dead symbols | {_fmt(summary['aggregate']['avg_dead_symbols'])} |")
    lines.append(f"| Average hidden coupling | {_fmt(summary['aggregate']['avg_hidden_coupling'])} |")
    lines.append("")
    lines.append("## Per repository")
    lines.append("")
    lines.append(
        "| Target | Tier | Status | Health | Dead | AvgCx | P90Cx | HiddenCoupling | Time(s) |"
    )
    lines.append("|---|---|---|---:|---:|---:|---:|---:|---:|")
    for row in summary["results"]:
        metrics = row.get("metrics", {})
        lines.append(
            f"| {row['id']} | {row['tier']} | {row['status']} | "
            f"{_fmt_int(metrics.get('health_score'))} | {_fmt_int(metrics.get('dead_symbols'))} | "
            f"{_fmt(metrics.get('avg_complexity'))} | {_fmt(metrics.get('p90_complexity'))} | "
            f"{_fmt_int(metrics.get('hidden_coupling'))} | {_fmt(row.get('elapsed_s'))} |"
        )
    lines.append("")
    missing_major = summary["counts"]["major_missing"]
    if missing_major:
        lines.append("## Missing required major targets")
        lines.append("")
        for row in missing_major:
            lines.append(f"- `{row['id']}` ({row['repo']}): {row['reason']}")
        lines.append("")
    return "\n".join(lines) + "\n"


def _fmt(value: Any) -> str:
    if isinstance(value, (int, float)):
        return f"{value:.2f}"
    return "N/A"


def _fmt_int(value: Any) -> str:
    if isinstance(value, bool):
        return "N/A"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        if value.is_integer():
            return str(int(value))
        return f"{value:.2f}"
    return "N/A"


def _avg(values: list[float]) -> float | None:
    if not values:
        return None
    return round(sum(values) / len(values), 2)


def run(manifest_path: Path, timeout_s: int, init_if_missing: bool) -> dict[str, Any]:
    root = _repo_root()
    manifest_path = manifest_path.resolve()
    raw = json.loads(manifest_path.read_text(encoding="utf-8"))
    targets = raw.get("targets", [])

    results: list[dict[str, Any]] = []
    major_missing: list[dict[str, str]] = []

    for target in targets:
        rel = target["local_path"]
        repo_dir = root / rel
        record: dict[str, Any] = {
            "id": target["id"],
            "repo": target["repo"],
            "tier": target.get("tier", "unknown"),
            "required_for_item_37": bool(target.get("required_for_item_37")),
            "local_path": rel,
        }

        if not repo_dir.is_dir():
            record["status"] = "missing_local_repo"
            record["reason"] = "repository path does not exist locally"
            if record["required_for_item_37"]:
                major_missing.append(
                    {"id": record["id"], "repo": record["repo"], "reason": record["reason"]}
                )
            results.append(record)
            continue

        index_db = repo_dir / ".roam" / "index.db"
        if not index_db.exists():
            if not init_if_missing:
                record["status"] = "missing_index"
                record["reason"] = "run with --init-if-missing to build index"
                results.append(record)
                continue
            init_res = _run_command(repo_dir, ["roam", "init"], timeout_s=max(timeout_s, 300))
            if init_res.returncode != 0:
                record["status"] = "init_failed"
                record["reason"] = init_res.stderr or "roam init failed"
                record["elapsed_s"] = init_res.elapsed_s
                results.append(record)
                continue

        total_elapsed = 0.0
        commands: dict[str, dict[str, Any] | None] = {"health": None, "dead": None, "complexity": None, "coupling": None}
        failed_optional: list[str] = []
        for cmd in ("health", "dead", "complexity", "coupling"):
            payload, cmd_res = _run_roam_json(repo_dir, cmd, timeout_s=timeout_s)
            total_elapsed += cmd_res.elapsed_s
            if payload is None:
                if cmd == "health":
                    record["status"] = "analysis_failed"
                    record["reason"] = cmd_res.stderr or f"roam {cmd} failed"
                    record["failed_command"] = cmd
                    record["elapsed_s"] = round(total_elapsed, 3)
                    failed_optional = []
                    break
                failed_optional.append(cmd)
                continue
            commands[cmd] = payload

        if record.get("status") == "analysis_failed":
            results.append(record)
            continue

        metrics = _extract_metrics(
            health=commands["health"] or {},
            dead=commands["dead"],
            complexity=commands["complexity"],
            coupling=commands["coupling"],
        )
        if failed_optional:
            record["status"] = "ok_partial"
            record["reason"] = "missing optional commands: " + ", ".join(failed_optional)
        else:
            record["status"] = "ok"
        record["metrics"] = metrics
        record["elapsed_s"] = round(total_elapsed, 3)
        results.append(record)

    evaluated_ok = [r for r in results if r.get("status") == "ok"]
    evaluated_partial = [r for r in results if r.get("status") == "ok_partial"]
    evaluated = evaluated_ok + evaluated_partial
    health_values = [
        float(r["metrics"]["health_score"])
        for r in evaluated
        if isinstance(r.get("metrics", {}).get("health_score"), (int, float))
    ]
    dead_values = [
        float(r["metrics"]["dead_symbols"])
        for r in evaluated
        if isinstance(r.get("metrics", {}).get("dead_symbols"), (int, float))
    ]
    hidden_values = [
        float(r["metrics"]["hidden_coupling"])
        for r in evaluated
        if isinstance(r.get("metrics", {}).get("hidden_coupling"), (int, float))
    ]

    major_targets = [t for t in targets if t.get("required_for_item_37")]
    major_eval = [
        r for r in evaluated if r.get("required_for_item_37")
    ]

    try:
        manifest_for_report = manifest_path.relative_to(root.resolve()).as_posix()
    except ValueError:
        manifest_for_report = manifest_path.as_posix()

    summary = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "manifest_path": manifest_for_report,
        "counts": {
            "targets_total": len(targets),
            "evaluated": len(evaluated),
            "evaluated_full": len(evaluated_ok),
            "evaluated_partial": len(evaluated_partial),
            "major_total": len(major_targets),
            "major_evaluated": len(major_eval),
            "major_missing": major_missing,
        },
        "aggregate": {
            "avg_health_score": _avg(health_values),
            "avg_dead_symbols": _avg(dead_values),
            "avg_hidden_coupling": _avg(hidden_values),
        },
        "results": results,
    }
    return summary


def main() -> int:
    root = _repo_root()
    parser = argparse.ArgumentParser(description="Run roam OSS benchmark snapshot")
    parser.add_argument(
        "--manifest",
        type=Path,
        default=root / "benchmarks" / "oss-eval" / "targets.json",
        help="Path to benchmark targets manifest",
    )
    parser.add_argument(
        "--output-json",
        type=Path,
        default=root / "benchmarks" / "oss-eval" / "results" / "latest.json",
        help="JSON output path",
    )
    parser.add_argument(
        "--output-md",
        type=Path,
        default=root / "benchmarks" / "oss-eval" / "results" / "latest.md",
        help="Markdown output path",
    )
    parser.add_argument(
        "--timeout-s",
        type=int,
        default=180,
        help="Per-command timeout in seconds",
    )
    parser.add_argument(
        "--init-if-missing",
        action="store_true",
        help="Run `roam init` when index is missing",
    )
    args = parser.parse_args()

    summary = run(args.manifest, timeout_s=args.timeout_s, init_if_missing=args.init_if_missing)

    args.output_json.parent.mkdir(parents=True, exist_ok=True)
    args.output_md.parent.mkdir(parents=True, exist_ok=True)
    args.output_json.write_text(
        json.dumps(summary, indent=2, ensure_ascii=True) + "\n",
        encoding="utf-8",
    )
    args.output_md.write_text(_render_markdown(summary), encoding="utf-8")

    print(f"Wrote {args.output_json}")
    print(f"Wrote {args.output_md}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
