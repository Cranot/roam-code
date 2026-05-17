"""Detect dark matter: co-changing files with no structural dependency."""

from __future__ import annotations

import hashlib
import json as _json
import sqlite3
from collections import Counter

import click

from roam.capability import roam_capability
from roam.commands.resolve import ensure_index
from roam.db.connection import find_project_root, open_db
from roam.output.formatter import json_envelope, to_json

# W154 (W93 follow-up): dark-matter is the Nth detector migrating onto the
# central findings registry (after ``clones`` in W95, ``dead`` in W99,
# ``complexity`` in W102, ``smells`` in W109, ``bus-factor`` in W115,
# ``pr-risk`` in W134, etc.). The shape mirrors those — a stable detector
# version stamp and a deterministic ``finding_id_str`` so re-runs upsert
# instead of duplicating rows. Bump this when the engine's category
# vocabulary, NPMI / lift formulas, or hypothesis output shape changes.
DARK_MATTER_DETECTOR_VERSION: str = "1.0.0"


# W154 — category-driven confidence tier mapping.
#
# Dark-matter pairs come in two evidence classes:
#
# * Typed hypotheses (the HypothesisEngine resolved a concrete reason via
#   shared-DB / event-bus / shared-config / shared-API / text-similarity
#   pattern matching — graph history + source-pattern evidence both
#   used) → ``structural``. The pair has a *named* cause beyond the
#   raw correlation.
# * UNKNOWN (the engine ran but didn't match any pattern) → ``heuristic``.
#   No resolved cause; pure statistical correlation (NPMI + co-change
#   count) — higher false-positive risk.
#
# A pair whose hypothesis was never computed (e.g. ``--explain`` not
# passed in the legacy text path) falls back to ``heuristic`` via the
# default below — but the ``--persist`` branch always classifies, so the
# typical write produces a clean structural/heuristic split.
_DARK_MATTER_TYPED_CATEGORIES: frozenset[str] = frozenset(
    {
        "SHARED_DB",
        "EVENT_BUS",
        "SHARED_CONFIG",
        "SHARED_API",
        "TEXT_SIMILARITY",
        # Spec-named categories preserved for forward-compat; the engine
        # doesn't currently emit these but a future hypothesizer could.
        "COPY_PASTE",
        "NAMING",
    }
)
_DARK_MATTER_DEFAULT_CONFIDENCE: str = "heuristic"


def _dark_matter_confidence_for_category(category: str | None) -> str:
    """Map a hypothesis category to a confidence tier.

    ``UNKNOWN`` (or missing) → ``heuristic`` (pure statistical
    correlation). Typed categories → ``structural`` (graph + source
    evidence both used to reach the verdict).
    """
    if not category or category == "UNKNOWN":
        return _DARK_MATTER_DEFAULT_CONFIDENCE
    if category in _DARK_MATTER_TYPED_CATEGORIES:
        return "structural"
    # Unknown new category — treat as heuristic until the mapping is
    # explicitly updated. Mirrors the ``_SMELL_DEFAULT_CONFIDENCE``
    # fallback pattern in W109.
    return _DARK_MATTER_DEFAULT_CONFIDENCE


def _canonical_pair(path_a: str, path_b: str) -> tuple[str, str]:
    """Return the (lo, hi) lexicographic ordering of a file-pair.

    Dark-matter couplings are undirected — ``(A, B)`` and ``(B, A)``
    describe the same hidden coupling. We canonicalise to lexicographic
    sort so the finding id is independent of the engine's emission
    order.
    """
    return (path_a, path_b) if path_a <= path_b else (path_b, path_a)


def _dark_matter_finding_id(path_a: str, path_b: str) -> str:
    """Stable, deterministic finding id for one dark-matter pair.

    The pair is sorted lexicographically before hashing so ``(A, B)``
    and ``(B, A)`` produce the same id — same hidden coupling, same
    registry row. Format mirrors W95 clones / W109 smells:
    ``"<detector>:<kind>:<sha1[:12]>"``.
    """
    lo, hi = _canonical_pair(path_a, path_b)
    raw = f"{lo}::{hi}"
    digest = hashlib.sha1(raw.encode("utf-8")).hexdigest()[:12]
    return f"dark-matter:arch.dark_matter:{digest}"


