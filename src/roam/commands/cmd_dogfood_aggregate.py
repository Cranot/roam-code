"""``roam dogfood-aggregate`` — triage view over the dogfood eval corpus.

Walks ``internal/dogfood/evals/`` (or an explicit ``--path``), parses each eval's
YAML frontmatter + findings table, and emits a backlog summary:

- totals (evals, findings) bucketed by severity (H/M/L)
- per-command counts (top-N)
- by-status breakdown (open / fixed-in-* / wontfix / obsoleted)
- per-finding rows sortable by severity

Default behavior shows ONLY ``open`` findings (the backlog view). Use ``--all``
to include resolved findings, or ``--status fixed-in-12.51`` to inspect what a
specific release closed. Evals lacking a ``status:`` field are treated as
``open`` for backward compatibility.

Closes Gap C from `the deep-dive notes` — the resolution
feedback loop. See `the implementation notes` Task 3.

Output formats: text (default), ``--json``. SARIF is deliberately NOT
emitted because dogfood-aggregate outputs are invocation-scoped
eval-corpus summary records — not per-location violations.
``dogfood-aggregate`` delegates SARIF emission to composed subcommands
when their own ``--sarif`` flag fires directly. See action.yml
_SUPPORTED_SARIF allowlist + W1145 / W1175-RESEARCH Bucket B
propagation plan + W1148 audit memo.
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Iterable

import click

from roam.capability import roam_capability
from roam.db.connection import find_project_root
from roam.output.formatter import json_envelope, to_json

# ---------------------------------------------------------------------------
# Frontmatter + findings-table parsing
# ---------------------------------------------------------------------------

_DEFAULT_STATUS = "open"
_VALID_SEVERITIES = ("H", "M", "L")

# Pattern 3a alias map (W1005-followup-G): canonical W547 severity tokens
# projected onto the H/M/L short-code emit vocab. Lets a canonical-aware
# agent pass ``--severity high`` (or ``critical``, ``error``, ``medium``,
# ``low``, ``info``, ``note``) without hitting a click usage error. Projection
# rationale mirrors :func:`roam.output._severity.severity_to_confidence_level`
# (the W565 closed table):
#
# * ``critical`` / ``error`` / ``high`` -> ``H`` -- the W547 tiers that gate
#   CI by default (SARIF level=error or rank>=4) map onto the short-code
#   blocker tier H (eval rows the user previously marked as blockers).
# * ``warning`` / ``medium`` -> ``M`` -- the W547 middle tiers (rank 3 / 2)
#   project onto the short-code mid tier M (eval rows the user marked as
#   warnings worth investigating).
# * ``info`` / ``low`` / ``note`` -> ``L`` -- the W547 floor (rank 0 / 1 / 0)
#   projects onto the short-code floor L (eval rows the user marked as
#   informational/observational).
#
# This is a one-way projection: emit strings stay H/M/L (every ``f["sev"]``
# in the JSON envelope is one of "H"/"M"/"L"; ``by_severity`` keys are
# H/M/L), only INPUT parsing is widened. See module docstring + W1004
# disclosure note above the command for the closed-enum rationale.
_CANONICAL_TO_SHORTCODE: dict[str, str] = {
    "critical": "H",
    "error": "H",
    "high": "H",
    "warning": "M",
    "medium": "M",
    "info": "L",
    "low": "L",
    "note": "L",
}


def _project_severity_input(severity: str) -> str:
    """Project a ``--severity`` input token onto the H/M/L emit vocab.

    Accepts short-code tokens (``H``/``M``/``L``) as identity AND the W547
    canonical 7-token vocab via :data:`_CANONICAL_TO_SHORTCODE`. Case-
    insensitive. Unknown labels collapse to ``M`` (mid-tier default) so a
    typo never accidentally hides every row OR widens to every row.
    """
    key = severity.strip().upper()
    if key in _VALID_SEVERITIES:
        return key
    lower = severity.strip().lower()
    return _CANONICAL_TO_SHORTCODE.get(lower, "M")


_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)
_YAML_KV_RE = re.compile(r"^(\w[\w-]*)\s*:\s*(.+?)\s*$", re.MULTILINE)


def _parse_frontmatter_yaml(text: str) -> dict[str, str]:
    """Tiny frontmatter parser — flat key:value pairs.

    Two-engine design: PyYAML first when importable (handles quoted strings,
    escapes, anchors, etc.); fall back to a regex sweep that handles the
    simple shape used in eval files when PyYAML is missing OR when PyYAML
    rejects the input as malformed.

    The fallback is intentional — eval files are hand-edited Markdown
    frontmatter and a stray colon in an `observation:` line is common. The
    regex engine extracts whatever flat-shape keys it can; the goal is
    "best-effort, never crash" for a dev-facing triage tool.

    Narrow exception set (W1053): we catch the PyYAML-specific failure
    modes (ImportError when PyYAML is absent, YAMLError when content is
    malformed, AttributeError as defence against trimmed PyYAML stubs)
    but let process-control exceptions (KeyboardInterrupt, SystemExit,
    MemoryError) propagate. The bare `except Exception` it replaced
    swallowed those too.
    """
    try:  # PyYAML is in the mcp extras + commonly installed, but stay defensive.
        import yaml  # type: ignore

        try:
            data = yaml.safe_load(text)
        except yaml.YAMLError:
            # Malformed YAML frontmatter — fall through to regex engine.
            data = None
        if isinstance(data, dict):
            return {str(k): "" if v is None else str(v) for k, v in data.items()}
    except (ImportError, AttributeError):
        # PyYAML missing or its module-level symbols absent — fall through.
        pass

    result: dict[str, str] = {}
    for m in _YAML_KV_RE.finditer(text):
        key = m.group(1).lower().strip()
        # Skip lines that begin with `#` (YAML comments inside frontmatter).
        if key.startswith("#"):
            continue
        value = m.group(2).strip()
        # Strip optional surrounding quotes.
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
            value = value[1:-1]
        result[key] = value
    return result


def _eval_status(frontmatter: dict[str, str]) -> str:
    """Return the eval's status, defaulting to ``open`` when absent."""
    raw = frontmatter.get("status", "").strip()
    return raw or _DEFAULT_STATUS


