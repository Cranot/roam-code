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
"""

from __future__ import annotations

import json as _json
from datetime import datetime, timezone
from pathlib import Path

import click
from click.testing import CliRunner

from roam.capability import roam_capability
from roam.commands.resolve import ensure_index
from roam.exit_codes import EXIT_SUCCESS
from roam.output.formatter import json_envelope, to_json

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


# ---------------------------------------------------------------------------
# Postmortem invocation (delegates the heavy lifting).
# ---------------------------------------------------------------------------


def _run_postmortem(commit_range: str, *, limit: int) -> dict:
    """Invoke ``roam --json postmortem <range> --limit N`` in-process.

    Returns the parsed JSON envelope. On any error, returns an envelope
    with empty ``commits`` so the renderer can still emit a sensible
    "no findings" report rather than crashing on the buyer.
    """
    from roam.cli import cli

    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["--json", "postmortem", commit_range, "--limit", str(limit)],
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


def _aggregate_by_detector(commits: list[dict]) -> list[dict]:
    """Roll up per-commit ``kinds`` lists into a single ranked summary.

    Each commit's ``kinds`` is a list of ``"<detector> x<count>"`` strings
    produced by ``cmd_postmortem._short_finding_summary``. We re-parse,
    sum across commits, and emit a list ranked by total hits — that's
    the "what does Roam keep catching across this window" table.
    """
    totals: dict[str, int] = {}
    commits_per_detector: dict[str, int] = {}
    for c in commits:
        seen_in_this_commit: set[str] = set()
        for kind_str in c.get("kinds") or []:
            # Format: "detector_name xN"
            try:
                name, count = kind_str.rsplit(" x", 1)
                n = int(count)
            except (ValueError, AttributeError):
                continue
            totals[name] = totals.get(name, 0) + n
            if name not in seen_in_this_commit:
                commits_per_detector[name] = commits_per_detector.get(name, 0) + 1
                seen_in_this_commit.add(name)
    return [
        {
            "detector": name,
            "total_findings": total,
            "commits_with_finding": commits_per_detector.get(name, 0),
        }
        for name, total in sorted(totals.items(), key=lambda kv: -kv[1])
    ]


# ---------------------------------------------------------------------------
# Report renderer.
# ---------------------------------------------------------------------------


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

    # ── Header ────────────────────────────────────────────────────────────
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

    # ── Executive summary ─────────────────────────────────────────────────
    out.append("## Executive summary")
    out.append("")
    commits_scanned = summary.get("commits_scanned", len(commits))
    commits_with = summary.get("commits_with_findings", 0)
    total_high = summary.get("total_high", 0)
    total_medium = summary.get("total_medium", 0)

    # Reframe the headline: a clean window is a positive observation, not a
    # neutral scan-count. A flagged window leads with the count of PRs that
    # would have been blocked.
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

    # ── Detector breakdown ────────────────────────────────────────────────
    out.append("## What Roam would have flagged")
    out.append("")
    if not by_detector:
        out.append("_No detector hits across this window._")
        out.append("")
    else:
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

    # ── Per-PR breakdown ──────────────────────────────────────────────────
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
    else:
        cap = tier_meta["max_per_pr_findings_listed"] * 3  # don't list every PR for tiny tiers
        listed = flagged[:cap] if tier == "sample" else flagged
        out.append(f"Top {len(listed)} PRs ranked by severity (high → medium → total).")
        out.append("")
        out.append("| Date | SHA | Subject | High | Medium | Top hits |")
        out.append("|---|---|---|---:|---:|---|")
        for c in listed:
            subject = (c.get("subject") or "").replace("|", "/")[:60]
            kinds = c.get("kinds") or []
            kinds_cap = ", ".join(kinds[: tier_meta["max_per_pr_findings_listed"]])
            out.append(
                f"| {c.get('date', '?')} | `{c.get('short_sha', '?')}` | "
                f"{subject} | {c.get('high', 0)} | {c.get('medium', 0)} | {kinds_cap or '-'} |"
            )
        out.append("")

    # ── Per-detector deep-dive (Deep tier only, only when there are hits) ─
    if tier == "deep" and by_detector:
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
            matching = [c for c in commits if any(k.startswith(detector + " x") for k in (c.get("kinds") or []))]
            if not matching:
                continue
            out.append(f"### `{detector}` — {row['total_findings']} finding(s)")
            out.append("")
            for c in matching[:5]:
                subject = (c.get("subject") or "").replace("|", "/")[:80]
                out.append(f"- `{c.get('short_sha', '?')}` ({c.get('date', '?')}) — {subject}")
            if len(matching) > 5:
                out.append(f"- _… and {len(matching) - 5} more_")
            out.append("")

    # ── What to do with this ──────────────────────────────────────────────
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
    else:
        # Surface the top-3 detector classes, not just the single highest, so
        # the buyer can see whether the pattern is concentrated or diffuse.
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

    # ── Methodology ──────────────────────────────────────────────────────
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

    return "\n".join(out)


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
    tags=["audit", "review", "monetization", "demo"],
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
    help="Write the markdown report to PATH instead of stdout.",
)
@click.pass_context
def pr_replay_cmd(
    ctx,
    tier: str,
    commit_range: str | None,
    client: str | None,
    output_path: str | None,
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

    \b
    Output: markdown by default; ``roam --json pr-replay`` returns the full
    envelope (summary + commits + by_detector + report_markdown) for
    machine consumption.
    """
    json_mode = ctx.obj.get("json") if ctx.obj else False
    ensure_index()

    tier = tier.lower()
    tier_meta = _TIERS[tier]
    if commit_range is None:
        commit_range = f"HEAD~{tier_meta['default_count']}..HEAD"

    if tier == "sample":
        # Sample never carries a client name — that would imply paid framing.
        client = None

    postmortem = _run_postmortem(commit_range, limit=max(tier_meta["default_count"], 100))
    summary = postmortem.get("summary") or {}
    commits = postmortem.get("commits") or []
    by_detector = _aggregate_by_detector(commits)

    generated_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    report_md = _render_report(
        tier=tier,
        tier_meta=tier_meta,
        commit_range=commit_range,
        client=client,
        summary=summary,
        commits=commits,
        by_detector=by_detector,
        generated_at=generated_at,
    )

    if output_path:
        Path(output_path).write_text(report_md, encoding="utf-8")
        if not json_mode:
            click.echo(f"Wrote {len(report_md):,} bytes to {output_path}")

    if json_mode:
        click.echo(
            to_json(
                json_envelope(
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
                        "generated_at": generated_at,
                    },
                    by_detector=by_detector,
                    commits=commits,
                    report_markdown=report_md,
                )
            )
        )
        return

    if not output_path:
        click.echo(report_md)

    _ = EXIT_SUCCESS
    return
