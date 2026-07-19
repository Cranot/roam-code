"""``roam laws`` — self-installing constitution.

Four subcommands:

* ``roam laws mine``    -- discover laws from index / tests / git history
* ``roam laws check``   -- enforce laws against a diff
* ``roam laws list``    -- print law id + description for browsing
* ``roam laws explain`` -- show the full evidence for one law

Pairs with R18 (policy DSL) and R24 (Agent Constitution): the
machine-readable ``rule`` dict each law carries is intentionally
shaped for R18 consumption, so an agent can pipe ``roam laws mine
--json`` into the policy engine without an intermediate format.
"""

from __future__ import annotations

import hashlib
import json as _json
import sqlite3
from pathlib import Path

import click

from roam.capability import roam_capability
from roam.commands.resolve import ensure_index
from roam.db.connection import find_project_root, open_db
from roam.exit_codes import EXIT_GATE_FAILURE
from roam.laws.checker import check_laws, get_diff_text, parse_added
from roam.laws.miner import Law, mine_laws
from roam.laws.serializer import (
    dump_laws_yaml,
    find_laws_file,
    load_laws_yaml,
    write_laws_file,
)
from roam.output.confidence import confidence_level_rank
from roam.output.formatter import json_envelope, to_json
from roam.runs.helpers import auto_log

# W119 (W93 follow-up): laws is the fifth detector migrating onto the
# central findings registry (after ``clones`` in W95, ``dead`` in W99,
# ``complexity`` in W102, ``smells`` in W109). The shape mirrors those —
# a stable detector version stamp and a deterministic ``finding_id_str``
# so re-runs upsert instead of duplicating rows. Bump this when the law
# shape, ``rule`` dict, or evidence keys change meaningfully.
LAWS_DETECTOR_VERSION: str = "1.0.0"


# W119 — per-law-kind confidence tier mapping.
#
# Mined laws split into three evidence classes:
#
# * ``naming`` and ``import`` — derived from deterministic counts over
#   the indexed AST / file-edge graph (``compute_conventions`` /
#   ``file_edges``). Same input → same law → ``structural``.
# * ``testing`` — name-based heuristic match between public symbol
#   names and test file basenames (``test_<name>.py`` / ``<name>.test.ts``
#   / …). Pattern is reliable but name-dependent → ``heuristic``.
# * ``errors`` and ``co_change`` — stubs in v1 (return ``[]``); listed
#   here so a follow-up implementation lands on the right tier
#   without re-deriving this table. Both target observable runtime /
#   git-history signal, so they map to ``structural`` once wired up.
_LAW_KIND_TO_CONFIDENCE: dict[str, str] = {
    "naming": "structural",
    "import": "structural",
    "testing": "heuristic",
    # Stubs (return [] today) — pre-mapped for the eventual wiring.
    "errors": "structural",
    "co_change": "structural",
}
_LAW_DEFAULT_CONFIDENCE: str = "structural"


# ---------------------------------------------------------------------------
# W1005-followup-J — Pattern 3a (cross-command metric divergence) sealing.
#
# Pre-W1005-followup-J, ``roam laws mine --min-confidence`` accepted ONLY the
# 3-tier ``{low, medium, high}`` emit vocab. An agent fluent in the W547
# canonical vocabulary (``critical / error / high / warning / medium / low /
# info / note``) — the vocabulary every sibling --confidence / --severity
# site accepts post-W1005-followup-{B, C, D, F, G, H} — who typed
# ``--min-confidence critical`` hit a click usage error 2.
#
# Path A-variant fix (mirroring W1005-followup-H on cmd_api_drift). Widen
# Click.Choice to accept the union of the emit vocab + W547 canonical
# tokens. Project canonical tokens onto the emit vocab (``high`` /
# ``medium`` / ``low``) BEFORE the existing
# ``confidence_level_rank()`` floor comparison. EMIT vocab stays
# ``low``/``medium``/``high`` so the W596 strict-floor clamp + the
# existing comparator are unchanged byte-for-byte.
#
# Projection mirrors :data:`roam.output._severity._DEFAULT_SEVERITY_TO_CONFIDENCE_LEVEL`
# (the W565 closed table; the same table api-drift adopts at W1005-followup-H):
#
# * ``critical`` / ``error`` / ``high`` -> ``high``
# * ``warning`` / ``medium`` -> ``medium``
# * ``info`` / ``low`` / ``note`` -> ``low``
#
# No ``all`` bypass sentinel: ``laws mine`` already treats
# ``--min-confidence`` as optional (default ``None`` -> no filter), so the
# pre-existing "no filter" path is the bypass.
#
# Asymmetry note for the next maintainer: this site uses a confidence-LEVEL
# comparator (``confidence_level_rank``), NOT a severity-rank comparator —
# unlike cmd_n1 / cmd_orphan_routes / cmd_auth_gaps which use
# ``severity_rank`` directly. The reason: ``Law.confidence`` is emitted by
# ``_confidence_from_pct()`` on the confidence-LEVEL axis (high/medium/low
# from a conformance %), so the rank table that matches the EMIT axis is
# ``_CONFIDENCE_LEVEL_RANK``. Sibling reference for the equality-flavoured
# Path A-variant: cmd_api_drift._CANONICAL_TO_CONFIDENCE (W1005-followup-H).
# ---------------------------------------------------------------------------

