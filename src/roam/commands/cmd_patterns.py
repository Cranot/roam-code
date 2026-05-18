"""Detect common architectural patterns in the codebase symbol graph.

Output formats: text (default), ``--json``. SARIF is deliberately NOT
emitted because patterns outputs are invocation-scoped design-pattern
catalogs (Factory / Singleton / Observer / Strategy / etc. instances
detected in the graph) — not per-location code violations. The output
describes informational design-pattern occurrences rather than defects
at source coordinates; identifying a Factory or Strategy instance is
not a finding to remediate. SARIF audiences scan for per-finding
rule_id + region rows. See action.yml _SUPPORTED_SARIF allowlist +
W1175-RESEARCH propagation plan + W1224-audit memo.
"""

from __future__ import annotations

import os
from collections import defaultdict

import click

from roam.capability import roam_capability
from roam.commands.resolve import ensure_index
from roam.db.connection import open_db
from roam.db.edge_kinds import CALL_EDGE_KINDS, inheritance_in_clause
from roam.output.formatter import abbrev_kind, json_envelope, loc, to_json


def _is_test_or_detector_path(file_path):
    """Return True if file is test code or a pattern detector itself."""
    p = file_path.replace("\\", "/").lower()
    base = os.path.basename(p)
    if base.startswith("test_") or base.endswith("_test.py"):
        return True
    if "tests/" in p or "test/" in p or "__tests__/" in p or "spec/" in p:
        return True
    # Exclude the patterns detector itself to avoid self-referential matches
    if base == "cmd_patterns.py":
        return True
    return False


# ---------------------------------------------------------------------------
# Pattern detection helpers
# ---------------------------------------------------------------------------


def _detect_singleton(conn):
    """Detect Singleton pattern: class with getInstance/get_instance/shared/default
    plus a class-level self-reference.
    """
    # Find methods named like singleton accessors that belong to a class
    accessor_names = (
        "getInstance",
        "get_instance",
        "shared",
        "default",
        "instance",
        "sharedInstance",
        "shared_instance",
    )
    ph = ",".join("?" for _ in accessor_names)
    rows = conn.execute(
        f"SELECT s.id, s.name, s.kind, s.parent_id, s.qualified_name, "
        f"f.path as file_path, s.line_start "
        f"FROM symbols s JOIN files f ON s.file_id = f.id "
        f"WHERE s.name IN ({ph}) "
        f"AND s.kind IN ('method', 'function', 'property') "
        f"AND s.parent_id IS NOT NULL",
        accessor_names,
    ).fetchall()

    results = []
    seen_parents = set()
    for r in rows:
        parent_id = r["parent_id"]
        if parent_id in seen_parents:
            continue

        # Look up the parent class
        parent = conn.execute(
            "SELECT s.id, s.name, s.qualified_name, s.kind, f.path as file_path, "
            "s.line_start "
            "FROM symbols s JOIN files f ON s.file_id = f.id "
            "WHERE s.id = ? AND s.kind = 'class'",
            (parent_id,),
        ).fetchone()
        if not parent:
            continue

        # Optionally check for self-type reference (edge from class to itself,
        # or a property/field of the same type)
        self_ref = conn.execute(
            "SELECT 1 FROM edges e "
            "JOIN symbols src ON e.source_id = src.id "
            "WHERE src.parent_id = ? AND e.target_id = ? LIMIT 1",
            (parent_id, parent_id),
        ).fetchone()

        seen_parents.add(parent_id)
        results.append(
            {
                "pattern": "singleton",
                "name": parent["qualified_name"] or parent["name"],
                "kind": parent["kind"],
                "location": loc(parent["file_path"], parent["line_start"]),
                "accessor": r["name"],
                "has_self_ref": bool(self_ref),
                "confidence": "high" if self_ref else "medium",
            }
        )

    return results


