"""roam history-grep — through-history search with provenance.

Wraps ``git log -S/--pickaxe`` and emits, per pattern, the commits that
*introduced or removed* the literal string. Useful for postmortems
("when did this regex first appear?"), provenance investigations, and
auditing renames or deletions that no longer leave a trace in HEAD.

Output is grouped per pattern; each commit row carries author + date +
short SHA + summary. JSON envelope mirrors the text shape.

Output formats: text (default), ``--json``. SARIF is deliberately NOT
emitted because history-grep outputs are invocation-scoped git-history
commit rows (provenance + pickaxe trail) — not per-location violations.
See action.yml _SUPPORTED_SARIF allowlist + W1175-RESEARCH Bucket B
propagation plan + W1148 audit memo.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import click

from roam.capability import roam_capability
from roam.commands.resolve import ensure_index
from roam.db.connection import find_project_root
from roam.git_utils import worktree_git_env
from roam.output.formatter import json_envelope, to_json

# CP45/CP46 fail-loud sentinels. ``_git_pickaxe`` / ``_diff_polarity`` previously
# swallowed ``FileNotFoundError`` (git absent) AND non-zero return codes (real
# git error) into the same empty-result shape used for "no commits matched",
# so an agent reading the envelope could not distinguish "string has no history"
# from "git is broken/missing". We now thread a typed error string through the
# subprocess wrappers and surface it on the envelope as ``git_errors[]`` so the
# lineage is loud (per the "Make fallback chains loud" rule in CLAUDE.md).
_GIT_MISSING = "git_not_available"
_GIT_TIMEOUT = "git_timeout"
_GIT_ERROR = "git_error"


def _git_pickaxe(
    root: Path,
    pattern: str,
    *,
    fixed: bool,
    case_insensitive: bool,
    since: str | None,
    until: str | None,
    limit: int,
    paths: list[str],
) -> tuple[list[dict], str | None]:
    """Run ``git log -S<pattern>`` and parse the output.

    Returns ``(commits, error_kind)``. ``error_kind`` is ``None`` on a
    successful git invocation (regardless of whether any commits matched);
    otherwise one of the ``_GIT_*`` sentinels disclosing why no commits
    are reported. Callers MUST propagate the sentinel to the envelope so
    consumers can distinguish "no history" from "git unavailable".
    """
    cmd = ["git", "log", "--no-merges", f"-n{limit}", "--pretty=format:%H%x09%an%x09%aI%x09%s"]
    cmd.append("-G" if not fixed else "-S")
    cmd.append(pattern)
    if case_insensitive:
        cmd.append("--regexp-ignore-case")
    if since:
        cmd.append(f"--since={since}")
    if until:
        cmd.append(f"--until={until}")
    if paths:
        cmd.append("--")
        cmd.extend(paths)

    try:
        result = subprocess.run(
            cmd,
            cwd=str(root),
            capture_output=True,
            text=True,
            timeout=60,
            encoding="utf-8",
            errors="replace",
            env=worktree_git_env(root),
        )
    except FileNotFoundError:
        return [], _GIT_MISSING
    except subprocess.TimeoutExpired:
        return [], _GIT_TIMEOUT

    if result.returncode != 0:
        return [], _GIT_ERROR

    commits: list[dict] = []
    for line in result.stdout.splitlines():
        parts = line.split("\t", 3)
        if len(parts) < 4:
            continue
        sha, author, date, summary = parts
        commits.append(
            {
                "sha": sha,
                "short_sha": sha[:8],
                "author": author,
                "date": date,
                "summary": summary,
            }
        )
    return commits, None


def _diff_polarity(root: Path, sha: str, pattern: str, fixed: bool) -> tuple[str | None, str | None]:
    """Return ``(polarity, degrade_reason)``.

    ``polarity`` is one of 'introduced' / 'removed' / 'modified' / None
    (no occurrence match on either side of the diff). ``degrade_reason``
    is ``None`` on a successful git invocation (regardless of whether
    polarity was annotated); otherwise one of the canonical degrade
    sentinels disclosing why polarity could not be computed:
    ``polarity_git_missing`` / ``polarity_git_timeout`` /
    ``polarity_git_error``.

    W607-H: the previous shape collapsed all three failure modes into a
    silent ``None`` return — observationally indistinguishable from a
    successful git invocation that simply found no +/- match. Callers
    MUST propagate ``degrade_reason`` to ``warnings_out`` so the
    Pattern-2 contract holds.
    """
    cmd = ["git", "show", "--unified=0", "--no-color", sha]
    try:
        result = subprocess.run(
            cmd,
            cwd=str(root),
            capture_output=True,
            text=True,
            timeout=20,
            encoding="utf-8",
            errors="replace",
            env=worktree_git_env(root),
        )
    except FileNotFoundError:
        return None, "polarity_git_missing"
    except subprocess.TimeoutExpired:
        return None, "polarity_git_timeout"
    if result.returncode != 0:
        return None, "polarity_git_error"
    plus = 0
    minus = 0
    if fixed:
        needle = pattern
        for ln in result.stdout.splitlines():
            if ln.startswith("+") and not ln.startswith("+++") and needle in ln:
                plus += 1
            elif ln.startswith("-") and not ln.startswith("---") and needle in ln:
                minus += 1
    else:
        import re

        rx = re.compile(pattern)
        for ln in result.stdout.splitlines():
            if ln.startswith("+") and not ln.startswith("+++") and rx.search(ln):
                plus += 1
            elif ln.startswith("-") and not ln.startswith("---") and rx.search(ln):
                minus += 1
    if plus and not minus:
        return "introduced", None
    if minus and not plus:
        return "removed", None
    if plus or minus:
        return "modified", None
    return None, None


@roam_capability(
    name="history-grep",
    category="exploration",
    summary="Through-history search using git pickaxe (-S / -G)",
    maturity="stable",
    mcp_expose=True,
    mcp_preset=("core",),
    side_effect=False,
    task_required=False,
    destructive=False,
    stale_sensitive=True,
    ai_safe=True,
    requires_index=True,
)
@click.command("history-grep")
@click.argument("positional", required=False)
@click.option("-e", "--regex", "patterns", multiple=True, help="Pattern (repeatable).")
@click.option("-F", "--fixed-string", "fixed", is_flag=True, default=True, help="Literal mode (default).")
@click.option(
    "-E",
    "--regexp",
    "regexp_mode",
    is_flag=True,
    default=False,
    help="W421 — regex mode: switch git pickaxe from -S (literal substring) to -G (regex match across hunks). Slower on large histories.",
)
@click.option("-i", "--ignore-case", "ci", is_flag=True, help="Case-insensitive search.")
@click.option("--since", default=None, help="Only commits after this date (YYYY-MM-DD or relative).")
@click.option("--until", default=None, help="Only commits before this date.")
@click.option("-n", "limit", default=20, help="Max commits per pattern.")
@click.option("--polarity", is_flag=True, help="Annotate each commit as introduced/removed/modified (slower).")
@click.option(
    "-p",
    "--path",
    "paths",
    multiple=True,
    help="Restrict to these paths (repeatable).",
)
@click.pass_context
def history_grep_cmd(ctx, positional, patterns, fixed, regexp_mode, ci, since, until, limit, polarity, paths):
    """Through-history search using git pickaxe (-S / -G).

    Default mode is ``-S`` (literal substring across commit diffs). Pass
    ``-E`` / ``--regexp`` to switch to ``-G`` (regex match across hunks).
    Regex mode is slower on large histories — git has to re-evaluate the
    pattern against every changed line of every commit.

    Examples:

      \b
      roam history-grep "DATABASE_URL"
      roam history-grep -e foo -e bar --polarity
      roam history-grep "deprecated_api" --since 2024-01-01
      roam history-grep "Article 12" -p docs/
      roam history-grep -E "set(Item|Value)"               # W421 regex mode
    """
    json_mode = ctx.obj.get("json") if ctx.obj else False
    token_budget = ctx.obj.get("budget", 0) if ctx.obj else 0

    pats: list[str] = []
    if positional:
        pats.append(positional)
    pats.extend(patterns)
    pats = [p for p in pats if p]
    if not pats:
        # Pattern 1B/1C discipline: emit a structured envelope in JSON mode
        # so MCP wrappers see actionable state, not a raw COMMAND_FAILED.
        if json_mode:
            click.echo(
                to_json(
                    json_envelope(
                        "history-grep",
                        summary={
                            "verdict": "no patterns provided",
                            "state": "usage_error",
                            "partial_success": True,
                        },
                        status="usage_error",
                        isError=True,
                        error_code="USAGE_ERROR",
                        error="no patterns provided",
                        hint="Pass a positional pattern or -e/--regex.",
                    )
                )
            )
        else:
            click.echo("VERDICT: no patterns provided")
            click.echo("Pass a positional pattern or -e/--regex.")
        raise SystemExit(2)

    ensure_index()
    root = find_project_root()

    # W421 — -E/--regexp opts into regex mode (git pickaxe -G); default
    # stays literal (-S) for backward compatibility.
    fixed_mode = fixed and not regexp_mode

    # W607-H: Pattern-2 consumer-layer wiring — thread a warnings_out
    # bucket through the GIT-SUBPROCESS axis (pickaxe + diff-polarity).
    # cmd_history_grep's substrate is `git log -S/-G` + `git show` — a
    # distinct subprocess shape from cmd_grep's ripgrep / git-grep
    # fan-out (sealed at W607-G). Threading is COMPLEMENTARY to the
    # existing CP45/CP46 ``git_errors`` disclosure: ``git_errors`` names
    # the per-pattern pickaxe failure kind; ``warnings_out`` carries
    # both an outer-guard for unexpected exceptions on either subprocess
    # AND the previously-silent ``--polarity`` degrade reason (W805-DD
    # MEDIUM-class shape parity gap: --polarity was requested but the
    # diff-polarity subprocess failed silently, indistinguishable from
    # "no +/- match"). Marker family is ``history_*`` (NOT ``grep_*`` /
    # ``search_*`` / ``complete_*`` / ``semantic_*``) — closed-enum
    # discipline parity with W607-G's ``grep_*`` family.
    warnings_out: list[str] = []

    per_pattern: dict[str, list[dict]] = {}
    git_errors: dict[str, str] = {}
    for p in pats:
        try:
            commits, err_kind = _git_pickaxe(
                root,
                p,
                fixed=fixed_mode,
                case_insensitive=ci,
                since=since,
                until=until,
                limit=limit,
                paths=list(paths),
            )
        except Exception as exc:  # noqa: BLE001 — W607-H outer-guard
            # The inner ``_git_pickaxe`` swallows FileNotFoundError +
            # TimeoutExpired + rc!=0 into ``err_kind``. Anything else
            # (e.g. PermissionError on Windows) propagates — disclose
            # it loudly via warnings_out (complementary to git_errors).
            warnings_out.append(f"history_pickaxe_failed:{type(exc).__name__}:{exc}")
            commits, err_kind = [], _GIT_ERROR
        if err_kind is not None:
            git_errors[p] = err_kind
        if polarity:
            for c in commits:
                try:
                    pol, degrade = _diff_polarity(root, c["sha"], p, fixed_mode)  # W421
                except Exception as exc:  # noqa: BLE001 — W607-H outer-guard
                    warnings_out.append(f"history_polarity_failed:{type(exc).__name__}:{exc}")
                    pol, degrade = None, "polarity_git_error"
                c["polarity"] = pol
                if degrade is not None:
                    # W607-H + W805-DD shape-parity: the --polarity
                    # subprocess silently degraded (git missing / timed
                    # out / errored on `git show`). Disclose so an
                    # agent can distinguish "feature flag honored,
                    # nothing to annotate" from "feature flag silently
                    # broken on this commit".
                    warnings_out.append(f"history_polarity_degraded:{degrade}:sha={c.get('short_sha', c['sha'][:8])}")
        per_pattern[p] = commits

    total = sum(len(v) for v in per_pattern.values())
    found = sum(1 for v in per_pattern.values() if v)
    # CP45/CP46 lineage: when EVERY pattern hit a git-availability error the
    # right verdict is "git unavailable", not "0 commits across N patterns"
    # (which an agent reads as "this string has no history" — a silent SAFE
    # for a gate-like consumer). Disclose the dominant error kind in both the
    # verdict line and a top-level ``git_errors`` field on the envelope.
    if git_errors and len(git_errors) == len(pats):
        # All patterns failed for the same reason — the underlying git invocation
        # is broken / missing. Lift the failure into the verdict.
        kinds = set(git_errors.values())
        kind_label = next(iter(kinds)) if len(kinds) == 1 else "git_error"
        verdict = f"history search unavailable: {kind_label}"
    else:
        verdict = f"{total} commit(s) across {found}/{len(pats)} pattern(s)"
        if git_errors:
            verdict += f" — {len(git_errors)} pattern(s) failed: see git_errors"

    if json_mode:
        _summary: dict = {
            "verdict": verdict,
            "patterns": len(pats),
            "total_commits": total,
            "partial_success": bool(git_errors),
        }
        # W607-H: non-empty bucket → summary mirror + partial_success
        # flip + top-level mirror. Empty bucket → byte-identical
        # envelope (hash-stable). ``warnings_out`` is complementary to
        # the CP45/CP46 ``git_errors`` field: ``git_errors`` survives
        # for per-pattern pickaxe failure kinds; ``warnings_out``
        # carries outer-guard exceptions + polarity-degrade lineage.
        extra: dict = {}
        if warnings_out:
            _summary["warnings_out"] = list(warnings_out)
            _summary["partial_success"] = True
            extra["warnings_out"] = list(warnings_out)
        click.echo(
            to_json(
                json_envelope(
                    "history-grep",
                    budget=token_budget,
                    summary=_summary,
                    patterns=list(pats),
                    git_errors=git_errors or None,
                    results=[{"pattern": p, "commits": commits} for p, commits in per_pattern.items()],
                    **extra,
                )
            )
        )
        return

    click.echo(f"VERDICT: {verdict}")
    click.echo()
    for p in pats:
        commits = per_pattern[p]
        err = git_errors.get(p)
        if err:
            click.echo(f"--- {p} — git error: {err} ---")
            click.echo()
            continue
        click.echo(f"--- {p} — {len(commits)} commit(s) ---")
        if not commits:
            click.echo("  (no history)")
            click.echo()
            continue
        for c in commits:
            tag = f" [{c['polarity']}]" if c.get("polarity") else ""
            click.echo(f"  {c['short_sha']}  {c['date'][:10]}  {c['author']}{tag}")
            click.echo(f"    {c['summary']}")
        click.echo()