# Canonical W547 token -> emit-vocab confidence-LEVEL projection. Closed map;
# keys mirror :data:`roam.output._severity.SEVERITY_LEVELS` plus the
# CVSS-style aliases. Values are the 3-tier laws emit vocab
# (low/medium/high) — what ``_confidence_from_pct()`` actually emits and
# what ``confidence_level_rank()`` ranks.
_CANONICAL_TO_CONFIDENCE: dict[str, str] = {
    # W547 canonical 4-tier
    "critical": "high",
    "error": "high",
    "warning": "medium",
    "info": "low",
    # CVSS-style aliases (round-trip with OSV / npm-audit / trivy feeds)
    "high": "high",
    "medium": "medium",
    "low": "low",
    "note": "low",
}


def _project_confidence_input(label: str) -> str:
    """Project a user-supplied confidence/severity token to the emit vocab.

    Case-insensitive. Unknown labels fall through unchanged so the
    Click.Choice (which is the closed-enum gate) stays the
    source-of-truth for what's accepted; this helper is purely the
    projection layer.

    Examples
    --------
    >>> _project_confidence_input("critical")
    'high'
    >>> _project_confidence_input("WARNING")
    'medium'
    >>> _project_confidence_input("low")
    'low'
    """
    return _CANONICAL_TO_CONFIDENCE.get(label.lower(), label.lower())


def _law_finding_id(law: Law) -> str:
    """Stable, deterministic finding id for a mined law.

    The (kind, id) tuple re-identifies the same law across runs: the
    miner emits the same ``Law.id`` slug for the same input (see
    ``_safe_id`` in :mod:`roam.laws.miner`). We fold the kind into the
    digest so a future kind reusing the same id slug (unlikely but
    possible) doesn't collide.
    """
    raw = f"{law.kind}:{law.id}"
    digest = hashlib.sha1(raw.encode("utf-8")).hexdigest()[:12]
    return f"laws:{law.kind}:{digest}"


def _emit_laws_findings(
    conn: sqlite3.Connection,
    laws: list[Law],
    source_version: str,
) -> int:
    """Mirror each mined law into the central findings registry.

    Returns the count of finding rows written. Caller is responsible
    for opening ``conn`` writable; emit_finding does not commit
    (the caller commits once at the end of the persist branch).

    Wrapped by the caller in a defensive try/except so a pre-W89 DB
    (without the ``findings`` table) silently no-ops rather than
    crashing the standard ``laws mine`` command path.
    """
    # Local import keeps the cost out of the read-only path —
    # callers without --persist never reach here.
    from roam.db.findings import FindingRecord, emit_finding

    written = 0
    for law in laws:
        finding_id = _law_finding_id(law)
        evidence = {
            "law_id": law.id,
            "kind": law.kind,
            "description": law.description,
            "severity": law.severity,
            "confidence_label": law.confidence,
            "rule": law.rule,
            "evidence": law.evidence,
        }
        sample = int((law.evidence or {}).get("sample_size", 0))
        pct = (law.evidence or {}).get("conformance_pct", 0)
        claim = (
            f"Law {law.id} ({law.kind}): {law.description} "
            f"[confidence={law.confidence}, n={sample}, conformance={pct}%]"
        )
        confidence = _LAW_KIND_TO_CONFIDENCE.get(law.kind, _LAW_DEFAULT_CONFIDENCE)
        emit_finding(
            conn,
            FindingRecord(
                finding_id_str=finding_id,
                # Laws are repo-level invariants, not symbol-level. Use
                # ``file`` as the closest subject_kind in the existing
                # vocabulary (matches how dead/smells fall back when no
                # symbols.id resolves). subject_id stays NULL — the
                # registry permits it by design.
                subject_kind="file",
                subject_id=None,
                claim=claim,
                evidence_json=_json.dumps(evidence, sort_keys=True),
                confidence=confidence,
                source_detector="laws",
                source_version=source_version,
            ),
        )
        written += 1
    return written


