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
        "Also see `internal/backlog.md` for the backlog.\n"
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
        """Bare backtick strings in prose are inline code, not link syntax.
        They're scanned only when ``--scan-bare-backticks`` is set. Without
        the flag the historic bare-backtick path (``internal backlog``)
        does NOT surface — see Bug 2 in the external dogfood findings
        (cmd_stale_refs.py module docstring and
        tests/test_stale_refs_corruption.py for context)."""
        monkeypatch.chdir(dangling_project)
        # Default: bare backtick refs are NOT extracted.
        result = invoke_cli(cli_runner, ["stale-refs"], cwd=dangling_project)
        assert "internal/backlog.md" not in result.output
        # Opt-in: re-enables the historical detection.
        result = invoke_cli(
            cli_runner,
            ["stale-refs", "--scan-bare-backticks"],
            cwd=dangling_project,
        )
        assert "internal/backlog.md" in result.output

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

    def test_in_page_anchor_present_resolves(self, cli_runner, tmp_path, monkeypatch):
        """Pure-anchor refs (``#fragment`` only) validate against the SOURCE
        file's own headers.  When the header exists, no finding."""
        proj = tmp_path / "anchor_self_ok"
        proj.mkdir()
        # Reference points at a header that DOES exist in the same file.
        (proj / "README.md").write_text("[top](#header)\n\n# Header\n")
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

    def test_sarif_schema_structural_validation(self, cli_runner, dangling_project, monkeypatch):
        """1G.3: structural validator that mirrors what GitHub Code
        Scanning rejects.

        We don't pull jsonschema as a hard test dep — instead this test
        encodes the SARIF 2.1.0 invariants we've actually been bitten
        by in the field:

        * ``$schema`` matches the published 2.1.0 URL.
        * Every result's ``ruleId`` resolves to a ``rules`` entry on
          the same run's tool driver (otherwise GitHub silently drops
          the result).
        * Every result has ``level`` ∈ {error, warning, note, none}.
        * Every location has a ``physicalLocation.artifactLocation.uri``.
        * Every location's ``region`` has ``startLine ≥ 1``.
        * No null/missing required fields anywhere in the chain.

        Failing this test means a CI consumer (GitHub, GitLab, custom
        SARIF parsers) will reject the report. Tightening this test is
        cheaper than fielding a downstream bug report.
        """
        import json

        monkeypatch.chdir(dangling_project)
        result = invoke_cli(cli_runner, ["--sarif", "stale-refs"], cwd=dangling_project)
        assert result.exit_code == 0
        data = json.loads(result.output)

        # Top-level invariants.
        assert data["version"] == "2.1.0"
        assert "$schema" in data
        assert "2.1.0" in data["$schema"]
        assert isinstance(data["runs"], list) and len(data["runs"]) == 1

        run = data["runs"][0]
        driver = run["tool"]["driver"]
        assert isinstance(driver["name"], str) and driver["name"]
        assert isinstance(driver["version"], str) and driver["version"]
        assert isinstance(driver["rules"], list)

        rule_ids_defined = {r["id"] for r in driver["rules"]}
        for rule in driver["rules"]:
            assert isinstance(rule["id"], str)
            assert rule["shortDescription"]["text"]

        valid_levels = {"error", "warning", "note", "none"}
        for res in run["results"]:
            assert res["ruleId"] in rule_ids_defined, (
                f"{res['ruleId']!r} is not in the rules table — GitHub Code Scanning will silently drop this result."
            )
            assert res.get("level") in valid_levels
            assert res["message"]["text"]
            assert isinstance(res["locations"], list) and res["locations"]
            for loc in res["locations"]:
                phys = loc["physicalLocation"]
                assert phys["artifactLocation"]["uri"]
                # Region is optional in SARIF, but when present startLine must be ≥1.
                region = phys.get("region")
                if region:
                    assert region.get("startLine", 0) >= 1


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


# ---------------------------------------------------------------------------
# v12.48 polish — anchor edge cases
# ---------------------------------------------------------------------------


class TestStaleRefsAnchorPolish:
    def test_anchor_match_case_insensitive(self, cli_runner, tmp_path, monkeypatch):
        """``#Setup`` (mixed case) must match header ``# Setup`` — GitHub semantics."""
        proj = tmp_path / "anchor_case"
        proj.mkdir()
        # Reference uses mixed case; header is plain.
        (proj / "README.md").write_text("[s](docs/install.md#Setup)\n")
        (proj / "docs").mkdir()
        (proj / "docs" / "install.md").write_text("# Setup\n\nrun pip\n")
        git_init(proj)
        monkeypatch.chdir(proj)
        result = invoke_cli(cli_runner, ["stale-refs"], cwd=proj, json_mode=True)
        data = parse_json_output(result, "stale-refs")
        # Target file resolves AND anchor matches case-insensitively.
        assert data["summary"]["missing_targets"] == 0

    def test_anchor_inside_code_fence_does_not_register(self, cli_runner, tmp_path, monkeypatch):
        """``# Heading`` inside a fenced code block must NOT count as an anchor.

        Otherwise tutorials embedding example markdown create phantom
        anchor targets that prose references appear to satisfy by
        accident — false negatives.
        """
        proj = tmp_path / "anchor_fence"
        proj.mkdir()
        # README references #real-header in tutorial.md.
        (proj / "README.md").write_text("[r](docs/tutorial.md#example-fence)\n")
        (proj / "docs").mkdir()
        # tutorial.md has only a fenced ``# Example fence`` line; no real
        # header by that text. So the anchor must fail to validate.
        (proj / "docs" / "tutorial.md").write_text("# Tutorial\n\n```markdown\n# Example fence\n```\n\nDone.\n")
        git_init(proj)
        monkeypatch.chdir(proj)
        result = invoke_cli(cli_runner, ["stale-refs"], cwd=proj, json_mode=True)
        data = parse_json_output(result, "stale-refs")
        targets = {t["target"] for t in data["targets"]}
        # The fenced "header" must NOT have created a valid anchor target.
        assert "docs/tutorial.md#example-fence" in targets

    def test_anchor_duplicate_header_suffixes(self, cli_runner, tmp_path, monkeypatch):
        """Two headers slugifying to the same value → GitHub appends ``-1``, ``-2``."""
        proj = tmp_path / "anchor_dup"
        proj.mkdir()
        # Reference uses the second-occurrence slug ``#setup-1``.
        (proj / "README.md").write_text("[s](docs/notes.md#setup-1)\n")
        (proj / "docs").mkdir()
        (proj / "docs" / "notes.md").write_text(
            "# Setup\n\nFirst section.\n\n## Setup\n\nSecond section, duplicate slug — should resolve as ``setup-1``.\n"
        )
        git_init(proj)
        monkeypatch.chdir(proj)
        result = invoke_cli(cli_runner, ["stale-refs"], cwd=proj, json_mode=True)
        data = parse_json_output(result, "stale-refs")
        # Both ``#setup`` and ``#setup-1`` should be valid anchors,
        # so the reference resolves and nothing is flagged.
        assert data["summary"]["missing_targets"] == 0


# ---------------------------------------------------------------------------
# v12.48 polish — SARIF anchor rule
# ---------------------------------------------------------------------------


class TestStaleRefsSarifAnchor:
    def test_sarif_emits_anchor_rule_with_anchor_message(self, cli_runner, tmp_path, monkeypatch):
        """Anchor findings get the ``stale-refs/anchor`` rule and a message
        that names the anchor and the target file (NOT the fake ``missing
        target`` phrasing used for path findings)."""
        import json

        proj = tmp_path / "sarif_anchor"
        proj.mkdir()
        (proj / "README.md").write_text("[s](docs/install.md#missing-anchor)\n")
        (proj / "docs").mkdir()
        (proj / "docs" / "install.md").write_text("# Other\n")
        git_init(proj)
        monkeypatch.chdir(proj)
        result = invoke_cli(cli_runner, ["--sarif", "stale-refs"], cwd=proj)
        assert result.exit_code == 0
        data = json.loads(result.output)
        runs = data["runs"]
        rules = {r["id"]: r for r in runs[0]["tool"]["driver"]["rules"]}
        assert "stale-refs/anchor" in rules
        # SARIF 2.1.0 stores shortDescription as ``{text: ...}`` (or as a
        # raw string — our :func:`to_sarif` builder accepts both shapes).
        short = rules["stale-refs/anchor"]["shortDescription"]
        short_text = short["text"] if isinstance(short, dict) else short
        assert "anchor" in short_text.lower()
        results = runs[0]["results"]
        anchor_results = [r for r in results if r["ruleId"] == "stale-refs/anchor"]
        assert len(anchor_results) == 1
        msg = anchor_results[0]["message"]["text"]
        assert "Anchor" in msg
        assert "missing-anchor" in msg
        assert "docs/install.md" in msg
        # The anchor message must NOT use the path-finding phrasing.
        assert "missing target" not in msg


# ---------------------------------------------------------------------------
# v12.48 polish — MCP wrapper exposes new flags (parameter contract)
# ---------------------------------------------------------------------------


class TestStaleRefsMcpWrapper:
    def test_mcp_wrapper_param_set(self):
        """The MCP wrapper must expose every v12.48 flag so agents can use them.

        We sanity-check the function signature rather than calling it
        through the MCP server harness — full integration is covered in
        the MCP tests.
        """
        import inspect

        from roam.mcp_server import roam_stale_refs

        params = inspect.signature(roam_stale_refs).parameters
        for required in (
            "limit",
            "rename_hint",
            "kind",
            "ignore",
            "ignore_target",
            "check_absolute_routes",
            "no_anchors",
            "diff",
            "sort_by",
            "fix",
            "by_file",
            "root",
        ):
            assert required in params, f"MCP wrapper missing param: {required}"


# ---------------------------------------------------------------------------
# v12.48 deeper polish — in-page anchors / atomic writes / msg clarity
# ---------------------------------------------------------------------------


class TestStaleRefsInPageAnchors:
    def test_in_page_anchor_missing_flagged(self, cli_runner, tmp_path, monkeypatch):
        """`[broken](#nonexistent)` in README.md must be flagged as stale.

        Pre-polish-3 this was silently accepted because the path resolver
        returns None for fragment-only URLs and the loop short-circuited
        before reaching the anchor validator.
        """
        proj = tmp_path / "in_page_miss"
        proj.mkdir()
        (proj / "README.md").write_text("[real](#setup)\n[broken](#nonexistent)\n# Setup\n")
        git_init(proj)
        monkeypatch.chdir(proj)
        result = invoke_cli(cli_runner, ["stale-refs"], cwd=proj, json_mode=True)
        data = parse_json_output(result, "stale-refs")
        targets = {t["target"] for t in data["targets"]}
        assert "README.md#nonexistent" in targets
        # The in-page reference to #setup IS valid — header exists.
        assert "README.md#setup" not in targets

    def test_in_page_anchor_finding_carries_anchor_kind(self, cli_runner, tmp_path, monkeypatch):
        """In-page findings emit kind=anchor so SARIF/JSON consumers can filter."""
        proj = tmp_path / "in_page_kind"
        proj.mkdir()
        (proj / "README.md").write_text("[broken](#nonexistent)\n# Setup\n")
        git_init(proj)
        monkeypatch.chdir(proj)
        result = invoke_cli(cli_runner, ["stale-refs"], cwd=proj, json_mode=True)
        data = parse_json_output(result, "stale-refs")
        first = data["targets"][0]
        assert first["sources"][0]["kind"] == "anchor"
        assert first["sources"][0]["anchor"] == "nonexistent"
        assert first["sources"][0]["anchor_target_file"] == "README.md"


class TestStaleRefsFixMessages:
    def test_fix_preview_zero_fixable_explains(self, cli_runner, tmp_path, monkeypatch):
        """When findings exist but none are HIGH-confidence, --fix preview must
        explain why nothing was rewritten — including the total finding count
        and a hint about ``--ignore``."""
        proj = tmp_path / "fix_zero"
        proj.mkdir()
        # Multiple basename matches → LOW confidence → not fixable.
        for d in ("a", "b", "c"):
            (proj / d).mkdir()
            (proj / d / "guide.md").write_text("hi\n")
        (proj / "README.md").write_text("[g](old/guide.md)\n")
        git_init(proj)
        monkeypatch.chdir(proj)
        result = invoke_cli(cli_runner, ["stale-refs", "--fix", "preview"], cwd=proj)
        assert result.exit_code == 0
        out = result.output
        assert "0 fixable" in out
        assert "1 total finding" in out
        assert "--ignore" in out


class TestStaleRefsAtomicWrites:
    def test_fix_apply_uses_atomic_write(self, cli_runner, tmp_path, monkeypatch):
        """`--fix apply` must not corrupt the file — temp+rename pattern.

        We can't easily simulate a crash, but we CAN verify the helper is
        invoked and the file content matches the post-fix expectation
        without intermediate states. Sanity check that no debris is
        left behind in the working directory either.
        """
        proj = tmp_path / "atomic"
        proj.mkdir()
        (proj / "docs").mkdir()
        (proj / "docs" / "guide.md").write_text("hi\n")
        readme = proj / "README.md"
        readme.write_text("[guide](docs/old/guide.md)\n")
        git_init(proj)
        monkeypatch.chdir(proj)
        result = invoke_cli(cli_runner, ["stale-refs", "--fix", "apply"], cwd=proj)
        assert result.exit_code == 0
        assert "docs/guide.md" in readme.read_text(encoding="utf-8")
        # No tempfile leftovers from atomic write helper.
        leftovers = [p.name for p in proj.iterdir() if p.name.startswith(".README.md.")]
        assert leftovers == []


# ---------------------------------------------------------------------------
# v12.48 polish-4 — URL-encoding + Unicode + --fix preserves uncommitted edits
# ---------------------------------------------------------------------------


class TestStaleRefsUrlEncoding:
    def test_percent_encoded_path_resolves(self, cli_runner, tmp_path, monkeypatch):
        """``[a](docs/file%20with%20spaces.md)`` must match the on-disk
        ``docs/file with spaces.md``."""
        proj = tmp_path / "url_encoded_path"
        proj.mkdir()
        (proj / "docs").mkdir()
        (proj / "docs" / "file with spaces.md").write_text("ok\n")
        (proj / "README.md").write_text("[a](docs/file%20with%20spaces.md)\n")
        git_init(proj)
        monkeypatch.chdir(proj)
        result = invoke_cli(cli_runner, ["stale-refs"], cwd=proj, json_mode=True)
        data = parse_json_output(result, "stale-refs")
        assert data["summary"]["missing_targets"] == 0

    def test_percent_encoded_anchor_resolves(self, cli_runner, tmp_path, monkeypatch):
        """``[c](docs/page.md#caf%C3%A9)`` must match header ``# Café``."""
        proj = tmp_path / "url_encoded_anchor"
        proj.mkdir()
        (proj / "docs").mkdir()
        (proj / "docs" / "page.md").write_text("# Café\n\nbody\n", encoding="utf-8")
        (proj / "README.md").write_text("[c](docs/page.md#caf%C3%A9)\n", encoding="utf-8")
        git_init(proj)
        monkeypatch.chdir(proj)
        result = invoke_cli(cli_runner, ["stale-refs"], cwd=proj, json_mode=True)
        data = parse_json_output(result, "stale-refs")
        assert data["summary"]["missing_targets"] == 0


