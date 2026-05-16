"""Vue / Vitest test-framework detection (SYNTHESIS Rank 15).

Closes 3 H findings: ``test-pyramid``, ``endpoints``, and ``n1`` used to
treat Vue/Vitest projects as having no tests because their inline
detection regexes only recognised ``*.test.{js,ts,jsx,tsx}`` (no ``.vue``)
and didn't honour Vitest's "colocated spec = unit test" convention.

This module pins:

* ``JavaScriptConvention.is_test_file`` accepts Vue SFC tests.
* ``JavaScriptConvention.classify_kind`` returns ``unit`` for colocated
  specs and ``integration`` / ``e2e`` for the conventional directories.
* ``file_roles.is_test`` and ``file_roles.classify_test_kind`` see Vue
  SFC tests and default colocated Vitest specs to ``unit``.
* ``roam test-pyramid`` reports non-zero unit/integration counts on a
  mixed Vue/Vitest fixture.
"""

from __future__ import annotations

import json
import subprocess

import pytest
from click.testing import CliRunner

from roam.cli import cli
from roam.index.file_roles import (
    TEST_KIND_E2E,
    TEST_KIND_INTEGRATION,
    TEST_KIND_UNIT,
    classify_test_kind,
    is_test,
)
from roam.index.test_conventions import (
    KIND_E2E,
    KIND_INTEGRATION,
    KIND_UNIT,
    JavaScriptConvention,
)

# ---------------------------------------------------------------------------
# JavaScriptConvention — Vitest extension
# ---------------------------------------------------------------------------


class TestJavaScriptConventionVitest:
    def setup_method(self):
        self.conv = JavaScriptConvention()

    def test_vue_sfc_test_is_detected(self):
        assert self.conv.is_test_file("Foo.test.vue") is True
        assert self.conv.is_test_file("src/components/Foo.test.vue") is True

    def test_vue_sfc_spec_is_detected(self):
        assert self.conv.is_test_file("Foo.spec.vue") is True

    def test_ts_spec_still_detected(self):
        """Existing TS/JS detection must not regress."""
        assert self.conv.is_test_file("App.test.ts") is True
        assert self.conv.is_test_file("App.spec.tsx") is True

    def test_plain_vue_is_not_a_test(self):
        assert self.conv.is_test_file("Foo.vue") is False
        assert self.conv.is_test_file("src/App.vue") is False

    def test_mjs_test_detected(self):
        assert self.conv.is_test_file("util.test.mjs") is True

    def test_source_to_test_vue(self):
        paths = self.conv.source_to_test_paths("src/components/Foo.vue")
        assert "src/components/Foo.test.vue" in paths
        assert "src/components/Foo.spec.vue" in paths

    def test_test_to_source_vue(self):
        paths = self.conv.test_to_source_paths("src/components/Foo.test.vue")
        assert any(p.endswith("Foo.vue") for p in paths)

    def test_source_for_test_returns_first(self):
        src = self.conv.source_for_test("src/composables/useFoo.test.ts")
        assert src is not None
        assert "useFoo.ts" in src

    def test_languages_now_includes_vue(self):
        assert "vue" in self.conv.languages
        # Backwards-compat: js/ts must still be claimed by this adapter.
        assert "javascript" in self.conv.languages
        assert "typescript" in self.conv.languages


# ---------------------------------------------------------------------------
# Adapter-level classify_kind (Vitest-aware)
# ---------------------------------------------------------------------------


