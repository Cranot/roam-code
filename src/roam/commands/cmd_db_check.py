"""roam db-check — integrity sweep over the local index.

Looks for: orphan symbols (no file row), broken edges (referenced
symbol missing), duplicate file paths, missing FTS rows, invalid
line spans, corrupt or missing metrics. Returns a verdict and a
list of findings. Exit code 5 on any HIGH-severity finding so CI
can gate on it.
"""

from __future__ import annotations

import click

from roam.capability import roam_capability
from roam.commands.resolve import ensure_index
from roam.db.connection import open_db
from roam.output.formatter import json_envelope, to_json


def _check_orphan_symbols(conn) -> dict:
    cur = conn.execute("SELECT COUNT(*) FROM symbols s WHERE s.file_id NOT IN (SELECT id FROM files)")
    n = cur.fetchone()[0]
    return {"name": "orphan_symbols", "count": n, "severity": "high" if n else "ok"}


def _check_broken_edges(conn) -> dict:
    cur = conn.execute(
        """
        SELECT COUNT(*) FROM edges e
        WHERE e.source_id NOT IN (SELECT id FROM symbols)
           OR e.target_id NOT IN (SELECT id FROM symbols)
        """
    )
    n = cur.fetchone()[0]
    return {"name": "broken_edges", "count": n, "severity": "high" if n else "ok"}


def _check_duplicate_file_paths(conn) -> dict:
    cur = conn.execute("SELECT COUNT(*) FROM (SELECT path, COUNT(*) c FROM files GROUP BY path HAVING c > 1)")
    n = cur.fetchone()[0]
    return {"name": "duplicate_file_paths", "count": n, "severity": "high" if n else "ok"}


def _check_missing_fts(conn) -> dict:
    try:
        cur = conn.execute("SELECT COUNT(*) FROM symbols WHERE id NOT IN (SELECT rowid FROM symbol_fts)")
        n = cur.fetchone()[0]
        sev = "medium" if n else "ok"
    except Exception:
        # FTS5 table not present (very old schema or build without FTS)
        return {"name": "missing_fts_rows", "count": 0, "severity": "ok", "note": "fts5 not available"}
    return {"name": "missing_fts_rows", "count": n, "severity": sev}


def _check_invalid_line_spans(conn) -> dict:
    cur = conn.execute(
        """
        SELECT COUNT(*) FROM symbols
        WHERE line_start IS NOT NULL AND line_end IS NOT NULL
          AND (line_end < line_start OR line_start < 0)
        """
    )
    n = cur.fetchone()[0]
    return {"name": "invalid_line_spans", "count": n, "severity": "medium" if n else "ok"}


def _check_corrupt_metrics(conn) -> dict:
    try:
        cur = conn.execute(
            """
            SELECT COUNT(*) FROM symbol_metrics
            WHERE cognitive_complexity < 0
               OR nesting_depth < 0
               OR param_count < 0
               OR line_count < 0
            """
        )
        n = cur.fetchone()[0]
        sev = "medium" if n else "ok"
        note = None
    except Exception as exc:
        n = 0
        sev = "ok"
        note = f"symbol_metrics not queryable: {exc.__class__.__name__}"
    out = {"name": "corrupt_metrics", "count": n, "severity": sev}
    if note:
        out["note"] = note
    return out


def _check_zero_symbols_per_file(conn) -> dict:
    """Files with role=source that have zero symbols. Often a parser failure."""
    try:
        cur = conn.execute(
            """
            SELECT COUNT(*) FROM files f
            WHERE COALESCE(f.file_role, 'source') = 'source'
              AND NOT EXISTS (SELECT 1 FROM symbols WHERE file_id = f.id)
              AND f.lang NOT IN ('json', 'yaml', 'toml', 'markdown', 'text', 'xml')
            """
        )
        n = cur.fetchone()[0]
        sev = "medium" if n > 0 else "ok"
    except Exception:
        return {"name": "files_with_zero_symbols", "count": 0, "severity": "ok", "note": "unsupported"}
    return {"name": "files_with_zero_symbols", "count": n, "severity": sev}


CHECKS = (
    _check_orphan_symbols,
    _check_broken_edges,
    _check_duplicate_file_paths,
    _check_missing_fts,
    _check_invalid_line_spans,
    _check_corrupt_metrics,
    _check_zero_symbols_per_file,
)

EXIT_GATE_FAILURE = 5


@roam_capability(
    name="db-check",
    category="health",
    summary="Integrity sweep over the local index: orphans, broken edges, missing FTS.",
    inputs=[],
    outputs=["findings", "verdict"],
    examples=["roam db-check", "roam db-check --ci"],
    tags=["diagnostics", "ci"],
    ai_safe=True,
    requires_index=True,
    maturity="stable",
    mcp_expose=True,
    mcp_preset=("core",),
    side_effect=False,
    task_required=False,
    destructive=False,
    stale_sensitive=False,
)
@click.command("db-check")
@click.option("--ci", is_flag=True, help="Exit with code 5 on any high-severity finding (CI gate).")
@click.pass_context
def db_check(ctx, ci: bool):
    """Integrity sweep over the local index. Reports orphans, broken edges, missing FTS, etc."""
    json_mode = bool(ctx.obj and ctx.obj.get("json"))
    ensure_index()

    findings = []
    with open_db(readonly=True) as conn:
        for check in CHECKS:
            try:
                findings.append(check(conn))
            except Exception as exc:
                findings.append(
                    {
                        "name": check.__name__.lstrip("_check_"),
                        "count": 0,
                        "severity": "error",
                        "note": f"check failed: {exc.__class__.__name__}: {exc}",
                    }
                )

    high = sum(1 for f in findings if f["severity"] == "high")
    medium = sum(1 for f in findings if f["severity"] == "medium")
    errors = sum(1 for f in findings if f["severity"] == "error")
    verdict = "BAD" if (high or errors) else ("REVIEW" if medium else "OK")

    if json_mode:
        click.echo(
            to_json(
                json_envelope(
                    "db-check",
                    summary={
                        "verdict": verdict,
                        "high": high,
                        "medium": medium,
                        "errors": errors,
                        "checks_run": len(findings),
                    },
                    findings=findings,
                )
            )
        )
    else:
        click.echo(f"VERDICT: {verdict}  ({high} high, {medium} medium, {errors} error)")
        click.echo("")
        for f in findings:
            sev_tag = f["severity"].upper()
            note = f.get("note")
            line = f"  [{sev_tag:6s}] {f['name']:30s} count={f['count']}"
            if note:
                line += f"  ({note})"
            click.echo(line)

    if ci and (high or errors):
        ctx.exit(EXIT_GATE_FAILURE)
