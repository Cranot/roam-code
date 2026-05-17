"""OSCAL v1.2 emission — Control Mapping (W464) + Assessment Results (W465).

This module compiles two OSCAL v1.2-conformant document shapes from
roam-local data:

* **Control Mapping** (W464) — direct projection of the wheel-bundled
  ``roam.templates.audit_report.control-mapping`` YAML (loaded via
  ``importlib.resources``; the project-root ``templates/audit-report/
  control-mapping.yaml`` stays as a hand-edited override fallback,
  W554). Repo-static; one document per repo. Built via
  :func:`build_oscal_control_mapping`.
* **Assessment Results (AR)** (W465) — per-run projection of one
  ``ChangeEvidence`` packet. Carries findings + observations +
  attestations for one code-change scope. Built via
  :func:`build_oscal_assessment_results`. AR mandates an
  ``import-ap`` reference to an Assessment Plan; when no external AP
  is provided, :func:`synthesize_stub_assessment_plan` generates a
  minimal stub following FedRAMP's continuous-assessment playbook
  (the AP says "the assessment activity is whatever roam did").

v1.2 added Control Mapping as a SEVENTH standalone OSCAL model in
addition to the original six (Catalog / Profile / Component
Definition / SSP / Assessment Plan / Assessment Results / POA&M).
See ``(internal memo)`` for the design
memo covering both AR and Control Mapping shape choices.

Wave-1 thesis: zero new prerequisites. Control Mapping is a direct
serialisation of the data the roam YAML already carries — no
Assessment Plan stub, no ChangeEvidence packet, no DB migration. The
emitter is a PROJECTION (read-only consumer of the YAML); the
ChangeEvidence schema is untouched and the existing 31 schema
migration tests stay byte-identical.

Two roam concepts have NO native OSCAL counterpart (per W359
research, Section 3):

* ``authority_refs`` (mode / permit / lease / policy_rule / approval /
  token_scope) — these are roam-OS substrate identities.
* ``redactions`` (machine_local_path / schema_strict /
  producer_not_available / ...) — OSCAL has no redaction model.

Both surface as OSCAL ``prop`` entries under the ``ns:`` namespace
``urn:roam:oscal:v1`` so external OSCAL tooling can read what it
understands and skip what it does not. The OSCAL ``prop`` mechanism
is the standard extension point — consumers MAY ignore unknown
properties safely.

Wording discipline (W184 lint, enforced by
``tests/test_doc_consistency.py``): every entry uses ``maps to`` or
``supports evidence for``. Never ``certifies``, ``makes compliant``,
``guarantees``.

Returns plain dicts (not OSCAL classes). Serialisation is the
caller's responsibility (``json.dumps(..., indent=2)`` works).
"""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from roam.evidence.change_evidence import ChangeEvidence
from roam.evidence.control_mapping_vocab import FRAMEWORK_TITLES

# ---------------------------------------------------------------------------
# OSCAL v1.2 constants
# ---------------------------------------------------------------------------

#: OSCAL version stamped on the emitted document. The v1.2 Control
#: Mapping model is documented at
#: https://pages.nist.gov/OSCAL-Reference/models/v1.2.0/ . v1.1.2 is
#: the latest tagged GitHub release; schema-level structure is stable
#: between v1.1.2 and v1.2.0 so the document validates against either.
OSCAL_VERSION: str = "1.1.2"

#: Namespace for roam-specific ``prop`` extensions. OSCAL allows any
#: ``ns:`` URI on extension properties; consumers MAY ignore unknown
#: namespaces. Using a ``urn:`` form (not ``https:``) prevents
#: consumers from accidentally dereferencing it as a live URL.
ROAM_OSCAL_NS: str = "urn:roam:oscal:v1"

#: Document-level title for the emitted Control Mapping document.
#: Stays free of compliance-overclaim vocabulary (no "certifies",
#: "compliant"); the W184 wording lint guards this.
DEFAULT_TITLE: str = "Roam control mapping — supports evidence for governance frameworks"

#: Document-level remarks block. Pinned wording per W184; consumers
#: (auditors, GRC tooling) should see this BEFORE drawing conclusions
#: about coverage.
DEFAULT_REMARKS: str = (
    "This Control Mapping maps to / supports evidence for the listed "
    "controls. It does not certify compliance and is not a substitute "
    "for an authorising body's assessment. Roam emits evidence "
    "packets that customers present to their auditor; the auditor "
    "remains the authority."
)


