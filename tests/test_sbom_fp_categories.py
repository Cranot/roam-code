"""Tests closing 5 systematic false-positive categories in `roam sbom`.

Each category historically inflated the phantom-dep count on real vue-vite
projects (external dogfood reported 33/39 phantoms — a 94% FP rate). These tests
pin the fix per category PLUS composer.json (PHP backend) ingestion.

Categories:

* A — CSS side-effect imports (``@import "primeicons/primeicons.css"``)
* B — Dynamic imports (``await import("microsoft-cognitiveservices-speech-sdk")``)
* C — Config-file imports (``vite.config.ts``, ``tsconfig.json:extends``)
* D — package.json script consumers (``rimraf dist``)
* E — Peer / loader deps (``jiti`` for ESLint TS flat config, ``@types/*``)

Plus: composer.json (PHP) at root and 1-deep subdir.
"""

from __future__ import annotations

import json
from pathlib import Path

from roam.security.sbom_reachability import (
    _BIN_TO_PACKAGE,
    _KNOWN_TS_LOADERS,
    compute_filesystem_reachability,
    merge_reachability,
    parse_composer_json,
)

# ---------------------------------------------------------------------------
# Per-category unit tests (pure functions, no DB needed)
# ---------------------------------------------------------------------------


def _make_vue_vite_skeleton(root: Path) -> None:
    """Synthesize a minimal vue-vite-like project layout under ``root``."""
    root.mkdir(parents=True, exist_ok=True)
    (root / "src").mkdir(exist_ok=True)
    (root / "src" / "main.ts").write_text(
        "import { createApp } from 'vue'\n"
        "import App from './App.vue'\n"
        "import './main.css'\n"
        "createApp(App).mount('#app')\n",
        encoding="utf-8",
    )


# ---------------------------------------------------------------------------
# Category A — CSS imports
# ---------------------------------------------------------------------------


def test_css_import_makes_dep_reachable(tmp_path: Path) -> None:
    _make_vue_vite_skeleton(tmp_path)
    (tmp_path / "src" / "main.css").write_text(
        '@import "primeicons/primeicons.css";\nbody { margin: 0; }\n',
        encoding="utf-8",
    )
    result = compute_filesystem_reachability(tmp_path, ["primeicons", "vue"])
    assert result["primeicons"]["reachable"] is True
    assert result["primeicons"]["confidence"] == "css_import"
    assert any("primeicons" in s for s in result["primeicons"]["sources"])


def test_vue_style_block_import(tmp_path: Path) -> None:
    _make_vue_vite_skeleton(tmp_path)
    (tmp_path / "src" / "App.vue").write_text(
        '<template><div>Hi</div></template>\n<style scoped>@import "primeicons/primeicons.css";</style>\n',
        encoding="utf-8",
    )
    result = compute_filesystem_reachability(tmp_path, ["primeicons"])
    assert result["primeicons"]["reachable"] is True
    assert result["primeicons"]["confidence"] == "css_import"


def test_scss_import_traced(tmp_path: Path) -> None:
    _make_vue_vite_skeleton(tmp_path)
    (tmp_path / "src" / "main.scss").write_text(
        "@import 'bootstrap/scss/bootstrap';\n",
        encoding="utf-8",
    )
    result = compute_filesystem_reachability(tmp_path, ["bootstrap"])
    assert result["bootstrap"]["reachable"] is True


# ---------------------------------------------------------------------------
# Category B — Dynamic imports
# ---------------------------------------------------------------------------


def test_dynamic_import_string_literal_traced(tmp_path: Path) -> None:
    _make_vue_vite_skeleton(tmp_path)
    (tmp_path / "src" / "speech.ts").write_text(
        "async function load() {\n"
        '  const sdk = await import("microsoft-cognitiveservices-speech-sdk");\n'
        "  return sdk;\n"
        "}\n",
        encoding="utf-8",
    )
    result = compute_filesystem_reachability(tmp_path, ["microsoft-cognitiveservices-speech-sdk"])
    info = result["microsoft-cognitiveservices-speech-sdk"]
    assert info["reachable"] is True
    assert info["confidence"] == "dynamic_import"


