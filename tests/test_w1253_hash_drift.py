"""W1253 - consumer-side wire-up: config-hash drift detection.

W1255-IMPL stamped the three canonical config hashes (``rules_config_hash``
/ ``constitution_hash`` / ``control_map_hash``) into ``RunMeta.extra`` at
run-start time. W1253 is the consumer side: :func:`collect_change_evidence`
takes the packet-stamped hashes plus the current on-disk hashes and flips
``evidence_stale=True`` (with a ``stale_reasons`` entry naming the drifted
field) whenever any of the three drift.

Test scope:

* Matching hashes -> no drift, ``evidence_stale`` unchanged.
* Mismatched hash -> drift detected, ``evidence_stale=True``,
  ``stale_reasons`` populated with the field name + truncated hashes.
* Missing packet hash -> no drift (insufficient data, NOT a positive
  staleness signal - mirrors W1234 discipline).
* Missing current hash -> no drift (insufficient data, same rule).
* Hash drift COMBINES with W1234 timestamp staleness: both signals
  contribute to ``stale_reasons``.
* Hash-stability: a packet with NEITHER side of the W1253 kwargs
  produces byte-identical canonical JSON to a pre-W1253 packet
  (W210 omit-when-default discipline intact).
"""

from __future__ import annotations

import json

from roam.evidence import ChangeEvidence, collect_change_evidence

# ---------------------------------------------------------------------------
# 1. Matching hashes -> no drift verdict
# ---------------------------------------------------------------------------


def test_matching_hashes_no_drift() -> None:
    """Packet hashes equal current hashes -> evidence_stale stays False."""
    matched = {
        "rules_config_hash": "a" * 64,
        "constitution_hash": "b" * 64,
        "control_map_hash": "c" * 64,
    }
    packet, _ = collect_change_evidence(
        packet_config_hashes=matched,
        current_config_hashes=matched,
    )

    # No drift, no W1234 staleness inputs either -> evidence_stale=False.
    assert packet.evidence_stale is False
    assert packet.stale_reasons == ()

    # Packet records the packet-stamped hashes for audit-time re-check.
    assert packet.rules_config_hash == "a" * 64
    assert packet.constitution_hash == "b" * 64
    assert packet.control_map_hash == "c" * 64


# ---------------------------------------------------------------------------
# 2. Mismatched hash -> drift detected
# ---------------------------------------------------------------------------


def test_mismatched_rules_config_hash_drift_detected() -> None:
    """One drifted field flips evidence_stale and names the field."""
    packet_h = {
        "rules_config_hash": "1" * 64,  # was at run-start
        "constitution_hash": "2" * 64,
        "control_map_hash": "3" * 64,
    }
    current_h = {
        "rules_config_hash": "9" * 64,  # drifted on disk
        "constitution_hash": "2" * 64,
        "control_map_hash": "3" * 64,
    }
    packet, _ = collect_change_evidence(
        packet_config_hashes=packet_h,
        current_config_hashes=current_h,
    )

    assert packet.evidence_stale is True
    assert len(packet.stale_reasons) == 1
    reason = packet.stale_reasons[0]
    # The reason names the field + truncated hashes (first 12 hex chars).
    assert "rules_config_hash" in reason
    assert "mismatch" in reason
    assert "111111111111" in reason  # packet hash prefix
    assert "999999999999" in reason  # current hash prefix

    # The packet records the PACKET-stamped value (run-start), not the
    # current on-disk drift value. Audit-time consumers re-compute the
    # on-disk hash and compare against this record.
    assert packet.rules_config_hash == "1" * 64


def test_all_three_hashes_drift_three_reasons() -> None:
    """When all three fields drift, all three reasons land."""
    packet_h = {
        "rules_config_hash": "a" * 64,
        "constitution_hash": "b" * 64,
        "control_map_hash": "c" * 64,
    }
    current_h = {
        "rules_config_hash": "d" * 64,
        "constitution_hash": "e" * 64,
        "control_map_hash": "f" * 64,
    }
    packet, _ = collect_change_evidence(
        packet_config_hashes=packet_h,
        current_config_hashes=current_h,
    )

    assert packet.evidence_stale is True
    assert len(packet.stale_reasons) == 3
    # Stable order matches _CONFIG_HASH_FIELDS: rules first, constitution
    # second, control-map third (same order as the dataclass fields).
    field_order = ("rules_config_hash", "constitution_hash", "control_map_hash")
    for reason, expected_field in zip(packet.stale_reasons, field_order):
        assert reason.startswith(expected_field), f"reason {reason!r} should start with {expected_field!r}"


# ---------------------------------------------------------------------------
# 3. Insufficient-data discipline: missing one side -> no drift
# ---------------------------------------------------------------------------


