"""Shared secret-redaction helpers for the producer boundary (W364).

Extracted from ``roam.commands.cmd_pr_bundle`` so any other producer of
free-form string fields (future MCP wrappers per W363, third-party
plugins, drive-by collector paths) can scrub identically without
duplicating the pattern set.

This module is **producer-boundary only** — the collector at
``roam.evidence.collector`` keeps its own deliberately-broader
defense-in-depth pattern set (W249 layer-2 scrub) so that a producer
that bypasses this helper still gets a second pass at ingest. See the
"intentional duplication, defense-in-depth" comment block in
``collector.py`` for the rationale.

Public API:

* :data:`SECRET_PATTERNS` -- closed tuple of secret-shaped regexes
* :func:`redact_secrets_in_string` -- scrub a single string value
* :func:`redact_secrets` -- scrub an ``Any``-typed value, ride non-strings
  through untouched
* :func:`scrub_actor_block` -- scrub every string field on an actor block

Each scrubbing helper returns ``(scrubbed_value, had_secrets)``. The
caller stamps ``"secret"`` into the envelope's ``redactions`` array when
``had_secrets`` is True (Pattern 2 -- explicit absence beats silent
absence). ``"secret"`` is one of the closed-enum REDACTION_REASONS at
``src/roam/evidence/_vocabulary.py``.

The legacy private names (``_redact_secrets``, ``_scrub_actor_block``,
``_SECRET_PATTERNS``) remain importable from ``cmd_pr_bundle`` as thin
re-export aliases so existing test fixtures and any direct module-level
access continue to work.
"""

from __future__ import annotations

import re
from typing import Any, Mapping

__all__ = [
    "SECRET_PATTERNS",
    "redact_secrets_in_string",
    "redact_secrets_in_string_with_counts",
    "redact_secrets",
    "scrub_actor_block",
    "redact_secrets_in_value",
    "PROMPT_INJECTION_MARKERS",
    "scan_prompt_injection_markers",
    "scan_prompt_injection_in_value",
]


# ---------------------------------------------------------------------------
# Pattern set
# ---------------------------------------------------------------------------
#
# Producer-boundary patterns. Word-bounded (``\b``) so they don't false-
# positive inside random alnum sequences. Each line documents the
# intentional shape so future audits can verify nothing is a noise
# magnet.
#
# DO NOT change this set without coordinating with the collector's
# layer-2 pattern set at ``roam.evidence.collector._SECRET_PATTERNS``;
# the duplication is deliberate (defense-in-depth) and the golden-hash
# tests in ``tests/test_evidence_redaction_snapshots.py`` pin the
# observable behavior.
SECRET_PATTERNS: tuple[re.Pattern[str], ...] = (
    # GitHub PAT (classic prefix is 36 chars; cover 32-40 for forward-compat).
    re.compile(r"\bghp_[A-Za-z0-9_]{32,40}\b"),
    # GitHub fine-grained PAT (82 chars; +/- a small tolerance window).
    re.compile(r"\bgithub_pat_\w{80,90}\b"),
    # OpenAI / Anthropic-shaped keys.
    re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),  # AWS access key ID
    re.compile(r"\bBearer [A-Za-z0-9\-._~+/]+=*\b"),  # Bearer token
    re.compile(r"-----BEGIN [A-Z ]+ PRIVATE KEY-----"),  # PEM private key marker
    re.compile(r"\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\b"),  # JWT
)

# Stable identifier per pattern. Used by the MCP receipt-egress wrapper to
# populate ``extra["redaction_details"]`` with per-pattern hit counts.
# Order-aligned with SECRET_PATTERNS by construction; keep them in sync.
_PATTERN_IDS: dict[re.Pattern[str], str] = {
    SECRET_PATTERNS[0]: "github_pat_classic",
    SECRET_PATTERNS[1]: "github_pat_fine_grained",
    SECRET_PATTERNS[2]: "sk_prefix",
    SECRET_PATTERNS[3]: "aws_access_key",
    SECRET_PATTERNS[4]: "bearer_token",
    SECRET_PATTERNS[5]: "pem_private_key",
    SECRET_PATTERNS[6]: "jwt",
}


# ---------------------------------------------------------------------------
# Scrubbers
# ---------------------------------------------------------------------------


def redact_secrets_in_string(value: str) -> tuple[str, bool]:
    """Scrub known secret patterns from a string.

    Returns ``(redacted_value, had_secrets)``. When ``value`` is empty
    or not a string, returns it unchanged and ``had_secrets=False`` so
    callers can pipe anything through. Substring matches are replaced
    with the literal token ``[REDACTED]``; the producer should append
    ``"secret"`` to the envelope's ``redactions[]`` array when
    ``had_secrets`` is True.

    The scrub is idempotent on already-scrubbed values -- the
    ``[REDACTED]`` placeholder matches none of the patterns above so a
    second pass is a no-op.
    """
    if not value or not isinstance(value, str):
        return value, False
    redacted = value
    found_secret = False
    for pattern in SECRET_PATTERNS:
        if pattern.search(redacted):
            redacted = pattern.sub("[REDACTED]", redacted)
            found_secret = True
    return redacted, found_secret


