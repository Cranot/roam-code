"""Detect LLM-API integration anti-patterns in human-authored code.

The ``llm-smells`` command scans files that import an LLM SDK (``openai``,
``anthropic``, ``langchain``, ``litellm``, …) and flags ten anti-patterns
derived from the W402 research catalog
(``(internal memo)``):

W415 (v1.0.0) — five baseline patterns:

1. ``mp1_no_model_version_pinning`` — model identifier uses a moving alias
   (e.g., ``"gpt-4o"``, ``"claude-3-5-sonnet-latest"``) instead of an
   immutable dated snapshot. arXiv:2512.18020 §3.2 (NMVP). Heuristic tier.
2. ``tb1_missing_max_tokens`` — completion call lacks any output-token bound
   (``max_tokens`` / ``max_output_tokens`` / ``max_new_tokens``). Cost story.
   arXiv:2512.18020 §3.1 (UMM). Heuristic tier.
3. ``pi1_direct_user_input_concatenation`` — user-controlled identifier is
   concatenated into a prompt-shaped string in the same function. OWASP
   LLM01:2025. Heuristic tier (same-function lexical scan; no taint graph).
4. ``so1_no_structured_output_validation`` — ``json.loads(...)`` on LLM
   response content without a surrounding ``try``/``except``. Heuristic.
5. ``tn1_temperature_not_set`` — completion call without an explicit
   ``temperature=`` kwarg. arXiv:2512.18020 §3.5 (TNES). Heuristic tier.

W415b (v1.1.0) — five cheap patterns:

6. ``tb2_missing_timeout`` — LLM client constructed without a ``timeout=``
   kwarg; requests can hang indefinitely. arXiv:2512.18020 §3.1 (UMM).
   Heuristic tier.
7. ``tb3_missing_max_retries`` — LLM client constructed without
   ``max_retries=`` — relies on opaque SDK defaults. Lower severity than
   tb2 because the SDK default is reasonable but not contractual.
8. ``sm1_no_system_message`` — chat-completion call with an inline
   ``messages=[...]`` list that lacks a ``role: system`` entry, leaving
   the model without behavioral guidance. arXiv:2512.18020 §3.3 (NSM).
9. ``re1_no_retry_on_rate_limit`` — file-level: LLM-using file contains
   no retry / backoff / RateLimitError indicator (tenacity, backoff,
   @retry, manual error handler). Operational gap under load.
10. ``cl1_llm_call_in_loop`` — LLM completion call within 30 lines of a
    loop header that lacks an explicit bound (range / slice / MAX_* /
    break). Cost-spiral surface; flagged once per loop.

This detector is DISTINCT from ``vibe-check``: ``vibe-check`` audits
AI-generated code shape (dead exports, hallucinated imports, etc.).
``llm-smells`` audits human-authored code that calls LLM APIs. The two
audiences and signals do not overlap.

Audience: teams shipping LLM-powered features who want a pre-prod gate
analogous to "audit your SQL queries before ship."

The detector emits one finding per occurrence (per-call patterns) or one
per file (file-level patterns like re1); each finding rides the
``heuristic`` confidence tier because every pattern uses regex over raw
source (no AST traversal, no dataflow). When a future sprint lands a
real taint pass for ``pi1``, that one kind will promote to
``static_analysis``.

W415 / W415b — first production-grade multi-provider LLM-API linter.
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from pathlib import Path

import click

from roam.capability import roam_capability
from roam.commands.resolve import ensure_index
from roam.db.connection import find_project_root, open_db
from roam.output._severity import severity_rank as _severity_rank
from roam.output.formatter import format_table, json_envelope, to_json

# W415 / W415b — detector version stamp. Bump per the W81 / ROADMAP A6
# rules when the pattern set, confidence-tier mapping, or evidence-shape
# changes meaningfully. Consumers can ``import LLM_SMELLS_DETECTOR_VERSION``
# the same way they read ``VIBE_CHECK_DETECTOR_VERSION``.
#
# 1.0.0 — W415 — 5 patterns: mp1, tb1, pi1, so1, rp1.
# 1.1.0 — W415b — adds 5 cheap patterns from the W402 catalog:
#         tb2 (missing timeout), tb3 (missing max_retries),
#         sm1 (no system message), re1 (no retry/backoff),
#         cl1 (LLM call inside a loop). All heuristic tier.
LLM_SMELLS_DETECTOR_VERSION: str = "1.1.0"


# ---------------------------------------------------------------------------
# Pattern catalog metadata
# ---------------------------------------------------------------------------
#
# Closed enumeration of v1 pattern kinds. ``llm-smells`` emits
# ``llm_api_<pattern_short_name>`` rather than the raw research id so the
# findings registry is easy to filter / route via prefix.

_PATTERN_KIND_NO_MODEL_PINNING = "llm_api_no_model_version_pinning"
_PATTERN_KIND_MISSING_MAX_TOKENS = "llm_api_missing_max_tokens"
_PATTERN_KIND_PI_CONCAT = "llm_api_direct_user_input_concatenation"
_PATTERN_KIND_NO_JSON_VALIDATION = "llm_api_no_structured_output_validation"
_PATTERN_KIND_TEMPERATURE_NOT_SET = "llm_api_temperature_not_set"
# W415b additions — cheap patterns from W402 catalog.
_PATTERN_KIND_MISSING_TIMEOUT = "llm_api_missing_timeout"
_PATTERN_KIND_MISSING_MAX_RETRIES = "llm_api_missing_max_retries"
_PATTERN_KIND_NO_SYSTEM_MESSAGE = "llm_api_no_system_message"
_PATTERN_KIND_NO_RETRY_BACKOFF = "llm_api_no_retry_on_rate_limit"
_PATTERN_KIND_CALL_IN_LOOP = "llm_api_call_in_loop"


_LLM_SMELLS_KINDS: tuple[str, ...] = (
    _PATTERN_KIND_NO_MODEL_PINNING,
    _PATTERN_KIND_MISSING_MAX_TOKENS,
    _PATTERN_KIND_PI_CONCAT,
    _PATTERN_KIND_NO_JSON_VALIDATION,
    _PATTERN_KIND_TEMPERATURE_NOT_SET,
    _PATTERN_KIND_MISSING_TIMEOUT,
    _PATTERN_KIND_MISSING_MAX_RETRIES,
    _PATTERN_KIND_NO_SYSTEM_MESSAGE,
    _PATTERN_KIND_NO_RETRY_BACKOFF,
    _PATTERN_KIND_CALL_IN_LOOP,
)


_PATTERN_LABELS: dict[str, str] = {
    _PATTERN_KIND_NO_MODEL_PINNING: "model version not pinned",
    _PATTERN_KIND_MISSING_MAX_TOKENS: "missing max_tokens",
    _PATTERN_KIND_PI_CONCAT: "direct user-input concatenation",
    _PATTERN_KIND_NO_JSON_VALIDATION: "unvalidated json.loads on LLM output",
    _PATTERN_KIND_TEMPERATURE_NOT_SET: "temperature not set",
    _PATTERN_KIND_MISSING_TIMEOUT: "missing timeout on LLM client/call",
    _PATTERN_KIND_MISSING_MAX_RETRIES: "missing max_retries on LLM client",
    _PATTERN_KIND_NO_SYSTEM_MESSAGE: "no system message in messages array",
    _PATTERN_KIND_NO_RETRY_BACKOFF: "no retry/backoff wrapper",
    _PATTERN_KIND_CALL_IN_LOOP: "LLM call inside unbounded loop",
}


_PATTERN_SEVERITY: dict[str, str] = {
    _PATTERN_KIND_NO_MODEL_PINNING: "warning",
    _PATTERN_KIND_MISSING_MAX_TOKENS: "warning",
    _PATTERN_KIND_PI_CONCAT: "critical",
    _PATTERN_KIND_NO_JSON_VALIDATION: "warning",
    _PATTERN_KIND_TEMPERATURE_NOT_SET: "info",
    _PATTERN_KIND_MISSING_TIMEOUT: "warning",
    _PATTERN_KIND_MISSING_MAX_RETRIES: "info",
    _PATTERN_KIND_NO_SYSTEM_MESSAGE: "warning",
    _PATTERN_KIND_NO_RETRY_BACKOFF: "warning",
    _PATTERN_KIND_CALL_IN_LOOP: "warning",
}


# Confidence-tier mapping. Every v1 / v1.1 pattern uses regex over raw
# source, so the canonical tier is ``heuristic``. ``pi1`` is kept at
# heuristic even though it does a (very) lightweight same-function scan
# because the detection is name-substring matching on user-input variables
# — not AST-level taint. A future follow-up will promote ``pi1`` to
# ``static_analysis`` once it lands a real same-function dataflow check.
_LLM_SMELL_KIND_TO_CONFIDENCE: dict[str, str] = {
    _PATTERN_KIND_NO_MODEL_PINNING: "heuristic",
    _PATTERN_KIND_MISSING_MAX_TOKENS: "heuristic",
    _PATTERN_KIND_PI_CONCAT: "heuristic",
    _PATTERN_KIND_NO_JSON_VALIDATION: "heuristic",
    _PATTERN_KIND_TEMPERATURE_NOT_SET: "heuristic",
    _PATTERN_KIND_MISSING_TIMEOUT: "heuristic",
    _PATTERN_KIND_MISSING_MAX_RETRIES: "heuristic",
    _PATTERN_KIND_NO_SYSTEM_MESSAGE: "heuristic",
    _PATTERN_KIND_NO_RETRY_BACKOFF: "heuristic",
    _PATTERN_KIND_CALL_IN_LOOP: "heuristic",
}


def _llm_smell_tier(kind: str) -> str:
    """Map an llm-smells pattern kind to a registry confidence tier."""
    from roam.db.findings import CONFIDENCE_HEURISTIC

    return _LLM_SMELL_KIND_TO_CONFIDENCE.get(kind, CONFIDENCE_HEURISTIC)


# ---------------------------------------------------------------------------
# Regex anchors
# ---------------------------------------------------------------------------
#
# Pre-compiled once at module import. Multi-line patterns use
# ``re.MULTILINE`` so ``^`` matches line starts; the per-call windows are
# computed by slicing the source text.

# Import fingerprint — file must contain at least one of these to be
# considered "an LLM-using file." Avoids false positives on the >99% of
# files in a typical repo that never touch an LLM SDK.
_LLM_IMPORT_RE = re.compile(
    r"^\s*(?:import|from)\s+(?:openai|anthropic|google\.generativeai"
    r"|google_generativeai|langchain|langchain_openai|langchain_anthropic"
    r"|litellm|cohere|mistralai|together|groq|fireworks"
    r"|llama_index|replicate)\b",
    re.MULTILINE,
)

# Completion-call entry points across providers. Each match anchors the
# start of a call block whose argument list we'll scan in a ~25-line
# window for the kwargs of interest.
_COMPLETION_CALL_RE = re.compile(
    r"(?:chat\.completions\.create|completions\.create|messages\.create"
    r"|generate_content|litellm\.completion|completion\.create"
    r"|responses\.create)\s*\(",
    re.MULTILINE,
)

# Model alias literals (mp1). Matches model= kwarg values that are bare
# moving aliases. The negative lookahead pins legitimate dated snapshots
# (``gpt-4o-2024-11-20``, ``claude-3-5-sonnet-20241022``) as PASSING.
_UNPINNED_MODEL_RE = re.compile(
    r"""model\s*=\s*["'](?P<model>"""
    r"""gpt-4o(?:-mini)?(?!-\d{4})"""
    r"""|gpt-4(?:-turbo)?(?!-\d)"""
    r"""|gpt-3\.5-turbo(?!-\d{4})"""
    r"""|o[134](?:-mini|-preview)?(?!-\d{4})"""
    r"""|claude-[34]-[a-z0-9-]+?-latest"""
    r"""|claude-(?:opus|sonnet|haiku)-[0-9-]+(?!-\d{8})"""
    r"""|gemini-(?:pro|ultra|flash|1\.[05]-pro|2\.[05]-flash)(?!-\d{8}|-exp|-preview)"""
    r""")["']""",
    re.MULTILINE,
)

