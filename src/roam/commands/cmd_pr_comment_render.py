"""``roam pr-comment-render`` — render a PR comment from a pr-analyze envelope.

Reads a ``roam pr-analyze --json`` envelope (stdin or ``--input``) and
emits a markdown comment ready to post on GitHub / GitLab / plain
markdown. The Roam Agent Review GitHub App pipes pr-analyze straight
into this command and posts the result as a sticky PR comment.

Keeping the rendering logic in Python (instead of duplicating it in
the GitHub App's TypeScript) means a single place updates the comment
shape — and local engineers can dogfood the same comment they'd see
on a PR by piping ``roam pr-analyze --json | roam pr-comment-render``.
"""

from __future__ import annotations

import json as _json
import sys

import click

from roam.output.formatter import json_envelope, to_json

_GITHUB_LINK = "https://github.com/Cranot/roam-code"
_DOCS_LINK = "redacted"


DEFAULT_BASELINE_PATH = "_baseline_default_"  # sentinel — resolved to .roam/last-pr-analysis.json


def _read_envelope(input_file: str | None, from_baseline: bool) -> tuple[dict, bool]:
    """Load the pr-analyze envelope.

    Returns ``(envelope, loaded_from_baseline)``. The bool tells the renderer
    whether to prepend the "Last analysis was X days ago" line (C.1.lll).
    """
    loaded_from_baseline = False
    if from_baseline:
        from pathlib import Path

        baseline_path = Path(".roam") / "last-pr-analysis.json"
        if not baseline_path.exists():
            raise click.UsageError(
                f"--from-baseline: no baseline at {baseline_path}. Run `roam pr-analyze --save-baseline` first."
            )
        with baseline_path.open("r", encoding="utf-8") as f:
            text = f.read()
        loaded_from_baseline = True
    elif input_file:
        with open(input_file, encoding="utf-8") as f:
            text = f.read()
    elif not sys.stdin.isatty():
        text = sys.stdin.read()
    else:
        text = ""
    if not (text and text.strip()):
        raise click.UsageError(
            "No input provided. Pipe a `roam pr-analyze --json` envelope, or pass --input PATH, or use --from-baseline."
        )
    try:
        return _json.loads(text), loaded_from_baseline
    except _json.JSONDecodeError as exc:
        raise click.UsageError(f"Could not parse JSON envelope: {exc}") from exc


_VERDICT_LABELS: dict[str, str] = {
    "INTENTIONAL": "Verdict: INTENTIONAL (explicit marker)",
    "SAFE": "Verdict: SAFE",
    "REVIEW": "Verdict: REVIEW",
    "BLOCK": "Verdict: BLOCK",
}


def _signal_explanation(name: str, raw: dict) -> str:
    """One-line plain-English description for an AI-likelihood signal.

    Pulls observed numerators / denominators from the ``raw_metrics``
    block so the comment surfaces *why* the signal scored where it did,
    not just the number. Returns ``""`` when raw data is unavailable.
    """
    if not raw:
        return ""
    if name == "comment_density":
        ratio = raw.get("comment_ratio")
        if ratio is None:
            return ""
        pct = round(float(ratio) * 100)
        return f"  *comment-to-code ratio is {pct}% on added lines (LLMs over-explain).*"
    if name == "generic_naming":
        gn = raw.get("generic_function_names")
        nf = raw.get("new_functions")
        if gn is None or nf is None:
            return ""
        return f"  *{gn} of {nf} new function(s) use a generic prefix (handle_/process_/manage_/...).*"
    if name == "orphan_imports":
        oi = raw.get("orphan_imports")
        if oi is None:
            return ""
        return f"  *{oi} added import(s) have no corresponding usage in the diff body.*"
    if name == "test_coverage":
        ratio = raw.get("test_coverage_ratio")
        if ratio is None:
            return ""
        return f"  *test-to-non-test file ratio is {ratio:.2f} (low test coverage on this PR).*"
    if name == "add_remove_ratio":
        r = raw.get("add_remove_ratio")
        if r is None:
            return ""
        return f"  *added {r}× as many lines as removed (heavy-write pattern).*"
    if name == "function_size":
        return "  *function-size variance is at extremes (very tiny or very large stubs).*"
    if name == "placeholder_density":
        pc = raw.get("placeholder_count")
        if pc is None:
            return ""
        return f"  *{pc} TODO/FIXME/NotImplementedError/stub marker(s) on added lines.*"
    if name == "llm_phrase_density":
        lc = raw.get("llm_phrase_count")
        if lc is None:
            return ""
        return f"  *{lc} comment(s) match common LLM phrasings (e.g. 'we use this approach because...').*"
    if name == "suspicious_imports":
        sc = raw.get("suspicious_import_count")
        if sc is None:
            return ""
        return f"  *{sc} import(s) match LLM-hallucination patterns (numbered modules, mass typing imports).*"
    return ""