class TestStaleRefsUnicodeSlugify:
    def test_unicode_letter_preserved_in_slug(self):
        """``# Über`` slugifies to ``über``, NOT ``ber`` or empty.

        Pre-fix the regex dropped non-ASCII letters and references to
        ``#über`` always failed against headers like ``# Über``.
        """
        from roam.commands.stale_refs_anchors import slugify

        assert slugify("Über") == "über"
        assert slugify("café") == "café"
        # Emoji + punctuation still drop; trailing dash from removed
        # whitespace+emoji is stripped by the final ``.strip("-")``.
        assert slugify("Setup 🎉") == "setup"
        # CJK preserved.
        assert slugify("日本語") == "日本語"

    def test_unicode_anchor_validates(self, cli_runner, tmp_path, monkeypatch):
        """``[u](docs/x.md#über)`` resolves against header ``# Über``."""
        proj = tmp_path / "unicode_anchor"
        proj.mkdir()
        (proj / "docs").mkdir()
        (proj / "docs" / "x.md").write_text("# Über\n\nbody\n", encoding="utf-8")
        (proj / "README.md").write_text("[u](docs/x.md#über)\n", encoding="utf-8")
        git_init(proj)
        monkeypatch.chdir(proj)
        result = invoke_cli(cli_runner, ["stale-refs"], cwd=proj, json_mode=True)
        data = parse_json_output(result, "stale-refs")
        assert data["summary"]["missing_targets"] == 0

    def test_cjk_anchor_validates(self, cli_runner, tmp_path, monkeypatch):
        """``[j](docs/x.md#日本語)`` resolves against ``# 日本語``."""
        proj = tmp_path / "cjk_anchor"
        proj.mkdir()
        (proj / "docs").mkdir()
        (proj / "docs" / "x.md").write_text("# 日本語\n\nbody\n", encoding="utf-8")
        (proj / "README.md").write_text("[j](docs/x.md#日本語)\n", encoding="utf-8")
        git_init(proj)
        monkeypatch.chdir(proj)
        result = invoke_cli(cli_runner, ["stale-refs"], cwd=proj, json_mode=True)
        data = parse_json_output(result, "stale-refs")
        assert data["summary"]["missing_targets"] == 0


class TestStaleRefsFixOnDirtyFile:
    def test_fix_apply_preserves_unrelated_uncommitted_edits(self, cli_runner, tmp_path, monkeypatch):
        """``--fix apply`` must touch only the matched substring; other
        uncommitted edits in the same file are preserved."""

        proj = tmp_path / "fix_dirty"
        proj.mkdir()
        (proj / "docs").mkdir()
        (proj / "docs" / "guide.md").write_text("hi\n")
        readme = proj / "README.md"
        readme.write_text("[g](docs/old/guide.md)\n")
        git_init(proj)
        # Add an UNCOMMITTED change to README that is NOT a stale ref.
        readme.write_text("[g](docs/old/guide.md)\n\n## My WIP section\n")
        monkeypatch.chdir(proj)
        result = invoke_cli(cli_runner, ["stale-refs", "--fix", "apply"], cwd=proj)
        assert result.exit_code == 0
        new_content = readme.read_text(encoding="utf-8")
        # Stale path was rewritten.
        assert "docs/guide.md" in new_content
        assert "docs/old/guide.md" not in new_content
        # Unrelated WIP content remains intact.
        assert "## My WIP section" in new_content

    def test_fix_apply_skips_when_url_no_longer_verbatim(self, cli_runner, tmp_path, monkeypatch):
        """If the user has already rewritten the URL between scans, ``--fix
        apply`` must NOT touch the file (raw URL no longer matches)."""
        proj = tmp_path / "fix_already_done"
        proj.mkdir()
        (proj / "docs").mkdir()
        (proj / "docs" / "guide.md").write_text("hi\n")
        readme = proj / "README.md"
        # Initially the README has an out-of-date raw URL the scan will see.
        readme.write_text("[g](docs/old/guide.md)\n")
        git_init(proj)
        # Now the user manually fixed it BEFORE we ran apply — but our
        # last scan still has the old raw URL in the edit list. The
        # _apply_fix_to_text helper looks for the literal raw URL; if it
        # isn't there any more, applied count == 0 and the file is
        # untouched. This test pins that behaviour.
        readme.write_text("[g](docs/guide.md)\n")
        monkeypatch.chdir(proj)
        invoke_cli(cli_runner, ["stale-refs", "--fix", "apply"], cwd=proj)
        # Final content unchanged from user's manual fix.
        assert readme.read_text(encoding="utf-8") == "[g](docs/guide.md)\n"


# ---------------------------------------------------------------------------
# v12.48 polish-5 — agent-ergonomic JSON aggregations + next_steps
# ---------------------------------------------------------------------------


class TestStaleRefsAggregations:
    def test_summary_includes_fixable_count(self, cli_runner, tmp_path, monkeypatch):
        """``summary.fixable_count`` reports how many HIGH-confidence hints
        ``--fix apply`` would act on. Lets agents/CI decide whether to call
        --fix at all."""
        proj = tmp_path / "fixable_count"
        proj.mkdir()
        # Two findings: one HIGH-confidence (shared dir prefix), one LOW
        # (multiple basename matches).
        (proj / "docs").mkdir()
        (proj / "docs" / "guide.md").write_text("hi\n")
        for d in ("a", "b"):
            (proj / d).mkdir()
            (proj / d / "shared.md").write_text("hi\n")
        (proj / "README.md").write_text("[high](docs/old/guide.md)\n[low](missing/shared.md)\n")
        git_init(proj)
        monkeypatch.chdir(proj)
        result = invoke_cli(cli_runner, ["stale-refs"], cwd=proj, json_mode=True)
        data = parse_json_output(result, "stale-refs")
        assert data["summary"]["fixable_count"] == 1

    def test_summary_includes_by_kind(self, cli_runner, tmp_path, monkeypatch):
        """``summary.by_kind`` breaks down findings by reference kind
        (md_inline, html_attr, backtick, anchor)."""
        proj = tmp_path / "by_kind"
        proj.mkdir()
        (proj / "README.md").write_text("[a](docs/missing.md)\n`docs/gone.md`\n# Setup\n")
        (proj / "docs").mkdir()
        (proj / "docs" / "live.md").write_text("[anchor](#missing-target)\n# OK\n")
        git_init(proj)
        monkeypatch.chdir(proj)
        result = invoke_cli(cli_runner, ["stale-refs"], cwd=proj, json_mode=True)
        data = parse_json_output(result, "stale-refs")
        by_kind = data["summary"]["by_kind"]
        assert "md_inline" in by_kind
        assert by_kind["md_inline"] >= 1
        # Expect at least one anchor finding (in-page broken anchor).
        assert by_kind.get("anchor", 0) >= 1

    def test_summary_includes_by_confidence(self, cli_runner, tmp_path, monkeypatch):
        """``summary.by_confidence`` reports the count per HIGH/MEDIUM/LOW/NONE
        band. Agents can prioritise review by confidence bucket."""
        proj = tmp_path / "by_conf"
        proj.mkdir()
        (proj / "docs").mkdir()
        (proj / "docs" / "guide.md").write_text("hi\n")
        # HIGH: shared dir prefix; NONE: no basename match anywhere; MEDIUM
        # would need a single match outside the source dir, etc.
        (proj / "README.md").write_text("[high](docs/old/guide.md)\n[none](nonexistent/random-thing.md)\n")
        git_init(proj)
        monkeypatch.chdir(proj)
        result = invoke_cli(cli_runner, ["stale-refs"], cwd=proj, json_mode=True)
        data = parse_json_output(result, "stale-refs")
        by_conf = data["summary"]["by_confidence"]
        # Both HIGH and NONE should appear; total must equal target count.
        assert by_conf.get("HIGH", 0) >= 1
        assert by_conf.get("NONE", 0) >= 1
        assert sum(by_conf.values()) == data["summary"]["missing_targets"]


class TestStaleRefsNextSteps:
    def test_next_steps_present_in_json(self, cli_runner, tmp_path, monkeypatch):
        """``summary.next_steps`` is a non-empty list when findings exist."""
        proj = tmp_path / "next_steps_exists"
        proj.mkdir()
        (proj / "README.md").write_text("[x](missing.md)\n")
        git_init(proj)
        monkeypatch.chdir(proj)
        result = invoke_cli(cli_runner, ["stale-refs"], cwd=proj, json_mode=True)
        data = parse_json_output(result, "stale-refs")
        assert isinstance(data["summary"]["next_steps"], list)
        assert len(data["summary"]["next_steps"]) >= 1

    def test_next_steps_recommends_fix_when_fixable(self, cli_runner, tmp_path, monkeypatch):
        """When fixable_count > 0, next_steps should mention --fix preview."""
        proj = tmp_path / "next_fix"
        proj.mkdir()
        (proj / "docs").mkdir()
        (proj / "docs" / "guide.md").write_text("hi\n")
        (proj / "README.md").write_text("[g](docs/old/guide.md)\n")
        git_init(proj)
        monkeypatch.chdir(proj)
        result = invoke_cli(cli_runner, ["stale-refs"], cwd=proj, json_mode=True)
        data = parse_json_output(result, "stale-refs")
        steps = data["summary"]["next_steps"]
        assert any("--fix preview" in s for s in steps)

    def test_next_steps_calls_out_anchor_findings(self, cli_runner, tmp_path, monkeypatch):
        """When anchor_findings > 0, next_steps should explicitly mention them."""
        proj = tmp_path / "next_anchor"
        proj.mkdir()
        (proj / "README.md").write_text("[broken](#nonexistent)\n# Setup\n")
        git_init(proj)
        monkeypatch.chdir(proj)
        result = invoke_cli(cli_runner, ["stale-refs"], cwd=proj, json_mode=True)
        data = parse_json_output(result, "stale-refs")
        steps = data["summary"]["next_steps"]
        assert any("anchor" in s.lower() for s in steps)

    def test_next_steps_recommends_gate_when_clean(self, cli_runner, tmp_path, monkeypatch):
        """Clean repo → next_steps should suggest setting up the CI gate."""
        proj = tmp_path / "next_clean"
        proj.mkdir()
        (proj / "README.md").write_text("# Hello\n")
        git_init(proj)
        monkeypatch.chdir(proj)
        result = invoke_cli(cli_runner, ["stale-refs"], cwd=proj, json_mode=True)
        data = parse_json_output(result, "stale-refs")
        steps = data["summary"]["next_steps"]
        assert any("--gate" in s for s in steps)

    def test_next_steps_rendered_in_text_output(self, cli_runner, tmp_path, monkeypatch):
        """Text mode appends a NEXT STEPS section when there are suggestions."""
        proj = tmp_path / "next_text"
        proj.mkdir()
        (proj / "README.md").write_text("[x](missing.md)\n")
        git_init(proj)
        monkeypatch.chdir(proj)
        result = invoke_cli(cli_runner, ["stale-refs"], cwd=proj)
        assert "NEXT STEPS:" in result.output


# ---------------------------------------------------------------------------
# v12.48 polish-6 — smart "did you mean" anchor hints
# ---------------------------------------------------------------------------


class TestStaleRefsAnchorDidYouMean:
    def test_typo_class_anchor_gets_did_you_mean_hint(self, cli_runner, tmp_path, monkeypatch):
        """Pluralisation drift: ``#mcp-server`` → ``#mcp-servers`` should
        score very high (~0.95) and surface as a HIGH-confidence hint."""
        proj = tmp_path / "anchor_didyoumean_typo"
        proj.mkdir()
        (proj / "docs").mkdir()
        (proj / "docs" / "x.md").write_text("# MCP Servers\n\nbody\n")
        (proj / "README.md").write_text("[s](docs/x.md#mcp-server)\n")
        git_init(proj)
        monkeypatch.chdir(proj)
        result = invoke_cli(cli_runner, ["stale-refs"], cwd=proj, json_mode=True)
        data = parse_json_output(result, "stale-refs")
        assert data["summary"]["anchor_findings"] == 1
        first = data["targets"][0]
        hint = first.get("hint")
        assert hint is not None
        assert hint["target"] == "docs/x.md#mcp-servers"
        assert hint["source"] == "anchor-similarity"
        assert hint["confidence"] == "HIGH"

    def test_word_reorder_anchor_uses_token_jaccard(self, cli_runner, tmp_path, monkeypatch):
        """Word-reorder drift: ``#docker-setup`` → ``#setup-with-docker``.

        Pure character-ratio scores this poorly; the token-Jaccard
        signal pushes it above the threshold. Both tokens (``docker``,
        ``setup``) match.
        """
        proj = tmp_path / "anchor_didyoumean_reorder"
        proj.mkdir()
        (proj / "docs").mkdir()
        (proj / "docs" / "x.md").write_text("# Setup with Docker\n\nbody\n")
        (proj / "README.md").write_text("[s](docs/x.md#docker-setup)\n")
        git_init(proj)
        monkeypatch.chdir(proj)
        result = invoke_cli(cli_runner, ["stale-refs"], cwd=proj, json_mode=True)
        data = parse_json_output(result, "stale-refs")
        first = data["targets"][0]
        hint = first.get("hint")
        assert hint is not None
        assert hint["target"] == "docs/x.md#setup-with-docker"
        assert hint["source"] == "anchor-similarity"

    def test_unrelated_anchor_no_hint(self, cli_runner, tmp_path, monkeypatch):
        """When no candidate clears the threshold, no hint is surfaced
        — better silent than a misleading suggestion."""
        proj = tmp_path / "anchor_no_hint"
        proj.mkdir()
        (proj / "docs").mkdir()
        # Target file has totally unrelated headers — none match #foo.
        (proj / "docs" / "x.md").write_text("# Architecture\n\n# Performance\n")
        (proj / "README.md").write_text("[bad](docs/x.md#foo)\n")
        git_init(proj)
        monkeypatch.chdir(proj)
        result = invoke_cli(cli_runner, ["stale-refs"], cwd=proj, json_mode=True)
        data = parse_json_output(result, "stale-refs")
        first = data["targets"][0]
        assert "hint" not in first

    def test_in_page_anchor_hint(self, cli_runner, tmp_path, monkeypatch):
        """In-page anchor findings (URL is just ``#fragment``) also get
        did-you-mean hints; the suggested rewrite is ``file#anchor``
        relative to the source file."""
        proj = tmp_path / "in_page_didyoumean"
        proj.mkdir()
        (proj / "README.md").write_text("[bad](#mcp-server)\n# MCP Servers\n")
        git_init(proj)
        monkeypatch.chdir(proj)
        result = invoke_cli(cli_runner, ["stale-refs"], cwd=proj, json_mode=True)
        data = parse_json_output(result, "stale-refs")
        first = data["targets"][0]
        hint = first.get("hint")
        assert hint is not None
        assert hint["target"] == "README.md#mcp-servers"

    def test_closest_anchor_helper_empty_candidates(self):
        """The ranking helper short-circuits cleanly on empty input."""
        from roam.commands.cmd_stale_refs import _closest_anchor_hint

        assert _closest_anchor_hint("anything", set()) is None


class TestStaleRefsMcpSchema:
    def test_stale_refs_schema_declares_new_fields(self):
        """The MCP output schema describes the v12.48 summary fields so
        clients can validate envelopes."""
        import json

        from roam.mcp_server import _SCHEMA_STALE_REFS

        assert isinstance(_SCHEMA_STALE_REFS, dict)
        serialised = json.dumps(_SCHEMA_STALE_REFS)
        for required_field in (
            "fixable_count",
            "by_kind",
            "by_confidence",
            "next_steps",
            "anchor_findings",
        ):
            assert required_field in serialised, f"_SCHEMA_STALE_REFS missing field: {required_field}"


# ---------------------------------------------------------------------------
# v12.48 polish-7 — auto-fix anchor hints + preserve fragment in path fix
# ---------------------------------------------------------------------------


