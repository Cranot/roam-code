"""W805-QQ -- empty-corpus Pattern-2 + Pattern-1C smoke test on ``roam visualize``.

Forty-third-in-batch W805 sweep. Graph-export surface -- empty-graph likely
Pattern-1C (empty stdout crash) or Pattern-2 (silent SAFE) per W805-NN agent
recommendation. ``cmd_visualize`` is the canonical Mermaid/DOT architecture
diagram generator.

Scope
-----

``cmd_visualize`` (``src/roam/commands/cmd_visualize.py``) is the graph-export
visualisation command. Output formats: ``mermaid`` (default) and ``dot``.
The command builds a symbol or file graph then renders to text/JSON.

W978 first-hypothesis discipline
--------------------------------

Hypothesis: "graph-export on empty corpus -> empty SVG/DOT/JSON likely
silent; Pattern-1C empty stdout crash possible on MCP wrapper consumption".

W978 probed twice. Findings:

* **No Pattern-1C crash.** ``cmd_visualize.py:372-385`` has a defensive
  empty-graph branch: emits ``"VERDICT: EMPTY -- no symbols in index"``
  (text) or a structured envelope (json). Non-empty stdout in both modes.
  **No bug on Pattern-1C axis.**

* **Pattern-2 silent-SAFE on the empty-graph branch.** Multiple Pattern-2
  symptoms in the empty-corpus envelope at ``cmd_visualize.py:373-382``:

  - ``summary.verdict = "EMPTY"`` -- LAW 6 violation: a single-token
    verdict does NOT work without other fields. The agent reading only the
    verdict cannot tell WHAT is empty (the project? the graph? the index?
    the focus filter?) and cannot tell what to do next. Compare to the
    non-empty path's verdict ("visualize rendered mermaid diagram with N
    nodes...") which IS standalone.
  - ``summary.partial_success = False`` (auto-injected default) -- the
    command failed to produce its analytical product (the diagram is
    empty string), but reports normal success. Pattern-2 silent-SAFE:
    a verdict indistinguishable from a fully-resolved success.
  - ``summary.state`` is absent -- the Pattern-2 explicit-absence pattern
    requires the empty-graph branch to disclose ``state="index_empty"`` or
    ``"not_indexed"``, distinguishing intentional absence (empty repo) from
    broken absence (index not built).
  - ``agent_contract.next_commands = []`` -- no copy-pasteable next step.
    CONSTRAINT 12 says when the command names a follow-up, it must be
    literal ``roam <subcommand>``. Here NO follow-up is named at all
    despite there being an obvious one (``roam init``).
  - ``agent_contract.facts[0] = "EMPTY"`` -- LAW 4 violation: terminal
    token ``EMPTY`` is not in the concrete-noun anchor set
    (`tests/test_law4_lint.py:_CONCRETE_NOUN_ANCHORS`). Compare ``"0
    nodes"`` and ``"0 edges"`` (facts[1] / facts[2]) which DO anchor
    correctly on ``nodes`` / ``edges``.

* **Empty stdout NEVER happens.** ``len(stdout) == 602`` bytes in JSON
  mode on the empty corpus, well above any wrapper-bridge ``json.loads("")``
  crash threshold. The Pattern-1C class is sealed for this command.

* **Same envelope on focus + dot.** ``--focus nonexistent`` and
  ``--format dot`` on the empty corpus reach the same early-return at
  line 372 BEFORE the focus-resolver runs. So Pattern-1, variant D
  (silent success on degraded resolution) does NOT apply -- the empty-graph
  guard short-circuits the resolver chain.

Conclusion: Pattern-1C is sealed (defensive empty-graph branch at line 372).
The real bug is Pattern-2 silent-SAFE with FOUR sub-symptoms in the
empty-graph envelope. Pinned xfail-strict below.

REAL BUG pinned (Pattern-2 silent-SAFE)
---------------------------------------

``cmd_visualize.py:373-382`` -- the empty-graph JSON envelope emits

    summary = {"verdict": "EMPTY", "nodes": 0, "edges": 0}

with no ``state`` disclosure, no ``partial_success=True``, no
``next_commands``, and a LAW-6-violating bare-token verdict.

Fix template:

    summary={
        "verdict": "visualize cannot render: 0 symbols in index -- run roam init",
        "state": "index_empty",
        "partial_success": True,
        "nodes": 0, "edges": 0,
    },
    diagram="",
    agent_contract={
        "facts": ["0 symbols indexed", "0 nodes", "0 edges"],
        "next_commands": ["roam init  # build the index first"],
    },

LAW 4: ``"0 symbols indexed"`` anchors on ``indexed`` (past participle, in
the anchor set). LAW 6: the verdict carries the next command. Pattern-2:
``state`` discloses explicit absence; ``partial_success`` flags the
non-rendered diagram.

Sweep brief: W805-QQ (Wave805-QQ, forty-third-in-batch).
"""

