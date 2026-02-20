"""Link documentation to code -- find what docs describe what code."""

from __future__ import annotations

import os
import re
import subprocess

import click

from roam.db.connection import open_db, find_project_root
from roam.output.formatter import abbrev_kind, to_json, json_envelope
from roam.commands.resolve import ensure_index


_DOC_EXTENSIONS = {".md", ".txt", ".rst", ".adoc"}
_SKIP_DIRS = {"node_modules", ".roam", ".git", "__pycache__", "vendor", "dist", "build"}
_MIN_NAME_LEN = 3  # skip very short names to avoid false positives


def _find_doc_files(root):
    """Find documentation files in the project."""
    doc_files = []
    try:
        result = subprocess.run(
            ["git", "ls-files"],
            cwd=str(root), capture_output=True, text=True, timeout=10,
            encoding="utf-8", errors="replace",
        )
        if result.returncode == 0:
            for line in result.stdout.strip().splitlines():
                path = line.strip().replace("\\", "/")
                if not path:
                    continue
                if any(skip in path.split("/") for skip in _SKIP_DIRS):
                    continue
                ext = os.path.splitext(path)[1].lower()
                if ext in _DOC_EXTENSIONS:
                    doc_files.append(path)
        else:
            raise OSError("git ls-files failed")
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        # Fallback: walk filesystem
        for dirpath, dirnames, filenames in os.walk(str(root)):
            dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS]
            for f in filenames:
                ext = os.path.splitext(f)[1].lower()
                if ext in _DOC_EXTENSIONS:
                    rel = os.path.relpath(
                        os.path.join(dirpath, f), str(root)
                    ).replace("\\", "/")
                    doc_files.append(rel)
    return sorted(doc_files)


def _scan_doc_for_symbols(root, doc_path, symbol_names):
    """Scan a doc file for references to known symbol names.

    Returns list of {symbol, line, snippet}.
    """
    refs = []
    full_path = os.path.join(str(root), doc_path)
    try:
        with open(full_path, "r", encoding="utf-8", errors="replace") as fh:
            lines = fh.readlines()
    except (OSError, IOError):
        return refs

    for line_num, line_text in enumerate(lines, 1):
        for name in symbol_names:
            if len(name) < _MIN_NAME_LEN:
                continue
            if re.search(r'\b' + re.escape(name) + r'\b', line_text):
                snippet = line_text.strip()[:100]
                refs.append({
                    "symbol": name,
                    "line": line_num,
                    "snippet": snippet,
                })
    return refs


def _scan_doc_for_potential_symbols(root, doc_path):
    """Scan a doc file for identifier-like tokens that might be symbol names.

    Returns set of potential names (for drift detection).
    """
    potential = set()
    full_path = os.path.join(str(root), doc_path)
    try:
        with open(full_path, "r", encoding="utf-8", errors="replace") as fh:
            content = fh.read()
    except (OSError, IOError):
        return potential

    # Find identifiers in code blocks/backticks
    for match in re.finditer(r'`([a-zA-Z_]\w+)`', content):
        name = match.group(1)
        if len(name) >= _MIN_NAME_LEN:
            potential.add(name)

    return potential


@click.command("intent")
@click.option("--symbol", "symbol_name", default=None,
              help="Find docs mentioning this symbol")
@click.option("--doc", "doc_path", default=None,
              help="Find code referenced by this doc")
@click.option("--drift", is_flag=True,
              help="Show references to non-existent symbols")
@click.option("--undocumented", is_flag=True,
              help="Show important symbols not in docs")
@click.option("--top", "top_n", default=20, type=int,
              help="Max items to show")
