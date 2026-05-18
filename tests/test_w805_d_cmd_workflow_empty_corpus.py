"""W805-D - empty-corpus smoke for ``roam workflow`` (W805 Pattern 2 sweep).

Fourth-in-batch of the W805 sweep extending the Pattern-2 audit beyond the
original W802/W836 cohort. ``cmd_workflow`` is *named* like a compound-recipe
executor, so the up-front hypothesis was "this is a compound, expect Pattern-2
silent-OK on empty children" (the same bug shape sealed in W805-A and pinned
in W805-B).

W978 first-hypothesis re-run before any test was written: **invalid for
cmd_workflow**. ``cmd_workflow`` is NOT a compound executor - it is an
*inspector* of the statically-defined ``RECIPES: list[Recipe]`` registry in
``src/roam/ask/recipes.py``. The recipes constant is materialised at module
import (25 entries today) and is independent of the workspace corpus. The
empty-corpus probe confirms:

- ``roam --json workflow`` (list mode, no args) emits
  ``verdict: "25 workflow recipe(s) available"`` on a 0-symbol corpus -
  CORRECT, because the recipe registry has 25 entries regardless of the
  corpus. Not a silent SAFE.
- ``roam --json workflow safe-delete-check`` (detail mode) emits
  ``verdict: "workflow recipe 'safe-delete-check'"`` - corpus-independent
  metadata read. Not a silent SAFE.
- ``roam --json workflow --next preflight`` emits 3 suggestions from the
  static ``_NEXT_HINTS`` dict. Not corpus-dependent. Not a silent SAFE.
- ``roam --json workflow --next bogus-cmd`` ALREADY emits an explicit
  ``verdict: "no canned next-command for `roam bogus-cmd`"`` (LAW 6
  standalone, already discloses the absent-state). The branch does NOT
  set ``summary.partial_success: True`` and does NOT carry a
  ``summary.state`` closed-enum disclosure, but the verdict text is loud.
- ``roam --json workflow unknown-recipe`` ALREADY emits
  ``state: "unknown_recipe"`` via ``structured_unknown_filter`` and
  ``error_code: UNKNOWN_RECIPE`` (sealed by W1083-followup).

The remaining Pattern-2 candidate is the ``--next <unknown>`` branch: the
verdict text is honest, but the closed-enum ``state`` field is absent and
``partial_success`` is auto-injected as ``False``. That's a milder Pattern-2
shape than the canonical silent SAFE (verdict text discloses the absence) -
pinned via xfail-strict for the fix wave so the contract surface remains
uniform across the W805 cohort.

W805-D verdict: NO REAL BUG in the canonical Pattern-2 silent-SAFE shape.
A milder closed-enum-disclosure gap on the ``--next <unknown>`` branch is
pinned. cmd_workflow is correctly invocation-scoped per its docstring
("invocation-scoped recipe-metadata enumeration").

Run isolation:
    python -m pytest tests/test_w805_d_cmd_workflow_empty_corpus.py -x -n 0
"""

from __future__ import annotations

import json as _json
import os
from pathlib import Path

import pytest
from click.testing import CliRunner

from tests.conftest import git_init, index_in_process

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _invoke_workflow(runner: CliRunner, cwd: Path, *args: str, json_mode: bool = True):
    """Invoke ``roam workflow`` through the Click group so ``--json`` is honoured."""
    from roam.cli import cli

    cli_args: list[str] = []
    if json_mode:
        cli_args.append("--json")
    cli_args.append("workflow")
    cli_args.extend(args)

    old_cwd = os.getcwd()
    try:
        os.chdir(str(cwd))
        result = runner.invoke(cli, cli_args, catch_exceptions=False)
    finally:
        os.chdir(old_cwd)
    return result


def _parse_envelope(result) -> dict:
    """Parse the first JSON object from stdout.

    The W1083-followup unknown-recipe path emits a structured envelope on
    stdout AND then raises a Click UsageError - the UsageError prefix can
    end up appended to stdout in some Click versions. Use ``raw_decode`` to
    pull off the first object and ignore any trailing prose.
    """
    raw = result.output.lstrip()
    assert raw.startswith("{"), f"expected JSON envelope, got:\n{result.output}"
    decoder = _json.JSONDecoder()
    obj, _end = decoder.raw_decode(raw)
    return obj


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def cli_runner():
    return CliRunner()


