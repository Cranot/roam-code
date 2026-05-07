"""``roam recommend <symbol>`` — surface symbols related to a given one.

three signal sources combined:

  * call-graph neighbours (inbound + outbound, 1 hop)
  * git co-change (other symbols whose files changed in the same commits)
  * persisted clone siblings (when ``roam clones --persist`` was run)

Each candidate gets a score that's the sum of normalised contributions
from each signal. Lets agents see "what else should I look at when
touching this symbol".
"""

from __future__ import annotations

import click

from roam.commands.resolve import ensure_index
from roam.db.connection import open_db
from roam.output.formatter import json_envelope, to_json


def _name_for(conn, sym_id: int) -> tuple[str, str, str]:
    row = conn.execute(
        "SELECT s.name, s.kind, f.path FROM symbols s JOIN files f ON f.id = s.file_id WHERE s.id = ?",
        (sym_id,),
    ).fetchone()
    if row is None:
        return ("?", "?", "?")
    return (row["name"], row["kind"], row["path"])


def _candidate_init() -> dict:
    return {"call": 0.0, "cochange": 0.0, "clone": 0.0}


@click.command()
@click.argument("symbol")
@click.option("--limit", type=int, default=10, show_default=True, help="Top N recommendations.")
@click.pass_context
def recommend(ctx, symbol, limit) -> None:
    """Recommend related symbols using call-graph, co-change, and clone signals."""
    json_mode = ctx.obj.get("json") if ctx.obj else False
    ensure_index()
    with open_db(readonly=True) as conn:
        sym_row = conn.execute(
            "SELECT s.id, s.file_id, f.path FROM symbols s "
            "JOIN files f ON f.id = s.file_id "
            "WHERE s.name = ? OR s.qualified_name = ? OR s.qualified_name LIKE ? "
            "LIMIT 1",
            (symbol, symbol, f"%.{symbol}"),
        ).fetchone()
        if sym_row is None:
            verdict = f"no symbol named '{symbol}' in index"
            if json_mode:
                click.echo(
                    to_json(
                        json_envelope(
                            "recommend",
                            summary={"verdict": verdict, "count": 0},
                            recommendations=[],
                        )
                    )
                )
            else:
                click.echo(f"VERDICT: {verdict}")
            return
        sym_id = sym_row["id"]
        file_id = sym_row["file_id"]

        scores: dict[int, dict] = {}

        # 1. Call-graph neighbours (1 hop).
        for r in conn.execute(
            "SELECT target_id AS id FROM edges WHERE source_id = ? "
            "UNION SELECT source_id AS id FROM edges WHERE target_id = ?",
            (sym_id, sym_id),
        ).fetchall():
            other = r["id"]
            if other == sym_id:
                continue
            entry = scores.setdefault(other, _candidate_init())
            entry["call"] += 1.0

        # 2. Git co-change at file level (commits that touched our file).
        try:
            commit_rows = conn.execute(
                "SELECT DISTINCT commit_id FROM git_file_changes WHERE file_id = ? LIMIT 200",
                (file_id,),
            ).fetchall()
        except Exception:
            commit_rows = []
        commit_ids = [r["commit_id"] for r in commit_rows]
        if commit_ids:
            from roam.db.connection import batched_in

            cofile_rows = batched_in(
                conn,
                "SELECT DISTINCT file_id FROM git_file_changes WHERE commit_id IN ({ph}) AND file_id != ?",
                commit_ids,
                post=(file_id,),
            )
            cofile_ids = [r["file_id"] for r in cofile_rows]
            if cofile_ids:
                # Symbols in those co-changed files.
                cosym_rows = batched_in(
                    conn,
                    "SELECT id FROM symbols WHERE file_id IN ({ph})",
                    cofile_ids,
                )
                for r in cosym_rows:
                    other = r["id"]
                    if other == sym_id:
                        continue
                    entry = scores.setdefault(other, _candidate_init())
                    entry["cochange"] += 1.0

        # 3. Persisted clone siblings.
        try:
            for r in conn.execute(
                "SELECT b_symbol_id AS id FROM clone_pairs WHERE a_symbol_id = ? "
                "UNION SELECT a_symbol_id AS id FROM clone_pairs WHERE b_symbol_id = ?",
                (sym_id, sym_id),
            ).fetchall():
                other = r["id"]
                entry = scores.setdefault(other, _candidate_init())
                entry["clone"] += 1.0
        except Exception:
            pass

    # Normalise & combine.
    if not scores:
        verdict = f"no related symbols found for '{symbol}'"
        if json_mode:
            click.echo(
                to_json(
                    json_envelope(
                        "recommend",
                        summary={"verdict": verdict, "count": 0},
                        recommendations=[],
                    )
                )
            )
        else:
            click.echo(f"VERDICT: {verdict}")
        return

    max_call = max((s["call"] for s in scores.values()), default=1.0) or 1.0
    max_co = max((s["cochange"] for s in scores.values()), default=1.0) or 1.0
    max_cl = max((s["clone"] for s in scores.values()), default=1.0) or 1.0

    enriched = []
    with open_db(readonly=True) as conn:
        for sid, contrib in scores.items():
            score = (
                0.5 * (contrib["call"] / max_call)
                + 0.3 * (contrib["cochange"] / max_co)
                + 0.2 * (contrib["clone"] / max_cl)
            )
            name, kind, path = _name_for(conn, sid)
            enriched.append(
                {
                    "id": sid,
                    "name": name,
                    "kind": kind,
                    "file": path,
                    "call_overlap": int(contrib["call"]),
                    "cochange_overlap": int(contrib["cochange"]),
                    "clone_overlap": int(contrib["clone"]),
                    "score": round(score, 4),
                }
            )
    enriched.sort(key=lambda x: -x["score"])
    enriched = enriched[: max(1, int(limit))]

    verdict = f"{len(enriched)} related symbol(s) for '{symbol}'"

    if json_mode:
        click.echo(
            to_json(
                json_envelope(
                    "recommend",
                    summary={"verdict": verdict, "count": len(enriched)},
                    recommendations=enriched,
                )
            )
        )
        return

    click.echo(f"VERDICT: {verdict}")
    click.echo()
    click.echo(f"{'Score':>6}  {'call':>4}  {'co-ch':>5}  {'clone':>5}  Symbol (file)")
    click.echo(f"{'-' * 6}  {'-' * 4}  {'-' * 5}  {'-' * 5}  {'-' * 30}")
    for r in enriched:
        click.echo(
            f"{r['score']:>6.3f}  {r['call_overlap']:>4}  {r['cochange_overlap']:>5}  "
            f"{r['clone_overlap']:>5}  {r['name']} ({r['file']})"
        )
