"""``roam pr-replay`` — generate a buyer-facing PR Replay report.

PR Replay is the productised version of "would Roam have caught my last 30
incidents?" It runs ``roam postmortem`` over a commit range, aggregates
the findings by detector class, and emits a narrative report ready to
hand to a buyer.

Three tiers, all share the same engine:

* ``--tier sample`` — DIY 5-PR sample. Free, self-serve, no founder
  involvement. Watermarked so it's clear what the buyer is looking at.
* ``--tier team`` — Team report. 30 commits.
* ``--tier deep`` — Deep report. 90 commits.

Pricing for the paid tiers lives at https://roam-code.com/#audit. The
command does the *analysis*; the *purchase* and *founder review window*
happen out-of-band (Stripe + a 30 / 90-minute call).

Usage:

    # Free DIY sample (5 most-recent commits on current branch)
    roam pr-replay --tier sample

    # Paid Team report on a buyer's repo
    roam pr-replay --tier team --client "Acme Inc" --output acme.md

    # Paid Deep report on a 90-day historical window
    roam pr-replay --tier deep --range "v1.0..main" --output report.md

    # Full dry-run packet before selling the first engagement
    roam pr-replay --tier team --client "Demo Buyer" --rehearsal

Output formats: text (default), ``--json``, plus a buyer-facing Markdown
narrative report written via ``--output``. SARIF is deliberately NOT
emitted because pr-replay outputs are invocation-scoped buyer-facing
report envelopes (composed from ``roam postmortem`` aggregations) — not
per-location violations. The composed subcommands emit their own
--sarif when applicable; cmd_pr_replay rolls them up into a narrative
report. See action.yml _SUPPORTED_SARIF allowlist + W1175-RESEARCH
Bucket B propagation plan + W1148 audit memo.
"""

from __future__ import annotations

import hashlib as _hashlib
import json as _json
import subprocess as _subprocess
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import TypeVar

import click
from click.testing import CliRunner

from roam.capability import roam_capability
from roam.commands.resolve import ensure_index
from roam.exit_codes import EXIT_SUCCESS
from roam.output.formatter import json_envelope, to_json
from roam.output.risk import normalize_risk_level, risk_rank
from roam.runs.helpers import auto_log

_T = TypeVar("_T")

# ---------------------------------------------------------------------------
# Tier definitions — single source of truth for what each tier means.
# ---------------------------------------------------------------------------

_TIERS: dict[str, dict] = {
    "sample": {
        "default_count": 5,
        "label": "DIY 5-PR sample",
        "purpose_line": (
            "Five-PR self-serve sample. Designed so a prospective buyer "
            "can run it locally and see the kind of report a paid PR "
            "Replay engagement produces, just on a tighter window."
        ),
        "watermark": True,
        "max_per_pr_findings_listed": 3,
    },
    "team": {
        "default_count": 30,
        "label": "Team — 30 PRs",
        "purpose_line": (
            "Thirty most-recent merged PRs on the target branch, scored "
            "against the current Roam detector set. Includes founder "
            "review of the top findings on a 30-minute call."
        ),
        "watermark": False,
        "max_per_pr_findings_listed": 5,
    },
    "deep": {
        "default_count": 90,
        "label": "Deep — 90 PRs",
        "purpose_line": (
            "Ninety merged PRs covering the full quarter, scored against "
            "the current Roam detector set, with a per-detector breakdown "
            "and a 90-minute founder walk-through of recommended CI gates."
        ),
        "watermark": False,
        "max_per_pr_findings_listed": 10,
    },
}


def _slug_for_path(value: str | None, fallback: str) -> str:
    """Return a filesystem-safe ASCII slug for generated engagement paths."""
    raw = (value or fallback or "").strip().lower()
    chars: list[str] = []
    previous_dash = False
    for ch in raw:
        if ch.isascii() and ch.isalnum():
            chars.append(ch)
            previous_dash = False
        elif not previous_dash:
            chars.append("-")
            previous_dash = True
    slug = "".join(chars).strip("-")
    return slug or fallback


def _default_rehearsal_paths(*, tier: str, client: str | None) -> dict[str, Path]:
    """Build the default private paths for a PR Replay delivery rehearsal."""
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    client_slug = _slug_for_path(client, f"{tier}-demo")
    root = Path("internal") / "engagements" / "rehearsals" / f"{stamp}-{client_slug}-{tier}"
    return {
        "root": root,
        "report": root / "report.md",
        "evidence_bundle": root / "evidence-bundle",
    }


# ---------------------------------------------------------------------------
# Postmortem invocation (delegates the heavy lifting).
# ---------------------------------------------------------------------------


def _run_postmortem(commit_range: str, *, limit: int) -> dict:
    """Invoke ``roam --json postmortem <range> --limit N`` in-process.

    Returns the parsed JSON envelope. On any error, returns an envelope
    with empty ``commits`` so the renderer can still emit a sensible
    "no findings" report rather than crashing on the buyer. Defends in
    depth against argv injection by passing ``--`` between the option
    list and the positional ``commit_range`` so a value beginning with
    ``-`` cannot be re-interpreted as a Click flag of ``postmortem``.
    The top-level ``pr-replay`` command pre-validates ``--range`` with
    :func:`_is_safe_commit_range`; this is the second layer.
    """
    from roam.cli import cli

    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["--json", "postmortem", "--limit", str(limit), "--", commit_range],
        catch_exceptions=False,
    )
    if not result.output:
        return {
            "summary": {"verdict": "postmortem returned no output", "commits_scanned": 0},
            "commits": [],
        }
    # ``click.progressbar`` writes to stdout, so the captured output may have
    # progress chrome ("Replaying detectors\n") prefixed before the JSON.
    # Find the first ``{`` and try to parse from there.
    text = result.output
    brace = text.find("{")
    if brace > 0:
        text = text[brace:]
    try:
        return _json.loads(text)
    except _json.JSONDecodeError:
        return {
            "summary": {"verdict": "postmortem output was not valid JSON", "commits_scanned": 0},
            "commits": [],
            "_parse_error": True,
        }


# ---------------------------------------------------------------------------
# Review-suggestion heuristics (BUILD-PRIORITIES P1.2).
#
# Every paid PR Replay should double as discovery for a Roam Review
# subscription. The block below converts the Replay's empirical findings
# into a four-part configuration suggestion:
#
#   1. recurring_risk_classes        — which detector classes keep firing
#   2. suggested_roam_rules_yml      — a starter ``.roam/rules.yml`` preview
#   3. suggested_ci_gates            — concrete CI invocations to wire
#   4. what_review_would_have_blocked — per-PR rationale for what stops
#
# Heuristics only — no LLM, no mock data. If a detector isn't in the rule
# template map we leave it out rather than fabricate a rule.
# ---------------------------------------------------------------------------

# Detector class → starter ``.roam/rules.yml`` snippet. Each entry pairs a
# concrete-noun rationale with a copy-pasteable rule body. The template
# bodies mirror the four pattern types documented in
# ``templates/examples/.roam-rules.yml``: import_from, function_call,
# class_inherit, decorator_use. We use BLOCK for high-severity-leaning
# detectors and WARN for the lighter-touch classes.
_DETECTOR_RULE_TEMPLATES: dict[str, dict] = {
    "clones-not-edited": {
        "rationale": "Clones-not-edited keeps firing — paired blocks edited inconsistently.",
        "rule_yaml": (
            "  - id: gate-clones-not-edited\n"
            "    description: Block PRs that edit one clone of a pair without touching its sibling\n"
            "    pattern: clone_pair_partial_edit\n"
            "    severity: BLOCK\n"
        ),
        "ci_gate": "roam clones --persist && roam critique --check clones-not-edited",
        "ci_rationale": "Persisted clone pairs make critique flag partial edits on every PR.",
    },
    "impact": {
        "rationale": "High blast-radius changes shipped without a preflight gate.",
        "rule_yaml": (
            "  - id: gate-high-blast-radius\n"
            "    description: Require preflight verdict before merging changes to high-blast symbols\n"
            "    pattern: preflight_required\n"
            "    severity: BLOCK\n"
        ),
        "ci_gate": "roam preflight --ci --gate-on high",
        "ci_rationale": "preflight exits 5 on high-blast changes — wires directly into CI.",
    },
    "intent": {
        "rationale": "Intent-drift findings — diff and commit message disagree.",
        "rule_yaml": (
            "  - id: gate-intent-drift\n"
            "    description: Flag PRs where the diff scope exceeds the commit-message intent\n"
            "    pattern: intent_alignment\n"
            "    severity: WARN\n"
        ),
        "ci_gate": "roam critique --check intent",
        "ci_rationale": "Critique's intent check surfaces scope creep without false positives.",
    },
    "cycles": {
        "rationale": "Dependency cycles introduced on recent PRs.",
        "rule_yaml": (
            "  - id: gate-no-new-cycles\n"
            "    description: Block PRs that introduce a new strongly-connected component\n"
            "    pattern: cycle_delta\n"
            "    severity: BLOCK\n"
        ),
        "ci_gate": "roam diff --gate-on cycles",
        "ci_rationale": "Diff-level cycle delta catches new SCCs before they land.",
    },
    "fan-out": {
        "rationale": "Fan-out spikes — single symbols pulling in many dependencies.",
        "rule_yaml": (
            "  - id: gate-fan-out-threshold\n"
            "    description: Warn when added symbols exceed the fan-out threshold\n"
            "    pattern: fan_out_limit\n"
            "    severity: WARN\n"
        ),
        "ci_gate": "roam fan --gate-on fan-out",
        "ci_rationale": "fan command gates outbound-edge spikes at PR time.",
    },
    "fan-in": {
        "rationale": "Fan-in spikes — heavily-depended-on symbols changed without review.",
        "rule_yaml": (
            "  - id: gate-fan-in-threshold\n"
            "    description: Block edits to symbols above the fan-in threshold without preflight\n"
            "    pattern: fan_in_limit\n"
            "    severity: BLOCK\n"
        ),
        "ci_gate": "roam fan --gate-on fan-in",
        "ci_rationale": "fan command flags central-symbol edits before they ship.",
    },
    "file-complexity": {
        "rationale": "Cognitive complexity over threshold on changed files.",
        "rule_yaml": (
            "  - id: gate-file-complexity\n"
            "    description: Warn when changed files exceed the cognitive-complexity threshold\n"
            "    pattern: complexity_limit\n"
            "    severity: WARN\n"
        ),
        "ci_gate": "roam complexity --ci --gate-on high",
        "ci_rationale": "complexity command exits 5 on threshold breaches.",
    },
    "file-length": {
        "rationale": "Oversize files keep landing in PRs.",
        "rule_yaml": (
            "  - id: gate-file-length\n"
            "    description: Warn when changed files exceed the size budget\n"
            "    pattern: file_length_limit\n"
            "    severity: WARN\n"
        ),
        "ci_gate": "roam guard --gate-on file-length",
        "ci_rationale": "guard command checks size deltas at PR time.",
    },
    "test-file-exists": {
        "rationale": "Source files added without corresponding tests.",
        "rule_yaml": (
            "  - id: gate-test-coverage-presence\n"
            "    description: Block source additions that ship without a sibling test file\n"
            "    pattern: test_file_required\n"
            "    severity: BLOCK\n"
        ),
        "ci_gate": "roam test-pyramid --gate-on missing-tests",
        "ci_rationale": "test-pyramid surfaces source-without-test deltas on the PR diff.",
    },
    "god-class": {
        "rationale": "God-class growth — classes accreting responsibilities.",
        "rule_yaml": (
            "  - id: gate-god-class\n"
            "    description: Warn on edits that grow a class past the god-class threshold\n"
            "    pattern: god_class_limit\n"
            "    severity: WARN\n"
        ),
        "ci_gate": "roam smells --gate-on god-class",
        "ci_rationale": "smells command flags god-class growth as it happens.",
    },
    "deep-inheritance": {
        "rationale": "Deepening inheritance chains in recent PRs.",
        "rule_yaml": (
            "  - id: gate-deep-inheritance\n"
            "    description: Warn when class inheritance depth exceeds threshold\n"
            "    pattern: inheritance_depth_limit\n"
            "    severity: WARN\n"
        ),
        "ci_gate": "roam smells --gate-on deep-inheritance",
        "ci_rationale": "smells command surfaces inheritance-depth spikes pre-merge.",
    },
    "layer-violation": {
        "rationale": "Architectural-layer violations — imports crossing the wrong boundary.",
        "rule_yaml": (
            "  - id: gate-no-layer-violations\n"
            "    description: Block imports that cross the declared architectural layers\n"
            "    pattern: import_from\n"
            "    severity: BLOCK\n"
        ),
        "ci_gate": "roam pr-analyze --rules .roam/rules.yml",
        "ci_rationale": "pr-analyze enforces import_from / layer rules on every PR diff.",
    },
    "orphan-symbols": {
        "rationale": "Orphan symbols accumulating — code added with no callers.",
        "rule_yaml": (
            "  - id: gate-orphan-symbols\n"
            "    description: Warn when PR adds symbols that no other module references\n"
            "    pattern: orphan_check\n"
            "    severity: WARN\n"
        ),
        "ci_gate": "roam orphan-imports --gate-on new-orphans",
        "ci_rationale": "orphan-imports catches dead-on-arrival additions.",
    },
}


def _recurring_risk_classes(by_detector: list[dict]) -> list[dict]:
    recurring: list[dict] = []
    for row in by_detector:
        if row["total_findings"] >= 2 or row["commits_with_finding"] >= 2:
            recurring.append(
                {
                    "class": row["detector"],
                    "total_findings": row["total_findings"],
                    "commits_with_finding": row["commits_with_finding"],
                }
            )
    return recurring[:10]


def _review_rules_preview(recurring: list[dict]) -> tuple[str | None, list[str]]:
    rule_bodies: list[str] = []
    matched_detectors: list[str] = []
    for row in recurring:
        tpl = _DETECTOR_RULE_TEMPLATES.get(row["class"])
        if not tpl:
            continue
        rule_bodies.append(tpl["rule_yaml"])
        matched_detectors.append(row["class"])
    if not rule_bodies:
        return None, matched_detectors
    header = (
        "# Generated by `roam pr-replay` — preview, not enforcement.\n"
        "# Drop at .roam/rules.yml then run `roam pr-analyze` to gate.\n"
        "# Rules below cover the detector classes that recurred in "
        "this replay window.\n"
        "rules:\n"
    )
    return header + "".join(rule_bodies), matched_detectors


def _detector_ci_gate(row: dict, seen_gates: set[str]) -> dict | None:
    tpl = _DETECTOR_RULE_TEMPLATES.get(row["class"])
    if not tpl:
        return None
    gate = tpl["ci_gate"]
    if gate in seen_gates:
        return None
    seen_gates.add(gate)
    return {
        "gate": gate,
        "detector": row["class"],
        "rationale": tpl["ci_rationale"],
    }


def _review_ci_gates(recurring: list[dict]) -> list[dict]:
    ci_gates: list[dict] = []
    seen_gates: set[str] = set()
    for row in recurring:
        gate = _detector_ci_gate(row, seen_gates)
        if gate is not None:
            ci_gates.append(gate)
    umbrella_gate = "roam critique --ci"
    if umbrella_gate not in seen_gates:
        ci_gates.append(
            {
                "gate": umbrella_gate,
                "detector": "*",
                "rationale": (
                    "critique exits 5 on any high-severity finding — single "
                    "step gates every PR against the full detector set."
                ),
            }
        )
    return ci_gates[:10]


def _blocked_review_commit(commit: dict) -> dict | None:
    high = int(commit.get("high", 0) or 0)
    if high <= 0:
        return None
    kinds = commit.get("kinds") or []
    top_kinds = ", ".join(kinds[:3]) if kinds else "high-severity finding"
    return {
        "sha": commit.get("short_sha") or commit.get("sha"),
        "date": commit.get("date"),
        "subject": (commit.get("subject") or "")[:80],
        "high_findings": high,
        "rationale": f"BLOCK verdict on {top_kinds}",
    }


def _blocked_review_commits(commits: list[dict]) -> list[dict]:
    blocked = []
    for commit in commits:
        row = _blocked_review_commit(commit)
        if row is not None:
            blocked.append(row)
    return blocked[:10]


def _build_review_suggestions(
    *,
    by_detector: list[dict],
    commits: list[dict],
    tier: str,
) -> dict | None:
    """Derive a four-part Roam Review configuration suggestion.

    Returns ``None`` (explicit absence per CLAUDE.md Pattern 1) when the
    Replay produced no detector hits — there's nothing concrete to
    suggest. Returns a dict with up to five keys when there's data:

      * ``recurring_risk_classes`` — detectors with ≥2 findings OR ≥2 PRs
      * ``suggested_roam_rules_yml`` — preview ``.roam/rules.yml`` body
      * ``suggested_ci_gates`` — concrete ``roam <cmd>`` gate invocations
      * ``what_review_would_have_blocked`` — per-PR block rationale (high
        severity only — medium-only PRs would get a REVIEW verdict, not
        BLOCK, and live in the Per-PR section already)
      * ``upgrade_pitch`` — single-line marketing nudge

    Each suggestion is bounded (max 10 items) so the block stays
    digestible even on a Deep tier with dozens of detector classes.

    All suggestions are *heuristic* — derived from data already in the
    Replay, no LLM call, no external lookup. Detectors we don't have a
    rule template for are silently skipped rather than mocked.
    """
    if not by_detector:
        return None

    recurring = _recurring_risk_classes(by_detector)
    suggested_yaml, matched_detectors = _review_rules_preview(recurring)
    suggestions: dict = {
        "recurring_risk_classes": recurring,
        "suggested_ci_gates": _review_ci_gates(recurring),
        "what_review_would_have_blocked": _blocked_review_commits(commits),
        "upgrade_pitch": (
            "Roam Review enforces these gates on every PR automatically. See https://roam-code.com/compare."
        ),
    }
    # Only include the YAML preview when we actually matched a template —
    # absent rather than a header-only-no-rules string (Pattern 1).
    if suggested_yaml is not None:
        suggestions["suggested_roam_rules_yml"] = suggested_yaml
        suggestions["suggested_rules_cover_detectors"] = matched_detectors

    # Tier hint — useful for the upgrade-pitch differentiation (sample
    # leads to Team, Team leads to Deep, Deep leads to Review).
    suggestions["replay_tier"] = tier
    return suggestions


def _parse_detector_kind(kind_str) -> tuple[str, int] | None:
    try:
        name, count = kind_str.rsplit(" x", 1)
        return name, int(count)
    except (ValueError, AttributeError):
        return None


def _accumulate_commit_kinds(
    kinds,
    totals: dict[str, int],
    commits_per_detector: dict[str, int],
) -> None:
    seen_in_this_commit: set[str] = set()
    for kind_str in kinds or []:
        parsed = _parse_detector_kind(kind_str)
        if parsed is None:
            continue
        name, count = parsed
        totals[name] += count
        if name not in seen_in_this_commit:
            commits_per_detector[name] += 1
            seen_in_this_commit.add(name)


def _detector_rollup_rows(totals: dict[str, int], commits_per_detector: dict[str, int]) -> list[dict]:
    return [
        {
            "detector": name,
            "total_findings": total,
            "commits_with_finding": commits_per_detector.get(name, 0),
        }
        for name, total in sorted(totals.items(), key=lambda kv: -kv[1])
    ]


def _aggregate_by_detector(commits: list[dict]) -> list[dict]:
    """Roll up per-commit ``kinds`` lists into a single ranked summary.

    Each commit's ``kinds`` is a list of ``"<detector> x<count>"`` strings
    produced by ``cmd_postmortem._short_finding_summary``. We re-parse,
    sum across commits, and emit a list ranked by total hits — that's
    the "what does Roam keep catching across this window" table.
    """
    totals: dict[str, int] = defaultdict(int)
    commits_per_detector: dict[str, int] = defaultdict(int)
    for commit in commits:
        _accumulate_commit_kinds(commit.get("kinds"), totals, commits_per_detector)
    return _detector_rollup_rows(totals, commits_per_detector)


# ---------------------------------------------------------------------------
# W177 Phase 3 — evidence collection + Markdown companion report.
#
# W179 swap: ``_collect_change_evidence`` now delegates to W176's canonical
# ``roam.evidence.collect_change_evidence`` collector. The W177 inline helper
# has been removed — PR Replay reshapes its postmortem-aggregate data into
# the synthetic envelopes the W176 collector consumes (one pr-bundle-shaped
# envelope for identity, one findings-shaped envelope for the postmortem +
# registry rows, one synthetic event stream for run-IDs sourced from the
# filesystem). Commit subjects keep ``kind="commit"`` (the W176 collector
# emits ``kind="symbol"`` for the ``affected_symbols`` it consumes) by being
# merged into the packet post-call via ``dataclasses.replace``.
#
# Deliberate design choices:
#
# * The Markdown report renders directly to a string (no jinja). The
#   ``templates/audit-report/pr-replay-template.md`` file in the repo is the
#   prose-stable reference; this function reproduces the same headings so a
#   diff between the rendered output and the template flags any drift.
# * Findings clustering for "Suggested Review configuration" reuses the
#   ``_aggregate_by_detector`` aggregator — same detector vocabulary the
#   existing Replay markdown already surfaces. Detector classes with ≥2
#   findings *or* ≥2 commits-with-finding become "recurring risk classes",
#   matching ``_build_review_suggestions``'s threshold.
# * Warnings returned by the W176 collector are logged to stderr (one line
#   per warning) but never fail the command — PR Replay has no ``--strict``
#   flag and the surface contract (``--evidence`` / ``--markdown`` /
#   ``--evidence-bundle``) must remain exit-code-stable. A future wave can
#   add a strict gate if the warning volume becomes interesting.
# ---------------------------------------------------------------------------


def _git_head_sha() -> str | None:
    """Best-effort lookup of the current ``HEAD`` SHA.

    Returns ``None`` if git is unavailable or the working tree isn't a
    checkout — we never raise; the evidence packet survives a missing SHA.

    W586: delegates to the shared ``roam.commands.git_helpers._run_git``
    helper which uses binary capture + manual UTF-8 decode so Windows
    shells with non-UTF8 default codepages (cp1252 / cp1253) don't trip
    the stdlib reader thread on a stray byte. The shared helper returns
    a stripped string (or ``""`` on any failure); we normalise that back
    to the historical ``None``-on-miss contract that callers expect.
    """
    from roam.commands.git_helpers import _run_git

    sha = _run_git(["git", "rev-parse", "HEAD"])
    return sha or None


def _is_safe_commit_range(commit_range: str) -> bool:
    """Reject argv-injection-shaped ranges.

    The ``--range`` flag flows into ``git diff``, ``git diff --name-only``,
    and an in-process ``roam postmortem`` invocation as a POSITIONAL
    argument. A value beginning with ``-`` would be re-interpreted by the
    receiving argument parser as an option flag (e.g. ``--upload-pack=evil``
    on git, or a Click option on the postmortem CLI). Reject any value that
    starts with ``-`` so the only allowed inputs are real git revspec strings
    (``HEAD~30..HEAD``, ``v1.0..main``, branch names, SHAs, …). Empty / None
    is rejected at the call site, not here.
    """
    if not commit_range:
        return False
    return not commit_range.lstrip().startswith("-")


