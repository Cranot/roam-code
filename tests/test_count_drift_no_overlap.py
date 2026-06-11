"""W23.1 — assert the two count-drift scripts handle disjoint *writeable* file sets.

``scripts/sync_surface_counts.py`` and ``dev/build_readme_counts.py`` are
intentional cousins (see each script's docstring + the BACKLOG note).
This test pins the boundary so a future refactor can't accidentally
make both scripts write to the same file (which would race them and
make the SHA of e.g. ``mcp-server-card.json`` depend on CI-step order).

It does NOT forbid the two scripts from naming the same file, because
``sync_surface_counts.py`` deliberately retains no-op entries
(``repl=None``) for the two ``mcp-server-card.json`` paths to document
that those files ARE covered (just not by it). The invariant is:
**at most one script may actually write to any given file.**
"""

from __future__ import annotations

import ast
import importlib.util
import sys
from pathlib import Path

import pytest

# xdist: these tests read or mutate the REAL repo card JSONs + the
# _EXPECTED_CARD_SHA256 pin (no --target override exists), so they must
# serialize on one worker. Surfaced on the first parallel CI run
# (2026-06-11): two w844 tests raced across workers and flagged a real
# --apply as non-idempotent.
pytestmark = pytest.mark.xdist_group("card_pin_mutation")

from tests._helpers.repo_root import repo_root

ROOT = repo_root()
SRC = ROOT / "src"
SYNC_SCRIPT = ROOT / "scripts" / "sync_surface_counts.py"
BUILD_SCRIPT = ROOT / "dev" / "build_readme_counts.py"


if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    # Register before exec — @dataclass needs sys.modules[__module__]
    # populated to resolve types via _is_type during class processing.
    sys.modules[name] = module
    try:
        spec.loader.exec_module(module)
    except Exception:
        sys.modules.pop(name, None)
        raise
    return module


def _sync_writeable_files(*, marker_aware: bool | None = None) -> set[Path]:
    """Files that ``sync_surface_counts.py`` would actually rewrite.

    Excludes entries whose replacement is ``None`` (those are
    deliberately inert — see the script docstring).

    ``marker_aware`` filters the result:
      - ``None``  -> every actively-written file (default)
      - ``False`` -> only legacy whole-file entries
      - ``True``  -> only marker-aware (marker-masked) entries
    """
    mod = _load("sync_surface_counts", SYNC_SCRIPT)
    counts = mod._live_counts()
    langs = mod._live_languages()
    mod.build_replacements(counts, langs)
    out: set[Path] = set()
    # ``iter_replacements()`` normalises 2-tuple (legacy) and 3-tuple
    # (marker-aware) REPLACEMENTS entries to one shape — see W23.1 cousin
    # docstring. Iterating it instead of REPLACEMENTS directly keeps this
    # test stable as new marker-aware surfaces land.
    for path, patterns, file_marker_aware in mod.iter_replacements():
        if marker_aware is not None and file_marker_aware != marker_aware:
            continue
        if any(repl is not None for _pat, repl in patterns):
            out.add(path.resolve())
    return out


def _build_writeable_files() -> set[Path]:
    """Files that ``build_readme_counts.py`` would rewrite."""
    mod = _load("build_readme_counts", BUILD_SCRIPT)
    out = {path.resolve() for path, _builder in mod.MARKDOWN_TARGETS}
    out.add(mod.MCP_CARD_PATH.resolve())
    out.add(mod.BUNDLED_MCP_CARD_PATH.resolve())
    return out


