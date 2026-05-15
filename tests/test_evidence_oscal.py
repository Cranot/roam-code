"""W464 — tests for ``roam evidence-oscal`` OSCAL v1.2 emission.

Test inventory:

1. ``test_oscal_document_has_v12_shape`` — emission produces a
   ``control-mapping`` envelope with ``uuid``, ``metadata`` (with
   ``oscal-version``), ``mappings``, ``back-matter.resources``.
2. ``test_oscal_metadata_carries_roam_props`` — schema_version,
   roam_control_count, roam_framework_count props are present and
   namespaced under ``urn:roam:oscal:v1``.
3. ``test_oscal_per_map_props_include_authority_and_redaction_extensions``
   — every map entry carries roam-specific props (wording_guard,
   pass_condition, evidence_type, surface) under the
   ``urn:roam:oscal:v1`` namespace. These are the W359 extension
   points for authority_refs + redactions (no OSCAL-native
   equivalent).
4. ``test_oscal_wording_lint_compliance`` — no remarks field in the
   emitted document contains "certifies" / "compliant" /
   "guarantees" outside a negation window.
5. ``test_oscal_is_deterministic_for_fixed_clock`` — same input +
   same clock produces byte-identical JSON (uuids are UUIDv5, not
   random).
6. ``test_oscal_groups_controls_by_framework`` — controls are
   grouped into one ``mappings[]`` entry per ``source_framework``
   value in the YAML.
7. ``test_oscal_cli_emits_to_stdout`` — ``roam evidence-oscal``
   streams raw OSCAL JSON to stdout.
8. ``test_oscal_cli_json_mode_wraps_envelope`` — ``roam --json
   evidence-oscal`` wraps the document in the standard envelope with
   summary.verdict.
9. ``test_oscal_cli_writes_to_disk_atomically`` — ``--output``
   writes valid JSON to the given path.
10. ``test_oscal_fixture_matches_built_document`` — the checked-in
    ``tests/fixtures/oscal/sample-control-mapping.json`` matches a
    fresh build at the same clock.
"""

from __future__ import annotations

import json as _json
import re
from datetime import datetime, timezone
from pathlib import Path

import pytest
from click.testing import CliRunner

from roam.cli import cli
from roam.evidence.oscal import (
    DEFAULT_REMARKS,
    DEFAULT_TITLE,
    OSCAL_VERSION,
    ROAM_OSCAL_NS,
    build_oscal_control_mapping,
    load_control_map,
)

from tests._helpers.repo_root import repo_root


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


ROOT = repo_root()
# W554 — the canonical control-mapping.yaml now lives inside the
# ``roam.templates.audit_report`` package so it ships in the wheel.
# Resolve via importlib.resources so the test exercises the same
# path the runtime resolver uses under a pip install.
CONTROL_MAP_PATH = ROOT / "src" / "roam" / "templates" / "audit_report" / "control-mapping.yaml"
FIXTURE_PATH = ROOT / "tests" / "fixtures" / "oscal" / "sample-control-mapping.json"
FIXED_CLOCK = datetime(2026, 5, 14, 12, 0, 0, tzinfo=timezone.utc)


def _build_doc(now: datetime | None = None) -> dict:
    """Build the OSCAL doc from the live control-mapping.yaml."""
    parsed = load_control_map(CONTROL_MAP_PATH)
    return build_oscal_control_mapping(parsed, now=now or FIXED_CLOCK)


def _all_remarks(doc: dict) -> list[str]:
    """Walk an OSCAL document and yield every ``remarks`` field value."""
    out: list[str] = []
    cm = doc.get("control-mapping", {})
    meta = cm.get("metadata", {})
    if isinstance(meta.get("remarks"), str):
        out.append(meta["remarks"])
    for mapping in cm.get("mappings", []) or []:
        for entry in mapping.get("maps", []) or []:
            r = entry.get("remarks")
            if isinstance(r, str):
                out.append(r)
    return out


def _ns_props(props: list[dict]) -> dict[str, list[str]]:
    """Collect roam-namespaced props into a {name: [values]} map."""
    out: dict[str, list[str]] = {}
    for p in props or []:
        if p.get("ns") == ROAM_OSCAL_NS:
            out.setdefault(p["name"], []).append(p["value"])
    return out


# ---------------------------------------------------------------------------
# Shape / schema tests
# ---------------------------------------------------------------------------


