"""Detect benign-default collapse at error boundaries.

This command replaces manual grep sweeps for broad catch blocks, numeric
fallbacks, and shell command substitutions that erase the difference between
an unavailable source and a legitimately empty source.
"""

from __future__ import annotations

import ast
import re
import sqlite3
from pathlib import Path

import click

from roam.capability import roam_capability
from roam.commands.resolve import ensure_index
from roam.db.connection import find_project_root, open_db
from roam.exit_codes import GateFailureError, gate_should_fail
from roam.index.test_conventions import is_test_file
from roam.output.formatter import format_table, json_envelope, to_json

COLLAPSE_DETECTOR_VERSION = "1.0.0"

RULE_CATCH_TO_BENIGN_LITERAL = "catch-to-benign-literal"
RULE_ENOENT_CONFLATION = "enoent-conflation"
RULE_FALLBACK_OR_ZERO = "fallback-or-zero-on-measurement"
RULE_SHELL_ECHO_FALLBACK = "shell-echo-fallback"
RULE_PARSE_FAILURE_MERGES_WITH_EMPTY = "parse-failure-merges-with-empty"

COLLAPSE_RULES: dict[str, dict[str, str]] = {
    RULE_CATCH_TO_BENIGN_LITERAL: {
        "label": "catch returns only a benign literal",
        "repair": "Return a typed failure reason or rethrow after recording the error.",
    },
    RULE_ENOENT_CONFLATION: {
        "label": "unreadable file is treated as absent",
        "repair": "Check the error code and preserve a distinct failure state.",
    },
    RULE_FALLBACK_OR_ZERO: {
        "label": "failed measurement falls back to zero",
        "repair": "Preserve measurement failure separately from numeric zero.",
    },
    RULE_SHELL_ECHO_FALLBACK: {
        "label": "failed shell command echoes a benign literal",
        "repair": "Emit a distinct failure state instead of echoing a benign literal.",
    },
    RULE_PARSE_FAILURE_MERGES_WITH_EMPTY: {
        "label": "invalid input is treated as empty input",
        "repair": "Represent invalid input separately from empty input.",
    },
}

# Five language identifiers are exercised by 8 positive and 12 conservation fixtures.
SUPPORTED_LANGUAGES = ("python", "javascript", "typescript", "tsx", "bash")

_EXTENSION_LANGUAGE = {
    ".py": "python",
    ".pyi": "python",
    ".js": "javascript",
    ".jsx": "javascript",
    ".mjs": "javascript",
    ".cjs": "javascript",
    ".ts": "typescript",
    ".mts": "typescript",
    ".cts": "typescript",
    ".tsx": "tsx",
    ".sh": "bash",
    ".bash": "bash",
    ".zsh": "bash",
}

_SUPPRESSION_RE = re.compile(
    r"roam\s*:\s*ignore-collapse(?:\[(?P<rules>[^\]]+)\])?",
    re.IGNORECASE,
)
_BEST_EFFORT_RE = re.compile(r"best[ -]effort", re.IGNORECASE)
_CACHE_RE = re.compile(r"\bcach(?:e|ed|ing)\b", re.IGNORECASE)

