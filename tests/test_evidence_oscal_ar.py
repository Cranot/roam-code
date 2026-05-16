"""W465 — tests for ``roam evidence-oscal --kind assessment-results``.

OSCAL v1.2 Assessment Results (AR) emission is the per-run sibling
to the W464 Control Mapping emitter. The emitter is a READ-ONLY
projection of one ChangeEvidence packet — it never touches the
evidence schema, so the 31-fixture schema-migration suite must
stay byte-identical.

Test inventory:

1. ``test_ar_document_has_v12_shape`` — emission produces an
   ``assessment-results`` envelope with uuid, metadata (with
   oscal-version + parties), import-ap, results[0].
2. ``test_ar_synthesizes_stub_ap_when_no_ref_given`` — calling with
   ``import_ap_ref=None`` produces an inline stub AP reference and
   embeds the stub AP doc as a back-matter resource.
3. ``test_ar_honours_explicit_import_ap_ref`` — passing an explicit
   ``import_ap_ref`` produces an import-ap href that points at the
   external path (no inline stub).
4. ``test_ar_props_carry_authority_refs_and_redaction_extensions`` —
   authority_refs and redactions surface as roam-namespaced props
   (no OSCAL-native equivalent; W359 extension points).
5. ``test_ar_wording_lint_compliance`` — no remarks / title /
   reason field carries certif* / compliant / guarantee outside a
   negation window.
6. ``test_ar_is_deterministic_for_fixed_clock`` — same input +
   same clock produces byte-identical JSON (UUIDv5, not random).
7. ``test_ar_cli_emits_assessment_results`` — ``roam evidence-oscal
   --kind assessment-results --evidence <path>`` streams valid AR
   JSON on stdout.
8. ``test_ar_cli_json_envelope_wraps_document`` — ``roam --json
   evidence-oscal --kind assessment-results --evidence <path>``
   wraps the document with verdict + summary.
9. ``test_ar_cli_requires_evidence_path`` — ``--kind ar`` without
   ``--evidence`` errors cleanly.
10. ``test_ar_fixture_matches_built_document`` — checked-in
    fixture at ``tests/fixtures/oscal/sample-assessment-results.json``
    matches a fresh build at the same clock.
"""

from __future__ import annotations

import json as _json
from datetime import datetime, timezone
from pathlib import Path

import pytest
from click.testing import CliRunner

from roam.cli import cli
from roam.evidence.oscal import (
    DEFAULT_AR_REMARKS,
    DEFAULT_AR_TITLE,
    OSCAL_VERSION,
    ROAM_OSCAL_NS,
    STUB_AP_REMARKS,
    STUB_AP_TITLE,
    build_oscal_assessment_results,
    synthesize_stub_assessment_plan,
)
from tests._helpers.repo_root import repo_root

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


ROOT = repo_root()
EVIDENCE_FIXTURE = ROOT / "tests" / "fixtures" / "evidence" / "v1_with_refs.json"
AR_FIXTURE = ROOT / "tests" / "fixtures" / "oscal" / "sample-assessment-results.json"
FIXED_CLOCK = datetime(2026, 5, 14, 12, 0, 0, tzinfo=timezone.utc)


def _load_evidence() -> dict:
    """Load the v1_with_refs ChangeEvidence fixture as a dict."""
    return _json.loads(EVIDENCE_FIXTURE.read_text(encoding="utf-8"))


def _build_ar(
    *,
    now: datetime | None = None,
    import_ap_ref: str | None = None,
) -> dict:
    """Build an AR document from the fixture evidence packet."""
    return build_oscal_assessment_results(
        _load_evidence(),
        import_ap_ref=import_ap_ref,
        now=now or FIXED_CLOCK,
    )


def _walk_strings(node):
    """Recursively yield every string value in a nested dict/list/scalar."""
    if isinstance(node, str):
        yield node
    elif isinstance(node, dict):
        for v in node.values():
            yield from _walk_strings(v)
    elif isinstance(node, list):
        for v in node:
            yield from _walk_strings(v)


def _ns_props(props):
    """Collect roam-namespaced props into a {name: [values]} map."""
    out: dict[str, list[str]] = {}
    for p in props or []:
        if isinstance(p, dict) and p.get("ns") == ROAM_OSCAL_NS:
            out.setdefault(p["name"], []).append(p["value"])
    return out