def test_dynamic_import_template_literal_traced(tmp_path: Path) -> None:
    _make_vue_vite_skeleton(tmp_path)
    (tmp_path / "src" / "viz.ts").write_text(
        "const v = import(`rollup-plugin-visualizer`);\n",
        encoding="utf-8",
    )
    result = compute_filesystem_reachability(tmp_path, ["rollup-plugin-visualizer"])
    assert result["rollup-plugin-visualizer"]["reachable"] is True


# ---------------------------------------------------------------------------
# Category C — Config-file imports
# ---------------------------------------------------------------------------


def test_vite_config_plugin_traced(tmp_path: Path) -> None:
    _make_vue_vite_skeleton(tmp_path)
    (tmp_path / "vite.config.ts").write_text(
        "import { defineConfig } from 'vite'\n"
        "import vue from '@vitejs/plugin-vue'\n"
        "import compression from 'vite-plugin-compression2'\n"
        "export default defineConfig({\n"
        "  plugins: [vue(), compression()],\n"
        "})\n",
        encoding="utf-8",
    )
    result = compute_filesystem_reachability(tmp_path, ["@vitejs/plugin-vue", "vite-plugin-compression2", "vite"])
    assert result["@vitejs/plugin-vue"]["reachable"] is True
    assert result["@vitejs/plugin-vue"]["confidence"] == "config_import"
    assert result["vite-plugin-compression2"]["reachable"] is True
    assert result["vite"]["reachable"] is True


def test_tsconfig_extends_traced(tmp_path: Path) -> None:
    _make_vue_vite_skeleton(tmp_path)
    (tmp_path / "tsconfig.json").write_text(
        json.dumps(
            {
                "extends": "@vue/tsconfig/tsconfig.dom.json",
                "compilerOptions": {"types": ["node", "vitest/globals"]},
            }
        ),
        encoding="utf-8",
    )
    result = compute_filesystem_reachability(tmp_path, ["@vue/tsconfig", "@types/node", "vitest"])
    assert result["@vue/tsconfig"]["reachable"] is True
    assert result["@vue/tsconfig"]["confidence"] == "config_import"
    # `types: ["node"]` should map to `@types/node`
    assert result["@types/node"]["reachable"] is True


def test_eslint_config_ts_plugin_traced(tmp_path: Path) -> None:
    _make_vue_vite_skeleton(tmp_path)
    (tmp_path / "eslint.config.ts").write_text(
        "import vue from 'eslint-plugin-vue'\n"
        "import ts from '@typescript-eslint/eslint-plugin'\n"
        "export default [vue.configs.recommended, ts.configs.recommended]\n",
        encoding="utf-8",
    )
    result = compute_filesystem_reachability(tmp_path, ["eslint-plugin-vue", "@typescript-eslint/eslint-plugin"])
    assert result["eslint-plugin-vue"]["reachable"] is True
    assert result["@typescript-eslint/eslint-plugin"]["reachable"] is True


# ---------------------------------------------------------------------------
# Category D — package.json scripts
# ---------------------------------------------------------------------------


def test_package_json_script_consumer(tmp_path: Path) -> None:
    _make_vue_vite_skeleton(tmp_path)
    (tmp_path / "package.json").write_text(
        json.dumps(
            {
                "name": "demo",
                "scripts": {
                    "clean": "rimraf dist",
                    "test": "vitest run",
                    "type-check": "vue-tsc --noEmit",
                },
                "devDependencies": {
                    "rimraf": "^5.0.0",
                    "vitest": "^1.0.0",
                    "vue-tsc": "^2.0.0",
                },
            }
        ),
        encoding="utf-8",
    )
    result = compute_filesystem_reachability(tmp_path, ["rimraf", "vitest", "vue-tsc"])
    assert result["rimraf"]["reachable"] is True
    assert result["rimraf"]["confidence"] == "script_consumer"
    assert result["vitest"]["reachable"] is True
    assert result["vue-tsc"]["reachable"] is True


