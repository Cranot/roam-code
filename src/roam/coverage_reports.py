"""Coverage report ingestion and lookup helpers.

Supports:
- LCOV (`*.info`)
- Cobertura XML
- coverage.py JSON
"""

from __future__ import annotations

import json
import sqlite3
import xml.etree.ElementTree as ET
from bisect import bisect_left, bisect_right
from pathlib import Path

from roam.db.connection import batched_in, find_project_root


def _new_cov_entry() -> dict[str, set[int]]:
    return {"covered": set(), "coverable": set()}


def _normalise_path(path: str) -> str:
    """Normalize report/index paths into comparable posix-ish strings."""
    norm = (path or "").strip().replace("\\", "/")
    while "//" in norm:
        norm = norm.replace("//", "/")
    if norm.startswith("./"):
        norm = norm[2:]
    return norm.rstrip("/")


def _strip_drive(path: str) -> str:
    """Strip `C:/` prefixes to improve path matching across CI/OS contexts."""
    if len(path) >= 2 and path[1] == ":":
        return path[2:].lstrip("/")
    return path.lstrip("/")


def _to_int(value) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _count_in_range(sorted_lines: list[int], start: int, end: int) -> int:
    """Count line numbers in inclusive [start, end]."""
    if not sorted_lines or end < start:
        return 0
    lo = bisect_left(sorted_lines, start)
    hi = bisect_right(sorted_lines, end)
    return max(0, hi - lo)


def parse_lcov_report(path: Path) -> dict[str, dict[str, set[int]]]:
    """Parse an LCOV report into `{file_path: {covered, coverable}}`."""
    mapping: dict[str, dict[str, set[int]]] = {}
    current_file: str | None = None

    for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.startswith("SF:"):
            current_file = line[3:].strip()
            if current_file:
                mapping.setdefault(current_file, _new_cov_entry())
            continue
        if line == "end_of_record":
            current_file = None
            continue
        if not current_file or not line.startswith("DA:"):
            continue

        payload = line[3:]
        parts = payload.split(",")
        if len(parts) < 2:
            continue
        lineno = _to_int(parts[0])
        hits = _to_int(parts[1])
        if lineno is None or lineno <= 0:
            continue

        entry = mapping.setdefault(current_file, _new_cov_entry())
        entry["coverable"].add(lineno)
        if hits is not None and hits > 0:
            entry["covered"].add(lineno)

    return mapping


def parse_cobertura_report(path: Path) -> dict[str, dict[str, set[int]]]:
    """Parse Cobertura XML into `{file_path: {covered, coverable}}`."""
    mapping: dict[str, dict[str, set[int]]] = {}
    root = ET.parse(path).getroot()

    for class_node in root.findall(".//{*}class"):
        filename = (class_node.attrib.get("filename") or "").strip()
        if not filename:
            continue

        entry = mapping.setdefault(filename, _new_cov_entry())
        for line_node in class_node.findall(".//{*}line"):
            lineno = _to_int(line_node.attrib.get("number"))
            if lineno is None or lineno <= 0:
                continue
            hits = _to_int(line_node.attrib.get("hits"))
            entry["coverable"].add(lineno)
            if hits is not None and hits > 0:
                entry["covered"].add(lineno)

    return mapping


def parse_coveragepy_json_report(path: Path) -> dict[str, dict[str, set[int]]]:
    """Parse coverage.py JSON into `{file_path: {covered, coverable}}`."""
    payload = json.loads(path.read_text(encoding="utf-8"))
    files = payload.get("files")
    if not isinstance(files, dict):
        return {}

    mapping: dict[str, dict[str, set[int]]] = {}
    for file_path, data in files.items():
        if not isinstance(data, dict):
            continue
        executed = {
            ln for ln in (_to_int(v) for v in data.get("executed_lines", []))
            if ln is not None and ln > 0
        }
        missing = {
            ln for ln in (_to_int(v) for v in data.get("missing_lines", []))
            if ln is not None and ln > 0
        }
        excluded = {
            ln for ln in (_to_int(v) for v in data.get("excluded_lines", []))
            if ln is not None and ln > 0
        }

        coverable = (executed | missing) - excluded
        if not coverable:
            # Some reports only include executed line lists.
            coverable = set(executed)
        covered = executed & coverable

        entry = mapping.setdefault(file_path, _new_cov_entry())
        entry["coverable"].update(coverable)
        entry["covered"].update(covered)

    return mapping


