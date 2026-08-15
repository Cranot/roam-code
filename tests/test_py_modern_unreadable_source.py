"""Absent-input disclosure for ``roam py-modern`` — unreadable indexed files.

Pre-fix, ``_python_files_with_text`` swallowed ``OSError`` with a bare
``continue``: an indexed file that could not be read vanished from the
scan and from ``files_scanned`` with no trace, so the adoption ratios
silently claimed a corpus they never measured. The fix records the drop
and surfaces it per channel (warnings_out / partial_success in JSON,
toolExecutionNotifications in SARIF via ``with_sarif_disclosures``,
stderr in text). The clean-and-measured path stays byte-identical.

The failure is produced for real: index two files, delete one from disk
(``ensure_index`` only builds a missing index, it does not re-scan), so
``open()`` raises ``FileNotFoundError`` on the stale row.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from conftest import invoke_cli, parse_json_output


def _project(project_factory):
    return project_factory(
        {
            "modern.py": "def f(x: int | None) -> list[int]:\n    return [x] if x else []\n",
            "gone.py": "from typing import Optional\n\ndef g(x: Optional[int]):\n    return '{}'.format(x)\n",
        }
    )


def test_unreadable_indexed_file_is_disclosed_in_json(project_factory, cli_runner):
    project = _project(project_factory)
    (project / "gone.py").unlink()

    result = invoke_cli(cli_runner, ["py-modern"], cwd=project, json_mode=True)
    data = parse_json_output(result, "py-modern")
    summary = data["summary"]
    assert summary["partial_success"] is True, summary
    assert any(w.startswith("py_modern_source_unreadable:") and "gone.py" in w for w in summary["warnings_out"]), (
        summary
    )
    assert any(w.startswith("py_modern_source_unreadable:") for w in data["warnings_out"]), data
    # ``files_scanned`` counts only what was actually read; the dropped
    # file is disclosed rather than silently absorbed into the count.
    assert summary["files_scanned"] == 1, summary


def test_unreadable_indexed_file_is_disclosed_in_sarif_and_text(project_factory, cli_runner):
    project = _project(project_factory)
    (project / "gone.py").unlink()

    sarif_result = invoke_cli(cli_runner, ["--sarif", "py-modern"], cwd=project)
    assert sarif_result.exit_code == 0, sarif_result.output
    doc = json.loads(sarif_result.stdout)
    notes = [
        n["message"]["text"]
        for inv in doc["runs"][0].get("invocations", [])
        for n in inv.get("toolExecutionNotifications", [])
    ]
    assert any("py_modern_source_unreadable:" in t for t in notes), (
        f"unreadable-file SARIF must carry the disclosure notification; got {notes!r}"
    )

    text_result = invoke_cli(cli_runner, ["py-modern"], cwd=project)
    assert text_result.exit_code == 0, text_result.output
    assert "py_modern_source_unreadable:" in text_result.stderr
    # stdout stays byte-identical (marker goes to stderr only).
    assert "py_modern_source_unreadable:" not in text_result.stdout


def test_clean_scan_carries_no_disclosure_keys(project_factory, cli_runner):
    """Empty-bucket discipline: the measured-clean envelope is unchanged."""
    project = _project(project_factory)

    result = invoke_cli(cli_runner, ["py-modern"], cwd=project, json_mode=True)
    data = parse_json_output(result, "py-modern")
    assert "warnings_out" not in data
    assert "warnings_out" not in data["summary"]
    # json_envelope normalises an absent partial_success to False
    # (formatter.py backstop) -- the clean scan must not flip it.
    assert data["summary"]["partial_success"] is False
    assert data["summary"]["files_scanned"] == 2
