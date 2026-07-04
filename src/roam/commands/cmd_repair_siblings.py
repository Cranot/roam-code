"""Experimental same-repair sibling review lens.

Gate: set ``ROAM_EXPERIMENTAL_REPAIR_SIBLINGS=1`` to expose
``roam repair-siblings``. The command is default-off and intentionally
not part of the static command surface.

Output formats: text (default), ``--json``. SARIF is deliberately NOT
emitted because this command returns ranked review candidates, not
file:line defect findings.
"""

from __future__ import annotations

import math
import re
import sys
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

import click

from roam.capability import roam_capability
from roam.commands.resolve import ensure_index, find_symbol, symbol_not_found_hint
from roam.db.connection import find_project_root, open_db
from roam.output.formatter import format_table, json_envelope, to_json

_FLAG_ENV = "ROAM_EXPERIMENTAL_REPAIR_SIBLINGS"
_FRAMING = (
    "experimental; validated on internal repos; precision review-lens "
    "surfaces same-repair sibling risk with fewer look-alikes; "
    "cross-module slice is weaker; not a defect detector"
)

_HUNK_RE = re.compile(r"@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@")
_TOKEN_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*|==|!=|<=|>=|&&|\|\||[-+*/%]=?|[(){}\[\].,:]")
_CALLEE_RE = re.compile(r"\b([A-Za-z_][A-Za-z0-9_]*)\s*\(")
_PATH_PREFIX_RE = re.compile(r"^[ab]/")
_NON_SIGNAL_LINES = {"", "{", "}", ");", "};", ")", "]", "]}"}
_CALL_KEYWORDS = {
    "if",
    "elif",
    "for",
    "while",
    "switch",
    "catch",
    "with",
    "return",
    "raise",
    "assert",
    "def",
    "class",
    "function",
}
_STRUCTURAL_KEYWORDS = {
    "and",
    "as",
    "assert",
    "await",
    "break",
    "case",
    "catch",
    "continue",
    "elif",
    "else",
    "except",
    "false",
    "for",
    "guard",
    "if",
    "in",
    "is",
    "none",
    "not",
    "null",
    "or",
    "raise",
    "return",
    "switch",
    "true",
    "unless",
    "while",
    "with",
}


@dataclass(frozen=True)
class RepairIntent:
    kind: str
    deleted_pattern: str | None
    added_pattern: str | None
    changed_callees: dict[str, list[str]]

    def to_dict(self) -> dict:
        return {
            "kind": self.kind,
            "deleted_pattern": self.deleted_pattern,
            "added_pattern": self.added_pattern,
            "changed_callees": self.changed_callees,
        }


@dataclass
class PatchSummary:
    old_path: str | None = None
    new_path: str | None = None
    deleted_lines: list[str] | None = None
    added_lines: list[str] | None = None
    changed_old_lines: list[int] | None = None
    changed_new_lines: list[int] | None = None

    def __post_init__(self) -> None:
        self.deleted_lines = [] if self.deleted_lines is None else self.deleted_lines
        self.added_lines = [] if self.added_lines is None else self.added_lines
        self.changed_old_lines = [] if self.changed_old_lines is None else self.changed_old_lines
        self.changed_new_lines = [] if self.changed_new_lines is None else self.changed_new_lines

    def has_changes(self) -> bool:
        return bool(self.deleted_lines or self.added_lines)

    def first_new_line(self) -> int | None:
        if self.changed_new_lines:
            return min(self.changed_new_lines)
        return None


@dataclass(frozen=True)
class SymbolBody:
    id: int
    file_path: str
    name: str
    qualified_name: str | None
    kind: str
    line_start: int | None
    line_end: int | None
    body: str
    lexical_score: float = 0.0

    @property
    def label(self) -> str:
        return self.qualified_name or self.name


@dataclass(frozen=True)
class RankedCandidate:
    symbol: SymbolBody
    score: float
    repair_applicability: float
    reason: str

    def to_dict(self, rank: int) -> dict:
        return {
            "rank": rank,
            "file": self.symbol.file_path,
            "symbol": self.symbol.label,
            "kind": self.symbol.kind,
            "line_start": self.symbol.line_start,
            "line_end": self.symbol.line_end,
            "score": self.score,
            "lexical_score": self.symbol.lexical_score,
            "repair_applicability": self.repair_applicability,
            "reason": self.reason,
        }


