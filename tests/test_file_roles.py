"""Tests for roam.index.file_roles — three-tier file role classifier."""

from __future__ import annotations

import pytest

from roam.index.file_roles import (
    ALL_ROLES,
    classify_file,
    is_generated,
    is_source,
    is_test,
    is_vendored,
)


# -----------------------------------------------------------------------
# Tier 1 — Path-based classification
# -----------------------------------------------------------------------


class TestTier1PathBased:
    """Tier 1: directory path patterns."""

    # CI directories
    def test_github_workflow(self):
        assert classify_file(".github/workflows/ci.yml") == "ci"

    def test_circleci_config(self):
        assert classify_file(".circleci/config.yml") == "ci"

    def test_gitlab_ci_dir(self):
        assert classify_file(".gitlab-ci/deploy.yml") == "ci"

    # Vendored directories
    def test_vendor_dir(self):
        assert classify_file("vendor/github.com/pkg/errors/errors.go") == "vendored"

    def test_node_modules(self):
        assert classify_file("node_modules/lodash/index.js") == "vendored"

    def test_third_party(self):
        assert classify_file("third_party/abseil/absl/base/log.cc") == "vendored"

    def test_external_dir(self):
        assert classify_file("external/grpc/src/main.cc") == "vendored"

    # Test directories
    def test_tests_dir(self):
        assert classify_file("tests/test_core.py") == "test"

    def test_test_dir(self):
        assert classify_file("test/helpers.js") == "test"

    def test_dunder_tests_dir(self):
        assert classify_file("src/__tests__/App.test.tsx") == "test"

    def test_spec_dir(self):
        assert classify_file("spec/models/user_spec.rb") == "test"

    # Docs directories
    def test_docs_dir(self):
        assert classify_file("docs/getting-started.md") == "docs"

    def test_doc_dir(self):
        assert classify_file("doc/api.rst") == "docs"

    # Examples directories
    def test_examples_dir(self):
        assert classify_file("examples/hello.py") == "examples"

    def test_samples_dir(self):
        assert classify_file("samples/demo.js") == "examples"

    # Scripts directories
    def test_scripts_dir(self):
        assert classify_file("scripts/deploy.sh") == "scripts"

    def test_bin_dir(self):
        assert classify_file("bin/run") == "scripts"

    # Build directories
    def test_build_dir(self):
        assert classify_file("build/output/bundle.js") == "build"

    def test_dist_dir(self):
        assert classify_file("dist/index.js") == "build"

    def test_target_dir(self):
        assert classify_file("target/release/main") == "build"


# -----------------------------------------------------------------------
# Tier 2 — Filename-based classification
# -----------------------------------------------------------------------


