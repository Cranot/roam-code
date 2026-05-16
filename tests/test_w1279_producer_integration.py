"""W1279 - producer wire-up for W1253 config-hash drift detection.

W1255-IMPL stamps the three canonical config hashes into ``RunMeta.extra``
at :func:`roam.runs.ledger.start_run` time. W1253 added the
``packet_config_hashes`` + ``current_config_hashes`` kwargs to
:func:`roam.evidence.collector.collect_change_evidence`. W1279 is the
producer-side glue that lifts the meta hashes and recomputes the
on-disk hashes so the collector's drift detector fires from real
producer paths (pr-bundle emit / pr-replay collector / emit_vsa).

Test scope:

* :func:`lift_packet_hashes` returns the three hashes from a real
  ``meta.json`` written by ``start_run``.
* :func:`lift_packet_hashes` returns ``None`` when ``run_id`` is None
  (no active run -> insufficient-data discipline).
* :func:`lift_packet_hashes` returns ``None`` when the meta file is
  absent (corrupt / missing run dir).
* :func:`current_hashes_or_none` returns the same three keys
  ``stamp_all`` does.
* :func:`gather_hash_kwargs` composes both into a ``**kwargs``-shaped
  dict.
* End-to-end: fresh stamping at run-start -> no drift, packet records
  the stamped hashes. Modifying a config file AFTER ``start_run`` flips
  ``evidence_stale=True`` and names the drifted field in
  ``stale_reasons``.
* Hash-stability: producer wire-up with NO run_id and NO meta produces
  byte-identical canonical JSON to a packet built without the W1279
  helpers at all (W210 omit-when-default discipline preserved).
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from roam.evidence import ChangeEvidence, collect_change_evidence
from roam.evidence.config_hashes_producer import (
    current_hashes_or_none,
    gather_hash_kwargs,
    lift_packet_hashes,
)
from roam.runs.ledger import start_run

# ---------------------------------------------------------------------------
# 1. lift_packet_hashes - happy path
# ---------------------------------------------------------------------------


def test_lift_packet_hashes_reads_meta_extra(tmp_path: Path) -> None:
    """start_run stamps hashes; lift_packet_hashes reads them back."""
    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / ".roam").mkdir()
    rules_bytes = b"rules: []\n"
    (proj / ".roam-rules.yml").write_bytes(rules_bytes)

    meta = start_run(proj, agent="claude-code")

    got = lift_packet_hashes(proj, meta.run_id)
    assert got is not None
    assert got["rules_config_hash"] == hashlib.sha256(rules_bytes).hexdigest()
    # The other two configs are absent -> empty string.
    assert got["constitution_hash"] == ""
    assert got["control_map_hash"] == ""


# ---------------------------------------------------------------------------
# 2. lift_packet_hashes - insufficient-data paths
# ---------------------------------------------------------------------------


def test_lift_packet_hashes_none_run_id_returns_none(tmp_path: Path) -> None:
    """No run_id -> None (NOT an empty dict)."""
    assert lift_packet_hashes(tmp_path, None) is None
    assert lift_packet_hashes(tmp_path, "") is None


def test_lift_packet_hashes_missing_meta_returns_none(tmp_path: Path) -> None:
    """Run id doesn't exist on disk -> None."""
    proj = tmp_path / "noproj"
    proj.mkdir()
    # No .roam/runs/<fake-id>/meta.json exists.
    assert lift_packet_hashes(proj, "run_20260516_deadbeef") is None


# ---------------------------------------------------------------------------
# 3. current_hashes_or_none parity with stamp_all
# ---------------------------------------------------------------------------


def test_current_hashes_or_none_returns_three_keys(tmp_path: Path) -> None:
    """Same key shape as stamp_all - rules / constitution / control-map."""
    got = current_hashes_or_none(tmp_path)
    assert got is not None
    assert set(got.keys()) == {
        "rules_config_hash",
        "constitution_hash",
        "control_map_hash",
    }


# ---------------------------------------------------------------------------
# 4. gather_hash_kwargs composes both lifts
# ---------------------------------------------------------------------------


def test_gather_hash_kwargs_composes_both_sides(tmp_path: Path) -> None:
    """gather_hash_kwargs returns ``**kwargs``-shaped dict for the collector."""
    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / ".roam-rules.yml").write_bytes(b"rules: []\n")
    meta = start_run(proj, agent="claude-code")

    kwargs = gather_hash_kwargs(proj, meta.run_id)
    assert set(kwargs.keys()) == {"packet_config_hashes", "current_config_hashes"}
    assert kwargs["packet_config_hashes"] is not None
    assert kwargs["current_config_hashes"] is not None
    # Stamping was fresh -> packet hashes equal current hashes for the
    # one file that exists.
    assert kwargs["packet_config_hashes"]["rules_config_hash"] == kwargs["current_config_hashes"]["rules_config_hash"]


