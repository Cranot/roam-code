"""Detect potential breaking changes between git refs.

Output formats: text (default), ``--json``. SARIF is deliberately NOT
emitted because breaking outputs are invocation-scoped breaking-change
verdicts — not per-location violations. See action.yml
_SUPPORTED_SARIF allowlist + W1175-RESEARCH Bucket B propagation plan
+ W1148 audit memo.
"""

from __future__ import annotations

from difflib import SequenceMatcher

import click

from roam.capability import roam_capability
from roam.commands.changed_files import (
    git_changed_files_against_ref as _git_changed_files,
)
from roam.commands.changed_files import (
    git_show_at_ref as _git_show,
)
from roam.commands.changed_files import (
    parse_source_with_grammar as _parse_source_bytes,
)
from roam.commands.resolve import ensure_index
from roam.db.connection import find_project_root, open_db
from roam.output.formatter import (
    abbrev_kind,
    format_signature,
    json_envelope,
    to_json,
)

# ---------------------------------------------------------------------------
# Git + parsing helpers
#
# ``_git_changed_files`` / ``_git_show`` / ``_parse_source_bytes`` are hoisted
# to ``roam.commands.changed_files`` so api-changes, breaking, and
# semantic-diff share one implementation (W-vibe-check DRY). Imported above;
# aliased to keep the original private names for back-compat with downstream
# importers (notably ``cmd_attest`` which imports ``_git_*`` from this module).
# ---------------------------------------------------------------------------


def _extract_old_symbols(source: bytes, file_path: str) -> list[dict]:
    """Parse *source* bytes and extract symbols for *file_path*.

    Returns a list of normalised symbol dicts (same shape as
    ``roam.index.symbols.extract_symbols``).
    """
    from roam.index.symbols import extract_symbols
    from roam.languages.registry import get_extractor_for_file, get_language_for_file

    language = get_language_for_file(file_path)
    if language is None:
        return []

    extractor = get_extractor_for_file(file_path)
    if extractor is None:
        return []

    tree, src, lang = _parse_source_bytes(source, language)
    if tree is None:
        return []

    return extract_symbols(tree, src, file_path, extractor)


# ---------------------------------------------------------------------------
# Comparison logic
# ---------------------------------------------------------------------------


def _exported_only(symbols: list[dict]) -> list[dict]:
    """Keep only exported symbols."""
    return [s for s in symbols if s.get("is_exported")]


def _key(sym: dict) -> str:
    """Unique key for matching: qualified_name or name."""
    return sym.get("qualified_name") or sym.get("name", "")


def _sig_normalise(sig: str | None, *, max_len: int = 80) -> str:
    """Normalise a signature for comparison.

    Collapses all whitespace (including ``\\r\\n`` vs ``\\n`` differences)
    and truncates to *max_len* so that storage-truncation artefacts in
    the DB do not cause false positives.
    """
    if not sig:
        return ""
    normed = " ".join(sig.replace("\r", "").split())
    return normed[:max_len]


def _display_sig(sig: str | None, max_len: int = 50) -> str:
    """Extract a readable one-line display signature.

    For Python functions whose stored signature starts with decorators,
    extract the ``def ...`` line.  Otherwise fall back to
    ``format_signature``.
    """
    if not sig:
        return ""
    # If the signature contains a 'def ' line, use that
    for line in sig.replace("\r", "").split("\n"):
        stripped = line.strip()
        if stripped.startswith("def "):
            return format_signature(stripped, max_len)
    # Fallback: first line, truncated
    return format_signature(sig.split("\n")[0].strip(), max_len)


def _similarity(a: str, b: str) -> float:
    """Return 0..1 similarity ratio between two strings."""
    return SequenceMatcher(None, a, b).ratio()


