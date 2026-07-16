"""Privacy-preserving historical episode extraction from agent transcripts.

The extractor reads raw Claude Code or Codex JSONL locally and emits a compact
derived snapshot. Raw prompts, responses, command values, paths, and tool
arguments never enter the snapshot. Sanitized shell templates retain the
executable, safe subcommand/flags, and control-flow shape. Historical episodes
are discovery evidence only; they cannot satisfy the prospective measurement
gate in :mod:`roam.savings`.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import shlex
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any, Iterable

BACKFILL_VERSION = 5
MAX_TRANSCRIPT_BYTES = 50 * 1024 * 1024
OUTPUT_NAME = "transcript-episodes.jsonl"
SALT_NAME = "savings-backfill.key"

_TOKEN_RE = re.compile(r"[A-Za-z0-9_./\\:-]+")
_CORRECTION_RE = re.compile(
    r"^\s*(no\b|stop\b|wait\b|actually\b|don'?t\b|instead\b|not that|"
    r"that'?s wrong|revert|undo|why did|you (missed|forgot|broke)|try again)",
    re.IGNORECASE,
)
_SYSTEM_BLOCK_RE = re.compile(
    r"<(system-reminder|local-command-caveat|command-name|command-message)>.*?</\1>",
    re.IGNORECASE | re.DOTALL,
)
_VERIFY_RE = re.compile(
    r"\b(pytest|unittest|jest|vitest|mocha|cargo\s+test|go\s+test|dotnet\s+test|"
    r"mvn\s+test|gradle\s+test|npm\s+(run\s+)?test|pnpm\s+(run\s+)?test|"
    r"yarn\s+test|ruff|eslint|mypy|pyright|tsc|typecheck|lint|check\.py|"
    r"roam\s+verify|prepush|pre-commit)\b",
    re.IGNORECASE,
)
_BUILD_RE = re.compile(
    r"\b(npm|pnpm|yarn)\s+(run\s+)?build\b|\b(cargo|go|mvn|gradle|dotnet)\s+build\b",
    re.IGNORECASE,
)
_SEARCH_RE = re.compile(r"\b(rg|grep|git\s+grep|findstr|select-string)\b", re.IGNORECASE)
_FORMAT_RE = re.compile(r"\b(black|prettier|ruff\s+format|gofmt|rustfmt)\b", re.IGNORECASE)
_INSPECT_RE = re.compile(
    r"\b(sed\s+-n|nl\s+-ba|head\b|tail\b|cat\b|less\b|more\b|"
    r"get-content\b|select-object\s+-(?:first|last|skip))",
    re.IGNORECASE,
)
_ORIENT_RE = re.compile(
    r"\b(pwd|tree|git\s+(?:status|branch|log|rev-parse|remote)|"
    r"get-location|get-childitem|ls|dir)\b",
    re.IGNORECASE,
)
_DIFF_RE = re.compile(r"\b(git\s+diff|roam\s+(?:diff|critique))\b", re.IGNORECASE)
_VCS_WRITE_RE = re.compile(
    r"\bgit\s+(?:add|commit|push|merge|rebase|cherry-pick|tag)\b",
    re.IGNORECASE,
)
_DEPLOY_RE = re.compile(
    r"\b(kubectl|helm|terraform|ansible|systemctl|docker\s+(?:compose|run|build|push)|"
    r"ssh|scp|rsync)\b",
    re.IGNORECASE,
)
_DEPENDENCY_RE = re.compile(
    r"\b(pip|pip3|uv|poetry|npm|pnpm|yarn|cargo|go)\s+"
    r"(?:install|add|remove|update|get|mod|sync)\b",
    re.IGNORECASE,
)
_PROJECTION_RE = re.compile(
    r"(?:\|\s*(?:jq|python(?:3)?|head|tail|sed|awk|cut|select-object)\b|head\s+-c\b)",
    re.IGNORECASE,
)
_STRUCTURED_PROJECTION_RE = re.compile(
    r"(?:^|(?:&&|\|\||;)\s*)roam\b[^;]*"
    r"(?:\|\s*(?:jq|python(?:3)?|head|tail|sed|awk|cut|select-object)\b|head\s+-c\b)",
    re.IGNORECASE,
)
_SLICE_TEMPLATE_RE = re.compile(
    r"\b(?:sed\s+-n|nl\s+-ba|head\b|tail\b|get-content\b|"
    r"select-object\s+-(?:first|last|skip))",
    re.IGNORECASE,
)
_HELP_RE = re.compile(r"(?:^|\s)--help(?:\s|$)|(?:^|\s)-h(?:\s|$)", re.IGNORECASE)
_EXIT_CODE_RE = re.compile(
    r"(?:process exited with code|exit code|return code)\s*[:=]?\s*(-?\d+)",
    re.I,
)
_FAILURE_CLASSIFIERS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "invalid_invocation",
        re.compile(
            r"\b(unrecognized arguments?|unknown option|invalid option|no such option|"
            r"unexpected argument|usage error)\b",
            re.I,
        ),
    ),
    (
        "command_unavailable",
        re.compile(
            r"\b(command not found|not recognized as an internal or external command|"
            r"executable file not found|could not find executable)\b",
            re.I,
        ),
    ),
    (
        "dependency_unavailable",
        re.compile(
            r"\b(modulenotfounderror|no module named|cannot find module|"
            r"package .* not found|missing dependency|could not import)\b",
            re.I,
        ),
    ),
    (
        "path_unavailable",
        re.compile(
            r"\b(no such file or directory|file not found|path not found|"
            r"cannot find the (?:file|path)|does not exist)\b",
            re.I,
        ),
    ),
    (
        "permission_or_auth",
        re.compile(
            r"\b(permission denied|access denied|unauthorized|forbidden|"
            r"authentication failed|not authorized)\b",
            re.I,
        ),
    ),
    (
        "timeout",
        re.compile(
            r"\b(timed? out|timeout expired|deadline exceeded)\b",
            re.I,
        ),
    ),
    (
        "network",
        re.compile(
            r"\b(connection refused|network is unreachable|could not resolve|"
            r"name resolution|dns failure|connection reset)\b",
            re.I,
        ),
    ),
    (
        "resource_exhausted",
        re.compile(
            r"\b(no space left|out of memory|memoryerror|too many open files|"
            r"resource temporarily unavailable)\b",
            re.I,
        ),
    ),
    (
        "state_conflict",
        re.compile(
            r"\b(merge conflict|would be overwritten|index\.lock|already exists|"
            r"non-fast-forward|working tree.*dirty)\b",
            re.I,
        ),
    ),
    (
        "syntax_or_compile",
        re.compile(
            r"\b(syntaxerror|syntax error|parse error|compilation failed|"
            r"compile error|typecheck failed|type error)\b",
            re.I,
        ),
    ),
    (
        "test_failure",
        re.compile(
            r"(?:\b(?:assertionerror|assertion failed|tests? failed|"
            r"failures?:\s*[1-9])\b|\b[1-9]\d* failed(?:,|\s|$))",
            re.I,
        ),
    ),
)
_URL_RE = re.compile(r"^(?:https?|ssh|git)://", re.IGNORECASE)
_SECRET_RE = re.compile(
    r"(?:^|[_-])(token|secret|password|passwd|api[_-]?key|private[_-]?key|authorization)(?:$|[_-])",
    re.IGNORECASE,
)
_SAFE_WORD_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_.+-]{0,39}$")
_PATHISH_RE = re.compile(
    r"(?:[/\\]|^\.+$|\.(?:py|js|ts|tsx|jsx|go|rs|java|cs|php|rb|sh|ps1|json|ya?ml|toml|md|txt|sql)$)", re.I
)
_KNOWN_SUBCOMMAND_EXECUTABLES = frozenset(
    {
        "git",
        "roam",
        "npm",
        "pnpm",
        "yarn",
        "cargo",
        "go",
        "docker",
        "kubectl",
        "systemctl",
        "journalctl",
        "pip",
        "pip3",
    }
)
_FLAGS_WITH_VALUES = frozenset(
    {
        "-c",
        "-e",
        "-f",
        "-m",
        "-o",
        "-name",
        "-path",
        "-maxdepth",
        "-mindepth",
        "--command",
        "--eval",
        "--filter",
        "--format",
        "--glob",
        "--include",
        "--name",
        "--output",
        "--pattern",
        "--root",
        "--select",
    }
)

_EDIT_TOOLS = frozenset({"Edit", "Write", "MultiEdit", "NotebookEdit", "apply_patch"})

_INTENT_ARCHETYPES: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("debug", re.compile(r"\b(debug|diagnos|root cause|why (?:is|does|did)|broken|failure|error)\b", re.I)),
    ("implement", re.compile(r"\b(implement|build|create|add|ship|develop|make)\b", re.I)),
    ("refactor", re.compile(r"\b(refactor|simplif|clean up|restructur|extract|rename)\b", re.I)),
    ("review", re.compile(r"\b(review|audit|critique|inspect|check the diff|code review)\b", re.I)),
    ("verify", re.compile(r"\b(test|verify|validate|lint|typecheck|prove|regression)\b", re.I)),
    ("performance", re.compile(r"\b(performance|latency|speed|optimi[sz]|memory|cpu|faster)\b", re.I)),
    ("security", re.compile(r"\b(security|vulnerab|secret|auth|permission|threat|exploit)\b", re.I)),
    ("deploy", re.compile(r"\b(deploy|release|production|server|service|container|kubernetes)\b", re.I)),
    ("research", re.compile(r"\b(research|compare|investigate|look into|find out|explore)\b", re.I)),
    ("document", re.compile(r"\b(document|readme|docs|guide|explain|write up)\b", re.I)),
    ("plan", re.compile(r"\b(plan|design|architecture|strategy|roadmap|approach)\b", re.I)),
    ("data", re.compile(r"\b(data|database|sql|query|migration|schema|analytics)\b", re.I)),
    ("ui", re.compile(r"\b(ui|ux|frontend|layout|screen|page|component|style|css)\b", re.I)),
    ("git", re.compile(r"\b(git|commit|branch|merge|rebase|pull request|\bpr\b)\b", re.I)),
)


def _parse_ts(value: Any) -> datetime | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00")).astimezone(timezone.utc)
    except ValueError:
        return None


def _iso(value: datetime | None) -> str:
    return (value or datetime.now(timezone.utc)).isoformat().replace("+00:00", "Z")


def _bucket(value: int, width: int) -> int:
    return max(width, ((max(0, value) + width - 1) // width) * width)


def _load_or_create_key(root: Path, *, create: bool = True) -> bytes:
    path = root / ".roam" / SALT_NAME
    try:
        raw = path.read_text(encoding="ascii").strip()
        key = bytes.fromhex(raw)
        if len(key) >= 16:
            return key
    except (OSError, ValueError):
        pass
    if not create:
        return os.urandom(32)
    path.parent.mkdir(parents=True, exist_ok=True)
    key = os.urandom(32)
    tmp = path.with_name(path.name + f".tmp-{os.getpid()}")
    tmp.write_text(key.hex() + "\n", encoding="ascii")
    try:
        os.chmod(tmp, 0o600)
    except OSError:
        pass
    os.replace(tmp, path)
    return key


def _keyed_hex(key: bytes, purpose: str, value: str, length: int = 24) -> str:
    digest = hmac.new(key, f"{purpose}\0{value}".encode("utf-8", "replace"), hashlib.sha256)
    return digest.hexdigest()[:length]


def _normalize_prompt(text: str) -> list[str]:
    text = _SYSTEM_BLOCK_RE.sub(" ", text)
    tokens: list[str] = []
    for raw in _TOKEN_RE.findall(text.lower())[:1024]:
        if len(raw) > 80:
            raw = "<long>"
        elif re.fullmatch(r"[0-9a-f]{12,}", raw):
            raw = "<hex>"
        elif re.fullmatch(r"\d+(?:\.\d+)*", raw):
            raw = "<n>"
        elif "/" in raw or "\\" in raw:
            raw = "<path>"
        tokens.append(raw)
    return tokens


def _intent_fingerprints(text: str, key: bytes) -> tuple[str, str, int]:
    tokens = _normalize_prompt(text)
    normalized = " ".join(tokens)
    exact = _keyed_hex(key, "intent-exact", normalized, 32)
    if not tokens:
        return exact, "0000000000000000", 0
    weights = [0] * 64
    features = tokens + [f"{a}\0{b}" for a, b in zip(tokens, tokens[1:])]
    for feature in features:
        digest = hashlib.blake2b(
            feature.encode("utf-8", "replace"),
            key=key,
            digest_size=8,
            person=b"roam-int",
        ).digest()
        value = int.from_bytes(digest, "big")
        for bit in range(64):
            weights[bit] += 1 if value & (1 << bit) else -1
    simhash = sum(1 << bit for bit, weight in enumerate(weights) if weight >= 0)
    return exact, f"{simhash:016x}", len(tokens)


def _text_content(content: Any) -> str:
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    return "\n".join(
        str(block.get("text") or "") for block in content if isinstance(block, dict) and block.get("type") == "text"
    ).strip()


def _is_correction(text: str) -> bool:
    cleaned = _SYSTEM_BLOCK_RE.sub(" ", text)
    cleaned = re.sub(r"<[^>]+>.*?</[^>]+>", " ", cleaned, flags=re.DOTALL)
    return bool(_CORRECTION_RE.match(cleaned.lstrip()))


def _tool_family(name: str) -> str:
    if name in _EDIT_TOOLS:
        return "edit"
    if name in {"Bash", "exec_command", "write_stdin"}:
        return "shell"
    if name in {"Read"}:
        return "read"
    if name in {"Grep", "Glob", "ToolSearch"}:
        return "search"
    if name.startswith("mcp__roam-code__") or name.startswith("roam_"):
        return "roam"
    if name in {"WebSearch", "WebFetch"} or name.startswith("web_search"):
        return "web"
    if name in {"Agent", "Task", "TaskCreate", "TaskUpdate", "TaskOutput"}:
        return "agent"
    return "other"


def _command_class(command: str) -> str:
    if _VERIFY_RE.search(command):
        return "verify"
    if _BUILD_RE.search(command):
        return "build"
    if _FORMAT_RE.search(command):
        return "format"
    if _DIFF_RE.search(command):
        return "review"
    if _VCS_WRITE_RE.search(command):
        return "vcs_write"
    if _DEPLOY_RE.search(command):
        return "deploy"
    if _DEPENDENCY_RE.search(command):
        return "dependency"
    if _SEARCH_RE.search(command):
        return "search"
    if _INSPECT_RE.search(command):
        return "inspect"
    if _ORIENT_RE.search(command):
        return "orient"
    if re.search(r"\bgit\b", command, re.IGNORECASE):
        return "git"
    return "other"


def _tool_phase(name: str, command: str = "") -> str:
    family = _tool_family(name)
    if family == "shell":
        command_class = _command_class(command)
        return {
            "verify": "verify",
            "build": "verify",
            "format": "format",
            "review": "review",
            "vcs_write": "publish",
            "deploy": "deploy",
            "dependency": "setup",
            "search": "search",
            "inspect": "inspect",
            "orient": "orient",
            "git": "orient",
        }.get(command_class, "shell")
    return {
        "edit": "edit",
        "read": "inspect",
        "search": "search",
        "roam": "intelligence",
        "web": "research",
        "agent": "delegate",
    }.get(family, "other")


def _intent_archetypes(text: str) -> list[str]:
    cleaned = _SYSTEM_BLOCK_RE.sub(" ", text)
    matches = [name for name, pattern in _INTENT_ARCHETYPES if pattern.search(cleaned)]
    return matches[:4] or ["other"]


def _content_size(value: Any) -> int:
    if isinstance(value, str):
        return len(value.encode("utf-8", "replace"))
    try:
        return len(json.dumps(value, default=str).encode("utf-8", "replace"))
    except (TypeError, ValueError):
        return 0


def _exit_code(value: Any) -> int | None:
    if isinstance(value, str):
        text = value
    else:
        try:
            text = json.dumps(value, default=str)
        except (TypeError, ValueError):
            return None
    match = _EXIT_CODE_RE.search(text)
    return int(match.group(1)) if match else None


def _result_text(value: Any) -> str:
    """Flatten transient tool-result text for classification only."""
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return "\n".join(_result_text(item) for item in value)
    if isinstance(value, dict):
        return "\n".join(
            _result_text(value.get(key))
            for key in ("content", "text", "output", "error", "message", "stderr")
            if key in value
        )
    return ""


def _failure_class(value: Any) -> str:
    """Return one closed failure label without retaining result content."""
    text = _result_text(value)[:200_000]
    for label, pattern in _FAILURE_CLASSIFIERS:
        if pattern.search(text):
            return label
    return "unknown"


def _elapsed_ms(started_at: datetime, observed_at: datetime | None) -> int | None:
    if observed_at is None:
        return None
    return max(0, int((observed_at - started_at).total_seconds() * 1000))


@lru_cache(maxsize=8192)
def _project_scope(cwd: str) -> tuple[str, str]:
    """Return the nearest live Git root, otherwise the normalized workspace."""
    if not cwd:
        return "", "missing"
    path = Path(cwd).expanduser()
    try:
        resolved = path.resolve()
    except OSError:
        resolved = path
    for candidate in (resolved, *resolved.parents):
        try:
            if (candidate / ".git").exists():
                return os.path.normcase(os.path.normpath(str(candidate))), "git_root"
        except OSError:
            break
    return os.path.normcase(os.path.normpath(str(resolved))), "workspace"


def _friction_metrics(
    actions: list[dict[str, Any]],
    *,
    verification_attempts: int,
) -> dict[str, int]:
    phases = [str(action.get("phase") or "other") for action in actions]
    templates = [str(action.get("template") or "") for action in actions if str(action.get("template") or "")]
    template_counts = Counter(templates)
    exact_shell_replays = sum(max(0, count - 1) for count in template_counts.values())
    adjacent_shell_replays = sum(bool(left and left == right) for left, right in zip(templates, templates[1:]))
    failed_action_retries = 0
    for index, action in enumerate(actions):
        template = str(action.get("template") or "")
        if not action.get("failed") or not template:
            continue
        if any(str(candidate.get("template") or "") == template for candidate in actions[index + 1 : index + 4]):
            failed_action_retries += 1
    first_edit = next((index for index, phase in enumerate(phases) if phase == "edit"), None)
    post_edit_context_calls = 0
    if first_edit is not None:
        for phase in phases[first_edit + 1 :]:
            if phase == "verify":
                break
            if phase in {"orient", "search", "inspect", "intelligence"}:
                post_edit_context_calls += 1
    search_inspect_cycles = sum(
        window
        in {
            ("search", "inspect", "search"),
            ("inspect", "search", "inspect"),
        }
        for window in zip(phases, phases[1:], phases[2:])
    )
    phase_switches = sum(left != right for left, right in zip(phases, phases[1:]))
    return {
        "orientation_calls": phases.count("orient"),
        "search_calls": phases.count("search"),
        "inspection_calls": phases.count("inspect"),
        "slice_calls": sum(bool(_SLICE_TEMPLATE_RE.search(template)) for template in templates),
        "output_postprocess_calls": sum(bool(_PROJECTION_RE.search(template)) for template in templates),
        "structured_output_postprocess_calls": sum(
            bool(_STRUCTURED_PROJECTION_RE.search(template)) for template in templates
        ),
        "help_calls": sum(bool(_HELP_RE.search(template)) for template in templates),
        "exact_shell_replays": exact_shell_replays,
        "adjacent_shell_replays": adjacent_shell_replays,
        "failed_action_retries": failed_action_retries,
        "verification_retries": max(0, verification_attempts - 1),
        "post_edit_context_calls": post_edit_context_calls,
        "search_inspect_cycles": search_inspect_cycles,
        "phase_switches": phase_switches,
    }


def _action_outcome_tables(
    actions: list[dict[str, Any]],
) -> dict[str, dict[str, dict[str, Any]]]:
    shell: dict[str, Counter[str]] = defaultdict(Counter)
    phases: dict[str, Counter[str]] = defaultdict(Counter)
    command_classes: dict[str, Counter[str]] = defaultdict(Counter)
    shell_failure_classes: dict[str, Counter[str]] = defaultdict(Counter)
    phase_failure_classes: dict[str, Counter[str]] = defaultdict(Counter)
    command_failure_classes: dict[str, Counter[str]] = defaultdict(Counter)
    for index, action in enumerate(actions):
        failed = bool(action.get("failed"))
        result_size = _int_nonnegative(action.get("result_size"))
        phase = str(action.get("phase") or "other")
        command_class = str(action.get("command_class") or "")
        template = str(action.get("template") or "")
        for table, key in (
            (phases, phase),
            (command_classes, command_class),
            (shell, template),
        ):
            if not key:
                continue
            table[key]["attempts"] += 1
            table[key]["failures"] += int(failed)
            table[key]["no_results"] += int(str(action.get("result_state") or "") == "no_results")
            table[key]["result_bytes"] += result_size
        failure_class = str(action.get("failure_class") or "")
        if failed and failure_class:
            for table, key in (
                (phase_failure_classes, phase),
                (command_failure_classes, command_class),
                (shell_failure_classes, template),
            ):
                if key:
                    table[key][failure_class] += 1
        if (
            failed
            and template
            and any(str(candidate.get("template") or "") == template for candidate in actions[index + 1 : index + 4])
        ):
            shell[template]["retries_after_failure"] += 1

    def render(
        table: dict[str, Counter[str]],
        failure_classes: dict[str, Counter[str]],
    ) -> dict[str, dict[str, Any]]:
        return {
            key: {
                "attempts": counts["attempts"],
                "failures": counts["failures"],
                "no_results": counts["no_results"],
                "retries_after_failure": counts["retries_after_failure"],
                "result_bytes_bucket": (_bucket(counts["result_bytes"], 4096) if counts["result_bytes"] else 0),
                "failure_classes": dict(sorted(failure_classes.get(key, Counter()).items())),
            }
            for key, counts in sorted(table.items())
        }

    return {
        "shell_template_outcomes": render(shell, shell_failure_classes),
        "phase_outcomes": render(phases, phase_failure_classes),
        "command_class_outcomes": render(
            command_classes,
            command_failure_classes,
        ),
    }


def _int_nonnegative(value: Any) -> int:
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return 0


def _run_length_values(values: list[str]) -> list[tuple[str, int]]:
    if not values:
        return []
    runs: list[tuple[str, int]] = []
    current = values[0]
    count = 1
    for value in values[1:]:
        if value == current:
            count += 1
            continue
        runs.append((current, count))
        current = value
        count = 1
    runs.append((current, count))
    return runs


def _compressed_sequence(values: list[str], *, limit: int = 20) -> str:
    runs = _run_length_values(values)
    rendered = [f"{value}*{count}" if count > 1 else value for value, count in runs[:limit]]
    if len(runs) > limit:
        rendered.append("<MORE>")
    return ">".join(rendered)


def _compressed_command_sequence(values: list[str], *, limit: int = 12) -> str:
    runs = _run_length_values(values)
    rendered = [f"{value} ×{count}" if count > 1 else value for value, count in runs[:limit]]
    if len(runs) > limit:
        rendered.append("<MORE>")
    return " => ".join(rendered)


def _sequence_ngrams(
    values: list[str],
    *,
    minimum: int = 2,
    maximum: int = 4,
) -> Counter[str]:
    out: Counter[str] = Counter()
    for width in range(minimum, maximum + 1):
        for index in range(0, len(values) - width + 1):
            out[" => ".join(values[index : index + width])] += 1
    return out


def _command_from_input(value: Any) -> str:
    if isinstance(value, dict):
        for key in ("command", "cmd"):
            command = value.get(key)
            if isinstance(command, str):
                return command
            if isinstance(command, list):
                return " ".join(str(part) for part in command)
        return ""
    if not isinstance(value, str):
        return ""
    try:
        decoded = json.loads(value)
    except (TypeError, ValueError):
        return value
    return _command_from_input(decoded)


def _safe_executable(token: str) -> str:
    token = token.strip("\"'`")
    base = re.split(r"[/\\]", token)[-1].lower()
    if base.endswith(".exe"):
        base = base[:-4]
    return base if _SAFE_WORD_RE.fullmatch(base) else "<EXEC>"


def _safe_argument(token: str, *, executable: str, positional_index: int) -> str:
    raw = token.strip()
    clean = raw.strip("\"'`")
    if not clean:
        return "<ARG>"
    if raw.startswith((">", "2>", "1>", "<")):
        return "<REDIR>"
    if _URL_RE.search(clean):
        return "<URL>"
    if _PATHISH_RE.search(clean):
        return "<PATH>"
    if "=" in clean and not clean.startswith(("==", "!=")):
        name, _value = clean.split("=", 1)
        if name.startswith("-"):
            flag = name if _SAFE_WORD_RE.fullmatch(name.lstrip("-")) else "--<FLAG>"
            return f"{flag}=<ARG>"
        return "<ENV>=<VALUE>"
    if _SECRET_RE.search(clean) or re.fullmatch(r"[A-Za-z0-9+/=_-]{32,}", clean):
        return "<SECRET>"
    if clean.startswith("-"):
        if len(clean) <= 32 and re.fullmatch(r"--?[A-Za-z0-9][A-Za-z0-9_.-]*", clean):
            return clean.lower()
        return "-<FLAG>"
    if re.fullmatch(r"\d+(?:\.\d+)*", clean):
        return "<N>"
    if positional_index == 0 and executable in _KNOWN_SUBCOMMAND_EXECUTABLES:
        return clean.lower() if _SAFE_WORD_RE.fullmatch(clean) else "<SUBCOMMAND>"
    if (
        positional_index == 1
        and executable in {"npm", "pnpm", "yarn"}
        and clean.lower() in {"build", "test", "lint", "typecheck", "format"}
    ):
        return clean.lower()
    if executable in {"python", "python3", "py"}:
        return clean.lower() if clean in {"pytest", "unittest", "pip"} else "<MODULE>"
    return "<ARG>"


def _split_shell_control(command: str) -> list[str]:
    """Split control operators outside quotes; normalize newlines to ``;``."""
    out: list[str] = []
    buf: list[str] = []
    quote = ""
    escaped = False
    index = 0

    def flush() -> None:
        value = "".join(buf).strip()
        if value:
            out.append(value)
        buf.clear()

    while index < len(command):
        char = command[index]
        if escaped:
            buf.append(char)
            escaped = False
            index += 1
            continue
        if char == "\\" and quote != "'":
            buf.append(char)
            escaped = True
            index += 1
            continue
        if quote:
            buf.append(char)
            if char == quote:
                quote = ""
            index += 1
            continue
        if char in {"'", '"', "`"}:
            quote = char
            buf.append(char)
            index += 1
            continue
        pair = command[index : index + 2]
        if pair in {"&&", "||"}:
            flush()
            out.append(pair)
            index += 2
            continue
        if char in {"|", ";", "\n"}:
            flush()
            operator = "|" if char == "|" else ";"
            if not out or out[-1] != operator:
                out.append(operator)
            index += 1
            continue
        buf.append(char)
        index += 1
    flush()
    return out


def sanitize_command_template(command: str) -> str:
    """Return a useful shell shape while removing values, paths, and secrets."""
    command = command.replace("\r", "\n").strip()
    if not command:
        return ""
    parts = _split_shell_control(command)
    rendered: list[str] = []
    for part in parts:
        part = part.strip()
        if not part:
            continue
        if part in {"&&", "||", "|", ";"}:
            rendered.append(part)
            continue
        if "\n" in part and any(marker in part for marker in ("<<", "@'", '@"')):
            head = part.splitlines()[0]
            part = head + " <HEREDOC>"
        try:
            # Transcript parsing must be reproducible across the host that runs
            # the miner. Agent shell records are predominantly POSIX-shaped;
            # using the mining host's ``os.name`` made one corpus produce
            # different templates on Windows and Linux.
            tokens = shlex.split(part, posix=True)
        except ValueError:
            tokens = re.findall(r"""(?:"[^"]*"|'[^']*'|`[^`]*`|\S+)""", part)
        if not tokens:
            continue
        env_prefix: list[str] = []
        token_index = 0
        while token_index < len(tokens) and "=" in tokens[token_index] and not tokens[token_index].startswith("-"):
            env_prefix.append("<ENV>=<VALUE>")
            token_index += 1
        if token_index >= len(tokens):
            rendered.extend(env_prefix or ["<ENV>=<VALUE>"])
            continue
        executable = _safe_executable(tokens[token_index])
        segment = [*env_prefix, executable]
        positional_index = 0
        value_expected = ""
        for token in tokens[token_index + 1 : token_index + 20]:
            if value_expected:
                if (
                    value_expected == "-m"
                    and executable in {"python", "python3", "py"}
                    and token.strip("\"'`").lower()
                    in {"pytest", "unittest", "pip", "py_compile", "compileall", "ruff", "mypy"}
                ):
                    safe = token.strip("\"'`").lower()
                else:
                    safe = "<CODE>" if value_expected in {"-c", "--eval", "--command"} else "<ARG>"
                value_expected = ""
            else:
                safe = _safe_argument(
                    token,
                    executable=executable,
                    positional_index=positional_index,
                )
            segment.append(safe)
            if safe in _FLAGS_WITH_VALUES:
                value_expected = safe
            if not token.startswith("-"):
                positional_index += 1
        if len(tokens) - token_index > 20:
            segment.append("<MORE>")
        rendered.append(" ".join(segment))
    template = " ".join(rendered)
    return template[:320]