def _strip_diff_path(raw: str) -> str | None:
    path = raw.strip().split("\t", 1)[0].split(" ", 1)[0]
    if path == "/dev/null":
        return None
    return _PATH_PREFIX_RE.sub("", path)


def parse_unified_diff(text: str) -> PatchSummary:
    """Return the first changed-file summary in a unified diff."""
    summaries: list[PatchSummary] = []
    current: PatchSummary | None = None
    pending_old: str | None = None
    old_line: int | None = None
    new_line: int | None = None

    for raw_line in text.splitlines():
        if raw_line.startswith("--- "):
            if current is not None and current.has_changes():
                summaries.append(current)
            pending_old = _strip_diff_path(raw_line[4:])
            current = None
            old_line = None
            new_line = None
            continue
        if raw_line.startswith("+++ "):
            current = PatchSummary(old_path=pending_old, new_path=_strip_diff_path(raw_line[4:]))
            continue
        if raw_line.startswith("@@ "):
            if current is None:
                current = PatchSummary()
            match = _HUNK_RE.search(raw_line)
            if match:
                old_line = int(match.group(1))
                new_line = int(match.group(2))
            continue
        if raw_line.startswith("\\"):
            continue
        if raw_line.startswith("-") and not raw_line.startswith("---"):
            if current is None:
                current = PatchSummary()
            current.deleted_lines.append(raw_line[1:])
            if old_line is not None:
                current.changed_old_lines.append(old_line)
                old_line += 1
            if new_line is not None:
                current.changed_new_lines.append(new_line)
            continue
        if raw_line.startswith("+") and not raw_line.startswith("+++"):
            if current is None:
                current = PatchSummary()
            current.added_lines.append(raw_line[1:])
            if new_line is not None:
                current.changed_new_lines.append(new_line)
                new_line += 1
            continue
        if raw_line.startswith(" "):
            if old_line is not None:
                old_line += 1
            if new_line is not None:
                new_line += 1

    if current is not None and current.has_changes():
        summaries.append(current)
    return summaries[0] if summaries else PatchSummary()


def _canonical_line(line: str) -> str:
    return " ".join(line.strip().split())


def _is_meaningful_line(line: str) -> bool:
    stripped = _canonical_line(line)
    if stripped in _NON_SIGNAL_LINES:
        return False
    return not stripped.startswith(("#", "//", "/*", "*"))


def _tokens(text: str) -> list[str]:
    return [tok.lower() for tok in _TOKEN_RE.findall(text)]


def _best_pattern(lines: list[str]) -> str | None:
    meaningful = [_canonical_line(line) for line in lines if _is_meaningful_line(line)]
    if not meaningful:
        return None
    return max(meaningful, key=lambda line: (len(set(_tokens(line))), len(line)))


def _callee_names(lines: list[str]) -> set[str]:
    names: set[str] = set()
    for line in lines:
        stripped = line.strip()
        for match in _CALLEE_RE.finditer(line):
            name = match.group(1)
            if name in _CALL_KEYWORDS:
                continue
            prefix = line[: match.start()].rstrip().split(" ")[-1:]
            if prefix and prefix[0] in {"def", "class", "function"}:
                continue
            if stripped.startswith(("def ", "class ", "function ")) and stripped.split("(", 1)[0].endswith(name):
                continue
            names.add(name)
    return names


def _is_guard_line(line: str) -> bool:
    stripped = _canonical_line(line).lower()
    return stripped.startswith(("if ", "elif ", "unless ", "guard ", "case ")) or stripped.startswith("assert ")


