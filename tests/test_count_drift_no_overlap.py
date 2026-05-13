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

ROOT = Path(__file__).resolve().parents[1]
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


def _sync_writeable_files() -> set[Path]:
    """Files that ``sync_surface_counts.py`` would actually rewrite.

    Excludes entries whose replacement is ``None`` (those are
    deliberately inert — see the script docstring).
    """
    mod = _load("sync_surface_counts", SYNC_SCRIPT)
    counts = mod._live_counts()
    langs = mod._live_languages()
    mod.build_replacements(counts, langs)
    out: set[Path] = set()
    for path, patterns in mod.REPLACEMENTS:
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
    """The two count-drift scripts must not both write to the same file.

    If this fails, either (a) drop the duplicate from one script, or
    (b) make one side a no-op (``repl=None`` in
    ``sync_surface_counts.py``'s REPLACEMENTS) so reviewers can still
    see the file is explicitly handled but only one script touches it.
    """
    sync_files = _sync_writeable_files()
    build_files = _build_writeable_files()
    overlap = sync_files & build_files
    assert not overlap, (
        "Both count-drift scripts claim to write to: "
        f"{sorted(p.relative_to(ROOT).as_posix() for p in overlap)}. "
        "Pick one writer per file (see docstrings + dev/BACKLOG.md W23.1 note)."
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
    for path, patterns in sync_mod.REPLACEMENTS:
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
