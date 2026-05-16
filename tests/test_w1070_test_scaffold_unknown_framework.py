"""W1070 -- ``cmd_test_scaffold --framework`` unknown-value disclosure.

Sibling of W1063 (cmd_findings unknown-detector disclosure), W1064 (difflib
closest-match), W1068 (cmd_search unknown-kind disclosure), and W1069
(cmd_endpoints unknown-framework disclosure). The bug being fixed: ``roam
test-scaffold src/foo.py --framework garbage`` previously emitted only a
text-mode ``"Warning:"`` line and silently generated a stub with the
default framework layout. The warning never reached JSON-mode consumers
(invisible to MCP / agents) and the success verdict was indistinguishable
from a successful override -- exactly Pattern-1D silent-success on
degraded filter resolution.

Four scenarios pinned here:

1. No ``--framework`` flag -> byte-identical to pre-W1070 (no
   ``unknown_framework`` state, no ``requested_framework`` field).
2. Valid ``--framework`` (e.g. ``unittest`` for Python) -> normal stub
   generation, no ``unknown_framework`` state.
3. Unknown ``--framework`` -> ``state="unknown_framework"``,
   ``partial_success=True``, ``requested_framework`` echoed,
   ``known_frameworks`` enumerated, ``agent_contract.facts`` anchored on
   ``frameworks`` (LAW 4), and a difflib closest-match suggestion when
   within cutoff 0.6.
4. LAW 4 anchor terminal verification on the agent_contract facts.
"""

from __future__ import annotations

import pytest

from tests.conftest import (
    assert_json_envelope,
    git_init,
    index_in_process,
    invoke_cli,
    parse_json_output,
)


@pytest.fixture
def scaffold_project(tmp_path):
    proj = tmp_path / "w1070_scaffold_proj"
    proj.mkdir()
    (proj / ".gitignore").write_text(".roam/\n")
    (proj / "calculator.py").write_text(
        "def add(a, b):\n"
        "    return a + b\n"
        "\n"
        "def subtract(a, b):\n"
        "    return a - b\n"
        "\n"
        "class MathEngine:\n"
        "    def divide(self, a, b):\n"
        "        return a / b\n"
    )
    git_init(proj)
    index_in_process(proj)
    return proj


# ---------------------------------------------------------------------------
# Scenario 1: no --framework flag -> byte-identical (no unknown state leak).
# ---------------------------------------------------------------------------


def test_no_framework_flag_does_not_emit_unknown_state(cli_runner, scaffold_project, monkeypatch):
    """Without ``--framework``, the W1070 disclosure must be a no-op.
    No ``unknown_framework`` state, no ``requested_framework`` /
    ``known_frameworks`` keys."""
    monkeypatch.chdir(scaffold_project)
    result = invoke_cli(
        cli_runner,
        ["test-scaffold", "calculator.py"],
        cwd=scaffold_project,
        json_mode=True,
    )
    data = parse_json_output(result, "test-scaffold")
    summary = data["summary"]
    assert summary.get("state") != "unknown_framework"
    assert "requested_framework" not in summary
    assert "known_frameworks" not in summary
    # The normal scaffold path emits a scaffolded count.
    assert summary.get("scaffolded", 0) > 0


def test_no_framework_text_mode_unchanged(cli_runner, scaffold_project, monkeypatch):
    """Text mode without ``--framework`` is the standard scaffold output --
    no `unknown framework` chatter, no `Known frameworks:` line."""
    monkeypatch.chdir(scaffold_project)
    result = invoke_cli(cli_runner, ["test-scaffold", "calculator.py"], cwd=scaffold_project)
    out = result.output
    assert "unknown framework" not in out.lower()
    assert "Known frameworks:" not in out


# ---------------------------------------------------------------------------
# Scenario 2: valid --framework -> normal stub generation.
# ---------------------------------------------------------------------------


def test_valid_framework_normal_stub_generation(cli_runner, scaffold_project, monkeypatch):
    """``--framework unittest`` on a Python file is valid and emits a
    normal scaffold envelope. No ``unknown_framework`` state."""
    monkeypatch.chdir(scaffold_project)
    result = invoke_cli(
        cli_runner,
        ["test-scaffold", "calculator.py", "--framework", "unittest"],
        cwd=scaffold_project,
        json_mode=True,
    )
    data = parse_json_output(result, "test-scaffold")
    summary = data["summary"]
    assert summary.get("state") != "unknown_framework"
    assert summary.get("framework") == "unittest"
    assert summary.get("scaffolded", 0) > 0


