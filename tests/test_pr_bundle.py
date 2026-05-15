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
# 1b. W521 - pr-bundle init populates commit_sha unconditionally
# ---------------------------------------------------------------------------


def test_init_populates_commit_sha_in_git_repo(cli_runner, bundle_project):
    """W521: pr-bundle init must stamp ``commit_sha`` on the envelope
    at init time when the workspace is a git repo. Before W521 the
    field was absent until ``emit`` re-derived it via the W509 fallback;
    the producer-side fix moves identity resolution to init so the
    persisted bundle AND every subsequent envelope carry the SHA.
    """
    import subprocess

    head_sha = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=bundle_project,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    assert head_sha, "test fixture must have a real HEAD sha"

    result = _invoke(cli_runner, ["--json", "pr-bundle", "init", "--intent", "x"])
    assert result.exit_code == 0, result.output
    data = parse_json_output(result, command="pr-bundle-init")

    # Top-level commit_sha is what emit_vsa.py probes via
    # ``envelope.get("commit_sha")``. Must match git rev-parse HEAD.
    assert data.get("commit_sha") == head_sha, (
        f"W521: envelope.commit_sha must equal git rev-parse HEAD; "
        f"got {data.get('commit_sha')!r}, expected {head_sha!r}"
    )
    # No pre_warnings on the happy path (git repo with a real SHA).
    assert not data.get("pre_warnings"), data.get("pre_warnings")

    # Persisted bundle carries commit_sha under git.commit_sha so the
    # SHA is portable across subsequent pr-bundle commands without
    # re-running git.
    bundle = _read_bundle_file(bundle_project)
    assert bundle["git"].get("commit_sha") == head_sha


def test_init_emits_pre_warning_outside_git_repo(cli_runner, tmp_path, monkeypatch):
    """W521: when the workspace is NOT a git repo, pr-bundle init must
    emit a ``pre_warnings`` entry explaining why ``commit_sha`` is
    absent (Pattern 2 — explicit absence beats silent absence).
    """
    proj = tmp_path / "no-git-repo"
    proj.mkdir()
    (proj / ".roam").mkdir()  # avoid find_project_root walking elsewhere
    (proj / "main.py").write_text("pass\n")
    monkeypatch.chdir(proj)

    result = _invoke(cli_runner, ["--json", "pr-bundle", "init", "--intent", "x"])
    assert result.exit_code == 0, result.output
    data = parse_json_output(result, command="pr-bundle-init")

    # No commit_sha on the envelope (or empty/None — both express
    # "absent" downstream).
    assert not data.get("commit_sha")
    # pre_warnings names the missing-commit-sha condition explicitly.
    pre = data.get("pre_warnings") or []
    assert any("commit_sha unresolved" in w for w in pre), pre


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


# ---------------------------------------------------------------------------
# 17. W189 — actor block + approvals + accepted_risks producers
# ---------------------------------------------------------------------------
#
# Backstory: the W186 8-evidence-questions gap audit found that the
# collector at ``src/roam/evidence/collector.py:551-569`` ALREADY probes
# for an ``actor`` block on the pr-bundle envelope, BUT
# ``cmd_pr_bundle.py`` never produced one. Every ``ChangeEvidence``
# packet built from a real pr-bundle had empty ``agent_id`` and
# ``human_actor`` fields. W189 closes that gap. These tests pin down:
#
#   1. The envelope carries an ``actor`` key with all 6 documented fields
#      AND ``approvals`` / ``accepted_risks`` as empty top-level arrays.
#   2. The ``--agent-id`` CLI flag wins over ``ROAM_AGENT_ID``.
#   3. With no flag and no env, ``human_actor`` falls back to
#      ``git config user.email`` (the fixture sets this in ``git_init``).
#   4. ``actor_kind`` resolves from the dominant populated field
#      (``agent`` > ``ci_runner`` > ``mcp_client`` > ``tool`` > ``human`` >
#      ``external``).
#   5. ``approvals`` and ``accepted_risks`` are always present as empty
#      lists (Pattern 2 — explicit absence, never silent absence).


