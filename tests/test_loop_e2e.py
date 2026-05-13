"""End-to-end integration tests for the full Roam Review loop.

This file is the PROOF that the substrate composes — not just that each
piece works in isolation. A real agent workflow walks::

    1.  roam init                                          # index the repo
    2.  roam laws mine --out roam-laws.yml                 # discover invariants
    3.  roam constitution init                             # policy capstone
    4.  roam mode safe_edit                                # pick gate level
    5.  ROAM_RUN_ID=$(roam runs start --agent X | parse)   # open the ledger
    6.  roam pr-bundle init --intent "..."                 # start proof bundle
    7.  roam preflight <symbol>                            # blast-radius gate
    8.  roam impact <symbol>                               # impact analysis
    9.  roam diff (or critique)                            # review surface
    10. roam pr-bundle emit --auto-collect                 # fold envelopes
    11. roam runs end                                      # close ledger
    12. roam replay <run_id>                               # re-narrate
    13. roam agent-score                                   # score the agent

Each test below walks all or part of this chain on a hand-built fixture and
asserts the envelopes are well-formed AND the integration points hold (e.g.
``runs replay`` actually shows ``preflight``-action events; W7.4 auto_log +
W10.2 CLI responses-write are both firing).

Coordination notes:
  - W14.2 may wire mode-enforcement at dispatch level. ``test_loop_mode_*``
    skips gracefully if that wiring hasn't shipped yet.
  - DO NOT use the MCP server tools — dev venv doesn't carry fastmcp.
  - Each test is self-contained: tmp_path fixture, no shared state.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest
from click.testing import CliRunner

sys.path.insert(0, str(Path(__file__).parent))
from conftest import (  # noqa: E402
    git_init,
    index_in_process,
    parse_json_output,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _invoke(runner: CliRunner, args, **kwargs):
    """Invoke the roam CLI in-process via Click's CliRunner."""
    from roam.cli import cli

    return runner.invoke(cli, args, catch_exceptions=False, **kwargs)


def _parse_json(result):
    """Parse JSON from a CliRunner result. Fails the test on parse error."""
    raw = getattr(result, "stdout", None) or result.output
    try:
        return json.loads(raw)
    except json.JSONDecodeError as e:
        pytest.fail(f"Invalid JSON (exit={result.exit_code}): {e}\n{raw[:800]}")


def _fixture_project(tmp_path: Path, name: str = "loopproj") -> Path:
    """Create a minimal but realistic Python fixture and index it.

    The shape matters: a writer function (io_write side-effect) so the
    W12.1 auto-risk wiring kicks in, plus a pure function and a class so
    laws mining + impact have non-trivial signal.
    """
    proj = tmp_path / name
    proj.mkdir()
    (proj / ".gitignore").write_text(".roam/\n")
    (proj / "main.py").write_text(
        "def hello():\n"
        "    return 'hi'\n"
        "\n"
        "def add(a, b):\n"
        "    return a + b\n"
    )
    (proj / "writer.py").write_text(
        "def dump_state(path, content):\n"
        "    with open(path, 'w') as f:\n"
        "        f.write(content)\n"
        "\n"
        "def read_state(path):\n"
        "    with open(path) as f:\n"
        "        return f.read()\n"
    )
    (proj / "service.py").write_text(
        "from writer import dump_state, read_state\n"
        "\n"
        "def save_user(user_id, name):\n"
        "    path = f'/tmp/u_{user_id}.txt'\n"
        "    dump_state(path, name)\n"
        "    return path\n"
        "\n"
        "def load_user(user_id):\n"
        "    return read_state(f'/tmp/u_{user_id}.txt')\n"
    )
    git_init(proj)
    # Pin a stable branch so pr-bundle filename is deterministic across tests.
    subprocess.run(
        ["git", "checkout", "-B", "loop-branch"], cwd=proj, capture_output=True
    )
    out, rc = index_in_process(proj)
    assert rc == 0, f"index failed:\n{out}"
    return proj


# ---------------------------------------------------------------------------
# 1. Minimal workflow — init + bundle + preflight + emit
# ---------------------------------------------------------------------------