def test_missing_packet_hash_no_drift() -> None:
    """Empty packet hash on a field -> no drift verdict for that field."""
    # Only constitution differs, but rules has an empty packet hash so
    # rules cannot drift (insufficient data). Control-map matches.
    packet_h = {
        "rules_config_hash": "",  # absent at run-start (W1255 sentinel)
        "constitution_hash": "2" * 64,
        "control_map_hash": "3" * 64,
    }
    current_h = {
        "rules_config_hash": "9" * 64,  # on disk now, but cannot drift-detect
        "constitution_hash": "8" * 64,  # drifted
        "control_map_hash": "3" * 64,
    }
    packet, _ = collect_change_evidence(
        packet_config_hashes=packet_h,
        current_config_hashes=current_h,
    )

    # Only constitution drift fires; rules cannot drift (empty packet hash).
    assert packet.evidence_stale is True
    assert len(packet.stale_reasons) == 1
    assert packet.stale_reasons[0].startswith("constitution_hash")

    # The empty packet hash collapses to None on the packet field so the
    # W210 omit-when-default rule kicks in. Constitution + control-map
    # are preserved verbatim.
    assert packet.rules_config_hash is None
    assert packet.constitution_hash == "2" * 64
    assert packet.control_map_hash == "3" * 64


def test_missing_current_hash_no_drift() -> None:
    """Empty current hash on a field -> no drift verdict for that field."""
    packet_h = {
        "rules_config_hash": "1" * 64,
        "constitution_hash": "2" * 64,
        "control_map_hash": "3" * 64,
    }
    current_h = {
        "rules_config_hash": "",  # config file deleted between run & collect
        "constitution_hash": "8" * 64,  # drifted
        "control_map_hash": "3" * 64,
    }
    packet, _ = collect_change_evidence(
        packet_config_hashes=packet_h,
        current_config_hashes=current_h,
    )

    # Only constitution drift fires; rules cannot drift (empty current hash).
    assert packet.evidence_stale is True
    assert len(packet.stale_reasons) == 1
    assert packet.stale_reasons[0].startswith("constitution_hash")


def test_both_kwargs_omitted_no_drift_no_hash_fields() -> None:
    """No W1253 kwargs at all -> no drift, no hash fields on the packet."""
    packet, _ = collect_change_evidence()
    assert packet.evidence_stale is False
    assert packet.stale_reasons == ()
    assert packet.rules_config_hash is None
    assert packet.constitution_hash is None
    assert packet.control_map_hash is None


# ---------------------------------------------------------------------------
# 4. Hash drift + W1234 timestamp staleness COMBINE
# ---------------------------------------------------------------------------


def test_hash_drift_combines_with_timestamp_staleness() -> None:
    """Both signals contribute to stale_reasons; either flips evidence_stale."""
    # W1234 stale: context-read post-dates edits-started.
    events = [
        {"ts": "2026-05-16T09:00:00Z", "seq": 1, "run_id": "run_w1253_x", "action": "preflight"},
        {"ts": "2026-05-16T09:30:00Z", "seq": 2, "run_id": "run_w1253_x", "action": "diff"},
        {"ts": "2026-05-16T10:00:00Z", "seq": 3, "run_id": "run_w1253_x", "action": "impact"},
    ]
    # W1253 stale: constitution hash drifted.
    packet_h = {
        "rules_config_hash": "a" * 64,
        "constitution_hash": "b" * 64,
        "control_map_hash": "c" * 64,
    }
    current_h = {
        "rules_config_hash": "a" * 64,
        "constitution_hash": "9" * 64,  # drifted
        "control_map_hash": "c" * 64,
    }
    packet, _ = collect_change_evidence(
        run_events=events,
        packet_config_hashes=packet_h,
        current_config_hashes=current_h,
    )

    assert packet.evidence_stale is True
    # 1 W1234 timestamp reason + 1 W1253 hash reason = 2 reasons total.
    assert len(packet.stale_reasons) == 2
    # W1234 reasons land first (preserved order), W1253 hash drift second.
    assert "context_read_at" in packet.stale_reasons[0]
    assert "constitution_hash" in packet.stale_reasons[1]


# ---------------------------------------------------------------------------
# 5. Hash-stability: pre-W1253 packets stay byte-identical
# ---------------------------------------------------------------------------


def test_w210_omit_when_default_unchanged_by_w1253() -> None:
    """W210 omit-when-default holds when no W1253 kwargs are passed.

    A packet built without the two new W1253 kwargs must produce
    byte-identical canonical JSON to a packet constructed via
    :class:`ChangeEvidence` with all three hash fields at their default
    (``None``). This is the invariant that lets pre-W1253 stored
    content_hash values stay valid after this wave ships.
    """
    collected, _ = collect_change_evidence()
    baseline = ChangeEvidence(
        evidence_id=collected.evidence_id,
        actor_refs=collected.actor_refs,
        authority_refs=collected.authority_refs,
        environment_refs=collected.environment_refs,
        roam_version=collected.roam_version,
    ).with_content_hash()

    assert collected.to_canonical_json() == baseline.to_canonical_json()
    assert collected.content_hash == baseline.content_hash

    # The three hash fields are absent from canonical JSON (omitted).
    parsed = json.loads(collected.to_canonical_json())
    for k in ("rules_config_hash", "constitution_hash", "control_map_hash"):
        assert k not in parsed