# Kwarg presence checks — applied to the call-block window.
_MAX_TOKENS_KWARG_RE = re.compile(r"\bmax_(?:tokens|output_tokens|new_tokens)\s*=", re.MULTILINE)
_TEMPERATURE_KWARG_RE = re.compile(r"\btemperature\s*=", re.MULTILINE)

# json.loads / JSON.parse without surrounding try.
_JSON_LOADS_RE = re.compile(r"\bjson\.loads\s*\(", re.MULTILINE)

# Variable-name fingerprints for prompt-injection same-function scan.
# Names that are conventionally bound to user-controlled input across
# Python web frameworks (Flask, FastAPI, Django) and CLI tools.
_USER_INPUT_NAME_RE = re.compile(
    r"\b(?:user_input|user_message|user_query|user_prompt"
    r"|request\.(?:json|form|args|body|data|values)"
    r"|req\.(?:json|body|params)"
    r"|params\[|form\[|query\[|args\[)",
)
# Prompt-shaped concatenation: an f-string or "+" join near a string that
# names a prompt role.
_PROMPT_KEYWORD_RE = re.compile(r"(?i)\b(?:system|you\s+are|prompt|instructions?|assistant)\b")
_FSTRING_OR_CONCAT_RE = re.compile(
    r"""(?:f["'][^"'\n]{0,200}\{[^}]+\}[^"'\n]{0,200}["']|["']\s*\+\s*\w+|\w+\s*\+\s*["'])"""
)

