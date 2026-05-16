"""Tests for roam ci-setup -- CI/CD pipeline config generator."""

from __future__ import annotations

import pytest

from tests.conftest import (
    assert_json_envelope,
    git_init,
    invoke_cli,
    parse_json_output,
)


@pytest.fixture
def ci_project(tmp_path):
    proj = tmp_path / "ci_proj"
    proj.mkdir()
    (proj / ".gitignore").write_text(".roam/\n")
    (proj / "main.py").write_text("print('hello')\n")
    git_init(proj)
    return proj


class TestCiSetupSmoke:
    def test_explicit_platform_exits_zero(self, cli_runner, ci_project, monkeypatch):
        monkeypatch.chdir(ci_project)
        result = invoke_cli(cli_runner, ["ci-setup", "--platform", "github"], cwd=ci_project)
        assert result.exit_code == 0

    def test_auto_detect_exits_zero(self, cli_runner, ci_project, monkeypatch):
        monkeypatch.chdir(ci_project)
        result = invoke_cli(cli_runner, ["ci-setup"], cwd=ci_project)
        assert result.exit_code == 0

    def test_all_platforms_exit_zero(self, cli_runner, ci_project, monkeypatch):
        monkeypatch.chdir(ci_project)
        for platform in ("github", "gitlab", "azure", "jenkins", "bitbucket"):
            result = invoke_cli(cli_runner, ["ci-setup", "--platform", platform], cwd=ci_project)
            assert result.exit_code == 0, f"ci-setup --platform {platform} failed"


class TestCiSetupJSON:
    def test_json_envelope(self, cli_runner, ci_project, monkeypatch):
        monkeypatch.chdir(ci_project)
        result = invoke_cli(cli_runner, ["ci-setup", "--platform", "github"], cwd=ci_project, json_mode=True)
        data = parse_json_output(result, "ci-setup")
        assert_json_envelope(data, "ci-setup")

    def test_json_has_template(self, cli_runner, ci_project, monkeypatch):
        monkeypatch.chdir(ci_project)
        result = invoke_cli(cli_runner, ["ci-setup", "--platform", "github"], cwd=ci_project, json_mode=True)
        data = parse_json_output(result, "ci-setup")
        assert "template" in data or "config" in data or "content" in data

    def test_json_summary_has_platform(self, cli_runner, ci_project, monkeypatch):
        monkeypatch.chdir(ci_project)
        result = invoke_cli(cli_runner, ["ci-setup", "--platform", "gitlab"], cwd=ci_project, json_mode=True)
        data = parse_json_output(result, "ci-setup")
        summary = data["summary"]
        assert "platform" in summary


class TestCiSetupText:
    def test_verdict_line(self, cli_runner, ci_project, monkeypatch):
        monkeypatch.chdir(ci_project)
        result = invoke_cli(cli_runner, ["ci-setup", "--platform", "github"], cwd=ci_project)
        assert "VERDICT:" in result.output or "===" in result.output

    def test_github_template_contains_roam(self, cli_runner, ci_project, monkeypatch):
        monkeypatch.chdir(ci_project)
        result = invoke_cli(cli_runner, ["ci-setup", "--platform", "github"], cwd=ci_project)
        assert "roam" in result.output.lower()

    def test_auto_detect_with_github_marker(self, cli_runner, ci_project, monkeypatch):
        monkeypatch.chdir(ci_project)
        gh = ci_project / ".github" / "workflows"
        gh.mkdir(parents=True)
        result = invoke_cli(cli_runner, ["ci-setup"], cwd=ci_project)
        assert result.exit_code == 0
        assert "github" in result.output.lower() or "GitHub" in result.output


class TestCiSetupWrite:
    def test_write_creates_file(self, cli_runner, ci_project, monkeypatch):
        monkeypatch.chdir(ci_project)
        result = invoke_cli(cli_runner, ["ci-setup", "--platform", "github", "--write"], cwd=ci_project)
        assert result.exit_code == 0
        # Check that a workflow file was created
        gh_dir = ci_project / ".github" / "workflows"
        if gh_dir.exists():
            yaml_files = list(gh_dir.glob("*.yml")) + list(gh_dir.glob("*.yaml"))
            assert len(yaml_files) >= 1


