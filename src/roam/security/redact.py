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