def _populate_minimal_emit_bundle(cli_runner) -> None:
    """Init + add the bare-minimum bits so emit returns a happy envelope."""
    _invoke(cli_runner, ["pr-bundle", "init", "--intent", "W189 actor smoke"])
    _invoke(
        cli_runner,
        ["pr-bundle", "add", "affected", "useFoo", "--blast-radius", "1"],
    )
    _invoke(cli_runner, ["pr-bundle", "add", "risk", "smoke"])
    _invoke(
        cli_runner,
        ["pr-bundle", "add", "test-required", "tests/test_foo.py"],
    )
    _invoke(
        cli_runner,
        ["pr-bundle", "add", "test-run", "tests/test_foo.py", "--passed"],
    )
    _invoke(
        cli_runner,
        ["pr-bundle", "add", "context-cmd", "roam preflight useFoo"],
    )


def test_pr_bundle_envelope_includes_actor_block(
    cli_runner, bundle_project, monkeypatch
):
    """Basic ``pr-bundle emit`` invocation has the ``actor`` key with all
    six documented fields, plus empty ``approvals`` / ``accepted_risks``
    arrays at the top level.

    The collector at ``src/roam/evidence/collector.py:551-569`` reads
    these fields; before W189 every ChangeEvidence packet had
    empty ``agent_id`` / ``human_actor`` because the producer was
    silent. Test pins down the six-field shape exactly.
    """
    # Clean env so the test result doesn't depend on the host CI runner.
    for var in (
        "ROAM_AGENT_ID",
        "ROAM_HUMAN_ACTOR",
        "ROAM_MCP_CLIENT_ID",
        "ROAM_CI_RUNNER_ID",
        "GITHUB_ACTIONS_RUN_ID",
    ):
        monkeypatch.delenv(var, raising=False)
    _populate_minimal_emit_bundle(cli_runner)
    result = _invoke(
        cli_runner, ["--json", "pr-bundle", "emit", "--no-auto-collect"]
    )
    assert result.exit_code == 0, result.output
    data = parse_json_output(result, command="pr-bundle")

    # 1. actor block exists with the documented six fields.
    #    W290 added per-field ``provenance_<field>`` sub-keys for any
    #    field that resolved through a known channel (cli flag / env /
    #    git config / run-ledger). The six documented fields must be
    #    present; provenance_* sub-keys are additive and allowed.
    assert "actor" in data, "envelope missing actor block (W189)"
    actor = data["actor"]
    expected_fields = {
        "agent_id",
        "human_actor",
        "mcp_client_id",
        "tool_id",
        "ci_runner_id",
        "actor_kind",
    }
    assert expected_fields <= set(actor.keys()), (
        f"actor block missing documented fields: "
        f"{expected_fields - set(actor.keys())}"
    )
    extra_keys = set(actor.keys()) - expected_fields
    assert all(k.startswith("provenance_") for k in extra_keys), (
        f"actor block has unexpected non-provenance keys: {extra_keys}"
    )
    # tool_id is reserved for W196 — always None today.
    assert actor["tool_id"] is None

    # 2. approvals / accepted_risks present as empty lists.
    assert data.get("approvals") == [], "approvals must be empty list, not absent"
    assert data.get("accepted_risks") == [], (
        "accepted_risks must be empty list, not absent"
    )


def test_pr_bundle_agent_id_flag_wins_over_env(
    cli_runner, bundle_project, monkeypatch
):
    """``--agent-id X`` MUST win over ``ROAM_AGENT_ID=Y`` (LAW 11)."""
    monkeypatch.setenv("ROAM_AGENT_ID", "env-agent-loses")
    # Clear unrelated env so actor_kind resolution is deterministic.
    for var in ("ROAM_MCP_CLIENT_ID", "ROAM_CI_RUNNER_ID", "GITHUB_ACTIONS_RUN_ID"):
        monkeypatch.delenv(var, raising=False)

    _populate_minimal_emit_bundle(cli_runner)
    result = _invoke(
        cli_runner,
        [
            "--json",
            "pr-bundle",
            "emit",
            "--no-auto-collect",
            "--agent-id",
            "flag-agent-wins",
        ],
    )
    assert result.exit_code == 0, result.output
    data = parse_json_output(result, command="pr-bundle")
    assert data["actor"]["agent_id"] == "flag-agent-wins", (
        f"expected --agent-id to win, got {data['actor']!r}"
    )


