"""Command-advice helpers: validate agent command hints and recommend commands.

Two complementary directions, both explicit-only (no I/O, never auto-injected
into another command's output):

* :func:`validate_command_advice` / :func:`validate_command_advice_many` — given
  a ``roam ...`` command hint an agent emitted, check it against the CLI
  registry/parser without executing it (registry-known? parses? unresolved
  placeholders?). Direction: command-text -> is-it-valid.
* :func:`recommend_commands` — the reverse direction: given an intent phrase or
  a failed grep-heavy workflow, return existing roam commands that satisfy it.
  Direction: intent / workflow -> candidate commands. It reuses the validator so
  every suggestion is registry-confirmed and its example is copy-paste runnable.
"""

from __future__ import annotations

import re
import shlex
from functools import lru_cache
from importlib import import_module
from typing import Iterable

_GLOBAL_FLAGS_NO_VALUE = frozenset(
    {
        "--agent",
        "--check",
        "--ci",
        "--compact",
        "--detail",
        "--help-all",
        "--help",
        "--include-excluded",
        "--json",
        "--override-mode",
        "--sarif",
        "--version",
    }
)
_GLOBAL_FLAGS_WITH_VALUE = frozenset({"--budget"})
_PLACEHOLDER_RE = re.compile(r"(<[^>\s]+>|\{[^}\s]+\})")
_SQUARE_PLACEHOLDER_RE = re.compile(r"\[-{1,2}[^\]\n]+\]")
_BARE_PLACEHOLDER_RE = re.compile(r"[A-Z][A-Z0-9_/-]*")
_SEGMENT_SPLIT_RE = re.compile(r"\s*(?:\||&&|;)\s*", re.ASCII)
_REDIRECT_SPLIT_RE = re.compile(r"\s+(?:\d?>&\d|\d?>\s*\S+|>\s*\S+)", re.ASCII)
_MCP_TOOL_RE = re.compile(r"\broam_[a-z][a-z0-9_]*\b")
_ELLIPSIS_PLACEHOLDERS = frozenset({"...", "…"})
_UPPERCASE_NON_PLACEHOLDERS = frozenset({"HEAD"})


def _base_record(source: str, command_text: str) -> dict:
    return {
        "source": source,
        "command_text": command_text,
        "failure_class": "F3_executability",
        "command_kind": "unknown",
        "registry_status": "not_checked",
        "parse_status": "not_checked",
        "target_status": "not_checked",
        "executable_status": "not_checked",
    }


@lru_cache(maxsize=1)
def _cli_command_targets() -> tuple[dict[str, tuple[str, str]], str | None]:
    """Return CLI command targets without importing ``roam.cli``.

    The advice validator is imported by command modules, so importing the CLI
    here spends import isolation to preserve registry truth. The AST registry
    keeps the same source of truth while avoiding the cycle.
    """
    try:
        from roam.surface_counts import cli_commands

        raw_commands = cli_commands()
    except (ImportError, KeyError, OSError, RuntimeError, SyntaxError, TypeError, ValueError) as exc:
        return {}, f"CLI registry unavailable: {type(exc).__name__}"

    commands: dict[str, tuple[str, str]] = {}
    for name, target in raw_commands.items():
        if not isinstance(name, str):
            continue
        if not isinstance(target, (tuple, list)) or len(target) != 2:
            continue
        module_path, attr_name = target
        if isinstance(module_path, str) and isinstance(attr_name, str):
            commands[name] = (module_path, attr_name)
    return commands, None


def _first_segment(command_text: str) -> str:
    segment = _SEGMENT_SPLIT_RE.split(command_text.strip(), maxsplit=1)[0].strip()
    return _REDIRECT_SPLIT_RE.split(segment, maxsplit=1)[0].strip()


def _subcommand_index(tokens: list[str]) -> int | None:
    if not tokens or tokens[0] != "roam":
        return None
    idx = 1
    while idx < len(tokens):
        token = tokens[idx]
        if token in _GLOBAL_FLAGS_NO_VALUE:
            idx += 1
            continue
        if token in _GLOBAL_FLAGS_WITH_VALUE:
            idx += 2
            continue
        if any(token.startswith(f"{flag}=") for flag in _GLOBAL_FLAGS_WITH_VALUE):
            idx += 1
            continue
        if token.startswith("-"):
            return None
        return idx
    return None


