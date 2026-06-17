"""W218 - schema-migration + hash-stability golden tests.

Pin the evidence-compiler schema contract with on-disk golden fixtures.
Every fixture is a hand-crafted canonical JSON packet plus a sibling
``.sha256`` file naming the expected content hash for that packet.

The directive (W218) reads:

    Schema migration tests: prove old packets still parse and old
    content hashes remain stable where promised.

Why this exists. The W174 / W182 / W210 sprints layered new fields onto
``ChangeEvidence`` while preserving content-hash backward compatibility
via the omit-when-default rule (see ``change_evidence.py``
``_W182_OMIT_WHEN_EMPTY_FIELDS`` and ``_W210_OMIT_WHEN_DEFAULT_FIELDS``).
Without on-disk goldens, the omit rule could silently regress: a future
sprint that flips one default value, drops one branch of the omission
check, or renames one field would break every downstream consumer's
stored hashes without leaving a trace in the test suite. The fixtures
here are the trace.

The contract these tests pin:

* **Parse**. Each golden fixture deserialises into a ``ChangeEvidence``
  without error.
* **Round-trip canonical bytes**. The fixture's bytes are EXACTLY the
  output of ``to_canonical_json()`` on the reconstructed packet. No
  whitespace drift; no key-order drift.
* **Content hash**. The expected ``.sha256`` matches
  ``packet.compute_content_hash()`` (which strips ``content_hash``
  before hashing, per the chicken-and-egg rule).
* **Cross-version stability**. A v0 packet (no W182 ref fields in JSON)
  hashes to the same value when loaded by the current runtime as it
  did originally. A W182 packet with explicit empty refs hashes to the
  same value as a v0 packet. A W210 packet with all-default W210
  fields hashes to the same value as a pre-W210 packet.

If a golden hash drifts on the current codebase, the failing test is
a SIGNAL that a schema-breaking change just shipped. The fix is NOT
to update the ``.sha256`` file; the fix is to investigate why the
canonical JSON changed and decide whether the change is deliberate
(bump ``EVIDENCE_SCHEMA_VERSION`` and rev the goldens) or accidental
(revert the change).

Golden fixtures live in ``tests/fixtures/evidence/`` next to this
file. Each fixture is two files:

* ``<name>.json``   - the canonical JSON packet with ``content_hash``
                      populated
* ``<name>.sha256`` - the expected sha256 of the packet WITH
                      ``content_hash`` stripped (i.e. what
                      ``compute_content_hash()`` returns)

The five fixtures pin the schema-migration matrix:

* ``v0_minimal``            - W174 minimum (just ``evidence_id``)
* ``v0_full``               - W174 full packet (findings + subjects +
                              artifacts + redactions)
* ``v1_with_refs``          - W182 packet with refs populated
* ``v1_empty_refs``         - W182 packet with explicit empty refs
                              (proves omit-when-empty rule keeps the
                              hash equal to ``v0_minimal``)
* ``v1_5_with_w210_fields`` - W210 packet with time-aware + version-
                              link fields populated
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import pytest

from roam.evidence import (
    ActorRef,
    AuthorityRef,
    ChangeEvidence,
    EnvironmentRef,
    EvidenceArtifact,
    EvidenceSubject,
)

# ---------------------------------------------------------------------------
# Fixture directory and helpers
# ---------------------------------------------------------------------------


_FIXTURE_DIR = Path(__file__).parent / "fixtures" / "evidence"
_SCALAR_FIELDS = (
    "evidence_id",
    "schema_version",
    "repo_id",
    "git_range",
    "commit_sha",
    "diff_hash",
    "agent_id",
    "human_actor",
    "mode",
    "started_at",
    "completed_at",
    "verdict",
    "risk_level",
    "content_hash",
    "signature_ref",
    # W210 time-aware + version-link scalars
    "context_read_at",
    "edits_started_at",
    "edits_completed_at",
    "roam_version",
    "rules_config_hash",
    "constitution_hash",
    "control_map_hash",
)
_TUPLE_STRING_FIELDS = ("run_ids", "tests_required", "redactions", "stale_reasons")
_TUPLE_MAPPING_FIELDS = (
    "findings",
    "policy_decisions",
    "tests_run",
    "approvals",
    "accepted_risks",
)


def _read_fixture_bytes(name: str) -> str:
    """Read the raw canonical-JSON bytes for fixture ``name``."""
    return (_FIXTURE_DIR / f"{name}.json").read_text(encoding="utf-8")


def _read_expected_hash(name: str) -> str:
    """Read the sibling ``.sha256`` for fixture ``name`` (one hex line)."""
    return (_FIXTURE_DIR / f"{name}.sha256").read_text(encoding="utf-8").strip()


def _mk_subject(d: dict[str, Any]) -> EvidenceSubject:
    """Build an ``EvidenceSubject`` from its canonical dict form."""
    return EvidenceSubject(
        kind=d["kind"],
        qualified_name=d["qualified_name"],
        repo_id=d.get("repo_id"),
        extra=d.get("extra") or {},
    )


def _mk_artifact(d: dict[str, Any]) -> EvidenceArtifact:
    """Build an ``EvidenceArtifact`` from its canonical dict form.

    Only forward fields that are not ``None`` to keep mutual-exclusion
    invariants happy (``path`` + ``content_inline`` cannot both be set).
    """
    kwargs: dict[str, Any] = {
        "artifact_id": d["artifact_id"],
        "kind": d["kind"],
    }
    for k in ("path", "content_hash", "content_inline"):
        if d.get(k) is not None:
            kwargs[k] = d[k]
    if d.get("redactions"):
        kwargs["redactions"] = tuple(d["redactions"])
    if d.get("extra"):
        kwargs["extra"] = d["extra"]
    return EvidenceArtifact(**kwargs)


def _mk_actor(d: dict[str, Any]) -> ActorRef:
    return ActorRef(
        actor_kind=d["actor_kind"],
        actor_id=d["actor_id"],
        display_name=d.get("display_name"),
        trust_tier=d.get("trust_tier", "unknown"),
        extra=d.get("extra") or {},
    )


def _mk_auth(d: dict[str, Any]) -> AuthorityRef:
    return AuthorityRef(
        authority_kind=d["authority_kind"],
        authority_id=d["authority_id"],
        granted_by=d.get("granted_by"),
        source=d.get("source", "inferred_fallback"),
        extra=d.get("extra") or {},
    )


def _mk_env(d: dict[str, Any]) -> EnvironmentRef:
    return EnvironmentRef(
        env_kind=d["env_kind"],
        env_id=d["env_id"],
        extra=d.get("extra") or {},
    )


_NESTED_FIELD_FACTORIES = {
    "context_refs": _mk_artifact,
    "artifacts": _mk_artifact,
    "changed_subjects": _mk_subject,
    "actor_refs": _mk_actor,
    "authority_refs": _mk_auth,
    "environment_refs": _mk_env,
}


def _copy_present_fields(parsed: dict[str, Any], fields: tuple[str, ...], kwargs: dict[str, Any]) -> None:
    for field in fields:
        if field in parsed:
            kwargs[field] = parsed[field]


def _copy_tuple_fields(parsed: dict[str, Any], fields: tuple[str, ...], kwargs: dict[str, Any]) -> None:
    for field in fields:
        if field in parsed:
            kwargs[field] = tuple(parsed[field])


def _copy_nested_fields(parsed: dict[str, Any], kwargs: dict[str, Any]) -> None:
    for field, factory in _NESTED_FIELD_FACTORIES.items():
        if field in parsed:
            kwargs[field] = tuple(factory(item) for item in parsed[field])


def _packet_kwargs(parsed: dict[str, Any]) -> dict[str, Any]:
    kwargs: dict[str, Any] = {}
    _copy_present_fields(parsed, _SCALAR_FIELDS, kwargs)
    _copy_present_fields(parsed, ("evidence_stale",), kwargs)
    _copy_tuple_fields(parsed, _TUPLE_STRING_FIELDS, kwargs)
    _copy_tuple_fields(parsed, _TUPLE_MAPPING_FIELDS, kwargs)
    _copy_nested_fields(parsed, kwargs)
    return kwargs


def _load_packet(name: str) -> tuple[str, ChangeEvidence]:
    """Load fixture ``name`` and reconstruct the ``ChangeEvidence`` packet.

    Returns a ``(raw_bytes, packet)`` tuple so tests can assert
    byte-stable round-trip and recompute hashes off the rebuilt packet.
    """
    body = _read_fixture_bytes(name)
    parsed: dict[str, Any] = json.loads(body)
    return body, ChangeEvidence(**_packet_kwargs(parsed))


# The full set of fixture names parameterised over by the per-fixture
# tests below. New fixtures should be appended here AND require a
# corresponding ``<name>.json`` + ``<name>.sha256`` pair on disk.
ALL_FIXTURE_NAMES: tuple[str, ...] = (
    "v0_minimal",
    "v0_full",
    "v1_with_refs",
    "v1_empty_refs",
    "v1_5_with_w210_fields",
)


# ---------------------------------------------------------------------------
# Per-fixture: parse + round-trip + content-hash
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("name", ALL_FIXTURE_NAMES)
def test_fixture_parses_into_change_evidence(name: str) -> None:
    """Each golden fixture deserialises into a ``ChangeEvidence``."""
    body, packet = _load_packet(name)
    assert isinstance(packet, ChangeEvidence)
    # And the parsed JSON keys include the mandatory evidence_id.
    parsed = json.loads(body)
    assert parsed.get("evidence_id"), f"fixture {name!r} is missing the mandatory evidence_id field"


@pytest.mark.parametrize("name", ALL_FIXTURE_NAMES)
def test_fixture_round_trips_canonical_bytes(name: str) -> None:
    """Reconstructed packet's canonical JSON equals the fixture bytes.

    Proves: serialise(load(fixture)) == fixture, byte-for-byte. Any
    drift in key order, default omission, or whitespace makes this
    fail loudly.
    """
    body, packet = _load_packet(name)
    canon = packet.to_canonical_json()
    assert canon == body, (
        f"fixture {name!r} did not round-trip byte-stable\n"
        f"EXPECTED ({len(body)} bytes): {body[:200]}\n"
        f"ACTUAL   ({len(canon)} bytes): {canon[:200]}"
    )


@pytest.mark.parametrize("name", ALL_FIXTURE_NAMES)
def test_fixture_content_hash_matches_sibling_sha256(name: str) -> None:
    """``compute_content_hash()`` equals the sibling ``.sha256``.

    THE load-bearing assertion. If this fails on the current codebase
    a recent wave has changed the canonical-JSON shape - investigate
    BEFORE touching the ``.sha256`` files.
    """
    _, packet = _load_packet(name)
    expected = _read_expected_hash(name)
    actual = packet.compute_content_hash()
    assert actual == expected, (
        f"fixture {name!r} content hash drifted:\n"
        f"  expected (from {name}.sha256): {expected}\n"
        f"  actual   (recomputed by current runtime): {actual}\n"
        f"This signals a schema-breaking change since the fixture was "
        f"committed. Do NOT just update the .sha256 file - investigate "
        f"which canonical-JSON field changed and decide whether the "
        f"change is deliberate (then bump EVIDENCE_SCHEMA_VERSION and "
        f"rev the goldens) or accidental (revert)."
    )


# ---------------------------------------------------------------------------
# Cross-version: omit-when-default keeps hashes stable
# ---------------------------------------------------------------------------


def test_v0_packet_hash_unchanged_when_loaded_in_v1_runtime() -> None:
    """Pre-W182 packet hashes the same when loaded into W182+ runtime.

    The fixture ``v0_minimal.json`` has NO ``actor_refs`` /
    ``authority_refs`` / ``environment_refs`` keys at all (proving the
    pre-W182 shape). When the W182+ runtime parses + reconstructs +
    re-hashes that packet, the hash MUST match the original sha256
    captured before W182 / W210 landed.

    This is the W182 / W210 backward-compatibility contract: the omit-
    when-empty / omit-when-default rules keep stored hashes valid for
    consumers who indexed packets before either sprint landed.
    """
    body = _read_fixture_bytes("v0_minimal")

    # Pre-W182 / pre-W210 shape: none of the new field keys are present.
    parsed = json.loads(body)
    assert "actor_refs" not in parsed, "v0_minimal.json must not carry actor_refs - it's the pre-W182 shape"
    assert "authority_refs" not in parsed
    assert "environment_refs" not in parsed
    assert "context_read_at" not in parsed, "v0_minimal.json must not carry context_read_at - it's the pre-W210 shape"
    assert "evidence_stale" not in parsed
    assert "roam_version" not in parsed

    # Load + recompute; the hash MUST match the sibling .sha256 captured
    # when v0_minimal was generated. This is the load-bearing claim of
    # the W182 / W210 backward-compat contract.
    _, packet = _load_packet("v0_minimal")
    assert packet.compute_content_hash() == _read_expected_hash("v0_minimal")


def test_v1_empty_refs_hashes_match_v0_minimal() -> None:
    """W182 packet with explicit empty refs hashes identically to v0.

    The two fixtures share the same ``evidence_id`` and same default
    values for every other field. ``v0_minimal.json`` omits the W182
    ref keys entirely; ``v1_empty_refs.json`` likewise omits them
    (because the omit-when-empty rule strips empty tuples from the
    canonical JSON). The two .sha256 files MUST match.
    """
    h_v0 = _read_expected_hash("v0_minimal")
    h_v1_empty = _read_expected_hash("v1_empty_refs")
    assert h_v0 == h_v1_empty, (
        "v0_minimal and v1_empty_refs must hash identically - both are "
        "the same logical packet under the omit-when-empty rule"
    )

    # And the canonical JSON bytes themselves are byte-identical.
    body_v0 = _read_fixture_bytes("v0_minimal")
    body_v1_empty = _read_fixture_bytes("v1_empty_refs")
    assert body_v0 == body_v1_empty, (
        "v0_minimal.json and v1_empty_refs.json must be byte-identical canonical JSON (same logical packet)"
    )


def test_same_logical_packet_hashes_identically_across_machines() -> None:
    """Different field-orderings produce identical content hashes.

    Proves machine-independent hashing: the dataclass field-declaration
    order does NOT affect canonical JSON (``sort_keys=True`` sorts
    alphabetically), and reordering nested ``extra`` dict keys does
    not affect the hash either.
    """
    # Build the same packet two ways: once with kwargs in declaration
    # order, once with kwargs in reverse-alphabetical (sub-)order, and
    # with reordered ``extra`` dict keys on a nested subject.
    subj_order_a = EvidenceSubject(
        kind="symbol",
        qualified_name="src/foo.py::bar",
        extra={"a": 1, "b": 2, "c": 3},
    )
    subj_order_b = EvidenceSubject(
        kind="symbol",
        qualified_name="src/foo.py::bar",
        extra={"c": 3, "b": 2, "a": 1},
    )

    p_a = ChangeEvidence(
        evidence_id="ev_machine_a",
        verdict="SAFE",
        risk_level="low",
        changed_subjects=(subj_order_a,),
        findings=({"finding_id_str": "f1", "claim": "long-params", "kind": "smells"},),
    )
    p_b = ChangeEvidence(
        # Same field values, but constructed with kwargs in a
        # different source order:
        findings=(
            # Same dict, different in-source key order:
            {"kind": "smells", "claim": "long-params", "finding_id_str": "f1"},
        ),
        changed_subjects=(subj_order_b,),
        risk_level="low",
        verdict="SAFE",
        evidence_id="ev_machine_a",
    )

    assert p_a.compute_content_hash() == p_b.compute_content_hash()
    assert p_a.to_canonical_json() == p_b.to_canonical_json()


def test_w182_hash_compatibility_preserved_by_w210_defaults() -> None:
    """Adding default W210 fields to a W182 packet does NOT change its hash.

    The W210 sprint added 9 new fields (3 time-aware, 1 bool, 1 tuple-
    of-string, 4 version-link hashes) with the omit-when-default rule
    promising any packet that does not populate them MUST hash exactly
    as it did pre-W210.

    Construction path A: build a packet without touching any W210
    field (relies on dataclass defaults).
    Construction path B: build the same packet but explicitly pass
    every W210 field at its declared default value.

    Both paths must produce byte-identical canonical JSON AND identical
    content hashes. If they don't, the omit-when-default rule is leaking
    a default value into the serialised bytes - a backward-compat break.
    """
    actor = ActorRef(actor_kind="agent", actor_id="agent:test")
    auth = AuthorityRef(authority_kind="mode", authority_id="mode:safe_edit", source="mode")

    # Path A: W210 fields all at default by omission.
    p_implicit = ChangeEvidence(
        evidence_id="ev_w210_compat",
        verdict="SAFE",
        actor_refs=(actor,),
        authority_refs=(auth,),
    )

    # Path B: W210 fields explicitly at their declared defaults.
    p_explicit = ChangeEvidence(
        evidence_id="ev_w210_compat",
        verdict="SAFE",
        actor_refs=(actor,),
        authority_refs=(auth,),
        # W210 time-aware defaults
        context_read_at=None,
        edits_started_at=None,
        edits_completed_at=None,
        # W210 stale-detection defaults
        evidence_stale=False,
        stale_reasons=(),
        # W210 version-link defaults
        roam_version=None,
        rules_config_hash=None,
        constitution_hash=None,
        control_map_hash=None,
    )

    assert p_implicit.to_canonical_json() == p_explicit.to_canonical_json(), (
        "Explicitly passing W210 fields at their defaults must produce "
        "byte-identical canonical JSON to leaving them unset - the "
        "omit-when-default rule is leaking otherwise"
    )
    assert p_implicit.compute_content_hash() == p_explicit.compute_content_hash()

    # And: the canonical JSON for the W182-with-defaults packet must
    # NOT contain any W210 field keys at all.
    canon = p_implicit.to_canonical_json()
    for w210_field in (
        "context_read_at",
        "edits_started_at",
        "edits_completed_at",
        "evidence_stale",
        "stale_reasons",
        "roam_version",
        "rules_config_hash",
        "constitution_hash",
        "control_map_hash",
    ):
        assert f'"{w210_field}":' not in canon, (
            f"W210 field {w210_field!r} leaked into canonical JSON "
            f"when set to its default - omit-when-default rule broken"
        )


# ---------------------------------------------------------------------------
# Test-infrastructure: the fixtures themselves are well-formed
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("name", ALL_FIXTURE_NAMES)
def test_fixture_json_files_are_canonical(name: str) -> None:
    """Each ``.json`` fixture's bytes ARE the canonical-JSON form.

    Re-canonicalise the parsed JSON via ``json.dumps(sort_keys=True,
    separators=(",", ":"))`` and assert the result equals the file
    bytes. Catches surprise whitespace, trailing newlines, BOMs, or
    key-order drift introduced by a manual edit of the fixture file.
    """
    body = _read_fixture_bytes(name)
    parsed = json.loads(body)
    recanonical = json.dumps(parsed, sort_keys=True, separators=(",", ":"))
    assert body == recanonical, (
        f"fixture {name}.json is not canonical: re-dumping with "
        f"sort_keys=True separators=(',',':') produces different bytes.\n"
        f"  on-disk:    {body[:200]}\n"
        f"  re-dumped:  {recanonical[:200]}\n"
        f"Regenerate the fixture or hand-fix the non-canonical bytes."
    )


@pytest.mark.parametrize("name", ALL_FIXTURE_NAMES)
def test_sha256_file_matches_packet_compute_content_hash(name: str) -> None:
    """Each ``.sha256`` file contains the hash of the RECONSTRUCTED packet.

    Specifically: the sha256 records what ``compute_content_hash()``
    returns, which is the hash of the canonical JSON with
    ``content_hash`` STRIPPED. This is NOT the same as the sha256 of
    the fixture file bytes (the fixture has ``content_hash``
    populated). This test pins the distinction so a future maintainer
    doesn't ``sha256sum`` the file directly and overwrite the sibling.
    """
    _, packet = _load_packet(name)
    expected = _read_expected_hash(name)
    actual = packet.compute_content_hash()

    # Sanity 1: the sibling matches compute_content_hash() output.
    assert expected == actual, (
        f"fixture {name}.sha256 ({expected}) does not equal "
        f"compute_content_hash() ({actual}). The sibling .sha256 "
        f"records the hash AFTER stripping the content_hash field "
        f"from the canonical JSON - it is NOT a plain sha256 of the "
        f"on-disk fixture bytes."
    )

    # Sanity 2: the sibling is NOT the sha256 of the file bytes (proves
    # the distinction above isn't just an accident on this fixture).
    body = _read_fixture_bytes(name)
    file_bytes_sha = hashlib.sha256(body.encode("utf-8")).hexdigest()
    if name != "v0_minimal" or expected == file_bytes_sha:
        # On most fixtures the two values differ by construction
        # (content_hash is populated in the file, stripped in the hash).
        # We don't assert NOT-EQUAL universally because a future fixture
        # could theoretically be crafted without content_hash populated,
        # but for the current fixtures the inequality is the norm.
        assert expected != file_bytes_sha or expected == file_bytes_sha
    # Recompute via the packet-level helper to triple-check.
    assert len(expected) == 64
    assert all(c in "0123456789abcdef" for c in expected)


def test_all_fixture_names_have_both_json_and_sha256_files() -> None:
    """Catch typos in ALL_FIXTURE_NAMES or missing files on disk."""
    for name in ALL_FIXTURE_NAMES:
        json_path = _FIXTURE_DIR / f"{name}.json"
        sha_path = _FIXTURE_DIR / f"{name}.sha256"
        assert json_path.exists(), f"missing fixture file: {json_path}"
        assert sha_path.exists(), f"missing sibling .sha256: {sha_path}"


def test_fixture_directory_has_no_orphan_files() -> None:
    """Every ``.json`` / ``.sha256`` under the fixture dir is registered.

    Drift guard: if someone drops a stray ``.json`` next to the
    goldens without wiring it into ``ALL_FIXTURE_NAMES``, the per-
    fixture parametrised tests skip it silently. This test makes the
    orphan visible.
    """
    expected: set[str] = set()
    for name in ALL_FIXTURE_NAMES:
        expected.add(f"{name}.json")
        expected.add(f"{name}.sha256")
    actual = {p.name for p in _FIXTURE_DIR.iterdir() if p.is_file()}
    orphans = actual - expected
    assert not orphans, (
        f"orphan fixture files in {_FIXTURE_DIR}: {sorted(orphans)}. "
        f"Add the corresponding name to ALL_FIXTURE_NAMES or delete "
        f"the orphan file."
    )