# ---------------------------------------------------------------------------
# Click group
# ---------------------------------------------------------------------------


@roam_capability(
    name="laws",
    category="workflow",
    summary=(
        "Self-installing constitution: mine a repo's unwritten rules"
        " from code + tests + git history, then enforce them."
    ),
    inputs=[],
    outputs=["laws", "violations"],
    examples=[
        "roam laws mine --top 10",
        "roam laws mine --out roam-laws.yml",
        "roam laws check --laws-file roam-laws.yml",
        "roam laws list",
        "roam laws explain snake_case_functions",
    ],
    tags=["laws", "agent-os", "constitution", "policy"],
    ai_safe=True,
    requires_index=True,
    maturity="beta",
    mcp_expose=True,
    mcp_preset=("core",),
    side_effect=True,
    task_required=False,
    destructive=False,
    stale_sensitive=True,
)
@click.group("laws")
@click.pass_context
def laws_group(ctx):
    """Self-installing constitution.

    ``roam laws mine`` walks your index + git history and emits a list
    of inferred rules. ``roam laws check`` enforces those rules against
    the current diff (or any saved diff). Output is a
    ``roam-laws.yml`` checked into the repo, so future PRs are gated
    against the same rules.
    """
    ctx.ensure_object(dict)


# ---------------------------------------------------------------------------
# laws mine
# ---------------------------------------------------------------------------


