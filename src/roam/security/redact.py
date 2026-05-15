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
    "redact_secrets",
    "scrub_actor_block",
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