def _cwd_matches(cwd: str, root: Path, all_projects: bool) -> bool:
    if all_projects:
        return True
    if not cwd:
        return False
    left = os.path.normcase(os.path.normpath(cwd))
    right = os.path.normcase(os.path.normpath(str(root)))
    return left == right or left.startswith(right + os.sep)


@dataclass
class _Episode:
    source: str
    session_key: str
    turn_seq: int
    started_at: datetime
    prompt: str
    cwd: str
    key: bytes
    last_at: datetime | None = None
    explicit_duration_ms: int | None = None
    tool_sequence: list[str] = field(default_factory=list)
    tool_counts: Counter[str] = field(default_factory=Counter)
    command_counts: Counter[str] = field(default_factory=Counter)
    command_templates: Counter[str] = field(default_factory=Counter)
    command_sequence: list[str] = field(default_factory=list)
    actions: list[dict[str, Any]] = field(default_factory=list)
    pending: dict[str, int] = field(default_factory=dict)
    tool_errors: int = 0
    edit_actions: int = 0
    failed_edit_actions: int = 0
    verification_attempts: int = 0
    verification_failures: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cached_input_tokens: int = 0
    cache_creation_tokens: int = 0
    reasoning_output_tokens: int = 0
    tool_result_bytes: int = 0
    assistant_messages: int = 0
    first_tool_at: datetime | None = None
    first_edit_at: datetime | None = None
    correction_after: bool = False
    explicit_complete: bool = False

    def add_tool(
        self,
        name: str,
        call_id: str,
        tool_input: Any,
        observed_at: datetime | None = None,
    ) -> None:
        family = _tool_family(name)
        self.tool_sequence.append(family)
        self.tool_counts[family] += 1
        command_class = ""
        raw_command = ""
        template = ""
        if family == "shell":
            raw_command = _command_from_input(tool_input)
            command_class = _command_class(raw_command)
            self.command_counts[command_class] += 1
            template = sanitize_command_template(raw_command)
            if template:
                self.command_templates[template] += 1
                self.command_sequence.append(template)
            if command_class in {"verify", "build"}:
                self.verification_attempts += 1
        if name in _EDIT_TOOLS:
            self.edit_actions += 1
        if self.first_tool_at is None:
            self.first_tool_at = observed_at
        if family == "edit" and self.first_edit_at is None:
            self.first_edit_at = observed_at
        self.actions.append(
            {
                "family": family,
                "phase": _tool_phase(name, raw_command),
                "command_class": command_class,
                "template": template,
                "failed": False,
                "result_state": "unknown",
                "result_size": 0,
            }
        )
        if call_id:
            self.pending[call_id] = len(self.actions) - 1

    def add_result(
        self,
        call_id: str,
        failed: bool,
        result_size: int = 0,
        exit_code: int | None = None,
        result_value: Any = "",
    ) -> None:
        self.tool_result_bytes += max(0, result_size)
        action_index = self.pending.pop(call_id, -1)
        action = self.actions[action_index] if 0 <= action_index < len(self.actions) else {}
        family = str(action.get("family") or "")
        command_class = str(action.get("command_class") or "")
        no_results = bool(failed and command_class == "search" and exit_code == 1)
        effective_failure = bool(failed and not no_results)
        if action:
            action["failed"] = effective_failure
            action["result_state"] = "no_results" if no_results else "failure" if effective_failure else "success"
            action["result_size"] = max(0, result_size)
            action["failure_class"] = _failure_class(result_value) if effective_failure else ""
        if effective_failure:
            self.tool_errors += 1
            if family == "edit":
                self.failed_edit_actions += 1
            if command_class in {"verify", "build"}:
                self.verification_failures += 1

    def finish(self, terminal_at: datetime | None) -> tuple[dict[str, Any], dict[str, Any]]:
        terminal_at = terminal_at or self.last_at or self.started_at
        duration_ms = self.explicit_duration_ms
        if duration_ms is None:
            duration_ms = max(0, int((terminal_at - self.started_at).total_seconds() * 1000))
        exact, simhash, token_count = _intent_fingerprints(self.prompt, self.key)
        episode_id = "hist_" + _keyed_hex(
            self.key,
            "episode",
            f"{self.source}\0{self.session_key}\0{self.turn_seq}",
            24,
        )
        session_id = "hist_" + _keyed_hex(self.key, "session", self.session_key, 16)
        successful_edits = max(0, self.edit_actions - self.failed_edit_actions)
        if successful_edits and self.verification_attempts and not self.verification_failures:
            outcome = "historical_acted_verified_proxy"
            health_state = "proxy_verification_passed"
        elif successful_edits and self.verification_failures:
            outcome = "historical_acted_verification_failed_proxy"
            health_state = "proxy_verification_failed"
        elif successful_edits:
            outcome = "historical_acted_unverified"
            health_state = "proxy_unverified"
        elif self.tool_errors:
            outcome = "historical_no_edit_tool_error"
            health_state = "proxy_tool_error"
        else:
            outcome = "historical_no_edit"
            health_state = "not_applicable"
        trajectory = ",".join(self.tool_sequence)
        trajectory_fingerprint = _keyed_hex(self.key, "trajectory", trajectory, 24)
        command_sequence = "\n".join(self.command_sequence)
        command_sequence_fingerprint = _keyed_hex(self.key, "command-sequence", command_sequence, 24)
        event_base = {
            "schema_version": 1,
            "backfill_version": BACKFILL_VERSION,
            "evidence_source": "transcript_backfill",
            "transcript_source": self.source,
            "episode_id": episode_id,
            "session_id": session_id,
            "turn_seq": self.turn_seq,
        }
        project_scope, project_identity_basis = _project_scope(self.cwd)
        project_id = "proj_" + _keyed_hex(self.key, "project", project_scope, 20) if project_scope else ""
        phases = [str(action.get("phase") or "other") for action in self.actions]
        friction = _friction_metrics(
            self.actions,
            verification_attempts=self.verification_attempts,
        )
        outcome_tables = _action_outcome_tables(self.actions)
        start = {
            **event_base,
            "event_id": f"evt_{episode_id[5:]}_start",
            "event_type": "prompt_submitted",
            "ts": _iso(self.started_at),
            "terminal": False,
            "outcome": "pending",
            "compile_expected": False,
            "prompt_hmac_sha256": exact,
            "intent_simhash64": simhash,
            "prompt_chars_bucket": _bucket(len(self.prompt), 100),
            "prompt_tokens_bucket": _bucket(token_count, 25),
            "project_id": project_id,
            "project_identity_basis": project_identity_basis,
            "cwd_hmac_sha256": _keyed_hex(self.key, "cwd", self.cwd, 24),
            "intent_archetypes": _intent_archetypes(self.prompt),
            "health_state": "unknown",
        }
        terminal = {
            **event_base,
            "event_id": f"evt_{episode_id[5:]}_terminal",
            "event_type": "transcript_terminal",
            "ts": _iso(terminal_at),
            "terminal": True,
            "outcome": outcome,
            "duration_ms": duration_ms,
            "changed_files": None,
            "health_state": health_state,
            "trajectory_fingerprint": trajectory_fingerprint,
            "trajectory_template": _compressed_sequence(self.tool_sequence),
            "phase_sequence_template": _compressed_sequence(phases),
            "phase_ngrams": dict(_sequence_ngrams(phases, minimum=2, maximum=5).most_common(80)),
            "command_sequence_fingerprint": command_sequence_fingerprint,
            "command_sequence_template": _compressed_command_sequence(self.command_sequence),
            "shell_templates": dict(self.command_templates.most_common(30)),
            "shell_ngrams": dict(_sequence_ngrams(self.command_sequence).most_common(80)),
            "tool_ngrams": dict(_sequence_ngrams(self.tool_sequence, minimum=2, maximum=5).most_common(80)),
            "tool_calls": sum(self.tool_counts.values()),
            "tool_errors": self.tool_errors,
            "failure_classes": dict(
                sorted(
                    Counter(
                        str(action.get("failure_class") or "")
                        for action in self.actions
                        if action.get("failed") and action.get("failure_class")
                    ).items()
                )
            ),
            "tool_result_bytes_bucket": (_bucket(self.tool_result_bytes, 4096) if self.tool_result_bytes else 0),
            "assistant_messages": self.assistant_messages,
            "time_to_first_tool_ms": _elapsed_ms(self.started_at, self.first_tool_at),
            "time_to_first_edit_ms": _elapsed_ms(self.started_at, self.first_edit_at),
            "tool_families": dict(sorted(self.tool_counts.items())),
            "command_classes": dict(sorted(self.command_counts.items())),
            "friction": friction,
            **outcome_tables,
            "edit_actions": successful_edits,
            "verification_attempts": self.verification_attempts,
            "verification_failures": self.verification_failures,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "cached_input_tokens": self.cached_input_tokens,
            "cache_creation_tokens": self.cache_creation_tokens,
            "reasoning_output_tokens": self.reasoning_output_tokens,
            "correction_after": self.correction_after,
            "explicit_complete": self.explicit_complete,
        }
        return start, terminal