def _detect_format(path: Path) -> str:
    """Detect report format from extension and content."""
    suffix = path.suffix.lower()
    if suffix == ".info":
        return "lcov"
    if suffix == ".xml":
        return "cobertura"
    if suffix == ".json":
        return "coveragepy-json"

    head = path.read_text(encoding="utf-8", errors="replace")[:4096]
    if "SF:" in head and "DA:" in head:
        return "lcov"
    if "<coverage" in head and "<class" in head:
        return "cobertura"
    if '"files"' in head and '"executed_lines"' in head:
        return "coveragepy-json"
    raise ValueError(f"Unsupported coverage report format: {path}")


def parse_coverage_report(path: Path) -> tuple[str, dict[str, dict[str, set[int]]]]:
    """Parse a coverage report and return `(format, mapping)`."""
    fmt = _detect_format(path)
    if fmt == "lcov":
        return fmt, parse_lcov_report(path)
    if fmt == "cobertura":
        return fmt, parse_cobertura_report(path)
    if fmt == "coveragepy-json":
        return fmt, parse_coveragepy_json_report(path)
    raise ValueError(f"Unsupported coverage report format: {path}")


def _candidate_report_paths(report_path: str, project_root: Path) -> list[str]:
    """Generate normalized path candidates for report->index matching."""
    candidates: list[str] = []
    norm = _normalise_path(report_path)
    if norm:
        candidates.append(norm)

    raw = Path(report_path)
    if raw.is_absolute():
        try:
            rel = raw.resolve().relative_to(project_root.resolve()).as_posix()
            candidates.append(_normalise_path(rel))
        except Exception:
            pass
    else:
        try:
            abs_candidate = (project_root / raw).resolve()
            rel = abs_candidate.relative_to(project_root.resolve()).as_posix()
            candidates.append(_normalise_path(rel))
        except Exception:
            pass

    expanded: list[str] = []
    seen: set[str] = set()
    for cand in candidates:
        if not cand:
            continue
        for item in (cand, _strip_drive(cand), cand.lstrip("/")):
            if item and item not in seen:
                expanded.append(item)
                seen.add(item)
    return expanded


def _resolve_file_id(
    report_path: str,
    *,
    project_root: Path,
    exact_index: dict[str, dict],
    basename_index: dict[str, list[dict]],
) -> dict | None:
    """Resolve a report path to an indexed file row (`id`, `path`)."""
    candidates = _candidate_report_paths(report_path, project_root)
    if not candidates:
        return None

    for cand in candidates:
        row = exact_index.get(cand)
        if row:
            return row

    suffix_matches: list[tuple[int, dict]] = []
    for cand in candidates:
        for indexed_path, row in exact_index.items():
            if indexed_path == cand:
                suffix_matches.append((len(cand), row))
                continue
            if indexed_path.endswith("/" + cand) or cand.endswith("/" + indexed_path):
                suffix_matches.append((min(len(cand), len(indexed_path)), row))

    if suffix_matches:
        suffix_matches.sort(key=lambda t: (-t[0], len(t[1]["path"])))
        top_score = suffix_matches[0][0]
        top_rows = [r for score, r in suffix_matches if score == top_score]
        uniq = {r["id"]: r for r in top_rows}
        if len(uniq) == 1:
            return next(iter(uniq.values()))

    for cand in candidates:
        base = Path(cand).name
        rows = basename_index.get(base, [])
        if len(rows) == 1:
            return rows[0]

    return None