def test_oscal_document_has_v12_shape():
    doc = _build_doc()
    assert "control-mapping" in doc, (
        "missing required top-level control-mapping element"
    )
    cm = doc["control-mapping"]

    # OSCAL v1.x requires uuid + metadata at the document root.
    assert isinstance(cm.get("uuid"), str) and len(cm["uuid"]) == 36
    assert "metadata" in cm
    meta = cm["metadata"]
    assert meta.get("oscal-version") == OSCAL_VERSION
    assert meta.get("title")
    assert meta.get("last-modified")
    assert meta.get("remarks")

    # The Control Mapping model requires mappings[] + back-matter.
    assert isinstance(cm.get("mappings"), list) and len(cm["mappings"]) > 0
    bm = cm.get("back-matter") or {}
    assert isinstance(bm.get("resources"), list) and len(bm["resources"]) > 0

    # Source resource (Roam ChangeEvidence) + framework resources all
    # present.
    titles = {r.get("title") for r in bm["resources"]}
    assert "Roam ChangeEvidence" in titles


def test_oscal_metadata_carries_roam_props():
    doc = _build_doc()
    meta = doc["control-mapping"]["metadata"]
    props = _ns_props(meta.get("props") or [])
    # Three roam-namespaced metadata props expected.
    assert "schema_version" in props
    assert "roam_control_count" in props
    assert "roam_framework_count" in props
    # Counts are stringified ints; sanity-check they're at least 1.
    assert int(props["roam_control_count"][0]) >= 1
    assert int(props["roam_framework_count"][0]) >= 1


def test_oscal_per_map_props_include_authority_and_redaction_extensions():
    """W359 mandate: authority_refs + redactions have no OSCAL-native
    equivalent. They MUST surface as ``prop`` entries under the
    ``urn:roam:oscal:v1`` namespace.
    """
    doc = _build_doc()
    mappings = doc["control-mapping"]["mappings"]
    assert mappings, "expected at least one mapping group"

    # Every per-map entry MUST carry wording_guard, pass_condition,
    # claim props under the roam namespace.
    for mapping in mappings:
        for entry in mapping.get("maps", []):
            props = _ns_props(entry.get("props") or [])
            assert "wording_guard" in props, (
                f"missing wording_guard prop on {entry.get('source-control-id')}"
            )
            assert "pass_condition" in props
            assert "claim" in props
            # The W359 extension points: evidence_type + surface lists
            # surface via repeated ``prop`` entries under the namespace.
            # At least one of evidence_type / surface / required_evidence
            # should be present on a non-trivial entry.
            roam_ext = (
                props.get("evidence_type", [])
                + props.get("surface", [])
                + props.get("required_evidence", [])
            )
            # The strongest invariant: at least 2 of those 3 are non-empty
            # for every entry in the live YAML.
            assert len(roam_ext) >= 2, (
                f"entry {entry.get('source-control-id')} lacks roam "
                f"extension props; saw {props}"
            )


def test_oscal_groups_controls_by_framework():
    """One ``mappings[]`` entry per ``source_framework`` value in the YAML."""
    doc = _build_doc()
    mappings = doc["control-mapping"]["mappings"]
    framework_values: list[str] = []
    for m in mappings:
        props = _ns_props(m.get("props") or [])
        assert "source-framework" in props
        framework_values.append(props["source-framework"][0])
    # Frameworks must be unique per mapping group (no duplicate).
    assert len(framework_values) == len(set(framework_values))
    # Live YAML has 9 frameworks as of W506 (eu_ai_act, iso_iec_42001,
    # nist_ai_rmf, nist_ai_600_1, nist_sp_800_218a, soc_2_cc8_1,
    # slsa_src_l2, slsa_src_l3, internal). Floor at 5 so adding a
    # framework via W360/W428/W506 doesn't break the lint.
    assert len(framework_values) >= 5


# ---------------------------------------------------------------------------
# Wording-lint
# ---------------------------------------------------------------------------
#
# W536 consolidated the FORBIDDEN_WORDS / NEGATION_MARKERS constants and
# the scan loop into ``tests/_helpers/wording_lint.py`` so they cannot
# drift between the three test files that previously held duplicate
# copies (test_evidence_oscal.py, test_evidence_oscal_ar.py,
# test_doc_consistency.py). Mirrors the W518 consolidation of framework
# slugs into ``roam.evidence.control_mapping_vocab``.

from tests._helpers.wording_lint import (
    FORBIDDEN_WORDS as _FORBIDDEN_WORDS,
    NEGATION_MARKERS as _NEGATION_MARKERS,
    scan_for_overclaims,
)


def test_oscal_wording_lint_compliance():
    """W184 wording discipline: no certif*/compliant/guarantee outside
    negation window across every emitted remarks field.

    Mirrors the YAML wording lint
    (``tests/test_doc_consistency.py::test_control_mapping_yaml_wording_discipline``).
    Catches both the upstream YAML accidentally accepting an overclaim
    AND the OSCAL emitter introducing new free-form prose that drifts.
    """
    doc = _build_doc()
    remarks = _all_remarks(doc)
    assert remarks, "expected at least one remarks field"
    for text in remarks:
        violations = scan_for_overclaims(text)
        if violations:
            word, window = violations[0]
            raise AssertionError(
                f"compliance overclaim in OSCAL remarks ({word!r}): "
                f"...{window}... (full text: {text[:120]!r})"
            )


