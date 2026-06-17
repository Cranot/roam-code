"""``roam py-modern`` — modern-Python adoption signal.

Reports usage of post-3.6 Python features so an agent can quickly
gauge how modernised a codebase is and where to focus migration
sprints. Counterpart to ``roam py-types`` which focuses on annotation
coverage.

Signals tracked:

* **Walrus operator (PEP 572, Py3.8)** — ``:=`` usage count.
* **Match statement (PEP 634, Py3.10)** — ``match X:`` blocks.
* **PEP 604 union syntax (Py3.10)** — ``X | None`` vs
  ``Optional[X]``.
* **PEP 585 generic syntax (Py3.9)** — ``dict[str, int]`` vs
  ``Dict[str, int]``.
* **Type aliases (PEP 695, Py3.12)** — ``type Vector = list[float]``.
* **f-strings vs ``.format()``** — modern format adoption ratio.
* **Async functions** — ``async def`` count (already in is_async col).

Read from source text via ``catalog/python_idioms._file_text`` so we
don't need a new schema. Per-file breakdown via ``--detail``.
"""

from __future__ import annotations

import re
import sqlite3

import click

from roam.capability import roam_capability
from roam.commands.resolve import ensure_index
from roam.db.connection import open_db
from roam.output.formatter import format_table, json_envelope, to_json

# All length-preserving so we can attribute per-symbol.
_WALRUS_RE = re.compile(r":=")
_MATCH_RE = re.compile(r"^\s*match\s+\w[\w.\[\]]*\s*:", re.MULTILINE)
# PEP 604: ``X | None`` / ``int | str`` in type contexts (param/return)
_PEP604_RE = re.compile(r"->\s*\w[\w.\[\]]*\s*\|\s*\w|:\s*\w[\w.\[\]]*\s*\|\s*\w")
# PEP 585: ``list[`` / ``dict[`` / ``tuple[`` / ``set[`` / ``frozenset[``
# at type position (after ``:`` or ``->``)
_PEP585_RE = re.compile(r"(?:->|:)\s*(?:list|dict|tuple|set|frozenset|type)\[")
# Legacy typing.X[] form (counts the OPPOSITE)
_LEGACY_TYPING_RE = re.compile(r"\b(Optional|Dict|List|Set|Tuple|FrozenSet|Type)\[")
# PEP 695 type alias: ``type Name = ...``
_PEP695_RE = re.compile(r"^\s*type\s+\w+\s*=", re.MULTILINE)
# f-strings
_FSTRING_RE = re.compile(r"\bf['\"]")
# .format() calls
_DOTFORMAT_RE = re.compile(r"['\"]\s*\.format\s*\(")
_FEATURE_KEYS = (
    "walrus",
    "match_stmt",
    "pep604",
    "pep585",
    "legacy_typing",
    "pep695",
    "fstring",
    "dot_format",
)


def _python_files_with_text(conn: sqlite3.Connection):
    rows = conn.execute("SELECT id, path FROM files WHERE language = 'python'").fetchall()
    for r in rows:
        path = r[1]
        try:
            with open(path, encoding="utf-8", errors="replace") as f:
                yield (int(r[0]), path, f.read())
        except OSError:
            continue


def _empty_totals() -> dict[str, int]:
    totals = {key: 0 for key in _FEATURE_KEYS}
    totals["files"] = 0
    return totals


def _feature_counts(text: str) -> dict[str, int]:
    return {
        "walrus": len(_WALRUS_RE.findall(text)),
        "match_stmt": len(_MATCH_RE.findall(text)),
        "pep604": len(_PEP604_RE.findall(text)),
        "pep585": len(_PEP585_RE.findall(text)),
        "legacy_typing": len(_LEGACY_TYPING_RE.findall(text)),
        "pep695": len(_PEP695_RE.findall(text)),
        "fstring": len(_FSTRING_RE.findall(text)),
        "dot_format": len(_DOTFORMAT_RE.findall(text)),
    }


