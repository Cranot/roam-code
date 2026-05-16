"""W1255 - canonical config-file hashes for evidence_stale drift detection.

Tests the new :mod:`roam.evidence.config_hashes` module + its wiring into
:func:`roam.runs.ledger.start_run`.

Coverage:

* All three canonical hashes compute correctly when the files are
  present (uses a temp directory; no fixture pollution).
* Missing files hash to ``""`` without raising.
* Same bytes -> same hash (determinism, sanity-check sha256 contract).
* ``stamp_all`` returns a dict keyed by the three W210 field names.
* End-to-end: ``start_run`` writes the three hashes into ``meta.json``
  (via ``RunMeta.extra``) so the collector can lift them onto
  ChangeEvidence.
* Hash-stability sanity: a ChangeEvidence packet with all three hash
  fields at their default (``None``) produces byte-identical canonical
  JSON to a packet constructed without touching the W210 fields at all
  (proves the W210 omit-when-default discipline still holds, so W1255
  cannot regress backward-compat for pre-W210 / W210-unstamped packets).
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from roam.evidence import ChangeEvidence
from roam.evidence.config_hashes import (
    CANONICAL_PATHS,
    compute_config_hash,
    stamp_all,
)
from roam.runs.ledger import start_run

# ---------------------------------------------------------------------------
# 1. compute_config_hash on present files
# ---------------------------------------------------------------------------


def test_compute_config_hash_present_file(tmp_path: Path) -> None:
    """sha256 of file bytes matches hashlib reference."""
    payload = b"version: 1\nrules: []\n"
    (tmp_path / ".roam-rules.yml").write_bytes(payload)

    got = compute_config_hash(tmp_path, ".roam-rules.yml")
    expected = hashlib.sha256(payload).hexdigest()
    assert got == expected
    assert len(got) == 64  # sha256 hex
    assert all(c in "0123456789abcdef" for c in got)


def test_compute_config_hash_nested_path(tmp_path: Path) -> None:
    """Nested .roam/constitution.yml resolves correctly."""
    (tmp_path / ".roam").mkdir()
    payload = b"laws:\n  - never push to main\n"
    (tmp_path / ".roam" / "constitution.yml").write_bytes(payload)

    got = compute_config_hash(tmp_path, ".roam/constitution.yml")
    assert got == hashlib.sha256(payload).hexdigest()


# ---------------------------------------------------------------------------
# 2. Missing file -> empty string (insufficient-data discipline)
# ---------------------------------------------------------------------------


def test_compute_config_hash_missing_file_returns_empty(tmp_path: Path) -> None:
    """Missing file returns '' and does NOT raise."""
    # tmp_path is empty - no .roam-rules.yml on disk.
    got = compute_config_hash(tmp_path, ".roam-rules.yml")
    assert got == "", "missing file must return empty string, not None or a fake hash"


def test_compute_config_hash_missing_nested_dir(tmp_path: Path) -> None:
    """Missing .roam/ directory is also handled cleanly."""
    got = compute_config_hash(tmp_path, ".roam/control-map.yml")
    assert got == ""


# ---------------------------------------------------------------------------
# 3. Determinism: identical content -> identical hash
# ---------------------------------------------------------------------------


def test_compute_config_hash_determinism(tmp_path: Path) -> None:
    """Same bytes hashed twice produce the same digest."""
    payload = b"deterministic content"
    (tmp_path / ".roam-rules.yml").write_bytes(payload)

    h1 = compute_config_hash(tmp_path, ".roam-rules.yml")
    h2 = compute_config_hash(tmp_path, ".roam-rules.yml")
    assert h1 == h2

    # And different content -> different hash.
    (tmp_path / ".roam-rules.yml").write_bytes(b"different content")
    h3 = compute_config_hash(tmp_path, ".roam-rules.yml")
    assert h3 != h1


# ---------------------------------------------------------------------------
# 4. stamp_all returns the three canonical field names
# ---------------------------------------------------------------------------


def test_stamp_all_returns_all_three_keys(tmp_path: Path) -> None:
    """stamp_all returns a dict keyed by the W210 field names."""
    got = stamp_all(tmp_path)
    assert set(got.keys()) == {
        "rules_config_hash",
        "constitution_hash",
        "control_map_hash",
    }
    # All three absent on a fresh tmp_path -> all three empty.
    assert all(v == "" for v in got.values())


def test_stamp_all_with_files_populated(tmp_path: Path) -> None:
    """stamp_all returns real hashes when the files are on disk."""
    (tmp_path / ".roam").mkdir()
    (tmp_path / ".roam-rules.yml").write_bytes(b"rules\n")
    (tmp_path / ".roam" / "constitution.yml").write_bytes(b"constitution\n")
    (tmp_path / ".roam" / "control-map.yml").write_bytes(b"controls\n")

    got = stamp_all(tmp_path)
    assert got["rules_config_hash"] == hashlib.sha256(b"rules\n").hexdigest()
    assert got["constitution_hash"] == hashlib.sha256(b"constitution\n").hexdigest()
    assert got["control_map_hash"] == hashlib.sha256(b"controls\n").hexdigest()


def test_canonical_paths_match_field_names() -> None:
    """CANONICAL_PATHS keys must match the W210 ChangeEvidence fields."""
    expected_fields = {"rules_config_hash", "constitution_hash", "control_map_hash"}
    assert set(CANONICAL_PATHS.keys()) == expected_fields
    # Spot-check the relative-path values are the canonical layout.
    assert CANONICAL_PATHS["rules_config_hash"] == ".roam-rules.yml"
    assert CANONICAL_PATHS["constitution_hash"] == ".roam/constitution.yml"
    assert CANONICAL_PATHS["control_map_hash"] == ".roam/control-map.yml"


# ---------------------------------------------------------------------------
# 5. End-to-end: start_run stamps the hashes into meta.json
# ---------------------------------------------------------------------------


def test_start_run_stamps_config_hashes_into_meta(tmp_path: Path) -> None:
    """start_run writes the three hashes via RunMeta.extra -> meta.json."""
    proj = tmp_path / "stamped"
    proj.mkdir()
    (proj / ".roam").mkdir()
    rules_bytes = b"rules: []\n"
    constitution_bytes = b"laws: []\n"
    (proj / ".roam-rules.yml").write_bytes(rules_bytes)
    (proj / ".roam" / "constitution.yml").write_bytes(constitution_bytes)
    # control-map.yml deliberately missing -> empty-string hash.

    meta = start_run(proj, agent="claude-code")

    meta_path = proj / ".roam" / "runs" / meta.run_id / "meta.json"
    on_disk = json.loads(meta_path.read_text(encoding="utf-8"))

    assert on_disk["rules_config_hash"] == hashlib.sha256(rules_bytes).hexdigest()
    assert on_disk["constitution_hash"] == hashlib.sha256(constitution_bytes).hexdigest()
    assert on_disk["control_map_hash"] == ""


def test_start_run_with_no_configs_still_stamps_empty_strings(tmp_path: Path) -> None:
    """No config files on disk -> all three hashes are empty strings."""
    proj = tmp_path / "bare"
    proj.mkdir()

    meta = start_run(proj, agent="claude-code")

    meta_path = proj / ".roam" / "runs" / meta.run_id / "meta.json"
    on_disk = json.loads(meta_path.read_text(encoding="utf-8"))

    assert on_disk["rules_config_hash"] == ""
    assert on_disk["constitution_hash"] == ""
    assert on_disk["control_map_hash"] == ""


# ---------------------------------------------------------------------------
# 6. Backward-compat: an unstamped ChangeEvidence still produces the
#    same canonical JSON as before W1255 (W210 omit-when-default holds).
# ---------------------------------------------------------------------------


def test_w210_omit_when_default_unchanged_by_w1255() -> None:
    """W210 omit-when-default discipline is unaffected by the W1255 wire-up.

    A ChangeEvidence packet that does NOT populate any of the three
    W210 hash fields must produce byte-identical canonical JSON to one
    that explicitly sets them to their default (``None``). This is the
    invariant that lets pre-W210 stored content_hash values stay valid
    after W1255 ships.
    """
    pkt_default = ChangeEvidence(evidence_id="ev_w1255")
    pkt_explicit = ChangeEvidence(
        evidence_id="ev_w1255",
        rules_config_hash=None,
        constitution_hash=None,
        control_map_hash=None,
    )
    assert pkt_default.to_canonical_json() == pkt_explicit.to_canonical_json()
    # And the three field names are absent from the canonical JSON (omit).
    canonical = json.loads(pkt_default.to_canonical_json())
    assert "rules_config_hash" not in canonical
    assert "constitution_hash" not in canonical
    assert "control_map_hash" not in canonical