# W415b anchors --------------------------------------------------------
#
# Client-construction sites — used for tb2 (missing timeout) + tb3
# (missing max_retries). We anchor on the named SDK constructor, then
# walk the argument list via ``_call_window`` (same approach as the
# completion-call detectors). NOTE: we do NOT match bare names like
# ``OpenAI`` inside `import OpenAI` lines because the regex requires
# an opening paren immediately after the identifier.
_CLIENT_CONSTRUCT_RE = re.compile(
    r"\b(?P<client>(?:openai\.|anthropic\.|litellm\.)?"
    r"(?:OpenAI|AsyncOpenAI|Anthropic|AsyncAnthropic"
    r"|Client|AsyncClient|Cohere|MistralClient|MistralAsyncClient"
    r"|Groq|AsyncGroq|GenerativeModel))\s*\(",
    re.MULTILINE,
)
_TIMEOUT_KWARG_RE = re.compile(r"\btimeout\s*=", re.MULTILINE)
_MAX_RETRIES_KWARG_RE = re.compile(r"\bmax_retries\s*=", re.MULTILINE)

# sm1: system-message presence inside the messages= argument of a
# completion call. We reuse the call-window and check for the literal
# ``"role": "system"`` (or single-quoted variant) anywhere in the window.
# False positive surface: a system message may be assembled dynamically
# in a helper and spread into messages; documented in module docstring.
_SYSTEM_ROLE_RE = re.compile(r"""["']role["']\s*:\s*["']system["']""", re.MULTILINE)
# Only the chat/messages completion calls take a `messages=` array;
# generate_content / responses.create do not. Anchor on the narrower set.
_CHAT_COMPLETION_CALL_RE = re.compile(
    r"(?:chat\.completions\.create|completions\.create|messages\.create"
    r"|litellm\.completion|completion\.create)\s*\(",
    re.MULTILINE,
)
_MESSAGES_KWARG_RE = re.compile(r"\bmessages\s*=", re.MULTILINE)

# re1: file-level retry/backoff indicators. If ANY of these appear in
# the file we consider the file "retry-aware" and do NOT flag. Otherwise
# we emit one finding per file (file-level, not per-call — matches the
# catalog's noise discipline).
_RETRY_INDICATOR_RE = re.compile(
    r"\btenacity\b|\bbackoff\b|@retry\b|\bRateLimitError\b"
    r"|\brate_limit\b|\bexponential_backoff\b|\bmax_retries\s*=\s*[1-9]"
    r"|\bAPIConnectionError\b|\bAPIError\b",
    re.MULTILINE,
)

# cl1: loop headers. We match ``for`` / ``while`` at the start of a line
# (with leading whitespace allowed). Detection: a completion call within
# a 30-line forward window of the loop header.
_LOOP_HEADER_RE = re.compile(
    r"^(?P<indent>[ \t]*)(?P<kw>for\s+\w[\w, ]*|while\s+\S)",
    re.MULTILINE,
)
# Explicit bounds that suppress the cl1 finding (best-effort guards
# against the most-common FP patterns: ``range(...)``, ``items[:N]``
# slicing, ``MAX_*`` constants, an explicit ``break``).
_EXPLICIT_BOUND_RE = re.compile(r"\brange\s*\(|\[\s*:\s*\d+\s*\]|\bMAX_[A-Z_]+\b|\bbreak\b|\bif\b[^:\n]{0,40}:\s*break")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _is_llm_file(text: str) -> bool:
    """Cheap up-front filter — does this file import any LLM SDK?"""
    return _LLM_IMPORT_RE.search(text) is not None


def _line_number(text: str, offset: int) -> int:
    """1-based line number of *offset* in *text*."""
    if offset <= 0:
        return 1
    return text.count("\n", 0, offset) + 1


def _call_window(text: str, open_paren_offset: int, max_lines: int = 25) -> tuple[str, int]:
    """Return (window_text, window_end_offset) starting at the call site.

    Walks forward from ``open_paren_offset`` until we either close the
    parens (depth = 0) or hit ``max_lines`` newlines. Robust to nested
    parens / brackets so a ``messages=[{...}]`` body doesn't truncate
    the window early.
    """
    depth = 0
    in_str: str | None = None
    escape = False
    line_count = 0
    i = open_paren_offset
    n = len(text)
    while i < n:
        ch = text[i]
        if in_str is not None:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == in_str:
                in_str = None
        else:
            if ch in ('"', "'"):
                in_str = ch
            elif ch == "(":
                depth += 1
            elif ch == ")":
                depth -= 1
                if depth == 0:
                    return text[open_paren_offset : i + 1], i + 1
            elif ch == "\n":
                line_count += 1
                if line_count >= max_lines:
                    return text[open_paren_offset:i], i
        i += 1
    # Unterminated — return what we have.
    return text[open_paren_offset:i], i