def test_writeable_file_sets_are_disjoint() -> None:
    """The two count-drift scripts must not both write to the same file
    in WHOLE-FILE mode.

    If this fails, either (a) drop the duplicate from one script, or
    (b) make one side a no-op (``repl=None`` in
    ``sync_surface_counts.py``'s REPLACEMENTS) so reviewers can still
    see the file is explicitly handled but only one script touches it.

    Files flagged ``marker_aware=True`` (README/CLAUDE/AGENTS) are
    EXCLUDED here: those are deliberately co-owned. The two scripts
    write strictly disjoint byte regions of the same file (cousin
    inside marker blocks, this script outside them) — see the
    byte-region test below for the stronger invariant.
    """
    sync_files = _sync_writeable_files(marker_aware=False)
    build_files = _build_writeable_files()
    overlap = sync_files & build_files
    assert not overlap, (
        "Both count-drift scripts claim to whole-file-write: "
        f"{sorted(p.relative_to(ROOT).as_posix() for p in overlap)}. "
        "Pick one writer per file (see docstrings + W23.1 note)."
    )


def test_marker_aware_co_ownership_writes_disjoint_byte_regions() -> None:
    """README/CLAUDE/AGENTS are co-owned but the two scripts touch
    disjoint byte regions — the cousin writes ONLY inside auto-count
    marker blocks, this script writes ONLY outside them.

    This is the stronger replacement for the whole-file disjoint check
    on marker-aware files: co-ownership is safe precisely because the
    byte regions never overlap, so CI-step ordering cannot race them.
    """
    co_owned = _sync_writeable_files(marker_aware=True) & _build_writeable_files()
    assert co_owned, (
        "Expected README/CLAUDE/AGENTS to be marker-aware co-owned; none found. "
        "The marker-aware extension may have regressed."
    )
    mod = _load("sync_surface_counts_byteregion", SYNC_SCRIPT)
    mod.build_replacements(mod._live_counts(), mod._live_languages())
    for path in sorted(co_owned):
        # Skip absent files (e.g. dev-local CLAUDE.md on a CI checkout)
        # AND pointer-only CLAUDE.md (collapsed to a 1-line ``@AGENTS.md``
        # import on 2026-02-27, commit e5993a6). Both shapes carry no
        # marker blocks; the invariant still holds for the tracked
        # marker-bearing files (README / AGENTS).
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8")
        if path.name == "CLAUDE.md" and text.lstrip().startswith("@"):
            continue
        spans = mod._marker_spans(text)
        assert spans, (
            f"{path.name} is marker-aware co-owned but has no auto-count "
            "marker blocks — the cousin script would have nothing to write."
        )
        # Every active substitution this script makes must land OUTSIDE
        # every marker span.
        patterns = None
        for p, pats, marker_aware in mod.iter_replacements():
            if p.resolve() == path.resolve():
                assert marker_aware is True
                patterns = pats
        assert patterns is not None
        for pat, repl in patterns:
            if repl is None:
                continue
            for m in pat.finditer(text):
                in_block = any(start <= m.start() < end for start, end in spans)
                assert not in_block, (
                    f"{path.name}: pattern {pat.pattern!r} matches INSIDE an "
                    "auto-count marker block — that region is owned by "
                    "build_readme_counts.py and this script must not touch it."
                )


def test_mcp_cards_are_owned_by_build_readme_counts_only() -> None:
    """The bundled + public mcp-server-card.json files must be written
    exclusively by ``dev/build_readme_counts.py``.

    ``tests/test_doc_consistency.py::test_bundled_card_matches_public_card``
    requires the two cards to stay byte-identical; only the JSON-aware
    writer in ``build_readme_counts.py`` preserves whitespace style
    correctly. ``sync_surface_counts.py`` keeps inert (``repl=None``)
    entries so reviewers can see the cards ARE covered elsewhere.
    """
    sync_mod = _load("sync_surface_counts_check_inert", SYNC_SCRIPT)
    counts = sync_mod._live_counts()
    langs = sync_mod._live_languages()
    sync_mod.build_replacements(counts, langs)
    build_mod = _load("build_readme_counts_owners", BUILD_SCRIPT)
    card_paths = {
        build_mod.MCP_CARD_PATH.resolve(),
        build_mod.BUNDLED_MCP_CARD_PATH.resolve(),
    }
    for path, patterns, _marker_aware in sync_mod.iter_replacements():
        if path.resolve() not in card_paths:
            continue
        for _pat, repl in patterns:
            assert repl is None, (
                f"sync_surface_counts.py has an active replacement for "
                f"{path.relative_to(ROOT).as_posix()}; mcp-server-card.json "
                "must be written only by dev/build_readme_counts.py."
            )