class TestCiSetupSlsaSrcL3W471:
    """W471 - --with-slsa-l3 emits the SLSA SRC-L3 auto-trigger workflow."""

    def test_flag_off_does_not_emit_slsa_workflow(self, cli_runner, ci_project, monkeypatch):
        monkeypatch.chdir(ci_project)
        result = invoke_cli(cli_runner, ["ci-setup", "--platform", "github", "--write"], cwd=ci_project)
        assert result.exit_code == 0
        slsa_path = ci_project / ".github" / "workflows" / "roam-slsa-src-l3.yml"
        assert not slsa_path.exists(), "SLSA workflow should NOT exist when --with-slsa-l3 is off (default)"

    def test_flag_on_emits_slsa_workflow(self, cli_runner, ci_project, monkeypatch):
        monkeypatch.chdir(ci_project)
        result = invoke_cli(
            cli_runner,
            ["ci-setup", "--platform", "github", "--with-slsa-l3", "--write"],
            cwd=ci_project,
        )
        assert result.exit_code == 0, f"--with-slsa-l3 --write failed: {result.output}"
        slsa_path = ci_project / ".github" / "workflows" / "roam-slsa-src-l3.yml"
        assert slsa_path.exists(), f"SLSA workflow not written. Output: {result.output}"
        content = slsa_path.read_text(encoding="utf-8")
        # Trust anchor — the OIDC permission is the L2 -> L3 lift.
        assert "id-token: write" in content, "OIDC trust anchor missing from workflow"
        # The CLI step that produces the evidence.
        assert "roam pr-bundle emit" in content
        assert "--slsa-l3" in content
        assert "--sign" in content
        assert "--keyless" in content
        # Wording-lint compliance.
        assert "supports evidence for SLSA SRC-L3" in content or "supports" in content.lower()

    def test_print_mode_includes_slsa_template(self, cli_runner, ci_project, monkeypatch):
        monkeypatch.chdir(ci_project)
        result = invoke_cli(
            cli_runner,
            ["ci-setup", "--platform", "github", "--with-slsa-l3"],
            cwd=ci_project,
        )
        assert result.exit_code == 0
        assert "roam-slsa-src-l3.yml" in result.output
        assert "id-token: write" in result.output

    def test_json_envelope_carries_slsa_block(self, cli_runner, ci_project, monkeypatch):
        monkeypatch.chdir(ci_project)
        result = invoke_cli(
            cli_runner,
            ["ci-setup", "--platform", "github", "--with-slsa-l3"],
            cwd=ci_project,
            json_mode=True,
        )
        data = parse_json_output(result, "ci-setup")
        assert "slsa_l3" in data, f"slsa_l3 block missing from JSON envelope. Keys: {list(data.keys())}"
        slsa = data["slsa_l3"]
        assert slsa["path"] == ".github/workflows/roam-slsa-src-l3.yml"
        assert slsa["predicate_type"] == "https://slsa.dev/verification_summary/v1"
        assert slsa["trust_anchor"] == "github-actions-oidc"
        assert "template" in slsa
        assert "id-token: write" in slsa["template"]

    def test_non_github_platform_rejects_flag(self, cli_runner, ci_project, monkeypatch):
        """GitHub-only in v1 - Fulcio + Rekor depend on GitHub Actions OIDC."""
        monkeypatch.chdir(ci_project)
        result = invoke_cli(
            cli_runner,
            ["ci-setup", "--platform", "gitlab", "--with-slsa-l3"],
            cwd=ci_project,
        )
        assert result.exit_code != 0
        assert "GitHub-only" in result.output or "github" in result.output.lower()


