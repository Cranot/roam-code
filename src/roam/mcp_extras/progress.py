"""Phase-aware progress reporting for long-running roam tools.

The existing ``mcp_server`` reports progress at 5% (start) and 100%
(end) -- that's fine for affordance but useless on a 30s+ index pass.
This module wraps a subprocess invocation, parses roam's stderr log
lines (which already carry phase markers like ``[index]``,
``[graph]``, ``[refs]``), and forwards them as MCP progress
notifications.

Falls back to coarse 5/100 progress if the line stream is unparseable
or no Context is provided.
"""

from __future__ import annotations

import asyncio
import os
import re
import subprocess
import sys
import threading
from typing import Any

# Recognised phase markers in indexer output. Order = monotonic
# progress so we never report a smaller value than we already
# emitted. Values are percentages.
_PHASE_MAP: list[tuple[re.Pattern, int, str]] = [
    (re.compile(r"\bdiscover", re.I), 8, "discovering files"),
    (re.compile(r"\b(parse|parsing|parsed)\b", re.I), 18, "parsing"),
    # "resolve / refs / references" must come before "symbols" so a line
    # like "resolving references for 4k symbols" lands at the higher phase.
    (re.compile(r"\b(refs?|references|resolving|resolved)\b", re.I), 55, "resolving references"),
    (re.compile(r"\b(extract|extracting|extracted)\b", re.I), 35, "extracting symbols"),
    (re.compile(r"\bgraph\b", re.I), 70, "building graph"),
    (re.compile(r"\b(pagerank|metrics)\b", re.I), 80, "computing metrics"),
    (re.compile(r"\b(complexity|cognitive)\b", re.I), 88, "complexity"),
    (re.compile(r"\b(git|churn|blame)\b", re.I), 93, "git stats"),
    (re.compile(r"\bhealth\b", re.I), 97, "health"),
]

_FILE_COUNT_RE = re.compile(r"(\d+)\s+files?", re.I)


def classify_line(line: str) -> tuple[int, str] | None:
    """Map a raw stderr line to ``(percent, phase_name)`` or None."""
    line = line.strip()
    if not line:
        return None
    for pat, pct, name in _PHASE_MAP:
        if pat.search(line):
            file_match = _FILE_COUNT_RE.search(line)
            if file_match:
                name = f"{name} ({file_match.group(1)} files)"
            return pct, name
    return None


async def _ctx_report_progress(
    ctx: Any,
    progress: float,
    *,
    total: float | None = None,
    message: str | None = None,
) -> None:
    if ctx is None or not hasattr(ctx, "report_progress"):
        return
    try:
        await ctx.report_progress(progress=progress, total=total, message=message)
    except Exception:
        pass


async def _ctx_info(ctx: Any, message: str) -> None:
    if ctx is None or not hasattr(ctx, "info"):
        return
    try:
        await ctx.info(message)
    except Exception:
        pass


async def run_with_phase_progress(
    args: list[str],
    *,
    ctx: Any = None,
    cwd: str | None = None,
    env: dict[str, str] | None = None,
    initial_message: str = "starting",
) -> tuple[int, str, str]:
    """Run ``roam <args>`` as a subprocess, forwarding progress.

    Returns ``(exit_code, stdout, stderr)``. Progress is reported via
    ``ctx.report_progress`` as the indexer logs phase markers to
    stderr.

    Caller is responsible for parsing the stdout JSON envelope.
    """
    full_cmd = [sys.executable, "-m", "roam", "--json", *args]

    await _ctx_report_progress(ctx, 2, total=100, message=initial_message)

    proc = await asyncio.create_subprocess_exec(
        *full_cmd,
        cwd=cwd,
        env={**os.environ, **(env or {})},
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )

    stdout_buf: list[bytes] = []
    stderr_buf: list[bytes] = []

    last_pct = 2

    async def pump_stdout() -> None:
        if proc.stdout is None:
            return
        async for line in proc.stdout:
            stdout_buf.append(line)

    async def pump_stderr() -> None:
        nonlocal last_pct
        if proc.stderr is None:
            return
        async for raw in proc.stderr:
            stderr_buf.append(raw)
            try:
                text = raw.decode("utf-8", errors="replace")
            except Exception:
                continue
            classified = classify_line(text)
            if classified is None:
                continue
            pct, name = classified
            if pct > last_pct:
                last_pct = pct
                await _ctx_report_progress(ctx, pct, total=100, message=name)
                await _ctx_info(ctx, name)

    await asyncio.gather(pump_stdout(), pump_stderr())
    exit_code = await proc.wait()

    await _ctx_report_progress(ctx, 100, total=100, message="completed")

    return (
        exit_code,
        b"".join(stdout_buf).decode("utf-8", errors="replace"),
        b"".join(stderr_buf).decode("utf-8", errors="replace"),
    )


def run_with_phase_progress_sync(
    args: list[str],
    *,
    cwd: str | None = None,
    env: dict[str, str] | None = None,
    on_phase: Any = None,
) -> tuple[int, str, str]:
    """Synchronous variant for callers that aren't async (tests).

    ``on_phase`` is a callable ``(percent, phase_name) -> None`` invoked
    on every recognised phase marker.
    """
    full_cmd = [sys.executable, "-m", "roam", "--json", *args]
    proc = subprocess.Popen(
        full_cmd,
        cwd=cwd,
        env={**os.environ, **(env or {})},
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )

    stdout_lines: list[str] = []
    stderr_lines: list[str] = []
    last_pct = 0

    def reader_stdout() -> None:
        if proc.stdout is None:
            return
        for line in proc.stdout:
            stdout_lines.append(line)

    def reader_stderr() -> None:
        nonlocal last_pct
        if proc.stderr is None:
            return
        for line in proc.stderr:
            stderr_lines.append(line)
            classified = classify_line(line)
            if classified is None:
                continue
            pct, name = classified
            if pct > last_pct:
                last_pct = pct
                if callable(on_phase):
                    try:
                        on_phase(pct, name)
                    except Exception:
                        pass

    t1 = threading.Thread(target=reader_stdout, daemon=True)
    t2 = threading.Thread(target=reader_stderr, daemon=True)
    t1.start()
    t2.start()
    proc.wait()
    t1.join()
    t2.join()

    return proc.returncode, "".join(stdout_lines), "".join(stderr_lines)