def _emit_dark_matter_findings(
    conn: sqlite3.Connection,
    pairs: list[dict],
    source_version: str,
) -> int:
    """Mirror each dark-matter pair into the central findings registry.

    Returns the count of finding rows written. Caller is responsible
    for opening ``conn`` writable; ``emit_finding`` does not commit
    (the caller commits once at the end of the persist branch).

    Wrapped by the caller in a defensive try/except so a pre-W89 DB
    (without the ``findings`` table) silently no-ops rather than
    crashing the standard dark-matter command path.

    ``subject_kind`` is the NEW ``file_pair`` vocabulary (W154) — the
    first file-pair subject on the registry. ``subject_id`` stays NULL
    because file pairs don't map to ``symbols.id``; consumers join on
    ``(subject_kind = 'file_pair' AND finding_id_str = ?)`` instead.
    Mirrors the W134 pr-risk NULL-subject pattern.
    """
    # Local import keeps the cost out of the read-only path —
    # callers without --persist never reach here.
    from roam.db.findings import FindingRecord, emit_finding

    written = 0
    for p in pairs:
        path_a = p.get("path_a") or ""
        path_b = p.get("path_b") or ""
        if not path_a or not path_b:
            # Skip malformed engine output — every legitimate row has
            # both paths populated.
            continue

        lo, hi = _canonical_pair(path_a, path_b)
        hyp = p.get("hypothesis") or {}
        category = hyp.get("category") or "UNKNOWN"
        detail = hyp.get("detail") or ""

        finding_id = _dark_matter_finding_id(path_a, path_b)
        qualified_name = f"{lo}::{hi}"

        evidence = {
            "qualified_name": qualified_name,
            "path_a": lo,
            "path_b": hi,
            "npmi": p.get("npmi"),
            "lift": p.get("lift"),
            "strength": p.get("strength"),
            "cochange_count": p.get("cochange_count"),
            "hypothesis_category": category,
            "hypothesis_detail": detail,
            "hypothesis_confidence": hyp.get("confidence"),
        }
        claim = (
            f"dark-matter coupling: {lo} <-> {hi} "
            f"(NPMI {p.get('npmi', 0)}, co-changes {p.get('cochange_count', 0)}, "
            f"hypothesis {category})"
        )
        confidence = _dark_matter_confidence_for_category(category)
        emit_finding(
            conn,
            FindingRecord(
                finding_id_str=finding_id,
                subject_kind="file_pair",
                subject_id=None,
                claim=claim,
                evidence_json=_json.dumps(evidence, sort_keys=True),
                confidence=confidence,
                source_detector="dark-matter",
                source_version=source_version,
            ),
        )
        written += 1
    return written


