"""W183 - ``McpDecisionReceipt`` data-model tests.

Per ``(internal memo)`` §Build delta 4
(lines 130-144). This wave delivers the data model only; CLI /
``mcp_server.py`` emission lives in a follow-up wave.

All tests are pure dataclass exercises - no DB, no filesystem, no MCP
client. The receipt's stable content hash is the single most important
property to lock down here.
"""

from __future__ import annotations

import json

import pytest

from roam.evidence.mcp_receipt import (
    McpDecisionReceipt,
    hash_input_args,
)


# ---------------------------------------------------------------------------
# Canonical JSON / hash stability
# ---------------------------------------------------------------------------


def _sample_receipt(**overrides) -> McpDecisionReceipt:
    """Build a representative receipt; overrides for per-test tweaks."""
    base = dict(
        tool_call="call-abc-001",
        client_id="pid:12345",
        tool_name="roam_preflight",
        actor_ref_id="actor:agent-7",
        declared_side_effects=("read_only",),
        required_mode="read_only",
        input_hash="0" * 64,
        policy_decision="allow",
        output_hash="1" * 64,
        run_event_id="event:run_20260514_abc/0042",
        redactions=("secret",),
        extra={"client_version": "1.4.2"},
    )
    base.update(overrides)
    return McpDecisionReceipt(**base)


def test_receipt_round_trips_canonical_json() -> None:
    """serialize → parse → serialize gives identical bytes."""
    receipt = _sample_receipt()
    first = receipt.to_canonical_json()
    parsed = json.loads(first)
    # Re-dumping the parsed dict with the same conventions must match
    # byte-for-byte.
    redump = json.dumps(parsed, sort_keys=True, separators=(",", ":"))
    assert first == redump


def test_receipt_content_hash_stable() -> None:
    """Two structurally identical receipts produce the same hash."""
    a = _sample_receipt()
    b = _sample_receipt()
    assert a.compute_content_hash() == b.compute_content_hash()
    # Hash is sha256 hex (64 lowercase hex chars)
    digest = a.compute_content_hash()
    assert len(digest) == 64
    assert all(c in "0123456789abcdef" for c in digest)


def test_receipt_content_hash_changes_when_field_changes() -> None:
    """Mutating any field must alter the content hash."""
    a = _sample_receipt()
    b = _sample_receipt(tool_call="call-abc-002")
    assert a.compute_content_hash() != b.compute_content_hash()


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def test_receipt_validates_policy_decision() -> None:
    """Unknown policy_decision raises ValueError."""
    with pytest.raises(ValueError, match="unknown policy_decision"):
        McpDecisionReceipt(
            tool_call="x",
            client_id="y",
            tool_name="roam_foo",
            policy_decision="maybe",
        )


def test_receipt_validates_redactions() -> None:
    """Unknown REDACTION_REASONS raises ValueError."""
    with pytest.raises(ValueError, match="unknown redaction reason"):
        McpDecisionReceipt(
            tool_call="x",
            client_id="y",
            tool_name="roam_foo",
            redactions=("bogus_reason",),
        )


def test_output_ref_and_output_hash_mutually_exclusive() -> None:
    """Setting both output_ref AND output_hash raises ValueError."""
    with pytest.raises(ValueError, match="mutually exclusive"):
        McpDecisionReceipt(
            tool_call="x",
            client_id="y",
            tool_name="roam_foo",
            output_ref="artifact:abc",
            output_hash="0" * 64,
        )


def test_neither_output_ref_nor_output_hash_is_allowed() -> None:
    """Both ``output_ref`` and ``output_hash`` may be None.

    A receipt may be constructed pre-call (e.g. for a deny decision)
    when the output isn't yet known. Neither field set is legal.
    """
    r = McpDecisionReceipt(
        tool_call="x",
        client_id="y",
        tool_name="roam_foo",
        policy_decision="deny",
    )
    assert r.output_ref is None
    assert r.output_hash is None


def test_only_output_ref_is_allowed() -> None:
    """Receipt with only ``output_ref`` (large output, referenced) is fine."""
    r = McpDecisionReceipt(
        tool_call="x",
        client_id="y",
        tool_name="roam_foo",
        output_ref="artifact:big-blob",
    )
    assert r.output_ref == "artifact:big-blob"
    assert r.output_hash is None


def test_only_output_hash_is_allowed() -> None:
    """Receipt with only ``output_hash`` (small inline output) is fine."""
    r = McpDecisionReceipt(
        tool_call="x",
        client_id="y",
        tool_name="roam_foo",
        output_hash="a" * 64,
    )
    assert r.output_hash == "a" * 64
    assert r.output_ref is None


def test_all_known_policy_decisions_accepted() -> None:
    """Every value in the closed enumeration constructs without raising."""
    for decision in ("allow", "deny", "escalate", "redact", "not_evaluated"):
        r = McpDecisionReceipt(
            tool_call="x",
            client_id="y",
            tool_name="roam_foo",
            policy_decision=decision,
        )
        assert r.policy_decision == decision


# ---------------------------------------------------------------------------
# hash_input_args helper
# ---------------------------------------------------------------------------


def test_hash_input_args_deterministic() -> None:
    """Same args (regardless of key order) → same hash."""
    a = hash_input_args({"name": "handleSave", "depth": 2})
    b = hash_input_args({"depth": 2, "name": "handleSave"})
    assert a == b
    assert len(a) == 64  # sha256 hex


def test_hash_input_args_differs_for_different_args() -> None:
    """Different args → different hash."""
    a = hash_input_args({"name": "handleSave"})
    b = hash_input_args({"name": "handleClose"})
    assert a != b


def test_hash_input_args_empty_mapping() -> None:
    """Empty mapping has a stable, well-defined hash."""
    a = hash_input_args({})
    b = hash_input_args({})
    assert a == b
    assert len(a) == 64


# ---------------------------------------------------------------------------
# Package re-exports
# ---------------------------------------------------------------------------


def test_receipt_imports_from_package() -> None:
    """``from roam.evidence import McpDecisionReceipt`` works."""
    from roam.evidence import McpDecisionReceipt as ReExported
    from roam.evidence import hash_input_args as reexported_hash

    assert ReExported is McpDecisionReceipt
    # The helper must be importable from the package root too, and
    # must produce the same hash as the direct-import path.
    sample = {"k": "v", "n": 1}
    assert reexported_hash(sample) == hash_input_args(sample)


def test_receipt_imports_from_package_alt() -> None:
    """Alternative import path: explicit attribute access on the package."""
    import roam.evidence as ev

    assert hasattr(ev, "McpDecisionReceipt")
    assert hasattr(ev, "hash_input_args")