def _function_blocks(text: str) -> list[tuple[int, int, str]]:
    """Approximate per-function scopes for Python source.

    Returns a list of ``(start_offset, end_offset, header_line)`` tuples,
    one per ``def`` / ``async def`` declaration. The end offset is the
    next dedent to a column <= the def's indentation (or end of file).
    Deliberately approximate — close enough for same-function lexical
    scans without requiring tree-sitter at detector time.
    """
    blocks: list[tuple[int, int, str]] = []
    lines = text.split("\n")
    line_offsets: list[int] = []
    cur = 0
    for ln in lines:
        line_offsets.append(cur)
        cur += len(ln) + 1  # +1 for the newline

    for i, line in enumerate(lines):
        stripped = line.lstrip()
        if not (stripped.startswith("def ") or stripped.startswith("async def ")):
            continue
        indent = len(line) - len(stripped)
        start = line_offsets[i]
        end = len(text)
        for j in range(i + 1, len(lines)):
            nxt = lines[j]
            if not nxt.strip():
                continue
            nxt_indent = len(nxt) - len(nxt.lstrip())
            if nxt_indent <= indent:
                end = line_offsets[j]
                break
        blocks.append((start, end, line))
    return blocks


# ---------------------------------------------------------------------------
# Detectors
# ---------------------------------------------------------------------------
#
# Each detector receives the project-relative path and the raw file text
# and returns a list of finding dicts. Dicts share a common shape:
#
#     {
#         "kind": <pattern kind constant>,
#         "file_path": <relative path>,
#         "line": <1-based line number>,
#         "snippet": <up-to-120-char source excerpt>,
#         "evidence": <pattern-specific extra fields>,
#     }


def _detect_no_model_pinning(file_path: str, text: str) -> list[dict]:
    """Flag every ``model="<moving-alias>"`` literal."""
    out: list[dict] = []
    for m in _UNPINNED_MODEL_RE.finditer(text):
        line = _line_number(text, m.start())
        snippet = m.group(0)
        out.append(
            {
                "kind": _PATTERN_KIND_NO_MODEL_PINNING,
                "file_path": file_path,
                "line": line,
                "snippet": snippet[:120],
                "evidence": {
                    "model_literal": m.group("model"),
                    "pattern": _PATTERN_KIND_NO_MODEL_PINNING,
                    "research": "arxiv:2512.18020",
                },
            }
        )
    return out


def _detect_missing_max_tokens(file_path: str, text: str) -> list[dict]:
    """Flag every completion call whose window lacks a max_tokens-style kwarg."""
    out: list[dict] = []
    for m in _COMPLETION_CALL_RE.finditer(text):
        # m.end() - 1 is the opening paren — _call_window scans from there.
        open_paren_offset = m.end() - 1
        window, _ = _call_window(text, open_paren_offset)
        if _MAX_TOKENS_KWARG_RE.search(window):
            continue
        line = _line_number(text, m.start())
        out.append(
            {
                "kind": _PATTERN_KIND_MISSING_MAX_TOKENS,
                "file_path": file_path,
                "line": line,
                "snippet": text[m.start() : m.start() + 120].replace("\n", " "),
                "evidence": {
                    "pattern": _PATTERN_KIND_MISSING_MAX_TOKENS,
                    "research": "arxiv:2512.18020",
                },
            }
        )
    return out


def _detect_temperature_not_set(file_path: str, text: str) -> list[dict]:
    """Flag every completion call whose window lacks ``temperature=``."""
    out: list[dict] = []
    for m in _COMPLETION_CALL_RE.finditer(text):
        open_paren_offset = m.end() - 1
        window, _ = _call_window(text, open_paren_offset)
        if _TEMPERATURE_KWARG_RE.search(window):
            continue
        line = _line_number(text, m.start())
        out.append(
            {
                "kind": _PATTERN_KIND_TEMPERATURE_NOT_SET,
                "file_path": file_path,
                "line": line,
                "snippet": text[m.start() : m.start() + 120].replace("\n", " "),
                "evidence": {
                    "pattern": _PATTERN_KIND_TEMPERATURE_NOT_SET,
                    "research": "arxiv:2512.18020",
                },
            }
        )
    return out


def _detect_no_json_validation(file_path: str, text: str) -> list[dict]:
    """Flag ``json.loads(...)`` calls without a try in the preceding lines.

    Heuristic: scan ~5 lines back from each ``json.loads(`` site. If no
    ``try:`` appears in that window, flag. The window is intentionally
    small — a try block whose body extends > 5 lines before the parse
    is unusual; bigger windows generate FPs on long fallback handlers.
    """
    out: list[dict] = []
    lines = text.split("\n")
    for m in _JSON_LOADS_RE.finditer(text):
        line = _line_number(text, m.start())
        # Walk back up to 5 lines looking for a ``try:`` at the same or
        # lower indent than the json.loads call.
        idx = line - 1  # 0-based
        json_line = lines[idx] if 0 <= idx < len(lines) else ""
        json_indent = len(json_line) - len(json_line.lstrip())
        found_try = False
        for back in range(1, 6):
            j = idx - back
            if j < 0:
                break
            candidate = lines[j]
            stripped = candidate.strip()
            if stripped.startswith("try:") or stripped == "try":
                cand_indent = len(candidate) - len(candidate.lstrip())
                if cand_indent <= json_indent:
                    found_try = True
                    break
        if found_try:
            continue
        out.append(
            {
                "kind": _PATTERN_KIND_NO_JSON_VALIDATION,
                "file_path": file_path,
                "line": line,
                "snippet": json_line.strip()[:120],
                "evidence": {
                    "pattern": _PATTERN_KIND_NO_JSON_VALIDATION,
                    "research": "arxiv:2512.18020",
                },
            }
        )
    return out