# ---------------------------------------------------------------------------
# Shape / schema tests
# ---------------------------------------------------------------------------


def test_ar_document_has_v12_shape():
    """The emitted AR must carry the v1.2 required scaffolding."""
    doc = _build_ar()
    assert "assessment-results" in doc, "missing required top-level assessment-results element"
    ar = doc["assessment-results"]
    assert isinstance(ar.get("uuid"), str) and len(ar["uuid"]) == 36

    meta = ar["metadata"]
    assert meta.get("oscal-version") == OSCAL_VERSION
    assert meta.get("title")
    assert meta.get("last-modified")
    assert meta.get("remarks")

    # AR mandates import-ap on every document.
    assert "import-ap" in ar
    assert ar["import-ap"].get("href")

    # AR mandates at least one results[] entry.
    results = ar.get("results")
    assert isinstance(results, list) and len(results) >= 1
    r0 = results[0]
    assert r0.get("uuid")
    assert r0.get("title")
    assert r0.get("start")
    # reviewed-controls is mandatory in OSCAL AR per the v1.2 schema.
    assert "reviewed-controls" in r0


def test_ar_synthesizes_stub_ap_when_no_ref_given():
    """Calling build_oscal_assessment_results with no import_ap_ref
    must synthesize a stub AP and embed it as a back-matter resource
    so the AR document is self-contained.
    """
    doc = _build_ar(import_ap_ref=None)
    ar = doc["assessment-results"]

    # import-ap href must be a urn:uuid: reference (not a path).
    href = ar["import-ap"]["href"]
    assert href.startswith("urn:uuid:"), f"expected urn:uuid: import-ap href; got {href!r}"

    # The stub AP must be embedded in back-matter.resources[].
    resources = (ar.get("back-matter") or {}).get("resources") or []
    stub_resources = [
        r for r in resources if _ns_props(r.get("props") or []).get("roam_resource_kind") == ["stub_assessment_plan"]
    ]
    assert len(stub_resources) == 1, f"expected exactly one stub AP resource; got {len(stub_resources)}"
    # The stub resource's uuid must match the import-ap href.
    stub_uuid = stub_resources[0]["uuid"]
    assert href == f"urn:uuid:{stub_uuid}"


def test_ar_honours_explicit_import_ap_ref():
    """Passing an explicit import_ap_ref must produce an AR whose
    import-ap href is that ref (NOT a urn:uuid:) and which does NOT
    embed a stub AP resource.
    """
    external_ref = ".roam/oscal/our-real-ap.json"
    doc = _build_ar(import_ap_ref=external_ref)
    ar = doc["assessment-results"]
    assert ar["import-ap"]["href"] == external_ref

    # No stub_assessment_plan resource should be present.
    resources = (ar.get("back-matter") or {}).get("resources") or []
    stub_resources = [
        r for r in resources if _ns_props(r.get("props") or []).get("roam_resource_kind") == ["stub_assessment_plan"]
    ]
    assert stub_resources == [], "stub AP must not be emitted when import_ap_ref is set"


def test_ar_props_carry_authority_refs_and_redaction_extensions():
    """W359 mandate: authority_refs and redactions have no OSCAL-
    native equivalent. They surface as ``prop`` entries under the
    ``urn:roam:oscal:v1`` namespace.
    """
    doc = _build_ar()
    ar = doc["assessment-results"]

    # Metadata-level props must carry roam-schema-version + evidence_id
    # + content-hash (when present on the source packet).
    meta_props = _ns_props(ar["metadata"].get("props") or [])
    assert "roam-schema-version" in meta_props
    assert "evidence_id" in meta_props
    # v1_with_refs fixture carries a content_hash.
    assert "content-hash" in meta_props

    # Result-level props must carry the authority-ref extension when
    # the source packet has authority_refs (fixture has mode:safe_edit).
    r0 = ar["results"][0]
    result_props = _ns_props(r0.get("props") or [])
    assert "authority-ref" in result_props, f"missing authority-ref prop on results[0]; got {sorted(result_props)}"
    # The fixture carries authority_kind=mode, authority_id=mode:safe_edit
    # which composes to "mode:mode:safe_edit".
    assert any(v.startswith("mode:") for v in result_props["authority-ref"])

    # Parties (actor_refs) must surface with their trust_tier prop.
    parties = ar["metadata"].get("parties") or []
    assert len(parties) >= 1
    for party in parties:
        p_props = _ns_props(party.get("props") or [])
        assert "actor_kind" in p_props
        assert "actor_id" in p_props


