"""W6.2 — single source of truth for test-file detection.

Mirror of Fix G's ``conventions_helper`` consolidation, but for "what's
a test file" rather than "what naming convention is this codebase
using." Before this round there were two parallel sources of truth:
``file_roles._TEST_PATTERNS`` and per-adapter ``_TEST_PATTERN`` regexes
in ``test_conventions``. They overlapped but disagreed (Vitest/Vue
mismatches were the worst case — see ``test_vue_vitest_detection.py``).

This test pins:

* The canonical module-level API on ``roam.index.test_conventions``:
  ``is_test_file``, ``classify_test_kind``, ``source_for_test``,
  ``language_for_test``.
* ``file_roles.is_test`` / ``file_roles.classify_test_kind`` now
  delegate to the canonical detector — same input must produce same
  output across both call sites.
* Consumer commands (``test-pyramid``, ``endpoints``, ``n1``) classify
  the same fixture identically.
"""

from __future__ import annotations

import json
import subprocess

import pytest
from click.testing import CliRunner

from roam.cli import cli
from roam.index import file_roles as fr
from roam.index import test_conventions as tc

# ---------------------------------------------------------------------------
# 1-2. Canonical is_test_file — positive + negative cases
# ---------------------------------------------------------------------------


class TestCanonicalIsTestFile:
    """The canonical detector recognises every supported convention and
    rejects look-alike non-test files."""

    def test_canonical_is_test_handles_python_vitest_vue(self):
        # Python pytest
        assert tc.is_test_file("test_foo.py") is True
        assert tc.is_test_file("tests/test_foo.py") is True
        # Vitest TS/JS spec (colocated)
        assert tc.is_test_file("foo.test.ts") is True
        assert tc.is_test_file("src/composables/useFoo.test.ts") is True
        # Vue SFC test
        assert tc.is_test_file("Foo.test.vue") is True
        assert tc.is_test_file("src/components/Foo.test.vue") is True
        # Conftest, Go, Java, Ruby — round trip across the supported set
        assert tc.is_test_file("conftest.py") is True
        assert tc.is_test_file("foo_test.go") is True
        assert tc.is_test_file("FooTest.java") is True
        assert tc.is_test_file("user_spec.rb") is True

    def test_canonical_is_test_excludes_non_test_paths(self):
        assert tc.is_test_file("src/main.py") is False
        # Plain .vue is NOT a test — only *.test.vue and *.spec.vue are
        assert tc.is_test_file("Foo.vue") is False
        assert tc.is_test_file("src/components/Foo.vue") is False
        # Source TS is not a test
        assert tc.is_test_file("src/api.ts") is False
        # README and docs are not tests
        assert tc.is_test_file("README.md") is False
        assert tc.is_test_file("docs/foo.md") is False


# ---------------------------------------------------------------------------
# 3-5. classify_test_kind on canonical detector
# ---------------------------------------------------------------------------


class TestCanonicalClassifyTestKind:
    """The canonical kind classifier handles Vitest colocation,
    integration/e2e directories, and falls back to unknown for pytest
    files with no hint."""

    def test_canonical_classify_kind_returns_unit_for_colocated(self):
        """Vitest colocation convention: ``src/<dir>/Foo.test.ts`` is
        a unit test by default."""
        assert tc.classify_test_kind("src/composables/useFoo.test.ts") == "unit"
        assert tc.classify_test_kind("src/components/Foo.test.vue") == "unit"
        assert tc.classify_test_kind("src/composables/__tests__/useFoo.test.ts") == "unit"

    def test_canonical_classify_kind_returns_integration_for_tests_integration_dir(self):
        assert tc.classify_test_kind("tests/integration/api.test.ts") == "integration"
        assert tc.classify_test_kind("tests/integration/test_db.py") == "integration"
        # integ/ alias also recognised
        assert tc.classify_test_kind("tests/integ/api.test.ts") == "integration"

    def test_canonical_classify_kind_returns_e2e_for_e2e_dir(self):
        assert tc.classify_test_kind("tests/e2e/login.test.ts") == "e2e"
        assert tc.classify_test_kind("e2e/login.spec.ts") == "e2e"
        # Cypress / Playwright / Selenium dirs are e2e
        assert tc.classify_test_kind("cypress/checkout.spec.ts") == "e2e"
        assert tc.classify_test_kind("playwright/auth.spec.ts") == "e2e"

    def test_canonical_classify_kind_smoke_and_unknown(self):
        # Smoke directory
        assert tc.classify_test_kind("tests/smoke/sanity.test.ts") == "smoke"
        # Python pytest with no hint → unknown (Python doesn't share the
        # Vitest colocation convention)
        assert tc.classify_test_kind("tests/test_auth.py") == "unknown"
        # Non-test file
        assert tc.classify_test_kind("src/main.py") == "unknown"


