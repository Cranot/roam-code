"""W808 — Empty-corpus smoke for ``roam clones``.

Part of the W805 sweep validating that detector commands emit a real
structured envelope on a corpus that has no clone-able input (single
empty ``.py`` file).  The contract under test:

* exit code 0 (empty input is not an error condition)
* a valid roam JSON envelope with the standard top-level keys
* ``summary.verdict`` mentions "no" / "empty" so consumers see the
  empty state explicitly — never a default success string
* ``agent_contract.facts`` is non-empty (LAW 4: at minimum the verdict
  + zero-count anchors land in the bounded facts block)
* ``summary.partial_success`` is present as a bool — Pattern-2 hygiene
  gate from CLAUDE.md ("silent fallback").  The current ``cmd_clones``
  envelope does NOT carry the field, so the dedicated check is marked
  ``xfail(strict=True)`` per the W802 pattern: the day ``cmd_clones``
  starts emitting ``partial_success`` the test will XPASS-fail the
  suite and prompt the owner to drop the marker.
"""

from __future__ import annotations

import os
import subprocess

from click.testing import CliRunner

from roam.cli import cli
from tests.conftest import parse_json_output


def _make_empty_corpus(tmp_path):
    """Git-initialised project containing one empty .py file under src/."""
    proj = tmp_path / "empty_corpus"
    proj.mkdir()
    (proj / ".gitignore").write_text(".roam/\n", encoding="utf-8")
    src = proj / "src"
    src.mkdir()
    (src / "empty.py").write_text("", encoding="utf-8")
    env = {
        **os.environ,
        "GIT_AUTHOR_NAME": "test",
        "GIT_AUTHOR_EMAIL": "t@t",
        "GIT_COMMITTER_NAME": "test",
        "GIT_COMMITTER_EMAIL": "t@t",
    }
    subprocess.run(["git", "init"], cwd=str(proj), capture_output=True)
    subprocess.run(["git", "add", "."], cwd=str(proj), capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "init", "--allow-empty"],
        cwd=str(proj),
        capture_output=True,
        env=env,
    )
    return proj


def _run_clones_empty(tmp_path):
    """Index an empty corpus then run ``roam --json clones`` against it.

    Returns the parsed envelope dict.  Asserts exit-0 on both calls.
    """
    proj = _make_empty_corpus(tmp_path)
    runner = CliRunner()
    old_cwd = os.getcwd()
    try:
        os.chdir(str(proj))

        # Index the empty corpus first — roam clones requires an index.
        init_result = runner.invoke(cli, ["init"], catch_exceptions=False)
        assert init_result.exit_code == 0, f"roam init failed (exit {init_result.exit_code}):\n{init_result.output}"

        result = runner.invoke(cli, ["--json", "clones"], catch_exceptions=False)
        assert result.exit_code == 0, f"roam clones --json failed (exit {result.exit_code}):\n{result.output}"
        return parse_json_output(result, command="clones")
    finally:
        os.chdir(old_cwd)


def test_w808_clones_empty_corpus_envelope(tmp_path):
    """Envelope shape + verdict + facts contract on an empty corpus."""
    data = _run_clones_empty(tmp_path)

    # --- Envelope shape -----------------------------------------------------
    assert data["command"] == "clones"
    assert "summary" in data and isinstance(data["summary"], dict)
    summary = data["summary"]
    assert "verdict" in summary and isinstance(summary["verdict"], str)

    # --- Verdict must NOT be a default success ------------------------------
    # On an empty corpus the verdict line must surface the empty state.
    # Accept any token in {"no", "empty", "0", "none"} so the detector
    # can phrase it however it likes ("No structural clones detected",
    # "0 clone clusters", ...) without making the assertion brittle.
    verdict_lower = summary["verdict"].lower()
    assert any(token in verdict_lower for token in ("no ", "empty", "0", "none")), (
        f"verdict must mention empty/zero state on empty corpus, got: {summary['verdict']!r}"
    )

    # --- Zero-count fields are present and consistent ----------------------
    assert summary.get("clusters", None) == 0
    assert summary.get("clone_pairs", None) == 0
    assert data.get("clusters", None) == []
    assert data.get("pairs", None) == []

    # --- agent_contract.facts is non-empty (LAW 4 minimum) ------------------
    contract = data.get("agent_contract") or {}
    facts = contract.get("facts") or []
    assert isinstance(facts, list) and len(facts) > 0, (
        f"agent_contract.facts must be non-empty on empty corpus, got: {facts!r}"
    )


def test_w808_clones_empty_corpus_has_partial_success_bool(tmp_path):
    """summary.partial_success must be present as a bool (Pattern-2 gate)."""
    data = _run_clones_empty(tmp_path)
    summary = data["summary"]
    assert "partial_success" in summary, "summary.partial_success must be set"
    assert isinstance(summary["partial_success"], bool), (
        f"summary.partial_success must be bool, got {type(summary['partial_success']).__name__}"
    )
