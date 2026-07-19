"""Tests for the R24 repo-local agent constitution (capstone).

Covers:
  - init writes ``.roam/constitution.yml`` with the expected top-level keys
  - init auto-detects AGENTS.md / roam-laws.yml / .roam/memory.jsonl
  - init without --force on an existing file emits already_initialized
  - check returns ok when sources resolve and commands are known
  - check flags missing sources with state="source_missing"
  - check flags unknown-command required_checks
  - apply runs the gate commands via an injected runner
  - apply --strict exits 5 on any failure
  - show emits a clean envelope
  - constitution-pending probe surfaces in `roam next`
"""

from __future__ import annotations

import builtins
import json
import shlex
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
from conftest import (  # noqa: E402
    assert_json_envelope,
    git_init,
    invoke_cli,
    parse_json_output,
)

from roam.constitution.loader import (  # noqa: E402
    CONSTITUTION_GENERATOR_FORMAT_VERSION,
    CONSTITUTION_GENERATOR_NAME,
    Constitution,
    ConstitutionConcurrentUpdate,
    ConstitutionUpgradePreviewMismatch,
    ConstitutionUpgradeRequiresAcceptance,
    _deprecated_commands,
    _known_commands,
    _project_name,
    apply_constitution,
    assess_constitution_upgrade,
    check_constitution,
    constitution_path,
    init_constitution,
    load_constitution,
    mode_policy_digest,
    upgrade_constitution,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _constitution_commands_run_in_declared_mode(monkeypatch):
    """This suite tests constitution behavior, not dispatch-mode denials."""
    monkeypatch.setenv("ROAM_AGENT_MODE", "autonomous_pr")
    monkeypatch.delenv("ROAM_MODE_ENFORCEMENT", raising=False)


@pytest.fixture
def empty_repo(tmp_path):
    """Bare git-initialised project. No constitution, no AGENTS.md, no laws."""
    proj = tmp_path / "cproj"
    proj.mkdir()
    (proj / ".gitignore").write_text(".roam/\n")
    (proj / "app.py").write_text("def main():\n    return 0\n")
    git_init(proj)
    return proj


@pytest.fixture
def rich_repo(tmp_path):
    """Repo that already has every supporting substrate file in place."""
    proj = tmp_path / "rich"
    proj.mkdir()
    (proj / ".gitignore").write_text(".roam/\n")
    (proj / "app.py").write_text("def main():\n    return 0\n")
    (proj / "AGENTS.md").write_text("# Agents\n\nBe helpful.\n")
    (proj / "roam-laws.yml").write_text("version: 1\ngenerated_by: roam laws mine\nlaws: []\n")
    roam_dir = proj / ".roam"
    roam_dir.mkdir()
    (roam_dir / "memory.jsonl").write_text('{"id":"x","kind":"fact","subject":"app","summary":"main returns 0"}\n')
    rules_dir = roam_dir / "rules"
    rules_dir.mkdir()
    (rules_dir / "house.yml").write_text("version: 1\nrules: []\n")
    git_init(proj)
    return proj


# ---------------------------------------------------------------------------
# 1. init creates constitution.yml with all expected top-level keys
# ---------------------------------------------------------------------------


def test_init_creates_constitution_yml(empty_repo):
    path = init_constitution(empty_repo)
    assert path == constitution_path(empty_repo)
    assert path.exists()
    text = path.read_text(encoding="utf-8")
    # Key top-level sections must be present (we don't pin YAML formatting).
    for key in (
        "version:",
        "metadata:",
        "sources:",
        "required_checks:",
        "modes:",
        "policy:",
        "metadata_signals:",
    ):
        assert key in text, f"missing key '{key}' in generated constitution"
    # Round-trip via the loader.
    constitution = load_constitution(empty_repo)
    assert constitution is not None
    assert constitution.version == 1
    assert "before_edit" in constitution.required_checks
    assert "before_pr" in constitution.required_checks
    # Default modes present.
    assert "read_only" in constitution.modes
    assert "safe_edit" in constitution.modes


def test_project_name_only_swallows_expected_resolve_errors(empty_repo, monkeypatch):
    def raise_os_error(self):
        raise OSError("resolve failed")

    monkeypatch.setattr(Path, "resolve", raise_os_error)
    assert _project_name(empty_repo) == "unknown"

    def raise_value_error(self):
        raise ValueError("unexpected bug")

    monkeypatch.setattr(Path, "resolve", raise_value_error)
    with pytest.raises(ValueError):
        _project_name(empty_repo)


# ---------------------------------------------------------------------------
# 2. init detects existing AGENTS.md
# ---------------------------------------------------------------------------


def test_init_detects_existing_agents_md(rich_repo):
    init_constitution(rich_repo)
    constitution = load_constitution(rich_repo)
    assert constitution is not None
    # AGENTS.md should be detected and pointed at.
    assert "agents_md" in constitution.sources, (
        f"AGENTS.md should have been auto-detected; got sources={constitution.sources}"
    )
    assert constitution.sources["agents_md"].endswith("AGENTS.md")


# ---------------------------------------------------------------------------
# 3. init detects existing roam-laws.yml + rules dir + memory.jsonl
# ---------------------------------------------------------------------------


def test_init_detects_existing_laws_yml(rich_repo):
    init_constitution(rich_repo)
    constitution = load_constitution(rich_repo)
    assert constitution is not None
    assert "laws" in constitution.sources
    assert constitution.sources["laws"].endswith("roam-laws.yml")
    # Rules directory glob is also resolved.
    assert "rules" in constitution.sources
    assert constitution.sources["rules"].endswith("/*.yml") or constitution.sources["rules"].endswith(".yml")
    # Memory file is detected.
    assert "memory" in constitution.sources
    assert constitution.sources["memory"].endswith("memory.jsonl")


# ---------------------------------------------------------------------------
# 4. init without --force errors on an existing file; --force overwrites
# ---------------------------------------------------------------------------


def test_init_force_overwrites(empty_repo):
    init_constitution(empty_repo)
    # Second init without force raises.
    with pytest.raises(FileExistsError):
        init_constitution(empty_repo, force=False)
    # With force=True it succeeds.
    path = init_constitution(empty_repo, force=True)
    assert path.exists()


# ---------------------------------------------------------------------------
# 5. check on a clean fresh constitution returns ok
# ---------------------------------------------------------------------------


def test_check_clean_constitution_returns_ok(rich_repo):
    init_constitution(rich_repo)
    constitution = load_constitution(rich_repo)
    assert constitution is not None
    report = check_constitution(rich_repo, constitution)
    # Every default required-check should be a known roam command.
    assert all(c.resolved for c in report.commands), (
        f"unresolved required-checks: {[c.command for c in report.commands if not c.resolved]}"
    )
    # Every detected source should exist (we just wrote them).
    for s in report.sources:
        assert s.exists, f"source {s.name}={s.path} should exist"
    assert report.ok is True
    assert report.state == "ok"


# ---------------------------------------------------------------------------
# 6. check flags missing source files
# ---------------------------------------------------------------------------


def test_check_missing_source_returns_partial(rich_repo):
    init_constitution(rich_repo)
    # Delete the laws file -> check should now flag it.
    (rich_repo / "roam-laws.yml").unlink()
    constitution = load_constitution(rich_repo)
    assert constitution is not None
    report = check_constitution(rich_repo, constitution)
    assert report.ok is False
    assert report.state == "partial"
    laws_status = next((s for s in report.sources if s.name == "laws"), None)
    assert laws_status is not None
    assert laws_status.state == "source_missing"


# ---------------------------------------------------------------------------
# 7. check flags unknown commands in required_checks
# ---------------------------------------------------------------------------


def test_check_unknown_command_in_required_checks_fails(empty_repo):
    # Hand-author a constitution that references a fake command.
    constitution = Constitution(
        version=1,
        metadata={},
        sources={},
        required_checks={
            "before_edit": ["roam totally-not-a-command --thing"],
        },
        modes={"read_only": ["search"]},
        policy={},
        metadata_signals={},
    )
    report = check_constitution(empty_repo, constitution)
    assert report.ok is False
    bad = [c for c in report.commands if c.state == "unknown_command"]
    assert bad, "expected 'totally-not-a-command' to be flagged as unknown"
    assert bad[0].name == "totally-not-a-command"


def test_command_registry_probe_only_swallows_import_errors(monkeypatch):
    real_import = builtins.__import__

    def raise_import_error(name, *args, **kwargs):
        if name == "roam.cli":
            raise ImportError("cli unavailable")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", raise_import_error)
    assert _known_commands() == set()
    assert _deprecated_commands() == set()

    def raise_runtime_error(name, *args, **kwargs):
        if name == "roam.cli":
            raise RuntimeError("cli import bug")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", raise_runtime_error)
    with pytest.raises(RuntimeError, match="cli import bug"):
        _known_commands()
    with pytest.raises(RuntimeError, match="cli import bug"):
        _deprecated_commands()


# ---------------------------------------------------------------------------
# 8. apply runs the gate commands via an injected runner
# ---------------------------------------------------------------------------


def test_apply_runs_gate_commands(empty_repo):
    # Constitution with two trivial before_edit checks. We use an injected
    # runner so we don't have to invoke real roam subprocesses.
    constitution = Constitution(
        version=1,
        sources={},
        required_checks={
            "before_edit": [
                "roam health",
                "roam doctor",
            ],
        },
        modes={},
        policy={},
        metadata_signals={},
    )
    called: list[list[str]] = []

    def fake_runner(argv, cwd, timeout):
        called.append(list(argv))
        return (0, f"VERDICT: ran {argv[1] if len(argv) > 1 else 'roam'}\n", "")

    report = apply_constitution(
        empty_repo,
        constitution,
        gate="before_edit",
        runner=fake_runner,
    )
    assert len(called) == 2
    # First arg is always "roam".
    assert called[0][0] == "roam"
    assert called[0][1] == "health"
    assert called[1][1] == "doctor"
    assert report.state == "ok"
    assert all(r.passed for r in report.results)
    assert report.passed_count == 2
    assert report.failed_count == 0


# ---------------------------------------------------------------------------
# 9. apply --strict exits 5 when any check fails
# ---------------------------------------------------------------------------


def test_apply_strict_exits_5_on_failure(empty_repo, cli_runner):
    # Write a constitution that runs a command guaranteed to non-zero
    # exit. We do this via a custom required-check that resolves a
    # placeholder which isn't supplied -> the check is SKIPPED, which
    # is NOT a failure. So we need a real failure: have the constitution
    # reference an unknown subcommand. Default subprocess runner will
    # exit non-zero on that.
    path = init_constitution(empty_repo, force=True)
    # Patch the constitution's before_edit gate to call a non-existent
    # roam command -- this will exit non-zero through the real CLI.
    # Was: an initial text.replace() approach to surgically swap the
    # before_edit block was abandoned in favour of rewriting the whole
    # file with a known-bad invocation (cleaner, no replace-string
    # coupling to the init-constitution template). See W53 audit.
    minimal = (
        "version: 1\n"
        "metadata:\n"
        "  name: cproj\n"
        "sources: {}\n"
        "required_checks:\n"
        "  before_edit:\n"
        "    - roam this-command-does-not-exist\n"
        "modes: {}\n"
        "policy: {}\n"
        "metadata_signals: {}\n"
    )
    path.write_text(minimal, encoding="utf-8")

    result = invoke_cli(
        cli_runner,
        ["constitution", "apply", "--gate", "before_edit", "--strict", "--timeout", "30"],
        cwd=empty_repo,
        json_mode=True,
    )
    # The CLI should exit 5 due to --strict.
    assert result.exit_code == 5, f"expected exit 5 from --strict; got {result.exit_code}\nOutput:\n{result.output}"


def test_apply_strict_exits_5_with_injected_runner(empty_repo, monkeypatch, cli_runner):
    """Same intent as the previous test, but doesn't depend on PATH having `roam`.

    Patches the runner used by the loader to a stub that returns a non-zero
    exit so we exercise the strict-exit-5 behaviour even when `roam` is not
    installed as a global binary in the test environment.
    """
    init_constitution(empty_repo, force=True)
    # Overwrite with a known-good single-check constitution.
    cp = constitution_path(empty_repo)
    cp.write_text(
        "version: 1\n"
        "metadata: {}\n"
        "sources: {}\n"
        "required_checks:\n"
        "  before_edit:\n"
        "    - roam fake-check\n"
        "modes: {}\n"
        "policy: {}\n"
        "metadata_signals: {}\n",
        encoding="utf-8",
    )

    # Patch apply_constitution so it uses our deterministic runner.
    from roam.constitution import loader as loader_mod

    original = loader_mod.apply_constitution

    def patched(repo_root, constitution, **kw):
        kw.setdefault("runner", lambda argv, cwd, t: (1, "", "boom\n"))
        return original(repo_root, constitution, **kw)

    monkeypatch.setattr(loader_mod, "apply_constitution", patched)
    # cmd_constitution imports apply_constitution by name; patch it there too.
    from roam.commands import cmd_constitution as cmd_mod

    monkeypatch.setattr(cmd_mod, "apply_constitution", patched)

    result = invoke_cli(
        cli_runner,
        ["constitution", "apply", "--gate", "before_edit", "--strict"],
        cwd=empty_repo,
        json_mode=True,
    )
    assert result.exit_code == 5, (
        f"expected exit 5 with injected failing runner; got {result.exit_code}\nOutput:\n{result.output}"
    )


# ---------------------------------------------------------------------------
# 10. show emits a parseable envelope
# ---------------------------------------------------------------------------


def test_show_emits_envelope(rich_repo, cli_runner):
    init_constitution(rich_repo)
    result = invoke_cli(
        cli_runner,
        ["constitution", "show"],
        cwd=rich_repo,
        json_mode=True,
    )
    data = parse_json_output(result, command="constitution-show")
    assert_json_envelope(data, command="constitution-show")
    assert data["summary"]["state"] == "ok"
    assert data["summary"]["source_count"] >= 3
    assert "constitution" in data
    assert "sources" in data["constitution"]
    assert "required_checks" in data["constitution"]


# ---------------------------------------------------------------------------
# 11. where prints the canonical path
# ---------------------------------------------------------------------------


def test_where_prints_path(rich_repo, cli_runner):
    init_constitution(rich_repo)
    result = invoke_cli(
        cli_runner,
        ["constitution", "where"],
        cwd=rich_repo,
    )
    assert result.exit_code == 0
    assert ".roam" in result.output
    assert "constitution.yml" in result.output


# ---------------------------------------------------------------------------
# 12. init via CLI returns already_initialized when run twice
# ---------------------------------------------------------------------------


def test_cli_init_already_initialized_when_no_force(rich_repo, cli_runner):
    invoke_cli(cli_runner, ["constitution", "init"], cwd=rich_repo)
    result = invoke_cli(
        cli_runner,
        ["constitution", "init"],
        cwd=rich_repo,
        json_mode=True,
    )
    data = parse_json_output(result, command="constitution-init")
    assert data["summary"]["state"] == "already_initialized"
    assert data["summary"]["partial_success"] is True
    assert data["summary"]["created"] is False


# ---------------------------------------------------------------------------
# 13. R24 wiring: roam next picks up pending before_pr check
# ---------------------------------------------------------------------------


def test_next_router_surfaces_pending_before_pr(rich_repo, monkeypatch, cli_runner):
    """When a run is active and the constitution has un-run before_pr checks,
    `roam next` should surface the first one."""
    # 1) Initialise the constitution.
    init_constitution(rich_repo)
    # 2) Author a minimal constitution with a single before_pr check the
    #    test can drive deterministically. Use a known roam command name.
    cp = constitution_path(rich_repo)
    cp.write_text(
        "version: 1\n"
        "metadata: {}\n"
        "sources: {}\n"
        "required_checks:\n"
        "  before_edit: []\n"
        "  after_edit: []\n"
        "  before_pr:\n"
        "    - roam health\n"
        "modes: {}\n"
        "policy: {}\n"
        "metadata_signals: {}\n",
        encoding="utf-8",
    )
    # 3) Start a run so the probe has something to consult.
    from roam.runs.ledger import start_run

    meta = start_run(rich_repo, agent="test-agent")
    monkeypatch.setenv("ROAM_RUN_ID", meta.run_id)

    # 4) The router checks for an index file before reaching our branch.
    #    Drop a stub so db_exists() returns True (no real index needed --
    #    the constitution branch is what we're exercising).
    (rich_repo / ".roam" / "index.db").write_bytes(b"x")
    # And bump the db mtime to be newer than every source so the
    # staleness probe doesn't fire either.
    import time as _t

    _t.sleep(0.05)
    (rich_repo / ".roam" / "index.db").touch()

    # 5) `roam next` should now route to the pending before_pr check.
    result = invoke_cli(cli_runner, ["next"], cwd=rich_repo, json_mode=True)
    data = parse_json_output(result, command="next")
    # State should be the new constitution_pending branch.
    assert data["summary"]["state"] == "constitution_pending", (
        f"expected constitution_pending state; got {data['summary']}"
    )
    assert data["summary"]["command"] == "health"


# ---------------------------------------------------------------------------
# 14. apply with unresolved placeholder skips the check (does not invoke)
# ---------------------------------------------------------------------------


def test_apply_skips_unresolved_placeholders(empty_repo):
    constitution = Constitution(
        version=1,
        sources={},
        required_checks={
            "before_edit": ["roam preflight ${symbol}"],
        },
        modes={},
        policy={},
        metadata_signals={},
    )
    called: list[list[str]] = []

    def fake_runner(argv, cwd, timeout):
        called.append(list(argv))
        return (0, "ok\n", "")

    # No `symbol` variable -> the check is skipped, not invoked.
    report = apply_constitution(
        empty_repo,
        constitution,
        gate="before_edit",
        variables={},
        runner=fake_runner,
    )
    assert called == [], "skipped checks must not invoke the runner"
    assert len(report.results) == 1
    assert report.results[0].skipped is True
    assert "symbol" in report.results[0].skip_reason
    # No failures -> overall state should be ok (one skipped).
    assert report.state == "ok"


# ---------------------------------------------------------------------------
# 15. constitution-where envelope has correct shape (non-JSON also tested
#     via test_where_prints_path)
# ---------------------------------------------------------------------------


def test_where_envelope_when_missing(empty_repo, cli_runner):
    """When the file doesn't exist, where should still return a clean envelope."""
    result = invoke_cli(
        cli_runner,
        ["constitution", "where"],
        cwd=empty_repo,
        json_mode=True,
    )
    data = parse_json_output(result, command="constitution-where")
    assert_json_envelope(data, command="constitution-where")
    assert data["summary"]["exists"] is False
    assert data["summary"]["state"] == "not_initialized"


# ---------------------------------------------------------------------------
# 16. generated mode-policy provenance and fail-safe upgrades
# ---------------------------------------------------------------------------


def _read_constitution_doc(repo: Path) -> dict:
    from roam.constitution import loader as loader_mod

    path = constitution_path(repo)
    return loader_mod._load_yaml(path.read_text(encoding="utf-8"))


def _write_constitution_doc(repo: Path, doc: dict) -> None:
    from roam.constitution import loader as loader_mod

    loader_mod._write_constitution(constitution_path(repo), doc)


def _remove_mode_command(doc: dict, command: str) -> None:
    for commands in doc["modes"].values():
        while command in commands:
            commands.remove(command)


def test_init_records_semantic_mode_policy_provenance(empty_repo):
    init_constitution(empty_repo)
    constitution = load_constitution(empty_repo)
    assert constitution is not None

    generator = constitution.metadata["generator"]
    assert constitution.version == 1  # additive metadata does not bump the document schema
    assert generator["name"] == CONSTITUTION_GENERATOR_NAME
    assert generator["format_version"] == CONSTITUTION_GENERATOR_FORMAT_VERSION
    assert generator["managed_modes_sha256"] == mode_policy_digest(constitution.modes)

    report = assess_constitution_upgrade(constitution)
    assert report.state == "up_to_date"
    assert report.provenance == "managed_unchanged"
    assert report.changed is False
    assert report.safe_to_apply is True


def test_mode_policy_digest_is_semantic_and_fallback_yaml_preserves_marker():
    from roam.constitution import loader as loader_mod

    modes_a = {"read_only": ["module", "audit"]}
    modes_b = {"read_only": ["audit", "module", "audit"]}
    assert mode_policy_digest(modes_a) == mode_policy_digest(modes_b)

    doc = {
        "version": 1,
        "metadata": {
            "generator": {
                "name": CONSTITUTION_GENERATOR_NAME,
                "format_version": CONSTITUTION_GENERATOR_FORMAT_VERSION,
                "managed_modes_sha256": mode_policy_digest(modes_a),
            }
        },
        "modes": modes_a,
    }
    rendered = loader_mod._fallback_dump(doc)
    assert loader_mod._fallback_parse(rendered) == doc


def test_unchanged_generated_snapshot_tracks_new_defaults_and_upgrades(empty_repo):
    from roam.modes import list_modes

    init_constitution(empty_repo)
    doc = _read_constitution_doc(empty_repo)
    _remove_mode_command(doc, "module")
    doc["metadata"]["generator"]["managed_modes_sha256"] = mode_policy_digest(doc["modes"])
    doc["metadata"]["owner_extension"] = {"team": "safety"}
    doc["vendor_extension"] = {"preserve": True}
    _write_constitution_doc(empty_repo, doc)

    constitution = load_constitution(empty_repo)
    assert constitution is not None
    assert "module" not in constitution.modes["read_only"]
    preview = assess_constitution_upgrade(constitution)
    assert preview.state == "upgrade_available"
    assert preview.provenance == "managed_unchanged"
    assert preview.safe_to_apply is True
    assert "module" in preview.additions["read_only"]

    # Runtime may follow the latest defaults only because the recorded digest
    # proves the generated modes were not customized.
    assert "module" in list_modes(empty_repo)["read_only"].allowed_commands

    applied = upgrade_constitution(empty_repo)
    assert applied.state == "upgraded"
    assert applied.applied is True
    assert applied.addition_total >= 4
    upgraded_doc = _read_constitution_doc(empty_repo)
    assert upgraded_doc["vendor_extension"] == {"preserve": True}
    assert upgraded_doc["metadata"]["owner_extension"] == {"team": "safety"}
    assert "module" in upgraded_doc["modes"]["read_only"]


def test_customized_modes_never_gain_permissions_without_acknowledgement(empty_repo):
    from roam.modes import list_modes

    init_constitution(empty_repo)
    doc = _read_constitution_doc(empty_repo)
    # Leave the recorded digest untouched: this is now a proven customization.
    _remove_mode_command(doc, "module")
    _write_constitution_doc(empty_repo, doc)
    before = constitution_path(empty_repo).read_bytes()

    constitution = load_constitution(empty_repo)
    assert constitution is not None
    preview = assess_constitution_upgrade(constitution)
    assert preview.state == "review_required"
    assert preview.provenance == "customized"
    assert preview.safe_to_apply is False
    assert preview.requires_explicit_acceptance is True
    assert "module" not in list_modes(empty_repo)["read_only"].allowed_commands

    with pytest.raises(ConstitutionUpgradeRequiresAcceptance):
        upgrade_constitution(empty_repo)
    assert constitution_path(empty_repo).read_bytes() == before

    with pytest.raises(ConstitutionUpgradePreviewMismatch):
        upgrade_constitution(empty_repo, accept_mode_replacement=True)
    with pytest.raises(ConstitutionUpgradePreviewMismatch):
        upgrade_constitution(
            empty_repo,
            accept_mode_replacement=True,
            expected_modes_digest="sha256:" + "0" * 64,
        )
    assert constitution_path(empty_repo).read_bytes() == before

    applied = upgrade_constitution(
        empty_repo,
        accept_mode_replacement=True,
        expected_modes_digest=preview.current_modes_digest,
    )
    assert applied.applied is True
    assert "module" in load_constitution(empty_repo).modes["read_only"]  # type: ignore[union-attr]


def test_legacy_upgrade_is_previewable_and_requires_explicit_replacement(empty_repo, cli_runner):
    init_constitution(empty_repo)
    doc = _read_constitution_doc(empty_repo)
    _remove_mode_command(doc, "module")
    doc["metadata"].pop("generator")
    _write_constitution_doc(empty_repo, doc)
    before = constitution_path(empty_repo).read_bytes()

    preview_result = invoke_cli(
        cli_runner,
        ["constitution", "upgrade"],
        cwd=empty_repo,
        json_mode=True,
    )
    preview = parse_json_output(preview_result, command="constitution-upgrade")
    assert preview["summary"]["state"] == "review_required"
    assert preview["summary"]["safe_to_apply"] is False
    assert preview["upgrade"]["provenance"] == "legacy_generated_unproven"
    assert "module" in preview["upgrade"]["additions"]["read_only"]

    blocked_result = invoke_cli(
        cli_runner,
        ["constitution", "upgrade", "--apply"],
        cwd=empty_repo,
        json_mode=True,
    )
    assert blocked_result.exit_code == 5
    blocked = json.loads(blocked_result.output)
    assert blocked["summary"]["state"] == "blocked"
    assert blocked["summary"]["partial_success"] is True
    assert constitution_path(empty_repo).read_bytes() == before

    next_argv = shlex.split(preview["agent_contract"]["next_commands"][0])
    assert next_argv[0] == "roam"
    applied_result = invoke_cli(cli_runner, next_argv[1:], cwd=empty_repo, json_mode=True)
    applied = parse_json_output(applied_result, command="constitution-upgrade")
    assert applied_result.exit_code == 0
    assert applied["summary"]["state"] == "upgraded"
    assert applied["summary"]["applied"] is True


def test_constitution_check_surfaces_advisory_legacy_upgrade(empty_repo, cli_runner):
    init_constitution(empty_repo)
    doc = _read_constitution_doc(empty_repo)
    _remove_mode_command(doc, "report")
    doc["metadata"].pop("generator")
    _write_constitution_doc(empty_repo, doc)

    result = invoke_cli(
        cli_runner,
        ["constitution", "check"],
        cwd=empty_repo,
        json_mode=True,
    )
    data = parse_json_output(result, command="constitution-check")
    assert data["summary"]["ok"] is True
    assert data["summary"]["mode_upgrade_state"] == "review_required"
    assert data["mode_upgrade"]["requires_explicit_acceptance"] is True
    assert "report" in data["mode_upgrade"]["additions"]["read_only"]


def test_unsupported_provenance_version_never_auto_broadens(empty_repo):
    from roam.modes import list_modes

    init_constitution(empty_repo)
    doc = _read_constitution_doc(empty_repo)
    _remove_mode_command(doc, "audit")
    doc["metadata"]["generator"]["format_version"] = CONSTITUTION_GENERATOR_FORMAT_VERSION + 1
    doc["metadata"]["generator"]["managed_modes_sha256"] = mode_policy_digest(doc["modes"])
    _write_constitution_doc(empty_repo, doc)

    constitution = load_constitution(empty_repo)
    assert constitution is not None
    preview = assess_constitution_upgrade(constitution)
    assert preview.provenance == "unsupported_generator_provenance"
    assert preview.requires_explicit_acceptance is True
    assert "audit" not in list_modes(empty_repo)["read_only"].allowed_commands


def test_partial_custom_policy_inherits_declared_permissions_not_baked_defaults(empty_repo):
    from roam.modes import list_modes

    _write_constitution_doc(
        empty_repo,
        {
            "version": 1,
            "metadata": {"owner": "user"},
            "sources": {},
            "required_checks": {},
            "modes": {
                "read_only": ["search"],
                "safe_edit": [],
                # migration intentionally omitted
                "autonomous_pr": ["attest"],
            },
            "policy": {},
            "metadata_signals": {},
        },
    )

    policies = list_modes(empty_repo)
    assert policies["read_only"].allowed_commands == frozenset({"search"})
    assert policies["safe_edit"].allowed_commands == frozenset({"search"})
    assert policies["migration"].allowed_commands == frozenset({"search"})
    assert policies["autonomous_pr"].allowed_commands == frozenset({"search", "attest"})
    assert all("module" not in policy.allowed_commands for policy in policies.values())


def test_upgrade_compare_and_swap_rejects_concurrent_edit(empty_repo, monkeypatch):
    from roam.constitution import loader as loader_mod

    init_constitution(empty_repo)
    doc = _read_constitution_doc(empty_repo)
    _remove_mode_command(doc, "module")
    doc["metadata"]["generator"]["managed_modes_sha256"] = mode_policy_digest(doc["modes"])
    _write_constitution_doc(empty_repo, doc)

    path = constitution_path(empty_repo)
    real_atomic_write = loader_mod.atomic_write_bytes

    def racing_atomic_write(target, content, **kwargs):
        path.write_bytes(path.read_bytes() + b"# concurrent user edit\n")
        return real_atomic_write(target, content, **kwargs)

    monkeypatch.setattr(loader_mod, "atomic_write_bytes", racing_atomic_write)
    with pytest.raises(ConstitutionConcurrentUpdate):
        upgrade_constitution(empty_repo)
    assert path.read_bytes().endswith(b"# concurrent user edit\n")