def _detect_pi_concat(file_path: str, text: str) -> list[dict]:
    """Flag same-function prompt-shaped concatenation of user-input names.

    Heuristic phases (single function body at a time):

    1. The function body must contain a completion-API call site
       (otherwise the file is LLM-importing but this function never
       talks to the model — out of scope).
    2. The body must reference at least one user-input-named variable
       (``user_input``, ``request.json``, …).
    3. The body must contain an f-string or ``+``-style string concat.
    4. The concat or its neighbourhood must mention a prompt keyword
       (``system``, ``You are``, ``prompt``, ``instructions``).

    All four signals together = same-function prompt-injection vector.
    Heuristic tier (no AST taint pass; W415b will promote).
    """
    out: list[dict] = []
    for start, end, header in _function_blocks(text):
        body = text[start:end]
        if not _COMPLETION_CALL_RE.search(body):
            continue
        user_match = _USER_INPUT_NAME_RE.search(body)
        if user_match is None:
            continue
        concat_match = _FSTRING_OR_CONCAT_RE.search(body)
        if concat_match is None:
            continue
        if _PROMPT_KEYWORD_RE.search(body) is None:
            continue
        # Use the user-input match site for the line — most-actionable anchor.
        offset = start + user_match.start()
        line = _line_number(text, offset)
        # Header without trailing colon/newline for snippet readability.
        out.append(
            {
                "kind": _PATTERN_KIND_PI_CONCAT,
                "file_path": file_path,
                "line": line,
                "snippet": header.strip()[:120],
                "evidence": {
                    "pattern": _PATTERN_KIND_PI_CONCAT,
                    "user_input_match": user_match.group(0),
                    "owasp": "LLM01:2025",
                },
            }
        )
    return out


# ---------------------------------------------------------------------------
# W415b detectors (5 cheap patterns from W402 catalog)
# ---------------------------------------------------------------------------


def _detect_missing_timeout(file_path: str, text: str) -> list[dict]:
    """Flag LLM client construction that lacks a ``timeout=`` kwarg (tb2).

    File-level discipline: if the user constructs ``OpenAI(timeout=30.0)``
    once and reuses the client, every call inherits that timeout. So we
    flag the CONSTRUCTION site, not each call. False positive surface:
    constructions inside helper functions invoked from elsewhere with
    timeout-bearing kwargs (rare).
    """
    out: list[dict] = []
    for m in _CLIENT_CONSTRUCT_RE.finditer(text):
        open_paren_offset = m.end() - 1
        window, _ = _call_window(text, open_paren_offset)
        if _TIMEOUT_KWARG_RE.search(window):
            continue
        line = _line_number(text, m.start())
        out.append(
            {
                "kind": _PATTERN_KIND_MISSING_TIMEOUT,
                "file_path": file_path,
                "line": line,
                "snippet": text[m.start() : m.start() + 120].replace("\n", " "),
                "evidence": {
                    "pattern": _PATTERN_KIND_MISSING_TIMEOUT,
                    "client": m.group("client"),
                    "research": "arxiv:2512.18020",
                },
            }
        )
    return out


def _detect_missing_max_retries(file_path: str, text: str) -> list[dict]:
    """Flag LLM client construction that lacks an explicit ``max_retries=`` (tb3).

    Lower severity than tb2 because SDK defaults are reasonable; the
    catalog rationale is making the retry count contractual rather
    than implicit.
    """
    out: list[dict] = []
    for m in _CLIENT_CONSTRUCT_RE.finditer(text):
        open_paren_offset = m.end() - 1
        window, _ = _call_window(text, open_paren_offset)
        if _MAX_RETRIES_KWARG_RE.search(window):
            continue
        line = _line_number(text, m.start())
        out.append(
            {
                "kind": _PATTERN_KIND_MISSING_MAX_RETRIES,
                "file_path": file_path,
                "line": line,
                "snippet": text[m.start() : m.start() + 120].replace("\n", " "),
                "evidence": {
                    "pattern": _PATTERN_KIND_MISSING_MAX_RETRIES,
                    "client": m.group("client"),
                    "research": "arxiv:2512.18020",
                },
            }
        )
    return out


def _detect_no_system_message(file_path: str, text: str) -> list[dict]:
    """Flag chat-completion calls with a ``messages=`` array that lacks a
    ``role: system`` entry (sm1).

    We only flag calls that have a ``messages=`` kwarg present — calls
    that build messages from a helper variable are out of scope (the
    helper might construct the system message). False positive surface
    documented in module docstring.
    """
    out: list[dict] = []
    for m in _CHAT_COMPLETION_CALL_RE.finditer(text):
        open_paren_offset = m.end() - 1
        window, _ = _call_window(text, open_paren_offset)
        # Only flag when ``messages=`` literally appears in the window —
        # the call might use a helper variable (``messages=msgs``) where
        # we can't tell whether `msgs` already contains a system entry.
        if not _MESSAGES_KWARG_RE.search(window):
            continue
        # If `messages=` is followed by a bare identifier rather than an
        # inline list literal, also skip — we'd need cross-variable
        # analysis to know what's in it.
        msgs_match = _MESSAGES_KWARG_RE.search(window)
        if msgs_match is not None:
            tail = window[msgs_match.end() :].lstrip()
            if not tail.startswith("["):
                # ``messages=msgs`` — can't see contents from here.
                continue
        if _SYSTEM_ROLE_RE.search(window):
            continue
        line = _line_number(text, m.start())
        out.append(
            {
                "kind": _PATTERN_KIND_NO_SYSTEM_MESSAGE,
                "file_path": file_path,
                "line": line,
                "snippet": text[m.start() : m.start() + 120].replace("\n", " "),
                "evidence": {
                    "pattern": _PATTERN_KIND_NO_SYSTEM_MESSAGE,
                    "research": "arxiv:2512.18020",
                },
            }
        )
    return out