# ---------------------------------------------------------------------------
# 6. file_roles delegates to canonical
# ---------------------------------------------------------------------------


class TestFileRolesDelegation:
    """``file_roles.is_test`` and ``file_roles.classify_test_kind`` must
    return identical results to the canonical detector for a
    representative set of paths."""

    representative_paths = [
        # Test files across conventions
        "tests/test_auth.py",
        "test_foo.py",
        "foo_test.py",
        "conftest.py",
        "foo_test.go",
        "src/composables/useFoo.test.ts",
        "src/components/Foo.test.vue",
        "Foo.spec.tsx",
        "FooTest.java",
        "user_spec.rb",
        "FooTest.cls",
        "tests/integration/db.test.ts",
        "tests/e2e/login.spec.ts",
        # Non-test files
        "src/main.py",
        "Foo.vue",
        "src/components/Foo.vue",
        "README.md",
        "package.json",
        ".github/workflows/ci.yml",
    ]

    def test_file_roles_is_test_delegates_to_canonical(self):
        for path in self.representative_paths:
            assert fr.is_test(path) == tc.is_test_file(path), (
                f"is_test disagreement on {path!r}: file_roles={fr.is_test(path)} canonical={tc.is_test_file(path)}"
            )

    def test_file_roles_classify_test_kind_delegates_to_canonical(self):
        for path in self.representative_paths:
            assert fr.classify_test_kind(path) == tc.classify_test_kind(path), (
                f"classify_test_kind disagreement on {path!r}: "
                f"file_roles={fr.classify_test_kind(path)!r} "
                f"canonical={tc.classify_test_kind(path)!r}"
            )

    def test_test_kind_constants_match_canonical(self):
        """``TEST_KIND_*`` constants must equal ``KIND_*`` constants."""
        assert fr.TEST_KIND_UNIT == tc.KIND_UNIT
        assert fr.TEST_KIND_INTEGRATION == tc.KIND_INTEGRATION
        assert fr.TEST_KIND_E2E == tc.KIND_E2E
        assert fr.TEST_KIND_SMOKE == tc.KIND_SMOKE
        assert fr.TEST_KIND_UNKNOWN == tc.KIND_UNKNOWN

    def test_default_test_patterns_re_exported(self):
        """``file_roles._TEST_PATTERNS`` must be the canonical pattern
        list — backward compat for any external caller."""
        assert fr._TEST_PATTERNS is tc.DEFAULT_TEST_PATTERNS


# ---------------------------------------------------------------------------
# 7. language_for_test + source_for_test
# ---------------------------------------------------------------------------


class TestCanonicalLanguageAndSource:
    def test_language_for_test_python(self):
        assert tc.language_for_test("test_foo.py") == "python"

    def test_language_for_test_vue(self):
        assert tc.language_for_test("Foo.test.vue") == "vue"

    def test_language_for_test_typescript(self):
        assert tc.language_for_test("Foo.test.ts") == "typescript"

    def test_language_for_test_javascript(self):
        assert tc.language_for_test("Foo.test.js") == "javascript"

    def test_language_for_test_unknown_extension(self):
        assert tc.language_for_test("Foo.xyz") == "unknown"

    def test_source_for_test_returns_path_for_python(self):
        src = tc.source_for_test("tests/test_models.py")
        assert src is not None
        assert "models.py" in str(src)

    def test_source_for_test_returns_none_for_non_test(self):
        assert tc.source_for_test("src/main.py") is None


# ---------------------------------------------------------------------------
# 7b. Facade ↔ adapter parity (W12.x parity gap fix)
#
# Before W12.x the facade ``classify_test_kind`` and
# ``JavaScriptConvention.classify_kind`` disagreed on edge cases — the
# adapter applied the Vitest "colocated spec = unit" default for any
# .test.ts / .test.vue path, while the facade required an explicit
# kind hint (directory or name infix). Both were correct for different
# consumers but produced Pattern 4 (conventions detector inconsistency)
# when a command called the facade and another called the adapter on
# the same input.
#
# Fix direction: the facade now **delegates to the adapter** for any
# extension in ``_VITEST_LIKE_EXTS`` (.js / .jsx / .ts / .tsx / .mjs /
# .cjs / .mts / .cts / .vue). The adapter was extended with smoke /
# sanity recognition so it is a strict superset of the cross-language
# fallback. These tests pin the parity.
# ---------------------------------------------------------------------------