def _add_counts(totals: dict[str, int], counts: dict[str, int]) -> None:
    for key, value in counts.items():
        totals[key] += value


def _regex_occurrences(path: str, text: str, regex: re.Pattern, kind: str) -> list[dict]:
    return [
        {
            "path": path,
            "line": text.count("\n", 0, match.start()) + 1,
            "kind": kind,
            "match": match.group(0).strip(),
        }
        for match in regex.finditer(text)
    ]


def _legacy_occurrences_for_file(path: str, text: str) -> list[dict]:
    occurrences = _regex_occurrences(path, text, _LEGACY_TYPING_RE, "legacy-typing")
    occurrences.extend(_regex_occurrences(path, text, _DOTFORMAT_RE, "dot-format"))
    return occurrences


def _scan_modern_python(conn: sqlite3.Connection) -> tuple[dict[str, dict], dict[str, int], list[dict]]:
    per_file: dict[str, dict] = {}
    totals = _empty_totals()
    legacy_occurrences: list[dict] = []

    for _file_id, path, text in _python_files_with_text(conn):
        totals["files"] += 1
        counts = _feature_counts(text)
        _add_counts(totals, counts)
        if any(counts.values()):
            per_file[path] = counts
        legacy_occurrences.extend(_legacy_occurrences_for_file(path, text))

    return per_file, totals, legacy_occurrences


def _pct(numerator: int, denominator: int) -> int:
    return numerator * 100 // denominator if denominator else 0


def _modern_ratios(totals: dict[str, int]) -> tuple[int, int]:
    type_total = totals["pep604"] + totals["pep585"] + totals["legacy_typing"]
    format_total = totals["fstring"] + totals["dot_format"]
    return _pct(totals["pep604"] + totals["pep585"], type_total), _pct(totals["fstring"], format_total)


def _modern_verdict(type_ratio: int, format_ratio: int) -> str:
    if type_ratio >= 80 and format_ratio >= 80:
        label = "modern Python"
    elif type_ratio >= 50 and format_ratio >= 50:
        label = "mixed Python"
    else:
        label = "legacy Python"
    return f"{label} (type-modern {type_ratio}%, f-string {format_ratio}%)"


def _ranked_files(per_file: dict[str, dict], limit: int | None = None) -> list[tuple[str, dict]]:
    ranked = sorted(per_file.items(), key=lambda kv: -sum(kv[1].values()))
    return ranked[:limit] if limit is not None else ranked


def _legacy_sample(legacy_occurrences: list[dict], limit: int) -> list[dict]:
    return sorted(legacy_occurrences, key=lambda item: (item["path"], item["line"]))[: limit * 5]


def _py_modern_json(verdict, type_ratio, format_ratio, totals, per_file, legacy_occurrences, detail, limit):
    return json_envelope(
        "py-modern",
        summary={
            "verdict": verdict,
            "type_modernisation_pct": type_ratio,
            "fstring_pct": format_ratio,
            **{key: value for key, value in totals.items() if key != "files"},
            "files_scanned": totals["files"],
        },
        by_file=[{"path": path, **counts} for path, counts in _ranked_files(per_file, limit)],
        legacy_occurrences=_legacy_sample(legacy_occurrences, limit) if detail else [],
    )


def _py_modern_sarif(per_file, type_ratio):
    from roam.output.sarif import py_modern_to_sarif, write_sarif

    by_file_list = [{"path": path, **counts} for path, counts in _ranked_files(per_file)]
    return write_sarif(py_modern_to_sarif(by_file_list, type_ratio))


def _emit_py_modern_detail(per_file, legacy_occurrences, limit):
    if not per_file:
        return

    click.echo()
    click.echo(f"Top {min(limit, len(per_file))} files by modern-feature usage:")
    click.echo(
        format_table(
            ["File", "walrus", "match", "604", "585", "f-str", "legacy", ".format"],
            [
                [
                    path,
                    str(counts["walrus"]),
                    str(counts["match_stmt"]),
                    str(counts["pep604"]),
                    str(counts["pep585"]),
                    str(counts["fstring"]),
                    str(counts["legacy_typing"]),
                    str(counts["dot_format"]),
                ]
                for path, counts in _ranked_files(per_file, limit)
            ],
        )
    )
    _emit_legacy_occurrences(legacy_occurrences, limit)