def derive_repair_intent(patch_text: str) -> RepairIntent:
    patch = parse_unified_diff(patch_text)
    deleted = patch.deleted_lines or []
    added = patch.added_lines or []
    deleted_pattern = _best_pattern(deleted)
    added_pattern = _best_pattern(added)
    added_guard_pattern = _best_pattern([line for line in added if _is_guard_line(line)])
    removed_callees = _callee_names(deleted)
    added_callees = _callee_names(added)
    changed = sorted((removed_callees | added_callees) - (removed_callees & added_callees))
    changed_callees = {
        "removed": sorted(removed_callees - added_callees),
        "added": sorted(added_callees - removed_callees),
        "changed": changed,
    }

    if added_guard_pattern:
        kind = "guard_added"
        added_pattern = added_guard_pattern
    elif changed:
        kind = "call_changed"
    elif deleted_pattern and added_pattern:
        kind = "pattern_replaced"
    elif deleted_pattern:
        kind = "pattern_removed"
    elif added_pattern:
        kind = "pattern_added"
    else:
        kind = "unknown"

    return RepairIntent(
        kind=kind,
        deleted_pattern=deleted_pattern,
        added_pattern=added_pattern,
        changed_callees=changed_callees,
    )


def lexical_similarity(left: str, right: str) -> float:
    left_counts = Counter(_tokens(left))
    right_counts = Counter(_tokens(right))
    if not left_counts or not right_counts:
        return 0.0
    dot = sum(count * right_counts.get(tok, 0) for tok, count in left_counts.items())
    left_norm = math.sqrt(sum(count * count for count in left_counts.values()))
    right_norm = math.sqrt(sum(count * count for count in right_counts.values()))
    if not left_norm or not right_norm:
        return 0.0
    return round(dot / (left_norm * right_norm), 4)


def lexical_candidate_generation(
    anchor: SymbolBody,
    candidates: list[SymbolBody],
    *,
    limit: int,
    min_score: float,
) -> list[SymbolBody]:
    scored: list[SymbolBody] = []
    for candidate in candidates:
        if candidate.id == anchor.id:
            continue
        score = lexical_similarity(anchor.body, candidate.body)
        if score < min_score:
            continue
        scored.append(
            SymbolBody(
                id=candidate.id,
                file_path=candidate.file_path,
                name=candidate.name,
                qualified_name=candidate.qualified_name,
                kind=candidate.kind,
                line_start=candidate.line_start,
                line_end=candidate.line_end,
                body=candidate.body,
                lexical_score=score,
            )
        )
    scored.sort(key=lambda item: (-item.lexical_score, item.file_path, item.label))
    return scored[:limit]


def _contains_pattern(body: str, pattern: str | None) -> bool:
    if not pattern:
        return False
    body_norm = "\n".join(_canonical_line(line) for line in body.splitlines())
    pattern_norm = _canonical_line(pattern)
    if not pattern_norm:
        return False
    if pattern_norm in body_norm:
        return True
    pattern_shape = _pattern_shape(pattern_norm)
    for line in body.splitlines():
        if pattern_shape == _pattern_shape(line):
            return True
    pattern_tokens = set(_tokens(pattern_norm))
    if len(pattern_tokens) < 3:
        return False
    return any(pattern_tokens.issubset(set(_tokens(line))) for line in body_norm.splitlines())


def _pattern_shape(text: str) -> tuple[str, ...]:
    raw_tokens = _TOKEN_RE.findall(_canonical_line(text))
    shaped: list[str] = []
    for idx, token in enumerate(raw_tokens):
        lower = token.lower()
        if not re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", token):
            shaped.append(token)
            continue
        prev_token = raw_tokens[idx - 1] if idx > 0 else ""
        next_token = raw_tokens[idx + 1] if idx + 1 < len(raw_tokens) else ""
        if lower in _STRUCTURAL_KEYWORDS or next_token == "(" or prev_token == ".":
            shaped.append(lower)
        else:
            shaped.append("id")
    return tuple(shaped)


def _body_callees(body: str) -> set[str]:
    return _callee_names(body.splitlines())


