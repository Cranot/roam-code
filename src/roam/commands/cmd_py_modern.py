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


def _python_files_with_text(conn: sqlite3.Connection):
    rows = conn.execute("SELECT id, path FROM files WHERE language = 'python'").fetchall()
    for r in rows:
        path = r[1]
        try:
            with open(path, encoding="utf-8", errors="replace") as f:
                yield (int(r[0]), path, f.read())
        except OSError:
            continue


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
    ensure_index()
    with open_db(readonly=True) as conn:
        per_file: dict[str, dict] = {}
        totals = {
            "walrus": 0,
            "match_stmt": 0,
            "pep604": 0,
            "pep585": 0,
            "legacy_typing": 0,
            "pep695": 0,
            "fstring": 0,
            "dot_format": 0,
            "files": 0,
        }
        for _file_id, path, text in _python_files_with_text(conn):
            totals["files"] += 1
            counts = {
                "walrus": len(_WALRUS_RE.findall(text)),
                "match_stmt": len(_MATCH_RE.findall(text)),
                "pep604": len(_PEP604_RE.findall(text)),
                "pep585": len(_PEP585_RE.findall(text)),
                "legacy_typing": len(_LEGACY_TYPING_RE.findall(text)),
                "pep695": len(_PEP695_RE.findall(text)),
                "fstring": len(_FSTRING_RE.findall(text)),
                "dot_format": len(_DOTFORMAT_RE.findall(text)),
            }
            for k, v in counts.items():
                totals[k] += v
            if any(counts.values()):
                per_file[path] = counts

        # Modernisation ratio: PEP 604/585 vs legacy typing; f-string vs .format().
        type_total = totals["pep604"] + totals["pep585"] + totals["legacy_typing"]
        type_ratio = (totals["pep604"] + totals["pep585"]) * 100 // type_total if type_total else 0
        format_total = totals["fstring"] + totals["dot_format"]
        format_ratio = totals["fstring"] * 100 // format_total if format_total else 0

        if type_ratio >= 80 and format_ratio >= 80:
            verdict = f"modern Python (type-modern {type_ratio}%, f-string {format_ratio}%)"
        elif type_ratio >= 50 and format_ratio >= 50:
            verdict = f"mixed Python (type-modern {type_ratio}%, f-string {format_ratio}%)"
        else:
            verdict = f"legacy Python (type-modern {type_ratio}%, f-string {format_ratio}%)"

        if json_mode:
            click.echo(
                to_json(
                    json_envelope(
                        "py-modern",
                        summary={
                            "verdict": verdict,
                            "type_modernisation_pct": type_ratio,
                            "fstring_pct": format_ratio,
                            **{k: v for k, v in totals.items() if k != "files"},
                            "files_scanned": totals["files"],
                        },
                        by_file=[
                            {"path": p, **c}
                            for p, c in sorted(per_file.items(), key=lambda kv: -sum(kv[1].values()))[:limit]
                        ],
                    )
                )
            )
            return

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

        if detail and per_file:
            click.echo()
            click.echo(f"Top {min(limit, len(per_file))} files by modern-feature usage:")
            ranked = sorted(per_file.items(), key=lambda kv: -sum(kv[1].values()))[:limit]
            click.echo(
                format_table(
                    ["File", "walrus", "match", "604", "585", "f-str", "legacy", ".format"],
                    [
                        [
                            p,
                            str(c["walrus"]),
                            str(c["match_stmt"]),
                            str(c["pep604"]),
                            str(c["pep585"]),
                            str(c["fstring"]),
                            str(c["legacy_typing"]),
                            str(c["dot_format"]),
                        ]
                        for p, c in ranked
                    ],
                )
            )