def redact_secrets_in_string_with_counts(value: str) -> tuple[str, dict[str, int]]:
    """Scrub known secret patterns from a string AND report what was hit.

    Same scrub semantics as :func:`redact_secrets_in_string` (idempotent,
    word-bounded patterns, ``[REDACTED]`` placeholder, ride-through on
    empty / non-string), but instead of a single boolean fired flag,
    returns a ``{pattern_id: hit_count}`` dict.

    The ``pattern_id`` is a short stable identifier derived from the
    pattern's source — see :data:`_PATTERN_IDS` below for the closed
    mapping. The dict is empty when nothing fired (callers should treat
    "empty" as "no secrets detected" — equivalent to the
    ``had_secrets=False`` arm of :func:`redact_secrets_in_string`).

    Used by the MCP receipt-egress wrapper (W195+) to populate the
    ``redactions[]`` audit-trail with per-pattern detail beyond the
    closed-enum ``"secret"`` reason.
    """
    if not value or not isinstance(value, str):
        return value, {}
    redacted = value
    counts: dict[str, int] = {}
    for pattern in SECRET_PATTERNS:
        hits = pattern.findall(redacted)
        if hits:
            counts[_PATTERN_IDS[pattern]] = len(hits)
            redacted = pattern.sub("[REDACTED]", redacted)
    return redacted, counts


def redact_secrets_in_value(value: Any) -> tuple[Any, dict[str, int]]:
    """Recursively scrub secret patterns inside any JSON-ish value.

    Walks dicts, lists, and tuples; redacts string leaves; rides
    non-string scalars (int / float / bool / None) through untouched.
    Returns ``(redacted_value, aggregate_counts)`` where
    ``aggregate_counts`` sums every pattern hit across the walk.

    The returned container has the same structural shape as the input
    (dict stays dict, list stays list, tuple stays tuple) so callers can
    pass the redacted value through unchanged downstream paths (e.g.,
    JSON canonicalisation for hashing).
    """
    if isinstance(value, str):
        redacted_str, hits = redact_secrets_in_string_with_counts(value)
        return redacted_str, dict(hits)
    if isinstance(value, dict):
        out_dict: dict = {}
        agg: dict[str, int] = {}
        for k, v in value.items():
            new_v, sub_hits = redact_secrets_in_value(v)
            out_dict[k] = new_v
            for pid, n in sub_hits.items():
                agg[pid] = agg.get(pid, 0) + n
        return out_dict, agg
    if isinstance(value, list):
        out_list: list = []
        agg = {}
        for item in value:
            new_item, sub_hits = redact_secrets_in_value(item)
            out_list.append(new_item)
            for pid, n in sub_hits.items():
                agg[pid] = agg.get(pid, 0) + n
        return out_list, agg
    if isinstance(value, tuple):
        out_items: list = []
        agg = {}
        for item in value:
            new_item, sub_hits = redact_secrets_in_value(item)
            out_items.append(new_item)
            for pid, n in sub_hits.items():
                agg[pid] = agg.get(pid, 0) + n
        return tuple(out_items), agg
    # Scalars (int, float, bool, None, etc.) — ride through.
    return value, {}


def redact_secrets(value: Any) -> tuple[Any, bool]:
    """Scrub known secret patterns from a free-form value.

    Wrapper around :func:`redact_secrets_in_string` that rides non-
    string values through untouched (e.g. ``None`` placeholders for
    ``tool_id``, integers, lists). Same return contract:
    ``(redacted_value, had_secrets)``.
    """
    return redact_secrets_in_string(value) if isinstance(value, str) else (value, False)


def scrub_actor_block(actor: Mapping[str, Any] | None) -> tuple[dict, bool]:
    """Run :func:`redact_secrets` over every field on the actor block.

    Returns ``(scrubbed_actor, had_secrets)``. Non-string fields (e.g.
    ``None`` placeholders for ``tool_id``) ride through untouched. The
    return type is always a ``dict`` (a shallow copy) when the input is
    a mapping, so callers can mutate the result freely without aliasing
    the producer's original.
    """
    if not isinstance(actor, Mapping):
        return actor, False  # type: ignore[return-value]
    scrubbed: dict = {}
    found_any = False
    for key, value in actor.items():
        new_value, hit = redact_secrets(value)
        if hit:
            found_any = True
        scrubbed[key] = new_value
    return scrubbed, found_any


