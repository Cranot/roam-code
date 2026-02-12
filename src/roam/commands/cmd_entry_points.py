"""Entry point catalog with protocol classification and reachability coverage."""

import re
from collections import defaultdict

import click

from roam.db.connection import open_db
from roam.output.formatter import abbrev_kind, loc, format_table, to_json, json_envelope
from roam.commands.resolve import ensure_index


# ---------------------------------------------------------------------------
# Protocol classification
# ---------------------------------------------------------------------------

# Decorator / signature patterns that identify each protocol.
# Each entry is (protocol, compiled_regex) tested against the symbol's
# signature column *and* against the symbol's name as a fallback.

_DECORATOR_PATTERNS = [
    # HTTP
    ("HTTP", re.compile(
        r"@(?:app|router|blueprint|bp)\."
        r"(?:route|get|post|put|patch|delete|head|options)"
        r"|@(?:Get|Post|Put|Patch|Delete|Request)Mapping"
        r"|@api_view"
        r"|@action"
        r"|@require_http_methods"
        r"|@csrf_exempt"
        r"|http\.Handle"
        r"|\.get\(|\.post\(|\.put\(|\.delete\(",
        re.IGNORECASE,
    )),
    # CLI
    ("CLI", re.compile(
        r"@click\.(?:command|group)"
        r"|@(?:cli|app)\.command"
        r"|argparse"
        r"|sys\.argv"
        r"|add_argument\("
        r"|@Cli",
        re.IGNORECASE,
    )),
    # Scheduled / periodic
    ("Scheduled", re.compile(
        r"@(?:schedule|periodic_task|crontab|celery_task)"
        r"|@shared_task"
        r"|@task\("
        r"|cron"
        r"|@Scheduled",
        re.IGNORECASE,
    )),
    # Message / queue
    ("Message", re.compile(
        r"@subscribe"
        r"|@consume"
        r"|@listener"
        r"|@queue"
        r"|@on_message"
        r"|@SqsListener"
        r"|@RabbitListener"
        r"|@KafkaListener"
        r"|message_handler"
        r"|consumer",
        re.IGNORECASE,
    )),
    # Event
    ("Event", re.compile(
        r"@on_event"
        r"|@event_handler"
        r"|@receiver"
        r"|\.on\("
        r"|addEventListener"
        r"|@HostListener"
        r"|EventHandler"
        r"|event_listener",
        re.IGNORECASE,
    )),
]

# Name-based patterns (tested against symbol name when signature gives no hit)
_NAME_PATTERNS = [
    ("Event", re.compile(r"^on_[a-z]|^handle_[a-z]|_handler$|_listener$", re.IGNORECASE)),
    ("Main", re.compile(r"^main$|^__main__$|^cli$|^run$|^app$|^entrypoint$", re.IGNORECASE)),
    ("Scheduled", re.compile(r"cron|schedule|periodic|tick", re.IGNORECASE)),
    ("Message", re.compile(r"consume|subscriber|on_message|process_message", re.IGNORECASE)),
    ("CLI", re.compile(r"^cmd_|_command$|_cmd$", re.IGNORECASE)),
    ("HTTP", re.compile(r"_view$|_endpoint$|_controller$|_handler$", re.IGNORECASE)),
]


def _classify_protocol(name, signature):
    """Return the protocol string for a symbol based on its signature and name.

    Returns one of: HTTP, CLI, Event, Scheduled, Message, Main, Export.
    """
    sig = signature or ""

    # 1. Check decorator / signature patterns first — highest confidence
    for proto, regex in _DECORATOR_PATTERNS:
        if regex.search(sig):
            return proto

    # 2. Check name-based patterns
    for proto, regex in _NAME_PATTERNS:
        if regex.search(name):
            return proto

    # 3. Default — it's an exported symbol with no internal callers
    return "Export"


# ---------------------------------------------------------------------------
# Reachability: BFS from an entry point through the call graph
# ---------------------------------------------------------------------------

def _build_adj(conn):
    """Build forward adjacency list (source -> set of targets)."""
    adj = defaultdict(set)
    for row in conn.execute("SELECT source_id, target_id FROM edges").fetchall():
        adj[row["source_id"]].add(row["target_id"])
    return adj


def _reachable_set(adj, start_id):
    """BFS from *start_id*; return the set of all reachable symbol IDs."""
    visited = {start_id}
    queue = [start_id]
    while queue:
        current = queue.pop(0)
        for neighbor in adj.get(current, ()):
            if neighbor not in visited:
                visited.add(neighbor)
                queue.append(neighbor)
    return visited


# ---------------------------------------------------------------------------
# Core query: find entry-point symbols
# ---------------------------------------------------------------------------

