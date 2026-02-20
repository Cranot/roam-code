"""Tests for file exclusion patterns (.roamignore, config.json, built-in)."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
from conftest import git_init, git_commit, index_in_process, invoke_cli

from roam.index.discovery import (
    BUILTIN_GENERATED_PATTERNS,
    _load_roamignore,
    _load_config_excludes,
    _matches_exclude,
    _is_generated_content,
    load_exclude_patterns,
    discover_files,
)


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
        (tmp_path / ".roamignore").write_text(
            "# This is a comment\n"
            "*.pb.go\n"
            "# Another comment\n"
            "generated/\n"
        )
        patterns = _load_roamignore(tmp_path)
        assert patterns == ["*.pb.go", "generated/"]

    def test_blank_lines_ignored(self, tmp_path):
        """Blank lines should be ignored."""
        (tmp_path / ".roamignore").write_text(
            "\n"
            "*.pb.go\n"
            "\n"
            "  \n"
            "generated/\n"
            "\n"
        )
        patterns = _load_roamignore(tmp_path)
        assert patterns == ["*.pb.go", "generated/"]

    def test_whitespace_stripped(self, tmp_path):
        """Leading and trailing whitespace should be stripped."""
        (tmp_path / ".roamignore").write_text(
            "  *.pb.go  \n"
            "\t generated/ \n"
        )
        patterns = _load_roamignore(tmp_path)
        assert patterns == ["*.pb.go", "generated/"]

    def test_multiple_patterns_loaded(self, tmp_path):
        """Multiple patterns are loaded in order."""
        (tmp_path / ".roamignore").write_text(
            "*.generated.*\n"
            "*_pb2.py\n"
            "vendor/\n"
            "dist/**\n"
        )
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
        (roam_dir / "config.json").write_text(json.dumps({
            "exclude": ["*.pb.go", "generated/**"]
        }))
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
        (roam_dir / "config.json").write_text(json.dumps({
            "exclude": ["my_config_pattern.*"]
        }))
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
        f.write_text(
            "// Code generated by protoc-gen-go. DO NOT EDIT.\n"
            "package main\n"
        )
        assert _is_generated_content(f)

    def test_python_generated_marker(self, tmp_path):
        """Files with '# Generated by' in first 3 lines are detected."""
        f = tmp_path / "gen.py"
        f.write_text(
            "# -*- coding: utf-8 -*-\n"
            "# Generated by the protocol buffer compiler.  DO NOT EDIT!\n"
            "import sys\n"
        )
        assert _is_generated_content(f)

    def test_marker_on_third_line(self, tmp_path):
        """Marker on the third line (within first 3) is still detected."""
        f = tmp_path / "gen.py"
        f.write_text(
            "#!/usr/bin/env python\n"
            "# -*- coding: utf-8 -*-\n"
            "# Generated by some tool\n"
            "import os\n"
        )
        assert _is_generated_content(f)

    def test_marker_on_fourth_line_not_detected(self, tmp_path):
        """Marker past line 3 should NOT be detected."""
        f = tmp_path / "normal.py"
        f.write_text(
            "#!/usr/bin/env python\n"
            "# -*- coding: utf-8 -*-\n"
            "import os\n"
            "# Generated by some tool\n"
        )
        assert not _is_generated_content(f)

    def test_normal_file_not_detected(self, tmp_path):
        """Normal source files are not flagged as generated."""
        f = tmp_path / "app.py"
        f.write_text(
            "def main():\n"
            "    print('hello')\n"
        )
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
        (roam_dir / "config.json").write_text(json.dumps({
            "exclude": ["*.generated.*"]
        }))
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
        (repo / "generated.go").write_text(
            "// Code generated by protoc-gen-go. DO NOT EDIT.\n"
            "package pb\n"
        )
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
        (roam_dir / "config.json").write_text(json.dumps({
            "exclude": ["*.proto", "*_pb2.py"]
        }))
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
        (roam_dir / "config.json").write_text(json.dumps({
            "exclude": ["config_pattern.*"]
        }))
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
