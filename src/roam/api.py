"""Programmatic Python API for running roam in-process.

This provides a clean embedding surface for agent frameworks and tools
that want structured roam output without shelling out to subprocesses.
"""

from __future__ import annotations

import json
import os
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from click.testing import CliRunner

from roam.db.connection import find_project_root, open_db
from roam.exit_codes import EXIT_GATE_FAILURE


class RoamAPIError(RuntimeError):
    """Raised when a programmatic roam invocation fails."""

    def __init__(
        self,
        message: str,
        *,
        command: list[str] | None = None,
        exit_code: int | None = None,
        output: str | None = None,
        payload: dict | None = None,
    ) -> None:
        super().__init__(message)
        self.command = command or []
        self.exit_code = exit_code
        self.output = output
        self.payload = payload or {}


def _cwd_scope(path: Path | None):
    """Context manager for temporary cwd changes."""
    if path is None:
        @contextmanager
        def _noop():
            yield
        return _noop()

    @contextmanager
    def _chdir():
        old = Path.cwd()
        os.chdir(path)
        try:
            yield
        finally:
            os.chdir(old)

    return _chdir()


def _normalise_project_root(project_root: str | Path | None) -> Path | None:
    if project_root is None:
        return None
    return Path(project_root).resolve()


def _extract_json_dict(text: str) -> tuple[dict | None, Exception | None]:
    """Parse the first JSON object found in possibly mixed command output."""
    if not text:
        return None, None

    decoder = json.JSONDecoder()
    starts = [i for i, ch in enumerate(text) if ch == "{"]
    last_err: Exception | None = None

    for start in starts:
        snippet = text[start:].lstrip()
        if not snippet:
            continue
        try:
            obj, _ = decoder.raw_decode(snippet)
            if isinstance(obj, dict):
                return obj, None
        except Exception as exc:
            last_err = exc
            continue

    return None, last_err


def run_json(
    command: str,
    *args: str,
    project_root: str | Path | None = None,
    allow_gate_failure: bool = True,
    budget: int | None = None,
    detail: bool = False,
    compact: bool = False,
    agent: bool = False,
    sarif: bool = False,
    include_excluded: bool = False,
) -> dict:
    """Run a roam command in-process and return parsed JSON output.

    Parameters
    ----------
    command:
        CLI command name (e.g. ``"health"``, ``"metrics"``).
    *args:
        Command-specific arguments.
    project_root:
        Working directory for command execution.
    allow_gate_failure:
        When True, exit code 5 returns parsed JSON with ``gate_failure=True``.
        When False, gate failures raise :class:`RoamAPIError`.
    """
    from roam.cli import cli

    cmd: list[str] = []
    if compact:
        cmd.append("--compact")
    if agent:
        cmd.append("--agent")
    if budget is not None and budget > 0:
        cmd.extend(["--budget", str(budget)])
    if detail:
        cmd.append("--detail")
    if sarif:
        cmd.append("--sarif")
    if include_excluded:
        cmd.append("--include-excluded")
    cmd.append("--json")
    cmd.append(command)
    cmd.extend(str(a) for a in args)

    root = _normalise_project_root(project_root)
    runner = CliRunner()

    with _cwd_scope(root):
        result = runner.invoke(cli, cmd, catch_exceptions=True)

    raw_output = (result.output or "").strip()
    parsed, parse_error = _extract_json_dict(raw_output)

    if result.exit_code == 0:
        if parsed is None:
            raise RoamAPIError(
                "Expected JSON output but command returned non-JSON text",
                command=cmd,
                exit_code=result.exit_code,
                output=raw_output,
            )
        return parsed

    if result.exit_code == EXIT_GATE_FAILURE and allow_gate_failure and parsed is not None:
        parsed.setdefault("gate_failure", True)
        parsed.setdefault("exit_code", EXIT_GATE_FAILURE)
        return parsed

    if parsed is not None:
        raise RoamAPIError(
            "Roam command failed",
            command=cmd,
            exit_code=result.exit_code,
            output=raw_output,
            payload=parsed,
        )

    if parse_error is not None:
        raise RoamAPIError(
            f"Roam command failed and output was not valid JSON: {parse_error}",
            command=cmd,
            exit_code=result.exit_code,
            output=raw_output,
        )

    raise RoamAPIError(
        "Roam command failed with empty output",
        command=cmd,
        exit_code=result.exit_code,
        output=raw_output,
    )


class RoamClient:
    """Reusable client for programmatic in-process roam calls."""

    def __init__(self, project_root: str | Path | None = None) -> None:
        self.project_root = (
            Path(project_root).resolve()
            if project_root is not None
            else find_project_root()
        )

    def run(self, command: str, *args: str, **kwargs) -> dict:
        kwargs.setdefault("project_root", self.project_root)
        return run_json(command, *args, **kwargs)

    def index(
        self,
        *,
        force: bool = False,
        verbose: bool = False,
        quiet: bool = True,
        include_excluded: bool = False,
    ) -> dict:
        args: list[str] = []
        if force:
            args.append("--force")
        if verbose:
            args.append("--verbose")
        if quiet:
            args.append("--quiet")
        return self.run("index", *args, include_excluded=include_excluded)

    def health(
        self,
        *,
        detail: bool = False,
        gate: bool = False,
        allow_gate_failure: bool = True,
    ) -> dict:
        args: list[str] = ["--gate"] if gate else []
        return self.run(
            "health",
            *args,
            detail=detail,
            allow_gate_failure=allow_gate_failure,
        )

    def context(self, symbol: str, *, depth: int = 2) -> dict:
        return self.run("context", symbol, "--depth", str(depth))

    def metrics(self, target: str) -> dict:
        return self.run("metrics", target)

    @contextmanager
    def db(self, *, readonly: bool = True) -> Iterator:
        """Open a DB connection scoped to this client project root."""
        with open_db(readonly=readonly, project_root=self.project_root) as conn:
            yield conn
