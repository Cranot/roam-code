"""Tests for file exclusion patterns (.roamignore, config.json, built-in)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from conftest import git_init, invoke_cli

from roam.index.discovery import (
    BUILTIN_GENERATED_PATTERNS,
    _is_generated_content,
    _load_config_excludes,
    _load_roamignore,
    _matches_exclude,
    discover_files,
    load_exclude_patterns,
)
from roam.index.gitignore import matches_exclude_patterns, matches_gitignore

# -----------------------------------------------------------------------
# 1. Loading patterns from .roamignore
# -----------------------------------------------------------------------


class TestLoadRoamignore:
    """Loading and parsing .roamignore files."""

    def test_missing_roamignore_returns_empty(self, tmp_path):
        """A missing .roamignore file returns an empty list."""
        assert _load_roamignore(tmp_path) == []

    def test_empty_roamignore_returns_empty(self, tmp_path):
        """An empty .roamignore file returns an empty list."""
        (tmp_path / ".roamignore").write_text("")
        assert _load_roamignore(tmp_path) == []

    def test_comment_lines_ignored(self, tmp_path):
        """Lines starting with # are comments and should be ignored."""
        (tmp_path / ".roamignore").write_text("# This is a comment\n*.pb.go\n# Another comment\ngenerated/\n")
        patterns = _load_roamignore(tmp_path)
        assert patterns == ["*.pb.go", "generated/"]

    def test_blank_lines_ignored(self, tmp_path):
        """Blank lines should be ignored."""
        (tmp_path / ".roamignore").write_text("\n*.pb.go\n\n  \ngenerated/\n\n")
        patterns = _load_roamignore(tmp_path)
        assert patterns == ["*.pb.go", "generated/"]

    def test_whitespace_stripped(self, tmp_path):
        """Leading and trailing whitespace should be stripped."""
        (tmp_path / ".roamignore").write_text("  *.pb.go  \n\t generated/ \n")
        patterns = _load_roamignore(tmp_path)
        assert patterns == ["*.pb.go", "generated/"]

    def test_multiple_patterns_loaded(self, tmp_path):
        """Multiple patterns are loaded in order."""
        (tmp_path / ".roamignore").write_text("*.generated.*\n*_pb2.py\nvendor/\ndist/**\n")
        patterns = _load_roamignore(tmp_path)
        assert len(patterns) == 4
        assert "*_pb2.py" in patterns


# -----------------------------------------------------------------------
# 2. Loading patterns from config.json
# -----------------------------------------------------------------------


class TestLoadConfigExcludes:
    """Loading exclude patterns from .roam/config.json."""

    def test_missing_config_returns_empty(self, tmp_path):
        """A missing config.json returns an empty list."""
        assert _load_config_excludes(tmp_path) == []

    def test_config_without_exclude_key(self, tmp_path):
        """Config without 'exclude' key returns an empty list."""
        roam_dir = tmp_path / ".roam"
        roam_dir.mkdir()
        (roam_dir / "config.json").write_text('{"db_dir": "/tmp/db"}')
        assert _load_config_excludes(tmp_path) == []

    def test_config_with_exclude_array(self, tmp_path):
        """Config with 'exclude' array returns the patterns."""
        roam_dir = tmp_path / ".roam"
        roam_dir.mkdir()
        (roam_dir / "config.json").write_text(json.dumps({"exclude": ["*.pb.go", "generated/**"]}))
        patterns = _load_config_excludes(tmp_path)
        assert patterns == ["*.pb.go", "generated/**"]

    def test_config_with_empty_exclude(self, tmp_path):
        """Config with empty 'exclude' array returns empty list."""
        roam_dir = tmp_path / ".roam"
        roam_dir.mkdir()
        (roam_dir / "config.json").write_text(json.dumps({"exclude": []}))
        assert _load_config_excludes(tmp_path) == []

    def test_malformed_config_returns_empty(self, tmp_path):
        """Malformed JSON in config.json returns an empty list gracefully."""
        roam_dir = tmp_path / ".roam"
        roam_dir.mkdir()
        (roam_dir / "config.json").write_text("{not valid json")
        assert _load_config_excludes(tmp_path) == []

    def test_config_exclude_non_list_returns_empty(self, tmp_path):
        """If exclude is not a list, return empty."""
        roam_dir = tmp_path / ".roam"
        roam_dir.mkdir()
        (roam_dir / "config.json").write_text(json.dumps({"exclude": "*.pb.go"}))
        assert _load_config_excludes(tmp_path) == []