def _detect_factory(conn, *, strict=False):
    """Detect Factory pattern with subtype split.

    Two outputs in one pass:
    - ``true_factory`` — emits when the symbol has outgoing edges to a
      class/constructor target. The dogfood signal: ``createLogger``,
      ``ColumnBuilder``, ``createResourceQuery``.
    - ``builder_helper`` — name matches but no class instantiation
      detected. ``buildTsv`` / ``buildHeaderSearchParams`` /
      ``buildKinisiDocumentDisplay`` land here. Useful to know about,
      but not architectural factories — separated so they don't
      drown the real factories in pattern output.

    ``strict=True`` drops builder helpers entirely (see ``--strict-factory``).
    """
    # Name-pattern based detection
    rows = conn.execute(
        "SELECT s.id, s.name, s.qualified_name, s.kind, f.path as file_path, "
        "s.line_start "
        "FROM symbols s JOIN files f ON s.file_id = f.id "
        "WHERE ("
        "  s.name LIKE 'create\\_%' ESCAPE '\\' OR s.name LIKE 'Create%' "
        "  OR s.name LIKE 'make\\_%' ESCAPE '\\' OR s.name LIKE 'Make%' "
        "  OR s.name LIKE 'build\\_%' ESCAPE '\\' OR s.name LIKE 'Build%' "
        "  OR s.name LIKE '%Factory' OR s.name LIKE '%factory' "
        "  OR s.name LIKE '%Builder' OR s.name LIKE '%builder' "
        ") "
        "AND s.kind IN ('function', 'method', 'class')"
    ).fetchall()

    results = []
    for r in rows:
        if _is_test_or_detector_path(r["file_path"]):
            continue
        # Check for outgoing edges to constructors or class instantiations
        targets = conn.execute(
            "SELECT DISTINCT t.name, t.kind FROM edges e "
            "JOIN symbols t ON e.target_id = t.id "
            "WHERE e.source_id = ? "
            "AND t.kind IN ('class', 'constructor', 'function')",
            (r["id"],),
        ).fetchall()

        creates_classes = [t["name"] for t in targets if t["kind"] in ("class", "constructor")]
        is_true_factory = bool(creates_classes)
        if strict and not is_true_factory:
            continue

        results.append(
            {
                "pattern": "factory",
                "subtype": "true_factory" if is_true_factory else "builder_helper",
                "name": r["qualified_name"] or r["name"],
                "kind": r["kind"],
                "location": loc(r["file_path"], r["line_start"]),
                "creates": creates_classes[:5],
                "confidence": "high" if is_true_factory else "low",
            }
        )

    # Surface true factories first so they don't get buried.
    results.sort(key=lambda x: 0 if x["subtype"] == "true_factory" else 1)
    return results