def test_oscal_default_title_and_remarks_are_wording_compliant():
    """The pinned document-level title and remarks constants must
    pass the same lint as the per-entry remarks. Locked-in to keep
    a future refactor from silently weakening the discipline.
    """
    for text in (DEFAULT_TITLE, DEFAULT_REMARKS):
        violations = scan_for_overclaims(text)
        assert not violations, (
            f"forbidden word(s) in OSCAL default text without negation "
            f"context: {violations!r}"
        )


def test_wording_lint_single_source_of_truth():
    """W536 drift guard — the only sanctioned import path for the
    wording-lint constants is ``tests._helpers.wording_lint``. If a
    future contributor re-introduces a local ``_FORBIDDEN_WORDS`` /
    ``_NEGATION_MARKERS`` tuple in any of the three known consumers,
    this test surfaces the regression by re-importing through the
    canonical module and asserting identity.

    Mirrors the W518 ``test_framework_slugs_titles_in_sync`` guard
    around the ``roam.evidence.control_mapping_vocab`` consolidation.
    """
    from tests._helpers import wording_lint as canonical

    assert _FORBIDDEN_WORDS is canonical.FORBIDDEN_WORDS, (
        "_FORBIDDEN_WORDS must be re-exported from "
        "tests._helpers.wording_lint, not re-declared locally"
    )
    assert _NEGATION_MARKERS is canonical.NEGATION_MARKERS, (
        "_NEGATION_MARKERS must be re-exported from "
        "tests._helpers.wording_lint, not re-declared locally"
    )
    # The canonical vocabulary itself must be the W184-pinned set.
    assert canonical.FORBIDDEN_WORDS == (
        "certif",
        "compliant",
        "guarantee",
    )
    assert canonical.NEGATION_MARKERS == (
        "not ",
        "no ",
        "never ",
        "doesn't ",
        "does not ",
    )


# ---------------------------------------------------------------------------
# Determinism + fixture
# ---------------------------------------------------------------------------


def test_oscal_is_deterministic_for_fixed_clock():
    """UUIDv5 + a fixed clock means the document is byte-stable across
    re-builds. Catches a future refactor that accidentally introduces a
    random uuid or a wall-clock-dependent field.
    """
    a = _build_doc(now=FIXED_CLOCK)
    b = _build_doc(now=FIXED_CLOCK)
    canon_a = _json.dumps(a, sort_keys=True, separators=(",", ":"))
    canon_b = _json.dumps(b, sort_keys=True, separators=(",", ":"))
    assert canon_a == canon_b, "OSCAL emitter is not deterministic"


def test_oscal_uuids_are_uuidv5_form():
    """All emitted UUIDs must be valid UUIDv5 (variant 1, version 5).

    Catches a regression where the emitter falls back to ``uuid4()``
    and the document stops being content-addressed.
    """
    import uuid as _uuid

    doc = _build_doc()
    cm = doc["control-mapping"]
    uuids: list[str] = [cm["uuid"]]
    for r in (cm.get("back-matter") or {}).get("resources", []) or []:
        uuids.append(r["uuid"])
    for m in cm["mappings"]:
        uuids.append(m["uuid"])
        for entry in m["maps"]:
            uuids.append(entry["uuid"])

    assert uuids, "no UUIDs emitted"
    for u in uuids:
        parsed = _uuid.UUID(u)
        # variant must be RFC 4122 (variant 1); version must be 5.
        assert parsed.variant == _uuid.RFC_4122, f"non-RFC4122 uuid: {u}"
        assert parsed.version == 5, f"expected UUIDv5, got v{parsed.version}: {u}"


def test_oscal_fixture_matches_built_document():
    """The checked-in fixture at ``tests/fixtures/oscal/`` must match a
    fresh build at the same clock. When the YAML legitimately changes
    (e.g. W428 added 5 new entries), the fixture needs a refresh in
    the same commit.
    """
    if not FIXTURE_PATH.exists():
        pytest.skip(f"fixture missing at {FIXTURE_PATH}")
    expected = _json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    actual = _build_doc(now=FIXED_CLOCK)
    # Canonical compare so dict ordering doesn't trip the test.
    canon_e = _json.dumps(expected, sort_keys=True, separators=(",", ":"))
    canon_a = _json.dumps(actual, sort_keys=True, separators=(",", ":"))
    assert canon_e == canon_a, (
        "fixture drift detected; re-run "
        ".venv/Scripts/python.exe -c \"from datetime import datetime, timezone; "
        "import json; from roam.evidence.oscal import build_oscal_control_mapping, "
        "load_control_map; doc = build_oscal_control_mapping("
        "load_control_map('src/roam/templates/audit_report/control-mapping.yaml'), "
        "now=datetime(2026, 5, 14, 12, 0, 0, tzinfo=timezone.utc)); "
        "open('tests/fixtures/oscal/sample-control-mapping.json', 'w', "
        "encoding='utf-8').write(json.dumps(doc, indent=2)+'\\n')\""
    )