def test_run_p_alias_resolves_to_npm_run_all2(tmp_path: Path) -> None:
    _make_vue_vite_skeleton(tmp_path)
    (tmp_path / "package.json").write_text(
        json.dumps(
            {
                "name": "demo",
                "scripts": {
                    "dev": "run-p dev:client dev:server",
                },
                "devDependencies": {"npm-run-all2": "^6.0.0"},
            }
        ),
        encoding="utf-8",
    )
    # Sanity-check the alias map
    assert _BIN_TO_PACKAGE.get("run-p") == "npm-run-all2"
    result = compute_filesystem_reachability(tmp_path, ["npm-run-all2"])
    assert result["npm-run-all2"]["reachable"] is True
    assert result["npm-run-all2"]["confidence"] == "script_consumer"


def test_husky_script_consumer(tmp_path: Path) -> None:
    _make_vue_vite_skeleton(tmp_path)
    (tmp_path / "package.json").write_text(
        json.dumps(
            {
                "name": "demo",
                "scripts": {"prepare": "husky install"},
                "devDependencies": {"husky": "^9.0.0"},
            }
        ),
        encoding="utf-8",
    )
    result = compute_filesystem_reachability(tmp_path, ["husky"])
    assert result["husky"]["reachable"] is True


# ---------------------------------------------------------------------------
# Category E — Loader / peer deps
# ---------------------------------------------------------------------------


def test_jiti_recognized_as_eslint_loader(tmp_path: Path) -> None:
    _make_vue_vite_skeleton(tmp_path)
    (tmp_path / "eslint.config.ts").write_text("export default []\n", encoding="utf-8")
    # Confirm presence of jiti in our loader set
    assert "jiti" in _KNOWN_TS_LOADERS
    result = compute_filesystem_reachability(tmp_path, ["jiti"])
    assert result["jiti"]["reachable"] is True
    assert result["jiti"]["confidence"] == "loader"


def test_types_packages_always_reachable(tmp_path: Path) -> None:
    _make_vue_vite_skeleton(tmp_path)
    (tmp_path / "tsconfig.json").write_text(json.dumps({"compilerOptions": {}}), encoding="utf-8")
    result = compute_filesystem_reachability(tmp_path, ["@types/node", "@types/lodash"])
    assert result["@types/node"]["reachable"] is True
    assert result["@types/lodash"]["reachable"] is True
    assert result["@types/node"]["confidence"] == "loader"


def test_loader_not_marked_when_no_ts(tmp_path: Path) -> None:
    # Plain JS-only project — jiti should NOT auto-mark reachable
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "main.js").write_text("console.log('hi')\n", encoding="utf-8")
    result = compute_filesystem_reachability(tmp_path, ["jiti"])
    # jiti is declared but no TS files -> still reachable=False
    assert result["jiti"]["reachable"] is False


# ---------------------------------------------------------------------------
# composer.json (PHP) ingestion
# ---------------------------------------------------------------------------


def test_composer_json_at_root_ingested(tmp_path: Path) -> None:
    (tmp_path / "composer.json").write_text(
        json.dumps(
            {
                "name": "vendor/app",
                "require": {
                    "php": ">=8.1",
                    "ext-pdo": "*",
                    "symfony/console": "^6.0",
                    "guzzlehttp/guzzle": "^7.5",
                },
                "require-dev": {
                    "phpunit/phpunit": "^10.0",
                },
            }
        ),
        encoding="utf-8",
    )

    # Direct parser hit
    entries = parse_composer_json(tmp_path / "composer.json")
    names = {e[0] for e in entries}
    assert "symfony/console" in names
    assert "guzzlehttp/guzzle" in names
    assert "phpunit/phpunit" in names
    # php + ext-pdo filtered out (platform deps, not packages)
    assert "php" not in names
    assert "ext-pdo" not in names

    # supply-chain discovery path
    from roam.commands.cmd_supply_chain import discover_and_parse

    deps = discover_and_parse(tmp_path)
    php_deps = [d for d in deps if d.ecosystem == "php"]
    assert len(php_deps) >= 3
    php_names = {d.name for d in php_deps}
    assert "symfony/console" in php_names
    assert "guzzlehttp/guzzle" in php_names
    # phpunit/phpunit should be flagged dev
    phpunit = next(d for d in deps if d.name == "phpunit/phpunit")
    assert phpunit.is_dev is True