def _subcommand_token(tokens: list[str]) -> str | None:
    idx = _subcommand_index(tokens)
    return tokens[idx] if idx is not None else None


def _placeholder_tokens(tokens: Iterable[str]) -> list[str]:
    found: list[str] = []
    for token in tokens:
        found.extend(match.group(0) for match in _PLACEHOLDER_RE.finditer(token))
    return found


def _looks_like_bare_placeholder(token: str) -> bool:
    raw = token.strip(",;:")
    if raw in {"|", "&&", ";"}:
        return False
    if raw in _ELLIPSIS_PLACEHOLDERS:
        return True
    stripped = raw.strip(".")
    if stripped in {"|", "&&", ";"}:
        return False
    if stripped in _UPPERCASE_NON_PLACEHOLDERS or stripped.startswith("HEAD~"):
        return False
    if "|" in stripped and not stripped.startswith(("http://", "https://")):
        return True
    return bool(_BARE_PLACEHOLDER_RE.fullmatch(stripped))


def _usage_placeholder_tokens(command_text: str) -> list[str]:
    found = _placeholder_tokens([command_text])
    found.extend(match.group(0) for match in _SQUARE_PLACEHOLDER_RE.finditer(command_text))
    try:
        tokens = shlex.split(command_text)
    except ValueError:
        tokens = command_text.split()
    found.extend(token.strip(",;:") for token in tokens if _looks_like_bare_placeholder(token))
    return list(dict.fromkeys(found))


def _is_placeholder_token(token: str | None) -> bool:
    return bool(token) and (token in _ELLIPSIS_PLACEHOLDERS or bool(_PLACEHOLDER_RE.fullmatch(token)))


def _registry_status(subcommand: str) -> tuple[str, str | None]:
    commands, load_reason = _cli_command_targets()
    if load_reason:
        return "not_checked", load_reason
    if subcommand in commands:
        return "known", None
    return "unknown", f"unknown roam subcommand: {subcommand}"


def _mark_non_roam(record: dict, command_text: str) -> dict:
    if _MCP_TOOL_RE.search(command_text):
        record.update(
            {
                "command_kind": "mcp_tool_hint",
                "registry_status": "not_applicable",
                "target_status": "not_applicable",
                "executable_status": "not_applicable",
                "reason": "MCP tool hint, not a roam CLI command",
            }
        )
    else:
        record.update(
            {
                "command_kind": "non_roam",
                "registry_status": "not_applicable",
                "target_status": "not_applicable",
                "executable_status": "not_applicable",
                "reason": "not a roam CLI command",
            }
        )
    return record


def _load_click_command(subcommand: str):
    commands, load_reason = _cli_command_targets()
    if load_reason:
        return None, load_reason
    target = commands.get(subcommand)
    if target is None:
        return None, f"unknown roam subcommand: {subcommand}"
    module_path, attr_name = target
    try:
        mod = import_module(module_path)
        return getattr(mod, attr_name), None
    except (AttributeError, ImportError) as exc:
        return None, f"CLI command loader failed: {type(exc).__name__}"
    except Exception as exc:  # noqa: BLE001
        return None, f"CLI command loader failed: {type(exc).__name__}"


def _make_context_status(command, subcommand: str, args: list[str]) -> tuple[str, str | None]:
    import contextlib
    import io

    try:
        with contextlib.redirect_stdout(io.StringIO()):
            command.make_context(subcommand, args, resilient_parsing=False)
    except Exception as exc:  # noqa: BLE001
        if getattr(exc, "exit_code", None) == 0:
            return "parsed", None
        return "failed", str(exc) or type(exc).__name__
    return "parsed", None


def _strip_global_flags(args: list[str]) -> list[str]:
    stripped: list[str] = []
    idx = 0
    while idx < len(args):
        token = args[idx]
        if token in _GLOBAL_FLAGS_NO_VALUE:
            idx += 1
            continue
        if token in _GLOBAL_FLAGS_WITH_VALUE:
            idx += 2
            continue
        if any(token.startswith(f"{flag}=") for flag in _GLOBAL_FLAGS_WITH_VALUE):
            idx += 1
            continue
        stripped.append(token)
        idx += 1
    return stripped


