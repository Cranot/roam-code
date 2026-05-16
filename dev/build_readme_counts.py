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

**Scope: core roam commands only.** Plugin commands registered at runtime
via ``ctx.register_command()`` are NOT counted in the headline — the
documented count reflects what ships with ``pip install roam-code``, not
what a user's environment exposes after loading third-party plugins.
Plugin command counts are a separate concern surfaced via
``roam plugins list``.

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

from roam.surface_counts import (  # noqa: E402
    cli_surface_counts,
    mcp_surface_counts,
    mcp_tool_descriptions,
)


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
    pyproject_version: str   # ``version`` string from pyproject.toml (truth)


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
        pyproject_version=_pyproject_version(),
    )


def _pyproject_version() -> str:
    """Read ``version = "X"`` from pyproject.toml — the canonical version.

    Mirrors ``tests/test_doc_consistency.py::_truth_version`` so the
    regenerator and the test agree on the source of truth.
    """
    text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    m = re.search(r'^version\s*=\s*"([^"]+)"', text, re.MULTILINE)
    if not m:
        raise RuntimeError("pyproject.toml is missing a version field")
    return m.group(1)


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


def _mcp_tool_table() -> str:
    """Emit the README's collapsed-section MCP tool table from source.

    Generates a Markdown table with one row per ``@_tool``-decorated MCP
    wrapper, sorted alphabetically by tool name. Pulled by
    ``surface_counts.mcp_tool_descriptions()`` (description kwarg with
    docstring fallback). The table replaces the hand-maintained list
    that drifted across W299..W306 (W449).
    """
    entries = mcp_tool_descriptions()
    lines = [
        "| Tool | Description |",
        "|------|-------------|",
    ]
    for name, desc in entries:
        # Pipe characters inside descriptions would break the table; escape them.
        safe = (desc or "").replace("|", "\\|").strip()
        if not safe:
            safe = "(no description)"
        lines.append(f"| `{name}` | {safe} |")
    return "\n".join(lines)


def _mcp_core_preset_inline(c: Counts) -> str:
    """Emit the inline ``Core preset tools: \\`x\\`, \\`y\\`, ...`` line."""
    import ast

    mcp_path = ROOT / "src" / "roam" / "mcp_server.py"
    module = ast.parse(mcp_path.read_text(encoding="utf-8"), filename=str(mcp_path))
    core: set[str] | None = None
    for node in module.body:
        if isinstance(node, ast.Assign):
            for tgt in node.targets:
                if isinstance(tgt, ast.Name) and tgt.id == "_CORE_TOOLS":
                    val = ast.literal_eval(node.value)
                    if isinstance(val, set):
                        core = val
                    break
    if not core:
        return "Core preset tools: (none — _CORE_TOOLS not found)"
    names = sorted(core)
    quoted = ", ".join(f"`{n}`" for n in names)
    return f"Core preset tools: {quoted}."


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
        # Inline ``Core preset tools: `x`, `y`, ...`` enumeration. Source of
        # truth: ``_CORE_TOOLS`` literal in ``src/roam/mcp_server.py``. W449.
        "readme-mcp-core-preset-tools": _mcp_core_preset_inline(c),
        # Collapsed ``<summary>`` header for the MCP tool table. W449.
        "readme-mcp-tool-list-summary": (
            f"<summary><strong>MCP tool list (all {c.mcp_full})</strong></summary>"
        ),
        # Collapsed ``<summary>`` header for the CLI command tables. W685 —
        # symmetric counterpart to the MCP ``(all N)`` pin so a silently
        # deleted CLI row (where deletion + addition cancel out) fails the
        # count gate instead of the row-membership gate alone. Count is the
        # canonical command total (aliases collapsed) to match what
        # ``test_readme_covers_all_canonical_cli_commands`` enforces.
        "readme-cli-command-list-summary": (
            f"<summary><strong>Full command reference — canonical command "
            f"list (all {c.canonical_commands})</strong></summary>"
        ),
        # The Markdown tool table itself. One row per `@_tool` decoration.
        # Source of truth: ``surface_counts.mcp_tool_descriptions()``. W449.
        "readme-mcp-tool-list-table": _mcp_tool_table(),
    }