from __future__ import annotations

import json as _json
import sys
from pathlib import Path

import pytest
from click.testing import CliRunner

sys.path.insert(0, str(Path(__file__).parent))
from conftest import git_init, index_in_process, invoke_cli  # noqa: E402

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def empty_corpus(tmp_path, monkeypatch):
    """Indexed git repo with NO source files (only README.txt + .gitignore).

    The indexer runs cleanly but produces zero symbols/edges; the symbol
    graph is empty so ``cmd_visualize`` hits its line-372 early-return.
    """
    repo = tmp_path / "empty-visualize-repo"
    repo.mkdir()
    (repo / ".gitignore").write_text(".roam/\n", encoding="utf-8")
    (repo / "README.txt").write_text("hello", encoding="utf-8")
    git_init(repo)
    monkeypatch.chdir(repo)
    out, rc = index_in_process(repo, "--force")
    assert rc == 0, f"roam index failed:\n{out}"
    return repo


@pytest.fixture
def clean_corpus(tmp_path, monkeypatch):
    """Indexed git repo with two Python files producing real nodes/edges."""
    repo = tmp_path / "clean-visualize-repo"
    repo.mkdir()
    (repo / ".gitignore").write_text(".roam/\n", encoding="utf-8")
    (repo / "lib.py").write_text(
        "def helper(x: int) -> int:\n    return x + 1\n",
        encoding="utf-8",
    )
    (repo / "main.py").write_text(
        "from lib import helper\n\ndef run() -> int:\n    return helper(1)\n",
        encoding="utf-8",
    )
    git_init(repo)
    monkeypatch.chdir(repo)
    out, rc = index_in_process(repo, "--force")
    assert rc == 0, f"roam index failed:\n{out}"
    return repo


def _invoke_visualize(runner: CliRunner, repo: Path, *args: str, json_mode: bool = True):
    cli_args: list[str] = []
    if json_mode:
        cli_args.append("--json")
    cli_args.append("visualize")
    cli_args.extend(args)
    return invoke_cli(runner, cli_args, cwd=repo)


def _parse_envelope(result) -> dict:
    raw = (getattr(result, "stdout", None) or result.output).lstrip()
    assert raw.startswith("{"), f"expected JSON envelope, got:\n{result.output}"
    decoder = _json.JSONDecoder()
    obj, _end = decoder.raw_decode(raw)
    return obj


# ---------------------------------------------------------------------------
# Existence gate
# ---------------------------------------------------------------------------


def test_command_exists_or_skip():
    """``cmd_visualize.visualize`` is importable + a Click command."""
    try:
        from roam.commands.cmd_visualize import visualize
    except ImportError:
        pytest.skip("cmd_visualize not importable -- skipping W805-QQ smoke test")
    import click

    assert isinstance(visualize, click.Command), f"visualize must be a Click command; got {type(visualize)!r}"


# ---------------------------------------------------------------------------
# SMOKE -- always-on contracts (sealed today)
# ---------------------------------------------------------------------------


