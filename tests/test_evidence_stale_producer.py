"""W1234 - producer wire-up for the W210 ``evidence_stale`` flag.

The W210 scaffold added ``context_read_at`` / ``edits_started_at`` /
``edits_completed_at`` + ``evidence_stale`` + ``stale_reasons`` to
:class:`ChangeEvidence` but never wired a producer. W1234 makes
:func:`collect_change_evidence` derive the three change-scope timestamps
from the run-ledger event stream and flip ``evidence_stale`` when the
context-read post-dates the start of edits.

Test scope:

* Normal-order timestamps -> ``evidence_stale=False``.
* Context-read after edits-started -> ``evidence_stale=True`` + a
  ``stale_reasons`` entry naming the comparison.
* Missing timestamps -> ``evidence_stale=False`` (insufficient data,
  NOT a positive staleness signal).
* Hash stability: a packet whose event stream contributes neither
  context-read nor edit events produces byte-identical canonical JSON
  to a packet built with no event stream at all (the W210
  omit-when-default rule must keep the wire shape unchanged).
"""

from __future__ import annotations

import json

from roam.evidence import ChangeEvidence, collect_change_evidence


def test_normal_order_timestamps_evidence_stale_false() -> None:
    """Context-read precedes edits-started -> evidence_stale stays False."""
    events = [
        {"ts": "2026-05-16T10:00:00Z", "seq": 1, "run_id": "run_w1234_a", "action": "preflight"},
        {"ts": "2026-05-16T10:05:00Z", "seq": 2, "run_id": "run_w1234_a", "action": "impact"},
        {"ts": "2026-05-16T10:30:00Z", "seq": 3, "run_id": "run_w1234_a", "action": "diff"},
        {"ts": "2026-05-16T10:45:00Z", "seq": 4, "run_id": "run_w1234_a", "action": "critique"},
    ]
    packet, warnings = collect_change_evidence(run_events=events)

    # Timestamps populated from the event stream.
    assert packet.context_read_at == "2026-05-16T10:05:00Z"  # latest read
    assert packet.edits_started_at == "2026-05-16T10:30:00Z"  # earliest edit
    assert packet.edits_completed_at == "2026-05-16T10:45:00Z"  # latest edit

    # Reads completed BEFORE edits started -> not stale.
    assert packet.evidence_stale is False
    assert packet.stale_reasons == ()

    # No warnings - the events are well-formed.
    assert not any("stale" in w for w in warnings)


def test_context_read_after_edits_started_evidence_stale_true() -> None:
    """Re-running a context-read AFTER edits began flips evidence_stale."""
    events = [
        # Initial context-read.
        {"ts": "2026-05-16T09:00:00Z", "seq": 1, "run_id": "run_w1234_b", "action": "preflight"},
        # Edits begin.
        {"ts": "2026-05-16T09:30:00Z", "seq": 2, "run_id": "run_w1234_b", "action": "diff"},
        # Agent re-reads state AFTER edits started - this is the stale signal.
        {"ts": "2026-05-16T10:00:00Z", "seq": 3, "run_id": "run_w1234_b", "action": "impact"},
        {"ts": "2026-05-16T10:15:00Z", "seq": 4, "run_id": "run_w1234_b", "action": "critique"},
    ]
    packet, _ = collect_change_evidence(run_events=events)

    # Latest context-read is 10:00, earliest edit is 09:30 -> stale.
    assert packet.context_read_at == "2026-05-16T10:00:00Z"
    assert packet.edits_started_at == "2026-05-16T09:30:00Z"
    assert packet.edits_completed_at == "2026-05-16T10:15:00Z"

    assert packet.evidence_stale is True
    # The reason names the precise comparison so a reviewer can audit it.
    assert len(packet.stale_reasons) == 1
    reason = packet.stale_reasons[0]
    assert "context_read_at (2026-05-16T10:00:00Z)" in reason
    assert "edits_started_at (2026-05-16T09:30:00Z)" in reason


def test_missing_timestamps_evidence_stale_false() -> None:
    """Insufficient data: no edit events at all -> evidence_stale stays False.

    Pattern-2 honest-default: missing data is NOT a positive staleness
    signal. The collector returns ``(False, ())`` and the packet's
    canonical JSON OMITS both fields entirely.
    """
    # Only context-read events, no diff / critique / attest / verify.
    events = [
        {"ts": "2026-05-16T11:00:00Z", "seq": 1, "run_id": "run_w1234_c", "action": "preflight"},
        {"ts": "2026-05-16T11:05:00Z", "seq": 2, "run_id": "run_w1234_c", "action": "impact"},
    ]
    packet, _ = collect_change_evidence(run_events=events)

    # context_read_at populated; edits_started_at / edits_completed_at not.
    assert packet.context_read_at == "2026-05-16T11:05:00Z"
    assert packet.edits_started_at is None
    assert packet.edits_completed_at is None

    # Insufficient data -> NOT stale.
    assert packet.evidence_stale is False
    assert packet.stale_reasons == ()