def _envelope_age_days(envelope: dict) -> int | None:
    """Compute days since envelope timestamp; ``None`` if untimed or unparseable."""
    import datetime as _dt

    ts = (envelope.get("_meta") or {}).get("timestamp")
    if not ts:
        return None
    try:
        dt = _dt.datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return (_dt.datetime.now(_dt.timezone.utc) - dt).days


def _delta_before_after(now: int, delta: int) -> str:
    """Render '(51 → 56, +5)' style drift comparison; empty when delta is 0."""
    if delta == 0:
        return ""
    before = now - delta
    return f" ({before} → {now}, {delta:+d})"


def _section_header(verdict: str, drift: dict, baseline_age_days: int | None) -> list[str]:
    """Title line + optional baseline-age line + verdict line."""
    out: list[str] = ["## Roam Agent Review", ""]
    if baseline_age_days is not None:
        if baseline_age_days == 0:
            out.append("_Rendered from `.roam/last-pr-analysis.json` (saved today)._")
        else:
            day_word = "day" if baseline_age_days == 1 else "days"
            out.append(f"_Rendered from `.roam/last-pr-analysis.json` (saved {baseline_age_days} {day_word} ago)._")
        out.append("")
    verdict_line = f"**{_VERDICT_LABELS.get(verdict, 'Verdict: ' + verdict)}**"
    if drift.get("verdict_changed") and drift.get("previous_verdict"):
        verdict_line += f" — was: {drift['previous_verdict']}"
    out.append(verdict_line)
    out.append("")
    return out


def _section_scores(summary: dict, drift: dict, blast: int, ai_score: int) -> list[str]:
    """Single line of pipe-separated score segments."""
    blast_delta = drift.get("blast_radius_delta") or 0 if drift else 0
    ai_delta = drift.get("ai_likelihood_delta") or 0 if drift else 0
    blast_seg = f"blast-radius **{blast}/100**" + _delta_before_after(blast, blast_delta)
    ai_seg = f"ai-likelihood **{ai_score}/100**" + _delta_before_after(ai_score, ai_delta)
    rv = summary.get("rule_violations", 0)
    if drift:
        rv_seg = (
            f"rule violations **{rv}** "
            f"(+{drift.get('new_violation_count', 0)} new, "
            f"-{drift.get('resolved_violation_count', 0)} resolved)"
        )
    else:
        rv_seg = f"rule violations **{rv}**"
    crit_seg = f"critique high-severity **{summary.get('high_severity_critique', 0)}**"
    return [f"{blast_seg} · {ai_seg} · {rv_seg} · {crit_seg}", ""]