# -----------------------------------------------------------------------
# 3. Combined load_exclude_patterns
# -----------------------------------------------------------------------


class TestLoadExcludePatterns:
    """load_exclude_patterns merges all sources."""

    def test_builtin_patterns_always_included(self, tmp_path):
        """Built-in generated file patterns are always present."""
        patterns = load_exclude_patterns(tmp_path)
        for builtin in BUILTIN_GENERATED_PATTERNS:
            assert builtin in patterns

    def test_roamignore_plus_builtin(self, tmp_path):
        """Patterns from .roamignore are included alongside built-in ones."""
        (tmp_path / ".roamignore").write_text("my_custom_pattern.*\n")
        patterns = load_exclude_patterns(tmp_path)
        assert "my_custom_pattern.*" in patterns
        assert "*_pb2.py" in patterns  # built-in

    def test_config_plus_builtin(self, tmp_path):
        """Patterns from config.json are included alongside built-in ones."""
        roam_dir = tmp_path / ".roam"
        roam_dir.mkdir()
        (roam_dir / "config.json").write_text(json.dumps({"exclude": ["my_config_pattern.*"]}))
        patterns = load_exclude_patterns(tmp_path)
        assert "my_config_pattern.*" in patterns
        assert "*_pb2.py" in patterns  # built-in

    def test_deduplication(self, tmp_path):
        """Duplicate patterns across sources are deduplicated."""
        (tmp_path / ".roamignore").write_text("*_pb2.py\n")
        patterns = load_exclude_patterns(tmp_path)
        # *_pb2.py is both in .roamignore and built-in -- should appear once
        assert patterns.count("*_pb2.py") == 1


# -----------------------------------------------------------------------
# 4. Glob pattern matching
# -----------------------------------------------------------------------


class TestMatchesExclude:
    """_matches_exclude pattern matching."""

    def test_filename_glob_match(self):
        """Filename patterns match against the basename."""
        assert _matches_exclude("src/models_pb2.py", ["*_pb2.py"])

    def test_full_path_glob_match(self):
        """Full path patterns match against the full relative path."""
        assert _matches_exclude("src/generated/api.py", ["src/generated/*"])

    def test_directory_prefix_match(self):
        """Directory prefix patterns with trailing slash match subpaths."""
        assert _matches_exclude("vendor/pkg/main.go", ["vendor/"])

    def test_no_match_returns_false(self):
        """Non-matching paths return False."""
        assert not _matches_exclude("src/app.py", ["*_pb2.py", "vendor/"])

    def test_generated_extension_pattern(self):
        """*.generated.* matches files with .generated. in the name."""
        assert _matches_exclude("lib/schema.generated.ts", ["*.generated.*"])

    def test_auto_extension_pattern(self):
        """*.auto.* matches files with .auto. in the name."""
        assert _matches_exclude("lib/config.auto.js", ["*.auto.*"])

    def test_dart_generated(self):
        """*.g.dart matches Dart generated files."""
        assert _matches_exclude("lib/model.g.dart", ["*.g.dart"])

    def test_protobuf_js(self):
        """*_pb.js matches protobuf JS files."""
        assert _matches_exclude("proto/user_pb.js", ["*_pb.js"])

    def test_grpc_pb_dts(self):
        """*_grpc_pb.d.ts matches gRPC TypeScript definition files."""
        assert _matches_exclude("proto/service_grpc_pb.d.ts", ["*_grpc_pb.d.ts"])


# -----------------------------------------------------------------------
# 5. Content-based generated file detection
# -----------------------------------------------------------------------