_PY_FILE_CALLS = frozenset(
    {
        "open",
        "io.open",
        "os.open",
        "os.stat",
        "os.lstat",
        "os.access",
        "pathlib.Path.open",
        "pathlib.Path.read_text",
        "pathlib.Path.read_bytes",
    }
)
_PY_FILE_METHODS = frozenset({"open", "read_text", "read_bytes", "stat", "lstat"})
_PY_PARSE_CALLS = frozenset(
    {
        "json.load",
        "json.loads",
        "tomllib.load",
        "tomllib.loads",
        "yaml.load",
        "yaml.safe_load",
        "re.compile",
        "re.fullmatch",
        "re.match",
        "re.search",
    }
)
_PY_MEASUREMENT_CALLS = (
    _PY_FILE_CALLS
    | _PY_PARSE_CALLS
    | frozenset(
        {
            "float",
            "int",
            "decimal.Decimal",
        }
    )
)
_JS_FILE_RE = re.compile(
    r"\b(?:fs\.)?(?:readFile|readFileSync|stat|statSync|lstat|lstatSync|access|accessSync|open|openSync)\s*\(",
)
_JS_PARSE_RE = re.compile(
    r"\b(?:JSON\.parse|parseInt|parseFloat|Number\.parseInt|Number\.parseFloat|RegExp)\s*\(|\.match\s*\(",
)
_JS_MEASUREMENT_RE = re.compile(
    r"\b(?:JSON\.parse|parseInt|parseFloat|Number\.parseInt|Number\.parseFloat|RegExp|"
    r"(?:fs\.)?(?:readFile|readFileSync|stat|statSync|lstat|lstatSync))\s*\(|\.match\s*\(",
)
_JS_EXISTS_RE = re.compile(r"\b(?:fs\.)?(?:existsSync|accessSync)\s*\(")
_SHELL_FALLBACK_RE = re.compile(
    r"\$\(\s*(?P<command>[^\n$()]+?)\s*\|\|\s*echo\s+(?P<default>0|''|\"\")\s*\)",
)


def _line_at(text: str, line: int) -> str:
    lines = text.splitlines()
    if 1 <= line <= len(lines):
        return lines[line - 1].strip()[:160]
    return ""


def _suppressed(text: str, line: int, rule: str, *extra_lines: int) -> bool:
    """Apply the conventional ``roam: ignore-<command>[rule]`` annotation."""
    lines = text.splitlines()
    for candidate in (line, *extra_lines):
        if not 1 <= candidate <= len(lines):
            continue
        for match in _SUPPRESSION_RE.finditer(lines[candidate - 1]):
            raw = (match.group("rules") or "").strip().lower()
            if not raw:
                return True
            rules = {part.strip() for part in raw.split(",") if part.strip()}
            if rule.lower() in rules or "*" in rules:
                return True
    return False


def _is_best_effort_cache(text: str, start_line: int, end_line: int) -> bool:
    lines = text.splitlines()
    start = max(0, start_line - 5)
    end = min(len(lines), end_line + 1)
    window = "\n".join(lines[start:end])
    return bool(_BEST_EFFORT_RE.search(window) and _CACHE_RE.search(window))


def _benign_literal(node: ast.AST | None) -> str | None:
    if isinstance(node, ast.List) and not node.elts:
        return "[]"
    if isinstance(node, ast.Dict) and not node.keys:
        return "{}"
    if isinstance(node, ast.Constant):
        if node.value is None:
            return "None"
        if node.value is False:
            return "False"
        if node.value == 0 and not isinstance(node.value, bool):
            return "0"
        if node.value == "":
            return "''"
    return None


def _call_name(node: ast.Call) -> str:
    parts: list[str] = []
    current: ast.AST = node.func
    while isinstance(current, ast.Attribute):
        parts.append(current.attr)
        current = current.value
    if isinstance(current, ast.Name):
        parts.append(current.id)
    return ".".join(reversed(parts))


def _call_is_file_io(node: ast.Call) -> bool:
    name = _call_name(node)
    return name in _PY_FILE_CALLS or name.rsplit(".", 1)[-1] in _PY_FILE_METHODS


def _call_is_parse(node: ast.Call) -> bool:
    return _call_name(node) in _PY_PARSE_CALLS


def _call_is_measurement(node: ast.Call) -> bool:
    name = _call_name(node)
    return _call_is_file_io(node) or name in _PY_MEASUREMENT_CALLS


def _first_call_subject(nodes: list[ast.stmt], predicate) -> str:
    for root in nodes:
        for node in ast.walk(root):
            if not isinstance(node, ast.Call) or not predicate(node):
                continue
            if node.args:
                arg = node.args[0]
                if isinstance(arg, ast.Name):
                    return arg.id
                if isinstance(arg, ast.Attribute):
                    return arg.attr
            if isinstance(node.func, ast.Attribute) and isinstance(node.func.value, ast.Name):
                return node.func.value.id
            name = _call_name(node).rsplit(".", 1)[-1]
            return name or "source"
    return "source"


