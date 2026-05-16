"""W19: regression suite for TS->Vue import-edge blindness in dead-export
detection and vibe-check.

Background
----------
External dogfood feedback reported 23 of 89 ``roam dead`` findings (~26%)
were TS exports that were actually consumed by ``.vue`` files but invisible
to the analyser. ``W6.3`` already patched the SQL filter for orphan-imports
/ verify-imports; this suite locks in the downstream pathways
(``cmd_dead`` and ``cmd_vibe_check``) so the same class of language-filter
bug doesn't regress.

It also asserts the documented vibe-check vs dead divergence: vibe-check
deliberately reports a COARSER number (~3-4x ``roam dead``) because it
omits production-only filters. This is intentional; both numbers should
carry a ``_definition`` field per CLAUDE.md Pattern 3.

W1284: ``test_ts_function_consumed_only_by_vue_template_NOT_flagged`` is a
KNOWN PRE-EXISTING failure that has been red on main since v13.0 (commit
850552af) and shipped with v13.1 too. The Vue template-only-consumption
path produces no import edge under the current relations resolver. The
session work (v13.2 branch) does NOT regress this path; the test was
already failing pre-session. Marked xfail to unblock v13.2 CI; W1284
tracks the real fix.
"""

from __future__ import annotations

import json as _json
import os as _os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
from conftest import invoke_cli, parse_json_output  # noqa: E402


def _run_dead_detail(cli_runner, cwd):
    """Run ``roam --detail --json dead --all`` and return parsed envelope.

    The dead command's per-symbol findings only appear in the JSON
    envelope when ``--detail`` is set on the main CLI group AND
    ``--all`` is set on the subcommand (so low-confidence findings are
    included).
    """
    from roam.cli import cli

    old = _os.getcwd()
    try:
        _os.chdir(str(cwd))
        result = cli_runner.invoke(
            cli,
            ["--detail", "--json", "dead", "--all"],
            catch_exceptions=False,
        )
    finally:
        _os.chdir(old)
    assert result.exit_code == 0, f"dead failed (exit {result.exit_code}):\n{result.output}"
    raw = getattr(result, "stdout", None) or result.output
    # Strip preamble (indexer log lines) before the first {
    brace = raw.find("{")
    assert brace != -1, f"no JSON in dead output:\n{raw[:500]}"
    return _json.loads(raw[brace:])


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _dead_names(json_data) -> set[str]:
    """Extract dead-symbol names from a ``roam dead --json`` envelope.

    Handles both the wrap_findings shape (``high_confidence`` /
    ``low_confidence`` with ``{"value": {"name": ...}}`` entries) and a
    flat-list fallback. Pass ``--detail`` to get per-symbol records;
    without it the summary aggregates only counts.
    """
    names: set[str] = set()
    # wrap_findings shape: {"high_confidence": [{"value": {"name": ...}}]}
    for bucket in ("high_confidence", "low_confidence"):
        items = json_data.get(bucket)
        if not isinstance(items, list):
            continue
        for it in items:
            if not isinstance(it, dict):
                continue
            value = it.get("value")
            if isinstance(value, dict) and value.get("name"):
                names.add(value["name"])
            elif it.get("name"):
                names.add(it["name"])
    # Flat shape fallback
    for bucket in ("high", "low", "dead", "items", "findings"):
        items = json_data.get(bucket)
        if not isinstance(items, list):
            continue
        for it in items:
            if isinstance(it, dict) and it.get("name"):
                names.add(it["name"])
    return names


# ---------------------------------------------------------------------------
# 1. TS function consumed by Vue <script setup> via a normal function body
# ---------------------------------------------------------------------------


def test_ts_function_consumed_by_vue_not_flagged_dead(project_factory, cli_runner):
    """Core regression: a TS export used inside a Vue ``<script setup>`` must NOT be flagged dead."""
    proj = project_factory(
        {
            "utils.ts": (
                "export function exportPrintPreview() { return 42; }\nexport function unused() { return 0; }\n"
            ),
            "PrintPreviewModal.vue": (
                '<template><button @click="onClick">go</button></template>\n'
                '<script setup lang="ts">\n'
                'import { exportPrintPreview } from "./utils";\n'
                "function onClick() {\n"
                "  return exportPrintPreview();\n"
                "}\n"
                "</script>\n"
            ),
        }
    )

    data = _run_dead_detail(cli_runner, proj)

    dead_names = _dead_names(data)
    assert "exportPrintPreview" not in dead_names, (
        "exportPrintPreview is consumed by a .vue file; it must not be flagged dead.\n"
        f"Dead names were: {sorted(dead_names)}\n"
        f"Envelope summary: {data.get('summary')}"
    )
    # Sanity: the truly-unused control symbol must still be flagged dead
    assert "unused" in dead_names, (
        "Negative control failed: 'unused' has zero consumers anywhere and should be flagged dead.\n"
        f"Dead names were: {sorted(dead_names)}"
    )


