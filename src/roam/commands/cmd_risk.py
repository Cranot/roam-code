"""Show domain-weighted risk ranking of symbols."""

from __future__ import annotations

import json
import os
import re

import click

from roam.db.connection import open_db
from roam.output.formatter import abbrev_kind, loc, format_table, to_json, json_envelope
from roam.commands.resolve import ensure_index


# Default domain keyword -> weight multiplier mapping.
# Symbols matching high-weight domains rank higher in risk output.
_DEFAULT_DOMAINS = {
    # Financial / accounting (critical -- bugs lose money)
    "money": 10, "payment": 10, "invoice": 10, "ledger": 10,
    "balance": 10, "transaction": 10, "credit": 10, "debit": 10,
    "tax": 10, "vat": 10, "price": 8, "cost": 8, "amount": 8,
    "currency": 8, "billing": 8, "refund": 8, "receipt": 8,
    "accounting": 10, "fiscal": 10, "journal": 8,
    # Auth / security (critical -- bugs leak data)
    "auth": 8, "password": 10, "token": 8, "session": 3,
    "permission": 8, "encrypt": 10, "decrypt": 10, "secret": 10,
    "credential": 10, "login": 6, "logout": 4,
    # Data integrity
    "delete": 6, "destroy": 6, "migrate": 6, "truncate": 8,
    "backup": 6, "restore": 6, "sync": 4, "import": 1.5, "export": 1.5,
    # Business logic (medium)
    "order": 2, "customer": 5, "user": 4, "account": 5,
    "calculate": 5, "validate": 4, "process": 1.5, "approve": 5,
    "schedule": 4, "notify": 3, "report": 4,
    # UI / presentation (dampened -- less risky than business logic)
    "render": 0.3, "display": 0.3, "show": 0.3, "hide": 0.3, "style": 0.3,
    "theme": 0.3, "color": 0.3, "icon": 0.3, "modal": 0.3, "tooltip": 0.3,
    "animation": 0.3, "layout": 0.3, "grid": 0.3, "menu": 0.3,
    "button": 0.3, "dialog": 0.3, "drawer": 0.3, "spinner": 0.3,
    "badge": 0.3, "card": 0.3, "panel": 0.3, "tab": 0.3,
}


def _load_custom_domains():
    """Load custom domain weights from .roam/domain-weights.json if present."""
    path = os.path.join(".roam", "domain-weights.json")
    if not os.path.isfile(path):
        return {}
    try:
        with open(path) as f:
            data = json.load(f)
        if isinstance(data, dict):
            return {str(k).lower(): float(v) for k, v in data.items()
                    if isinstance(v, (int, float))}
    except (json.JSONDecodeError, ValueError, OSError):
        pass
    return {}


# ---- Path-zone matching ----

_DEFAULT_PATH_ZONES = {
    "accounting": (("kiniseis/", "accounting/", "vat/", "ledger/", "journal/"), 10),
    "auth": (("auth/", "login/", "session/"), 8),
    "backup": (("backup/", "restore/"), 6),
    "data": (("migration", "seed"), 4),
}


def _load_custom_path_zones():
    """Load custom path zones from .roam/path-zones.json if present."""
    path = os.path.join(".roam", "path-zones.json")
    if not os.path.isfile(path):
        return {}
    try:
        with open(path) as f:
            data = json.load(f)
        if isinstance(data, dict):
            result = {}
            for zone_name, zone_cfg in data.items():
                if isinstance(zone_cfg, dict):
                    patterns = zone_cfg.get("patterns", [])
                    weight = zone_cfg.get("weight", 5)
                    if isinstance(patterns, list) and isinstance(weight, (int, float)):
                        result[str(zone_name)] = (tuple(str(p) for p in patterns), float(weight))
            return result
    except (json.JSONDecodeError, ValueError, OSError):
        pass
    return {}


def _match_path_zone(file_path, path_zones):
    """Return the highest path-zone weight for a file path."""
    p = file_path.replace("\\", "/").lower()
    best_weight = 0
    best_zone = ""
    for zone_name, (patterns, weight) in path_zones.items():
        for pat in patterns:
            if pat.lower() in p:
                if weight > best_weight:
                    best_weight = weight
                    best_zone = zone_name
                break
    return best_weight, best_zone