# ---------------------------------------------------------------------------
# Wording-lint
# ---------------------------------------------------------------------------
#
# W536 consolidated the FORBIDDEN_WORDS / NEGATION_MARKERS constants and
# the scan loop into ``tests/_helpers/wording_lint.py``. The local
# ``_scan_for_overclaims(text, *, location)`` wrapper stays so the
# AssertionError messages keep their location-aware prose, but the
# scanning logic itself now lives in the shared helper.

from tests._helpers.wording_lint import scan_for_overclaims


def _scan_for_overclaims(text: str, *, location: str) -> None:
    """Raise AssertionError if `text` contains a forbidden word outside
    a negation window. Thin location-aware wrapper around the shared
    ``tests._helpers.wording_lint.scan_for_overclaims`` scanner.
    """
    violations = scan_for_overclaims(text)
    if violations:
        word, window = violations[0]
        raise AssertionError(
            f"compliance overclaim at {location} ({word!r}): ...{window}... (full text: {text[:120]!r})"
        )


def test_ar_wording_lint_compliance():
    """W184 wording discipline across every string in the emitted AR
    document. Forbidden words must always sit inside a negation
    window. Catches both the upstream evidence accidentally accepting
    an overclaim AND the AR emitter introducing new free-form prose
    that drifts.
    """
    doc = _build_ar()
    for s in _walk_strings(doc):
        _scan_for_overclaims(s, location="AR document")


def test_ar_default_title_and_remarks_are_wording_compliant():
    """Pinned AR + stub AP constants must pass the same lint."""
    for text in (
        DEFAULT_AR_TITLE,
        DEFAULT_AR_REMARKS,
        STUB_AP_TITLE,
        STUB_AP_REMARKS,
    ):
        _scan_for_overclaims(text, location="AR/AP constant")


# ---------------------------------------------------------------------------
# Determinism + fixture
# ---------------------------------------------------------------------------


def test_ar_is_deterministic_for_fixed_clock():
    """Same evidence + same clock → byte-identical JSON.

    Catches a future refactor that accidentally introduces a random
    uuid or a wall-clock-dependent field.
    """
    a = _build_ar(now=FIXED_CLOCK)
    b = _build_ar(now=FIXED_CLOCK)
    canon_a = _json.dumps(a, sort_keys=True, separators=(",", ":"))
    canon_b = _json.dumps(b, sort_keys=True, separators=(",", ":"))
    assert canon_a == canon_b, "AR emitter is not deterministic"


def test_ar_uuids_are_uuidv5_form():
    """All emitted UUIDs must be valid UUIDv5 (variant 1, version 5).

    Catches a regression where the emitter falls back to ``uuid4()``
    and the document stops being content-addressed.
    """
    import uuid as _uuid

    doc = _build_ar()
    ar = doc["assessment-results"]
    uuids: list[str] = [ar["uuid"]]
    for party in ar["metadata"].get("parties") or []:
        uuids.append(party["uuid"])
    for r in ar["results"]:
        uuids.append(r["uuid"])
        for obs in r.get("observations") or []:
            uuids.append(obs["uuid"])
            for subj in obs.get("subjects") or []:
                uuids.append(subj["subject-uuid"])
        for f in r.get("findings") or []:
            uuids.append(f["uuid"])
        for risk in r.get("risks") or []:
            uuids.append(risk["uuid"])
    for resource in (ar.get("back-matter") or {}).get("resources") or []:
        uuids.append(resource["uuid"])

    assert uuids, "no UUIDs emitted"
    for u in uuids:
        parsed = _uuid.UUID(u)
        assert parsed.variant == _uuid.RFC_4122, f"non-RFC4122 uuid: {u}"
        assert parsed.version == 5, f"expected UUIDv5, got v{parsed.version}: {u}"