def _handler_is_broad(handler: ast.ExceptHandler) -> bool:
    if handler.type is None:
        return True
    names: set[str] = set()
    candidates = handler.type.elts if isinstance(handler.type, ast.Tuple) else [handler.type]
    for candidate in candidates:
        if isinstance(candidate, ast.Name):
            names.add(candidate.id)
        elif isinstance(candidate, ast.Attribute):
            names.add(candidate.attr)
    return bool(names) and names <= {"BaseException", "Exception", "IOError", "OSError"}


def _has_existence_guard(function: ast.AST | None, before_line: int) -> bool:
    if function is None:
        return False
    for node in ast.walk(function):
        if not isinstance(node, ast.If) or not (before_line - 6 <= node.lineno < before_line):
            continue
        for child in ast.walk(node.test):
            if isinstance(child, ast.Call):
                name = _call_name(child)
                if name.endswith((".exists", ".is_file", ".isfile", ".access")) or name in {
                    "exists",
                    "os.access",
                    "os.path.exists",
                    "os.path.isfile",
                }:
                    return True
    return False


def _empty_branch_returns(
    function: ast.AST | None,
    *,
    before_line: int,
    default: str,
    subject: str,
) -> bool:
    if function is None:
        return False
    for node in ast.walk(function):
        if not isinstance(node, ast.If) or node.lineno >= before_line:
            continue
        empty_test = isinstance(node.test, ast.UnaryOp) and isinstance(node.test.op, ast.Not)
        empty_test = empty_test or any(
            isinstance(child, ast.Constant) and child.value in (None, "", 0) for child in ast.walk(node.test)
        )
        if not empty_test:
            continue
        if subject != "source" and re.search(rf"\b{re.escape(subject)}\b", ast.unparse(node.test)) is None:
            continue
        for child in node.body:
            if isinstance(child, ast.Return) and _benign_literal(child.value) == default:
                return True
    return False


def _parents(tree: ast.AST) -> dict[ast.AST, ast.AST]:
    result: dict[ast.AST, ast.AST] = {}
    for parent in ast.walk(tree):
        for child in ast.iter_child_nodes(parent):
            result[child] = parent
    return result


def _enclosing_function(node: ast.AST, parents: dict[ast.AST, ast.AST]) -> ast.AST | None:
    current = parents.get(node)
    while current is not None:
        if isinstance(current, (ast.FunctionDef, ast.AsyncFunctionDef)):
            return current
        current = parents.get(current)
    return None


def _python_catch_findings(file_path: str, text: str, tree: ast.AST) -> list[dict]:
    findings: list[dict] = []
    parents = _parents(tree)
    for try_node in (node for node in ast.walk(tree) if isinstance(node, ast.Try)):
        function = _enclosing_function(try_node, parents)
        for handler in try_node.handlers:
            if not _handler_is_broad(handler) or len(handler.body) != 1:
                continue
            return_node = handler.body[0]
            if not isinstance(return_node, ast.Return):
                continue
            default = _benign_literal(return_node.value)
            if default is None:
                continue
            if _has_existence_guard(function, try_node.lineno):
                continue
            if _is_best_effort_cache(text, try_node.lineno, getattr(handler, "end_lineno", return_node.lineno)):
                continue

            file_io = any(
                isinstance(node, ast.Call) and _call_is_file_io(node)
                for root in try_node.body
                for node in ast.walk(root)
            )
            parse = any(
                isinstance(node, ast.Call) and _call_is_parse(node) for root in try_node.body for node in ast.walk(root)
            )
            if file_io:
                rule = RULE_ENOENT_CONFLATION
                subject = _first_call_subject(try_node.body, _call_is_file_io)
                facts = f"unreadable {subject} returns the same {default} as absent {subject}."
            elif parse:
                subject = _first_call_subject(try_node.body, _call_is_parse)
                if _empty_branch_returns(
                    function,
                    before_line=try_node.lineno,
                    default=default,
                    subject=subject,
                ):
                    rule = RULE_PARSE_FAILURE_MERGES_WITH_EMPTY
                    facts = f"invalid {subject} returns the same {default} as empty {subject}."
                else:
                    rule = RULE_CATCH_TO_BENIGN_LITERAL
                    subject = getattr(function, "name", None) or subject
                    facts = f"failed {subject} returns the same {default} as an empty {subject} result."
            else:
                rule = RULE_CATCH_TO_BENIGN_LITERAL
                subject = getattr(function, "name", None) or _first_call_subject(
                    try_node.body,
                    lambda _node: True,
                )
                facts = f"failed {subject} returns the same {default} as an empty {subject} result."
            if _suppressed(text, return_node.lineno, rule, handler.lineno):
                continue
            findings.append(
                _finding(
                    file_path,
                    return_node.lineno,
                    rule,
                    "high",
                    facts,
                    _line_at(text, return_node.lineno),
                    "python",
                )
            )
    return findings