def _iter_jsonl(path: Path) -> Iterable[dict[str, Any]]:
    try:
        with path.open(encoding="utf-8", errors="replace") as fh:
            for line in fh:
                try:
                    value = json.loads(line)
                except (TypeError, ValueError):
                    continue
                if isinstance(value, dict):
                    yield value
    except OSError:
        return


def _scan_claude(path: Path, root: Path, key: bytes, all_projects: bool) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    current: _Episode | None = None
    turn_seq = 0
    session_key = path.stem
    session_cwd = ""
    last_ts: datetime | None = None
    for row in _iter_jsonl(path):
        ts = _parse_ts(row.get("timestamp")) or last_ts
        if ts:
            last_ts = ts
        cwd = str(row.get("cwd") or "")
        if cwd:
            session_cwd = cwd
        message = row.get("message")
        if not isinstance(message, dict):
            continue
        content = message.get("content")
        if row.get("type") == "assistant" and current:
            current.assistant_messages += 1
            usage = message.get("usage")
            if isinstance(usage, dict):
                current.input_tokens += int(usage.get("input_tokens") or 0)
                current.output_tokens += int(usage.get("output_tokens") or 0)
                current.cached_input_tokens += int(usage.get("cache_read_input_tokens") or 0)
                current.cache_creation_tokens += int(usage.get("cache_creation_input_tokens") or 0)
            if isinstance(content, list):
                for block in content:
                    if not isinstance(block, dict) or block.get("type") != "tool_use":
                        continue
                    current.add_tool(
                        str(block.get("name") or ""),
                        str(block.get("id") or ""),
                        block.get("input"),
                        ts,
                    )
            current.last_at = ts or current.last_at
            continue
        if row.get("type") != "user":
            continue
        if isinstance(content, list) and current:
            for block in content:
                if not isinstance(block, dict) or block.get("type") != "tool_result":
                    continue
                current.add_result(
                    str(block.get("tool_use_id") or ""),
                    block.get("is_error") is True,
                    _content_size(block.get("content")),
                    _exit_code(block.get("content")),
                    block.get("content"),
                )
            current.last_at = ts or current.last_at
        prompt = _text_content(content)
        if not prompt:
            continue
        if current:
            current.correction_after = _is_correction(prompt)
            events.extend(current.finish(ts))
        turn_seq += 1
        effective_cwd = cwd or session_cwd
        if not _cwd_matches(effective_cwd, root, all_projects):
            current = None
            continue
        current = _Episode(
            source="claude",
            session_key=session_key,
            turn_seq=turn_seq,
            started_at=ts or datetime.now(timezone.utc),
            prompt=prompt,
            cwd=effective_cwd,
            key=key,
        )
    if current:
        events.extend(current.finish(last_ts))
    return events


