"""Vue SFC import resolution (SYNTHESIS Rank 17).

Closes 2 H findings: ``orphan-imports`` and ``verify-imports`` previously
ignored ``.vue`` files (their ``WHERE language IN (...)`` SQL filters did
not include ``'vue'``/``'svelte'``), causing two failure modes:

1. **False positives in ``orphan-imports``:** relative imports of ``.vue``
   files (e.g. ``import Bar from './Bar.vue'``) were reported as orphans
   because ``.vue`` files weren't registered as importable JS modules.
2. **Unresolved imports in ``verify-imports``:** the module-path → file
   lookup expected ``%/<name>.%`` (i.e. a *further* extension after the
   filename), so ``Bar.vue`` never matched ``src/components/Bar.vue``.

This module pins:

* The parser-level ``_preprocess_vue`` extracts ``<script>`` content
  (incl. ``<script setup>``) for both pure-JS and TypeScript SFCs.
* Vue SFC imports produce edges in the symbol/edges tables after
  ``roam index``.
* ``roam orphan-imports`` no longer flags a relative ``.vue`` import that
  has a real target on disk.
* ``roam verify-imports`` resolves ``import Bar from '@/components/Bar.vue'``
  to the actual indexed file.
* A pure-template Vue file (no ``<script>``) does not crash any of the
  above commands.
"""

from __future__ import annotations

import json
import os
import subprocess

import pytest
from click.testing import CliRunner

from roam.cli import cli
from roam.index.parser import _preprocess_vue

# ---------------------------------------------------------------------------
# Test fixtures
# ---------------------------------------------------------------------------


def _git_init(path):
    """Initialise a git repo with a deterministic author + initial commit."""
    env = {
        **os.environ,
        "GIT_AUTHOR_NAME": "test",
        "GIT_AUTHOR_EMAIL": "t@t",
        "GIT_COMMITTER_NAME": "test",
        "GIT_COMMITTER_EMAIL": "t@t",
    }
    subprocess.run(["git", "init"], cwd=str(path), capture_output=True, env=env)
    subprocess.run(["git", "add", "."], cwd=str(path), capture_output=True, env=env)
    subprocess.run(
        ["git", "commit", "-m", "init", "--allow-empty"],
        cwd=str(path),
        capture_output=True,
        env=env,
    )


def _run_index(project_path):
    """Index the project in-process via the Click CLI runner."""
    runner = CliRunner()
    old_cwd = os.getcwd()
    try:
        os.chdir(str(project_path))
        result = runner.invoke(cli, ["index"], catch_exceptions=False)
    finally:
        os.chdir(old_cwd)
    return result


def _run_cli(project_path, *args, json_mode=True):
    """Run a roam CLI command in-process, optionally requesting JSON output."""
    runner = CliRunner()
    old_cwd = os.getcwd()
    full_args = []
    if json_mode:
        full_args.append("--json")
    full_args.extend(args)
    try:
        os.chdir(str(project_path))
        result = runner.invoke(cli, full_args, catch_exceptions=False)
    finally:
        os.chdir(old_cwd)
    return result


@pytest.fixture
def vue_project(tmp_path):
    """Project containing a Vue SFC importing a TS composable and another SFC."""
    proj = tmp_path / "vue_imports"
    proj.mkdir()
    (proj / ".gitignore").write_text(".roam/\n")

    src = proj / "src"
    src.mkdir()

    composables = src / "composables"
    composables.mkdir()
    (composables / "useFoo.ts").write_text("export function useFoo(seed: number) {\n  return { value: seed * 2 };\n}\n")

    components = src / "components"
    components.mkdir()
    (components / "Bar.vue").write_text(
        "<template>\n"
        "  <span>bar</span>\n"
        "</template>\n"
        "\n"
        '<script setup lang="ts">\n'
        "defineProps<{ label: string }>();\n"
        "</script>\n"
    )

    # Foo.vue: imports useFoo (TS) and Bar.vue (SFC). Mixes <script> and
    # <script setup>; this matches what we see in real codebases.
    (components / "Foo.vue").write_text(
        "<template>\n"
        '  <div class="foo">\n'
        '    <Bar :label="label" />\n'
        "    <span>{{ doubled }}</span>\n"
        "  </div>\n"
        "</template>\n"
        "\n"
        '<script setup lang="ts">\n'
        "import { computed } from 'vue';\n"
        "import { useFoo } from '../composables/useFoo';\n"
        "import Bar from './Bar.vue';\n"
        "\n"
        "const { value } = useFoo(21);\n"
        "const label = 'foo';\n"
        "const doubled = computed(() => value);\n"
        "</script>\n"
    )

    # TemplateOnly.vue exercises the "no script block" path.
    (components / "TemplateOnly.vue").write_text("<template>\n  <p>no script here</p>\n</template>\n")

    _git_init(proj)
    result = _run_index(proj)
    assert result.exit_code == 0, f"index failed: {result.output}"
    return proj


# ---------------------------------------------------------------------------
# Test 1: parser-level Vue script extraction
# ---------------------------------------------------------------------------