def _python_fallback_findings(file_path: str, text: str, tree: ast.AST) -> list[dict]:
    findings: list[dict] = []
    parents = _parents(tree)
    for node in (candidate for candidate in ast.walk(tree) if isinstance(candidate, ast.BoolOp)):
        if not isinstance(node.op, ast.Or) or len(node.values) < 2:
            continue
        if _benign_literal(node.values[-1]) != "0":
            continue
        left = node.values[0]
        calls = [child for child in ast.walk(left) if isinstance(child, ast.Call)]
        if not calls or not any(_call_is_measurement(call) for call in calls):
            continue
        parent = parents.get(node)
        if (
            not isinstance(parent, (ast.Assign, ast.AnnAssign, ast.Return))
            or getattr(parent, "value", None) is not node
        ):
            continue
        function = _enclosing_function(node, parents)
        if _has_existence_guard(function, node.lineno):
            continue
        if _is_best_effort_cache(text, node.lineno, getattr(node, "end_lineno", node.lineno)):
            continue
        if _suppressed(text, node.lineno, RULE_FALLBACK_OR_ZERO):
            continue
        call = next(call for call in calls if _call_is_measurement(call))
        subject = _first_call_subject([ast.Expr(value=call)], _call_is_measurement)
        severity = "high" if isinstance(parent, ast.Return) else "medium"
        findings.append(
            _finding(
                file_path,
                node.lineno,
                RULE_FALLBACK_OR_ZERO,
                severity,
                f"unavailable {subject} returns the same 0 as measured zero.",
                _line_at(text, node.lineno),
                "python",
            )
        )
    return findings


def _scan_python(file_path: str, text: str) -> tuple[list[dict], str | None]:
    try:
        tree = ast.parse(text, filename=file_path)
    except (SyntaxError, ValueError) as exc:
        return [], f"{type(exc).__name__}: {exc}"
    findings = _python_catch_findings(file_path, text, tree)
    findings.extend(_python_fallback_findings(file_path, text, tree))
    return findings, None


def _mask_javascript(text: str) -> str:
    """Mask comments and strings while preserving offsets and newlines."""
    chars = list(text)
    state = "code"
    quote = ""
    i = 0
    while i < len(chars):
        char = chars[i]
        nxt = chars[i + 1] if i + 1 < len(chars) else ""
        if state == "code":
            if char in ("'", '"', "`"):
                state, quote = "string", char
                chars[i] = " "
            elif char == "/" and nxt == "/":
                state = "line_comment"
                chars[i] = chars[i + 1] = " "
                i += 1
            elif char == "/" and nxt == "*":
                state = "block_comment"
                chars[i] = chars[i + 1] = " "
                i += 1
        elif state == "string":
            if char == "\\":
                chars[i] = " "
                if i + 1 < len(chars):
                    if chars[i + 1] != "\n":
                        chars[i + 1] = " "
                    i += 1
            elif char == quote:
                chars[i] = " "
                state = "code"
            elif char != "\n":
                chars[i] = " "
        elif state == "line_comment":
            if char == "\n":
                state = "code"
            else:
                chars[i] = " "
        else:
            if char == "*" and nxt == "/":
                chars[i] = chars[i + 1] = " "
                i += 1
                state = "code"
            elif char != "\n":
                chars[i] = " "
        i += 1
    return "".join(chars)