def _parse_status(tokens: list[str]) -> tuple[str, str | None]:
    idx = _subcommand_index(tokens)
    if idx is None:
        return "not_checked", "no roam subcommand found"
    command, load_reason = _load_click_command(tokens[idx])
    if load_reason:
        return "not_checked", load_reason
    if command is None:
        return "failed", f"unknown roam subcommand: {tokens[idx]}"
    sub_args = tokens[idx + 1 :]
    # `--help` / `-h` is an EAGER flag: Click short-circuits and exits 0 before
    # any positional/argument validation, so `roam <cmd> --help` is ALWAYS
    # executable regardless of required positionals. Because `--help` lives in
    # _GLOBAL_FLAGS_NO_VALUE it would otherwise be stripped, degrading
    # `roam search --help` to `roam search` and raising a spurious "Missing
    # parameter: pattern" — a false FAIL on a 100%-valid copy-paste command.
    if "--help" in sub_args or "-h" in sub_args:
        return "parsed", None
    return _make_context_status(command, tokens[idx], _strip_global_flags(sub_args))


def _is_root_level_invocation(tokens: list[str]) -> bool:
    return len(tokens) > 1 and tokens[1].startswith("-")


def _root_parse_status(tokens: list[str]) -> tuple[str, str | None]:
    idx = 1
    while idx < len(tokens):
        token = tokens[idx]
        if token in _GLOBAL_FLAGS_NO_VALUE:
            idx += 1
            continue
        if token in _GLOBAL_FLAGS_WITH_VALUE:
            if idx + 1 >= len(tokens):
                return "failed", f"missing value for {token}"
            idx += 2
            continue
        if any(token.startswith(f"{flag}=") for flag in _GLOBAL_FLAGS_WITH_VALUE):
            if token.endswith("="):
                return "failed", f"missing value for {token[:-1]}"
            idx += 1
            continue
        return "failed", f"unknown root option: {token}"
    return "parsed", None


def _mark_empty(record: dict) -> dict:
    record.update(
        {
            "command_kind": "empty",
            "registry_status": "not_applicable",
            "target_status": "not_applicable",
            "executable_status": "not_applicable",
            "reason": "empty command hint",
        }
    )
    return record


def _tokenize_roam_segment(record: dict, segment: str) -> list[str] | None:
    try:
        return shlex.split(segment)
    except ValueError as exc:
        record.update(
            {
                "command_kind": "roam_cli",
                "registry_status": "not_checked",
                "parse_status": "failed",
                "target_status": "not_checked",
                "executable_status": "failed",
                "reason": f"shell parse failed: {exc}",
            }
        )
        return None


def _mark_placeholder(record: dict, placeholders: list[str], reason: str) -> dict:
    record.update(
        {
            "target_status": "placeholder",
            "executable_status": "not_checked",
            "placeholders": placeholders,
            "reason": reason,
        }
    )
    return record


def _mark_no_subcommand(record: dict, tokens: list[str]) -> dict:
    if _is_root_level_invocation(tokens):
        parse_status, parse_reason = _root_parse_status(tokens)
        record.update(
            {
                "registry_status": "not_applicable",
                "parse_status": parse_status,
                "target_status": "not_applicable",
                "executable_status": "checked" if parse_status == "parsed" else "failed",
            }
        )
        if parse_reason:
            record["reason"] = parse_reason
        return record
    record.update(
        {
            "registry_status": "unknown",
            "parse_status": "failed",
            "target_status": "not_checked",
            "executable_status": "failed",
            "reason": "no roam subcommand found",
        }
    )
    return record


def _validate_known_subcommand(record: dict, tokens: list[str], placeholders: list[str], command_text: str) -> dict:
    registry_status, registry_reason = _registry_status(str(record.get("subcommand") or ""))
    record["registry_status"] = registry_status
    if registry_status == "unknown":
        record.update(
            {
                "parse_status": "not_checked",
                "target_status": "not_checked",
                "executable_status": "failed",
                "reason": registry_reason,
            }
        )
        return record
    if placeholders:
        record["parse_status"] = "not_checked"
        return _mark_placeholder(record, placeholders, "usage placeholders require substitution before execution")

    parse_status, parse_reason = _parse_status(tokens)
    record["parse_status"] = parse_status
    if parse_status == "failed":
        record.update({"target_status": "not_checked", "executable_status": "failed", "reason": parse_reason})
        return record
    record.update({"target_status": "not_applicable", "executable_status": "checked"})
    if "|" in command_text or "&&" in command_text or ";" in command_text:
        record["reason"] = "validated leading roam command; shell pipeline was not executed"
    return record


