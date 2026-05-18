"""``roam evidence-oscal`` — emit OSCAL v1.2 docs (W464 + W465).

Two emission modes, chosen via ``--kind``:

* ``--kind control-mapping`` (default; W464) — repo-static crosswalk
  document compiled from the wheel-bundled
  ``src/roam/templates/audit_report/control-mapping.yaml`` (loaded via
  ``importlib.resources``; W554). The project-root
  ``templates/audit-report/control-mapping.yaml`` stays as a
  hand-edited override fallback. Zero prerequisites, one document
  per repo.
* ``--kind assessment-results`` (W465) — per-run document compiled
  from one ``ChangeEvidence`` packet (``--evidence <path>``). AR
  mandates an ``import-ap`` reference to an Assessment Plan; pass
  ``--import-ap-ref <path>`` to point at an external AP, or omit it
  to have roam synthesize a minimal stub AP inline (FedRAMP
  continuous-assessment pattern).

OSCAL v1.2 added a SEVENTH model — standalone **Control Mapping** —
not in the original 6-model description. It is a direct, zero-
prerequisite fit for the existing wheel-bundled
``src/roam/templates/audit_report/control-mapping.yaml`` source-of-truth
file (W554 moved it inside the package; the legacy project-root path
remains as a downstream-user override):
no Assessment Plan, no SSP, no DB migration, no ChangeEvidence
schema touch. The emitter is a PROJECTION (read-only consumer of
the YAML). The W465 AR path keeps the same projection discipline —
it reads from one parsed ChangeEvidence packet and writes the OSCAL
AR shape, never modifying the source.

These commands are Phase 4 of the evidence-compiler thesis. The
strategic memo is ``(internal memo)``
(W359 research) — both AR and CM shape choices are documented there.

Default behaviour: stream OSCAL v1.2 JSON to stdout. Use
``--output <path>`` to write to disk (atomic write via
``roam.atomic_io``). Use ``--json`` for the standard roam envelope
wrapper around the OSCAL document; otherwise the raw OSCAL JSON is
emitted (which is what an external OSCAL consumer wants).

Wording discipline (W184 lint, see
``tests/test_doc_consistency.py``): every entry in the emitted
document inherits its wording from the source YAML's ``export_text``
field (CM) or the upstream finding's description verbatim (AR). The
YAML lint already gates that field; the OSCAL emitter adds no new
free-form prose to per-entry text. The document-level title and
remarks are pinned to W184-compliant constants in
``roam.evidence.oscal``.

Output formats: text (default), ``--json``. SARIF is deliberately NOT
emitted because evidence-oscal outputs are OSCAL compliance documents
— not per-location violations. SARIF is reserved for findings with
file:line coordinates; evidence-oscal's primary deliverable is the
OSCAL v1.2 control-mapping or assessment-results document. See
action.yml _SUPPORTED_SARIF allowlist + W1175-RESEARCH Bucket C
propagation plan + W1148 audit memo.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import click

from roam.capability import roam_capability
from roam.evidence.change_evidence import ChangeEvidence
from roam.evidence.oscal import (
    build_oscal_assessment_results,
    build_oscal_control_mapping,
    load_control_map,
)
from roam.output.formatter import json_envelope, to_json

# Closed enumeration of supported emission kinds. Adding a new shape
# (e.g. POA&M, SSP) belongs here AND in roam.evidence.oscal AND in the
# MCP wrapper's docstring + tests. A typo on the CLI now fails the
# Click choice check rather than silently emitting the wrong document.
_KIND_CONTROL_MAPPING = "control-mapping"
_KIND_ASSESSMENT_RESULTS = "assessment-results"
_OSCAL_KINDS: tuple[str, ...] = (
    _KIND_CONTROL_MAPPING,
    _KIND_ASSESSMENT_RESULTS,
)


# Default location of the control map. Resolved at command-invocation
# time so plugins / tests can supply an alternative path via the
# ``--control-map`` flag.
#
# Pre-W554 this was a fixed ``Path("templates/audit-report/control-mapping.yaml")``
# relative to CWD. That works for source-checkout users but FAILS under
# ``pip install roam-code`` because the YAML did not ship in the wheel.
# W554 lifts the YAML into ``src/roam/templates/audit_report/`` so the
# wheel ships it, and we resolve via ``importlib.resources`` for the
# default — preserving CWD-relative override as a fallback so downstream
# users who keep a hand-edited YAML at the legacy project-root path
# continue to be picked up.
_DEFAULT_CONTROL_MAP = Path("templates/audit-report/control-mapping.yaml")


def _default_control_map_path() -> Path:
    """Resolve the default control-mapping.yaml path (W554, wheel-safe).

    Priority:

    1. CWD-relative ``templates/audit-report/control-mapping.yaml`` —
       downstream-user override (their hand-edited copy).
    2. Wheel-bundled ``roam.templates.audit_report`` resource.

    Falls back to the legacy CWD-relative path string when neither
    resolves; the caller's ``Path.exists()`` check then surfaces a
    clean error envelope pointing the user at ``--control-map``.
    """
    cwd_candidate = Path.cwd() / _DEFAULT_CONTROL_MAP
    if cwd_candidate.exists():
        return cwd_candidate

    try:
        from importlib.resources import files

        package_resource = files("roam.templates.audit_report") / "control-mapping.yaml"
        # W668: previously wrapped this in ``as_file(...)`` and captured
        # the result OUTSIDE the ``with`` block — the W643 anti-pattern.
        # ``roam.templates.audit_report`` is a real package (W664 lint
        # enforces ``__init__.py``), so ``files()`` returns a concrete
        # on-disk Path. Skip ``as_file()`` and normalise directly.
        resolved = Path(str(package_resource))
        if resolved.exists():
            return resolved
    except (FileNotFoundError, ModuleNotFoundError, AttributeError):
        pass

    # Final fallback — the original CWD-relative path. Caller's
    # ``map_path.exists()`` will fail next, surfacing a clean message.
    return _DEFAULT_CONTROL_MAP


@roam_capability(
    name="evidence-oscal",
    category="review",
    summary=("Emit OSCAL v1.2 Control Mapping (default) or Assessment Results (--kind assessment-results) JSON."),
    inputs=["control_map_path", "evidence_path"],
    outputs=[
        "OSCAL document",
        "framework count",
        "control count",
        "result count",
    ],
    examples=[
        "roam evidence-oscal",
        "roam evidence-oscal --output .roam/oscal/control-mapping.json",
        "roam --json evidence-oscal",
        "roam evidence-oscal --kind assessment-results --evidence .roam/evidence/last.json",
    ],
    tags=[
        "evidence",
        "oscal",
        "control-mapping",
        "assessment-results",
        "governance",
    ],
    ai_safe=True,
    requires_index=False,
    maturity="beta",
    mcp_expose=True,
    # Not a daily-workflow core tool — OSCAL emission is a niche
    # governance/compliance surface, opt-in via the full preset.
    mcp_preset=("full",),
    side_effect=False,
    task_required=False,
    destructive=False,
    stale_sensitive=False,
)
@click.command("evidence-oscal")
@click.option(
    "--kind",
    "kind",
    type=click.Choice(_OSCAL_KINDS),
    default=_KIND_CONTROL_MAPPING,
    show_default=True,
    help=(
        "OSCAL emission shape. control-mapping (default) emits the "
        "repo-static crosswalk document. assessment-results emits a "
        "per-run AR document built from a ChangeEvidence packet "
        "(requires --evidence)."
    ),
)
@click.option(
    "--control-map",
    "control_map",
    type=click.Path(dir_okay=False, readable=True),
    default=None,
    help=(
        "Path to control-mapping.yaml. Defaults to the wheel-bundled "
        "copy under roam.templates.audit_report; falls back to "
        "templates/audit-report/control-mapping.yaml relative to CWD "
        "if you keep a hand-edited copy. Used only when --kind "
        "control-mapping."
    ),
)
@click.option(
    "--evidence",
    "evidence_path",
    type=click.Path(dir_okay=False, readable=True),
    default=None,
    help=("Path to a ChangeEvidence JSON packet. Required when --kind assessment-results."),
)
@click.option(
    "--import-ap-ref",
    "import_ap_ref",
    type=str,
    default=None,
    help=(
        "Reference (path or URI) to an external Assessment Plan. "
        "When omitted on --kind assessment-results, a stub AP is "
        "synthesized inline. Used only when --kind assessment-results."
    ),
)
@click.option(
    "--strict",
    "strict",
    is_flag=True,
    default=False,
    help=(
        "Validate the ChangeEvidence packet against closed-enum "
        "vocabulary (W534). When set, unknown enum values raise an "
        "error; default off preserves W465 forgiving-projection "
        "behaviour (unknown rows emit a UserWarning and drop). Used "
        "only when --kind assessment-results."
    ),
)
@click.option(
    "--output",
    "-o",
    "output_path",
    type=click.Path(dir_okay=False, writable=True),
    default=None,
    help=("Write OSCAL JSON to this path instead of stdout. Parent directories are created if missing."),
)
@click.option(
    "--title",
    "title",
    type=str,
    default=None,
    help=(
        "Document-level title. Defaults to a W184-compliant constant; "
        "must use 'maps to' or 'supports evidence for' phrasing if "
        "overridden."
    ),
)
@click.option(
    "--indent",
    type=int,
    default=2,
    show_default=True,
    help=("JSON indent for pretty-printing. Use 0 for compact output."),
)
@click.pass_context
def evidence_oscal(
    ctx,
    kind,
    control_map,
    evidence_path,
    import_ap_ref,
    strict,
    output_path,
    title,
    indent,
):
    """Emit an OSCAL v1.2 document (Control Mapping or Assessment Results).

    Default ``--kind control-mapping``:
      Reads the wheel-bundled
      ``src/roam/templates/audit_report/control-mapping.yaml`` (or
      ``--control-map PATH``) and emits a single OSCAL v1.2 Control
      Mapping JSON document. The project-root
      ``templates/audit-report/control-mapping.yaml`` is honoured as
      a hand-edited override when present (W554 fallback).

    ``--kind assessment-results``:
      Reads a ``ChangeEvidence`` packet from ``--evidence PATH`` and
      emits a single OSCAL v1.2 Assessment Results JSON document with
      an ``import-ap`` reference (synthesized stub by default; pass
      ``--import-ap-ref PATH`` for an external Assessment Plan).

    Two roam-specific concepts that have no OSCAL native equivalent —
    authority_refs (mode/permit/lease/...) and redactions — surface
    as ``prop`` entries under the ``urn:roam:oscal:v1`` namespace so
    external OSCAL tooling can safely ignore unknown extensions.

    Document-level title and remarks are pinned to W184-compliant
    constants. Every per-entry ``remarks`` field is the verbatim
    ``export_text`` from the YAML / finding description (the YAML
    wording-guard lint enforces 'maps to' / 'supports evidence for'
    phrasing; AR observations reproduce the upstream finding's
    description verbatim).
    """
    json_mode = ctx.obj.get("json") if ctx.obj else False
    token_budget = ctx.obj.get("budget", 0) if ctx.obj else 0

    if kind == _KIND_ASSESSMENT_RESULTS:
        if not evidence_path:
            raise click.ClickException("--kind assessment-results requires --evidence <path-to-ChangeEvidence.json>")
        ev_path = Path(evidence_path)
        if not ev_path.exists():
            raise click.ClickException(f"evidence packet not found at {ev_path!s}")
        # W559: parse via ChangeEvidence.from_canonical_json so closed-
        # enum validation runs at the CLI boundary. --strict surfaces
        # unknown vocabulary as a hard error; the default keeps W465's
        # forgiving-projection behaviour (unknown rows drop with a
        # UserWarning, packet still loads).
        # W561: use the drop-aware classmethod so the AR envelope can
        # surface ``dropped_enum_rows`` + ``partial_success`` instead of
        # silently emitting a fully-resolved-looking success verdict
        # (Pattern 1 variant D fix).
        raw_text = ev_path.read_text(encoding="utf-8")
        try:
            evidence, dropped = ChangeEvidence.from_canonical_json_with_drops(
                raw_text,
                strict=strict,
            )
        except ValueError as exc:
            # ValueError covers malformed JSON, missing evidence_id,
            # and (with --strict) closed-enum violations. The
            # underlying message already names the failing field.
            raise click.ClickException(f"evidence packet failed validation: {exc}")

        # W556: auto-detect the canonical stub Assessment Plan written by
        # ``roam ci-setup --with-oscal`` (W535) when ``--import-ap-ref`` is
        # not supplied. The W535 emitter writes the stub AP to
        # ``.roam/oscal/stub-assessment-plan.json`` precisely so AR
        # emissions can reference it by stable path instead of inlining
        # the stub every time (FedRAMP continuous-assessment pattern).
        # Falling back to None (inline-stub synthesis) when the canonical
        # file is absent preserves the pre-W556 default behaviour.
        if import_ap_ref is None:
            canonical_ap = Path.cwd() / ".roam" / "oscal" / "stub-assessment-plan.json"
            if canonical_ap.is_file():
                import_ap_ref = str(canonical_ap)
                click.echo(f"Auto-detected Assessment Plan at {canonical_ap}", err=True)

        doc = build_oscal_assessment_results(
            evidence,
            import_ap_ref=import_ap_ref,
            title=title,
        )
        ar = doc["assessment-results"]
        results = ar.get("results") or []
        result_count = len(results)
        finding_count = sum(len((r or {}).get("findings") or []) for r in results)
        observation_count = sum(len((r or {}).get("observations") or []) for r in results)
        framework_count = 0
        control_count = finding_count
        document_uuid = ar["uuid"]

        # W350 drive-by: surface authority-axis summary counters on the
        # envelope so consumers reading only ``summary`` can detect the
        # P1.10 producer surface (identity / authority / evidence Q2)
        # without re-parsing the OSCAL document. Always-emit shape
        # (Pattern-2): all 6 AUTHORITY_KINDS keys present, zero-padded
        # so consumers don't branch on "did the projection populate
        # this?". Counts come straight from the parsed evidence packet
        # — they are not derived from observations[] (observations are
        # capped at AUTHORITY_REFS_CAP per kind; counters are uncapped
        # totals).
        from roam.evidence._vocabulary import AUTHORITY_KINDS as _AUTH_KINDS

        authority_kinds_count: dict[str, int] = {k: 0 for k in sorted(_AUTH_KINDS)}
        for _aref in getattr(evidence, "authority_refs", None) or ():
            _ak = getattr(_aref, "authority_kind", None)
            if isinstance(_ak, str) and _ak in authority_kinds_count:
                authority_kinds_count[_ak] += 1
        authority_refs_total = sum(authority_kinds_count.values())

        # W561 Pattern 1 variant D: when non-strict parsing dropped any
        # rows, the verdict + envelope MUST disclose the degradation.
        # `dropped` is always a list (empty when nothing was dropped); we
        # branch only on a non-empty list so the no-drop path keeps the
        # original byte-for-byte verdict string.
        dropped_count = len(dropped)
        if dropped_count:
            verdict = (
                f"emitted OSCAL v1.2 assessment-results with "
                f"{result_count} results, {finding_count} findings, "
                f"{observation_count} observations "
                f"({dropped_count} dropped enum rows)"
            )
        else:
            verdict = (
                f"emitted OSCAL v1.2 assessment-results with "
                f"{result_count} results, {finding_count} findings, "
                f"{observation_count} observations"
            )

        ar_counts: dict = {
            "result_count": result_count,
            "finding_count": finding_count,
            "observation_count": observation_count,
            "control_count": control_count,
            "framework_count": framework_count,
            "import_ap_ref": import_ap_ref,
            # W350 drive-by: authority-axis projection counters.
            "authority_refs_count": authority_refs_total,
            "authority_kinds": authority_kinds_count,
            # W561 disclosure fields: always present so the envelope
            # shape is stable; values are 0 / False on the happy path so
            # the no-drop fixtures stay byte-stable.
            "dropped_enum_rows": dropped_count,
            "partial_success": bool(dropped_count),
        }
        if dropped_count:
            # Surface the first 5 reasons for grep-ability. Cap at 5 to
            # keep the envelope token-budget-friendly; the full UserWarning
            # stream still carries every reason for callers that want
            # them all.
            ar_counts["dropped_reasons"] = dropped[:5]

        return _emit_doc(
            doc=doc,
            verdict=verdict,
            kind=kind,
            indent=indent,
            output_path=output_path,
            json_mode=json_mode,
            token_budget=token_budget,
            document_uuid=document_uuid,
            counts=ar_counts,
        )

    # ---------------- control-mapping path (W464) ----------------
    map_path = Path(control_map) if control_map else _default_control_map_path()
    if not map_path.exists():
        raise click.ClickException(
            f"control map not found at {map_path!s}; pass --control-map to point at an explicit file"
        )

    try:
        parsed = load_control_map(map_path)
    except RuntimeError as exc:
        # PyYAML not installed — surface a clean error envelope.
        raise click.ClickException(str(exc))

    doc = build_oscal_control_mapping(parsed, title=title)
    cm = doc["control-mapping"]

    # Surface counts up front for the verdict / fact strings.
    mappings = cm.get("mappings") or []
    control_count = sum(len(m.get("maps") or []) for m in mappings)
    framework_count = len(mappings)
    document_uuid = cm["uuid"]

    verdict = f"emitted OSCAL v1.2 control-mapping with {control_count} controls across {framework_count} frameworks"

    return _emit_doc(
        doc=doc,
        verdict=verdict,
        kind=kind,
        indent=indent,
        output_path=output_path,
        json_mode=json_mode,
        token_budget=token_budget,
        document_uuid=document_uuid,
        counts={
            "control_count": control_count,
            "framework_count": framework_count,
        },
    )


def _emit_doc(
    *,
    doc: dict,
    verdict: str,
    kind: str,
    indent: int,
    output_path: str | None,
    json_mode: bool,
    token_budget: int,
    document_uuid: str,
    counts: dict,
) -> None:
    """Render + emit one OSCAL document.

    Shared tail for both Control Mapping (W464) and Assessment
    Results (W465) emission. Keeps the command body focused on the
    kind-specific build step; the rendering / file-write / envelope
    discipline is identical across kinds.
    """
    # Serialise the OSCAL document — separators are tuple to avoid
    # trailing spaces; indent=0 gives compact one-line output.
    if indent and indent > 0:
        rendered = json.dumps(doc, indent=indent, sort_keys=False)
    else:
        rendered = json.dumps(doc, separators=(",", ":"), sort_keys=False)

    # Write to disk if requested, using the atomic_io helper to avoid
    # half-written files on crash. Parent directory is created if
    # missing (parents=True, exist_ok=True).
    if output_path:
        try:
            from roam.atomic_io import atomic_write_text
        except ImportError:
            # Fallback path: if atomic_io isn't on the import path for
            # some reason, fall back to plain write. Not ideal but
            # never blocks the emission.
            out = Path(output_path)
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(rendered, encoding="utf-8")
        else:
            out = Path(output_path)
            out.parent.mkdir(parents=True, exist_ok=True)
            atomic_write_text(out, rendered)

    if json_mode:
        # In JSON mode we wrap the OSCAL document in the standard
        # roam envelope so agents see verdict + summary first, then
        # the OSCAL document as a payload field. Facts strings are
        # LAW-4 concrete-noun terminal compliant.
        facts: list[str] = []
        if kind == _KIND_ASSESSMENT_RESULTS:
            facts.append(f"{counts.get('result_count', 0)} results")
            facts.append(f"{counts.get('finding_count', 0)} findings")
            facts.append(f"{counts.get('observation_count', 0)} observations")
            facts.append("OSCAL v1.2 assessment-results document")
            # W350 drive-by: LAW-4 ``records`` terminal is anchored.
            facts.append(f"{counts.get('authority_refs_count', 0)} authority records")
            # W561 Pattern 1 variant D: surface dropped rows in facts
            # so an agent reading only ``agent_contract.facts`` still
            # sees the degradation. LAW 4 anchor: terminal noun is the
            # plural "rows", already in the anchor set.
            dropped_rows = counts.get("dropped_enum_rows") or 0
            if dropped_rows:
                facts.append(f"{dropped_rows} dropped enum rows")
        else:
            facts.append(f"{counts.get('control_count', 0)} controls")
            facts.append(f"{counts.get('framework_count', 0)} frameworks")
            facts.append("OSCAL v1.2 control-mapping document")

        next_commands: list[str] = []
        if output_path:
            facts.append(f"wrote {Path(output_path).as_posix()}")
        else:
            if kind == _KIND_ASSESSMENT_RESULTS:
                next_commands.append(
                    "roam evidence-oscal --kind assessment-results --evidence <path> --output .roam/oscal/ar.json"
                )
            else:
                next_commands.append("roam evidence-oscal --output .roam/oscal/control-mapping.json")

        summary = {
            "verdict": verdict,
            "partial_success": False,
            "kind": kind,
            "document_uuid": document_uuid,
            "output_path": str(output_path) if output_path else None,
        }
        summary.update(counts)

        envelope = json_envelope(
            "evidence-oscal",
            summary=summary,
            budget=token_budget,
            oscal_document=doc,
            agent_contract={
                "facts": facts,
                "next_commands": next_commands,
            },
        )
        click.echo(to_json(envelope))
        return

    if output_path:
        # When writing to a file, emit only the verdict line on stdout
        # so the command is shell-pipeline friendly (callers can grep
        # or capture the verdict without polluting their consumer
        # pipeline with the full OSCAL JSON).
        click.echo(f"VERDICT: {verdict}")
        click.echo(f"  output: {output_path}")
        click.echo(f"  document_uuid: {document_uuid}")
        return

    # Default: stream raw OSCAL JSON to stdout so external tooling
    # can pipe it into `jq` / `compliance-trestle` / etc.
    sys.stdout.write(rendered)
    if not rendered.endswith("\n"):
        sys.stdout.write("\n")
