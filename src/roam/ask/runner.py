"""Execute a recipe's command DAG.

Each recipe declares a sequence of (command, args) tuples. The runner
fills ``{symbol}`` / ``{task}`` placeholders from the parsed query,
invokes each command via ``python -m roam --json`` for process isolation,
and collects the JSON envelopes for the caller to render.

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

_IDENTIFIER_RE = re.compile(
    # PascalCase / camelCase ≥3 chars
    r"(?:^|[^A-Za-z0-9_])([A-Z][A-Za-z0-9]{2,}"
    # snake_case with optional leading underscore. Without ``_?`` the
    # leading underscore breaks the word-boundary anchor and queries
    # like "is it safe to delete _resolve_file" extracted zero
    # identifiers — the recipe runner then passed the full query
    # text as the symbol name. v12.12.6.
    r"|_?[a-z][a-z0-9]+(?:_[a-z0-9]+)+)"
    r"(?=$|[^A-Za-z0-9_])"
)


def extract_recipe_symbol(query: str) -> str | None:
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


# File/module arguments are a separate shape from code identifiers: file-oriented
# commands (deps, coupling) need the name WITH extension ("formatter.py", not the
# "formatter" stem extract_recipe_symbol would yield — and underscore-less stems
# don't match the identifier regex at all). Extension whitelist avoids matching
# "3.5".
_FILE_RE = re.compile(
    r"(?:^|[^\w./-])"
    r"([\w./-]*[\w-]\.(?:py|pyi|js|jsx|ts|tsx|go|rs|java|rb|php|c|h|hpp|cpp|cc|"
    r"cs|kt|kts|swift|scala|sql|sh|md|rst|ya?ml|toml|json|cfg|ini|html|css))"
    r"(?=$|[^\w/-])"
)


def extract_recipe_file(query: str) -> str | None:
    """Return the single file path/name in *query* (e.g. ``cmd_ask.py`` or
    ``src/x/formatter.py``), or ``None`` if there are zero or more than one."""
    matches = _FILE_RE.findall(query or "")
    unique = list(dict.fromkeys(matches))
    if len(unique) == 1:
        return unique[0]
    return None


def fill_args(
    template: tuple[str, ...],
    query: str,
    symbol: str | None,
    file: str | None = None,
) -> list[str]:
    """Substitute ``{symbol}`` / ``{file}`` / ``{task}`` placeholders.

    ``{symbol}`` falls back to the full query when no single identifier was
    extracted. ``{file}`` prefers the extracted file, then symbol, then query.
    ``{task}`` always uses the full query text.
    """
    out: list[str] = []
    for tok in template:
        if tok == "{symbol}":
            out.append(symbol or query)
        elif tok == "{file}":
            out.append(file or symbol or query)
        elif tok == "{task}":
            out.append(query)
        else:
            out.append(tok)
    return out


def fill_followups(
    followups: tuple[str, ...],
    query: str,
    symbol: str | None,
    file: str | None = None,
) -> list[str]:
    """Substitute ``{symbol}`` / ``{file}`` / ``{task}`` placeholders in follow-ups."""
    subject = symbol or query
    fsub = file or symbol or query
    return [item.replace("{symbol}", subject).replace("{file}", fsub).replace("{task}", query) for item in followups]


def expand_followups(followups: tuple[str, ...], query: str) -> list[str]:
    """Substitute placeholders in *followups*, extracting symbol/file from *query*.

    One-call entry point pairing :func:`extract_recipe_symbol` /
    :func:`extract_recipe_file` with :func:`fill_followups`, so callers build
    rendered follow-ups without orchestrating runner's three primitives
    (the analogue of the extract→fill step ``run_recipe`` does for args).
    """
    return fill_followups(followups, query, extract_recipe_symbol(query), extract_recipe_file(query))


def _arg_present(args: list[str], name: str) -> bool:
    return any(arg == name or arg.startswith(f"{name}=") for arg in args)


def _current_patch_text(cwd: str) -> str | None:
    """Return the current git diff text for commands that consume patch stdin."""
    commands = (
        ["git", "diff", "--no-ext-diff", "HEAD"],
        ["git", "diff", "--no-ext-diff"],
        ["git", "diff", "--cached", "--no-ext-diff"],
    )
    for command in commands:
        try:
            proc = subprocess.run(
                command,
                cwd=cwd,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=30,
            )
        except (subprocess.SubprocessError, OSError):
            continue
        if proc.returncode == 0 and proc.stdout.strip():
            return proc.stdout
    return None


def _stdin_for_command(cmd_name: str, args: list[str], cwd: str) -> str | None:
    if cmd_name != "critique":
        return None
    if _arg_present(args, "--input") or _arg_present(args, "--batch"):
        return None
    return _current_patch_text(cwd)


def _invocation_error(cmd_name: str, args: list[str], exc: BaseException) -> dict[str, Any]:
    return {
        "command": cmd_name,
        "error": f"failed to invoke: {exc}",
        "args": args[3:],
    }


def _invoke_roam_command(args: list[str], cwd: str, stdin_text: str | None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        args,
        cwd=cwd,
        input=stdin_text,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=180,
    )


def _parse_command_envelope(cmd_name: str, proc: subprocess.CompletedProcess[str]) -> dict[str, Any]:
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
    return envelope


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
    symbol = extract_recipe_symbol(query)
    file = extract_recipe_file(query)
    out: list[dict[str, Any]] = []
    for cmd_name, args_template in recipe.commands:
        args = [sys.executable, "-m", "roam", "--json", cmd_name]
        args.extend(fill_args(args_template, query, symbol, file))
        stdin_text = _stdin_for_command(cmd_name, args[5:], cwd)
        try:
            proc = _invoke_roam_command(args, cwd, stdin_text)
        except (subprocess.SubprocessError, OSError) as exc:
            out.append(_invocation_error(cmd_name, args, exc))
            continue
        out.append(_parse_command_envelope(cmd_name, proc))
    return out
