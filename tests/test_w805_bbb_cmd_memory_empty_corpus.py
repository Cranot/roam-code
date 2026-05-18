r"""W805-BBB -- empty-corpus Pattern-2 smoke test on ``roam memory list``.

Fifty-fourth-in-batch W805 sweep. Substrate state-reader peer of
``cmd_lease`` (W805-XX) -- ``roam memory list`` enumerates the
repo-local agent memory records under ``.roam/memory.jsonl`` (R19, per
CLAUDE.md substrate: "memory/ (portable agent memory.jsonl)").

Scope
-----

``cmd_memory`` (``src/roam/commands/cmd_memory.py``) exposes three
subcommands: ``add`` / ``list`` / ``relevant``. This sweep is
READ-ONLY -- only ``list`` and ``relevant`` are exercised on
no-state corpora; ``add`` mutates disk state and is out-of-scope
for the empty-corpus probe (per W805 hard constraint: DO NOT trigger
state-mutating subcommands).

W978 first-hypothesis discipline
--------------------------------

Hypothesis: "substrate state-reader on empty corpus -- likely
Pattern-2 silent-divergence between missing-file and empty-file
branches, same shape as ``cmd_lease`` W805-XX".

W978 probed THREE corpora:

* **Missing ``.roam/memory.jsonl`` file (W978-VERIFIED OK)**: the
  early return at ``cmd_memory.py:226-252`` (memory_list) and
  ``cmd_memory.py:321-353`` (memory_relevant) produces an explicit
  ``state="no_memory"`` envelope with a LAW-6 verdict naming the
  recovery command in prose, AND a populated
  ``agent_contract.next_commands=["roam memory add ..."]``. This
  is the EXEMPLARY branch -- Pattern-2 + LAW-6 + CONSTRAINT 12 all
  satisfied.

* **Empty ``.roam/memory.jsonl`` file (0 bytes) (W978-VERIFIED BUG)**:
  ``path.exists()`` returns True so the early return is bypassed.
  ``list_memory`` walks the file, yields 0 entries (the stream is
  empty), and the envelope sets ``state="ok"`` with
  ``total=0``, verdict ``"0 memory entries"``, and an EMPTY
  ``agent_contract.next_commands=[]``. The structured next-step
  signal is lost -- exactly the cmd_lease W805-XX shape.

* **Whitespace-only ``.roam/memory.jsonl`` (W978-VERIFIED BUG)**:
  same shape as the 0-byte case -- ``_parse_line`` skips blank lines,
  the stream yields nothing, envelope reports ``state="ok"``.

Divergence: three empty-corpus paths produce divergent envelope
shapes:

  | path                                    | state      | next_commands populated |
  | --------------------------------------- | ---------- | ----------------------- |
  | file MISSING (early return)             | no_memory  | YES (1 entry)           |
  | file EXISTS + 0 bytes (walked)          | ok         | NO (empty)              |
  | file EXISTS + whitespace only (walked)  | ok         | NO (empty)              |

All three represent "0 memory entries known to this repo" but the
structured agent-contract diverges. An agent that auto-routes on
``next_commands[0]`` gets a recovery hint on one path and a dead
envelope on the other -- the exact same Pattern-2-family failure
as ``cmd_lease``'s ``no_leases`` vs ``no_matches`` divergence and
``cmd_replay``'s ``state="ok"`` on empty ledger.

This is also Pattern-1 variant D (silent success on degraded
resolution): ``state="ok"`` is indistinguishable from a fully-
resolved success despite the empty-file degraded state.

REAL BUG pinned (Pattern-2 silent-divergence on empty-file path)
----------------------------------------------------------------

``cmd_memory.py:254-289`` (memory_list non-early-return branch) and
``cmd_memory.py:355-393`` (memory_relevant non-early-return branch):
when ``path.exists()`` is True but the file is empty / whitespace-
only / all-corrupt-lines, ``list_memory`` yields zero entries; the
code emits ``state="ok"`` (success) and omits the ``agent_contract``
hand-anchor. The sibling missing-file branch at lines 226-252 hand-
anchors a ``next_commands=["roam memory add --kind fact --subject
TOPIC --body TEXT"]`` on the equivalent empty-corpus condition.

Fix template (analogous to the missing-file branch handling):

    if total == 0:
        verdict = "no memory yet -- run `roam memory add` to store the first entry"
        envelope = json_envelope(
            "memory-list",
            summary={"verdict": verdict, "partial_success": False,
                     "state": "no_memory", "total": 0},
            ...
            agent_contract={
                "facts": ["0 memory entries (file present but empty)"],
                "next_commands": ["roam memory add --kind fact --subject TOPIC --body TEXT"],
            },
        )

LAW 4 ``entries`` is in the concrete-noun anchor set. LAW 6: the
verdict already names the recovery command in prose on the
missing-file path; the empty-file path should mirror it.
CONSTRAINT 12: the structured ``next_commands`` mirrors the verdict
prose, giving agents a copy-pasteable recovery action regardless of
which axis they consume.

Sweep brief: W805-BBB (Wave805-BBB, fifty-fourth-in-batch).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from click.testing import CliRunner

sys.path.insert(0, str(Path(__file__).parent))
from conftest import git_init, invoke_cli  # noqa: E402

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def bare_project(tmp_path, monkeypatch):
    """Bare git-init project with NO ``.roam/memory.jsonl`` file at all.

    Drives the early-return branch at ``cmd_memory.py:226-252``
    (memory_list) where ``memory_path(root).exists()`` is False.
    """
    proj = tmp_path / "bare-memory-proj"
    proj.mkdir()
    (proj / ".gitignore").write_text(".roam/\n", encoding="utf-8")
    (proj / "app.py").write_text("def f():\n    return 0\n", encoding="utf-8")
    git_init(proj)
    monkeypatch.chdir(proj)
    return proj


@pytest.fixture
def empty_memory_file_project(tmp_path, monkeypatch):
    """Project with ``.roam/memory.jsonl`` created but 0 bytes (empty).

    Drives the populated-walk branch at ``cmd_memory.py:254-289`` where
    ``path.exists()`` is True but ``list_memory`` yields no entries.
    """
    proj = tmp_path / "empty-memory-file-proj"
    proj.mkdir()
    (proj / ".gitignore").write_text(".roam/\n", encoding="utf-8")
    (proj / "app.py").write_text("def f():\n    return 0\n", encoding="utf-8")
    git_init(proj)
    monkeypatch.chdir(proj)
    (proj / ".roam").mkdir()
    (proj / ".roam" / "memory.jsonl").write_text("", encoding="utf-8")
    return proj


@pytest.fixture
def whitespace_memory_file_project(tmp_path, monkeypatch):
    """Project with ``.roam/memory.jsonl`` containing only blank lines.

    Drives the same populated-walk branch -- ``_parse_line`` skips
    blank lines so the stream yields no entries.
    """
    proj = tmp_path / "ws-memory-file-proj"
    proj.mkdir()
    (proj / ".gitignore").write_text(".roam/\n", encoding="utf-8")
    (proj / "app.py").write_text("def f():\n    return 0\n", encoding="utf-8")
    git_init(proj)
    monkeypatch.chdir(proj)
    (proj / ".roam").mkdir()
    (proj / ".roam" / "memory.jsonl").write_text("\n\n   \n", encoding="utf-8")
    return proj


def _parse_envelope(result) -> dict:
    raw = (getattr(result, "stdout", None) or result.output).lstrip()
    assert raw.startswith("{"), f"expected JSON envelope, got:\n{result.output}"
    decoder = json.JSONDecoder()
    obj, _end = decoder.raw_decode(raw)
    return obj


# ---------------------------------------------------------------------------
# Existence gate
# ---------------------------------------------------------------------------


def test_command_exists_or_skip():
    """``cmd_memory.memory_group`` is importable + a Click group."""
    try:
        from roam.commands.cmd_memory import memory_group
    except ImportError:
        pytest.skip("cmd_memory not importable -- skipping W805-BBB smoke test")
    import click

    assert isinstance(memory_group, click.Group), f"memory_group must be a Click Group; got {type(memory_group)!r}"
    # All three documented subcommands must be present.
    expected = {"add", "list", "relevant"}
    actual = set(memory_group.commands.keys())
    assert expected <= actual, f"memory group missing subcommands: expected {expected}, got {actual}"


# ---------------------------------------------------------------------------
# SMOKE -- properties satisfied today on the ``state="no_memory"`` early-
# return path. Regression guard.
# ---------------------------------------------------------------------------


class TestMemoryListMissingFileSealed:
    """Pin the current shape of the file-missing empty-corpus path.

    Today: ``state="no_memory"`` + LAW-6 verdict in prose + populated
    ``agent_contract.next_commands``. The EXEMPLARY branch -- regression
    guard.
    """

    def test_empty_corpus_no_crash(self, bare_project):
        """Missing ``.roam/memory.jsonl`` -> exit 0 without traceback."""
        runner = CliRunner()
        result = invoke_cli(
            runner,
            ["memory", "list"],
            cwd=bare_project,
            json_mode=True,
        )
        assert result.exit_code == 0, (
            f"expected exit 0 on missing-file empty corpus; got {result.exit_code}\n{result.output}"
        )
        assert "Traceback" not in result.output, f"unexpected traceback in missing-file output:\n{result.output}"

    def test_missing_file_state_explicit(self, bare_project):
        """Pattern-2 explicit-absence: file-missing discloses ``state="no_memory"``."""
        runner = CliRunner()
        result = invoke_cli(
            runner,
            ["memory", "list"],
            cwd=bare_project,
            json_mode=True,
        )
        env = _parse_envelope(result)
        state = env.get("summary", {}).get("state")
        assert state == "no_memory", f"file-missing must disclose summary.state='no_memory'; got {state!r}"

    def test_empty_corpus_partial_success_set(self, bare_project):
        """Pattern-2: file-missing is a clean known-empty state, NOT partial.

        Mirror cmd_lease W805-XX shape: absent ``.roam/memory.jsonl``
        is the *expected* shape pre-first-add, not a degraded analytical
        product. ``partial_success=False`` here.
        """
        runner = CliRunner()
        result = invoke_cli(
            runner,
            ["memory", "list"],
            cwd=bare_project,
            json_mode=True,
        )
        env = _parse_envelope(result)
        assert env["summary"].get("partial_success") is False, (
            f"file-missing should be clean-empty (partial_success=False); got summary={env['summary']!r}"
        )
        assert env["summary"].get("total") == 0, f"file-missing must disclose summary.total=0; got {env['summary']!r}"

    def test_law6_verdict_standalone(self, bare_project):
        """LAW 6: file-missing verdict works without any other field.

        The verdict "no memory yet -- run `roam memory add` ..."
        names both the state (no memory) AND the recovery command in
        prose -- an agent reading only the verdict can act on it.
        """
        runner = CliRunner()
        result = invoke_cli(
            runner,
            ["memory", "list"],
            cwd=bare_project,
            json_mode=True,
        )
        env = _parse_envelope(result)
        verdict = env["summary"]["verdict"]
        assert "\n" not in verdict, f"verdict embeds newline: {verdict!r}"
        v_lower = verdict.lower()
        assert "no memory" in v_lower or "0 memory" in v_lower or "empty" in v_lower, (
            f"LAW 6: file-missing verdict must name the empty state; got {verdict!r}"
        )

    def test_missing_file_command_field(self, bare_project):
        """Envelope identifies itself as ``memory-list`` (subcommand-named)."""
        runner = CliRunner()
        result = invoke_cli(
            runner,
            ["memory", "list"],
            cwd=bare_project,
            json_mode=True,
        )
        env = _parse_envelope(result)
        assert env.get("command") == "memory-list", (
            f"envelope command field must be 'memory-list'; got {env.get('command')!r}"
        )
        assert env.get("entries") == [], f"empty corpus must emit entries=[]; got {env.get('entries')!r}"

    def test_missing_file_next_commands_populated(self, bare_project):
        """CONSTRAINT 12: file-missing populates ``next_commands``.

        The EXEMPLARY branch -- regression guard that the existing
        ``agent_contract`` hand-anchor stays in place.
        """
        runner = CliRunner()
        result = invoke_cli(
            runner,
            ["memory", "list"],
            cwd=bare_project,
            json_mode=True,
        )
        env = _parse_envelope(result)
        next_cmds = env.get("agent_contract", {}).get("next_commands") or []
        assert next_cmds, (
            f"file-missing must populate agent_contract.next_commands (CONSTRAINT 12, sealed); got {next_cmds!r}"
        )
        for nc in next_cmds:
            assert nc.startswith("roam "), f"CONSTRAINT 12: next_command must be literal 'roam ...'; got {nc!r}"
        assert any("memory add" in nc for nc in next_cmds), (
            f"file-missing next_commands should include 'roam memory add'; got {next_cmds!r}"
        )


# ---------------------------------------------------------------------------
# Empty-file walks: the populated-walk branch on degenerate input.
# Currently emits state="ok" -- documented in xfail-strict below.
# ---------------------------------------------------------------------------


def test_empty_file_no_crash(empty_memory_file_project):
    """0-byte ``.roam/memory.jsonl`` -> exit 0 without traceback."""
    runner = CliRunner()
    result = invoke_cli(
        runner,
        ["memory", "list"],
        cwd=empty_memory_file_project,
        json_mode=True,
    )
    assert result.exit_code == 0, f"expected exit 0 on empty-file corpus; got {result.exit_code}\n{result.output}"
    assert "Traceback" not in result.output, f"unexpected traceback in empty-file output:\n{result.output}"


def test_whitespace_file_no_crash(whitespace_memory_file_project):
    """Whitespace-only memory file -> exit 0 without traceback."""
    runner = CliRunner()
    result = invoke_cli(
        runner,
        ["memory", "list"],
        cwd=whitespace_memory_file_project,
        json_mode=True,
    )
    assert result.exit_code == 0, f"expected exit 0 on whitespace-only corpus; got {result.exit_code}\n{result.output}"
    assert "Traceback" not in result.output, f"unexpected traceback in whitespace-only output:\n{result.output}"


# ---------------------------------------------------------------------------
# Parity probe: file-missing vs file-empty SHOULD agree on shape.
# Currently they do NOT -- xfail-strict pin below.
# ---------------------------------------------------------------------------


def test_missing_vs_empty_envelope_command_parity(bare_project, empty_memory_file_project):
    """Both empty-corpus paths emit the same ``command`` field.

    Regression guard on the axis that DOES already match across the
    two paths.
    """
    runner = CliRunner()
    # We cannot use the same runner.invoke for two cwds in one body, but
    # the fixtures use monkeypatch.chdir; the LAST fixture wins. Invoke
    # against each project explicitly via the invoke_cli cwd parameter.
    r1 = invoke_cli(runner, ["memory", "list"], cwd=bare_project, json_mode=True)
    r2 = invoke_cli(runner, ["memory", "list"], cwd=empty_memory_file_project, json_mode=True)
    env1 = _parse_envelope(r1)
    env2 = _parse_envelope(r2)
    assert env1["command"] == env2["command"] == "memory-list", (
        f"both empty-corpus paths should emit command='memory-list'; "
        f"got missing={env1['command']!r} empty={env2['command']!r}"
    )


# ---------------------------------------------------------------------------
# REAL BUG -- xfail-strict pin. Pattern-2 silent-divergence between the
# missing-file and empty-file paths: the empty-file walk emits
# ``state="ok"`` with empty ``next_commands`` while its missing-file
# sibling emits ``state="no_memory"`` with the populated recovery hint.
# Fix wave separate from W805 accumulate-only constraint.
# ---------------------------------------------------------------------------


@pytest.mark.xfail(
    strict=True,
    reason=(
        "W805-BBB Pattern-2 silent-divergence (peer of cmd_lease W805-XX): "
        "cmd_memory.py:254-289 (memory_list) and cmd_memory.py:355-393 "
        "(memory_relevant) emit state='ok' with empty next_commands when "
        ".roam/memory.jsonl exists but is empty / whitespace-only. The "
        "sibling missing-file branch at 226-252 / 321-353 emits "
        "state='no_memory' with next_commands=['roam memory add ...']. "
        "Two empty-corpus paths produce divergent structured next-step "
        "fields -- agents auto-routing on next_commands[0] get a recovery "
        "hint on one path and a dead 'state=ok' envelope on the other "
        "(also Pattern-1 variant D: silent success on degraded resolution). "
        "Fix: when total==0 on the walked-file path, mirror the missing-"
        "file shape -- set state='no_memory' (or new state='empty_memory') "
        "and pass agent_contract={'facts':[...], 'next_commands':"
        "['roam memory add --kind fact --subject TOPIC --body TEXT']}. "
        "Pinned for separate fix wave."
    ),
)
def test_no_silent_no_memory_on_empty(empty_memory_file_project):
    """Pattern-2 + CONSTRAINT 12: empty-file walk discloses no_memory.

    The empty-file path should emit an explicit-absence state and
    populate ``agent_contract.next_commands`` to match the
    missing-file sibling. Currently emits ``state='ok'`` -- xfail-strict.
    """
    runner = CliRunner()
    result = invoke_cli(
        runner,
        ["memory", "list"],
        cwd=empty_memory_file_project,
        json_mode=True,
    )
    env = _parse_envelope(result)
    state = env.get("summary", {}).get("state")
    assert state in {"no_memory", "empty_memory"}, (
        f"empty-file walk must disclose explicit-absence state (no_memory / empty_memory); got {state!r}"
    )
    next_cmds = env.get("agent_contract", {}).get("next_commands") or []
    assert next_cmds, f"empty-file walk must populate agent_contract.next_commands (CONSTRAINT 12); got {next_cmds!r}"
    for nc in next_cmds:
        assert nc.startswith("roam "), f"CONSTRAINT 12: next_command must be literal 'roam ...'; got {nc!r}"
    assert any("memory add" in nc for nc in next_cmds), (
        f"empty-file next_commands should include 'roam memory add'; got {next_cmds!r}"
    )