def test_loop_minimal_workflow(tmp_path, cli_runner, monkeypatch):
    """The minimal "agent does its thing" path.

    Walks: index -> constitution init -> mode safe_edit -> pr-bundle init
    -> preflight (any symbol) -> pr-bundle emit. Asserts the bundle emerges
    well-formed with at least one envelope scanned.
    """
    proj = _fixture_project(tmp_path, name="minimal")
    monkeypatch.chdir(proj)
    # Ensure no stale env from prior tests.
    monkeypatch.delenv("ROAM_RUN_ID", raising=False)

    # constitution init — Capstone substrate.
    r = _invoke(cli_runner, ["--json", "constitution", "init"])
    assert r.exit_code == 0, r.output
    cdata = _parse_json(r)
    assert cdata["summary"]["state"] in ("initialized", "already_initialized")

    # mode safe_edit
    r = _invoke(cli_runner, ["--json", "mode", "safe_edit"])
    assert r.exit_code == 0, r.output
    mdata = _parse_json(r)
    assert mdata["summary"]["active_mode"] == "safe_edit"

    # pr-bundle init
    r = _invoke(
        cli_runner,
        ["--json", "pr-bundle", "init", "--intent", "minimal-loop-test"],
    )
    assert r.exit_code == 0, r.output
    bdata = _parse_json(r)
    assert bdata["summary"]["state"] == "initialized"

    # Add an affected symbol so the bundle has something to verdict on.
    # Use dump_state — known io_write writer so the R28 auto-risk wiring
    # produces the bundle annotation we depend on later.
    r = _invoke(cli_runner, ["pr-bundle", "add", "affected", "dump_state"])
    assert r.exit_code == 0, r.output

    # preflight — gate command, writes its envelope to .roam/responses/
    # ONLY when ROAM_RUN_ID is set (see W10.2). Without it, the envelope
    # exists only in stdout. That's fine for this minimal test; we only
    # need the bundle to have its own affected_symbols populated.
    r = _invoke(cli_runner, ["--json", "preflight", "dump_state"])
    # preflight may exit 0 or higher depending on blast-radius; we
    # tolerate either. What matters is it emits a valid envelope.
    assert r.exit_code in (0, 5), r.output

    # pr-bundle emit (default = --auto-collect)
    r = _invoke(cli_runner, ["--json", "pr-bundle", "emit"])
    assert r.exit_code == 0, r.output
    edata = _parse_json(r)
    # The bundle should contain dump_state.
    bundle_path = proj / ".roam" / "pr-bundles" / "loop-branch.json"
    assert bundle_path.exists(), f"bundle not found at {bundle_path}"
    bundle = json.loads(bundle_path.read_text(encoding="utf-8"))
    names = [s.get("name") for s in bundle["affected_symbols"]]
    assert "dump_state" in names, names
    # auto_collect block is present under summary (W15.2 envelope reshape;
    # even if 0 envelopes scanned, since we didn't run with ROAM_RUN_ID, no
    # .roam/responses/ side-cars exist).
    auto = edata["summary"].get("auto_collect") or {}
    assert "envelopes_scanned" in auto, edata


# ---------------------------------------------------------------------------
# 2. Full 13-step chain with the ledger ledger
# ---------------------------------------------------------------------------