def test_pr_bundle_env_fallback_to_git_config(
    cli_runner, bundle_project, monkeypatch
):
    """No flag + no env -> ``human_actor`` populated from
    ``git config user.email`` (the fixture's ``git_init`` sets this)."""
    for var in (
        "ROAM_AGENT_ID",
        "ROAM_HUMAN_ACTOR",
        "ROAM_MCP_CLIENT_ID",
        "ROAM_CI_RUNNER_ID",
        "GITHUB_ACTIONS_RUN_ID",
    ):
        monkeypatch.delenv(var, raising=False)
    # Pin the git user so the assertion isn't host-dependent.
    subprocess.run(
        ["git", "config", "user.email", "alice@example.com"],
        cwd=bundle_project,
        capture_output=True,
    )

    _populate_minimal_emit_bundle(cli_runner)
    result = _invoke(
        cli_runner, ["--json", "pr-bundle", "emit", "--no-auto-collect"]
    )
    assert result.exit_code == 0, result.output
    data = parse_json_output(result, command="pr-bundle")
    assert data["actor"]["human_actor"] == "alice@example.com", (
        f"expected human_actor from git config, got {data['actor']!r}"
    )
    assert data["actor"]["agent_id"] is None
    # No agent / CI -> kind falls back to "human" when only human_actor set.
    assert data["actor"]["actor_kind"] == "human"


def test_pr_bundle_actor_kind_resolved_from_fields(
    cli_runner, bundle_project, monkeypatch
):
    """``actor_kind`` derives from the dominant populated field:
    agent_id -> 'agent'; human_actor only -> 'human'; nothing -> 'external'.

    Pins the priority chain in ``_resolve_actor_kind``: an AI-agent
    identity is the load-bearing one when present (agent-OS thesis).
    """
    for var in (
        "ROAM_AGENT_ID",
        "ROAM_HUMAN_ACTOR",
        "ROAM_MCP_CLIENT_ID",
        "ROAM_CI_RUNNER_ID",
        "GITHUB_ACTIONS_RUN_ID",
    ):
        monkeypatch.delenv(var, raising=False)
    # Suppress git config lookups entirely so the "nothing set" case is
    # achievable even when the host machine has a global ``user.email``.
    # ``GIT_CONFIG_GLOBAL=/dev/null`` neutralises ~/.gitconfig; the local
    # ``--unset`` clears anything written by the bundle_project fixture.
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", os.devnull)
    monkeypatch.setenv("GIT_CONFIG_SYSTEM", os.devnull)
    subprocess.run(
        ["git", "config", "--local", "--unset", "user.email"],
        cwd=bundle_project,
        capture_output=True,
    )
    subprocess.run(
        ["git", "config", "--local", "--unset", "user.name"],
        cwd=bundle_project,
        capture_output=True,
    )

    _populate_minimal_emit_bundle(cli_runner)

    # Case A: agent_id only -> kind="agent" (wins even when human is also set).
    result_a = _invoke(
        cli_runner,
        [
            "--json",
            "pr-bundle",
            "emit",
            "--no-auto-collect",
            "--agent-id",
            "claude-opus-4.7",
            "--human-actor",
            "bob@example.com",
        ],
    )
    assert result_a.exit_code == 0, result_a.output
    data_a = parse_json_output(result_a, command="pr-bundle")
    assert data_a["actor"]["actor_kind"] == "agent", data_a["actor"]
    assert data_a["actor"]["agent_id"] == "claude-opus-4.7"
    assert data_a["actor"]["human_actor"] == "bob@example.com"

    # Case B: human_actor only -> kind="human".
    result_b = _invoke(
        cli_runner,
        [
            "--json",
            "pr-bundle",
            "emit",
            "--no-auto-collect",
            "--human-actor",
            "carol@example.com",
        ],
    )
    assert result_b.exit_code == 0, result_b.output
    data_b = parse_json_output(result_b, command="pr-bundle")
    assert data_b["actor"]["actor_kind"] == "human", data_b["actor"]
    assert data_b["actor"]["agent_id"] is None
    assert data_b["actor"]["human_actor"] == "carol@example.com"

    # Case C: nothing set -> kind="external" (escape hatch).
    result_c = _invoke(
        cli_runner, ["--json", "pr-bundle", "emit", "--no-auto-collect"]
    )
    assert result_c.exit_code == 0, result_c.output
    data_c = parse_json_output(result_c, command="pr-bundle")
    assert data_c["actor"]["actor_kind"] == "external", data_c["actor"]
    assert data_c["actor"]["agent_id"] is None
    assert data_c["actor"]["human_actor"] is None