@laws_group.command("mine")
@click.option(
    "--top",
    type=int,
    default=None,
    help="Keep only the top <N> highest-confidence laws.",
)
@click.option(
    "--min-confidence",
    # W1005-followup-J: widened from 3-tier {low, medium, high} to the
    # union of emit vocab + W547 canonical 4-tier + CVSS aliases so
    # canonical-aware agents can pass any of {critical, error, high,
    # warning, medium, low, info, note} without hitting click usage
    # error 2. Canonical tokens project onto the emit vocab via
    # :data:`_CANONICAL_TO_CONFIDENCE` BEFORE the existing
    # ``confidence_level_rank()`` floor — EMIT vocab unchanged, the
    # W596 strict-floor clamp at line 255 is preserved.
    type=click.Choice(
        [
            "low",
            "medium",
            "high",  # emit vocab (back-compat)
            "critical",
            "error",
            "warning",
            "info",
            "note",  # W547 canonical aliases
        ],
        case_sensitive=False,
    ),
    default=None,
    help=(
        "Drop laws below this confidence level. Accepts the laws emit "
        "vocab {low, medium, high} OR W547 canonical tokens {critical, "
        "error, warning, info, note} — canonical tokens project onto the "
        "emit vocab (critical/error/high -> high; warning/medium -> medium; "
        "info/low/note -> low) before the floor comparison."
    ),
)
@click.option(
    "--out",
    "out_path",
    default=None,
    help="Write YAML to this path (default: stdout).",
)
@click.option(
    "--persist",
    "persist",
    is_flag=True,
    default=False,
    help=(
        "Mirror each mined law into the central findings registry "
        "(``roam findings list --detector laws``). Detector-specific "
        "output (text / JSON / YAML) is unchanged; the registry rows "
        "are the denormalised cross-detector surface."
    ),
)
@click.pass_context
def laws_mine(ctx, top, min_confidence, out_path, persist):
    """Discover laws from the indexed codebase + tests + git history.

    Examples:

    \b
      roam laws mine --top 10
      roam laws mine --out roam-laws.yml
      roam laws mine --json | jq .laws
    """
    json_mode = ctx.obj.get("json") if ctx.obj else False
    token_budget = ctx.obj.get("budget", 0) if ctx.obj else 0

    ensure_index()
    with open_db(readonly=not persist) as conn:
        laws = mine_laws(conn, top=top)

        if min_confidence:
            # W596: canonical confidence-LEVEL rank — higher = more confident.
            # Pre-W596 min_confidence fallback was 1 (treat unknown filter
            # as "low"); canonical returns -1 for an unknown filter label
            # which would keep everything — clamp to 1 to preserve the
            # pre-W596 strict-floor semantic.
            #
            # W1005-followup-J: project the user-supplied token onto the
            # emit-vocab BEFORE ranking. Pre-fix the Choice accepted only
            # {low, medium, high}; post-fix it also accepts the W547
            # canonical {critical, error, warning, info, note} which
            # ``confidence_level_rank()`` doesn't recognise (its closed vocab
            # is the LEVEL axis only). The projection lifts a severity-axis
            # token onto the LEVEL axis so the existing rank+clamp
            # machinery stays unchanged.
            projected = _project_confidence_input(min_confidence)
            min_rank = max(confidence_level_rank(projected), 1)
            laws = [law for law in laws if confidence_level_rank(law.confidence, fallback=-1) >= min_rank]

        # --- W119: mirror into the central findings registry ---
        # Runs ONLY with --persist. The persisted set respects the same
        # --top / --min-confidence filters that shape the YAML output so
        # the registry stays in lockstep with the user-facing view (a
        # ``roam laws mine --top 5 --persist`` campaign writes the same
        # five laws it prints).
        if persist:
            try:
                _emit_laws_findings(conn, laws, LAWS_DETECTOR_VERSION)
                conn.commit()
            except sqlite3.OperationalError as _exc:
                # Expected: findings table missing (pre-W89 schema) —
                # degrade gracefully. Surface lineage so a non-expected
                # variant (locked / corrupt DB) is still discoverable.
                from roam.observability import log_swallowed

                log_swallowed("cmd_laws:emit_findings", _exc)

        # W805 (Pattern 2): pre-fetch symbol count INSIDE the open_db
        # block so the empty-state verdict below has a real input shape
        # to disclose. Cheap (one COUNT(*) over the symbols table) and
        # only used when laws is empty, but always queried so the
        # control flow stays linear.
        _w805_symbol_count_laws = conn.execute("SELECT COUNT(*) FROM symbols").fetchone()[0]

    yaml_text = dump_laws_yaml(laws)

    if out_path:
        try:
            target = Path(out_path)
            write_laws_file(target, laws)
            out_msg = f"wrote {target}"
        except Exception as exc:
            out_msg = f"error writing {out_path}: {exc}"
    else:
        out_msg = None

    high = sum(1 for law in laws if law.confidence == "high")
    medium = sum(1 for law in laws if law.confidence == "medium")
    low = sum(1 for law in laws if law.confidence == "low")

    # W805 (Pattern 2: silent fallbacks) — distinguish "mined zero laws
    # from a populated graph + git history" from "mined zero laws because
    # the corpus had no symbols / no git history to analyze". The
    # previous verdict "Mined 0 laws (0 high-confidence)" + partial_success=False
    # was a silent SAFE on a degraded run.
    summary: dict
    if not laws:
        symbol_count = _w805_symbol_count_laws
        if symbol_count == 0:
            verdict = (
                "no symbols to analyze (corpus empty; run `roam index --force` to populate the graph before law mining)"
            )
            summary = {
                "verdict": verdict,
                "law_count": 0,
                "high_confidence": 0,
                "medium_confidence": 0,
                "low_confidence": 0,
                "partial_success": True,
                "state": "empty_corpus",
            }
        else:
            verdict = (
                f"no laws met the conformance / sample thresholds "
                f"(min_confidence={min_confidence or 'low'}, top={top}; "
                f"detector ran across {symbol_count} symbols but produced 0 candidates)"
            )
            summary = {
                "verdict": verdict,
                "law_count": 0,
                "high_confidence": 0,
                "medium_confidence": 0,
                "low_confidence": 0,
                "partial_success": True,
                "state": "no_laws_passed_thresholds",
            }
    else:
        verdict = f"Mined {len(laws)} laws ({high} high-confidence)"
        summary = {
            "verdict": verdict,
            "law_count": len(laws),
            "high_confidence": high,
            "medium_confidence": medium,
            "low_confidence": low,
            "partial_success": False,
        }
    if out_msg:
        summary["written_to"] = str(out_path)

    envelope = json_envelope(
        "laws-mine",
        budget=token_budget,
        summary=summary,
        laws=[law.to_dict() for law in laws],
        agent_contract={
            "facts": [verdict]
            + [
                f"{law.id}: {law.description} (confidence={law.confidence}, n={law.evidence.get('sample_size', 0)})"
                for law in laws[:5]
            ],
            "next_commands": [
                f"roam laws check --laws-file {out_path or 'roam-laws.yml'}",
                "roam laws list",
            ],
        },
    )

    auto_log(envelope, action="laws-mine", target=str(out_path or ""))

    if json_mode:
        click.echo(to_json(envelope))
        return

    if out_path:
        click.echo(f"VERDICT: {verdict} -> {out_path}")
    else:
        click.echo(f"VERDICT: {verdict}")
    click.echo("")
    if not laws:
        click.echo("(no laws met the conformance / sample thresholds)")
        return

    if out_path:
        # Still summarise on stdout when --out is used; the YAML is on disk.
        for law in laws:
            click.echo(f"  {law.id}  [{law.kind}/{law.confidence}]  {law.description}")
    else:
        click.echo(yaml_text)