def _parse_findings_table(body: str) -> list[dict[str, str]]:
    """Extract findings rows from the Markdown table in an eval body.

    Returns a list of dicts with keys: ``num``, ``sev``, ``type``,
    ``observation``, ``suggestion``. Rows whose severity is not H/M/L are
    skipped (they're spacer rows or partial entries).
    """
    findings: list[dict[str, str]] = []
    saw_header = False
    saw_separator = False
    for line in body.splitlines():
        stripped = line.strip()
        if not stripped.startswith("|"):
            # Reset if we've left a table block.
            if saw_header and saw_separator:
                # We may encounter another table later; keep scanning.
                pass
            saw_header = False
            saw_separator = False
            continue

        cells = [c.strip() for c in stripped.strip("|").split("|")]
        if not saw_header:
            # The header row should contain "Sev" (any case).
            joined = " ".join(c.lower() for c in cells)
            if "sev" in joined and ("type" in joined or "observation" in joined):
                saw_header = True
            continue
        if not saw_separator:
            # Markdown separator row: all cells are dashes (---, :---:, etc).
            if all(set(c) <= set("-: ") for c in cells if c):
                saw_separator = True
                continue
            # Not a real table; reset.
            saw_header = False
            continue

        # Data row. Expected columns: # | Sev | Type | Observation | Suggestion
        if len(cells) < 3:
            continue
        num = cells[0]
        sev = cells[1].strip().upper()
        if sev not in _VALID_SEVERITIES:
            # Spacer / unscored row — skip rather than miscount.
            continue
        findings.append(
            {
                "num": num,
                "sev": sev,
                "type": cells[2] if len(cells) > 2 else "",
                "observation": cells[3] if len(cells) > 3 else "",
                "suggestion": cells[4] if len(cells) > 4 else "",
            }
        )
    return findings


