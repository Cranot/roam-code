"""``roam orphan-imports`` — Python imports that don't resolve to a known module.

redactedquick lint that catches:

* ``import x.y`` / ``from x.y import z`` where ``x.y`` is neither in the
  indexed source tree nor a stdlib / installed package.
* Typo'd local imports (e.g. ``from roam.cmds.foo import bar`` when the
  module is ``roam.commands.cmd_foo``).

Strictly Python-only for now. JS/TS/Go orphan-import detection would need
per-language scaffolding; this is the 80/20 starting point.
"""

from __future__ import annotations

import importlib.util
import re
import sys
from pathlib import Path

import click

from roam.commands.resolve import ensure_index
from roam.db.connection import open_db
from roam.output.formatter import json_envelope, to_json

_IMPORT_RE = re.compile(r"^\s*(?:import\s+([\w.]+)|from\s+([\w.]+)\s+import\s+)", re.MULTILINE)
# Skip relative imports — resolution depends on package depth.
_RELATIVE_PREFIX_RE = re.compile(r"^\s*from\s+\.")


def _indexed_modules() -> set[str]:
    """Return dotted-module names derivable from indexed Python files.

    Includes anything under ``src/`` regardless of role classification —
    a path like ``src/roam/index/test_conventions.py`` is a real source
    module even though the filename triggers the test-role classifier.
    Excludes top-level ``tests/`` and ``benchmarks/`` directories.
    """
    out: set[str] = set()
    with open_db(readonly=True) as conn:
        rows = conn.execute(
            """
            SELECT path FROM files
            WHERE language = 'python'
              AND path NOT LIKE 'tests/%'
              AND path NOT LIKE 'tests\\%'
              AND path NOT LIKE 'benchmarks/%'
              AND path NOT LIKE 'benchmarks\\%'
            """
        ).fetchall()
    for r in rows:
        path = Path(r[0])
        # Treat ``src/<root>/...`` and top-level layout. Strip the ``src/``
        # prefix if present so ``src/roam/cli.py`` becomes ``roam.cli``.
        parts = path.with_suffix("").parts
        if parts and parts[0] == "src":
            parts = parts[1:]
        if not parts:
            continue
        if parts[-1] == "__init__":
            parts = parts[:-1]
        if parts:
            out.add(".".join(parts))
            # Also register every prefix: ``roam.commands.cmd_foo`` → ``roam``,
            # ``roam.commands``, ``roam.commands.cmd_foo``.
            for i in range(1, len(parts)):
                out.add(".".join(parts[:i]))
    return out


def _is_external_package(module: str) -> bool:
    """Best-effort check: does Python think this top-level module exists?"""
    head = module.split(".", 1)[0]
    if head in sys.builtin_module_names:
        return True
    try:
        return importlib.util.find_spec(head) is not None
    except Exception:
        return False


@click.command(name="orphan-imports")
@click.pass_context
def orphan_imports(ctx) -> None:
    """List Python imports that don't resolve to any indexed module or installed package."""
    json_mode = ctx.obj.get("json") if ctx.obj else False
    ensure_index()
    with open_db(readonly=True) as conn:
        rows = conn.execute("SELECT path FROM files WHERE language = 'python' ORDER BY path").fetchall()

    indexed = _indexed_modules()
    orphans: list[dict] = []
    files_scanned = 0
    for r in rows:
        rel_path = r[0]
        full = Path(rel_path)
        if not full.is_file():
            continue
        try:
            text = full.read_text(encoding="utf-8")
        except OSError:
            continue
        files_scanned += 1
        for m in _IMPORT_RE.finditer(text):
            # Skip relative imports — they depend on package context which
            # we can't easily resolve without re-parsing.
            line_start = m.start()
            line_end = text.find("\n", line_start)
            line = text[line_start:line_end] if line_end > 0 else text[line_start:]
            if _RELATIVE_PREFIX_RE.match(line):
                continue
            mod = m.group(1) or m.group(2)
            if not mod:
                continue
            if mod in indexed:
                continue
            head = mod.split(".", 1)[0]
            if head in indexed:
                # Top-level package is indexed but the dotted submodule isn't —
                # that's the typo case we care about. Flag it.
                line_no = text.count("\n", 0, line_start) + 1
                orphans.append(
                    {
                        "file": rel_path,
                        "line": line_no,
                        "module": mod,
                        "kind": "internal_typo",
                        "hint": f"top-level package '{head}' is indexed but '{mod}' is not",
                    }
                )
                continue
            if _is_external_package(mod):
                continue
            line_no = text.count("\n", 0, line_start) + 1
            orphans.append(
                {
                    "file": rel_path,
                    "line": line_no,
                    "module": mod,
                    "kind": "missing_package",
                    "hint": "neither indexed nor importable; check spelling or install package",
                }
            )

    verdict = (
        f"OK — no orphan imports across {files_scanned} Python file(s)"
        if not orphans
        else f"{len(orphans)} orphan import(s) across {files_scanned} Python file(s)"
    )

    if json_mode:
        click.echo(
            to_json(
                json_envelope(
                    "orphan-imports",
                    summary={"verdict": verdict, "count": len(orphans), "files_scanned": files_scanned},
                    orphans=orphans[:200],
                )
            )
        )
        return

    click.echo(f"VERDICT: {verdict}")
    if not orphans:
        return
    click.echo()
    click.echo(f"{'File:Line':<55}  {'Kind':<16}  Module")
    click.echo(f"{'-' * 55}  {'-' * 16}  {'-' * 30}")
    for o in orphans[:50]:
        loc = f"{o['file']}:{o['line']}"
        click.echo(f"{loc:<55}  {o['kind']:<16}  {o['module']}")
    if len(orphans) > 50:
        click.echo()
        click.echo(f"… {len(orphans) - 50} more (use --json for the full list)")