def _scan_codex(path: Path, root: Path, key: bytes, all_projects: bool) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    current: _Episode | None = None
    turn_seq = 0
    session_key = path.stem
    cwd = ""
    last_ts: datetime | None = None
    for row in _iter_jsonl(path):
        ts = _parse_ts(row.get("timestamp")) or last_ts
        if ts:
            last_ts = ts
        payload = row.get("payload")
        if not isinstance(payload, dict):
            continue
        if not cwd and isinstance(payload.get("cwd"), str):
            cwd = payload["cwd"]
        payload_type = str(payload.get("type") or "")
        if row.get("type") == "event_msg" and payload_type == "user_message":
            prompt = str(payload.get("message") or "")
            if not prompt:
                continue
            if current:
                current.correction_after = _is_correction(prompt)
                events.extend(current.finish(ts))
            turn_seq += 1
            current = _Episode(
                source="codex",
                session_key=session_key,
                turn_seq=turn_seq,
                started_at=ts or datetime.now(timezone.utc),
                prompt=prompt,
                cwd=cwd,
                key=key,
            )
            continue
        if not current:
            continue
        if row.get("type") == "response_item" and payload_type in {
            "function_call",
            "custom_tool_call",
        }:
            current.add_tool(
                str(payload.get("name") or ""),
                str(payload.get("call_id") or ""),
                payload.get("arguments") if "arguments" in payload else payload.get("input"),
                ts,
            )
        elif row.get("type") == "response_item" and payload_type in {
            "function_call_output",
            "custom_tool_call_output",
        }:
            output = str(payload.get("output") or "")
            exit_code = _exit_code(output)
            current.add_result(
                str(payload.get("call_id") or ""),
                exit_code not in {None, 0},
                _content_size(output),
                exit_code,
                output,
            )
        elif row.get("type") == "event_msg" and payload_type == "patch_apply_end":
            if payload.get("success") is not True:
                current.tool_errors += 1
                current.failed_edit_actions += 1
        elif row.get("type") == "event_msg" and payload_type == "token_count":
            info = payload.get("info")
            last_usage = info.get("last_token_usage") if isinstance(info, dict) else {}
            if isinstance(last_usage, dict):
                current.input_tokens = int(last_usage.get("input_tokens") or 0)
                current.output_tokens = int(last_usage.get("output_tokens") or 0)
                current.cached_input_tokens = int(last_usage.get("cached_input_tokens") or 0)
                current.reasoning_output_tokens = int(last_usage.get("reasoning_output_tokens") or 0)
        elif row.get("type") == "event_msg" and payload_type == "task_complete":
            current.explicit_complete = True
            try:
                current.explicit_duration_ms = int(payload.get("duration_ms"))
            except (TypeError, ValueError):
                pass
            current.last_at = ts or current.last_at
            events.extend(current.finish(ts))
            current = None
            continue
        current.last_at = ts or current.last_at
    if current:
        events.extend(current.finish(last_ts))
    if all_projects or _cwd_matches(cwd, root, False):
        return events
    return []