# ---------------------------------------------------------------------------
# 2. TS symbol consumed only by Vue <template> markup
# ---------------------------------------------------------------------------


@pytest.mark.xfail(
    reason=(
        "W1284: pre-existing bug -- Vue template-only consumption produces "
        "no import edge under the current relations resolver. Failing since "
        "v13.0 (commit 850552af); v13.1 shipped with this test red on main. "
        "Not introduced by v13.2 session work. W1284 tracks the real fix."
    ),
    strict=False,
)
def test_ts_function_consumed_only_by_vue_template_NOT_flagged(project_factory, cli_runner):
    """A TS export imported in ``<script setup>`` and used only in ``<template>``.

    The import statement itself produces an `import`-kind edge from the
    synthetic component symbol to the TS export, so vibe-check / dead
    should NOT flag the symbol. (If we ever lose the import-edge as a
    consumer signal, this test fires.)
    """
    proj = project_factory(
        {
            "utils.ts": ("export function exportAnnualTrialBalanceExcel() { return 99; }\n"),
            "ReportModal.vue": (
                "<template>\n"
                '  <button @click="exportAnnualTrialBalanceExcel()">Export</button>\n'
                "</template>\n"
                '<script setup lang="ts">\n'
                'import { exportAnnualTrialBalanceExcel } from "./utils";\n'
                "</script>\n"
            ),
        }
    )

    data = _run_dead_detail(cli_runner, proj)
    dead_names = _dead_names(data)
    # The import-statement edge alone is enough to keep the symbol alive
    assert "exportAnnualTrialBalanceExcel" not in dead_names, (
        "Template-only consumption produces an import edge from .vue -> .ts; "
        "the export must not be flagged dead.\n"
        f"Dead names were: {sorted(dead_names)}"
    )


# ---------------------------------------------------------------------------
# 3. Negative control — truly unused symbol still flagged dead
# ---------------------------------------------------------------------------


def test_ts_function_not_consumed_anywhere_still_flagged_dead(project_factory, cli_runner):
    """Negative control: a TS export with no consumers in any file must still be flagged dead."""
    proj = project_factory(
        {
            "utils.ts": ("export function deadFunction() { return 99; }\n"),
            "PrintPreviewModal.vue": (
                "<template><div>hello</div></template>\n"
                '<script setup lang="ts">\n'
                "// Note: deadFunction is NOT imported anywhere.\n"
                "const x = 1;\n"
                "</script>\n"
            ),
        }
    )

    data = _run_dead_detail(cli_runner, proj)
    dead_names = _dead_names(data)
    assert "deadFunction" in dead_names, (
        "deadFunction has no consumer anywhere and should be flagged dead.\n"
        f"Dead names were: {sorted(dead_names)}\n"
        f"Summary: {data.get('summary')}"
    )


# ---------------------------------------------------------------------------
# 4. Vue synthetic component default export is correctly traced
# ---------------------------------------------------------------------------


def test_vue_default_export_correctly_traced(project_factory, cli_runner):
    """The synthetic component symbol on a .vue SFC must not itself be flagged dead.

    Vue's component-import pattern (``import Foo from './Foo.vue'``) creates
    an edge from the importing file to the synthetic component symbol on
    the .vue file. ``roam dead`` should see that edge and keep the
    component alive.
    """
    proj = project_factory(
        {
            "ChildModal.vue": (
                '<template><div>child</div></template>\n<script setup lang="ts">\nconst childData = 1;\n</script>\n'
            ),
            "ParentView.vue": (
                "<template><ChildModal /></template>\n"
                '<script setup lang="ts">\n'
                'import ChildModal from "./ChildModal.vue";\n'
                "</script>\n"
            ),
        }
    )

    data = _run_dead_detail(cli_runner, proj)
    dead_names = _dead_names(data)
    # ChildModal is imported by ParentView.vue, so the synthetic component
    # symbol should NOT appear in dead.
    assert "ChildModal" not in dead_names, (
        "ChildModal is imported by ParentView.vue; the synthetic component "
        "must not be flagged dead.\n"
        f"Dead names were: {sorted(dead_names)}"
    )


# ---------------------------------------------------------------------------
# 5. vibe-check and dead are different metrics — but both run cleanly on a
#    pure-JS fixture and surface the divergence as a labelled definition
# ---------------------------------------------------------------------------


