"""Tests for ``roam stale-refs`` — dangling file reference detection."""

from __future__ import annotations

import pytest

from tests.conftest import (
    assert_json_envelope,
    git_init,
    invoke_cli,
    parse_json_output,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def clean_project(tmp_path):
    """Project where every reference resolves."""
    proj = tmp_path / "clean_refs"
    proj.mkdir()
    (proj / ".gitignore").write_text(".roam/\n")

    (proj / "README.md").write_text("# Hello\n\nSee [the docs](docs/intro.md) for more.\n")
    (proj / "docs").mkdir()
    (proj / "docs" / "intro.md").write_text("intro\n")
    git_init(proj)
    return proj


@pytest.fixture
def dangling_project(tmp_path):
    """Project with several dangling references in different forms."""
    proj = tmp_path / "stale_refs_proj"
    proj.mkdir()
    (proj / ".gitignore").write_text(".roam/\n")

    # Markdown inline link to a missing file.
    (proj / "README.md").write_text(
        "# README\n"
        "\n"
        "See [the strategy doc](docs/strategy/cold-outreach.md).\n"
        "\n"
        "Also `internal backlog` for the backlog.\n"
    )
    # Reference-style + HTML href, both pointing at missing files.
    (proj / "docs").mkdir()
    (proj / "docs" / "site.html").write_text('<a href="missing/landing-page-spec.html">landing</a>\n')
    (proj / "docs" / "index.md").write_text(
        "[ref]: docs/products/launch.md\n[home]: docs/intro.md\n"  # this one exists below
    )
    (proj / "docs" / "intro.md").write_text("intro\n")
    git_init(proj)
    return proj


# ---------------------------------------------------------------------------
# Smoke
# ---------------------------------------------------------------------------


class TestStaleRefsSmoke:
    def test_clean_project_exits_zero(self, cli_runner, clean_project, monkeypatch):
        monkeypatch.chdir(clean_project)
        result = invoke_cli(cli_runner, ["stale-refs"], cwd=clean_project)
        assert result.exit_code == 0
        assert "all refs resolve" in result.output

    def test_dangling_project_exits_zero_without_gate(self, cli_runner, dangling_project, monkeypatch):
        """Default behaviour is informational — exit 0 even when stale refs found."""
        monkeypatch.chdir(dangling_project)
        result = invoke_cli(cli_runner, ["stale-refs"], cwd=dangling_project)
        assert result.exit_code == 0
        assert "stale ref" in result.output.lower()

    def test_no_index_required(self, cli_runner, tmp_path, monkeypatch):
        """Command must work without ``roam index`` ever being run."""
        proj = tmp_path / "no_idx"
        proj.mkdir()
        (proj / "README.md").write_text("[x](missing.md)\n")
        git_init(proj)
        monkeypatch.chdir(proj)
        result = invoke_cli(cli_runner, ["stale-refs"], cwd=proj)
        assert result.exit_code == 0
        assert "missing.md" in result.output


# ---------------------------------------------------------------------------
# Detection coverage
# ---------------------------------------------------------------------------


class TestStaleRefsDetection:
    def test_finds_inline_markdown_link(self, cli_runner, dangling_project, monkeypatch):
        monkeypatch.chdir(dangling_project)
        result = invoke_cli(cli_runner, ["stale-refs"], cwd=dangling_project)
        assert "docs/strategy/cold-outreach.md" in result.output

    def test_finds_backtick_path(self, cli_runner, dangling_project, monkeypatch):
        monkeypatch.chdir(dangling_project)
        result = invoke_cli(cli_runner, ["stale-refs"], cwd=dangling_project)
        assert "internal backlog" in result.output

    def test_finds_html_href(self, cli_runner, dangling_project, monkeypatch):
        monkeypatch.chdir(dangling_project)
        result = invoke_cli(cli_runner, ["stale-refs"], cwd=dangling_project)
        # Path is relative to docs/site.html → resolves to docs/missing/...
        assert "missing/landing-page-spec.html" in result.output

    def test_finds_reference_style_link(self, cli_runner, dangling_project, monkeypatch):
        monkeypatch.chdir(dangling_project)
        result = invoke_cli(cli_runner, ["stale-refs"], cwd=dangling_project)
        # Reference-style link pointing at a missing file. Resolved relative
        # to docs/index.md so it becomes docs/docs/products/launch.md.
        assert "docs/products/launch.md" in result.output

    def test_skips_existing_targets(self, cli_runner, tmp_path, monkeypatch):
        """Live targets must NOT appear in the report."""
        proj = tmp_path / "live_target"
        proj.mkdir()
        (proj / "README.md").write_text("[exists](docs/intro.md) and [missing](docs/gone.md)\n")
        (proj / "docs").mkdir()
        (proj / "docs" / "intro.md").write_text("hi\n")
        git_init(proj)
        monkeypatch.chdir(proj)
        result = invoke_cli(cli_runner, ["stale-refs"], cwd=proj, json_mode=True)
        data = parse_json_output(result, "stale-refs")
        targets = {t["target"] for t in data["targets"]}
        assert "docs/gone.md" in targets
        assert "docs/intro.md" not in targets

    def test_skips_external_urls(self, cli_runner, tmp_path, monkeypatch):
        proj = tmp_path / "ext"
        proj.mkdir()
        (proj / "README.md").write_text("[google](https://google.com) and [mail](mailto:a@b.com)\n")
        git_init(proj)
        monkeypatch.chdir(proj)
        result = invoke_cli(cli_runner, ["stale-refs"], cwd=proj)
        assert result.exit_code == 0
        assert "all refs resolve" in result.output

    def test_skips_anchor_only_refs(self, cli_runner, tmp_path, monkeypatch):
        proj = tmp_path / "anchor"
        proj.mkdir()
        (proj / "README.md").write_text("[top](#header)\n")
        git_init(proj)
        monkeypatch.chdir(proj)
        result = invoke_cli(cli_runner, ["stale-refs"], cwd=proj)
        assert result.exit_code == 0
        assert "all refs resolve" in result.output

    def test_strips_fragments_and_queries(self, cli_runner, tmp_path, monkeypatch):
        proj = tmp_path / "frag"
        proj.mkdir()
        (proj / "README.md").write_text("[a](docs/page.md#section)\n[b](docs/page.md?v=1)\n")
        (proj / "docs").mkdir()
        # Anchor must exist for the check to pass — that's the v12.48
        # contract: file resolves AND anchor resolves.
        (proj / "docs" / "page.md").write_text("# Section\n\nok\n")
        git_init(proj)
        monkeypatch.chdir(proj)
        result = invoke_cli(cli_runner, ["stale-refs"], cwd=proj)
        assert result.exit_code == 0
        # Page exists with the section anchor, query variant resolves the
        # same way — neither should be flagged.
        assert "all refs resolve" in result.output


# ---------------------------------------------------------------------------
# JSON envelope
# ---------------------------------------------------------------------------


class TestStaleRefsJSON:
    def test_envelope_shape(self, cli_runner, dangling_project, monkeypatch):
        monkeypatch.chdir(dangling_project)
        result = invoke_cli(cli_runner, ["stale-refs"], cwd=dangling_project, json_mode=True)
        data = parse_json_output(result, "stale-refs")
        assert_json_envelope(data, "stale-refs")
        assert data["summary"]["missing_targets"] >= 1
        assert data["summary"]["stale_refs"] >= 1
        assert "verdict" in data["summary"]
        assert isinstance(data["targets"], list)
        first = data["targets"][0]
        assert "target" in first
        assert "ref_count" in first
        assert "sources" in first
        assert isinstance(first["sources"], list)

    def test_envelope_clean_project(self, cli_runner, clean_project, monkeypatch):
        monkeypatch.chdir(clean_project)
        result = invoke_cli(cli_runner, ["stale-refs"], cwd=clean_project, json_mode=True)
        data = parse_json_output(result, "stale-refs")
        assert data["summary"]["missing_targets"] == 0
        assert data["summary"]["stale_refs"] == 0


# ---------------------------------------------------------------------------
# Flags
# ---------------------------------------------------------------------------


class TestStaleRefsFlags:
    def test_gate_exits_5_on_stale(self, cli_runner, dangling_project, monkeypatch):
        monkeypatch.chdir(dangling_project)
        result = invoke_cli(cli_runner, ["stale-refs", "--gate"], cwd=dangling_project)
        assert result.exit_code == 5

    def test_gate_exits_0_on_clean(self, cli_runner, clean_project, monkeypatch):
        monkeypatch.chdir(clean_project)
        result = invoke_cli(cli_runner, ["stale-refs", "--gate"], cwd=clean_project)
        assert result.exit_code == 0

    def test_kind_filter(self, cli_runner, dangling_project, monkeypatch):
        """Restricting to backtick should hide markdown-link results."""
        monkeypatch.chdir(dangling_project)
        result = invoke_cli(
            cli_runner,
            ["stale-refs", "--kind", "backtick"],
            cwd=dangling_project,
            json_mode=True,
        )
        data = parse_json_output(result, "stale-refs")
        all_kinds = {s["kind"] for t in data["targets"] for s in t["sources"]}
        assert all_kinds == {"backtick"} or not all_kinds

    def test_rename_hint_suggests_existing_basename(self, cli_runner, tmp_path, monkeypatch):
        """When a referenced filename was moved, the basename match should surface."""
        proj = tmp_path / "rename_proj"
        proj.mkdir()
        # Renamed-in-tree: README still points at old/path/notes.md, but
        # notes.md actually lives at new/notes.md now.
        (proj / "README.md").write_text("see [notes](old/path/notes.md)\n")
        (proj / "new").mkdir()
        (proj / "new" / "notes.md").write_text("hi\n")
        git_init(proj)
        monkeypatch.chdir(proj)
        result = invoke_cli(cli_runner, ["stale-refs"], cwd=proj, json_mode=True)
        data = parse_json_output(result, "stale-refs")
        targets = {t["target"]: t for t in data["targets"]}
        assert "old/path/notes.md" in targets
        assert targets["old/path/notes.md"].get("rename_hint") == "new/notes.md"

    def test_no_rename_hint_flag(self, cli_runner, tmp_path, monkeypatch):
        proj = tmp_path / "no_hint_proj"
        proj.mkdir()
        (proj / "README.md").write_text("[x](old/notes.md)\n")
        (proj / "new").mkdir()
        (proj / "new" / "notes.md").write_text("hi\n")
        git_init(proj)
        monkeypatch.chdir(proj)
        result = invoke_cli(
            cli_runner,
            ["stale-refs", "--no-rename-hint"],
            cwd=proj,
            json_mode=True,
        )
        data = parse_json_output(result, "stale-refs")
        targets = {t["target"]: t for t in data["targets"]}
        assert "old/notes.md" in targets
        assert "rename_hint" not in targets["old/notes.md"]


# ---------------------------------------------------------------------------
# Polish coverage — false-positive filters and new flags
# ---------------------------------------------------------------------------


class TestStaleRefsFalsePositiveFilters:
    """Each test pins down one of the noise classes we deliberately filter."""

    def test_source_code_regex_char_class_not_flagged(self, cli_runner, tmp_path, monkeypatch):
        """Regex character classes inside .py files must not trigger md_inline."""
        proj = tmp_path / "regex_noise"
        proj.mkdir()
        (proj / "code.py").write_text('import re\n_RE = re.compile(r"(\\w+)[\'\\"]([^\'\\"]+)")\n')
        git_init(proj)
        monkeypatch.chdir(proj)
        result = invoke_cli(cli_runner, ["stale-refs"], cwd=proj, json_mode=True)
        data = parse_json_output(result, "stale-refs")
        # If we mistakenly treat `[^'"]+` as a markdown link, this would be
        # non-empty. Prose-mode restriction must keep it empty.
        assert data["summary"]["missing_targets"] == 0

    def test_runtime_path_skipped(self, cli_runner, tmp_path, monkeypatch):
        """Refs into .roam/ are runtime-generated and must not be flagged."""
        proj = tmp_path / "runtime_skip"
        proj.mkdir()
        (proj / "README.md").write_text("see `.roam/rules.yml` for config\n")
        git_init(proj)
        monkeypatch.chdir(proj)
        result = invoke_cli(cli_runner, ["stale-refs"], cwd=proj, json_mode=True)
        data = parse_json_output(result, "stale-refs")
        assert data["summary"]["missing_targets"] == 0

    def test_placeholder_and_glob_skipped(self, cli_runner, tmp_path, monkeypatch):
        """Placeholders <foo>, globs *.html, and {x} braces are not paths."""
        proj = tmp_path / "placeholders"
        proj.mkdir()
        (proj / "README.md").write_text("[a](<project_root>/foo.md) [b](docs/*.html) [c](prompts/{task}.txt)\n")
        git_init(proj)
        monkeypatch.chdir(proj)
        result = invoke_cli(cli_runner, ["stale-refs"], cwd=proj, json_mode=True)
        data = parse_json_output(result, "stale-refs")
        assert data["summary"]["missing_targets"] == 0

    def test_dotfile_basename_skipped(self, cli_runner, tmp_path, monkeypatch):
        """Bare `.eslintrc` style refs are user-creatable optional configs."""
        proj = tmp_path / "dotfile"
        proj.mkdir()
        (proj / "README.md").write_text("Drop a `.roam-gates.yml` next to .git\n")
        git_init(proj)
        monkeypatch.chdir(proj)
        result = invoke_cli(cli_runner, ["stale-refs"], cwd=proj, json_mode=True)
        data = parse_json_output(result, "stale-refs")
        assert data["summary"]["missing_targets"] == 0

    def test_bare_basename_in_source_code_skipped(self, cli_runner, tmp_path, monkeypatch):
        """`auth.py` mentioned in a .py docstring without a path = placeholder, skip."""
        proj = tmp_path / "bare_in_code"
        proj.mkdir()
        (proj / "test_demo.py").write_text('"""Test that exercises `auth.py` and `cmd_FOO.py`."""\nx = 1\n')
        git_init(proj)
        monkeypatch.chdir(proj)
        result = invoke_cli(cli_runner, ["stale-refs"], cwd=proj, json_mode=True)
        data = parse_json_output(result, "stale-refs")
        # Neither auth.py nor cmd_FOO.py exist; without the source-code
        # filter this would be 2 false positives.
        assert data["summary"]["missing_targets"] == 0

    def test_existing_basename_anywhere_skipped(self, cli_runner, tmp_path, monkeypatch):
        """`cli.py` in README is a generic mention; if the file exists anywhere, OK."""
        proj = tmp_path / "basename_anywhere"
        proj.mkdir()
        (proj / "README.md").write_text("Edit `cli.py` to register commands.\n")
        (proj / "src").mkdir()
        (proj / "src" / "cli.py").write_text("# the cli\n")
        git_init(proj)
        monkeypatch.chdir(proj)
        result = invoke_cli(cli_runner, ["stale-refs"], cwd=proj, json_mode=True)
        data = parse_json_output(result, "stale-refs")
        assert data["summary"]["missing_targets"] == 0


class TestStaleRefsAbsoluteRoutes:
    def test_extensionless_routes_skipped_by_default(self, cli_runner, tmp_path, monkeypatch):
        """`<a href="/setup">` is a static-site URL route, not a file reference."""
        proj = tmp_path / "routes"
        proj.mkdir()
        (proj / "index.html").write_text('<a href="/setup">Setup</a> <a href="/pricing">Pricing</a>\n')
        git_init(proj)
        monkeypatch.chdir(proj)
        result = invoke_cli(cli_runner, ["stale-refs"], cwd=proj, json_mode=True)
        data = parse_json_output(result, "stale-refs")
        assert data["summary"]["missing_targets"] == 0

    def test_check_absolute_routes_flips_default(self, cli_runner, tmp_path, monkeypatch):
        """--check-absolute-routes treats /setup as a missing file."""
        proj = tmp_path / "routes_strict"
        proj.mkdir()
        (proj / "index.html").write_text('<a href="/setup">Setup</a>\n')
        git_init(proj)
        monkeypatch.chdir(proj)
        result = invoke_cli(
            cli_runner,
            ["stale-refs", "--check-absolute-routes"],
            cwd=proj,
            json_mode=True,
        )
        data = parse_json_output(result, "stale-refs")
        assert data["summary"]["missing_targets"] == 1

    def test_public_folder_fallback_resolves(self, cli_runner, tmp_path, monkeypatch):
        """`/favicon.svg` referenced from HTML resolves to public/favicon.svg."""
        proj = tmp_path / "public_fb"
        proj.mkdir()
        (proj / "index.html").write_text('<link rel="icon" href="/favicon.svg">\n')
        (proj / "public").mkdir()
        (proj / "public" / "favicon.svg").write_text("<svg/>\n")
        git_init(proj)
        monkeypatch.chdir(proj)
        result = invoke_cli(cli_runner, ["stale-refs"], cwd=proj, json_mode=True)
        data = parse_json_output(result, "stale-refs")
        assert data["summary"]["missing_targets"] == 0

    def test_deploy_root_walk_resolves(self, cli_runner, tmp_path, monkeypatch):
        """`/asset.png` from templates/site/about.html resolves to templates/site/asset.png."""
        proj = tmp_path / "deploy_root"
        proj.mkdir()
        site = proj / "templates" / "site"
        site.mkdir(parents=True)
        (site / "about.html").write_text('<img src="/asset.png">\n')
        (site / "asset.png").write_text("png-bytes\n")
        git_init(proj)
        monkeypatch.chdir(proj)
        result = invoke_cli(cli_runner, ["stale-refs"], cwd=proj, json_mode=True)
        data = parse_json_output(result, "stale-refs")
        assert data["summary"]["missing_targets"] == 0


class TestStaleRefsIgnore:
    def test_ignore_source_glob(self, cli_runner, tmp_path, monkeypatch):
        """--ignore CHANGELOG.md must remove CHANGELOG-sourced findings."""
        proj = tmp_path / "ignore_src"
        proj.mkdir()
        (proj / "CHANGELOG.md").write_text("v1: removed [old](docs/old.md)\n")
        (proj / "README.md").write_text("see [active](docs/active.md)\n")
        git_init(proj)
        monkeypatch.chdir(proj)
        result = invoke_cli(
            cli_runner,
            ["stale-refs", "--ignore", "CHANGELOG.md"],
            cwd=proj,
            json_mode=True,
        )
        data = parse_json_output(result, "stale-refs")
        targets = {t["target"] for t in data["targets"]}
        assert "docs/active.md" in targets
        assert "docs/old.md" not in targets

    def test_ignore_target_glob(self, cli_runner, tmp_path, monkeypatch):
        """--ignore-target docs/old/* suppresses missing files in that subtree."""
        proj = tmp_path / "ignore_tgt"
        proj.mkdir()
        (proj / "README.md").write_text("[old](docs/old/x.md) and [new](docs/new/y.md)\n")
        git_init(proj)
        monkeypatch.chdir(proj)
        result = invoke_cli(
            cli_runner,
            ["stale-refs", "--ignore-target", "docs/old/*"],
            cwd=proj,
            json_mode=True,
        )
        data = parse_json_output(result, "stale-refs")
        targets = {t["target"] for t in data["targets"]}
        assert "docs/new/y.md" in targets
        assert "docs/old/x.md" not in targets


class TestStaleRefsBacktickFallback:
    def test_backtick_resolves_via_project_root_when_source_relative_misses(self, cli_runner, tmp_path, monkeypatch):
        """`docs/intro.md` mentioned in src/foo.py should not flag src/docs/intro.md.

        Source-relative resolution would put it at src/docs/intro.md (missing),
        but project-root anchor at docs/intro.md exists. Either anchor = live.
        """
        proj = tmp_path / "backtick_root"
        proj.mkdir()
        (proj / "src").mkdir()
        (proj / "src" / "foo.py").write_text('"""See `docs/intro.md` for prose."""\nx = 1\n')
        (proj / "docs").mkdir()
        (proj / "docs" / "intro.md").write_text("hi\n")
        git_init(proj)
        monkeypatch.chdir(proj)
        result = invoke_cli(cli_runner, ["stale-refs"], cwd=proj, json_mode=True)
        data = parse_json_output(result, "stale-refs")
        assert data["summary"]["missing_targets"] == 0


class TestStaleRefsTimingAndShape:
    def test_scan_seconds_in_summary(self, cli_runner, dangling_project, monkeypatch):
        monkeypatch.chdir(dangling_project)
        result = invoke_cli(cli_runner, ["stale-refs"], cwd=dangling_project, json_mode=True)
        data = parse_json_output(result, "stale-refs")
        assert "scan_seconds" in data["summary"]
        assert isinstance(data["summary"]["scan_seconds"], (int, float))
        assert data["summary"]["scan_seconds"] >= 0

    def test_verdict_summarises_counts(self, cli_runner, dangling_project, monkeypatch):
        """Verdict line must surface stale_refs, missing_targets, and files_scanned."""
        monkeypatch.chdir(dangling_project)
        result = invoke_cli(cli_runner, ["stale-refs"], cwd=dangling_project)
        first_line = result.output.splitlines()[0]
        assert "VERDICT:" in first_line
        assert "stale ref" in first_line.lower()
        assert "missing target" in first_line.lower()
        assert "files" in first_line.lower()


# ---------------------------------------------------------------------------
# Edge cases & robustness
# ---------------------------------------------------------------------------


class TestStaleRefsEdgeCases:
    def test_empty_directory_handled(self, cli_runner, tmp_path, monkeypatch):
        """No files, no git — should emit a clean verdict, exit 0."""
        proj = tmp_path / "empty_dir"
        proj.mkdir()
        monkeypatch.chdir(proj)
        result = invoke_cli(cli_runner, ["stale-refs"], cwd=proj, json_mode=True)
        data = parse_json_output(result, "stale-refs")
        assert data["summary"]["files_scanned"] == 0
        assert data["summary"]["missing_targets"] == 0
        assert data["summary"]["stale_refs"] == 0

    def test_path_escape_via_dotdot_segments(self, cli_runner, tmp_path, monkeypatch):
        """`[link](../../etc/passwd)` must NOT escape the project root."""
        proj = tmp_path / "escape"
        proj.mkdir()
        (proj / "docs").mkdir()
        (proj / "docs" / "page.md").write_text("[etc](../../../etc/passwd)\n")
        git_init(proj)
        monkeypatch.chdir(proj)
        result = invoke_cli(cli_runner, ["stale-refs"], cwd=proj, json_mode=True)
        data = parse_json_output(result, "stale-refs")
        # Either skipped (escape detected) or kept inside project_root.
        # Either way, no path containing 'etc/passwd' from outside the project.
        for tgt in (t["target"] for t in data["targets"]):
            assert ".." not in tgt
            assert not tgt.startswith("/")

    def test_roamignore_honoured_via_discovery(self, cli_runner, tmp_path, monkeypatch):
        """.roamignore should suppress files from the scan via discover_files."""
        proj = tmp_path / "ignore_via_roamignore"
        proj.mkdir()
        (proj / ".gitignore").write_text(".roam/\n")
        (proj / ".roamignore").write_text("LEGACY.md\n")
        (proj / "README.md").write_text("[a](docs/active.md)\n")
        (proj / "LEGACY.md").write_text("[old](docs/old/x.md)\n")
        git_init(proj)
        monkeypatch.chdir(proj)
        result = invoke_cli(cli_runner, ["stale-refs"], cwd=proj, json_mode=True)
        data = parse_json_output(result, "stale-refs")
        targets = {t["target"] for t in data["targets"]}
        assert "docs/active.md" in targets
        assert "docs/old/x.md" not in targets

    def test_backslash_normalized_in_ignore_pattern(self, cli_runner, tmp_path, monkeypatch):
        """Windows-style `docs\\old\\*` and POSIX `docs/old/*` must behave identically."""
        proj = tmp_path / "bslash"
        proj.mkdir()
        (proj / "README.md").write_text("[old](docs/old/x.md) and [new](docs/new/y.md)\n")
        git_init(proj)
        monkeypatch.chdir(proj)
        # Backslash form should match the same files as the forward-slash form.
        result = invoke_cli(
            cli_runner,
            ["stale-refs", "--ignore-target", r"docs\old\*"],
            cwd=proj,
            json_mode=True,
        )
        data = parse_json_output(result, "stale-refs")
        targets = {t["target"] for t in data["targets"]}
        assert "docs/new/y.md" in targets
        assert "docs/old/x.md" not in targets


# ---------------------------------------------------------------------------
# SARIF output
# ---------------------------------------------------------------------------


class TestStaleRefsSarif:
    def test_sarif_envelope_shape(self, cli_runner, dangling_project, monkeypatch):
        """--sarif emits a valid SARIF 2.1.0 envelope with stale-refs/* rules."""
        import json

        monkeypatch.chdir(dangling_project)
        result = invoke_cli(cli_runner, ["--sarif", "stale-refs"], cwd=dangling_project)
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["version"] == "2.1.0"
        assert "$schema" in data
        runs = data["runs"]
        assert len(runs) == 1
        rule_ids = {r["id"] for r in runs[0]["tool"]["driver"]["rules"]}
        # Every emitted rule should be scoped under stale-refs/.
        assert all(rid.startswith("stale-refs/") for rid in rule_ids)
        results = runs[0]["results"]
        assert len(results) >= 1
        for r in results:
            assert r["ruleId"].startswith("stale-refs/")
            assert r["locations"]
            assert r["message"]["text"]

    def test_sarif_clean_repo(self, cli_runner, clean_project, monkeypatch):
        """SARIF on a clean repo emits an empty results array, valid envelope."""
        import json

        monkeypatch.chdir(clean_project)
        result = invoke_cli(cli_runner, ["--sarif", "stale-refs"], cwd=clean_project)
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["version"] == "2.1.0"
        assert data["runs"][0]["results"] == []

    def test_sarif_gate_exits_5_on_findings(self, cli_runner, dangling_project, monkeypatch):
        """--gate combined with --sarif still exits 5 on findings."""
        monkeypatch.chdir(dangling_project)
        result = invoke_cli(
            cli_runner,
            ["--sarif", "stale-refs", "--gate"],
            cwd=dangling_project,
        )
        assert result.exit_code == 5


# ---------------------------------------------------------------------------
# --by-file mode
# ---------------------------------------------------------------------------


class TestStaleRefsByFile:
    def test_by_file_groups_by_source(self, cli_runner, tmp_path, monkeypatch):
        """--by-file inverts the report, grouping refs under each source file."""
        proj = tmp_path / "by_file_mode"
        proj.mkdir()
        (proj / "README.md").write_text("[a](docs/missing-1.md) and [b](docs/missing-2.md)\n")
        (proj / "OTHER.md").write_text("[c](docs/missing-1.md)\n")
        git_init(proj)
        monkeypatch.chdir(proj)
        result = invoke_cli(cli_runner, ["stale-refs", "--by-file"], cwd=proj)
        assert result.exit_code == 0
        out = result.output
        # README.md should appear with 2 stale refs (it has two missing targets).
        assert "README.md" in out
        assert "2 stale refs" in out
        # OTHER.md has 1 stale ref.
        assert "OTHER.md" in out
        # Verdict still emitted.
        assert "VERDICT:" in out


# ---------------------------------------------------------------------------
# v12.48 — anchor validation
# ---------------------------------------------------------------------------


class TestStaleRefsAnchors:
    def test_anchor_present_resolves(self, cli_runner, tmp_path, monkeypatch):
        """File exists + anchor exists → not flagged."""
        proj = tmp_path / "anchor_ok"
        proj.mkdir()
        (proj / "README.md").write_text("[setup](docs/install.md#prereqs)\n")
        (proj / "docs").mkdir()
        (proj / "docs" / "install.md").write_text("# Prereqs\n\nstuff\n")
        git_init(proj)
        monkeypatch.chdir(proj)
        result = invoke_cli(cli_runner, ["stale-refs"], cwd=proj, json_mode=True)
        data = parse_json_output(result, "stale-refs")
        assert data["summary"]["missing_targets"] == 0

    def test_anchor_missing_flagged_as_anchor_kind(self, cli_runner, tmp_path, monkeypatch):
        """File exists + anchor missing → flagged with kind=anchor."""
        proj = tmp_path / "anchor_miss"
        proj.mkdir()
        (proj / "README.md").write_text("[setup](docs/install.md#cloudflare-pages)\n")
        (proj / "docs").mkdir()
        (proj / "docs" / "install.md").write_text("# Prereqs\n\nstuff\n")
        git_init(proj)
        monkeypatch.chdir(proj)
        result = invoke_cli(cli_runner, ["stale-refs"], cwd=proj, json_mode=True)
        data = parse_json_output(result, "stale-refs")
        assert data["summary"]["missing_targets"] == 1
        assert data["summary"]["anchor_findings"] == 1
        first = data["targets"][0]
        assert first["target"] == "docs/install.md#cloudflare-pages"
        assert first["sources"][0]["kind"] == "anchor"
        assert first["sources"][0]["anchor"] == "cloudflare-pages"

    def test_anchor_setext_header_recognised(self, cli_runner, tmp_path, monkeypatch):
        """Setext-style ``Header`` followed by ``===`` is treated as a header."""
        proj = tmp_path / "setext"
        proj.mkdir()
        (proj / "README.md").write_text("[deploy](docs/cd.md#deployment-guide)\n")
        (proj / "docs").mkdir()
        (proj / "docs" / "cd.md").write_text("Deployment Guide\n================\n\nGo!\n")
        git_init(proj)
        monkeypatch.chdir(proj)
        result = invoke_cli(cli_runner, ["stale-refs"], cwd=proj, json_mode=True)
        data = parse_json_output(result, "stale-refs")
        assert data["summary"]["missing_targets"] == 0

    def test_anchor_html_id_attribute_recognised(self, cli_runner, tmp_path, monkeypatch):
        """``<a id="foo">`` declarations satisfy ``#foo`` references."""
        proj = tmp_path / "html_anchor"
        proj.mkdir()
        (proj / "README.md").write_text("[lookup](docs/api.md#custom-anchor)\n")
        (proj / "docs").mkdir()
        (proj / "docs" / "api.md").write_text('# API\n\n<a id="custom-anchor"></a>\n\nLookup table here.\n')
        git_init(proj)
        monkeypatch.chdir(proj)
        result = invoke_cli(cli_runner, ["stale-refs"], cwd=proj, json_mode=True)
        data = parse_json_output(result, "stale-refs")
        assert data["summary"]["missing_targets"] == 0

    def test_no_anchors_flag_disables_validation(self, cli_runner, tmp_path, monkeypatch):
        """``--no-anchors`` skips anchor validation entirely."""
        proj = tmp_path / "no_anchors"
        proj.mkdir()
        (proj / "README.md").write_text("[setup](docs/install.md#missing)\n")
        (proj / "docs").mkdir()
        (proj / "docs" / "install.md").write_text("# Prereqs\n")
        git_init(proj)
        monkeypatch.chdir(proj)
        result = invoke_cli(cli_runner, ["stale-refs", "--no-anchors"], cwd=proj, json_mode=True)
        data = parse_json_output(result, "stale-refs")
        assert data["summary"]["missing_targets"] == 0


# ---------------------------------------------------------------------------
# v12.48 — confidence-tagged hint chain
# ---------------------------------------------------------------------------


class TestStaleRefsHints:
    def test_unique_basename_match_in_subtree_is_high(self, cli_runner, tmp_path, monkeypatch):
        """Single basename match with shared dir prefix → HIGH confidence."""
        proj = tmp_path / "high_basename"
        proj.mkdir()
        (proj / "docs").mkdir()
        (proj / "docs" / "guide.md").write_text("hi\n")
        (proj / "README.md").write_text("[g](docs/missing/guide.md)\n")
        git_init(proj)
        monkeypatch.chdir(proj)
        result = invoke_cli(cli_runner, ["stale-refs"], cwd=proj, json_mode=True)
        data = parse_json_output(result, "stale-refs")
        first = data["targets"][0]
        assert first["hint"]["confidence"] == "HIGH"
        assert first["hint"]["target"] == "docs/guide.md"
        assert first["hint"]["source"] == "basename"

    def test_multiple_basename_matches_is_low(self, cli_runner, tmp_path, monkeypatch):
        """Multiple basename matches → LOW confidence."""
        proj = tmp_path / "low_basename"
        proj.mkdir()
        for d in ("a", "b", "c"):
            (proj / d).mkdir()
            (proj / d / "guide.md").write_text("hi\n")
        (proj / "README.md").write_text("[g](old/guide.md)\n")
        git_init(proj)
        monkeypatch.chdir(proj)
        result = invoke_cli(cli_runner, ["stale-refs"], cwd=proj, json_mode=True)
        data = parse_json_output(result, "stale-refs")
        first = data["targets"][0]
        assert first["hint"]["confidence"] == "LOW"

    def test_git_history_rename_is_high_confidence(self, cli_runner, tmp_path, monkeypatch):
        """A git-attested rename should beat basename heuristics with HIGH confidence."""
        import subprocess

        proj = tmp_path / "git_rename"
        proj.mkdir()
        (proj / ".gitignore").write_text(".roam/\n")
        (proj / "old-name.md").write_text("content\n")
        (proj / "README.md").write_text("[link](old-name.md)\n")
        git_init(proj)
        # Now do an attested git mv so the rename shows in history.
        subprocess.run(["git", "mv", "old-name.md", "new-name.md"], cwd=proj, capture_output=True)
        subprocess.run(["git", "commit", "-m", "rename"], cwd=proj, capture_output=True)
        monkeypatch.chdir(proj)
        result = invoke_cli(cli_runner, ["stale-refs"], cwd=proj, json_mode=True)
        data = parse_json_output(result, "stale-refs")
        first = data["targets"][0]
        assert first["target"] == "old-name.md"
        assert first["hint"]["confidence"] == "HIGH"
        assert first["hint"]["source"] == "git-history"
        assert first["hint"]["target"] == "new-name.md"


# ---------------------------------------------------------------------------
# v12.48 — --diff branch filter
# ---------------------------------------------------------------------------


class TestStaleRefsDiff:
    def test_diff_filters_to_branch_changes_only(self, cli_runner, tmp_path, monkeypatch):
        """Refs in main-only files are dropped; only branch-introduced refs remain."""
        import subprocess

        proj = tmp_path / "diff_branch"
        proj.mkdir()
        (proj / ".gitignore").write_text(".roam/\n")
        # State on main: an existing CHANGELOG with a stale ref (historical).
        (proj / "CHANGELOG.md").write_text("[old](docs/historical.md)\n")
        git_init(proj)
        # Branch off and add a NEW stale ref via README.
        subprocess.run(["git", "checkout", "-b", "feat"], cwd=proj, capture_output=True)
        (proj / "README.md").write_text("[new](docs/freshly-removed.md)\n")
        subprocess.run(["git", "add", "README.md"], cwd=proj, capture_output=True)
        subprocess.run(["git", "commit", "-m", "feat"], cwd=proj, capture_output=True)
        monkeypatch.chdir(proj)
        result = invoke_cli(
            cli_runner,
            ["stale-refs", "--diff", "master"],
            cwd=proj,
            json_mode=True,
        )
        data = parse_json_output(result, "stale-refs")
        targets = {t["target"] for t in data["targets"]}
        # CHANGELOG mention pre-existed → should be filtered out.
        # README mention is branch-new → should remain.
        assert "docs/freshly-removed.md" in targets
        assert "docs/historical.md" not in targets
        # Diff metadata should be in summary.
        assert "diff_base" in data["summary"]

    def test_diff_invalid_ref_warns_and_keeps_results(self, cli_runner, tmp_path, monkeypatch):
        """Unresolvable --diff ref logs a warning and falls back to no filter."""
        proj = tmp_path / "diff_bad_ref"
        proj.mkdir()
        (proj / ".gitignore").write_text(".roam/\n")
        (proj / "README.md").write_text("[x](missing.md)\n")
        git_init(proj)
        monkeypatch.chdir(proj)
        result = invoke_cli(
            cli_runner,
            ["stale-refs", "--diff", "no-such-ref-xyz"],
            cwd=proj,
            json_mode=True,
        )
        data = parse_json_output(result, "stale-refs")
        # Without a valid base, we keep the original findings.
        assert data["summary"]["missing_targets"] == 1


# ---------------------------------------------------------------------------
# v12.48 — --fix preview/apply
# ---------------------------------------------------------------------------


class TestStaleRefsFix:
    def test_fix_preview_emits_diff(self, cli_runner, tmp_path, monkeypatch):
        """``--fix preview`` prints a unified diff for HIGH-confidence hints.

        Uses a shared ``docs/`` prefix so the basename match is HIGH
        confidence — the threshold for ``--fix`` to act.
        """
        proj = tmp_path / "fix_preview"
        proj.mkdir()
        (proj / "docs").mkdir()
        (proj / "docs" / "guide.md").write_text("hi\n")
        (proj / "README.md").write_text("[guide](docs/old/guide.md)\n")
        git_init(proj)
        monkeypatch.chdir(proj)
        result = invoke_cli(cli_runner, ["stale-refs", "--fix", "preview"], cwd=proj)
        assert result.exit_code == 0
        assert "--fix preview" in result.output
        assert "docs/old/guide.md" in result.output  # the line being changed
        assert "docs/guide.md" in result.output  # the replacement
        assert "@@" in result.output  # unified diff hunk marker

    def test_fix_apply_rewrites_in_place(self, cli_runner, tmp_path, monkeypatch):
        """``--fix apply`` writes the substituted reference back to disk."""
        proj = tmp_path / "fix_apply"
        proj.mkdir()
        (proj / "docs").mkdir()
        (proj / "docs" / "guide.md").write_text("hi\n")
        readme = proj / "README.md"
        readme.write_text("[guide](docs/old/guide.md)\n")
        git_init(proj)
        monkeypatch.chdir(proj)
        result = invoke_cli(cli_runner, ["stale-refs", "--fix", "apply"], cwd=proj)
        assert result.exit_code == 0
        # File on disk has been rewritten with the new path.
        new_content = readme.read_text(encoding="utf-8")
        assert "docs/guide.md" in new_content
        assert "docs/old/guide.md" not in new_content

    def test_fix_skips_low_confidence(self, cli_runner, tmp_path, monkeypatch):
        """LOW-confidence hints (multiple basename matches) MUST NOT auto-fix."""
        proj = tmp_path / "fix_skip_low"
        proj.mkdir()
        for d in ("a", "b"):
            (proj / d).mkdir()
            (proj / d / "guide.md").write_text("hi\n")
        readme = proj / "README.md"
        readme.write_text("[guide](old/guide.md)\n")
        git_init(proj)
        monkeypatch.chdir(proj)
        result = invoke_cli(cli_runner, ["stale-refs", "--fix", "apply"], cwd=proj)
        assert result.exit_code == 0
        # File untouched — LOW confidence shouldn't trigger writes.
        assert readme.read_text(encoding="utf-8") == "[guide](old/guide.md)\n"

    def test_fix_preview_json_envelope(self, cli_runner, tmp_path, monkeypatch):
        """JSON mode under --fix preview still produces a valid envelope."""
        proj = tmp_path / "fix_json"
        proj.mkdir()
        (proj / "docs").mkdir()
        (proj / "docs" / "guide.md").write_text("hi\n")
        (proj / "README.md").write_text("[guide](docs/old/guide.md)\n")
        git_init(proj)
        monkeypatch.chdir(proj)
        result = invoke_cli(
            cli_runner,
            ["stale-refs", "--fix", "preview"],
            cwd=proj,
            json_mode=True,
        )
        data = parse_json_output(result, "stale-refs")
        assert_json_envelope(data, "stale-refs")
        assert data["summary"]["fix_mode"] == "preview"
        assert data["summary"]["edits_planned"] >= 1
        assert "diff" in data


# ---------------------------------------------------------------------------
# v12.48 — --sort-by ranking
# ---------------------------------------------------------------------------


class TestStaleRefsSort:
    def test_priority_outranks_ref_count(self, cli_runner, tmp_path, monkeypatch):
        """README references should rank above templates/ refs even with fewer hits."""
        proj = tmp_path / "sort_priority"
        proj.mkdir()
        # README has 1 stale ref, templates/sample.md has 5 (all to the same target).
        (proj / "README.md").write_text("[important](docs/important.md)\n")
        (proj / "templates").mkdir()
        (proj / "templates" / "sample.md").write_text(
            "[a](docs/sample-only.md)\n[b](docs/sample-only.md)\n"
            "[c](docs/sample-only.md)\n[d](docs/sample-only.md)\n"
            "[e](docs/sample-only.md)\n"
        )
        git_init(proj)
        monkeypatch.chdir(proj)
        result = invoke_cli(cli_runner, ["stale-refs"], cwd=proj, json_mode=True)
        data = parse_json_output(result, "stale-refs")
        # Priority sort places README finding above the templates/ finding.
        first_target = data["targets"][0]["target"]
        assert first_target == "docs/important.md"

    def test_ref_count_sort_inverts(self, cli_runner, tmp_path, monkeypatch):
        """``--sort-by ref-count`` puts the highest-ref-count target first."""
        proj = tmp_path / "sort_refcount"
        proj.mkdir()
        # README contributes 1 ref to docs/important.md.
        # templates/sample.md contributes 3 refs to a different target.
        # We use absolute-from-root paths so resolution doesn't shift the
        # target into the ``templates/`` subtree.
        (proj / "README.md").write_text("[a](docs/important.md)\n")
        (proj / "templates").mkdir()
        (proj / "templates" / "sample.md").write_text("[a](/docs/many.md)\n[b](/docs/many.md)\n[c](/docs/many.md)\n")
        git_init(proj)
        monkeypatch.chdir(proj)
        result = invoke_cli(
            cli_runner,
            ["stale-refs", "--sort-by", "ref-count"],
            cwd=proj,
            json_mode=True,
        )
        data = parse_json_output(result, "stale-refs")
        first_target = data["targets"][0]["target"]
        assert first_target == "docs/many.md"

    def test_alpha_sort_orders_by_target_path(self, cli_runner, tmp_path, monkeypatch):
        """``--sort-by alpha`` orders deterministically by target path."""
        proj = tmp_path / "sort_alpha"
        proj.mkdir()
        (proj / "README.md").write_text("[a](docs/zeta.md)\n[b](docs/alpha.md)\n[c](docs/middle.md)\n")
        git_init(proj)
        monkeypatch.chdir(proj)
        result = invoke_cli(
            cli_runner,
            ["stale-refs", "--sort-by", "alpha"],
            cwd=proj,
            json_mode=True,
        )
        data = parse_json_output(result, "stale-refs")
        targets = [t["target"] for t in data["targets"]]
        assert targets == sorted(targets)