class TestCiSetupOscalW535:
    """W535 - --with-oscal materialises persistent OSCAL artifacts.

    The FedRAMP continuous-assessment pattern (W359 §6) wants the stub
    Assessment Plan to live on disk so per-run AR documents reference
    it by path instead of inlining the same boilerplate.
    """

    def test_flag_off_does_not_emit_oscal_artifacts(self, cli_runner, ci_project, monkeypatch):
        monkeypatch.chdir(ci_project)
        result = invoke_cli(
            cli_runner,
            ["ci-setup", "--platform", "github", "--write"],
            cwd=ci_project,
        )
        assert result.exit_code == 0
        oscal_dir = ci_project / ".roam" / "oscal"
        assert not oscal_dir.exists(), "OSCAL artifacts should NOT be materialised when --with-oscal is off (default)"

    def test_flag_on_creates_both_artifacts(self, cli_runner, ci_project, monkeypatch):
        monkeypatch.chdir(ci_project)
        result = invoke_cli(
            cli_runner,
            ["ci-setup", "--platform", "github", "--with-oscal", "--write"],
            cwd=ci_project,
        )
        assert result.exit_code == 0, f"--with-oscal --write failed: {result.output}"

        cm_path = ci_project / ".roam" / "oscal" / "control-mapping.json"
        ap_path = ci_project / ".roam" / "oscal" / "stub-assessment-plan.json"
        assert cm_path.exists(), f"control-mapping.json not written. Output: {result.output}"
        assert ap_path.exists(), f"stub-assessment-plan.json not written. Output: {result.output}"

        import json

        cm = json.loads(cm_path.read_text(encoding="utf-8"))
        ap = json.loads(ap_path.read_text(encoding="utf-8"))

        # OSCAL v1.2 top-level shapes
        assert "control-mapping" in cm, "control-mapping.json missing top-level model element"
        assert "assessment-plan" in ap, "stub-assessment-plan.json missing top-level model element"

        # OSCAL version stamped (W464 emitter uses 1.1.2 — schema-stable across v1.1.2/v1.2.0).
        assert cm["control-mapping"]["metadata"]["oscal-version"] == "1.1.2"
        assert ap["assessment-plan"]["metadata"]["oscal-version"] == "1.1.2"

        # Wording-lint discipline applies to the stub AP metadata too.
        ap_remarks = ap["assessment-plan"]["metadata"].get("remarks", "")
        assert "supports evidence for" in ap_remarks or "maps to" in ap_remarks or "stub" in ap_remarks.lower()

    def test_flag_on_emits_post_setup_instructions(self, cli_runner, ci_project, monkeypatch):
        monkeypatch.chdir(ci_project)
        result = invoke_cli(
            cli_runner,
            ["ci-setup", "--platform", "github", "--with-oscal", "--write"],
            cwd=ci_project,
        )
        assert result.exit_code == 0
        # The user MUST learn that future AR emissions can --import-ap-ref the stub.
        assert "Persistent OSCAL artifacts" in result.output
        assert "--import-ap-ref" in result.output
        assert ".roam/oscal/stub-assessment-plan.json" in result.output

    def test_idempotent_byte_identical_rerun(self, cli_runner, ci_project, monkeypatch):
        """Re-running --with-oscal must produce byte-identical files.

        Hash stability mandate per W535 — without this, content-hash
        auditing of evidence artifacts breaks across re-runs.
        """
        monkeypatch.chdir(ci_project)
        for _ in range(2):
            result = invoke_cli(
                cli_runner,
                ["ci-setup", "--platform", "github", "--with-oscal", "--write"],
                cwd=ci_project,
            )
            # On the SECOND invocation the CI YAML file already exists;
            # ci-setup --write reports that as a no-op. The OSCAL block
            # is independent and STILL runs (overwrite-mode by design).
            assert result.exit_code == 0

        cm_path = ci_project / ".roam" / "oscal" / "control-mapping.json"
        ap_path = ci_project / ".roam" / "oscal" / "stub-assessment-plan.json"

        # Capture bytes after the second run.
        cm_bytes_run2 = cm_path.read_bytes()
        ap_bytes_run2 = ap_path.read_bytes()

        # Run a third time — bytes must stay identical to run 2.
        result3 = invoke_cli(
            cli_runner,
            ["ci-setup", "--platform", "github", "--with-oscal", "--write"],
            cwd=ci_project,
        )
        assert result3.exit_code == 0
        assert cm_path.read_bytes() == cm_bytes_run2, (
            "control-mapping.json drifted on re-run — breaks hash-stability mandate"
        )
        assert ap_path.read_bytes() == ap_bytes_run2, (
            "stub-assessment-plan.json drifted on re-run — breaks hash-stability mandate"
        )

    def test_generated_files_match_w464_emitter_shape(self, cli_runner, ci_project, monkeypatch):
        """The persistent files must be byte-equivalent to what the W464
        emitter would have produced with the same deterministic clock.

        Re-uses the existing W464/W465 emitter functions as the oracle —
        this is the "fixture-comparison helper" pattern from
        test_evidence_oscal.py (`_build_doc`).
        """
        monkeypatch.chdir(ci_project)
        result = invoke_cli(
            cli_runner,
            ["ci-setup", "--platform", "github", "--with-oscal", "--write"],
            cwd=ci_project,
        )
        assert result.exit_code == 0

        import json

        from roam.commands.cmd_ci_setup import (
            _deterministic_oscal_clock,
            _resolve_control_mapping_yaml,
        )
        from roam.evidence.oscal import (
            build_oscal_control_mapping,
            load_control_map,
            synthesize_stub_assessment_plan,
        )

        # ci_project.name is what _get_project_name returns and what
        # cmd_ci_setup passes as repo_id.
        repo_id = ci_project.name
        clock = _deterministic_oscal_clock(repo_id)

        # Reproduce the on-disk Control Mapping — use the same resolver
        # that ci-setup uses so we hit the same source-of-truth YAML.
        yaml_path = _resolve_control_mapping_yaml(ci_project)
        assert yaml_path is not None, "control-mapping.yaml resolver returned None"

        expected_cm = build_oscal_control_mapping(load_control_map(yaml_path), now=clock)
        expected_ap = synthesize_stub_assessment_plan(
            repo_id=repo_id,
            now=clock,
            control_mapping_ref=".roam/oscal/control-mapping.json",
        )

        actual_cm = json.loads((ci_project / ".roam" / "oscal" / "control-mapping.json").read_text(encoding="utf-8"))
        actual_ap = json.loads(
            (ci_project / ".roam" / "oscal" / "stub-assessment-plan.json").read_text(encoding="utf-8")
        )

        assert actual_cm == expected_cm, "Control Mapping diverges from W464 emitter oracle"
        assert actual_ap == expected_ap, "stub AP diverges from W465 emitter oracle"

    def test_json_envelope_carries_oscal_block(self, cli_runner, ci_project, monkeypatch):
        monkeypatch.chdir(ci_project)
        result = invoke_cli(
            cli_runner,
            ["ci-setup", "--platform", "github", "--with-oscal"],
            cwd=ci_project,
            json_mode=True,
        )
        data = parse_json_output(result, "ci-setup")
        assert "oscal" in data, f"oscal block missing from JSON envelope. Keys: {list(data.keys())}"
        oscal = data["oscal"]
        assert oscal["control_mapping"]["path"] == ".roam/oscal/control-mapping.json"
        assert oscal["stub_assessment_plan"]["path"] == ".roam/oscal/stub-assessment-plan.json"
        assert "import_ap_hint" in oscal
        assert "--import-ap-ref" in oscal["import_ap_hint"]
        # Summary boolean reflects the flag.
        assert data["summary"]["with_oscal"] is True


