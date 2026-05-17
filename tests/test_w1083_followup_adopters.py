"""W1083-followup — two clean adopters of ``structured_unknown_filter``.

Adoption sites:

1. ``cmd_workflow`` unknown-recipe-name handler (cmd_workflow.py:~196).
2. ``cmd_explain_command`` unknown-command-name handler
   (cmd_explain_command.py:~176).

Both sites previously inlined ``difflib.get_close_matches(n=2, cutoff=0.6)``
with hand-formatted ``Did you mean: 'x' or 'y'?`` suffixes. W1083-followup
delegates to the helper (same canonical knobs) and ALSO closes the
Pattern-1C gap by emitting a structured JSON envelope on
``--json``-mode unknown-name.

Third investigation site:

3. ``cmd_math`` task-id closest-match (cmd_math.py:~250) uses
   ``cutoff=0.4, n=3`` — looser than canonical. This test pins the
   inline-comment marker so the next audit doesn't drift.
"""

from __future__ import annotations

import json

import pytest
from click.testing import CliRunner

from roam.cli import cli


@pytest.fixture
def cli_runner():
    return CliRunner()


# ---------------------------------------------------------------------------
# Adopter 1 — cmd_workflow unknown recipe name.
# ---------------------------------------------------------------------------


def test_workflow_unknown_json_emits_envelope_with_summary_payload(cli_runner):
    """``roam --json workflow safe-delet-check`` closes the Pattern-1C
    gap: the path now emits a structured envelope on stdout that
    carries the ``to_summary_payload`` splice (``state``,
    ``partial_success``, ``requested_recipe``, ``known_recipes``,
    ``did_you_mean``)."""
    result = cli_runner.invoke(cli, ["--json", "workflow", "safe-delet-check"], catch_exceptions=False)
    # Exit code stays non-zero (structured-usage-error still raised).
    assert result.exit_code != 0
    # The first JSON object on stdout is the envelope. CliRunner combines
    # stderr+stdout; find the JSON line.
    output = result.output or ""
    # locate the first '{' so we can parse just the envelope.
    brace = output.find("{")
    assert brace >= 0, output
    # Find a matching closing brace by tracking depth.
    depth = 0
    end = brace
    for i, ch in enumerate(output[brace:], start=brace):
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                end = i + 1
                break
    payload = json.loads(output[brace:end])
    summary = payload["summary"]
    assert summary["state"] == "unknown_recipe"
    assert summary["partial_success"] is True
    assert summary["requested_recipe"] == "safe-delet-check"
    assert "safe-delete-check" in summary["known_recipes"]
    # The close match lands in did_you_mean AND in the verdict suffix.
    assert "safe-delete-check" in summary["did_you_mean"]
    assert "Did you mean" in summary["verdict"]
    # agent_contract facts anchor on the LAW-4 concrete noun "recipes".
    facts = payload["agent_contract"]["facts"]
    assert any(f.endswith("recipes") for f in facts), facts


def test_workflow_unknown_text_mode_preserves_w1074_byte_shape(cli_runner):
    """Text-mode (no ``--json``) keeps the pre-W1083-followup error
    text byte-identical so the W1074 contract tests stay green."""
    result = cli_runner.invoke(cli, ["workflow", "safe-delet-check"], catch_exceptions=False)
    assert result.exit_code != 0
    combined = (result.output or "") + (getattr(result, "stderr", "") or "")
    assert "UNKNOWN_RECIPE" in combined
    assert "safe-delet-check" in combined
    assert "Did you mean" in combined
    assert "safe-delete-check" in combined


# ---------------------------------------------------------------------------
# Adopter 2 — cmd_explain_command unknown command name.
# ---------------------------------------------------------------------------


def test_explain_command_unknown_json_emits_envelope_with_summary_payload(cli_runner):
    """``roam --json explain-command healt`` emits a structured
    envelope (Pattern-1C closure) with the ``to_summary_payload``
    splice."""
    result = cli_runner.invoke(cli, ["--json", "explain-command", "healt"], catch_exceptions=False)
    # Path still exits 2.
    assert result.exit_code == 2
    output = result.output or ""
    brace = output.find("{")
    assert brace >= 0, output
    depth = 0
    end = brace
    for i, ch in enumerate(output[brace:], start=brace):
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                end = i + 1
                break
    payload = json.loads(output[brace:end])
    summary = payload["summary"]
    assert summary["state"] == "unknown_command"
    assert summary["partial_success"] is True
    assert summary["requested_command"] == "healt"
    assert "health" in summary["known_commands"]
    assert "health" in summary["did_you_mean"]
    assert "Did you mean" in summary["verdict"]
    facts = payload["agent_contract"]["facts"]
    assert any(f.endswith("commands") for f in facts), facts


def test_explain_command_unknown_text_mode_preserves_w1074_byte_shape(cli_runner):
    """Text-mode keeps the pre-W1083-followup error/hint lines
    byte-identical."""
    result = cli_runner.invoke(cli, ["explain-command", "healt"], catch_exceptions=False)
    assert result.exit_code == 2, result.output
    combined = (result.output or "") + (getattr(result, "stderr", "") or "")
    assert "ERROR: unknown command 'healt'" in combined
    assert "did you mean" in combined.lower()
    assert "'health'" in combined
    assert "run 'roam surface'" in combined


# ---------------------------------------------------------------------------
# cmd_math:~250 — cutoff=0.4/n=3 intentional, pinned by inline comment.
# ---------------------------------------------------------------------------


def test_cmd_math_task_filter_close_match_knobs_documented():
    """The looser ``cutoff=0.4, n=3`` is intentional for the CATALOG
    task-id vocabulary (high token-variety, frequent permutation
    typos). The inline ``W1083-followup`` marker must remain so the
    next audit doesn't flag the divergence from the canonical
    helper's 0.6/2."""
    from pathlib import Path

    src = Path(__file__).parent.parent / "src" / "roam" / "commands" / "cmd_math.py"
    text = src.read_text(encoding="utf-8")
    # The call signature stays at the chosen knobs.
    assert "n=3, cutoff=0.4" in text
    # The marker pins the rationale.
    assert "W1083-followup: cutoff=0.4/n=3 intentional" in text
    # The marker is on the SAME stanza as the call (proximity check).
    marker_idx = text.index("W1083-followup: cutoff=0.4/n=3 intentional")
    call_idx = text.index("n=3, cutoff=0.4")
    # The comment precedes the call within ~30 lines.
    assert 0 < (call_idx - marker_idx) < 2000, (marker_idx, call_idx)