def _strip_js_comments(text: str) -> str:
    return re.sub(r"/\*.*?\*/|//[^\n]*", "", text, flags=re.DOTALL)


def _matching_brace(mask: str, opening: int) -> int | None:
    depth = 0
    for index in range(opening, len(mask)):
        if mask[index] == "{":
            depth += 1
        elif mask[index] == "}":
            depth -= 1
            if depth == 0:
                return index
    return None


def _opening_brace(mask: str, closing: int) -> int | None:
    depth = 0
    for index in range(closing, -1, -1):
        if mask[index] == "}":
            depth += 1
        elif mask[index] == "{":
            depth -= 1
            if depth == 0:
                return index
    return None


def _js_default(block: str) -> tuple[str, int] | None:
    cleaned = _strip_js_comments(block)
    match = re.fullmatch(
        r"\s*return\s+(?P<default>\[\]|\{\}|null|false|0|''|\"\")\s*;?\s*",
        cleaned,
    )
    if match is None:
        return None
    original_match = re.search(
        r"\breturn\s+(?:\[\]|\{\}|null|false|0|''|\"\")",
        block,
    )
    return match.group("default"), original_match.start() if original_match else 0


def _js_subject(source: str, pattern: re.Pattern[str]) -> str:
    match = pattern.search(source)
    if match is None:
        return "source"
    tail = source[match.end() :]
    arg = re.match(r"\s*([A-Za-z_$][\w$]*)", tail)
    if arg is not None:
        return arg.group(1)
    call = source[max(0, match.start() - 50) : match.end()]
    names = re.findall(r"[A-Za-z_$][\w$]*", call)
    return names[-1] if names else "source"


def _js_has_empty_branch(prefix: str, default: str, subject: str) -> bool:
    escaped = re.escape(default)
    empty_if = re.compile(
        rf"if\s*\([^\n)]*(?:!|\.length\b|===?\s*(?:''|\"\"|null|undefined|0))[^)]*\)"
        rf"[\s{{]*return\s+{escaped}\s*;?",
    )
    match = empty_if.search(_strip_js_comments(prefix))
    if match is None:
        return False
    return subject == "source" or re.search(rf"\b{re.escape(subject)}\b", match.group(0)) is not None