def _merge_mapping(
    merged: dict[str, dict[str, set[int]]],
    parsed: dict[str, dict[str, set[int]]],
) -> None:
    for raw_path, data in parsed.items():
        norm = _normalise_path(raw_path)
        if not norm:
            continue
        entry = merged.setdefault(norm, _new_cov_entry())
        entry["coverable"].update(
            ln for ln in data.get("coverable", set()) if isinstance(ln, int) and ln > 0
        )
        entry["covered"].update(
            ln for ln in data.get("covered", set()) if isinstance(ln, int) and ln > 0
        )
        # covered is always a subset of coverable
        entry["covered"].intersection_update(entry["coverable"])


def ingest_coverage_reports(
    conn: sqlite3.Connection,
    report_paths: list[str],
    *,
    replace_existing: bool = True,
    project_root: Path | None = None,
) -> dict:
    """Import coverage reports and persist per-file/per-symbol coverage columns."""
    if not report_paths:
        raise ValueError("No coverage report paths provided")

    if project_root is None:
        project_root = find_project_root()
    project_root = Path(project_root).resolve()

    parsed_by_file: dict[str, dict[str, set[int]]] = {}
    formats: dict[str, str] = {}
    for report in report_paths:
        report_path = Path(report)
        if not report_path.exists() or not report_path.is_file():
            raise FileNotFoundError(f"Coverage report not found: {report}")
        fmt, mapping = parse_coverage_report(report_path)
        formats[str(report_path)] = fmt
        _merge_mapping(parsed_by_file, mapping)

    indexed_rows = conn.execute("SELECT id, path FROM files").fetchall()
    exact_index: dict[str, dict] = {}
    basename_index: dict[str, list[dict]] = {}
    for row in indexed_rows:
        norm = _normalise_path(row["path"])
        rec = {"id": row["id"], "path": row["path"]}
        exact_index[norm] = rec
        basename_index.setdefault(Path(norm).name, []).append(rec)

    file_cov_by_id: dict[int, dict] = {}
    unmatched_files: list[str] = []
    for report_file, cov in parsed_by_file.items():
        resolved = _resolve_file_id(
            report_file,
            project_root=project_root,
            exact_index=exact_index,
            basename_index=basename_index,
        )
        if not resolved:
            unmatched_files.append(report_file)
            continue
        fid = int(resolved["id"])
        bucket = file_cov_by_id.setdefault(
            fid,
            {
                "path": resolved["path"],
                "covered": set(),
                "coverable": set(),
            },
        )
        bucket["coverable"].update(cov.get("coverable", set()))
        bucket["covered"].update(cov.get("covered", set()))
        bucket["covered"].intersection_update(bucket["coverable"])

    if replace_existing:
        conn.execute(
            "UPDATE file_stats "
            "SET coverage_pct = NULL, covered_lines = NULL, coverable_lines = NULL"
        )
        conn.execute(
            "UPDATE symbol_metrics "
            "SET coverage_pct = NULL, covered_lines = NULL, coverable_lines = NULL"
        )

    file_rows_updated = 0
    symbol_rows_updated = 0
    total_covered = 0
    total_coverable = 0

    for fid, cov in file_cov_by_id.items():
        coverable = {ln for ln in cov["coverable"] if isinstance(ln, int) and ln > 0}
        covered = {ln for ln in cov["covered"] if isinstance(ln, int) and ln > 0}
        covered.intersection_update(coverable)

        covered_lines = len(covered)
        coverable_lines = len(coverable)
        pct = round((covered_lines * 100.0) / coverable_lines, 2) if coverable_lines else None

        conn.execute(
            "INSERT INTO file_stats (file_id, coverage_pct, covered_lines, coverable_lines) "
            "VALUES (?, ?, ?, ?) "
            "ON CONFLICT(file_id) DO UPDATE SET "
            "coverage_pct = excluded.coverage_pct, "
            "covered_lines = excluded.covered_lines, "
            "coverable_lines = excluded.coverable_lines",
            (fid, pct, covered_lines, coverable_lines),
        )
        file_rows_updated += 1
        total_covered += covered_lines
        total_coverable += coverable_lines

        if not coverable:
            continue

        cov_sorted = sorted(coverable)
        covered_sorted = sorted(covered)
        sym_rows = conn.execute(
            "SELECT id, line_start, line_end FROM symbols "
            "WHERE file_id = ? AND line_start IS NOT NULL",
            (fid,),
        ).fetchall()

        for sym in sym_rows:
            start = _to_int(sym["line_start"]) or 0
            end = _to_int(sym["line_end"])
            if start <= 0:
                continue
            if end is None or end < start:
                end = start

            sym_coverable = _count_in_range(cov_sorted, start, end)
            if sym_coverable <= 0:
                continue
            sym_covered = _count_in_range(covered_sorted, start, end)
            sym_pct = round((sym_covered * 100.0) / sym_coverable, 2)

            conn.execute(
                "INSERT INTO symbol_metrics (symbol_id, coverage_pct, covered_lines, coverable_lines) "
                "VALUES (?, ?, ?, ?) "
                "ON CONFLICT(symbol_id) DO UPDATE SET "
                "coverage_pct = excluded.coverage_pct, "
                "covered_lines = excluded.covered_lines, "
                "coverable_lines = excluded.coverable_lines",
                (sym["id"], sym_pct, sym_covered, sym_coverable),
            )
            symbol_rows_updated += 1

    overall_pct = round((total_covered * 100.0) / total_coverable, 2) if total_coverable else None
    return {
        "reports": len(report_paths),
        "formats": formats,
        "parsed_files": len(parsed_by_file),
        "matched_files": len(file_cov_by_id),
        "unmatched_files": sorted(unmatched_files),
        "unmatched_count": len(unmatched_files),
        "file_rows_updated": file_rows_updated,
        "symbol_rows_updated": symbol_rows_updated,
        "covered_lines": total_covered,
        "coverable_lines": total_coverable,
        "coverage_pct": overall_pct,
    }


