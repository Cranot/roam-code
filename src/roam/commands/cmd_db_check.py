"""roam db-check — integrity sweep over the local index.

Looks for: orphan symbols (no file row), broken edges (referenced
symbol missing), duplicate file paths, missing FTS rows, invalid
line spans, corrupt or missing metrics. Returns a verdict and a
list of findings. Exit code 5 on any HIGH-severity finding, or on a
check that could not run at all, so CI can gate on it.

Severity vocabulary: ``high`` / ``medium`` gate as findings, ``ok``
means MEASURED and clean, ``error`` means the check raised, and
``unsupported`` means the feature it inspects is absent here. The last
two carry ``count: None`` -- they produced no measurement, and a floored
zero would read as "measured, nothing wrong" (W1332).

Output formats: text (default), ``--json``. SARIF is deliberately NOT
emitted because db-check verifies INDEX INTEGRITY (orphan symbol rows,
broken edge references, duplicate file paths, FTS row coverage) — not
per-location code violations in user source. The integrity findings
describe the SQLite index state, which has no source coordinates to
populate SARIF ``locations[]``. SARIF here would conflate
validator-output (index well-formed?) with code-analyzer-output (user
code well-formed?). See ``cmd_rules_validate`` for the parallel
validator-not-detector disclosure pattern + action.yml _SUPPORTED_SARIF
allowlist + W1192 audit memo + W1221-audit memo.
"""

from __future__ import annotations

import sqlite3

import click

from roam.capability import roam_capability
from roam.commands.resolve import ensure_index
from roam.db.connection import open_db
from roam.output.formatter import json_envelope, to_json

#: Severity for a check that could NOT be computed on this database (an
#: optional feature is absent). It is deliberately NOT ``ok``: ``ok`` means
#: "measured, nothing wrong", and the two must never render the same. ``count``
#: is ``None`` for these rows so no consumer can read a measured zero out of a
#: check that never ran.
SEVERITY_UNSUPPORTED = "unsupported"


def _unsupported(name: str, note: str) -> dict:
    return {"name": name, "count": None, "severity": SEVERITY_UNSUPPORTED, "note": note}


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
    except sqlite3.OperationalError as exc:
        if "no such table" not in str(exc).lower():
            # Not a capability gap -- a real DB fault. Let _run_checks record
            # it as an `error` rather than reporting a computed zero.
            raise
        # FTS5 table not present (very old schema or build without FTS).
        # UNSUPPORTED, not OK: nothing was measured, so `count` stays None.
        return _unsupported("missing_fts_rows", f"fts5 not available: {exc}")
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
    # `symbol_metrics` is created unconditionally by the shipped schema, so a
    # query failure here is a fault in THIS database, not an optional feature.
    # It propagates to _run_checks, which records severity `error` -- a caught
    # OperationalError used to report count=0/severity=ok, i.e. "metrics are
    # clean" for a table that could not be read at all.
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
    return {"name": "corrupt_metrics", "count": n, "severity": "medium" if n else "ok"}


def _check_zero_symbols_per_file(conn) -> dict:
    """Files with role=source that have zero symbols. Often a parser failure.

    The column is ``files.language``; this check queried ``f.lang`` and so
    raised on EVERY database, healthy or not, and the swallowed
    OperationalError reported it as `ok`/`unsupported`. Both are fixed: the
    column name is correct, and a genuine query failure now propagates to
    _run_checks instead of being floored to a clean zero.
    """
    cur = conn.execute(
        """
        SELECT COUNT(*) FROM files f
        WHERE COALESCE(f.file_role, 'source') = 'source'
          AND NOT EXISTS (SELECT 1 FROM symbols WHERE file_id = f.id)
          AND f.language NOT IN ('json', 'yaml', 'toml', 'markdown', 'text', 'xml')
        """
    )
    n = cur.fetchone()[0]
    return {"name": "files_with_zero_symbols", "count": n, "severity": "medium" if n > 0 else "ok"}


CHECKS = (
    _check_orphan_symbols,
    _check_broken_edges,
    _check_duplicate_file_paths,
    _check_missing_fts,
    _check_invalid_line_spans,
    _check_corrupt_metrics,
    _check_zero_symbols_per_file,
)


def _run_checks(conn) -> list[dict]:
    findings = []
    for check in CHECKS:
        try:
            findings.append(check(conn))
        except sqlite3.Error as exc:
            findings.append(
                {
                    # removeprefix, not lstrip: lstrip strips a CHARACTER SET,
                    # so `_check_corrupt_metrics` came out as "orrupt_metrics".
                    "name": check.__name__.removeprefix("_check_"),
                    # None, not 0 -- the check produced no measurement, and a
                    # zero here reads as "measured, nothing wrong".
                    "count": None,
                    "severity": "error",
                    "note": f"check failed: {exc.__class__.__name__}: {exc}",
                }
            )
    return findings


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
@click.option(
    "--ci",
    is_flag=True,
    help="Exit with code 5 on any high-severity finding or failed check (CI gate).",
)
@click.pass_context
def db_check(ctx, ci: bool):
    """Integrity sweep over the local index. Reports orphans, broken edges, missing FTS, etc."""
    json_mode = bool(ctx.obj and ctx.obj.get("json"))
    ensure_index()

    with open_db(readonly=True) as conn:
        findings = _run_checks(conn)

    high = sum(1 for f in findings if f["severity"] == "high")
    medium = sum(1 for f in findings if f["severity"] == "medium")
    errors = sum(1 for f in findings if f["severity"] == "error")
    unsupported = sum(1 for f in findings if f["severity"] == SEVERITY_UNSUPPORTED)
    # A check that could not run is not a passing check: INCOMPLETE ranks
    # between REVIEW and OK so an uncomputed sweep never renders as a clean one.
    if high or errors:
        verdict = "BAD"
    elif medium:
        verdict = "REVIEW"
    elif unsupported:
        verdict = "INCOMPLETE"
    else:
        verdict = "OK"
    checks_complete = not (errors or unsupported)

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
                        "unsupported": unsupported,
                        "checks_run": len(findings),
                        "checks_complete": checks_complete,
                        "partial_success": not checks_complete,
                    },
                    findings=findings,
                )
            )
        )
    else:
        click.echo(f"VERDICT: {verdict}  ({high} high, {medium} medium, {errors} error, {unsupported} not computed)")
        click.echo("")
        for f in findings:
            sev_tag = f["severity"].upper()
            note = f.get("note")
            count = f["count"]
            rendered = "n/a" if count is None else str(count)
            line = f"  [{sev_tag:11s}] {f['name']:30s} count={rendered}"
            if note:
                line += f"  ({note})"
            click.echo(line)
        if not checks_complete:
            click.echo("")
            click.echo(
                f"INCOMPLETE: {errors + unsupported} of {len(findings)} checks produced no measurement; "
                "the counts above do not cover them."
            )

    if ci and (high or errors):
        ctx.exit(EXIT_GATE_FAILURE)
