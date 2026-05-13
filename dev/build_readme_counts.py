"""Auto-generate count numbers across README + CLAUDE + llms-install + mcp-server-card.

Eliminates the count-drift class that has churned every sprint that added a
command (213 to 217 to 223 to 226 to 229 to 232 to 233 ...). Each wave the
counts in README.md, CLAUDE.md, llms-install.md, and the public MCP server
card had to be hand-bumped, and the test only caught the discrepancy after
the fact.

See also: ``scripts/sync_surface_counts.py``
    The two scripts are intentional cousins, not duplicates. This one
    (``build_readme_counts.py``) owns **marker-protected Markdown
    blocks** in README.md / CLAUDE.md / llms-install.md and the **two
    mcp-server-card.json files** (which must stay byte-identical per
    ``test_bundled_card_matches_public_card``).
    ``sync_surface_counts.py`` owns **free-form prose surfaces** that
    do not have markers: landing-page HTML, llms.txt, server.json,
    ``skills/roam/SKILL.md``, ``competitor_site_data.py``,
    ``docs/ci-integration.md``. The mcp-server-card entries in
    ``sync_surface_counts.py`` are intentionally no-op (``repl=None``);
    this script is the sole writer of those cards.

CI runs both scripts back-to-back in the ``doc-hygiene`` job
(.github/workflows/roam-ci.yml). Either failing is a hard gate.

This script reads the single source of truth (``roam.surface_counts``,
which AST-parses ``src/roam/cli.py`` and ``src/roam/mcp_server.py``) and
substitutes the counts into the documentation files between explicit
markers.

Markdown marker pattern (one count site per pair)::

    <!-- BEGIN auto-count:NAME -->
    ...prose line containing the count...
    <!-- END auto-count:NAME -->

The script ONLY rewrites text between markers. Surrounding content is
preserved byte-for-byte. Re-running the script is idempotent.

JSON has no comments, so ``templates/.../mcp-server-card.json`` is
updated by JSON parse + key edit + write (only specific keys; everything
else is preserved).

Usage::

    python dev/build_readme_counts.py --check   # exit 1 if any drift
    python dev/build_readme_counts.py --apply   # rewrite files in place
    python dev/build_readme_counts.py           # same as --apply

Operates per-named-block, so adding a new site is a small additive edit.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from roam.surface_counts import cli_surface_counts, mcp_surface_counts  # noqa: E402


# ---------------------------------------------------------------------------
# Source of truth
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Counts:
    command_names: int       # ``roam --help-all`` row count (counts aliases)
    canonical_commands: int  # alias-collapsed command count
    alias_names: int         # number of alias names (command_names - canonical)
    category_count: int      # number of categories in _CATEGORIES
    mcp_core: int            # tools in the default ``core`` preset
    mcp_full: int            # tools in the full preset (every @_tool decorator)
    mcp_default_preset: int  # core + 1 meta-tool (``roam_expand_toolset``)


def collect_counts() -> Counts:
    cli = cli_surface_counts()
    mcp = mcp_surface_counts()
    # category_count: parse _CATEGORIES from cli.py via AST to avoid runtime import.
    cats = _category_count()
    return Counts(
        command_names=int(cli["command_names"]),
        canonical_commands=int(cli["canonical_commands"]),
        alias_names=int(cli["alias_names"]),
        category_count=cats,
        mcp_core=int(mcp["core_tools"]),
        mcp_full=int(mcp["registered_tools"]),
        mcp_default_preset=int(mcp["core_tools"]) + 1,
    )


def _category_count() -> int:
    """Count keys in ``_CATEGORIES`` in src/roam/cli.py via AST."""
    import ast

    cli_path = ROOT / "src" / "roam" / "cli.py"
    module = ast.parse(cli_path.read_text(encoding="utf-8"), filename=str(cli_path))
    for node in module.body:
        targets = []
        if isinstance(node, ast.Assign):
            targets = node.targets
        elif isinstance(node, ast.AnnAssign):
            targets = [node.target]
        for target in targets:
            if isinstance(target, ast.Name) and target.id == "_CATEGORIES":
                value = node.value if isinstance(node, ast.Assign) else node.value
                # _CATEGORIES is a dict literal; counting keys.
                if isinstance(value, ast.Dict):
                    return len(value.keys)
                # Fallback to literal_eval if it's an expression dict.
                try:
                    obj = ast.literal_eval(value)
                    if isinstance(obj, dict):
                        return len(obj)
                except Exception:
                    pass
    # If _CATEGORIES isn't a plain dict literal, return 0 so callers notice.
    return 0


# ---------------------------------------------------------------------------
# Markdown block rewriting
# ---------------------------------------------------------------------------


MARKER_BEGIN = "<!-- BEGIN auto-count:{name} -->"
MARKER_END = "<!-- END auto-count:{name} -->"


def _block_pattern(name: str) -> re.Pattern[str]:
    """Match the marker pair (inclusive) plus everything between them."""
    return re.compile(
        re.escape(MARKER_BEGIN.format(name=name))
        + r"(?P<body>.*?)"
        + re.escape(MARKER_END.format(name=name)),
        flags=re.DOTALL,
    )


def _replace_block(text: str, name: str, new_body: str) -> tuple[str, bool]:
    """Replace the body between markers ``name``. Returns (new_text, found)."""
    pattern = _block_pattern(name)
    found = pattern.search(text) is not None
    if not found:
        return text, False
    begin = MARKER_BEGIN.format(name=name)
    end = MARKER_END.format(name=name)
    replacement = f"{begin}\n{new_body}\n{end}"
    new_text = pattern.sub(lambda _m: replacement, text, count=1)
    return new_text, True


# ---------------------------------------------------------------------------
# Per-file plans
# ---------------------------------------------------------------------------


@dataclass
class MarkdownPlan:
    path: Path
    blocks: dict[str, Callable[[Counts], str]]


def _readme_blocks(c: Counts) -> dict[str, str]:
    return {
        # Line 43: "exposes the graph through N commands and M MCP tools across 28 languages."
        "readme-headline-prose": (
            f"Mechanically: Roam parses your repo once, stores structural facts "
            f"in a local SQLite graph (symbols, dependencies, call graphs, "
            f"architecture layers, git history, runtime traces), and exposes "
            f"the graph through {c.command_names} commands and {c.mcp_full} "
            f"MCP tools across 28 languages."
        ),
        # Line 325: "the canonical surface is N commands (X canonical + Y aliases) organised into Z categories"
        "readme-canonical-mention": (
            f"**Lead with the 5 verbs.** The [5 core commands](#core-commands) "
            f"cover ~80% of agent workflows: `understand`, `context`, "
            f"`retrieve`, `preflight`, `critique`. The remaining "
            f"~{c.command_names - 5} commands are detail surface for "
            f"specialised workflows (taint, fleet, cga, oracle, eval, …) — "
            f"they're called by agents on demand, not memorised. This is "
            f"intentional design; under the hood the canonical surface is "
            f"**{c.command_names} commands ({c.canonical_commands} canonical "
            f"+ {c.alias_names} aliases) organised into {c.category_count} "
            f"categories** (aliases for muscle memory: `algo` → `math`, "
            f"`weather` → `churn`, `digest` / `snapshot` / `trend` → "
            f"`trends`, `onboard` → `understand`, `refs` → `uses`), but you "
            f"don't need to know that to start."
        ),
        # Line 1023: "Default preset: core (N tools: M core + roam_expand_toolset meta-tool)"
        "readme-default-preset": (
            f"**Default preset:** `core` ({c.mcp_default_preset} tools: "
            f"{c.mcp_core} core + `roam_expand_toolset` meta-tool)."
        ),
    }


def _claude_blocks(c: Counts) -> dict[str, str]:
    return {
        # Line 7: "**N commands · M core MCP tools (K in full preset) · ..."
        "claude-headline": (
            f"roam-code is a CLI tool that gives AI coding agents instant "
            f"codebase comprehension.\n"
            f"It pre-indexes symbols, call graphs, dependencies, architecture, "
            f"and git history into\n"
            f"a local SQLite DB. **{c.command_names} commands · {c.mcp_core} "
            f"core MCP tools ({c.mcp_full} in `full` preset) · 28 languages "
            f"· 100% local · zero API keys.**"
        ),
        # Line 9: "Authoritative counts: roam surface returns command_count: N · ..."
        "claude-authoritative": (
            f"Authoritative counts: `roam surface` returns "
            f"`command_count: {c.command_names} · "
            f"canonical_count: {c.canonical_commands} · "
            f"category_count: {c.category_count} · "
            f"mcp_tool_count: {c.mcp_core}`."
        ),
    }


def _llms_install_blocks(c: Counts) -> dict[str, str]:
    return {
        # Line 4: "N commands, M MCP tools, 28 languages, 100% local, zero API keys."
        "llms-install-headline": (
            f"{c.command_names} commands, {c.mcp_full} MCP tools, 28 "
            f"languages, 100% local, zero API keys."
        ),
        # Line 81: "Run roam --help for all N commands (+ alias pairs)."
        "llms-install-footer": (
            f"Run `roam --help` for all {c.command_names} commands "
            f"(+ alias pairs)."
        ),
    }


MARKDOWN_TARGETS: tuple[tuple[Path, Callable[[Counts], dict[str, str]]], ...] = (
    (ROOT / "README.md", _readme_blocks),
    (ROOT / "CLAUDE.md", _claude_blocks),
    (ROOT / "llms-install.md", _llms_install_blocks),
)


# ---------------------------------------------------------------------------
# JSON target — mcp-server-card.json
# ---------------------------------------------------------------------------


MCP_CARD_PATH = (
    ROOT
    / "templates"
    / "distribution"
    / "landing-page"
    / ".well-known"
    / "mcp-server-card.json"
)
BUNDLED_MCP_CARD_PATH = ROOT / "src" / "roam" / "mcp-server-card.json"


def _update_mcp_card_text(text: str, c: Counts) -> str:
    """Update count-bearing keys in the card JSON text WITHOUT reformatting.

    Doing it textually (rather than parse+dump) preserves the file's
    existing whitespace style — inline arrays, blank lines, etc. The
    bundled and public cards are required to be byte-identical
    (tests/test_doc_consistency.py::test_bundled_card_matches_public_card),
    so any reformatting drift would cascade into a test failure.

    Validates that the result still parses as JSON before returning.
    """
    new_text = text
    # description: "... N MCP tools ..." (the only count in the description string).
    new_text = re.sub(
        r'("description"\s*:\s*"[^"]*?)\b\d+\s+MCP\s+tools\b',
        lambda m: f"{m.group(1)}{c.mcp_full} MCP tools",
        new_text,
        count=1,
    )
    # capabilities.tools.total — a single top-level "total": N in a "tools" block.
    new_text = re.sub(
        r'("total"\s*:\s*)\d+',
        lambda m: f"{m.group(1)}{c.mcp_full}",
        new_text,
        count=1,
    )
    # capabilities.tools.presets.core / .full — the two specific keys we own.
    new_text = re.sub(
        r'("core"\s*:\s*)\d+',
        lambda m: f"{m.group(1)}{c.mcp_core}",
        new_text,
        count=1,
    )
    new_text = re.sub(
        r'("full"\s*:\s*)\d+',
        lambda m: f"{m.group(1)}{c.mcp_full}",
        new_text,
        count=1,
    )
    # Validate the result still parses; refuse to write garbage.
    json.loads(new_text)
    return new_text


# ---------------------------------------------------------------------------
# Apply / check
# ---------------------------------------------------------------------------


@dataclass
class FileResult:
    path: Path
    changed: bool
    missing_blocks: list[str]


def _apply_markdown(path: Path, builder: Callable[[Counts], dict[str, str]], c: Counts,
                    write: bool) -> FileResult:
    text = path.read_text(encoding="utf-8")
    new_text = text
    blocks = builder(c)
    missing: list[str] = []
    for name, body in blocks.items():
        new_text, found = _replace_block(new_text, name, body)
        if not found:
            missing.append(name)
    changed = new_text != text
    if changed and write:
        path.write_text(new_text, encoding="utf-8")
    return FileResult(path=path, changed=changed, missing_blocks=missing)


def _apply_mcp_card(path: Path, c: Counts, write: bool) -> FileResult:
    if not path.exists():
        return FileResult(path=path, changed=False, missing_blocks=["<file-missing>"])
    raw = path.read_text(encoding="utf-8")
    new_raw = _update_mcp_card_text(raw, c)
    changed = new_raw != raw
    if changed and write:
        path.write_text(new_raw, encoding="utf-8")
    return FileResult(path=path, changed=changed, missing_blocks=[])


def run(write: bool, *, mode_label: str) -> int:
    c = collect_counts()
    results: list[FileResult] = []

    for path, builder in MARKDOWN_TARGETS:
        if not path.exists():
            results.append(FileResult(path=path, changed=False, missing_blocks=["<file-missing>"]))
            continue
        results.append(_apply_markdown(path, builder, c, write))

    # MCP card (public + bundled copy — they MUST stay byte-identical per
    # tests/test_doc_consistency.py::test_bundled_card_matches_public_card).
    results.append(_apply_mcp_card(MCP_CARD_PATH, c, write))
    if BUNDLED_MCP_CARD_PATH.exists():
        results.append(_apply_mcp_card(BUNDLED_MCP_CARD_PATH, c, write))

    # Report.
    any_change = any(r.changed for r in results)
    any_missing = any(r.missing_blocks for r in results)

    print(f"[build_readme_counts] mode={mode_label}")
    print(f"  truth: command_names={c.command_names} canonical={c.canonical_commands} "
          f"aliases={c.alias_names} categories={c.category_count} "
          f"mcp_core={c.mcp_core} mcp_full={c.mcp_full} "
          f"default_preset={c.mcp_default_preset}")
    for r in results:
        status = "changed" if r.changed else "ok"
        rel = r.path.relative_to(ROOT).as_posix() if r.path.is_absolute() else str(r.path)
        missing = f" missing_blocks={r.missing_blocks}" if r.missing_blocks else ""
        print(f"  {status:8s}  {rel}{missing}")

    if mode_label == "check":
        if any_change:
            print("DRIFT: docs disagree with truth; run "
                  "`python dev/build_readme_counts.py --apply` to fix.",
                  file=sys.stderr)
            return 1
        if any_missing:
            print("MISSING-MARKERS: some auto-count blocks are missing; "
                  "see report above.", file=sys.stderr)
            return 2
        return 0
    # apply mode
    if any_missing:
        print("WARN: some auto-count blocks were missing; those sites "
              "were skipped.", file=sys.stderr)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    grp = parser.add_mutually_exclusive_group()
    grp.add_argument("--check", action="store_true",
                     help="exit non-zero if any file would change")
    grp.add_argument("--apply", action="store_true",
                     help="rewrite files in place (default)")
    args = parser.parse_args()

    if args.check:
        return run(write=False, mode_label="check")
    return run(write=True, mode_label="apply")


if __name__ == "__main__":
    raise SystemExit(main())