class TestGeneratedContent:
    """_is_generated_content detects generated-code markers."""

    def test_go_generated_marker(self, tmp_path):
        """Files with '// Code generated' in first 3 lines are detected."""
        f = tmp_path / "gen.go"
        f.write_text("// Code generated by protoc-gen-go. DO NOT EDIT.\npackage main\n")
        assert _is_generated_content(f)

    def test_python_generated_marker(self, tmp_path):
        """Files with '# Generated by' in first 3 lines are detected."""
        f = tmp_path / "gen.py"
        f.write_text(
            "# -*- coding: utf-8 -*-\n# Generated by the protocol buffer compiler.  DO NOT EDIT!\nimport sys\n"
        )
        assert _is_generated_content(f)

    def test_marker_on_third_line(self, tmp_path):
        """Marker on the third line (within first 3) is still detected."""
        f = tmp_path / "gen.py"
        f.write_text("#!/usr/bin/env python\n# -*- coding: utf-8 -*-\n# Generated by some tool\nimport os\n")
        assert _is_generated_content(f)

    def test_marker_on_fourth_line_not_detected(self, tmp_path):
        """Marker past line 3 should NOT be detected."""
        f = tmp_path / "normal.py"
        f.write_text("#!/usr/bin/env python\n# -*- coding: utf-8 -*-\nimport os\n# Generated by some tool\n")
        assert not _is_generated_content(f)

    def test_normal_file_not_detected(self, tmp_path):
        """Normal source files are not flagged as generated."""
        f = tmp_path / "app.py"
        f.write_text("def main():\n    print('hello')\n")
        assert not _is_generated_content(f)

    def test_missing_file_returns_false(self, tmp_path):
        """Non-existent file returns False gracefully."""
        assert not _is_generated_content(tmp_path / "nonexistent.py")


# -----------------------------------------------------------------------
# 6. discover_files integration
# -----------------------------------------------------------------------


class TestDiscoverFilesExclusion:
    """discover_files respects exclusion patterns end-to-end."""

    def test_roamignore_excludes_files(self, tmp_path):
        """Files matching .roamignore patterns are excluded from discovery."""
        repo = tmp_path / "proj"
        repo.mkdir()
        (repo / ".gitignore").write_text(".roam/\n")
        (repo / ".roamignore").write_text("*_pb2.py\n")
        (repo / "app.py").write_text("def main(): pass\n")
        (repo / "user_pb2.py").write_text("# protobuf stub\n")
        git_init(repo)

        files = discover_files(repo)
        assert "app.py" in files
        assert "user_pb2.py" not in files

    def test_config_excludes_files(self, tmp_path):
        """Files matching config.json exclude patterns are excluded."""
        repo = tmp_path / "proj"
        repo.mkdir()
        (repo / ".gitignore").write_text(".roam/\n")
        (repo / "app.py").write_text("def main(): pass\n")
        (repo / "schema.generated.ts").write_text("export type Foo = {};\n")
        roam_dir = repo / ".roam"
        roam_dir.mkdir()
        (roam_dir / "config.json").write_text(json.dumps({"exclude": ["*.generated.*"]}))
        git_init(repo)

        files = discover_files(repo)
        assert "app.py" in files
        assert "schema.generated.ts" not in files

    def test_builtin_patterns_exclude_protobuf(self, tmp_path):
        """Built-in patterns auto-exclude protobuf stubs."""
        repo = tmp_path / "proj"
        repo.mkdir()
        (repo / ".gitignore").write_text(".roam/\n")
        (repo / "app.py").write_text("def main(): pass\n")
        (repo / "service_pb2_grpc.py").write_text("# grpc stub\n")
        (repo / "api_pb.js").write_text("// protobuf\n")
        (repo / "types_pb.d.ts").write_text("// types\n")
        git_init(repo)

        files = discover_files(repo)
        assert "app.py" in files
        assert "service_pb2_grpc.py" not in files
        assert "api_pb.js" not in files
        assert "types_pb.d.ts" not in files

    def test_content_based_exclusion(self, tmp_path):
        """Files with generated-code markers in first 3 lines are excluded."""
        repo = tmp_path / "proj"
        repo.mkdir()
        (repo / ".gitignore").write_text(".roam/\n")
        (repo / "app.py").write_text("def main(): pass\n")
        (repo / "generated.go").write_text("// Code generated by protoc-gen-go. DO NOT EDIT.\npackage pb\n")
        git_init(repo)

        files = discover_files(repo)
        assert "app.py" in files
        assert "generated.go" not in files

    def test_include_excluded_flag(self, tmp_path):
        """include_excluded=True includes files that would be excluded."""
        repo = tmp_path / "proj"
        repo.mkdir()
        (repo / ".gitignore").write_text(".roam/\n")
        (repo / ".roamignore").write_text("*_pb2.py\n")
        (repo / "app.py").write_text("def main(): pass\n")
        (repo / "user_pb2.py").write_text("# protobuf stub\n")
        git_init(repo)

        # Without flag: excluded
        files = discover_files(repo, include_excluded=False)
        assert "user_pb2.py" not in files

        # With flag: included
        files = discover_files(repo, include_excluded=True)
        assert "user_pb2.py" in files