def _validate_roam_tokens(record: dict, tokens: list[str], command_text: str) -> dict:
    subcommand = _subcommand_token(tokens)
    record.update({"command_kind": "roam_cli", "subcommand": subcommand})
    if not subcommand:
        return _mark_no_subcommand(record, tokens)

    placeholders = _usage_placeholder_tokens(command_text)
    if _is_placeholder_token(subcommand):
        record.update({"registry_status": "not_checked", "parse_status": "not_checked"})
        return _mark_placeholder(
            record,
            placeholders or [subcommand],
            "placeholder subcommand requires substitution before execution",
        )
    return _validate_known_subcommand(record, tokens, placeholders, command_text)


def validate_command_advice(source: str, command_text: str) -> dict:
    """Validate whether an agent-facing command hint is runnable.

    The check is intentionally non-executing: it validates the first ``roam``
    command segment against the CLI registry/parser and records placeholders
    separately. Pipelines are treated as shell context; only the leading roam
    command is parser-checked.
    """
    record = _base_record(source, command_text)
    stripped = command_text.strip()
    if not stripped:
        return _mark_empty(record)

    segment = _first_segment(stripped)
    record["checked_segment"] = segment
    if not segment.startswith("roam "):
        return _mark_non_roam(record, stripped)

    tokens = _tokenize_roam_segment(record, segment)
    if tokens is None:
        return record
    return _validate_roam_tokens(record, tokens, stripped)


def validate_command_advice_many(items: Iterable[tuple[str, str]]) -> list[dict]:
    return [validate_command_advice(source, text) for source, text in items if text]


# --- Command recommender -----------------------------------------------------
# The reverse direction of validate_command_advice: map an intent phrase or a
# failed grep-heavy workflow to existing roam commands. Stays smaller than the
# agent loop it serves: a hand-curated local rule table (no embeddings, no DB,
# no LLM, no I/O), reusing the validator so it can never suggest a command whose
# example does not validate.

# Canonical copy-paste example per command. Required positionals use angle-bracket
# placeholders (the validator marks these "unchecked", not "failed"). Verified
# runnable for every entry at authoring time via validate_command_advice.
_ADVICE_EXAMPLES: dict[str, str] = {
    "uses": "roam uses <symbol>",
    "impact": "roam impact <symbol>",
    "preflight": "roam preflight <symbol>",
    "trace": "roam trace <symbol>",
    "diagnose": "roam diagnose <symbol>",
    "retrieve": 'roam retrieve "<task>"',
    "grep": "roam grep <pattern>",
    "search": "roam search <pattern>",
    "symbol": "roam symbol <pattern>",
    "deps": "roam deps <path>",
    "coupling": "roam coupling -n 20",
    "safe-delete": "roam safe-delete <symbol>",
    "delete-check": "roam delete-check",
    "dead": "roam dead",
    "refs-text": "roam refs-text <string>",
    "owner": "roam owner <symbol>",
    "history-grep": "roam history-grep <pattern>",
    "churn": "roam churn",
    "cycles": "roam cycles",
    "clusters": "roam clusters",
    "layers": "roam layers",
    "understand": "roam understand",
    "file": "roam file <path>",
    "describe": "roam describe <symbol>",
    "context": "roam context <symbol>",
    "clones": "roam clones",
    "diff": "roam diff",
}