@click.pass_context
def intent(ctx, symbol_name, doc_path, drift, undocumented, top_n):
    """Link documentation to code -- find what docs describe what code.

    Scans markdown/text documentation for references to known symbols
    and reports doc-to-code links, drift (dead references), and
    undocumented high-importance symbols.
    """
    json_mode = ctx.obj.get("json") if ctx.obj else False
    ensure_index()
    root = find_project_root()

    with open_db(readonly=True) as conn:
        # Get all symbol names from DB (length >= _MIN_NAME_LEN)
        all_syms = conn.execute(
            "SELECT DISTINCT name FROM symbols WHERE length(name) >= ?",
            (_MIN_NAME_LEN,)
        ).fetchall()
        symbol_names = set(s["name"] for s in all_syms)

        # Find doc files
        doc_files = _find_doc_files(root)

        if not doc_files:
            verdict = "No documentation files found"
            if json_mode:
                click.echo(to_json(json_envelope(
                    "intent",
                    summary={
                        "verdict": verdict,
                        "doc_files": 0,
                        "links": 0,
                        "drift_count": 0,
                        "undocumented_count": 0,
                    },
                    links=[],
                    by_doc={},
                    drift=[],
                    undocumented=[],
                )))
            else:
                click.echo(f"VERDICT: {verdict}")
            return

        # ------------------------------------------------------------------
        # Mode: specific symbol
        # ------------------------------------------------------------------
        if symbol_name:
            links = []
            for df in doc_files:
                refs = _scan_doc_for_symbols(root, df, {symbol_name})
                for ref in refs:
                    ref["doc"] = df
                    links.append(ref)

            links = links[:top_n]
            n = len(links)
            verdict = (
                f"{n} doc mention{'s' if n != 1 else ''} of '{symbol_name}'"
            )

            if json_mode:
                click.echo(to_json(json_envelope(
                    "intent",
                    summary={
                        "verdict": verdict,
                        "doc_files": len(doc_files),
                        "links": n,
                        "drift_count": 0,
                        "undocumented_count": 0,
                    },
                    links=links,
                    by_doc={},
                    drift=[],
                    undocumented=[],
                )))
                return

            click.echo(f"VERDICT: {verdict}")
            if not links:
                click.echo(f"  '{symbol_name}' is not mentioned in any documentation files.")
                return
            click.echo()
            click.echo(f"DOCS MENTIONING '{symbol_name}':")
            for lnk in links:
                click.echo(f"  {lnk['doc']}:{lnk['line']}: {lnk['snippet']}")
            return

        # ------------------------------------------------------------------
        # Mode: specific doc
        # ------------------------------------------------------------------
        if doc_path:
            # Normalize path separators
            doc_path_norm = doc_path.replace("\\", "/")
            refs = _scan_doc_for_symbols(root, doc_path_norm, symbol_names)
            # De-duplicate: keep first occurrence of each symbol
            seen = set()
            unique_refs = []
            for ref in refs:
                if ref["symbol"] not in seen:
                    seen.add(ref["symbol"])
                    unique_refs.append(ref)
            unique_refs = unique_refs[:top_n]
            n = len(unique_refs)
            verdict = f"{n} symbol{'s' if n != 1 else ''} referenced in '{doc_path_norm}'"

            if json_mode:
                click.echo(to_json(json_envelope(
                    "intent",
                    summary={
                        "verdict": verdict,
                        "doc_files": 1,
                        "links": n,
                        "drift_count": 0,
                        "undocumented_count": 0,
                    },
                    links=[dict(doc=doc_path_norm, **r) for r in unique_refs],
                    by_doc={doc_path_norm: unique_refs},
                    drift=[],
                    undocumented=[],
                )))
                return

            click.echo(f"VERDICT: {verdict}")
            if not unique_refs:
                click.echo(f"  No known symbols found in '{doc_path_norm}'.")
                return
            click.echo()
            click.echo(f"SYMBOLS IN '{doc_path_norm}':")
            for ref in unique_refs:
                click.echo(f"  L{ref['line']}: {ref['symbol']}  -- {ref['snippet']}")
            return

        # ------------------------------------------------------------------
        # Mode: drift detection
        # ------------------------------------------------------------------
        if drift:
            drift_refs = []
            for df in doc_files:
                potential = _scan_doc_for_potential_symbols(root, df)
                for name in sorted(potential):
                    if name not in symbol_names:
                        drift_refs.append({"doc": df, "symbol": name})

            drift_refs = drift_refs[:top_n]
            n = len(drift_refs)
            verdict = (
                f"{n} drift reference{'s' if n != 1 else ''} found "
                f"(symbols in docs that don't exist in codebase)"
            )

            if json_mode:
                click.echo(to_json(json_envelope(
                    "intent",
                    summary={
                        "verdict": verdict,
                        "doc_files": len(doc_files),
                        "links": 0,
                        "drift_count": n,
                        "undocumented_count": 0,
                    },
                    links=[],
                    by_doc={},
                    drift=drift_refs,
                    undocumented=[],
                )))
                return

            click.echo(f"VERDICT: {verdict}")
            if not drift_refs:
                click.echo("  No drift detected -- all backtick identifiers in docs exist in codebase.")
                return
            click.echo()
            click.echo("DRIFT (referenced in docs, not in codebase):")
            # Group by doc
            by_doc: dict[str, list[str]] = {}
            for dr in drift_refs:
                by_doc.setdefault(dr["doc"], []).append(dr["symbol"])
            for doc_f, syms in sorted(by_doc.items()):
                click.echo(f"  {doc_f} references: {', '.join(syms)}")
            return

        # ------------------------------------------------------------------
        # Mode: undocumented high-centrality symbols
        # ------------------------------------------------------------------
        if undocumented:
            # Collect all documented symbol names across all docs
            documented = set()
            for df in doc_files:
                refs = _scan_doc_for_symbols(root, df, symbol_names)
                documented.update(r["symbol"] for r in refs)

            # Find high-pagerank symbols NOT documented
            # symbol_metrics may not always exist; guard with LEFT JOIN
            try:
                high_pr = conn.execute(
                    """SELECT s.name, s.kind, f.path as file_path, sm.pagerank
                       FROM symbols s
                       JOIN files f ON s.file_id = f.id
                       JOIN symbol_metrics sm ON sm.symbol_id = s.id
                       WHERE s.name NOT LIKE '\\_%' ESCAPE '\\'
                       AND s.kind IN ('function', 'method', 'class')
                       ORDER BY sm.pagerank DESC
                       LIMIT ?""",
                    (top_n * 3,)
                ).fetchall()
            except Exception:
                high_pr = []

            undoc_list = [
                {
                    "name": s["name"],
                    "kind": s["kind"],
                    "file": s["file_path"],
                    "pagerank": round(float(s["pagerank"]), 6),
                }
                for s in high_pr
                if s["name"] not in documented
            ][:top_n]

            n = len(undoc_list)
            verdict = (
                f"{n} high-centrality symbol{'s' if n != 1 else ''} "
                f"with no documentation coverage"
            )

            if json_mode:
                click.echo(to_json(json_envelope(
                    "intent",
                    summary={
                        "verdict": verdict,
                        "doc_files": len(doc_files),
                        "links": 0,
                        "drift_count": 0,
                        "undocumented_count": n,
                    },
                    links=[],
                    by_doc={},
                    drift=[],
                    undocumented=undoc_list,
                )))
                return

            click.echo(f"VERDICT: {verdict}")
            if not undoc_list:
                click.echo("  All high-centrality symbols appear in documentation.")
                return
            click.echo()
            click.echo("UNDOCUMENTED HIGH-CENTRALITY SYMBOLS:")
            for sym in undoc_list:
                kind_abbr = abbrev_kind(sym["kind"])
                click.echo(
                    f"  {kind_abbr}  {sym['name']}  {sym['file']}  "
                    f"pagerank={sym['pagerank']}"
                )
            return

        # ------------------------------------------------------------------
        # Default: all doc-to-code links
        # ------------------------------------------------------------------
        all_links = []
        for df in doc_files:
            refs = _scan_doc_for_symbols(root, df, symbol_names)
            for ref in refs:
                ref["doc"] = df
                all_links.append(ref)

        # Count drift references (backtick identifiers not in symbol set)
        drift_count = 0
        for df in doc_files:
            potential = _scan_doc_for_potential_symbols(root, df)
            drift_count += sum(1 for name in potential if name not in symbol_names)

        # Group by doc file
        by_doc: dict[str, list[dict]] = {}
        for link in all_links:
            by_doc.setdefault(link["doc"], []).append(
                {"symbol": link["symbol"], "line": link["line"], "snippet": link["snippet"]}
            )

        # Apply top_n limit on total links
        total_links = len(all_links)
        display_links = all_links[:top_n]

        n_docs = len(by_doc)
        verdict = (
            f"{total_links} doc-code link{'s' if total_links != 1 else ''} "
            f"across {n_docs} doc{'s' if n_docs != 1 else ''}"
        )
        if drift_count:
            verdict += f", {drift_count} drift{'s' if drift_count != 1 else ''}"

        if json_mode:
            click.echo(to_json(json_envelope(
                "intent",
                summary={
                    "verdict": verdict,
                    "doc_files": len(doc_files),
                    "links": total_links,
                    "drift_count": drift_count,
                    "undocumented_count": 0,
                },
                links=display_links,
                by_doc=by_doc,
                drift=[],
                undocumented=[],
            )))
            return

        click.echo(f"VERDICT: {verdict}")

        if not all_links:
            click.echo("  No doc-to-code links found.")
            return

        click.echo()
        click.echo("DOC-CODE LINKS:")
        shown = 0
        for doc_f, doc_refs in sorted(by_doc.items()):
            # Count unique symbols for this doc
            unique_syms = {r["symbol"] for r in doc_refs}
            click.echo(f"  {doc_f} -> {len(unique_syms)} symbol{'s' if len(unique_syms) != 1 else ''}")
            for ref in doc_refs:
                if shown >= top_n:
                    break
                # Find kind for this symbol
                click.echo(f"    L{ref['line']}: {ref['symbol']}")
                shown += 1
            if shown >= top_n:
                remaining = total_links - shown
                if remaining > 0:
                    click.echo(f"  (+{remaining} more)")
                break
            click.echo()