def test_both_scripts_use_same_truth_source() -> None:
    """Both scripts must read counts from ``roam.surface_counts``.

    If a third copy of the count logic sneaks in, this test points the
    next reviewer at the right file to merge it into.
    """
    sync_src = SYNC_SCRIPT.read_text(encoding="utf-8")
    build_src = BUILD_SCRIPT.read_text(encoding="utf-8")
    assert "roam.surface_counts" in sync_src, (
        "sync_surface_counts.py no longer imports roam.surface_counts; "
        "it may have grown its own count logic. Consolidate."
    )
    assert "roam.surface_counts" in build_src, (
        "build_readme_counts.py no longer imports roam.surface_counts; "
        "it may have grown its own count logic. Consolidate."
    )


def test_ast_loadable_no_syntax_errors() -> None:
    """Belt-and-braces: both scripts parse cleanly. Catches the failure
    mode where a hasty edit to one docstring leaves a broken module
    that the CI script invocation would only catch on the next push."""
    for path in (SYNC_SCRIPT, BUILD_SCRIPT):
        ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


# ---------------------------------------------------------------------------
# 2026-05-21 — coverage for the newly-walked count-bearing surfaces.
# The 224-vs-227 drift cascade exposed that README/CLAUDE/AGENTS/CONTRIBUTING
# free-form prose + several landing-page docs pages had NO count guard. These
# tests pin that the extended sync_surface_counts.py now walks them.
# ---------------------------------------------------------------------------


def _sync_module():
    """Load sync_surface_counts.py with REPLACEMENTS already built."""
    mod = _load("sync_surface_counts_newcov", SYNC_SCRIPT)
    mod.build_replacements(mod._live_counts(), mod._live_languages())
    return mod


def test_extended_surfaces_are_walked() -> None:
    """Every surface the 224-vs-227 cascade exposed must now be in REPLACEMENTS.

    A surface counts as "walked" only if it carries at least one ACTIVE
    (non-``None``) replacement — an inert ``repl=None`` entry documents
    coverage-elsewhere, it does not guard the file.
    """
    mod = _sync_module()
    active: set[str] = set()
    for path, patterns, _marker_aware in mod.iter_replacements():
        if any(repl is not None for _pat, repl in patterns):
            active.add(path.relative_to(ROOT).as_posix())

    must_be_walked = {
        # Free-form (non-marker) prose count phrases.
        "README.md",
        "CLAUDE.md",
        "AGENTS.md",
        "CONTRIBUTING.md",
        # Landing-page docs pages that quote the hard counts.
        "templates/distribution/landing-page/docs/agent-contract.html",
        "templates/distribution/landing-page/docs/integration-tutorials.html",
        "templates/distribution/landing-page/docs/canonical-demo.html",
        "templates/distribution/landing-page/docs/mcp-usage.html",
    }
    missing = must_be_walked - active
    assert not missing, (
        f"sync_surface_counts.py does not actively walk: {sorted(missing)}. "
        "These count-bearing surfaces drift silently without a guard."
    )


def test_readme_claude_agents_are_marker_aware() -> None:
    """README/CLAUDE/AGENTS entries must run on a marker-MASKED copy.

    The auto-count marker blocks in those files are owned by
    ``dev/build_readme_counts.py``. If sync_surface_counts.py walked them
    whole-file, the two scripts could fight over the same bytes.
    """
    mod = _sync_module()
    flags: dict[str, bool] = {}
    for path, _patterns, marker_aware in mod.iter_replacements():
        flags[path.name] = flags.get(path.name, False) or marker_aware
    for name in ("README.md", "CLAUDE.md", "AGENTS.md"):
        assert flags.get(name) is True, (
            f"{name} must be flagged marker_aware=True so substitution "
            "skips the auto-count marker blocks owned by build_readme_counts.py."
        )