def _section_drift_banner(drift: dict) -> list[str]:
    """Regression/improvement banner + previous-verdict link line."""
    if not drift or not (drift.get("regression") or drift.get("improvement")):
        return []
    out: list[str] = []
    if drift.get("regression"):
        out.append(
            "> **Regression vs previous analysis.** "
            f"blast {drift.get('blast_radius_delta', 0):+d} · "
            f"ai {drift.get('ai_likelihood_delta', 0):+d} · "
            f"new violations: {drift.get('new_violation_count', 0)}."
        )
    elif drift.get("improvement"):
        out.append(
            "> **Improvement vs previous analysis.** "
            f"blast {drift.get('blast_radius_delta', 0):+d} · "
            f"ai {drift.get('ai_likelihood_delta', 0):+d} · "
            f"resolved violations: {drift.get('resolved_violation_count', 0)}."
        )
    prev_ts = drift.get("baseline_timestamp")
    if prev_ts:
        prev_v = drift.get("previous_verdict") or "?"
        out.append(f"> _Previous: {prev_v} at {prev_ts}._")

    # B6 (C.1.ll) — per-rule drift surfaces "new rule fired this PR"
    # vs "existing rule's violation count grew" as distinct narratives.
    first_seen = drift.get("rules_first_seen") or []
    resolved = drift.get("rules_resolved_entirely") or []
    rule_changes = drift.get("rule_count_changes") or []
    if first_seen:
        rule_list = ", ".join(f"`{r}`" for r in first_seen[:5])
        more = f" (+{len(first_seen) - 5} more)" if len(first_seen) > 5 else ""
        out.append(f"> _Rules first seen this PR: {rule_list}{more}._")
    if resolved:
        rule_list = ", ".join(f"`{r}`" for r in resolved[:5])
        more = f" (+{len(resolved) - 5} more)" if len(resolved) > 5 else ""
        out.append(f"> _Rules resolved entirely vs prev: {rule_list}{more}._")
    for rc in rule_changes[:5]:
        out.append(f"> _`{rc['rule_id']}`: {rc['before']} → {rc['after']} ({rc['delta']:+d})._")
    out.append("")
    return out


def _section_concerns(concerns: list[dict]) -> list[str]:
    if not concerns:
        return []
    out = ["### Concerns", ""]
    for i, c in enumerate(concerns, 1):
        label = c.get("concern", "concern")
        score = c.get("score")
        if score is not None:
            label += f" ({score}/100)"
        out.append(f"{i}. **{label}**")
        evidence = (c.get("evidence", "") or "").strip()
        if evidence:
            out.append(f"   {evidence}")
        # redacted — surface matched_patterns when present so reviewers
        # see WHY the concern fired, not just THAT it fired. Quiet when absent.
        patterns = c.get("matched_patterns") or []
        if patterns:
            out.append(f"   _matched: {', '.join(str(p) for p in patterns)}_")
        out.extend(_render_context_lines(c.get("context_lines"), indent="   "))
    out.append("")
    return out


def _render_context_lines(context_lines, *, indent: str = "") -> list[str]:
    """D6: render an optional context_lines block as a fenced code snippet.

    Used by both the concerns section and the rule-violation section so any
    upstream that attaches `context_lines: list[str]` gets uniform rendering.
    """
    if not context_lines:
        return []
    cleaned = [str(ln).rstrip() for ln in context_lines if str(ln).strip()]
    if not cleaned:
        return []
    return [f"{indent}```", *(f"{indent}{ln}" for ln in cleaned), f"{indent}```"]


def _section_reviewers(suggested: list[dict]) -> list[str]:
    if not suggested:
        return []
    out = ["### Suggested reviewers", ""]
    for r in suggested[:5]:
        name = r.get("name", "?")
        score = r.get("score")
        source = r.get("source") or ""
        handle = f"@{name}" if name and name != "?" else "?"
        score_part = f" — {score}" if score is not None else ""
        source_part = f" ({source})" if source else ""
        out.append(f"- {handle}{score_part}{source_part}")
    out.append("")
    return out