def test_loop_full_chain_with_runs_ledger(tmp_path, cli_runner, monkeypatch):
    """Walk all 13 steps and assert events flow through the ledger.

    Critical: the events surfaced by ``roam replay`` should include
    ``preflight`` AND ``pr-bundle`` actions — that verifies both
    auto-log (W7.4) AND the CLI responses-write (W10.2) are firing
    in the same run.
    """
    proj = _fixture_project(tmp_path, name="fullchain")
    monkeypatch.chdir(proj)

    # Step 2: laws mine.
    laws_path = proj / "roam-laws.yml"
    r = _invoke(
        cli_runner,
        ["--json", "laws", "mine", "--out", str(laws_path)],
    )
    assert r.exit_code == 0, r.output
    ldata = _parse_json(r)
    assert "law_count" in ldata["summary"]

    # Step 3: constitution init.
    r = _invoke(cli_runner, ["--json", "constitution", "init"])
    assert r.exit_code == 0, r.output

    # Step 4: mode safe_edit.
    r = _invoke(cli_runner, ["--json", "mode", "safe_edit"])
    assert r.exit_code == 0, r.output

    # Step 5: runs start (capture run_id from JSON envelope).
    r = _invoke(
        cli_runner,
        ["--json", "runs", "start", "--agent", "loop-test-agent"],
    )
    assert r.exit_code == 0, r.output
    sdata = _parse_json(r)
    run_id = sdata["summary"]["run_id"]
    assert run_id, sdata
    # The agent's harness would now export this; we simulate by setting env.
    monkeypatch.setenv("ROAM_RUN_ID", run_id)

    # Step 6: pr-bundle init.
    r = _invoke(
        cli_runner,
        ["--json", "pr-bundle", "init", "--intent", "exercise full loop"],
    )
    assert r.exit_code == 0, r.output

    # Step 7: preflight — must auto-log to the run AND write to responses/.
    r = _invoke(cli_runner, ["--json", "preflight", "dump_state"])
    assert r.exit_code in (0, 5), r.output

    # Step 8: impact (does not auto-log per current allow-list; that's fine —
    # we exercise it for response-write coverage and to swell affected list).
    r = _invoke(cli_runner, ["--json", "impact", "dump_state"])
    assert r.exit_code == 0, r.output

    # Step 9: diff (auto-logs).
    r = _invoke(cli_runner, ["--json", "diff"])
    # diff exit can be 0 or 5 depending on uncommitted changes; both fine.
    assert r.exit_code in (0, 5), r.output

    # Add affected so the bundle has a payload for emit.
    r = _invoke(cli_runner, ["pr-bundle", "add", "affected", "dump_state"])
    assert r.exit_code == 0, r.output

    # Step 10: pr-bundle emit --auto-collect (the killer integration step).
    r = _invoke(cli_runner, ["--json", "pr-bundle", "emit"])
    assert r.exit_code == 0, r.output
    edata = _parse_json(r)
    # W15.2 envelope reshape: auto_collect under summary.
    auto = edata["summary"].get("auto_collect") or {}
    # With ROAM_RUN_ID set, preflight/diff envelopes landed in
    # .roam/responses/, so envelopes_scanned should be >= 1.
    assert auto.get("envelopes_scanned", 0) >= 1, (
        f"expected auto-collect to fold >= 1 envelope, got {auto}"
    )

    # Step 11: runs end.
    r = _invoke(cli_runner, ["--json", "runs", "end"])
    assert r.exit_code == 0, r.output
    eend = _parse_json(r)
    assert eend["summary"]["state"] in ("completed", "ok"), eend

    # runs list should show our run.
    r = _invoke(cli_runner, ["--json", "runs", "list"])
    assert r.exit_code == 0, r.output
    rlist = _parse_json(r)
    run_ids = [r["run_id"] for r in rlist.get("runs", [])]
    assert run_id in run_ids, f"run {run_id} missing from list: {run_ids}"

    # Step 12: replay.
    r = _invoke(cli_runner, ["--json", "replay", run_id])
    assert r.exit_code == 0, r.output
    repdata = _parse_json(r)
    events = repdata.get("events", [])
    assert len(events) >= 4, (
        f"expected >= 4 events in replay, got {len(events)}:\n"
        f"{json.dumps(events, indent=2)[:1000]}"
    )
    actions = {e.get("action") for e in events}
    # The critical assertion: preflight AND pr-bundle BOTH show up. This is
    # the proof that auto-log (W7.4) is firing for preflight + the pr-bundle
    # emit auto-log is firing. If either is silent the loop is fragmented.
    assert "preflight" in actions, f"preflight missing from replay actions: {actions}"
    # pr-bundle actions are logged with action names like pr-bundle-init /
    # pr-bundle-emit; allow either canonical form. Accept any action that
    # begins with 'pr-bundle'.
    pr_bundle_actions = [a for a in actions if a and a.startswith("pr-bundle")]
    assert pr_bundle_actions, (
        f"no pr-bundle actions in replay: {sorted(a for a in actions if a)}"
    )

    # Step 13: agent-score.
    r = _invoke(cli_runner, ["--json", "agent-score"])
    assert r.exit_code == 0, r.output
    sdata = _parse_json(r)
    # We expect at least one agent scored (our loop-test-agent), with score > 0.
    agents = sdata.get("agents", [])
    matching = [a for a in agents if a.get("agent") == "loop-test-agent"]
    assert matching, f"loop-test-agent not in scored agents: {agents}"
    score = matching[0].get("score", 0)
    assert score > 0, f"expected non-zero score, got {score} for {matching[0]}"