class TestStaleRefsAutoFixAnchor:
    def test_fix_apply_rewrites_high_confidence_anchor_hint(self, cli_runner, tmp_path, monkeypatch):
        """A HIGH-confidence anchor-similarity hint (``#mcp-server`` →
        ``#mcp-servers``) must be auto-fixable via ``--fix apply``.

        Pre-polish-7 the rewrite was unconditionally skipped because
        anchor sources had no path-rename to substitute. The new logic
        substitutes the fragment portion of the URL only.
        """
        proj = tmp_path / "fix_anchor_high"
        proj.mkdir()
        (proj / "docs").mkdir()
        (proj / "docs" / "x.md").write_text("# MCP Servers\n\nbody\n", encoding="utf-8")
        readme = proj / "README.md"
        readme.write_text("[s](docs/x.md#mcp-server)\n", encoding="utf-8")
        git_init(proj)
        monkeypatch.chdir(proj)
        result = invoke_cli(cli_runner, ["stale-refs", "--fix", "apply"], cwd=proj)
        assert result.exit_code == 0
        assert readme.read_text(encoding="utf-8") == "[s](docs/x.md#mcp-servers)\n"

    def test_fix_apply_preserves_path_prefix_on_anchor_rewrite(self, cli_runner, tmp_path, monkeypatch):
        """The rewrite must preserve everything BEFORE the ``#`` —
        substituting only the fragment, not the whole URL."""
        proj = tmp_path / "fix_anchor_preserve"
        proj.mkdir()
        (proj / "docs").mkdir()
        (proj / "docs" / "x.md").write_text("# MCP Servers\n", encoding="utf-8")
        readme = proj / "README.md"
        readme.write_text("[a](docs/x.md#mcp-server) and [b](docs/x.md#mcp-server)\n", encoding="utf-8")
        git_init(proj)
        monkeypatch.chdir(proj)
        invoke_cli(cli_runner, ["stale-refs", "--fix", "apply"], cwd=proj)
        new_content = readme.read_text(encoding="utf-8")
        # Both occurrences rewritten, path prefix intact in both.
        assert "docs/x.md#mcp-servers" in new_content
        assert "docs/x.md#mcp-server)" not in new_content  # old form gone
        # File-prefix is preserved — we did NOT replace with bare anchor.
        assert "(#mcp-servers)" not in new_content

    def test_fix_apply_in_page_anchor_rewrite(self, cli_runner, tmp_path, monkeypatch):
        """In-page anchor refs (URL is just ``#fragment``) must be
        rewritten as bare anchors, NOT the full file#anchor form."""
        proj = tmp_path / "fix_in_page_anchor"
        proj.mkdir()
        readme = proj / "README.md"
        readme.write_text("[bad](#mcp-server)\n# MCP Servers\n", encoding="utf-8")
        git_init(proj)
        monkeypatch.chdir(proj)
        invoke_cli(cli_runner, ["stale-refs", "--fix", "apply"], cwd=proj)
        new_content = readme.read_text(encoding="utf-8")
        # Bare anchor preserved (not rewritten as README.md#mcp-servers).
        assert "[bad](#mcp-servers)" in new_content
        # The file-prefix form must NOT have been written.
        assert "[bad](README.md" not in new_content


class TestStaleRefsAutoFixFragmentPreserved:
    def test_path_fix_preserves_fragment(self, cli_runner, tmp_path, monkeypatch):
        """``[s](old/foo.md#section)`` rewriting to a new path must
        preserve the ``#section`` fragment.

        Pre-polish-7 the fragment was silently dropped because
        ``replacement = hint["target"]`` returned a path-only string.
        """
        proj = tmp_path / "fix_path_fragment"
        proj.mkdir()
        (proj / "docs").mkdir()
        (proj / "docs" / "guide.md").write_text("# Section\n\nbody\n", encoding="utf-8")
        readme = proj / "README.md"
        # Path is broken (docs/old/guide.md doesn't exist) but fragment
        # is valid against the eventual rename target.
        readme.write_text("[s](docs/old/guide.md#section)\n", encoding="utf-8")
        git_init(proj)
        monkeypatch.chdir(proj)
        invoke_cli(cli_runner, ["stale-refs", "--fix", "apply"], cwd=proj)
        new_content = readme.read_text(encoding="utf-8")
        # Fragment preserved alongside the corrected path.
        assert "docs/guide.md#section" in new_content
        assert "docs/old/guide.md" not in new_content


class TestStaleRefsHelpUri:
    def test_sarif_helpuri_points_to_correct_org(self, cli_runner, tmp_path, monkeypatch):
        """SARIF helpUri for stale-refs rules must point at Cranot/roam-code,
        not the historical AbanteAI/roam-code reference."""
        import json

        proj = tmp_path / "helpuri"
        proj.mkdir()
        (proj / "README.md").write_text("[x](missing.md)\n")
        git_init(proj)
        monkeypatch.chdir(proj)
        result = invoke_cli(cli_runner, ["--sarif", "stale-refs"], cwd=proj)
        assert result.exit_code == 0
        data = json.loads(result.output)
        rules = data["runs"][0]["tool"]["driver"]["rules"]
        for rule in rules:
            assert "Cranot/roam-code" in rule["helpUri"], rule
            assert "AbanteAI" not in rule["helpUri"], rule


# ---------------------------------------------------------------------------
# v12.49 — Phase 1: --with-candidates + LLM-enrichment helper
# ---------------------------------------------------------------------------


class TestStaleRefsWithCandidates:
    def test_with_candidates_emits_repo_paths_sample(self, cli_runner, tmp_path, monkeypatch):
        """--with-candidates surfaces repo_paths_sample under summary.

        Used by the MCP wrapper's LLM enricher to give the calling LLM
        context on what paths the repo actually contains.
        """
        proj = tmp_path / "with_candidates"
        proj.mkdir()
        (proj / "docs").mkdir()
        (proj / "docs" / "intro.md").write_text("hi\n")
        (proj / "docs" / "guide.md").write_text("hi\n")
        (proj / "README.md").write_text("[m](missing.md)\n")
        git_init(proj)
        monkeypatch.chdir(proj)
        result = invoke_cli(cli_runner, ["stale-refs", "--with-candidates"], cwd=proj, json_mode=True)
        data = parse_json_output(result, "stale-refs")
        sample = data["summary"].get("repo_paths_sample")
        assert isinstance(sample, list)
        # Both prose files should appear in the sample.
        assert "docs/intro.md" in sample
        assert "docs/guide.md" in sample

    def test_without_flag_no_repo_paths_sample(self, cli_runner, tmp_path, monkeypatch):
        proj = tmp_path / "no_candidates"
        proj.mkdir()
        (proj / "README.md").write_text("[m](missing.md)\n")
        git_init(proj)
        monkeypatch.chdir(proj)
        result = invoke_cli(cli_runner, ["stale-refs"], cwd=proj, json_mode=True)
        data = parse_json_output(result, "stale-refs")
        assert "repo_paths_sample" not in data["summary"]


class TestLlmEnrichParser:
    def test_strips_code_fences(self):
        from roam.mcp_server import _parse_llm_enrich_response

        # Legacy single-string shape — accepted for back-compat, normalised
        # to a single-element list so callers see one ranked candidate.
        assert _parse_llm_enrich_response('```json\n{"a": "b"}\n```') == {"a": ["b"]}

    def test_handles_leading_prose(self):
        from roam.mcp_server import _parse_llm_enrich_response

        assert _parse_llm_enrich_response('Sure: {"a": "b"}') == {"a": ["b"]}

    def test_returns_empty_on_garbage(self):
        from roam.mcp_server import _parse_llm_enrich_response

        assert _parse_llm_enrich_response("nope") == {}
        assert _parse_llm_enrich_response("") == {}

    def test_preserves_null_values(self):
        from roam.mcp_server import _parse_llm_enrich_response

        # null → empty list (the LLM had no plausible candidate).
        assert _parse_llm_enrich_response('{"a": null}') == {"a": []}

    def test_accepts_ranked_candidate_lists(self):
        from roam.mcp_server import _parse_llm_enrich_response

        # New ranked shape — what the prompt actually asks for now.
        result = _parse_llm_enrich_response('{"a": ["x", "y", "z"]}')
        assert result == {"a": ["x", "y", "z"]}

    def test_caps_candidate_list_at_three(self):
        from roam.mcp_server import _parse_llm_enrich_response

        # Anything beyond the third candidate is noise; the parser caps
        # the list defensively so a chatty model can't bloat envelopes.
        result = _parse_llm_enrich_response('{"a": ["1", "2", "3", "4", "5"]}')
        assert result == {"a": ["1", "2", "3"]}

    def test_drops_non_string_list_entries(self):
        from roam.mcp_server import _parse_llm_enrich_response

        # Entries that aren't strings are silently dropped from the
        # ranked list; numbers etc. would just hallucinate a path.
        result = _parse_llm_enrich_response('{"a": ["x", 1, "y"]}')
        assert result == {"a": ["x", "y"]}

    def test_drops_non_string_values(self):
        from roam.mcp_server import _parse_llm_enrich_response

        # Number values still skipped — only str / list / null are accepted.
        # (List values containing non-strings are handled by the test above.)
        assert _parse_llm_enrich_response('{"a": 123}') == {}


# ---------------------------------------------------------------------------
# v12.49 — Phase 2: --watch mode helpers
# ---------------------------------------------------------------------------


class TestStaleRefsWatchHelpers:
    def test_finding_set_round_trip(self):
        from roam.commands.cmd_stale_refs import _scan_finding_set

        stale_by_target = {
            "docs/foo.md": [
                {"file": "README.md", "line": 1, "kind": "md_inline", "raw": "docs/foo.md"},
                {"file": "OTHER.md", "line": 5, "kind": "md_inline", "raw": "docs/foo.md"},
            ],
            "docs/bar.md": [
                {"file": "README.md", "line": 2, "kind": "md_inline", "raw": "docs/bar.md"},
            ],
        }
        s = _scan_finding_set(stale_by_target)
        assert ("docs/foo.md", "README.md", 1, "md_inline") in s
        assert ("docs/bar.md", "README.md", 2, "md_inline") in s
        assert len(s) == 3

    def test_collect_mtimes_returns_dict(self, tmp_path):
        from roam.commands.cmd_stale_refs import _collect_mtimes

        proj = tmp_path / "mtimes"
        proj.mkdir()
        (proj / "README.md").write_text("hi")
        # No git init — _collect_mtimes uses discover_files which falls
        # back to os.walk.
        result = _collect_mtimes(proj, include_excluded=False)
        # README.md should appear with a positive mtime.
        assert "README.md" in result
        assert result["README.md"] > 0

    def test_watch_rejects_json_mode(self, cli_runner, tmp_path, monkeypatch):
        """--watch + --json is a UsageError (streaming JSON makes no sense)."""
        proj = tmp_path / "watch_json"
        proj.mkdir()
        (proj / "README.md").write_text("[x](missing.md)\n")
        git_init(proj)
        monkeypatch.chdir(proj)
        result = invoke_cli(cli_runner, ["stale-refs", "--watch"], cwd=proj, json_mode=True)
        # Click UsageError surfaces as exit code 2.
        assert result.exit_code == 2
        assert "watch" in result.output.lower() or "json" in result.output.lower()


# ---------------------------------------------------------------------------
# v12.49 — Phase 3: persistent baseline
# ---------------------------------------------------------------------------


class TestStaleRefsBaseline:
    def test_baseline_save_creates_deterministic_file(self, cli_runner, tmp_path, monkeypatch):
        """`--baseline-save FILE` writes a sorted JSON snapshot."""
        import json

        proj = tmp_path / "baseline_save"
        proj.mkdir()
        (proj / "README.md").write_text("[a](missing-1.md)\n[b](missing-2.md)\n")
        git_init(proj)
        baseline_path = str(proj / "baseline.json")
        monkeypatch.chdir(proj)
        result = invoke_cli(cli_runner, ["stale-refs", "--baseline-save", baseline_path], cwd=proj)
        assert result.exit_code == 0
        with open(baseline_path) as fh:
            data = json.load(fh)
        assert data["schema"] == "roam-stale-refs-baseline-v2"
        assert data["finding_count"] == 2
        # The records list is sorted.
        assert data["findings"] == sorted(data["findings"])

    def test_baseline_from_filters_pre_existing(self, cli_runner, tmp_path, monkeypatch):
        """`--baseline-from FILE` filters to only NEW findings."""
        proj = tmp_path / "baseline_from"
        proj.mkdir()
        (proj / "README.md").write_text("[old](docs/old.md)\n")
        git_init(proj)
        baseline_path = str(proj / "baseline.json")
        monkeypatch.chdir(proj)
        # Save baseline of current findings.
        invoke_cli(cli_runner, ["stale-refs", "--baseline-save", baseline_path], cwd=proj)
        # Add a NEW finding.
        (proj / "README.md").write_text("[old](docs/old.md)\n[new](docs/new-broken.md)\n")
        result = invoke_cli(
            cli_runner,
            ["stale-refs", "--baseline-from", baseline_path],
            cwd=proj,
            json_mode=True,
        )
        data = parse_json_output(result, "stale-refs")
        targets = {t["target"] for t in data["targets"]}
        # Only the new finding should appear.
        assert "docs/new-broken.md" in targets
        assert "docs/old.md" not in targets
        # Summary should report baseline metadata.
        assert data["summary"]["baseline_size"] >= 1
        assert data["summary"]["baseline_filtered_out"] >= 1

    def test_baseline_gate_only_fails_on_new_findings(self, cli_runner, tmp_path, monkeypatch):
        """`--baseline-from + --gate` exits 0 when only baselined findings remain."""
        proj = tmp_path / "baseline_gate"
        proj.mkdir()
        (proj / "README.md").write_text("[old](docs/old.md)\n")
        git_init(proj)
        baseline_path = str(proj / "baseline.json")
        monkeypatch.chdir(proj)
        invoke_cli(cli_runner, ["stale-refs", "--baseline-save", baseline_path], cwd=proj)
        # Re-run with the same content + gate; no NEW findings → exit 0.
        result = invoke_cli(
            cli_runner,
            ["stale-refs", "--baseline-from", baseline_path, "--gate"],
            cwd=proj,
        )
        assert result.exit_code == 0


# ---------------------------------------------------------------------------
# v12.49 — Phase 4: --check-external (HTTP link checker)
# ---------------------------------------------------------------------------


class TestStaleRefsCheckExternal:
    def test_extract_external_urls_finds_md_link(self):
        from roam.commands.cmd_stale_refs import _extract_external_urls

        content = "See [docs](https://example.com/x).\nAnd <https://auto.example>.\n"
        urls = _extract_external_urls(content)
        # md_inline + autolink → 2 URLs over 2 lines.
        assert any(u == "https://example.com/x" for _, u in urls)
        assert any(u == "https://auto.example" for _, u in urls)

    def test_extract_external_urls_finds_html_attr(self):
        from roam.commands.cmd_stale_refs import _extract_external_urls

        content = '<a href="https://example.com">x</a>\n<img src="http://img.example/i.png">\n'
        urls = [u for _, u in _extract_external_urls(content)]
        assert "https://example.com" in urls
        assert "http://img.example/i.png" in urls

    def test_is_external_finding_classifies_correctly(self):
        from roam.commands.cmd_stale_refs import _is_external_finding

        # Live = no finding.
        assert _is_external_finding(200) is False
        assert _is_external_finding(301) is False
        assert _is_external_finding(204) is False
        # Broken = finding.
        assert _is_external_finding(404) is True
        assert _is_external_finding(500) is True
        assert _is_external_finding(None) is True


# ---------------------------------------------------------------------------
# v12.49 — Phase 5: LSP server
# ---------------------------------------------------------------------------