def _scan_javascript(file_path: str, text: str, language: str) -> tuple[list[dict], str | None]:
    findings: list[dict] = []
    mask = _mask_javascript(text)
    catch_re = re.compile(r"\bcatch\s*(?:\([^)]*\))?\s*\{")
    for catch_match in catch_re.finditer(mask):
        catch_open = mask.find("{", catch_match.start(), catch_match.end())
        catch_close = _matching_brace(mask, catch_open)
        if catch_close is None:
            continue
        block = text[catch_open + 1 : catch_close]
        default_result = _js_default(block)
        if default_result is None:
            continue
        default, return_offset = default_result
        return_absolute = catch_open + 1 + return_offset
        line = text.count("\n", 0, return_absolute) + 1

        previous_close = catch_match.start() - 1
        while previous_close >= 0 and mask[previous_close].isspace():
            previous_close -= 1
        if previous_close < 0 or mask[previous_close] != "}":
            continue
        try_open = _opening_brace(mask, previous_close)
        if try_open is None or not re.search(r"\btry\s*$", mask[max(0, try_open - 30) : try_open]):
            continue
        try_source = text[try_open + 1 : previous_close]
        try_line = text.count("\n", 0, try_open) + 1
        prefix_lines = text.splitlines()[max(0, try_line - 8) : try_line]
        prefix = "\n".join(prefix_lines)
        if _JS_EXISTS_RE.search(prefix):
            continue
        if _is_best_effort_cache(text, try_line, text.count("\n", 0, catch_close) + 1):
            continue

        if _JS_FILE_RE.search(try_source):
            rule = RULE_ENOENT_CONFLATION
            subject = _js_subject(try_source, _JS_FILE_RE)
            facts = f"unreadable {subject} returns the same {default} as absent {subject}."
        elif _JS_PARSE_RE.search(try_source):
            subject = _js_subject(try_source, _JS_PARSE_RE)
            if _js_has_empty_branch(prefix, default, subject):
                rule = RULE_PARSE_FAILURE_MERGES_WITH_EMPTY
                facts = f"invalid {subject} returns the same {default} as empty {subject}."
            else:
                rule = RULE_CATCH_TO_BENIGN_LITERAL
                facts = f"failed parsing returns the same {default} as an empty parsing result."
        else:
            rule = RULE_CATCH_TO_BENIGN_LITERAL
            call = re.search(r"([A-Za-z_$][\w$]*(?:\.[A-Za-z_$][\w$]*)*)\s*\(", try_source)
            subject = call.group(1).rsplit(".", 1)[-1] if call else "operation"
            facts = f"failed {subject} returns the same {default} as an empty {subject} result."
        catch_line = text.count("\n", 0, catch_match.start()) + 1
        if _suppressed(text, line, rule, catch_line):
            continue
        findings.append(_finding(file_path, line, rule, "high", facts, _line_at(text, line), language))

    lines = text.splitlines()
    for index, raw_line in enumerate(lines, 1):
        code_line = _strip_js_comments(raw_line).strip()
        if not re.search(r"(?:\|\||\?\?)\s*0\s*;?\s*$", code_line):
            continue
        left = re.split(r"\|\||\?\?", code_line, maxsplit=1)[0]
        if not _JS_MEASUREMENT_RE.search(left):
            continue
        if not (left.startswith("return ") or re.match(r"(?:(?:const|let|var)\s+)?[A-Za-z_$][\w$]*\s*=", left)):
            continue
        prefix = "\n".join(lines[max(0, index - 6) : index - 1])
        if _JS_EXISTS_RE.search(prefix):
            continue
        if _is_best_effort_cache(text, index, index):
            continue
        if _suppressed(text, index, RULE_FALLBACK_OR_ZERO):
            continue
        subject = _js_subject(left, _JS_MEASUREMENT_RE)
        severity = "high" if left.startswith("return ") else "medium"
        findings.append(
            _finding(
                file_path,
                index,
                RULE_FALLBACK_OR_ZERO,
                severity,
                f"unavailable {subject} returns the same 0 as measured zero.",
                raw_line.strip()[:160],
                language,
            )
        )
    return findings, None


def _scan_shell(file_path: str, text: str) -> tuple[list[dict], str | None]:
    findings: list[dict] = []
    lines = text.splitlines()
    for line_number, line in enumerate(lines, 1):
        for match in _SHELL_FALLBACK_RE.finditer(line):
            if _suppressed(text, line_number, RULE_SHELL_ECHO_FALLBACK):
                continue
            prefix = "\n".join(lines[max(0, line_number - 4) : line_number])
            if re.search(r"\[\s+-[ef]\s+|\btest\s+-[ef]\s+", prefix):
                continue
            if _is_best_effort_cache(text, line_number, line_number):
                continue
            command = match.group("command").strip().split()[0]
            default = match.group("default")
            default = "''" if default in ("''", '""') else default
            facts = f"failed {command} output returns the same {default} as an empty {command} result."
            findings.append(
                _finding(
                    file_path,
                    line_number,
                    RULE_SHELL_ECHO_FALLBACK,
                    "medium",
                    facts,
                    line.strip()[:160],
                    "bash",
                )
            )
    return findings, None


def _finding(
    file_path: str,
    line: int,
    rule: str,
    severity: str,
    collapsed_facts: str,
    snippet: str,
    language: str,
) -> dict:
    return {
        "file": file_path,
        "line": line,
        "rule": rule,
        "severity": severity,
        "collapsed_facts": collapsed_facts,
        "repair": COLLAPSE_RULES[rule]["repair"],
        "snippet": snippet,
        "language": language,
    }