def test_pr_bundle_emits_empty_approvals_and_accepted_risks(
    cli_runner, bundle_project, monkeypatch
):
    """Both top-level arrays are present with empty lists, not absent
    (Pattern 2 — explicit absence beats silent absence)."""
    for var in ("ROAM_AGENT_ID", "ROAM_HUMAN_ACTOR"):
        monkeypatch.delenv(var, raising=False)
    _populate_minimal_emit_bundle(cli_runner)
    result = _invoke(
        cli_runner, ["--json", "pr-bundle", "emit", "--no-auto-collect"]
    )
    assert result.exit_code == 0, result.output
    data = parse_json_output(result, command="pr-bundle")
    assert "approvals" in data, "approvals must be a top-level key"
    assert "accepted_risks" in data, "accepted_risks must be a top-level key"
    assert data["approvals"] == []
    assert data["accepted_risks"] == []


# ---------------------------------------------------------------------------
# W236a / W232: secret scrub on actor block + verdict
# ---------------------------------------------------------------------------


def test_pr_bundle_redacts_github_pat_in_human_actor(
    cli_runner, bundle_project, monkeypatch
):
    """ROAM_HUMAN_ACTOR containing a GitHub PAT MUST be scrubbed to
    ``[REDACTED]`` on the envelope's actor block, and ``redactions``
    must contain ``"secret"`` (W236a / W232).

    Before this fix the PAT flowed verbatim into ``actor.human_actor``
    and survived into ``ChangeEvidence.human_actor`` /
    ``ActorRef.actor_id``.
    """
    monkeypatch.setenv(
        "ROAM_HUMAN_ACTOR",
        "alice+ghp_abc1234567890abc1234567890abc12345678@example.com",
    )
    _populate_minimal_emit_bundle(cli_runner)
    result = _invoke(
        cli_runner, ["--json", "pr-bundle", "emit", "--no-auto-collect"]
    )
    assert result.exit_code == 0, result.output
    data = parse_json_output(result, command="pr-bundle")
    assert "ghp_abc1234567890abc1234567890abc12345678" not in data["actor"][
        "human_actor"
    ], data["actor"]["human_actor"]
    assert "[REDACTED]" in data["actor"]["human_actor"]
    assert "secret" in (data.get("redactions") or []), (
        f"expected 'secret' in redactions; got {data.get('redactions')}"
    )


def test_pr_bundle_redacts_openai_key_in_verdict(
    cli_runner, bundle_project, monkeypatch
):
    """An OpenAI-key-shaped substring in any verdict-rendered field MUST
    be scrubbed before the envelope leaves the producer.

    The init path bakes ``--intent`` into the verdict, so an intent
    carrying a key-shaped substring would otherwise leak through. The
    finalisation pass scrubs the verdict and stamps ``redactions``.
    """
    for var in ("ROAM_AGENT_ID", "ROAM_HUMAN_ACTOR"):
        monkeypatch.delenv(var, raising=False)
    leaked_key = "sk-proj-abc1234567890abc1234567890"
    result = _invoke(
        cli_runner,
        [
            "--json",
            "pr-bundle",
            "init",
            "--intent",
            f"sync with {leaked_key}",
        ],
    )
    assert result.exit_code == 0, result.output
    data = parse_json_output(result, command="pr-bundle-init")
    verdict = data["summary"]["verdict"]
    assert leaked_key not in verdict, (
        f"OpenAI key leaked into verdict: {verdict!r}"
    )
    assert "[REDACTED]" in verdict
    assert "secret" in (data.get("redactions") or []), (
        f"expected 'secret' in redactions; got {data.get('redactions')}"
    )


# ---------------------------------------------------------------------------
# W224a: context_files promoted to top level
# ---------------------------------------------------------------------------


def test_pr_bundle_context_files_promoted_to_top_level(
    cli_runner, bundle_project, monkeypatch
):
    """``pr-bundle add context-file <path>`` now surfaces ``<path>`` on the
    envelope's top-level ``context_files[]`` array of ``{path,
    content_hash}`` dicts (W224a / W219).

    Before the fix, the inspected file lived only under
    ``context_read.files_inspected`` and the evidence collector (which
    probes ``context_files`` directly) saw nothing.
    """
    for var in ("ROAM_AGENT_ID", "ROAM_HUMAN_ACTOR"):
        monkeypatch.delenv(var, raising=False)
    _invoke(cli_runner, ["pr-bundle", "init", "--intent", "context files top-level"])
    _invoke(cli_runner, ["pr-bundle", "add", "context-file", "src/upload.py"])
    _invoke(cli_runner, ["pr-bundle", "add", "context-file", "src/retry.py"])
    result = _invoke(
        cli_runner, ["--json", "pr-bundle", "emit", "--no-auto-collect"]
    )
    assert result.exit_code == 0, result.output
    data = parse_json_output(result, command="pr-bundle")
    assert "context_files" in data, "envelope missing top-level context_files"
    paths = [entry.get("path") for entry in data["context_files"]]
    assert "src/upload.py" in paths
    assert "src/retry.py" in paths
    # Each row carries a content_hash key (None when not computed).
    for entry in data["context_files"]:
        assert "content_hash" in entry, entry