def _compare_file(
    file_path: str,
    old_symbols: list[dict],
    new_symbols: list[dict],
) -> tuple[list[dict], list[dict], list[dict]]:
    """Compare old vs new exported symbols for a single file.

    Returns (removed, sig_changed, renamed) where each entry is a dict
    carrying the relevant information for display.
    """
    old_exported = _exported_only(old_symbols)
    new_exported = _exported_only(new_symbols)

    old_by_key = {_key(s): s for s in old_exported}
    new_by_key = {_key(s): s for s in new_exported}

    old_keys = set(old_by_key)
    new_keys = set(new_by_key)

    # Kinds whose signatures represent an API contract
    _SIG_KINDS = {
        "function",
        "method",
        "class",
        "constructor",
        "interface",
        "trait",
        "struct",
    }

    # 1. Signature changes: same key exists in both, but signature differs
    sig_changed = []
    for k in old_keys & new_keys:
        if old_by_key[k]["kind"] not in _SIG_KINDS:
            continue
        old_sig = _sig_normalise(old_by_key[k].get("signature"))
        new_sig = _sig_normalise(new_by_key[k].get("signature"))
        if old_sig and new_sig and old_sig != new_sig:
            sig_changed.append(
                {
                    "name": old_by_key[k]["name"],
                    "kind": old_by_key[k]["kind"],
                    "old_signature": old_by_key[k].get("signature", ""),
                    "new_signature": new_by_key[k].get("signature", ""),
                    "file": file_path,
                    "line": new_by_key[k].get("line_start"),
                }
            )

    # 2. Removed: in old but not in new
    missing_keys = old_keys - new_keys
    # 3. Added: in new but not in old (candidates for rename matching)
    added_keys = new_keys - old_keys

    removed = []
    renamed = []

    # Try fuzzy rename matching for missing symbols
    added_map = {k: new_by_key[k] for k in added_keys}

    for mk in missing_keys:
        old_sym = old_by_key[mk]
        best_match = None
        best_score = 0.0

        for ak, new_sym in added_map.items():
            # Must be same kind to be considered a rename
            if old_sym["kind"] != new_sym["kind"]:
                continue

            # Name similarity
            name_sim = _similarity(old_sym["name"], new_sym["name"])

            # Line proximity bonus (if within 10 lines, boost score)
            old_line = old_sym.get("line_start") or 0
            new_line = new_sym.get("line_start") or 0
            line_dist = abs(old_line - new_line)
            line_bonus = max(0, (10 - line_dist) / 10) * 0.3

            # Signature similarity bonus
            old_sig = _sig_normalise(old_sym.get("signature"))
            new_sig = _sig_normalise(new_sym.get("signature"))
            sig_sim = _similarity(old_sig, new_sig) * 0.2 if old_sig and new_sig else 0

            score = name_sim + line_bonus + sig_sim

            if score > best_score:
                best_score = score
                best_match = ak

        # Threshold: require a reasonable match (name_sim > 0.5 area)
        if best_match is not None and best_score >= 0.6:
            new_sym = added_map.pop(best_match)
            renamed.append(
                {
                    "old_name": old_sym["name"],
                    "new_name": new_sym["name"],
                    "kind": old_sym["kind"],
                    "file": file_path,
                    "line": new_sym.get("line_start"),
                }
            )
        else:
            removed.append(
                {
                    "name": old_sym["name"],
                    "kind": old_sym["kind"],
                    "signature": old_sym.get("signature", ""),
                    "file": file_path,
                    "line": old_sym.get("line_start"),
                }
            )

    return removed, sig_changed, renamed


# ---------------------------------------------------------------------------
# Current DB symbol lookup
# ---------------------------------------------------------------------------


def _get_current_symbols(conn, file_path: str) -> list[dict]:
    """Fetch current symbols for *file_path* from the index DB.

    Returns dicts with the same keys as the extractor output so
    ``_compare_file`` can work uniformly.
    """
    row = conn.execute("SELECT id FROM files WHERE path = ?", (file_path,)).fetchone()
    if not row:
        # Try LIKE match
        row = conn.execute(
            "SELECT id FROM files WHERE path LIKE ? LIMIT 1",
            (f"%{file_path}",),
        ).fetchone()
    if not row:
        return []

    file_id = row["id"]
    rows = conn.execute(
        "SELECT name, qualified_name, kind, signature, line_start, line_end, "
        "visibility, is_exported FROM symbols WHERE file_id = ?",
        (file_id,),
    ).fetchall()

    return [
        {
            "name": r["name"],
            "qualified_name": r["qualified_name"],
            "kind": r["kind"],
            "signature": r["signature"],
            "line_start": r["line_start"],
            "line_end": r["line_end"],
            "visibility": r["visibility"],
            "is_exported": bool(r["is_exported"]),
        }
        for r in rows
    ]


# ---------------------------------------------------------------------------
# CLI command
# ---------------------------------------------------------------------------