class TestVisualizeEmptyCorpusSealed:
    """Properties already satisfied by the current cmd_visualize envelope."""

    def test_empty_corpus_no_crash(self, empty_corpus):
        """Empty corpus + visualize exits 0 without crashing."""
        runner = CliRunner()
        result = _invoke_visualize(runner, empty_corpus, json_mode=True)
        assert result.exit_code == 0, (
            f"expected exit 0 on empty corpus; got {result.exit_code}\noutput:\n{result.output}"
        )
        # Sanity: no unhandled exception traceback in output.
        assert "Traceback" not in result.output, f"unexpected traceback in empty-corpus output:\n{result.output}"

    def test_empty_corpus_envelope_has_verdict(self, empty_corpus):
        """Empty-corpus envelope carries ``command=visualize`` + non-empty
        ``summary.verdict`` string."""
        runner = CliRunner()
        result = _invoke_visualize(runner, empty_corpus, json_mode=True)
        env = _parse_envelope(result)
        assert env["command"] == "visualize"
        verdict = env.get("summary", {}).get("verdict") or ""
        assert isinstance(verdict, str) and verdict, f"summary.verdict must be a non-empty string, got {verdict!r}"

    def test_empty_corpus_no_empty_stdout(self, empty_corpus):
        """Pattern-1C class: stdout must NOT be empty in --json mode.

        The MCP wrapper-bridge feeds CLI stdout to ``json.loads()``; an
        empty-stdout crash here is the canonical Pattern-1, variant C
        failure mode. cmd_visualize.py:372-385 emits a structured envelope
        on the empty-graph branch -- this guard keeps that invariant.
        """
        runner = CliRunner()
        result = _invoke_visualize(runner, empty_corpus, json_mode=True)
        assert result.output.strip(), "Pattern-1C: --json mode emitted empty stdout on empty corpus"

    def test_empty_corpus_no_empty_stdout_dot(self, empty_corpus):
        """Pattern-1C class extended to ``--format dot``: still non-empty stdout."""
        runner = CliRunner()
        result = _invoke_visualize(runner, empty_corpus, "--format", "dot", json_mode=True)
        assert result.output.strip(), "Pattern-1C: --json --format dot emitted empty stdout on empty corpus"

    def test_empty_corpus_no_empty_stdout_focus(self, empty_corpus):
        """Pattern-1C class extended to ``--focus``: still non-empty stdout.

        The empty-graph guard at line 372 short-circuits the focus-resolver
        path entirely, so even ``--focus nonexistent`` reaches the same
        envelope-emitting branch rather than dropping into the resolver's
        ClickException -> empty-stderr path.
        """
        runner = CliRunner()
        result = _invoke_visualize(runner, empty_corpus, "--focus", "nonexistent_xyz", json_mode=True)
        assert result.output.strip(), "Pattern-1C: --json --focus nonexistent emitted empty stdout on empty corpus"

    def test_empty_corpus_diagram_empty_string(self, empty_corpus):
        """Empty corpus -> ``diagram`` field is an empty string (not None / missing).

        Drift guard: downstream consumers (CI graph-export pipelines) rely
        on ``diagram`` always being a string for shape-stable consumption.
        """
        runner = CliRunner()
        result = _invoke_visualize(runner, empty_corpus, json_mode=True)
        env = _parse_envelope(result)
        assert "diagram" in env, f"envelope must always carry 'diagram' key; got keys={sorted(env.keys())}"
        assert env["diagram"] == "", f"empty corpus should yield empty diagram string; got {env['diagram']!r}"

    def test_empty_corpus_partial_success_key_present(self, empty_corpus):
        """Drift guard: auto-injected ``summary.partial_success`` key present."""
        runner = CliRunner()
        result = _invoke_visualize(runner, empty_corpus, json_mode=True)
        env = _parse_envelope(result)
        assert "partial_success" in env.get("summary", {}), (
            "summary.partial_success key must be auto-injected; got summary keys "
            f"= {sorted(env.get('summary', {}).keys())}"
        )

    def test_clean_corpus_emits_real_graph(self, clean_corpus):
        """Non-empty corpus emits a real diagram with >0 nodes."""
        runner = CliRunner()
        result = _invoke_visualize(runner, clean_corpus, json_mode=True)
        assert result.exit_code == 0, f"clean-corpus failed: {result.output}"
        env = _parse_envelope(result)
        assert env["summary"]["nodes"] > 0, f"clean corpus should yield >0 nodes; got summary={env['summary']!r}"
        # Verdict on the non-empty path names the analytical product per LAW 4 (W17.3).
        assert env["summary"]["verdict"].startswith("visualize rendered"), (
            f"non-empty-corpus verdict should name the rendered diagram; got {env['summary']['verdict']!r}"
        )
        assert env["diagram"], "non-empty corpus should yield non-empty diagram"


# ---------------------------------------------------------------------------
# REAL BUG -- xfail-strict pins. Pattern-2 silent-SAFE in empty-graph envelope.
# Fix wave separate from W805 accumulate-only constraint.
# ---------------------------------------------------------------------------