def _diff_hash_for_range(commit_range: str) -> str | None:
    """Deterministic hash of the unified diff for ``commit_range``.

    Used as the ``ChangeEvidence.diff_hash`` field. Falls back to ``None``
    when git isn't reachable so the packet stays well-formed. Captures in
    binary mode so the Windows codepage decoder never sees the bytes.

    Refuses to invoke ``git`` when ``commit_range`` fails the argv-shape
    guard so a value beginning with ``-`` cannot be re-interpreted by git
    as an option flag.
    """
    if not _is_safe_commit_range(commit_range):
        return None
    try:
        result = _subprocess.run(
            # ``--`` separates revisions from paths; pinning the range
            # before ``--`` and an empty path list after ``--`` makes the
            # argv unambiguous to git's argument parser even if a future
            # refactor changes the value's shape.
            ["git", "diff", commit_range, "--"],
            capture_output=True,
            timeout=30,
        )
        if result.returncode != 0:
            return None
        return _hashlib.sha256(result.stdout or b"").hexdigest()
    except (FileNotFoundError, _subprocess.TimeoutExpired, OSError):
        return None


def _collect_findings_from_registry(limit: int = 200) -> list[dict]:
    """Pull rows from the central findings registry (W90 substrate).

    Returns an empty list when the DB or table is missing — the evidence
    packet then carries zero findings rather than crashing, which is the
    correct shape for a fresh / unindexed repo.
    """
    try:
        from roam.db.connection import open_db
        from roam.db.findings import list_findings

        with open_db(readonly=True) as conn:
            return list_findings(conn, limit=limit)
    except Exception:  # noqa: BLE001 — defensive; evidence must not crash CLI
        return []


def _collect_run_ids(commit_range: str) -> list[str]:
    """List run_ids whose meta.json sits inside ``.roam/runs/``.

    The replay is range-scoped, not run-scoped, so we surface every run
    visible to the local control plane and let the caller correlate. A
    follow-up wave can narrow by commit if the runs ledger gains
    git-correlation metadata.
    """
    runs_dir = Path(".roam") / "runs"
    if not runs_dir.is_dir():
        return []
    out: list[str] = []
    try:
        for child in sorted(runs_dir.iterdir()):
            if child.is_dir() and (child / "meta.json").exists():
                out.append(child.name)
    except OSError:
        return []
    return out[:50]  # bounded — Replay isn't a runs browser


def _run_meta_in_progress(child: Path) -> bool:
    try:
        meta = _json.loads((child / "meta.json").read_text(encoding="utf-8"))
        return meta.get("status") == "in_progress"
    except Exception as exc:  # noqa: BLE001 — meta drift must not block
        # A corrupt meta.json defaults in_progress to False — surface lineage
        # so a misclassified run has a cause.
        from roam.observability import log_swallowed

        log_swallowed(f"cmd_pr_replay:run_meta:{child.name}", exc)
        return False


def _run_candidate_for_replay(child: Path) -> tuple[float, str, bool] | None:
    if not (child.is_dir() and (child / "meta.json").exists()):
        return None
    try:
        return child.stat().st_mtime, child.name, _run_meta_in_progress(child)
    except OSError:
        return None


def _run_candidates_for_replay(runs_dir: Path) -> list[tuple[float, str, bool]]:
    try:
        children = list(runs_dir.iterdir())
    except OSError:
        return []
    candidates: list[tuple[float, str, bool]] = []
    for child in children:
        candidate = _run_candidate_for_replay(child)
        if candidate is not None:
            candidates.append(candidate)
    return candidates


def _pick_replay_run_id(candidates: list[tuple[float, str, bool]]) -> str | None:
    if not candidates:
        return None
    # Prefer in_progress runs over completed ones; within each tier, newest.
    candidates.sort(key=lambda candidate: (candidate[2], candidate[0]), reverse=True)
    return candidates[0][1]


# ---------------------------------------------------------------------------
# W223 — best-effort gatherers for the W199 collector kwargs.
#
# Each gatherer probes a well-known repo-local path under ``.roam/`` (or
# invokes a sibling subcommand) and returns whatever it found. Every
# gatherer is wrapped in a try/except that appends to the caller's
# ``warnings`` list — a missing source must never abort the PR Replay
# pipeline. The producer is range-scoped, not run-scoped, so when an
# explicit active run is available we prefer its run-local artefacts;
# otherwise we look at the most recent on-disk run and global state.
# ---------------------------------------------------------------------------


def _active_run_id_for_replay() -> str | None:
    """Resolve the run-id whose artefacts we should bind to the packet.

    Falls back from ``ROAM_RUN_ID`` -> newest in-progress run on disk ->
    newest run on disk (any status). The third tier exists because PR
    Replay typically runs AFTER the agent's run has been ended (and so is
    no longer "in_progress"). Returns ``None`` when no run is visible.
    """
    import os

    env_id = os.environ.get("ROAM_RUN_ID", "").strip()
    if env_id:
        return env_id
    runs_dir = Path(".roam") / "runs"
    if not runs_dir.is_dir():
        return None
    return _pick_replay_run_id(_run_candidates_for_replay(runs_dir))


def _rules_envelopes_dir(active_run_id: str | None) -> Path | None:
    if not active_run_id:
        return None
    env_dir = Path(".roam") / "runs" / active_run_id / "envelopes"
    if env_dir.is_dir():
        return env_dir
    return None


def _load_staged_rules_envelopes(env_dir: Path, warnings: list[str]) -> list[dict]:
    envelopes: list[dict] = []
    try:
        paths = sorted(env_dir.glob("rules-*.json"))
    except OSError as exc:
        warnings.append(f"rules-envelopes dir unreadable: {exc}")
        return envelopes
    for path in paths:
        try:
            envelopes.append(_json.loads(path.read_text(encoding="utf-8")))
        except Exception as exc:  # noqa: BLE001
            warnings.append(f"rules-envelope at {path.name} unparseable: {exc}")
    return envelopes


def _invoke_rules_envelope(warnings: list[str]) -> list[dict]:
    if not (Path(".roam") / "rules").is_dir():
        # No rules configured for this repo -> nothing to gather.
        return []
    try:
        from roam.cli import cli

        runner = CliRunner()
        result = runner.invoke(cli, ["--json", "rules"], catch_exceptions=True)
        if result.exit_code == 0 and result.output:
            return [_json.loads(result.output)]
        if result.exit_code != 0:
            warnings.append(f"roam rules exited {result.exit_code}; rules envelope skipped")
    except Exception as exc:  # noqa: BLE001 — best-effort
        warnings.append(f"rules-envelope gather failed: {exc}")
    return []


def _gather_rules_envelopes(active_run_id: str | None, warnings: list[str]) -> list[dict]:
    """Best-effort: emit one envelope by invoking ``roam rules --json``.

    The collector consumes any envelope shaped like ``{results: [...]}``
    — that's exactly what ``roam rules`` produces. We prefer reading
    pre-staged envelopes from ``.roam/runs/<run_id>/envelopes/rules-*.json``
    (a convention this command pins for the wave's downstream tooling)
    and fall back to invoking the subcommand when no envelope is staged.
    """
    # 1) Pre-staged envelopes under the active run, if any.
    env_dir = _rules_envelopes_dir(active_run_id)
    if env_dir is not None:
        staged = _load_staged_rules_envelopes(env_dir, warnings)
        if staged:
            return staged

    # 2) Fallback: invoke ``roam rules --json`` in-process via CliRunner.
    return _invoke_rules_envelope(warnings)


def _gather_audit_trail_envelope(active_run_id: str | None, warnings: list[str]) -> dict | None:
    """Best-effort: invoke ``roam audit-trail-verify --json``.

    The audit trail itself lives at ``.roam/audit-trail.jsonl`` (global,
    not per-run) — we never invoke the sub-command unless the file
    exists. The collector then promotes the envelope to a manifest
    artifact and emits ``audit_trail_chain_integrity`` policy decisions.
    """
    trail_path = Path(".roam") / "audit-trail.jsonl"
    if not trail_path.exists():
        return None
    try:
        from roam.cli import cli

        runner = CliRunner()
        result = runner.invoke(cli, ["--json", "audit-trail-verify"], catch_exceptions=True)
        if result.exit_code != 0 or not result.output:
            warnings.append(f"audit-trail-verify exited {result.exit_code}; envelope skipped")
            return None
        env = _json.loads(result.output)
        # Stamp the active run id onto the envelope summary so the
        # collector's manifest-artifact id includes a meaningful suffix.
        # The audit-trail-verify command doesn't know which run is active.
        if active_run_id and isinstance(env.get("summary"), dict):
            env["summary"].setdefault("run_id", active_run_id)
        return env
    except Exception as exc:  # noqa: BLE001 — best-effort
        warnings.append(f"audit-trail envelope gather failed: {exc}")
        return None


def _gather_vuln_reach_envelopes(commit_range: str, warnings: list[str]) -> list[dict]:
    """Best-effort: emit one envelope by invoking ``roam vuln-reach --json``.

    Skips when no vulnerabilities DB has been ingested (the command
    short-circuits with ``total_vulns: 0`` in that case; we still return
    that envelope so the collector sees a deterministic signal).
    """
    out: list[dict] = []
    if not (Path(".roam") / "index.db").exists():
        return out
    try:
        from roam.cli import cli

        runner = CliRunner()
        result = runner.invoke(cli, ["--json", "vuln-reach"], catch_exceptions=True)
        if result.exit_code == 0 and result.output:
            payload = _json.loads(result.output)
            # Only attach if the command actually reports vulnerabilities;
            # an empty envelope would produce no policy/finding signal but
            # would still take a raw_envelope artifact slot — skip in that
            # case to keep the packet lean.
            vulns = payload.get("vulnerabilities") or []
            if vulns:
                out.append(payload)
        elif result.exit_code != 0:
            warnings.append(f"roam vuln-reach exited {result.exit_code}; envelope skipped")
    except Exception as exc:  # noqa: BLE001 — best-effort
        warnings.append(f"vuln-reach envelope gather failed: {exc}")
    return out


def _gather_test_impact_envelopes(commit_range: str, warnings: list[str]) -> list[dict]:
    """Best-effort: emit one envelope by invoking ``roam test-impact --json``.

    The command expects ``--changed-files`` or scans staged + unstaged
    diffs against HEAD. For PR Replay we run with no args so it picks up
    whatever's currently changed in the working tree.
    """
    out: list[dict] = []
    if not (Path(".roam") / "index.db").exists():
        return out
    try:
        from roam.cli import cli

        runner = CliRunner()
        result = runner.invoke(cli, ["--json", "test-impact"], catch_exceptions=True)
        if result.exit_code == 0 and result.output:
            payload = _json.loads(result.output)
            tests = payload.get("tests") or []
            if tests:
                out.append(payload)
        elif result.exit_code != 0:
            warnings.append(f"roam test-impact exited {result.exit_code}; envelope skipped")
    except Exception as exc:  # noqa: BLE001 — best-effort
        warnings.append(f"test-impact envelope gather failed: {exc}")
    return out


def _recent_cga_statement_paths(attest_dir: Path, warnings: list[str]) -> list[Path]:
    try:
        return sorted(
            (p for p in attest_dir.glob("*.intoto.json") if p.is_file()),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )[:5]
    except OSError as exc:
        warnings.append(f"cga attestations dir unreadable: {exc}")
        return []


def _load_cga_statement(path: Path, warnings: list[str]) -> dict | None:
    try:
        statement = _json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        warnings.append(f"cga statement at {path.name} unparseable: {exc}")
        return None
    if not isinstance(statement, dict):
        warnings.append(f"cga statement at {path.name} is not a JSON object")
        return None
    return statement


def _cga_statement_envelope(path: Path, statement: dict) -> dict:
    predicate = statement.get("predicate") or {}
    return {
        "command": "cga-emit",
        "summary": {
            "verdict": "loaded from .roam/attestations/",
            "merkle_root": predicate.get("merkle_root"),
            "edge_bundle_digest": predicate.get("edge_bundle_digest"),
            "predicate_type": statement.get("predicateType"),
            "written_to": str(path),
        },
        "statement": statement,
    }


def _gather_cga_envelopes(active_run_id: str | None, warnings: list[str]) -> list[dict]:
    """Best-effort: load CGA in-toto statements from ``.roam/attestations/``.

    A signed CGA emits a ``<short_hash>.intoto.json`` file under
    ``.roam/attestations/``. We synthesise the envelope shape the
    collector expects (``{summary, statement}``) so the in-toto statement
    flows through unchanged. Bounded to the 5 most recent statements to
    keep the packet lean.
    """
    del active_run_id
    attest_dir = Path(".roam") / "attestations"
    if not attest_dir.is_dir():
        return []
    envelopes: list[dict] = []
    for path in _recent_cga_statement_paths(attest_dir, warnings):
        statement = _load_cga_statement(path, warnings)
        if statement is not None:
            envelopes.append(_cga_statement_envelope(path, statement))
    return envelopes


