r"""W805-XX -- empty-corpus Pattern-2 smoke test on ``roam lease list``.

Fiftieth-in-batch W805 sweep. Substrate state-reader peer of
``cmd_replay`` / ``cmd_runs`` -- ``roam lease list`` enumerates the
multi-agent claim records under ``.roam/leases/`` (R21, per CLAUDE.md).

Scope
-----

``cmd_lease`` (``src/roam/commands/cmd_lease.py``) exposes five
subcommands: ``claim`` / ``release`` / ``list`` / ``show`` / ``gc``.
This sweep is READ-ONLY -- only ``list`` is exercised (claim/release/gc
mutate disk state and are out-of-scope for the empty-corpus probe).

W978 first-hypothesis discipline
--------------------------------

Hypothesis: "substrate state-reader on empty corpus -- likely silent
Pattern-2 SAFE on the ``no leases yet`` path".

W978 probed two corpora:

* **Missing ``.roam/leases/`` dir (W978-VERIFIED partial)**: the early
  return at ``cmd_lease.py:510-528`` produces an explicit
  ``state="no_leases"`` with a LAW-6 verdict that names the next
  command in prose (``"no leases yet -- run \`roam lease claim --agent
  NAME --file PATH\` to open one"``). Good on the state + verdict axes.
  HOWEVER ``agent_contract.next_commands`` is **empty** -- the
  envelope's structured next-step field is absent while the populated
  branch's ``no_matches`` sibling DOES populate it (see below).
  CONSTRAINT 12 reads on this axis: the verdict embeds the command as
  a prose string but the structured field agents actually consume is
  empty. (Verdict prose: "no leases yet -- run roam lease claim
  --agent NAME --file PATH to open one".)

* **``.roam/leases/`` exists but empty (W978-VERIFIED OK)**:
  ``cmd_lease.py:530-574`` walks the directory, gets 0 results, and
  sets ``state="no_matches"`` with
  ``next_commands=["roam lease claim --agent NAME --file PATH"]``.
  The structured next-step IS populated here. Pattern-2 + CONSTRAINT 12
  both satisfied on this axis.

Divergence: two empty-corpus paths produce divergent envelope shapes:

  | path                        | state       | next_commands populated |
  | --------------------------- | ----------- | ----------------------- |
  | dir MISSING (early return)  | no_leases   | NO (empty list)         |
  | dir EXISTS + empty (walked) | no_matches  | YES (1 entry)           |

Both represent "0 leases known to this repo" but the structured
agent-contract diverges. An agent that auto-routes on
``next_commands[0]`` gets a recovery hint on one path and a dead
envelope on the other -- the exact same Pattern-2-family failure as
``cmd_replay``'s ``state="ok"`` on empty ledger: the structured signal
is lost on the degraded-corpus path.

REAL BUG pinned (Pattern-2 silent-divergence on dir-missing path)
-----------------------------------------------------------------

``cmd_lease.py:510-528`` -- the early-return branch sets the verdict
prose but does NOT pass ``agent_contract={...}`` to the envelope, so
``json_envelope`` auto-derives a contract whose
``next_commands`` is empty. The sibling branch at lines 545-574 hand-
anchors a ``next_commands=["roam lease claim --agent NAME --file PATH"]``
on the equivalent empty-corpus condition.

Fix template (analogous to the populated-branch handling):

    envelope = json_envelope(
        "lease-list",
        summary={...},
        budget=token_budget,
        leases=[],
        path=str(lroot),
        agent_contract={
            "facts": [f"0 leases (directory {lroot} does not yet exist)"],
            "next_commands": ["roam lease claim --agent NAME --file PATH"],
        },
    )

LAW 4 ``leases`` is in the concrete-noun anchor set. LAW 6: verdict
already names the next command in prose. CONSTRAINT 12: the
structured ``next_commands`` mirrors the verdict prose, giving agents
a copy-pasteable recovery action regardless of which axis they
consume.

Sweep brief: W805-XX (Wave805-XX, fiftieth-in-batch).
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
    """Bare git-init project with NO ``.roam/leases/`` directory at all.

    Drives the early-return branch at ``cmd_lease.py:510-528`` where
    ``leases_root(root)`` does not yet exist.
    """
    proj = tmp_path / "bare-lease-proj"
    proj.mkdir()
    (proj / ".gitignore").write_text(".roam/\n", encoding="utf-8")
    (proj / "app.py").write_text("def f():\n    return 0\n", encoding="utf-8")
    git_init(proj)
    monkeypatch.chdir(proj)
    return proj


@pytest.fixture
def empty_leases_dir_project(tmp_path, monkeypatch):
    """Project with ``.roam/leases/`` created but containing 0 lease files.

    Drives the populated branch at ``cmd_lease.py:530-574`` where
    ``list_leases`` returns ``[]`` and ``state="no_matches"``.
    """
    proj = tmp_path / "empty-leases-dir-proj"
    proj.mkdir()
    (proj / ".gitignore").write_text(".roam/\n", encoding="utf-8")
    (proj / "app.py").write_text("def f():\n    return 0\n", encoding="utf-8")
    git_init(proj)
    monkeypatch.chdir(proj)
    (proj / ".roam" / "leases").mkdir(parents=True)
    return proj


@pytest.fixture
def clean_lease_project(tmp_path, monkeypatch):
    """Project with one real lease file under ``.roam/leases/``.

    Drives the analytical path at ``cmd_lease.py:530-574`` where
    ``list_leases`` returns one or more records and ``state="ok"``.
    """
    proj = tmp_path / "clean-lease-proj"
    proj.mkdir()
    (proj / ".gitignore").write_text(".roam/\n", encoding="utf-8")
    (proj / "app.py").write_text("def f():\n    return 0\n", encoding="utf-8")
    git_init(proj)
    monkeypatch.chdir(proj)

    lroot = proj / ".roam" / "leases"
    lroot.mkdir(parents=True)
    lease_id = "lease_20260518_W805XX"
    lease = {
        "lease_id": lease_id,
        "agent": "w805-xx-test-agent",
        "subject_kind": "files",
        "subject": ["app.py"],
        "acquired_at": "2026-05-18T08:00:00Z",
        "expires_at": "2099-01-01T00:00:00Z",
        "ttl_seconds": 1800,
        "state": "active",
    }
    (lroot / f"{lease_id}.json").write_text(json.dumps(lease, indent=2), encoding="utf-8")
    return proj, lease_id


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
    """``cmd_lease.lease_group`` is importable + a Click group."""
    try:
        from roam.commands.cmd_lease import lease_group
    except ImportError:
        pytest.skip("cmd_lease not importable -- skipping W805-XX smoke test")
    import click

    assert isinstance(lease_group, click.Group), f"lease_group must be a Click Group; got {type(lease_group)!r}"
    # All five documented subcommands must be present.
    expected = {"claim", "release", "list", "show", "gc"}
    actual = set(lease_group.commands.keys())
    assert expected <= actual, f"lease group missing subcommands: expected {expected}, got {actual}"


# ---------------------------------------------------------------------------
# SMOKE -- properties satisfied today on the ``state="no_leases"`` early-
# return path. Regression guard.
# ---------------------------------------------------------------------------


class TestLeaseListMissingDirSealed:
    """Pin the current shape of the dir-missing empty-corpus path.

    Today: ``state="no_leases"`` + LAW-6 verdict in prose. These tests
    guard against regression on the axes that are already correct.
    """

    def test_lease_list_empty_no_crash(self, bare_project):
        """Missing ``.roam/leases/`` -> exit 0 without traceback."""
        runner = CliRunner()
        result = invoke_cli(
            runner,
            ["lease", "list"],
            cwd=bare_project,
            json_mode=True,
        )
        # Empty corpus is a clean success (no leases to enumerate), not a crash.
        assert result.exit_code == 0, (
            f"expected exit 0 on missing-dir empty corpus; got {result.exit_code}\n{result.output}"
        )
        assert "Traceback" not in result.output, f"unexpected traceback in missing-dir output:\n{result.output}"

    def test_lease_list_empty_envelope_verdict(self, bare_project):
        """Missing-dir envelope carries non-empty ``summary.verdict`` string."""
        runner = CliRunner()
        result = invoke_cli(
            runner,
            ["lease", "list"],
            cwd=bare_project,
            json_mode=True,
        )
        env = _parse_envelope(result)
        assert env["command"] == "lease-list"
        verdict = env.get("summary", {}).get("verdict") or ""
        assert isinstance(verdict, str) and verdict, f"summary.verdict must be a non-empty string, got {verdict!r}"

    def test_lease_list_empty_state_explicit(self, bare_project):
        """Pattern-2 explicit-absence: dir-missing discloses ``state="no_leases"``."""
        runner = CliRunner()
        result = invoke_cli(
            runner,
            ["lease", "list"],
            cwd=bare_project,
            json_mode=True,
        )
        env = _parse_envelope(result)
        state = env.get("summary", {}).get("state")
        assert state == "no_leases", f"dir-missing must disclose summary.state='no_leases'; got {state!r}"

    def test_lease_list_empty_partial_success_set(self, bare_project):
        """Pattern-2: dir-missing is a clean known-empty state, NOT partial.

        Distinct from missing-run / empty-ledger paths: an absent
        ``.roam/leases/`` directory is the *expected* shape pre-first-
        claim, not a degraded analytical product. ``partial_success``
        is therefore False here -- mirror the existing exemplary
        ``no_matches`` sibling branch.
        """
        runner = CliRunner()
        result = invoke_cli(
            runner,
            ["lease", "list"],
            cwd=bare_project,
            json_mode=True,
        )
        env = _parse_envelope(result)
        # The shape today: dir-missing is clean-empty.
        assert env["summary"].get("partial_success") is False, (
            f"dir-missing should be clean-empty (partial_success=False); got summary={env['summary']!r}"
        )
        # Total must be explicit.
        assert env["summary"].get("total") == 0, f"dir-missing must disclose summary.total=0; got {env['summary']!r}"

    def test_law6_verdict_standalone(self, bare_project):
        """LAW 6: dir-missing verdict works without any other field.

        The verdict "no leases yet -- run roam lease claim ..."
        names both the state (no leases) AND the recovery command in
        prose -- an agent reading only the verdict can act on it.
        """
        runner = CliRunner()
        result = invoke_cli(
            runner,
            ["lease", "list"],
            cwd=bare_project,
            json_mode=True,
        )
        env = _parse_envelope(result)
        verdict = env["summary"]["verdict"]
        assert "\n" not in verdict, f"verdict embeds newline: {verdict!r}"
        v_lower = verdict.lower()
        # Verdict names the empty state.
        assert "no lease" in v_lower or "0 lease" in v_lower or "empty" in v_lower, (
            f"LAW 6: dir-missing verdict must name the empty state; got {verdict!r}"
        )

    def test_lease_list_empty_command_field(self, bare_project):
        """Envelope identifies itself as ``lease-list`` (subcommand-named)."""
        runner = CliRunner()
        result = invoke_cli(
            runner,
            ["lease", "list"],
            cwd=bare_project,
            json_mode=True,
        )
        env = _parse_envelope(result)
        assert env.get("command") == "lease-list", (
            f"envelope command field must be 'lease-list'; got {env.get('command')!r}"
        )
        # leases payload is an empty list (never None / missing).
        assert env.get("leases") == [], f"empty corpus must emit leases=[]; got {env.get('leases')!r}"


# ---------------------------------------------------------------------------
# CLEAN-corpus regression guard -- a real lease enumerates correctly.
# ---------------------------------------------------------------------------


def test_clean_lease_emits_real_list(clean_lease_project):
    """One real lease yields ``state="ok"`` + populated leases array."""
    proj, lease_id = clean_lease_project
    runner = CliRunner()
    result = invoke_cli(runner, ["lease", "list"], cwd=proj, json_mode=True)
    assert result.exit_code == 0, f"clean-lease failed: {result.output}"
    env = _parse_envelope(result)
    assert env["summary"]["state"] == "ok", f"populated corpus should have state='ok'; got {env['summary']!r}"
    assert env["summary"]["total"] == 1, f"clean corpus should yield 1 lease; got {env['summary']['total']}"
    assert len(env["leases"]) == 1, f"leases array should contain the record; got {env['leases']!r}"
    assert env["leases"][0]["lease_id"] == lease_id, f"emitted lease_id mismatch: {env['leases'][0]!r}"


def test_lease_list_empty_dir_state_explicit(empty_leases_dir_project):
    """``.roam/leases/`` exists but empty -> ``state="no_matches"`` (sealed).

    This is the EXEMPLARY sibling branch -- proper Pattern-2 + LAW-6
    + CONSTRAINT 12 shape on the populated-walk path. Regression guard.
    """
    runner = CliRunner()
    result = invoke_cli(
        runner,
        ["lease", "list"],
        cwd=empty_leases_dir_project,
        json_mode=True,
    )
    env = _parse_envelope(result)
    assert env["summary"]["state"] == "no_matches", (
        f"empty-walked dir must disclose 'no_matches'; got {env['summary']!r}"
    )
    next_cmds = env.get("agent_contract", {}).get("next_commands") or []
    assert next_cmds, f"empty-walked dir must populate next_commands (sealed); got {next_cmds!r}"
    assert any("roam lease claim" in nc for nc in next_cmds), (
        f"empty-walked next_commands should include 'roam lease claim'; got {next_cmds!r}"
    )


# ---------------------------------------------------------------------------
# REAL BUG -- xfail-strict pin. Pattern-2 silent-divergence between
# the two empty-corpus paths: the dir-missing branch fails CONSTRAINT 12
# (empty ``next_commands``) while its populated-branch sibling does not.
# Fix wave separate from W805 accumulate-only constraint.
# ---------------------------------------------------------------------------


@pytest.mark.xfail(
    strict=True,
    reason=(
        "W805-XX CONSTRAINT 12 + Pattern-2 silent-divergence: "
        "cmd_lease.py:510-528 early-return on missing .roam/leases/ "
        "omits the agent_contract hand-anchor, so json_envelope auto-"
        "derives a contract whose next_commands is empty. The sibling "
        "branch at lines 545-574 sets next_commands=['roam lease claim "
        "--agent NAME --file PATH'] on the equivalent empty-corpus walk. "
        "Two empty-corpus paths produce divergent structured next-step "
        "fields -- agents auto-routing on next_commands[0] get a "
        "recovery hint on one path and a dead envelope on the other. "
        "Fix: pass agent_contract={'facts':[...], 'next_commands':"
        "['roam lease claim --agent NAME --file PATH']} on the early-"
        "return path to match the sibling. Pinned for separate fix wave."
    ),
)
def test_no_silent_no_leases_on_empty(bare_project):
    """Pattern-2 + CONSTRAINT 12: dir-missing populates ``next_commands``.

    The verdict already embeds the recovery command in prose; the
    structured ``agent_contract.next_commands`` should mirror it so
    agents that route on the structured field also get the hint.
    """
    runner = CliRunner()
    result = invoke_cli(
        runner,
        ["lease", "list"],
        cwd=bare_project,
        json_mode=True,
    )
    env = _parse_envelope(result)
    next_cmds = env.get("agent_contract", {}).get("next_commands") or []
    assert next_cmds, f"dir-missing must populate agent_contract.next_commands (CONSTRAINT 12); got {next_cmds!r}"
    # All literal 'roam ...' (CONSTRAINT 12 shape check).
    for nc in next_cmds:
        assert nc.startswith("roam "), f"CONSTRAINT 12: next_command must be literal 'roam ...'; got {nc!r}"
    # Mirror the verdict prose: recovery is 'roam lease claim'.
    assert any("lease claim" in nc for nc in next_cmds), (
        f"dir-missing next_commands should include 'roam lease claim'; got {next_cmds!r}"
    )
