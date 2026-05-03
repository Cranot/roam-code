"""Execute a recipe's command DAG.

Each recipe declares a sequence of (command, args) tuples. The runner
fills ``{symbol}`` / ``{task}`` placeholders from the parsed query,
invokes each command in-process via Click's ``Context.invoke``, and
collects the JSON envelopes for the caller to render.

Symbol extraction is deliberately conservative: we only fill ``{symbol}``
when the query contains exactly one identifier-shaped token. Otherwise
the placeholder is replaced with the original query text and the
command is responsible for resolving / disambiguating.
"""

from __future__ import annotations

import json as _json
import re
import subprocess
import sys
from typing import Any

from roam.ask.recipes import Recipe

_IDENTIFIER_RE = re.compile(r"\b([A-Z][A-Za-z0-9]{2,}|[a-z][a-z0-9]+(?:_[a-z0-9]+)+)\b")


def extract_symbol(query: str) -> str | None:
    """Return the single identifier in *query*, or ``None`` if there are
    zero or more than one.

    PascalCase ("UserSession") and snake_case-with-underscore
    ("handle_login") match. Single lowercase words don't, because they
    have a high false-positive rate in natural-language queries.
    """
    matches = _IDENTIFIER_RE.findall(query or "")
    unique = list(dict.fromkeys(matches))  # preserve order, dedupe
    if len(unique) == 1:
        return unique[0]
    return None


def fill_args(
    template: tuple[str, ...],
    query: str,
    symbol: str | None,
) -> list[str]:
    """Substitute ``{symbol}`` / ``{task}`` placeholders in *template*.

    ``{symbol}`` falls back to the full query when no single identifier
    was extracted. ``{task}`` always uses the full query text.
    """
    out: list[str] = []
    for tok in template:
        if tok == "{symbol}":
            out.append(symbol or query)
        elif tok == "{task}":
            out.append(query)
        else:
            out.append(tok)
    return out


def fill_followups(
    followups: tuple[str, ...],
    query: str,
    symbol: str | None,
) -> list[str]:
    """Substitute ``{symbol}`` / ``{task}`` placeholders in follow-up commands."""
    subject = symbol or query
    return [item.replace("{symbol}", subject).replace("{task}", query) for item in followups]


def run_recipe(
    recipe: Recipe,
    query: str,
    *,
    json_mode: bool = False,
    cwd: str = ".",
) -> list[dict[str, Any]]:
    """Run a recipe's command DAG. Returns one envelope per command.

    Each command is invoked via ``python -m roam --json <cmd> ...`` to
    keep the runner process-isolated. Sequential — no parallelism in
    v12.0; the planner/orchestrator usage is what `roam fleet` is for.
    """
    symbol = extract_symbol(query)
    out: list[dict[str, Any]] = []
    for cmd_name, args_template in recipe.commands:
        args = [sys.executable, "-m", "roam", "--json", cmd_name]
        args.extend(fill_args(args_template, query, symbol))
        try:
            proc = subprocess.run(
                args,
                cwd=cwd,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=180,
            )
        except (subprocess.SubprocessError, OSError) as exc:
            out.append(
                {
                    "command": cmd_name,
                    "error": f"failed to invoke: {exc}",
                    "args": args[3:],
                }
            )
            continue
        try:
            envelope = _json.loads(proc.stdout) if proc.stdout.strip() else {}
        except _json.JSONDecodeError:
            envelope = {
                "command": cmd_name,
                "error": "command produced non-JSON output",
                "stdout_head": proc.stdout[:200],
            }
        if proc.returncode not in (0, 5):  # 5 = critique gate failure
            envelope.setdefault("error", f"exit code {proc.returncode}")
            envelope.setdefault("stderr_head", proc.stderr[:200])
        out.append(envelope)
    return out