def _parse_eval_file(path: Path) -> dict | None:
    """Read one eval file → structured dict, or ``None`` if unparseable."""
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    m = _FRONTMATTER_RE.match(text)
    if not m:
        return None
    frontmatter = _parse_frontmatter_yaml(m.group(1))
    body = text[m.end() :]
    findings = _parse_findings_table(body)
    return {
        "file": str(path),
        "command": frontmatter.get("command", path.parent.name),
        "date": frontmatter.get("date", ""),
        "status": _eval_status(frontmatter),
        "fix_ref": frontmatter.get("fix_ref", ""),
        "verdict": frontmatter.get("verdict", ""),
        "task": frontmatter.get("task", ""),
        "findings": findings,
    }


def _iter_eval_files(root: Path) -> Iterable[Path]:
    """Yield every ``*.md`` under ``root`` except the conventional README/TEMPLATE."""
    for p in sorted(root.rglob("*.md")):
        name = p.name.lower()
        if name in {"readme.md", "template.md", "index.md"}:
            continue
        yield p


# ---------------------------------------------------------------------------
# Aggregation
# ---------------------------------------------------------------------------


def _matches_status(eval_status: str, wanted: tuple[str, ...]) -> bool:
    """True if ``eval_status`` matches any entry in ``wanted`` (case-insensitive)."""
    if not wanted:
        return True
    es = eval_status.lower()
    return any(es == w.lower() for w in wanted)


def _matches_severity(sev: str, wanted: tuple[str, ...]) -> bool:
    if not wanted:
        return True
    # Pattern 3a alias projection (W1005-followup-G): user input may be
    # H/M/L short-codes OR W547 canonical tokens (high/medium/low/etc.).
    # Project each wanted token through _CANONICAL_TO_SHORTCODE before
    # comparing against the emit-side H/M/L sev value. EMIT stays H/M/L
    # (sev was already H/M/L-validated at parse time via _VALID_SEVERITIES).
    projected = {_project_severity_input(w) for w in wanted}
    return sev.upper() in projected


def _matches_type(t: str, wanted: tuple[str, ...]) -> bool:
    if not wanted:
        return True
    t_lower = t.lower()
    return any(w.lower() in t_lower for w in wanted)


def _aggregate(
    evals: list[dict],
    *,
    status_filter: tuple[str, ...],
    severity_filter: tuple[str, ...],
    type_filter: tuple[str, ...],
    since: str,
    top_n: int,
) -> dict:
    """Compute the aggregate view, returning a dict ready for envelope assembly."""
    # by_status uses the FULL corpus (pre-filter) — that's what the user
    # actually wants to see at the top of the report.
    by_status_all: Counter[str] = Counter()
    for ev in evals:
        by_status_all[ev["status"]] += 1

    sev_total = Counter({"H": 0, "M": 0, "L": 0})
    per_command: Counter[str] = Counter()
    per_command_sev: dict[str, Counter[str]] = defaultdict(lambda: Counter({"H": 0, "M": 0, "L": 0}))
    flat_findings: list[dict] = []
    filtered_evals = 0

    for ev in evals:
        if not _matches_status(ev["status"], status_filter):
            continue
        if since and ev.get("date", "") < since:
            continue
        ev_findings = ev["findings"]
        # Apply severity/type filters at the FINDING level.
        kept = [
            f
            for f in ev_findings
            if _matches_severity(f["sev"], severity_filter) and _matches_type(f["type"], type_filter)
        ]
        if not kept:
            # Still count the eval as visible (no findings, but in scope) — only
            # if we didn't actually have severity/type filters. With explicit
            # filters, an eval with zero matching findings is dropped from the
            # visible count to avoid misleading totals.
            if severity_filter or type_filter:
                continue
        filtered_evals += 1
        for f in kept:
            sev_total[f["sev"]] += 1
            per_command[ev["command"]] += 1
            per_command_sev[ev["command"]][f["sev"]] += 1
            flat_findings.append(
                {
                    "command": ev["command"],
                    "status": ev["status"],
                    "date": ev["date"],
                    "sev": f["sev"],
                    "type": f["type"],
                    "observation": f["observation"],
                    "suggestion": f["suggestion"],
                    "file": ev["file"],
                }
            )

    total_findings = sum(sev_total.values())

    sev_order = {"H": 0, "M": 1, "L": 2}
    flat_findings.sort(key=lambda f: (sev_order.get(f["sev"], 9), f["command"]))

    top_commands = [
        {
            "command": cmd,
            "total": cnt,
            "H": per_command_sev[cmd]["H"],
            "M": per_command_sev[cmd]["M"],
            "L": per_command_sev[cmd]["L"],
        }
        for cmd, cnt in per_command.most_common(top_n)
    ]

    return {
        "evals_total": len(evals),
        "evals_in_view": filtered_evals,
        "findings_total": total_findings,
        "by_severity": dict(sev_total),
        "by_status_all": dict(by_status_all),
        "by_command_top": top_commands,
        "findings": flat_findings,
    }


