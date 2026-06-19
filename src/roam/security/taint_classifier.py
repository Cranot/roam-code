"""LLM-augmented classification of taint findings.

`roam taint` produces graph-reach findings with structural metadata
(source → sink path, sanitizer presence, file paths, CWE). Static rules
alone cannot tell IDOR (broken authorization) apart from SQLi or
command-injection — those distinctions need *semantic* reasoning over
the path symbols.

This module runs an MCP **sampling** call (the agent's own model) over
each reachable finding and labels it with a category + confidence. It is
opt-in (``--classify`` flag) and a graceful no-op when no sampler is
available, so the v12 zero-API-key promise still holds.

Counter to Semgrep Multimodal: their LLM reasoning is hosted, paid, and
opinionated. roam's reasoning runs through the agent's existing model,
costs nothing extra, and is fully reproducible.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from fastmcp.exceptions import McpError

# Categories the classifier may return. The list is closed so the agent's
# label is parseable without an LLM-side schema spec.
_CATEGORIES: tuple[str, ...] = (
    "IDOR",  # broken authorization, missing object-ownership check
    "AUTHZ",  # broader authorization gap (role, scope, ACL)
    "SQLI",
    "XSS",
    "CMD_INJECTION",
    "PATH_TRAVERSAL",
    "DESERIALIZATION",
    "SSRF",
    "OPEN_REDIRECT",
    "OTHER",  # taint reaches but the category is unclear
    "FALSE_POSITIVE",  # path exists but the LLM is confident it's not exploitable
)

_SAMPLING_FAILURE_EXCEPTIONS = (
    McpError,
    RuntimeError,
    ValueError,
)


_CLASSIFIER_SYSTEM_PROMPT = (
    "You are a senior application-security engineer reviewing a taint "
    "analysis finding. The static engine has already proven that user-"
    "controlled input flows from `source` to `sink` through the listed "
    "path. Your job is to classify the *kind* of vulnerability, judge "
    "confidence, and explain in <=2 sentences why. Do not output anything "
    "outside the JSON object asked for. Pick exactly one category from "
    "this fixed list: " + ", ".join(_CATEGORIES) + "."
)


@dataclass
class Classification:
    """Structured classifier output. ``label`` is one of ``_CATEGORIES``."""

    label: str
    confidence: str  # "high" | "medium" | "low"
    reasoning: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "label": self.label,
            "confidence": self.confidence,
            "reasoning": self.reasoning,
        }


@dataclass
class ClassifyOptions:
    """Knobs for the classifier — exposed so callers can tighten/loosen."""

    max_tokens: int = 250
    temperature: float = 0.0
    skip_sanitized: bool = True


def _build_user_prompt(finding: dict[str, Any]) -> str:
    """Render a finding into the classifier's user prompt.

    The prompt deliberately quotes the *path symbols* + the rule's CWE so
    the model has structural evidence on top of textual hints. Keeping
    this short matters — sampling cost scales with prompt size.
    """
    rule_id = finding.get("rule_id") or "?"
    cwe = finding.get("cwe") or ""
    severity = finding.get("severity") or "warning"
    src = finding.get("source_symbol") or {}
    snk = finding.get("sink_symbol") or {}
    path_syms = finding.get("path_symbols") or []
    sanitizer = finding.get("sanitizer_in_path", False)

    path_lines = []
    for i, sym in enumerate(path_syms):
        name = sym.get("qualified_name") or sym.get("name") or f"<id={sym.get('id', '?')}>"
        loc = ""
        if sym.get("file"):
            loc = f"  {sym['file']}"
            if sym.get("line"):
                loc += f":{sym['line']}"
        path_lines.append(f"  {i + 1}. {name}{loc}")

    return (
        f"Taint finding from rule={rule_id} (CWE={cwe}, severity={severity}).\n"
        f"Sanitizer on path: {sanitizer}.\n\n"
        f"Source: {src.get('qualified_name') or src.get('name') or '?'}\n"
        f"Sink:   {snk.get('qualified_name') or snk.get('name') or '?'}\n\n"
        f"Path:\n"
        + "\n".join(path_lines)
        + "\n\n"
        + "Return ONLY a JSON object on a single line with these keys:\n"
        + '{"label": "<one of: '
        + ", ".join(_CATEGORIES)
        + '>", "confidence": "<high|medium|low>", "reasoning": "<<=2 sentences>"}'
    )


def _parse_classifier_response(raw_text: str) -> Classification | None:
    """Parse the model's JSON response into a Classification.

    Tolerant of surrounding fluff — extracts the first ``{...}`` block,
    coerces unknown labels to ``OTHER``, and clips the reasoning at
    400 chars. Returns ``None`` on total failure (caller falls back).
    """
    if not raw_text:
        return None
    text = raw_text.strip()
    # Pull the first JSON object out of the text, in case the model
    # added a "Sure, here is …" preamble.
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end < start:
        return None
    try:
        doc = json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        return None
    if not isinstance(doc, dict):
        return None

    label = str(doc.get("label", "OTHER")).upper().strip()
    if label not in _CATEGORIES:
        label = "OTHER"
    confidence = str(doc.get("confidence", "low")).lower().strip()
    if confidence not in ("high", "medium", "low"):
        confidence = "low"
    reasoning = str(doc.get("reasoning", "")).strip()[:400]
    return Classification(label=label, confidence=confidence, reasoning=reasoning)


async def classify_finding(
    finding: dict[str, Any],
    ctx: Any,
    *,
    options: ClassifyOptions | None = None,
) -> Classification | None:
    """Classify one taint finding via MCP sampling.

    ``ctx`` is the FastMCP ``Context`` provided to the tool wrapper. When
    ``ctx`` lacks a ``.sample`` method (no client sampling, or the tool
    was called outside MCP), this returns ``None`` — callers should
    treat that as "skip classification" and pass through the finding.

    With ``options.skip_sanitized=True`` (default), findings that the
    static engine already marked sanitized are skipped — they're already
    OpenVEX ``not_affected`` and don't need LLM corroboration.

    Always returns ``None`` on transport / parse failure — never raises.
    """
    options = options or ClassifyOptions()

    if options.skip_sanitized and finding.get("sanitizer_in_path"):
        return None
    if ctx is None:
        return None
    sampler = getattr(ctx, "sample", None)
    if not callable(sampler):
        return None

    user_prompt = _build_user_prompt(finding)
    try:
        result = await sampler(
            user_prompt,
            system_prompt=_CLASSIFIER_SYSTEM_PROMPT,
            max_tokens=options.max_tokens,
            temperature=options.temperature,
        )
    except _SAMPLING_FAILURE_EXCEPTIONS:
        return None

    # Reuse the same text-extraction helper the compression module uses.
    from roam.mcp_extras.sampling import _extract_summary_text

    raw_text = _extract_summary_text(result)
    return _parse_classifier_response(raw_text)


async def classify_findings(
    findings: list[dict[str, Any]],
    ctx: Any,
    *,
    options: ClassifyOptions | None = None,
) -> list[dict[str, Any]]:
    """Classify each finding in *findings*; return the list with each
    finding annotated with a ``classification`` dict on success.

    Findings whose classification is ``None`` (sanitized, no sampler, or
    parse failure) are passed through unchanged. The returned list is a
    new list — the input is not mutated.

    **Performance note**: classification is sequential — each finding waits
    for the prior sampler call to return. For a finding-heavy repo (>50
    paths) this can add tens of seconds. Concurrency-bounded gather is
    deferred to v12.2 once we observe the real-world finding distribution.
    Callers can cap up-front by filtering ``findings`` before calling.
    """
    out: list[dict[str, Any]] = []
    for f in findings:
        copy = dict(f)
        cls = await classify_finding(f, ctx, options=options)
        if cls is not None:
            copy["classification"] = cls.to_dict()
        out.append(copy)
    return out