class TestTier2FilenameBased:
    """Tier 2: filename and extension patterns."""

    # Build tool files
    def test_makefile(self):
        assert classify_file("Makefile") == "build"

    def test_dockerfile(self):
        assert classify_file("Dockerfile") == "build"

    def test_webpack_config(self):
        assert classify_file("webpack.config.js") == "build"

    def test_vite_config(self):
        assert classify_file("vite.config.ts") == "build"

    def test_cmakelists(self):
        assert classify_file("CMakeLists.txt") == "build"

    # Docs filenames
    def test_readme(self):
        assert classify_file("README.md") == "docs"

    def test_license(self):
        assert classify_file("LICENSE") == "docs"

    def test_changelog(self):
        assert classify_file("CHANGELOG.md") == "docs"

    # Config filenames
    def test_gitignore(self):
        assert classify_file(".gitignore") == "config"

    def test_package_json(self):
        assert classify_file("package.json") == "config"

    def test_pyproject_toml(self):
        assert classify_file("pyproject.toml") == "config"

    def test_cargo_toml(self):
        assert classify_file("Cargo.toml") == "config"

    # Config by extension
    def test_yaml_config(self):
        assert classify_file("settings.yaml") == "config"

    def test_ini_config(self):
        assert classify_file("app.ini") == "config"

    # Test filename patterns
    def test_python_test_prefix(self):
        assert classify_file("test_foo.py") == "test"

    def test_python_test_suffix(self):
        assert classify_file("foo_test.py") == "test"

    def test_go_test_suffix(self):
        assert classify_file("handler_test.go") == "test"

    def test_js_spec(self):
        assert classify_file("foo.spec.tsx") == "test"

    def test_js_test(self):
        assert classify_file("app.test.js") == "test"

    def test_java_test(self):
        assert classify_file("UserTest.java") == "test"

    def test_ruby_spec(self):
        assert classify_file("user_spec.rb") == "test"

    def test_conftest(self):
        assert classify_file("conftest.py") == "test"

    # Generated filename patterns
    def test_pb_go(self):
        assert classify_file("service.pb.go") == "generated"

    def test_pb2_py(self):
        assert classify_file("service_pb2.py") == "generated"

    def test_min_js(self):
        assert classify_file("bundle.min.js") == "generated"

    def test_min_css(self):
        assert classify_file("styles.min.css") == "generated"

    def test_generated_ext(self):
        assert classify_file("schema.generated.ts") == "generated"

    # CI filename patterns
    def test_travis_yml(self):
        assert classify_file(".travis.yml") == "ci"

    def test_gitlab_ci_file(self):
        assert classify_file(".gitlab-ci.yml") == "ci"

    # Data extensions
    def test_png(self):
        assert classify_file("logo.png") == "data"

    def test_csv(self):
        assert classify_file("results.csv") == "data"

    def test_wasm(self):
        assert classify_file("module.wasm") == "data"

    # Lock files as config
    def test_lock_file(self):
        assert classify_file("Gemfile.lock") == "config"

    def test_package_lock(self):
        assert classify_file("package-lock.json") == "config"

    # Doc extensions
    def test_rst_file(self):
        assert classify_file("guide.rst") == "docs"

    # Source fallback
    def test_plain_python_source(self):
        assert classify_file("src/main.py") == "source"

    def test_plain_go_source(self):
        assert classify_file("pkg/server.go") == "source"

    def test_plain_rust_source(self):
        assert classify_file("src/lib.rs") == "source"


# -----------------------------------------------------------------------
# Tier 3 — Content-based classification
# -----------------------------------------------------------------------


class TestTier3ContentBased:
    """Tier 3: content-based detection (generated markers, shebang, minified)."""

    def test_do_not_edit_marker(self):
        content = "// DO NOT EDIT — this file was generated by protoc\npackage pb\n"
        assert classify_file("api.go", content=content) == "generated"

    def test_generated_annotation(self):
        content = "# @generated by Buck\nimport foo\n"
        assert classify_file("gen_config.py", content=content) == "generated"

    def test_auto_generated_header(self):
        content = "/* auto-generated file */\nint main() { return 0; }\n"
        assert classify_file("output.c", content=content) == "generated"

    def test_machine_generated(self):
        content = "// machine generated — do not modify\nfunc init() {}\n"
        assert classify_file("init.go", content=content) == "generated"

    def test_generated_marker_beyond_first_10_lines(self):
        """Generated markers past line 10 are ignored."""
        content = "\n" * 12 + "// DO NOT EDIT\nfoo bar\n"
        assert classify_file("src/module.py", content=content) == "source"

    def test_shebang_script(self):
        content = "#!/usr/bin/env python3\nprint('hello')\n"
        # Without path cues, a shebang file is classified as scripts
        assert classify_file("run_job", content=content) == "scripts"

    def test_shebang_bash(self):
        content = "#!/bin/bash\nset -e\necho hello\n"
        assert classify_file("entrypoint", content=content) == "scripts"

    def test_minified_js_detection(self):
        """Long average line length in .js triggers generated classification."""
        # Single line of 200 chars
        content = "a" * 200
        assert classify_file("app.js", content=content) == "generated"

    def test_minified_css_detection(self):
        content = "b" * 200
        assert classify_file("styles.css", content=content) == "generated"

    def test_normal_js_not_minified(self):
        """Regular JS with short lines should not be marked generated."""
        content = "function hello() {\n  return 42;\n}\n"
        assert classify_file("app.js", content=content) == "source"

    def test_no_content_skips_tier3(self):
        """Without content, Tier 3 is not applied."""
        assert classify_file("app.js") == "source"


# -----------------------------------------------------------------------
# Priority resolution
# -----------------------------------------------------------------------