class TestStaleRefsLsp:
    def test_lsp_command_registered(self):
        """`roam lsp` resolves through the lazy command map."""
        from roam.commands.cmd_lsp import lsp

        assert lsp.name == "lsp"

    def test_lsp_initialize_response(self, tmp_path, monkeypatch):
        """The server responds to `initialize` with capabilities."""
        import json
        import subprocess
        import sys

        proj = tmp_path / "lsp_init"
        proj.mkdir()
        (proj / "README.md").write_text("hi\n")
        git_init(proj)
        monkeypatch.chdir(proj)

        # Build a single initialize message and pipe through the CLI.
        body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
        framed = f"Content-Length: {len(body)}\r\n\r\n{body}"

        result = subprocess.run(
            [sys.executable, "-m", "roam", "lsp", "--once"],
            input=framed.encode("utf-8"),
            capture_output=True,
            cwd=str(proj),
            timeout=15,
        )
        assert result.returncode == 0, result.stderr.decode("utf-8", errors="replace")
        # Response is also LSP-framed.
        out = result.stdout.decode("utf-8", errors="replace")
        assert "Content-Length:" in out
        # Find the JSON body and parse capabilities.
        body_start = out.find("\r\n\r\n")
        assert body_start > 0
        response = json.loads(out[body_start + 4 :].strip())
        assert response["id"] == 1
        caps = response["result"]["capabilities"]
        assert "textDocumentSync" in caps
        assert response["result"]["serverInfo"]["name"] == "roam-stale-refs-lsp"

    def test_uri_to_path_round_trip(self, tmp_path):
        from roam.commands.cmd_lsp import _uri_to_path

        # Round-trip via Path.as_uri() so we get a platform-correct file URI.
        proj = tmp_path / "uri"
        proj.mkdir()
        target = proj / "docs" / "x.md"
        target.parent.mkdir()
        target.write_text("hi")
        uri = target.as_uri()
        rel = _uri_to_path(uri, proj)
        assert rel == "docs/x.md"

    def test_uri_to_path_external_returns_none(self, tmp_path):
        from roam.commands.cmd_lsp import _uri_to_path

        # A URI outside the project root resolves to None.
        outside = tmp_path / "outside_root"
        outside.mkdir()
        rel = _uri_to_path(outside.as_uri(), tmp_path / "nonexistent")
        assert rel is None


# ---------------------------------------------------------------------------
# v12.49 round-1 hardening — defensive correctness across all 5 phases
# ---------------------------------------------------------------------------


class TestStaleRefsDomainThrottle:
    def test_domain_of_extracts_netloc(self):
        from roam.commands.cmd_stale_refs import _domain_of

        assert _domain_of("https://example.com/foo") == "example.com"
        assert _domain_of("http://example.com:8080/x") == "example.com:8080"
        assert _domain_of("https://Sub.Example.COM/x") == "sub.example.com"
        assert _domain_of("not-a-url") == ""
        assert _domain_of("http://[::1") == ""

    def test_domain_of_only_swallows_parse_value_errors(self, monkeypatch):
        import roam.commands.cmd_stale_refs as stale_refs

        def _raise_type_error(_url):
            raise TypeError("parser bug")

        monkeypatch.setattr(stale_refs.urllib.parse, "urlparse", _raise_type_error)
        with pytest.raises(TypeError, match="parser bug"):
            stale_refs._domain_of("https://example.com/foo")

    def test_per_domain_concurrency_constant(self):
        from roam.commands.cmd_stale_refs import _PER_DOMAIN_CONCURRENCY

        assert 1 <= _PER_DOMAIN_CONCURRENCY <= 8


class TestStaleRefsLlmHintValidation:
    """Round-1 hardening: the LLM enricher must reject hallucinated paths."""

    def test_basename_resolution_helper_logic(self):
        repo_paths = ["docs/intro.md", "tutorials/setup.md"]
        basenames = {p.rsplit("/", 1)[-1] for p in repo_paths}
        suggested = "intro.md"
        assert suggested in basenames
        matches = [p for p in repo_paths if p.endswith("/" + suggested) or p == suggested]
        assert matches == ["docs/intro.md"]

    def test_ambiguous_basename_picks_shortest(self):
        repo_paths = ["very/deep/nested/intro.md", "intro.md"]
        matches = [p for p in repo_paths if p.endswith("/intro.md") or p == "intro.md"]
        assert min(matches, key=len) == "intro.md"

    def test_hallucinated_path_outside_repo(self):
        repo_paths = ["docs/intro.md"]
        valid_paths = set(repo_paths)
        valid_basenames = {p.rsplit("/", 1)[-1] for p in repo_paths}
        assert "phantom/path.md" not in valid_paths
        assert "phantom-path.md" not in valid_basenames