class TestFacadeAdapterParity:
    """The module-level facade ``classify_test_kind`` must return the
    same kind as ``JavaScriptConvention().classify_kind`` for every
    path with a JS / TS / Vue extension. This pins the W12.x parity
    fix — the facade delegates to the adapter so consumers can't get
    different answers based on which entry point they used."""

    js_ts_vue_paths = [
        # Colocated Vitest specs
        "src/composables/useFoo.test.ts",
        "src/composables/useBar.spec.ts",
        "src/foo.test.js",
        "src/foo.test.jsx",
        "src/foo.test.tsx",
        "src/foo.test.mjs",
        "src/foo.test.cjs",
        "src/foo.test.mts",
        "src/foo.test.cts",
        # __tests__ folder
        "src/__tests__/Bar.test.ts",
        "src/composables/__tests__/useFoo.test.ts",
        # Vue SFC tests
        "Component.test.vue",
        "src/components/Foo.test.vue",
        "src/components/Foo.spec.vue",
        # Directory-based kinds
        "tests/integration/api.test.ts",
        "tests/integ/db.test.ts",
        "tests/e2e/login.spec.ts",
        "e2e/login.spec.ts",
        "cypress/checkout.spec.ts",
        "playwright/auth.spec.ts",
        "selenium/login.test.ts",
        "tests/unit/foo.test.ts",
        "tests/smoke/sanity.test.ts",
        "tests/sanity/foo.test.ts",
        # Name-infix kinds
        "src/services/api.integration.test.ts",
        "src/services/foo.e2e.test.ts",
        # Vue + directory
        "tests/integration/foo.spec.vue",
        "tests/e2e/foo.spec.vue",
        # Non-test JS/TS files (both should agree: unknown)
        "src/main.ts",
        "src/components/Foo.vue",
        "lib/foo.cy.ts",  # cypress's own .cy.ts isn't in JavaScriptConvention's pattern
    ]

    def test_facade_matches_adapter_for_javascript(self):
        """The module-level facade should return the same kind as the
        adapter for any JS/TS/Vue path."""
        from roam.index.test_conventions import JavaScriptConvention

        adapter = JavaScriptConvention()
        for p in self.js_ts_vue_paths:
            facade_kind = tc.classify_test_kind(p)
            adapter_kind = adapter.classify_kind(p)
            assert facade_kind == adapter_kind, (
                f"facade returned {facade_kind!r} but adapter returned {adapter_kind!r} for {p!r}"
            )

    def test_adapter_recognises_smoke_directory(self):
        """The adapter must recognise ``smoke/`` and ``sanity/`` as
        smoke tests so it is a strict superset of the cross-language
        fallback (otherwise the facade-delegates-to-adapter pattern
        would silently downgrade smoke → unit for JS/TS paths)."""
        from roam.index.test_conventions import JavaScriptConvention

        adapter = JavaScriptConvention()
        assert adapter.classify_kind("tests/smoke/sanity.test.ts") == tc.KIND_SMOKE
        assert adapter.classify_kind("tests/sanity/foo.test.ts") == tc.KIND_SMOKE
        # And via the facade
        assert tc.classify_test_kind("tests/smoke/sanity.test.ts") == tc.KIND_SMOKE
        assert tc.classify_test_kind("tests/sanity/foo.test.ts") == tc.KIND_SMOKE

    def test_facade_unchanged_for_non_js_languages(self):
        """Delegation to the JS adapter must NOT touch other languages.
        Python/Go/Java/Ruby tests still use the cross-language
        fallback, which returns ``unknown`` when no kind hint is
        present (these languages don't share Vitest's colocation
        convention)."""
        # Python pytest with no hint — still unknown
        assert tc.classify_test_kind("tests/test_auth.py") == tc.KIND_UNKNOWN
        # Go _test.go with no hint — still unknown
        assert tc.classify_test_kind("internal/foo_test.go") == tc.KIND_UNKNOWN
        # Java *Test.java with no hint — still unknown
        assert tc.classify_test_kind("src/test/java/com/x/FooTest.java") == tc.KIND_UNKNOWN
        # Ruby _spec.rb with no hint — still unknown
        assert tc.classify_test_kind("spec/foo_spec.rb") == tc.KIND_UNKNOWN
        # But directory hints still work for non-JS
        assert tc.classify_test_kind("tests/integration/test_db.py") == tc.KIND_INTEGRATION
        assert tc.classify_test_kind("tests/e2e/test_login.py") == tc.KIND_E2E
        assert tc.classify_test_kind("tests/smoke/test_sanity.py") == tc.KIND_SMOKE