def test_gather_hash_kwargs_missing_run_yields_none_packet(tmp_path: Path) -> None:
    """No run_id -> packet_config_hashes is None; current side still computes."""
    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / ".roam-rules.yml").write_bytes(b"rules: []\n")

    kwargs = gather_hash_kwargs(proj, None)
    assert kwargs["packet_config_hashes"] is None
    assert kwargs["current_config_hashes"] is not None
    # The on-disk hash is still real.
    assert kwargs["current_config_hashes"]["rules_config_hash"] == hashlib.sha256(b"rules: []\n").hexdigest()


# ---------------------------------------------------------------------------
# 5. End-to-end: fresh stamping -> no drift; modified file -> drift detected
# ---------------------------------------------------------------------------


def test_end_to_end_no_drift_on_fresh_stamping(tmp_path: Path) -> None:
    """Right after start_run, current == packet -> evidence_stale=False."""
    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / ".roam").mkdir()
    (proj / ".roam-rules.yml").write_bytes(b"rules: v1\n")
    (proj / ".roam" / "constitution.yml").write_bytes(b"laws: []\n")

    meta = start_run(proj, agent="claude-code")
    kwargs = gather_hash_kwargs(proj, meta.run_id)
    packet, _ = collect_change_evidence(**kwargs)

    assert packet.evidence_stale is False
    assert packet.stale_reasons == ()
    # The packet records the stamped hashes.
    assert packet.rules_config_hash == hashlib.sha256(b"rules: v1\n").hexdigest()
    assert packet.constitution_hash == hashlib.sha256(b"laws: []\n").hexdigest()


def test_end_to_end_drift_detected_when_config_modified_post_start(
    tmp_path: Path,
) -> None:
    """Modifying a config file AFTER start_run flips evidence_stale=True."""
    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / ".roam").mkdir()
    (proj / ".roam-rules.yml").write_bytes(b"rules: v1\n")

    meta = start_run(proj, agent="claude-code")

    # Simulate config drift: someone edits .roam-rules.yml after the run
    # was stamped. The collector should detect this and flag the packet
    # as stale.
    (proj / ".roam-rules.yml").write_bytes(b"rules: v2-DRIFTED\n")

    kwargs = gather_hash_kwargs(proj, meta.run_id)
    packet, _ = collect_change_evidence(**kwargs)

    assert packet.evidence_stale is True
    assert any("rules_config_hash" in r for r in packet.stale_reasons)
    # The packet records the RUN-START hash (not the drifted current
    # value) - audit-time consumers re-derive the current hash and
    # compare against this record.
    assert packet.rules_config_hash == hashlib.sha256(b"rules: v1\n").hexdigest()


# ---------------------------------------------------------------------------
# 6. Hash-stability: producer wire-up with no inputs -> byte-identical JSON
# ---------------------------------------------------------------------------


def test_producer_no_run_id_preserves_canonical_json_shape(tmp_path: Path) -> None:
    """No run_id + no configs -> packet matches the pre-W1279 baseline.

    A pre-W1253 packet built without the new kwargs produces
    byte-identical canonical JSON to a packet built through the W1279
    helper when the run_id is None AND no config files exist on disk
    (so current_config_hashes is all-empty-strings, which the collector
    skips). This proves the W210 omit-when-default discipline survives
    the wire-up.
    """
    # Path A: pre-W1279, no W1253 kwargs at all.
    pre_pkt, _ = collect_change_evidence()

    # Path B: W1279 helper with no run_id (so packet_config_hashes is
    # None) and no on-disk configs (so all three current hashes are "").
    # The collector skips drift detection on empty strings, so the
    # packet should not flip evidence_stale and should not stamp any of
    # the three hash fields.
    proj = tmp_path / "empty"
    proj.mkdir()
    kwargs = gather_hash_kwargs(proj, None)
    post_pkt, _ = collect_change_evidence(**kwargs)

    # Same canonical JSON => W210 omit-when-default discipline preserved.
    assert pre_pkt.to_canonical_json() == post_pkt.to_canonical_json()
    # Same content hash => stored hashes from pre-W1279 packets stay
    # valid after this wave ships.
    assert pre_pkt.content_hash == post_pkt.content_hash

    # The three hash fields are absent from canonical JSON on both
    # paths (omitted because they collapse to None when current side
    # is all-empty).
    parsed = json.loads(post_pkt.to_canonical_json())
    for k in ("rules_config_hash", "constitution_hash", "control_map_hash"):
        assert k not in parsed


def test_producer_kwargs_omitted_when_helpers_unused_matches_baseline() -> None:
    """A packet built via ChangeEvidence() directly matches collect_change_evidence().

    Sanity check that the test_w1253_hash_drift baseline still holds -
    no regression from the W1279 wire-up module being importable.
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