def test_pr_bundle_emits_empty_context_files_array(
    cli_runner, bundle_project, monkeypatch
):
    """When no context files were added, ``context_files`` is still
    present as an empty array (Pattern 2 — explicit absence)."""
    for var in ("ROAM_AGENT_ID", "ROAM_HUMAN_ACTOR"):
        monkeypatch.delenv(var, raising=False)
    _populate_minimal_emit_bundle(cli_runner)
    result = _invoke(
        cli_runner, ["--json", "pr-bundle", "emit", "--no-auto-collect"]
    )
    assert result.exit_code == 0, result.output
    data = parse_json_output(result, command="pr-bundle")
    assert "context_files" in data
    assert data["context_files"] == []


# ---------------------------------------------------------------------------
# W224b: add-approval / add-accepted-risk CLI affordances
# ---------------------------------------------------------------------------


def test_pr_bundle_add_approval_appends_to_envelope(
    cli_runner, bundle_project, monkeypatch
):
    """``roam pr-bundle add-approval --approver X --scope Y`` appends a
    row that survives into the emit envelope's top-level
    ``approvals[]`` array (W224b).
    """
    for var in ("ROAM_AGENT_ID", "ROAM_HUMAN_ACTOR"):
        monkeypatch.delenv(var, raising=False)
    _populate_minimal_emit_bundle(cli_runner)
    res_add = _invoke(
        cli_runner,
        [
            "pr-bundle",
            "add-approval",
            "--approver",
            "alice@example.com",
            "--scope",
            "pr-42",
            "--reason",
            "signed off",
        ],
    )
    assert res_add.exit_code == 0, res_add.output
    result = _invoke(
        cli_runner, ["--json", "pr-bundle", "emit", "--no-auto-collect"]
    )
    assert result.exit_code == 0, result.output
    data = parse_json_output(result, command="pr-bundle")
    approvals = data.get("approvals", [])
    assert len(approvals) == 1, approvals
    row = approvals[0]
    assert row["approver"] == "alice@example.com"
    assert row["scope"] == "pr-42"
    assert row["reason"] == "signed off"
    assert "approval_id" in row


def test_pr_bundle_add_accepted_risk_appends_to_envelope(
    cli_runner, bundle_project, monkeypatch
):
    """``roam pr-bundle add-accepted-risk --reviewer X --scope Y``
    appends a row that survives into the emit envelope's
    ``accepted_risks[]`` array (W224b).
    """
    for var in ("ROAM_AGENT_ID", "ROAM_HUMAN_ACTOR"):
        monkeypatch.delenv(var, raising=False)
    _populate_minimal_emit_bundle(cli_runner)
    res_add = _invoke(
        cli_runner,
        [
            "pr-bundle",
            "add-accepted-risk",
            "--reviewer",
            "bob@example.com",
            "--scope",
            "R-001",
            "--reason",
            "blast radius minimal",
        ],
    )
    assert res_add.exit_code == 0, res_add.output
    result = _invoke(
        cli_runner, ["--json", "pr-bundle", "emit", "--no-auto-collect"]
    )
    assert result.exit_code == 0, result.output
    data = parse_json_output(result, command="pr-bundle")
    accepted = data.get("accepted_risks", [])
    assert len(accepted) == 1, accepted
    row = accepted[0]
    assert row["reviewer"] == "bob@example.com"
    assert row["scope"] == "R-001"
    assert row["reason"] == "blast radius minimal"
    # The W219 collector reads either ``rationale`` or ``reason``; the
    # producer stamps both so the collector picks up the row reliably.
    assert row.get("rationale") == "blast radius minimal"
    assert "risk_id" in row


# ---------------------------------------------------------------------------
# W224c: mode always emitted on normal emit
# ---------------------------------------------------------------------------