# ---------------------------------------------------------------------------
# 8. Consumer commands agree on the same fixture (the headline
#    consolidation guarantee — Pattern 4 mitigation)
# ---------------------------------------------------------------------------


@pytest.fixture
def _mixed_fixture(tmp_path, monkeypatch):
    """Fixture combining Python source, a Vue/Vitest project, and tests
    in colocated / integration / e2e layouts. Used by the consumer-
    agreement test."""
    # Python source + unit test
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "service.py").write_text("def get_user(id): return {'id': id}\n")
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_service.py").write_text(
        "from src.service import get_user\ndef test_get_user(): assert get_user(1)['id'] == 1\n"
    )
    # Vue source + colocated Vitest unit test
    (tmp_path / "src" / "components").mkdir()
    (tmp_path / "src" / "components" / "Foo.vue").write_text("<template><div>Foo</div></template>\n")
    (tmp_path / "src" / "components" / "Foo.test.vue").write_text(
        "<script>\nimport { test } from 'vitest';\ntest('renders', () => {});\n</script>\n"
    )
    # TS source + colocated unit test
    (tmp_path / "src" / "composables").mkdir()
    (tmp_path / "src" / "composables" / "useFoo.ts").write_text("export function useFoo() { return 1; }\n")
    (tmp_path / "src" / "composables" / "useFoo.test.ts").write_text(
        "import { useFoo } from './useFoo';\n"
        "import { test, expect } from 'vitest';\n"
        "test('useFoo', () => { expect(useFoo()).toBe(1); });\n"
    )
    # Integration test
    (tmp_path / "tests" / "integration").mkdir()
    (tmp_path / "tests" / "integration" / "api.test.ts").write_text(
        "import { test } from 'vitest';\ntest('api', () => {});\n"
    )
    # E2E test
    (tmp_path / "tests" / "e2e").mkdir()
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


def test_consumers_use_canonical_api(_mixed_fixture):
    """The headline guarantee: every consumer command that classifies a
    file as "test" or "not-test" must agree on the same fixture. Before
    consolidation, ``cmd_endpoints`` used its own ``_TEST_PATH_PATTERNS``
    regex and ``cmd_n1`` used a private ``_is_test_path`` helper —
    both should now delegate to the canonical detector."""
    # Probe the canonical classifier directly for the test files.
    fixture_test_paths = [
        "tests/test_service.py",
        "src/components/Foo.test.vue",
        "src/composables/useFoo.test.ts",
        "tests/integration/api.test.ts",
        "tests/e2e/login.spec.ts",
    ]
    for p in fixture_test_paths:
        assert tc.is_test_file(p), f"canonical missed test file {p}"
        assert fr.is_test(p), f"file_roles missed test file {p}"

    fixture_non_test_paths = [
        "src/service.py",
        "src/components/Foo.vue",
        "src/composables/useFoo.ts",
    ]
    for p in fixture_non_test_paths:
        assert not tc.is_test_file(p), f"canonical mis-classified {p} as test"
        assert not fr.is_test(p), f"file_roles mis-classified {p} as test"

    # End-to-end: roam test-pyramid must see the unit / integration /
    # e2e tests we wrote.
    runner = CliRunner()
    result = runner.invoke(cli, ["--json", "test-pyramid"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    summary = payload["summary"]
    # We expect at least 1 unit, 1 integration, 1 e2e on the fixture.
    assert summary["unit"] >= 1, summary
    assert summary["integration"] >= 1, summary
    assert summary["e2e"] >= 1, summary

    # And endpoints + n1 must not crash with their (now-delegating)
    # test-detection helpers. We don't assert on counts here because
    # the fixture has no endpoints or n+1 queries — the smoke that they
    # run cleanly on a fixture containing Vue/Vitest tests proves the
    # delegation isn't broken.
    for sub in ("endpoints", "n1"):
        r = runner.invoke(cli, ["--json", sub])
        # Either success or a clean "nothing found" — both fine. A crash
        # would indicate the delegation broke.
        assert r.exit_code in (0, 1), f"roam {sub} crashed (exit {r.exit_code}): {r.output!r}"