_UI_PATH_PATTERNS = ("components/", "views/", "pages/", "templates/",
                      "layouts/", "/ui/", "widgets/", "screens/")
_UI_EXTENSIONS = (".vue", ".svelte", ".jsx", ".tsx")


def _is_ui_file(file_path):
    """Check if a file is in a UI-related directory or has a UI extension."""
    p = file_path.replace("\\", "/").lower()
    return (any(pat in p for pat in _UI_PATH_PATTERNS)
            or any(p.endswith(ext) for ext in _UI_EXTENSIONS))


_SPLIT_RE = re.compile(r'[A-Z][a-z]+|[a-z]+|[A-Z]+(?=[A-Z][a-z]|\b)')


def _match_domain(name, domains):
    """Return the highest domain weight for a symbol name."""
    words = [w.lower() for w in _SPLIT_RE.findall(name)]
    best_weight = None
    matched = ""
    for w in words:
        if w in domains:
            wt = domains[w]
            if best_weight is None or wt > best_weight:
                best_weight = wt
                matched = w
    if best_weight is None:
        return 1, ""  # No domain match -- neutral
    return best_weight, matched


# ---- Callee-chain domain analysis ----

_CALLEE_DECAY = [1.0, 0.5, 0.25]  # distance decay per hop


def _callee_chain_domain(conn, symbol_id, domains, max_depth=3):
    """Walk callee graph up to max_depth hops, find strongest domain match.

    Returns (effective_weight, domain_match, via_symbol_name, chain_path).
    ``chain_path`` is a list of symbol names from source to matched callee.
    """
    best_weight = 0
    best_match = ""
    best_via = ""
    best_chain: list[str] = []

    # BFS through callees — track parent pointers for chain reconstruction
    visited = {symbol_id}
    parent: dict[int, int] = {}           # child_id -> parent_id
    id_to_name: dict[int, str] = {}
    frontier = [symbol_id]

    for depth in range(min(max_depth, len(_CALLEE_DECAY))):
        if not frontier:
            break
        placeholders = ",".join("?" for _ in frontier)
        callees = conn.execute(
            f"SELECT e.target_id, s.name FROM edges e "
            f"JOIN symbols s ON e.target_id = s.id "
            f"WHERE e.source_id IN ({placeholders}) "
            f"AND e.kind IN ('call', 'uses')",
            frontier,
        ).fetchall()
        next_frontier = []
        for callee_id, callee_name in callees:
            if callee_id in visited:
                continue
            visited.add(callee_id)
            id_to_name[callee_id] = callee_name
            # Record first parent only (BFS guarantees shortest path)
            if callee_id not in parent:
                # find which frontier node led here
                for fid in frontier:
                    parent[callee_id] = fid
                    break

            w, m = _match_domain(callee_name, domains)
            if w <= 1:
                next_frontier.append(callee_id)
                continue
            effective = w * _CALLEE_DECAY[depth]
            if effective > best_weight:
                best_weight = effective
                best_match = m
                best_via = callee_name
                # Reconstruct chain
                chain = [callee_name]
                cur = callee_id
                while cur in parent:
                    cur = parent[cur]
                    if cur in id_to_name:
                        chain.append(id_to_name[cur])
                chain.reverse()
                best_chain = chain
            next_frontier.append(callee_id)
        frontier = next_frontier

    return best_weight, best_match, best_via, best_chain


@click.command()
@click.option('-n', 'count', default=30, help='Number of symbols to show')
@click.option('--domain', 'domain_keywords', default=None,
              help='Comma-separated high-weight domain keywords (e.g. "payment,tax,ledger")')
