"""W364 - Tests for the shared producer-boundary secret-redaction module.

The helpers at ``roam.security.redact`` were extracted from
``cmd_pr_bundle`` so any future producer (W363 MCP wrappers, third-
party plugins) can scrub identically without duplicating the pattern
set. These tests pin the public contract:

* :func:`redact_secrets_in_string` - basic pattern detection, fired
  flag correctness, idempotence on already-scrubbed input.
* :func:`redact_secrets` - rides non-string values through untouched.
* :func:`scrub_actor_block` - iterates every field on the actor block
  (no per-key filter on the producer side, see W364 task notes).

Behavior MUST stay byte-identical to the pre-W364 ``cmd_pr_bundle``
helpers - the golden-hash tests in
``tests/test_evidence_redaction_snapshots.py`` validate the
end-to-end pipeline, and these unit tests validate the seams.
"""

from __future__ import annotations

import pytest

from roam.security.redact import (
    SECRET_PATTERNS,
    redact_secrets,
    redact_secrets_in_string,
    scrub_actor_block,
)


# ---------------------------------------------------------------------------
# redact_secrets_in_string
# ---------------------------------------------------------------------------


def test_redact_secrets_in_string_basic() -> None:
    """A GitHub PAT must be replaced with the ``[REDACTED]`` token."""
    token = "ghp_abc1234567890abc1234567890abc12345678"
    haystack = f"SAFE - emitted with PAT {token}"
    redacted, fired = redact_secrets_in_string(haystack)
    assert fired is True
    assert token not in redacted
    assert "[REDACTED]" in redacted


def test_redact_secrets_in_string_returns_fired_flag() -> None:
    """``fired`` is True when any pattern matches, False otherwise."""
    # No secret -> fired False, value unchanged.
    redacted, fired = redact_secrets_in_string("plain text, no secrets here")
    assert fired is False
    assert redacted == "plain text, no secrets here"

    # JWT pattern fires.
    jwt = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxIn0.signature_part_here"
    redacted, fired = redact_secrets_in_string(jwt)
    assert fired is True
    assert "[REDACTED]" in redacted

    # AWS access key ID fires.
    aws = "AKIAIOSFODNN7EXAMPLE"
    redacted, fired = redact_secrets_in_string(aws)
    assert fired is True
    assert aws not in redacted


def test_redact_secrets_in_string_handles_empty_and_non_string() -> None:
    """Empty and non-string inputs ride through unchanged with fired=False."""
    assert redact_secrets_in_string("") == ("", False)
    # Non-string fed via redact_secrets (the Any-typed wrapper) must not
    # raise - the string-typed helper is contract-narrower but still
    # ride-through-safe for empty string.
    assert redact_secrets(None) == (None, False)
    assert redact_secrets(42) == (42, False)
    assert redact_secrets(["a", "list"]) == (["a", "list"], False)


def test_idempotent_after_redaction() -> None:
    """Re-running the scrub on an already-scrubbed string is a no-op.

    The W249 collector relies on this: the layer-2 scrub runs a second
    pass on ingest, and the ``[REDACTED]`` placeholder must contain no
    patterns that match any secret regex - otherwise repeated scrubs
    would corrupt the placeholder.
    """
    token = "ghp_abc1234567890abc1234567890abc12345678"
    first_pass, fired_1 = redact_secrets_in_string(f"prefix {token} suffix")
    assert fired_1 is True
    second_pass, fired_2 = redact_secrets_in_string(first_pass)
    assert fired_2 is False, "second pass must not fire on scrubbed input"
    assert second_pass == first_pass


# ---------------------------------------------------------------------------
# redact_secrets (Any-typed wrapper)
# ---------------------------------------------------------------------------


def test_redact_secrets_dict_mutation() -> None:
    """``redact_secrets`` wraps the string helper; dict-shaped envelope
    mutation is the caller's responsibility.

    Pre-W364 callers (cmd_pr_bundle) pass each individual string field
    (verdict, actor[*]) through ``redact_secrets`` and reassign on the
    envelope themselves. This test pins that the helper returns the
    scrubbed value + the fired flag so the caller can decide whether to
    stamp ``redactions: ["secret"]`` on the envelope.
    """
    envelope: dict = {
        "verdict": "SAFE - PAT ghp_abc1234567890abc1234567890abc12345678",
        "summary": "no secret here",
    }
    redactions: list[str] = []
    for key, value in list(envelope.items()):
        scrubbed, fired = redact_secrets(value)
        envelope[key] = scrubbed
        if fired and "secret" not in redactions:
            redactions.append("secret")

    assert "ghp_" not in envelope["verdict"]
    assert "[REDACTED]" in envelope["verdict"]
    assert envelope["summary"] == "no secret here"
    assert redactions == ["secret"]