def _format_filter_label(
    status_filter: tuple[str, ...],
    *,
    show_all: bool,
) -> str:
    if show_all:
        return "all"
    if not status_filter:
        return _DEFAULT_STATUS
    return ",".join(status_filter)


# ---------------------------------------------------------------------------
# Click command
# ---------------------------------------------------------------------------


@roam_capability(
    name="dogfood-aggregate",
    category="health",
    summary="Triage view over the dogfood eval corpus: totals, per-command, by-status.",
    inputs=["path"],
    outputs=["totals", "per_command", "findings", "verdict"],
    examples=["roam dogfood-aggregate", "roam dogfood-aggregate --status open"],
    tags=["dogfood", "triage"],
    ai_safe=True,
    requires_index=False,
    maturity="stable",
    mcp_expose=True,
    mcp_preset=("core",),
    side_effect=False,
    task_required=False,
    destructive=False,
    stale_sensitive=False,
)
# W1004 (audit follow-up to W996): --severity uses a closed Click.Choice enum.
# Unknown --severity raises a click usage error (exit 2). --status and --type
# intentionally accept free strings: --status reflects whatever values appear
# in eval frontmatter (open vocabulary authored by humans across sprints), and
# --type is documented as a substring filter, not an exact-match against a
# known type registry. Unknown --status / --type values therefore silently
# filter to zero matching findings (the verdict shows "0 findings" with the
# active filter label) rather than raising — same divergence rationale as
# cmd_smells: registry-derived / open vocabularies stay permissive, fixed
# enums hard-fail at parse.
#
# W1005-followup-G (Pattern 3a alias widening): the closed enum was H/M/L
# pre-widening. Post-W1005, sibling commands (cmd_smells / cmd_alerts /
# cmd_health / cmd_api_changes / etc.) accept the W547 canonical 7-token
# vocab; an agent fluent in canonical severity who typed --severity high
# would trip click usage error 2. The Choice now accepts BOTH short-codes
# AND canonical tokens; canonical tokens project onto H/M/L via
# :data:`_CANONICAL_TO_SHORTCODE` at filter time. EMIT vocab stays H/M/L
# (one-way projection — _VALID_SEVERITIES still gates row parsing).
@click.command(name="dogfood-aggregate")
@click.option(
    "--path",
    "evals_path",
    type=click.Path(),
    default=None,
    help="Directory of evals (default: <project>/internal/dogfood/evals/).",
)
@click.option(
    "--status",
    "status_filter",
    multiple=True,
    help="Filter by status; repeatable for OR semantics (e.g. --status open --status wontfix).",
)
@click.option(
    "--all",
    "show_all",
    is_flag=True,
    default=False,
    help="Show findings of every status (mutually exclusive with --status).",
)
@click.option(
    "--severity",
    "severity_filter",
    multiple=True,
    type=click.Choice(
        # Short-code emit vocab (H/M/L) + W547 canonical 7-tier (input only,
        # projected via _CANONICAL_TO_SHORTCODE). Pattern 3a alias widening
        # per W1005-followup-G -- canonical-aware agents pass any of them
        # without hitting click usage error 2. EMIT stays H/M/L.
        ["H", "M", "L", "critical", "error", "high", "warning", "medium", "low", "info", "note"],
        case_sensitive=False,
    ),
    help=(
        "Filter findings by severity (repeatable; OR semantics). Accepts "
        "short-codes {H, M, L} OR W547 canonical tokens {critical, error, "
        "high, warning, medium, low, info, note} -- canonical tokens project "
        "onto H/M/L (critical/error/high -> H; warning/medium -> M; "
        "info/low/note -> L)."
    ),
)
@click.option(
    "--type",
    "type_filter",
    multiple=True,
    help="Filter findings by type substring (e.g. wrong, missing, signal, noise).",
)
@click.option(
    "--since",
    "since",
    default="",
    help="Only include evals with frontmatter date >= this YYYY-MM-DD value.",
)
@click.option(
    "--top",
    "top_n",
    type=int,
    default=10,
    show_default=True,
    help="Show this many top commands by findings count.",
)
@click.option(
    "--limit",
    "limit",
    type=int,
    default=50,
    show_default=True,
    help="Cap the number of findings emitted in text mode (use 0 for no cap).",
)
@click.pass_context
def dogfood_aggregate(
    ctx,
    evals_path: str | None,
    status_filter: tuple[str, ...],
    show_all: bool,
    severity_filter: tuple[str, ...],
    type_filter: tuple[str, ...],
    since: str,
    top_n: int,
    limit: int,
) -> None:
    """Aggregate the dogfood eval corpus into a backlog/triage view.

    \b
    Examples:
      roam dogfood-aggregate                          # default: open only (backlog)
      roam dogfood-aggregate --all                    # include resolved findings
      roam dogfood-aggregate --status fixed-in-12.51  # what shipped in v12.51
      roam dogfood-aggregate --severity H             # only blockers
      roam dogfood-aggregate --type wrong             # only bugs
      roam --json dogfood-aggregate                   # full structured envelope
    """
    json_mode = ctx.obj.get("json") if ctx.obj else False

    # Mutual exclusion: --all OR --status, not both.
    if show_all and status_filter:
        raise click.UsageError("--all is mutually exclusive with --status")

    # Resolve the evals directory.
    if evals_path:
        root_path = Path(evals_path).expanduser().resolve()
    else:
        project_root = find_project_root(".")
        root_path = project_root / "internal" / "dogfood" / "evals"

    if not root_path.exists() or not root_path.is_dir():
        verdict = f"no evals directory at {root_path}"
        envelope_summary = {
            "verdict": verdict,
            "state": "no_evals",
            "partial_success": True,
            "evals_total": 0,
            "findings_total": 0,
            "by_status_all": {},
            "showing": _format_filter_label(status_filter, show_all=show_all),
            "evals_dir": str(root_path),
            "exists": False,
        }
        if json_mode:
            click.echo(
                to_json(
                    json_envelope(
                        "dogfood-aggregate",
                        summary=envelope_summary,
                        findings=[],
                        by_command_top=[],
                    )
                )
            )
            return
        click.echo(f"VERDICT: {verdict}")
        click.echo("Pass --path <dir> to point at an evals directory.")
        return

    evals: list[dict] = []
    parse_failures: list[str] = []
    for p in _iter_eval_files(root_path):
        parsed = _parse_eval_file(p)
        if parsed is None:
            parse_failures.append(str(p))
            continue
        evals.append(parsed)

    # Determine effective status filter.
    effective_status_filter: tuple[str, ...]
    if show_all:
        effective_status_filter = ()
    elif status_filter:
        effective_status_filter = status_filter
    else:
        effective_status_filter = (_DEFAULT_STATUS,)

    agg = _aggregate(
        evals,
        status_filter=effective_status_filter,
        severity_filter=severity_filter,
        type_filter=type_filter,
        since=since,
        top_n=top_n,
    )

    showing_label = _format_filter_label(status_filter, show_all=show_all)

    # Build the verdict line.
    sev_part = (
        f"H:{agg['by_severity'].get('H', 0)} M:{agg['by_severity'].get('M', 0)} L:{agg['by_severity'].get('L', 0)}"
    )
    by_status_part = " ".join(f"{k}:{v}" for k, v in sorted(agg["by_status_all"].items())) or "(none)"
    tail = f"showing: {showing_label}" + (
        " (use --all for resolved findings)" if not show_all and not status_filter else ""
    )
    verdict = (
        f"{agg['findings_total']} {showing_label} findings "
        f"across {agg['evals_in_view']}/{agg['evals_total']} evals "
        f"· {sev_part} · by status: {by_status_part} · {tail}"
    )

    parse_failure_count = len(parse_failures)
    summary = {
        "verdict": verdict,
        # Consistent with the rest of the W7/W8 sprint envelopes (runs,
        # next, memory): every dogfood-aggregate envelope carries both
        # ``state`` and ``partial_success`` so MCP consumers can branch
        # on completeness without having to inspect parse_failures.
        "state": ("partial_parse" if parse_failure_count > 0 else ("no_evals" if agg["evals_total"] == 0 else "ok")),
        "partial_success": parse_failure_count > 0 or agg["evals_total"] == 0,
        "evals_total": agg["evals_total"],
        "evals_in_view": agg["evals_in_view"],
        "findings_total": agg["findings_total"],
        "by_severity": agg["by_severity"],
        "by_status_all": agg["by_status_all"],
        "showing": showing_label,
        "filters": {
            "status": list(effective_status_filter),
            "severity": list(severity_filter),
            "type": list(type_filter),
            "since": since,
            "show_all": show_all,
        },
        "evals_dir": str(root_path),
        "parse_failures": parse_failure_count,
    }

    if json_mode:
        click.echo(
            to_json(
                json_envelope(
                    "dogfood-aggregate",
                    summary=summary,
                    findings=agg["findings"],
                    by_command_top=agg["by_command_top"],
                    parse_failures=parse_failures,
                )
            )
        )
        return

    # Text mode — concise, ASCII only.
    click.echo(f"VERDICT: {verdict}")
    click.echo()
    click.echo(f"Evals dir: {root_path}")
    if parse_failures:
        click.echo(f"Parse failures: {len(parse_failures)} (see --json for paths)")
    click.echo()

    # Top-N commands.
    if agg["by_command_top"]:
        click.echo(f"Top {len(agg['by_command_top'])} commands by findings:")
        for row in agg["by_command_top"]:
            click.echo(f"  {row['command']:<32} {row['total']:>3}  H:{row['H']} M:{row['M']} L:{row['L']}")
        click.echo()

    # Findings list (capped by --limit; 0 = unlimited).
    findings = agg["findings"]
    cap = len(findings) if limit <= 0 else min(limit, len(findings))
    if findings:
        click.echo(f"Findings ({cap} of {len(findings)} shown):")
        for f in findings[:cap]:
            obs = f["observation"]
            if len(obs) > 90:
                obs = obs[:87] + "..."
            click.echo(f"  [{f['sev']}] {f['command']:<24} {f['type']:<8} ({f['status']}) {obs}")
        if cap < len(findings):
            click.echo(f"  ... ({len(findings) - cap} more — use --json or --limit 0)")
    else:
        click.echo("No findings match the current filters.")