def _section_rule_violations(rule_violations: list[dict]) -> list[str]:
    if not rule_violations:
        return []
    out = ["### Architecture rule violations", ""]
    block_v = [v for v in rule_violations if v.get("severity") == "BLOCK"]
    warn_v = [v for v in rule_violations if v.get("severity") in ("WARN", "WARNING")]
    for v in block_v[:5]:
        out.append(f"- **BLOCK** `{v['rule_id']}`: `{v['file']}` -> `{v['matched_import']}`")
        if v.get("description"):
            out.append(f"  *{v['description']}*")
        out.extend(_render_context_lines(v.get("context_lines"), indent="  "))
    for v in warn_v[:5]:
        out.append(f"- WARN `{v['rule_id']}`: `{v['file']}` -> `{v['matched_import']}`")
        out.extend(_render_context_lines(v.get("context_lines"), indent="  "))
    # redacted — when more than the first-5 limit of either tier
    # was truncated, summarise BY RULE so reviewers see the long tail at
    # a glance instead of guessing which rules dominate. The previous
    # "...and N more" line was truthful but uninformative.
    extra_block = max(0, len(block_v) - 5)
    extra_warn = max(0, len(warn_v) - 5)
    if extra_block or extra_warn:
        from collections import Counter

        tail = block_v[5:] + warn_v[5:]
        counts = Counter((v.get("severity") or "WARN", v.get("rule_id") or "?") for v in tail)
        chunks = [f"`{rid}` x{n} ({sev})" for (sev, rid), n in counts.most_common(5)]
        more_total = extra_block + extra_warn
        out.append(f"- _...{more_total} more violation(s): " + ", ".join(chunks) + "._")
    out.append("")
    return out


def _section_next_steps(next_steps: list[str]) -> list[str]:
    if not next_steps:
        return []
    out = ["### Next steps", ""]
    for step in next_steps:
        out.append(f"- {step}")
    out.append("")
    return out


def _section_top_signals(ai: dict) -> list[str]:
    """Collapsible block of top-3 AI-likelihood signals when score >= 50."""
    if (ai.get("score") or 0) < 50:
        return []
    signals = ai.get("signals") or {}
    weights = ai.get("weights") or {}
    raw = ai.get("raw_metrics") or {}
    top = sorted(signals.items(), key=lambda kv: -kv[1])[:3]
    if not top:
        return []
    out = ["<details><summary>Top AI-likelihood signals</summary>", ""]
    for name, val in top:
        weight = weights.get(name, 0)
        contribution = val * weight
        explanation = _signal_explanation(name, raw)
        out.append(f"- `{name}`: **{val}/100** ({contribution:.1f} pts at weight x{weight:.2f})")
        if explanation:
            out.append(f"  {explanation}")
    out.extend(["", "</details>", ""])
    return out


def _section_footer() -> list[str]:
    return [
        "---",
        f"<sub>Powered by [roam-code]({_GITHUB_LINK}) — Apache 2.0, "
        f"100% local. Customize thresholds in `.roam/rules.yml`. "
        f"[Docs]({_DOCS_LINK}).</sub>",
    ]


def _render_github_markdown(envelope: dict, include_links: bool, baseline_age_days: int | None = None) -> str:
    """Render the GitHub-flavored sticky comment.

    Single comment per PR; the GitHub App overwrites this on push so the
    latest analysis is always at the top of the thread. Each section is
    a small helper above so this coordinator stays at low complexity.
    """
    summary = envelope.get("summary") or {}
    rationale = envelope.get("rationale") or {}
    rule_violations = envelope.get("rule_violations") or []
    ai = envelope.get("ai_likelihood") or {}
    drift = envelope.get("drift") or {}

    verdict = summary.get("verdict") or "UNKNOWN"
    blast = summary.get("blast_radius") or 0
    ai_score = summary.get("ai_likelihood") or 0

    lines: list[str] = []
    lines.extend(_section_header(verdict, drift, baseline_age_days))
    lines.extend(_section_scores(summary, drift, blast, ai_score))
    lines.extend(_section_drift_banner(drift))
    summary_text = (rationale.get("summary_text") or "").strip()
    if summary_text:
        lines.extend([summary_text, ""])
    lines.extend(_section_concerns(rationale.get("concerns") or []))
    lines.extend(_section_reviewers(rationale.get("suggested_reviewers") or []))
    lines.extend(_section_rule_violations(rule_violations))
    lines.extend(_section_next_steps(rationale.get("next_steps") or []))
    lines.extend(_section_top_signals(ai))
    if include_links:
        lines.extend(_section_footer())

    while lines and not lines[-1]:
        lines.pop()
    return "\n".join(lines) + "\n"


