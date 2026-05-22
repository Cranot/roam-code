"""Auto-generate count numbers across README + CLAUDE + llms-install + mcp-server-card.

Eliminates the count-drift class that has churned every sprint that added a
command (213 to 217 to 223 to 226 to 229 to 232 to 233 ...). Each wave the
counts in README.md, CLAUDE.md, llms-install.md, and the public MCP server
card had to be hand-bumped, and the test only caught the discrepancy after
the fact.

W844 [landed 2026-05-17] — close the W563 auto-rotate gap. The card writer used to
update 2 of the 4 card paths (bundled + flat well-known .json) and leave the
SEP-1649 nested + SEP-2127 no-suffix mirrors stale, plus the SHA-256 pin in
``tests/test_mcp_server_card_hash.py`` had to be hand-bumped after every
``--apply``. Both gaps required manual W789/W794/W1307/W1308 fix-up commits.
``--apply`` now (1) syncs all 3 well-known mirrors to the canonical bytes
and (2) rewrites ``_EXPECTED_CARD_SHA256`` to the new digest. Opt out per
invocation via ``--no-rotate-card-hash`` (the substrate stays available for
debugging without auto-rotation).

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
import hashlib
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

# The default repo root is the script's own ancestor. ``--root`` (or the
# ``ROAM_REPO_ROOT`` env var) overrides this at runtime so tests can point
# the writer at a tmp_path copy of the count-bearing files, eliminating the
# parallel-race / cross-test-contamination class observed in v13.5 hardening
# (when ``test_readme_recipe_count_matches_registry`` failed because a
# parallel auto-count test had momentarily written BAD bytes that the
# recipe-count test read in its window).
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


def collect_counts(root: Path | None = None) -> Counts:
    """Collect counts. ``root`` defaults to module-global ROOT.

    The ``cli_surface_counts`` / ``mcp_surface_counts`` / ``mcp_tool_descriptions``
    helpers resolve their inputs via ``importlib.resources`` on the live
    ``roam`` package, so they are independent of ``root`` — only the
    file-system reads (pyproject.toml, cli.py for ``_CATEGORIES``) are
    routed through ``root``.
    """
    if root is None:
        root = ROOT
    cli = cli_surface_counts()
    mcp = mcp_surface_counts()
    # category_count: parse _CATEGORIES from cli.py via AST to avoid runtime import.
    cats = _category_count(root)
    return Counts(
        command_names=int(cli["command_names"]),
        canonical_commands=int(cli["canonical_commands"]),
        alias_names=int(cli["alias_names"]),
        category_count=cats,
        mcp_core=int(mcp["core_tools"]),
        mcp_full=int(mcp["registered_tools"]),
        mcp_default_preset=int(mcp["core_tools"]) + 1,
        pyproject_version=_pyproject_version(root),
    )


def _pyproject_version(root: Path) -> str:
    """Read ``version = "X"`` from pyproject.toml — the canonical version.

    Mirrors ``tests/test_doc_consistency.py::_truth_version`` so the
    regenerator and the test agree on the source of truth.
    """
    text = (root / "pyproject.toml").read_text(encoding="utf-8")
    m = re.search(r'^version\s*=\s*"([^"]+)"', text, re.MULTILINE)
    if not m:
        raise RuntimeError("pyproject.toml is missing a version field")
    return m.group(1)


def _category_count(root: Path) -> int:
    """Count keys in ``_CATEGORIES`` in src/roam/cli.py via AST."""
    import ast

    cli_path = root / "src" / "roam" / "cli.py"
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


def _mcp_core_preset_inline(c: Counts, root: Path) -> str:
    """Emit the inline ``Core preset tools: \\`x\\`, \\`y\\`, ...`` line."""
    import ast

    mcp_path = root / "src" / "roam" / "mcp_server.py"
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


def _readme_blocks(c: Counts, root: Path) -> dict[str, str]:
    return {
        # ``readme-headline-prose`` was dropped in v13.2 per the
        # 2026-05-16 ecosystem refresh — README hero now leads with the
        # positioning core (credential-free + zero-egress + tamper-evident
        # evidence) rather than the count headline. Authoritative counts
        # still flow through ``readme-canonical-mention`` and the table /
        # preset blocks below.
        # README:15 ``<sub>N commands · M MCP tools (C in the default
        # `core` preset) · 28 languages</sub>`` headline. Re-wrapped in
        # auto-count markers after the integration smoke caught a +3 MCP
        # drift (224 → 227) that the hand-maintained W419-era headline
        # missed. Closes the headline-counts drift class.
        "readme-headline-counts": (
            f"<sub>{c.command_names} commands · {c.mcp_full} MCP tools "
            f"({c.mcp_core} in the default `core` preset) · 28 languages</sub>"
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
            f"categories** (aliases for muscle memory: `math` → `algo`, "
            f"`churn` → `weather`, `digest` / `snapshot` / `trend` → "
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
        "readme-mcp-core-preset-tools": _mcp_core_preset_inline(c, root),
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


def _claude_blocks(c: Counts, root: Path) -> dict[str, str]:  # noqa: ARG001 — root accepted for signature symmetry with _readme_blocks
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


def _llms_install_blocks(c: Counts, root: Path) -> dict[str, str]:  # noqa: ARG001 — root accepted for signature symmetry with _readme_blocks
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


def _agents_md_blocks(c: Counts, root: Path) -> dict[str, str]:  # noqa: ARG001 — root accepted for signature symmetry with _readme_blocks
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


# Markdown targets — built lazily inside ``run(root, ...)`` so each
# invocation can be retargeted at a ``--root`` override (tmp_path copies
# for parallel-safe testing). The module-level constants below are kept
# for back-compat with any importer that referenced them directly; their
# values reflect the default ROOT and are NOT updated by ``--root``.
MARKDOWN_TARGETS: tuple[tuple[Path, Callable[[Counts, Path], dict[str, str]]], ...] = (
    (ROOT / "README.md", _readme_blocks),
    (ROOT / "CLAUDE.md", _claude_blocks),
    (ROOT / "llms-install.md", _llms_install_blocks),
    (ROOT / "AGENTS.md", _agents_md_blocks),
)


def _markdown_targets(root: Path) -> tuple[tuple[Path, Callable[[Counts, Path], dict[str, str]]], ...]:
    """Return the per-root MARKDOWN_TARGETS tuple."""
    return (
        (root / "README.md", _readme_blocks),
        (root / "CLAUDE.md", _claude_blocks),
        (root / "llms-install.md", _llms_install_blocks),
        (root / "AGENTS.md", _agents_md_blocks),
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

# W844 — the two extra .well-known mirrors that ``--apply`` used to leave
# stale. ``test_w792_well_known_card_mirrors`` requires all 3 mirror paths
# to hash identically; treating them as derived copies of MCP_CARD_PATH
# closes the W1308 manual-sync gap.
_WELL_KNOWN_DIR = ROOT / "templates" / "distribution" / "landing-page" / ".well-known"
WELL_KNOWN_MIRROR_PATHS: tuple[Path, ...] = (
    _WELL_KNOWN_DIR / "mcp" / "server-card.json",   # SEP-1649 nested
    _WELL_KNOWN_DIR / "mcp-server-card",            # SEP-2127 no-suffix
)


def _mcp_card_path(root: Path) -> Path:
    """Public ``.well-known/mcp-server-card.json`` for the given root."""
    return root / "templates" / "distribution" / "landing-page" / ".well-known" / "mcp-server-card.json"


def _bundled_mcp_card_path(root: Path) -> Path:
    """Bundled ``src/roam/mcp-server-card.json`` for the given root."""
    return root / "src" / "roam" / "mcp-server-card.json"


def _well_known_mirror_paths(root: Path) -> tuple[Path, ...]:
    """The two extra ``.well-known`` mirrors (SEP-1649 nested + SEP-2127 no-suffix)."""
    well_known = root / "templates" / "distribution" / "landing-page" / ".well-known"
    return (
        well_known / "mcp" / "server-card.json",
        well_known / "mcp-server-card",
    )


# W844 — the SHA-256 pin in tests/test_mcp_server_card_hash.py. The hash
# is computed on the LF-line-ending bytes (canonical git storage) per
# W1308; ``_canonical_card_bytes`` enforces that on Windows checkouts.
CARD_HASH_TEST_PATH = ROOT / "tests" / "test_mcp_server_card_hash.py"


def _card_hash_test_path(root: Path) -> Path:
    """Per-root ``tests/test_mcp_server_card_hash.py`` for SHA-256 pin rotation."""
    return root / "tests" / "test_mcp_server_card_hash.py"


CARD_HASH_PIN_PATTERN = re.compile(
    r'^(_EXPECTED_CARD_SHA256\s*=\s*")([0-9a-f]{64})(")',
    flags=re.MULTILINE,
)


def _canonical_card_bytes(path: Path) -> bytes:
    """Return LF-normalized card bytes for hashing.

    The pin in ``tests/test_mcp_server_card_hash.py`` is computed on LF
    bytes (canonical git storage) so CI Linux and local Windows agree.
    W1308 normalized the working tree to LF; this helper is a belt-and-
    suspenders guard against a future CRLF-introducing edit reaching the
    auto-rotate path.
    """
    return path.read_bytes().replace(b"\r\n", b"\n")


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


def _apply_markdown(path: Path, builder: Callable[[Counts, Path], dict[str, str]], c: Counts,
                    write: bool, root: Path) -> FileResult:
    text = path.read_text(encoding="utf-8")
    new_text = text
    blocks = builder(c, root)
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


def _sync_well_known_mirror(canonical: Path, mirror: Path, write: bool) -> FileResult:
    """Copy ``canonical`` to ``mirror`` if bytes differ (W844 — close W1308 gap).

    The W792 byte-identity guard requires all 3 ``.well-known`` card paths
    to hash identically. ``--apply`` used to update only MCP_CARD_PATH and
    leave the SEP-1649 / SEP-2127 mirrors stale, forcing a manual sync. We
    now treat the mirrors as derived copies of MCP_CARD_PATH so a single
    ``--apply`` keeps the W792 invariant green.
    """
    if not canonical.exists() or not mirror.exists():
        return FileResult(path=mirror, changed=False, missing_blocks=["<file-missing>"])
    canonical_bytes = canonical.read_bytes()
    mirror_bytes = mirror.read_bytes()
    changed = canonical_bytes != mirror_bytes
    if changed and write:
        mirror.write_bytes(canonical_bytes)
    return FileResult(path=mirror, changed=changed, missing_blocks=[])


def _rotate_card_hash_pin(canonical: Path, write: bool, card_hash_test_path: Path) -> FileResult:
    """Recompute SHA-256 of the canonical card + rewrite the test-file pin (W844).

    Closes the W563 auto-rotate gap. Every prior card edit (W789, W794,
    W1307, W1308) needed a manual hand-bump of ``_EXPECTED_CARD_SHA256``
    in ``tests/test_mcp_server_card_hash.py`` after running ``--apply``.
    This helper computes the LF-bytes digest of the canonical card and
    rewrites the pin in place. The original pin line shape is preserved
    via a tight regex (anchored on the assignment, not the digest); only
    the 64-hex digit run between the quotes is touched.
    """
    if not canonical.exists() or not card_hash_test_path.exists():
        return FileResult(path=card_hash_test_path, changed=False,
                          missing_blocks=["<file-missing>"])
    digest = hashlib.sha256(_canonical_card_bytes(canonical)).hexdigest()
    text = card_hash_test_path.read_text(encoding="utf-8")
    new_text, count = CARD_HASH_PIN_PATTERN.subn(
        lambda m: f"{m.group(1)}{digest}{m.group(3)}",
        text,
        count=1,
    )
    if count == 0:
        # Pin moved or was renamed — surface as a missing block so callers
        # notice rather than silently failing to update.
        return FileResult(path=card_hash_test_path, changed=False,
                          missing_blocks=["_EXPECTED_CARD_SHA256"])
    changed = new_text != text
    if changed and write:
        card_hash_test_path.write_text(new_text, encoding="utf-8")
    return FileResult(path=card_hash_test_path, changed=changed, missing_blocks=[])


def run(write: bool, *, mode_label: str, rotate_card_hash: bool = True,
        root: Path | None = None) -> int:
    if root is None:
        root = ROOT
    c = collect_counts(root)
    results: list[FileResult] = []

    mcp_card_path = _mcp_card_path(root)
    bundled_mcp_card_path = _bundled_mcp_card_path(root)
    well_known_mirrors = _well_known_mirror_paths(root)
    card_hash_test_path = _card_hash_test_path(root)

    for path, builder in _markdown_targets(root):
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
        results.append(_apply_markdown(path, builder, c, write, root))

    # MCP card (public + bundled copy — they MUST stay byte-identical per
    # tests/test_doc_consistency.py::test_bundled_card_matches_public_card).
    results.append(_apply_mcp_card(mcp_card_path, c, write))
    if bundled_mcp_card_path.exists():
        results.append(_apply_mcp_card(bundled_mcp_card_path, c, write))

    # W844 — sync the two extra .well-known mirrors to the canonical card
    # bytes (closes the W1308 manual-sync gap) and rotate the SHA-256 pin
    # in tests/test_mcp_server_card_hash.py (closes the W563 auto-rotate
    # gap). Both run on the post-_apply_mcp_card bytes so the digest
    # reflects the just-written counts/version. Skipping rotation via
    # ``--no-rotate-card-hash`` is intentional: useful when debugging a
    # card edit you do NOT want the pin to chase.
    for mirror in well_known_mirrors:
        results.append(_sync_well_known_mirror(mcp_card_path, mirror, write))
    if rotate_card_hash:
        results.append(_rotate_card_hash_pin(bundled_mcp_card_path, write, card_hash_test_path))

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
        # Use the configured root for the relative display path; fall back to
        # ROOT for paths that happen to live outside the root (none today, but
        # the older module-level constants made this assumption).
        try:
            rel = r.path.relative_to(root).as_posix() if r.path.is_absolute() else str(r.path)
        except ValueError:
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
    import os

    parser = argparse.ArgumentParser(description=__doc__)
    grp = parser.add_mutually_exclusive_group()
    grp.add_argument("--check", action="store_true",
                     help="exit non-zero if any file would change")
    grp.add_argument("--apply", action="store_true",
                     help="rewrite files in place (default)")
    parser.add_argument(
        "--no-rotate-card-hash",
        action="store_true",
        help="W844: skip auto-rotation of _EXPECTED_CARD_SHA256 in "
             "tests/test_mcp_server_card_hash.py (default: rotate)",
    )
    parser.add_argument(
        "--root",
        type=Path,
        default=None,
        help="Override the repo root that the script reads/writes "
             "(default: the script's own ancestor). The ROAM_REPO_ROOT "
             "env var sets the same value. Used by parallel-safe tests "
             "that operate on a tmp_path copy of the count-bearing files.",
    )
    args = parser.parse_args()
    rotate = not args.no_rotate_card_hash
    # CLI flag wins; env var is the secondary override; default is module ROOT.
    root: Path | None = args.root
    if root is None:
        env_root = os.environ.get("ROAM_REPO_ROOT")
        if env_root:
            root = Path(env_root)
    if root is not None:
        root = root.resolve()

    if args.check:
        return run(write=False, mode_label="check", rotate_card_hash=rotate, root=root)
    return run(write=True, mode_label="apply", rotate_card_hash=rotate, root=root)


if __name__ == "__main__":
    raise SystemExit(main())