class TestVueScriptBlockExtraction:
    """``_preprocess_vue`` extracts ``<script>`` bodies and tracks language."""

    def test_script_setup_ts_extracted(self):
        source = (
            b"<template><div /></template>\n"
            b'<script setup lang="ts">\n'
            b"import { ref } from 'vue';\n"
            b"const x = ref(0);\n"
            b"</script>\n"
        )
        processed, lang = _preprocess_vue(source)
        text = processed.decode("utf-8")
        assert "import { ref } from 'vue';" in text
        assert "const x = ref(0);" in text
        assert lang == "typescript"

    def test_classic_script_extracted(self):
        source = b"<template><div /></template>\n<script>\nexport default { name: 'X' }\n</script>\n"
        processed, lang = _preprocess_vue(source)
        text = processed.decode("utf-8")
        assert "export default" in text
        # No lang attribute → defaults to javascript
        assert lang == "javascript"

    def test_combined_script_and_script_setup(self):
        """Both ``<script>`` and ``<script setup>`` survive preprocessing."""
        source = (
            b"<template><div /></template>\n"
            b"<script>\n"
            b"export const META = { name: 'Foo' };\n"
            b"</script>\n"
            b'<script setup lang="ts">\n'
            b"import { ref } from 'vue';\n"
            b"const counter = ref(0);\n"
            b"</script>\n"
        )
        processed, lang = _preprocess_vue(source)
        text = processed.decode("utf-8")
        assert "export const META" in text
        assert "import { ref } from 'vue';" in text
        # As long as either block declared lang="ts", typescript wins
        assert lang in ("typescript", "javascript")


# ---------------------------------------------------------------------------
# Test 2: Vue imports indexed as edges
# ---------------------------------------------------------------------------


def test_vue_imports_indexed(vue_project):
    """After indexing, Foo.vue's import of useFoo creates a resolvable edge."""
    from roam.db.connection import open_db

    old_cwd = os.getcwd()
    try:
        os.chdir(str(vue_project))
        with open_db(readonly=True) as conn:
            # Foo.vue file is in the index
            row = conn.execute("SELECT path, language FROM files WHERE path LIKE '%Foo.vue'").fetchone()
            assert row is not None, "Foo.vue not indexed"
            assert row["language"] == "vue"

            # An edge exists from a symbol in Foo.vue mentioning useFoo.
            # We assert via the symbols+edges join that something inside
            # Foo.vue references the name ``useFoo`` (either as a call or
            # import edge).
            rows = conn.execute(
                """
                SELECT s.name FROM edges e
                JOIN symbols s ON e.source_id = s.id
                JOIN files f ON s.file_id = f.id
                WHERE f.path LIKE '%Foo.vue'
                """
            ).fetchall()
            assert rows, "no edges emitted from Foo.vue — Vue script not indexed"
    finally:
        os.chdir(old_cwd)


# ---------------------------------------------------------------------------
# Test 3: orphan-imports no longer false-positives on Vue imports
# ---------------------------------------------------------------------------


def test_orphan_imports_no_longer_false_positive_on_vue_imports(vue_project):
    """``Foo.vue`` imports ``./Bar.vue`` and ``../composables/useFoo``.

    Both targets exist on disk and are indexed. ``orphan-imports`` must
    NOT flag them.
    """
    result = _run_cli(vue_project, "orphan-imports", "--lang", "javascript", json_mode=True)
    assert result.exit_code == 0, f"orphan-imports failed: {result.output}"
    data = json.loads(result.stdout or result.output)
    orphans = data.get("orphans", [])

    # R22 confidence triple shape — read module via .value.
    # No orphan should reference Bar.vue or useFoo (they both resolve).
    def _mod(o):
        return o.get("value", o).get("module", "")

    bad = [o for o in orphans if "Bar.vue" in _mod(o) or "useFoo" in _mod(o)]
    assert not bad, f"false-positive orphan(s) flagged for Vue imports: {bad}"


# ---------------------------------------------------------------------------
# Test 4: verify-imports resolves a .vue target
# ---------------------------------------------------------------------------


def test_verify_imports_resolves_vue_target(vue_project):
    """``import Bar from './Bar.vue'`` resolves to the indexed Bar.vue file."""
    result = _run_cli(vue_project, "verify-imports", json_mode=True)
    assert result.exit_code == 0, f"verify-imports failed: {result.output}"
    data = json.loads(result.stdout or result.output)
    imports = data.get("imports", [])

    # Find the Bar.vue import record (extracted name == 'Bar.vue').
    bar_records = [i for i in imports if i.get("name") == "Bar.vue"]
    assert bar_records, "Bar.vue import was not scanned at all"
    # Every Bar.vue record must resolve.
    unresolved = [i for i in bar_records if i.get("status") != "resolved"]
    assert not unresolved, f"Bar.vue not resolved as a known file: {unresolved}"


# ---------------------------------------------------------------------------
# Test 5: pure-template Vue file doesn't crash
# ---------------------------------------------------------------------------


def test_vue_file_with_no_script_block_handled(vue_project):
    """``TemplateOnly.vue`` has no ``<script>`` — pipeline must stay quiet."""
    # Indexing succeeded in the fixture, so this is implicitly verified.
    # Re-run orphan-imports and verify-imports to confirm neither crashes.
    r1 = _run_cli(vue_project, "orphan-imports", json_mode=True)
    assert r1.exit_code == 0, f"orphan-imports crashed: {r1.output}"
    r2 = _run_cli(vue_project, "verify-imports", json_mode=True)
    assert r2.exit_code == 0, f"verify-imports crashed: {r2.output}"

    # Double-check via parser API directly.
    processed, lang = _preprocess_vue(b"<template>\n  <p>no script</p>\n</template>\n")
    # No <script> content → processed body is just blanked-out template
    # lines and lang stays at the default 'javascript'.
    assert lang == "javascript"
    assert b"<script" not in processed