@roam_capability(
    name="breaking",
    category="reports",
    summary="Detect potential breaking changes vs a git ref",
    maturity="stable",
    mcp_expose=True,
    mcp_preset=("core", "review"),
    side_effect=False,
    task_required=False,
    destructive=False,
    stale_sensitive=True,
    ai_safe=True,
    requires_index=True,
)
@click.command("breaking")
@click.argument("target", required=False, default="HEAD~1")
@click.pass_context
def breaking(ctx, target):
    """Detect potential breaking changes vs a git ref.

    Compares the current exported API surface against TARGET (default: HEAD~1)
    and reports removed exports, signature changes, and renames.

    For severity-classified output including additions and visibility changes,
    use ``api-changes``. This command provides a simpler binary signal:
    removed, signature-changed, or renamed exports.
    """
    json_mode = ctx.obj.get("json") if ctx.obj else False
    ensure_index()
    root = find_project_root()

    # 1. Find changed files
    changed = _git_changed_files(root, target)
    if not changed:
        if json_mode:
            click.echo(
                to_json(
                    json_envelope(
                        "breaking",
                        summary={
                            "verdict": f"no breaking changes vs {target}",
                            "removed": 0,
                            "signature_changed": 0,
                            "renamed": 0,
                        },
                        target=target,
                        removed=[],
                        signature_changed=[],
                        renamed=[],
                    )
                )
            )
        else:
            click.echo(f"VERDICT: no breaking changes vs {target}")
            click.echo()
            click.echo(f"No changed files vs {target}.")
        return

    all_removed: list[dict] = []
    all_sig_changed: list[dict] = []
    all_renamed: list[dict] = []

    with open_db(readonly=True) as conn:
        for fpath in changed:
            # Get old file content from the ref
            old_source = _git_show(root, target, fpath)
            if old_source is None:
                # File didn't exist at ref — it's new, no breaking changes
                continue

            old_symbols = _extract_old_symbols(old_source, fpath)
            if not old_symbols:
                continue

            # Get current symbols from the indexed DB
            new_symbols = _get_current_symbols(conn, fpath)

            removed, sig_changed, renamed = _compare_file(
                fpath,
                old_symbols,
                new_symbols,
            )
            all_removed.extend(removed)
            all_sig_changed.extend(sig_changed)
            all_renamed.extend(renamed)

    # Sort for stable output
    all_removed.sort(key=lambda r: (r["file"], r.get("line") or 0))
    all_sig_changed.sort(key=lambda r: (r["file"], r.get("line") or 0))
    all_renamed.sort(key=lambda r: (r["file"], r.get("line") or 0))

    total = len(all_removed) + len(all_sig_changed) + len(all_renamed)

    if total == 0:
        _breaking_verdict = f"no breaking changes vs {target}"
    else:
        _parts = []
        if all_removed:
            _parts.append(f"{len(all_removed)} removed")
        if all_sig_changed:
            _parts.append(f"{len(all_sig_changed)} sig changes")
        if all_renamed:
            _parts.append(f"{len(all_renamed)} renames")
        _breaking_verdict = f"{total} breaking changes vs {target}: {', '.join(_parts)}"

    if json_mode:
        click.echo(
            to_json(
                json_envelope(
                    "breaking",
                    summary={
                        "verdict": _breaking_verdict,
                        "removed": len(all_removed),
                        "signature_changed": len(all_sig_changed),
                        "renamed": len(all_renamed),
                        "total": total,
                    },
                    target=target,
                    removed=all_removed,
                    signature_changed=all_sig_changed,
                    renamed=all_renamed,
                )
            )
        )
        return

    # --- Text output ---
    click.echo(f"VERDICT: {_breaking_verdict}")
    click.echo()
    if total == 0:
        click.echo(f"No breaking changes vs {target}.")
        return

    click.echo(f"Breaking changes vs {target}:\n")

    if all_removed:
        click.echo("REMOVED:")
        for r in all_removed:
            kind = abbrev_kind(r["kind"])
            sig = format_signature(r.get("signature"), max_len=60)
            loc = f"{r['file']}:{r['line']}" if r.get("line") else r["file"]
            if sig:
                click.echo(f"  {kind} {sig}    {loc}")
            else:
                click.echo(f"  {kind} {r['name']}    {loc}")
        click.echo()

    if all_sig_changed:
        click.echo("SIGNATURE CHANGED:")
        for s in all_sig_changed:
            kind = abbrev_kind(s["kind"])
            old_sig = _display_sig(s["old_signature"])
            new_sig = _display_sig(s["new_signature"])
            loc = f"{s['file']}:{s['line']}" if s.get("line") else s["file"]
            click.echo(f"  {kind} {old_sig} -> {new_sig}    {loc}")
        click.echo()

    if all_renamed:
        click.echo("RENAMED:")
        for r in all_renamed:
            kind = abbrev_kind(r["kind"])
            loc = f"{r['file']}:{r['line']}" if r.get("line") else r["file"]
            click.echo(f"  {kind} {r['old_name']} -> {r['new_name']}    {loc}")
        click.echo()

    # Summary line
    parts = []
    if all_removed:
        parts.append(f"{len(all_removed)} removed")
    if all_sig_changed:
        parts.append(f"{len(all_sig_changed)} signature change{'s' if len(all_sig_changed) != 1 else ''}")
    if all_renamed:
        parts.append(f"{len(all_renamed)} rename{'s' if len(all_renamed) != 1 else ''}")
    click.echo(f"Summary: {', '.join(parts)}")