@roam_capability(
    name="dark-matter",
    category="architecture",
    summary="Detect dark matter: file pairs that co-change but have no structural link",
    maturity="stable",
    mcp_expose=True,
    mcp_preset=("core", "architecture"),
    side_effect=False,
    task_required=False,
    destructive=False,
    stale_sensitive=True,
    ai_safe=True,
    requires_index=True,
)
@click.command()
@click.option("-n", "limit", default=30, help="Max pairs to show")
@click.option("--min-npmi", default=0.3, type=float, show_default=True, help="Minimum NPMI threshold")
@click.option("--min-cochanges", default=3, type=int, show_default=True, help="Minimum co-change count")
@click.option("--explain", is_flag=True, help="Add hypothesis for each pair")
@click.option("--category", is_flag=True, help="Group output by hypothesis category")
@click.option(
    "--persist",
    is_flag=True,
    default=False,
    help=(
        "Persist findings to .roam/index.db findings registry "
        "(cross-detector queryable via `roam findings list --detector dark-matter`). "
        "The detector-specific output is unchanged; the registry rows are "
        "the denormalised cross-detector surface. Persisted set always "
        "carries hypothesis categories — the engine is invoked unconditionally "
        "under --persist so the confidence tier is computed for every pair."
    ),
)
@click.pass_context
def dark_matter(ctx, limit, min_npmi, min_cochanges, explain, category, persist):
    """Detect dark matter: file pairs that co-change but have no structural link.

    Unlike ``coupling`` (which measures file-level co-change frequency),
    this command finds file pairs that co-change frequently despite having
    no structural dependency.

    Dark matter couplings indicate hidden dependencies -- shared databases,
    event buses, config keys, or copy-paste patterns. Use --explain to see
    hypothesized reasons for each coupling.
    """
    json_mode = ctx.obj.get("json") if ctx.obj else False
    sarif_mode = ctx.obj.get("sarif") if ctx.obj else False
    token_budget = ctx.obj.get("budget", 0) if ctx.obj else 0
    ensure_index()

    from roam.graph.dark_matter import HypothesisEngine, dark_matter_edges

    with open_db(readonly=not persist) as conn:
        pairs = dark_matter_edges(conn, min_cochanges=min_cochanges, min_npmi=min_npmi)
        pairs = pairs[:limit]

        # Run hypothesis engine when needed.
        # --persist always classifies so the confidence tier is computed
        # for every row written into the registry; otherwise we only pay
        # the classification cost when the human-facing output needs it.
        # ``sarif_mode`` joins the set so the SARIF projection's
        # severity (structural -> warning, heuristic -> note) reflects
        # the engine's typed-vs-untyped split, not the default fallback.
        need_hypotheses = explain or category or json_mode or persist or sarif_mode
        if need_hypotheses and pairs:
            root = find_project_root()
            engine = HypothesisEngine(root)
            engine.classify_all(pairs)

        # --- W154: mirror into the central findings registry ---
        # Runs ONLY with --persist. The persisted set is independent of
        # any display-time filtering (``--explain`` / ``--category``);
        # we emit every detected pair (already capped by ``-n``) so the
        # registry stays comprehensive regardless of how a particular
        # invocation slices the view.
        if persist and pairs:
            try:
                _emit_dark_matter_findings(conn, pairs, DARK_MATTER_DETECTOR_VERSION)
                conn.commit()
            except sqlite3.OperationalError:
                # findings table missing (pre-W89 schema) — degrade gracefully.
                pass

        # --- SARIF output (W1211) ---
        # Branches BEFORE json/text so the pre-existing paths stay
        # byte-identical to pre-W1211. The SARIF projection mirrors the
        # displayed slice — ``pairs`` here has already been capped by
        # ``-n``, so a CI gate sees the same evidence the human / agent
        # sees. Hypotheses are guaranteed-resolved on this path
        # (``sarif_mode`` joins ``need_hypotheses`` above) so the SARIF
        # severity tracks the W154 confidence tier (structural ->
        # warning; heuristic -> note).
        if sarif_mode:
            from roam.output.sarif import dark_matter_to_sarif, write_sarif

            sarif_findings = [
                {
                    "file_a": p["path_a"],
                    "file_b": p["path_b"],
                    "npmi": p.get("npmi"),
                    "lift": p.get("lift"),
                    "strength": p.get("strength"),
                    "cochange_count": p.get("cochange_count"),
                    "hypothesis": p.get("hypothesis"),
                }
                for p in pairs
            ]
            click.echo(write_sarif(dark_matter_to_sarif(sarif_findings)))
            return

        if json_mode:
            by_cat: dict[str, int] = Counter()
            for p in pairs:
                cat = p.get("hypothesis", {}).get("category", "UNKNOWN")
                by_cat[cat] += 1

            total = len(pairs)
            parts = [f"{v} {k}" for k, v in sorted(by_cat.items(), key=lambda x: -x[1])]
            # W805-followup-D: empty-state disclosure (Pattern 2 silent-
            # fallback fix). Zero dark-matter pairs can mean two things:
            # (a) the co-change graph was analyzed cleanly and produced
            # no hidden couplings (real success — no `state` stamp), OR
            # (b) the corpus had no co-change history to analyze
            # (degraded — `state=no_cochange` + partial_success=True).
            # Distinguish by actually querying git_cochange instead of
            # inferring from the empty pairs result; the previous code
            # always claimed (b) on zero pairs which is itself a silent
            # Pattern-2 — a clean populated graph would be labelled
            # "no co-change history" incorrectly.
            cochange_count: int
            if total == 0:
                try:
                    cochange_count = conn.execute("SELECT COUNT(*) FROM git_cochange").fetchone()[0]
                except sqlite3.OperationalError:
                    cochange_count = 0
            else:
                cochange_count = -1  # unused when total > 0
            if total > 0:
                verdict_str = f"{total} dark-matter coupling{'s' if total != 1 else ''} found"
            elif cochange_count == 0:
                verdict_str = (
                    "no co-change history to analyze (corpus has 0 cochange records — "
                    "run `roam index --force` to populate)"
                )
            else:
                verdict_str = "0 dark-matter couplings found"
            _summary: dict = {
                "verdict": verdict_str,
                "total_dark_matter_edges": total,
                "by_category": dict(by_cat),
            }
            if total == 0 and cochange_count == 0:
                _summary["partial_success"] = True
                _summary["state"] = "no_cochange"
            elif total > 0 and parts:
                _summary["verdict"] += f" ({', '.join(parts)})"

            click.echo(
                to_json(
                    json_envelope(
                        "dark-matter",
                        summary=_summary,
                        budget=token_budget,
                        dark_matter_pairs=[
                            {
                                "file_a": p["path_a"],
                                "file_b": p["path_b"],
                                "npmi": p["npmi"],
                                "lift": p["lift"],
                                "strength": p["strength"],
                                "cochange_count": p["cochange_count"],
                                "hypothesis": p.get("hypothesis"),
                            }
                            for p in pairs
                        ],
                    )
                )
            )
            return

        total = len(pairs)

        if not pairs:
            # W805 (Pattern 2 propagation to text branch): the JSON branch
            # above already distinguishes "0 pairs from a populated
            # co-change graph" from "no co-change history to analyze".
            # Mirror that disclosure on the text branch so agents reading
            # the verdict line alone get the same lineage signal.
            try:
                cochange_count = conn.execute("SELECT COUNT(*) FROM git_cochange").fetchone()[0]
            except sqlite3.OperationalError:
                # git_cochange table missing (older schema) — treat as
                # no-cochange state per the loud-fallback rule.
                cochange_count = 0
            if cochange_count == 0:
                click.echo(
                    "VERDICT: no co-change history to analyze "
                    "(corpus has 0 cochange records — "
                    "run `roam index --force` to populate)"
                )
            else:
                click.echo("VERDICT: 0 dark-matter couplings found")
            return

        # Build verdict with category breakdown if hypotheses available
        if need_hypotheses:
            by_cat = Counter()
            for p in pairs:
                cat = p.get("hypothesis", {}).get("category", "UNKNOWN")
                by_cat[cat] += 1
            parts = [f"{v} {k}" for k, v in sorted(by_cat.items(), key=lambda x: -x[1])]
            click.echo(f"VERDICT: {total} dark-matter coupling{'s' if total != 1 else ''} found ({', '.join(parts)})")
        else:
            click.echo(f"VERDICT: {total} dark-matter coupling{'s' if total != 1 else ''} found")

        click.echo()

        if category:
            # Group by hypothesis category
            groups: dict[str, list[dict]] = {}
            for p in pairs:
                cat = p.get("hypothesis", {}).get("category", "UNKNOWN")
                groups.setdefault(cat, []).append(p)
            for cat in sorted(groups.keys()):
                click.echo(f"  [{cat}]")
                for p in groups[cat]:
                    detail = p.get("hypothesis", {}).get("detail", "")
                    click.echo(f"    {p['path_a']} <-> {p['path_b']}")
                    click.echo(
                        f"      NPMI: {p['npmi']:.2f} | Lift: {p['lift']:.1f} | Co-changes: {p['cochange_count']}"
                    )
                    if detail:
                        click.echo(f"      Hypothesis: {cat} ({detail})")
                click.echo()
        else:
            for p in pairs:
                click.echo(f"  {p['path_a']} <-> {p['path_b']}")
                click.echo(f"    NPMI: {p['npmi']:.2f} | Lift: {p['lift']:.1f} | Co-changes: {p['cochange_count']}")
                if explain:
                    hyp = p.get("hypothesis", {})
                    cat = hyp.get("category", "UNKNOWN")
                    detail = hyp.get("detail", "")
                    click.echo(f"    Hypothesis: {cat} ({detail})")
                click.echo()