def _detect_observer(conn):
    """Detect Observer/Pub-Sub pattern: classes/methods with event emitter names
    (on_*, addEventListener, subscribe, emit, notify, publish).
    """
    emitter_names = (
        "emit",
        "notify",
        "publish",
        "dispatch",
        "fire",
        "trigger",
        "broadcast",
    )
    listener_names = (
        "addEventListener",
        "subscribe",
        "on",
        "addListener",
        "observe",
        "watch",
        "listen",
    )
    remover_names = (
        "removeEventListener",
        "unsubscribe",
        "off",
        "removeListener",
        "unobserve",
        "unwatch",
    )

    all_event_names = emitter_names + listener_names + remover_names
    ph = ",".join("?" for _ in all_event_names)

    # Also match on_* and handle_* patterns
    rows = conn.execute(
        f"SELECT s.id, s.name, s.qualified_name, s.kind, s.parent_id, "
        f"f.path as file_path, s.line_start "
        f"FROM symbols s JOIN files f ON s.file_id = f.id "
        f"WHERE (s.name IN ({ph}) "
        f"  OR s.name LIKE 'on\\_%' ESCAPE '\\' "
        f"  OR s.name LIKE 'handle\\_%' ESCAPE '\\' "
        f") "
        f"AND s.kind IN ('method', 'function')",
        all_event_names,
    ).fetchall()

    # Group by parent class
    by_parent = defaultdict(list)
    standalone = []
    for r in rows:
        if r["parent_id"]:
            by_parent[r["parent_id"]].append(r)
        else:
            standalone.append(r)

    results = []

    for parent_id, methods in by_parent.items():
        emitters = [m for m in methods if m["name"] in emitter_names]
        listeners = [
            m
            for m in methods
            if m["name"] in listener_names or m["name"].startswith("on_") or m["name"].startswith("handle_")
        ]

        if not emitters and not listeners:
            continue

        # Look up parent class
        parent = conn.execute(
            "SELECT s.name, s.qualified_name, s.kind, f.path as file_path, "
            "s.line_start "
            "FROM symbols s JOIN files f ON s.file_id = f.id "
            "WHERE s.id = ?",
            (parent_id,),
        ).fetchone()
        if not parent:
            continue

        # Count how many other symbols call the emitter methods
        listener_count = 0
        for em in emitters:
            cnt = conn.execute(
                "SELECT COUNT(DISTINCT e.source_id) FROM edges e WHERE e.target_id = ?",
                (em["id"],),
            ).fetchone()[0]
            listener_count += cnt

        results.append(
            {
                "pattern": "observer",
                "name": parent["qualified_name"] or parent["name"],
                "kind": parent["kind"],
                "location": loc(parent["file_path"], parent["line_start"]),
                "emitters": [m["name"] for m in emitters],
                "listeners": [m["name"] for m in listeners[:5]],
                "subscriber_count": listener_count,
                "confidence": "high" if emitters and listeners else "medium",
            }
        )

    # Standalone emitter functions (e.g. EventBus-style modules)
    for r in standalone:
        if r["name"] in emitter_names:
            listener_count = conn.execute(
                "SELECT COUNT(DISTINCT e.source_id) FROM edges e WHERE e.target_id = ?",
                (r["id"],),
            ).fetchone()[0]
            results.append(
                {
                    "pattern": "observer",
                    "name": r["qualified_name"] or r["name"],
                    "kind": r["kind"],
                    "location": loc(r["file_path"], r["line_start"]),
                    "emitters": [r["name"]],
                    "listeners": [],
                    "subscriber_count": listener_count,
                    "confidence": "medium",
                }
            )

    return results


def _detect_repository(conn):
    """Detect Repository/DAO pattern: classes named *Repository, *Repo, *DAO,
    *Store with data-access methods (find*, get*, save*, delete*).
    """
    rows = conn.execute(
        "SELECT s.id, s.name, s.qualified_name, s.kind, f.path as file_path, "
        "s.line_start "
        "FROM symbols s JOIN files f ON s.file_id = f.id "
        "WHERE ("
        "  s.name LIKE '%Repository' OR s.name LIKE '%repository' "
        "  OR s.name LIKE '%Repo' "
        "  OR s.name LIKE '%DAO' OR s.name LIKE '%Dao' "
        "  OR s.name LIKE '%Store' OR s.name LIKE '%store' "
        "  OR s.name LIKE '%Gateway' "
        ") "
        "AND s.kind = 'class'"
    ).fetchall()

    results = []
    for r in rows:
        # Check for data-access methods as children
        methods = conn.execute(
            "SELECT s.name, s.kind FROM symbols s "
            "WHERE s.parent_id = ? "
            "AND s.kind IN ('method', 'function') "
            "AND ("
            "  s.name LIKE 'find%' OR s.name LIKE 'get%' "
            "  OR s.name LIKE 'save%' OR s.name LIKE 'delete%' "
            "  OR s.name LIKE 'update%' OR s.name LIKE 'create%' "
            "  OR s.name LIKE 'remove%' OR s.name LIKE 'fetch%' "
            "  OR s.name LIKE 'list%' OR s.name LIKE 'query%' "
            "  OR s.name LIKE 'insert%' OR s.name LIKE 'upsert%' "
            ")",
            (r["id"],),
        ).fetchall()

        dao_methods = [m["name"] for m in methods]

        results.append(
            {
                "pattern": "repository",
                "name": r["qualified_name"] or r["name"],
                "kind": r["kind"],
                "location": loc(r["file_path"], r["line_start"]),
                "data_methods": dao_methods[:8],
                "confidence": "high" if dao_methods else "medium",
            }
        )

    return results