# ---------------------------------------------------------------------------
# laws check
# ---------------------------------------------------------------------------


@laws_group.command("check")
@click.option(
    "--laws-file",
    default=None,
    help="Path to a roam-laws.yml. Default: ./roam-laws.yml or ./.roam/laws.yml.",
)
@click.option(
    "--diff-source",
    type=click.Choice(["working", "staged", "head", "pr"]),
    default="working",
    help="Which diff to gate.",
)
@click.option(
    "--diff-file",
    default=None,
    help="Read a saved diff from this path (overrides --diff-source).",
)
@click.option("--base-ref", default="main", help="Base ref for --diff-source pr.")
@click.option(
    "--strict",
    is_flag=True,
    default=False,
    help="Exit 5 if any blockers are found (CI gate behaviour).",
)
@click.pass_context
def laws_check(ctx, laws_file, diff_source, diff_file, base_ref, strict):
    """Run mined laws against a diff and report violations."""
    json_mode = ctx.obj.get("json") if ctx.obj else False
    sarif_mode = ctx.obj.get("sarif") if ctx.obj else False
    token_budget = ctx.obj.get("budget", 0) if ctx.obj else 0

    root = find_project_root()

    # Load laws.
    laws_path = find_laws_file(root, laws_file)
    if not laws_path:
        verdict = "no roam-laws.yml found — run `roam laws mine --out roam-laws.yml` to create one"
        envelope = json_envelope(
            "laws-check",
            budget=token_budget,
            summary={
                "verdict": verdict,
                "violations": 0,
                "partial_success": True,
                "state": "not_initialized",
            },
            violations=[],
            agent_contract={
                "facts": [verdict],
                "next_commands": ["roam laws mine --out roam-laws.yml"],
            },
        )
        if sarif_mode:
            # W1216: emit an empty-but-valid SARIF doc (closed-enum
            # rule catalogue is always present) so a CI consumer
            # reading SARIF on an unconfigured repo gets a well-formed
            # zero-results document rather than a no-such-tool error.
            from roam.output.sarif import laws_to_sarif, write_sarif

            click.echo(write_sarif(laws_to_sarif([])))
            return
        if json_mode:
            click.echo(to_json(envelope))
        else:
            click.echo(f"VERDICT: {verdict}")
        return

    laws = load_laws_yaml(laws_path.read_text(encoding="utf-8"))
    if not laws:
        verdict = f"{laws_path} contains no laws"
        # Mirror the not_initialized branch above: an empty laws file is
        # an actionable state, so emit an agent_contract that names the
        # recovery command. Without this, MCP/agent consumers reaching
        # the JSON envelope had a verdict but no next_commands, which
        # forced them to guess (Pattern 2 silent-fallback adjacency).
        envelope = json_envelope(
            "laws-check",
            budget=token_budget,
            summary={
                "verdict": verdict,
                "violations": 0,
                "partial_success": True,
                "state": "empty",
            },
            violations=[],
            agent_contract={
                "facts": [verdict],
                "next_commands": ["roam laws mine --out roam-laws.yml"],
            },
        )
        if sarif_mode:
            from roam.output.sarif import laws_to_sarif, write_sarif

            click.echo(write_sarif(laws_to_sarif([])))
            return
        if json_mode:
            click.echo(to_json(envelope))
        else:
            click.echo(f"VERDICT: {verdict}")
        return

    # Resolve diff source.
    actual_source = "file" if diff_file else diff_source
    diff_text = get_diff_text(
        repo_root=root,
        diff_source=actual_source,
        diff_file=diff_file,
        base_ref=base_ref,
    )

    if not diff_text.strip():
        verdict = "no diff content — nothing to check"
        envelope = json_envelope(
            "laws-check",
            budget=token_budget,
            summary={
                "verdict": verdict,
                "violations": 0,
                "law_count": len(laws),
                "diff_source": actual_source,
                "partial_success": False,
            },
            violations=[],
        )
        auto_log(envelope, action="laws-check", target=str(laws_path))
        if sarif_mode:
            from roam.output.sarif import laws_to_sarif, write_sarif

            click.echo(write_sarif(laws_to_sarif([])))
            return
        if json_mode:
            click.echo(to_json(envelope))
        else:
            click.echo(f"VERDICT: {verdict}")
        return

    parsed = parse_added(diff_text)
    violations = check_laws(laws, parsed=parsed, repo_root=root)

    blockers = sum(1 for v in violations if v.severity == "blocker")
    warnings = sum(1 for v in violations if v.severity == "warning")
    advisories = sum(1 for v in violations if v.severity == "advisory")
    verdict = f"{len(violations)} violations ({blockers} blockers, {warnings} warnings, {advisories} advisories)"

    envelope = json_envelope(
        "laws-check",
        budget=token_budget,
        summary={
            "verdict": verdict,
            "violations": len(violations),
            "blockers": blockers,
            "warnings": warnings,
            "advisories": advisories,
            "law_count": len(laws),
            "diff_source": actual_source,
            "partial_success": False,
        },
        violations=[v.to_dict() for v in violations],
        laws_file=str(laws_path),
        agent_contract={
            "facts": [verdict] + [f"{v.law_id}: {v.message} ({v.file}:{v.line})" for v in violations[:5]],
            "next_commands": [
                "roam laws list",
                f"roam laws explain {violations[0].law_id}" if violations else "roam laws mine",
            ],
        },
    )

    auto_log(envelope, action="laws-check", target=str(laws_path))

    if sarif_mode:
        # W1216: emit per-violation SARIF results anchored on file:line.
        # The strict-mode CI gate still fires on blockers below — SARIF
        # output is a projection, not a replacement, for the exit code
        # contract.
        from roam.output.sarif import laws_to_sarif, write_sarif

        click.echo(write_sarif(laws_to_sarif([v.to_dict() for v in violations])))
    elif json_mode:
        click.echo(to_json(envelope))
    else:
        click.echo(f"VERDICT: {verdict}")
        click.echo("")
        for v in violations[:50]:
            loc_str = f"{v.file}:{v.line}" if v.line else v.file
            click.echo(f"  [{v.severity}] {v.law_id} -- {v.message} ({loc_str})")
        if len(violations) > 50:
            click.echo(f"  (+ {len(violations) - 50} more)")

    if strict and blockers > 0:
        ctx.exit(EXIT_GATE_FAILURE)