def test_ar_fixture_matches_built_document():
    """The checked-in fixture must match a fresh build at the same clock.

    When the source evidence fixture legitimately changes, the AR
    fixture needs a refresh in the same commit. The error message
    below gives the exact regenerate command.
    """
    if not AR_FIXTURE.exists():
        pytest.skip(f"fixture missing at {AR_FIXTURE}")
    expected = _json.loads(AR_FIXTURE.read_text(encoding="utf-8"))
    actual = _build_ar()
    canon_e = _json.dumps(expected, sort_keys=True, separators=(",", ":"))
    canon_a = _json.dumps(actual, sort_keys=True, separators=(",", ":"))
    assert canon_e == canon_a, (
        "AR fixture drift detected; regenerate with:\n"
        '.venv/Scripts/python.exe -c "import json; '
        "from datetime import datetime, timezone; "
        "from roam.evidence.oscal import build_oscal_assessment_results; "
        "ev = json.loads(open('tests/fixtures/evidence/v1_with_refs.json').read()); "
        "doc = build_oscal_assessment_results(ev, "
        "now=datetime(2026, 5, 14, 12, 0, 0, tzinfo=timezone.utc)); "
        "open('tests/fixtures/oscal/sample-assessment-results.json', 'w', "
        "encoding='utf-8').write(json.dumps(doc, indent=2)+'\\n')\""
    )


# ---------------------------------------------------------------------------
# Stub AP unit test
# ---------------------------------------------------------------------------


def test_stub_ap_has_required_oscal_fields():
    """The stub AP must carry uuid + metadata + import-ssp +
    assessment-subjects so it is schema-valid on its own.
    """
    ap = synthesize_stub_assessment_plan(
        repo_id="github.com/owner/repo",
        now=FIXED_CLOCK,
    )
    plan = ap["assessment-plan"]
    assert isinstance(plan["uuid"], str) and len(plan["uuid"]) == 36
    assert plan["metadata"]["oscal-version"] == OSCAL_VERSION
    assert plan["metadata"]["title"] == STUB_AP_TITLE
    assert "import-ssp" in plan
    assert isinstance(plan["assessment-subjects"], list)
    assert plan["assessment-subjects"]

    # The stub-ness must be advertised in metadata.props.
    props = _ns_props(plan["metadata"]["props"])
    assert props.get("stub") == ["true"]
    assert props.get("repo_id") == ["github.com/owner/repo"]


def test_stub_ap_is_deterministic_per_repo_id():
    """Two stub APs for the same repo_id + same clock must be
    byte-identical, so consumers can dedupe them by uuid.
    """
    a = synthesize_stub_assessment_plan(
        repo_id="github.com/x/y",
        now=FIXED_CLOCK,
    )
    b = synthesize_stub_assessment_plan(
        repo_id="github.com/x/y",
        now=FIXED_CLOCK,
    )
    assert _json.dumps(a, sort_keys=True) == _json.dumps(b, sort_keys=True)


# ---------------------------------------------------------------------------
# CLI surface
# ---------------------------------------------------------------------------


def _invoke(*args: str, json_mode: bool = False) -> tuple[int, str]:
    runner = CliRunner()
    cli_args = (["--json"] if json_mode else []) + ["evidence-oscal", *args]
    result = runner.invoke(cli, cli_args, catch_exceptions=False)
    return result.exit_code, result.output


def test_ar_cli_emits_assessment_results():
    """``--kind assessment-results --evidence <path>`` streams AR JSON."""
    code, out = _invoke(
        "--kind",
        "assessment-results",
        "--evidence",
        str(EVIDENCE_FIXTURE),
        "--indent",
        "0",
    )
    assert code == 0, out
    parsed = _json.loads(out)
    assert "assessment-results" in parsed
    ar = parsed["assessment-results"]
    assert ar["metadata"]["oscal-version"] == OSCAL_VERSION
    assert "import-ap" in ar
    assert len(ar["results"]) >= 1


def test_ar_cli_json_envelope_wraps_document(tmp_path):
    """``roam --json evidence-oscal --kind assessment-results`` wraps
    the OSCAL document in the standard envelope with verdict +
    summary.
    """
    code, out = _invoke(
        "--kind",
        "assessment-results",
        "--evidence",
        str(EVIDENCE_FIXTURE),
        json_mode=True,
    )
    assert code == 0, out
    parsed = _json.loads(out)
    assert parsed.get("command") == "evidence-oscal"
    summary = parsed.get("summary") or {}
    assert "assessment-results" in (summary.get("verdict") or "")
    assert summary.get("kind") == "assessment-results"
    assert summary.get("result_count", 0) >= 1
    assert "oscal_document" in parsed
    assert "assessment-results" in parsed["oscal_document"]