# -----------------------------------------------------------------------
# 7. CLI config --exclude integration
# -----------------------------------------------------------------------


class TestConfigExcludeCLI:
    """CLI integration for config --exclude."""

    def test_config_show_displays_exclude_patterns(self, tmp_path, cli_runner):
        """roam config --show displays active exclude patterns."""
        repo = tmp_path / "proj"
        repo.mkdir()
        (repo / ".gitignore").write_text(".roam/\n")
        (repo / ".roamignore").write_text("my_pattern.*\n")
        (repo / "app.py").write_text("def main(): pass\n")
        git_init(repo)

        result = invoke_cli(cli_runner, ["config", "--show"], cwd=repo)
        assert result.exit_code == 0
        assert "my_pattern.*" in result.output
        assert "Exclude patterns:" in result.output

    def test_config_exclude_adds_pattern(self, tmp_path, cli_runner):
        """roam config --exclude adds a pattern to config.json."""
        repo = tmp_path / "proj"
        repo.mkdir()
        (repo / ".gitignore").write_text(".roam/\n")
        (repo / "app.py").write_text("def main(): pass\n")
        git_init(repo)

        result = invoke_cli(cli_runner, ["config", "--exclude", "*.proto"], cwd=repo)
        assert result.exit_code == 0
        assert "*.proto" in result.output

        # Verify it was saved
        config_path = repo / ".roam" / "config.json"
        assert config_path.exists()
        config_data = json.loads(config_path.read_text())
        assert "*.proto" in config_data.get("exclude", [])

    def test_config_remove_exclude(self, tmp_path, cli_runner):
        """roam config --remove-exclude removes a pattern from config.json."""
        repo = tmp_path / "proj"
        repo.mkdir()
        (repo / ".gitignore").write_text(".roam/\n")
        (repo / "app.py").write_text("def main(): pass\n")
        roam_dir = repo / ".roam"
        roam_dir.mkdir()
        (roam_dir / "config.json").write_text(json.dumps({"exclude": ["*.proto", "*_pb2.py"]}))
        git_init(repo)

        result = invoke_cli(cli_runner, ["config", "--remove-exclude", "*.proto"], cwd=repo)
        assert result.exit_code == 0
        assert "Removed" in result.output

        config_data = json.loads((roam_dir / "config.json").read_text())
        assert "*.proto" not in config_data.get("exclude", [])
        assert "*_pb2.py" in config_data.get("exclude", [])

    def test_config_show_json_includes_excludes(self, tmp_path, cli_runner):
        """roam --json config --show includes exclude patterns in JSON output."""
        repo = tmp_path / "proj"
        repo.mkdir()
        (repo / ".gitignore").write_text(".roam/\n")
        (repo / ".roamignore").write_text("my_pattern.*\n")
        (repo / "app.py").write_text("def main(): pass\n")
        roam_dir = repo / ".roam"
        roam_dir.mkdir()
        (roam_dir / "config.json").write_text(json.dumps({"exclude": ["config_pattern.*"]}))
        git_init(repo)

        result = invoke_cli(cli_runner, ["config", "--show"], cwd=repo, json_mode=True)
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert "exclude_roamignore" in data
        assert "exclude_config" in data
        assert "exclude_builtin" in data
        assert "exclude_all" in data
        assert "my_pattern.*" in data["exclude_roamignore"]
        assert "config_pattern.*" in data["exclude_config"]


# -----------------------------------------------------------------------
# 8. Gitignore-compatible pattern semantics
# -----------------------------------------------------------------------