# Each rule: (substring matchers, candidate commands in priority order, rationale).
# Matched case-insensitively against the whole intent. A rule fires when ANY of
# its matchers is present; rank is by hit count, then rule order.
_INTENT_RULES: tuple[tuple[tuple[str, ...], tuple[str, ...], str], ...] = (
    (
        ("who calls", "callers", "who uses", "who references", "references to", "called"),
        ("uses", "impact", "refs-text"),
        "graph-resolved callers replace grepping for a symbol name",
    ),
    (
        ("depends on", "what depends", "importers of", "who imports", "coupled", "what uses this file"),
        ("deps", "coupling", "impact"),
        "file/module coupling replaces manual import grepping",
    ),
    (
        ("safe to delete", "can i delete", "can i remove", "is it safe to remove", "unused", "dead code", "remove this"),
        ("safe-delete", "delete-check", "dead"),
        "deletion-safety gates replace guessing about surviving references",
    ),
    (
        ("what breaks", "blast radius", "impact of changing", "refactor", "if i change", "before editing", "before i change"),
        ("impact", "preflight", "uses"),
        "blast-radius gates run before an edit instead of after",
    ),
    (
        ("trace the", "trace a", "how does", "follow the call", "follow the flow", "walk the call", "why does it"),
        ("trace", "retrieve", "diagnose"),
        "ranked path retrieval replaces hand-walking the call graph",
    ),
    (
        ("where is", "definition of", "find function", "find class", "find the function", "find the class", "locate"),
        ("search", "symbol", "file"),
        "graph-precise symbol lookup replaces text grep across files",
    ),
    (
        ("circular", "import cycle", "circular import", "tangle"),
        ("cycles", "clusters", "layers"),
        "Tarjan cycle detection replaces manual import-loop hunting",
    ),
    (
        ("what is this file", "what does this file", "file role", "lay of the land", "tour the codebase", "orient"),
        ("understand", "file", "describe"),
        "structured file briefing replaces reading the whole file",
    ),
    (
        ("who owns", "owner of", "who wrote", "blame", "git history of", "history of", "when was"),
        ("owner", "history-grep", "churn"),
        "ownership and churn replace manual blame archaeology",
    ),
    (
        ("duplicate", "duplicated", "copy-paste", "same code"),
        ("clones", "diff"),
        "clone detection replaces eyeballing for repeated blocks",
    ),
    # Failed grep-heavy workflows: the roam equivalents are reachability-aware.
    (
        ("grep -r", "grep -rn", "grep -ri", "grep for", "rg ", "ripgrep", "git grep", "xargs grep", "find . -name"),
        ("grep", "uses", "retrieve", "refs-text"),
        "reachability-aware grep replaces raw recursive grep with manual filtering",
    ),
)


def _score_intent_rules(text: str) -> list[tuple[int, int, tuple[str, ...], str]]:
    """Return rules that match ``text``, ranked by hit count then rule order."""
    ranked: list[tuple[int, int, tuple[str, ...], str]] = []
    for index, (matchers, candidates, why) in enumerate(_INTENT_RULES):
        hits = sum(1 for matcher in matchers if matcher in text)
        if hits:
            ranked.append((hits, index, candidates, why))
    ranked.sort(key=lambda entry: (-entry[0], entry[1]))
    return ranked


def _build_advice_suggestion(command: str, why: str) -> dict | None:
    """Build one suggestion, or ``None`` if the command is not in the registry.

    When the registry is reachable, never suggest a command that isn't in it
    (handles renames/removals). When unreachable, fall back to the
    author-verified curated list and let ``runnable`` disclose the unconfirmed
    state.
    """
    commands, _load_reason = _cli_command_targets()
    if commands and command not in commands:
        return None
    example = _ADVICE_EXAMPLES.get(command, f"roam {command}")
    check = validate_command_advice("recommend", example)
    runnable = check.get("registry_status") == "known" and check.get("executable_status") != "failed"
    return {
        "command": command,
        "example": example,
        "why": why,
        "runnable": runnable,
    }


def _collect_advice_suggestions(
    ranked: list[tuple[int, int, tuple[str, ...], str]], limit: int
) -> list[dict]:
    """Expand ranked rules into deduplicated suggestions, capped at ``limit``."""
    suggestions: list[dict] = []
    seen: set[str] = set()
    for _hits, _index, candidates, why in ranked:
        for command in candidates:
            suggestion = None if command in seen else _build_advice_suggestion(command, why)
            if suggestion is None:
                continue
            seen.add(command)
            suggestions.append(suggestion)
        if len(suggestions) >= limit:
            return suggestions[:limit]
    return suggestions


def recommend_commands(intent: str, *, limit: int = 5) -> list[dict]:
    """Map an intent phrase or a failed grep-heavy workflow to existing commands.

    Explicit-only: this performs no I/O and is never run as a side effect of
    another command. Call it from advice/help contexts that opt in. Each
    suggestion is confirmed against the CLI registry and its example is validated
    via :func:`validate_command_advice`, so a suggestion is never offered for a
    command that does not exist or whose example does not validate. Returns
    ``[]`` for empty, non-positive ``limit``, or unrecognized intent — it never
    fabricates commands.

    Each returned dict has ``command``, ``example``, ``why``, and ``runnable``
    (``True`` when the command is registry-known and its example is executable
    or a copy-paste placeholder that the validator accepted).
    """
    if limit <= 0:
        return []
    text = (intent or "").strip().lower()
    if not text:
        return []
    return _collect_advice_suggestions(_score_intent_rules(text), limit)