def test_default_python_framework_is_pytest(cli_runner, scaffold_project, monkeypatch):
    """``--framework pytest`` (the default for Python) is valid and emits a
    normal scaffold envelope."""
    monkeypatch.chdir(scaffold_project)
    result = invoke_cli(
        cli_runner,
        ["test-scaffold", "calculator.py", "--framework", "pytest"],
        cwd=scaffold_project,
        json_mode=True,
    )
    data = parse_json_output(result, "test-scaffold")
    summary = data["summary"]
    assert summary.get("state") != "unknown_framework"


# ---------------------------------------------------------------------------
# Scenario 3: unknown --framework -> state=unknown_framework, partial_success,
# known_frameworks enumerated, closest-match suggestion.
# ---------------------------------------------------------------------------


def test_unknown_framework_json_envelope_shape(cli_runner, scaffold_project, monkeypatch):
    """``--framework garblargle`` triggers the W1070 disclosure envelope."""
    monkeypatch.chdir(scaffold_project)
    result = invoke_cli(
        cli_runner,
        ["test-scaffold", "calculator.py", "--framework", "garblargle"],
        cwd=scaffold_project,
        json_mode=True,
    )
    data = parse_json_output(result, "test-scaffold")
    assert_json_envelope(data, "test-scaffold")
    summary = data["summary"]
    assert summary["state"] == "unknown_framework"
    assert summary["partial_success"] is True
    assert summary["requested_framework"] == "garblargle"
    assert summary["scaffolded"] == 0
    assert isinstance(summary["known_frameworks"], list)
    # For Python, the known frameworks are pytest + unittest.
    assert "pytest" in summary["known_frameworks"]
    assert "unittest" in summary["known_frameworks"]
    # known_frameworks is sorted (deterministic disclosure surface).
    assert summary["known_frameworks"] == sorted(summary["known_frameworks"])
    # Verdict names the unknown value explicitly.
    assert "garblargle" in summary["verdict"]
    assert "unknown" in summary["verdict"].lower()


def test_unknown_framework_text_mode_lists_known(cli_runner, scaffold_project, monkeypatch):
    """Text mode (non-JSON) discloses the known-frameworks set so a human
    reader sees the same information as an agent reading JSON."""
    monkeypatch.chdir(scaffold_project)
    result = invoke_cli(
        cli_runner,
        ["test-scaffold", "calculator.py", "--framework", "garblargle"],
        cwd=scaffold_project,
    )
    assert "unknown framework" in result.output.lower()
    assert "Known frameworks:" in result.output
    # The two canonical Python frameworks should appear.
    assert "pytest" in result.output
    assert "unittest" in result.output


def test_unknown_framework_close_match_suggests_correction(cli_runner, scaffold_project, monkeypatch):
    """A typo close to a real framework (``pytset`` -> ``pytest``) emits a
    difflib-derived correction in the verdict (cutoff 0.6, n=2)."""
    monkeypatch.chdir(scaffold_project)
    result = invoke_cli(
        cli_runner,
        ["test-scaffold", "calculator.py", "--framework", "pytset"],
        cwd=scaffold_project,
        json_mode=True,
    )
    data = parse_json_output(result, "test-scaffold")
    verdict = data["summary"]["verdict"]
    assert "did you mean" in verdict.lower(), f"expected 'did you mean' suggestion in verdict, got: {verdict!r}"
    assert "pytest" in verdict


def test_unknown_framework_text_mode_close_match(cli_runner, scaffold_project, monkeypatch):
    """Text mode surfaces the suggestion via the verdict line as well."""
    monkeypatch.chdir(scaffold_project)
    result = invoke_cli(
        cli_runner,
        ["test-scaffold", "calculator.py", "--framework", "pytset"],
        cwd=scaffold_project,
    )
    assert result.exit_code == 0
    assert "did you mean" in result.output.lower()
    assert "pytest" in result.output


# ---------------------------------------------------------------------------
# Scenario 4: LAW 4 anchor terminal verification.
# ---------------------------------------------------------------------------


def test_unknown_framework_agent_contract_law4_anchored(cli_runner, scaffold_project, monkeypatch):
    """The agent_contract facts must terminal on the ``frameworks``
    concrete-noun anchor -- see LAW 4 in CLAUDE.md."""
    monkeypatch.chdir(scaffold_project)
    result = invoke_cli(
        cli_runner,
        ["test-scaffold", "calculator.py", "--framework", "garblargle"],
        cwd=scaffold_project,
        json_mode=True,
    )
    data = parse_json_output(result, "test-scaffold")
    facts = data["agent_contract"]["facts"]
    assert isinstance(facts, list) and facts
    # Both facts terminal on ``frameworks`` (concrete-noun-anchored per LAW 4).
    for fact in facts:
        terminal = fact.strip().split()[-1].rstrip(",.;:!?)")
        assert terminal == "frameworks", f"fact {fact!r} terminal {terminal!r} not anchored on 'frameworks'"