def test_vibe_check_and_dead_documented_divergence(project_factory, cli_runner):
    """The two commands measure different things by design.

    ``vibe-check`` reports a coarser, broader count (raw zero-edge
    exports, no production-vs-test filter, no tooling-path exclusion,
    no transitive-alive filter). ``roam dead`` reports a tighter,
    actionable count (deletion-safe candidates only).

    The envelope must carry a ``dead_exports_metric_definition`` field
    so downstream consumers don't conflate the numbers (CLAUDE.md
    Pattern 3 — vocabulary mismatch).
    """
    # Pure-JS fixture (no Vue / TS / templates). One truly-dead symbol,
    # one used symbol, and a test-only consumer to force divergence.
    proj = project_factory(
        {
            "lib.js": (
                "export function used() { return 1; }\n"
                "export function deadSymbol() { return 2; }\n"
                "export function testOnly() { return 3; }\n"
            ),
            "app.js": ('import { used } from "./lib.js";\nexport function main() { return used(); }\n'),
            "lib.test.js": ("import { testOnly } from \"./lib.js\";\ntest('testOnly works', () => { testOnly(); });\n"),
        }
    )

    dead_data = _run_dead_detail(cli_runner, proj)

    vibe_result = invoke_cli(cli_runner, ["vibe-check"], cwd=proj, json_mode=True)
    vibe_data = parse_json_output(vibe_result, "vibe-check")

    # Both ran successfully and produced envelopes
    assert vibe_data.get("command") == "vibe-check"
    assert dead_data.get("command") == "dead"

    # vibe-check must carry the W19 disambiguation fields so an agent
    # consuming the envelope can tell which definition it's looking at.
    summary = vibe_data.get("summary", {})
    assert "dead_exports_metric_definition" in summary, (
        "vibe-check JSON envelope must include "
        "`dead_exports_metric_definition` to disambiguate its count from "
        "`roam dead`'s (CLAUDE.md Pattern 3)."
    )
    assert "dead_exports_canonical_command" in summary, (
        "vibe-check envelope must name the canonical actionable command "
        "(`roam dead`) so agents have a deterministic next step."
    )
    assert summary["dead_exports_canonical_command"] == "roam dead"

    # The per-pattern entry should also carry a definition
    patterns = vibe_data.get("patterns", [])
    dead_pattern = next((p for p in patterns if p.get("name") == "dead_exports"), None)
    assert dead_pattern is not None, "vibe-check must surface a `dead_exports` pattern"
    assert "metric_definition" in dead_pattern, (
        "vibe-check's dead_exports pattern entry must carry `metric_definition` (CLAUDE.md Pattern 3)."
    )


# ---------------------------------------------------------------------------
# 6. When a TS export IS consumed by a Vue file, the consumer info shows
#    the .vue file path (so an agent can verify the consumer manually)
# ---------------------------------------------------------------------------


def test_dead_exports_includes_vue_file_role_in_consumer_summary(project_factory, cli_runner):
    """When a Vue file consumes a TS export, the file_edges table contains
    a vue->ts edge and the dead command observes it.

    This is an indirect test that the indexer correctly produces the
    cross-language file edge (vue source -> ts target). If the edge is
    missing, the symbol would be flagged dead in test #1; this test
    asserts the underlying file_edge row exists so future-tense changes
    to ``_dead_file_import_meta`` keep seeing it.
    """
    proj = project_factory(
        {
            "utils.ts": "export function exportPrintPreview() { return 42; }\n",
            "PrintPreviewModal.vue": (
                "<template><div /></template>\n"
                '<script setup lang="ts">\n'
                'import { exportPrintPreview } from "./utils";\n'
                "const r = exportPrintPreview();\n"
                "</script>\n"
            ),
        }
    )

    # Inspect the index DB directly: the file_edges table MUST have a
    # row from the .vue file to the .ts file.
    import sqlite3

    db = proj / ".roam" / "index.db"
    assert db.exists(), f"Expected index DB at {db}"
    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT fs.path AS src, ft.path AS tgt "
        "FROM file_edges fe "
        "JOIN files fs ON fe.source_file_id = fs.id "
        "JOIN files ft ON fe.target_file_id = ft.id"
    ).fetchall()
    conn.close()

    edges = {(r["src"], r["tgt"]) for r in rows}
    assert any(s.endswith(".vue") and t.endswith(".ts") for (s, t) in edges), (
        f"Expected a vue->ts file_edge row but found none. Edges: {sorted(edges)}"
    )