def _language_for(file_path: str, language: str | None = None) -> str | None:
    if language in SUPPORTED_LANGUAGES:
        return language
    return _EXTENSION_LANGUAGE.get(Path(file_path).suffix.lower())


def _scan_source_with_diagnostics(
    file_path: str,
    text: str,
    language: str | None = None,
) -> tuple[list[dict], str | None]:
    detected = _language_for(file_path, language)
    if detected == "python":
        return _scan_python(file_path, text)
    if detected in {"javascript", "typescript", "tsx"}:
        return _scan_javascript(file_path, text, detected)
    if detected == "bash":
        return _scan_shell(file_path, text)
    return [], None


def scan_source(file_path: str, text: str, language: str | None = None) -> list[dict]:
    """Scan one supported source file and return deterministic findings."""
    findings, _diagnostic = _scan_source_with_diagnostics(file_path, text, language)
    return sorted(findings, key=lambda finding: (finding["line"], finding["rule"]))


def _iter_indexed_files(
    conn,
    project_root: Path,
    file_path: str | None,
    include_tests: bool,
) -> list[tuple[str, str, Path]]:
    rows = conn.execute("SELECT path, language FROM files ORDER BY path").fetchall()
    normalized_filter = (file_path or "").replace("\\", "/")
    while normalized_filter.startswith("./"):
        normalized_filter = normalized_filter[2:]
    normalized_filter = normalized_filter.rstrip("/")
    output: list[tuple[str, str, Path]] = []
    for row in rows:
        rel = str(row["path"] if isinstance(row, sqlite3.Row) else row[0]).replace("\\", "/")
        language = str(row["language"] if isinstance(row, sqlite3.Row) else row[1] or "")
        detected = _language_for(rel, language)
        if detected not in SUPPORTED_LANGUAGES:
            continue
        if normalized_filter and rel != normalized_filter and not rel.startswith(normalized_filter.rstrip("/") + "/"):
            continue
        if not include_tests and is_test_file(rel):
            continue
        output.append((rel, detected, project_root / rel))
    return output