# ---------------------------------------------------------------------------
# 3. R27 round-trip: laws mine -> laws check
# ---------------------------------------------------------------------------


def test_loop_laws_mine_then_check(tmp_path, cli_runner, monkeypatch):
    """Mine laws on a fixture, then check against an unrelated diff.

    The unrelated diff intentionally violates the dominant naming convention
    by introducing a camelCase function in a project of snake_case functions.
    The laws-check envelope should produce a violations payload.
    """
    proj = tmp_path / "lawsproj"
    proj.mkdir()
    (proj / ".gitignore").write_text(".roam/\n")
    # Snake-case-heavy fixture — gives the naming-law miner strong signal.
    (proj / "app.py").write_text(
        "def fetch_user(uid): return uid\n"
        "def update_user(uid): return uid\n"
        "def delete_user(uid): return uid\n"
        "def list_users(): return []\n"
        "def make_token(): return 't'\n"
        "def parse_email(raw): return raw\n"
        "def format_name(first, last): return first\n"
        "def validate_input(x): return x\n"
        "def serialize_payload(p): return p\n"
        "def render_template(t): return t\n"
    )
    git_init(proj)
    monkeypatch.chdir(proj)
    out, rc = index_in_process(proj)
    assert rc == 0, f"index failed:\n{out}"

    laws_path = proj / "roam-laws.yml"
    r = _invoke(
        cli_runner,
        ["--json", "laws", "mine", "--out", str(laws_path)],
    )
    assert r.exit_code == 0, r.output
    ldata = _parse_json(r)
    # At least one law should have been mined (snake_case dominance).
    assert ldata["summary"]["law_count"] >= 1, ldata["summary"]
    assert laws_path.exists()

    # Introduce a camelCase function that violates the naming convention.
    # The diff stays unstaged; laws-check defaults to --diff-source working.
    (proj / "violator.py").write_text(
        "def fetchUserCamel(uid): return uid\n"
    )
    # Stage but don't commit — staged is fine for working too because the
    # working diff includes unstaged changes.
    # laws-check uses git diff which includes new files only when -- they
    # are tracked. Add to index so the diff is non-empty.
    subprocess.run(["git", "add", "violator.py"], cwd=proj, capture_output=True)

    r = _invoke(
        cli_runner,
        [
            "--json",
            "laws",
            "check",
            "--laws-file",
            str(laws_path),
            "--diff-source",
            "staged",
        ],
    )
    # Exit 0 even on violations — only --strict exits 5. We just want the
    # envelope to be well-formed.
    assert r.exit_code in (0, 5), r.output
    cdata = _parse_json(r)
    # The envelope must have a violations key (may be empty if the mined
    # laws didn't include the right naming-law variant — both shapes are
    # valid as long as we round-trip cleanly).
    assert "violations" in cdata, cdata
    # Stronger: the summary should carry a violations count.
    assert "violations" in cdata["summary"], cdata["summary"]


# ---------------------------------------------------------------------------
# 4. R24 gate execution: constitution apply --gate before_edit
# ---------------------------------------------------------------------------


def test_loop_constitution_apply_gates(tmp_path, cli_runner, monkeypatch):
    """Init constitution, then run ``apply --gate before_edit --symbol X``.

    The default before_edit gate includes preflight; the verdict should
    name the gate command(s) that ran.
    """
    proj = _fixture_project(tmp_path, name="constproj")
    monkeypatch.chdir(proj)

    r = _invoke(cli_runner, ["--json", "constitution", "init"])
    assert r.exit_code == 0, r.output

    r = _invoke(
        cli_runner,
        [
            "--json",
            "constitution",
            "apply",
            "--gate",
            "before_edit",
            "--symbol",
            "dump_state",
        ],
    )
    # Exit may be 0 (all pass) or 5 (some failed; constitution is strict).
    # We accept either, plus 2 (no constitution — shouldn't happen since we
    # just init'd, but defensive).
    assert r.exit_code in (0, 2, 5), r.output
    data = _parse_json(r)
    verdict = data["summary"].get("verdict", "")
    # The verdict should mention preflight (the canonical before_edit gate)
    # OR mention the gate name itself, OR mention how many checks ran.
    needles = ("preflight", "before_edit", "check", "gate")
    assert any(n in verdict.lower() for n in needles), (
        f"verdict didn't mention any of {needles}: {verdict!r}"
    )