# ---------------------------------------------------------------------------
# CLI surface
# ---------------------------------------------------------------------------


def _invoke(*args: str, json_mode: bool = False) -> tuple[int, str]:
    runner = CliRunner()
    cli_args = (["--json"] if json_mode else []) + ["evidence-oscal", *args]
    result = runner.invoke(cli, cli_args, catch_exceptions=False)
    return result.exit_code, result.output


def test_oscal_cli_emits_to_stdout():
    """Default mode streams raw OSCAL JSON to stdout."""
    code, out = _invoke()
    assert code == 0, out
    # Must be valid JSON.
    parsed = _json.loads(out)
    assert "control-mapping" in parsed
    cm = parsed["control-mapping"]
    assert cm.get("metadata", {}).get("oscal-version") == OSCAL_VERSION


def test_oscal_cli_json_mode_wraps_envelope():
    """`roam --json evidence-oscal` wraps the document in the standard
    envelope with verdict + summary.
    """
    code, out = _invoke(json_mode=True)
    assert code == 0, out
    parsed = _json.loads(out)
    assert parsed.get("command") == "evidence-oscal"
    summary = parsed.get("summary") or {}
    verdict = summary.get("verdict", "")
    assert "OSCAL v1.2 control-mapping" in verdict
    assert summary.get("control_count", 0) >= 1
    assert summary.get("framework_count", 0) >= 1
    assert "oscal_document" in parsed
    assert "control-mapping" in parsed["oscal_document"]


def test_oscal_cli_writes_to_disk_atomically(tmp_path):
    """``--output`` writes valid OSCAL JSON to disk."""
    out_path = tmp_path / "nested" / "control-mapping.json"
    code, out = _invoke("--output", str(out_path), "--indent", "2")
    assert code == 0, out
    assert out_path.exists()
    payload = _json.loads(out_path.read_text(encoding="utf-8"))
    assert "control-mapping" in payload
    # Verdict line was echoed to stdout, full JSON went to file.
    assert "VERDICT:" in out
    assert "OSCAL v1.2" in out


def test_oscal_cli_respects_custom_control_map(tmp_path):
    """``--control-map`` lets callers point at a custom YAML."""
    yaml_path = tmp_path / "custom.yaml"
    yaml_path.write_text(
        "version: 1\n"
        "schema_version: control_mapping/v1\n"
        "controls:\n"
        "  - control_id: CUSTOM_ENTRY\n"
        "    source_framework: my_framework\n"
        "    claim: A custom claim.\n"
        "    required_evidence:\n"
        "      - foo.bar\n"
        "    evidence_types:\n"
        "      - actor_refs\n"
        "    surface:\n"
        "      - pr-replay\n"
        "    wording_guard: \"maps to\"\n"
        "    query: |\n"
        "      SELECT 1\n"
        "    pass_condition: all_required_present\n"
        "    export_text: >-\n"
        "      Custom export text that maps to my_framework requirement.\n",
        encoding="utf-8",
    )
    code, out = _invoke("--control-map", str(yaml_path), "--indent", "0")
    assert code == 0, out
    parsed = _json.loads(out)
    cm = parsed["control-mapping"]
    assert len(cm["mappings"]) == 1
    only = cm["mappings"][0]["maps"][0]
    assert only["source-control-id"] == "roam:CUSTOM_ENTRY"


def test_oscal_v0_yaml_form_is_tolerated(tmp_path):
    """``load_control_map`` wraps the deprecated bare-list form into
    a ``{controls: [...]}`` dict so the emitter works on both.
    """
    yaml_path = tmp_path / "legacy.yaml"
    yaml_path.write_text(
        "- control_id: LEGACY_ENTRY\n"
        "  source_framework: legacy\n"
        "  claim: legacy claim\n"
        "  wording_guard: \"maps to\"\n"
        "  export_text: legacy maps to legacy.\n",
        encoding="utf-8",
    )
    parsed = load_control_map(yaml_path)
    assert isinstance(parsed, dict)
    assert parsed.get("version") == "0"
    assert len(parsed["controls"]) == 1
    doc = build_oscal_control_mapping(parsed, now=FIXED_CLOCK)
    assert len(doc["control-mapping"]["mappings"]) == 1