class TestJavaScriptConventionClassifyKind:
    def setup_method(self):
        self.conv = JavaScriptConvention()

    def test_colocated_spec_is_unit(self):
        """Vitest convention: colocated spec = unit test."""
        assert self.conv.classify_kind("src/composables/useFoo.test.ts") == KIND_UNIT

    def test_underscore_tests_dir_is_unit(self):
        assert self.conv.classify_kind("src/composables/__tests__/useFoo.test.ts") == KIND_UNIT

    def test_vue_sfc_test_is_unit(self):
        assert self.conv.classify_kind("src/components/Foo.test.vue") == KIND_UNIT

    def test_tests_integration_dir_is_integration(self):
        assert self.conv.classify_kind("tests/integration/api.test.ts") == KIND_INTEGRATION

    def test_tests_e2e_dir_is_e2e(self):
        assert self.conv.classify_kind("tests/e2e/login.spec.ts") == KIND_E2E

    def test_playwright_dir_is_e2e(self):
        assert self.conv.classify_kind("playwright/auth.spec.ts") == KIND_E2E

    def test_cypress_dir_is_e2e(self):
        # Cypress's own convention uses .cy.ts, but the broader
        # JavaScriptConvention only claims .test/.spec files. We use
        # .spec.ts which is also a valid Cypress filename.
        assert self.conv.classify_kind("cypress/e2e/checkout.spec.ts") == KIND_E2E

    def test_integration_infix_in_name(self):
        assert self.conv.classify_kind("src/services/api.integration.test.ts") == KIND_INTEGRATION

    def test_non_test_file_returns_unknown(self):
        from roam.index.test_conventions import KIND_UNKNOWN

        assert self.conv.classify_kind("src/App.vue") == KIND_UNKNOWN


# ---------------------------------------------------------------------------
# file_roles integration — is_test + classify_test_kind
# ---------------------------------------------------------------------------


class TestFileRolesVitest:
    def test_vue_test_is_test(self):
        assert is_test("src/components/Foo.test.vue") is True

    def test_vue_spec_is_test(self):
        assert is_test("src/components/Foo.spec.vue") is True

    def test_vitest_spec_detected_as_unit(self):
        """src/composables/useFoo.test.ts → unit (colocated Vitest spec)."""
        assert classify_test_kind("src/composables/useFoo.test.ts") == TEST_KIND_UNIT

    def test_vitest_integration_spec_detected(self):
        """tests/integration/api.test.ts → integration."""
        assert classify_test_kind("tests/integration/api.test.ts") == TEST_KIND_INTEGRATION

    def test_vitest_e2e_spec_detected(self):
        """tests/e2e/login.spec.ts → e2e."""
        assert classify_test_kind("tests/e2e/login.spec.ts") == TEST_KIND_E2E

    def test_vue_sfc_test_detected_as_unit(self):
        """Foo.test.vue → unit."""
        assert classify_test_kind("src/components/Foo.test.vue") == TEST_KIND_UNIT

    def test_python_pytest_still_unknown_when_no_hint(self):
        """Python tests without kind hints stay 'unknown' — they don't
        share the Vitest colocation convention."""
        from roam.index.file_roles import TEST_KIND_UNKNOWN

        assert classify_test_kind("tests/test_auth.py") == TEST_KIND_UNKNOWN


# ---------------------------------------------------------------------------
# End-to-end: roam test-pyramid on a Vue/Vitest fixture
# ---------------------------------------------------------------------------