def load_symbol_coverage_map(conn: sqlite3.Connection, symbol_ids: set[int]) -> dict[int, dict]:
    """Return imported coverage rows keyed by symbol_id."""
    if not symbol_ids:
        return {}

    try:
        rows = batched_in(
            conn,
            "SELECT symbol_id, coverage_pct, covered_lines, coverable_lines "
            "FROM symbol_metrics WHERE symbol_id IN ({ph})",
            sorted(symbol_ids),
        )
    except Exception:
        return {}

    out: dict[int, dict] = {}
    for row in rows:
        sid = int(row["symbol_id"])
        out[sid] = {
            "coverage_pct": row["coverage_pct"],
            "covered_lines": row["covered_lines"] or 0,
            "coverable_lines": row["coverable_lines"] or 0,
        }
    return out


def imported_coverage_overview(conn: sqlite3.Connection) -> dict:
    """Return aggregate imported coverage summary from `file_stats`."""
    try:
        row = conn.execute(
            "SELECT COUNT(*) AS files_with_coverage, "
            "COALESCE(SUM(covered_lines), 0) AS covered_lines, "
            "COALESCE(SUM(coverable_lines), 0) AS coverable_lines "
            "FROM file_stats "
            "WHERE coverable_lines IS NOT NULL AND coverable_lines > 0"
        ).fetchone()
    except Exception:
        return {
            "files_with_coverage": 0,
            "covered_lines": 0,
            "coverable_lines": 0,
            "coverage_pct": None,
        }

    covered = row["covered_lines"] or 0
    coverable = row["coverable_lines"] or 0
    pct = round((covered * 100.0) / coverable, 2) if coverable > 0 else None
    return {
        "files_with_coverage": row["files_with_coverage"] or 0,
        "covered_lines": covered,
        "coverable_lines": coverable,
        "coverage_pct": pct,
    }