def test_composer_json_in_subdir_ingested(tmp_path: Path) -> None:
    # Simulate a real-world layout: PHP backend at ./accounting-backend/
    backend = tmp_path / "accounting-backend"
    backend.mkdir()
    (backend / "composer.json").write_text(
        json.dumps(
            {
                "name": "example/accounting-backend",
                "require": {
                    "php": ">=8.1",
                    "laravel/framework": "^10.0",
                },
            }
        ),
        encoding="utf-8",
    )

    from roam.commands.cmd_supply_chain import discover_and_parse

    deps = discover_and_parse(tmp_path)
    laravel = next((d for d in deps if d.name == "laravel/framework"), None)
    assert laravel is not None, f"Expected laravel/framework, got: {[d.name for d in deps]}"
    assert laravel.ecosystem == "php"
    assert laravel.source_file.endswith("composer.json")


# ---------------------------------------------------------------------------
# Synthetic vue-vite-like fixture — end-to-end FP-rate regression test
# ---------------------------------------------------------------------------


def test_phantom_count_drops_dramatically_on_vue_vite_like_fixture(tmp_path: Path) -> None:
    """Synthesize a vue-vite-like layout with FP-prone deps that historically
    inflated phantom counts on real projects. Assert filesystem reachability
    claims most of them.

    The historical phantom rate on the dogfood target was 33/39 = 84.6%. After the
    5-category fix, fewer than 20% of declared deps should still look
    phantom on this synthetic fixture.
    """
    _make_vue_vite_skeleton(tmp_path)

    # --- Source files exercising each category ---
    (tmp_path / "src" / "main.css").write_text(
        '@import "primeicons/primeicons.css";\n@import "tailwindcss/tailwind.css";\n',
        encoding="utf-8",
    )
    (tmp_path / "src" / "App.vue").write_text(
        '<template><div/></template>\n<style>@import "primeicons/primeicons.css";</style>\n',
        encoding="utf-8",
    )
    (tmp_path / "src" / "speech.ts").write_text(
        'await import("microsoft-cognitiveservices-speech-sdk");\n',
        encoding="utf-8",
    )

    # --- Config files exercising Category C ---
    (tmp_path / "vite.config.ts").write_text(
        "import { defineConfig } from 'vite'\n"
        "import vue from '@vitejs/plugin-vue'\n"
        "import { visualizer } from 'rollup-plugin-visualizer'\n"
        "import compression from 'vite-plugin-compression2'\n"
        "export default defineConfig({ plugins: [vue(), visualizer(), compression()] })\n",
        encoding="utf-8",
    )
    (tmp_path / "tsconfig.json").write_text(
        json.dumps(
            {
                "extends": "@vue/tsconfig/tsconfig.dom.json",
                "compilerOptions": {"types": ["node"]},
            }
        ),
        encoding="utf-8",
    )
    (tmp_path / "eslint.config.ts").write_text(
        "import vue from 'eslint-plugin-vue'\nexport default [vue.configs.recommended]\n",
        encoding="utf-8",
    )
    (tmp_path / "postcss.config.js").write_text(
        "module.exports = { plugins: { tailwindcss: {}, autoprefixer: {} } }\n",
        encoding="utf-8",
    )
    (tmp_path / "tailwind.config.ts").write_text(
        "import typography from '@tailwindcss/typography'\nexport default { plugins: [typography] }\n",
        encoding="utf-8",
    )
    (tmp_path / "cypress.config.ts").write_text(
        "import { defineConfig } from 'cypress'\nexport default defineConfig({})\n",
        encoding="utf-8",
    )

    # --- package.json with scripts (Category D) + the full dep set ---
    declared = {
        # Category A
        "primeicons": "^7.0.0",
        "tailwindcss": "^3.4.0",
        # Category B
        "microsoft-cognitiveservices-speech-sdk": "^1.0.0",
        "rollup-plugin-visualizer": "^5.0.0",
        "vite-plugin-compression2": "^1.0.0",
        # Category C
        "vite": "^5.0.0",
        "@vitejs/plugin-vue": "^5.0.0",
        "@vue/tsconfig": "^0.5.0",
        "eslint-plugin-vue": "^9.0.0",
        "@tailwindcss/typography": "^0.5.0",
        "cypress": "^13.0.0",
        # Category D — scripts
        "rimraf": "^5.0.0",
        "vitest": "^1.0.0",
        "vue-tsc": "^2.0.0",
        "npm-run-all2": "^6.0.0",
        "husky": "^9.0.0",
        # Category E — loaders/types
        "jiti": "^1.0.0",
        "@types/node": "^20.0.0",
        # Always-on
        "vue": "^3.4.0",
    }
    (tmp_path / "package.json").write_text(
        json.dumps(
            {
                "name": "synthetic",
                "scripts": {
                    "clean": "rimraf dist",
                    "test": "vitest run",
                    "type-check": "vue-tsc --noEmit",
                    "dev": "run-p watch type-check",
                    "prepare": "husky install",
                },
                "devDependencies": declared,
            }
        ),
        encoding="utf-8",
    )

    dep_names = list(declared.keys())
    result = compute_filesystem_reachability(tmp_path, dep_names)
    phantom = [name for name in dep_names if not result[name]["reachable"]]
    phantom_pct = len(phantom) / len(dep_names)

    # Diagnostic on failure — print which deps are still phantom
    assert phantom_pct < 0.20, (
        f"Phantom rate {phantom_pct:.1%} ({len(phantom)}/{len(dep_names)}) "
        f"exceeds 20% threshold. Still phantom: {phantom}"
    )