# ---------------------------------------------------------------------------
# scrub_actor_block
# ---------------------------------------------------------------------------


def test_scrub_actor_block_handles_8_keys() -> None:
    """Every string field on the actor block must be scrubbed.

    The producer-side scrubber iterates EVERY field on the actor block
    (no per-key filter, unlike the collector's W249 layer-2 scrubber
    which uses a closed key list). This test plants a secret in each of
    the 8 canonical actor-block keys + a non-string ``tool_id`` and
    verifies all string fields are scrubbed.
    """
    token = "ghp_abc1234567890abc1234567890abc12345678"
    actor = {
        "agent_id": f"agent-{token}",
        "human_actor": f"alice+{token}@example.com",
        "mcp_client_id": f"client-{token}",
        "ci_runner_id": f"runner-{token}",
        "tool_id": None,  # Non-string rides through.
        "actor_kind": "agent",
        "display_name": f"Alice {token}",
        "session_id": f"sess-{token}",
    }
    scrubbed, had_secrets = scrub_actor_block(actor)
    assert had_secrets is True
    assert isinstance(scrubbed, dict)
    # Every string field with a planted token must no longer contain it.
    for key, value in scrubbed.items():
        if isinstance(value, str):
            assert token not in value, f"token leaked through field {key!r}"
    # The non-string field rides through untouched.
    assert scrubbed["tool_id"] is None
    # ``actor_kind`` had no token in it; must be preserved verbatim.
    assert scrubbed["actor_kind"] == "agent"


def test_scrub_actor_block_no_secrets_returns_false() -> None:
    """A clean actor block returns fired=False and ride-through values."""
    actor = {
        "agent_id": "agent-alice",
        "human_actor": "alice@example.com",
        "actor_kind": "human",
        "tool_id": None,
    }
    scrubbed, had_secrets = scrub_actor_block(actor)
    assert had_secrets is False
    assert dict(scrubbed) == actor


def test_scrub_actor_block_handles_none() -> None:
    """A ``None`` actor input rides through untouched, fired=False.

    Some producer paths (init / set before emit) have no actor block
    yet; the scrubber must not raise.
    """
    scrubbed, had_secrets = scrub_actor_block(None)
    assert had_secrets is False
    assert scrubbed is None


# ---------------------------------------------------------------------------
# Module surface
# ---------------------------------------------------------------------------


def test_secret_patterns_is_non_empty_tuple() -> None:
    """The pattern set is a closed tuple - constants only, no runtime
    mutation. The exact pattern set is pinned by the golden-hash tests
    in ``tests/test_evidence_redaction_snapshots.py``; here we only
    sanity-check the shape.
    """
    assert isinstance(SECRET_PATTERNS, tuple)
    assert len(SECRET_PATTERNS) >= 7  # 7 patterns at W364 freeze.
    # Each entry is a compiled regex.
    for pattern in SECRET_PATTERNS:
        assert hasattr(pattern, "search")
        assert hasattr(pattern, "sub")


def test_cmd_pr_bundle_backcompat_aliases_present() -> None:
    """The legacy private names must remain importable from
    ``cmd_pr_bundle`` as thin re-export aliases (W364 backward-compat
    promise). Direct module-level access by older tests / call sites
    keeps working.
    """
    from roam.commands import cmd_pr_bundle

    assert cmd_pr_bundle._SECRET_PATTERNS is SECRET_PATTERNS
    assert cmd_pr_bundle._redact_secrets is redact_secrets
    assert cmd_pr_bundle._scrub_actor_block is scrub_actor_block


@pytest.mark.parametrize(
    "secret",
    [
        "ghp_abc1234567890abc1234567890abc12345678",  # GitHub classic PAT
        "sk-1234567890abcdefghij",  # OpenAI / Anthropic sk-
        "AKIAIOSFODNN7EXAMPLE",  # AWS access key ID
        "Bearer eyJhbGciOiJIUzI1NiJ9",  # Bearer token
        "-----BEGIN RSA PRIVATE KEY-----",  # PEM marker
    ],
)
def test_each_pattern_fires(secret: str) -> None:
    """Each pattern in ``SECRET_PATTERNS`` fires on its canonical shape."""
    _, fired = redact_secrets_in_string(f"prefix {secret} suffix")
    assert fired is True, f"no pattern fired on {secret!r}"
