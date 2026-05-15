"""W293 — tests for policy_decisions provenance wiring.

The W293 directive wires the W282 :func:`provenance_label` helper onto
``PolicyDecision.extra["provenance"]`` (and the wire-shape top-level
``provenance`` key) so each policy-decision row records WHICH data
channel produced it. Stamping happens at PRODUCER / GATHERER /
COLLECTOR INGESTION POINTS, not as a dataclass default — the
``PolicyDecision`` schema stays signal-free per the spec.

Mapping the channels to provenance labels (per the W293 directive):

* W267 constitution gatherer (``.roam/constitution.yml``)
                                            -> ``producer_envelope(constitution)``
* W267 permit gatherer (``.roam/permits/*.json``)
                                            -> ``producer_envelope(permit)``
* W267 lease gatherer (``roam.leases.list_leases``)
                                            -> ``producer_envelope(lease)``
* W192 rules-validate envelopes
                                            -> ``producer_envelope(rule)``
* W195 audit-trail-verify envelope (chain_integrity)
                                            -> ``audit_trail``
* W247b CHANGES_REQUESTED from GitHub PR review
                                            -> ``producer_envelope(github_review)``
* Legacy / no-source-signal row at collector
                                            -> ``unknown``

Constraints pinned here:

* ``PolicyDecision.extra["provenance"]`` must be present on every row
  after collection (Pattern-2 always-emit).
* Existing producer-stamped ``provenance`` MUST NOT be overwritten by
  the collector fallback.
* Every emitted provenance value MUST live in the closed
  :data:`PROVENANCE_SOURCES` frozenset (drift guard at module end).
* Bodies / body_text / body_html MUST NOT travel through the
  provenance hop (W247a guardrail still holds).
"""

from __future__ import annotations

import json as _json
from pathlib import Path
from typing import Any

import pytest

from roam.evidence import PROVENANCE_SOURCES
from roam.evidence.collector import (
    _audit_trail_to_artifact_and_decisions,
    _flatten_rules_envelope_to_policy_decisions,
    collect_change_evidence,
)
from roam.evidence.github_reviews import parse_github_reviews
from roam.evidence.policy import PolicyDecision


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _provenance_base(label: str) -> str:
    """Strip the optional ``"(detail)"`` suffix from a provenance label.

    The W282 helper emits either bare ``"audit_trail"`` or compact
    ``"producer_envelope(rule)"`` - both forms validate against
    :data:`PROVENANCE_SOURCES`. The drift guard checks the base.
    """
    return label.split("(", 1)[0]


# ---------------------------------------------------------------------------
# W267 gatherers: constitution / permit / lease
# ---------------------------------------------------------------------------