# ---------------------------------------------------------------------------
# Category G — plain Python + JS/TS source imports (label by the hit's own
# ecosystem; a JS/TS hit must NOT be mislabelled ``python import``).
# ---------------------------------------------------------------------------


def test_source_import_python_dep_labelled_python(tmp_path: Path) -> None:
    (tmp_path / "main.py").write_text("import requests\n", encoding="utf-8")
    result = compute_filesystem_reachability(tmp_path, ["requests"])
    assert result["requests"]["reachable"] is True
    assert result["requests"]["confidence"] == "direct"
    assert any(s.startswith("python import ") for s in result["requests"]["sources"])


def test_source_import_jsts_dep_labelled_jsts_not_python(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir(exist_ok=True)
    (tmp_path / "src" / "app.ts").write_text("import axios from 'axios'\n", encoding="utf-8")
    result = compute_filesystem_reachability(tmp_path, ["axios"])
    assert result["axios"]["reachable"] is True
    assert result["axios"]["confidence"] == "direct"
    sources = result["axios"]["sources"]
    assert any(s.startswith("js/ts import ") for s in sources), sources
    # the exact regression: a .ts hit is never labelled a python import
    assert not any(s.startswith("python import ") for s in sources), sources


def test_filesystem_reachability_surfaces_scan_truncation(tmp_path: Path) -> None:
    for index in range(3):
        (tmp_path / f"app{index}.js").write_text(f"import 'dependency-{index}'\n", encoding="utf-8")

    result = compute_filesystem_reachability(tmp_path, ["dependency-2"], max_files=2)

    assert result["_scan_truncated"] == {"truncated": True, "max_files": 2, "caps_hit": [2]}
    assert merge_reachability(None, result)["_scan_truncated"] == result["_scan_truncated"]


def test_filesystem_reachability_reports_complete_scan_below_cap(tmp_path: Path) -> None:
    (tmp_path / "app.js").write_text("import 'dependency'\n", encoding="utf-8")

    result = compute_filesystem_reachability(tmp_path, ["dependency"], max_files=2)

    assert result["_scan_truncated"] == {"truncated": False, "max_files": None, "caps_hit": []}


# ---------------------------------------------------------------------------
# merge_reachability — graph + fs fusion
# ---------------------------------------------------------------------------


def test_merge_prefers_direct_over_heuristic() -> None:
    graph = {
        "vue": {"reachable": True, "entry_points": ["main"], "matched_symbols": ["createApp"]},
        "rimraf": {"reachable": False, "entry_points": [], "matched_symbols": []},
    }
    fs = {
        "vue": {"reachable": False, "sources": [], "confidence": "indirect"},
        "rimraf": {
            "reachable": True,
            "sources": ["package.json:scripts.clean"],
            "confidence": "script_consumer",
        },
    }
    merged = merge_reachability(graph, fs)
    assert merged["vue"]["reachable"] is True
    assert merged["vue"]["confidence"] == "direct"
    assert merged["rimraf"]["reachable"] is True
    assert merged["rimraf"]["confidence"] == "script_consumer"


def test_merge_handles_missing_inputs() -> None:
    # Either side may be None / empty
    merged = merge_reachability(None, {"x": {"reachable": True, "sources": ["a"], "confidence": "css_import"}})
    assert merged["x"]["reachable"] is True
    merged2 = merge_reachability({"y": {"reachable": True, "entry_points": [], "matched_symbols": []}}, None)
    assert merged2["y"]["reachable"] is True
    assert merged2["y"]["confidence"] == "direct"


# ---------------------------------------------------------------------------
# W18.2 LAW 12 — confidence bucketing in `roam sbom` summary + verdict
#
# The reachability dict tags every match with a 6-level confidence label.
# An agent reading only the verdict / summary cannot tell a graph-traced
# (``direct``) hit apart from a filesystem deduction (``config_import``,
# ``script_consumer``, ``loader``, ``css_import``, ``dynamic_import``).
# These tests pin the 2-macro-bucket collapse: ``direct`` vs ``heuristic``.
# ---------------------------------------------------------------------------


def _make_minimal_sbom_project(root: Path) -> None:
    """Build a tiny project that exercises both buckets via ``roam sbom``.

    * ``primeicons`` and ``rimraf`` are *heuristic* — reached only through
      CSS @import + package.json scripts respectively, NEVER through the
      symbol graph.
    * ``vue`` and ``axios`` are *graph-direct* — imported in source files
      that the indexer can statically resolve.
    * ``unused-pkg`` is a *phantom* — declared but never referenced.
    """
    root.mkdir(parents=True, exist_ok=True)
    (root / "src").mkdir(exist_ok=True)
    (root / "src" / "main.ts").write_text(
        "import { createApp } from 'vue'\n"
        "import axios from 'axios'\n"
        "import './main.css'\n"
        "createApp({}).mount('#app')\n"
        "axios.get('/')\n",
        encoding="utf-8",
    )
    (root / "src" / "main.css").write_text(
        '@import "primeicons/primeicons.css";\n',
        encoding="utf-8",
    )
    (root / "package.json").write_text(
        json.dumps(
            {
                "name": "demo",
                "scripts": {"clean": "rimraf dist"},
                "dependencies": {"vue": "^3.4.0", "axios": "^1.0.0"},
                "devDependencies": {
                    "primeicons": "^7.0.0",
                    "rimraf": "^5.0.0",
                    "unused-pkg": "^1.0.0",
                },
            }
        ),
        encoding="utf-8",
    )
    # Initialise git so find_project_root discovers it.
    (root / ".git").mkdir(exist_ok=True)
    (root / ".gitignore").write_text("node_modules/\n.roam/\n", encoding="utf-8")


def _invoke_sbom_json(root: Path) -> dict:
    """Run ``roam --json sbom`` against *root* and return the parsed envelope.

    Runs ``roam init`` first so ``ensure_index()`` inside sbom doesn't emit
    the "No roam index found" banner ahead of the JSON envelope. After
    init, sbom's stdout is clean JSON.
    """
    import os

    from click.testing import CliRunner

    from roam.cli import cli

    runner = CliRunner()
    cwd = os.getcwd()
    try:
        os.chdir(root)
        init_result = runner.invoke(cli, ["init"])
        assert init_result.exit_code == 0, (
            f"roam init failed: exit={init_result.exit_code} output={init_result.output!r}"
        )
        result = runner.invoke(cli, ["--json", "sbom"])
    finally:
        os.chdir(cwd)
    assert result.exit_code == 0, f"roam sbom failed: exit={result.exit_code} output={result.output!r}"
    return json.loads(result.output)


def test_summary_includes_reachable_direct_and_heuristic_counts(tmp_path: Path) -> None:
    """W18.2: summary must carry both ``reachable_direct_count`` and
    ``reachable_heuristic_count`` so consumers can bucket the verdict by
    evidence strength."""
    _make_minimal_sbom_project(tmp_path)
    env = _invoke_sbom_json(tmp_path)
    summary = env["summary"]
    # Both new fields must be present + integer-valued (reachability ran).
    assert "reachable_direct_count" in summary, summary
    assert "reachable_heuristic_count" in summary, summary
    assert isinstance(summary["reachable_direct_count"], int), summary
    assert isinstance(summary["reachable_heuristic_count"], int), summary
    # Their sum must equal the total reachable count (no double-counting).
    assert summary["reachable_direct_count"] + summary["reachable_heuristic_count"] == summary["reachable_count"], (
        summary
    )


def test_verdict_buckets_direct_vs_heuristic(tmp_path: Path) -> None:
    """The verdict string itself must surface the bucket breakdown so an
    agent that reads only ``summary.verdict`` (per LAW 6 compression
    survives) still sees the evidence-strength split."""
    _make_minimal_sbom_project(tmp_path)
    env = _invoke_sbom_json(tmp_path)
    verdict = env["summary"]["verdict"]
    # Verdict format: "X reachable (D direct, H heuristic), Y phantom".
    assert "direct" in verdict, verdict
    assert "heuristic" in verdict, verdict
    assert "phantom" in verdict, verdict
    # Heuristic count must be non-zero — rimraf (script) + primeicons
    # (css) are both heuristic-only hits in the fixture.
    assert env["summary"]["reachable_heuristic_count"] >= 1, env["summary"]


def test_facts_anchor_on_concrete_phantom_count(tmp_path: Path) -> None:
    """LAW 4: ``agent_contract.facts`` must name the analytical subject in
    every fact. W18.2's report flagged the OLD verdict made a
    ``config_import`` deduction look as authoritative as a graph hit —
    so the new facts must explicitly say *phantom packages*,
    *via heuristic*, *directly imported*."""
    _make_minimal_sbom_project(tmp_path)
    env = _invoke_sbom_json(tmp_path)
    facts = env["agent_contract"]["facts"]
    joined = " | ".join(facts).lower()
    # Each macro-bucket must have a concrete-noun fact.
    assert "phantom packages" in joined, facts
    assert "directly imported" in joined, facts
    assert "heuristic" in joined, facts
    # And the bare verdict (LAW 3 — verdict-first) must still be the
    # first fact entry so an agent reading only facts[0] gets the verdict.
    assert "reachable" in facts[0].lower(), facts


def test_reachable_count_field_preserved(tmp_path: Path) -> None:
    """The new buckets are ADDITIVE — pre-W18.2 consumers reading the old
    ``reachable_count`` / ``phantom_count`` fields must keep working."""
    _make_minimal_sbom_project(tmp_path)
    env = _invoke_sbom_json(tmp_path)
    summary = env["summary"]
    assert "reachable_count" in summary
    assert "phantom_count" in summary
    assert summary["reachable_count"] is not None
    assert summary["phantom_count"] is not None