def test_marker_aware_substitution_never_touches_marker_blocks() -> None:
    """``_apply_marker_aware`` must leave bytes inside marker blocks alone.

    Injects a bogus count INSIDE an AGENTS.md auto-count marker block
    and confirms the marker-aware path does not "fix" it — that site
    belongs to the cousin script. AGENTS.md is the marker-bearing
    source of truth since CLAUDE.md collapsed to a 1-line
    ``@AGENTS.md`` pointer (commit e5993a6, 2026-02-27).
    """
    import re as _re

    agents = ROOT / "AGENTS.md"
    if not agents.exists():
        pytest.skip("AGENTS.md not found; marker-block test runs only where it is present")

    mod = _sync_module()
    text = agents.read_text(encoding="utf-8")
    block_match = _re.search(
        r"<!--\s*BEGIN auto-count:Codex-headline.*?<!--\s*END auto-count:Codex-headline.*?-->",
        text,
        _re.DOTALL,
    )
    assert block_match, "AGENTS.md is missing the Codex-headline marker block"
    block = block_match.group(0)
    assert "commands" in block, "Codex-headline block unexpectedly has no count"
    corrupt_block = _re.sub(r"\b\d+ commands\b", "99999 commands", block, count=1)
    corrupt_text = text.replace(block, corrupt_block)

    agents_patterns = None
    for path, patterns, marker_aware in mod.iter_replacements():
        if path.name == "AGENTS.md":
            assert marker_aware is True
            agents_patterns = patterns
    assert agents_patterns is not None

    out, hits = mod._apply_marker_aware(corrupt_text, agents_patterns)
    assert "99999 commands" in out, (
        "marker-aware substitution rewrote a count INSIDE an auto-count "
        "marker block — that block is owned by build_readme_counts.py."
    )
    assert not any("99999" in before for before, _after in hits)


def test_new_patterns_fix_simulated_free_form_drift() -> None:
    """The new free-form patterns must actually correct drifted prose.

    Shape-valid regexes are not enough — each must resolve a real drifted
    phrase to the live count (CLAUDE.md "validate shape AND executability").
    """
    mod = _sync_module()
    counts = mod._live_counts()
    cmds, canon, mcp, core = (
        counts["commands"],
        counts["canonical"],
        counts["mcp_tools"],
        counts["mcp_core_tools"],
    )
    aliases = counts["alias_names"]

    # (drifted phrase, expected corrected phrase)
    cases = [
        (
            "227 tools, 10 resources, and 5 prompts are available".replace("227", "999"),
            f"{mcp} tools, 10 resources, and 5 prompts are available",
        ),
        (
            "MCP server (999 tools, 10 resources, 6 prompts)",
            f"MCP server ({mcp} tools, 10 resources, 6 prompts)",
        ),
        (
            "Click CLI (1 canonical + 2 aliases)",
            f"Click CLI ({canon} canonical + {aliases} aliases)",
        ),
        (
            "999 command names (1 canonical + 2 aliases)",
            f"{cmds} command names ({canon} canonical + {aliases} aliases)",
        ),
        (
            "FastMCP server (1 tools in core preset; up to 2 in `full`)",
            f"FastMCP server ({core} tools in core preset; up to {mcp} in `full`)",
        ),
        (
            "MCP server with 9 tools (9 in the default `core` preset)",
            f"MCP server with {mcp} tools ({core} in the default `core` preset)",
        ),
    ]
    all_pats: list[tuple] = []
    for _path, patterns, _ma in mod.iter_replacements():
        for pat, repl in patterns:
            if repl is not None:
                all_pats.append((pat, repl))

    for drifted, expected in cases:
        fixed = False
        for pat, repl in all_pats:
            if pat.sub(repl, drifted) == expected:
                fixed = True
                break
        assert fixed, f"No pattern corrects drifted phrase: {drifted!r}"
