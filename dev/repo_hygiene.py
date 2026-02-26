#!/usr/bin/env python3
"""Report git working-tree hygiene and optionally enforce thresholds."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import subprocess
import sys
from collections import Counter
from pathlib import Path


def _run_git(args: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )


def _entry_counts(entries: list[tuple[str, str]]) -> dict[str, int]:
    counts = {"staged": 0, "unstaged": 0, "untracked": 0, "conflicts": 0}
    conflict_codes = {"DD", "AU", "UD", "UA", "DU", "AA", "UU"}
    for status, _path in entries:
        if status == "??":
            counts["untracked"] += 1
            continue
        if status in conflict_codes:
            counts["conflicts"] += 1
            continue
        x, y = status[0], status[1]
        if x != " ":
            counts["staged"] += 1
        if y != " ":
            counts["unstaged"] += 1
    return counts


def _normalize(path: str) -> str:
    return path.replace("\\", "/").strip()


def _path_in_focus(path: str, focus_paths: list[str]) -> bool:
    norm = _normalize(path)
    for focus in focus_paths:
        f = _normalize(focus).rstrip("/")
        if not f:
            continue
        if norm == f or norm.startswith(f + "/"):
            return True
    return False


def _top_roots(entries: list[tuple[str, str]], predicate) -> list[dict[str, int | str]]:
    counts: Counter[str] = Counter()
    for status, path in entries:
        if not predicate(status):
            continue
        norm = _normalize(path)
        if not norm:
            root = "(unknown)"
        else:
            root = norm.split("/", 1)[0]
        counts[root] += 1
    return [{"root": root, "count": count} for root, count in counts.most_common(20)]


def _parse_porcelain(lines: list[str]) -> tuple[str, list[tuple[str, str]]]:
    branch = ""
    entries: list[tuple[str, str]] = []

    for raw in lines:
        if not raw:
            continue
        if raw.startswith("## "):
            branch = raw[3:].strip()
            continue

        status = raw[:2]
        path = raw[3:] if len(raw) >= 4 else ""
        entries.append((status, path))
    return branch, entries


def _failures(counts: dict[str, int], args: argparse.Namespace) -> list[str]:
    problems: list[str] = []
    if args.max_untracked is not None and counts["untracked"] > args.max_untracked:
        problems.append(f"untracked {counts['untracked']} > {args.max_untracked}")
    if args.max_staged is not None and counts["staged"] > args.max_staged:
        problems.append(f"staged {counts['staged']} > {args.max_staged}")
    if args.max_unstaged is not None and counts["unstaged"] > args.max_unstaged:
        problems.append(f"unstaged {counts['unstaged']} > {args.max_unstaged}")
    if args.max_conflicts is not None and counts["conflicts"] > args.max_conflicts:
        problems.append(f"conflicts {counts['conflicts']} > {args.max_conflicts}")
    if args.fail_when_dirty and any(counts[key] > 0 for key in ("staged", "unstaged", "untracked", "conflicts")):
        problems.append("working tree is not clean")
    return problems


def _read_baseline(path: Path) -> tuple[dict | None, str | None]:
    if not path.exists():
        return None, f"missing file: {path}"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return None, f"invalid JSON: {exc}"
    except OSError as exc:
        return None, f"read error: {exc}"
    if not isinstance(payload, dict):
        return None, "baseline root must be a JSON object"
    counts = payload.get("counts_global")
    if not isinstance(counts, dict):
        return None, "baseline missing counts_global object"
    for key in ("staged", "unstaged", "untracked", "conflicts"):
        if not isinstance(counts.get(key), int):
            return None, f"baseline counts_global.{key} must be an integer"
    return payload, None


def _write_baseline(path: Path, counts_global: dict[str, int], untracked_top: list[dict[str, int | str]], dirty_top: list[dict[str, int | str]]) -> None:
    payload = {
        "created_at": dt.datetime.now().isoformat(timespec="seconds"),
        "counts_global": counts_global,
        "top_untracked_roots": untracked_top,
        "top_dirty_roots": dirty_top,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def _count_deltas(current: dict[str, int], baseline: dict[str, int] | None) -> dict[str, int] | None:
    if baseline is None:
        return None
    return {
        "staged": current.get("staged", 0) - baseline.get("staged", 0),
        "unstaged": current.get("unstaged", 0) - baseline.get("unstaged", 0),
        "untracked": current.get("untracked", 0) - baseline.get("untracked", 0),
        "conflicts": current.get("conflicts", 0) - baseline.get("conflicts", 0),
    }


def _debt_failures(deltas: dict[str, int] | None, args: argparse.Namespace) -> list[str]:
    if deltas is None:
        return []
    problems: list[str] = []
    if args.max_new_untracked is not None and deltas["untracked"] > args.max_new_untracked:
        problems.append(f"new untracked debt {deltas['untracked']} > {args.max_new_untracked}")
    if args.max_new_unstaged is not None and deltas["unstaged"] > args.max_new_unstaged:
        problems.append(f"new unstaged debt {deltas['unstaged']} > {args.max_new_unstaged}")
    if args.max_new_staged is not None and deltas["staged"] > args.max_new_staged:
        problems.append(f"new staged debt {deltas['staged']} > {args.max_new_staged}")
    return problems


def main() -> int:
    parser = argparse.ArgumentParser(description="Check repository working-tree hygiene.")
    parser.add_argument(
        "--focus-path",
        action="append",
        default=[],
        help="Limit threshold checks to this path prefix (can be repeated).",
    )
    parser.add_argument(
        "--threshold-scope",
        choices=("auto", "global", "focus"),
        default="auto",
        help="Choose whether thresholds apply to global or focus counts.",
    )
    parser.add_argument("--max-untracked", type=int, default=None)
    parser.add_argument("--max-staged", type=int, default=None)
    parser.add_argument("--max-unstaged", type=int, default=None)
    parser.add_argument("--max-conflicts", type=int, default=0)
    parser.add_argument("--debt-baseline", type=Path, default=None, help="Path to global hygiene debt baseline JSON.")
    parser.add_argument("--write-debt-baseline", action="store_true", help="Write current global state as debt baseline.")
    parser.add_argument(
        "--require-debt-baseline",
        action="store_true",
        help="Fail when --debt-baseline is missing or invalid.",
    )
    parser.add_argument("--max-new-untracked", type=int, default=None, help="Max allowed increase vs debt baseline.")
    parser.add_argument("--max-new-unstaged", type=int, default=None, help="Max allowed increase vs debt baseline.")
    parser.add_argument("--max-new-staged", type=int, default=None, help="Max allowed increase vs debt baseline.")
    parser.add_argument("--fail-when-dirty", action="store_true")
    parser.add_argument("--show", type=int, default=20, help="Show first N status entries.")
    parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON.")
    args = parser.parse_args()

    repo_root = Path(__file__).resolve().parent.parent
    result = _run_git(["status", "--porcelain=v1", "--branch"], cwd=repo_root)
    if result.returncode != 0:
        sys.stderr.write(result.stderr.strip() + "\n")
        return result.returncode

    lines = [line.rstrip("\n") for line in result.stdout.splitlines()]
    branch, entries = _parse_porcelain(lines)
    global_counts = _entry_counts(entries)
    focus_paths = [p for p in args.focus_path if p and p.strip()]
    focus_entries = [e for e in entries if _path_in_focus(e[1], focus_paths)] if focus_paths else []
    focus_counts = _entry_counts(focus_entries) if focus_paths else None

    if args.threshold_scope == "global":
        threshold_scope = "global"
    elif args.threshold_scope == "focus":
        threshold_scope = "focus"
    else:
        threshold_scope = "focus" if focus_paths else "global"

    target_counts = focus_counts if threshold_scope == "focus" and focus_counts is not None else global_counts
    failures = _failures(target_counts, args)
    untracked_top = _top_roots(entries, lambda status: status == "??")
    dirty_top = _top_roots(entries, lambda status: status != "??")

    baseline_payload = None
    baseline_counts = None
    baseline_error = None
    debt_deltas = None
    debt_failures: list[str] = []
    if args.debt_baseline is not None:
        if args.write_debt_baseline:
            _write_baseline(args.debt_baseline, global_counts, untracked_top, dirty_top)
            # Recompute after baseline file materializes, then rewrite with settled counts.
            refreshed = _run_git(["status", "--porcelain=v1", "--branch"], cwd=repo_root)
            if refreshed.returncode != 0:
                sys.stderr.write(refreshed.stderr.strip() + "\n")
                return refreshed.returncode
            lines = [line.rstrip("\n") for line in refreshed.stdout.splitlines()]
            branch, entries = _parse_porcelain(lines)
            global_counts = _entry_counts(entries)
            focus_entries = [e for e in entries if _path_in_focus(e[1], focus_paths)] if focus_paths else []
            focus_counts = _entry_counts(focus_entries) if focus_paths else None
            target_counts = focus_counts if threshold_scope == "focus" and focus_counts is not None else global_counts
            failures = _failures(target_counts, args)
            untracked_top = _top_roots(entries, lambda status: status == "??")
            dirty_top = _top_roots(entries, lambda status: status != "??")
            _write_baseline(args.debt_baseline, global_counts, untracked_top, dirty_top)
        baseline_payload, baseline_error = _read_baseline(args.debt_baseline)
        if args.require_debt_baseline and baseline_payload is None:
            failures.append(f"debt baseline unavailable: {baseline_error}")
        if baseline_payload is not None:
            baseline_counts = baseline_payload.get("counts_global")
            if isinstance(baseline_counts, dict):
                debt_deltas = _count_deltas(global_counts, baseline_counts)
                debt_failures = _debt_failures(debt_deltas, args)
                failures.extend(debt_failures)

    payload = {
        "repo": str(repo_root),
        "branch": branch,
        "counts": target_counts,
        "counts_global": global_counts,
        "counts_focus": focus_counts,
        "focus_paths": focus_paths,
        "threshold_scope": threshold_scope,
        "top_untracked_roots": untracked_top,
        "top_dirty_roots": dirty_top,
        "debt_baseline": str(args.debt_baseline) if args.debt_baseline else None,
        "debt_baseline_error": baseline_error,
        "debt_baseline_counts": baseline_counts,
        "debt_deltas": debt_deltas,
        "debt_failed_checks": debt_failures,
        "entries": [{"status": s, "path": p} for s, p in entries[: max(args.show, 0)]],
        "failed_checks": failures,
    }

    if args.json:
        sys.stdout.write(json.dumps(payload, indent=2) + "\n")
    else:
        sys.stdout.write(f"repo: {repo_root}\n")
        sys.stdout.write(f"branch: {branch or '(unknown)'}\n")
        sys.stdout.write(
            "global counts: "
            f"staged={global_counts['staged']} "
            f"unstaged={global_counts['unstaged']} "
            f"untracked={global_counts['untracked']} "
            f"conflicts={global_counts['conflicts']}\n"
        )
        if focus_counts is not None:
            sys.stdout.write(
                "focus counts: "
                f"staged={focus_counts['staged']} "
                f"unstaged={focus_counts['unstaged']} "
                f"untracked={focus_counts['untracked']} "
                f"conflicts={focus_counts['conflicts']}\n"
            )
            sys.stdout.write(f"focus paths: {', '.join(focus_paths)}\n")
        sys.stdout.write(f"threshold scope: {threshold_scope}\n")
        if entries and args.show > 0:
            sys.stdout.write(f"entries (first {args.show}):\n")
            for status, path in entries[: args.show]:
                sys.stdout.write(f"  {status} {path}\n")
        if untracked_top:
            sys.stdout.write("top untracked roots:\n")
            for item in untracked_top[:10]:
                sys.stdout.write(f"  - {item['root']}: {item['count']}\n")
        if dirty_top:
            sys.stdout.write("top dirty roots:\n")
            for item in dirty_top[:10]:
                sys.stdout.write(f"  - {item['root']}: {item['count']}\n")
        if args.debt_baseline:
            sys.stdout.write(f"debt baseline file: {args.debt_baseline}\n")
            if args.write_debt_baseline:
                sys.stdout.write("debt baseline updated from current global state.\n")
            if baseline_error:
                sys.stdout.write(f"debt baseline status: {baseline_error}\n")
            if baseline_counts:
                sys.stdout.write(
                    "debt deltas: "
                    f"staged={debt_deltas['staged']} "
                    f"unstaged={debt_deltas['unstaged']} "
                    f"untracked={debt_deltas['untracked']} "
                    f"conflicts={debt_deltas['conflicts']}\n"
                )
        if failures:
            sys.stdout.write("violations:\n")
            for failure in failures:
                sys.stdout.write(f"  - {failure}\n")

    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