def test_pr_bundle_emit_always_carries_mode(
    cli_runner, bundle_project, monkeypatch
):
    """A normal (non-blocked) emit MUST include both ``mode`` at the
    top level AND ``summary.active_mode`` so the evidence collector's
    mode probe always picks up a value (W224c / W219).
    """
    for var in ("ROAM_AGENT_ID", "ROAM_HUMAN_ACTOR", "ROAM_AGENT_MODE"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("ROAM_AGENT_MODE", "safe_edit")
    _populate_minimal_emit_bundle(cli_runner)
    result = _invoke(
        cli_runner, ["--json", "pr-bundle", "emit", "--no-auto-collect"]
    )
    assert result.exit_code == 0, result.output
    data = parse_json_output(result, command="pr-bundle")
    assert "mode" in data, "envelope missing top-level 'mode' key"
    assert data["mode"] == "safe_edit", data["mode"]
    assert data["summary"].get("active_mode") == "safe_edit", data["summary"]


def test_pr_bundle_emit_mode_unmoded_when_none_active(
    cli_runner, bundle_project, monkeypatch
):
    """When no mode resolver fires, the envelope carries
    ``mode="unmoded"`` rather than omitting the key (Pattern 2 —
    explicit absence). W224c.
    """
    for var in ("ROAM_AGENT_ID", "ROAM_HUMAN_ACTOR", "ROAM_AGENT_MODE"):
        monkeypatch.delenv(var, raising=False)
    # Patch the mode resolver to return an empty name (simulates the
    # "no mode declared" branch). The producer must then default to
    # "unmoded" rather than dropping the key.
    import roam.commands.cmd_pr_bundle as pr_bundle_mod

    def _fake_mode_blocks_emit(_root):
        return (False, "", None)

    monkeypatch.setattr(
        pr_bundle_mod, "_mode_blocks_emit", _fake_mode_blocks_emit
    )

    _populate_minimal_emit_bundle(cli_runner)
    result = _invoke(
        cli_runner, ["--json", "pr-bundle", "emit", "--no-auto-collect"]
    )
    assert result.exit_code == 0, result.output
    data = parse_json_output(result, command="pr-bundle")
    assert data.get("mode") == "unmoded", data.get("mode")
    assert data["summary"].get("active_mode") == "unmoded"


# ---------------------------------------------------------------------------
# W266 — pr-bundle envelope carries environment_refs[]
# ---------------------------------------------------------------------------


def test_pr_bundle_envelope_carries_environment_refs(
    cli_runner, bundle_project, monkeypatch,
):
    """W266: pr-bundle emit envelope MUST include environment_refs[].

    The W252 producer-coverage matrix flagged ``environment`` as the
    most under-served evidence axis - only ``pr-replay`` materialised
    EnvironmentRef rows. After W266 the pr-bundle envelope carries
    its own producer-side env signal so consumers reading the envelope
    DIRECTLY (without going through the collector) see workspace +
    branch_range + (ci_job or local_run).
    """
    # Scrub CI env so the local_run path is deterministic.
    for var in (
        "CI",
        "GITHUB_ACTIONS", "GITHUB_RUN_ID",
        "GITLAB_CI", "CI_JOB_ID",
        "BUILDKITE", "BUILDKITE_BUILD_ID",
        "CIRCLECI", "CIRCLE_BUILD_NUM",
        "JENKINS_URL", "BUILD_TAG",
        "TF_BUILD", "BUILD_BUILDID",
    ):
        monkeypatch.delenv(var, raising=False)

    _invoke(cli_runner, ["pr-bundle", "init", "--intent", "Add env refs to bundle"])
    _invoke(
        cli_runner,
        ["pr-bundle", "add", "affected", "foo", "--blast-radius", "1"],
    )
    _invoke(cli_runner, ["pr-bundle", "add", "context-cmd", "roam preflight foo"])

    result = _invoke(
        cli_runner, ["--json", "pr-bundle", "emit", "--no-auto-collect"]
    )
    assert result.exit_code == 0, result.output
    data = parse_json_output(result, command="pr-bundle")

    env_refs = data.get("environment_refs")
    assert isinstance(env_refs, list), (
        f"environment_refs must be a list, got {type(env_refs).__name__}"
    )
    assert env_refs, "environment_refs must be non-empty on a real emit"

    kinds = {r["env_kind"] for r in env_refs}
    # workspace is always present; local_run when no CI was detected.
    assert "workspace" in kinds, (
        f"expected workspace env_kind; got {kinds}"
    )
    assert "local_run" in kinds, (
        f"expected local_run env_kind in no-CI run; got {kinds}"
    )
    # branch_range is present when the bundle has a head_sha (git_init
    # in the fixture creates a commit, so it will).
    # The exact assertion is best-effort: skip when the fixture didn't
    # produce a commit (e.g. some CI sandbox without git config).
    has_branch = any(r["env_kind"] == "branch_range" for r in env_refs)
    if has_branch:
        branch_ref = next(r for r in env_refs if r["env_kind"] == "branch_range")
        assert isinstance(branch_ref["env_id"], str) and branch_ref["env_id"]


def test_pr_bundle_envelope_carries_ci_job_in_ci(
    cli_runner, bundle_project, monkeypatch,
):
    """In a CI context, pr-bundle envelope's environment_refs include ci_job."""
    # Pin a synthetic CI provider.
    for var in (
        "CI",
        "GITHUB_ACTIONS", "GITHUB_RUN_ID",
        "GITLAB_CI", "CI_JOB_ID",
        "BUILDKITE", "BUILDKITE_BUILD_ID",
        "CIRCLECI", "CIRCLE_BUILD_NUM",
        "JENKINS_URL", "BUILD_TAG",
        "TF_BUILD", "BUILD_BUILDID",
    ):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    monkeypatch.setenv("GITHUB_RUN_ID", "pr-bundle-test-42")

    _invoke(cli_runner, ["pr-bundle", "init", "--intent", "CI context test"])
    _invoke(
        cli_runner,
        ["pr-bundle", "add", "affected", "bar", "--blast-radius", "1"],
    )
    _invoke(cli_runner, ["pr-bundle", "add", "context-cmd", "roam preflight bar"])

    result = _invoke(
        cli_runner, ["--json", "pr-bundle", "emit", "--no-auto-collect"]
    )
    assert result.exit_code == 0, result.output
    data = parse_json_output(result, command="pr-bundle")

    env_refs = data.get("environment_refs") or []
    kinds = {r["env_kind"] for r in env_refs}
    assert "ci_job" in kinds, (
        f"expected ci_job env_kind in CI run; got {kinds}"
    )
    ci = next(r for r in env_refs if r["env_kind"] == "ci_job")
    assert ci["env_id"] == "pr-bundle-test-42", (
        f"ci_job env_id should match GITHUB_RUN_ID; got {ci['env_id']!r}"
    )
    assert "local_run" not in kinds, (
        f"local_run must NOT appear in a CI run; got {kinds}"
    )


# ---------------------------------------------------------------------------
# W268 - pr-bundle envelope carries permits[] / leases[]
# ---------------------------------------------------------------------------


def test_pr_bundle_envelope_always_emits_permits_empty(
    cli_runner, bundle_project,
):
    """W268: pr-bundle emit ALWAYS emits ``permits[]`` (empty in a fresh repo).

    Pattern 2 always-emit contract: with no ``.roam/permits/`` directory,
    the envelope still carries the key as ``[]`` so consumers can rely
    on its presence. The W252 producer-coverage matrix flagged this gap.
    """
    _invoke(cli_runner, ["pr-bundle", "init", "--intent", "Add permits test"])
    _invoke(
        cli_runner,
        ["pr-bundle", "add", "affected", "foo", "--blast-radius", "1"],
    )
    _invoke(cli_runner, ["pr-bundle", "add", "context-cmd", "roam preflight foo"])

    result = _invoke(
        cli_runner, ["--json", "pr-bundle", "emit", "--no-auto-collect"]
    )
    assert result.exit_code == 0, result.output
    data = parse_json_output(result, command="pr-bundle")

    permits = data.get("permits")
    assert isinstance(permits, list), (
        f"permits must be a list, got {type(permits).__name__}"
    )
    # Fresh repo - .roam/permits/ does not exist; the always-emit
    # contract requires an empty list (NOT a missing key).
    assert permits == [], (
        f"permits must be [] when .roam/permits/ is missing; got {permits!r}"
    )


def test_pr_bundle_envelope_always_emits_leases_empty(
    cli_runner, bundle_project,
):
    """W268: pr-bundle emit ALWAYS emits ``leases[]`` (empty in a fresh repo)."""
    _invoke(cli_runner, ["pr-bundle", "init", "--intent", "Add leases test"])
    _invoke(
        cli_runner,
        ["pr-bundle", "add", "affected", "foo", "--blast-radius", "1"],
    )
    _invoke(cli_runner, ["pr-bundle", "add", "context-cmd", "roam preflight foo"])

    result = _invoke(
        cli_runner, ["--json", "pr-bundle", "emit", "--no-auto-collect"]
    )
    assert result.exit_code == 0, result.output
    data = parse_json_output(result, command="pr-bundle")

    leases = data.get("leases")
    assert isinstance(leases, list), (
        f"leases must be a list, got {type(leases).__name__}"
    )
    assert leases == [], (
        f"leases must be [] when .roam/leases/ is empty; got {leases!r}"
    )


def test_pr_bundle_envelope_lifts_permits_from_disk(
    cli_runner, bundle_project,
):
    """W268: a ``.roam/permits/<id>.json`` row flows onto envelope.permits[].

    W380 hardening: the reader now schema-validates each row through the
    W198 ``PermitRecord`` contract, so the on-disk permit MUST carry the
    full required field set (permit_id matching PERMIT_ID_RE, scope,
    expires_at, issued_to, issued_at, issued_by). A skeletal "facade"
    row that omits required fields is dropped + warned.
    """
    permits_dir = bundle_project / ".roam" / "permits"
    permits_dir.mkdir(parents=True, exist_ok=True)
    permit_row = {
        "permit_id": "permit_20260514_268a00",
        "scope": "edit:src/roam/cli.py",
        "issued_to": "agent:claude-opus-4.7",
        "expires_at": "2026-12-31T23:59:59Z",
        "issued_at": "2026-05-14T10:00:00Z",
        "issued_by": "human:w268-operator",
    }
    (permits_dir / "permit_20260514_268a00.json").write_text(
        json.dumps(permit_row), encoding="utf-8"
    )

    _invoke(cli_runner, ["pr-bundle", "init", "--intent", "Add permit lift test"])
    _invoke(
        cli_runner,
        ["pr-bundle", "add", "affected", "foo", "--blast-radius", "1"],
    )
    _invoke(cli_runner, ["pr-bundle", "add", "context-cmd", "roam preflight foo"])

    result = _invoke(
        cli_runner, ["--json", "pr-bundle", "emit", "--no-auto-collect"]
    )
    assert result.exit_code == 0, result.output
    data = parse_json_output(result, command="pr-bundle")

    permits = data.get("permits") or []
    assert len(permits) == 1, f"expected one permit lifted; got {permits!r}"
    lifted = permits[0]
    assert lifted.get("permit_id") == "permit_20260514_268a00"
    assert lifted.get("scope") == "edit:src/roam/cli.py"
    # The on-disk shape is mirrored verbatim - the producer does NOT
    # invent fields.
    assert lifted.get("issued_to") == "agent:claude-opus-4.7"


def test_pr_bundle_envelope_lifts_leases_from_disk(
    cli_runner, bundle_project,
):
    """W268: a ``.roam/leases/<id>.json`` row flows onto envelope.leases[]."""
    leases_dir = bundle_project / ".roam" / "leases"
    leases_dir.mkdir(parents=True, exist_ok=True)
    lease_row = {
        "lease_id": "lease_20260514_w268a",
        "agent": "w268-smoke",
        "subject_kind": "files",
        "subject": ["src/roam/cli.py"],
        "ttl_seconds": 1800,
        "acquired_at": "2026-05-14T09:00:00.000000Z",
        "expires_at": "2099-05-14T09:30:00.000000Z",
        "state": "active",
    }
    (leases_dir / "lease_20260514_w268a.json").write_text(
        json.dumps(lease_row), encoding="utf-8"
    )

    _invoke(cli_runner, ["pr-bundle", "init", "--intent", "Add lease lift test"])
    _invoke(
        cli_runner,
        ["pr-bundle", "add", "affected", "foo", "--blast-radius", "1"],
    )
    _invoke(cli_runner, ["pr-bundle", "add", "context-cmd", "roam preflight foo"])

    result = _invoke(
        cli_runner, ["--json", "pr-bundle", "emit", "--no-auto-collect"]
    )
    assert result.exit_code == 0, result.output
    data = parse_json_output(result, command="pr-bundle")

    leases = data.get("leases") or []
    assert len(leases) == 1, f"expected one lease lifted; got {leases!r}"
    lifted = leases[0]
    assert lifted.get("lease_id") == "lease_20260514_w268a"
    assert lifted.get("agent") == "w268-smoke"
    assert lifted.get("subject_kind") == "files"
    # Subject + TTL flow through verbatim.
    assert lifted.get("subject") == ["src/roam/cli.py"]
    assert lifted.get("ttl_seconds") == 1800