# ---------------------------------------------------------------------------
# Prompt-injection marker scan (MCP-P1.2)
# ---------------------------------------------------------------------------
#
# A *conservative* marker set scanned over MCP tool-call output bytes at the
# egress boundary. Unlike :data:`SECRET_PATTERNS`, a marker hit does NOT mask
# anything — a prompt-injection string is a *signal*, not a credential. The
# egress scan leaves the output bytes intact and only stamps the closed-enum
# reason ``"prompt_injection_marker"`` onto the MCP receipt's ``redactions[]``
# audit trail, so a downstream gateway / host can escalate or quarantine.
#
# Design constraint: false positives here are costly. roam tool output is
# codebase intelligence — symbol names, code snippets, file paths, git
# history — so every pattern below is chosen to be *very unlikely* to appear
# in legitimate analysis output:
#
#  1. The canonical override phrases ("ignore previous instructions",
#     "disregard all prior instructions"). Free-text imperative sentences
#     that do not occur in code identifiers or AST-derived envelopes.
#  2. Chat-template control tokens (``<|im_start|>``, ``<|im_end|>``,
#     ``<|endoftext|>``, ``[INST]`` / ``[/INST]``, ``<<SYS>>``). These are
#     model-serialisation delimiters; they should never be embedded in a
#     source symbol or a roam JSON envelope.
#  3. Fake conversation-turn headers — a line that *starts* with
#     ``system:`` or ``assistant:`` followed by content. Anchored to
#     line-start (``re.MULTILINE``) and deliberately limited to the two
#     roles an injected payload spoofs to seize control; the much more
#     common ``user:`` is intentionally EXCLUDED because it is a frequent
#     YAML / config key and would false-positive.
#  4. Tool-result spoofing tags (``</tool_result>``, ``<tool_result>``,
#     ``</function_results>``) — an injected payload forging the end of a
#     tool result to smuggle a new instruction past the boundary.
#
# All matching is case-insensitive. The set is deliberately small; widening
# it is a source-level edit that must justify the marginal false-positive
# cost. Coordinate any change with the MCP-P1.2 tests.
PROMPT_INJECTION_MARKERS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "ignore_previous_instructions",
        re.compile(
            r"\b(?:ignore|disregard|forget)\s+(?:all\s+)?(?:the\s+)?"
            r"(?:previous|prior|above|earlier)\s+(?:instructions?|prompts?|messages?|context)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "chat_template_control_token",
        re.compile(
            r"<\|(?:im_start|im_end|endoftext|system|user|assistant)\|>"
            r"|\[/?INST\]"
            r"|<</?SYS>>",
            re.IGNORECASE,
        ),
    ),
    (
        "spoofed_turn_header",
        re.compile(r"^[ \t]*(?:system|assistant)[ \t]*:[ \t]*\S", re.IGNORECASE | re.MULTILINE),
    ),
    (
        "tool_result_spoof",
        re.compile(r"</?(?:tool_result|function_results|function_calls)>", re.IGNORECASE),
    ),
)


def scan_prompt_injection_markers(value: str) -> dict[str, int]:
    """Scan a string for known prompt-injection markers (MCP-P1.2).

    Returns a ``{marker_id: hit_count}`` dict naming every marker that
    fired and how many times. The dict is empty when nothing matched
    (callers treat "empty" as "no markers detected"). Empty / non-string
    input rides through as an empty dict so callers can pipe anything.

    This function NEVER alters ``value`` — a prompt-injection marker is a
    signal, not a secret. The caller stamps the closed-enum reason
    ``"prompt_injection_marker"`` onto the receipt ``redactions[]`` array
    when the returned dict is non-empty; the offending bytes are left
    intact for the downstream gateway / host to inspect.
    """
    if not value or not isinstance(value, str):
        return {}
    counts: dict[str, int] = {}
    for marker_id, pattern in PROMPT_INJECTION_MARKERS:
        hits = pattern.findall(value)
        if hits:
            counts[marker_id] = len(hits)
    return counts


def scan_prompt_injection_in_value(value: Any) -> dict[str, int]:
    """Recursively scan any JSON-ish value for prompt-injection markers.

    Walks dicts, lists, and tuples; scans string leaves; rides non-string
    scalars (int / float / bool / None) through untouched. Returns an
    aggregate ``{marker_id: hit_count}`` dict summing every marker hit
    across the walk.

    Like :func:`scan_prompt_injection_markers`, this is non-mutating: it
    inspects the value and reports, it never rewrites it.
    """
    if isinstance(value, str):
        return scan_prompt_injection_markers(value)
    agg: dict[str, int] = {}
    if isinstance(value, dict):
        for v in value.values():
            for mid, n in scan_prompt_injection_in_value(v).items():
                agg[mid] = agg.get(mid, 0) + n
        return agg
    if isinstance(value, (list, tuple)):
        for item in value:
            for mid, n in scan_prompt_injection_in_value(item).items():
                agg[mid] = agg.get(mid, 0) + n
        return agg
    # Scalars (int, float, bool, None, etc.) — ride through.
    return agg