def _emit_legacy_occurrences(legacy_occurrences, limit):
    if not legacy_occurrences:
        return

    click.echo()
    click.echo("Legacy occurrences (file:line, kind, match):")
    click.echo(
        format_table(
            ["Location", "Kind", "Match"],
            [
                [f"{item['path']}:{item['line']}", item["kind"], item["match"]]
                for item in _legacy_sample(legacy_occurrences, limit)
            ],
        )
    )


def _emit_py_modern_text(verdict, totals, type_ratio, format_ratio, detail, per_file, legacy_occurrences, limit):
    click.echo(f"VERDICT: {verdict}\n")
    click.echo(f"  files scanned:        {totals['files']}")
    click.echo()
    click.echo("Modern features (higher is better):")
    click.echo(f"  walrus (:=):          {totals['walrus']}")
    click.echo(f"  match statements:     {totals['match_stmt']}")
    click.echo(f"  PEP 604 (X | None):   {totals['pep604']}")
    click.echo(f"  PEP 585 (dict[…]):    {totals['pep585']}")
    click.echo(f"  PEP 695 (type aliases): {totals['pep695']}")
    click.echo(f"  f-strings:            {totals['fstring']}")
    click.echo()
    click.echo("Legacy features (migration candidates):")
    click.echo(f"  typing.Optional/Dict/List…: {totals['legacy_typing']}")
    click.echo(f'  ``".format(…)``:        {totals["dot_format"]}')
    click.echo()
    click.echo(f"Type modernisation:   {type_ratio}% (PEP 604/585 vs legacy)")
    click.echo(f"f-string adoption:    {format_ratio}% (f-string vs .format)")

    if detail:
        _emit_py_modern_detail(per_file, legacy_occurrences, limit)


@roam_capability(
    name="py-modern",
    category="health",
    summary="Modern-Python adoption: walrus, match, PEP 604/585/695, f-strings",
    maturity="stable",
    mcp_expose=True,
    mcp_preset=("core",),
    side_effect=False,
    task_required=False,
    destructive=False,
    stale_sensitive=True,
    ai_safe=True,
    requires_index=True,
)
@click.command("py-modern")
@click.option("--detail", is_flag=True, help="Per-file breakdown of feature usage")
@click.option("--top", "limit", default=10, type=int, show_default=True, help="Files to show in --detail mode")
@click.pass_context
def py_modern(ctx, detail, limit):
    """Modern-Python adoption: walrus, match, PEP 604/585/695, f-strings.

    Use this to gauge how modernised a codebase is and where to focus
    migration sprints. Counterpart to ``roam py-types`` which scores
    annotation coverage.
    """
    json_mode = ctx.obj.get("json") if ctx.obj else False
    detail = bool(detail or (ctx.obj.get("detail", False) if ctx.obj else False))
    ensure_index()
    with open_db(readonly=True) as conn:
        per_file, totals, legacy_occurrences = _scan_modern_python(conn)
        type_ratio, format_ratio = _modern_ratios(totals)
        verdict = _modern_verdict(type_ratio, format_ratio)

        if json_mode:
            envelope = _py_modern_json(
                verdict, type_ratio, format_ratio, totals, per_file, legacy_occurrences, detail, limit
            )
            click.echo(to_json(envelope))
            return

        sarif_mode = ctx.obj.get("sarif") if ctx.obj else False
        if sarif_mode:
            click.echo(_py_modern_sarif(per_file, type_ratio))
            return

        _emit_py_modern_text(verdict, totals, type_ratio, format_ratio, detail, per_file, legacy_occurrences, limit)