@pytest.mark.xfail(
    strict=True,
    reason=(
        "W805-QQ Pattern-2: cmd_visualize.py:373-382 empty-graph envelope "
        "emits summary.verdict='EMPTY' -- a bare token that violates LAW 6 "
        "(verdict must work without any other field). The agent reading "
        "only the verdict cannot tell WHAT is empty (project? graph? index? "
        "focus filter?). Fix: verdict='visualize cannot render: 0 symbols "
        "in index -- run roam init'. Pinned for separate fix wave."
    ),
)
def test_empty_corpus_law6_verdict_standalone(empty_corpus):
    """LAW 6: the empty-corpus verdict must work without other fields.

    The verdict is the single field many agents read; ``"EMPTY"`` does not
    name what's empty, doesn't name the next action, and doesn't disclose
    why. Compare ``test_validate_no_bundle_state_explicit`` in W805-NN
    where the no-bundle verdict carries ``"run roam pr-bundle init"`` --
    the LAW-6-correct shape.
    """
    runner = CliRunner()
    result = _invoke_visualize(runner, empty_corpus, json_mode=True)
    env = _parse_envelope(result)
    verdict = env["summary"]["verdict"]
    assert "\n" not in verdict, f"verdict embeds newline: {verdict!r}"
    # LAW 6: verdict carries the next action.
    assert "roam init" in verdict.lower() or "roam index" in verdict.lower(), (
        f"LAW 6: empty-corpus verdict must name the next command; got {verdict!r}"
    )


@pytest.mark.xfail(
    strict=True,
    reason=(
        "W805-QQ Pattern-2 explicit-absence: cmd_visualize.py:373-382 empty-graph "
        "envelope does NOT disclose summary.state. Pattern-2 requires "
        "state='index_empty' (or 'not_indexed') so agents can distinguish "
        "intentional absence (empty repo) from broken absence (index not "
        "built). Pinned for separate fix wave."
    ),
)
def test_empty_corpus_state_explicit(empty_corpus):
    """Pattern-2 explicit-absence: empty corpus discloses state."""
    runner = CliRunner()
    result = _invoke_visualize(runner, empty_corpus, json_mode=True)
    env = _parse_envelope(result)
    state = env.get("summary", {}).get("state")
    assert state in {"index_empty", "not_indexed", "empty_index"}, (
        f"empty corpus must disclose summary.state in {{index_empty, not_indexed, empty_index}}; got {state!r}"
    )


@pytest.mark.xfail(
    strict=True,
    reason=(
        "W805-QQ Pattern-2 silent-SAFE: cmd_visualize.py:373-382 empty-graph "
        "envelope reports summary.partial_success=False (auto-injected default) "
        "but the command FAILED to produce its analytical product (diagram is "
        "''). Pattern-2 mandates partial_success=True on degraded output. "
        "Pinned for separate fix wave."
    ),
)
def test_empty_corpus_partial_success_set(empty_corpus):
    """Pattern-2 silent-SAFE: empty corpus sets ``partial_success=True``."""
    runner = CliRunner()
    result = _invoke_visualize(runner, empty_corpus, json_mode=True)
    env = _parse_envelope(result)
    assert env["summary"].get("partial_success") is True, (
        f"empty corpus must set summary.partial_success=True (Pattern-2); got summary={env['summary']!r}"
    )


@pytest.mark.xfail(
    strict=True,
    reason=(
        "W805-QQ CONSTRAINT 12: cmd_visualize.py:373-382 empty-graph envelope "
        "emits agent_contract.next_commands=[] -- no copy-pasteable next step. "
        "On an empty corpus the obvious next command is 'roam init' to build "
        "the index; the envelope must name it. Pinned for separate fix wave."
    ),
)
def test_no_silent_empty_graph_export_on_empty(empty_corpus):
    """Empty-corpus envelope names the next command (CONSTRAINT 12)."""
    runner = CliRunner()
    result = _invoke_visualize(runner, empty_corpus, json_mode=True)
    env = _parse_envelope(result)
    next_cmds = env.get("agent_contract", {}).get("next_commands") or []
    assert next_cmds, f"agent_contract.next_commands must name a follow-up on empty corpus; got {next_cmds!r}"
    first = next_cmds[0]
    assert first.startswith("roam "), f"CONSTRAINT 12: next_command must be literal 'roam ...'; got {first!r}"