def evaluate_repair_applicability(intent: RepairIntent, candidate_body: str) -> tuple[float, str]:
    removed_calls = set(intent.changed_callees.get("removed", []))
    added_calls = set(intent.changed_callees.get("added", []))
    candidate_calls = _body_callees(candidate_body)

    deleted_present = _contains_pattern(candidate_body, intent.deleted_pattern) or bool(removed_calls & candidate_calls)
    added_present = _contains_pattern(candidate_body, intent.added_pattern) or bool(added_calls & candidate_calls)

    if intent.kind == "unknown":
        return 0.0, "repair intent too weak"
    if added_present:
        return 0.0, "already contains the added repair pattern"
    if deleted_present:
        return 1.0, "contains the removed pre-fix pattern and lacks the added repair"
    if (
        intent.kind in {"guard_added", "pattern_added"}
        and intent.added_pattern
        and not intent.deleted_pattern
        and not added_present
    ):
        return 0.72, "lacks the added guard or repair pattern"
    return 0.0, "missing the removed pre-fix pattern"


def rerank_by_repair_applicability(
    intent: RepairIntent,
    lexical_candidates: list[SymbolBody],
) -> tuple[list[RankedCandidate], int]:
    ranked: list[RankedCandidate] = []
    suppressed = 0
    for candidate in lexical_candidates:
        applicability, reason = evaluate_repair_applicability(intent, candidate.body)
        if applicability <= 0:
            suppressed += 1
            continue
        score = round((applicability * 0.8) + (candidate.lexical_score * 0.2), 4)
        ranked.append(
            RankedCandidate(
                symbol=candidate,
                score=score,
                repair_applicability=applicability,
                reason=reason,
            )
        )
    ranked.sort(key=lambda item: (-item.repair_applicability, -item.symbol.lexical_score, item.symbol.file_path))
    return ranked, suppressed


def _read_patch_input(path: str) -> str:
    if path == "-":
        return sys.stdin.read()
    return Path(path).read_text(encoding="utf-8", errors="replace")


def _read_symbol_body(root: Path, file_path: str, line_start: int | None, line_end: int | None) -> str:
    path = root / file_path
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return ""
    if not lines:
        return ""
    start = max((line_start or 1) - 1, 0)
    if line_end is None or line_end < (line_start or 1):
        end = min(len(lines), start + 120)
    else:
        end = min(len(lines), line_end, start + 400)
    return "\n".join(lines[start:end])


def _symbol_from_row(row: dict, root: Path, lexical_score: float = 0.0) -> SymbolBody:
    return SymbolBody(
        id=int(row["id"]),
        file_path=row["file_path"],
        name=row["name"],
        qualified_name=row.get("qualified_name"),
        kind=row["kind"],
        line_start=row.get("line_start"),
        line_end=row.get("line_end"),
        body=_read_symbol_body(root, row["file_path"], row.get("line_start"), row.get("line_end")),
        lexical_score=lexical_score,
    )


def _resolve_file_symbol_ref(conn, anchor_ref: str) -> dict | None:
    if "::" not in anchor_ref:
        return None
    file_hint, symbol_name = anchor_ref.split("::", 1)
    file_hint = file_hint.replace("\\", "/")
    if not file_hint or not symbol_name:
        return None
    rows = conn.execute(
        """
        SELECT s.*, f.path AS file_path
        FROM symbols s
        JOIN files f ON s.file_id = f.id
        WHERE (f.path = ? OR f.path LIKE ?)
          AND (s.name = ? OR s.qualified_name = ? OR s.qualified_name LIKE ?)
        ORDER BY
          CASE WHEN f.path = ? THEN 0 ELSE 1 END,
          CASE WHEN s.name = ? THEN 0 ELSE 1 END,
          COALESCE(s.line_start, 999999)
        LIMIT 1
        """,
        (
            file_hint,
            f"%{file_hint}",
            symbol_name,
            symbol_name,
            f"%{symbol_name}",
            file_hint,
            symbol_name,
        ),
    ).fetchone()
    return dict(rows) if rows is not None else None


def _resolve_anchor_ref(conn, root: Path, anchor_ref: str) -> SymbolBody:
    row = _resolve_file_symbol_ref(conn, anchor_ref)
    if row is None:
        resolved = find_symbol(conn, anchor_ref)
        row = dict(resolved) if resolved is not None else None
    if row is None:
        raise click.ClickException(symbol_not_found_hint(anchor_ref))
    return _symbol_from_row(row, root)