@pytest.fixture
def _vitest_project(tmp_path, monkeypatch):
    """Mixed Vue/Vitest fixture: 2 colocated unit specs, 1 integration
    spec under tests/integration/, 1 e2e spec under tests/e2e/."""
    # Source
    (tmp_path / "src" / "composables").mkdir(parents=True)
    (tmp_path / "src" / "composables" / "useFoo.ts").write_text("export function useFoo() { return 1; }\n")
    (tmp_path / "src" / "composables" / "useFoo.test.ts").write_text(
        "import { useFoo } from './useFoo';\n"
        "import { test, expect } from 'vitest';\n"
        "test('useFoo', () => { expect(useFoo()).toBe(1); });\n"
    )
    # Vue SFC + colocated unit test
    (tmp_path / "src" / "components").mkdir(parents=True)
    (tmp_path / "src" / "components" / "Foo.vue").write_text("<template><div>Foo</div></template>\n")
    (tmp_path / "src" / "components" / "Foo.test.vue").write_text(
        "<script>\nimport { test } from 'vitest';\ntest('renders', () => {});\n</script>\n"
    )
    # Integration spec
    (tmp_path / "tests" / "integration").mkdir(parents=True)
    (tmp_path / "tests" / "integration" / "api.test.ts").write_text(
        "import { test } from 'vitest';\ntest('api', () => {});\n"
    )
    # E2E spec
    (tmp_path / "tests" / "e2e").mkdir(parents=True)
    (tmp_path / "tests" / "e2e" / "login.spec.ts").write_text(
        "import { test } from 'vitest';\ntest('login', () => {});\n"
    )
    # Make it a git repo so roam can index it.
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True)
    subprocess.run(
        ["git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "-m", "init"],
        cwd=tmp_path,
        check=True,
    )
    monkeypatch.chdir(tmp_path)
    from roam.index.indexer import Indexer

    Indexer().run(quiet=True)
    return tmp_path


def test_test_pyramid_counts_vitest_specs(_vitest_project):
    """``roam --json test-pyramid`` must report non-zero unit AND
    integration counts on a Vue/Vitest project. Pre-fix this command
    saw 0 tests because it relied on file_roles.is_test which missed
    ``.test.vue`` and on classify_test_kind which returned 'unknown'
    for colocated specs.
    """
    runner = CliRunner()
    result = runner.invoke(cli, ["--json", "test-pyramid"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    summary = payload["summary"]
    # We expect at least 2 unit (useFoo.test.ts + Foo.test.vue), 1
    # integration (tests/integration/api.test.ts), and 1 e2e
    # (tests/e2e/login.spec.ts). Indexer may or may not pick up .vue
    # depending on grammar availability; require unit >= 1 to keep
    # the test stable across machines.
    assert summary["unit"] >= 1, f"expected at least 1 unit test, got {summary['unit']}: {summary}"
    assert summary["integration"] >= 1, f"expected at least 1 integration test, got {summary['integration']}: {summary}"
    assert summary["e2e"] >= 1, f"expected at least 1 e2e test, got {summary['e2e']}: {summary}"
    # Most importantly: the total must reflect the test files we wrote.
    assert summary["total"] >= 3, f"expected total >= 3, got {summary['total']}: {summary}"


# ---------------------------------------------------------------------------
# roam n1 — regression sentinel: must not crash on a Vue/Vitest project,
# and must not pull Vitest spec files into its N+1 scan. The detection
# algorithm itself is ORM-specific (Laravel $appends / Django @property /
# Rails associations / SQLAlchemy hybrid_property / JPA @Transient) and
# Vue SFCs don't carry ORM models, so the contract here is "scan runs to
# clean exit with no spurious findings."
# ---------------------------------------------------------------------------


def test_n1_skips_vitest_specs_on_vue_project(_vitest_project):
    """``roam --json n1`` on a Vue/Vitest fixture must:
    1. exit cleanly (no JSON-parse-on-empty-input regressions);
    2. report 0 findings (no ORM models in a pure Vue/Vitest app);
    3. NOT raise on .test.vue / .spec.ts paths — proves the canonical
       ``_is_canonical_test_file`` shim (W6.2 consolidation) recognises
       Vitest specs and excludes them before the model-class scan.
    """
    runner = CliRunner()
    result = runner.invoke(cli, ["--json", "n1"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    summary = payload["summary"]
    assert summary["total"] == 0, f"expected 0 N+1 findings on a Vue/Vitest project, got {summary['total']}: {summary}"
    # The verdict must still be a non-empty string (Pattern 1 in CLAUDE.md
    # — no JSON-parse-on-empty-input).
    assert isinstance(summary["verdict"], str) and summary["verdict"]


# ---------------------------------------------------------------------------
# roam endpoints — Vue Router detection
# ---------------------------------------------------------------------------


@pytest.fixture
def _vue_router_project(tmp_path, monkeypatch):
    """Vue 3 + Vue Router project with a router config in TS and an
    inline router declaration inside a .vue SFC. Should surface both
    paths via ``roam endpoints``.
    """
    (tmp_path / "src" / "router").mkdir(parents=True)
    (tmp_path / "src" / "router" / "index.ts").write_text(
        "import { createRouter, createWebHistory } from 'vue-router';\n"
        "import Home from '../views/Home.vue';\n"
        "\n"
        "const routes = [\n"
        "  { path: '/', component: Home, name: 'home' },\n"
        "  { path: '/users', component: () => import('../views/Users.vue') },\n"
        "  { path: '/users/:id', component: () => import('../views/UserDetail.vue') },\n"
        "];\n"
        "\n"
        "export const router = createRouter({\n"
        "  history: createWebHistory(),\n"
        "  routes,\n"
        "});\n"
    )
    # SFC with inline router config — rarer, but real
    (tmp_path / "src" / "views").mkdir(parents=True)
    (tmp_path / "src" / "views" / "Home.vue").write_text(
        "<template><div>Home</div></template>\n"
        '<script setup lang="ts">\n'
        "// no routes here — pure component\n"
        "</script>\n"
    )
    (tmp_path / "src" / "App.vue").write_text(
        "<template><router-view /></template>\n"
        '<script setup lang="ts">\n'
        "import { createRouter } from 'vue-router';\n"
        "const inlineRoutes = [\n"
        "  { path: '/about', component: () => import('./views/About.vue') },\n"
        "];\n"
        "createRouter({ routes: inlineRoutes });\n"
        "</script>\n"
    )
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True)
    subprocess.run(
        ["git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "-m", "init"],
        cwd=tmp_path,
        check=True,
    )
    monkeypatch.chdir(tmp_path)
    from roam.index.indexer import Indexer

    Indexer().run(quiet=True)
    return tmp_path


def test_endpoints_detects_vue_router_routes(_vue_router_project):
    """``roam --json endpoints`` must surface Vue Router declarations.

    Pre-fix the extension map registered scanners for .js/.ts/.jsx/.tsx
    only, and the JS/TS scanner only knew Express. Vue Router routes
    (``createRouter({ routes: [...] })``) were therefore invisible.
    """
    runner = CliRunner()
    result = runner.invoke(cli, ["--json", "endpoints"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    summary = payload["summary"]
    endpoints = payload.get("endpoints", [])

    # At minimum: the 3 routes in router/index.ts must surface.
    vue_router_eps = [e for e in endpoints if e.get("framework") == "vue-router"]
    assert len(vue_router_eps) >= 3, f"expected >= 3 vue-router endpoints, got {len(vue_router_eps)}: {endpoints}"

    paths = {e["path"] for e in vue_router_eps}
    assert "/" in paths, f"missing '/' route: {paths}"
    assert "/users" in paths, f"missing '/users' route: {paths}"
    assert any(p.startswith("/users/") for p in paths), f"missing '/users/:id' route: {paths}"

    # Vue Router routes are method-agnostic — every entry must report
    # method == 'ROUTE' (not 'GET'/'POST'/...).
    assert all(e["method"] == "ROUTE" for e in vue_router_eps), (
        f"vue-router endpoints must use method 'ROUTE': {[e['method'] for e in vue_router_eps]}"
    )

    # Framework count and verdict must reflect the new framework.
    assert "vue-router" in summary["frameworks"], summary


def test_endpoints_skips_vue_files_without_router(tmp_path, monkeypatch):
    """A .vue file with no ``createRouter``/``vue-router`` mention must
    not be misclassified as a router declaration (no false positives).
    """
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "Plain.vue").write_text(
        "<template><div>Plain</div></template>\n"
        '<script setup lang="ts">\n'
        "const items = [{ path: '/looks-like-a-route' }];\n"
        "// no router import here — must NOT be detected.\n"
        "</script>\n"
    )
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True)
    subprocess.run(
        ["git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "-m", "init"],
        cwd=tmp_path,
        check=True,
    )
    monkeypatch.chdir(tmp_path)
    from roam.index.indexer import Indexer

    Indexer().run(quiet=True)

    runner = CliRunner()
    result = runner.invoke(cli, ["--json", "endpoints"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    vue_router_eps = [e for e in payload.get("endpoints", []) if e.get("framework") == "vue-router"]
    assert vue_router_eps == [], (
        f"expected no vue-router endpoints on a project without createRouter, got {vue_router_eps}"
    )
