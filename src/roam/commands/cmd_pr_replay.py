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

    # ── Subscription credit (paid tiers only) ─────────────────────────────
    # Pricing v4 promise: 50% of the engagement fee credits toward Roam
    # Review if the buyer subscribes within 60 days. Surfacing it inside
    # the deliverable is more credible than only on the marketing page.
    if tier in ("team", "deep"):
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

    # ── What's not in scope ────────────────────────────────────────────────
    if tier in ("team", "deep"):
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


def _render_pdf(markdown_text: str, output_path: Path) -> tuple[bool, str]:
    """Render the report markdown to a PDF at ``output_path``.

    Returns ``(success, backend_used_or_error_message)``.

    Prefers pandoc (better typography, native markdown awareness).
    Falls back to reportlab (pure-Python, simpler output) when pandoc
    is missing. If both are unavailable, returns ``(False, message)``
    so the caller can surface a useful error to the operator.
    """
    # ── Path 1: pandoc ────────────────────────────────────────────────────
    import shutil

    if shutil.which("pandoc"):
        import subprocess
        import tempfile

        with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False, encoding="utf-8") as tmp:
            tmp.write(markdown_text)
            tmp_path = tmp.name
        try:
            result = subprocess.run(
                ["pandoc", tmp_path, "-o", str(output_path), "--pdf-engine=xelatex"],
                capture_output=True,
                text=True,
                timeout=60,
            )
            if result.returncode == 0:
                return True, "pandoc"
            # pandoc may fail on systems without xelatex; retry default engine.
            result2 = subprocess.run(
                ["pandoc", tmp_path, "-o", str(output_path)],
                capture_output=True,
                text=True,
                timeout=60,
            )
            if result2.returncode == 0:
                return True, "pandoc"
            err = (result.stderr or result2.stderr or "").strip()[:200]
            # Fall through to reportlab.
            pandoc_err = f"pandoc failed: {err}"
        except (FileNotFoundError, subprocess.TimeoutExpired) as e:
            pandoc_err = f"pandoc invocation error: {e}"
        finally:
            Path(tmp_path).unlink(missing_ok=True)
    else:
        pandoc_err = "pandoc not on PATH"

    # ── Path 2: reportlab fallback ────────────────────────────────────────
    try:
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.styles import getSampleStyleSheet
        from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer
    except ImportError:
        return False, (
            f"PDF rendering unavailable: {pandoc_err} and reportlab not installed. "
            "Install pandoc (recommended) or `pip install reportlab`."
        )

    styles = getSampleStyleSheet()
    doc = SimpleDocTemplate(str(output_path), pagesize=A4)
    story: list = []
    # Reportlab is intentionally simple here — render markdown line-by-line
    # without parsing tables/code-fences. Pandoc is the preferred path; this
    # branch is the "any PDF is better than no PDF" safety net.
    for line in markdown_text.splitlines():
        if not line.strip():
            story.append(Spacer(1, 6))
            continue
        # Map a few markdown shapes to reportlab styles. Anything else =
        # body paragraph with HTML escaping.
        if line.startswith("# "):
            style = styles["Title"]
            text = line[2:]
        elif line.startswith("## "):
            style = styles["Heading1"]
            text = line[3:]
        elif line.startswith("### "):
            style = styles["Heading2"]
            text = line[4:]
        else:
            style = styles["BodyText"]
            text = line
        # Escape minimal HTML entities reportlab interprets.
        text = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        story.append(Paragraph(text, style))
    try:
        doc.build(story)
        return True, "reportlab"
    except Exception as e:  # noqa: BLE001 — defensive; report the error
        return False, f"reportlab build failed: {e}"


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
    help="Write the markdown report to PATH instead of stdout.",
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
@click.pass_context
def pr_replay_cmd(
    ctx,
    tier: str,
    commit_range: str | None,
    client: str | None,
    output_path: str | None,
    pdf_path: str | None,
    track_engagement: bool,
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

    # If --pdf is set without --output, derive the markdown sibling path so
    # the operator always has the editable source next to the PDF deliverable.
    if pdf_path and not output_path:
        output_path = str(Path(pdf_path).with_suffix(".md"))

    if output_path:
        Path(output_path).write_text(report_md, encoding="utf-8")
        if not json_mode:
            click.echo(f"Wrote {len(report_md):,} bytes to {output_path}")

    pdf_backend = None
    if pdf_path:
        ok, info = _render_pdf(report_md, Path(pdf_path))
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
                        "engagement_logged_to": str(engagement_record) if engagement_record else None,
                        "pdf_path": pdf_path,
                        "pdf_backend": pdf_backend,
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