@roam_capability(
    name="collapse",
    category="health",
    summary="Detect error paths that collapse unavailable sources into benign defaults",
    inputs=("repo_path", "file_path"),
    outputs=("findings", "verdict"),
    examples=("roam collapse", "roam collapse --file src", "roam --sarif collapse"),
    tags=("health", "correctness", "error-handling"),
    ai_safe=True,
    requires_index=True,
    maturity="beta",
    mcp_expose=True,
    mcp_preset=("full",),
    side_effect=False,
    task_required=False,
    destructive=False,
    stale_sensitive=True,
    displaces=("grep sweeps for broad catch blocks and benign fallbacks",),
)
@click.command("collapse")
@click.option(
    "--file",
    "file_path",
    default=None,
    type=click.Path(),
    help="Restrict the scan to one file or directory prefix.",
)
@click.option(
    "--include-tests",
    is_flag=True,
    default=False,
    help="Include test files and detector fixtures in the scan.",
)
@click.option(
    "--fail-on-found",
    is_flag=True,
    default=False,
    help="Exit with code 5 when findings exist or the scan is incomplete.",
)
@click.pass_context
def collapse(ctx, file_path: str | None, include_tests: bool, fail_on_found: bool) -> None:
    """Detect unavailable sources collapsed into benign defaults.

    WHEN TO USE: Run before changing persistence, parsing, measurement, or
    recovery code where an unreadable source must stay distinct from an empty
    source. Supports Python, JavaScript, TypeScript, TSX, and shell scripts.
    """
    json_mode = ctx.obj.get("json") if ctx.obj else False
    sarif_mode = ctx.obj.get("sarif") if ctx.obj else False
    budget = ctx.obj.get("budget", 0) if ctx.obj else 0
    ensure_index()
    project_root = find_project_root()

    findings: list[dict] = []
    unreadable_files: list[str] = []
    unparsed_files: list[dict[str, str]] = []
    with open_db(readonly=True) as conn:
        candidates = _iter_indexed_files(conn, project_root, file_path, include_tests)
        for rel, language, full_path in candidates:
            try:
                text = full_path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                unreadable_files.append(rel)
                continue
            file_findings, diagnostic = _scan_source_with_diagnostics(rel, text, language)
            findings.extend(file_findings)
            if diagnostic is not None:
                unparsed_files.append({"file": rel, "reason": diagnostic})

    findings.sort(key=lambda finding: (finding["file"], finding["line"], finding["rule"]))
    high_findings = sum(1 for finding in findings if finding["severity"] == "high")
    medium_findings = sum(1 for finding in findings if finding["severity"] == "medium")
    counts_by_rule = {rule: 0 for rule in COLLAPSE_RULES}
    for finding in findings:
        counts_by_rule[finding["rule"]] += 1

    scan_incomplete = not candidates or bool(unreadable_files) or bool(unparsed_files)
    if not candidates:
        verdict = "collapse scan did not run: 0 supported files"
        state = "no_supported_files"
    elif unreadable_files or unparsed_files:
        verdict = (
            f"{len(findings)} collapse findings with {len(unreadable_files)} unreadable "
            f"and {len(unparsed_files)} unparsed files"
        )
        state = "partial_scan"
    else:
        verdict = f"{len(findings)} collapse findings in {len(candidates)} scanned files"
        state = "completed"

    gate_failed = gate_should_fail(
        fail_on_found,
        findings=findings,
        scan_incomplete=scan_incomplete,
    )

    if sarif_mode:
        from roam.output.sarif import collapse_to_sarif, write_sarif

        disclosures = []
        if scan_incomplete:
            disclosures.append(verdict)
        click.echo(write_sarif(collapse_to_sarif(findings, disclosures=disclosures)))
        if gate_failed:
            raise GateFailureError(verdict)
        return

    if json_mode:
        summary = {
            "verdict": verdict,
            "state": state,
            "total_findings": len(findings),
            "high_findings": high_findings,
            "medium_findings": medium_findings,
            "files_scanned": len(candidates) - len(unreadable_files),
            "supported_files": len(candidates),
            "rules_checked": len(COLLAPSE_RULES),
            "suppression_comment": "roam: ignore-collapse[rule-id]",
            "findings_metric_definition": "Per-occurrence count of distinct collapsed error/default sites.",
        }
        if scan_incomplete:
            summary["partial_success"] = True
        envelope = json_envelope(
            "collapse",
            budget=budget,
            summary=summary,
            rules=[
                {
                    "id": rule,
                    "label": metadata["label"],
                    "repair": metadata["repair"],
                    "count": counts_by_rule[rule],
                }
                for rule, metadata in COLLAPSE_RULES.items()
            ],
            findings=findings,
            supported_languages=list(SUPPORTED_LANGUAGES),
            unreadable_files=unreadable_files,
            unparsed_files=unparsed_files,
            next_steps=["Run `roam collapse --file <path>` to narrow the findings."],
            agent_contract={
                "facts": [
                    f"{len(findings)} benign-default collapse findings",
                    f"{high_findings} return-flow findings",
                    f"{len(candidates) - len(unreadable_files)} scanned files",
                ],
            },
        )
        click.echo(to_json(envelope))
        if gate_failed:
            raise GateFailureError(verdict)
        return

    click.echo(f"VERDICT: {verdict}")
    click.echo()
    rows = [
        [
            finding["severity"].upper(),
            finding["rule"],
            f"{finding['file']}:{finding['line']}",
            finding["collapsed_facts"],
        ]
        for finding in findings
    ]
    if rows:
        click.echo(format_table(["Severity", "Rule", "Location", "Collapsed facts"], rows))
        click.echo()
        for finding in findings:
            click.echo(f"  REPAIR {finding['file']}:{finding['line']}: {finding['repair']}")
    else:
        click.echo("  No benign-default collapse sites detected.")
    if unreadable_files:
        click.echo(f"  NOT SCANNED: {len(unreadable_files)} unreadable files.")
    if unparsed_files:
        click.echo(f"  NOT SCANNED: {len(unparsed_files)} unparsed files.")
    if gate_failed:
        raise GateFailureError(verdict)