class TestGitignoreSemantics:
    """Full gitignore-compatible pattern matching via roam.index.gitignore."""

    # -- Star (*) does NOT cross / boundaries --

    def test_star_matches_single_segment(self):
        """* should match within a single path segment only."""
        assert matches_gitignore("src/app.py", "*.py")
        assert matches_gitignore("app.py", "*.py")

    def test_star_does_not_cross_slash(self):
        """* must not match across / boundaries."""
        # Pattern src/*.py should NOT match src/sub/app.py
        assert not matches_gitignore("src/sub/app.py", "src/*.py")
        assert matches_gitignore("src/app.py", "src/*.py")

    # -- Double-star (**) crosses / boundaries --

    def test_doublestar_recursive_middle(self):
        """src/**/*.py matches files at any depth under src/."""
        assert matches_gitignore("src/app.py", "src/**/*.py")
        assert matches_gitignore("src/sub/deep/app.py", "src/**/*.py")
        assert not matches_gitignore("lib/app.py", "src/**/*.py")

    def test_doublestar_leading(self):
        """**/test.py matches test.py at any depth."""
        assert matches_gitignore("test.py", "**/test.py")
        assert matches_gitignore("src/test.py", "**/test.py")
        assert matches_gitignore("a/b/c/test.py", "**/test.py")

    def test_doublestar_trailing(self):
        """src/** matches everything under src/."""
        assert matches_gitignore("src/app.py", "src/**")
        assert matches_gitignore("src/sub/deep/app.py", "src/**")
        assert not matches_gitignore("lib/app.py", "src/**")

    # -- Root anchoring (leading /) --

    def test_root_anchored_pattern(self):
        """/vendor matches only at root, not nested."""
        assert matches_gitignore("vendor/lib.go", "/vendor/")
        assert not matches_gitignore("src/vendor/lib.go", "/vendor/")

    def test_unanchored_matches_anywhere(self):
        """vendor/ without leading / matches at any depth."""
        assert matches_gitignore("vendor/lib.go", "vendor/")
        assert matches_gitignore("src/vendor/lib.go", "vendor/")

    # -- Implicit anchoring (/ in middle of pattern) --

    def test_implicit_anchoring_with_slash(self):
        """src/generated has a /, so it's implicitly anchored to root."""
        assert matches_gitignore("src/generated/api.py", "src/generated/")
        assert not matches_gitignore("lib/src/generated/api.py", "src/generated/")

    # -- Directory patterns (trailing /) --

    def test_directory_trailing_slash(self):
        """vendor/ matches the dir and everything under it."""
        assert matches_gitignore("vendor/pkg/main.go", "vendor/")
        assert matches_gitignore("vendor/main.go", "vendor/")

    def test_directory_trailing_slash_no_false_positive(self):
        """vendor/ should not match a file named 'vendor' (no trailing content)."""
        # A file exactly named "vendor" with no / after — edge case.
        # In gitignore, trailing / means "only if it is a directory".
        # We match prefix, so "vendor" alone won't have content after /.
        assert not matches_gitignore("vendor_stuff/file.py", "vendor/")

    # -- Negation (!pattern) --

    def test_negation_un_excludes(self):
        """!important.log un-excludes a file matched by *.log."""
        patterns = ["*.log", "!important.log"]
        assert matches_exclude_patterns("debug.log", patterns)
        assert not matches_exclude_patterns("important.log", patterns)

    def test_negation_last_match_wins(self):
        """Last matching pattern wins — re-exclude after negation."""
        patterns = ["*.log", "!important.log", "*.log"]
        assert matches_exclude_patterns("important.log", patterns)

    def test_negation_with_directory(self):
        """Negation works with directory patterns."""
        patterns = ["build/", "!build/keep/"]
        assert matches_exclude_patterns("build/output.js", patterns)
        assert not matches_exclude_patterns("build/keep/important.js", patterns)

    # -- Character classes --

    def test_char_class_positive(self):
        """[abc] matches any of the listed characters."""
        assert matches_gitignore("file_a.txt", "file_[abc].txt")
        assert matches_gitignore("file_b.txt", "file_[abc].txt")
        assert not matches_gitignore("file_d.txt", "file_[abc].txt")

    def test_char_class_negated(self):
        """[!abc] matches any character NOT listed."""
        assert matches_gitignore("file_d.txt", "file_[!abc].txt")
        assert not matches_gitignore("file_a.txt", "file_[!abc].txt")

    # -- Question mark --

    def test_question_mark_single_char(self):
        """? matches exactly one character (not /)."""
        assert matches_gitignore("foo.py", "fo?.py")
        assert not matches_gitignore("fooo.py", "fo?.py")
        assert not matches_gitignore("fo/py", "fo?.py")

    # -- Backward compatibility with existing patterns --

    def test_compat_pb2_py(self):
        """*_pb2.py still matches protobuf stubs."""
        assert matches_gitignore("src/models_pb2.py", "*_pb2.py")
        assert matches_gitignore("models_pb2.py", "*_pb2.py")

    def test_compat_generated_extension(self):
        """*.generated.* still matches generated files."""
        assert matches_gitignore("lib/schema.generated.ts", "*.generated.*")

    def test_compat_vendor_directory(self):
        """vendor/ still matches vendor directory contents."""
        assert matches_gitignore("vendor/pkg/main.go", "vendor/")

    def test_compat_src_generated_star(self):
        """src/generated/* matches files directly in src/generated/."""
        assert matches_gitignore("src/generated/api.py", "src/generated/*")
        # * doesn't cross /, so nested won't match
        assert not matches_gitignore("src/generated/sub/api.py", "src/generated/*")

    # -- Comments and blanks in pattern lists --

    def test_comments_and_blanks_ignored(self):
        """# lines and empty strings are skipped in pattern lists."""
        patterns = ["# comment", "", "*.log", "  ", "# another"]
        assert matches_exclude_patterns("debug.log", patterns)
        assert not matches_exclude_patterns("app.py", patterns)