def _find_entry_point_symbols(conn, protocol_filter, limit):
    """Return a list of dicts describing each entry point symbol.

    Strategy:
      1. Symbols with in_degree = 0 in graph_metrics (no internal callers).
      2. Cross-reference with is_exported.
      3. Also include symbols whose name/signature matches a known
         entry-point decorator even if in_degree > 0 (framework routes can
         call each other, but are still entry points).
    """
    # --- Phase 1: in_degree=0, exported, function-like -----------------
    rows_zero = conn.execute(
        "SELECT s.id, s.name, s.qualified_name, s.kind, s.signature, "
        "       s.is_exported, f.path AS file_path, s.line_start, "
        "       gm.in_degree, gm.out_degree "
        "FROM symbols s "
        "JOIN files f ON s.file_id = f.id "
        "JOIN graph_metrics gm ON s.id = gm.symbol_id "
        "WHERE gm.in_degree = 0 "
        "  AND s.kind IN ('function', 'method', 'class') "
        "ORDER BY gm.out_degree DESC"
    ).fetchall()

    # --- Phase 2: symbols with known entry-point decorators ------------
    # These may have in_degree > 0 (e.g. route that also gets called internally).
    # We pull all function-like symbols with a non-null signature and test them.
    rows_deco = conn.execute(
        "SELECT s.id, s.name, s.qualified_name, s.kind, s.signature, "
        "       s.is_exported, f.path AS file_path, s.line_start, "
        "       gm.in_degree, gm.out_degree "
        "FROM symbols s "
        "JOIN files f ON s.file_id = f.id "
        "JOIN graph_metrics gm ON s.id = gm.symbol_id "
        "WHERE s.signature IS NOT NULL "
        "  AND gm.in_degree > 0 "
        "  AND s.kind IN ('function', 'method', 'class') "
        "ORDER BY gm.out_degree DESC"
    ).fetchall()

    seen_ids = set()
    entries = []

    def _add(row):
        if row["id"] in seen_ids:
            return
        proto = _classify_protocol(row["name"], row["signature"])
        if protocol_filter and proto.lower() != protocol_filter.lower():
            return
        seen_ids.add(row["id"])
        entries.append({
            "id": row["id"],
            "name": row["qualified_name"] or row["name"],
            "kind": row["kind"],
            "protocol": proto,
            "file": row["file_path"],
            "line": row["line_start"],
            "fan_out": row["out_degree"] or 0,
            "is_exported": bool(row["is_exported"]),
        })

    # Phase 1 results
    for r in rows_zero:
        _add(r)

    # Phase 2: only add if the signature matches a decorator pattern
    for r in rows_deco:
        sig = r["signature"] or ""
        for _proto, regex in _DECORATOR_PATTERNS:
            if regex.search(sig):
                _add(r)
                break

    # Sort by protocol then fan_out descending
    protocol_order = ["HTTP", "CLI", "Event", "Scheduled", "Message", "Main", "Export"]
    entries.sort(key=lambda e: (
        protocol_order.index(e["protocol"]) if e["protocol"] in protocol_order else 99,
        -e["fan_out"],
    ))

    if limit:
        entries = entries[:limit]

    return entries


# ---------------------------------------------------------------------------
# Coverage: % of total symbols reachable from each entry point
# ---------------------------------------------------------------------------

def _compute_coverage(conn, entries, adj):
    """Add a 'coverage_pct' field to each entry dict."""
    total_symbols = conn.execute("SELECT COUNT(*) FROM symbols").fetchone()[0]
    if total_symbols == 0:
        for e in entries:
            e["coverage_pct"] = 0.0
        return

    for e in entries:
        reachable = _reachable_set(adj, e["id"])
        e["coverage_pct"] = round(len(reachable) * 100 / total_symbols, 1)


# ---------------------------------------------------------------------------
# CLI command
# ---------------------------------------------------------------------------

@click.command("entry-points")
@click.option("--protocol", "protocol_filter", default=None,
              type=click.Choice(
                  ["HTTP", "CLI", "Event", "Scheduled", "Message", "Main", "Export"],
                  case_sensitive=False,
              ),
              help="Show only entry points of this protocol type.")
@click.option("--limit", default=50, show_default=True,
              help="Maximum number of entry points to display.")
@click.pass_context
def entry_points(ctx, protocol_filter, limit):
    """Entry point catalog with protocol classification.

    Lists every symbol that serves as an entry point into the codebase,
    classified by protocol (HTTP, CLI, Event, Scheduled, Message, Main,
    Export).  For each entry point the command shows reachability coverage —
    what percentage of symbols in the project are transitively reachable
    from that entry point through the call graph.
    """
    json_mode = ctx.obj.get("json") if ctx.obj else False
    ensure_index()

    with open_db(readonly=True) as conn:
        entries = _find_entry_point_symbols(conn, protocol_filter, limit)

        if not entries:
            if json_mode:
                click.echo(to_json(json_envelope("entry-points",
                    summary={"total": 0, "note": "no entry points found"},
                    entry_points=[],
                )))
            else:
                click.echo("No entry points found.")
            return

        # Build adjacency once, compute coverage for every entry
        adj = _build_adj(conn)
        _compute_coverage(conn, entries, adj)

        # Group by protocol for display
        by_protocol = defaultdict(list)
        for e in entries:
            by_protocol[e["protocol"]].append(e)

        # --- JSON output ----------------------------------------------
        if json_mode:
            # Strip internal 'id' key before serialising
            clean = []
            for e in entries:
                clean.append({k: v for k, v in e.items() if k != "id"})

            protocol_summary = {
                proto: len(items) for proto, items in by_protocol.items()
            }

            click.echo(to_json(json_envelope("entry-points",
                summary={
                    "total": len(entries),
                    "by_protocol": protocol_summary,
                },
                entry_points=clean,
            )))
            return

        # --- Text output ----------------------------------------------
        click.echo("=== Entry Points ===\n")
        click.echo(f"Total: {len(entries)}")
        proto_parts = [f"{p} {len(items)}" for p, items in by_protocol.items()]
        click.echo(f"Protocols: {', '.join(proto_parts)}")
        click.echo()

        protocol_order = ["HTTP", "CLI", "Event", "Scheduled", "Message", "Main", "Export"]
        for proto in protocol_order:
            items = by_protocol.get(proto)
            if not items:
                continue
            click.echo(f"-- {proto} ({len(items)}) --")
            rows = []
            for e in items:
                rows.append([
                    e["name"],
                    abbrev_kind(e["kind"]),
                    loc(e["file"], e["line"]),
                    str(e["fan_out"]),
                    f"{e['coverage_pct']}%",
                ])
            click.echo(format_table(
                ["Name", "Kind", "Location", "Fan-out", "Coverage"],
                rows,
                budget=30,
            ))
            click.echo()