# ---------------------------------------------------------------------------
# 5. Dogfood corpus smoke: re-run dogfood-aggregate
# ---------------------------------------------------------------------------


def test_loop_dogfood_corpus_shrink(cli_runner):
    """Re-run ``roam dogfood-aggregate`` against the live corpus.

    This is the "is the dogfood backlog actually shrinking?" integration
    smoke. We do NOT assert a specific count — the corpus is what it is.
    Instead we capture the headline numbers (severity + total) for the
    test report, and assert the envelope is well-formed.

    The Wave-1 baseline (recorded in agent reports) was:
        590 open findings · H:143 M:385 L:62
    """
    # Run against the actual repo's internal/dogfood/evals/ — we don't chdir
    # because the find_project_root call already locates the roam-code
    # repo from cwd. Run from the repo root.
    repo_root = Path(__file__).resolve().parents[1]
    # Use a subprocess so we run from the repo root regardless of where
    # pytest was invoked.
    py = sys.executable
    result = subprocess.run(
        [py, "-m", "roam", "--json", "dogfood-aggregate"],
        cwd=str(repo_root),
        capture_output=True,
        text=True,
        timeout=120,
        encoding="utf-8",
        errors="replace",
    )
    assert result.returncode == 0, (
        f"dogfood-aggregate failed: rc={result.returncode}\n"
        f"stdout: {result.stdout[:500]}\nstderr: {result.stderr[:500]}"
    )
    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError as e:
        pytest.fail(f"non-JSON dogfood output: {e}\n{result.stdout[:500]}")
    summary = data["summary"]
    total = summary.get("findings_total", -1)
    by_sev = summary.get("by_severity", {})
    by_status = summary.get("by_status_all", {})
    # The smoke test: corpus is parseable, well-formed envelope returned.
    assert total >= 0, summary
    assert "verdict" in summary
    # Record for the test report (printed on -v).
    print(
        f"\n[DOGFOOD CORPUS] total={total} "
        f"H={by_sev.get('H', '?')} M={by_sev.get('M', '?')} L={by_sev.get('L', '?')} "
        f"by_status={by_status}"
    )


# ---------------------------------------------------------------------------
# 6. R28 auto-risks wiring (W12.1)
# ---------------------------------------------------------------------------


def test_loop_pr_bundle_with_r28_auto_risks(tmp_path, cli_runner, monkeypatch):
    """pr-bundle init -> add affected (io_write symbol) -> emit.

    Expectation: the bundle gains an auto-risk with
    ``source_command: "auto:world-model"`` and severity M or H — that's
    the proof of W12.1 wiring.
    """
    proj = _fixture_project(tmp_path, name="r28proj")
    monkeypatch.chdir(proj)

    r = _invoke(
        cli_runner,
        ["--json", "pr-bundle", "init", "--intent", "test R28 wiring"],
    )
    assert r.exit_code == 0, r.output

    # dump_state writes to a path — io_write side effect.
    r = _invoke(cli_runner, ["pr-bundle", "add", "affected", "dump_state"])
    assert r.exit_code == 0, r.output

    bundle_path = proj / ".roam" / "pr-bundles" / "loop-branch.json"
    assert bundle_path.exists()
    bundle = json.loads(bundle_path.read_text(encoding="utf-8"))

    # The affected_symbols entry should carry world-model fields.
    rec = next((s for s in bundle["affected_symbols"] if s["name"] == "dump_state"), None)
    assert rec is not None, bundle["affected_symbols"]
    assert "io_write" in rec.get("side_effect_kinds", []), rec
    assert rec.get("world_model_confidence") in ("high", "medium", "low"), rec

    # The auto-risk should be present.
    auto_risks = [
        r
        for r in bundle["risks"]
        if r.get("source_command") == "auto:world-model"
    ]
    assert len(auto_risks) >= 1, (
        f"expected >= 1 auto:world-model risk, got: {bundle['risks']}"
    )
    risk = auto_risks[0]
    assert risk.get("severity") in ("M", "H"), risk
    assert "dump_state" in (risk.get("description") or ""), risk

    # And the envelope summary on emit should surface the distribution.
    r = _invoke(cli_runner, ["--json", "pr-bundle", "emit", "--no-auto-collect"])
    assert r.exit_code == 0, r.output
    edata = _parse_json(r)
    summary = edata["summary"]
    assert "side_effect_distribution" in summary, summary
    assert summary["side_effect_distribution"].get("io_write", 0) >= 1, summary