def test_constitution_gatherer_stamps_producer_envelope_constitution(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """W267 constitution gatherer rows carry
    ``provenance="producer_envelope(constitution)"``.
    """
    # Build a fake repo root with a constitution.yml. ``find_project_root``
    # looks for ``.git``, so seed that too.
    (tmp_path / ".git").mkdir()
    roam_dir = tmp_path / ".roam"
    roam_dir.mkdir()
    (roam_dir / "constitution.yml").write_text(
        "required_checks:\n"
        "  before_edit: [\"roam preflight\"]\n"
        "  before_merge: [\"roam impact\"]\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    from roam.commands.cmd_pr_replay import _gather_constitution_policy_decisions

    warnings: list[str] = []
    rows = _gather_constitution_policy_decisions(warnings)
    assert rows, (
        f"constitution gatherer produced zero rows; warnings={warnings!r}"
    )

    for row in rows:
        pd = PolicyDecision.from_dict(row)
        assert pd.extra.get("provenance") == "producer_envelope(constitution)", (
            f"row {row!r} missing producer_envelope(constitution) provenance"
        )


def test_permit_gatherer_stamps_producer_envelope_permit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """W267 permit gatherer rows carry
    ``provenance="producer_envelope(permit)"``.
    """
    (tmp_path / ".git").mkdir()
    permits_dir = tmp_path / ".roam" / "permits"
    permits_dir.mkdir(parents=True)
    # W383: gatherer now routes through the validated shared reader, so
    # the fixture permit_id MUST match ``PERMIT_ID_RE`` and carry the
    # full ``PermitRecord`` field set (matches what the W198 writer
    # produces on disk).
    pid = "permit_20260514_aaaaaa"
    (permits_dir / f"{pid}.json").write_text(
        _json.dumps({
            "permit_id": pid,
            "scope": "test_scope",
            "expires_at": "2030-01-01T00:00:00Z",
            "issued_to": "agent:test",
            "issued_at": "2026-05-14T10:00:00Z",
            "issued_by": "human:operator",
        }),
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    from roam.commands.cmd_pr_replay import _gather_permit_policy_decisions

    warnings: list[str] = []
    rows = _gather_permit_policy_decisions(warnings)
    assert rows, "permit gatherer produced zero rows"

    for row in rows:
        pd = PolicyDecision.from_dict(row)
        assert pd.extra.get("provenance") == "producer_envelope(permit)", (
            f"row {row!r} missing producer_envelope(permit) provenance"
        )


def test_lease_gatherer_stamps_producer_envelope_lease(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """W267 lease gatherer rows carry ``provenance="producer_envelope(lease)"``."""
    (tmp_path / ".git").mkdir()
    leases_dir = tmp_path / ".roam" / "leases"
    leases_dir.mkdir(parents=True)
    (leases_dir / "lease_xyz.json").write_text(
        _json.dumps({
            "lease_id": "lease_xyz",
            "agent": "agent_a",
            "subject_kind": "file",
            "subject": ["src/foo.py"],
            "ttl_seconds": 3600,
            "acquired_at": "2026-05-14T00:00:00Z",
            "expires_at": "2030-01-01T00:00:00Z",
            "state": "active",
        }),
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    from roam.commands.cmd_pr_replay import _gather_lease_policy_decisions

    warnings: list[str] = []
    rows = _gather_lease_policy_decisions(warnings)
    assert rows, (
        f"lease gatherer produced zero rows; warnings={warnings!r}"
    )

    for row in rows:
        pd = PolicyDecision.from_dict(row)
        assert pd.extra.get("provenance") == "producer_envelope(lease)", (
            f"row {row!r} missing producer_envelope(lease) provenance"
        )


# ---------------------------------------------------------------------------
# W195 audit-trail chain integrity
# ---------------------------------------------------------------------------


def test_audit_trail_chain_integrity_stamps_audit_trail() -> None:
    """audit-trail chain-integrity rows carry ``provenance="audit_trail"``."""
    envelope: dict[str, Any] = {
        "summary": {
            "audit_trail_path": "/nowhere/.roam/runs/run_abc.jsonl",
            "run_id": "run_abc",
            "chain_valid": True,
            "total_records": 4,
        },
        "issues": [],
    }
    warnings: list[str] = []
    _, decisions = _audit_trail_to_artifact_and_decisions(
        envelope, warnings, source_label="audit_trail_envelope",
    )
    assert decisions, "expected at least one chain-integrity decision row"
    for row in decisions:
        pd = PolicyDecision.from_dict(row)
        assert pd.extra.get("provenance") == "audit_trail", (
            f"row {row!r} missing audit_trail provenance"
        )


def test_audit_trail_failure_rows_also_stamp_audit_trail() -> None:
    """Failure rows (per-issue) also carry the same audit_trail provenance."""
    envelope: dict[str, Any] = {
        "summary": {
            "audit_trail_path": "/nowhere/.roam/runs/run_bad.jsonl",
            "run_id": "run_bad",
            "chain_valid": False,
            "total_records": 2,
        },
        "issues": [
            {
                "issue": "hash_mismatch",
                "line": 7,
                "expected_prev": "abc",
                "computed_prev": "def",
            },
        ],
    }
    warnings: list[str] = []
    _, decisions = _audit_trail_to_artifact_and_decisions(
        envelope, warnings, source_label="audit_trail_envelope",
    )
    # First row is the summary "fail"; second is the per-issue row.
    assert len(decisions) >= 2
    for row in decisions:
        pd = PolicyDecision.from_dict(row)
        assert pd.extra.get("provenance") == "audit_trail"


# ---------------------------------------------------------------------------
# W192 rules-validate envelopes
# ---------------------------------------------------------------------------


def test_rules_envelope_rows_stamp_producer_envelope_rule() -> None:
    """rules-validate rows carry ``provenance="producer_envelope(rule)"``."""
    envelope: dict[str, Any] = {
        "results": [
            {"rule_id": "no-print", "passed": True, "severity": "warning"},
            {"rule_id": "no-tabs", "passed": False, "severity": "error",
             "reason": "tab detected"},
        ],
    }
    warnings: list[str] = []
    rows = _flatten_rules_envelope_to_policy_decisions(
        envelope, warnings, source_label="rules_envelopes[0]",
    )
    assert len(rows) == 2
    for row in rows:
        pd = PolicyDecision.from_dict(row)
        assert pd.extra.get("provenance") == "producer_envelope(rule)"


# ---------------------------------------------------------------------------
# W247b GitHub PR-review CHANGES_REQUESTED
# ---------------------------------------------------------------------------


def test_github_review_changes_requested_stamps_producer_envelope_github_review() -> None:
    """CHANGES_REQUESTED PolicyDecision carries
    ``provenance="producer_envelope(github_review)"``.
    """
    reviews: list[dict[str, Any]] = [
        {
            "id": 12345,
            "state": "CHANGES_REQUESTED",
            "user": {"login": "bob"},
            "submitted_at": "2026-05-14T10:00:00Z",
            "commit_id": "deadbeef" * 5,  # 40 chars
            "html_url": "https://example.com/pr/42#review-12345",
            "body": "rework the migration plan",
        },
    ]
    _, policy_decisions, _ = parse_github_reviews(
        reviews=reviews,
        head_commit_sha="cafebabe" * 5,
        pr_number=42,
    )
    assert policy_decisions, "expected CHANGES_REQUESTED row"
    pd = policy_decisions[0]
    assert pd.extra.get("provenance") == "producer_envelope(github_review)"
    # And the wire shape carries the same value at top-level (to_dict flatten).
    wire = pd.to_dict()
    assert wire.get("provenance") == "producer_envelope(github_review)"
    # Body MUST NOT leak through the provenance hop (W247a guardrail).
    assert "rework the migration plan" not in _json.dumps(wire)


# ---------------------------------------------------------------------------
# Collector fallback + preserve-existing discipline
# ---------------------------------------------------------------------------


def test_legacy_dict_row_gets_unknown_provenance_at_collector() -> None:
    """A legacy row without ``provenance`` lands with ``"unknown"`` at the
    collector's Pattern-2 always-emit fallback.
    """
    legacy_row = {"rule_id": "legacy:foo", "decision": "pass"}
    packet, warnings = collect_change_evidence(
        extra_policy_decisions=[legacy_row],
        repo_id="github.com/example/repo",
        commit_sha="0" * 40,
    )
    matching = [
        r for r in packet.policy_decisions
        if r.get("rule_id") == "legacy:foo"
    ]
    assert matching, "legacy row missing from packet"
    row = matching[0]
    assert row.get("provenance") == "unknown", (
        f"legacy row should land at unknown provenance; got {row!r}"
    )


def test_existing_provenance_preserved_at_collector() -> None:
    """A row with a producer-stamped ``provenance`` is NOT overwritten
    by the collector's fallback stamping.
    """
    custom_row = {
        "rule_id": "future:bar",
        "decision": "allow",
        "provenance": "cli_flag",  # producer-stamped, not unknown
    }
    packet, _ = collect_change_evidence(
        extra_policy_decisions=[custom_row],
        repo_id="github.com/example/repo",
        commit_sha="1" * 40,
    )
    matching = [
        r for r in packet.policy_decisions
        if r.get("rule_id") == "future:bar"
    ]
    assert matching, "custom row missing from packet"
    row = matching[0]
    assert row.get("provenance") == "cli_flag", (
        f"existing provenance was overwritten; got {row!r}"
    )


# ---------------------------------------------------------------------------
# Drift guard — every emitted provenance lives in PROVENANCE_SOURCES
# ---------------------------------------------------------------------------


def test_policy_decision_provenance_uses_only_PROVENANCE_SOURCES_values(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Every stamped policy_decision provenance has a base in
    :data:`PROVENANCE_SOURCES` (closed-enum drift guard).
    """
    # Set up a workspace with constitution/permit/lease so we hit all
    # three producer channels in one fan-out.
    (tmp_path / ".git").mkdir()
    roam_dir = tmp_path / ".roam"
    roam_dir.mkdir()
    (roam_dir / "constitution.yml").write_text(
        "required_checks:\n  before_edit: [\"roam preflight\"]\n",
        encoding="utf-8",
    )
    (roam_dir / "permits").mkdir()
    # W383: validated reader requires PERMIT_ID_RE-conformant id + full
    # ``PermitRecord`` field set. Mirrors what the W198 writer produces.
    _perm_a_id = "permit_20260514_aaaaaa"
    (roam_dir / "permits" / f"{_perm_a_id}.json").write_text(
        _json.dumps({
            "permit_id": _perm_a_id,
            "scope": "test_scope",
            "expires_at": "2030-01-01T00:00:00Z",
            "issued_to": "agent:test",
            "issued_at": "2026-05-14T00:00:00Z",
            "issued_by": "human:operator",
        }),
        encoding="utf-8",
    )
    (roam_dir / "leases").mkdir()
    (roam_dir / "leases" / "lease_a.json").write_text(
        _json.dumps({
            "lease_id": "lease_a",
            "agent": "ag",
            "subject_kind": "file",
            "subject": ["src/x.py"],
            "ttl_seconds": 3600,
            "acquired_at": "2026-05-14T00:00:00Z",
            "expires_at": "2030-01-01T00:00:00Z",
            "state": "active",
        }),
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)

    from roam.commands.cmd_pr_replay import (
        _gather_constitution_policy_decisions,
        _gather_lease_policy_decisions,
        _gather_permit_policy_decisions,
    )

    all_rows: list[dict] = []
    warnings: list[str] = []
    all_rows.extend(_gather_constitution_policy_decisions(warnings))
    all_rows.extend(_gather_permit_policy_decisions(warnings))
    all_rows.extend(_gather_lease_policy_decisions(warnings))

    # Also include audit-trail + rules + github-review paths.
    _, at_rows = _audit_trail_to_artifact_and_decisions(
        {"summary": {"chain_valid": True}, "issues": []},
        warnings, source_label="audit_trail_envelope",
    )
    all_rows.extend(at_rows)
    rules_rows = _flatten_rules_envelope_to_policy_decisions(
        {"results": [{"rule_id": "r1", "passed": True}]},
        warnings, source_label="rules_envelopes[0]",
    )
    all_rows.extend(rules_rows)

    _, gh_pd, _ = parse_github_reviews(
        reviews=[{
            "id": 99, "state": "CHANGES_REQUESTED",
            "user": {"login": "rev"}, "submitted_at": "2026-05-14T00:00:00Z",
            "commit_id": "a" * 40,
        }],
        head_commit_sha="b" * 40,
        pr_number=7,
    )
    all_rows.extend([d.to_dict() for d in gh_pd])

    assert all_rows, "fanout produced no rows - test bug"

    for row in all_rows:
        prov = row.get("provenance")
        assert isinstance(prov, str) and prov, (
            f"row missing provenance: {row!r}"
        )
        base = _provenance_base(prov)
        assert base in PROVENANCE_SOURCES, (
            f"row provenance base {base!r} (from {prov!r}) not in "
            f"PROVENANCE_SOURCES"
        )


# ---------------------------------------------------------------------------
# W425 — malformed lease file surfaces in producer_warnings bucket
# ---------------------------------------------------------------------------


def test_w425_malformed_lease_surfaces_in_producer_warnings(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """W425: a malformed lease file produces a warning naming the file
    and the closed-form reason (malformed JSON / non-dict top-level /
    schema-invalid dict). The lease gatherer threads its ``warnings``
    list into :func:`roam.leases.list_leases` so the warning lands in
    the same bucket the replay envelope publishes as
    ``producer_warnings``.
    """
    (tmp_path / ".git").mkdir()
    leases_dir = tmp_path / ".roam" / "leases"
    leases_dir.mkdir(parents=True)
    # File 1: a malformed JSON file (truncated brace).
    (leases_dir / "lease_bad_json.json").write_text(
        "{not valid json", encoding="utf-8"
    )
    # File 2: top-level is a JSON list instead of a dict.
    (leases_dir / "lease_wrong_type.json").write_text(
        "[1, 2, 3]", encoding="utf-8"
    )
    # File 3: schema-invalid dict (missing required ``agent`` field).
    (leases_dir / "lease_schema_invalid.json").write_text(
        _json.dumps({
            "lease_id": "lease_20260514_aaaaaa",
            # NOTE: ``agent`` deliberately missing
            "subject_kind": "files",
            "subject": ["src/foo.py"],
            "ttl_seconds": 3600,
            "acquired_at": "2026-05-14T00:00:00Z",
            "expires_at": "2030-01-01T00:00:00Z",
            "state": "active",
        }),
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    from roam.commands.cmd_pr_replay import _gather_lease_policy_decisions

    warnings: list[str] = []
    rows = _gather_lease_policy_decisions(warnings)
    # No surviving leases → no rows.
    assert rows == [], f"expected zero rows; got {rows!r}"
    # All three malformed files produced one warning each.
    assert len(warnings) == 3, (
        f"expected 3 producer warnings (one per malformed lease file); "
        f"got {len(warnings)}: {warnings!r}"
    )
    joined = "\n".join(warnings)
    assert "lease_bad_json.json" in joined, joined
    assert "lease_wrong_type.json" in joined, joined
    assert "lease_schema_invalid.json" in joined, joined
    assert "malformed JSON" in joined, joined
    assert "not a JSON object" in joined, joined
    assert "schema validation" in joined, joined


# ---------------------------------------------------------------------------
# W426 — unparseable constitution.yml surfaces in producer_warnings
# ---------------------------------------------------------------------------


def test_w426_unparseable_constitution_surfaces_in_producer_warnings(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """W426: an empty / unparseable ``.roam/constitution.yml`` produces
    one ``producer_warnings`` entry naming the malformed file. The
    loader returns a marker constitution with ``metadata.unparseable
    = True`` (rather than raising) so without the W426 check the empty
    ``required_checks`` walk silently emits zero rows — auditors then
    cannot distinguish "no gates configured" from "gates exist but the
    file is malformed."
    """
    (tmp_path / ".git").mkdir()
    roam_dir = tmp_path / ".roam"
    roam_dir.mkdir()
    # An empty file is the simplest unparseable case: ``_load_yaml``
    # returns falsy → the loader stamps ``metadata.unparseable = True``.
    (roam_dir / "constitution.yml").write_text("", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    from roam.commands.cmd_pr_replay import _gather_constitution_policy_decisions

    warnings: list[str] = []
    rows = _gather_constitution_policy_decisions(warnings)
    assert rows == [], f"expected zero rows; got {rows!r}"
    assert len(warnings) == 1, (
        f"expected one constitution-unparseable warning; got {warnings!r}"
    )
    msg = warnings[0]
    assert "constitution" in msg, msg
    assert "malformed" in msg, msg
    assert "required_checks ignored" in msg, msg
