from __future__ import annotations

import re
import shlex
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
    try:
        from roam.cli import _COMMANDS, _DEPRECATED_COMMANDS
    except Exception:  # noqa: BLE001 - validation must never break output
        return "not_checked", "CLI registry unavailable"
    if subcommand in _COMMANDS:
        return "known", None
    if subcommand in _DEPRECATED_COMMANDS:
        return "known_deprecated", None
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
    try:
        from roam.cli import cli
    except Exception as exc:  # noqa: BLE001 - validation must never break output
        return None, f"CLI parser unavailable: {type(exc).__name__}"
    try:
        return cli.get_command(None, subcommand), None
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
    return len(tokens) > 1 and all(token.startswith("-") for token in tokens[1:])


def _root_parse_status(tokens: list[str]) -> tuple[str, str | None]:
    import contextlib
    import io

    try:
        from roam.cli import cli
    except Exception as exc:  # noqa: BLE001
        return "not_checked", f"CLI parser unavailable: {type(exc).__name__}"
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            cli.make_context("roam", tokens[1:], resilient_parsing=False)
        return "parsed", None
    except Exception as exc:  # noqa: BLE001
        if getattr(exc, "exit_code", None) == 0:
            return "parsed", None
        reason = str(exc) or type(exc).__name__
        return "failed", reason


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