def _resolve_anchor_from_patch(conn, root: Path, patch: PatchSummary) -> SymbolBody:
    path = patch.new_path or patch.old_path
    if not path:
        raise click.ClickException("Patch input does not name a changed file. Use --anchor FILE::SYMBOL.")
    line = patch.first_new_line() or 1
    row = conn.execute(
        """
        SELECT s.*, f.path AS file_path
        FROM symbols s
        JOIN files f ON s.file_id = f.id
        WHERE (f.path = ? OR f.path LIKE ?)
          AND COALESCE(s.line_start, 1) <= ?
          AND COALESCE(s.line_end, s.line_start, 1) >= ?
        ORDER BY
          CASE WHEN f.path = ? THEN 0 ELSE 1 END,
          (COALESCE(s.line_end, s.line_start, ?) - COALESCE(s.line_start, ?)) ASC
        LIMIT 1
        """,
        (path, f"%{path}", line, line, path, line, line),
    ).fetchone()
    if row is None:
        row = conn.execute(
            """
            SELECT s.*, f.path AS file_path
            FROM symbols s
            JOIN files f ON s.file_id = f.id
            WHERE f.path = ? OR f.path LIKE ?
            ORDER BY COALESCE(s.line_start, 999999)
            LIMIT 1
            """,
            (path, f"%{path}"),
        ).fetchone()
    if row is None:
        raise click.ClickException(f"Patch anchor file not found in index: {path}")
    return _symbol_from_row(dict(row), root)


def _compatible_kinds(kind: str) -> tuple[str, ...]:
    if kind in {"function", "method"}:
        return ("function", "method")
    return (kind,)


def _load_candidate_symbols(conn, root: Path, anchor: SymbolBody) -> list[SymbolBody]:
    kinds = _compatible_kinds(anchor.kind)
    placeholders = ",".join("?" for _ in kinds)
    rows = conn.execute(
        f"""
        SELECT s.*, f.path AS file_path
        FROM symbols s
        JOIN files f ON s.file_id = f.id
        WHERE s.id != ?
          AND s.kind IN ({placeholders})
          AND s.line_start IS NOT NULL
        ORDER BY f.path, COALESCE(s.line_start, 999999)
        """,
        (anchor.id, *kinds),
    ).fetchall()
    return [_symbol_from_row(dict(row), root) for row in rows]


def _result_rows(results: list[dict]) -> list[list[str]]:
    rows: list[list[str]] = []
    for item in results:
        rows.append(
            [
                str(item["rank"]),
                f"{item['score']:.3f}",
                f"{item['repair_applicability']:.2f}",
                f"{item['file']}:{item.get('line_start') or '?'}",
                item["symbol"],
                item["reason"],
            ]
        )
    return rows


def _build_verdict(result_count: int, suppressed_count: int) -> str:
    return (
        f"experimental repair-siblings lens ranked {result_count} candidates "
        f"({suppressed_count} look-alikes suppressed); not a defect detector"
    )


def _patch_path(input_path: str | None, diff_path: str | None) -> str:
    if input_path and diff_path and input_path != diff_path:
        raise click.UsageError("Use either --input or --diff, not both.")
    path = input_path or diff_path
    if not path:
        raise click.UsageError("Provide --input PATCH or --anchor FILE::SYMBOL --diff PATCH.")
    return path


