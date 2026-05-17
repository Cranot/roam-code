"""Closed-enum vocabulary for the control-mapping YAML schema (W518).

Mirrors the ``src/roam/evidence/_vocabulary.py`` pattern: one named
module-level constant per closed enumeration, immutable
``frozenset`` membership, plus a ``dict`` for the slug -> title
display map. The W506 drive-by surfaced that the same allowlists
were duplicated between ``oscal.py`` (the framework -> title dict
inside ``_framework_title()``) and ``tests/test_doc_consistency.py``
(the ``_SOURCE_FRAMEWORK_ALLOWED`` / ``_PASS_CONDITION_ALLOWED`` /
``_SURFACE_ALLOWED`` frozensets) - W506 had to update each side
lockstep, and the next contributor could easily miss one. This
module consolidates them into one source of truth.

Why a separate module from ``_vocabulary.py``?

* ``_vocabulary.py`` defines vocabulary for evidence-packet
  dataclasses (``EvidenceSubject.kind``, ``ChangeEvidence.actor_refs``,
  ``redactions[].reason``). Membership is validated at dataclass
  construction time.
* This module defines vocabulary for the control-mapping YAML
  *input* file (``src/roam/templates/audit_report/control-mapping.yaml``
  inside the wheel via ``importlib.resources``; W554 moved it from
  the project-root ``templates/audit-report/`` location so pip-install
  users get the YAML shipped). Membership is validated by the lint
  tests in ``tests/test_doc_consistency.py`` against the on-disk
  YAML, NOT at dataclass construction.

Drift-guard test lives at the bottom of ``tests/test_doc_consistency.py``:
``FRAMEWORK_SLUGS == set(FRAMEWORK_TITLES.keys())`` is asserted so
the two structures cannot fall out of agreement when a new slug
lands.
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# Framework slugs (W502 closed enum + W506 SLSA / ISO rename)
# ---------------------------------------------------------------------------

#: Closed enumeration of allowed ``source_framework`` slug values on
#: every entry in ``src/roam/templates/audit_report/control-mapping.yaml``
#: (W554 wheel-bundled; ``templates/audit-report/control-mapping.yaml``
#: remains the project-root override fallback).
#:
#: A free-string field invites silent typo splits (e.g. ``iso_42001``
#: vs ``iso_iec_42001`` would create two cohorts and break every
#: "filter by framework" report downstream). W506 renamed
#: ``iso_42001`` -> ``iso_iec_42001`` to match the spec's official
#: name (ISO/IEC 42001:2023) and added ``slsa_src_l2`` /
#: ``slsa_src_l3`` for the SLSA v1.2 Source Track entries.
#:
#: Slugs and what they mean:
#:
#: * ``eu_ai_act_art_12``         - EU AI Act, Article 12 (record-keeping)
#: * ``iso_iec_42001``            - ISO/IEC 42001 (AI management system)
#: * ``nist_ai_rmf``              - NIST AI Risk Management Framework
#: * ``nist_ai_600_1``            - NIST AI 600-1 (Generative AI Profile)
#: * ``nist_sp_800_218a``         - NIST SP 800-218A (SSDF GenAI Community Profile)
#: * ``soc_2_cc8_1``              - SOC 2 CC8.1 (change management)
#: * ``slsa_src_l2``              - SLSA v1.2 Source Track Level 2
#: * ``slsa_src_l3``              - SLSA v1.2 Source Track Level 3
#: * ``internal_ai_change_policy``- Internal AI-change policy
FRAMEWORK_SLUGS: frozenset[str] = frozenset(
    {
        "eu_ai_act_art_12",
        "iso_iec_42001",
        "nist_ai_rmf",
        "nist_ai_600_1",
        "nist_sp_800_218a",
        "soc_2_cc8_1",
        "slsa_src_l2",
        "slsa_src_l3",
        "internal_ai_change_policy",
    }
)


# ---------------------------------------------------------------------------
# Framework slug -> human-readable title (OSCAL resource.title source)
# ---------------------------------------------------------------------------

#: Display titles for each framework slug.
#:
#: Used by ``roam.evidence.oscal._framework_title()`` to populate the
#: ``resource.title`` field on the emitted OSCAL Control Mapping
#: document. Unknown slugs (i.e. a slug present in
#: :data:`FRAMEWORK_SLUGS` without an entry here) would be a drift
#: bug - the drift-guard test in
#: ``tests/test_doc_consistency.py::test_framework_slugs_titles_in_sync``
#: locks the two structures into agreement.
FRAMEWORK_TITLES: dict[str, str] = {
    "eu_ai_act_art_12": "EU AI Act, Article 12 (record-keeping)",
    "iso_iec_42001": "ISO/IEC 42001 (AI management system)",
    "nist_ai_rmf": "NIST AI Risk Management Framework",
    "nist_ai_600_1": "NIST AI 600-1 (Generative AI Profile)",
    "nist_sp_800_218a": "NIST SP 800-218A (SSDF GenAI Community Profile)",
    "soc_2_cc8_1": "SOC 2 CC8.1 (change management)",
    "slsa_src_l2": "SLSA v1.2 Source Track Level 2 (history + provenance)",
    "slsa_src_l3": "SLSA v1.2 Source Track Level 3 (continuous technical controls)",
    "internal_ai_change_policy": "Internal AI-change policy",
}


# ---------------------------------------------------------------------------
# Pass conditions (W503 closed enum)
# ---------------------------------------------------------------------------

#: Closed enumeration of allowed ``pass_condition`` values on every
#: entry in ``src/roam/templates/audit_report/control-mapping.yaml``
#: (W554 wheel-bundled location).
#:
#: Textbook three-state verdict rule. A typo here silently downgrades
#: a hard "all" gate to an "always-fail-on-missing" string compare,
#: so the closed enum is load-bearing.
#:
#: Values and what they mean:
#:
#: * ``all_required_present`` - every ``required_evidence`` entry must
#:                              be present for the control to PASS
#: * ``any_required_present`` - at least one ``required_evidence``
#:                              entry must be present for PASS
#: * ``conditional``          - only evaluate when an external
#:                              precondition holds (e.g. a feature
#:                              flag is enabled)
PASS_CONDITIONS: frozenset[str] = frozenset(
    {
        "all_required_present",
        "any_required_present",
        "conditional",
    }
)


# ---------------------------------------------------------------------------
# Product surfaces (W504 closed enum)
# ---------------------------------------------------------------------------

#: Closed enumeration of allowed ``surface[]`` item values on every
#: entry in ``src/roam/templates/audit_report/control-mapping.yaml``
#: (W554 wheel-bundled location).
#:
#: The six values below are the canonical product surfaces
#: documented inline in the YAML header (control-mapping.yaml lines
#: 26-30).
#:
#: W507: pruned dead 'self-hosted' vocab (zero consumers in the YAML
#: across 23 entries; only appeared in this enum declaration + a
#: header-comment listing). Re-add if a future control-mapping entry
#: needs it — the closed-enum lint will trip on the offending entry
#: until the vocab is restored.
#:
#: Values and what they mean:
#:
#: * ``pr-replay``             - the PR Replay compiled report
#: * ``governance-pack``       - the governance evidence pack
#: * ``review``                - the inline ``roam critique`` review surface
#: * ``team-mcp-gateway``      - the future networked Team MCP gateway
#: * ``due-diligence``         - the due-diligence evidence pack
#: * ``security-reachability`` - the vuln-reachability report
SURFACES: frozenset[str] = frozenset(
    {
        "pr-replay",
        "governance-pack",
        "review",
        "team-mcp-gateway",
        "due-diligence",
        "security-reachability",
    }
)


__all__ = [
    "FRAMEWORK_SLUGS",
    "FRAMEWORK_TITLES",
    "PASS_CONDITIONS",
    "SURFACES",
]
