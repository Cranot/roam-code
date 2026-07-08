"""Vulnerability data management: ingestion from scanner reports and symbol matching."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import click

# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

# The vulnerabilities table is defined in roam.db.schema (SCHEMA_SQL) and is
# created by ensure_schema() during open_db().  The helper below is kept for
# callers that operate on standalone connections (e.g. tests, external tools).


def ensure_vuln_table(conn: sqlite3.Connection) -> None:
    """Ensure the vulnerabilities table exists.

    Delegates to the canonical schema in roam.db.schema so there is a single
    source of truth for the table definition.
    """
    from roam.db.schema import SCHEMA_SQL

    conn.executescript(SCHEMA_SQL)


# ---------------------------------------------------------------------------
# Symbol matching
# ---------------------------------------------------------------------------


def _escape_like(pattern: str) -> str:
    """Escape SQLite LIKE wildcards in a literal so they match themselves.

    Without this, a package name containing ``_`` (single-char wildcard)
    or ``%`` (multi-char wildcard) would match-explode against the
    symbols table — a hostile or malformed scanner report with
    ``package_name = "_"`` would match every symbol.

    Used together with an explicit ``ESCAPE '\\'`` clause on the LIKE.
    """
    return pattern.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def match_vuln_to_symbols(conn: sqlite3.Connection, package_name: str) -> list[dict]:
    """Try to find symbols that reference or match the vulnerable package.

    Strategy:
    1. Search symbols whose name or qualified_name contains the package name.
    2. Search edges for import references where the target symbol name matches.
    3. Return matched symbol IDs and file paths.
    """
    matches: list[dict] = []
    seen_ids: set[int] = set()

    # Direct symbol name match. The qualified_name match is anchored to
    # DOTTED-SEGMENT boundaries, not a naked substring: a short package name
    # like ``os`` must appear as a full path segment (``os``, ``os.path``,
    # ``pkg.os``, ``pkg.os.sub``) and must NOT substring-match unrelated
    # symbols such as ``positions`` / ``close`` / ``host`` -- the false-positive
    # class that would poison a reachability report on a buyer's repo. The
    # ESCAPE clause keeps a literal ``_`` / ``%`` in the name from acting as a
    # SQL wildcard.
    esc = _escape_like(package_name)
    rows = conn.execute(
        "SELECT s.id, s.name, s.qualified_name, f.path AS file_path "
        "FROM symbols s JOIN files f ON s.file_id = f.id "
        "WHERE s.name = ? "
        "   OR s.qualified_name = ? "
        "   OR s.qualified_name LIKE ? ESCAPE '\\' "  # pkg.something
        "   OR s.qualified_name LIKE ? ESCAPE '\\' "  # something.pkg
        "   OR s.qualified_name LIKE ? ESCAPE '\\'",  # something.pkg.something
        (
            package_name,
            package_name,
            f"{esc}.%",
            f"%.{esc}",
            f"%.{esc}.%",
        ),
    ).fetchall()
    for r in rows:
        if r["id"] not in seen_ids:
            seen_ids.add(r["id"])
            matches.append(
                {
                    "symbol_id": r["id"],
                    "name": r["name"],
                    "qualified_name": r["qualified_name"],
                    "file_path": r["file_path"],
                }
            )

    # Edge-based: look for import edges targeting a symbol whose name matches.
    # W543-followup: previously bare ``e.kind = 'import'``; the canonical
    # writer emits singular but plugin extractors may emit ``'imports'``.
    # Source the IN-clause from the shared helper so vuln-reach catches
    # plugin-emitted rows too.
    from roam.db.edge_kinds import import_in_clause

    rows = conn.execute(
        "SELECT DISTINCT e.source_id, s.name, s.qualified_name, f.path AS file_path "
        "FROM edges e "
        "JOIN symbols s ON e.source_id = s.id "
        "JOIN files f ON s.file_id = f.id "
        f"WHERE {import_in_clause('e.kind')} AND EXISTS ("
        "  SELECT 1 FROM symbols t WHERE t.id = e.target_id AND t.name = ?"
        ")",
        (package_name,),
    ).fetchall()
    for r in rows:
        if r["source_id"] not in seen_ids:
            seen_ids.add(r["source_id"])
            matches.append(
                {
                    "symbol_id": r["source_id"],
                    "name": r["name"],
                    "qualified_name": r["qualified_name"],
                    "file_path": r["file_path"],
                }
            )

    return matches


# ---------------------------------------------------------------------------
# Ingest helpers
# ---------------------------------------------------------------------------


def _insert_vuln(
    conn: sqlite3.Connection,
    cve_id: str | None,
    package_name: str,
    severity: str | None,
    title: str | None,
    source: str,
) -> dict:
    """Insert a single vulnerability and attempt symbol matching.

    Returns a dict describing the ingested vuln.
    """
    matches = match_vuln_to_symbols(conn, package_name)
    matched_id = matches[0]["symbol_id"] if matches else None
    matched_file = matches[0]["file_path"] if matches else None

    conn.execute(
        "INSERT INTO vulnerabilities "
        "(cve_id, package_name, severity, title, source, matched_symbol_id, matched_file) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (cve_id, package_name, severity, title, source, matched_id, matched_file),
    )

    return {
        "cve_id": cve_id,
        "package_name": package_name,
        "severity": severity,
        "title": title,
        "source": source,
        "matched_symbol_id": matched_id,
        "matched_file": matched_file,
    }


# Maximum size of a vulnerability scanner report we'll ingest. Real npm /
# pip / trivy / osv outputs are typically <5 MB even on huge codebases;
# 50 MB is a safe ceiling that catches hostile or malformed reports
# (a 1 GB generic JSON would otherwise OOM the process).
_MAX_REPORT_BYTES = 50 * 1024 * 1024


def _load_json(report_path: str) -> object:
    """Load a JSON scanner report from disk and return the parsed content.

    Refuses to load files larger than ``_MAX_REPORT_BYTES`` to bound the
    memory footprint — a hostile scanner output with a multi-GB JSON
    payload would otherwise be loaded entirely into memory.
    """
    p = Path(report_path)
    try:
        size = p.stat().st_size
    except OSError as exc:
        raise click.ClickException(
            f"could not stat scanner report {report_path}: {exc}\n"
            "  If this looks unexpected, run `roam doctor` to diagnose your install."
        ) from exc
    if size > _MAX_REPORT_BYTES:
        raise click.ClickException(
            f"scanner report {report_path} is {size:,} bytes, exceeding the "
            f"{_MAX_REPORT_BYTES:,}-byte cap. Real npm/pip/trivy/osv outputs "
            f"are typically under 5 MB; a multi-GB report likely indicates "
            f"a hostile or malformed file. Re-run the scanner or split the "
            f"report into smaller chunks."
        )
    return json.loads(p.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# Format-specific ingesters
# ---------------------------------------------------------------------------


def ingest_npm_audit(conn: sqlite3.Connection, report_path: str) -> list[dict]:
    """Parse npm audit JSON format and ingest vulnerabilities.

    Supports both npm audit v1 (advisories dict) and v2 (vulnerabilities dict).
    """
    ensure_vuln_table(conn)
    data = _load_json(report_path)
    results: list[dict] = []

    if isinstance(data, dict):
        # npm audit v2: {"vulnerabilities": {"pkg_name": {...}}}
        if "vulnerabilities" in data and isinstance(data["vulnerabilities"], dict):
            for pkg_name, info in data["vulnerabilities"].items():
                severity = info.get("severity", "unknown")
                via = info.get("via", [])
                cve_id = None
                title = None
                if isinstance(via, list):
                    for v in via:
                        if isinstance(v, dict):
                            # REAL BUG (operator precedence): the prior
                            # ``cve_id or v.get("url", "").split("/")[-1] if "url" in v else None``
                            # parsed as ``(cve_id or X) if "url" in v else None`` —
                            # so any *later* via-entry lacking ``url`` would
                            # wipe a previously-found CVE back to ``None``.
                            # Fix: only attempt to derive cve_id from this
                            # entry's ``url``; preserve the running cve_id
                            # when this entry has none.
                            if not cve_id and "url" in v:
                                cve_id = v.get("url", "").split("/")[-1] or None
                            if not title:
                                title = v.get("title")
                results.append(_insert_vuln(conn, cve_id, pkg_name, severity, title, "npm-audit"))

        # npm audit v1: {"advisories": {"id": {...}}}
        elif "advisories" in data and isinstance(data["advisories"], dict):
            for _id, adv in data["advisories"].items():
                cve_id = None
                cves = adv.get("cves", [])
                if cves:
                    cve_id = cves[0]
                results.append(
                    _insert_vuln(
                        conn,
                        cve_id,
                        adv.get("module_name", "unknown"),
                        adv.get("severity", "unknown"),
                        adv.get("title"),
                        "npm-audit",
                    )
                )

    return results


def ingest_pip_audit(conn: sqlite3.Connection, report_path: str) -> list[dict]:
    """Parse pip-audit JSON format and ingest vulnerabilities.

    pip-audit JSON is a list of dicts with keys: name, version, vulns.
    """
    ensure_vuln_table(conn)
    data = _load_json(report_path)
    results: list[dict] = []

    if isinstance(data, list):
        for entry in data:
            pkg = entry.get("name", "unknown")
            for vuln in entry.get("vulns", []):
                cve_id = vuln.get("id") or vuln.get("aliases", [None])[0] if vuln.get("aliases") else vuln.get("id")
                results.append(
                    _insert_vuln(
                        conn,
                        cve_id,
                        pkg,
                        vuln.get("fix_versions", [""])[0] if vuln.get("fix_versions") else "unknown",
                        vuln.get("description"),
                        "pip-audit",
                    )
                )
    # pip-audit may also produce {"dependencies": [...]}
    elif isinstance(data, dict) and "dependencies" in data:
        for entry in data["dependencies"]:
            pkg = entry.get("name", "unknown")
            for vuln in entry.get("vulns", []):
                cve_id = vuln.get("id")
                results.append(
                    _insert_vuln(
                        conn,
                        cve_id,
                        pkg,
                        vuln.get("severity", "unknown"),
                        vuln.get("description"),
                        "pip-audit",
                    )
                )

    return results


def ingest_trivy(conn: sqlite3.Connection, report_path: str) -> list[dict]:
    """Parse Trivy JSON format and ingest vulnerabilities.

    Trivy JSON has {"Results": [{"Vulnerabilities": [...]}]}.
    """
    ensure_vuln_table(conn)
    data = _load_json(report_path)
    results: list[dict] = []

    if isinstance(data, dict):
        for result_block in data.get("Results", []):
            for vuln in result_block.get("Vulnerabilities", []):
                results.append(
                    _insert_vuln(
                        conn,
                        vuln.get("VulnerabilityID"),
                        vuln.get("PkgName", "unknown"),
                        vuln.get("Severity", "unknown").lower(),
                        vuln.get("Title"),
                        "trivy",
                    )
                )

    return results


def _normalized_osv_severity(vuln: dict) -> str:
    """Return the OSV database_specific severity lowercased, or 'unknown'.

    OSV stores severity inside ``database_specific`` as a raw string. We
    normalize it once so downstream reporting sees a consistent lowercase
    value, and default to ``'unknown'`` when the field is absent or empty.
    """
    db_specific = vuln.get("database_specific") or {}
    severity = db_specific.get("severity")
    return severity.lower() if severity else "unknown"


def ingest_osv(conn: sqlite3.Connection, report_path: str) -> list[dict]:
    """Parse OSV JSON format and ingest vulnerabilities.

    OSV scanner output: {"results": [{"packages": [{"package": {...}, "vulnerabilities": [...]}]}]}
    Also supports a flat list of OSV entries.
    """
    ensure_vuln_table(conn)
    data = _load_json(report_path)
    results: list[dict] = []

    if isinstance(data, dict) and "results" in data:
        for result_block in data["results"]:
            for pkg_info in result_block.get("packages", []):
                pkg_name = pkg_info.get("package", {}).get("name", "unknown")
                for vuln in pkg_info.get("vulnerabilities", []):
                    cve_id = vuln.get("id")
                    aliases = vuln.get("aliases", [])
                    if not cve_id and aliases:
                        cve_id = aliases[0]
                    results.append(
                        _insert_vuln(
                            conn,
                            cve_id,
                            pkg_name,
                            _normalized_osv_severity(vuln),
                            vuln.get("summary"),
                            "osv",
                        )
                    )
    elif isinstance(data, list):
        for vuln in data:
            cve_id = vuln.get("id")
            pkg_name = "unknown"
            affected = vuln.get("affected", [])
            if affected:
                pkg_name = affected[0].get("package", {}).get("name", pkg_name)
            results.append(
                _insert_vuln(
                    conn,
                    cve_id,
                    pkg_name,
                    _normalized_osv_severity(vuln),
                    vuln.get("summary"),
                    "osv",
                )
            )

    return results


def ingest_generic(conn: sqlite3.Connection, report_path: str) -> list[dict]:
    """Parse a simple generic JSON format.

    Expected: [{"cve": "...", "package": "...", "severity": "...", "title": "..."}]
    """
    ensure_vuln_table(conn)
    data = _load_json(report_path)
    results: list[dict] = []

    if isinstance(data, list):
        for entry in data:
            results.append(
                _insert_vuln(
                    conn,
                    entry.get("cve"),
                    entry.get("package", "unknown"),
                    entry.get("severity", "unknown"),
                    entry.get("title"),
                    "generic",
                )
            )

    return results
