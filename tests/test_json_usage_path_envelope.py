"""Drift-guard: argless `roam --json <cmd>` MUST emit a parseable JSON envelope.

Pattern-1C discipline (CLAUDE.md "Six systemic anti-patterns", variant C):
in `--json` mode a command's no-argument / usage-guidance path must NOT dump
plain text (Click `Usage:`, a bare `VERDICT:` line, raw markdown/YAML) to
stdout — a JSON consumer parsing stdout would choke. The usage path must route
through `json_envelope()` like the canonical example in `cmd_grep.py:418-441`.

A bounded `dev/roam_smoke.py` sweep (2026-05-20) caught 11 commands violating
this on their argless path. This test pins the SHAPE of the fix (stdout parses
as a JSON envelope with `command` + `summary.verdict`), not specific verdict
strings, so it survives copy-edits but catches a regression to plain text.

Add any NEW command whose argless `--json` invocation should emit guidance to
``_USAGE_PATH_COMMANDS`` below. The two positive controls (grep/invariants)
already gated their usage path before this sweep.
"""

from __future__ import annotations

import json

import pytest
from click.testing import CliRunner

from roam.cli import cli

# Commands whose argless --json invocation hits a usage / no-arg guidance path.
# Each MUST emit a JSON envelope (not plain text) in --json mode.
_USAGE_PATH_COMMANDS = [
    # Fixed in the 2026-05-20 BAD_JSON sweep:
    "preflight",
    "plan",
    "affected-tests",
    "ask",
    "relate",
    "context",
    "file",
    "history-grep",
    "ingest-trace",
    "report",
    "skill-generate",
    # Positive controls — already correct before the sweep:
    "grep",
    "invariants",
]


@pytest.mark.parametrize("command", _USAGE_PATH_COMMANDS)
def test_argless_json_emits_parseable_envelope(command: str) -> None:
    """Argless `roam --json <command>` writes a JSON envelope to stdout."""
    result = CliRunner().invoke(cli, ["--json", command], input="")

    # The command may exit 0 (generated content) or non-zero (usage error);
    # what matters for Pattern-1C is that stdout is parseable JSON, never text.
    assert result.output.strip(), f"{command}: emitted empty stdout (Pattern-1C)"
    try:
        envelope = json.loads(result.output)
    except (json.JSONDecodeError, ValueError) as exc:  # pragma: no cover - failure path
        raise AssertionError(
            f"{command}: --json stdout is not JSON (Pattern-1C). First 160 chars: {result.output[:160]!r}"
        ) from exc

    assert isinstance(envelope, dict), f"{command}: envelope is not a JSON object"
    assert "command" in envelope, f"{command}: envelope missing 'command' key"
    summary = envelope.get("summary")
    assert isinstance(summary, dict) and summary.get("verdict"), (
        f"{command}: envelope missing summary.verdict (LAW 6 standalone line)"
    )