def _gather_mcp_receipts_dir(active_run_id: str | None, warnings: list[str]) -> str | None:
    """Best-effort: return ``.roam/mcp_receipts/<run_id>/`` when present.

    The collector tolerates a missing directory (no warnings, no
    artifacts). We only return a path when the directory exists AND
    contains at least one ``*.json`` file, so an empty directory doesn't
    masquerade as a populated receipt store.
    """
    base = Path(".roam") / "mcp_receipts"
    if not base.is_dir():
        return None
    # Prefer the run-specific subdirectory; fall back to the most recent
    # on-disk receipts directory if no active run is set.
    if active_run_id:
        candidate = base / active_run_id
        if candidate.is_dir() and any(candidate.glob("*.json")):
            return str(candidate)
    try:
        subdirs = sorted(
            (p for p in base.iterdir() if p.is_dir()),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
    except OSError as exc:
        warnings.append(f"mcp_receipts dir unreadable: {exc}")
        return None
    for sub in subdirs:
        if any(sub.glob("*.json")):
            return str(sub)
    # Top-level receipts (some installs don't nest under a run id).
    if any(base.glob("*.json")):
        return str(base)
    return None


# W246 — context_refs gatherer.
#
# The W201/W230/W244 audit traced Q3 ("WHAT context did the actor read?")
# staying at ``missing`` end-to-end because PR Replay never populated the
# collector's ``context_files`` channel. The collector itself has the
# wiring (`_build_context_refs_from_context_files`), but the upstream
# producer left the key empty.
#
# Postmortem's per-commit envelope doesn't carry changed-file lists, so
# we derive the surface from git directly. ``git diff --name-only`` on the
# range gives us the union of files touched across the window, naturally
# deduplicated and already gitignore-respecting. That matches the spirit
# of the task's "walks postmortem per_commit changed_files" with the
# pragmatic source: there's no other authoritative store for "files this
# commit range touched" inside the replay pipeline.
#
# Per the W246 directive, ``content_hash`` is left as ``None`` — hashing
# every file on a large diff is a perf disaster and the collector
# transparently falls back to the inline-path artifact form (see
# ``_build_context_refs_from_context_files`` in src/roam/evidence/collector.py).
def _git_diff_name_only(commit_range: str, warnings: list[str]) -> str | None:
    try:
        result = _subprocess.run(
            # ``--`` pins ``commit_range`` as a revision; the empty path
            # list after ``--`` is implicit. Defense in depth against
            # the same argv-injection shape ``_diff_hash_for_range``
            # already guards.
            ["git", "diff", "--name-only", commit_range, "--"],
            capture_output=True,
            timeout=15,
        )
    except (FileNotFoundError, _subprocess.TimeoutExpired, OSError) as exc:
        warnings.append(f"_gather_context_files: git unavailable ({exc})")
        return None
    if result.returncode == 0:
        return result.stdout.decode("utf-8", errors="replace")
    stderr_tail = result.stderr.decode("utf-8", errors="replace").strip()[:200]
    warnings.append(f"_gather_context_files: git diff returned {result.returncode}: {stderr_tail}")
    return None


def _context_file_rows_from_diff(diff_text: str, warnings: list[str]) -> list[dict]:
    seen: set[str] = set()
    out: list[dict] = []
    for line in diff_text.splitlines():
        path = line.strip()
        if not path or path in seen:
            continue
        seen.add(path)
        out.append({"path": path, "content_hash": None, "kind": "changed"})
        if len(out) >= 500:
            warnings.append("_gather_context_files: capped at 500 entries; larger diffs are intentionally truncated")
            break
    return out


def _gather_context_files(
    commit_range: str,
    commits: list[dict],
    warnings: list[str],
) -> list[dict]:
    """Best-effort: enumerate files touched by ``commit_range``.

    Returns a list of dicts shaped for the collector's
    ``context_files`` channel::

        [
            {"path": "src/foo.py", "content_hash": None, "kind": "changed"},
            ...
        ]

    ``content_hash`` is intentionally ``None``: hashing every changed file
    in a large replay would be a perf regression with no audit value
    (the collector falls back to the inline-path artifact form when the
    hash is absent — see ``_build_context_refs_from_context_files``).

    The ``commits`` arg is accepted for signature symmetry with the
    other gatherers and to leave room for a future per-commit walk; the
    canonical source today is ``git diff --name-only <commit_range>``.
    """
    # Empty range or empty commits means nothing to enumerate — return
    # an empty list rather than calling git for no reason.
    if not commit_range or not commits:
        return []
    if not _is_safe_commit_range(commit_range):
        warnings.append(f"_gather_context_files: refusing argv-injection-shaped commit_range ({commit_range!r})")
        return []
    text = _git_diff_name_only(commit_range, warnings)
    return _context_file_rows_from_diff(text, warnings) if text is not None else []


# ---------------------------------------------------------------------------
# W267 — policy-decision gatherers for constitution / permits / leases.
#
# The W252 producer-coverage matrix flagged the policy axis as one of the
# under-served evidence questions: only ``rules`` and ``audit-trail-verify``
# fed ``ChangeEvidence.policy_decisions[]``. Every active permit, every
# active lease, and every constitution gate represents an *authority
# decision* that should also surface as a row, because:
#
# * ``AuthorityRef`` says "this authority object existed during the change"
#   (descriptive). The W268 lift of permits[] / leases[] into AuthorityRefs
#   covers this axis already.
# * ``PolicyDecision`` says "this rule/gate evaluated to <pass|fail|allow|
#   not_evaluated> during the change" (evaluative). That's the gap.
#
# Both rows tell a different story to a reviewer; we emit both.
#
# Each gatherer follows the W223 contract: best-effort, returns a (possibly
# empty) list, appends a warning on parse/IO failure, never raises. The
# dispatcher in ``_collect_change_evidence`` wraps every call in try/except
# as defense-in-depth.
#
# Granularity: one ``PolicyDecision`` per top-level constitution gate, per
# permit file, and per lease record. We do NOT recursively expand the per-
# gate command list, per-permit scope, or per-lease subject[] into
# separate rows — keeping cardinality bounded means a 50-line constitution
# stays at 3 rows, not 30.
# ---------------------------------------------------------------------------


def _gather_constitution_policy_decisions(
    warnings: list[str],
) -> list[dict]:
    """Read ``.roam/constitution.yml``; emit one decision per top-level gate.

    Returns a list of ``policy_decisions`` rows shaped::

        {
            "rule_id": "constitution:<gate_id>",
            "decision": "not_evaluated",
            "evidence_ref": "constitution:<gate_id>",
            ...
        }

    ``decision`` is ``"not_evaluated"`` because PR Replay records the gate's
    *presence* in the constitution, not whether it actually ran (the
    constitution.yml schema today does not carry a per-gate enabled flag —
    a gate's existence in the file implies it's in force, but the
    *evaluation result* is captured elsewhere by the gate's own command).
    A future ``required_checks_status`` field could flip this to
    ``"allow"`` / ``"deny"`` per gate; until then ``not_evaluated`` is the
    honest vocabulary.
    """
    try:
        from roam.constitution.loader import load_constitution
        from roam.db.connection import find_project_root
    except Exception as exc:  # noqa: BLE001 — best-effort
        warnings.append(f"_gather_constitution_policy_decisions: import failed ({exc})")
        return []
    try:
        repo_root = find_project_root()
    except Exception:  # noqa: BLE001 — best-effort policy capture
        repo_root = None
    if repo_root is None:
        return []
    try:
        constitution = load_constitution(Path(repo_root))
    except Exception as exc:  # noqa: BLE001 — loader is supposed to never
        # raise, but be defensive in case a future change reverses that.
        warnings.append(f"_gather_constitution_policy_decisions: load failed ({exc})")
        return []
    if constitution is None:
        return []
    # W426: surface unparseable constitution.yml as a producer warning so
    # the operator can locate and repair the file. The loader returns a
    # marker constitution with ``metadata.unparseable = True`` rather
    # than raising; without this check the empty ``required_checks``
    # walk below silently emits no policy decisions and the auditor has
    # no way to distinguish "no gates configured" from "gates exist but
    # the file is malformed."
    if constitution.metadata.get("unparseable"):
        warnings.append("constitution: .roam/constitution.yml is malformed — required_checks ignored")
        return []
    prov = _producer_provenance("constitution")
    required_checks = constitution.required_checks or {}
    return [
        entry
        for gate_id, commands in required_checks.items()
        if (entry := _constitution_policy_decision(gate_id, commands, prov)) is not None
    ]


def _producer_provenance(detail: str) -> str | None:
    # W293 — stamp provenance at the producer/gatherer ingestion site so
    # typed policy decisions carry the channel attribution.
    try:
        from roam.evidence.provenance import provenance_label

        return provenance_label("producer_envelope", detail=detail)
    except Exception:  # noqa: BLE001 - helper is supposed to never fail
        return None


def _constitution_policy_decision(gate_id, commands, prov: str | None) -> dict | None:
    if not isinstance(gate_id, str) or not gate_id:
        return None
    entry: dict = {
        "rule_id": f"constitution:{gate_id}",
        "decision": "not_evaluated",
        "evidence_ref": f"constitution:{gate_id}",
    }
    if isinstance(commands, list) and commands:
        entry["command_count"] = len(commands)
    if prov is not None:
        entry["provenance"] = prov
    return entry


def _policy_project_root(find_project_root, prefix: str, warnings: list[str]) -> Path | None:
    try:
        repo_root = find_project_root()
    except Exception as exc:  # noqa: BLE001 — best-effort, never block
        warnings.append(f"{prefix}: project_root_lookup_failed — find_project_root raised {type(exc).__name__}: {exc}")
        return None
    if repo_root is None:
        warnings.append(
            f"{prefix}: project_root_not_found — find_project_root returned None (no .git ancestor; not a roam project)"
        )
        return None
    return Path(repo_root)


def _permit_reader_dependencies_for_degraded_replay(
    warnings: list[str],
) -> tuple[Callable[[], Path | None], Callable[..., list[dict]]] | None:
    try:
        from roam.db.connection import find_project_root
        from roam.permits.store import load_permits_from_disk
    except ImportError as exc:
        warnings.append(f"_gather_permit_policy_decisions: import failed ({exc})")
        return None
    return find_project_root, load_permits_from_disk


def _gather_permit_policy_decisions(
    warnings: list[str],
) -> list[dict]:
    """Read ``.roam/permits/*.json``; emit one ``allow`` decision per permit.

    Each permit file on disk represents an issued permit — its existence
    is proof that an ``allow`` authority decision was made at issue time.
    We therefore emit ``decision="allow"`` per file. ``expires_at`` and
    ``scope`` fold into the row when present so downstream consumers can
    tell stale permits from live ones and see what was permitted.

    W383: this gatherer now delegates the directory walk + parse +
    validation to :func:`roam.permits.store.load_permits_from_disk`,
    the SAME validated reader ``cmd_pr_bundle._load_permits_from_disk``
    uses. Before W383, pr-replay had its own loop that skipped the W380
    schema gate entirely; a malformed permit dropped by the bundle path
    would silently surface in the replay envelope (divergence). The
    shared reader closes that gap: schema-invalid / duplicate / malformed
    rows are dropped uniformly across both producer paths and surface as
    actionable warnings here in ``warnings`` (the W199 collector's
    ``producer_warnings`` bucket).
    """
    deps = _permit_reader_dependencies_for_degraded_replay(warnings)
    if deps is None:
        return []
    find_project_root, load_permits_from_disk = deps
    # W591: mirror W590's surgical Pattern-2 fix for the SIBLING permit
    # gatherer. Before W591 the project-root lookup silently fell through
    # to an empty list — making "not a roam project" indistinguishable
    # from "no permits on this PR". Each failure path now appends one
    # structured warning (``permits:`` prefix + closed-form kind) to
    # ``warnings``. The propagation channel is unchanged: the W590-
    # plumbed ``producer_warnings_out`` chain carries these markers from
    # the gatherer → ``pre_warnings`` → ``_collect_change_evidence`` →
    # envelope ``warnings_out`` without further wiring.
    repo_root = _policy_project_root(find_project_root, "permits", warnings)
    if repo_root is None:
        return []
    # W383: route every dict through the shared validated reader. Append
    # one warning per dropped row (malformed JSON / non-dict / schema-
    # invalid / duplicate permit_id) into the collector's pre-warning
    # bucket so the replay envelope's ``producer_warnings`` surface
    # narrates exactly which permits the gatherer dropped and why.
    permits: list[dict] = load_permits_from_disk(
        repo_root,
        warnings_out=warnings,
    )
    prov = _producer_provenance("permit")
    return [entry for raw in permits if (entry := _permit_policy_decision(raw, prov)) is not None]


def _permit_policy_decision(raw: dict, prov: str | None) -> dict | None:
    # The shared reader returns raw dicts that survived
    # ``_permit_from_dict`` validation; ``permit_id`` is guaranteed
    # non-empty and matches ``PERMIT_ID_RE``.
    permit_id = raw.get("permit_id") or raw.get("id")
    if not isinstance(permit_id, str) or not permit_id:
        # Defensive: validator already enforces a non-empty
        # ``permit_id``, but if a future reader change loosens that
        # contract, skip rather than emit a garbage row.
        return None
    entry: dict = {
        "rule_id": f"permit:{permit_id}",
        "decision": "allow",
        "evidence_ref": f"permit:{permit_id}",
    }
    expires_at = raw.get("expires_at")
    if isinstance(expires_at, str) and expires_at:
        entry["expires_at"] = expires_at
    scope = raw.get("scope")
    if isinstance(scope, str) and scope:
        entry["scope"] = scope
    if prov is not None:
        entry["provenance"] = prov
    return entry


def _gather_lease_policy_decisions(
    warnings: list[str],
) -> list[dict]:
    """Read ``.roam/leases/*.json``; emit one ``allow`` decision per lease.

    Delegates the file walk to :func:`roam.leases.list_leases` so the on-
    disk schema stays single-sourced — same approach
    ``_load_leases_from_disk`` in ``cmd_pr_bundle`` uses. We pass
    ``include_expired=True`` / ``include_released=True`` because the
    evidence packet is a snapshot: a recently-released or expired lease is
    still proof of "an agent claimed this scope during the change."

    Each lease's ``subject[]`` flows verbatim onto the row (the
    ``subject_kind`` is folded in too) so a reviewer can see exactly what
    was leased without consulting the on-disk file.
    """
    try:
        from roam.db.connection import find_project_root
        from roam.leases.store import list_leases
    except Exception as exc:  # noqa: BLE001 — best-effort
        warnings.append(f"_gather_lease_policy_decisions: import failed ({exc})")
        return []
    # W590: surface the project-root lookup outcome instead of swallowing
    # silently (Pattern-2 silent fallback). When ``find_project_root``
    # returns None — or raises — the lease gatherer previously emitted
    # zero rows with no signal, making "no roam project here" look
    # identical to "no leases on this PR" in the replay envelope. Each
    # path now appends one structured warning so the operator can tell
    # the two cases apart.
    repo_root = _policy_project_root(find_project_root, "leases", warnings)
    if repo_root is None:
        return []
    leases_dir = repo_root / ".roam" / "leases"
    if not leases_dir.is_dir():
        _warn_missing_leases_dir(repo_root, warnings)
        return []
    try:
        # W425: thread the producer-warning bucket so malformed /
        # schema-invalid ``.roam/leases/*.json`` files surface in the
        # replay envelope's ``producer_warnings`` (mirrors the W383
        # permit gatherer).
        leases = list_leases(
            repo_root,
            include_expired=True,
            include_released=True,
            warnings_out=warnings,
        )
    except Exception as exc:  # noqa: BLE001 — list_leases is supposed to
        # never raise, but be defensive.
        warnings.append(f"_gather_lease_policy_decisions: list_leases failed ({exc})")
        return []
    prov = _producer_provenance("lease")
    return [entry for lease in leases if (entry := _lease_policy_decision(lease, prov)) is not None]


def _warn_missing_leases_dir(repo_root: Path, warnings: list[str]) -> None:
    # W447: structured-signal-beats-silent-fallback (Pattern 2). When
    # the active mode is one that *expects* leases (``migration`` /
    # ``autonomous_pr``), an absent ``.roam/leases/`` directory is
    # operationally surprising — emit an info marker so the operator has
    # a breadcrumb for "why aren't my leases showing?". Default /
    # ``safe_edit`` / ``read_only`` runs stay silent; W267's
    # ``test_pr_replay_gathers_handle_missing_state`` asserts the quiet
    # path on a bare repo.
    try:
        from roam.modes import get_active_mode

        active_mode = get_active_mode(repo_root)
    except Exception:  # noqa: BLE001 — best-effort, never block
        active_mode = None
    if active_mode in {"migration", "autonomous_pr"}:
        warnings.append(f"leases: .roam/leases/ directory not found — expected for mode '{active_mode}'")


def _lease_policy_decision(lease, prov: str | None) -> dict | None:
    lease_id = lease.lease_id
    if not lease_id:
        return None
    entry: dict = {
        "rule_id": f"lease:{lease_id}",
        "decision": "allow",
        "evidence_ref": f"lease:{lease_id}",
        "subject_kind": lease.subject_kind,
        "subject": list(lease.subject),
    }
    if lease.state:
        entry["state"] = lease.state
    if lease.expires_at:
        entry["expires_at"] = lease.expires_at
    if prov is not None:
        entry["provenance"] = prov
    return entry


# ---------------------------------------------------------------------------
# W247b - GitHub PR-review harvester wiring.
#
# Consumes the W247a parser at :mod:`roam.evidence.github_reviews` and emits
# two outputs the collector already understands:
#
#   * ``ApprovalRecord`` rows (APPROVED on the current head commit) - dict-
#     ified onto ``pr_bundle_envelope["approvals"]`` so the collector's
#     bundle-approvals reader picks them up.
#   * ``PolicyDecision`` rows (CHANGES_REQUESTED on any commit) - dict-ified
#     via ``PolicyDecision.to_dict()`` onto ``extra_policy_decisions`` so the
#     collector concatenates them with the existing W267 channel.
#
# The harvester is opt-in: nothing fires unless the operator passes one of
# ``--github-reviews-json`` (fixture / offline) or ``--github-reviews-gh``
# (live ``gh api`` subprocess). The third return value is the
# ``source_was_provided`` flag the W261 ``producer_not_available`` emitter
# consumes to distinguish "checked, no approval" from "producer unavailable."
#
# Bodies are NEVER stored (W247a guardrail asserted in the parser tests +
# replicated in tests/test_pr_replay_github_reviews.py).
# ---------------------------------------------------------------------------


def _approval_extra_fields(record) -> dict:
    extra = dict(record.extra or {})
    review_id = extra.pop("review_id", None)
    out: dict = {}
    if review_id is not None:
        out["approval_id"] = f"github_review:{review_id}"
    for key, value in extra.items():
        if key not in out:
            out[key] = value
    return out


def _stamp_github_review_provenance(out: dict) -> None:
    if "provenance" in out:
        return
    try:
        from roam.evidence.provenance import provenance_label

        out["provenance"] = provenance_label(
            "producer_envelope",
            detail="github_review",
        )
    except Exception as _exc:  # noqa: BLE001 - helper is supposed to never fail
        # A failure silently drops the provenance link from the evidence
        # packet — surface lineage so the gap has a cause.
        from roam.observability import log_swallowed

        log_swallowed("cmd_pr_replay:provenance_label", _exc)


def _approval_record_to_envelope_dict(record) -> dict:
    """Flatten an :class:`ApprovalRecord` into the envelope dict shape.

    The collector's ``bundle_approvals`` reader (collector.py L2523-L2527)
    expects a list of dicts with ``approver`` / ``scope`` / optional
    ``approval_id`` / ``reason`` / ``recorded_at``. The collector's
    authority-ref builder (collector.py L1003-L1012) reads
    ``approval_id`` and ``approver``. We flatten ``ApprovalRecord.extra``
    into the top level so the collector's existing dict-reading code sees
    the same shape ``roam pr-bundle add-approval`` produces today. Body
    fields are NEVER copied (parser stripped them; we never re-add them).

    W293 — stamp ``provenance = "producer_envelope(github_review)"`` on the
    emitted dict so the collector's ingestion-point provenance reader
    attributes this approval to the GitHub PR-review channel. Existing
    provenance on the record (if any future producer pre-stamps one) is
    preserved.
    """
    out: dict = {
        "approver": record.approver,
        "scope": record.scope,
        "recorded_at": record.timestamp,
        "reason": record.reason or "",
    }
    # Carry the surviving extras (commit_id, html_url) so the packet
    # retains the provenance link back to the GitHub review.
    for k, v in _approval_extra_fields(record).items():
        if k not in out:
            out[k] = v
    if record.expiry:
        out["expiry"] = record.expiry
    if record.risk_accepted:
        out["risk_accepted"] = record.risk_accepted
    # W293 — stamp provenance ONLY when not already present (preserve
    # discipline). The parser's ``record.extra`` may carry a
    # ``provenance`` from a future producer; we don't overwrite.
    _stamp_github_review_provenance(out)
    return out


def _github_review_inputs_ready(
    *,
    pr_number: int | None,
    head_commit_sha: str | None,
    warnings: list[str],
) -> bool:
    if pr_number is None:
        warnings.append(
            "github review source provided without --github-pr-number; skipping (parser requires a PR number)"
        )
        return False
    if not head_commit_sha:
        warnings.append(
            "github review source provided but HEAD SHA could not be "
            "resolved; skipping (head-commit filter unavailable)"
        )
        return False
    return True


def _github_review_parser(warnings: list[str]):
    try:
        from roam.evidence.github_reviews import (
            harvest_reviews_from_gh_cli,
            load_reviews_from_fixture,
            parse_github_reviews,
        )
    except Exception as exc:  # noqa: BLE001 - parser import is best-effort
        warnings.append(f"github review parser unavailable: {exc}")
        return None
    return load_reviews_from_fixture, harvest_reviews_from_gh_cli, parse_github_reviews


def _load_github_reviews(
    *,
    fixture_path,
    gh_spec: str | None,
    pr_number: int,
    loaders,
    warnings: list[str],
):
    load_reviews_from_fixture, harvest_reviews_from_gh_cli, _parse_reviews = loaders
    try:
        if fixture_path is not None:
            return load_reviews_from_fixture(Path(fixture_path))
        owner, repo, gh_pr = _parse_gh_spec(gh_spec or "")
        if gh_pr is not None and gh_pr != pr_number:
            warnings.append(
                f"--github-reviews-gh PR number ({gh_pr}) disagrees "
                f"with --github-pr-number ({pr_number}); using "
                f"--github-pr-number"
            )
        return harvest_reviews_from_gh_cli(
            owner=owner,
            repo=repo,
            pr_number=pr_number,
        )
    except Exception as exc:  # noqa: BLE001 - load is best-effort
        warnings.append(f"github review load failed: {exc}")
        return None


def _parse_github_review_outputs(
    *,
    reviews,
    head_commit_sha: str,
    pr_number: int,
    parse_github_reviews,
    warnings: list[str],
):
    try:
        approvals, policy_decisions, parser_warnings = parse_github_reviews(
            reviews=reviews,
            head_commit_sha=head_commit_sha,
            pr_number=pr_number,
        )
    except Exception as exc:  # noqa: BLE001 - parse is best-effort
        warnings.append(f"github review parse failed: {exc}")
        return (), ()
    for warning in parser_warnings:
        warnings.append(f"github review: {warning}")
    approval_dicts = tuple(_approval_record_to_envelope_dict(record) for record in approvals)
    policy_dicts = tuple(decision.to_dict() for decision in policy_decisions)
    return approval_dicts, policy_dicts


def _gather_github_reviews(
    *,
    fixture_path,
    gh_spec: str | None,
    pr_number: int | None,
    head_commit_sha: str | None,
    warnings: list[str],
):
    """Resolve GitHub PR reviews from one of two opt-in sources.

    Returns ``(approval_dicts, policy_decision_dicts, source_was_provided)``:

    * ``approval_dicts``       - tuple of dicts ready for
      ``pr_bundle_envelope["approvals"]``. Empty when the source had no
      APPROVED reviews on the current head commit OR no source was given.
    * ``policy_decision_dicts``- tuple of dicts (from
      :meth:`PolicyDecision.to_dict`) ready to extend
      ``extra_policy_decisions``.
    * ``source_was_provided``  - ``True`` iff the caller passed one of the
      GitHub options. The W261 emitter consumes this to suppress
      ``producer_not_available`` when a source WAS checked even if it
      found zero approvals (the "checked, no approval" case).

    Best-effort: any exception is caught, logged via ``warnings``, and
    returns ``((), (), source_was_provided)``. The replay never aborts
    on a GitHub-side failure.
    """
    source_was_provided = bool(fixture_path) or bool(gh_spec)
    if not source_was_provided:
        return (), (), False

    if not _github_review_inputs_ready(
        pr_number=pr_number,
        head_commit_sha=head_commit_sha,
        warnings=warnings,
    ):
        return (), (), True

    loaders = _github_review_parser(warnings)
    if loaders is None:
        return (), (), True

    reviews = _load_github_reviews(
        fixture_path=fixture_path,
        gh_spec=gh_spec,
        pr_number=pr_number,
        loaders=loaders,
        warnings=warnings,
    )
    if reviews is None:
        return (), (), True

    approval_dicts, policy_dicts = _parse_github_review_outputs(
        reviews=reviews,
        head_commit_sha=head_commit_sha,
        pr_number=pr_number,
        parse_github_reviews=loaders[2],
        warnings=warnings,
    )
    return approval_dicts, policy_dicts, True


def _parse_gh_spec(spec: str) -> tuple[str, str, int | None]:
    """Parse ``OWNER/REPO#PR`` into ``(owner, repo, pr_number)``.

    Accepts ``OWNER/REPO`` (no ``#PR``; returns ``pr_number=None``) so
    the caller can fall back to ``--github-pr-number``. Raises
    ``ValueError`` if the OWNER/REPO half is malformed.
    """
    if "#" in spec:
        repo_part, pr_part = spec.split("#", 1)
        try:
            pr_num: int | None = int(pr_part)
        except ValueError as exc:
            raise ValueError(f"--github-reviews-gh PR component is not an int: {pr_part!r}") from exc
    else:
        repo_part = spec
        pr_num = None
    if "/" not in repo_part:
        raise ValueError(f"--github-reviews-gh expected OWNER/REPO[#PR], got {spec!r}")
    owner, repo = repo_part.split("/", 1)
    if not owner or not repo:
        raise ValueError(f"--github-reviews-gh OWNER/REPO halves must be non-empty: {spec!r}")
    return owner, repo, pr_num


@dataclass(frozen=True)
class _PrReplayProducerInputs:
    active_run_id: str | None
    pre_warnings: list[str]
    rules_envelopes: list[dict]
    audit_trail_envelope: dict | None
    vuln_reach_envelopes: list[dict]
    test_impact_envelopes: list[dict]
    cga_envelopes: list[dict]
    mcp_receipts_dir: str | None
    extra_policy_decisions: list[dict]


def _run_pr_replay_gatherer(
    name: str,
    warnings: list[str],
    default: _T,
    gather: Callable[[], _T],
) -> _T:
    try:
        return gather()
    except Exception as exc:  # noqa: BLE001 — gatherers are best-effort
        warnings.append(f"{name} crashed: {exc}")
        return default


def _gather_pr_replay_producer_inputs(
    *,
    commit_range: str,
    commits: list[dict],
    pr_bundle_envelope: dict,
) -> _PrReplayProducerInputs:
    """Gather best-effort producer inputs for ``ChangeEvidence``."""
    pre_warnings: list[str] = []
    active_run_id = _active_run_id_for_replay()
    rules_envelopes = _run_pr_replay_gatherer(
        "_gather_rules_envelopes",
        pre_warnings,
        [],
        lambda: _gather_rules_envelopes(active_run_id, pre_warnings),
    )
    audit_trail_envelope = _run_pr_replay_gatherer(
        "_gather_audit_trail_envelope",
        pre_warnings,
        None,
        lambda: _gather_audit_trail_envelope(active_run_id, pre_warnings),
    )
    vuln_reach_envelopes = _run_pr_replay_gatherer(
        "_gather_vuln_reach_envelopes",
        pre_warnings,
        [],
        lambda: _gather_vuln_reach_envelopes(commit_range, pre_warnings),
    )
    test_impact_envelopes = _run_pr_replay_gatherer(
        "_gather_test_impact_envelopes",
        pre_warnings,
        [],
        lambda: _gather_test_impact_envelopes(commit_range, pre_warnings),
    )
    cga_envelopes = _run_pr_replay_gatherer(
        "_gather_cga_envelopes",
        pre_warnings,
        [],
        lambda: _gather_cga_envelopes(active_run_id, pre_warnings),
    )
    mcp_receipts_dir = _run_pr_replay_gatherer(
        "_gather_mcp_receipts_dir",
        pre_warnings,
        None,
        lambda: _gather_mcp_receipts_dir(active_run_id, pre_warnings),
    )

    # W267 — three policy-decision gatherers. Each represents an
    # authority decision recorded in the repo-local agent-OS substrate.
    constitution_policy_decisions = _run_pr_replay_gatherer(
        "_gather_constitution_policy_decisions",
        pre_warnings,
        [],
        lambda: _gather_constitution_policy_decisions(pre_warnings),
    )
    permit_policy_decisions = _run_pr_replay_gatherer(
        "_gather_permit_policy_decisions",
        pre_warnings,
        [],
        lambda: _gather_permit_policy_decisions(pre_warnings),
    )
    lease_policy_decisions = _run_pr_replay_gatherer(
        "_gather_lease_policy_decisions",
        pre_warnings,
        [],
        lambda: _gather_lease_policy_decisions(pre_warnings),
    )
    extra_policy_decisions: list[dict] = []
    extra_policy_decisions.extend(constitution_policy_decisions)
    extra_policy_decisions.extend(permit_policy_decisions)
    extra_policy_decisions.extend(lease_policy_decisions)

    context_files = _run_pr_replay_gatherer(
        "_gather_context_files",
        pre_warnings,
        [],
        lambda: _gather_context_files(commit_range, commits, pre_warnings),
    )
    if context_files:
        pr_bundle_envelope["context_files"] = context_files

    return _PrReplayProducerInputs(
        active_run_id=active_run_id,
        pre_warnings=pre_warnings,
        rules_envelopes=rules_envelopes,
        audit_trail_envelope=audit_trail_envelope,
        vuln_reach_envelopes=vuln_reach_envelopes,
        test_impact_envelopes=test_impact_envelopes,
        cga_envelopes=cga_envelopes,
        mcp_receipts_dir=mcp_receipts_dir,
        extra_policy_decisions=extra_policy_decisions,
    )


def _stamp_actor_block_on_envelope(pr_bundle_envelope: dict) -> None:
    """W260: stamp the W189-shape actor block onto the synth envelope.

    Resolves identity via the same priority chain ``pr-bundle emit`` uses
    (CLI flag > env > git config > active run-ledger agent), through the
    shared helper at ``roam.commands.actor_helpers``. Defense-in-depth: also
    runs the W249 collector-side scrubber so any secret-shaped substring in
    ``ROAM_AGENT_ID`` / ``ROAM_HUMAN_ACTOR`` is sanitised at the producer
    boundary (idempotent with the collector's own second pass). Best-effort --
    a producer must never abort the replay on actor-resolution failure; the
    collector's audit-trail / run-event sources still populate ``actor_refs``.
    """
    try:
        from roam.commands.actor_helpers import resolve_actor_block
        from roam.db.connection import find_project_root
        from roam.evidence.collector import _scrub_actor_block

        try:
            repo_root = find_project_root()
        except Exception:  # noqa: BLE001 — best-effort actor capture
            repo_root = None
        actor_block = resolve_actor_block(
            agent_id_override=None,
            human_actor_override=None,
            repo_root=repo_root,
        )
        scrubbed_actor, actor_had_secret = _scrub_actor_block(actor_block)
        pr_bundle_envelope["actor"] = dict(scrubbed_actor or {})
        if actor_had_secret:
            existing = pr_bundle_envelope.get("redactions") or []
            if "secret" not in existing:
                existing = list(existing) + ["secret"]
            pr_bundle_envelope["redactions"] = existing
    except Exception as exc:  # noqa: BLE001 - actor block is best-effort
        print(
            f"[pr-replay] actor-block resolution failed: {exc}",
            file=__import__("sys").stderr,
        )


def _stamp_authority_env_on_envelope(pr_bundle_envelope: dict, commit_range: str) -> tuple:
    """W272: stamp W266/W268 producer-pattern fields on the synth envelope.

    Writes ``permits[]`` (``.roam/permits/*.json``), ``leases[]``
    (``.roam/leases/*.json``), and ``environment_refs[]`` (W266 helper) so
    pr-replay has the same authority + environment parity as ``pr-bundle
    emit``. Always-emit (Pattern 2): even empty directories leave the keys
    present for a stable direct-envelope shape. Returns the W266-derived
    ``environment_refs`` tuple so the caller can merge it into the packet
    post-collector (the collector rebuilds env_refs from caller kwargs and
    would otherwise drop the ``workspace`` ref). Best-effort: every reader
    is independently guarded so the replay never aborts.
    """
    try:
        # W422: ``cmd_pr_bundle._load_permits_from_disk`` is the deprecated
        # thin wrapper; new code imports the canonical helper directly from
        # ``roam.permits.store``. The lease wrapper has no canonical
        # substrate-side reader yet (it bundles ``list_leases`` + dict
        # projection) so we keep that import.
        from roam.commands.cmd_pr_bundle import _load_leases_from_disk
        from roam.db.connection import find_project_root
        from roam.evidence.env_refs import build_environment_refs
        from roam.permits.store import load_permits_from_disk

        try:
            w272_ws_root = find_project_root()
        except Exception:  # noqa: BLE001 — best-effort authority/env capture
            w272_ws_root = None
        try:
            w272_permits = load_permits_from_disk(w272_ws_root)
        except Exception:  # noqa: BLE001 — best-effort authority/env capture
            w272_permits = []
        try:
            w272_leases = _load_leases_from_disk(w272_ws_root)
        except Exception:  # noqa: BLE001 — best-effort authority/env capture
            w272_leases = []
        try:
            w272_env_refs_tuple = build_environment_refs(
                commit_range=commit_range,
                workspace_root=str(w272_ws_root) if w272_ws_root else None,
            )
            w272_env_refs_dicts = [{"env_kind": r.env_kind, "env_id": r.env_id} for r in w272_env_refs_tuple]
        except Exception:  # noqa: BLE001 — best-effort authority/env capture
            w272_env_refs_tuple = ()
            w272_env_refs_dicts = []
        pr_bundle_envelope["permits"] = w272_permits
        pr_bundle_envelope["leases"] = w272_leases
        pr_bundle_envelope["environment_refs"] = w272_env_refs_dicts
    except Exception as exc:  # noqa: BLE001 — W272 wiring is best-effort
        print(
            f"[pr-replay] W272 authority/env stamping failed: {exc}",
            file=__import__("sys").stderr,
        )
        w272_env_refs_tuple = ()
    return w272_env_refs_tuple


def _stamp_q8_limitation_marker(pr_bundle_envelope: dict, gh_source_was_provided: bool) -> None:
    """W261: stamp the Q8 (accept) ``producer_not_available`` limitation.

    PR Replay has no approvals/accepted-risks harvester today. When nothing
    populated ``approvals`` / ``accepted_risks`` AND no GitHub review source
    was supplied, stamp the honest ``producer_not_available`` redaction reason
    (the data source does not exist yet -- not "checked, found nothing").
    Stays conditional for forward compatibility: a future approvals source
    populating those keys must NOT trip the marker (Q8 would then score
    ``complete`` and the limitation would be inaccurate).
    """
    _existing_approvals = pr_bundle_envelope.get("approvals") or []
    _existing_accepted = pr_bundle_envelope.get("accepted_risks") or []
    # W247b: when a GitHub review source WAS provided, "no approvals" means
    # "checked, no approval on head commit" — not "producer unavailable."
    if not _existing_approvals and not _existing_accepted and not gh_source_was_provided:
        _q8_redactions = list(pr_bundle_envelope.get("redactions") or [])
        if "producer_not_available" not in _q8_redactions:
            _q8_redactions.append("producer_not_available")
            pr_bundle_envelope["redactions"] = _q8_redactions


def _gather_w1279_config_hash_kwargs(active_run_id: str | None, pre_warnings: list[str]) -> dict:
    """W1279: gather the W1255-IMPL config-hash kwargs for the collector.

    Lifts the three hashes from the active run's meta.json and recomputes them
    on-disk so the collector's W1253 drift detector can fire. Best-effort:
    missing run/meta degrades to an empty dict (no drift flag, no crash); on a
    crash the failure is appended to ``pre_warnings`` so it surfaces in the
    envelope rather than being silently swallowed.
    """
    try:
        from roam.db.connection import find_project_root
        from roam.evidence.config_hashes_producer import gather_hash_kwargs

        try:
            _repo_root_for_hashes = find_project_root()
        except Exception:  # noqa: BLE001
            _repo_root_for_hashes = Path(".")
        return gather_hash_kwargs(_repo_root_for_hashes, active_run_id)
    except Exception as exc:  # noqa: BLE001 - hash wire-up is best-effort
        pre_warnings.append(f"_w1279_gather_hash_kwargs crashed: {exc}")
        return {}


def _merge_env_refs_into_packet(packet, env_refs_tuple: tuple):
    """W272: merge producer-derived env_refs into the packet post-collector.

    The collector rebuilds env_refs from caller kwargs and would otherwise drop
    the ``workspace`` ref that ``build_environment_refs`` always emits. Dedupes
    by ``(env_kind, env_id)`` so matching collector entries are not double-
    counted; collector rows keep canonical precedence, ours append behind.
    Returns ``packet`` unchanged when there is nothing to merge.
    """
    if not env_refs_tuple:
        return packet
    import dataclasses

    seen: set[tuple[str, str]] = {(r.env_kind, r.env_id) for r in packet.environment_refs}
    merged_env_refs = list(packet.environment_refs)
    for r in env_refs_tuple:
        key = (r.env_kind, r.env_id)
        if key in seen:
            continue
        seen.add(key)
        merged_env_refs.append(r)
    if len(merged_env_refs) != len(packet.environment_refs):
        return dataclasses.replace(
            packet,
            environment_refs=tuple(merged_env_refs),
        )
    return packet


def _collect_change_evidence(
    *,
    commit_range: str,
    commits: list[dict],
    summary: dict,
    by_detector: list[dict],
    generated_at: str,
    github_reviews_json: str | None = None,
    github_pr_number: int | None = None,
    github_reviews_gh: str | None = None,
    producer_warnings_out: list[str] | None = None,
):
    """Build a ``ChangeEvidence`` packet for a PR Replay window.

    W179 swap: delegates the bulk of construction to the canonical W176
    collector :func:`roam.evidence.collect_change_evidence`. This function
    is the **adapter** that turns PR Replay's postmortem-aggregate inputs
    (``commits``, ``summary``, ``by_detector``) into the envelope shapes
    the W176 collector consumes, then merges PR Replay-specific subjects
    back in.

    Mapping:

    * postmortem ``commits[*]`` -> ``changed_subjects`` (kind=commit),
      merged into the packet post-call (W176 emits subjects with
      ``kind="symbol"`` — keeping commit semantics requires the merge).
    * central findings registry + ``by_detector`` rows -> one synthetic
      findings envelope handed to ``collect_change_evidence``.
    * ``.roam/runs/`` directory -> one synthetic event stream so the
      collector populates ``run_ids`` for us.
    * git ``rev-parse HEAD`` -> ``commit_sha`` (caller kwarg).
    * git ``diff <range>`` -> ``diff_hash`` (caller kwarg).
    * postmortem verdict + tier-derived risk level -> top-level fields
      on the synthetic pr-bundle envelope.

    Returns a frozen ``ChangeEvidence`` with ``content_hash`` populated.
    Warnings emitted by the W176 collector are logged to stderr but never
    abort the command — see the module-level comment for the rationale.
    """
    import dataclasses
    import sys

    from roam.evidence import (
        EVIDENCE_SCHEMA_VERSION,
        ChangeEvidence,  # noqa: F401 — re-exported for downstream tests
        EvidenceSubject,
        collect_change_evidence,
    )

    # -- Commit subjects: kept local so kind="commit" survives the swap.
    # W176's ``_build_changed_subjects_from_affected`` hardcodes
    # ``kind="symbol"`` for everything it builds; we therefore do NOT
    # route commit data through ``affected_symbols``.
    commit_subjects: list[EvidenceSubject] = []
    for c in commits[:50]:  # cap so the packet stays small
        sha = c.get("sha") or c.get("short_sha")
        if not sha:
            continue
        commit_subjects.append(
            EvidenceSubject(
                kind="commit",
                qualified_name=f"commit:{sha}",
                extra={
                    "subject": (c.get("subject") or "")[:120],
                    "date": c.get("date"),
                    "high": int(c.get("high", 0) or 0),
                    "medium": int(c.get("medium", 0) or 0),
                },
            )
        )

    # -- Findings envelope: combine registry rows + per-commit kinds.
    # Shape mirrors a ``roam findings list`` envelope: a top-level
    # ``findings: [...]`` array. The W176 collector flattens it directly.
    findings_rows: list[dict] = []
    for row in _collect_findings_from_registry(limit=200):
        findings_rows.append(
            {
                "id": row.get("finding_id_str"),
                "detector": row.get("source_detector"),
                "subject_kind": row.get("subject_kind"),
                "subject_id": row.get("subject_id"),
                "claim": row.get("claim"),
                "confidence": row.get("confidence"),
                "source_version": row.get("source_version"),
            }
        )
    for row in by_detector:
        findings_rows.append(
            {
                "detector": row["detector"],
                "total_findings": row["total_findings"],
                "commits_with_finding": row["commits_with_finding"],
                "source": "postmortem-aggregate",
            }
        )
    findings_envelope = {"findings": findings_rows}

    # -- Run-event stream: synthesised from the filesystem listing so
    # the W176 collector can populate ``run_ids`` via its standard path.
    run_id_list = _collect_run_ids(commit_range)
    run_events = [{"run_id": rid} for rid in run_id_list]

    head_sha = _git_head_sha()
    diff_hash = _diff_hash_for_range(commit_range)

    # -- Risk level: derived from the postmortem summary, aligned to the
    # CLAIM_SEVERITIES vocabulary the collector and renderer expect.
    total_high = int(summary.get("total_high", 0) or 0)
    total_medium = int(summary.get("total_medium", 0) or 0)
    if total_high > 0:
        risk_level = "high"
    elif total_medium > 0:
        risk_level = "medium"
    else:
        risk_level = "low"
    # W641-followup-F — also emit the canonical mirror so the W176 collector
    # (which now prefers ``risk_level_canonical`` over the legacy
    # ``risk_level`` synthesis path) lifts THIS verdict-derived value through
    # the canonical priority lane. Without the mirror, the collector lineage
    # helper would classify each replay packet as ``verdict_text_legacy``
    # even though the producer already knows the canonical bucket — the
    # mirror keeps producer→packet projection canonical end-to-end.
    risk_level_canonical = normalize_risk_level(risk_level) or "low"

    # -- PR-bundle-shaped envelope: carries identity + verdict + risk
    # level. The W176 collector reads top-level ``verdict`` / ``risk_level``
    # / ``run_ids`` / ``mode`` / ``commit_sha`` / ``git_range`` /
    # ``diff_hash``; we set the ones we have and let it ignore the rest.
    pr_bundle_envelope = {
        "verdict": summary.get("verdict") or "no verdict",
        "risk_level": risk_level,
        "risk_level_canonical": risk_level_canonical,
        "timestamps": {"completed_at": generated_at},
    }

    # W260: stamp the W189-shape actor block (best-effort; secret-scrubbed).
    _stamp_actor_block_on_envelope(pr_bundle_envelope)

    # W272: stamp permits/leases/env_refs; capture env_refs for the post-
    # collector merge (best-effort; always-emit keys for a stable shape).
    w272_env_refs_tuple = _stamp_authority_env_on_envelope(pr_bundle_envelope, commit_range)

    # -- W223/W267/W246 — best-effort producer inputs for the W199 kwargs.
    # Each gatherer appends to ``pre_warnings`` so producer-level issues
    # never abort the replay; the collector merges them into its own list.
    producer_inputs = _gather_pr_replay_producer_inputs(
        commit_range=commit_range,
        commits=commits,
        pr_bundle_envelope=pr_bundle_envelope,
    )
    pre_warnings = producer_inputs.pre_warnings
    active_run_id = producer_inputs.active_run_id
    extra_policy_decisions = list(producer_inputs.extra_policy_decisions)

    # -- W247b — GitHub review harvester. Runs BEFORE the W261 emitter so
    # an APPROVED-on-head review populates ``pr_bundle_envelope["approvals"]``
    # (lifting Q8 to ``complete``) and a CHANGES_REQUESTED review appends a
    # ``deny`` PolicyDecision via ``extra_policy_decisions``. When a review
    # source was supplied but yielded no approvals, we deliberately SKIP the
    # ``producer_not_available`` stamp below — the operator checked, the
    # data was empty, that is not the same as "producer unavailable."
    gh_approval_dicts, gh_policy_dicts, gh_source_was_provided = _gather_github_reviews(
        fixture_path=github_reviews_json,
        gh_spec=github_reviews_gh,
        pr_number=github_pr_number,
        head_commit_sha=head_sha,
        warnings=pre_warnings,
    )
    if gh_approval_dicts:
        existing = list(pr_bundle_envelope.get("approvals") or [])
        existing.extend(gh_approval_dicts)
        pr_bundle_envelope["approvals"] = existing
    if gh_policy_dicts:
        extra_policy_decisions.extend(gh_policy_dicts)

    # W261: stamp the Q8 ``producer_not_available`` limitation marker when no
    # approvals source populated the envelope (best-effort; conditional).
    _stamp_q8_limitation_marker(pr_bundle_envelope, gh_source_was_provided)

    # W1279: config-hash drift kwargs (best-effort; crashes -> pre_warnings).
    _hash_kwargs = _gather_w1279_config_hash_kwargs(active_run_id, pre_warnings)

    packet, warnings = collect_change_evidence(
        pr_bundle_envelope=pr_bundle_envelope,
        findings_envelopes=[findings_envelope],
        run_events=run_events,
        audit_trail_envelope=producer_inputs.audit_trail_envelope,
        rules_envelopes=producer_inputs.rules_envelopes,
        vuln_reach_envelopes=producer_inputs.vuln_reach_envelopes,
        test_impact_envelopes=producer_inputs.test_impact_envelopes,
        cga_envelopes=producer_inputs.cga_envelopes,
        mcp_receipts_dir=producer_inputs.mcp_receipts_dir,
        extra_policy_decisions=extra_policy_decisions,
        commit_sha=head_sha,
        git_range=commit_range,
        diff_hash=diff_hash,
        mode="pr_replay",
        schema_version=EVIDENCE_SCHEMA_VERSION,
        **_hash_kwargs,
    )
    warnings = list(pre_warnings) + list(warnings)

    # W590: thread producer-level warnings into the caller-supplied
    # ``producer_warnings_out`` bucket so the pr-replay envelope can
    # disclose silent-fallback markers (e.g. ``leases:
    # project_root_not_found``) at the envelope level — not just on
    # stderr. The stderr loop below still fires for operators tailing
    # logs; envelope plumbing is the additive new channel.
    if producer_warnings_out is not None:
        producer_warnings_out.extend(warnings)

    # Warnings -> stderr, one line each. Surface them so an operator can
    # spot upstream-envelope drift, but never fail the command.
    for w in warnings:
        # Deliberate stderr write — surfacing collector warnings without
        # failing the command. click.echo to stderr would be equivalent but
        # less explicit about the intent.
        print(f"[pr-replay] evidence-collector warning: {w}", file=sys.stderr)  # noqa: T201

    # Merge commit subjects back in (W176 produced none — we passed no
    # ``affected_symbols``), then re-stamp the content hash so the on-disk
    # bytes match the merged contents.
    if commit_subjects:
        packet = dataclasses.replace(
            packet,
            changed_subjects=tuple(commit_subjects) + tuple(packet.changed_subjects),
        )

    # W272: merge producer-derived env_refs (workspace ref) into the packet.
    packet = _merge_env_refs_into_packet(packet, w272_env_refs_tuple)

    # Stable per-(range, generation moment) evidence_id — overrides the
    # collector-derived one so the value stays human-readable for tickets.
    packet = dataclasses.replace(
        packet,
        evidence_id=f"pr-replay:{commit_range}:{generated_at}".replace(" ", "_"),
    )
    return packet.with_content_hash()


# ---------------------------------------------------------------------------
# W217 — hostile-input sanitisation for Markdown table cells.
#
# Markdown tables are pipe-delimited; an unescaped ``|`` in a cell value
# silently introduces a new column and breaks every row of the table.
# Newlines / CR break the cell entirely (a row spans one physical line in
# CommonMark). Control characters (NUL, BEL, ANSI escape sequences) can
# corrupt downstream renderers or terminals. Markdown headers (``# ...``)
# inside a cell can be promoted to real headings by buggy renderers.
# HTML tags / Markdown link syntax can inject executable content into
# Markdown -> HTML pipelines.
#
# These three helpers normalise free-form text into table-cell-safe
# strings. ``_escape_cell_text`` is for un-backticked columns
# (display_name, granted_by, ...); ``_escape_cell_code`` is for columns
# wrapped in backticks (actor_id, authority_id, env_id, detector, ...);
# ``_collapse_to_line`` is the shared first stage (newline / control-char
# normalisation) that both helpers run.
#
# Rationale on the substitution choice (``|`` -> ``/``):
#
# * The existing W191 renderer already uses ``replace("|", "/")`` on
#   display_name and granted_by columns. Keeping the same substitution
#   keeps the in-file convention single-sourced.
# * ``\|`` (backslash-pipe) is the formally correct CommonMark escape,
#   but mixed renderer support makes it less reliable than substitution
#   for a buyer-facing report.
# * The substitution is loud — a stray ``/`` in an actor ID is visibly
#   wrong to a reviewer, which is the correct affordance: we want the
#   reviewer to notice the suspicious value.
#
# DO NOT add a Markdown sanitisation library. The stdlib substitutions
# below are sufficient for the threat model (a hostile commit author /
# rogue agent populating evidence fields) and keep the dependency
# surface zero. Per W217 directive.
# ---------------------------------------------------------------------------


# Maximum length for one rendered cell. Truncation protects table layout
# from a single 10 KB actor_id blowing out terminal / report width. The
# truncation marker is ``...`` so a reviewer sees the value was clipped.
_MAX_CELL_LEN: int = 200

# Sentinel rendered when a cell value is empty / whitespace-only. Without
# this, the table-column count would collapse silently (Markdown
# normalises empty cells in some renderers).
_EMPTY_CELL_SENTINEL: str = "<empty>"


def _collapse_to_line(s: str) -> str:
    """Collapse a hostile string to one displayable line.

    Pipeline:

    1. Coerce to ``str`` defensively (callers may pass ``None`` via
       upstream ``.get()``; we want a sentinel, not a crash).
    2. Strip ANSI escape sequences (``\\x1b[...m`` and friends) — they
       corrupt terminal output and confuse downstream renderers.
    3. Replace newline (``\\n``) and carriage-return (``\\r``) with a
       single space so the cell stays on one physical Markdown line.
    4. Strip every other C0 / C1 control character (NUL through ESC,
       plus DEL). Preserving the surrounding spaces so word boundaries
       survive.
    5. Strip Unicode BIDI / RTL / zero-width / direction-override
       characters. These are invisible-but-dangerous: a hostile actor
       can hide content using ``\\u200b`` (ZWSP) or reverse the visual
       order using ``\\u202e`` (RTL override). Replacing with the
       literal codepoint name (e.g. ``<U+202E>``) keeps the signal
       visible to the reviewer.
    """
    if s is None:
        return ""
    text = str(s)

    # ANSI escape sequences: ``\x1b[`` followed by parameter bytes ending
    # in a letter, OR ``\x1b]...\x07`` OSC sequences. The substitution is
    # deliberately wide — we'd rather drop a legitimate escape than let
    # one through into a buyer report.
    import re as _re

    text = _re.sub(r"\x1b\[[0-9;?]*[ -/]*[@-~]", "", text)
    text = _re.sub(r"\x1b\][^\x07]*\x07", "", text)
    text = text.replace("\x1b", "")

    # Newlines / CR -> single space.
    text = text.replace("\r\n", " ").replace("\n", " ").replace("\r", " ")

    # BIDI / direction-override / zero-width chars: surface as a visible
    # codepoint marker so a reviewer can see the suspicious content.
    _BIDI_CHARS = (
        "​",  # zero-width space
        "‌",  # zero-width non-joiner
        "‍",  # zero-width joiner
        "‎",  # LTR mark
        "‏",  # RTL mark
        "‪",  # LTR embedding
        "‫",  # RTL embedding
        "‬",  # pop directional formatting
        "‭",  # LTR override
        "‮",  # RTL override
        "⁦",  # LTR isolate
        "⁧",  # RTL isolate
        "⁨",  # first-strong isolate
        "⁩",  # pop directional isolate
        "﻿",  # zero-width no-break space (BOM)
    )
    for ch in _BIDI_CHARS:
        text = text.replace(ch, f"<U+{ord(ch):04X}>")

    # Strip remaining C0 / C1 control chars (everything in 0x00-0x1F and
    # 0x7F, since we already handled \n / \r / \x1b above).
    text = "".join(c for c in text if c >= " " and c != "\x7f")

    return text


def _escape_cell_text(s: str | None) -> str:
    """Escape ``s`` for use as a Markdown table cell (no backticks).

    Use this for cells that render as plain prose: ``display_name``,
    ``granted_by``, rationale strings, etc. Applies the full pipeline:

    * collapse newlines / strip control chars / mark BIDI;
    * substitute ``|`` -> ``/`` so the cell stays inside its column;
    * neutralise Markdown link / image syntax (``[text](url)``,
      ``![alt](url)``) by escaping the opening brackets — buggy
      renderers can otherwise navigate a ``javascript:`` URL;
    * neutralise leading ``#`` and ``>`` so the cell can't be promoted
      to a header / blockquote even if the renderer is confused;
    * truncate to ``_MAX_CELL_LEN`` with a ``...`` marker;
    * fall back to ``<empty>`` for empty / whitespace-only input so the
      table column count is preserved.
    """
    text = _collapse_to_line(s)

    # Pipe substitution — match the existing in-file convention.
    text = text.replace("|", "/")

    # Markdown link / image: escape the opening bracket so the renderer
    # treats ``[click](javascript:...)`` as literal text.
    text = text.replace("[", "\\[").replace("]", "\\]")

    # Strip leading whitespace so the next "leading char" check sees the
    # actual first non-space character (otherwise ``"   ## hi"`` slips
    # through). We then re-check for empty.
    stripped = text.lstrip()
    if stripped:
        # Neutralise leading ``#`` (heading) and ``>`` (blockquote).
        if stripped[0] in "#>":
            stripped = "\\" + stripped
        text = stripped

    # Truncate after escapes so the budget bounds the rendered string
    # length, not the pre-escape length.
    if len(text) > _MAX_CELL_LEN:
        text = text[: _MAX_CELL_LEN - 3] + "..."

    if not text or not text.strip():
        return _EMPTY_CELL_SENTINEL
    return text


def _escape_cell_code(s: str | None) -> str:
    """Escape ``s`` for use inside a backtick-wrapped Markdown table cell.

    Use this for ID columns wrapped in backticks (``actor_id``,
    ``authority_id``, ``env_id``, ``detector``, ``confidence``). Pipes
    are still substituted (``|`` inside a code-span can still break
    table column count in some renderers), and stray backticks are
    escaped (a ``\\`\\`\\` injection`` value would otherwise terminate
    the code-span and inject Markdown).

    The result is the *inner* string; the caller wraps it in backticks.
    For an empty value the helper returns ``empty`` (without backticks)
    so the caller can render ``\\`empty\\``` consistently.
    """
    text = _collapse_to_line(s)

    # Pipe -> / inside a backtick span as well; some Markdown table
    # parsers still terminate the cell on a raw ``|`` even when wrapped
    # in backticks.
    text = text.replace("|", "/")

    # Backtick: escape so the code-span doesn't terminate early. Using a
    # backslash-backtick keeps the value legible.
    text = text.replace("`", "\\`")

    if len(text) > _MAX_CELL_LEN:
        text = text[: _MAX_CELL_LEN - 3] + "..."

    if not text or not text.strip():
        return _EMPTY_CELL_SENTINEL
    return text


def _render_banner_markdown(evidence) -> str:
    """Thin wrapper around :func:`roam.evidence.banner.render_banner_markdown`.

    Kept here so the import is local to the renderer call-site (the
    rest of this module follows the same lazy-import discipline to
    keep ``roam --help`` cold-start fast).
    """
    from roam.evidence.banner import render_banner_markdown

    return render_banner_markdown(evidence)


def _banner_envelope_block(evidence) -> dict:
    """Thin wrapper for :func:`roam.evidence.banner.banner_envelope_block`."""
    from roam.evidence.banner import banner_envelope_block

    return banner_envelope_block(evidence)


def _append_evidence_header(out: list[str], evidence) -> None:
    sha = evidence.commit_sha or "no-sha"
    sha_short = sha[:8] if sha and sha != "no-sha" else "no-sha"
    out.append(f"# PR Replay — {sha_short}")
    out.append("")
    out.append(_render_banner_markdown(evidence))
    out.append("")
    out.append(f"**Verdict**: {evidence.verdict or 'no verdict'}")
    out.append(f"**Risk level**: {evidence.risk_level or 'unknown'}")
    out.append(f"**Mode**: {evidence.mode or 'pr_replay'}")
    out.append(f"**Range**: `{evidence.git_range or 'HEAD~5..HEAD'}`")
    run_ids_text = ", ".join(evidence.run_ids) if evidence.run_ids else "_(no runs recorded)_"
    out.append(f"**Run IDs**: {run_ids_text}")
    out.append(f"**Schema**: {evidence.schema_version}")
    out.append("")


def _append_evidence_scope(out: list[str], *, evidence, commits: list[dict]) -> None:
    out.append("## Scope")
    out.append("")
    files_seen: set[str] = set()
    for commit in commits:
        for file_name in commit.get("files") or []:
            files_seen.add(str(file_name))
    out.append(f"- {len(evidence.changed_subjects)} symbols changed across {len(files_seen)} files")
    out.append(f"- Diff hash: `{evidence.diff_hash or 'unavailable'}`")
    out.append("")


def _append_changed_subjects_table(out: list[str], evidence) -> None:
    out.append("## Changed subjects (top 20)")
    out.append("")
    out.append("| Subject | Kind | Blast radius |")
    out.append("|---|---|---|")
    if not evidence.changed_subjects:
        out.append("| _(none)_ | — | — |")
    else:
        for subj in evidence.changed_subjects[:20]:
            extra = subj.extra or {}
            blast = extra.get("high", 0) + extra.get("medium", 0)
            qn = _escape_cell_code(subj.qualified_name)
            kind = _escape_cell_text(subj.kind)
            out.append(f"| `{qn}` | {kind} | {blast} |")
    out.append("")


def _append_agentic_frame(out: list[str], evidence) -> None:
    out.append("## Actors")
    out.append("")
    out.append(_render_actors_section(evidence))
    out.append("")
    out.append("## Authorities")
    out.append("")
    out.append(_render_authorities_section(evidence))
    out.append("")
    out.append("## Environment")
    out.append("")
    out.append(_render_environment_section(evidence))
    out.append("")


def _finding_pair_counts(findings) -> dict[tuple[str, str], int]:
    pair_counts: dict[tuple[str, str], int] = {}
    for finding in findings:
        det = str(finding.get("detector") or "unknown")
        conf = str(finding.get("confidence") or finding.get("source") or "aggregate")
        key = (det, conf)
        pair_counts[key] = pair_counts.get(key, 0) + int(finding.get("total_findings", 1) or 1)
    return pair_counts


def _append_findings_summary(out: list[str], evidence) -> None:
    out.append(f"## Findings ({len(evidence.findings)})")
    out.append("")
    out.append("| Detector | Confidence | Count |")
    out.append("|---|---|---|")
    pair_counts = _finding_pair_counts(evidence.findings)
    if not pair_counts:
        out.append("| _(none)_ | — | 0 |")
    else:
        for (det, conf), count in sorted(pair_counts.items(), key=lambda kv: -kv[1])[:20]:
            safe_det = _escape_cell_code(det)
            safe_conf = _escape_cell_text(conf)
            out.append(f"| `{safe_det}` | {safe_conf} | {count} |")
    out.append("")


def _append_tests_summary(out: list[str], evidence) -> None:
    out.append("## Tests")
    out.append("")
    out.append(f"- Required: {len(evidence.tests_required)}")
    out.append(f"- Run: {len(evidence.tests_run)}")
    status = (
        "all required tests recorded as run"
        if len(evidence.tests_run) >= len(evidence.tests_required) and evidence.tests_required
        else "no test data attached to this replay"
    )
    out.append("- Status: " + status)
    out.append("")


def _append_approval_items(out: list[str], approvals) -> None:
    if not approvals:
        return
    out.append("Approvals:")
    for approval in approvals[:10]:
        out.append(f"- {_escape_cell_text(str(approval))}")


def _append_accepted_risk_items(out: list[str], accepted_risks, *, need_separator: bool) -> None:
    if not accepted_risks:
        return
    if need_separator:
        out.append("")
    out.append("Accepted risks:")
    for risk in accepted_risks[:10]:
        out.append(f"- {_escape_cell_text(str(risk))}")


def _append_approvals_section(out: list[str], evidence) -> None:
    out.append("## Approvals and accepted risks")
    out.append("")
    if not evidence.approvals and not evidence.accepted_risks:
        out.append("_No approvals or accepted risks recorded for this replay window._")
    else:
        _append_approval_items(out, evidence.approvals)
        _append_accepted_risk_items(
            out,
            evidence.accepted_risks,
            need_separator=bool(evidence.approvals),
        )
    out.append("")


def _append_recurring_risk_classes(out: list[str], recurring: list[dict]) -> None:
    out.append("### Recurring risk classes")
    out.append("")
    if not recurring:
        out.append("_None recurred across this window._")
    else:
        out.append("| Class | Total findings | PRs with this finding |")
        out.append("|---|---:|---:|")
        for row in recurring[:5]:
            safe_class = _escape_cell_code(row["class"])
            out.append(f"| `{safe_class}` | {row['total_findings']} | {row['commits_with_finding']} |")
    out.append("")


def _append_suggested_rules(out: list[str], rules_yaml: str | None) -> None:
    out.append("### Suggested .roam/rules.yml")
    out.append("")
    if rules_yaml:
        out.append("```yaml")
        out.append(rules_yaml.rstrip())
        out.append("```")
    else:
        out.append("_No rule templates matched the recurring detectors._")
    out.append("")


def _append_suggested_gates(out: list[str], gates: list[dict]) -> None:
    out.append("### Suggested CI gates")
    out.append("")
    if gates:
        out.append("```bash")
        for gate in gates[:5]:
            out.append(f"# {gate.get('rationale', '')}")
            out.append(gate.get("gate", ""))
        out.append("```")
    else:
        out.append("_No gate suggestions available._")
    out.append("")


def _append_blocked_review_table(out: list[str], would_block: list[dict]) -> None:
    out.append("### What Review would have blocked")
    out.append("")
    if not would_block:
        out.append("_No high-severity findings recurred. Review's BLOCK verdict would not have fired on this window._")
    else:
        out.append("| SHA | Date | Subject | High findings | Rationale |")
        out.append("|---|---|---|---:|---|")
        for blocked in would_block[:5]:
            sha = _escape_cell_code(blocked.get("sha", "?"))
            date = _escape_cell_text(blocked.get("date", "?"))
            subj = _escape_cell_text(blocked.get("subject", "") or "")
            rationale = _escape_cell_text(blocked.get("rationale", "") or "")
            out.append(f"| `{sha}` | {date} | {subj} | {blocked.get('high_findings', 0)} | {rationale} |")
    out.append("")


def _append_review_configuration(out: list[str], *, evidence, review_suggestions: dict | None) -> None:
    out.append("## Suggested Review configuration")
    out.append("")
    total_findings = len(evidence.findings)
    if review_suggestions is None:
        out.append(
            "_No recurring detector hits in this replay window — no Review "
            "configuration to suggest. Run a longer range (`--tier deep` or "
            "`--range HEAD~90..HEAD`) for a more representative sample._"
        )
        out.append("")
        return
    would_block = review_suggestions.get("what_review_would_have_blocked") or []
    out.append(
        f"Based on this replay's findings, the following Review configuration "
        f"would have caught {len(would_block)} of {total_findings} findings "
        f"before merge:"
    )
    out.append("")
    _append_recurring_risk_classes(out, review_suggestions.get("recurring_risk_classes") or [])
    _append_suggested_rules(out, review_suggestions.get("suggested_roam_rules_yml"))
    _append_suggested_gates(out, review_suggestions.get("suggested_ci_gates") or [])
    _append_blocked_review_table(out, would_block)


def _append_evidence_limitations_section(out: list[str], evidence) -> None:
    out.append("## Evidence limitations")
    out.append("")
    out.append(_render_evidence_limitations(evidence))
    out.append("")


def _append_evidence_footer(out: list[str]) -> None:
    out.append("---")
    out.append("")
    out.append(
        "*Per the agentic-assurance crosswalk, this report **supports evidence "
        "for** governance review and **maps to** change-management controls. "
        "It does not certify compliance with SOC 2, ISO 42001, the EU AI Act, "
        "or any other framework — the conformity assessment remains with the "
        "customer.*"
    )
    out.append("")


def _render_evidence_markdown(
    *,
    evidence,
    commits: list[dict],
    by_detector: list[dict],
    review_suggestions: dict | None,
) -> str:
    """Render the Markdown companion report from a ``ChangeEvidence`` packet.

    Mirrors ``templates/audit-report/pr-replay-template.md`` heading-by-
    heading so a diff against the template surfaces drift. Pure function —
    no I/O, no clock, no env — so the output is reproducible from inputs.
    """
    out: list[str] = []

    # Header
    _append_evidence_header(out, evidence)

    # Scope
    _append_evidence_scope(out, evidence=evidence, commits=commits)

    # Changed subjects table (top 20)
    _append_changed_subjects_table(out, evidence)

    # W191 — Agentic-assurance identity + authority + environment frame.
    # Per the crosswalk memo ((internal memo)),
    # Roam's distinguishing claim is "identity + authority + evidence". These
    # three sections render that frame BEFORE findings so a reviewer reading
    # top-to-bottom sees WHO acted under WHAT authority in WHICH environment
    # before they see WHAT was found. All three sections are unconditional —
    # an empty packet renders a "no X recorded" sentinel rather than a
    # missing heading, so a Markdown diff against the template is loud.
    _append_agentic_frame(out, evidence)

    # Findings summary by detector + confidence
    _append_findings_summary(out, evidence)

    # Tests section — we don't have test-execution data in the packet
    # yet (W176 will fold it in), so we surface declared zero counts
    # explicitly rather than skipping the section.
    _append_tests_summary(out, evidence)

    # Approvals / accepted risks
    _append_approvals_section(out, evidence)

    # Suggested Review configuration
    _append_review_configuration(out, evidence=evidence, review_suggestions=review_suggestions)

    # Evidence limitations (W185 — agentic-assurance crosswalk §"Build deltas"
    # item 6). Always emits the section; item 6 (non-certification) is
    # unconditional, items 1-5 are gated on packet contents.
    _append_evidence_limitations_section(out, evidence)

    _append_evidence_footer(out)
    return "\n".join(out)


def _render_evidence_limitations(evidence) -> str:
    """Compute the bulleted limitations list for the Markdown template.

    Wave W185 — implements §"Build deltas" item 6 of the agentic-assurance
    crosswalk memo (``(internal memo)``).

    W284 — limitations are now DERIVED from packet structure rather than
    hand-written boilerplate. Three sources contribute bullets, in this
    order:

    1. Per-question gaps from :meth:`ChangeEvidence.evidence_completeness`
       (``missing`` and ``partial`` Q's, sorted Q1..Q8). Each Q-gap names
       the evidence question and the reviewer-facing implication.
    2. Redaction reasons from :attr:`ChangeEvidence.redactions` (tuple
       iteration order is preserved so a deterministic packet produces a
       deterministic bullet list).
    3. Trust-tier warnings for ``ActorRef`` entries whose ``trust_tier``
       is ``self_reported_agent`` or ``unknown`` (W211).

    The non-certification statement (item 6 in the original memo
    enumeration) is always appended as the final bullet — that one
    sentence is unconditional because it speaks to what the report is,
    not what's missing from it.

    If the three derived sources are all empty, an italic "no
    limitations detected" sentinel is emitted (followed by the
    non-certification bullet on its own line). This keeps the rendered
    section honest on a clean packet rather than emitting a misleading
    list of absent items.

    Pure function. Reads the packet through ``getattr`` so the renderer
    works whether W182's identity refs (``actor_refs``) are populated or
    not — empty tuples and missing attributes both collapse to "no
    actor identity attached." Deterministic — same packet input -> same
    bullet ordering.
    """
    bullets = list(_derive_limitations(evidence))

    # Non-certification bullet — always appended. This is a statement
    # about what the report IS, not a derived gap, so it lives outside
    # the derivation helper.
    non_cert = (
        "- **Non-certification**: this report **supports evidence for** "
        "governance review and **maps to** change-management controls. "
        "It is not certification of compliance with any framework "
        "(SOC 2 / ISO 42001 / EU AI Act / etc.). Mapping to specific "
        "framework controls and the conformity assessment remain with "
        "the customer."
    )

    if not bullets:
        # W284 — explicit "nothing to report" sentinel so the section
        # stays honest on a STRONG, pristine packet. Without this, the
        # only bullet would be the non-cert one, which reads as "the
        # only thing we couldn't certify is certification itself" —
        # technically correct but unhelpful to the reviewer.
        return "_No evidence limitations detected._\n\n" + non_cert

    bullets.append(non_cert)
    return "\n".join(bullets)


# ---------------------------------------------------------------------------
# W284 — derive limitations bullets from packet structure
#
# The renderer above is a thin wrapper that prepends the derived bullets
# to the always-emitted non-certification statement. The derivation
# logic lives here so it can be unit-tested in isolation and so the
# three-source ordering (Q-gaps -> redactions -> trust warnings) is
# explicit and reviewable.
# ---------------------------------------------------------------------------


# Q-id -> (human-readable name, reviewer-facing explanation when missing).
# The explanation is the consequence FROM THE REVIEWER'S PERSPECTIVE, not
# a re-statement of the field name. Tuned for the "reading the report"
# audience, not for the "writing producer code" audience.
_Q_GAP_LABELS: dict[str, tuple[str, str]] = {
    "Q1": (
        "actor",
        "no actor identity recorded; the change cannot be attributed to a specific human, agent, or MCP client.",
    ),
    "Q2": (
        "authority",
        "no authority recorded; the report does not name the "
        "mode, permit, approval, or policy rule that authorised "
        "this change.",
    ),
    "Q3": (
        "context_read",
        "no `context_refs[]` entries; the reviewer cannot see "
        "which files or symbols the actor read while preparing "
        "the change.",
    ),
    "Q4": (
        "changed_subjects",
        "no `changed_subjects[]` entries; the packet does not name what changed.",
    ),
    "Q5": (
        "risk",
        "no `risk_level` recorded; the report does not classify the risk introduced by the change.",
    ),
    "Q6": (
        "policy",
        "no `policy_decisions[]` entries; the report does not "
        "show which policy rules or controls evaluated against "
        "the change.",
    ),
    "Q7": (
        "verify",
        "no `tests_run[]` or `artifacts[]` entries; nothing external verifies that the change was tested.",
    ),
    "Q8": (
        "accept",
        "no `approvals[]` or `accepted_risks[]` entries; the report does not show who accepted residual risk.",
    ),
}


# Redaction-reason -> reviewer-facing explanation. Mirrors the
# REDACTION_REASONS enumeration in ``roam.evidence._vocabulary`` plus the
# W261 ``producer_not_available`` entry. Adding a new redaction reason
# WITHOUT adding an explanation here causes the derived bullet to fall
# back to a generic phrasing — see :func:`_derive_limitations`.
_REDACTION_EXPLANATIONS: dict[str, str] = {
    "secret": ("secrets scrubbed by collector hardening (W232/W241/W249)"),
    "pii": "personally identifiable information removed",
    "sensitive_content": "policy-sensitive content removed",
    "size_limit": ("packet exceeded the 256 KiB budget; some fields were truncated (W280)"),
    "policy": "content removed by an explicit policy rule",
    "user_opt_in_required": ("raw context requires explicit user enablement before appearing in the report"),
    "machine_local_path": ("local filesystem path redacted to avoid leaking the developer's working directory"),
    "schema_strict": ("content stripped to keep the packet schema-strict"),
    "producer_not_available": (
        "no producer is wired for this evidence type yet — the data source does not exist, it is not masked (W261)"
    ),
}


def _evidence_completeness_or_none(evidence) -> dict | None:
    if not hasattr(evidence, "evidence_completeness"):
        return None
    try:
        return evidence.evidence_completeness()
    except Exception:  # noqa: BLE001 — best-effort limitations rendering
        return None


def _partial_q_gap_detail(evidence, q_key: str) -> str:
    # Q8 + producer_not_available is the canonical partial path today
    # (W261). Surface that wording when present so the reader sees the
    # SAME explanation in both the Q-gap bullet and the redaction bullet.
    redactions = getattr(evidence, "redactions", ()) or ()
    if q_key == "Q8" and "producer_not_available" in redactions:
        return (
            "limitation declared via `producer_not_available` redaction; "
            "no real approval data available from this producer."
        )
    return "the packet carries weak signal but the corroborating structured field is empty."


def _q_gap_limitation(evidence, q_key: str, state: str) -> str | None:
    if state not in ("missing", "partial"):
        return None
    name, why = _Q_GAP_LABELS[q_key]
    if state == "partial":
        return f"- **{q_key} ({name}): PARTIAL** — {_partial_q_gap_detail(evidence, q_key)}"
    return f"- **{q_key} ({name}): MISSING** — {why}"


def _q_gap_limitations(evidence) -> list[str]:
    completeness = _evidence_completeness_or_none(evidence)
    if not completeness:
        return []
    bullets: list[str] = []
    for q_num in range(1, 9):
        q_key = f"Q{q_num}"
        bullet = _q_gap_limitation(evidence, q_key, completeness.get(q_key))
        if bullet is not None:
            bullets.append(bullet)
    return bullets


def _redaction_limitations(evidence) -> list[str]:
    bullets: list[str] = []
    redactions = getattr(evidence, "redactions", ()) or ()
    for reason in redactions:
        explanation = _REDACTION_EXPLANATIONS.get(
            reason,
            "reason not in the documented vocabulary; consult the producer that emitted it.",
        )
        bullets.append(f"- **Redacted content: `{reason}`** — {explanation}")
    return bullets


def _actor_trust_limitation(ref) -> str | None:
    tier = getattr(ref, "trust_tier", None)
    if tier not in ("self_reported_agent", "unknown"):
        return None
    # W285-followup: actor_id originates from untrusted producers
    # (rogue agents, mistaken commit authors, fuzzy MITM) so it must
    # flow through the same hostile-input pipeline as every other
    # backtick-wrapped ID column in this report.
    actor_id = _escape_cell_code(getattr(ref, "actor_id", "?"))
    safe_tier = _escape_cell_code(tier)
    corroboration = "no CI-attested or git-author corroboration available"
    return f'- **Actor identity unverified**: `agent_id="{actor_id}"` classified as `{safe_tier}`; {corroboration}.'


def _actor_trust_limitations(evidence) -> list[str]:
    bullets: list[str] = []
    actor_refs = getattr(evidence, "actor_refs", ()) or ()
    for ref in actor_refs:
        bullet = _actor_trust_limitation(ref)
        if bullet is not None:
            bullets.append(bullet)
    return bullets


def _derive_limitations(evidence) -> tuple[str, ...]:
    """Derive limitation bullets from packet structure.

    W284 — replaces the hand-written limitation list with a pure
    projection of three packet sources, in deterministic order:

    1. Per-Q gaps from :meth:`ChangeEvidence.evidence_completeness`.
       For each ``Q1..Q8`` whose state is ``missing`` or ``partial``,
       emit one bullet. Sorted Q1 -> Q8 for stability across runs.
    2. Redaction reasons from :attr:`ChangeEvidence.redactions`. One
       bullet per reason, preserving the tuple's iteration order (the
       tuple is part of the canonical packet shape, so its order is
       already deterministic).
    3. Trust-tier warnings. For each ``ActorRef`` whose ``trust_tier``
       is ``self_reported_agent`` or ``unknown``, emit one bullet
       naming the actor id and the tier. Iterated in ``actor_refs``
       order, which is the canonical packet order.

    Returns a tuple of pre-formatted Markdown bullet strings (each
    line starts with ``- ``). The non-certification bullet is NOT
    included here — that lives in :func:`_render_evidence_limitations`
    which is the only public seam.

    Pure function. Reads ``evidence`` via ``getattr`` so duck-typed
    fixtures work; never mutates the packet.
    """
    bullets: list[str] = []
    bullets.extend(_q_gap_limitations(evidence))
    bullets.extend(_redaction_limitations(evidence))
    bullets.extend(_actor_trust_limitations(evidence))
    return tuple(bullets)


# ---------------------------------------------------------------------------
# W191 — Actors / Authorities / Environment renderers.
#
# These three helpers materialise the W182 agentic-assurance ref tuples
# (``actor_refs`` / ``authority_refs`` / ``environment_refs``) as Markdown
# tables in the PR Replay report. Each helper is a pure function so the
# renderer stays deterministic.
#
# Fallback chain: when the collector has populated the refs (W190),
# these helpers render rich tables. When the collector hasn't populated
# them (legacy bundles, partial-evidence paths), the actors helper falls
# back to the flat ``agent_id`` / ``human_actor`` fields so reviewers
# still see SOME identity surface; authorities and environment fall back
# to a "no X recorded" sentinel because they have no legacy scalar
# equivalent on ``ChangeEvidence``.
# ---------------------------------------------------------------------------


def _format_actors_table(rows: list[tuple[str, str, str]]) -> str:
    """Format a list of (kind, id, display) tuples as a Markdown table.

    W217 hostile-input safety: every cell is run through the shared
    ``_escape_cell_*`` helpers so pipes / newlines / control chars /
    BIDI overrides / Markdown link injections / overlong values cannot
    break the table layout or inject report structure. The id columns
    are wrapped in backticks, so use ``_escape_cell_code``; the display
    column is prose, so use ``_escape_cell_text``.
    """
    lines = ["| Kind | ID | Display |", "|---|---|---|"]
    for kind, id_, display in rows:
        safe_kind = _escape_cell_code(kind)
        safe_id = _escape_cell_code(id_)
        safe_display = _escape_cell_text(display if display else "—")
        lines.append(f"| `{safe_kind}` | `{safe_id}` | {safe_display} |")
    return "\n".join(lines)


def _format_authorities_table(rows: list[tuple[str, str, str]]) -> str:
    """Format a list of (kind, id, granted_by) tuples as a Markdown table.

    W217 hostile-input safety: see ``_format_actors_table`` for the
    full rationale.
    """
    lines = ["| Kind | ID | Granted by |", "|---|---|---|"]
    for kind, id_, granted_by in rows:
        safe_kind = _escape_cell_code(kind)
        safe_id = _escape_cell_code(id_)
        safe_granted = _escape_cell_text(granted_by if granted_by else "—")
        lines.append(f"| `{safe_kind}` | `{safe_id}` | {safe_granted} |")
    return "\n".join(lines)


def _format_environment_table(rows: list[tuple[str, str]]) -> str:
    """Format a list of (kind, id) tuples as a Markdown table.

    W217 hostile-input safety: both columns are backticked code-spans,
    so route through ``_escape_cell_code`` to neutralise hostile chars.
    """
    lines = ["| Kind | ID |", "|---|---|"]
    for kind, id_ in rows:
        safe_kind = _escape_cell_code(kind)
        safe_id = _escape_cell_code(id_)
        lines.append(f"| `{safe_kind}` | `{safe_id}` |")
    return "\n".join(lines)


def _render_actors_section(evidence) -> str:
    """Render the ``## Actors`` section body.

    Three branches:

    1. ``actor_refs`` populated -> render a (kind / id / display_name)
       table. Display falls back to ``—`` when ``ActorRef.display_name``
       is ``None``.
    2. ``actor_refs`` empty but legacy ``agent_id`` / ``human_actor``
       fields set -> synthesise a single-row-each table from the legacy
       scalars and annotate that the synthesis is a fallback (per the
       W190 race-condition note in the docstring above).
    3. No identity surface at all -> emit a one-line ``_No actors
       recorded._`` sentinel that points to the "Evidence limitations"
       section below for the recommendation. Keeps the section
       unconditional so a Markdown diff against the template is loud.
    """
    actor_refs = getattr(evidence, "actor_refs", ()) or ()

    if actor_refs:
        rows = [(r.actor_kind, r.actor_id, r.display_name or "—") for r in actor_refs]
        return _format_actors_table(rows)

    # Fall back to legacy flat fields if refs are empty but the older
    # ``agent_id`` / ``human_actor`` scalars are set. This keeps the
    # section useful for packets emitted before W190 lands.
    agent_id = getattr(evidence, "agent_id", None)
    human_actor = getattr(evidence, "human_actor", None)
    if agent_id or human_actor:
        rows = []
        if agent_id:
            rows.append(("agent", agent_id, "—"))
        if human_actor:
            rows.append(("human", human_actor, "—"))
        return (
            _format_actors_table(rows) + "\n\n_(Synthesised from `agent_id` / `human_actor` legacy "
            "fields; populate `actor_refs` for richer attribution.)_"
        )

    return (
        "_No actors recorded. The change cannot be attributed to a "
        "specific human or agent — see Evidence limitations below._"
    )


def _render_authorities_section(evidence) -> str:
    """Render the ``## Authorities`` section body.

    No legacy-scalar fallback path exists (``ChangeEvidence`` carries no
    pre-W182 authority field), so the branches are: populated -> table;
    empty -> sentinel.
    """
    authority_refs = getattr(evidence, "authority_refs", ()) or ()
    if not authority_refs:
        return (
            "_No authorities recorded. The change is not bound to a "
            "mode, permit, approval, policy rule, or token scope — see "
            "Evidence limitations below._"
        )
    rows = [(r.authority_kind, r.authority_id, r.granted_by or "—") for r in authority_refs]
    return _format_authorities_table(rows)


def _render_environment_section(evidence) -> str:
    """Render the ``## Environment`` section body.

    Mirrors the authorities helper — populated tuple renders the table,
    empty tuple renders the sentinel. Environment refs carry no
    ``granted_by`` analogue, so the table is two columns (kind + id).
    """
    environment_refs = getattr(evidence, "environment_refs", ()) or ()
    if not environment_refs:
        return (
            "_No environment recorded. The packet does not name the "
            "workspace, branch range, or CI job that produced this "
            "change — see Evidence limitations below._"
        )
    rows = [(r.env_kind, r.env_id) for r in environment_refs]
    return _format_environment_table(rows)


# ---------------------------------------------------------------------------
# Report renderer.
# ---------------------------------------------------------------------------


def _append_report_header(
    out: list[str],
    *,
    tier_meta: dict,
    commit_range: str,
    client: str | None,
    generated_at: str,
) -> None:
    if client:
        out.append(f"# PR Replay Report — {client}")
    else:
        out.append("# PR Replay Report")
    out.append("")
    out.append(f"**Tier:** {tier_meta['label']}  ")
    out.append(f"**Commit range:** `{commit_range}`  ")
    out.append(f"**Generated:** {generated_at}  ")
    out.append("**Tool:** `roam pr-replay` — `postmortem` + `critique` engine")
    out.append("")
    if tier_meta["watermark"]:
        out.append(
            "> **Sample report.** Five PRs only, no founder review. "
            "For a Team report (30 PRs + 30-minute walk-through) or "
            "Deep report (90 PRs + 90-minute walk-through), see "
            "https://roam-code.com/#audit."
        )
        out.append("")
    out.append(tier_meta["purpose_line"])
    out.append("")
    out.append(
        "> **Evidence framing.** PR Replay produces a structural-review report "
        "that **supports evidence for** governance review and **maps to** "
        "change-management controls. It does not certify compliance with "
        "SOC 2, ISO 42001, the EU AI Act, or any other framework; the "
        "control mapping and conformity assessment stay with the customer."
    )
    out.append("")


def _append_executive_summary(out: list[str], *, summary: dict, commits: list[dict]) -> int:
    out.append("## Executive summary")
    out.append("")
    commits_scanned = summary.get("commits_scanned", len(commits))
    commits_with = summary.get("commits_with_findings", 0)
    total_high = summary.get("total_high", 0)
    total_medium = summary.get("total_medium", 0)
    if commits_with == 0:
        out.append(
            f"**Verdict:** Clean window. None of the {commits_scanned} PRs replayed would "
            f"have been flagged by the current detector set."
        )
    else:
        block_word = "block-eligible" if total_high > 0 else "review-eligible"
        out.append(
            f"**Verdict:** {commits_with} of {commits_scanned} PRs ({commits_with * 100 // max(commits_scanned, 1)}%) "
            f"would have surfaced findings — {total_high} {block_word} (high), {total_medium} review-required (medium)."
        )
    out.append("")
    out.append(f"- PRs replayed: **{commits_scanned}**")
    out.append(f"- PRs Roam would have flagged pre-merge: **{commits_with}**")
    out.append(f"- High-severity findings (would block CI): **{total_high}**")
    out.append(f"- Medium-severity findings (would gate review): **{total_medium}**")
    out.append("")
    return commits_scanned


def _append_evidence_coverage(out: list[str]) -> None:
    out.append("## Evidence coverage")
    out.append("")
    out.append(
        "PR Replay is a merged-history replay, not a continuous approval system. "
        "The companion evidence bundle answers all eight evidence questions "
        "fully or partially when `--evidence-bundle` is used; the buyer-facing "
        "coverage floor is:"
    )
    out.append("")
    out.append("| Evidence question | PR Replay coverage |")
    out.append("|---|---|")
    out.append("| Who acted? | Out of scope for attribution; git metadata may appear only as source metadata. |")
    out.append("| What authority existed? | Out of scope except for the replay mode used to produce this report. |")
    out.append("| What context was read? | Partial: commit range, detector version, and local run context. |")
    out.append("| What changed? | In scope: replay window and per-commit changed subjects. |")
    out.append("| What could break? | In scope: detector findings, blast-radius signals, and severity. |")
    out.append("| What policy applied? | In scope: current Roam detector set and any supplied rules. |")
    out.append("| What verified it? | Partial: replay detectors only; no test execution is implied. |")
    out.append("| Who accepted risk? | Out of scope unless GitHub approval data is attached explicitly. |")
    out.append("")


def _append_detector_breakdown(out: list[str], *, by_detector: list[dict], commits_scanned: int) -> None:
    out.append("## What Roam would have flagged")
    out.append("")
    if not by_detector:
        out.append("_No detector hits across this window._")
        out.append("")
        return
    out.append("| Detector | Total findings | PRs with this finding |")
    out.append("|---|---:|---:|")
    for row in by_detector:
        out.append(
            f"| `{row['detector']}` | {row['total_findings']} | {row['commits_with_finding']} / {commits_scanned} |"
        )
    out.append("")
    top = by_detector[0]
    out.append(
        f"The highest-impact class on this window was "
        f"**`{top['detector']}`** ({top['total_findings']} findings across "
        f"{top['commits_with_finding']} PRs). Wiring a CI gate against this class is "
        f"the single highest-leverage move surfacing from this replay."
    )
    out.append("")


def _append_per_pr_breakdown(
    out: list[str],
    *,
    tier: str,
    tier_meta: dict,
    commits: list[dict],
) -> None:
    out.append("## Per-PR breakdown")
    out.append("")
    flagged = [c for c in commits if (c.get("high", 0) + c.get("medium", 0)) > 0]
    if not flagged:
        out.append("_No PRs in this window would have been flagged by current detectors._")
        out.append("")
        out.append(
            "That can mean three things: (1) the codebase has been clean over this "
            "window, (2) the detector set doesn't yet cover the kinds of bugs your "
            "team has been shipping, or (3) the window is too small to be representative. "
            "Run a Deep report (90 PRs) for the strongest signal."
        )
        out.append("")
        return
    cap = tier_meta["max_per_pr_findings_listed"] * 3
    listed = flagged[:cap] if tier == "sample" else flagged
    out.append(f"Top {len(listed)} PRs ranked by severity (high → medium → total).")
    out.append("")
    out.append("| Date | SHA | Subject | High | Medium | Top hits |")
    out.append("|---|---|---|---:|---:|---|")
    for commit in listed:
        subject = (commit.get("subject") or "").replace("|", "/")[:60]
        kinds = commit.get("kinds") or []
        kinds_cap = ", ".join(kinds[: tier_meta["max_per_pr_findings_listed"]])
        out.append(
            f"| {commit.get('date', '?')} | `{commit.get('short_sha', '?')}` | "
            f"{subject} | {commit.get('high', 0)} | {commit.get('medium', 0)} | {kinds_cap or '-'} |"
        )
    out.append("")


def _matching_detector_commits(commits: list[dict], detector: str) -> list[dict]:
    return [c for c in commits if any(k.startswith(detector + " x") for k in (c.get("kinds") or []))]


def _append_deep_detector_breakdown(
    out: list[str],
    *,
    tier: str,
    by_detector: list[dict],
    commits: list[dict],
) -> None:
    if tier != "deep" or not by_detector:
        return
    out.append("## Per-detector deep-dive")
    out.append("")
    out.append(
        "For each detector class with hits across this window, the PRs that "
        "surfaced findings of that class. Use this to triage which detector "
        "warrants its own CI gate vs. lighter-touch enforcement."
    )
    out.append("")
    for row in by_detector:
        detector = row["detector"]
        matching = _matching_detector_commits(commits, detector)
        if not matching:
            continue
        out.append(f"### `{detector}` — {row['total_findings']} finding(s)")
        out.append("")
        for commit in matching[:5]:
            subject = (commit.get("subject") or "").replace("|", "/")[:80]
            out.append(f"- `{commit.get('short_sha', '?')}` ({commit.get('date', '?')}) — {subject}")
        if len(matching) > 5:
            out.append(f"- _… and {len(matching) - 5} more_")
        out.append("")


def _append_recommended_next_steps(out: list[str], *, tier: str, by_detector: list[dict]) -> None:
    out.append("## Recommended next steps")
    out.append("")
    if not by_detector:
        out.append(
            "- No detector hits surfaced. Pick a longer window or a higher-traffic "
            "branch for a more representative replay."
        )
        if tier == "sample":
            out.append(
                "- A Team report (30 PRs) or Deep report (90 PRs) covers a longer "
                "window and adds founder review of the patterns that surface: "
                "<https://roam-code.com/#audit>."
            )
        out.append("")
        return
    top_three = by_detector[:3]
    labels = ", ".join(f"`{r['detector']}`" for r in top_three)
    out.append(
        f"- **Wire CI gates against the top {len(top_three)} detector class(es)** — {labels}. "
        f"`roam critique` returns exit code 5 on any high-severity finding, "
        f"so a single CI step gates every PR. See <https://roam-code.com/docs/>."
    )
    out.append(
        "- **Run `roam preflight <symbol>` before changing high-blast-radius code.** "
        "The blast radius doesn't show up in the diff; it shows up in the graph."
    )
    out.append(
        "- **Add `roam clones --persist` to your indexing pipeline.** Then "
        "`roam critique` picks up clone-not-edited cases on every PR — the "
        "single most common AI-shaped bug across replays in similar codebases."
    )
    if tier == "sample":
        out.append(
            "- **Upgrade to a paid Team or Deep report** for a founder walk-"
            "through tailored to your codebase and a written 90-day "
            "remediation plan: <https://roam-code.com/#audit>."
        )
    elif tier == "team":
        out.append(
            "- **Consider the Deep tier** if the patterns above warrant a "
            "90-PR window, per-detector deep-dive, and a 90-minute walk-"
            "through with a written remediation plan: <https://roam-code.com/#audit>."
        )
    out.append("")


def _append_subscription_credit(out: list[str], *, tier: str) -> None:
    if tier not in ("team", "deep"):
        return
    out.append("## Apply this fee toward Roam Review")
    out.append("")
    credit_amount = "$1,250" if tier == "team" else "$3,000"
    out.append(
        f"50% of the engagement fee — **{credit_amount}** — credits toward your "
        f"first year of [Roam Review](https://roam-code.com/pricing) if you "
        f"subscribe within **60 days** of report delivery. Roam Review runs the "
        f"same detectors on every pull request automatically, with a sticky PR "
        f"comment, BLOCK / REVIEW / APPROVE verdict, and exit-code-5 CI gating. "
        f"Mention this report when subscribing and we apply the credit to the "
        f"first invoice."
    )
    out.append("")


def _append_out_of_scope(out: list[str], *, tier: str) -> None:
    if tier not in ("team", "deep"):
        return
    out.append("## What this report does *not* cover")
    out.append("")
    out.append(
        "- **Semantic correctness** — whether the code does the right thing. "
        "We complement semantic reviewers (CodeRabbit, Greptile, Qodo), we "
        "don't replace them."
    )
    out.append(
        "- **Security audit** of the kind a third-party penetration test "
        "would produce. We surface structural risks (clones, blast radius, "
        "layer violations) — not exploit paths."
    )
    out.append(
        "- **Performance profiling**. Some findings touch hot paths "
        "(when runtime telemetry is wired), but this isn't a benchmark run."
    )
    out.append(
        "- **Code review of in-flight PRs.** This report covers *merged* "
        "history. For pre-merge gating, install the free CLI plus, when it "
        "ships, the Roam Review GitHub App."
    )
    out.append("")


def _append_methodology(out: list[str], *, tier: str, generated_at: str) -> None:
    out.append("## Methodology")
    out.append("")
    out.append(
        "Roam replays the current detector set against each commit's outgoing diff "
        "as if it were a PR — no historical re-indexing. Findings reflect what Roam "
        "catches today on those PRs, not what an earlier Roam version would have. "
        "The detector set is stable across Team (30 PRs) and Deep (90 PRs) windows."
    )
    out.append("")
    out.append(
        f"_Generated by `roam pr-replay --tier {tier}` on {generated_at}. Engine: "
        f"`roam postmortem` walks the range; `roam critique` evaluates each diff. "
        f"Both ship in the open-source CLI ([github.com/Cranot/roam-code](https://github.com/Cranot/roam-code))._"
    )
    out.append("")


def _render_report(
    *,
    tier: str,
    tier_meta: dict,
    commit_range: str,
    client: str | None,
    summary: dict,
    commits: list[dict],
    by_detector: list[dict],
    generated_at: str,
) -> str:
    """Render the markdown report. Pure function, no I/O."""
    out: list[str] = []

    _append_report_header(
        out,
        tier_meta=tier_meta,
        commit_range=commit_range,
        client=client,
        generated_at=generated_at,
    )

    # -- Executive summary -------------------------------------------------
    commits_scanned = _append_executive_summary(out, summary=summary, commits=commits)

    _append_evidence_coverage(out)

    # -- Detector breakdown ------------------------------------------------
    _append_detector_breakdown(out, by_detector=by_detector, commits_scanned=commits_scanned)

    # -- Per-PR breakdown --------------------------------------------------
    _append_per_pr_breakdown(out, tier=tier, tier_meta=tier_meta, commits=commits)

    # -- Per-detector deep-dive (Deep tier only, only when there are hits) -
    _append_deep_detector_breakdown(out, tier=tier, by_detector=by_detector, commits=commits)

    # -- What to do with this ----------------------------------------------
    _append_recommended_next_steps(out, tier=tier, by_detector=by_detector)

    # -- Subscription credit (paid tiers only) -----------------------------
    _append_subscription_credit(out, tier=tier)

    # -- What's not in scope ------------------------------------------------
    _append_out_of_scope(out, tier=tier)

    # -- Methodology ------------------------------------------------------
    _append_methodology(out, tier=tier, generated_at=generated_at)

    return "\n".join(out)


# ---------------------------------------------------------------------------
# Engagement ledger — append-only JSONL written next to .roam/index.db.
# ---------------------------------------------------------------------------


def _record_engagement(
    *,
    tier: str,
    client: str | None,
    commit_range: str,
    commits_scanned: int,
    commits_with_findings: int,
    top_detector: str | None,
    output_path: str,
    generated_at: str,
) -> Path | None:
    """Append one record to ``.roam/engagements.jsonl``.

    Returns the ledger path on success, ``None`` on failure (we never
    raise — telemetry must not break a buyer-facing run).

    Schema is intentionally flat so the operator can do
    ``cat .roam/engagements.jsonl | jq -s 'group_by(.tier)'`` without
    nested-key acrobatics. Schema version bump = additive only.
    """
    try:
        ledger_dir = Path(".roam")
        ledger_dir.mkdir(exist_ok=True)
        ledger = ledger_dir / "engagements.jsonl"
        record = {
            "ledger_schema": 1,
            "tier": tier,
            "client": client,
            "commit_range": commit_range,
            "commits_scanned": commits_scanned,
            "commits_with_findings": commits_with_findings,
            "top_detector": top_detector,
            "output_path": output_path,
            "generated_at": generated_at,
        }
        with ledger.open("a", encoding="utf-8") as f:
            f.write(_json.dumps(record) + "\n")
        return ledger
    except OSError:
        # Filesystem refused us (read-only mount, no permission, …) —
        # silently skip rather than crash the report run.
        return None


# ---------------------------------------------------------------------------
# PDF rendering — pandoc preferred, reportlab fallback.
# ---------------------------------------------------------------------------


def _run_pandoc_pdf(markdown_text: str, output_path: Path) -> tuple[bool, str]:
    import shutil
    import tempfile

    if not shutil.which("pandoc"):
        return False, "pandoc not on PATH"

    with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False, encoding="utf-8") as tmp:
        tmp.write(markdown_text)
        tmp_path = tmp.name
    try:
        result = _subprocess.run(
            ["pandoc", tmp_path, "-o", str(output_path), "--pdf-engine=xelatex"],
            capture_output=True,
            text=True,
            timeout=60,
        )
        if result.returncode == 0:
            return True, "pandoc"
        # Pandoc may fail on systems without xelatex; retry default engine.
        result2 = _subprocess.run(
            ["pandoc", tmp_path, "-o", str(output_path)],
            capture_output=True,
            text=True,
            timeout=60,
        )
        if result2.returncode == 0:
            return True, "pandoc"
        err = (result.stderr or result2.stderr or "").strip()[:200]
        return False, f"pandoc failed: {err}"
    except (FileNotFoundError, _subprocess.TimeoutExpired) as exc:
        return False, f"pandoc invocation error: {exc}"
    finally:
        Path(tmp_path).unlink(missing_ok=True)