def test_ar_cli_requires_evidence_path():
    """``--kind assessment-results`` without ``--evidence`` exits non-zero
    with a clean error message.
    """
    code, out = _invoke("--kind", "assessment-results")
    assert code != 0
    assert "--evidence" in out


def test_ar_cli_honours_explicit_import_ap_ref():
    """``--import-ap-ref <path>`` makes its way into the AR import-ap href."""
    code, out = _invoke(
        "--kind",
        "assessment-results",
        "--evidence",
        str(EVIDENCE_FIXTURE),
        "--import-ap-ref",
        ".roam/oscal/external-ap.json",
        "--indent",
        "0",
    )
    assert code == 0, out
    parsed = _json.loads(out)
    href = parsed["assessment-results"]["import-ap"]["href"]
    assert href == ".roam/oscal/external-ap.json"


def test_ar_cli_writes_to_disk(tmp_path):
    """``--output`` writes valid AR JSON to disk."""
    out_path = tmp_path / "nested" / "ar.json"
    code, out = _invoke(
        "--kind",
        "assessment-results",
        "--evidence",
        str(EVIDENCE_FIXTURE),
        "--output",
        str(out_path),
        "--indent",
        "2",
    )
    assert code == 0, out
    assert out_path.exists()
    payload = _json.loads(out_path.read_text(encoding="utf-8"))
    assert "assessment-results" in payload
    assert "VERDICT:" in out
    assert "assessment-results" in out


# ---------------------------------------------------------------------------
# W559 — closed-enum validation at the CLI boundary
# ---------------------------------------------------------------------------


def _write_packet_with_unknown_enum(tmp_path) -> Path:
    """Return a fixture packet with an unknown actor_kind injected.

    Cloned from v1_with_refs.json so the rest of the schema stays
    valid; only the closed-enum field is poisoned. Both ``strict`` and
    non-``strict`` paths see exactly the same diff.
    """
    packet = _json.loads(EVIDENCE_FIXTURE.read_text(encoding="utf-8"))
    # actor_refs[0].actor_kind is in the closed ACTOR_KINDS set ("agent",
    # "human", "mcp_client", "tool", "ci_runner", "external"). Anything
    # outside that set must fail in strict mode and drop the row in
    # non-strict mode.
    packet["actor_refs"][0]["actor_kind"] = "wizard"
    out = tmp_path / "evidence_with_unknown_kind.json"
    out.write_text(_json.dumps(packet), encoding="utf-8")
    return out


def test_ar_cli_strict_rejects_unknown_enum(tmp_path):
    """``--strict`` must raise on closed-enum violations (W534 path)."""
    packet_path = _write_packet_with_unknown_enum(tmp_path)
    code, out = _invoke(
        "--kind",
        "assessment-results",
        "--evidence",
        str(packet_path),
        "--strict",
    )
    assert code != 0, f"expected non-zero exit on closed-enum violation under --strict; got code={code} out={out!r}"
    # The error message should make the failure mode visible.
    assert "validation" in out.lower() or "actor_kind" in out.lower(), out


def test_ar_cli_non_strict_drops_unknown_enum_and_emits(tmp_path):
    """Without ``--strict`` the parser drops the row and emits anyway.

    Default behaviour preserves W465 forgiving-projection: the
    packet still loads, the AR document is still emitted, and the
    bad row is silently dropped (with a UserWarning the CLI does not
    surface). The other actor_ref entry must still appear in the
    emitted parties[] list.
    """
    packet_path = _write_packet_with_unknown_enum(tmp_path)
    import warnings

    with warnings.catch_warnings():
        # The fallback path emits a UserWarning per dropped row; we
        # don't want pytest's warnings-as-errors policy (if enabled
        # elsewhere) to flip this success path into a failure.
        warnings.simplefilter("ignore")
        code, out = _invoke(
            "--kind",
            "assessment-results",
            "--evidence",
            str(packet_path),
            "--indent",
            "0",
        )
    assert code == 0, out
    doc = _json.loads(out)
    assert "assessment-results" in doc
    ar = doc["assessment-results"]
    parties = ar.get("metadata", {}).get("parties") or []
    # The poisoned row dropped; the surviving "human:alice@example.com"
    # row should still surface as a party.
    names = {p.get("name") for p in parties}
    assert "Alice" in names or any("alice" in (n or "").lower() for n in names), (
        f"surviving actor row not emitted: parties={parties!r}"
    )