@roam_capability(
    name="repair-siblings",
    category="refactoring",
    summary="Experimental precision review lens for same-repair sibling candidates",
    inputs=("patch", "anchor"),
    outputs=("ranked_candidates", "repair_intent"),
    examples=(
        "ROAM_EXPERIMENTAL_REPAIR_SIBLINGS=1 roam repair-siblings --input fix.patch",
        "ROAM_EXPERIMENTAL_REPAIR_SIBLINGS=1 roam repair-siblings --anchor src/app.py::handle --diff fix.patch",
    ),
    tags=("experimental", "review-lens", "repair-intent"),
    maturity="experimental",
    mcp_expose=False,
    side_effect=False,
    task_required=False,
    destructive=False,
    stale_sensitive=True,
    ai_safe=True,
    requires_index=True,
)
@click.command("repair-siblings")
@click.option(
    "--input",
    "input_path",
    type=str,
    default=None,
    help="Unified diff/patch path for the validated fix ('-' reads stdin).",
)
@click.option(
    "--diff",
    "diff_path",
    type=str,
    default=None,
    help="Unified diff/patch path used with --anchor ('-' reads stdin).",
)
@click.option(
    "--anchor",
    "anchor_ref",
    default=None,
    help="Anchor symbol as FILE::SYMBOL. Without this, the first changed symbol in the patch is used.",
)
@click.option("--top-n", default=10, show_default=True, type=click.IntRange(1, 100), help="Ranked candidates to show.")
@click.option(
    "--candidate-limit",
    default=50,
    show_default=True,
    type=click.IntRange(1, 500),
    help="Top lexical candidates to rerank.",
)
@click.option(
    "--min-lexical",
    default=0.05,
    show_default=True,
    type=click.FloatRange(0.0, 1.0),
    help="Minimum lexical similarity for the candidate pool.",
)
@click.pass_context
def repair_siblings_cmd(ctx, input_path, diff_path, anchor_ref, top_n, candidate_limit, min_lexical):
    """Experimental precision review-lens for same-repair sibling risk.

    Enable with ROAM_EXPERIMENTAL_REPAIR_SIBLINGS=1. This is not a defect
    detector: it ranks review candidates whose code is lexically similar to
    the anchor and still appears repair-applicable.
    """
    json_mode = bool(ctx.obj and ctx.obj.get("json"))
    patch_text = _read_patch_input(_patch_path(input_path, diff_path))
    patch = parse_unified_diff(patch_text)
    intent = derive_repair_intent(patch_text)

    ensure_index()
    root = find_project_root()
    with open_db(readonly=True) as conn:
        anchor = _resolve_anchor_ref(conn, root, anchor_ref) if anchor_ref else _resolve_anchor_from_patch(conn, root, patch)
        raw_candidates = _load_candidate_symbols(conn, root, anchor)

    lexical_candidates = lexical_candidate_generation(
        anchor,
        raw_candidates,
        limit=candidate_limit,
        min_score=min_lexical,
    )
    ranked, suppressed_count = rerank_by_repair_applicability(intent, lexical_candidates)
    shown = ranked[:top_n]
    result_dicts = [candidate.to_dict(rank) for rank, candidate in enumerate(shown, start=1)]
    verdict = _build_verdict(len(result_dicts), suppressed_count)
    anchor_payload = {
        "file": anchor.file_path,
        "symbol": anchor.label,
        "kind": anchor.kind,
        "line_start": anchor.line_start,
        "line_end": anchor.line_end,
    }

    if json_mode:
        click.echo(
            to_json(
                json_envelope(
                    "repair-siblings",
                    summary={
                        "verdict": verdict,
                        "experimental": True,
                        "default_off_flag": f"{_FLAG_ENV}=1",
                        "candidate_count": len(result_dicts),
                        "lexical_candidate_count": len(lexical_candidates),
                        "suppressed_count": suppressed_count,
                        "framing": _FRAMING,
                        "partial_success": intent.kind == "unknown",
                    },
                    anchor=anchor_payload,
                    repair_intent=intent.to_dict(),
                    candidates=result_dicts,
                    agent_contract={
                        "facts": [
                            f"{len(result_dicts)} ranked sibling items",
                            f"{len(lexical_candidates)} lexical candidate symbols",
                            f"{suppressed_count} suppressed look-alike items",
                        ],
                        "next_commands": ["roam repair-siblings --help"],
                    },
                )
            )
        )
        return

    click.echo(f"VERDICT: {verdict}")
    click.echo(f"Frame: {_FRAMING}")
    click.echo(f"Flag: {_FLAG_ENV}=1")
    click.echo(f"Anchor: {anchor.file_path}:{anchor.line_start or '?'} {anchor.label}")
    click.echo(
        "Intent: "
        f"kind={intent.kind}; "
        f"deleted={intent.deleted_pattern or '-'}; "
        f"added={intent.added_pattern or '-'}"
    )
    click.echo()
    if result_dicts:
        click.echo(format_table(["rank", "score", "applic", "location", "symbol", "reason"], _result_rows(result_dicts)))
    else:
        click.echo("(no repair-applicable candidates in lexical pool)")