def _detect_middleware(conn):
    """Detect Middleware/Pipeline pattern: linear call chains (A->B->C)
    where symbols share naming conventions or consistent call structure.
    """
    # Look for classes/functions named *Middleware, *Handler, *Interceptor,
    # *Filter, *Pipe, *Pipeline
    rows = conn.execute(
        "SELECT s.id, s.name, s.qualified_name, s.kind, f.path as file_path, "
        "s.line_start "
        "FROM symbols s JOIN files f ON s.file_id = f.id "
        "WHERE ("
        "  s.name LIKE '%Middleware' OR s.name LIKE '%middleware' "
        "  OR s.name LIKE '%Interceptor' OR s.name LIKE '%interceptor' "
        "  OR s.name LIKE '%Pipe' OR s.name LIKE '%Pipeline' OR s.name LIKE '%pipeline' "
        ") "
        "AND s.kind IN ('class', 'function', 'method')"
    ).fetchall()

    if not rows:
        return []

    results = []
    seen = set()

    for r in rows:
        if r["id"] in seen:
            continue
        if _is_test_or_detector_path(r["file_path"]):
            continue
        seen.add(r["id"])

        # Look for call-chain: what does this symbol call, and what calls it?
        # W512: edge-kind vocabulary lives in roam.db.edge_kinds. Pure call
        # edges only — middleware chains are call-graph relationships,
        # not reference relationships.
        call_kind_ph = ", ".join("?" for _ in CALL_EDGE_KINDS)
        callees = conn.execute(
            f"SELECT DISTINCT t.id, t.name, t.kind FROM edges e "
            f"JOIN symbols t ON e.target_id = t.id "
            f"WHERE e.source_id = ? AND e.kind IN ({call_kind_ph})",
            (r["id"], *CALL_EDGE_KINDS),
        ).fetchall()

        callers = conn.execute(
            f"SELECT DISTINCT t.id, t.name, t.kind FROM edges e "
            f"JOIN symbols t ON e.source_id = t.id "
            f"WHERE e.target_id = ? AND e.kind IN ({call_kind_ph})",
            (r["id"], *CALL_EDGE_KINDS),
        ).fetchall()

        # Check if part of a chain (has both callers and callees, or
        # multiple middleware siblings in same file)
        chain_members = []
        for c in callees:
            if any(pat in c["name"].lower() for pat in ("middleware", "handler", "interceptor", "filter", "pipe")):
                chain_members.append(c["name"])

        results.append(
            {
                "pattern": "middleware",
                "name": r["qualified_name"] or r["name"],
                "kind": r["kind"],
                "location": loc(r["file_path"], r["line_start"]),
                "chain_next": chain_members[:5],
                "callers": len(callers),
                "callees": len(callees),
                "confidence": "high" if chain_members else "medium",
            }
        )

    return results


