"""Find all consumers of a symbol: callers, importers, inheritors.

Output formats: text (default), ``--json``. SARIF is deliberately NOT
emitted because uses outputs are invocation-scoped consumer rankings —
not per-location violations. Editor consumers should use the JSON
envelope directly. See action.yml _SUPPORTED_SARIF allowlist
+ W1175-RESEARCH Bucket B propagation plan + W1148 audit memo.
"""

from __future__ import annotations

import click

from roam.capability import roam_capability
from roam.commands.changed_files import is_test_file
from roam.commands.resolve import ensure_index, symbol_not_found_hint
from roam.db.connection import find_project_root, open_db
from roam.languages import JS_FAMILY_LANGUAGES
from roam.output.formatter import abbrev_kind, format_table, json_envelope, loc, to_json
from roam.output.metric_definitions import CALLER_METRIC_RAW


def _test_text_consumers(conn, name: str, existing_files: set[str]) -> list[dict]:
    """Find test-file mentions when no symbol edge could be created.

    JS/Vitest tests often contain only top-level imports and test callbacks,
    leaving the resolver without a concrete source symbol for an edge. This
    fallback is deliberately scoped to test files and exact identifier matches.
    """
    import re

    project_root = find_project_root()
    pattern = re.compile(rf"\b{re.escape(name)}\b")
    consumers: list[dict] = []
    for f in conn.execute("SELECT path FROM files").fetchall():
        path = f["path"]
        if path in existing_files or not is_test_file(path):
            continue
        try:
            source = (project_root / path).read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        match = pattern.search(source)
        if not match:
            continue
        line = source.count("\n", 0, match.start()) + 1
        consumers.append(
            {
                "name": path.rsplit("/", 1)[-1],
                "qualified_name": path,
                "kind": "test",
                "line_start": line,
                "path": path,
                "edge_kind": "test",
                "edge_line": line,
                "target_name": name,
            }
        )
    return consumers


