"""Restore-loss detector — flags silent table loss in replace/restore bodies.

Heuristic detector — false negatives expected, false positives should be rare.

This surfaces the bug class where a single function unconditionally deletes a
set of DB tables and then re-inserts only a subset of them. Any table that is
deleted but never re-inserted in the same function is silent data loss.

The detector is deliberately narrow:

- only literal table names are considered;
- only unconditional ``DELETE FROM <table>`` statements count;
- conditional deletes (``WHERE`` present) are ignored;
- delete-only functions are ignored;
- unresolved/dynamic table names are ignored;
- a function must contain both delete and insert activity to fire.

The goal is precision.  If the code cannot prove the table names statically,
the detector stays silent.
"""

from __future__ import annotations

import re
import warnings
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from roam.db.connection import find_project_root
from roam.graph.dark_matter import _extract_sql_tables
from roam.observability import log_swallowed
from roam.world_model.side_effects import SideEffectClassification, classify_side_effects

# ---------------------------------------------------------------------------
# Taxonomy
# ---------------------------------------------------------------------------

RESTORE_LOSS_KINDS = ("silent_data_loss",)


@dataclass
class RestoreLossFinding:
    """Per-symbol restore-loss finding."""

    symbol: str
    file: str
    kind: str = "silent_data_loss"
    deleted_tables: list[str] = field(default_factory=list)
    inserted_tables: list[str] = field(default_factory=list)
    lost_tables: list[str] = field(default_factory=list)
    evidence: dict = field(default_factory=dict)
    confidence: str = "high"
    symbol_id: int = 0
    line_start: int = 0
    line_end: int = 0

    def to_dict(self) -> dict:
        return {
            "symbol": self.symbol,
            "file": self.file,
            "kind": self.kind,
            "deleted_tables": list(self.deleted_tables),
            "inserted_tables": list(self.inserted_tables),
            "lost_tables": list(self.lost_tables),
            "evidence": dict(self.evidence),
            "confidence": self.confidence,
            "line_start": self.line_start,
            "line_end": self.line_end,
        }


# ---------------------------------------------------------------------------
# SQL extraction helpers
# ---------------------------------------------------------------------------

# Match a method call whose first argument is a quoted SQL string.
_SQL_CALL_RE = re.compile(
    r"""\.\s*(?:execute(?:many)?|executescript)\s*\(\s*
        (?:[rubfRUBF]{0,3})?
        (?P<quote>'''|\"\"\"|'|\")
        (?P<sql>.*?)
        (?P=quote)
    """,
    re.DOTALL | re.VERBOSE,
)

_DELETE_ORDER_ASSIGN_RE = re.compile(
    r"""\b(?:DELETE_ORDER|DELETE_TABLES|TABLES_TO_DELETE|TABLES_TO_REMOVE)\b\s*=
        \s*(?P<expr>\[[^\]]*\]|\([^\)]*\)|\{[^\}]*\})
    """,
    re.IGNORECASE | re.DOTALL | re.VERBOSE,
)

_DELETE_ORDER_LOOP_RE = re.compile(
    r"""\bfor\s+\w+\s+in\s+(?:DELETE_ORDER|DELETE_TABLES|TABLES_TO_DELETE|TABLES_TO_REMOVE)\b""",
    re.IGNORECASE,
)

_QUOTED_TABLE_RE = re.compile(r"""['"]([A-Za-z_][A-Za-z0-9_]*)['"]""")


def _read_source(repo_root: Path, rel_path: str) -> tuple[str, list[str]]:
    """Read one source file, preserving empty slices for missing content."""
    try:
        p = repo_root / rel_path
        if not p.exists():
            return "", []
        text = p.read_text(encoding="utf-8", errors="replace")
        return text, text.splitlines(keepends=True)
    except OSError as exc:
        log_swallowed(f"world_model.restore_loss:body_read:{rel_path}", exc)
        return "", []


def _iter_sql_literals(body_text: str) -> list[str]:
    """Return quoted SQL string literals passed to execute-like calls."""
    literals: list[str] = []
    for match in _SQL_CALL_RE.finditer(body_text):
        sql = match.group("sql").strip()
        if not sql:
            continue
        # Dynamic placeholders make the table name unresolved; stay silent.
        if "{" in sql or "}" in sql:
            continue
        literals.append(sql)
    return literals