@pytest.fixture
def empty_corpus(tmp_path, monkeypatch):
    """Indexed project with a single empty .py file - 0 symbols, 0 edges."""
    proj = tmp_path / "empty_corpus"
    proj.mkdir()
    (proj / ".gitignore").write_text(".roam/\n")
    (proj / "empty.py").write_text("")
    git_init(proj)
    monkeypatch.chdir(proj)
    out, rc = index_in_process(proj, "--force")
    assert rc == 0, f"index failed: {out}"
    return proj


@pytest.fixture
def clean_corpus(tmp_path, monkeypatch):
    """Indexed project with real symbols - regression baseline."""
    proj = tmp_path / "clean_corpus"
    proj.mkdir()
    (proj / ".gitignore").write_text(".roam/\n")
    src = proj / "src"
    src.mkdir()
    (src / "main.py").write_text(
        "def main():\n    helper()\n\ndef helper():\n    pass\n",
        encoding="utf-8",
    )
    git_init(proj)
    monkeypatch.chdir(proj)
    out, rc = index_in_process(proj, "--force")
    assert rc == 0, f"index failed: {out}"
    return proj


# ---------------------------------------------------------------------------
# Sealed-today contracts (always-on smoke)
# ---------------------------------------------------------------------------


class TestWorkflowEmptyCorpusSealed:
    """Properties already satisfied by the current cmd_workflow envelope."""

    def test_empty_corpus_no_crash(self, cli_runner, empty_corpus):
        """``roam workflow`` (list mode) on empty corpus exits 0."""
        result = _invoke_workflow(cli_runner, empty_corpus, json_mode=True)
        assert result.exit_code == 0, f"expected exit 0, got {result.exit_code}; output:\n{result.output}"
        # Pattern-1C: stdout MUST be non-empty in --json mode.
        assert result.output.strip(), "stdout must NOT be empty in --json mode"

    def test_empty_corpus_envelope_has_verdict(self, cli_runner, empty_corpus):
        """List mode emits ``command=workflow`` + non-empty ``summary.verdict``."""
        result = _invoke_workflow(cli_runner, empty_corpus, json_mode=True)
        envelope = _parse_envelope(result)
        assert envelope["command"] == "workflow"
        summary = envelope.get("summary") or {}
        verdict = summary.get("verdict") or ""
        assert isinstance(verdict, str) and verdict, f"summary.verdict must be a non-empty string, got {verdict!r}"

    def test_empty_corpus_explicit_state_via_verdict(self, cli_runner, empty_corpus):
        """W978 outcome: the recipe registry is corpus-independent.

        ``RECIPES`` is a 25-entry constant in ``src/roam/ask/recipes.py`` so
        the empty-corpus list mode legitimately emits
        ``"N workflow recipe(s) available"``. There is NO empty-state branch
        to disclose here - the inspector reads a static module-level
        constant. This test pins the W805-D verdict ("not a Pattern-2 bug")
        by asserting the verdict matches the corpus-independent shape.
        """
        result = _invoke_workflow(cli_runner, empty_corpus, json_mode=True)
        envelope = _parse_envelope(result)
        verdict = envelope["summary"]["verdict"]
        # 25 today, but kept loose so additions to RECIPES don't break this.
        assert " workflow recipe(s) available" in verdict, (
            f"list-mode verdict must name the recipe count; got {verdict!r}"
        )

    def test_empty_corpus_partial_success_propagates_from_children(self, cli_runner, empty_corpus):
        """W805-D Pattern-2 audit: ``summary.partial_success`` is present.

        cmd_workflow does not invoke child commands - it inspects the static
        RECIPES list. The compound-recipe "silent-OK on empty children" bug
        class CANNOT manifest here because there are no children to be empty.
        We only assert the auto-injected ``partial_success`` key is present;
        the value is legitimately False because the read succeeded fully.
        """
        result = _invoke_workflow(cli_runner, empty_corpus, json_mode=True)
        envelope = _parse_envelope(result)
        summary = envelope.get("summary") or {}
        assert "partial_success" in summary, (
            f"summary.partial_success key must be auto-injected; got summary keys = {sorted(summary.keys())}"
        )
        # Read on a static registry SUCCEEDED - partial_success is False here
        # and that is correct, not a silent SAFE (the verdict is true).
        assert summary["partial_success"] is False

    def test_empty_corpus_law6_verdict_standalone(self, cli_runner, empty_corpus):
        """LAW 6: the verdict line works without any other field.

        ``"N workflow recipe(s) available"`` carries the command identifier
        ("workflow") and a concrete numeric anchor.
        """
        result = _invoke_workflow(cli_runner, empty_corpus, json_mode=True)
        envelope = _parse_envelope(result)
        verdict = envelope["summary"]["verdict"]
        assert "\n" not in verdict, f"verdict embeds newline: {verdict!r}"
        assert verdict.isascii(), f"verdict not plain ASCII: {verdict!r}"
        assert "workflow" in verdict.lower(), f"LAW 6: verdict must be self-describing standalone; got {verdict!r}"

    def test_empty_corpus_no_silent_workflow_OK(self, cli_runner, empty_corpus):
        """Anti-shape: list-mode verdict must NOT be a bare 'OK'/'workflow OK'.

        The canonical Pattern-2 silent SAFE shape ("workflow OK" /
        "completed" / "non-conformant") never appears here because the
        inspector's verdict carries a concrete count. Drift guard.
        """
        result = _invoke_workflow(cli_runner, empty_corpus, json_mode=True)
        envelope = _parse_envelope(result)
        verdict = envelope["summary"]["verdict"].lower()
        forbidden = ("workflow ok", "completed", "non-conformant", "compound operation completed")
        for token in forbidden:
            assert token not in verdict, (
                f"Pattern-2 silent SAFE shape detected (verdict contains {token!r}): {verdict!r}"
            )

    def test_clean_corpus_emits_real_workflow_summary(self, cli_runner, clean_corpus):
        """Regression baseline: on a real-symbol corpus the list mode emits
        the same corpus-independent recipe enumeration (validating that
        cmd_workflow is genuinely invocation-scoped, not corpus-derived)."""
        result = _invoke_workflow(cli_runner, clean_corpus, json_mode=True)
        assert result.exit_code == 0
        envelope = _parse_envelope(result)
        recipes = envelope.get("recipes") or []
        assert recipes, "recipes payload must be non-empty on any corpus"
        # Spot-check a known recipe slug is present.
        names = {r.get("recipe") for r in recipes}
        assert "safe-delete-check" in names, f"known recipe missing from list mode; got {sorted(names)}"

    def test_detail_mode_emits_named_recipe(self, cli_runner, empty_corpus):
        """``roam workflow safe-delete-check`` emits the detail envelope
        with a corpus-independent verdict + command DAG."""
        result = _invoke_workflow(cli_runner, empty_corpus, "safe-delete-check", json_mode=True)
        envelope = _parse_envelope(result)
        assert envelope["command"] == "workflow"
        assert envelope["summary"]["verdict"] == "workflow recipe 'safe-delete-check'"
        assert envelope["summary"]["recipe"] == "safe-delete-check"
        commands = envelope.get("commands") or []
        # Recipe declares 2 commands: preflight + uses.
        assert len(commands) >= 1
        cmd_names = {c.get("cmd") for c in commands}
        assert {"preflight", "uses"} <= cmd_names

    def test_unknown_recipe_already_discloses_state(self, cli_runner, empty_corpus):
        """W1083-followup regression: unknown recipe is already structured.

        This branch ALREADY sets ``state=unknown_recipe``, ``error_code=
        UNKNOWN_RECIPE``, and emits a structured envelope on stdout despite
        the non-zero exit. Drift guard so the W1083-followup fix is not
        accidentally regressed by future cmd_workflow edits.
        """
        result = _invoke_workflow(cli_runner, empty_corpus, "no-such-recipe", json_mode=True)
        # Exit code is 2 (UsageError) - structured stdout still landed.
        envelope = _parse_envelope(result)
        summary = envelope.get("summary") or {}
        assert summary.get("state") == "unknown_recipe", (
            f"unknown-recipe state should be auto-disclosed; got {summary!r}"
        )
        assert summary.get("error_code") == "UNKNOWN_RECIPE"

    def test_next_after_known_command_emits_suggestions(self, cli_runner, empty_corpus):
        """``--next preflight`` returns the 3 canned next-commands from
        ``_NEXT_HINTS`` regardless of corpus state."""
        result = _invoke_workflow(cli_runner, empty_corpus, "--next", "preflight", json_mode=True)
        assert result.exit_code == 0
        envelope = _parse_envelope(result)
        suggestions = envelope.get("suggestions") or []
        assert len(suggestions) == 3
        assert envelope["summary"]["after"] == "preflight"

    def test_agent_contract_facts_law4_anchored(self, cli_runner, empty_corpus):
        """LAW 4 drift guard: ``agent_contract.facts`` is non-empty + each
        fact has a concrete-noun terminal (``recipes`` is in the LAW 4
        anchor vocabulary)."""
        result = _invoke_workflow(cli_runner, empty_corpus, json_mode=True)
        envelope = _parse_envelope(result)
        contract = envelope.get("agent_contract") or {}
        facts = contract.get("facts") or []
        assert isinstance(facts, list) and facts, f"agent_contract.facts must be non-empty; got {facts!r}"
        # The list-mode fact is "N workflow recipes available".
        assert any("recipes available" in f for f in facts), (
            f"facts should anchor on 'recipes' (LAW 4 vocab); got {facts!r}"
        )