def _detect_source(path: Path) -> str:
    for row in _iter_jsonl(path):
        if set(row) == {"timestamp", "type", "payload"}:
            return "codex"
        if "message" in row or "sessionId" in row:
            return "claude"
        break
    return "unknown"


def _candidate_files(
    transcripts_dir: Path,
    *,
    since: datetime | None,
    max_files: int,
) -> list[Path]:
    paths: list[Path] = []
    for path in transcripts_dir.rglob("*.jsonl"):
        try:
            stat = path.stat()
        except OSError:
            continue
        if not path.is_file() or stat.st_size > MAX_TRANSCRIPT_BYTES:
            continue
        if since and datetime.fromtimestamp(stat.st_mtime, timezone.utc) < since:
            continue
        paths.append(path)
    paths.sort(key=lambda path: (path.stat().st_mtime, str(path)))
    if max_files > 0:
        paths = paths[-max_files:]
    return paths


def backfill_transcripts(
    root: str | Path,
    transcripts_dir: str | Path | Iterable[str | Path],
    *,
    source: str = "auto",
    since: datetime | None = None,
    max_files: int = 0,
    all_projects: bool = False,
    dry_run: bool = False,
) -> dict[str, Any]:
    root_path = Path(root).resolve()
    if isinstance(transcripts_dir, (str, Path)):
        source_values = [transcripts_dir]
    else:
        source_values = list(transcripts_dir)
    source_paths: list[Path] = []
    seen_sources: set[str] = set()
    for value in source_values:
        path = Path(value).expanduser().resolve()
        identity = os.path.normcase(str(path))
        if identity not in seen_sources:
            seen_sources.add(identity)
            source_paths.append(path)
    key = _load_or_create_key(root_path, create=not dry_run)
    files: list[Path] = []
    seen_files: set[str] = set()
    for source_path in source_paths:
        for path in _candidate_files(source_path, since=since, max_files=max_files):
            identity = os.path.normcase(str(path.resolve()))
            if identity not in seen_files:
                seen_files.add(identity)
                files.append(path)
    files.sort(key=lambda path: (path.stat().st_mtime, str(path)))
    events: list[dict[str, Any]] = []
    source_counts: Counter[str] = Counter()
    skipped_unknown = 0
    for path in files:
        detected = source if source != "auto" else _detect_source(path)
        if detected == "claude":
            extracted = _scan_claude(path, root_path, key, all_projects)
        elif detected == "codex":
            extracted = _scan_codex(path, root_path, key, all_projects)
        else:
            skipped_unknown += 1
            continue
        if extracted:
            source_counts[detected] += 1
            events.extend(extracted)
    events.sort(key=lambda row: (str(row.get("ts") or ""), str(row.get("event_id") or "")))
    output = root_path / ".roam" / OUTPUT_NAME
    if not dry_run:
        output.parent.mkdir(parents=True, exist_ok=True)
        tmp = output.with_name(output.name + f".tmp-{os.getpid()}")
        with tmp.open("w", encoding="utf-8", newline="\n") as fh:
            for event in events:
                fh.write(json.dumps(event, sort_keys=True, separators=(",", ":")) + "\n")
            fh.flush()
            os.fsync(fh.fileno())
        try:
            os.chmod(tmp, 0o600)
        except OSError:
            pass
        os.replace(tmp, output)
    return {
        "state": "dry_run" if dry_run else "written",
        "output": str(output),
        "files_considered": len(files),
        "source_directories": [str(path) for path in source_paths],
        "files_with_episodes": sum(source_counts.values()),
        "files_by_source": dict(sorted(source_counts.items())),
        "unknown_format_files": skipped_unknown,
        "episodes": len(events) // 2,
        "events": len(events),
        "privacy_contract": {
            "prompt_text_persisted": False,
            "assistant_text_persisted": False,
            "raw_commands_persisted": False,
            "sanitized_command_templates_persisted": True,
            "paths_persisted": False,
            "tool_arguments_persisted": False,
            "keyed_fingerprints": True,
            "closed_intent_archetypes_persisted": True,
            "phase_and_friction_counts_persisted": True,
            "sanitized_template_outcomes_persisted": True,
            "search_no_results_separated_from_failures": True,
            "closed_failure_classes_persisted": True,
            "tool_result_content_persisted": False,
        },
    }