@roam_capability(
    category="exploration",
    summary="Show all consumers of a symbol: callers, importers, inheritors.",
    inputs=["name"],
    outputs=["consumers"],
    examples=[
        "roam uses handleSave",
        "roam uses AuthService --full",
    ],
    tags=["exploration", "consumers"],
    ai_safe=True,
    requires_index=True,
    maturity="stable",
    mcp_expose=True,
    mcp_preset=("core",),
    side_effect=False,
    task_required=False,
    destructive=False,
    stale_sensitive=True,
)
@click.command()
@click.argument("name", metavar="SYMBOL")
@click.option("--full", is_flag=True, help="Show all results without truncation")
@click.pass_context
def uses(ctx, name, full):
    """Show all consumers of SYMBOL: callers, importers, inheritors.

    SYMBOL is a symbol identifier (bare name or qualified name). Unlike
    ``impact`` (which computes transitive blast radius via graph
    traversal), this command lists direct consumers grouped by
    relationship type.

    Also available as ``roam refs <SYMBOL>`` — the grep-familiar alias.

    \b
    Examples:
      roam uses handle_login
      roam refs handle_login
      roam uses UserService.create --full
      roam --json uses authenticate

    See also ``impact`` (transitive blast radius), ``deps`` (file-level
    imports), and ``refs-text`` (string-literal audit with verdicts).
    """
    json_mode = ctx.obj.get("json") if ctx.obj else False
    token_budget = ctx.obj.get("budget", 0) if ctx.obj else 0
    ensure_index()

    with open_db(readonly=True) as conn:
        # Find the target symbol(s) by name
        targets = conn.execute(
            "SELECT id, name, kind, qualified_name FROM symbols WHERE name = ?",
            (name,),
        ).fetchall()

        if not targets:
            # Try LIKE search
            targets = conn.execute(
                "SELECT id, name, kind, qualified_name FROM symbols WHERE name LIKE ? LIMIT 50",
                (f"%{name}%",),
            ).fetchall()

        if not targets:
            # JSON mode must always emit an envelope — never plaintext.
            # Pre-v12, the plaintext hint was printed unconditionally and
            # downstream parsers (recipe runner, MCP tool wrappers,
            # `roam ask`) crashed on the non-JSON output.
            if json_mode:
                click.echo(
                    to_json(
                        json_envelope(
                            "uses",
                            summary={
                                "verdict": f"symbol not found: '{name}'",
                                "total_consumers": 0,
                                "total_files": 0,
                                "error": "symbol_not_found",
                            },
                            symbol=name,
                            consumers={},
                            hint=symbol_not_found_hint(name),
                        )
                    )
                )
                raise SystemExit(1)
            click.echo(symbol_not_found_hint(name))
            raise SystemExit(1)

        target_ids = [t["id"] for t in targets]
        placeholders = ",".join("?" for _ in target_ids)

        # Find ALL edges pointing TO these targets
        rows = list(
            conn.execute(
                f"""SELECT s.name, s.qualified_name, s.kind, s.line_start,
                       f.path, e.kind as edge_kind, e.line as edge_line,
                       t.name as target_name
                FROM edges e
                JOIN symbols s ON e.source_id = s.id
                JOIN symbols t ON e.target_id = t.id
                JOIN files f ON s.file_id = f.id
                WHERE e.target_id IN ({placeholders})
                ORDER BY e.kind, f.path, s.line_start""",
                target_ids,
            ).fetchall()
        )
        # 12.13 perf — only scan test files for text mentions when the
        # target lives in a language where the symbol resolver leaves
        # gaps (JS / TS / Vue / Svelte). Python / Go / Rust resolvers
        # already produce edges for every test reference, so the
        # fallback was just a 4-second-per-call no-op on those repos
        # (590 file reads against this Python repo to find the same
        # answer the edges table already had). Skipping it on
        # languages that don't need it brings ``roam uses`` from
        # ~700ms warm to ~120ms.
        target_langs = {(t["qualified_name"] or "").split(".", 1)[0] for t in targets}
        target_files = conn.execute(
            f"SELECT DISTINCT f.language FROM symbols s JOIN files f ON s.file_id = f.id "
            f"WHERE s.id IN ({placeholders})",
            target_ids,
        ).fetchall()
        target_langs = {(r["language"] or "").lower() for r in target_files}
        if target_langs & set(JS_FAMILY_LANGUAGES):
            rows.extend(
                _test_text_consumers(
                    conn,
                    name,
                    {r["path"] for r in rows if is_test_file(r["path"])},
                )
            )

        if not rows:
            if json_mode:
                click.echo(
                    to_json(
                        json_envelope(
                            "uses",
                            summary={
                                "verdict": f"no consumers of '{name}' found",
                                "total_consumers": 0,
                                "production_consumers": 0,
                                "test_consumers": 0,
                                "tested": False,
                                "total_files": 0,
                                "caller_metric_definition": CALLER_METRIC_RAW,
                            },
                            symbol=name,
                            consumers={},
                        )
                    )
                )
            else:
                click.echo(f"VERDICT: no consumers of '{name}' found.\n")
                click.echo(f"No consumers of '{name}' found.")
            return

        # Group by edge kind
        by_kind = {}
        for r in rows:
            by_kind.setdefault(r["edge_kind"], []).append(r)

        def _scope(row) -> str:
            return "test" if is_test_file(row["path"]) else "production"

        def _dedupe(items):
            seen = set()
            deduped = []
            for item in items:
                key = (item["qualified_name"], item["path"], item["edge_kind"])
                if key not in seen:
                    seen.add(key)
                    deduped.append(item)
            return deduped

        deduped_rows = _dedupe(rows)
        production_rows = [r for r in deduped_rows if _scope(r) == "production"]
        test_rows = [r for r in deduped_rows if _scope(r) == "test"]

        # Dedup within each group by (name, path)
        kind_labels = {
            "call": "Called by",
            "import": "Imported by",
            "inherits": "Extended by",
            "implements": "Implemented by",
            "uses_trait": "Used by (trait)",
            "template": "Used in template",
            "test": "Mentioned in tests",
        }

        if json_mode:
            json_groups = {}
            for kind, items in by_kind.items():
                deduped = _dedupe(items)
                json_groups[kind] = [
                    {
                        "name": r["name"],
                        "kind": r["kind"],
                        "location": loc(r["path"], r["line_start"]),
                        "scope": _scope(r),
                    }
                    for r in deduped
                ]
            files = set(r["path"] for r in rows)
            total_consumers = sum(len(v) for v in json_groups.values())
            _verdict = (
                f"'{name}': {len(production_rows)} production consumers, "
                f"{len(test_rows)} test consumers in {len(files)} files"
            )
            click.echo(
                to_json(
                    json_envelope(
                        "uses",
                        summary={
                            "verdict": _verdict,
                            "total_consumers": total_consumers,
                            "production_consumers": len(production_rows),
                            "test_consumers": len(test_rows),
                            "tested": bool(test_rows),
                            "total_files": len(files),
                            "caller_metric_definition": CALLER_METRIC_RAW,
                        },
                        budget=token_budget,
                        symbol=name,
                        consumers=json_groups,
                        total_files=len(files),
                    )
                )
            )
            return

        total = 0
        # Compute totals for verdict
        _files_set = set(r["path"] for r in rows)
        click.echo(
            f"VERDICT: '{name}': {len(production_rows)} production consumers, "
            f"{len(test_rows)} test consumers in {len(_files_set)} files\n"
        )
        click.echo(f"=== Consumers of '{name}' ===\n")

        # Show in a consistent order, then any remaining kinds
        display_order = ["call", "import", "template", "inherits", "implements", "uses_trait"]
        remaining = [k for k in by_kind if k not in display_order]
        for kind in display_order + remaining:
            items = by_kind.get(kind)
            if not items:
                continue

            deduped = _dedupe(items)

            label = kind_labels.get(kind, kind)
            total += len(deduped)

            table_rows = []
            for r in deduped:
                table_rows.append(
                    [
                        abbrev_kind(r["kind"]),
                        r["name"],
                        loc(r["path"], r["line_start"]),
                        _scope(r),
                    ]
                )

            click.echo(f"-- {label} ({len(deduped)}) --")
            click.echo(
                format_table(
                    ["Kind", "Name", "Location", "Scope"],
                    table_rows,
                    budget=0 if full else 20,
                )
            )
            click.echo()

        # File summary: which files depend on this symbol
        files = set()
        for r in rows:
            files.add(r["path"])
        click.echo(f"Total: {total} consumers across {len(files)} files")