def _detect_no_retry_backoff(file_path: str, text: str) -> list[dict]:
    """File-level: LLM-using file with NO retry/backoff indicator (re1).

    One finding per file (not per call). The anchor line is the first
    completion-call site in the file — that gives the operator a
    jumping-off point for the fix. If the file contains no completion
    call we don't bother — re1 only matters when there is an actual
    LLM API surface to retry.
    """
    if _RETRY_INDICATOR_RE.search(text):
        return []
    call_match = _COMPLETION_CALL_RE.search(text)
    if call_match is None:
        return []
    line = _line_number(text, call_match.start())
    return [
        {
            "kind": _PATTERN_KIND_NO_RETRY_BACKOFF,
            "file_path": file_path,
            "line": line,
            "snippet": text[call_match.start() : call_match.start() + 120].replace("\n", " "),
            "evidence": {
                "pattern": _PATTERN_KIND_NO_RETRY_BACKOFF,
                "scope": "file",
                "owasp": None,
                "research": "portkey.ai retries-fallbacks-circuit-breakers",
            },
        }
    ]


def _detect_call_in_loop(file_path: str, text: str) -> list[dict]:
    """Flag LLM call within a 30-line forward window of a loop header
    that lacks an explicit bound (cl1).

    Heuristic suppressions (no finding emitted):

    * ``for x in range(N)`` — bounded by range.
    * ``items[:N]`` slice — bounded by slice.
    * ``MAX_*`` constant in the loop body — explicit bound.
    * ``break`` statement in the loop body — early exit.

    False positives expected for intentional agentic loops — the
    catalog calls this out and the confidence tier is ``heuristic``.
    Deduplicate per (file, completion-call line): a tight inner loop
    nested inside an outer loop would otherwise emit twice.
    """
    out: list[dict] = []
    seen_call_lines: set[int] = set()
    lines = text.split("\n")
    line_offsets: list[int] = []
    cur = 0
    for ln in lines:
        line_offsets.append(cur)
        cur += len(ln) + 1

    for loop_m in _LOOP_HEADER_RE.finditer(text):
        loop_start = loop_m.start()
        loop_line = _line_number(text, loop_start)
        # Determine the loop body span: from the line after the header
        # to either +30 lines or the end of the file, whichever is
        # smaller. We don't try to parse indentation strictly — a 30-
        # line window covers the typical call-in-loop bug surface.
        end_line_idx = min(loop_line - 1 + 30, len(lines))
        end_offset = line_offsets[end_line_idx] if end_line_idx < len(lines) else len(text)
        window = text[loop_start:end_offset]
        if _EXPLICIT_BOUND_RE.search(window):
            continue
        for call_m in _COMPLETION_CALL_RE.finditer(window):
            absolute_offset = loop_start + call_m.start()
            call_line = _line_number(text, absolute_offset)
            if call_line in seen_call_lines:
                continue
            seen_call_lines.add(call_line)
            out.append(
                {
                    "kind": _PATTERN_KIND_CALL_IN_LOOP,
                    "file_path": file_path,
                    "line": call_line,
                    "snippet": text[absolute_offset : absolute_offset + 120].replace("\n", " "),
                    "evidence": {
                        "pattern": _PATTERN_KIND_CALL_IN_LOOP,
                        "loop_line": loop_line,
                        "loop_keyword": loop_m.group("kw").split()[0],
                        "research": "VentureBeat cost-spiral",
                    },
                }
            )
            # One finding per loop is enough; break the inner search
            # so a single loop containing N calls reports one row.
            break
    return out


# Detector registry — single iteration point so the command stays compact.
_DETECTORS: tuple[tuple[str, "callable"], ...] = (
    (_PATTERN_KIND_NO_MODEL_PINNING, _detect_no_model_pinning),
    (_PATTERN_KIND_MISSING_MAX_TOKENS, _detect_missing_max_tokens),
    (_PATTERN_KIND_TEMPERATURE_NOT_SET, _detect_temperature_not_set),
    (_PATTERN_KIND_NO_JSON_VALIDATION, _detect_no_json_validation),
    (_PATTERN_KIND_PI_CONCAT, _detect_pi_concat),
    # W415b additions (cheap patterns)
    (_PATTERN_KIND_MISSING_TIMEOUT, _detect_missing_timeout),
    (_PATTERN_KIND_MISSING_MAX_RETRIES, _detect_missing_max_retries),
    (_PATTERN_KIND_NO_SYSTEM_MESSAGE, _detect_no_system_message),
    (_PATTERN_KIND_NO_RETRY_BACKOFF, _detect_no_retry_backoff),
    (_PATTERN_KIND_CALL_IN_LOOP, _detect_call_in_loop),
)


# ---------------------------------------------------------------------------
# Findings registry emission
# ---------------------------------------------------------------------------


def _llm_smells_finding_id(kind: str, file_path: str, line: int, snippet_hash: str) -> str:
    """Stable, deterministic id for one llm-smells finding.

    The ``(kind, file_path, line, snippet_hash)`` tuple is sufficient to
    re-identify the same finding across runs. ``snippet_hash`` is a
    short SHA-1 of the matched substring so two distinct call sites on
    the same line (rare, but possible) collide cleanly with their own
    rows rather than overwriting each other.
    """
    raw = f"{kind}:{file_path}:{line}:{snippet_hash}"
    digest = hashlib.sha1(raw.encode("utf-8")).hexdigest()[:12]
    return f"llm-smells:{kind}:{digest}"


def _emit_llm_smells_findings(
    conn,
    records: list[dict],
    source_version: str,
) -> int:
    """Emit one ``FindingRecord`` per record into the registry.

    Wrapped by the caller in a defensive try/except so a pre-W89 DB
    (no ``findings`` table) silently no-ops rather than crashing the
    standard llm-smells command path.
    """
    from roam.db.findings import FindingRecord, emit_finding

    emitted = 0
    for rec in records:
        kind = rec["kind"]
        file_path = rec["file_path"]
        line = rec["line"]
        snippet = rec.get("snippet", "")
        snippet_hash = hashlib.sha1(snippet.encode("utf-8")).hexdigest()[:8]
        finding_id = _llm_smells_finding_id(kind, file_path, line, snippet_hash)
        label = _PATTERN_LABELS.get(kind, kind)
        severity = _PATTERN_SEVERITY.get(kind, "warning")
        claim = f"LLM-API smell ({label}): {file_path}:{line} -- {snippet[:80]}"
        evidence = dict(rec.get("evidence", {}))
        evidence.setdefault("pattern", kind)
        evidence["severity"] = severity
        evidence["file_path"] = file_path
        evidence["line"] = line
        evidence["snippet"] = snippet
        emit_finding(
            conn,
            FindingRecord(
                finding_id_str=finding_id,
                subject_kind="file",
                subject_id=None,
                claim=claim,
                evidence_json=json.dumps(evidence, sort_keys=True),
                confidence=_llm_smell_tier(kind),
                source_detector="llm-smells",
                source_version=source_version,
            ),
        )
        emitted += 1
    return emitted