# Deterministic UUIDv5 namespace seed so re-emissions for the same
# control map produce byte-identical documents. UUIDv5 is name-based
# SHA1 — given a fixed namespace and name, output is stable across
# runs and across hosts. This matters for content-hash auditing and
# git-diff stability of generated artifacts.
_UUID_NS: uuid.UUID = uuid.UUID("a3c8b0e2-1234-5678-90ab-cdef00000001")


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def build_oscal_control_mapping(
    control_map: Mapping[str, Any],
    *,
    title: str | None = None,
    document_id: str | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Compile a roam control map into an OSCAL v1.2 Control Mapping doc.

    Parameters
    ----------
    control_map:
        Parsed ``control-mapping.yaml`` (top-level mapping with a
        ``controls`` key). Accepts the v1 schema documented in the
        wheel-bundled ``src/roam/templates/audit_report/
        control-mapping.yaml`` header (W554 wheel-safe location;
        loaded at runtime via ``importlib.resources``).
    title:
        Optional document title. Falls back to :data:`DEFAULT_TITLE`.
        Must comply with the W184 wording lint (no "certifies",
        "compliant", "guarantee" outside negation context).
    document_id:
        Optional explicit document UUID. When ``None`` (the default)
        a deterministic UUIDv5 is computed from the control map's
        content hash so re-emissions stay byte-identical.
    now:
        Optional clock override for testing; defaults to
        ``datetime.now(timezone.utc)``. The timestamp is the only
        non-deterministic field; pass an explicit clock to lock it.

    Returns
    -------
    dict
        OSCAL v1.2 Control Mapping JSON-shaped dict, ready for
        ``json.dumps(..., indent=2)``. Top-level structure::

            {
              "control-mapping": {
                "uuid": "...",
                "metadata": {...},
                "mappings": [{"uuid":..., "source-resource-id":...,
                              "target-resource-id":..., "maps":[...]}],
                "back-matter": {"resources": [...]}
              }
            }

    Wording-discipline invariant
    ----------------------------
    Every entry's ``remarks`` field reproduces the source YAML's
    ``export_text`` verbatim. Because the YAML wording-guard lint
    (``tests/test_doc_consistency.py::test_control_mapping_yaml_wording_discipline``)
    already gates the YAML, the emitted OSCAL inherits the same
    wording discipline by construction. The emitter adds no new
    free-form prose to per-entry text.
    """
    controls = _extract_controls(control_map)

    timestamp = (now or datetime.now(timezone.utc)).replace(microsecond=0)
    last_modified = timestamp.isoformat().replace("+00:00", "Z")

    # Deterministic document id — UUIDv5 over a canonical hash of the
    # input. Stable across re-emissions of the same control map.
    if document_id is None:
        canon = json.dumps(controls, sort_keys=True, separators=(",", ":")).encode("utf-8")
        digest = hashlib.sha256(canon).hexdigest()
        document_id = str(uuid.uuid5(_UUID_NS, f"control-mapping:{digest}"))

    # Group controls by source_framework. OSCAL Control Mapping uses
    # one ``mappings[]`` entry per (source-resource, target-resource)
    # pair — roam's source resource is always "roam-evidence", and the
    # target resource is the framework (eu_ai_act_art_12, etc.).
    by_framework: dict[str, list[Mapping[str, Any]]] = {}
    for entry in controls:
        fw = str(entry.get("source_framework") or "unknown")
        by_framework.setdefault(fw, []).append(entry)

    resources, source_resource_id, framework_resource_ids = _build_resources(sorted(by_framework.keys()))

    mappings = []
    for fw in sorted(by_framework.keys()):
        entries = by_framework[fw]
        mapping_uuid = str(uuid.uuid5(_UUID_NS, f"mapping:{document_id}:{fw}"))
        mappings.append(
            {
                "uuid": mapping_uuid,
                "source-resource-id": source_resource_id,
                "target-resource-id": framework_resource_ids[fw],
                "props": [
                    {
                        "ns": ROAM_OSCAL_NS,
                        "name": "source-framework",
                        "value": fw,
                    }
                ],
                "maps": [_build_map_entry(entry, document_id) for entry in entries],
            }
        )

    metadata: dict[str, Any] = {
        "title": title or DEFAULT_TITLE,
        "last-modified": last_modified,
        "version": str(control_map.get("version") or "1"),
        "oscal-version": OSCAL_VERSION,
        "remarks": DEFAULT_REMARKS,
        "props": [
            {
                "ns": ROAM_OSCAL_NS,
                "name": "schema_version",
                "value": str(control_map.get("schema_version") or "control_mapping/v1"),
            },
            {
                "ns": ROAM_OSCAL_NS,
                "name": "roam_control_count",
                "value": str(len(controls)),
            },
            {
                "ns": ROAM_OSCAL_NS,
                "name": "roam_framework_count",
                "value": str(len(by_framework)),
            },
        ],
    }

    return {
        "control-mapping": {
            "uuid": document_id,
            "metadata": metadata,
            "mappings": mappings,
            "back-matter": {"resources": resources},
        }
    }


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


def _extract_controls(
    control_map: Mapping[str, Any],
) -> list[Mapping[str, Any]]:
    """Pull the controls list out of a parsed YAML.

    Tolerates both the v0 bare-list form (deprecated) and the v1 dict
    form with a top-level ``controls`` key (the canonical schema).
    """
    if isinstance(control_map, list):
        return list(control_map)
    if not isinstance(control_map, Mapping):
        return []
    controls = control_map.get("controls") or control_map.get("entries") or []
    if not isinstance(controls, list):
        return []
    return [c for c in controls if isinstance(c, Mapping)]


def _build_resources(
    framework_names: list[str],
) -> tuple[list[dict[str, Any]], str, dict[str, str]]:
    """Build the OSCAL ``back-matter.resources[]`` list.

    Returns ``(resources, source_resource_id, framework_resource_ids)``.
    The source resource is a single roam-evidence handle; each
    framework gets one target resource whose UUID is stable per
    framework name (UUIDv5).
    """
    resources: list[dict[str, Any]] = []

    source_id = str(uuid.uuid5(_UUID_NS, "resource:roam-evidence"))
    resources.append(
        {
            "uuid": source_id,
            "title": "Roam ChangeEvidence",
            "description": (
                "Portable evidence packet emitted per code change. Source "
                "of evidence that maps to the listed governance controls."
            ),
            "props": [
                {
                    "ns": ROAM_OSCAL_NS,
                    "name": "roam_resource_kind",
                    "value": "evidence_source",
                }
            ],
        }
    )

    framework_resource_ids: dict[str, str] = {}
    for fw in framework_names:
        rid = str(uuid.uuid5(_UUID_NS, f"resource:framework:{fw}"))
        framework_resource_ids[fw] = rid
        resources.append(
            {
                "uuid": rid,
                "title": _framework_title(fw),
                "props": [
                    {
                        "ns": ROAM_OSCAL_NS,
                        "name": "roam_resource_kind",
                        "value": "framework",
                    },
                    {
                        "ns": ROAM_OSCAL_NS,
                        "name": "framework_slug",
                        "value": fw,
                    },
                ],
            }
        )

    return resources, source_id, framework_resource_ids


def _framework_title(slug: str) -> str:
    """Human-readable title for a known framework slug.

    Unknown slugs fall back to the slug itself so the document stays
    valid (OSCAL requires ``title`` on resources). Mapping is
    deliberately conservative — adding a new framework to the YAML
    without updating
    :data:`roam.evidence.control_mapping_vocab.FRAMEWORK_TITLES`
    emits a slug-titled resource, not a crash.

    The mapping lives in
    :mod:`roam.evidence.control_mapping_vocab` (W518) so the YAML
    closed-enum lint (``tests/test_doc_consistency.py``) and the
    OSCAL emitter consume one source of truth.
    """
    return FRAMEWORK_TITLES.get(slug, slug)


def _build_map_entry(
    entry: Mapping[str, Any],
    document_id: str,
) -> dict[str, Any]:
    """Build one OSCAL ``maps[]`` entry from one YAML control row.

    The OSCAL Control Mapping ``map`` element carries:

    * ``uuid``                    - stable per (document, control_id)
    * ``relationship``            - set-theory relation. Roam emits
                                    ``"supports"`` (custom value via
                                    ``urn:roam:oscal:v1`` namespace) —
                                    not OSCAL's strict ``equivalent-to``
                                    / ``subset-of`` because the
                                    roam-evidence-to-control relation
                                    is "provides evidence for", not a
                                    set-theory relation between two
                                    catalogs.
    * ``source-control-id`` (the roam control_id, namespaced)
    * ``target-control-id`` (the framework control / claim)
    * ``remarks`` (the verbatim ``export_text`` — inherits wording
                                    discipline from the YAML lint)
    * ``props`` (roam-specific extensions for authority_refs,
                                    redactions, evidence_types, surface)
    """
    control_id = str(entry.get("control_id") or "UNKNOWN")
    wording_guard = str(entry.get("wording_guard") or "maps to")
    claim = str(entry.get("claim") or "")
    export_text = str(entry.get("export_text") or "")
    pass_condition = str(entry.get("pass_condition") or "all_required_present")

    evidence_types = entry.get("evidence_types") or []
    if not isinstance(evidence_types, list):
        evidence_types = []
    surfaces = entry.get("surface") or []
    if not isinstance(surfaces, list):
        surfaces = []
    required_evidence = entry.get("required_evidence") or []
    if not isinstance(required_evidence, list):
        required_evidence = []

    map_uuid = str(uuid.uuid5(_UUID_NS, f"map:{document_id}:{control_id}"))

    props: list[dict[str, str]] = [
        {
            "ns": ROAM_OSCAL_NS,
            "name": "wording_guard",
            "value": wording_guard,
        },
        {
            "ns": ROAM_OSCAL_NS,
            "name": "pass_condition",
            "value": pass_condition,
        },
        {
            "ns": ROAM_OSCAL_NS,
            "name": "claim",
            "value": claim,
        },
    ]

    # authority_refs surface: no OSCAL native; emit each
    # evidence_type and surface as a ``prop`` entry. This is the
    # "ns: prop" extension point per W359 research.
    for et in evidence_types:
        if isinstance(et, str) and et:
            props.append(
                {
                    "ns": ROAM_OSCAL_NS,
                    "name": "evidence_type",
                    "value": et,
                }
            )
    for surface in surfaces:
        if isinstance(surface, str) and surface:
            props.append(
                {
                    "ns": ROAM_OSCAL_NS,
                    "name": "surface",
                    "value": surface,
                }
            )
    for req in required_evidence:
        if isinstance(req, str) and req:
            props.append(
                {
                    "ns": ROAM_OSCAL_NS,
                    "name": "required_evidence",
                    "value": req,
                }
            )

    return {
        "uuid": map_uuid,
        # OSCAL Control Mapping spec uses tokens like equivalent-to,
        # subset-of, intersects-with. roam emits the extension token
        # "supports" because the relation is "source evidence supports
        # target control" — not a set-theory map between two control
        # catalogues. The ``ns`` namespace marks this as roam-specific.
        "relationship": "supports",
        "source-control-id": f"roam:{control_id}",
        # Without a target catalog OSCAL ID, we synthesise the target
        # control id from the framework slug. Real auditors can rewrite
        # this with a `roam control-id -> NIST control` lookup once an
        # OSCAL catalog import lands.
        "target-control-id": _synthesize_target_control_id(entry),
        "remarks": export_text or claim,
        "props": props,
    }


def _synthesize_target_control_id(entry: Mapping[str, Any]) -> str:
    """Construct a target-control-id from the source framework.

    OSCAL Control Mapping ``target-control-id`` expects an identifier
    from the TARGET catalog. Roam does not import OSCAL catalogs, so
    we synthesise an identifier of the form
    ``<framework_slug>:<control_id>``. A downstream consumer with a
    real catalog can rewrite these via a JSONPath patch.
    """
    framework = str(entry.get("source_framework") or "unknown")
    control_id = str(entry.get("control_id") or "UNKNOWN")
    return f"{framework}:{control_id}"


# ---------------------------------------------------------------------------
# Convenience loader
# ---------------------------------------------------------------------------


def load_control_map(yaml_path: str | Path) -> Mapping[str, Any]:
    """Load + parse ``control-mapping.yaml`` from disk.

    Wrapper around ``yaml.safe_load`` that returns the canonical v1
    dict-with-controls-key shape; bare-list YAML files are wrapped
    into ``{"controls": [...], "version": "0"}`` so the emitter can
    treat both forms uniformly.

    Raises ``RuntimeError`` if PyYAML is not installed; the optional
    dependency keeps roam's base wheel light.
    """
    try:
        import yaml
    except ImportError as exc:  # pragma: no cover - guard
        raise RuntimeError("PyYAML is required to load control-mapping.yaml. Install with: pip install pyyaml") from exc

    text = Path(yaml_path).read_text(encoding="utf-8")
    parsed = yaml.safe_load(text)

    if isinstance(parsed, dict) and "controls" in parsed:
        return parsed
    if isinstance(parsed, list):
        return {"controls": parsed, "version": "0"}
    return {"controls": [], "version": "0"}


# ---------------------------------------------------------------------------
# W465 — Assessment Results (AR) + stub Assessment Plan (AP)
# ---------------------------------------------------------------------------
#
# OSCAL v1.2 Assessment Results is the per-run sibling to Control Mapping.
# Where Control Mapping is a repo-static crosswalk (one document per repo),
# AR is one document per code-change scope: it carries the actual findings,
# observations, and attestations generated during one ChangeEvidence flow.
#
# AR mandates ``import-ap`` pointing to an Assessment Plan document. AI-
# assisted coding workflows do not have a pre-written assessment plan in
# the traditional FISMA sense — there is no human-authored AP describing
# "what we will test and how" before a PR. The recommended mitigation per
# W359 §6 is the FedRAMP continuous-assessment pattern: generate a minimal
# stub AP once per repo, declaring "the assessment activity is whatever
# roam did". The schema accepts this; humans reading the AP see the stub
# is boilerplate. Roam ships :func:`synthesize_stub_assessment_plan` for
# this fallback and accepts an external AP via ``import_ap_ref`` when one
# exists.

#: Pinned title for the AR document. Wording-lint compliant — uses
#: "supports evidence for", never "certifies".
DEFAULT_AR_TITLE: str = "Roam Assessment Results — supports evidence for governance controls"

#: Pinned remarks for the AR document. Carries the same audit-clarity
#: caveat as DEFAULT_REMARKS so consumers see it BEFORE drawing
#: conclusions about coverage.
DEFAULT_AR_REMARKS: str = (
    "This Assessment Results document compiles findings and observations "
    "from one roam ChangeEvidence packet. It maps to / supports evidence "
    "for the controls referenced in the imported Assessment Plan. It "
    "does not certify compliance and is not a substitute for an "
    "authorising body's review."
)

#: Pinned title for the synthesized stub Assessment Plan. The AP is
#: paper-formality boilerplate; the wording flags this explicitly so a
#: human reviewer immediately knows the AP isn't a hand-authored plan.
STUB_AP_TITLE: str = "Roam stub Assessment Plan — continuous AI-assisted code change assessment"

#: Pinned remarks for the stub AP. Explicit about its synthetic nature.
STUB_AP_REMARKS: str = (
    "Synthetic stub Assessment Plan generated by roam. Continuous-assessment "
    "pattern (FedRAMP precedent): the assessment activity is the roam "
    "evidence-gathering flow itself (preflight, impact, critique, tests, "
    "approvals). This stub plan is not a substitute for a hand-authored "
    "Assessment Plan; it satisfies the OSCAL ``import-ap`` requirement so "
    "that per-run Assessment Results documents are schema-valid."
)


def synthesize_stub_assessment_plan(
    repo_id: str,
    *,
    title: str | None = None,
    document_id: str | None = None,
    now: datetime | None = None,
    control_mapping_ref: str | None = None,
) -> dict[str, Any]:
    """Generate a minimal OSCAL v1.2 Assessment Plan stub.

    The stub AP exists to satisfy AR's mandatory ``import-ap``
    reference. It declares a single ``local-definitions`` activity —
    "AI-assisted code change review" — and points back at the
    repo-level Control Mapping document as the source of objectives.
    Following the FedRAMP continuous-assessment pattern, the AP itself
    is boilerplate: the real assessment happens at AR emission time.

    Parameters
    ----------
    repo_id:
        Stable repository identifier (e.g. ``"github.com/owner/repo"``).
        Used as the ``system-id`` and to seed the deterministic UUIDv5
        document id.
    title:
        Optional override for the AP title. Defaults to
        :data:`STUB_AP_TITLE`.
    document_id:
        Optional explicit UUID. When ``None``, computed deterministically
        from ``repo_id`` so re-emissions are byte-identical.
    now:
        Optional clock override for testing.
    control_mapping_ref:
        Optional path/URI to a Control Mapping document. When set,
        becomes a ``back-matter.resources[]`` entry so consumers can
        navigate AP → Control Mapping → controls.

    Returns
    -------
    dict
        OSCAL v1.2 ``assessment-plan`` JSON-shaped dict.
    """
    timestamp = (now or datetime.now(timezone.utc)).replace(microsecond=0)
    last_modified = timestamp.isoformat().replace("+00:00", "Z")

    if document_id is None:
        document_id = str(uuid.uuid5(_UUID_NS, f"stub-assessment-plan:{repo_id}"))

    activity_uuid = str(uuid.uuid5(_UUID_NS, f"stub-ap-activity:{document_id}"))
    # assessment_subject_uuid is reserved for the OSCAL assessment-subjects
    # block once we model concrete subjects (commits / runs). Leaving the
    # deterministic UUID derivation in place documents the slot.
    _reserved_assessment_subject_uuid = str(uuid.uuid5(_UUID_NS, f"stub-ap-subject:{document_id}"))  # noqa: F841

    back_matter_resources: list[dict[str, Any]] = []
    if control_mapping_ref:
        cm_uuid = str(uuid.uuid5(_UUID_NS, f"stub-ap-cm-link:{document_id}"))
        back_matter_resources.append(
            {
                "uuid": cm_uuid,
                "title": "Roam Control Mapping",
                "description": (
                    "Control Mapping document that supports evidence for the "
                    "controls referenced by this Assessment Plan."
                ),
                "props": [
                    {
                        "ns": ROAM_OSCAL_NS,
                        "name": "roam_resource_kind",
                        "value": "control_mapping_ref",
                    }
                ],
                "rlinks": [
                    {
                        "href": control_mapping_ref,
                        "media-type": "application/json",
                    }
                ],
            }
        )

    metadata: dict[str, Any] = {
        "title": title or STUB_AP_TITLE,
        "last-modified": last_modified,
        "version": "1",
        "oscal-version": OSCAL_VERSION,
        "remarks": STUB_AP_REMARKS,
        "props": [
            {
                "ns": ROAM_OSCAL_NS,
                "name": "stub",
                "value": "true",
            },
            {
                "ns": ROAM_OSCAL_NS,
                "name": "repo_id",
                "value": repo_id,
            },
        ],
    }

    plan: dict[str, Any] = {
        "uuid": document_id,
        "metadata": metadata,
        # AR mandates import-ssp on AP — when no SSP exists, we use a
        # self-referential href "#" and declare via props that this is
        # a stub plan with no SSP backing. Real-world consumers (e.g.
        # compliance-trestle) accept this for continuous-assessment
        # flows.
        "import-ssp": {
            "href": "#",
            "remarks": ("No System Security Plan; stub AP for continuous AI-assisted code change assessment."),
        },
        "assessment-subjects": [
            {
                "type": "component",
                "include-all": {},
                "remarks": (
                    "Subject under assessment: all code-change events "
                    "produced by roam-instrumented AI agents in this "
                    "repository."
                ),
            }
        ],
        "local-definitions": {
            "activities": [
                {
                    "uuid": activity_uuid,
                    "title": "AI-assisted code change review",
                    "description": (
                        "Continuous-assessment activity. The activity "
                        "executes whenever roam produces a "
                        "ChangeEvidence packet: preflight, impact, "
                        "critique, tests, approvals — all evidence "
                        "gathered at change-time supports evidence for "
                        "the controls in the linked Control Mapping."
                    ),
                    "props": [
                        {
                            "ns": ROAM_OSCAL_NS,
                            "name": "activity_kind",
                            "value": "continuous_assessment",
                        }
                    ],
                    "related-controls": {
                        # Empty — the linked Control Mapping carries
                        # the full crosswalk. An external consumer can
                        # rewrite this to point at specific objectives
                        # when an OSCAL catalog import lands.
                        "control-selections": [{"include-all": {}}],
                    },
                }
            ],
        },
    }

    if back_matter_resources:
        plan["back-matter"] = {"resources": back_matter_resources}

    return {"assessment-plan": plan}


def build_oscal_assessment_results(
    evidence: "Mapping[str, Any] | ChangeEvidence",  # noqa: F821 — string forward-ref; ChangeEvidence imported under TYPE_CHECKING below
    *,
    import_ap_ref: str | None = None,
    title: str | None = None,
    document_id: str | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Compile one ``ChangeEvidence`` packet into OSCAL v1.2 AR JSON.

    The emitter is a READ-ONLY projection of the ChangeEvidence packet:
    no DB migration, no schema bump on the evidence side. Mapping
    follows ``(internal memo)`` §3:

    * ``ChangeEvidence.verdict``           → ``results[0].title``
    * ``ChangeEvidence.commit_sha``        → ``results[0].props[name=commit-sha]``
    * ``ChangeEvidence.git_range``         → ``results[0].props[name=git-range]``
    * ``ChangeEvidence.diff_hash``         → ``results[0].props[name=diff-hash]``
    * ``ChangeEvidence.run_ids[]``         → repeated ``run-id`` props
    * ``ChangeEvidence.mode``              → ``roam-mode`` prop
    * ``ChangeEvidence.risk_level``        → ``risk-level`` prop
    * ``ChangeEvidence.agent_id``          → ``metadata.parties[type=tool]``
    * ``ChangeEvidence.actor_refs[]``      → ``metadata.parties[]``
    * ``ChangeEvidence.changed_subjects[]`` → ``results[0].observations[].subjects[]``
    * ``ChangeEvidence.findings[]``        → ``results[0].findings[]`` +
                                              ``results[0].observations[]``
    * ``ChangeEvidence.approvals[]``       → ``results[0].attestations[]``
    * ``ChangeEvidence.accepted_risks[]``  → ``results[0].risks[].mitigating-factors[]``
    * ``ChangeEvidence.authority_refs[]``  → ``results[0].props[name=authority-ref]``
                                              (flat per-ref, byte-stable
                                              backward-compat) +
                                              ``results[0].observations[]``
                                              with one observation per
                                              distinct ``authority_kind``
                                              (W350 drive-by; no OSCAL-
                                              native equivalent;
                                              W359 extension point)
    * ``ChangeEvidence.redactions[]``      → ``metadata.props[name=redaction]``
                                              (no OSCAL-native equivalent)
    * ``ChangeEvidence.content_hash``      → ``metadata.props[name=content-hash]``
    * ``ChangeEvidence.artifacts[]``       → ``back-matter.resources[]``

    Parameters
    ----------
    evidence:
        Either a parsed ChangeEvidence packet (the canonical JSON form,
        parsed via ``json.loads``) OR a :class:`ChangeEvidence`
        dataclass instance. Mapping-typed callers continue to work
        unchanged. When a ``ChangeEvidence`` instance is passed, it is
        normalised back to canonical-JSON dict form before the
        projection runs, so the AR output is byte-identical regardless
        of input type (W559: closed-enum validation at the CLI
        boundary, projection stays a pure dict reader).
    import_ap_ref:
        Optional reference (path or URI) to an external Assessment
        Plan document. When ``None``, a stub AP is generated inline
        via :func:`synthesize_stub_assessment_plan` and the AR's
        ``import-ap`` points at it via an embedded stub-AP document
        UUID prefix (``urn:uuid:<stub-uuid>``).
    title:
        Optional AR title override. Defaults to :data:`DEFAULT_AR_TITLE`.
    document_id:
        Optional explicit UUID. When ``None``, derived deterministically
        from the evidence's ``content_hash`` (or evidence_id when
        content_hash is absent) so re-emissions are byte-identical.
    now:
        Optional clock override for testing.

    Returns
    -------
    dict
        OSCAL v1.2 ``assessment-results`` JSON-shaped dict, ready for
        ``json.dumps(..., indent=2)``.

    Wording-discipline invariant
    ----------------------------
    Every emitted ``remarks`` / ``title`` / ``reason`` field uses
    "maps to" or "supports evidence for". No "certifies",
    "compliant", or "guarantees". When an upstream finding carries a
    description, it is reproduced verbatim — the same wording-guard
    discipline that gates the YAML lint applies here too.
    """
    # W559: accept both Mapping and ChangeEvidence at the projection
    # boundary. When a dataclass instance is passed, normalise it back
    # to the canonical-JSON dict shape so the rest of the function is
    # a pure dict reader (preserves byte-identical AR output across
    # both input types; the W465 golden fixture stays unchanged).
    # ``ChangeEvidence`` is imported at top-of-module (W907 cargo-cult
    # cycle hedge removed: change_evidence -> approval/artifact/policy/
    # refs/subject, none of which load oscal — verified by re-import).
    if isinstance(evidence, ChangeEvidence):
        evidence = json.loads(evidence.to_canonical_json())

    evidence_id = str(evidence.get("evidence_id") or "unknown")
    content_hash = evidence.get("content_hash")
    repo_id = evidence.get("repo_id") or "unknown"
    commit_sha = evidence.get("commit_sha")
    git_range = evidence.get("git_range")
    diff_hash = evidence.get("diff_hash")
    run_ids = evidence.get("run_ids") or []
    if not isinstance(run_ids, list):
        run_ids = []
    agent_id = evidence.get("agent_id")
    mode = evidence.get("mode")
    risk_level = evidence.get("risk_level")
    verdict = evidence.get("verdict") or "no verdict recorded"
    started_at = evidence.get("started_at")
    completed_at = evidence.get("completed_at")
    schema_version = str(evidence.get("schema_version") or "1.0.0")

    timestamp = (now or datetime.now(timezone.utc)).replace(microsecond=0)
    last_modified = timestamp.isoformat().replace("+00:00", "Z")

    # Deterministic doc id from content_hash when available; fall back
    # to evidence_id (stable across re-runs for the same packet).
    if document_id is None:
        seed = str(content_hash or f"evidence:{evidence_id}")
        document_id = str(uuid.uuid5(_UUID_NS, f"assessment-results:{seed}"))

    # Build the import-ap reference. When no external AP path is given,
    # synthesize a stub AP and emit it inline in back-matter resources;
    # the AR's import-ap href points at the stub's UUID via urn:uuid:.
    stub_ap: dict[str, Any] | None = None
    if import_ap_ref is None:
        stub_ap = synthesize_stub_assessment_plan(
            repo_id=str(repo_id),
            now=timestamp,
        )
        stub_ap_uuid = stub_ap["assessment-plan"]["uuid"]
        import_ap_block = {
            "href": f"urn:uuid:{stub_ap_uuid}",
            "remarks": (
                "Inline stub Assessment Plan (continuous-assessment "
                "pattern). See back-matter resource for the stub AP "
                "document. Synthesised because no hand-authored AP "
                "exists for AI-assisted code change workflows."
            ),
        }
    else:
        import_ap_block = {
            "href": import_ap_ref,
            "remarks": ("External Assessment Plan reference supplied by caller."),
        }

    # ------------------------------------------------------------------
    # Metadata + parties (actor_refs)
    # ------------------------------------------------------------------
    parties: list[dict[str, Any]] = []
    actor_refs = evidence.get("actor_refs") or []
    if not isinstance(actor_refs, list):
        actor_refs = []
    for ar in actor_refs:
        if not isinstance(ar, Mapping):
            continue
        actor_id = str(ar.get("actor_id") or "unknown")
        actor_kind = str(ar.get("actor_kind") or "external")
        # OSCAL party.type vocabulary: person / organization. We map
        # human/agent/ci_runner/tool/mcp_client → tool (closest fit for
        # non-person actors) and human → person.
        party_type = "person" if actor_kind == "human" else "tool"
        party_uuid = str(uuid.uuid5(_UUID_NS, f"party:{document_id}:{actor_id}"))
        party: dict[str, Any] = {
            "uuid": party_uuid,
            "type": party_type,
            "name": str(ar.get("display_name") or actor_id),
            "props": [
                {
                    "ns": ROAM_OSCAL_NS,
                    "name": "actor_kind",
                    "value": actor_kind,
                },
                {
                    "ns": ROAM_OSCAL_NS,
                    "name": "actor_id",
                    "value": actor_id,
                },
            ],
        }
        trust_tier = ar.get("trust_tier")
        if isinstance(trust_tier, str) and trust_tier:
            party["props"].append(
                {
                    "ns": ROAM_OSCAL_NS,
                    "name": "trust_tier",
                    "value": trust_tier,
                }
            )
        parties.append(party)

    # Agent id (if not already covered by actor_refs) becomes its own
    # tool party for compatibility with consumers that expect a single
    # tool identity.
    if agent_id and not any(p["name"] == str(agent_id) for p in parties):
        parties.append(
            {
                "uuid": str(uuid.uuid5(_UUID_NS, f"party-agent:{agent_id}")),
                "type": "tool",
                "name": str(agent_id),
                "props": [
                    {
                        "ns": ROAM_OSCAL_NS,
                        "name": "actor_kind",
                        "value": "agent",
                    }
                ],
            }
        )

    metadata_props: list[dict[str, str]] = [
        {
            "ns": ROAM_OSCAL_NS,
            "name": "roam-schema-version",
            "value": schema_version,
        },
        {
            "ns": ROAM_OSCAL_NS,
            "name": "evidence_id",
            "value": evidence_id,
        },
    ]
    if content_hash:
        metadata_props.append(
            {
                "ns": ROAM_OSCAL_NS,
                "name": "content-hash",
                "value": str(content_hash),
            }
        )
    redactions = evidence.get("redactions") or []
    if isinstance(redactions, list):
        for reason in redactions:
            if isinstance(reason, str) and reason:
                metadata_props.append(
                    {
                        "ns": ROAM_OSCAL_NS,
                        "name": "redaction",
                        "value": reason,
                    }
                )

    metadata: dict[str, Any] = {
        "title": title or DEFAULT_AR_TITLE,
        "last-modified": last_modified,
        "version": "1",
        "oscal-version": OSCAL_VERSION,
        "remarks": DEFAULT_AR_REMARKS,
        "props": metadata_props,
    }
    if parties:
        metadata["parties"] = parties

    # ------------------------------------------------------------------
    # Results[0]
    # ------------------------------------------------------------------
    result_uuid = str(uuid.uuid5(_UUID_NS, f"result:{document_id}"))
    result_props: list[dict[str, str]] = []
    if commit_sha:
        result_props.append(
            {
                "ns": ROAM_OSCAL_NS,
                "name": "commit-sha",
                "value": str(commit_sha),
            }
        )
    if git_range:
        result_props.append(
            {
                "ns": ROAM_OSCAL_NS,
                "name": "git-range",
                "value": str(git_range),
            }
        )
    if diff_hash:
        result_props.append(
            {
                "ns": ROAM_OSCAL_NS,
                "name": "diff-hash",
                "value": str(diff_hash),
            }
        )
    for rid in run_ids:
        if isinstance(rid, str) and rid:
            result_props.append(
                {
                    "ns": ROAM_OSCAL_NS,
                    "name": "run-id",
                    "value": rid,
                }
            )
    if mode:
        result_props.append(
            {
                "ns": ROAM_OSCAL_NS,
                "name": "roam-mode",
                "value": str(mode),
            }
        )
    if risk_level:
        result_props.append(
            {
                "ns": ROAM_OSCAL_NS,
                "name": "risk-level",
                "value": str(risk_level),
            }
        )

    # authority_refs surface as result-level props (no native OSCAL
    # equivalent — W359 extension point under urn:roam:oscal:v1).
    # The flat per-ref props are preserved for byte-stable backward
    # compatibility; the load-bearing per-kind aggregation now lands as
    # ``observations[]`` entries below (W350 drive-by).
    authority_refs = evidence.get("authority_refs") or []
    if not isinstance(authority_refs, list):
        authority_refs = []
    authority_refs_clean: list[Mapping[str, Any]] = [r for r in authority_refs if isinstance(r, Mapping)]
    for authref in authority_refs_clean:
        ak = authref.get("authority_kind")
        aid = authref.get("authority_id")
        if isinstance(ak, str) and isinstance(aid, str):
            result_props.append(
                {
                    "ns": ROAM_OSCAL_NS,
                    "name": "authority-ref",
                    "value": f"{ak}:{aid}",
                }
            )

    # Observations: one per finding row + one per changed_subject + one
    # per distinct authority_kind (W350 drive-by — close the
    # producer-wired-but-not-consumed gap on the OSCAL AR surface).
    observations: list[dict[str, Any]] = []

    # Authority-axis observations (W350 drive-by). One observation per
    # distinct ``authority_kind`` present in the packet; the observation
    # body lists each authority ref (kind:id) up to AUTHORITY_REFS_CAP
    # entries per kind. Truncated kinds carry a ``truncated`` prop and a
    # ``total_count`` so downstream consumers can detect the cap-hit.
    # Wording stays in the "axis observed" register — never "complied",
    # "satisfied", "certifies".
    _AUTHORITY_REFS_CAP = 10
    by_kind: dict[str, list[Mapping[str, Any]]] = {}
    for authref in authority_refs_clean:
        ak = authref.get("authority_kind")
        if isinstance(ak, str) and ak:
            by_kind.setdefault(ak, []).append(authref)
    for kind in sorted(by_kind.keys()):
        refs_of_kind = by_kind[kind]
        total_count = len(refs_of_kind)
        truncated = total_count > _AUTHORITY_REFS_CAP
        kept = refs_of_kind[:_AUTHORITY_REFS_CAP]

        obs_uuid = str(uuid.uuid5(_UUID_NS, f"obs-authority:{document_id}:{kind}"))
        commit_marker = str(commit_sha) if commit_sha else "<no-commit>"
        # Each ref becomes a subject under the observation so the OSCAL
        # consumer can navigate to the individual permit / lease / etc.
        subjects: list[dict[str, Any]] = []
        for authref in kept:
            aid = authref.get("authority_id") or "<unknown>"
            granted_by = authref.get("granted_by")
            source = authref.get("source")
            subj_props: list[dict[str, str]] = [
                {
                    "ns": ROAM_OSCAL_NS,
                    "name": "authority_kind",
                    "value": kind,
                },
                {
                    "ns": ROAM_OSCAL_NS,
                    "name": "authority_id",
                    "value": str(aid),
                },
            ]
            if isinstance(granted_by, str) and granted_by:
                subj_props.append(
                    {
                        "ns": ROAM_OSCAL_NS,
                        "name": "granted_by",
                        "value": granted_by,
                    }
                )
            if isinstance(source, str) and source:
                subj_props.append(
                    {
                        "ns": ROAM_OSCAL_NS,
                        "name": "source",
                        "value": source,
                    }
                )
            subjects.append(
                {
                    "subject-uuid": str(
                        uuid.uuid5(
                            _UUID_NS,
                            f"subj-authority:{document_id}:{kind}:{aid}",
                        )
                    ),
                    "type": "component",
                    "title": f"{kind}:{aid}",
                    "props": subj_props,
                }
            )

        obs_props: list[dict[str, str]] = [
            {
                "ns": ROAM_OSCAL_NS,
                "name": "authority_kind",
                "value": kind,
            },
            {
                "ns": ROAM_OSCAL_NS,
                "name": "authority_refs_total",
                "value": str(total_count),
            },
        ]
        if truncated:
            obs_props.append(
                {
                    "ns": ROAM_OSCAL_NS,
                    "name": "truncated",
                    "value": "true",
                }
            )

        observations.append(
            {
                "uuid": obs_uuid,
                "title": f"Authority axis observed: {kind}",
                "description": (f"Authority axis: {kind} — {total_count} entries observed at {commit_marker}."),
                "methods": ["EXAMINE"],
                "subjects": subjects,
                "props": obs_props,
                "remarks": (
                    f"Authority-axis observation supports evidence for "
                    f"identity / authorisation review (W350 producer "
                    f"surface). Kind: {kind}. Total observed: {total_count}"
                    + (f"; truncated to first {_AUTHORITY_REFS_CAP} subjects." if truncated else ".")
                ),
            }
        )

    findings_in = evidence.get("findings") or []
    if not isinstance(findings_in, list):
        findings_in = []
    findings_out: list[dict[str, Any]] = []

    for idx, fnd in enumerate(findings_in):
        if not isinstance(fnd, Mapping):
            continue
        f_id = str(fnd.get("finding_id") or fnd.get("id") or f"finding-{idx:04d}")
        f_kind = str(fnd.get("kind") or fnd.get("detector") or "finding")
        f_severity = str(fnd.get("severity") or "info")
        f_subject = str(fnd.get("subject_id") or fnd.get("subject") or fnd.get("path") or "<unknown>")
        f_message = str(fnd.get("message") or fnd.get("description") or fnd.get("claim") or "")

        obs_uuid = str(uuid.uuid5(_UUID_NS, f"obs:{document_id}:{f_id}"))
        subj_uuid = str(uuid.uuid5(_UUID_NS, f"subj:{document_id}:{f_subject}"))
        observations.append(
            {
                "uuid": obs_uuid,
                "title": f"{f_kind} — {f_subject}",
                "description": f_message or f"finding {f_id}",
                "methods": ["EXAMINE"],
                "subjects": [
                    {
                        "subject-uuid": subj_uuid,
                        "type": "component",
                        "title": f_subject,
                        "props": [
                            {
                                "ns": ROAM_OSCAL_NS,
                                "name": "subject-kind",
                                "value": str(fnd.get("subject_kind") or "symbol"),
                            }
                        ],
                    }
                ],
                "props": [
                    {
                        "ns": ROAM_OSCAL_NS,
                        "name": "finding_kind",
                        "value": f_kind,
                    },
                    {
                        "ns": ROAM_OSCAL_NS,
                        "name": "severity",
                        "value": f_severity,
                    },
                ],
                "remarks": (f"Observation supports evidence for control review. Source detector: {f_kind}."),
            }
        )

        finding_uuid = str(uuid.uuid5(_UUID_NS, f"finding:{document_id}:{f_id}"))
        findings_out.append(
            {
                "uuid": finding_uuid,
                "title": f"{f_kind} — {f_subject}",
                "description": f_message or f"finding {f_id}",
                "target": {
                    "type": "objective-id",
                    "target-id": f"roam:{f_kind}",
                    "status": {
                        "state": ("not-satisfied" if f_severity in ("critical", "high", "blocker") else "satisfied"),
                        "reason": (f"Finding {f_id} maps to / supports evidence for the {f_kind} control objective."),
                    },
                },
                "related-observations": [{"observation-uuid": obs_uuid}],
                "props": [
                    {
                        "ns": ROAM_OSCAL_NS,
                        "name": "severity",
                        "value": f_severity,
                    },
                    {
                        "ns": ROAM_OSCAL_NS,
                        "name": "finding_kind",
                        "value": f_kind,
                    },
                ],
            }
        )

    # Changed subjects become subjects on a single bulk observation
    # when they aren't already covered by per-finding observations.
    changed_subjects = evidence.get("changed_subjects") or []
    if isinstance(changed_subjects, list) and changed_subjects:
        bulk_obs_uuid = str(uuid.uuid5(_UUID_NS, f"obs-bulk:{document_id}"))
        bulk_subjects: list[dict[str, Any]] = []
        for cs in changed_subjects:
            if not isinstance(cs, Mapping):
                continue
            sid = str(cs.get("id") or cs.get("subject_id") or "")
            sk = str(cs.get("kind") or cs.get("subject_kind") or "symbol")
            if not sid:
                continue
            bulk_subjects.append(
                {
                    "subject-uuid": str(uuid.uuid5(_UUID_NS, f"subj-bulk:{document_id}:{sid}")),
                    "type": "component",
                    "title": sid,
                    "props": [
                        {
                            "ns": ROAM_OSCAL_NS,
                            "name": "subject-kind",
                            "value": sk,
                        }
                    ],
                }
            )
        if bulk_subjects:
            observations.append(
                {
                    "uuid": bulk_obs_uuid,
                    "title": "changed subjects in scope",
                    "description": (f"Subjects modified by the change scope ({len(bulk_subjects)} entries)."),
                    "methods": ["EXAMINE"],
                    "subjects": bulk_subjects,
                    "remarks": (
                        "Bulk observation supports evidence for change-tracking controls (which symbols changed)."
                    ),
                }
            )

    # Attestations (approvals)
    attestations: list[dict[str, Any]] = []
    approvals = evidence.get("approvals") or []
    if isinstance(approvals, list):
        for idx, ap in enumerate(approvals):
            if not isinstance(ap, Mapping):
                continue
            approver = str(ap.get("approver") or ap.get("party") or "unknown")
            rationale = str(ap.get("rationale") or ap.get("note") or "approval recorded")
            attestations.append(
                {
                    "responsible-parties": [
                        {
                            "role-id": "approver",
                            "party-uuids": [
                                str(
                                    uuid.uuid5(
                                        _UUID_NS,
                                        f"party-approver:{document_id}:{approver}",
                                    )
                                )
                            ],
                        }
                    ],
                    "parts": [
                        {
                            "name": "approval",
                            "title": f"Approval by {approver}",
                            "prose": rationale,
                        }
                    ],
                }
            )

    # Risks + mitigating factors (accepted_risks)
    risks: list[dict[str, Any]] = []
    accepted_risks = evidence.get("accepted_risks") or []
    if isinstance(accepted_risks, list):
        for idx, ar_row in enumerate(accepted_risks):
            if not isinstance(ar_row, Mapping):
                continue
            risk_uuid = str(uuid.uuid5(_UUID_NS, f"risk:{document_id}:{idx}"))
            risk_title = str(ar_row.get("title") or ar_row.get("name") or f"risk-{idx}")
            risk_descr = str(ar_row.get("description") or ar_row.get("rationale") or "accepted risk")
            risks.append(
                {
                    "uuid": risk_uuid,
                    "title": risk_title,
                    "description": risk_descr,
                    "statement": (
                        "Risk accepted; mitigation maps to / supports evidence for risk-acceptance controls."
                    ),
                    "mitigating-factors": [
                        {
                            "uuid": str(
                                uuid.uuid5(
                                    _UUID_NS,
                                    f"mitigation:{document_id}:{idx}",
                                )
                            ),
                            "description": risk_descr,
                        }
                    ],
                }
            )

    result: dict[str, Any] = {
        "uuid": result_uuid,
        "title": str(verdict),
        "description": (
            f"Roam ChangeEvidence packet {evidence_id} compiled into "
            f"OSCAL Assessment Results. Maps to / supports evidence "
            f"for the controls referenced by the imported Assessment "
            f"Plan."
        ),
        "start": str(started_at or last_modified),
        "reviewed-controls": {
            # An AR result needs at least one control selection; we
            # defer to the linked Control Mapping by selecting all.
            "control-selections": [{"include-all": {}}]
        },
    }
    if completed_at:
        result["end"] = str(completed_at)
    if result_props:
        result["props"] = result_props
    if observations:
        result["observations"] = observations
    if findings_out:
        result["findings"] = findings_out
    if attestations:
        result["attestations"] = attestations
    if risks:
        result["risks"] = risks

    # ------------------------------------------------------------------
    # Back-matter (artifacts + stub AP)
    # ------------------------------------------------------------------
    bm_resources: list[dict[str, Any]] = []

    artifacts = evidence.get("artifacts") or []
    if isinstance(artifacts, list):
        for idx, art in enumerate(artifacts):
            if not isinstance(art, Mapping):
                continue
            art_id = str(art.get("artifact_id") or f"artifact-{idx:04d}")
            art_kind = str(art.get("kind") or "other")
            art_title = str(art.get("title") or art_id)
            art_href = str(art.get("href") or art.get("path") or art.get("uri") or "")
            res: dict[str, Any] = {
                "uuid": str(uuid.uuid5(_UUID_NS, f"artifact:{document_id}:{art_id}")),
                "title": art_title,
                "props": [
                    {
                        "ns": ROAM_OSCAL_NS,
                        "name": "artifact-kind",
                        "value": art_kind,
                    }
                ],
            }
            if art_href:
                res["rlinks"] = [
                    {
                        "href": art_href,
                        "media-type": "application/json",
                    }
                ]
            ch = art.get("content_hash") or art.get("hash")
            if isinstance(ch, str) and ch:
                res["props"].append(
                    {
                        "ns": ROAM_OSCAL_NS,
                        "name": "content-hash",
                        "value": ch,
                    }
                )
            bm_resources.append(res)

    # When we synthesized a stub AP inline, embed it as a back-matter
    # resource so the AR document is self-contained.
    if stub_ap is not None:
        stub_ap_doc = stub_ap["assessment-plan"]
        bm_resources.append(
            {
                "uuid": stub_ap_doc["uuid"],
                "title": stub_ap_doc["metadata"]["title"],
                "description": (
                    "Inline stub Assessment Plan generated by roam. The AR "
                    "document's import-ap field references this resource "
                    "via urn:uuid:."
                ),
                "props": [
                    {
                        "ns": ROAM_OSCAL_NS,
                        "name": "roam_resource_kind",
                        "value": "stub_assessment_plan",
                    },
                    {
                        "ns": ROAM_OSCAL_NS,
                        "name": "stub",
                        "value": "true",
                    },
                ],
                # Embed the full AP doc so consumers can resolve the
                # urn:uuid:<ap-uuid> reference without external fetches.
                "rlinks": [],
                "remarks": json.dumps(stub_ap, separators=(",", ":")),
            }
        )

    ar: dict[str, Any] = {
        "uuid": document_id,
        "metadata": metadata,
        "import-ap": import_ap_block,
        "results": [result],
    }
    if bm_resources:
        ar["back-matter"] = {"resources": bm_resources}

    return {"assessment-results": ar}


__all__ = [
    "DEFAULT_AR_REMARKS",
    "DEFAULT_AR_TITLE",
    "DEFAULT_REMARKS",
    "DEFAULT_TITLE",
    "OSCAL_VERSION",
    "ROAM_OSCAL_NS",
    "STUB_AP_REMARKS",
    "STUB_AP_TITLE",
    "build_oscal_assessment_results",
    "build_oscal_control_mapping",
    "load_control_map",
    "synthesize_stub_assessment_plan",
]