def _split_sql_statements(sql_text: str) -> list[str]:
    """Split a SQL blob into coarse statements."""
    return [stmt.strip() for stmt in sql_text.split(";") if stmt.strip()]


def _extract_unconditional_delete_tables(sql_text: str) -> set[str]:
    """Extract literal tables from unconditional DELETE statements."""
    tables: set[str] = set()
    for stmt in _split_sql_statements(sql_text):
        upper = stmt.upper()
        if "DELETE FROM" not in upper:
            continue
        if "WHERE" in upper:
            continue
        tables.update(_extract_sql_tables(stmt))
    return tables


def _extract_insert_tables(sql_text: str) -> set[str]:
    """Extract literal tables from INSERT statements."""
    tables: set[str] = set()
    for stmt in _split_sql_statements(sql_text):
        if "INSERT INTO" not in stmt.upper():
            continue
        tables.update(_extract_sql_tables(stmt))
    return tables


def _extract_delete_order_tables(body_text: str) -> set[str]:
    """Resolve explicit delete-order lists when a loop consumes them.

    Only fire when the body shows both:
    - a literal delete-order collection with quoted table names; and
    - a loop over one of the known delete-order variable names.
    """
    if not _DELETE_ORDER_LOOP_RE.search(body_text):
        return set()

    tables: set[str] = set()
    for match in _DELETE_ORDER_ASSIGN_RE.finditer(body_text):
        expr = match.group("expr")
        for table in _QUOTED_TABLE_RE.findall(expr):
            tables.add(table)
    return tables


def _classify_one(se: SideEffectClassification, body_text: str) -> RestoreLossFinding | None:
    """Map a single side-effects record + source body to a finding."""
    if "io_write" not in (se.kinds or []):
        return None

    sql_literals = _iter_sql_literals(body_text)
    delete_tables: set[str] = set()
    insert_tables: set[str] = set()

    for sql in sql_literals:
        delete_tables.update(_extract_unconditional_delete_tables(sql))
        insert_tables.update(_extract_insert_tables(sql))

    # Explicit delete-order lists are a secondary path: only consider them
    # when the function visibly iterates the list and the tables are literal.
    if not delete_tables:
        delete_tables.update(_extract_delete_order_tables(body_text))

    if not delete_tables or not insert_tables:
        return None

    lost_tables = sorted(delete_tables - insert_tables)
    if not lost_tables:
        return None

    evidence = {
        "delete_tables": sorted(delete_tables),
        "insert_tables": sorted(insert_tables),
        "lost_tables": list(lost_tables),
    }

    if _DELETE_ORDER_LOOP_RE.search(body_text) and not any("DELETE FROM" in sql.upper() for sql in sql_literals):
        evidence["delete_order"] = True

    return RestoreLossFinding(
        symbol=se.symbol,
        file=se.file,
        deleted_tables=sorted(delete_tables),
        inserted_tables=sorted(insert_tables),
        lost_tables=lost_tables,
        evidence=evidence,
        confidence="high",
        symbol_id=se.symbol_id,
        line_start=se.line_start,
        line_end=se.line_end,
    )


def classify_restore_loss(
    conn,
    symbol_name: Optional[str] = None,
    limit: Optional[int] = None,
    side_effects: Optional[list[SideEffectClassification]] = None,
) -> list[RestoreLossFinding]:
    """Scan symbols and report silent data-loss restore shapes."""
    if side_effects is None:
        side_effects = classify_side_effects(conn, symbol_name=symbol_name, limit=limit)

    try:
        repo_root = find_project_root()
    except OSError as exc:
        warnings.warn(
            f"find_project_root() failed in classify_restore_loss "
            f"({type(exc).__name__}: {exc}); falling back to Path('.')",
            category=RuntimeWarning,
            stacklevel=2,
        )
        repo_root = Path(".")

    by_file: dict[str, list[SideEffectClassification]] = {}
    for se in side_effects:
        by_file.setdefault(se.file, []).append(se)

    out: list[RestoreLossFinding] = []
    for file_path, items in by_file.items():
        text, lines = _read_source(repo_root, file_path)
        if not text or not lines:
            continue
        for se in items:
            ls = se.line_start or 1
            le = se.line_end or ls
            body = "".join(lines[max(0, ls - 1) : le])
            finding = _classify_one(se, body)
            if finding is not None:
                out.append(finding)

    return out


__all__ = [
    "RESTORE_LOSS_KINDS",
    "RestoreLossFinding",
    "classify_restore_loss",
]
