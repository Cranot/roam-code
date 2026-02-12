"""Show fan-in/fan-out metrics for symbols or files."""

import click

from roam.db.connection import open_db
from roam.output.formatter import abbrev_kind, loc, format_table, to_json, json_envelope
from roam.commands.resolve import ensure_index


_FRAMEWORK_NAMES = frozenset({
    # Python dunders
    "__init__", "__str__", "__repr__", "__new__", "__del__", "__enter__",
    "__exit__", "__getattr__", "__setattr__", "__getitem__", "__setitem__",
    "__len__", "__iter__", "__next__", "__call__", "__hash__", "__eq__",
    # JS/TS generic
    "constructor", "render", "toString", "valueOf", "toJSON",
    "setUp", "tearDown", "setup", "teardown",
    "configure", "register", "bootstrap", "main",
    # Vue
    "computed", "ref", "reactive", "watch", "watchEffect",
    "defineProps", "defineEmits", "defineExpose", "defineSlots",
    "onMounted", "onUnmounted", "onBeforeMount", "onBeforeUnmount",
    "onActivated", "onDeactivated", "onUpdated", "onBeforeUpdate",
    "provide", "inject", "toRef", "toRefs", "unref", "isRef",
    "shallowRef", "shallowReactive", "readonly", "shallowReadonly",
    "nextTick", "h", "resolveComponent", "emit", "emits", "props",
    # React
    "useState", "useEffect", "useCallback", "useMemo", "useRef",
    "useContext", "useReducer", "useLayoutEffect",
    # Angular
    "ngOnInit", "ngOnDestroy", "ngOnChanges", "ngAfterViewInit",
    # Go
    "init", "New", "Close", "String", "Error",
    # Rust
    "new", "default", "fmt", "from", "into", "drop",
})


@click.command()
@click.argument('mode', default='symbol', type=click.Choice(['symbol', 'file']))
@click.option('-n', 'count', default=20, help='Number of items to show')
@click.option('--no-framework', is_flag=True, help='Filter out framework/boilerplate symbols')
@click.pass_context
def fan(ctx, mode, count, no_framework):
    """Show fan-in/fan-out: most connected symbols or files."""
    json_mode = ctx.obj.get('json') if ctx.obj else False
    ensure_index()

    with open_db(readonly=True) as conn:
        if mode == 'symbol':
            rows = conn.execute("""
                SELECT s.name, s.kind, f.path as file_path, s.line_start,
                       gm.in_degree, gm.out_degree,
                       (gm.in_degree + gm.out_degree) as total,
                       gm.betweenness, gm.pagerank
                FROM graph_metrics gm
                JOIN symbols s ON gm.symbol_id = s.id
                JOIN files f ON s.file_id = f.id
                WHERE gm.in_degree + gm.out_degree > 0
                ORDER BY total DESC
                LIMIT ?
            """, (count,)).fetchall()

            if no_framework:
                rows = [r for r in rows if r["name"] not in _FRAMEWORK_NAMES]

            if not rows:
                if json_mode:
                    click.echo(to_json(json_envelope("fan",
                        summary={"mode": mode, "items": 0},
                        mode=mode, items=[],
                    )))
                else:
                    click.echo("No graph metrics available. Run `roam index` first.")
                return

            if json_mode:
                click.echo(to_json(json_envelope("fan",
                    summary={"mode": mode, "items": len(rows)},
                    mode=mode,
                    items=[
                        {
                            "name": r["name"], "kind": r["kind"],
                            "fan_in": r["in_degree"] or 0, "fan_out": r["out_degree"] or 0,
                            "total": (r["in_degree"] or 0) + (r["out_degree"] or 0),
                            "betweenness": round(r["betweenness"] or 0, 1),
                            "pagerank": round(r["pagerank"] or 0, 4),
                            "location": loc(r["file_path"], r["line_start"]),
                        }
                        for r in rows
                    ],
                )))
                return

            table_rows = []
            for r in rows:
                in_deg = r["in_degree"] or 0
                out_deg = r["out_degree"] or 0
                total = in_deg + out_deg
                flag = ""
                if in_deg > 10 and out_deg > 10:
                    flag = "HIGH-RISK"
                elif in_deg > 10:
                    flag = "hub"
                elif out_deg > 10:
                    flag = "spreader"

                bw = r["betweenness"] or 0
                bw_str = f"{bw:.0f}" if bw >= 10 else (f"{bw:.1f}" if bw > 0.5 else "")
                pr = r["pagerank"] or 0
                pr_str = f"{pr:.4f}" if pr > 0 else ""

                table_rows.append([
                    abbrev_kind(r["kind"]),
                    r["name"],
                    str(in_deg),
                    str(out_deg),
                    str(total),
                    bw_str,
                    pr_str,
                    flag,
                    loc(r["file_path"], r["line_start"]),
                ])

            click.echo("=== Fan-in/Fan-out (symbol level) ===")
            click.echo(format_table(
                ["kind", "name", "fan-in", "fan-out", "total", "btwn", "PR", "flag", "location"],
                table_rows,
            ))

        else:  # file mode
            rows = conn.execute("""
                SELECT f.path,
                       COUNT(DISTINCT CASE WHEN fe_in.target_file_id = f.id THEN fe_in.source_file_id END) as fan_in,
                       COUNT(DISTINCT CASE WHEN fe_out.source_file_id = f.id THEN fe_out.target_file_id END) as fan_out
                FROM files f
                LEFT JOIN file_edges fe_in ON fe_in.target_file_id = f.id
                LEFT JOIN file_edges fe_out ON fe_out.source_file_id = f.id
                GROUP BY f.id
                HAVING fan_in + fan_out > 0
                ORDER BY fan_in + fan_out DESC
                LIMIT ?
            """, (count,)).fetchall()

            if not rows:
                if json_mode:
                    click.echo(to_json(json_envelope("fan",
                        summary={"mode": mode, "items": 0},
                        mode=mode, items=[],
                    )))
                else:
                    click.echo("No file edges available. Run `roam index` first.")
                return

            if json_mode:
                click.echo(to_json(json_envelope("fan",
                    summary={"mode": mode, "items": len(rows)},
                    mode=mode,
                    items=[
                        {
                            "path": r["path"],
                            "fan_in": r["fan_in"], "fan_out": r["fan_out"],
                            "total": r["fan_in"] + r["fan_out"],
                        }
                        for r in rows
                    ],
                )))
                return

            table_rows = []
            for r in rows:
                total = r["fan_in"] + r["fan_out"]
                flag = ""
                if r["fan_in"] > 5 and r["fan_out"] > 5:
                    flag = "HIGH-RISK"
                elif r["fan_in"] > 5:
                    flag = "hub"
                elif r["fan_out"] > 5:
                    flag = "spreader"

                table_rows.append([
                    r["path"],
                    str(r["fan_in"]),
                    str(r["fan_out"]),
                    str(total),
                    flag,
                ])

            click.echo("=== Fan-in/Fan-out (file level) ===")
            click.echo(format_table(
                ["path", "fan-in", "fan-out", "total", "flag"],
                table_rows,
            ))