class TestStaleRefsLspIntegration:
    """End-to-end LSP handshake. Spawns the server as a subprocess."""

    def test_full_handshake_emits_diagnostics(self, tmp_path, monkeypatch):
        import json
        import subprocess
        import sys

        proj = tmp_path / "lsp_full"
        proj.mkdir()
        (proj / "README.md").write_text("# Hi\n\n[broken](docs/missing.md)\n")
        git_init(proj)
        monkeypatch.chdir(proj)

        readme_path = proj / "README.md"
        uri = readme_path.as_uri()

        messages = [
            {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
            {"jsonrpc": "2.0", "method": "initialized", "params": {}},
            {
                "jsonrpc": "2.0",
                "method": "textDocument/didOpen",
                "params": {
                    "textDocument": {
                        "uri": uri,
                        "languageId": "markdown",
                        "version": 1,
                        "text": readme_path.read_text(encoding="utf-8"),
                    }
                },
            },
            {"jsonrpc": "2.0", "id": 2, "method": "shutdown"},
            {"jsonrpc": "2.0", "method": "exit"},
        ]
        framed = b""
        for msg in messages:
            body = json.dumps(msg).encode("utf-8")
            framed += f"Content-Length: {len(body)}\r\n\r\n".encode("ascii") + body

        result = subprocess.run(
            [sys.executable, "-m", "roam", "lsp"],
            input=framed,
            capture_output=True,
            cwd=str(proj),
            timeout=20,
        )
        assert result.returncode == 0, result.stderr.decode("utf-8", errors="replace")

        out = result.stdout.decode("utf-8", errors="replace")
        responses = []
        cursor = 0
        while True:
            header_end = out.find("\r\n\r\n", cursor)
            if header_end < 0:
                break
            header = out[cursor:header_end]
            content_len = None
            for hline in header.split("\r\n"):
                if ":" in hline:
                    key, _, value = hline.partition(":")
                    if key.strip().lower() == "content-length":
                        content_len = int(value.strip())
            if content_len is None:
                break
            body_start = header_end + 4
            body = out[body_start : body_start + content_len]
            responses.append(json.loads(body))
            cursor = body_start + content_len

        kinds = [r.get("method") or f"id={r.get('id')}" for r in responses]
        assert "id=1" in kinds  # initialize response
        assert "textDocument/publishDiagnostics" in kinds
        assert "id=2" in kinds  # shutdown response

        diag_msg = next(r for r in responses if r.get("method") == "textDocument/publishDiagnostics")
        params = diag_msg["params"]
        assert params["uri"] == uri
        assert len(params["diagnostics"]) >= 1
        first = params["diagnostics"][0]
        assert first["source"] == "roam-stale-refs"
        assert first["severity"] in (2, 3)
        assert "range" in first
        assert "missing.md" in first["message"]

    def test_initialize_advertises_codeaction_and_filewatcher(self, tmp_path, monkeypatch):
        """Round-2: initialize response surfaces codeAction + workspace.fileOperations."""
        import json
        import subprocess
        import sys

        proj = tmp_path / "lsp_caps"
        proj.mkdir()
        (proj / "README.md").write_text("hi\n")
        git_init(proj)
        monkeypatch.chdir(proj)

        body = json.dumps(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "capabilities": {
                        "workspace": {"didChangeWatchedFiles": {"dynamicRegistration": True}},
                    }
                },
            }
        )
        framed = f"Content-Length: {len(body)}\r\n\r\n{body}".encode()
        result = subprocess.run(
            [sys.executable, "-m", "roam", "lsp", "--once"],
            input=framed,
            capture_output=True,
            cwd=str(proj),
            timeout=15,
        )
        assert result.returncode == 0
        out = result.stdout.decode("utf-8", errors="replace")
        body_start = out.find("\r\n\r\n")
        response = json.loads(out[body_start + 4 :].strip())
        caps = response["result"]["capabilities"]
        assert "codeActionProvider" in caps
        assert "quickfix" in caps["codeActionProvider"]["codeActionKinds"]
        # File-rename notifications are advertised statically.
        file_ops = (caps.get("workspace") or {}).get("fileOperations") or {}
        assert "willRename" in file_ops

    def test_will_rename_files_emits_workspace_edits(self, tmp_path, monkeypatch):
        """Round-2: ``workspace/willRenameFiles`` returns a WorkspaceEdit
        that updates references in OTHER files to the renamed path."""
        import json
        import subprocess
        import sys

        proj = tmp_path / "lsp_rename"
        proj.mkdir()
        (proj / "docs").mkdir()
        (proj / "docs" / "old.md").write_text("body\n")
        (proj / "README.md").write_text("# R\n\nSee [old](docs/old.md).\n")
        git_init(proj)
        monkeypatch.chdir(proj)

        old_uri = (proj / "docs" / "old.md").as_uri()
        new_uri = (proj / "docs" / "new.md").as_uri()

        messages = [
            {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
            {"jsonrpc": "2.0", "method": "initialized", "params": {}},
            {
                "jsonrpc": "2.0",
                "id": 7,
                "method": "workspace/willRenameFiles",
                "params": {
                    "files": [{"oldUri": old_uri, "newUri": new_uri}],
                },
            },
            {"jsonrpc": "2.0", "id": 99, "method": "shutdown"},
            {"jsonrpc": "2.0", "method": "exit"},
        ]
        framed = b""
        for msg in messages:
            body = json.dumps(msg).encode("utf-8")
            framed += f"Content-Length: {len(body)}\r\n\r\n".encode("ascii") + body

        result = subprocess.run(
            [sys.executable, "-m", "roam", "lsp"],
            input=framed,
            capture_output=True,
            cwd=str(proj),
            timeout=15,
        )
        assert result.returncode == 0, result.stderr.decode("utf-8", errors="replace")
        out = result.stdout.decode("utf-8", errors="replace")
        # Find the rename response (id=7).
        cursor = 0
        rename_response = None
        while True:
            header_end = out.find("\r\n\r\n", cursor)
            if header_end < 0:
                break
            header = out[cursor:header_end]
            length = None
            for h in header.split("\r\n"):
                if h.lower().startswith("content-length:"):
                    length = int(h.split(":", 1)[1].strip())
            if length is None:
                break
            body_start = header_end + 4
            body = out[body_start : body_start + length]
            cursor = body_start + length
            try:
                msg = json.loads(body)
            except json.JSONDecodeError:
                continue
            if msg.get("id") == 7:
                rename_response = msg
                break
        assert rename_response is not None
        result_obj = rename_response["result"]
        # WorkspaceEdit must contain a ``changes`` field with the README's URI.
        readme_uri = (proj / "README.md").as_uri()
        changes = result_obj.get("changes") or {}
        assert readme_uri in changes
        edits = changes[readme_uri]
        assert len(edits) == 1
        assert edits[0]["newText"] == "docs/new.md"

    def test_collect_rename_edits_preserves_fragment(self, tmp_path):
        """Unit: when a renamed-file ref carries an anchor, keep the anchor."""
        from roam.commands.cmd_lsp import _collect_rename_edits

        proj = tmp_path / "p"
        proj.mkdir()
        (proj / "docs").mkdir()
        (proj / "docs" / "old.md").write_text("# heading\n")
        readme_text = "[x](docs/old.md#heading)\n"
        edits = _collect_rename_edits(
            "README.md",
            readme_text,
            proj,
            old_rel="docs/old.md",
            new_rel="docs/new.md",
        )
        assert len(edits) == 1
        assert edits[0]["newText"] == "docs/new.md#heading"

    def test_out_of_project_uri_publishes_empty_diagnostics(self, tmp_path, monkeypatch):
        import json
        import subprocess
        import sys

        proj = tmp_path / "lsp_oop"
        proj.mkdir()
        (proj / "README.md").write_text("hi\n")
        git_init(proj)
        monkeypatch.chdir(proj)

        outside = tmp_path / "outside.md"
        outside.write_text("[x](missing.md)\n")
        outside_uri = outside.as_uri()

        messages = [
            {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
            {
                "jsonrpc": "2.0",
                "method": "textDocument/didOpen",
                "params": {
                    "textDocument": {
                        "uri": outside_uri,
                        "languageId": "markdown",
                        "version": 1,
                        "text": "[x](missing.md)\n",
                    }
                },
            },
            {"jsonrpc": "2.0", "method": "exit"},
        ]
        framed = b""
        for msg in messages:
            body = json.dumps(msg).encode("utf-8")
            framed += f"Content-Length: {len(body)}\r\n\r\n".encode("ascii") + body

        result = subprocess.run(
            [sys.executable, "-m", "roam", "lsp"],
            input=framed,
            capture_output=True,
            cwd=str(proj),
            timeout=15,
        )
        out = result.stdout.decode("utf-8", errors="replace")
        assert "textDocument/publishDiagnostics" in out
        # The out-of-project URI gets an EMPTY diagnostics array
        # (round-1 hardening: clears stale squiggles).
        assert '"diagnostics":[]' in out


# ---------------------------------------------------------------------------
# v12.49 round-2 hardening — line-tolerant baselines, dedup, watch composition
# ---------------------------------------------------------------------------


class TestStaleRefsBaselineLineTolerant:
    def test_baseline_survives_line_shifts(self, cli_runner, tmp_path, monkeypatch):
        """Findings that move down by N lines (e.g. user added a header)
        must still match the baseline so CI doesn't go red on a cosmetic
        edit."""
        proj = tmp_path / "baseline_lineshift"
        proj.mkdir()
        readme = proj / "README.md"
        readme.write_text("[broken](docs/missing.md)\n")
        git_init(proj)
        bp = str(proj / "baseline.json")
        monkeypatch.chdir(proj)
        invoke_cli(cli_runner, ["stale-refs", "--baseline-save", bp], cwd=proj)
        # User adds a copyright header — the broken ref shifts to line 6.
        readme.write_text(
            "<!-- Copyright 2026 Cranot -->\n<!-- Apache 2.0 -->\n\n# README\n\n[broken](docs/missing.md)\n"
        )
        # Re-scan with --baseline-from --gate. The single finding is
        # known so gate must pass.
        result = invoke_cli(
            cli_runner,
            ["stale-refs", "--baseline-from", bp, "--gate"],
            cwd=proj,
        )
        assert result.exit_code == 0

    def test_v1_baseline_files_still_load(self, tmp_path):
        """Backward compat: a v1 (line-precise) baseline file from an old
        roam-code release must still be honoured by the current loader."""
        import json

        from roam.commands.cmd_stale_refs import _load_baseline

        # Hand-construct a v1 baseline.
        v1_path = tmp_path / "v1_baseline.json"
        v1_payload = {
            "schema": "roam-stale-refs-baseline-v1",
            "findings": [
                "docs/missing.md|README.md:5:md_inline",
                "docs/gone.md|OTHER.md:12:html_attr",
            ],
        }
        v1_path.write_text(json.dumps(v1_payload), encoding="utf-8")
        loaded = _load_baseline(str(v1_path))
        # Records normalised to line-less v2 form.
        assert "docs/missing.md|README.md:md_inline" in loaded
        assert "docs/gone.md|OTHER.md:html_attr" in loaded
        # No leakage of the line numbers.
        assert all(":5:" not in r and ":12:" not in r for r in loaded)

    def test_normalise_handles_unknown_shape(self):
        """Records that don't match v1 or v2 shape pass through unchanged."""
        from roam.commands.cmd_stale_refs import _normalise_baseline_record

        # Record without ``|`` separator — passed through.
        assert _normalise_baseline_record("oddly-shaped") == "oddly-shaped"
        # v2 (already line-less) — unchanged.
        assert _normalise_baseline_record("a|b:kind") == "a|b:kind"
        # v1 with target containing a colon (URL-shaped target).
        # Anchor target like ``docs/foo.md#section|README.md:5:anchor``.
        v1 = "docs/foo.md#section|README.md:5:anchor"
        assert _normalise_baseline_record(v1) == "docs/foo.md#section|README.md:anchor"


class TestStaleRefsExternalDedup:
    def test_dedup_helper_collapses_repeated_urls(self):
        """Many references to the same URL ⇒ one HTTP probe."""
        from roam.commands.cmd_stale_refs import _check_external_urls_parallel

        # Build a list with 5 references to the same URL across 3 files
        # plus one distinct URL. Use an unroutable test domain so we
        # don't actually hit the network — _check_one_external_url
        # times out, returning None.
        urls_with_meta = [
            ("https://invalid.test.invalid/a", "f1.md", 1),
            ("https://invalid.test.invalid/a", "f2.md", 2),
            ("https://invalid.test.invalid/a", "f3.md", 3),
            ("https://invalid.test.invalid/a", "f1.md", 5),
            ("https://invalid.test.invalid/b", "f4.md", 1),
        ]
        results = _check_external_urls_parallel(urls_with_meta, timeout=0.5, concurrency=4)
        # Exactly 2 distinct URLs → exactly 2 result entries.
        assert len(results) == 2
        assert "https://invalid.test.invalid/a" in results
        assert "https://invalid.test.invalid/b" in results

    def test_parallel_helper_surfaces_programmer_errors(self, monkeypatch):
        """Worker bugs must not be reported as unreachable URLs."""
        import roam.commands.cmd_stale_refs as stale_refs

        def _raise_type_error(*_args, **_kwargs):
            raise TypeError("bad worker")

        monkeypatch.setattr(stale_refs, "_check_one_external_url", _raise_type_error)
        with pytest.raises(TypeError, match="bad worker"):
            stale_refs._check_external_urls_parallel(
                [("https://example.com", "README.md", 1)], timeout=0.5, concurrency=1
            )


class TestStaleRefsWatchHelpersComposition:
    def test_collect_mtimes_skips_ignored_files(self, tmp_path):
        """``_collect_mtimes`` honours the discovery's normal exclusions
        (gitignore, generated patterns) — verify that a junk-file
        extension doesn't show up in the mtime map."""
        from roam.commands.cmd_stale_refs import _collect_mtimes

        proj = tmp_path / "watch_skip"
        proj.mkdir()
        # ``.lock`` is in SKIP_EXTENSIONS, ``.md`` is not.
        (proj / "package-lock.json").write_text("{}")  # in SKIP_NAMES
        (proj / "README.md").write_text("hi")
        result = _collect_mtimes(proj, include_excluded=False)
        assert "README.md" in result
        assert "package-lock.json" not in result


class TestStaleRefsLspIncrementalFlow:
    def test_didchange_clearing_findings_sends_empty_diagnostics(self, tmp_path, monkeypatch):
        """Open a file with broken refs → diagnostics fire. didChange
        with content that fixes them → next publishDiagnostics is empty.
        """
        import json
        import subprocess
        import sys

        proj = tmp_path / "lsp_didchange"
        proj.mkdir()
        (proj / "docs").mkdir()
        (proj / "docs" / "intro.md").write_text("hi\n")
        readme = proj / "README.md"
        readme.write_text("[broken](docs/missing.md)\n")
        git_init(proj)
        monkeypatch.chdir(proj)
        uri = readme.as_uri()

        messages = [
            {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
            # didOpen with broken content.
            {
                "jsonrpc": "2.0",
                "method": "textDocument/didOpen",
                "params": {
                    "textDocument": {
                        "uri": uri,
                        "languageId": "markdown",
                        "version": 1,
                        "text": "[broken](docs/missing.md)\n",
                    }
                },
            },
            # didChange with content that fixes the ref (now points to live file).
            {
                "jsonrpc": "2.0",
                "method": "textDocument/didChange",
                "params": {
                    "textDocument": {"uri": uri, "version": 2},
                    "contentChanges": [{"text": "[fixed](docs/intro.md)\n"}],
                },
            },
            {"jsonrpc": "2.0", "method": "exit"},
        ]
        framed = b""
        for msg in messages:
            body = json.dumps(msg).encode("utf-8")
            framed += f"Content-Length: {len(body)}\r\n\r\n".encode("ascii") + body

        result = subprocess.run(
            [sys.executable, "-m", "roam", "lsp"],
            input=framed,
            capture_output=True,
            cwd=str(proj),
            timeout=20,
        )
        out = result.stdout.decode("utf-8", errors="replace")
        # Two publishDiagnostics calls expected: first non-empty
        # (broken), second empty (fixed). The presence of the empty
        # one in the post-didChange portion is what we care about.
        # Extract all diagnostics counts in order.
        import re

        diag_counts = []
        for match in re.finditer(r'"diagnostics":\[([^\]]*)\]', out):
            inner = match.group(1).strip()
            diag_counts.append(0 if not inner else inner.count('"range"'))
        # Should have at least 2 publishes; first has findings, last is empty.
        assert len(diag_counts) >= 2
        assert diag_counts[0] >= 1  # broken at didOpen
        assert diag_counts[-1] == 0  # cleared at didChange


# ---------------------------------------------------------------------------
# v12.49 round-3 hardening — comprehensive multi-angle coverage
# ---------------------------------------------------------------------------


class TestStaleRefsLlmEnricherIntegration:
    """End-to-end async tests for ``_enrich_stale_refs_with_llm_hints``
    with a stub Context.sample. Probe the actual async helper rather
    than just the parser + validator in isolation."""

    def _make_stub_ctx(self, response_text=None, *, raise_exc=None):
        class _StubResult:
            def __init__(self, text):
                self.text = text

        class _StubCtx:
            sample_calls = []

            async def sample(self, prompt, **kwargs):
                self.sample_calls.append((prompt, kwargs))
                if raise_exc is not None:
                    raise raise_exc
                if response_text is None:
                    return None
                return _StubResult(response_text)

        return _StubCtx()

    def _make_envelope(self, *, with_repo_paths=True):
        summary = {"missing_targets": 1}
        if with_repo_paths:
            summary["repo_paths_sample"] = [
                "docs/intro.md",
                "docs/setup.md",
                "README.md",
            ]
        return {
            "summary": summary,
            "targets": [
                {
                    "target": "docs/cold-outreach.md",
                    "ref_count": 1,
                    "sources": [
                        {
                            "file": "README.md",
                            "line": 1,
                            "kind": "md_inline",
                            "raw": "docs/cold-outreach.md",
                        }
                    ],
                }
            ],
        }

    def test_enricher_no_op_when_ai_disabled(self, monkeypatch):
        import asyncio

        from roam.mcp_server import _enrich_stale_refs_with_llm_hints

        monkeypatch.delenv("ROAM_AI_ENABLED", raising=False)
        envelope = self._make_envelope()
        ctx = self._make_stub_ctx(response_text='{"docs/cold-outreach.md": "docs/setup.md"}')
        result = asyncio.run(_enrich_stale_refs_with_llm_hints(envelope, ctx))
        assert "hint" not in result["targets"][0]
        assert ctx.sample_calls == []

    def test_enricher_attaches_hint_when_enabled(self, monkeypatch):
        import asyncio

        from roam.mcp_server import _enrich_stale_refs_with_llm_hints

        monkeypatch.setenv("ROAM_AI_ENABLED", "1")
        envelope = self._make_envelope()
        ctx = self._make_stub_ctx(response_text='{"docs/cold-outreach.md": "docs/setup.md"}')
        result = asyncio.run(_enrich_stale_refs_with_llm_hints(envelope, ctx))
        first = result["targets"][0]
        hint = first.get("hint")
        assert hint is not None
        assert hint["target"] == "docs/setup.md"
        assert hint["confidence"] == "MEDIUM"
        assert hint["source"] == "llm-sampling"
        assert result["summary"]["llm_hints_added"] == 1
        assert result["summary"]["by_confidence"].get("MEDIUM", 0) == 1

    def test_enricher_rejects_hallucinated_path(self, monkeypatch):
        import asyncio

        from roam.mcp_server import _enrich_stale_refs_with_llm_hints

        monkeypatch.setenv("ROAM_AI_ENABLED", "1")
        envelope = self._make_envelope()
        ctx = self._make_stub_ctx(response_text='{"docs/cold-outreach.md": "phantom/x.md"}')
        result = asyncio.run(_enrich_stale_refs_with_llm_hints(envelope, ctx))
        assert "hint" not in result["targets"][0]
        # 1C.2 + 1C.3: even on a fully-rejected response we expect the
        # observability fields plus the per-target diagnostics.
        assert result["summary"]["llm_hints_added"] == 0
        per_target = result["summary"]["llm_per_target"]
        assert "docs/cold-outreach.md" in per_target
        assert per_target["docs/cold-outreach.md"]["skip_reason"] == ("all candidates failed validation")
        rejected = per_target["docs/cold-outreach.md"].get("candidates_rejected", [])
        assert any(r["candidate"] == "phantom/x.md" for r in rejected)

    def test_enricher_resolves_basename_to_full_path(self, monkeypatch):
        import asyncio

        from roam.mcp_server import _enrich_stale_refs_with_llm_hints

        monkeypatch.setenv("ROAM_AI_ENABLED", "1")
        envelope = self._make_envelope()
        ctx = self._make_stub_ctx(response_text='{"docs/cold-outreach.md": "intro.md"}')
        result = asyncio.run(_enrich_stale_refs_with_llm_hints(envelope, ctx))
        assert result["targets"][0]["hint"]["target"] == "docs/intro.md"

    def test_enricher_graceful_when_sample_raises(self, monkeypatch):
        """Expected sampling errors (transport/network) are caught and the
        enricher returns the envelope without hints."""
        import asyncio

        from roam.mcp_server import _enrich_stale_refs_with_llm_hints

        monkeypatch.setenv("ROAM_AI_ENABLED", "1")
        envelope = self._make_envelope()
        ctx = self._make_stub_ctx(raise_exc=ConnectionError("net error"))
        result = asyncio.run(_enrich_stale_refs_with_llm_hints(envelope, ctx))
        assert "hint" not in result["targets"][0]

    def test_enricher_no_op_without_repo_paths(self, monkeypatch):
        """If caller forgot ``--with-candidates``, don't waste the LLM call."""
        import asyncio

        from roam.mcp_server import _enrich_stale_refs_with_llm_hints

        monkeypatch.setenv("ROAM_AI_ENABLED", "1")
        envelope = self._make_envelope(with_repo_paths=False)
        ctx = self._make_stub_ctx(response_text='{"a": "b"}')
        result = asyncio.run(_enrich_stale_refs_with_llm_hints(envelope, ctx))
        assert "hint" not in result["targets"][0]
        assert ctx.sample_calls == []

    def test_enricher_records_latency_and_response_size(self, monkeypatch):
        """1C.3: every successful sample call should record observability fields."""
        import asyncio

        from roam.mcp_server import _enrich_stale_refs_with_llm_hints

        monkeypatch.setenv("ROAM_AI_ENABLED", "1")
        envelope = self._make_envelope()
        ctx = self._make_stub_ctx(response_text='{"docs/cold-outreach.md": ["docs/setup.md"]}')
        result = asyncio.run(_enrich_stale_refs_with_llm_hints(envelope, ctx))
        summary = result["summary"]
        assert "llm_latency_ms" in summary
        assert isinstance(summary["llm_latency_ms"], int)
        assert summary["llm_latency_ms"] >= 0
        assert "llm_response_chars" in summary
        assert summary["llm_response_chars"] > 0
        assert "llm_targets_asked" in summary
        assert summary["llm_targets_asked"] == 1
        assert "llm_prompt_chars" in summary
        assert summary["llm_prompt_chars"] > 0

    def test_enricher_records_latency_on_failure(self, monkeypatch):
        """1C.3: even when sample() raises an expected sampling error, latency
        is still recorded and the failure is surfaced as a skip reason."""
        import asyncio

        from roam.mcp_server import _enrich_stale_refs_with_llm_hints

        monkeypatch.setenv("ROAM_AI_ENABLED", "1")
        envelope = self._make_envelope()
        ctx = self._make_stub_ctx(raise_exc=ConnectionError("net error"))
        result = asyncio.run(_enrich_stale_refs_with_llm_hints(envelope, ctx))
        summary = result["summary"]
        # Latency lands even though no hints landed.
        assert "llm_latency_ms" in summary
        assert summary["llm_skip_reason"].startswith("sampling raised:")

    def test_enricher_reraises_unexpected_errors(self, monkeypatch):
        """Unexpected programming errors must not be swallowed silently."""
        import asyncio

        from roam.mcp_server import _enrich_stale_refs_with_llm_hints

        monkeypatch.setenv("ROAM_AI_ENABLED", "1")
        envelope = self._make_envelope()
        ctx = self._make_stub_ctx(raise_exc=RuntimeError("internal bug"))
        try:
            asyncio.run(_enrich_stale_refs_with_llm_hints(envelope, ctx))
        except RuntimeError as exc:
            assert "internal bug" in str(exc)
        else:
            raise AssertionError("expected RuntimeError to propagate")

    def test_enricher_uses_ranked_candidates(self, monkeypatch):
        """1C.1: when the LLM returns a ranked list we pick the first valid one."""
        import asyncio

        from roam.mcp_server import _enrich_stale_refs_with_llm_hints

        monkeypatch.setenv("ROAM_AI_ENABLED", "1")
        envelope = self._make_envelope()
        # First candidate is hallucinated; second is valid. Enricher should fall through.
        response = '{"docs/cold-outreach.md": ["phantom/x.md", "docs/setup.md", "docs/intro.md"]}'
        ctx = self._make_stub_ctx(response_text=response)
        result = asyncio.run(_enrich_stale_refs_with_llm_hints(envelope, ctx))
        chosen = result["targets"][0]["hint"]["target"]
        assert chosen == "docs/setup.md"
        # The third candidate becomes a runner-up.
        assert result["targets"][0].get("llm_alternates") == ["docs/intro.md"]

    def test_enricher_per_target_diagnostics(self, monkeypatch):
        """1C.2: per-target skip reasons land in summary.llm_per_target."""
        import asyncio

        from roam.mcp_server import _enrich_stale_refs_with_llm_hints

        monkeypatch.setenv("ROAM_AI_ENABLED", "1")
        envelope = self._make_envelope()
        # Empty list means "LLM had no plausible candidate" — must map
        # to a per-target skip reason rather than a global one.
        ctx = self._make_stub_ctx(response_text='{"docs/cold-outreach.md": []}')
        result = asyncio.run(_enrich_stale_refs_with_llm_hints(envelope, ctx))
        per_target = result["summary"]["llm_per_target"]
        assert per_target["docs/cold-outreach.md"]["skip_reason"] == ("LLM returned empty candidate list")


class TestStaleRefsBaselineRobustness:
    def test_load_handles_corrupt_json(self, tmp_path):
        from roam.commands.cmd_stale_refs import _load_baseline

        path = tmp_path / "corrupt.json"
        path.write_text("not json {{{")
        assert _load_baseline(str(path)) == set()

    def test_load_handles_unexpected_schema(self, tmp_path):
        import json

        from roam.commands.cmd_stale_refs import _load_baseline

        path = tmp_path / "weird.json"
        path.write_text(json.dumps({"some_other_key": ["x"]}))
        assert _load_baseline(str(path)) == set()

    def test_load_filters_non_string_records(self, tmp_path):
        import json

        from roam.commands.cmd_stale_refs import _load_baseline

        path = tmp_path / "mixed.json"
        path.write_text(
            json.dumps(
                {
                    "findings": [
                        "docs/x.md|README.md:md_inline",
                        42,
                        None,
                        "docs/y.md|F.md:backtick",
                    ]
                }
            )
        )
        out = _load_baseline(str(path))
        assert "docs/x.md|README.md:md_inline" in out
        assert "docs/y.md|F.md:backtick" in out
        assert len(out) == 2

    def test_baseline_composes_with_diff(self, cli_runner, tmp_path, monkeypatch):
        """`--baseline-from + --diff` filters stack correctly."""
        import subprocess

        proj = tmp_path / "baseline_diff"
        proj.mkdir()
        (proj / ".gitignore").write_text(".roam/\n")
        (proj / "README.md").write_text("[old](docs/old.md)\n")
        git_init(proj)
        bp = str(proj / "baseline.json")
        monkeypatch.chdir(proj)
        invoke_cli(cli_runner, ["stale-refs", "--baseline-save", bp], cwd=proj)
        subprocess.run(["git", "checkout", "-b", "feat"], cwd=proj, capture_output=True)
        (proj / "README.md").write_text("[old](docs/old.md)\n[new](docs/new.md)\n")
        subprocess.run(["git", "add", "."], cwd=proj, capture_output=True)
        subprocess.run(
            [
                "git",
                "-c",
                "user.email=t@t",
                "-c",
                "user.name=t",
                "commit",
                "-m",
                "feat",
            ],
            cwd=proj,
            capture_output=True,
        )
        result = invoke_cli(
            cli_runner,
            ["stale-refs", "--baseline-from", bp, "--diff", "master"],
            cwd=proj,
            json_mode=True,
        )
        data = parse_json_output(result, "stale-refs")
        targets = {t["target"] for t in data["targets"]}
        assert "docs/new.md" in targets
        assert "docs/old.md" not in targets


class TestStaleRefsExternalIntegration:
    """End-to-end --check-external using unroutable test domains."""

    def test_external_findings_appear_in_targets(self, cli_runner, tmp_path, monkeypatch):
        proj = tmp_path / "ext_int"
        proj.mkdir()
        (proj / "README.md").write_text("[broken](https://invalid.test.invalid/x)\n")
        git_init(proj)
        monkeypatch.chdir(proj)
        result = invoke_cli(
            cli_runner,
            ["stale-refs", "--check-external", "--external-timeout", "1"],
            cwd=proj,
            json_mode=True,
        )
        data = parse_json_output(result, "stale-refs")
        targets = {t["target"] for t in data["targets"]}
        assert "https://invalid.test.invalid/x" in targets

    def test_external_findings_emit_external_sarif_rule(self, cli_runner, tmp_path, monkeypatch):
        import json

        proj = tmp_path / "ext_sarif"
        proj.mkdir()
        (proj / "README.md").write_text("[broken](https://invalid.test.invalid/x)\n")
        git_init(proj)
        monkeypatch.chdir(proj)
        result = invoke_cli(
            cli_runner,
            ["--sarif", "stale-refs", "--check-external", "--external-timeout", "1"],
            cwd=proj,
        )
        assert result.exit_code == 0
        data = json.loads(result.output)
        rules = {r["id"] for r in data["runs"][0]["tool"]["driver"]["rules"]}
        assert "stale-refs/external" in rules

    def test_external_findings_respect_ignore_target(self, cli_runner, tmp_path, monkeypatch):
        proj = tmp_path / "ext_ignore"
        proj.mkdir()
        (proj / "README.md").write_text(
            "[a](https://invalid.test.invalid/keep)\n[b](https://invalid.test.invalid/drop)\n"
        )
        git_init(proj)
        monkeypatch.chdir(proj)
        result = invoke_cli(
            cli_runner,
            [
                "stale-refs",
                "--check-external",
                "--external-timeout",
                "1",
                "--ignore-target",
                "*invalid.test.invalid/drop*",
            ],
            cwd=proj,
            json_mode=True,
        )
        data = parse_json_output(result, "stale-refs")
        targets = {t["target"] for t in data["targets"]}
        assert "https://invalid.test.invalid/keep" in targets
        assert "https://invalid.test.invalid/drop" not in targets


class TestStaleRefsLspEdgeCases:
    def test_didopen_clean_file_emits_empty_diagnostics(self, tmp_path, monkeypatch):
        """Clean file → publishDiagnostics with empty array (not silent)."""
        import json
        import subprocess
        import sys

        proj = tmp_path / "lsp_clean"
        proj.mkdir()
        (proj / "docs").mkdir()
        (proj / "docs" / "intro.md").write_text("hi\n")
        readme = proj / "README.md"
        readme.write_text("[ok](docs/intro.md)\n")
        git_init(proj)
        monkeypatch.chdir(proj)
        uri = readme.as_uri()

        messages = [
            {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
            {
                "jsonrpc": "2.0",
                "method": "textDocument/didOpen",
                "params": {
                    "textDocument": {
                        "uri": uri,
                        "languageId": "markdown",
                        "version": 1,
                        "text": readme.read_text(encoding="utf-8"),
                    }
                },
            },
            {"jsonrpc": "2.0", "method": "exit"},
        ]
        framed = b""
        for msg in messages:
            body = json.dumps(msg).encode("utf-8")
            framed += f"Content-Length: {len(body)}\r\n\r\n".encode("ascii") + body

        result = subprocess.run(
            [sys.executable, "-m", "roam", "lsp"],
            input=framed,
            capture_output=True,
            cwd=str(proj),
            timeout=15,
        )
        out = result.stdout.decode("utf-8", errors="replace")
        assert "textDocument/publishDiagnostics" in out
        assert '"diagnostics":[]' in out

    def test_didchange_introduces_diagnostic(self, tmp_path, monkeypatch):
        """Open clean → didChange with broken link → diagnostics fire."""
        import json
        import re
        import subprocess
        import sys

        proj = tmp_path / "lsp_introduce"
        proj.mkdir()
        readme = proj / "README.md"
        readme.write_text("# Hi\n")
        git_init(proj)
        monkeypatch.chdir(proj)
        uri = readme.as_uri()

        messages = [
            {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
            {
                "jsonrpc": "2.0",
                "method": "textDocument/didOpen",
                "params": {
                    "textDocument": {
                        "uri": uri,
                        "languageId": "markdown",
                        "version": 1,
                        "text": "# Hi\n",
                    }
                },
            },
            {
                "jsonrpc": "2.0",
                "method": "textDocument/didChange",
                "params": {
                    "textDocument": {"uri": uri, "version": 2},
                    "contentChanges": [{"text": "# Hi\n\n[broken](docs/missing.md)\n"}],
                },
            },
            {"jsonrpc": "2.0", "method": "exit"},
        ]
        framed = b""
        for msg in messages:
            body = json.dumps(msg).encode("utf-8")
            framed += f"Content-Length: {len(body)}\r\n\r\n".encode("ascii") + body

        result = subprocess.run(
            [sys.executable, "-m", "roam", "lsp"],
            input=framed,
            capture_output=True,
            cwd=str(proj),
            timeout=15,
        )
        out = result.stdout.decode("utf-8", errors="replace")
        diag_counts = []
        for match in re.finditer(r'"diagnostics":\[([^\]]*)\]', out):
            inner = match.group(1).strip()
            diag_counts.append(0 if not inner else inner.count('"range"'))
        assert len(diag_counts) >= 2
        assert diag_counts[0] == 0  # clean
        assert diag_counts[-1] >= 1  # broken introduced

    def test_unknown_method_returns_method_not_found(self, tmp_path, monkeypatch):
        """Unknown method WITH id → JSON-RPC -32601 error response."""
        import json
        import subprocess
        import sys

        proj = tmp_path / "lsp_unknown"
        proj.mkdir()
        (proj / "README.md").write_text("hi\n")
        git_init(proj)
        monkeypatch.chdir(proj)

        messages = [
            {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
            {
                "jsonrpc": "2.0",
                "id": 99,
                "method": "workspace/madeUpMethod",
                "params": {},
            },
            {"jsonrpc": "2.0", "method": "exit"},
        ]
        framed = b""
        for msg in messages:
            body = json.dumps(msg).encode("utf-8")
            framed += f"Content-Length: {len(body)}\r\n\r\n".encode("ascii") + body

        result = subprocess.run(
            [sys.executable, "-m", "roam", "lsp"],
            input=framed,
            capture_output=True,
            cwd=str(proj),
            timeout=15,
        )
        out = result.stdout.decode("utf-8", errors="replace")
        assert '"id":99' in out
        assert "-32601" in out


class TestStaleRefsWatchInNonGitDir:
    def test_collect_mtimes_works_without_git(self, tmp_path):
        """``_collect_mtimes`` falls back to ``os.walk`` when no .git
        is present — the watch loop's primitives don't require git."""
        from roam.commands.cmd_stale_refs import _collect_mtimes, _scan_finding_set

        proj = tmp_path / "nogit"
        proj.mkdir()
        (proj / "README.md").write_text("[x](missing.md)\n")
        # No git_init.
        result = _collect_mtimes(proj, include_excluded=False)
        assert "README.md" in result
        assert _scan_finding_set({}) == set()


# ---------------------------------------------------------------------------
# v12.49 round-4 polish — composition guards, skip-reasons, dynamic version
# ---------------------------------------------------------------------------


class TestStaleRefsWatchCompositionGuards:
    """``--watch`` rejects flags whose semantics don't fit a polling loop;
    composes correctly with ``--baseline-from``."""

    def test_watch_rejects_check_external(self, cli_runner, tmp_path, monkeypatch):
        proj = tmp_path / "watch_check_ext"
        proj.mkdir()
        (proj / "README.md").write_text("hi\n")
        git_init(proj)
        monkeypatch.chdir(proj)
        result = invoke_cli(cli_runner, ["stale-refs", "--watch", "--check-external"], cwd=proj)
        assert result.exit_code == 2
        assert "watch" in result.output.lower()
        assert "external" in result.output.lower()

    def test_watch_rejects_fix(self, cli_runner, tmp_path, monkeypatch):
        proj = tmp_path / "watch_fix"
        proj.mkdir()
        (proj / "README.md").write_text("hi\n")
        git_init(proj)
        monkeypatch.chdir(proj)
        for fix_mode in ("preview", "apply"):
            result = invoke_cli(cli_runner, ["stale-refs", "--watch", "--fix", fix_mode], cwd=proj)
            assert result.exit_code == 2, f"--watch + --fix {fix_mode}"
            assert "fix" in result.output.lower()

    def test_watch_loop_signature_accepts_baseline(self):
        """``_run_watch_loop`` signature has a ``baseline`` keyword.

        Full filter testing requires fs events; we lock the contract
        at the helper-signature level so refactors don't silently
        drop the parameter and re-introduce the round-1 bug.
        """
        import inspect

        from roam.commands.cmd_stale_refs import _run_watch_loop

        sig = inspect.signature(_run_watch_loop)
        assert "baseline" in sig.parameters
        assert sig.parameters["baseline"].default is None


class TestStaleRefsLlmSkipReason:
    """The LLM enricher must populate ``summary.llm_skip_reason`` on
    every silent-degradation path so callers can debug."""

    def _make_envelope(self, *, with_repo_paths=True, with_targets=True):
        summary = {"missing_targets": 1 if with_targets else 0}
        if with_repo_paths:
            summary["repo_paths_sample"] = ["docs/intro.md"]
        targets = []
        if with_targets:
            targets = [
                {
                    "target": "docs/missing.md",
                    "ref_count": 1,
                    "sources": [
                        {
                            "file": "README.md",
                            "line": 1,
                            "kind": "md_inline",
                            "raw": "docs/missing.md",
                        }
                    ],
                }
            ]
        return {"summary": summary, "targets": targets}

    def _stub_ctx(self, *, sample_response=None):
        class _Result:
            def __init__(self, text):
                self.text = text

        class _Ctx:
            async def sample(self, prompt, **kwargs):
                if sample_response is None:
                    return None
                return _Result(sample_response)

        return _Ctx()

    def test_skip_reason_when_env_unset(self, monkeypatch):
        import asyncio

        from roam.mcp_server import _enrich_stale_refs_with_llm_hints

        monkeypatch.delenv("ROAM_AI_ENABLED", raising=False)
        envelope = self._make_envelope()
        result = asyncio.run(_enrich_stale_refs_with_llm_hints(envelope, self._stub_ctx()))
        assert "ROAM_AI_ENABLED" in result["summary"]["llm_skip_reason"]

    def test_skip_reason_when_no_sample_method(self, monkeypatch):
        import asyncio

        from roam.mcp_server import _enrich_stale_refs_with_llm_hints

        monkeypatch.setenv("ROAM_AI_ENABLED", "1")
        envelope = self._make_envelope()
        result = asyncio.run(_enrich_stale_refs_with_llm_hints(envelope, object()))
        assert "sample()" in result["summary"]["llm_skip_reason"]

    def test_skip_reason_when_no_repo_paths(self, monkeypatch):
        import asyncio

        from roam.mcp_server import _enrich_stale_refs_with_llm_hints

        monkeypatch.setenv("ROAM_AI_ENABLED", "1")
        envelope = self._make_envelope(with_repo_paths=False)
        result = asyncio.run(_enrich_stale_refs_with_llm_hints(envelope, self._stub_ctx()))
        assert "repo_paths_sample" in result["summary"]["llm_skip_reason"]

    def test_skip_reason_when_no_findings(self, monkeypatch):
        import asyncio

        from roam.mcp_server import _enrich_stale_refs_with_llm_hints

        monkeypatch.setenv("ROAM_AI_ENABLED", "1")
        envelope = self._make_envelope(with_targets=False)
        result = asyncio.run(_enrich_stale_refs_with_llm_hints(envelope, self._stub_ctx()))
        assert "no findings" in result["summary"]["llm_skip_reason"]

    def test_skip_reason_when_sampling_raises(self, monkeypatch):
        import asyncio

        from roam.mcp_server import _enrich_stale_refs_with_llm_hints

        monkeypatch.setenv("ROAM_AI_ENABLED", "1")
        envelope = self._make_envelope()

        class _RaisingCtx:
            async def sample(self, *a, **kw):
                raise ConnectionError("net down")

        result = asyncio.run(_enrich_stale_refs_with_llm_hints(envelope, _RaisingCtx()))
        assert "sampling raised" in result["summary"]["llm_skip_reason"]
        assert "ConnectionError" in result["summary"]["llm_skip_reason"]

    def test_skip_reason_when_response_unparseable(self, monkeypatch):
        import asyncio

        from roam.mcp_server import _enrich_stale_refs_with_llm_hints

        monkeypatch.setenv("ROAM_AI_ENABLED", "1")
        envelope = self._make_envelope()
        result = asyncio.run(_enrich_stale_refs_with_llm_hints(envelope, self._stub_ctx(sample_response="garbage")))
        assert "unparseable" in result["summary"]["llm_skip_reason"]

    def test_no_skip_reason_on_success(self, monkeypatch):
        """Successful enrichment doesn't set llm_skip_reason."""
        import asyncio

        from roam.mcp_server import _enrich_stale_refs_with_llm_hints

        monkeypatch.setenv("ROAM_AI_ENABLED", "1")
        envelope = self._make_envelope()
        result = asyncio.run(
            _enrich_stale_refs_with_llm_hints(
                envelope,
                self._stub_ctx(sample_response='{"docs/missing.md": "docs/intro.md"}'),
            )
        )
        assert result["summary"].get("llm_hints_added") == 1
        assert "llm_skip_reason" not in result["summary"]


class TestStaleRefsLspDynamicVersion:
    def test_server_version_matches_package(self):
        """LSP serverInfo version reflects ``roam.__version__``."""
        from roam import __version__
        from roam.commands.cmd_lsp import _server_version

        assert _server_version() == str(__version__)

    def test_server_version_is_non_empty(self):
        from roam.commands.cmd_lsp import _server_version

        assert _server_version()

    def test_server_version_falls_back_when_package_version_missing(self, monkeypatch):
        import roam
        from roam.commands.cmd_lsp import _server_version

        # roam.__version__ is resolved lazily via a PEP 562 module __getattr__
        # (deferred so importlib.metadata is not paid on every import). Force the
        # helper's deferred ``from roam import __version__`` to raise ImportError
        # so its ``"unknown"`` fallback is exercised.
        def _raise_import_error(name: str) -> str:
            raise ImportError(f"simulated version-resolution failure for {name!r}")

        monkeypatch.setattr(roam, "__getattr__", _raise_import_error)
        assert _server_version() == "unknown"


class TestFindBrokenLinksRecipeFollowups:
    """The find-broken-links recipe surfaces all v12.49 channels in
    its ``followups`` list so agents discover them via ``roam ask``."""

    def test_followups_mention_watch(self):
        from roam.ask.recipes import by_name

        recipe = by_name("find-broken-links")
        assert recipe is not None
        assert any("--watch" in f for f in recipe.followups)

    def test_followups_mention_lsp(self):
        from roam.ask.recipes import by_name

        recipe = by_name("find-broken-links")
        assert any("roam lsp" in f for f in recipe.followups)

    def test_followups_mention_baseline(self):
        from roam.ask.recipes import by_name

        recipe = by_name("find-broken-links")
        assert any("--baseline" in f for f in recipe.followups)

    def test_followups_mention_check_external(self):
        from roam.ask.recipes import by_name

        recipe = by_name("find-broken-links")
        assert any("--check-external" in f for f in recipe.followups)

    def test_followups_mention_fix(self):
        from roam.ask.recipes import by_name

        recipe = by_name("find-broken-links")
        assert any("--fix" in f for f in recipe.followups)


# ---------------------------------------------------------------------------
# Repo config (.roam/stale-refs.toml) + ``--init``
# ---------------------------------------------------------------------------


class TestRepoConfig:
    """Repo-level config file is loaded as defaults; CLI flags still override."""

    def test_init_creates_config_file(self, cli_runner, dangling_project):
        result = invoke_cli(cli_runner, ["stale-refs", "--init"], cwd=dangling_project)
        config_path = dangling_project / ".roam" / "stale-refs.toml"
        assert result.exit_code == 0
        assert config_path.exists()
        text = config_path.read_text(encoding="utf-8")
        assert "stale-refs" in text
        assert "sort_by" in text

    def test_init_does_not_clobber_existing(self, cli_runner, dangling_project):
        config_path = dangling_project / ".roam" / "stale-refs.toml"
        config_path.parent.mkdir(parents=True, exist_ok=True)
        config_path.write_text('# user edits\nignore = ["custom.md"]\n', encoding="utf-8")
        result = invoke_cli(cli_runner, ["stale-refs", "--init"], cwd=dangling_project)
        assert result.exit_code == 0
        assert "already exists" in result.output
        # Original content preserved
        assert "user edits" in config_path.read_text(encoding="utf-8")

    def test_init_force_overwrites(self, cli_runner, dangling_project):
        config_path = dangling_project / ".roam" / "stale-refs.toml"
        config_path.parent.mkdir(parents=True, exist_ok=True)
        config_path.write_text('ignore = ["custom.md"]\n', encoding="utf-8")
        result = invoke_cli(
            cli_runner,
            ["stale-refs", "--init", "--init-force"],
            cwd=dangling_project,
        )
        assert result.exit_code == 0
        assert "custom.md" not in config_path.read_text(encoding="utf-8")
        assert "sort_by" in config_path.read_text(encoding="utf-8")

    def test_config_ignore_applied_when_flag_omitted(self, cli_runner, dangling_project):
        config_path = dangling_project / ".roam" / "stale-refs.toml"
        config_path.parent.mkdir(parents=True, exist_ok=True)
        # Without the config, dangling_project reports >0 stale refs.
        # Glob-blocking every source file should drop the count to 0.
        config_path.write_text(
            'ignore = ["README.md", "docs/site.html", "docs/index.md"]\n',
            encoding="utf-8",
        )
        result = invoke_cli(cli_runner, ["stale-refs"], cwd=dangling_project, json_mode=True)
        assert result.exit_code == 0
        data = parse_json_output(result)
        assert data["summary"]["stale_refs"] == 0

    def test_minimal_toml_parser_handles_arrays_and_bools(self, tmp_path):
        from roam.commands.cmd_stale_refs import _parse_minimal_toml

        path = tmp_path / "x.toml"
        path.write_text(
            '# comment\nignore = ["a.md", "b.md"]\ncheck_external = true\nlimit = 50\n',
            encoding="utf-8",
        )
        parsed = _parse_minimal_toml(path)
        assert parsed["ignore"] == ["a.md", "b.md"]
        assert parsed["check_external"] is True
        assert parsed["limit"] == 50

    def test_suggest_config_picks_up_changelog(self, tmp_path):
        from roam.commands.cmd_stale_refs import _suggest_config_for_repo

        proj = tmp_path / "p"
        proj.mkdir()
        (proj / "CHANGELOG.md").write_text("# changelog\n")
        suggestion = _suggest_config_for_repo(proj)
        assert "CHANGELOG.md" in suggestion.get("ignore", [])
        assert suggestion.get("sort_by") == "priority"

    def test_suggest_config_picks_up_legacy_dir(self, tmp_path):
        from roam.commands.cmd_stale_refs import _suggest_config_for_repo

        proj = tmp_path / "p"
        proj.mkdir()
        (proj / "docs" / "legacy").mkdir(parents=True)
        suggestion = _suggest_config_for_repo(proj)
        assert "docs/legacy/**" in suggestion.get("ignore", [])

    def test_attest_writes_in_toto_statement(self, cli_runner, dangling_project, tmp_path):
        """1G.2: --attest writes a structurally valid in-toto v1 statement."""
        import json as _json

        out_path = tmp_path / "attest.intoto.json"
        result = invoke_cli(
            cli_runner,
            ["stale-refs", "--attest", str(out_path)],
            cwd=dangling_project,
        )
        assert result.exit_code == 0
        assert out_path.exists()
        statement = _json.loads(out_path.read_text(encoding="utf-8"))
        assert statement["_type"] == "https://in-toto.io/Statement/v1"
        assert statement["predicateType"] == "https://roam-code.com/StaleRefs/v1"
        assert isinstance(statement["subject"], list) and len(statement["subject"]) == 1
        predicate = statement["predicate"]
        assert "scan_summary" in predicate
        assert "targets" in predicate
        assert "tool" in predicate
        assert predicate["tool"]["name"] == "roam-stale-refs"

    def test_attest_to_stdout(self, cli_runner, dangling_project):
        """1G.2: --attest=- writes the statement to stdout instead of a file."""
        import json as _json

        result = invoke_cli(
            cli_runner,
            ["stale-refs", "--attest", "-"],
            cwd=dangling_project,
        )
        assert result.exit_code == 0
        # stdout contains the verdict line plus the JSON statement; pull the JSON out.
        for line in result.output.splitlines():
            line = line.strip()
            if line.startswith("{") and line.endswith("}"):
                statement = _json.loads(line)
                assert statement["predicateType"] == "https://roam-code.com/StaleRefs/v1"
                return
        raise AssertionError("no JSON statement found on stdout")

    def test_verify_stale_refs_attestation_round_trip(self, dangling_project):
        """1G.2: ``verify_stale_refs_attestation`` validates a freshly built statement."""
        from roam.commands.cmd_stale_refs import (
            build_stale_refs_attestation,
            verify_stale_refs_attestation,
        )

        statement = build_stale_refs_attestation(
            project_root=dangling_project,
            summary={"verdict": "ok", "stale_refs": 3},
            targets=[{"target": "docs/x.md", "ref_count": 1}],
            findings=[{"file": "README.md", "line": 1}],
        )
        ok, reason = verify_stale_refs_attestation(statement)
        assert ok, reason
        # Tampering invalidates.
        statement["predicateType"] = "wrong"
        ok, reason = verify_stale_refs_attestation(statement)
        assert not ok
        assert "predicateType" in reason

    def test_root_override_scans_different_repo(self, cli_runner, tmp_path):
        """1I.1: --root scans a directory that isn't the current working dir."""
        # Build two repos. We invoke from one; --root points at the other.
        repo_a = tmp_path / "repo_a"
        repo_a.mkdir()
        (repo_a / ".gitignore").write_text(".roam/\n")
        (repo_a / "README.md").write_text("nothing broken here\n")
        git_init(repo_a)

        repo_b = tmp_path / "repo_b"
        repo_b.mkdir()
        (repo_b / ".gitignore").write_text(".roam/\n")
        (repo_b / "README.md").write_text("[broken](missing-from-b.md)\n")
        git_init(repo_b)

        # Run from repo_a but scan repo_b via --root.
        result = invoke_cli(
            cli_runner,
            ["stale-refs", "--root", str(repo_b), "--json"],
            cwd=repo_a,
        )
        assert result.exit_code == 0
        data = parse_json_output(result)
        # missing-from-b.md was the only dangling reference.
        assert data["summary"]["stale_refs"] >= 1
        assert any("missing-from-b.md" in t.get("target", "") for t in data["targets"])

    def test_root_override_rejects_non_directory(self, cli_runner, tmp_path):
        """1I.1: --root pointing at a non-directory bails with a usage error."""
        result = invoke_cli(
            cli_runner,
            ["stale-refs", "--root", "/totally/not/a/dir/xyz123"],
            cwd=tmp_path,
        )
        # Click exits 2 on UsageError.
        assert result.exit_code != 0

    # ---- Phase 2A: composition matrix ---------------------------------

    def test_init_writes_into_root_override(self, cli_runner, tmp_path):
        """2A: --init writes ``.roam/stale-refs.toml`` into the --root path,
        not into the current working directory."""
        scan_target = tmp_path / "scan_target"
        scan_target.mkdir()
        (scan_target / "CHANGELOG.md").write_text("changelog\n")
        git_init(scan_target)

        invoking_from = tmp_path / "elsewhere"
        invoking_from.mkdir()
        git_init(invoking_from)

        result = invoke_cli(
            cli_runner,
            ["stale-refs", "--root", str(scan_target), "--init"],
            cwd=invoking_from,
        )
        assert result.exit_code == 0
        assert (scan_target / ".roam" / "stale-refs.toml").exists()
        assert not (invoking_from / ".roam" / "stale-refs.toml").exists()

    def test_github_summary_writes_markdown_table(self, cli_runner, dangling_project, tmp_path):
        """Smarter-5: ``--github-summary`` writes a GH-flavoured markdown table."""
        out = tmp_path / "STEP_SUMMARY.md"
        result = invoke_cli(
            cli_runner,
            ["stale-refs", "--github-summary", str(out)],
            cwd=dangling_project,
        )
        assert result.exit_code == 0
        assert out.exists()
        text = out.read_text(encoding="utf-8")
        assert text.startswith("## roam stale-refs")
        assert "| Missing target | Confidence | Hint | Sources |" in text

    def test_github_summary_clean_repo(self, cli_runner, clean_project, tmp_path):
        """Smarter-5: clean repo → summary still emits, with 'no findings' line."""
        out = tmp_path / "S.md"
        result = invoke_cli(
            cli_runner,
            ["stale-refs", "--github-summary", str(out)],
            cwd=clean_project,
        )
        assert result.exit_code == 0
        assert "All references resolve" in out.read_text(encoding="utf-8")

    def test_fix_apply_logs_hint_acceptances(self, cli_runner, tmp_path):
        """Smarter-3: ``--fix apply`` appends to .roam/hint-acceptances.jsonl."""
        proj = tmp_path / "feedback_loop"
        proj.mkdir()
        (proj / ".gitignore").write_text(".roam/\n")
        (proj / "docs").mkdir()
        (proj / "docs" / "old.md").write_text("# Old\n")
        (proj / "README.md").write_text("[g](docs/old.md)\n")
        git_init(proj)
        # Commit + git mv to trigger HIGH-confidence rename detection.
        import subprocess

        subprocess.run(["git", "mv", "docs/old.md", "docs/new.md"], cwd=proj, capture_output=True)
        subprocess.run(
            ["git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "rename"],
            cwd=proj,
            capture_output=True,
        )
        result = invoke_cli(cli_runner, ["stale-refs", "--fix", "apply"], cwd=proj)
        assert result.exit_code == 0
        log_path = proj / ".roam" / "hint-acceptances.jsonl"
        assert log_path.exists()
        import json as _json

        lines = log_path.read_text(encoding="utf-8").strip().splitlines()
        assert len(lines) >= 1
        row = _json.loads(lines[0])
        assert row["missing"] == "docs/old.md"
        assert row["rewrite"] == "docs/new.md"
        assert row["confidence"] == "HIGH"
        assert "source" in row and row["source"]
        assert row["src_file"] == "README.md"

    def test_register_hint_provider_runs_after_builtins(self):
        """Smarter-1: external providers can be plugged in via the registry."""
        from roam.commands.stale_refs_hints import (
            _EXTRA_PROVIDERS,
            Hint,
            HintContext,
            best_hint,
            register_hint_provider,
        )

        class _Custom:
            def hint(self, missing_rel, ctx):
                if missing_rel == "vendor/docs/missing.md":
                    return Hint(
                        target="vendor/docs/intro.md",
                        confidence="MEDIUM",
                        reason="vendor docs alias",
                        source="custom",
                    )
                return None

        custom = _Custom()
        register_hint_provider(custom)
        try:
            ctx = HintContext(project_root=type(self).__module__ and type("X", (), {})(), basename_idx={})
            ctx.project_root = type("X", (), {"__fspath__": lambda self: "."})()
            # Use the actual providers chain via best_hint with no ctx-dep providers.
            from roam.commands.stale_refs_hints import HintProvider

            providers: list[HintProvider] = [custom]
            result = best_hint("vendor/docs/missing.md", ctx, providers=providers)
            assert result is not None
            assert result.target == "vendor/docs/intro.md"
            assert result.source == "custom"
            # Idempotency check.
            register_hint_provider(custom)
            assert _EXTRA_PROVIDERS.count(custom) == 1
        finally:
            if custom in _EXTRA_PROVIDERS:
                _EXTRA_PROVIDERS.remove(custom)

    def test_best_hint_skips_recoverable_provider_failures(self, tmp_path):
        from roam.commands.stale_refs_hints import Hint, HintContext, best_hint

        class _Flaky:
            def hint(self, missing_rel, ctx):
                raise OSError("provider cache unavailable")

        class _Custom:
            def hint(self, missing_rel, ctx):
                return Hint(
                    target="docs/intro.md",
                    confidence="MEDIUM",
                    reason="fallback provider",
                    source="custom",
                )

        ctx = HintContext(project_root=tmp_path, basename_idx={})
        result = best_hint("docs/missing.md", ctx, providers=[_Flaky(), _Custom()])
        assert result is not None
        assert result.target == "docs/intro.md"

    def test_best_hint_propagates_programming_errors(self, tmp_path):
        from roam.commands.stale_refs_hints import HintContext, best_hint

        class _Broken:
            def hint(self, missing_rel, ctx):
                raise TypeError("bad provider contract")

        ctx = HintContext(project_root=tmp_path, basename_idx={})
        with pytest.raises(TypeError):
            best_hint("docs/missing.md", ctx, providers=[_Broken()])

    def test_attest_with_sarif_writes_both_artifacts(self, cli_runner, dangling_project, tmp_path):
        """Dogfood-2 regression: ``--sarif`` previously short-circuited
        before the attest writer ran. Both must produce output now."""
        import json as _json

        out = tmp_path / "ci.intoto.json"
        result = invoke_cli(
            cli_runner,
            ["--sarif", "stale-refs", "--attest", str(out)],
            cwd=dangling_project,
        )
        assert result.exit_code == 0
        # SARIF on stdout.
        sarif = _json.loads(result.output)
        assert sarif["version"] == "2.1.0"
        # Attestation on disk.
        assert out.exists()
        statement = _json.loads(out.read_text(encoding="utf-8"))
        assert statement["predicateType"] == "https://roam-code.com/StaleRefs/v1"

    def test_attest_with_gate_returns_5_and_still_writes(self, cli_runner, dangling_project, tmp_path):
        """2A: ``--attest`` writes the statement BEFORE the gate exit."""
        out_path = tmp_path / "attest.intoto.json"
        result = invoke_cli(
            cli_runner,
            ["stale-refs", "--gate", "--attest", str(out_path)],
            cwd=dangling_project,
        )
        # Gate fires because there are dangling refs.
        assert result.exit_code == 5
        # Attestation must have landed despite the non-zero exit.
        assert out_path.exists()
        import json as _json

        statement = _json.loads(out_path.read_text(encoding="utf-8"))
        assert statement["predicateType"] == "https://roam-code.com/StaleRefs/v1"

    def test_config_ignore_composes_with_fix_preview(self, cli_runner, dangling_project):
        """2A: Config-loaded ``ignore`` list is honoured by --fix preview, not just --json."""
        config_path = dangling_project / ".roam" / "stale-refs.toml"
        config_path.parent.mkdir(parents=True, exist_ok=True)
        config_path.write_text(
            'ignore = ["README.md", "docs/site.html", "docs/index.md"]\n',
            encoding="utf-8",
        )
        result = invoke_cli(
            cli_runner,
            ["stale-refs", "--fix", "preview"],
            cwd=dangling_project,
        )
        assert result.exit_code == 0
        # Every source file is ignored by the config — no edits to plan.
        assert "no edits planned" in result.output.lower() or "0 edit" in result.output

    # ---- Phase 2C: performance + footprint audit -----------------------

    def test_attest_does_not_balloon_scan_time(self, cli_runner, dangling_project):
        """2C: --attest should add at most a few hundred ms to a small
        scan (it's just a JSON serialisation)."""
        import time as _t

        baseline_start = _t.monotonic()
        baseline = invoke_cli(cli_runner, ["stale-refs"], cwd=dangling_project)
        baseline_dur = _t.monotonic() - baseline_start

        attested_start = _t.monotonic()
        out_path = dangling_project / ".roam" / "attest.intoto.json"
        attested = invoke_cli(
            cli_runner,
            ["stale-refs", "--attest", str(out_path)],
            cwd=dangling_project,
        )
        attested_dur = _t.monotonic() - attested_start

        assert baseline.exit_code == 0
        assert attested.exit_code == 0
        # Attest path adds JSON serialisation + atomic write — should be
        # <0.5s on top of the baseline scan, even on slow CI runners.
        # The check is loose because timing on Windows pytest harness is
        # noisy; tightening it would just produce flake.
        assert attested_dur - baseline_dur < 1.0, (
            f"--attest added {attested_dur - baseline_dur:.2f}s vs baseline; regression candidate"
        )

    # ---- Phase 3C: edge cases ------------------------------------------

    def test_init_on_empty_repo(self, cli_runner, tmp_path):
        """3C: --init on a repo with nothing but a .gitignore still works."""
        proj = tmp_path / "empty_repo"
        proj.mkdir()
        (proj / ".gitignore").write_text(".roam/\n")
        git_init(proj)
        result = invoke_cli(cli_runner, ["stale-refs", "--init"], cwd=proj)
        assert result.exit_code == 0
        cfg = (proj / ".roam" / "stale-refs.toml").read_text(encoding="utf-8")
        assert "sort_by" in cfg
        # Empty repo → no CHANGELOG/legacy → bare suggestion.
        assert "CHANGELOG.md" not in cfg

    def test_config_sort_by_is_honoured(self, cli_runner, dangling_project):
        """4B fix: ``sort_by`` from .roam/stale-refs.toml flows to the runtime."""
        cfg = dangling_project / ".roam" / "stale-refs.toml"
        cfg.parent.mkdir(parents=True, exist_ok=True)
        cfg.write_text('sort_by = "alpha"\n', encoding="utf-8")
        result = invoke_cli(cli_runner, ["stale-refs"], cwd=dangling_project, json_mode=True)
        assert result.exit_code == 0
        data = parse_json_output(result)
        assert data["summary"]["sort_by"] == "alpha"

    def test_config_limit_is_honoured(self, cli_runner, dangling_project):
        """4B fix: ``limit`` from config flows to the runtime."""
        cfg = dangling_project / ".roam" / "stale-refs.toml"
        cfg.parent.mkdir(parents=True, exist_ok=True)
        cfg.write_text("limit = 1\n", encoding="utf-8")
        result = invoke_cli(cli_runner, ["stale-refs"], cwd=dangling_project, json_mode=True)
        data = parse_json_output(result)
        assert data["summary"]["displayed"] == 1

    def test_config_with_unicode_paths(self, cli_runner, tmp_path):
        """3C: Unicode source files in ignore list are matched correctly."""
        proj = tmp_path / "unicode_repo"
        proj.mkdir()
        (proj / ".gitignore").write_text(".roam/\n")
        (proj / "Λ-greek.md").write_text("[broken](missing.md)\n")
        git_init(proj)
        cfg = proj / ".roam" / "stale-refs.toml"
        cfg.parent.mkdir(parents=True, exist_ok=True)
        cfg.write_text('ignore = ["Λ-greek.md"]\n', encoding="utf-8")
        result = invoke_cli(cli_runner, ["stale-refs"], cwd=proj, json_mode=True)
        assert result.exit_code == 0
        data = parse_json_output(result)
        assert data["summary"]["stale_refs"] == 0

    def test_attest_on_clean_repo_emits_zero_findings(self, cli_runner, clean_project, tmp_path):
        """3C: A clean repo still produces a valid attestation."""
        import json as _json

        out = tmp_path / "clean.intoto.json"
        result = invoke_cli(
            cli_runner,
            ["stale-refs", "--attest", str(out)],
            cwd=clean_project,
        )
        assert result.exit_code == 0
        statement = _json.loads(out.read_text(encoding="utf-8"))
        assert statement["predicate"]["scan_summary"]["stale_refs"] == 0
        assert statement["predicate"]["targets"] == []

    # ---- Phase 3B: cross-feature integration ---------------------------

    def test_init_then_scan_reads_generated_config(self, cli_runner, tmp_path):
        """3B: ``--init`` followed by a real scan reads the just-written config."""
        proj = tmp_path / "init_then_scan"
        proj.mkdir()
        (proj / ".gitignore").write_text(".roam/\n")
        (proj / "CHANGELOG.md").write_text("# CHANGELOG\n[broken](docs/old.md)\n")
        (proj / "README.md").write_text("ok\n")
        git_init(proj)

        # Step 1: init writes the suggested config (CHANGELOG.md → ignore).
        init_result = invoke_cli(cli_runner, ["stale-refs", "--init"], cwd=proj)
        assert init_result.exit_code == 0
        cfg = (proj / ".roam" / "stale-refs.toml").read_text(encoding="utf-8")
        assert "CHANGELOG.md" in cfg

        # Step 2: scan honours the config — CHANGELOG.md's broken link is suppressed.
        scan_result = invoke_cli(cli_runner, ["stale-refs"], cwd=proj, json_mode=True)
        assert scan_result.exit_code == 0
        data = parse_json_output(scan_result)
        # CHANGELOG.md was the only source — should be ignored now.
        assert data["summary"]["stale_refs"] == 0

    def test_attest_plus_json_mode_keeps_both_outputs_consistent(self, cli_runner, dangling_project, tmp_path):
        """3B: When --attest and --json are both used, the attestation
        on disk and the JSON envelope on stdout report the same verdict."""
        import json as _json

        out = tmp_path / "scan.intoto.json"
        result = invoke_cli(
            cli_runner,
            ["stale-refs", "--attest", str(out)],
            cwd=dangling_project,
            json_mode=True,
        )
        assert result.exit_code == 0
        envelope = parse_json_output(result)
        statement = _json.loads(out.read_text(encoding="utf-8"))
        # Verdict text is identical in both artefacts.
        assert envelope["summary"]["verdict"] == statement["predicate"]["scan_summary"]["verdict"]
        # Counts also match.
        assert envelope["summary"]["stale_refs"] == statement["predicate"]["scan_summary"]["stale_refs"]

    def test_root_override_plus_attest_writes_relative_to_cwd(self, cli_runner, tmp_path):
        """3B: ``--root`` doesn't accidentally redirect ``--attest`` PATH —
        the attest path is resolved against the user's cwd, not the
        scan target. Otherwise CI artefact upload paths would silently
        change."""
        scan_target = tmp_path / "monorepo_subtree"
        scan_target.mkdir()
        (scan_target / ".gitignore").write_text(".roam/\n")
        (scan_target / "README.md").write_text("[broken](missing.md)\n")
        git_init(scan_target)

        cwd = tmp_path / "ci_workspace"
        cwd.mkdir()
        git_init(cwd)
        out_path = cwd / "artifacts" / "stale.intoto.json"
        out_path.parent.mkdir()

        result = invoke_cli(
            cli_runner,
            [
                "stale-refs",
                "--root",
                str(scan_target),
                "--attest",
                str(out_path),
            ],
            cwd=cwd,
        )
        assert result.exit_code == 0
        # The attest landed in cwd's artifacts/, NOT in scan_target/.
        assert out_path.exists()
        assert not (scan_target / "artifacts").exists()

    def test_root_override_does_not_double_scan(self, cli_runner, tmp_path):
        """2C: --root must not accidentally scan TWICE (once for find_project_root,
        once for the override). This regression would manifest as a 2x slowdown."""
        scan_target = tmp_path / "perf_target"
        scan_target.mkdir()
        (scan_target / ".gitignore").write_text(".roam/\n")
        for i in range(20):
            (scan_target / f"file_{i}.md").write_text(f"[broken](missing-{i}.md)\n")
        git_init(scan_target)
        result = invoke_cli(
            cli_runner,
            ["stale-refs", "--root", str(scan_target), "--json"],
            cwd=tmp_path,
        )
        assert result.exit_code == 0
        data = parse_json_output(result)
        # files_scanned should be the count we wrote (excluding .gitignore).
        # Anything significantly higher would indicate a duplicate walk.
        assert data["summary"]["files_scanned"] <= 25

    # ---- Phase 2B: failure-mode audit --------------------------------

    def test_corrupt_config_does_not_crash(self, cli_runner, dangling_project):
        """2B: A malformed TOML config is silently ignored — no crash."""
        cfg = dangling_project / ".roam" / "stale-refs.toml"
        cfg.parent.mkdir(parents=True, exist_ok=True)
        cfg.write_text("this is not = valid [toml at all\n!!!", encoding="utf-8")
        result = invoke_cli(cli_runner, ["stale-refs"], cwd=dangling_project)
        # No crash. Verdict still emitted. Just no config defaults applied.
        assert result.exit_code == 0
        assert "VERDICT" in result.output

    def test_init_force_with_root_override(self, cli_runner, tmp_path):
        """2B: --init-force on --root overwrites only the targeted root's config."""
        scan_target = tmp_path / "target"
        scan_target.mkdir()
        git_init(scan_target)
        cfg = scan_target / ".roam" / "stale-refs.toml"
        cfg.parent.mkdir(parents=True, exist_ok=True)
        cfg.write_text('ignore = ["legacy"]\n', encoding="utf-8")

        result = invoke_cli(
            cli_runner,
            ["stale-refs", "--root", str(scan_target), "--init", "--init-force"],
            cwd=tmp_path,
        )
        assert result.exit_code == 0
        new_text = cfg.read_text(encoding="utf-8")
        # Legacy config is gone; suggested defaults are now there.
        assert "legacy" not in new_text
        assert "sort_by" in new_text

    def test_attest_path_with_unwritable_parent_falls_through(self, cli_runner, dangling_project):
        """W126: ``--attest`` must not crash the scan when the target
        directory cannot be created (parent is a file / no perms / etc.).

        The scan still completed successfully — only the side-channel
        attestation write failed. The fix surfaces the failure as
        structured state (``summary.attest_status == "failed"`` plus
        ``summary.attest_error``) and lets the scan fall through cleanly
        per this test's name.

        History: prior to W126 this test pinned the crash (asserted
        ``exit_code != 0``); the docstring admitted the test was
        documenting the bug, not the contract. W126 inverts the
        assertion to match the test's name.
        """
        # Pass an attestation path whose parent contains an existing
        # FILE (not a directory), so mkdir(parents=True) would raise.
        bad = dangling_project / "blocker"
        bad.write_text("i am a file not a directory\n")
        result = invoke_cli(
            cli_runner,
            ["stale-refs", "--attest", str(bad / "child" / "out.json")],
            cwd=dangling_project,
            json_mode=True,
        )
        # Fall-through: scan completes. Exit 0 (no --gate so no
        # promotion to EXIT_PARTIAL).
        assert result.exit_code == 0, f"expected fall-through, got exit {result.exit_code}:\n{result.output}"
        # Structured error surfaced in the JSON envelope.
        data = parse_json_output(result)
        summary = data["summary"]
        assert summary.get("attest_status") == "failed", summary
        assert "attest_error" in summary, summary
        assert "could not create" in summary["attest_error"].lower()
        # The scan itself completed: a real verdict, not the attest error.
        assert isinstance(summary.get("verdict"), str) and summary["verdict"]
        # And we still have the scan-level fields.
        assert "stale_refs" in summary
        assert "files_scanned" in summary

    def test_config_loaded_through_root_override(self, cli_runner, tmp_path):
        """2A: --root + .roam/stale-refs.toml is read from the OVERRIDDEN
        project root, not from the current working dir.

        Bug class this guards against: a future regression that calls
        ``find_project_root()`` somewhere down the chain after the
        override has been applied — the config would silently load
        from the wrong path.
        """
        scan_target = tmp_path / "scan_target"
        scan_target.mkdir()
        (scan_target / ".gitignore").write_text(".roam/\n")
        (scan_target / "README.md").write_text("[broken](missing.md)\n")
        git_init(scan_target)
        cfg = scan_target / ".roam" / "stale-refs.toml"
        cfg.parent.mkdir(parents=True, exist_ok=True)
        cfg.write_text('ignore = ["README.md"]\n', encoding="utf-8")

        invoke_from = tmp_path / "elsewhere2"
        invoke_from.mkdir()
        git_init(invoke_from)

        result = invoke_cli(
            cli_runner,
            ["stale-refs", "--root", str(scan_target)],
            cwd=invoke_from,
            json_mode=True,
        )
        data = parse_json_output(result)
        # README.md was the only source; the override config ignored it.
        assert data["summary"]["stale_refs"] == 0

    def test_serialise_round_trip(self):
        from roam.commands.cmd_stale_refs import (
            _parse_minimal_toml,
            _serialise_config_toml,
        )

        original = {
            "sort_by": "priority",
            "ignore": ["a.md", "b/c.md"],
            "check_external": True,
        }
        text = _serialise_config_toml(original)
        # Roundtrip via the minimal parser
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "x.toml"
            p.write_text(text, encoding="utf-8")
            parsed = _parse_minimal_toml(p)
        assert parsed["sort_by"] == "priority"
        assert parsed["ignore"] == ["a.md", "b/c.md"]
        assert parsed["check_external"] is True