def _detect_strategy(conn):
    """Detect Strategy pattern: multiple classes inheriting from the same parent
    with similar method signatures.
    """
    # Find inheritance edges, group by target (parent class).
    # W543-followup: source the inheritance-kind tuple from the
    # canonical helper so a future widening (e.g. ``uses_trait``,
    # already in :data:`INHERITANCE_EDGE_KINDS`) reaches every reader.
    rows = conn.execute(
        "SELECT e.source_id, e.target_id, "
        "src.name as child_name, src.qualified_name as child_qname, "
        "src.kind as child_kind, "
        "tgt.name as parent_name, tgt.qualified_name as parent_qname, "
        "tgt.kind as parent_kind, "
        "sf.path as child_path, src.line_start as child_line, "
        "tf.path as parent_path, tgt.line_start as parent_line "
        "FROM edges e "
        "JOIN symbols src ON e.source_id = src.id "
        "JOIN symbols tgt ON e.target_id = tgt.id "
        "JOIN files sf ON src.file_id = sf.id "
        "JOIN files tf ON tgt.file_id = tf.id "
        f"WHERE {inheritance_in_clause('e.kind')} "
        "AND tgt.kind IN ('class', 'interface', 'trait')"
    ).fetchall()

    # Group by parent
    by_parent = defaultdict(list)
    parent_info = {}
    for r in rows:
        by_parent[r["target_id"]].append(r)
        parent_info[r["target_id"]] = {
            "name": r["parent_qname"] or r["parent_name"],
            "kind": r["parent_kind"],
            "location": loc(r["parent_path"], r["parent_line"]),
        }

    results = []
    for parent_id, children in by_parent.items():
        # Strategy requires 2+ implementations
        if len(children) < 2:
            continue

        pinfo = parent_info[parent_id]

        # Check if children have overlapping method names (shared interface)
        child_methods = {}
        for child in children:
            methods = conn.execute(
                "SELECT name FROM symbols WHERE parent_id = ? AND kind IN ('method', 'function')",
                (child["source_id"],),
            ).fetchall()
            child_methods[child["child_name"]] = {m["name"] for m in methods}

        # Find common methods across implementations
        if child_methods:
            all_method_sets = list(child_methods.values())
            common = set.intersection(*all_method_sets) if all_method_sets else set()
            # Filter out noise: constructors, dunders
            common = {m for m in common if not m.startswith("__") and m not in ("constructor", "__init__")}
        else:
            common = set()

        impl_names = [c["child_qname"] or c["child_name"] for c in children]

        results.append(
            {
                "pattern": "strategy",
                "name": pinfo["name"],
                "kind": pinfo["kind"],
                "location": pinfo["location"],
                "implementations": impl_names[:10],
                "implementation_count": len(children),
                "shared_methods": sorted(common)[:8],
                "confidence": "high" if len(common) >= 1 else "medium",
            }
        )

    return results


def _detect_decorator(conn):
    """Detect Decorator/Wrapper pattern: symbols with kind 'decorator',
    or functions that wrap other functions (higher-order functions).
    """
    # Direct decorator symbols
    rows = conn.execute(
        "SELECT s.id, s.name, s.qualified_name, s.kind, f.path as file_path, "
        "s.line_start "
        "FROM symbols s JOIN files f ON s.file_id = f.id "
        "WHERE s.kind = 'decorator'"
    ).fetchall()

    results = []
    seen = set()

    for r in rows:
        if r["name"] in seen:
            continue
        if _is_test_or_detector_path(r["file_path"]):
            continue
        seen.add(r["name"])

        # Count how many symbols this decorator is applied to
        usage_count = conn.execute(
            "SELECT COUNT(DISTINCT e.source_id) FROM edges e WHERE e.target_id = ?",
            (r["id"],),
        ).fetchone()[0]

        results.append(
            {
                "pattern": "decorator",
                "name": r["qualified_name"] or r["name"],
                "kind": r["kind"],
                "location": loc(r["file_path"], r["line_start"]),
                "usage_count": usage_count,
                "confidence": "high",
            }
        )

    # Also find functions named with_*, wrap_*, *Decorator, *Wrapper
    wrapper_rows = conn.execute(
        "SELECT s.id, s.name, s.qualified_name, s.kind, f.path as file_path, "
        "s.line_start "
        "FROM symbols s JOIN files f ON s.file_id = f.id "
        "WHERE ("
        "  s.name LIKE 'with\\_%' ESCAPE '\\' "
        "  OR s.name LIKE 'wrap\\_%' ESCAPE '\\' "
        "  OR s.name LIKE '%Decorator' "
        "  OR s.name LIKE '%Wrapper' OR s.name LIKE '%wrapper' "
        ") "
        "AND s.kind IN ('function', 'class', 'method') "
        "AND s.kind != 'decorator'"
    ).fetchall()

    for r in wrapper_rows:
        if r["name"] in seen:
            continue
        if _is_test_or_detector_path(r["file_path"]):
            continue
        seen.add(r["name"])

        usage_count = conn.execute(
            "SELECT COUNT(DISTINCT e.source_id) FROM edges e WHERE e.target_id = ?",
            (r["id"],),
        ).fetchone()[0]

        results.append(
            {
                "pattern": "decorator",
                "name": r["qualified_name"] or r["name"],
                "kind": r["kind"],
                "location": loc(r["file_path"], r["line_start"]),
                "usage_count": usage_count,
                "confidence": "medium",
            }
        )

    return results