def _claude_blocks(c: Counts) -> dict[str, str]:
    return {
        # W138 reorder: lead with the env-independent total (mcp_full) and
        # name the core preset as a sub-claim. Headlines that lead with
        # mcp_core invited a downstream-test bug where ``roam surface``
        # reported 0 in fastmcp-less environments while docs claimed 57.
        "claude-headline": (
            f"roam-code is a local codebase intelligence CLI for developers "
            f"and AI coding agents.\n"
            f"It pre-indexes symbols, call graphs, dependencies, architecture, "
            f"and git history into\n"
            f"a local SQLite DB. **{c.command_names} commands · {c.mcp_full} "
            f"MCP tools ({c.mcp_core} in the default `core` preset) · 28 "
            f"languages · 100% local · zero API keys.**"
        ),
        # W138: the authoritative-counts line now names the AST-derived,
        # env-independent totals (``roam.surface_counts``) — distinct from
        # ``_REGISTERED_TOOLS`` (which only populates when fastmcp is
        # installed). The envelope from ``roam surface --json`` exposes
        # per-preset counts via ``mcp_tool_count_by_preset``.
        "claude-authoritative": (
            f"Authoritative counts (AST-derived, env-independent): "
            f"`command_count: {c.command_names} · "
            f"canonical_count: {c.canonical_commands} · "
            f"category_count: {c.category_count} · "
            f"mcp tools registered: {c.mcp_full} · "
            f"mcp tools in core preset: {c.mcp_core}`. "
            f"The `roam surface --json` envelope additionally exposes "
            f"`mcp_tool_count_by_preset` for per-preset counts."
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


def _agents_md_blocks(c: Counts) -> dict[str, str]:
    """AGENTS.md Codex-headline + Codex-authoritative blocks (W397).

    AGENTS.md ships a hand-curated prelude (the `roam agents-md` generator
    does not write it). The headline + authoritative-counts marker pair was
    added in W397's drive-by predecessors but never wired into the marker
    writer, so the numbers drifted (233/226/57/149 vs the live 238/231/224/57).
    These two blocks bring AGENTS.md onto the same auto-count contract as
    CLAUDE.md so the AI-coding-agent-facing headline stays in sync with
    `roam.surface_counts`.
    """
    return {
        # Headline mirrors CLAUDE.md's claude-headline contract: lead with
        # the env-independent total (mcp_full), name the core preset as a
        # sub-claim. Headlines that lead with mcp_core invited the W138
        # downstream-test bug where ``roam surface`` reported 0 in
        # fastmcp-less environments while docs claimed 57.
        "Codex-headline": (
            f"roam-code is a local codebase intelligence CLI for developers "
            f"and AI coding agents.\n"
            f"It pre-indexes symbols, call graphs, dependencies, architecture, "
            f"and git history into\n"
            f"a local SQLite DB. **{c.command_names} commands · {c.mcp_full} "
            f"MCP tools ({c.mcp_core} in the default `core` preset) · 28 "
            f"languages · 100% local · zero API keys.**"
        ),
        # Authoritative-counts line matches CLAUDE.md's claude-authoritative
        # contract: AST-derived, env-independent totals (`roam.surface_counts`)
        # — distinct from `_REGISTERED_TOOLS` (which only populates when
        # fastmcp is installed). The envelope from `roam surface --json`
        # exposes per-preset counts via `mcp_tool_count_by_preset`.
        "Codex-authoritative": (
            f"Authoritative counts (AST-derived, env-independent): "
            f"`command_count: {c.command_names} · "
            f"canonical_count: {c.canonical_commands} · "
            f"category_count: {c.category_count} · "
            f"mcp tools registered: {c.mcp_full} · "
            f"mcp tools in core preset: {c.mcp_core}`. "
            f"The `roam surface --json` envelope additionally exposes "
            f"`mcp_tool_count_by_preset` for per-preset counts."
        ),
    }


MARKDOWN_TARGETS: tuple[tuple[Path, Callable[[Counts], dict[str, str]]], ...] = (
    (ROOT / "README.md", _readme_blocks),
    (ROOT / "CLAUDE.md", _claude_blocks),
    (ROOT / "llms-install.md", _llms_install_blocks),
    (ROOT / "AGENTS.md", _agents_md_blocks),
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
    # Top-level "version" — must match pyproject.toml::version (the source
    # of truth that ``test_doc_consistency.py::test_mcp_card_matches_pyproject``
    # gates on). Only the FIRST "version" key is the card's own version;
    # other "version"-suffixed keys (e.g. ``aibom_extension_version``,
    # ``schema_version``) live deeper in the JSON and aren't touched by
    # the ``count=1`` constraint.
    new_text = re.sub(
        r'("version"\s*:\s*")[^"]+(")',
        lambda m: f'{m.group(1)}{c.pyproject_version}{m.group(2)}',
        new_text,
        count=1,
    )
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
            # CLAUDE.md is intentionally untracked from the public repo
            # (removed in commit 89a338d9 — it's the local-only project
            # intelligence file). Skip it silently when absent so CI doesn't
            # fail on a deliberate-absence rather than a real drift. Other
            # targets in MARKDOWN_TARGETS must always exist in CI; their
            # absence remains a check failure.
            if path.name == "CLAUDE.md":
                continue
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