# ---------------------------------------------------------------------------
# CLI command
# ---------------------------------------------------------------------------


def _iter_indexed_files(conn, project_root: Path) -> list[tuple[str, Path]]:
    """Return ``(relative_path, absolute_path)`` for every indexed file.

    Skips files whose disk read fails (deleted between index and scan).
    """
    rows = conn.execute("SELECT path FROM files WHERE language IS NOT NULL ORDER BY path").fetchall()
    out: list[tuple[str, Path]] = []
    for r in rows:
        rel = r["path"] if isinstance(r, sqlite3.Row) else r[0]
        full = project_root / rel
        if not full.exists():
            continue
        out.append((rel, full))
    return out


@roam_capability(
    name="llm-smells",
    category="health",
    summary="Detect LLM-API integration anti-patterns in human-authored code",
    maturity="experimental",
    mcp_expose=True,
    mcp_preset=("core",),
    side_effect=False,
    task_required=False,
    destructive=False,
    stale_sensitive=True,
    ai_safe=True,
    requires_index=True,
)
@click.command("llm-smells")
@click.option(
    "--min-severity",
    # W1005-followup-A: widened from 3-tier {info, warning, critical} to W547
    # canonical 7-token vocabulary so agents can pass any of
    # {critical, error, high, warning, medium, low, info} and have it compared
    # via ``severity_rank()`` from ``roam.output._severity``. The llm-smells
    # detectors currently emit only {info, warning, critical} (per
    # ``_PATTERN_SEVERITY``), but the W547 rank table accepts CVSS terms
    # (high/medium/low) and SARIF ``error`` as equivalents under the
    # canonical ordering (higher = worse). Aliases like ``note``/``unknown``
    # are intentionally NOT in the Choice — they collapse to ``info``/
    # sort-below-info via severity_rank, so a user-facing filter on them
    # would be confusing. Mirrors the cmd_smells widening landed in W1005.
    type=click.Choice(
        ["critical", "error", "high", "warning", "medium", "low", "info"],
        case_sensitive=False,
    ),
    default="info",
    help=(
        "Minimum severity to include. Uses the canonical W547 7-token "
        "ordering (critical > error == high > warning > medium > low > "
        "info). Detectors emit info/warning/critical today; CVSS aliases "
        "(high/medium/low) and SARIF ``error`` rank via the same "
        "severity_rank() comparator."
    ),
)
@click.option(
    "--persist",
    "persist",
    is_flag=True,
    default=False,
    help=(
        "Mirror each llm-smells finding into the central findings registry "
        "(``roam findings list --detector llm-smells``)."
    ),
)
@click.pass_context
def llm_smells(ctx, min_severity, persist):
    """Detect LLM-API integration anti-patterns.

    Scans every indexed file whose source imports a supported LLM SDK
    (openai, anthropic, langchain, litellm, google.generativeai, cohere,
    mistralai, together, groq, fireworks, llama_index, replicate) and
    flags ten high-value anti-patterns:

    Baseline (W415):

    * ``llm_api_no_model_version_pinning`` -- model uses a moving alias.
    * ``llm_api_missing_max_tokens`` -- completion call lacks token bound.
    * ``llm_api_direct_user_input_concatenation`` -- same-function prompt
      injection vector (OWASP LLM01:2025).
    * ``llm_api_no_structured_output_validation`` -- ``json.loads`` on LLM
      output without try/except.
    * ``llm_api_temperature_not_set`` -- completion call without explicit
      temperature.

    Cheap-pattern wave (W415b):

    * ``llm_api_missing_timeout`` -- LLM client constructed without
      ``timeout=`` (requests can hang).
    * ``llm_api_missing_max_retries`` -- LLM client constructed without
      explicit ``max_retries=`` (relies on opaque SDK defaults).
    * ``llm_api_no_system_message`` -- chat call with inline
      ``messages=[...]`` lacking a ``role: system`` entry.
    * ``llm_api_no_retry_on_rate_limit`` -- file-level: no retry /
      backoff / RateLimitError indicator anywhere in the file.
    * ``llm_api_call_in_loop`` -- completion call within 30 lines of an
      unbounded loop header (cost-spiral surface).

    Different from ``vibe-check`` (AI-generated code shape) and ``smells``
    (structural anti-patterns). This is the first production-grade
    multi-provider LLM-API linter.
    """
    json_mode = ctx.obj.get("json") if ctx.obj else False
    sarif_mode = ctx.obj.get("sarif") if ctx.obj else False
    budget = ctx.obj.get("budget", 0) if ctx.obj else 0
    ensure_index()

    project_root = find_project_root()
    # W564: severity floor sourced from canonical roam.output._severity.
    # The legacy 3-tier table ``{info:0, warning:1, critical:2}`` is
    # gone; ``_severity_rank`` is imported above and gives the same
    # ORDER (critical > warning > info) with alias support.
    floor = _severity_rank(min_severity)

    with open_db(readonly=not persist) as conn:
        indexed_files = _iter_indexed_files(conn, project_root)

        all_records: list[dict] = []
        files_scanned = 0
        llm_files: list[str] = []
        for rel, full in indexed_files:
            try:
                text = full.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            files_scanned += 1
            if not _is_llm_file(text):
                continue
            llm_files.append(rel)
            for _kind, fn in _DETECTORS:
                all_records.extend(fn(rel, text))

        # Severity filter.
        filtered_records = [
            r for r in all_records if _severity_rank(_PATTERN_SEVERITY.get(r["kind"], "warning")) >= floor
        ]

        if persist and filtered_records:
            try:
                _emit_llm_smells_findings(
                    conn,
                    filtered_records,
                    source_version=LLM_SMELLS_DETECTOR_VERSION,
                )
                conn.commit()
            except sqlite3.OperationalError:
                # findings table missing (pre-W89 schema) — degrade gracefully.
                pass

        # Aggregates
        counts_by_kind: dict[str, int] = {k: 0 for k in _LLM_SMELLS_KINDS}
        for rec in filtered_records:
            counts_by_kind[rec["kind"]] = counts_by_kind.get(rec["kind"], 0) + 1
        total_findings = sum(counts_by_kind.values())
        critical_findings = sum(1 for r in filtered_records if _PATTERN_SEVERITY.get(r["kind"]) == "critical")

        # LAW 6 — verdict line works without any other field. LAW 4 —
        # terminal token is ``findings`` (anchor noun) or ``files`` (also
        # an anchor) when the scan found nothing.
        #
        # W805: empty-corpus disclosure (Pattern 2 silent-fallback fix).
        # When no LLM-using files are detected, the scan did not run on
        # any analyzable input — distinguish that degraded state from
        # "0 findings in a real LLM-using codebase" via ``partial_success``
        # + a closed-enum ``state`` field. Mirrors W834 / W836.
        no_llm_files = len(llm_files) == 0
        if no_llm_files:
            verdict = "no LLM-using files detected (scan empty — corpus may not use LLM APIs)"
        elif total_findings == 0:
            verdict = f"0 LLM-API findings in {len(llm_files)} scanned files"
        else:
            verdict = f"{total_findings} LLM-API findings ({critical_findings} critical) in {len(llm_files)} files"

        # SARIF output (W1207): projection for CI / GitHub Code Scanning.
        # Branches BEFORE json/text so the pre-existing paths stay
        # byte-identical to pre-W1207. The rules catalogue is the closed
        # enumeration of 10 LLM-API anti-pattern kinds (W415 + W415b);
        # per-finding severity drives the SARIF level (critical -> error,
        # warning -> warning, info -> note) via the canonical _to_level
        # mapping. The findings list shape matches the envelope's
        # ``findings`` projection — ``file`` (not ``file_path``), ``kind``,
        # ``line``, ``severity``, ``snippet`` — so the projection stays
        # consistent with the JSON consumer.
        if sarif_mode:
            from roam.output.sarif import llm_smells_to_sarif, write_sarif

            sarif_findings = [
                {
                    "kind": rec["kind"],
                    "file": rec["file_path"],
                    "line": rec["line"],
                    "severity": _PATTERN_SEVERITY.get(rec["kind"], "warning"),
                    "confidence": _llm_smell_tier(rec["kind"]),
                    "snippet": rec.get("snippet", ""),
                }
                for rec in filtered_records
            ]
            click.echo(write_sarif(llm_smells_to_sarif(sarif_findings)))
            return

        # JSON output
        if json_mode:
            _summary = {
                "verdict": verdict,
                "total_findings": total_findings,
                "critical_findings": critical_findings,
                "llm_files_scanned": len(llm_files),
                "files_scanned": files_scanned,
                "patterns_detected": sum(1 for v in counts_by_kind.values() if v > 0),
                "min_severity": min_severity,
            }
            # W805: empty-corpus disclosure (Pattern 2). When the scan
            # found zero LLM-using files, distinguish "0 findings on real
            # LLM code" (clean success) from "0 findings because we found
            # nothing to analyze" (degraded) via partial_success + state.
            if no_llm_files:
                _summary["partial_success"] = True
                _summary["state"] = "no_llm_files"
            envelope = json_envelope(
                "llm-smells",
                budget=budget,
                summary={
                    **_summary,
                    # Pattern 3 vocabulary: name what the metric counts so
                    # downstream agents reading just the summary know the
                    # axis. ``findings`` here is per-OCCURRENCE, NOT per
                    # file (a file with 4 unpinned models contributes 4).
                    "findings_metric_definition": (
                        "Per-occurrence count: one finding per regex match. "
                        "A file with N unpinned-model literals contributes N."
                    ),
                },
                patterns=[
                    {
                        "kind": kind,
                        "label": _PATTERN_LABELS[kind],
                        "severity": _PATTERN_SEVERITY[kind],
                        "confidence": _llm_smell_tier(kind),
                        "count": counts_by_kind.get(kind, 0),
                    }
                    for kind in _LLM_SMELLS_KINDS
                ],
                findings=[
                    {
                        "kind": rec["kind"],
                        "file": rec["file_path"],
                        "line": rec["line"],
                        "severity": _PATTERN_SEVERITY.get(rec["kind"], "warning"),
                        "confidence": _llm_smell_tier(rec["kind"]),
                        "snippet": rec.get("snippet", ""),
                    }
                    for rec in filtered_records
                ],
                llm_files=llm_files,
                next_steps=[
                    "roam findings list --detector llm-smells",
                ],
                agent_contract={
                    "facts": [
                        f"{total_findings} LLM-API findings",
                        f"{critical_findings} critical findings",
                        f"{len(llm_files)} LLM files",
                    ],
                },
            )
            click.echo(to_json(envelope))
            return

        # Text output
        click.echo(f"VERDICT: {verdict}")
        click.echo()
        headers = ["Pattern", "Severity", "Count"]
        rows = [
            [
                _PATTERN_LABELS[kind],
                _PATTERN_SEVERITY[kind],
                str(counts_by_kind.get(kind, 0)),
            ]
            for kind in _LLM_SMELLS_KINDS
        ]
        click.echo(format_table(headers, rows))
        click.echo()
        click.echo(f"  {total_findings} findings in {len(llm_files)} LLM files (scanned {files_scanned} files total)")

        if filtered_records:
            click.echo()
            click.echo("  Top findings:")
            for rec in filtered_records[:20]:
                sev = _PATTERN_SEVERITY.get(rec["kind"], "warning")
                click.echo(
                    f"    [{sev}] {rec['file_path']}:{rec['line']} -- {_PATTERN_LABELS.get(rec['kind'], rec['kind'])}"
                )
            if len(filtered_records) > 20:
                click.echo(f"    ... and {len(filtered_records) - 20} more")
            click.echo()
            click.echo("  Next: roam findings list --detector llm-smells")