# ---------------------------------------------------------------------------
# Pattern registry
# ---------------------------------------------------------------------------

_PATTERN_DETECTORS = {
    "singleton": ("Singleton", _detect_singleton),
    "factory": ("Factory", _detect_factory),
    "observer": ("Observer/PubSub", _detect_observer),
    "repository": ("Repository/DAO", _detect_repository),
    "middleware": ("Middleware/Pipeline", _detect_middleware),
    "strategy": ("Strategy", _detect_strategy),
    "decorator": ("Decorator/Wrapper", _detect_decorator),
}

_VALID_PATTERNS = list(_PATTERN_DETECTORS.keys())


def _format_pattern_detail(key: str, inst: dict) -> str:
    """Render the per-pattern detail string for an instance."""
    if key == "singleton":
        return f"  accessor: {inst['accessor']}"
    if key == "factory":
        subtype = inst.get("subtype", "")
        if subtype == "builder_helper":
            return "  [builder helper — returns POJO/primitive]"
        if inst.get("creates"):
            return f"  creates: {', '.join(inst['creates'][:3])}"
        return ""
    if key == "observer":
        parts = []
        if inst.get("emitters"):
            parts.append(f"emits: {', '.join(inst['emitters'][:3])}")
        if inst.get("subscriber_count"):
            parts.append(f"{inst['subscriber_count']} subscriber(s)")
        return f"  {', '.join(parts)}" if parts else ""
    if key == "repository":
        if inst.get("data_methods"):
            return f"  methods: {', '.join(inst['data_methods'][:4])}"
        return ""
    if key == "middleware":
        if inst.get("chain_next"):
            return f"  chains to: {', '.join(inst['chain_next'][:3])}"
        return ""
    if key == "strategy":
        n = inst.get("implementation_count", 0)
        detail = f"  {n} impl(s)"
        if inst.get("shared_methods"):
            detail += f", shared: {', '.join(inst['shared_methods'][:3])}"
        if inst.get("implementations"):
            detail += f"\n    impls: {', '.join(inst['implementations'][:5])}"
            if n > 5:
                detail += f" +{n - 5} more"
        return detail
    if key == "decorator":
        if inst.get("usage_count"):
            return f"  used {inst['usage_count']}x"
    return ""


def _print_pattern_instances(key: str, instances: list[dict]) -> None:
    for inst in instances:
        name = inst["name"]
        location = inst["location"]
        confidence = inst.get("confidence", "")
        conf_tag = f"  [{confidence}]" if confidence else ""
        detail = _format_pattern_detail(key, inst)
        click.echo(f"  {abbrev_kind(inst['kind'])}  {name:<40s}  {location}{conf_tag}{detail}")
    click.echo()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


