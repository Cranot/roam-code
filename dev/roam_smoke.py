#!/usr/bin/env python3
"""Bounded roam-command smoke harness — surface broken commands without hanging.

Runs every canonical CLI command once, argless, in `--json` mode, each in its
OWN subprocess with `stdin=DEVNULL` (so a stdin-reading command gets immediate
EOF instead of blocking forever on an inherited console handle) and a HARD
per-command timeout (the OS kills any genuine hang). Commands run on a small
thread pool so one slow command can't stall the whole sweep. A finite list run
once = no loop; subprocess isolation = one crash/hang never blocks the next.

This is a HANG/CRASH detector first: argless invocation is intentional. A
command that emits a clean usage/error envelope argless is healthy (correct
Pattern-1 behavior). The bugs we hunt are: HANG (no return), CRASH (uncaught
traceback), BAD_JSON / EMPTY_STDOUT (Pattern-1C envelope violations in --json).

Usage:  python dev/roam_smoke.py [--timeout 180] [--workers 6]
Output: dev/roam_smoke_results.jsonl  (incremental, one row per command)
        dev/ROAM-SMOKE-<date>.md       (human summary of the actionable failures)
Safe to Ctrl-C: partial JSONL survives.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import datetime as _dt
import json
import os
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

_WIN = sys.platform == "win32"


def _tree_kill(pid: int) -> None:
    """Kill the whole process tree of ``pid`` (Windows: taskkill /T; POSIX: killpg).

    ``subprocess`` only kills the direct child on timeout; a roam command that
    spawned a detached grandchild (git, an index worker, a server) would leak it
    and — worse — its inherited stdout/stderr pipe stays open, so ``communicate``
    blocks forever and the ThreadPoolExecutor never shuts down. Tree-killing on
    timeout is what makes this a true HANG detector instead of a hang itself.
    """
    try:
        if _WIN:
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(pid)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=10,
            )
        else:
            os.killpg(os.getpgid(pid), signal.SIGKILL)
    except Exception:
        pass


# Destructive / daemon / interactive / setup commands: NEVER auto-run argless.
# Skipping a few is far cheaper than mutating state or hanging on a daemon.
_SKIP = {
    "init",  # rebuilds index (mutates .roam)
    "mcp",  # long-running server
    "watch",  # daemon
    "ci-setup",  # writes workflow files
    "mcp-setup",  # writes client config
    "hooks",  # writes git hooks
    "pre-commit",  # writes hook
    "repl",  # interactive
    "dashboard",  # interactive/long
    "config",  # may write config
    "index-export",  # writes large artifact
    "index-bundle",  # writes large artifact
    "graph-export",  # writes large artifact
    "agent-export",  # writes artifact
    "index",  # rebuilds the index (mutating) — alias of init-family
    "metrics-push",  # pushes metrics; needs --dry-run, not argless
    "lsp",  # language-server daemon mode
}


def _roam_commands() -> list[str]:
    """Pull the canonical command list from `roam surface --json`."""
    out = subprocess.run(
        ["roam", "surface", "--json"],
        capture_output=True,
        text=True,
        timeout=60,
        stdin=subprocess.DEVNULL,
    )
    data = json.loads(out.stdout)
    cmds = data.get("commands") or []
    names = [c if isinstance(c, str) else c.get("name", "") for c in cmds]
    return sorted(n for n in names if n and n not in _SKIP)


def _classify(rc: int | None, stdout: str, stderr: str, timed_out: bool) -> tuple[str, str]:
    """Return (classification, one-line note). HANG/CRASH/BAD_JSON/EMPTY = actionable."""
    if timed_out:
        return "HANG", "no return within timeout (killed)"
    if rc != 0 and "Traceback (most recent call last)" in stderr:
        last = stderr.strip().splitlines()[-1] if stderr.strip() else ""
        return "CRASH", f"uncaught: {last[:140]}"
    out = stdout.strip()
    if rc == 0 and not out:
        return "EMPTY_STDOUT", "exit 0 but empty stdout (Pattern-1C)"
    if out:
        try:
            obj = json.loads(out)
        except (json.JSONDecodeError, ValueError):
            return "BAD_JSON", f"exit {rc}, stdout not JSON (Pattern-1C): {out[:120]!r}"
        if rc == 0:
            return "OK", ""
        is_err = isinstance(obj, dict) and (obj.get("isError") or obj.get("status"))
        return (
            ("STRUCTURED_ERR", f"exit {rc}, structured envelope (healthy)")
            if is_err
            else (
                "ERROR_NO_ENVELOPE",
                f"exit {rc}, JSON but no isError/status",
            )
        )
    first = stderr.strip().splitlines()[0] if stderr.strip() else ""
    return "USAGE_ERR", f"exit {rc}, stderr: {first[:120]}"


def _run_one(cmd: str, timeout: int) -> dict:
    """Run one ``roam --json <cmd>`` in an isolated process GROUP with stdin closed.

    Uses Popen in a fresh process group so a timeout tree-kills any detached
    grandchild (see ``_tree_kill``); ``subprocess.run``'s timeout would only kill
    the direct child and then block on the orphan's still-open pipe.
    """
    t0 = time.monotonic()
    timed_out = False
    rc: int | None = None
    so = se = ""
    popen_kwargs: dict = {
        "stdin": subprocess.DEVNULL,  # critical: stdin readers get EOF, never block
        "stdout": subprocess.PIPE,
        "stderr": subprocess.PIPE,
        "text": True,
    }
    if _WIN:
        popen_kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        popen_kwargs["start_new_session"] = True  # own pgid for killpg
    try:
        p = subprocess.Popen(["roam", "--json", cmd], **popen_kwargs)
        try:
            so, se = p.communicate(timeout=timeout)
            rc = p.returncode
        except subprocess.TimeoutExpired:
            timed_out = True
            _tree_kill(p.pid)
            try:  # reap so no zombie / pipe lingers
                so, se = p.communicate(timeout=5)
            except subprocess.TimeoutExpired:
                so = se = ""
    except Exception as e:  # harness must never die mid-run
        rc, se = -999, f"harness-exception: {type(e).__name__}: {e}"
    dur = round(time.monotonic() - t0, 1)
    kind, note = _classify(rc, so, se, timed_out)
    return {"cmd": cmd, "kind": kind, "rc": rc, "dur_s": dur, "note": note}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--timeout",
        type=int,
        default=180,
        help=(
            "hard per-command timeout (s). Heavy analysis commands "
            "(clones/smells/partition/dead/math) run 30-100s+ solo and 2-3x "
            "that under worker contention; a low value false-flags "
            "slow-but-healthy commands as HANG. 180s clears every command's "
            "real runtime so only a genuine non-returning hang trips it."
        ),
    )
    ap.add_argument("--workers", type=int, default=6, help="parallel subprocesses")
    ap.add_argument("--only", default="", help="comma-list: run ONLY these commands (disambiguation re-run)")
    args = ap.parse_args()

    root = Path(__file__).resolve().parent.parent
    jsonl = root / "dev" / "roam_smoke_results.jsonl"
    jsonl.write_text("", encoding="utf-8")  # truncate prior run

    cmds = _roam_commands()
    if args.only:
        wanted = {c.strip() for c in args.only.split(",") if c.strip()}
        cmds = [c for c in cmds if c in wanted]
    print(
        f"[smoke] {len(cmds)} commands, {args.timeout}s timeout, stdin=DEVNULL, {args.workers} workers, argless --json",
        flush=True,
    )

    rows: list[dict] = []
    lock = threading.Lock()
    n = len(cmds)
    with jsonl.open("a", encoding="utf-8") as fh, concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as ex:
        futs = {ex.submit(_run_one, c, args.timeout): c for c in cmds}
        for fut in concurrent.futures.as_completed(futs):
            row = fut.result()
            with lock:
                rows.append(row)
                fh.write(json.dumps(row) + "\n")
                fh.flush()
                flag = "" if row["kind"] in ("OK", "STRUCTURED_ERR", "USAGE_ERR") else "  <<<"
                print(f"[{len(rows):3}/{n}] {row['kind']:16} {row['dur_s']:5}s  {row['cmd']}{flag}", flush=True)

    by_kind: dict[str, list[str]] = {}
    for r in rows:
        by_kind.setdefault(r["kind"], []).append(r["cmd"])
    actionable = {
        k: v for k, v in by_kind.items() if k in ("HANG", "CRASH", "BAD_JSON", "EMPTY_STDOUT", "ERROR_NO_ENVELOPE")
    }

    date = _dt.date.today().isoformat()
    md = root / "dev" / f"ROAM-SMOKE-{date}.md"
    lines = [f"# roam command smoke - {date}", ""]
    lines.append(f"{len(rows)} commands run argless (`roam --json <cmd>`), {args.timeout}s timeout, stdin=DEVNULL.\n")
    lines.append("## Tally")
    for k in sorted(by_kind, key=lambda k: -len(by_kind[k])):
        lines.append(f"- **{k}**: {len(by_kind[k])}")
    lines.append("\n## Actionable (HANG / CRASH / BAD_JSON / EMPTY_STDOUT / ERROR_NO_ENVELOPE)")
    if not actionable:
        lines.append("- none - all commands returned a healthy envelope or usage error.")
    for k, names in actionable.items():
        lines.append(f"\n### {k} ({len(names)})")
        for name in names:
            note = next((r["note"] for r in rows if r["cmd"] == name), "")
            lines.append(f"- `{name}` - {note}")
    md.write_text("\n".join(lines) + "\n", encoding="utf-8")

    print("\n[smoke] === SUMMARY ===", flush=True)
    for k in sorted(by_kind, key=lambda k: -len(by_kind[k])):
        print(f"  {k:18} {len(by_kind[k])}", flush=True)
    print(f"[smoke] actionable failures: {sum(len(v) for v in actionable.values())}", flush=True)
    print(f"[smoke] report: {md}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