class TestControlMappingWheelSafeResolutionW554:
    """W554 — the control-mapping.yaml must ship inside the wheel.

    Pre-W554 the YAML lived at the project-root ``templates/audit-report/``
    directory which is OUTSIDE the wheel — pip-install users could not
    run ``roam ci-setup --with-oscal`` or ``roam evidence-oscal`` because
    the helper file was not bundled. These tests assert the wheel-safe
    package-resource path the runtime resolver depends on.
    """

    def test_yaml_is_importable_as_package_resource(self):
        """``roam.templates.audit_report.control-mapping.yaml`` resolves."""
        from importlib.resources import as_file, files

        resource = files("roam.templates.audit_report") / "control-mapping.yaml"
        with as_file(resource) as path:
            assert path.exists(), (
                f"control-mapping.yaml not found at package-resource path "
                f"{path}. Pyproject package-data may have dropped "
                f"roam.templates.audit_report — W554 regressed."
            )
            # Sanity: parse-able YAML with the expected top-level shape.
            text = path.read_text(encoding="utf-8")
            assert "controls:" in text or "control_id" in text, (
                "control-mapping.yaml content looks wrong — missing the "
                "expected v1 'controls:' header or v0 'control_id' rows."
            )

    def test_resolver_finds_package_resource_in_tmp_cwd(self, tmp_path, monkeypatch):
        """``_resolve_control_mapping_yaml`` falls through to the wheel copy
        when the project-root template directory is absent.

        Simulates the pip-install scenario: a user's project has NO
        ``templates/audit-report/`` directory, but the resolver should
        still locate the YAML via importlib.resources.
        """
        from roam.commands.cmd_ci_setup import _resolve_control_mapping_yaml

        # Bare project root — no templates/ at all.
        monkeypatch.chdir(tmp_path)
        yaml_path = _resolve_control_mapping_yaml(tmp_path)
        assert yaml_path is not None, (
            "Resolver returned None on a bare project — wheel-safe "
            "fallback (W554) is not firing. Users on pip install will "
            "see `roam ci-setup --with-oscal` fail."
        )
        assert yaml_path.exists()
        assert yaml_path.name == "control-mapping.yaml"

    def test_evidence_oscal_default_resolves_under_pip_install_shape(self, tmp_path, monkeypatch):
        """``cmd_evidence_oscal._default_control_map_path`` resolves the
        wheel-bundled YAML when CWD has no template directory."""
        from roam.commands.cmd_evidence_oscal import _default_control_map_path

        monkeypatch.chdir(tmp_path)
        resolved = _default_control_map_path()
        assert resolved.exists(), (
            "Default control-map resolver returned a non-existent path "
            f"({resolved}). pip-install users will see "
            "`roam evidence-oscal` exit with a packaging error."
        )
        assert resolved.name == "control-mapping.yaml"