# ---------------------------------------------------------------------------
# laws list
# ---------------------------------------------------------------------------


@laws_group.command("list")
@click.option("--laws-file", default=None, help="Path to a roam-laws.yml.")
@click.pass_context
def laws_list(ctx, laws_file):
    """Dump law id + description for browsing."""
    json_mode = ctx.obj.get("json") if ctx.obj else False
    token_budget = ctx.obj.get("budget", 0) if ctx.obj else 0

    root = find_project_root()
    laws_path = find_laws_file(root, laws_file)
    if not laws_path:
        verdict = "no roam-laws.yml found"
        # Pattern 1: text-mode emits a recovery hint; JSON-mode used to
        # drop it. Stamp the same hint into agent_contract so MCP/agent
        # consumers see the recovery command without re-parsing the
        # verdict string.
        envelope = json_envelope(
            "laws-list",
            budget=token_budget,
            summary={
                "verdict": verdict,
                "law_count": 0,
                "partial_success": True,
                "state": "not_initialized",
            },
            laws=[],
            agent_contract={
                "facts": [verdict],
                "next_commands": ["roam laws mine --out roam-laws.yml"],
            },
        )
        if json_mode:
            click.echo(to_json(envelope))
        else:
            click.echo(f"VERDICT: {verdict}")
            click.echo("Hint: run `roam laws mine --out roam-laws.yml`.")
        return

    laws = load_laws_yaml(laws_path.read_text(encoding="utf-8"))
    verdict = f"{len(laws)} laws in {laws_path.name}"
    envelope = json_envelope(
        "laws-list",
        budget=token_budget,
        summary={
            "verdict": verdict,
            "law_count": len(laws),
            "partial_success": False,
        },
        laws=[law.to_dict() for law in laws],
        laws_file=str(laws_path),
    )

    if json_mode:
        click.echo(to_json(envelope))
        return

    click.echo(f"VERDICT: {verdict}")
    click.echo("")
    if not laws:
        click.echo("(empty)")
        return
    for law in laws:
        click.echo(f"  {law.id}  [{law.kind}/{law.confidence}]  {law.description}")