def test_ar_builder_accepts_change_evidence_instance():
    """W559 hybrid signature: ``build_oscal_assessment_results`` must
    accept a ``ChangeEvidence`` dataclass and produce byte-identical
    output to the dict-input path. Guards the projection-boundary
    refactor: parsing through ``from_canonical_json`` then handing
    the typed packet to the builder must not change the bytes.
    """
    from roam.evidence.change_evidence import ChangeEvidence

    raw = EVIDENCE_FIXTURE.read_text(encoding="utf-8")
    packet = ChangeEvidence.from_canonical_json(raw)

    from_dict = build_oscal_assessment_results(
        _json.loads(raw),
        now=FIXED_CLOCK,
    )
    from_dataclass = build_oscal_assessment_results(
        packet,
        now=FIXED_CLOCK,
    )
    canon_dict = _json.dumps(from_dict, sort_keys=True, separators=(",", ":"))
    canon_dc = _json.dumps(
        from_dataclass,
        sort_keys=True,
        separators=(",", ":"),
    )
    assert canon_dict == canon_dc, (
        "AR byte drift between dict-input and ChangeEvidence-input paths: the projection must be input-shape-agnostic."
    )


# ---------------------------------------------------------------------------
# W561 - Pattern 1 variant D disclosure on the AR envelope
# ---------------------------------------------------------------------------


def test_ar_envelope_clean_packet_partial_success_false():
    """Clean packet -> envelope reports zero drops + partial_success false.

    Hash-stability mandate: the W534 31-fixture suite must stay byte-
    identical. The OSCAL document itself is unchanged on the no-drop
    path; the envelope adds two new summary fields
    (``dropped_enum_rows: 0`` + ``partial_success: false``) that the
    schema-migration tests do not assert against.
    """
    code, out = _invoke(
        "--kind",
        "assessment-results",
        "--evidence",
        str(EVIDENCE_FIXTURE),
        json_mode=True,
    )
    assert code == 0, out
    envelope = _json.loads(out)
    summary = envelope["summary"]
    assert summary["dropped_enum_rows"] == 0
    assert summary["partial_success"] is False
    # No ``dropped_reasons`` key on the happy path (omit-when-empty).
    assert "dropped_reasons" not in summary


def test_ar_envelope_dropped_enum_rows_disclosed(tmp_path):
    """W561 fix: poisoned packet -> envelope shows dropped_enum_rows.

    Pattern 1 variant D in CLAUDE.md mandates that a degraded
    resolution must NOT emit a success verdict indistinguishable from
    a fully-resolved success. With one unknown ``actor_kind`` row, the
    envelope must:

    * set ``summary.partial_success: true``
    * set ``summary.dropped_enum_rows`` to a positive count
    * surface the first 5 drop reasons in ``summary.dropped_reasons``
    * append a ``rows`` LAW-4 anchored fact to ``agent_contract.facts``
    * include a ``dropped enum rows`` clause in the verdict string
    """
    packet_path = _write_packet_with_unknown_enum(tmp_path)
    import warnings

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        code, out = _invoke(
            "--kind",
            "assessment-results",
            "--evidence",
            str(packet_path),
            json_mode=True,
        )
    assert code == 0, out
    envelope = _json.loads(out)
    summary = envelope["summary"]
    assert summary["partial_success"] is True
    assert summary["dropped_enum_rows"] >= 1
    assert "dropped_reasons" in summary
    assert isinstance(summary["dropped_reasons"], list)
    assert len(summary["dropped_reasons"]) <= 5
    assert all("actor_ref" in r or "actor_kind" in r for r in summary["dropped_reasons"])
    # Verdict discloses the drop.
    assert "dropped enum rows" in summary["verdict"], summary["verdict"]
    # Facts string surfaces the drop with LAW-4 ``rows`` terminal.
    facts = envelope["agent_contract"]["facts"]
    assert any("dropped enum rows" in f for f in facts), facts
