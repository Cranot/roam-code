"""Tests for ``roam pr-bundle`` -- R26 proof-carrying PR bundle.

The bundle is the Roam Review MVP differentiator: agents emit a
``{intent, context_read, affected_symbols, risks, tests_required, tests_run,
known_non_goals, roam_verdict}`` envelope that reviewers can block on.

These tests pin down:
- subcommand wiring (init / set / add / emit / validate)
- on-disk persistence + atomic-update semantics
- the validation rules from CLAUDE.md / the W8.2 ticket
- the KILLER ``--auto-collect`` feature (folds .roam/responses/ envelopes
  into the bundle automatically)
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from click.testing import CliRunner

sys.path.insert(0, str(Path(__file__).parent))
from conftest import git_init, parse_json_output  # noqa: E402

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def cli_runner():
    return CliRunner()


@pytest.fixture
def bundle_project(tmp_path, monkeypatch):
    """A minimal git repo so ``find_project_root()`` resolves correctly."""
    proj = tmp_path / "repo"
    proj.mkdir()
    (proj / ".gitignore").write_text(".roam/\n")
    (proj / "main.py").write_text("def hello():\n    return 'hi'\n")
    git_init(proj)
    # Pin branch name -- on some hosts default may be "master".
    subprocess.run(["git", "checkout", "-B", "test-branch"], cwd=proj, capture_output=True)
    monkeypatch.chdir(proj)
    return proj


def _invoke(cli_runner, args, **kw):
    from roam.cli import cli

    return cli_runner.invoke(cli, args, catch_exceptions=False, **kw)


def _read_bundle_file(proj: Path, branch: str = "test-branch") -> dict:
    """Read the on-disk bundle JSON for ``branch``."""
    safe = branch.replace("/", "__")
    path = proj / ".roam" / "pr-bundles" / f"{safe}.json"
    if not path.exists():
        # Detached-fallback path.
        path = proj / ".roam" / "pr-bundle.json"
    assert path.exists(), f"bundle file missing -- looked at {path}"
    return json.loads(path.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# 1. init creates the bundle file
# ---------------------------------------------------------------------------


def test_init_creates_bundle_file(cli_runner, bundle_project):
    result = _invoke(cli_runner, ["--json", "pr-bundle", "init", "--intent", "Add retry"])
    assert result.exit_code == 0, result.output
    data = parse_json_output(result, command="pr-bundle-init")
    assert data["summary"]["state"] == "initialized"
    assert "pr-bundle initialised" in data["summary"]["verdict"]

    bundle = _read_bundle_file(bundle_project)
    assert bundle["intent"] == "Add retry"
    assert bundle["schema"] == "roam-pr-bundle"
    assert bundle["schema_version"] == 1
    # Core sections all present and empty.
    for section in (
        "affected_symbols",
        "risks",
        "tests_required",
        "tests_run",
        "known_non_goals",
    ):
        assert section in bundle and bundle[section] == [], f"section {section} not empty"
    assert "commands_run" in bundle["context_read"]
    assert "roam_verdict" in bundle
    # Git fingerprint snapshotted.
    assert bundle["git"].get("branch") == "test-branch"


# ---------------------------------------------------------------------------
# 2. set intent updates the bundle
# ---------------------------------------------------------------------------


def test_set_intent_updates_bundle(cli_runner, bundle_project):
    _invoke(cli_runner, ["pr-bundle", "init", "--intent", "original"])
    result = _invoke(cli_runner, ["--json", "pr-bundle", "set", "intent", "rewritten intent"])
    assert result.exit_code == 0, result.output
    bundle = _read_bundle_file(bundle_project)
    assert bundle["intent"] == "rewritten intent"


# ---------------------------------------------------------------------------
# 3. add affected appends to the list (and dedupes)
# ---------------------------------------------------------------------------


def test_add_affected_appends_to_list(cli_runner, bundle_project):
    _invoke(cli_runner, ["pr-bundle", "init", "--intent", "x"])
    r1 = _invoke(
        cli_runner,
        ["pr-bundle", "add", "affected", "useFoo", "--kind", "function", "--blast-radius", "12"],
    )
    assert r1.exit_code == 0, r1.output
    r2 = _invoke(cli_runner, ["pr-bundle", "add", "affected", "useBar"])
    assert r2.exit_code == 0, r2.output
    # Duplicate -- should not be appended.
    _invoke(cli_runner, ["pr-bundle", "add", "affected", "useFoo"])

    bundle = _read_bundle_file(bundle_project)
    names = [s["name"] for s in bundle["affected_symbols"]]
    assert names == ["useFoo", "useBar"]
    foo = next(s for s in bundle["affected_symbols"] if s["name"] == "useFoo")
    assert foo["kind"] == "function"
    assert foo["blast_radius"] == 12


# ---------------------------------------------------------------------------
# 4. add risk appends
# ---------------------------------------------------------------------------


def test_add_risk_appends(cli_runner, bundle_project):
    _invoke(cli_runner, ["pr-bundle", "init", "--intent", "x"])
    r = _invoke(
        cli_runner,
        [
            "pr-bundle",
            "add",
            "risk",
            "blast radius high",
            "--severity",
            "H",
            "--source-command",
            "roam preflight useFoo",
        ],
    )
    assert r.exit_code == 0, r.output
    bundle = _read_bundle_file(bundle_project)
    assert len(bundle["risks"]) == 1
    risk = bundle["risks"][0]
    assert risk["severity"] == "H"
    assert risk["description"] == "blast radius high"
    assert risk["source_command"] == "roam preflight useFoo"


# ---------------------------------------------------------------------------
# 5. add test-run
# ---------------------------------------------------------------------------


def test_add_test_run(cli_runner, bundle_project):
    _invoke(cli_runner, ["pr-bundle", "init", "--intent", "x"])
    r = _invoke(
        cli_runner,
        [
            "pr-bundle",
            "add",
            "test-run",
            "tests/test_retry.py",
            "--passed",
            "--duration-ms",
            "421",
        ],
    )
    assert r.exit_code == 0, r.output
    bundle = _read_bundle_file(bundle_project)
    assert len(bundle["tests_run"]) == 1
    run = bundle["tests_run"][0]
    assert run["test_file"] == "tests/test_retry.py"
    assert run["passed"] is True
    assert run["duration_ms"] == 421
    assert run["ran_at"].endswith("Z")


# ---------------------------------------------------------------------------
# 6. emit returns a complete envelope (happy path)
# ---------------------------------------------------------------------------


def test_emit_returns_complete_envelope(cli_runner, bundle_project):
    _invoke(cli_runner, ["pr-bundle", "init", "--intent", "Add retry to S3 upload"])
    _invoke(
        cli_runner,
        ["pr-bundle", "add", "affected", "useRetry", "--blast-radius", "5"],
    )
    _invoke(cli_runner, ["pr-bundle", "add", "risk", "external API"])
    _invoke(
        cli_runner,
        [
            "pr-bundle",
            "add",
            "test-required",
            "tests/test_retry.py",
            "--reason",
            "covers retry path",
        ],
    )
    _invoke(
        cli_runner,
        ["pr-bundle", "add", "test-run", "tests/test_retry.py", "--passed"],
    )
    _invoke(
        cli_runner,
        ["pr-bundle", "add", "context-cmd", "roam preflight useRetry"],
    )

    result = _invoke(cli_runner, ["--json", "pr-bundle", "emit", "--no-auto-collect"])
    assert result.exit_code == 0, result.output
    data = parse_json_output(result, command="pr-bundle")

    assert data["summary"]["state"] == "complete"
    assert data["summary"]["partial_success"] is False
    assert data["summary"]["missing_proofs"] == []
    assert "PR proof bundle complete" in data["summary"]["verdict"]
    assert data["intent"] == "Add retry to S3 upload"
    assert any(s["name"] == "useRetry" for s in data["affected_symbols"])
    assert any(r["description"] == "external API" for r in data["risks"])
    assert len(data["tests_run"]) == 1


# ---------------------------------------------------------------------------
# 7. validate -- missing intent fails
# ---------------------------------------------------------------------------


def test_validate_missing_intent_fails(cli_runner, bundle_project):
    _invoke(cli_runner, ["pr-bundle", "init", "--intent", ""])
    # Add other bits so only intent is missing.
    _invoke(
        cli_runner,
        ["pr-bundle", "add", "affected", "useFoo", "--blast-radius", "3"],
    )
    _invoke(cli_runner, ["pr-bundle", "add", "context-cmd", "roam preflight useFoo"])

    result = _invoke(cli_runner, ["--json", "pr-bundle", "validate"])
    # Without --strict, exit is 0 -- but state must be incomplete.
    assert result.exit_code == 0, result.output
    data = parse_json_output(result, command="pr-bundle-validate")
    assert data["summary"]["state"] == "incomplete"
    assert data["summary"]["partial_success"] is True
    missing = data["summary"]["missing_proofs"]
    assert any("intent" in m for m in missing), missing


# ---------------------------------------------------------------------------
# 8. validate -- missing context fails
# ---------------------------------------------------------------------------


def test_validate_missing_context_fails(cli_runner, bundle_project):
    _invoke(cli_runner, ["pr-bundle", "init", "--intent", "Add retry"])
    _invoke(
        cli_runner,
        ["pr-bundle", "add", "affected", "useFoo", "--blast-radius", "3"],
    )
    # Deliberately omit any context-reading command.

    result = _invoke(cli_runner, ["--json", "pr-bundle", "validate"])
    assert result.exit_code == 0
    data = parse_json_output(result, command="pr-bundle-validate")
    assert data["summary"]["state"] == "incomplete"
    missing = " ".join(data["summary"]["missing_proofs"])
    assert "context_read.commands_run" in missing


# ---------------------------------------------------------------------------
# 9. auto-collect pulls from .roam/responses/
# ---------------------------------------------------------------------------


def test_auto_collect_pulls_from_responses(cli_runner, bundle_project):
    _invoke(cli_runner, ["pr-bundle", "init", "--intent", "Add retry"])

    # Drop a fake response envelope as if some prior roam command had written
    # to .roam/responses/. The bundle should fold its findings on emit.
    responses = bundle_project / ".roam" / "responses"
    responses.mkdir(parents=True, exist_ok=True)
    fake = {
        "command": "preflight",
        "summary": {"verdict": "high blast radius"},
        "affected_symbols": [
            {"name": "useRetry", "kind": "function", "file": "src/s3.py", "blast_radius": 18},
        ],
        "risks": [
            {"severity": "H", "description": "blast radius exceeds 10 callers"},
        ],
        "agent_contract": {
            "facts": ["useRetry has 18 callers"],
            "next_commands": ["roam impact useRetry"],
        },
    }
    # Bump mtime forward so the since-filter passes deterministically.
    fake_path = responses / "abcd1234.json"
    fake_path.write_text(json.dumps(fake), encoding="utf-8")
    now = fake_path.stat().st_atime
    os.utime(fake_path, (now + 1, now + 1))

    result = _invoke(cli_runner, ["--json", "pr-bundle", "emit"])
    assert result.exit_code == 0, result.output
    data = parse_json_output(result, command="pr-bundle")

    # The findings folded in.
    names = [s["name"] for s in data["affected_symbols"]]
    assert "useRetry" in names, f"affected_symbols={data['affected_symbols']}"
    risk_descs = [r["description"] for r in data["risks"]]
    assert any("blast radius" in d for d in risk_descs), risk_descs
    cmds = data["context_read"]["commands_run"]
    assert any("roam preflight" in c for c in cmds), cmds

    # auto_collect telemetry surfaced under summary (W15.2 envelope reshape).
    auto = data["summary"].get("auto_collect", {})
    assert auto.get("enabled") is True
    assert auto.get("envelopes_scanned", 0) >= 1


# W15.2 envelope reshape — Pattern 3 consistency assertion. auto_collect MUST
# live under ``summary``, never at the top level. This test pins the contract
# so a future refactor can't silently restore the split-brain layout.
def test_auto_collect_lives_under_summary(cli_runner, bundle_project):
    """auto_collect telemetry must be ``summary.auto_collect``, NOT top-level."""
    _invoke(cli_runner, ["pr-bundle", "init", "--intent", "test reshape"])
    result = _invoke(cli_runner, ["--json", "pr-bundle", "emit"])
    assert result.exit_code == 0, result.output
    data = parse_json_output(result, command="pr-bundle")
    # Lives under summary.
    assert "auto_collect" in data["summary"], (
        f"summary.auto_collect missing — W15.2 envelope reshape broken; "
        f"summary keys={list(data['summary'].keys())}"
    )
    # Does NOT live at the top level (Pattern 3 split-brain regression guard).
    assert "auto_collect" not in data or data.get("auto_collect") is None, (
        f"top-level auto_collect found — Pattern 3 split-brain regressed; "
        f"top-level={data.get('auto_collect')!r}"
    )
    # The block's contract is still intact.
    auto = data["summary"]["auto_collect"]
    assert "enabled" in auto
    assert "envelopes_scanned" in auto


# ---------------------------------------------------------------------------
# 10. strict validate exits 5 on incomplete
# ---------------------------------------------------------------------------


def test_strict_validate_exits_5(cli_runner, bundle_project):
    # Empty bundle -- nothing satisfies validation.
    _invoke(cli_runner, ["pr-bundle", "init", "--intent", ""])
    result = _invoke(cli_runner, ["--json", "pr-bundle", "validate", "--strict"])
    assert result.exit_code == 5, f"expected exit 5, got {result.exit_code}: {result.output}"


# ---------------------------------------------------------------------------
# 11. emit with partial data marks incomplete
# ---------------------------------------------------------------------------


def test_emit_with_partial_data_marks_incomplete(cli_runner, bundle_project):
    _invoke(cli_runner, ["pr-bundle", "init", "--intent", "Add retry"])
    # Intent is set, but nothing else. emit should still succeed, but
    # report state=incomplete + list the missing pieces.
    result = _invoke(cli_runner, ["--json", "pr-bundle", "emit", "--no-auto-collect"])
    assert result.exit_code == 0, result.output
    data = parse_json_output(result, command="pr-bundle")
    assert data["summary"]["state"] == "incomplete"
    assert data["summary"]["partial_success"] is True
    assert len(data["summary"]["missing_proofs"]) >= 2  # at least affected + context + verdict


# ---------------------------------------------------------------------------
# 12. add non-goal records text
# ---------------------------------------------------------------------------


def test_add_non_goal_records_text(cli_runner, bundle_project):
    _invoke(cli_runner, ["pr-bundle", "init", "--intent", "x"])
    r = _invoke(
        cli_runner,
        ["pr-bundle", "add", "non-goal", "Don't change credential provider"],
    )
    assert r.exit_code == 0, r.output
    bundle = _read_bundle_file(bundle_project)
    assert "Don't change credential provider" in bundle["known_non_goals"]


# ---------------------------------------------------------------------------
# 13. operating on a non-existent bundle yields a clear errored envelope
# ---------------------------------------------------------------------------


def test_add_without_init_returns_not_initialized(cli_runner, bundle_project):
    result = _invoke(cli_runner, ["--json", "pr-bundle", "add", "affected", "useFoo"])
    assert result.exit_code == 2, result.output
    data = parse_json_output(result, command="pr-bundle") if result.exit_code == 0 else json.loads(
        getattr(result, "stdout", None) or result.output
    )
    assert data["summary"]["state"] == "not_initialized"
    assert "roam pr-bundle init" in data["summary"]["verdict"]


# ---------------------------------------------------------------------------
# 14. W20.5 — --strict flag on emit + validate (CI gating)
# ---------------------------------------------------------------------------


def _populate_complete_bundle(cli_runner) -> None:
    """Helper: drive the CLI to a state where _validate_bundle() returns complete."""
    _invoke(cli_runner, ["pr-bundle", "init", "--intent", "Add retry to S3 upload"])
    _invoke(
        cli_runner,
        ["pr-bundle", "add", "affected", "useRetry", "--blast-radius", "5"],
    )
    _invoke(cli_runner, ["pr-bundle", "add", "risk", "external API"])
    _invoke(
        cli_runner,
        [
            "pr-bundle",
            "add",
            "test-required",
            "tests/test_retry.py",
            "--reason",
            "covers retry path",
        ],
    )
    _invoke(
        cli_runner,
        ["pr-bundle", "add", "test-run", "tests/test_retry.py", "--passed"],
    )
    _invoke(
        cli_runner,
        ["pr-bundle", "add", "context-cmd", "roam preflight useRetry"],
    )


def test_validate_strict_exits_5_on_incomplete(cli_runner, bundle_project):
    """validate --strict on a bundle missing proofs exits 5."""
    # Init with empty intent so the bundle is missing intent + affected + context.
    _invoke(cli_runner, ["pr-bundle", "init", "--intent", ""])
    result = _invoke(cli_runner, ["--json", "pr-bundle", "validate", "--strict"])
    assert result.exit_code == 5, (
        f"expected exit 5 on incomplete bundle, got {result.exit_code}: {result.output}"
    )


def test_validate_strict_exits_0_on_complete(cli_runner, bundle_project):
    """validate --strict on a fully populated bundle exits 0."""
    _populate_complete_bundle(cli_runner)
    result = _invoke(cli_runner, ["--json", "pr-bundle", "validate", "--strict"])
    assert result.exit_code == 0, (
        f"expected exit 0 on complete bundle, got {result.exit_code}: {result.output}"
    )
    data = parse_json_output(result, command="pr-bundle-validate")
    assert data["summary"]["state"] == "complete"


def test_emit_strict_exits_5_on_incomplete(cli_runner, bundle_project):
    """emit --strict on an incomplete bundle exits 5 (CI gating)."""
    _invoke(cli_runner, ["pr-bundle", "init", "--intent", "Add retry"])
    # Intent only — no affected, no context-cmd, no verdict signal.
    result = _invoke(
        cli_runner,
        ["--json", "pr-bundle", "emit", "--no-auto-collect", "--strict"],
    )
    assert result.exit_code == 5, (
        f"expected exit 5 on incomplete emit, got {result.exit_code}: {result.output}"
    )
    # Envelope MUST still be echoed before the non-zero exit, so reviewers
    # see which proofs are missing. Parse manually -- parse_json_output
    # asserts exit_code == 0 which is wrong for the strict-gate case.
    raw = getattr(result, "stdout", None) or result.output
    data = json.loads(raw)
    assert data["summary"]["state"] == "incomplete"
    assert data["summary"]["partial_success"] is True


def test_emit_strict_exits_0_on_complete(cli_runner, bundle_project):
    """emit --strict on a fully populated bundle exits 0."""
    _populate_complete_bundle(cli_runner)
    result = _invoke(
        cli_runner,
        ["--json", "pr-bundle", "emit", "--no-auto-collect", "--strict"],
    )
    assert result.exit_code == 0, (
        f"expected exit 0 on complete emit, got {result.exit_code}: {result.output}"
    )
    data = parse_json_output(result, command="pr-bundle")
    assert data["summary"]["state"] == "complete"
    assert data["summary"]["partial_success"] is False


def test_emit_without_strict_returns_partial_success_envelope_for_incomplete(
    cli_runner, bundle_project
):
    """Default behaviour (no --strict): incomplete bundle still exits 0."""
    _invoke(cli_runner, ["pr-bundle", "init", "--intent", "Add retry"])
    result = _invoke(
        cli_runner,
        ["--json", "pr-bundle", "emit", "--no-auto-collect"],
    )
    # Preserves existing behaviour: exit 0 even when incomplete.
    assert result.exit_code == 0, result.output
    data = parse_json_output(result, command="pr-bundle")
    assert data["summary"]["state"] == "incomplete"
    assert data["summary"]["partial_success"] is True


# ---------------------------------------------------------------------------
# 15. W21.4 — --strict-resolved flag (CI gating on ghost symbols)
# ---------------------------------------------------------------------------
#
# W21.3 shipped --strict which exits 5 on structural incompleteness
# (missing intent / affected / context-cmd / tests / verdict signal).
# But W21.2 surfaced ghost symbols (resolution_state="not_found"/etc.)
# without making them block strict gating: an agent could pile up
# affected_symbols with names that don't exist in the index and pass.
#
# --strict-resolved is the opt-in flag that closes that hole. Additive:
# combined with --strict it exits 5; alone it still exits 0 and the
# unresolved count surfaces in the envelope summary (Pattern 2).


def _pin_w21_branch(proj):
    """Pin branch for W21.4 tests so the bundle path is deterministic."""
    subprocess.run(
        ["git", "checkout", "-B", "w21-4-branch"],
        cwd=proj,
        capture_output=True,
    )


def _populate_complete_resolved_bundle(cli_runner) -> None:
    """Drive the CLI to a state where the bundle is structurally complete
    AND every affected_symbol resolves cleanly in the index.

    Differs from ``_populate_complete_bundle`` (which uses ``useRetry``
    against a non-indexed repo: ``resolution_state="no_db"``). Here we
    pick a symbol the ``project_factory`` indexer actually saw.
    """
    _invoke(cli_runner, ["pr-bundle", "init", "--intent", "Refactor real_symbol"])
    _invoke(
        cli_runner,
        ["pr-bundle", "add", "affected", "real_symbol", "--blast-radius", "1"],
    )
    _invoke(cli_runner, ["pr-bundle", "add", "risk", "behavior change"])
    _invoke(
        cli_runner,
        [
            "pr-bundle",
            "add",
            "test-required",
            "tests/test_real.py",
            "--reason",
            "covers refactor",
        ],
    )
    _invoke(
        cli_runner,
        ["pr-bundle", "add", "test-run", "tests/test_real.py", "--passed"],
    )
    _invoke(
        cli_runner,
        ["pr-bundle", "add", "context-cmd", "roam preflight real_symbol"],
    )


def test_emit_strict_resolved_exits_5_on_ghost_symbols(
    project_factory, cli_runner, monkeypatch
):
    """A bundle is structurally complete but contains a ghost symbol;
    ``--strict --strict-resolved`` MUST exit 5 (CI block).

    This is the load-bearing W21.4 behavior: --strict alone (W21.3)
    would pass, --strict-resolved closes the silent-SAFE hole.
    """
    proj = project_factory(
        {
            "src/real.py": "def real_symbol():\n    return 1\n",
        }
    )
    _pin_w21_branch(proj)
    monkeypatch.chdir(proj)

    _populate_complete_resolved_bundle(cli_runner)
    # Now add ONE ghost on top. The bundle is structurally still
    # complete -- intent/affected/context-cmd/tests/verdict all set --
    # but it now has an unresolved symbol.
    _invoke(cli_runner, ["pr-bundle", "add", "affected", "ghost_zzz_xyz"])

    result = _invoke(
        cli_runner,
        [
            "--json",
            "pr-bundle",
            "emit",
            "--no-auto-collect",
            "--strict",
            "--strict-resolved",
        ],
    )
    assert result.exit_code == 5, (
        f"expected exit 5 with --strict --strict-resolved on ghost-symbol "
        f"bundle, got {result.exit_code}: {result.output}"
    )
    raw = getattr(result, "stdout", None) or result.output
    data = json.loads(raw)
    # The unresolved-symbol miss landed in missing_proofs.
    assert data["summary"]["state"] == "incomplete"
    assert data["summary"]["unresolved_affected_symbols_count"] >= 1
    assert data["summary"]["strict_resolved"] is True
    missing = data["summary"]["missing_proofs"]
    assert any("unresolved_affected_symbols" in m for m in missing), missing


def test_emit_strict_resolved_exits_0_on_all_resolved(
    project_factory, cli_runner, monkeypatch
):
    """A structurally-complete bundle whose every affected_symbol
    resolves cleanly MUST exit 0 under ``--strict --strict-resolved``.

    Confirms the new flag is not over-eager: clean bundles still pass.
    """
    proj = project_factory(
        {
            "src/real.py": "def real_symbol():\n    return 1\n",
        }
    )
    _pin_w21_branch(proj)
    monkeypatch.chdir(proj)

    _populate_complete_resolved_bundle(cli_runner)

    result = _invoke(
        cli_runner,
        [
            "--json",
            "pr-bundle",
            "emit",
            "--no-auto-collect",
            "--strict",
            "--strict-resolved",
        ],
    )
    assert result.exit_code == 0, (
        f"expected exit 0 with --strict --strict-resolved on all-resolved "
        f"bundle, got {result.exit_code}: {result.output}"
    )
    data = parse_json_output(result, command="pr-bundle")
    assert data["summary"]["state"] == "complete"
    assert data["summary"]["partial_success"] is False
    assert data["summary"]["unresolved_affected_symbols_count"] == 0
    assert data["summary"]["strict_resolved"] is True


def test_emit_strict_without_strict_resolved_ignores_ghosts(
    project_factory, cli_runner, monkeypatch
):
    """Preserves W21.3: ``--strict`` alone exits 0 on a structurally
    complete bundle, even when it contains ghost symbols.

    Without this guarantee, --strict-resolved is not additive -- the
    new flag would be the only way to get back the W21.3 contract.
    """
    proj = project_factory(
        {
            "src/real.py": "def real_symbol():\n    return 1\n",
        }
    )
    _pin_w21_branch(proj)
    monkeypatch.chdir(proj)

    _populate_complete_resolved_bundle(cli_runner)
    # Add a ghost; --strict alone should still pass (W21.3 contract).
    _invoke(cli_runner, ["pr-bundle", "add", "affected", "ghost_zzz_xyz"])

    result = _invoke(
        cli_runner,
        [
            "--json",
            "pr-bundle",
            "emit",
            "--no-auto-collect",
            "--strict",
        ],
    )
    assert result.exit_code == 0, (
        f"expected exit 0 with --strict alone on ghost-symbol bundle "
        f"(W21.3 contract), got {result.exit_code}: {result.output}"
    )
    data = parse_json_output(result, command="pr-bundle")
    # Structural state is still complete -- ghost does not bump structural.
    assert data["summary"]["state"] == "complete"
    # The unresolved count is still surfaced (Pattern 2).
    assert data["summary"]["unresolved_affected_symbols_count"] >= 1
    assert data["summary"]["strict_resolved"] is False


# ---------------------------------------------------------------------------
# 16. W26.5 — --strict-resolved flips exit code on the SAME complete bundle
# ---------------------------------------------------------------------------
#
# W26.3 ("--ci implies --strict-resolved") landed an envelope-field
# assertion rather than an exit-code-flip assertion because its fixture
# (intent-only bundle) was structurally incomplete, so --strict alone
# already exited 5 -- toggling --strict-resolved couldn't move the exit
# code. The author flagged the follow-up: a structurally-COMPLETE bundle
# with one unresolved affected symbol, where flipping --strict-resolved
# is the ONLY thing that flips the exit code 0 <-> 5.
#
# These tests close that gap. They prove --strict-resolved is the
# load-bearing gate -- independent of the structural-completeness gate
# that --strict operates on -- by running BOTH invocations against the
# SAME on-disk bundle in a single test. The 0->5 flip is then
# unmissable (one assert pair per test).


def test_emit_strict_resolved_flips_exit_code_on_complete_bundle(
    project_factory, cli_runner, monkeypatch
):
    """Same complete bundle + 1 ghost, ``emit --strict``:
       - ``--no-strict-resolved`` -> exit 0
       - ``--strict-resolved``    -> exit 5

    Proves W22.1's --strict-resolved gate is the SOLE driver of the
    flip on this fixture. Independent of W21.3's structural gate.
    """
    proj = project_factory(
        {
            "src/real.py": "def real_symbol():\n    return 1\n",
        }
    )
    _pin_w21_branch(proj)
    monkeypatch.chdir(proj)

    _populate_complete_resolved_bundle(cli_runner)
    # Add one ghost. Structurally the bundle is still complete; the only
    # thing --strict-resolved adds is treating the ghost as a missing proof.
    _invoke(cli_runner, ["pr-bundle", "add", "affected", "ghost_zzz_xyz"])

    # First invocation: --strict alone (or with --no-strict-resolved).
    # Structural state is "complete", so exit 0.
    result_off = _invoke(
        cli_runner,
        [
            "--json",
            "pr-bundle",
            "emit",
            "--no-auto-collect",
            "--strict",
            "--no-strict-resolved",
        ],
    )
    assert result_off.exit_code == 0, (
        f"expected exit 0 with --strict --no-strict-resolved on complete "
        f"bundle with 1 ghost, got {result_off.exit_code}: {result_off.output}"
    )
    data_off = parse_json_output(result_off, command="pr-bundle")
    assert data_off["summary"]["state"] == "complete"
    assert data_off["summary"]["unresolved_affected_symbols_count"] >= 1
    assert data_off["summary"]["strict_resolved"] is False

    # Second invocation: same bundle, --strict-resolved flipped on.
    # The ghost is now a missing proof -> state="incomplete" -> exit 5.
    result_on = _invoke(
        cli_runner,
        [
            "--json",
            "pr-bundle",
            "emit",
            "--no-auto-collect",
            "--strict",
            "--strict-resolved",
        ],
    )
    assert result_on.exit_code == 5, (
        f"expected exit 5 with --strict --strict-resolved on complete "
        f"bundle with 1 ghost, got {result_on.exit_code}: {result_on.output}"
    )
    raw = getattr(result_on, "stdout", None) or result_on.output
    data_on = json.loads(raw)
    assert data_on["summary"]["state"] == "incomplete"
    assert data_on["summary"]["unresolved_affected_symbols_count"] >= 1
    assert data_on["summary"]["strict_resolved"] is True
    # The unresolved-symbol miss landed in missing_proofs.
    missing = data_on["summary"]["missing_proofs"]
    assert any("unresolved_affected_symbols" in m for m in missing), missing


def test_validate_strict_resolved_flips_exit_code_on_complete_bundle(
    project_factory, cli_runner, monkeypatch
):
    """Same complete bundle + 1 ghost, ``validate --strict``:
       - ``--no-strict-resolved`` -> exit 0
       - ``--strict-resolved``    -> exit 5

    Mirror of the emit-side test for the validate subcommand.
    Validate is the read-only checkpoint reviewers run on a bundle they
    didn't author -- the gate semantics MUST match emit exactly.
    """
    proj = project_factory(
        {
            "src/real.py": "def real_symbol():\n    return 1\n",
        }
    )
    _pin_w21_branch(proj)
    monkeypatch.chdir(proj)

    _populate_complete_resolved_bundle(cli_runner)
    _invoke(cli_runner, ["pr-bundle", "add", "affected", "ghost_zzz_xyz"])

    # --strict --no-strict-resolved: structurally complete -> exit 0.
    result_off = _invoke(
        cli_runner,
        [
            "--json",
            "pr-bundle",
            "validate",
            "--strict",
            "--no-strict-resolved",
        ],
    )
    assert result_off.exit_code == 0, (
        f"expected exit 0 with validate --strict --no-strict-resolved on "
        f"complete bundle with 1 ghost, got {result_off.exit_code}: "
        f"{result_off.output}"
    )
    data_off = parse_json_output(result_off, command="pr-bundle-validate")
    assert data_off["summary"]["state"] == "complete"
    assert data_off["summary"]["unresolved_affected_symbols_count"] >= 1
    assert data_off["summary"]["strict_resolved"] is False

    # --strict --strict-resolved: ghost becomes a missing proof -> exit 5.
    result_on = _invoke(
        cli_runner,
        [
            "--json",
            "pr-bundle",
            "validate",
            "--strict",
            "--strict-resolved",
        ],
    )
    assert result_on.exit_code == 5, (
        f"expected exit 5 with validate --strict --strict-resolved on "
        f"complete bundle with 1 ghost, got {result_on.exit_code}: "
        f"{result_on.output}"
    )
    raw = getattr(result_on, "stdout", None) or result_on.output
    data_on = json.loads(raw)
    assert data_on["summary"]["state"] == "incomplete"
    assert data_on["summary"]["unresolved_affected_symbols_count"] >= 1
    assert data_on["summary"]["strict_resolved"] is True
    missing = data_on["summary"]["missing_proofs"]
    assert any("unresolved_affected_symbols" in m for m in missing), missing