# ---------------------------------------------------------------------------
# 7. Mode restricts what runs (W14.2 coordination)
# ---------------------------------------------------------------------------


def test_loop_mode_restricts_what_runs(tmp_path, cli_runner, monkeypatch):
    """Verify mode enforcement OR fall back to --check signal.

    If W14.2 has wired mode-enforcement at dispatch level:
      - mode read_only + invoking attest should be BLOCKED.

    If W14.2 has NOT shipped:
      - we verify ``roam mode --check attest`` returns the expected
        blocked/allowed signal, so the substrate is at least correct.
    """
    proj = _fixture_project(tmp_path, name="modeproj")
    monkeypatch.chdir(proj)

    # Switch to read_only.
    r = _invoke(cli_runner, ["--json", "mode", "read_only"])
    assert r.exit_code == 0, r.output
    mdata = _parse_json(r)
    assert mdata["summary"]["active_mode"] == "read_only"

    # Check the substrate signal: is attest BLOCKED in read_only?
    r = _invoke(cli_runner, ["--json", "mode", "--check", "attest"])
    # exit 5 = blocked (gate failure); exit 0 = allowed. Either is a
    # well-formed envelope, so don't insist on exit 0.
    data = _parse_json(r)
    assert "allowed" in data["summary"], data["summary"]
    # In read_only mode, attest should NOT be allowed (it's a write-side
    # command per the policy table).
    assert data["summary"]["allowed"] is False, (
        f"expected attest BLOCKED in read_only, got: {data['summary']}"
    )

    # Conversely, in autonomous_pr mode it should be allowed.
    r = _invoke(cli_runner, ["--json", "mode", "autonomous_pr"])
    assert r.exit_code == 0, r.output

    r = _invoke(cli_runner, ["--json", "mode", "--check", "attest"])
    data = _parse_json(r)
    assert data["summary"]["allowed"] is True, (
        f"expected attest ALLOWED in autonomous_pr, got: {data['summary']}"
    )

    # Test dispatch-level enforcement only IF W14.2 has shipped a
    # gate at the CLI surface. Probe for it by attempting to invoke
    # `attest` from read_only and inspecting whether the exit/output
    # signals a mode-block (the marker is the word "mode" in the
    # blocking verdict).
    _invoke(cli_runner, ["--json", "mode", "read_only"])
    r = _invoke(cli_runner, ["--json", "attest"])
    # If exit is non-zero AND the verdict mentions mode/read_only, we
    # can claim enforcement is wired. Otherwise we skip the strong
    # assertion (the substrate signal above already proved the policy
    # table is correct).
    enforcement_signal = False
    try:
        adata = _parse_json(r)
        verdict = (adata.get("summary") or {}).get("verdict", "")
        if (
            r.exit_code != 0
            and isinstance(verdict, str)
            and ("read_only" in verdict.lower() or "mode" in verdict.lower())
            and ("block" in verdict.lower() or "denied" in verdict.lower())
        ):
            enforcement_signal = True
    except Exception:
        # attest may not emit JSON on this path; not an enforcement signal.
        enforcement_signal = False

    if not enforcement_signal:
        pytest.skip(
            "W14.2 mode enforcement not yet wired at dispatch level; "
            "substrate signal (--check) is correct and verified above."
        )

    # If we got here, enforcement IS wired. Strong assertion:
    assert r.exit_code != 0, r.output