# ---------------------------------------------------------------------------
# Pattern-2 milder-shape pin: --next <unknown_command> branch lacks the
# closed-enum state field (verdict text already discloses, but the contract
# surface should be uniform across the W805 cohort).
# ---------------------------------------------------------------------------


@pytest.mark.xfail(
    strict=True,
    reason=(
        "W805-D Pattern-2 milder shape: cmd_workflow.py L183-213 (--next "
        "branch) does NOT emit a closed-enum summary.state when the prior "
        "command name is unknown to _NEXT_HINTS. Verdict text ALREADY "
        "discloses the absence ('no canned next-command for `roam X`') so "
        "this is NOT a canonical silent SAFE - but partial_success stays "
        "False and summary.state is absent. Surface-uniformity gap, not a "
        "silent bug. Separate fix wave: set partial_success=True + "
        "state='no_canned_hint' on the empty-suggestions branch."
    ),
)
def test_next_after_unknown_command_partial_success(cli_runner, empty_corpus):
    """Pin: ``--next <unknown>`` should set ``partial_success=True``.

    The verdict text is already honest. The contract-surface gap is that
    the closed-enum state disclosure (used elsewhere in the W805 cohort -
    ``path_not_found``, ``unknown_recipe``, ``empty_corpus``) is missing.
    """
    result = _invoke_workflow(cli_runner, empty_corpus, "--next", "bogus-command", json_mode=True)
    envelope = _parse_envelope(result)
    summary = envelope.get("summary") or {}
    assert summary.get("partial_success") is True, (
        f"partial_success should be True on the empty-suggestions branch; got summary={summary!r}"
    )


@pytest.mark.xfail(
    strict=True,
    reason=(
        "W805-D Pattern-2 milder shape: cmd_workflow.py L183-213 does not "
        "emit a closed-enum summary.state on the --next <unknown> branch. "
        "Acceptable values: 'no_canned_hint' / 'unknown_prior_command'. "
        "Surface-uniformity gap with the W805 cohort. Separate fix wave."
    ),
)
def test_next_after_unknown_command_explicit_state(cli_runner, empty_corpus):
    """Pin: ``--next <unknown>`` should expose a closed-enum state field.

    Mirrors the ``unknown_recipe`` / ``path_not_found`` pattern used by
    the unknown-recipe branch in the same file.
    """
    result = _invoke_workflow(cli_runner, empty_corpus, "--next", "bogus-command", json_mode=True)
    envelope = _parse_envelope(result)
    summary = envelope.get("summary") or {}
    state = summary.get("state") or envelope.get("state")
    accepted = {"no_canned_hint", "unknown_prior_command", "no_suggestions"}
    assert state in accepted, (
        f"summary.state should disclose the absent-hint condition; got {state!r}; expected one of {accepted}"
    )