def test_hash_stable_when_no_classifiable_events() -> None:
    """W210 hash-stability: when no events classify into either phase,
    the canonical JSON OMITS all three timestamp fields + the stale
    flag + stale_reasons, producing byte-identical output to a packet
    built without run_events at all.

    This protects every stored pre-W1234 content_hash from drift on
    repos whose run-ledger only contains unclassified actions (e.g.
    ``constitution-init``, ``lease-claim``, ``laws-mine``).
    """
    # Events use actions that are NOT in either classifier allowlist.
    events = [
        {"ts": "2026-05-16T12:00:00Z", "seq": 1, "run_id": "run_w1234_d", "action": "constitution-init"},
        {"ts": "2026-05-16T12:01:00Z", "seq": 2, "run_id": "run_w1234_d", "action": "lease-claim"},
    ]
    packet_with_unclassified_events, _ = collect_change_evidence(
        run_events=events,
    )
    # Build a baseline packet with the SAME wire-affecting inputs but no
    # run_events. The two should serialise identically because the
    # unclassified events contribute neither phase timestamp.
    #
    # NOTE: ``collect_change_evidence`` reads run_ids + started_at /
    # completed_at off the event stream too, so we feed the run_ids /
    # timestamps explicitly into a hand-built ChangeEvidence to match.
    # Use ``with_content_hash`` so both packets carry a content_hash
    # computed off the canonical bytes - hash equality means byte
    # equality.
    baseline_packet = ChangeEvidence(
        evidence_id=packet_with_unclassified_events.evidence_id,
        run_ids=packet_with_unclassified_events.run_ids,
        started_at=packet_with_unclassified_events.started_at,
        completed_at=packet_with_unclassified_events.completed_at,
        actor_refs=packet_with_unclassified_events.actor_refs,
        authority_refs=packet_with_unclassified_events.authority_refs,
        environment_refs=packet_with_unclassified_events.environment_refs,
        roam_version=packet_with_unclassified_events.roam_version,
    ).with_content_hash()

    # The wire bytes match - the W210 omit-when-default rule fires for
    # every new field on the events-bearing packet.
    assert packet_with_unclassified_events.to_canonical_json() == baseline_packet.to_canonical_json()
    # Content hashes match (consequence of byte equality above).
    assert packet_with_unclassified_events.content_hash == baseline_packet.content_hash
    # The W210 keys are absent from the canonical JSON.
    parsed = json.loads(packet_with_unclassified_events.to_canonical_json())
    for k in (
        "context_read_at",
        "edits_started_at",
        "edits_completed_at",
        "evidence_stale",
        "stale_reasons",
    ):
        assert k not in parsed


def test_pr_bundle_subcommand_classification() -> None:
    """``pr-bundle`` is split by ``envelope_command``: init/add-context-*
    classify as context-read; add-test-run / emit classify as edit."""
    events = [
        # Context-read pr-bundle subcommands.
        {
            "ts": "2026-05-16T08:00:00Z",
            "seq": 1,
            "run_id": "run_w1234_e",
            "action": "pr-bundle",
            "envelope_command": "pr-bundle-init",
        },
        {
            "ts": "2026-05-16T08:05:00Z",
            "seq": 2,
            "run_id": "run_w1234_e",
            "action": "pr-bundle",
            "envelope_command": "pr-bundle-add-context-file",
        },
        # Edit pr-bundle subcommands.
        {
            "ts": "2026-05-16T09:00:00Z",
            "seq": 3,
            "run_id": "run_w1234_e",
            "action": "pr-bundle",
            "envelope_command": "pr-bundle-add-test-run",
        },
        {
            "ts": "2026-05-16T09:30:00Z",
            "seq": 4,
            "run_id": "run_w1234_e",
            "action": "pr-bundle",
            "envelope_command": "pr-bundle",
        },
    ]
    packet, _ = collect_change_evidence(run_events=events)

    # Latest context-read pr-bundle subcommand.
    assert packet.context_read_at == "2026-05-16T08:05:00Z"
    # Earliest + latest edit-phase pr-bundle subcommand.
    assert packet.edits_started_at == "2026-05-16T09:00:00Z"
    assert packet.edits_completed_at == "2026-05-16T09:30:00Z"
    assert packet.evidence_stale is False