def _reportlab_backend():
    import importlib

    try:
        pagesizes = importlib.import_module("reportlab.lib.pagesizes")
        styles_mod = importlib.import_module("reportlab.lib.styles")
        platypus = importlib.import_module("reportlab.platypus")
    except ImportError:
        return None
    return (
        pagesizes.A4,
        styles_mod.getSampleStyleSheet,
        platypus.Paragraph,
        platypus.SimpleDocTemplate,
        platypus.Spacer,
    )


def _reportlab_line_shape(line: str, styles) -> tuple[object, str] | None:
    if not line.strip():
        return None
    if line.startswith("# "):
        return styles["Title"], line[2:]
    if line.startswith("## "):
        return styles["Heading1"], line[3:]
    if line.startswith("### "):
        return styles["Heading2"], line[4:]
    return styles["BodyText"], line


def _escape_reportlab_text(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _render_pdf_with_reportlab(markdown_text: str, output_path: Path, pandoc_err: str) -> tuple[bool, str]:
    backend = _reportlab_backend()
    if backend is None:
        return False, (
            f"PDF rendering unavailable: {pandoc_err} and reportlab not installed. "
            "Install pandoc (recommended) or `pip install reportlab`."
        )

    a4, get_styles, paragraph, doc_template, spacer = backend
    styles = get_styles()
    doc = doc_template(str(output_path), pagesize=a4)
    story: list = []
    # Reportlab is intentionally simple here — render markdown line-by-line
    # without parsing tables/code-fences. Pandoc is the preferred path; this
    # branch is the "any PDF is better than no PDF" safety net.
    for line in markdown_text.splitlines():
        shaped = _reportlab_line_shape(line, styles)
        if shaped is None:
            story.append(spacer(1, 6))
            continue
        style, text = shaped
        story.append(paragraph(_escape_reportlab_text(text), style))
    try:
        doc.build(story)
        return True, "reportlab"
    except Exception as exc:  # noqa: BLE001 — defensive; report the error
        return False, f"reportlab build failed: {exc}"


def _render_pdf(markdown_text: str, output_path: Path) -> tuple[bool, str]:
    """Render the report markdown to a PDF at ``output_path``.

    Returns ``(success, backend_used_or_error_message)``.

    Prefers pandoc (better typography, native markdown awareness).
    Falls back to reportlab (pure-Python, simpler output) when pandoc
    is missing. If both are unavailable, returns ``(False, message)``
    so the caller can surface a useful error to the operator.
    """
    ok, info = _run_pandoc_pdf(markdown_text, output_path)
    if ok:
        return ok, info
    return _render_pdf_with_reportlab(markdown_text, output_path, info)


# ---------------------------------------------------------------------------
# CLI entry point.
# ---------------------------------------------------------------------------


@roam_capability(
    category="review",
    summary="Generate a PR Replay report — what current detectors would have caught on past PRs.",
    inputs=["tier_or_range"],
    outputs=["narrative_report", "by_detector", "per_pr"],
    examples=[
        "roam pr-replay --tier sample",
        "roam pr-replay --tier team --client 'Acme Inc' --output acme.md",
        "roam pr-replay --range HEAD~50..HEAD --output report.md",
    ],
    tags=["audit", "review", "demo"],
    ai_safe=True,
    requires_index=True,
    since="12.48",
)
@click.command(name="pr-replay")
@click.option(
    "--tier",
    type=click.Choice(list(_TIERS.keys()), case_sensitive=False),
    default="sample",
    show_default=True,
    help=(
        "Report tier. ``sample`` is the free 5-PR DIY sample; "
        "``team`` is the paid 30-PR report; ``deep`` is the paid 90-PR report."
    ),
)
@click.option(
    "--range",
    "commit_range",
    default=None,
    help=(
        "Explicit git commit range (e.g. ``HEAD~30..HEAD``, ``v1.0..main``). "
        "Overrides the commit count implied by --tier. The tier still controls "
        "report shape (watermark, founder-review framing, recommended-actions block)."
    ),
)
@click.option(
    "--client",
    default=None,
    help=(
        "Client name to inject into the report header. Used for paid tiers; "
        "the sample tier omits the client name even when set."
    ),
)
@click.option(
    "--output",
    "output_path",
    default=None,
    type=click.Path(dir_okay=False, writable=True),
    help="Write the markdown report to <PATH> instead of stdout.",  # W1117-followup
)
@click.option(
    "--pdf",
    "pdf_path",
    default=None,
    type=click.Path(dir_okay=False, writable=True),
    help=(
        "Also write a PDF render of the report to PATH. Requires ``pandoc`` on "
        "PATH (preferred — better typography) or ``reportlab`` (simpler "
        "fallback if pandoc unavailable). Implies --output if not set; the "
        "Markdown source is written next to the PDF as ``<pdf>.md``."
    ),
)
@click.option(
    "--track-engagement/--no-track-engagement",
    default=True,
    show_default=True,
    help=(
        "On paid tiers (team / deep), append a one-line JSONL record to "
        "``.roam/engagements.jsonl`` so the operator has a single-file "
        "ledger of every paid engagement (tier, client, commit count, "
        "findings, output path, timestamp). Skipped on sample tier and "
        "when --output is unset (no artefact = no engagement)."
    ),
)
@click.option(
    "--evidence",
    "evidence_path",
    type=click.Path(),
    default=None,
    help=(
        "Write a canonical ``ChangeEvidence`` JSON packet to PATH. The "
        "packet captures the replay window's changed subjects, findings, "
        "and run-IDs in the W174 evidence schema. Wins over "
        "``--evidence-bundle`` when both are set (explicit beats inferred)."
    ),
)
@click.option(
    "--markdown",
    "markdown_path",
    type=click.Path(),
    default=None,
    help=(
        "Write a Markdown companion report to PATH. The report renders "
        "the ChangeEvidence packet into the audit-template format and "
        "includes the suggested Review configuration."
    ),
)
@click.option(
    "--evidence-bundle",
    "evidence_bundle_dir",
    type=click.Path(),
    default=None,
    help=(
        "Write BOTH the evidence JSON and the Markdown report into "
        "OUTPUT_DIR (creates ``OUTPUT_DIR/evidence.json`` and "
        "``OUTPUT_DIR/report.md``). When combined with ``--evidence`` or "
        "``--markdown``, the more specific flag wins for that file."
    ),
)
@click.option(
    "--rehearsal",
    is_flag=True,
    help=(
        "Write a full dry-run packet under "
        "``internal/engagements/rehearsals/<timestamp>-<client>-<tier>/`` "
        "when --output / --evidence-bundle are not set, and skip the paid "
        "engagement ledger. Useful before publishing live payment links."
    ),
)
@click.option(
    "--github-reviews-json",
    "github_reviews_json",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    default=None,
    help=(
        "Path to a saved GitHub PR reviews JSON fixture (offline mode). "
        "APPROVED reviews on the current head commit populate "
        "``approvals[]`` on the evidence packet; CHANGES_REQUESTED "
        "reviews populate ``policy_decisions[]`` as deny rows. Requires "
        "``--github-pr-number``. No network."
    ),
)
@click.option(
    "--github-pr-number",
    "github_pr_number",
    type=int,
    default=None,
    help=(
        "PR number context for ``--github-reviews-json`` / "
        "``--github-reviews-gh``. Used to scope the parsed approvals to "
        "this PR (``scope='pr:<N>'``)."
    ),
)
@click.option(
    "--github-reviews-gh",
    "github_reviews_gh",
    type=str,
    default=None,
    help=(
        "Live ``gh api`` harvest mode (opt-in, no network by default). "
        "Argument is ``OWNER/REPO[#PR]``; requires the ``gh`` CLI on "
        "PATH and is authenticated by ``gh auth``. Mutually exclusive "
        "with ``--github-reviews-json``."
    ),
)
@click.pass_context
def pr_replay_cmd(
    ctx,
    tier: str,
    commit_range: str | None,
    client: str | None,
    output_path: str | None,
    pdf_path: str | None,
    track_engagement: bool,
    evidence_path: str | None,
    markdown_path: str | None,
    evidence_bundle_dir: str | None,
    rehearsal: bool,
    github_reviews_json: Path | None,
    github_pr_number: int | None,
    github_reviews_gh: str | None,
):
    """Generate a PR Replay report.

    Wraps ``roam postmortem`` with tier-aware framing, an aggregated
    detector-class breakdown, and a buyer-facing narrative.

    \b
    Examples:
      # Free DIY sample on the current repo
      roam pr-replay --tier sample

      # Paid Team report, written to a file
      roam pr-replay --tier team --client "Acme Inc" --output acme.md

      # Custom range with deep-tier framing
      roam pr-replay --tier deep --range v1.0..main --output q1.md

      # Full private dry run before live sales
      roam pr-replay --tier team --client "Demo Buyer" --rehearsal

    \b
    Output: markdown by default; ``roam --json pr-replay`` returns the full
    envelope (summary + commits + by_detector + report_markdown) for
    machine consumption.
    """
    json_mode = ctx.obj.get("json") if ctx.obj else False
    ensure_index()

    # W607-AH -- substrate-CALL marker plumbing for cmd_pr_replay. Mirrors
    # the canonical W607 template (latest landed: W607-AE cmd_pr_bundle).
    # cmd_pr_replay is the **consumer at the heart of the W805-OOOOO
    # 3-artifact family**: it reads the bundle JSON from disk (artifact 1),
    # reconstructs the ChangeEvidence packet via W534
    # ``from_canonical_json`` (artifact 2 boundary), and reads the
    # run-ledger root (artifact 3 boundary). It is the producer/consumer
    # pair of cmd_pr_bundle (W607-AE just landed): pr-bundle emits the
    # bundle, pr-replay reads + renders it. Plumbing pr-replay closes
    # that pair on the W805 family.
    #
    # Each wrapped phase becomes a structured
    # ``pr_replay_<phase>_failed:<exc_class>:<detail>`` marker on
    # ``_w607ah_warnings_out`` and the envelope still emits cleanly. The
    # marker rides BOTH ``summary.warnings_out`` and top-level
    # ``warnings_out`` so consumers reading either surface see the
    # disclosure. ``partial_success`` flips on non-empty bucket.
    #
    # W805 reader bridge: Pattern-2 disclosure on the READER side has
    # even higher consequence than the writer side -- if pr-replay
    # silently falls back when an artifact is missing/malformed,
    # downstream consumers (audit-report, GRC export) treat the
    # partial replay as complete. W607-AH markers on each read
    # boundary lift this from silent to disclosed.
    _w607ah_warnings_out: list[str] = []

    def _run_check_ah(phase, fn, *args, default=None, **kwargs):
        """Run one substrate helper with W607-AH marker emission.

        On a clean call the result is returned as-is. On an uncaught
        exception, surface a ``pr_replay_<phase>_failed:<exc_class>:<detail>``
        marker via ``_w607ah_warnings_out`` and return *default* -- the
        envelope still emits cleanly with the remaining substrates.
        """
        try:
            return fn(*args, **kwargs)
        except Exception as exc:  # noqa: BLE001 -- top-level disclosure
            _w607ah_warnings_out.append(f"pr_replay_{phase}_failed:{type(exc).__name__}:{exc}")
            return default

    # W607-CA -- ADDITIVE aggregation-phase plumbing on top of the
    # W607-AH substrate-CALL markers. W607-AH already wrapped 8 substrate
    # boundaries (run_postmortem / aggregate_by_detector / render_report /
    # render_pdf / build_review_suggestions / collect_change_evidence /
    # to_canonical_json / render_evidence_markdown); W607-CA extends marker
    # coverage to the AGGREGATION-PHASE boundaries that W607-AH left
    # unguarded:
    #
    #   - ``score_classify``      -- per-replay classification of the canonical
    #                                W631 risk-LEVEL set via an additive
    #                                re-probe over the postmortem summary's
    #                                verdict-signal block.
    #   - ``severity_normalize``  -- additive canonical W631 risk-LEVEL
    #                                projection (``normalize_risk_level`` +
    #                                ``risk_rank``) on the replay's domain
    #                                level. Conservative-on-critical floor.
    #   - ``compute_verdict``     -- augmented verdict-floor build with the
    #                                canonical risk_level suffix (LAW 6
    #                                standalone-parse) via the
    #                                ``_make_pr_replay_verdict_floor``.
    #   - ``render_markdown``     -- pr-replay-specific: re-probe of the
    #                                ``report_md`` projection. The inner
    #                                _render_report already produced the
    #                                primary report markdown; the OUTER
    #                                re-probe surfaces a marker if a future
    #                                refactor breaks the projection contract.
    #   - ``serialize_envelope``  -- ``json_envelope("pr-replay", ...)``
    #                                re-projection (downstream contract
    #                                changes / shape regressions).
    #   - ``auto_log``            -- active-run ledger write (silent no-op
    #                                if no run is active, but the underlying
    #                                ``auto_log`` can still raise on HMAC
    #                                chain misshape or filesystem failures).
    #
    # cmd_pr_replay is the consumer at the heart of the W805-OOOOO
    # 3-artifact family. With W607-CA landed, the full W631 risk-LEVEL
    # vocabulary range is now dual-bucket plumbed via the substrate-CALL
    # layer (W607-AH) + aggregation-phase layer (W607-CA). Pairs with
    # cmd_pr_bundle's W607-AE + BW closure on the producer side -- the
    # PR-bundle ecosystem (emit + analyze + replay) is now closed on both
    # axes.
    #
    # Marker family ``pr_replay_*`` -- same family as W607-AH (additive,
    # not a separate prefix). Empty bucket -> byte-identical envelope.
    _w607ca_warnings_out: list[str] = []

    def _run_check_ca(phase, fn, *args, default=None, **kwargs):
        """Run one aggregation-phase boundary with W607-CA marker emission.

        Mirror of ``_run_check_ah`` shape (same ``pr_replay_<phase>_failed:``
        marker family) but writes into ``_w607ca_warnings_out`` so the
        additive bucket stays distinguishable in tests + audits.
        """
        try:
            return fn(*args, **kwargs)
        except Exception as exc:  # noqa: BLE001 -- top-level disclosure
            _w607ca_warnings_out.append(f"pr_replay_{phase}_failed:{type(exc).__name__}:{exc}")
            return default

    # W607-DV -- ADDITIVE aggregation-LAYER plumbing on top of the W607-AH
    # substrate-CALL layer + W607-CA aggregation-phase layer. cmd_pr_replay
    # is the ledger-reader consumer that pairs with cmd_postmortem (the
    # git-log reader) and cmd_audit_trail_verify (the ledger-verify reader)
    # to close the runs-ledger reader 3-way:
    #
    #   * cmd_postmortem           -- W607-AN + W607-CV + W607-DR (3 layers,
    #                                 16 phases) -- git-log reader
    #   * cmd_audit_trail_verify   -- W607-AI (likely substrate-only)
    #   * cmd_pr_replay            -- W607-AH (substrate) + W607-CA + DV
    #                                 (this wave) -- ledger consumer +
    #                                 replay-renderer
    #
    # W607-DV phases focus on the evidence-completeness aggregation
    # slice that W607-CA does NOT cover. CA wraps the risk_level /
    # markdown projection / auto_log axes; DV wraps the 8-question
    # evidence-completeness rollup + W276 INSUFFICIENT-tier classification
    # + W561-spirit dropped-counts disclosure + verdict synthesis:
    #
    #   * ``completeness_classify``   -- bucket the 8-question completeness
    #                                    count into one of four W276 tiers:
    #                                    PASS (>=6 complete) / WARN (>=4) /
    #                                    FAIL (<4) / INSUFFICIENT (no
    #                                    evidence_completeness() method on
    #                                    the packet).
    #   * ``completeness_rollup``     -- roll up Q1..Q8 totals (complete /
    #                                    partial / missing / not_applicable)
    #                                    PLUS the banner tier from
    #                                    ``_banner_envelope_block`` PLUS
    #                                    a count of producer-level
    #                                    redaction sentinels (the W561
    #                                    dropped-row spirit applied to the
    #                                    pr_replay surface, which doesn't
    #                                    have an OSCAL-style dropped_enum_rows
    #                                    but DOES have ``redactions[]`` +
    #                                    ``envelope_producer_warnings`` as
    #                                    the equivalent disclosure channel).
    #   * ``evidence_verdict_compose`` -- synthesise the canonical "N of 8
    #                                    evidence questions answered" verdict
    #                                    with literal floor ``"pr_replay
    #                                    completed"`` (LAW 6 standalone-parse).
    #   * ``dv_serialize_envelope``   -- additive ``json_envelope`` re-projection
    #                                    over the assembled DV signals; mirrors
    #                                    W607-CA's serialize_envelope but with
    #                                    a DISTINCT phase name (``dv_serialize_envelope``)
    #                                    so the per-phase marker prefix
    #                                    stays unambiguous and the 4 DV
    #                                    phases stay disjoint from the 6
    #                                    CA phases + 8 AH substrate phases.
    #
    # W978 7-DISCIPLINE applies to every ``_run_check_dv(...)`` call:
    #   1. f-string verdict floor: the verdict default= floor is the LITERAL
    #      string "pr_replay completed", NEVER re-interpolating completeness
    #      counts that may have tripped the closure.
    #   2. kwarg-default eagerness: ``default=`` is a literal constant on
    #      every call. The AST audit below pins this.
    #   3. json.dumps(default=str) sentinel: floors are str/int/dict/None
    #      (json-serialisable with the standard encoder).
    #   4. phase-name collision: the 4 DV phases (completeness_classify /
    #      completeness_rollup / evidence_verdict_compose /
    #      dv_serialize_envelope) MUST NOT collide with the 8 AH substrate
    #      phases (run_postmortem / aggregate_by_detector / render_report /
    #      render_pdf / build_review_suggestions / collect_change_evidence /
    #      to_canonical_json / render_evidence_markdown) OR the 6 CA
    #      aggregation phases (score_classify / severity_normalize /
    #      compute_verdict / render_markdown / serialize_envelope / auto_log).
    #      The phase-name disjointness test pins this.
    #   5. len() at kwarg-bind: every len() call lives INSIDE the wrapped
    #      closure, never at the ``_run_check_dv(...)`` call site.
    #   6. unguarded len()/if on poisoned object: floors are concrete
    #      dict/str/None, never sentinels that __len__-raise downstream.
    #   7. dict.get(key, expensive_default): use bare ``dict[key]`` when
    #      the floor guarantees the key.
    #
    # Marker family ``pr_replay_*`` -- same family as W607-AH + W607-CA
    # (additive, NOT a separate prefix). Empty bucket -> byte-identical
    # envelope.
    _w607dv_warnings_out: list[str] = []

    def _run_check_dv(phase, fn, *args, default=None, **kwargs):
        """Run one aggregation-LAYER boundary with W607-DV marker emission.

        Mirror of ``_run_check_ah`` / ``_run_check_ca`` shape (same
        ``pr_replay_<phase>_failed:`` marker family) but writes into
        ``_w607dv_warnings_out`` so the additive bucket stays
        distinguishable in tests + audits.
        """
        try:
            return fn(*args, **kwargs)
        except Exception as exc:  # noqa: BLE001 -- top-level disclosure
            _w607dv_warnings_out.append(f"pr_replay_{phase}_failed:{type(exc).__name__}:{exc}")
            return default

    # W247b: validate GitHub review options. Mutual exclusion + the
    # PR-number requirement live here so an operator sees the error
    # before any evidence work begins.
    if github_reviews_json is not None and github_reviews_gh is not None:
        raise click.UsageError("Use --github-reviews-json OR --github-reviews-gh, not both.")
    if (github_reviews_json is not None or github_reviews_gh is not None) and github_pr_number is None:
        raise click.UsageError(
            "--github-pr-number is required when --github-reviews-json or --github-reviews-gh is set."
        )

    tier = tier.lower()
    tier_meta = _TIERS[tier]
    if commit_range is None:
        commit_range = f"HEAD~{tier_meta['default_count']}..HEAD"
    elif not _is_safe_commit_range(commit_range):
        # ``--range`` flows positionally into ``git diff`` and the
        # in-process ``roam postmortem`` invocation. A value beginning
        # with ``-`` would be re-interpreted as an option flag by the
        # receiving parser. Fail fast at the CLI boundary rather than
        # downstream where the failure mode is silent (None hash) or
        # misleading (Click's "no such option" error from postmortem).
        raise click.UsageError(
            f"--range value must not start with '-' (got {commit_range!r}); "
            "use a git revspec like 'HEAD~30..HEAD', 'v1.0..main', or a branch name."
        )

    if tier == "sample":
        # Sample never carries a client name — that would imply paid framing.
        client = None

    postmortem = (
        _run_check_ah(
            "run_postmortem",
            _run_postmortem,
            commit_range,
            limit=max(tier_meta["default_count"], 100),
            default={},
        )
        or {}
    )
    summary = postmortem.get("summary") or {}
    commits = postmortem.get("commits") or []
    by_detector = (
        _run_check_ah(
            "aggregate_by_detector",
            _aggregate_by_detector,
            commits,
            default=[],
        )
        or []
    )

    generated_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    rehearsal_dir: str | None = None
    if rehearsal:
        track_engagement = False
        rehearsal_paths = _default_rehearsal_paths(tier=tier, client=client)
        used_rehearsal_default = False
        if output_path is None:
            output_path = str(rehearsal_paths["report"])
            used_rehearsal_default = True
        if evidence_bundle_dir is None and not (evidence_path and markdown_path):
            evidence_bundle_dir = str(rehearsal_paths["evidence_bundle"])
            used_rehearsal_default = True
        if used_rehearsal_default:
            rehearsal_dir = str(rehearsal_paths["root"])

    report_md = (
        _run_check_ah(
            "render_report",
            _render_report,
            tier=tier,
            tier_meta=tier_meta,
            commit_range=commit_range,
            client=client,
            summary=summary,
            commits=commits,
            by_detector=by_detector,
            generated_at=generated_at,
            default="",
        )
        or ""
    )

    # If --pdf is set without --output, derive the markdown sibling path so
    # the operator always has the editable source next to the PDF deliverable.
    if pdf_path and not output_path:
        output_path = str(Path(pdf_path).with_suffix(".md"))

    if output_path:
        if rehearsal and rehearsal_dir and not json_mode:
            click.echo(f"Rehearsal artifacts root: {rehearsal_dir}")
        output_target = Path(output_path)
        output_target.parent.mkdir(parents=True, exist_ok=True)
        output_target.write_text(report_md, encoding="utf-8")
        if not json_mode:
            click.echo(f"Wrote {len(report_md):,} bytes to {output_path}")

    pdf_backend = None
    if pdf_path:
        _pdf_result = _run_check_ah(
            "render_pdf",
            _render_pdf,
            report_md,
            Path(pdf_path),
            default=(False, "render_pdf_raised"),
        )
        ok, info = _pdf_result if _pdf_result else (False, "render_pdf_raised")
        if ok:
            pdf_backend = info
            # Strip identifying metadata (timezone leaks in /CreationDate,
            # MiKTeX/xelatex chain in /Producer). Match the gate that
            # scripts/strip_metadata.py enforces in CI so the deliverable
            # we ship to a buyer doesn't carry our timezone.
            try:
                from pypdf import PdfReader, PdfWriter

                neutral = {
                    "/Title": "PR Replay Report",
                    "/Author": "Roam",
                    "/Subject": "",
                    "/Keywords": "",
                    "/Creator": "pandoc",
                    "/Producer": "pandoc",
                }
                reader = PdfReader(str(pdf_path))
                writer = PdfWriter()
                for page in reader.pages:
                    writer.add_page(page)
                writer.add_metadata(neutral)
                with open(pdf_path, "wb") as f:
                    writer.write(f)
            except ImportError:
                # pypdf not available — PDF metadata stays as-is. Operator
                # can run scripts/strip_metadata.py manually before delivery.
                pass
            except Exception:  # noqa: BLE001 — defensive
                # PDF survives even if the metadata-strip step fails.
                pass
            if not json_mode:
                click.echo(f"Wrote PDF to {pdf_path} (backend: {info})")
        else:
            # Surface the error but don't fail the command — markdown is the
            # primary deliverable; PDF is a convenience.
            click.echo(f"WARNING: PDF render failed — {info}", err=True)

    # Engagement ledger — paid tiers only, only when an output artefact
    # exists. Cheap, append-only JSONL the operator can `cat | jq` later
    # to see every paid engagement at a glance. No external service.
    engagement_record = None
    if track_engagement and tier in ("team", "deep") and output_path:
        engagement_record = _record_engagement(
            tier=tier,
            client=client,
            commit_range=commit_range,
            commits_scanned=summary.get("commits_scanned", len(commits)),
            commits_with_findings=summary.get("commits_with_findings", 0),
            top_detector=by_detector[0]["detector"] if by_detector else None,
            output_path=output_path,
            generated_at=generated_at,
        )
        if engagement_record and not json_mode:
            click.echo(f"Logged engagement to {engagement_record}")

    # -- W177: evidence JSON + Markdown companion -------------------------
    # The Review-suggestions block doubles as input to the Markdown report,
    # so we compute it eagerly (not just in json_mode) when an evidence
    # artefact is being written. Returns None when there are no detector
    # hits — the Markdown then prints a "nothing to suggest" block.
    review_suggestions = _run_check_ah(
        "build_review_suggestions",
        _build_review_suggestions,
        by_detector=by_detector,
        commits=commits,
        tier=tier,
        default=None,
    )

    evidence_json_target: Path | None = None
    markdown_companion_target: Path | None = None
    if evidence_bundle_dir:
        bundle_root = Path(evidence_bundle_dir)
        bundle_root.mkdir(parents=True, exist_ok=True)
        evidence_json_target = bundle_root / "evidence.json"
        markdown_companion_target = bundle_root / "report.md"
    # Specific flags win over the bundle directory — Click options give
    # the user explicit control over each artefact path.
    if evidence_path:
        evidence_json_target = Path(evidence_path)
    if markdown_path:
        markdown_companion_target = Path(markdown_path)

    evidence_packet = None
    evidence_written_to: str | None = None
    markdown_companion_written_to: str | None = None
    # W590: bucket for producer-level warnings (Pattern-2 silent-fallback
    # disclosure). Populated by ``_collect_change_evidence`` when a
    # gatherer surfaces a structured kind (e.g. lease policy gatherer's
    # ``leases: project_root_not_found`` marker). Surfaced on the JSON
    # envelope as ``warnings_out`` so consumers don't have to tail
    # stderr to learn an upstream signal degraded.
    envelope_producer_warnings: list[str] = []
    if evidence_json_target or markdown_companion_target:
        evidence_packet = _run_check_ah(
            "collect_change_evidence",
            _collect_change_evidence,
            commit_range=commit_range,
            commits=commits,
            summary=summary,
            by_detector=by_detector,
            generated_at=generated_at,
            github_reviews_json=(str(github_reviews_json) if github_reviews_json else None),
            github_pr_number=github_pr_number,
            github_reviews_gh=github_reviews_gh,
            producer_warnings_out=envelope_producer_warnings,
            default=None,
        )
        if evidence_json_target and evidence_packet is not None:
            evidence_json_target.parent.mkdir(parents=True, exist_ok=True)
            # W607-AH: ``to_canonical_json`` is the W534 ChangeEvidence
            # serialization boundary -- the canonical JSON projection used
            # downstream by audit-report / GRC consumers. A raise here
            # historically crashed the entire replay; now a structured
            # ``pr_replay_to_canonical_json_failed:`` marker rides
            # ``warnings_out`` and the disk-write is skipped (the packet
            # stays in memory so the Markdown companion still renders).
            _canonical_json = _run_check_ah(
                "to_canonical_json",
                lambda packet: packet.to_canonical_json(),
                evidence_packet,
                default=None,
            )
            if _canonical_json is not None:
                evidence_json_target.write_text(_canonical_json, encoding="utf-8")
                evidence_written_to = str(evidence_json_target)
                if not json_mode:
                    click.echo(f"Wrote ChangeEvidence JSON to {evidence_written_to}")
        if markdown_companion_target and evidence_packet is not None:
            companion_md = (
                _run_check_ah(
                    "render_evidence_markdown",
                    _render_evidence_markdown,
                    evidence=evidence_packet,
                    commits=commits,
                    by_detector=by_detector,
                    review_suggestions=review_suggestions,
                    default="",
                )
                or ""
            )
            if companion_md:
                markdown_companion_target.parent.mkdir(parents=True, exist_ok=True)
                markdown_companion_target.write_text(companion_md, encoding="utf-8")
                markdown_companion_written_to = str(markdown_companion_target)
                if not json_mode:
                    click.echo(f"Wrote Markdown companion to {markdown_companion_written_to}")

    # W607-CA -- aggregation-phase ADDITIVE plumbing. Each step is wrapped
    # via ``_run_check_ca`` so a raise becomes a structured
    # ``pr_replay_<phase>_failed:`` marker on ``_w607ca_warnings_out`` and
    # the envelope still emits cleanly.
    #
    # The wraps below are POST-postmortem/by_detector/render so they re-probe
    # the assembled signals from the OUTER replay-level scope -- additive on
    # top of the inner _run_postmortem / _render_report calls already
    # performed via W607-AH. A raise on the inner call surfaces via W607-AH's
    # ``run_postmortem`` / ``render_report`` marker; a raise on the OUTER
    # additive re-probe surfaces via W607-CA's ``score_classify`` /
    # ``severity_normalize`` / ``compute_verdict`` / ``render_markdown``
    # markers. The two layers compose without shadowing.

    # W607-CA -- score_classify boundary. Additive re-probe of the replay's
    # risk classification over the assembled postmortem summary. Floor to
    # ``None`` so the score_classification "unknown" sentinel disambiguates
    # a degraded outcome from a real classification (mirror of cmd_pr_bundle
    # W607-BW + cmd_attest W607-BT score_classify pattern).
    def _classify_pr_replay_risk():
        # pr-replay's score signal is derived from the postmortem summary's
        # commit-with-findings ratio (or, when present, the summary's own
        # risk_level). Returns the canonical W631 short-code on success.
        risk_level_raw = summary.get("risk_level") if isinstance(summary, dict) else None
        if risk_level_raw:
            return str(risk_level_raw).lower()
        # Fall back to a coarse projection from commits-with-findings ratio.
        scanned = int(summary.get("commits_scanned") or len(commits) or 0)
        with_findings = int(summary.get("commits_with_findings") or 0)
        if scanned <= 0:
            return "low"
        ratio = with_findings / scanned
        if ratio >= 0.5:
            return "high"
        if ratio >= 0.2:
            return "medium"
        return "low"

    _ca_score_probe = _run_check_ca(
        "score_classify",
        _classify_pr_replay_risk,
        default=None,
    )
    _score_classification_state = "unknown" if _ca_score_probe is None else "classified"

    # W607-CA -- severity_normalize boundary. Additive ``normalize_risk_level``
    # + ``risk_rank`` over the inner-derived domain level. Floors to ``"low"``
    # / rank ``1`` so downstream comparators stay non-null. Mirror of
    # cmd_pr_bundle W607-BW + cmd_attest W607-BT severity_normalize.
    _ca_domain_level = _ca_score_probe if _ca_score_probe is not None else "low"
    _ca_canonical_raw = _run_check_ca(
        "severity_normalize",
        lambda level: normalize_risk_level(level) or "low",
        _ca_domain_level,
        default="low",
    )
    # Defensive coercion: a hostile sentinel from a downstream refactor of
    # ``normalize_risk_level`` could return an object whose ``__str__`` or
    # ``__format__`` raises (W978: don't let the type escape into JSON
    # serialization). Convert to a known-good str at the boundary; raises
    # here flow through the W607-CA wrap rather than crashing the CLI.
    _ca_canonical = _run_check_ca(
        "severity_normalize",
        lambda raw: str(raw) if isinstance(raw, str) else "low",
        _ca_canonical_raw,
        default="low",
    )
    _ca_rank = _run_check_ca(
        "severity_normalize",
        risk_rank,
        _ca_canonical,
        default=1,
    )

    # W607-CA -- compute_verdict boundary. Wraps the additive verdict-floor
    # build with the canonical risk_level suffix (LAW 6 standalone-parse).
    # W978 first-hypothesis check: the canonical floor MUST NOT re-format
    # the same ``_ca_canonical`` value that may have tripped the closure.
    # Use a literal "low" floor instead -- LAW 6 still holds (the line
    # works standalone; the W631 floor is "low"). Mirror of cmd_pr_bundle
    # W607-BW / cmd_attest W607-BT discipline.
    def _make_pr_replay_verdict_floor(canonical: str) -> str:
        # NOTE: the canonical f-string interpolation here is what would
        # raise if a downstream sentinel made __format__ throw. The
        # default= floor is a literal string, NOT this same closure.
        return f"pr-replay verdict (risk_level {canonical})"

    _ca_verdict_floor = _run_check_ca(
        "compute_verdict",
        _make_pr_replay_verdict_floor,
        _ca_canonical,
        default="pr-replay verdict (risk_level low)",
    )

    # W607-CA -- render_markdown boundary. PR-replay-specific re-probe of
    # the report_md projection. Inner _render_report already produced the
    # primary report markdown via W607-AH; the OUTER re-probe surfaces a
    # marker if a future refactor breaks the projection contract.
    # Closed-enum sentinel: "rendered" / "unknown".
    _ca_md_probe = _run_check_ca(
        "render_markdown",
        lambda md: len(md) if md else 0,
        report_md,
        default=None,
    )
    _render_markdown_state = "unknown" if _ca_md_probe is None else "rendered"

    if json_mode:
        extra_payload: dict = {
            "by_detector": by_detector,
            "commits": commits,
            "report_markdown": report_md,
        }
        if review_suggestions is not None:
            extra_payload["review_suggestions"] = review_suggestions
        # W607-AH -- substrate-CALL markers from this command's
        # ``_run_check_ah`` wraps (orthogonal to W590's producer-level
        # warnings, which come from the evidence-collector gatherers).
        # Merge into the existing top-level + summary ``warnings_out``
        # mirrors below. ``partial_success`` flips on non-empty bucket.
        # W590: surface producer-level warnings at the envelope level so
        # silent-fallback markers (e.g. lease policy gatherer's
        # ``leases: project_root_not_found``) flow into the JSON output
        # rather than only stderr. Always-emit (Pattern 2) — empty list
        # means "the producers ran cleanly", absent key means "we
        # didn't build an evidence packet" (the gatherers were never
        # invoked).
        if evidence_packet is not None:
            extra_payload["warnings_out"] = list(envelope_producer_warnings)
        # W607-AH + W607-CA: thread substrate-CALL + aggregation-phase
        # markers onto top-level ``warnings_out`` (combining with the W590
        # producer-level bucket when present). Empty buckets on the happy
        # path -> no surface change. Both share the canonical ``pr_replay_*``
        # family per the marker-prefix discipline; the additive bucket stays
        # distinguishable in tests + audits via the phase names.
        _combined_warnings_out: list[str] = (
            list(_w607ah_warnings_out) + list(_w607ca_warnings_out) + list(_w607dv_warnings_out)
        )
        if _combined_warnings_out:
            existing_top_wo = list(extra_payload.get("warnings_out") or [])
            extra_payload["warnings_out"] = existing_top_wo + list(_combined_warnings_out)

        # W259 — honest evidence-coverage banner, projected into the
        # JSON envelope as a top-level ``evidence_coverage`` block so
        # programmatic consumers (CI gates, dashboards) get the same
        # signal the Markdown banner conveys. ``None`` when no evidence
        # packet was built for this invocation (e.g. neither --evidence
        # nor --markdown / --evidence-bundle was passed).
        if evidence_packet is not None:
            extra_payload["evidence_coverage"] = _banner_envelope_block(evidence_packet)

        # W350 — surface ``authority_refs[]`` + permit count in the JSON
        # envelope so consumers don't have to parse ``report_markdown``
        # to recover the agentic-assurance authority axis. Permits flow
        # into the packet via ``authority_refs[authority_kind="permit"]``
        # (no top-level ``permits[]`` field on ChangeEvidence per W268);
        # the canonical mapping is enforced by the collector. Both keys
        # are always emitted when an evidence packet exists (Pattern-2
        # always-emit; an empty list reads as "no authorities" rather
        # than "we didn't look").
        authority_refs_payload: list[dict] = []
        authority_permits_count = 0
        if evidence_packet is not None:
            for ref in evidence_packet.authority_refs or ():
                row = {
                    "authority_kind": ref.authority_kind,
                    "authority_id": ref.authority_id,
                    "granted_by": ref.granted_by,
                    "source": ref.source,
                    "extra": dict(ref.extra or {}),
                }
                authority_refs_payload.append(row)
                if ref.authority_kind == "permit":
                    authority_permits_count += 1
            extra_payload["authority_refs"] = authority_refs_payload
            extra_payload["permits_count"] = authority_permits_count

        # W607-DV -- aggregation-LAYER plumbing for cmd_pr_replay. Sits ON
        # TOP of the W607-AH substrate-CALL layer + W607-CA aggregation-
        # phase layer. CA wrapped the risk_level / markdown projection /
        # auto_log axes; DV wraps the 8-question evidence-completeness
        # rollup + W276 INSUFFICIENT-tier classification + verdict
        # synthesis. The 4 DV phases compose without shadowing the 8 AH
        # substrate phases or the 6 CA aggregation phases.
        #
        # W607-DV -- completeness_classify boundary. Buckets the 8-question
        # evidence-completeness count into one of FOUR W276 tiers. Floor
        # returns the documented INSUFFICIENT shape so downstream consumers
        # still find ``state`` + ``complete_count`` on the envelope. W978
        # 5th-discipline: ``evidence_packet`` passed as raw arg; counting
        # / iteration lives INSIDE the closure.
        def _completeness_classify_run(_pkt):
            # No packet -> INSUFFICIENT (the gatherers were never invoked).
            if _pkt is None:
                return {
                    "state": "INSUFFICIENT",
                    "complete_count": 0,
                    "partial_count": 0,
                    "missing_count": 0,
                    "not_applicable_count": 0,
                }
            if not hasattr(_pkt, "evidence_completeness"):
                return {
                    "state": "INSUFFICIENT",
                    "complete_count": 0,
                    "partial_count": 0,
                    "missing_count": 0,
                    "not_applicable_count": 0,
                }
            _comp = _pkt.evidence_completeness()
            _complete = int(_comp.get("complete") or 0)
            _partial = int(_comp.get("partial") or 0)
            _missing = int(_comp.get("missing") or 0)
            _na = int(_comp.get("not_applicable") or 0)
            # W276 four-tier vocabulary: PASS / WARN / FAIL / INSUFFICIENT.
            # INSUFFICIENT is reserved for the no-packet / no-method case
            # above; the PASS / WARN / FAIL tiers map to evidence-completeness
            # counts. >=6 complete = PASS; >=4 complete = WARN; else FAIL.
            if _complete >= 6:
                _state = "PASS"
            elif _complete >= 4:
                _state = "WARN"
            else:
                _state = "FAIL"
            return {
                "state": _state,
                "complete_count": _complete,
                "partial_count": _partial,
                "missing_count": _missing,
                "not_applicable_count": _na,
            }

        _dv_completeness = _run_check_dv(
            "completeness_classify",
            _completeness_classify_run,
            evidence_packet,
            default={
                "state": "INSUFFICIENT",
                "complete_count": 0,
                "partial_count": 0,
                "missing_count": 0,
                "not_applicable_count": 0,
            },
        )

        # W607-DV -- completeness_rollup boundary. Rollup metrics dict
        # surfacing aggregate dimensions (banner tier + redaction-count
        # disclosure) so a downstream refactor of the rollup logic
        # surfaces a marker rather than crashing. The redaction count is
        # the W561-spirit "dropped-row disclosure" for the pr_replay
        # surface -- pr-replay doesn't have OSCAL-style dropped_enum_rows
        # but DOES carry ``redactions[]`` + producer_warnings as the
        # equivalent closed-enum drop visibility channel.
        # W978 5th-discipline: ``evidence_packet`` + ``envelope_producer_warnings``
        # passed as raw args; counting lives INSIDE the closure.
        def _completeness_rollup_run(_pkt, _producer_wo):
            _redaction_count = 0
            _banner_tier = None
            _q_states: dict = {}
            if _pkt is not None:
                _redactions = getattr(_pkt, "redactions", ()) or ()
                _redaction_count = len(_redactions)
                # Re-derive banner tier via the envelope-block helper so
                # the rollup row STAYS IN AGREEMENT with the W259 banner
                # the envelope already exposes. The helper is pure /
                # deterministic; both sites read the same packet.
                if hasattr(_pkt, "evidence_completeness"):
                    _comp = _pkt.evidence_completeness()
                    for _q in ("Q1", "Q2", "Q3", "Q4", "Q5", "Q6", "Q7", "Q8"):
                        _q_states[_q] = _comp.get(_q, "missing")
            _producer_warning_count = len(_producer_wo or ())
            return {
                "redaction_count": _redaction_count,
                "producer_warning_count": _producer_warning_count,
                "q_states": _q_states,
                "banner_tier": _banner_tier,
            }

        _dv_rollup = _run_check_dv(
            "completeness_rollup",
            _completeness_rollup_run,
            evidence_packet,
            envelope_producer_warnings,
            default={"redaction_count": 0, "producer_warning_count": 0, "q_states": {}, "banner_tier": None},
        )

        # W607-DV -- evidence_verdict_compose boundary. Synthesises the
        # canonical "N of 8 evidence questions answered" verdict suffix.
        # W978 1st-discipline: the floor MUST NOT re-interpolate the
        # same values that tripped the closure. W978 2nd-discipline:
        # ``default=`` is the literal LAW-6 floor "pr_replay completed".
        # LAW 6 standalone-parse: the line works without any other field.
        def _compose_evidence_verdict(_complete_count):
            return f"{_complete_count} of 8 evidence questions answered"

        _dv_verdict = _run_check_dv(
            "evidence_verdict_compose",
            _compose_evidence_verdict,
            _dv_completeness["complete_count"],
            default="pr_replay completed",
        )

        _pr_replay_env = json_envelope(
            "pr-replay",
            summary={
                "verdict": summary.get("verdict") or "no verdict",
                "tier": tier,
                "commit_range": commit_range,
                "client": client,
                "commits_scanned": summary.get("commits_scanned", len(commits)),
                "commits_with_findings": summary.get("commits_with_findings", 0),
                "top_detector": by_detector[0]["detector"] if by_detector else None,
                "output_path": output_path,
                "rehearsal": rehearsal,
                "rehearsal_dir": rehearsal_dir,
                "generated_at": generated_at,
                "engagement_logged_to": str(engagement_record) if engagement_record else None,
                "pdf_path": pdf_path,
                "pdf_backend": pdf_backend,
                "review_suggestions_present": review_suggestions is not None,
                "evidence_path": evidence_written_to,
                "markdown_path": markdown_companion_written_to,
                "evidence_content_hash": (evidence_packet.content_hash if evidence_packet else None),
                "evidence_coverage_tier": (
                    extra_payload.get("evidence_coverage", {}).get("tier") if evidence_packet is not None else None
                ),
                # W350: summary-level authority counters so a
                # CI gate reading only ``summary`` sees the
                # P1.10 axis without scanning ``authority_refs``.
                # ``None`` when no evidence packet was produced.
                "authority_refs_count": (
                    len(evidence_packet.authority_refs or ()) if evidence_packet is not None else None
                ),
                "permits_count": (authority_permits_count if evidence_packet is not None else None),
                # W607-CA aggregation-phase sentinels (closed-enum). Surface
                # the score / render projections so downstream consumers
                # see degradation lineage even when reading only the
                # summary block. Mirror of cmd_pr_bundle W607-BW
                # ``score_classification`` discipline.
                "score_classification": _score_classification_state,
                "render_markdown_state": _render_markdown_state,
                "risk_level_canonical": _ca_canonical,
                "risk_rank": _ca_rank,
                "verdict_floor": _ca_verdict_floor,
                # W607-DV aggregation-LAYER sentinels (closed-enum). Surface
                # the W276 completeness tier + the 8-question rollup +
                # the evidence-verdict suffix so a downstream consumer
                # reading ONLY the summary block sees the canonical
                # evidence-completeness signal. W978 7th-discipline anchor:
                # bare ``_dv_completeness["state"]`` lookup (floor dict
                # guarantees the key) -- NOT ``.get("state", expensive_default)``.
                "completeness_tier": _dv_completeness["state"],
                "evidence_complete_count": _dv_completeness["complete_count"],
                "evidence_partial_count": _dv_completeness["partial_count"],
                "evidence_missing_count": _dv_completeness["missing_count"],
                "evidence_not_applicable_count": _dv_completeness["not_applicable_count"],
                "redaction_count": _dv_rollup["redaction_count"],
                "producer_warning_count": _dv_rollup["producer_warning_count"],
                "evidence_verdict": _dv_verdict,
            },
            **extra_payload,
        )
        # W607-AH + W607-CA + W607-DV -- mirror substrate-CALL +
        # aggregation-phase + aggregation-LAYER markers onto
        # summary.warnings_out AND flip summary.partial_success when ANY
        # bucket is non-empty. Empty buckets on the happy path -> no
        # surface change (byte-identical envelope to the pre-W607
        # consumer). All three buckets share the canonical ``pr_replay_*``
        # marker family; the per-phase prefix keeps them distinguishable.
        _summary_combined: list[str] = (
            list(_w607ah_warnings_out) + list(_w607ca_warnings_out) + list(_w607dv_warnings_out)
        )
        if _summary_combined:
            _pr_replay_env.setdefault("summary", {})
            existing_summary_wo = list(_pr_replay_env["summary"].get("warnings_out") or [])
            _pr_replay_env["summary"]["warnings_out"] = existing_summary_wo + list(_summary_combined)
            _pr_replay_env["summary"]["partial_success"] = True
            # Top-level mirror: the initial ``extra_payload["warnings_out"]``
            # update above ran BEFORE the W607-DV phases executed (DV runs
            # AFTER the AH+CA bucket snapshot but BEFORE json_envelope).
            # The W607-DV markers landed in ``_w607dv_warnings_out`` AFTER
            # the top-level mirror was first set, so we mirror the full
            # combined bucket onto ``_pr_replay_env["warnings_out"]`` here
            # to keep top-level + summary parity. Append-only -- preserve
            # any pre-existing producer warnings already on top-level.
            existing_top_wo_for_dv = list(_pr_replay_env.get("warnings_out") or [])
            new_top_markers = [m for m in _summary_combined if m not in existing_top_wo_for_dv]
            if new_top_markers:
                _pr_replay_env["warnings_out"] = existing_top_wo_for_dv + new_top_markers

        # W607-CA -- serialize_envelope boundary. Additive ``json_envelope``
        # re-probe. A downstream schema-shape refactor that breaks the call
        # would otherwise crash AFTER all substrate + aggregation signals
        # were already gathered. The probe result is discarded -- the real
        # envelope is already built; the wrap exists to surface a marker on
        # raise. Mirror of cmd_pr_bundle W607-BW serialize_envelope discipline.
        _run_check_ca(
            "serialize_envelope",
            json_envelope,
            "pr-replay",
            default=None,
            summary={"verdict": "pr-replay ca-serialize probe"},
        )

        # W607-DV -- dv_serialize_envelope boundary. Additive
        # ``json_envelope`` re-projection over the DV-layer signals, with
        # a DISTINCT phase name from CA's ``serialize_envelope`` so the
        # per-phase marker prefix stays unambiguous. A future schema-shape
        # refactor that breaks the call surfaces a marker via
        # ``pr_replay_dv_serialize_envelope_failed:`` rather than crashing
        # the replay AFTER all substrate + aggregation signals were
        # already gathered. The probe result is discarded -- the real
        # envelope is already built; the wrap exists to surface a marker
        # on raise. Mirror of cmd_dead W607-DL serialize_envelope discipline.
        _run_check_dv(
            "dv_serialize_envelope",
            json_envelope,
            "pr-replay",
            default=None,
            summary={"verdict": "pr-replay dv-serialize probe"},
        )

        # W607-CA -- auto_log boundary. Wrap the auto_log call so a HMAC
        # chain misshape / filesystem failure surfaces a structured marker
        # rather than crashing the replay AFTER the envelope was already
        # built. Mirror of cmd_pr_bundle W607-BW + cmd_attest W607-BT
        # auto_log discipline.
        _pr_replay_target = (commit_range or "")[:80]
        _run_check_ca(
            "auto_log",
            auto_log,
            _pr_replay_env,
            action="pr-replay",
            target=_pr_replay_target,
            default=None,
        )

        # Re-thread the combined warnings_out in case auto_log /
        # serialize_envelope / dv_serialize_envelope raised AFTER the
        # marker channel was already snapshotted. Empty bucket (clean
        # wraps) -> envelope stays byte-identical to the version already
        # built above (W978: only touch when something actually appended).
        _post_combined: list[str] = list(_w607ah_warnings_out) + list(_w607ca_warnings_out) + list(_w607dv_warnings_out)
        if _post_combined and _post_combined != _summary_combined:
            _pr_replay_env.setdefault("summary", {})
            _pr_replay_env["summary"]["warnings_out"] = list(_post_combined)
            _pr_replay_env["summary"]["partial_success"] = True
            existing_top_wo_post = list(_pr_replay_env.get("warnings_out") or [])
            # Append only NEW markers (avoid double-emit for the bucket
            # that was already threaded above).
            new_markers = [m for m in _post_combined if m not in _summary_combined]
            _pr_replay_env["warnings_out"] = existing_top_wo_post + new_markers

        click.echo(to_json(_pr_replay_env))
        return

    if not output_path:
        click.echo(report_md)

    _ = EXIT_SUCCESS
    return