# -----------------------------------------------------------------------
# 9. Gitignore integration with discover_files
# -----------------------------------------------------------------------


class TestGitignoreDiscoverIntegration:
    """Integration tests: gitignore patterns with discover_files."""

    def test_negation_in_roamignore(self, tmp_path):
        """!pattern in .roamignore un-excludes a file."""
        repo = tmp_path / "proj"
        repo.mkdir()
        (repo / ".gitignore").write_text(".roam/\n")
        # Exclude all .log but keep important.log
        (repo / ".roamignore").write_text("*.log\n!important.log\n")
        (repo / "app.py").write_text("def main(): pass\n")
        (repo / "debug.log").write_text("debug info\n")
        (repo / "important.log").write_text("important info\n")
        git_init(repo)

        files = discover_files(repo)
        assert "app.py" in files
        assert "debug.log" not in files
        assert "important.log" in files

    def test_root_anchored_in_roamignore(self, tmp_path):
        """Leading / in .roamignore anchors to repo root."""
        repo = tmp_path / "proj"
        repo.mkdir()
        (repo / ".gitignore").write_text(".roam/\n")
        # /build/ only matches root-level build/
        (repo / ".roamignore").write_text("/build/\n")
        (repo / "app.py").write_text("def main(): pass\n")
        build_dir = repo / "build"
        build_dir.mkdir()
        (build_dir / "output.js").write_text("// compiled\n")
        src_build = repo / "src" / "build"
        src_build.mkdir(parents=True)
        (src_build / "helper.py").write_text("def help(): pass\n")
        git_init(repo)

        files = discover_files(repo)
        assert "app.py" in files
        assert "build/output.js" not in files
        # src/build/ should NOT be excluded (root-anchored)
        assert "src/build/helper.py" in files

    def test_star_doesnt_cross_slash_in_discovery(self, tmp_path):
        """* in .roamignore should not cross / boundaries."""
        repo = tmp_path / "proj"
        repo.mkdir()
        (repo / ".gitignore").write_text(".roam/\n")
        (repo / ".roamignore").write_text("src/*.tmp\n")
        (repo / "app.py").write_text("def main(): pass\n")
        src = repo / "src"
        src.mkdir()
        (src / "cache.tmp").write_text("temp\n")
        sub = src / "sub"
        sub.mkdir()
        (sub / "deep.tmp").write_text("temp\n")
        git_init(repo)

        files = discover_files(repo)
        assert "app.py" in files
        assert "src/cache.tmp" not in files
        # src/sub/deep.tmp should NOT be excluded (star doesn't cross /)
        assert "src/sub/deep.tmp" in files
