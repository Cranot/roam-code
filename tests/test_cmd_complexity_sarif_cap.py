"""Cap-as-census (finding #62) — the complexity SARIF document must
disclose its own ``-n`` display cap.

``cmd_complexity`` slices the ranking to the display cap (default 20)
BEFORE the SARIF projection, so the SARIF document — the only artifact a
Code-Scanning consumer reads — carried the capped slice with no signal
that the qualifying population was larger. The fix records the
post-filter, pre-cap row count and attaches a
``toolExecutionNotifications`` disclosure with the pre-cap denominator
whenever the cap binds. When the ranking fetch itself hit its bound
(``fetch_limit``), the denominator is suffixed ``+`` because the true
population was never measured.

Conventions mirror ``test_cmd_complexity_warnings_out.py`` (in-process
CliRunner + ``indexed_project``).
"""

from __future__ import annotations

import json as _json
import os
import re as _re

from click.testing import CliRunner

from roam.cli import cli


def _invoke_complexity_sarif(project_path, *extra):
    """Run ``roam --sarif complexity`` in-process under the given cwd."""
    runner = CliRunner()
    old_cwd = os.getcwd()
    try:
        os.chdir(str(project_path))
        result = runner.invoke(cli, ["--sarif", "complexity", *extra], catch_exceptions=False)
    finally:
        os.chdir(old_cwd)
    return result


def _notification_texts(doc):
    texts = []
    for run in doc.get("runs", []):
        for inv in run.get("invocations", []):
            for note in inv.get("toolExecutionNotifications", []):
                texts.append(note.get("message", {}).get("text", ""))
    return texts


def test_sarif_document_discloses_binding_display_cap(indexed_project):
    """``-n 1`` on a corpus with >1 ranked symbol must disclose the cap."""
    result = _invoke_complexity_sarif(indexed_project, "-n", "1")
    assert result.exit_code == 0, result.output
    doc = _json.loads(result.stdout if hasattr(result, "stdout") else result.output)
    # Precondition: exactly one result projected (the capped slice).
    assert len(doc["runs"][0]["results"]) == 1
    texts = _notification_texts(doc)
    assert any(_re.search(r"SARIF results truncated to top 1 of \d+\+? qualifying symbols", t) for t in texts), (
        f"SARIF document must disclose the -n cap; notifications: {texts}"
    )


def test_sarif_document_clean_when_cap_does_not_bind(indexed_project):
    """Default ``-n 20`` on the 3-file fixture: no truncation notification."""
    result = _invoke_complexity_sarif(indexed_project)
    assert result.exit_code == 0, result.output
    doc = _json.loads(result.stdout if hasattr(result, "stdout") else result.output)
    for text in _notification_texts(doc):
        assert "truncated" not in text