@click.option('--explain', is_flag=True, help='Show full callee-chain reasoning per symbol')
@click.pass_context
def risk(ctx, count, domain_keywords, explain):
    """Show domain-weighted risk ranking of symbols.

    Combines static risk (fan-in + fan-out + betweenness) with domain
    criticality weights. Financial, auth, and data-integrity symbols
    rank higher than UI symbols.

    Domain matching uses three sources (highest wins):
    - Symbol name keyword matching
    - Callee-chain analysis (what the symbol calls, up to 3 hops)
    - File path zone matching (e.g. kiniseis/ -> accounting zone)
    """
    json_mode = ctx.obj.get('json') if ctx.obj else False
    ensure_index()

    # Build domain map: defaults -> .roam/domain-weights.json -> CLI overrides
    domains = dict(_DEFAULT_DOMAINS)
    custom = _load_custom_domains()
    if custom:
        domains.update(custom)
    if domain_keywords:
        for kw in domain_keywords.split(","):
            kw = kw.strip().lower()
            if kw:
                domains[kw] = 10  # User-specified keywords get max weight

    # Build path zones: defaults -> .roam/path-zones.json
    path_zones = dict(_DEFAULT_PATH_ZONES)
    custom_zones = _load_custom_path_zones()
    if custom_zones:
        path_zones.update(custom_zones)

    with open_db(readonly=True) as conn:
        rows = conn.execute("""
            SELECT s.id, s.name, s.kind, f.path as file_path, s.line_start,
                   gm.in_degree, gm.out_degree, gm.betweenness, gm.pagerank
            FROM graph_metrics gm
            JOIN symbols s ON gm.symbol_id = s.id
            JOIN files f ON s.file_id = f.id
            WHERE s.kind IN ('function', 'class', 'method', 'interface', 'struct')
            AND (gm.in_degree + gm.out_degree) > 0
        """).fetchall()

        if not rows:
            if json_mode:
                click.echo(to_json(json_envelope("risk",
                    summary={"items": 0, "max_risk": 0},
                    items=[],
                )))
            else:
                click.echo("No graph metrics available. Run `roam index` first.")
            return

        # Compute static risk (0-10 scale)
        max_total = max(((r["in_degree"] or 0) + (r["out_degree"] or 0)) for r in rows) or 1
        max_bw = max((r["betweenness"] or 0) for r in rows) or 1

        scored = []
        for r in rows:
            total_deg = (r["in_degree"] or 0) + (r["out_degree"] or 0)
            bw = r["betweenness"] or 0

            # Static risk: weighted combination of degree and betweenness
            static_risk = (
                (total_deg / max_total) * 5 +
                (bw / max_bw) * 5
            )

            # --- Three-source domain matching ---
            name_weight, name_match = _match_domain(r["name"], domains)
            zone_weight, zone_match = _match_path_zone(r["file_path"], path_zones)
            callee_weight, callee_match, callee_via, callee_chain = _callee_chain_domain(
                conn, r["id"], domains
            )

            # Pick the strongest source
            domain_weight = name_weight
            domain_match = name_match
            domain_source = "name"

            if callee_weight > domain_weight:
                domain_weight = callee_weight
                domain_match = callee_match
                domain_source = "callee"

            if zone_weight > domain_weight:
                domain_weight = zone_weight
                domain_match = zone_match
                domain_source = "zone"

            # File-path UI dampening: if symbol is in a UI file and matched
            # a non-UI domain keyword (e.g. "restore" in a component),
            # halve the domain weight to avoid false positives
            ui_dampened = False
            if domain_weight > 1 and domain_source != "zone" and _is_ui_file(r["file_path"]):
                domain_weight = max(1, domain_weight * 0.5)
                ui_dampened = True

            adjusted_risk = static_risk * domain_weight

            # Build domain description string for text output
            if domain_source == "name" and domain_weight > 1:
                domain_desc = f"x{domain_weight:.4g} ({domain_match})"
            elif domain_source == "callee" and domain_weight > 1:
                domain_desc = f"x{domain_weight:.4g} ({domain_match}) via {callee_via}"
            elif domain_source == "zone" and domain_weight > 1:
                domain_desc = f"x{domain_weight:.4g} [{domain_match} zone]"
            else:
                domain_desc = ""

            scored.append({
                "name": r["name"],
                "kind": r["kind"],
                "file_path": r["file_path"],
                "line_start": r["line_start"],
                "static_risk": round(static_risk, 1),
                "domain_weight": domain_weight,
                "domain_match": domain_match,
                "domain_source": domain_source,
                "domain_desc": domain_desc,
                "ui_dampened": ui_dampened,
                "adjusted_risk": round(adjusted_risk, 1),
                "in_degree": r["in_degree"] or 0,
                "out_degree": r["out_degree"] or 0,
                "betweenness": round(bw, 1),
                "callee_chain": callee_chain,
                "callee_via": callee_via,
                "name_weight": name_weight,
                "name_match": name_match,
                "zone_weight": zone_weight,
                "zone_match": zone_match,
                "callee_weight": callee_weight,
                "callee_match": callee_match,
            })

        scored.sort(key=lambda x: -x["adjusted_risk"])
        scored = scored[:count]

        if json_mode:
            items = []
            for s in scored:
                item = {
                    "name": s["name"],
                    "kind": s["kind"],
                    "static_risk": s["static_risk"],
                    "domain_weight": s["domain_weight"],
                    "domain_match": s["domain_match"],
                    "domain_source": s["domain_source"],
                    "ui_dampened": s["ui_dampened"],
                    "adjusted_risk": s["adjusted_risk"],
                    "location": loc(s["file_path"], s["line_start"]),
                }
                if explain:
                    item["in_degree"] = s["in_degree"]
                    item["out_degree"] = s["out_degree"]
                    item["betweenness"] = s["betweenness"]
                    item["chain"] = s["callee_chain"]
                    item["domain_sources"] = {}
                    if s["name_weight"] > 1:
                        item["domain_sources"]["name"] = {
                            "keyword": s["name_match"], "weight": s["name_weight"],
                        }
                    if s["callee_weight"] > 1:
                        item["domain_sources"]["callee"] = {
                            "keyword": s["callee_match"],
                            "weight": s["callee_weight"],
                            "via": s["callee_via"],
                        }
                    if s["zone_weight"] > 1:
                        item["domain_sources"]["zone"] = {
                            "pattern": s["zone_match"], "weight": s["zone_weight"],
                        }
                items.append(item)
            click.echo(to_json(json_envelope("risk",
                summary={"count": len(items), "explain": explain},
                items=items,
            )))
            return

        # --- Text output ---
        click.echo("=== Domain-Weighted Risk ===")
        if domain_keywords:
            click.echo(f"  Custom domain keywords: {domain_keywords}")
        click.echo()

        if explain:
            # Detailed per-symbol reasoning
            for s in scored:
                flag = ""
                if s["adjusted_risk"] >= 30:
                    flag = "CRITICAL"
                elif s["adjusted_risk"] >= 15:
                    flag = "HIGH"
                elif s["adjusted_risk"] >= 5:
                    flag = "MEDIUM"

                click.echo(f"{flag:8s}  {s['name']}  (adjusted: {s['adjusted_risk']:.1f})")
                click.echo(f"  Static risk: {s['static_risk']:.1f} "
                           f"(fan-in: {s['in_degree']}, fan-out: {s['out_degree']}, "
                           f"betweenness: {s['betweenness']:.0f})")
                if s["name_weight"] > 1:
                    click.echo(f"  Name match: x{s['name_weight']:.4g} ({s['name_match']})")
                if s["callee_chain"]:
                    chain_str = " -> ".join(s["callee_chain"])
                    click.echo(f"  Callee chain: {chain_str} "
                               f"(matched: {s['callee_match']}, x{s['callee_weight']:.4g})")
                if s["zone_weight"] > 1:
                    click.echo(f"  Path zone: {s['zone_match']} (x{s['zone_weight']:.4g})")
                if s["ui_dampened"]:
                    click.echo(f"  UI dampened: yes")
                click.echo(f"  Location: {loc(s['file_path'], s['line_start'])}")
                click.echo()
        else:
            table_rows = []
            for s in scored:
                flag = ""
                if s["adjusted_risk"] >= 30:
                    flag = "CRITICAL"
                elif s["adjusted_risk"] >= 15:
                    flag = "HIGH"
                elif s["adjusted_risk"] >= 5:
                    flag = "MEDIUM"

                notes = s["domain_desc"]
                if s["ui_dampened"]:
                    notes += " [UI dampened]" if notes else "[UI dampened]"

                table_rows.append([
                    abbrev_kind(s["kind"]),
                    s["name"],
                    f"{s['static_risk']:.1f}",
                    notes,
                    f"{s['adjusted_risk']:.1f}",
                    flag,
                    loc(s["file_path"], s["line_start"]),
                ])

            click.echo(format_table(
                ["Kind", "Name", "Static", "Domain", "Adjusted", "Level", "Location"],
                table_rows,
            ))