class TestPriorityResolution:
    """Generated > Vendored > Test > Build/CI > Others > Source."""

    def test_generated_beats_vendored(self):
        """Content-detected generated wins over vendored path."""
        content = "// DO NOT EDIT\nmodule.exports = {};\n"
        assert classify_file("vendor/lib/gen.js", content=content) == "generated"

    def test_generated_beats_test(self):
        """Content-detected generated wins over test path."""
        content = "# @generated\ndef test_stub(): pass\n"
        assert classify_file("tests/test_auto.py", content=content) == "generated"

    def test_vendored_beats_test_name(self):
        """Vendored path wins over test-like filename."""
        assert classify_file("vendor/test_utils.py") == "vendored"

    def test_vendored_beats_build(self):
        """Vendored path wins over build-like filename."""
        assert classify_file("node_modules/webpack/Makefile") == "vendored"

    def test_test_path_beats_config_ext(self):
        """Test directory wins even for config-like extensions."""
        assert classify_file("tests/fixtures/config.json") == "test"

    def test_ci_path_beats_config_ext(self):
        """CI directory wins even for config-like extensions."""
        assert classify_file(".github/workflows/build.yml") == "ci"

    def test_build_filename_beats_source(self):
        assert classify_file("Makefile") == "build"

    def test_source_is_default(self):
        assert classify_file("src/app.py") == "source"


# -----------------------------------------------------------------------
# Helper functions
# -----------------------------------------------------------------------


class TestIsTest:
    def test_test_dir(self):
        assert is_test("tests/test_core.py") is True

    def test_dunder_tests(self):
        assert is_test("src/__tests__/App.test.tsx") is True

    def test_test_filename(self):
        assert is_test("test_helpers.py") is True

    def test_go_test(self):
        assert is_test("handler_test.go") is True

    def test_source_is_not_test(self):
        assert is_test("src/main.py") is False

    def test_config_is_not_test(self):
        assert is_test("package.json") is False


class TestIsSource:
    def test_regular_python(self):
        assert is_source("src/core.py") is True

    def test_regular_go(self):
        assert is_source("pkg/handler.go") is True

    def test_test_is_not_source(self):
        assert is_source("tests/test_core.py") is False

    def test_config_is_not_source(self):
        assert is_source("package.json") is False

    def test_docs_is_not_source(self):
        assert is_source("README.md") is False


class TestIsGenerated:
    def test_min_js(self):
        assert is_generated("bundle.min.js") is True

    def test_pb_go(self):
        assert is_generated("service.pb.go") is True

    def test_content_marker(self):
        assert is_generated("output.go", content="// DO NOT EDIT\npackage main\n") is True

    def test_regular_file(self):
        assert is_generated("src/main.py") is False

    def test_no_content_no_pattern(self):
        assert is_generated("src/util.go") is False


class TestIsVendored:
    def test_vendor_dir(self):
        assert is_vendored("vendor/pkg/errors.go") is True

    def test_node_modules(self):
        assert is_vendored("node_modules/react/index.js") is True

    def test_third_party(self):
        assert is_vendored("third_party/zlib/zlib.h") is True

    def test_regular_source(self):
        assert is_vendored("src/main.py") is False


# -----------------------------------------------------------------------
# Edge cases
# -----------------------------------------------------------------------


class TestEdgeCases:
    def test_windows_backslashes(self):
        """Backslashes are normalised before matching."""
        assert classify_file("tests\\test_core.py") == "test"

    def test_windows_backslash_vendor(self):
        assert classify_file("vendor\\lib\\utils.go") == "vendored"

    def test_windows_backslash_github(self):
        assert classify_file(".github\\workflows\\ci.yml") == "ci"

    def test_empty_basename_slash(self):
        """Trailing slash (directory-like path) falls back to source."""
        result = classify_file("")
        assert result in ALL_ROLES

    def test_nested_test_path(self):
        """Deeply nested test directory still matches."""
        assert classify_file("packages/core/tests/unit/test_parser.py") == "test"

    def test_nested_vendor_path(self):
        assert classify_file("apps/web/node_modules/react/index.js") == "vendored"

    def test_case_insensitive_filename(self):
        """Exact filename matching is case-insensitive."""
        assert classify_file("makefile") == "build"
        assert classify_file("MAKEFILE") == "build"

    def test_case_insensitive_readme(self):
        assert classify_file("readme.md") == "docs"
        assert classify_file("Readme.md") == "docs"

    def test_all_roles_constant(self):
        """ALL_ROLES contains exactly 11 roles."""
        assert len(ALL_ROLES) == 11
        expected = {
            "source", "test", "config", "build", "docs",
            "generated", "vendored", "data", "examples", "scripts", "ci",
        }
        assert ALL_ROLES == expected