# ---------------------------------------------------------------------------
# laws explain
# ---------------------------------------------------------------------------


def _emit_consistent_laws_explain_contract(
    json_mode,
    token_budget,
    summary,
    **payload,
):
    """Emit one laws-explain envelope shape across all result states."""
    envelope = json_envelope(
        "laws-explain",
        budget=token_budget,
        summary=summary,
        **payload,
    )
    if json_mode:
        click.echo(to_json(envelope))
        return True

    click.echo(f"VERDICT: {summary['verdict']}")
    return False


def _emit_laws_explain_resolution_failure(
    json_mode,
    token_budget,
    law_id,
    *,
    verdict,
    state,
    available_ids=None,
    next_commands=None,
):
    """Keep unresolved law requests structured without repeating branches."""
    payload = {"law": None}
    if available_ids is not None:
        payload["available_ids"] = available_ids
    if next_commands:
        payload["agent_contract"] = {
            "facts": [verdict],
            "next_commands": next_commands,
        }

    _emit_consistent_laws_explain_contract(
        json_mode,
        token_budget,
        {
            "verdict": verdict,
            "law_id": law_id,
            "partial_success": True,
            "state": state,
        },
        **payload,
    )

    if available_ids is not None and not json_mode:
        click.echo("Available ids:")
        for available_id in available_ids:
            click.echo(f"  {available_id}")


@laws_group.command("explain")
@click.argument("law_id")
@click.option("--laws-file", default=None, help="Path to a roam-laws.yml.")
@click.pass_context
def laws_explain(ctx, law_id, laws_file):
    """Show the full evidence dict for one law."""
    json_mode = ctx.obj.get("json") if ctx.obj else False
    token_budget = ctx.obj.get("budget", 0) if ctx.obj else 0

    root = find_project_root()
    laws_path = find_laws_file(root, laws_file)
    if not laws_path:
        verdict = "no roam-laws.yml found"
        # Pattern 1: parallel with laws-list / laws-check — JSON-mode
        # consumers reaching the not_initialized branch get the same
        # recovery hint that text-mode users see.
        _emit_laws_explain_resolution_failure(
            json_mode,
            token_budget,
            law_id,
            verdict=verdict,
            state="not_initialized",
            next_commands=["roam laws mine --out roam-laws.yml"],
        )
        return

    laws = load_laws_yaml(laws_path.read_text(encoding="utf-8"))
    match = next((law for law in laws if law.id == law_id), None)
    if match is None:
        verdict = f"no law with id '{law_id}'"
        _emit_laws_explain_resolution_failure(
            json_mode,
            token_budget,
            law_id,
            verdict=verdict,
            state="not_found",
            available_ids=[law.id for law in laws],
        )
        return

    verdict = f"{match.id} -- {match.description}"
    wrote_json = _emit_consistent_laws_explain_contract(
        json_mode,
        token_budget,
        {
            "verdict": verdict,
            "law_id": match.id,
            "kind": match.kind,
            "confidence": match.confidence,
            "severity": match.severity,
            "partial_success": False,
        },
        law=match.to_dict(),
    )
    if wrote_json:
        return

    click.echo("")
    click.echo(f"  id:          {match.id}")
    click.echo(f"  kind:        {match.kind}")
    click.echo(f"  severity:    {match.severity}")
    click.echo(f"  confidence:  {match.confidence}")
    click.echo("")
    click.echo("  Evidence:")
    for k, v in match.evidence.items():
        click.echo(f"    {k}: {v}")
    click.echo("")
    click.echo("  Rule (machine-readable):")
    for k, v in match.rule.items():
        click.echo(f"    {k}: {v}")