@roam_capability(
    name="patterns",
    category="architecture",
    summary="Detect common architectural patterns in the codebase",
    maturity="stable",
    mcp_expose=True,
    mcp_preset=("core", "architecture"),
    side_effect=False,
    task_required=False,
    destructive=False,
    stale_sensitive=True,
    ai_safe=True,
    requires_index=True,
)
@click.command()
@click.option(
    "--strict-factory",
    "strict_factory",
    is_flag=True,
    default=False,
    help=(
        "Drop builder-helper functions (build_X / make_X returning POJOs). "
        "Default keeps them but tags them with subtype='builder_helper' so "
        "true factories aren't buried."
    ),
)
@click.option(
    "--pattern",
    "pattern_filter",
    default=None,
    type=click.Choice(_VALID_PATTERNS, case_sensitive=False),
    help="Filter to a specific pattern type",
)
@click.pass_context
def patterns(ctx, pattern_filter, strict_factory):
    """Detect common architectural patterns in the codebase.

    Unlike ``smells`` (which flags negative anti-patterns), this command
    discovers positive design patterns like Singleton, Factory, and Observer.

    Analyzes the symbol graph to find Singleton, Factory, Observer,
    Repository, Middleware, Strategy, and Decorator patterns.
    """
    json_mode = ctx.obj.get("json") if ctx.obj else False
    token_budget = ctx.obj.get("budget", 0) if ctx.obj else 0
    ensure_index()

    with open_db(readonly=True) as conn:
        all_results = {}
        total = 0

        detectors = _PATTERN_DETECTORS
        if pattern_filter:
            detectors = {pattern_filter: _PATTERN_DETECTORS[pattern_filter]}

        for key, (label, detect_fn) in detectors.items():
            if key == "factory":
                hits = detect_fn(conn, strict=strict_factory)
            else:
                hits = detect_fn(conn)
            if hits:
                all_results[key] = {"label": label, "instances": hits}
                total += len(hits)

        # --- Build verdict ---
        if all_results:
            top_parts = [f"{key}({len(data['instances'])})" for key, data in list(all_results.items())[:3]]
            verdict = f"{total} design patterns detected: {', '.join(top_parts)}"
        else:
            verdict = "no patterns detected"

        # --- JSON output ---
        if json_mode:
            patterns_json = {}
            for key, data in all_results.items():
                patterns_json[key] = {
                    "label": data["label"],
                    "count": len(data["instances"]),
                    "instances": data["instances"],
                }

            click.echo(
                to_json(
                    json_envelope(
                        "patterns",
                        summary={
                            "verdict": verdict,
                            "total_patterns": total,
                            "pattern_types": len(all_results),
                            "types_found": list(all_results.keys()),
                        },
                        budget=token_budget,
                        patterns=patterns_json,
                    )
                )
            )
            return

        # --- Text output ---
        if not all_results:
            click.echo(f"VERDICT: {verdict}\n")
            click.echo("No architectural patterns detected.")
            if pattern_filter:
                click.echo(f"  (filtered to: {pattern_filter})")
            return

        click.echo(f"VERDICT: {verdict}\n")
        click.echo(f"Architectural patterns detected ({total} total):\n")

        for key, data in all_results.items():
            label = data["label"]
            instances = data["instances"]
            # Round 4 #38: split factory output into "true factories"
            # and "builder helpers" sub-sections so real factories stop
            # being buried by buildXxx/makeXxx helpers.
            if key == "factory":
                true_factories = [i for i in instances if i.get("subtype") == "true_factory"]
                builder_helpers = [i for i in instances if i.get("subtype") == "builder_helper"]
                if true_factories:
                    click.echo(f"{label} (true factories — {len(true_factories)}):")
                    _print_pattern_instances(key, true_factories)
                if builder_helpers:
                    click.echo(f"{label} (builder helpers — {len(builder_helpers)}, returns POJO/primitive):")
                    _print_pattern_instances(key, builder_helpers)
                if not true_factories and not builder_helpers:
                    click.echo(f"{label} ({len(instances)} instance{'s' if len(instances) != 1 else ''}):")
                    _print_pattern_instances(key, instances)
                continue
            click.echo(f"{label} ({len(instances)} instance{'s' if len(instances) != 1 else ''}):")
            _print_pattern_instances(key, instances)