def _render_plain(envelope: dict) -> str:
    """Render a plain-text variant for non-markdown surfaces (Slack thread, email)."""
    summary = envelope.get("summary") or {}
    rationale = envelope.get("rationale") or {}
    verdict = summary.get("verdict") or "UNKNOWN"
    lines = [
        f"Roam Agent Review — Verdict: {verdict}",
        "",
        f"blast-radius {summary.get('blast_radius', 0)}/100  "
        f"ai-likelihood {summary.get('ai_likelihood', 0)}/100  "
        f"rule violations {summary.get('rule_violations', 0)}",
        "",
        rationale.get("summary_text") or "",
    ]
    concerns = rationale.get("concerns") or []
    if concerns:
        lines.append("")
        lines.append("Concerns:")
        for i, c in enumerate(concerns, 1):
            label = c.get("concern", "concern")
            if c.get("score") is not None:
                label += f" ({c['score']}/100)"
            lines.append(f"  {i}. {label}")
            if c.get("evidence"):
                lines.append(f"     {c['evidence']}")
            # redacted — plain renderer also surfaces matched_patterns
            # so Slack/email threads get the same explainability as the
            # markdown surface.
            patterns = c.get("matched_patterns") or []
            if patterns:
                lines.append(f"     matched: {', '.join(str(p) for p in patterns)}")
            ctx = c.get("context_lines") or []
            for ln in ctx:
                snippet = str(ln).rstrip()
                if snippet:
                    lines.append(f"       | {snippet}")
    next_steps = rationale.get("next_steps") or []
    if next_steps:
        lines.append("")
        lines.append("Next steps:")
        for step in next_steps:
            lines.append(f"  - {step}")
    return "\n".join(lines) + "\n"


@click.command(name="pr-comment-render")
@click.option(
    "--input",
    "input_file",
    type=click.Path(exists=True, dir_okay=False),
    default=None,
    help="Read pr-analyze JSON envelope from file (default: stdin).",
)
@click.option(
    "--style",
    type=click.Choice(["github", "gitlab", "plain"], case_sensitive=False),
    default="github",
    show_default=True,
    help="Comment style. github + gitlab share the same markdown shape today.",
)
@click.option(
    "--include-links/--no-links",
    default=True,
    show_default=True,
    help="Append the small attribution + docs footer.",
)
@click.option(
    "--from-baseline",
    is_flag=True,
    help="Auto-load the envelope from .roam/last-pr-analysis.json (saved by pr-analyze --save-baseline).",
)
@click.pass_context
def pr_comment_render(
    ctx,
    input_file: str | None,
    style: str,
    include_links: bool,
    from_baseline: bool,
) -> None:
    """Render a markdown PR comment from a pr-analyze envelope.

    \b
    Examples:
      roam pr-analyze --json | roam pr-comment-render
      roam pr-comment-render --input analysis.json --style plain
      roam pr-comment-render --no-links

    The Roam Agent Review GitHub App uses this verbatim — a single
    sticky PR comment, edited on each push so the latest verdict
    stays at the top.
    """
    json_mode = ctx.obj.get("json") if ctx.obj else False
    envelope, loaded_from_baseline = _read_envelope(input_file, from_baseline)
    baseline_age = _envelope_age_days(envelope) if loaded_from_baseline else None

    if style.lower() in ("github", "gitlab"):
        rendered = _render_github_markdown(envelope, include_links, baseline_age_days=baseline_age)
    else:
        rendered = _render_plain(envelope)

    summary = envelope.get("summary") or {}
    if json_mode:
        click.echo(
            to_json(
                json_envelope(
                    "pr-comment-render",
                    summary={
                        "verdict": summary.get("verdict"),
                        "style": style,
                        "char_count": len(rendered),
                    },
                    markdown=rendered,
                )
            )
        )
    else:
        click.echo(rendered, nl=False)
