"""`roam ignore-drift` — find files git TRACKS despite a rule that claims to exclude them.

A ``.gitignore`` rule added *after* a file was committed does nothing to that
file: gitignore governs what git picks up, never what it already holds. So a
repo accumulates paths that every reader believes are excluded and that git is
in fact publishing on every push — old build output, a `memory/` corpus, a
`CREDENTIALS.md` under a rule that names it.

The natural audit for this is a false all-clear::

    git ls-files | xargs git check-ignore        # ALWAYS 0 hits. See below.

``git check-ignore`` silently skips paths that are in the index unless
``--no-index`` is passed, so the naive form reports clean on exactly the
defect it is hunting. Measured 2026-08-05 across a real estate: the naive
form found 0 everywhere; the ``--no-index`` form found 43 tracked-but-ignored
files in one repo and 27 in another — both previously "audited clean".

Two follow-on traps this command handles, both required for correctness:

1. ``check-ignore -v`` also prints paths whose matching rule is a **negation**
   (``!keep.log``). Those are NOT ignored and must not be reported. In one
   real repo this inflated the count from 27 to 29.
2. gitignore does not untrack. The remediation is ``git rm --cached <path>``,
   which leaves the file on disk. This command REPORTS; it never mutates the
   index.

Three-valued by construction — CLEAN / VIOLATION / UNANALYZABLE. An absent
measurement is UNKNOWN, never a benign CLEAN: that substitution is the bug
this command exists to catch, so it must not ship inside it.

Needs no roam index, no language support and no configuration — it works on
any git repo on the first run.

Output formats: text (default), ``--json``. SARIF is deliberately NOT
emitted because the finding is a file's *index membership*, not a location
inside that file — the offending artifact is a rule in a different file
(``.gitignore:183``), which SARIF's per-location model would attribute to a
line of source code that has nothing to do with the defect.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import click

from roam.capability import roam_capability
from roam.git_utils import worktree_git_env
from roam.output.formatter import format_table, json_envelope, to_json

# Git calls here are index/pattern reads over a whole worktree. 120s is far
# above the ~0.3s measured on a 4958-file repo; it exists so a wedged git
# becomes UNANALYZABLE rather than a hung gate.
_GIT_TIMEOUT_SECONDS = 120

# Per-path re-confirmation is O(candidates) subprocesses. Candidates are 0 in
# a clean repo and were 43 in the worst case ever measured, so the cap is
# never reached in practice — but a repo that blew past it would otherwise
# turn a report into a multi-minute stall. Above the cap we skip the second
# opinion and SAY SO (``per_path_confirmed: false``) rather than silently
# downgrading the evidence.
_PER_PATH_CONFIRM_CAP = 5000

_REMEDIATION_NOTE = (
    "gitignore does not untrack. Stop tracking with `git rm --cached <path>` — "
    "the file STAYS on disk. Never `git rm` without --cached: that deletes it."
)


# ---------------------------------------------------------------------------
# git plumbing
# ---------------------------------------------------------------------------


def _run_git(args: list[str], *, cwd: Path, stdin: bytes | None = None) -> subprocess.CompletedProcess[bytes]:
    """Run one git command in *cwd*, returning bytes. Never raises on rc != 0."""
    return subprocess.run(
        ["git", *args],
        cwd=str(cwd),
        input=stdin,
        capture_output=True,
        check=False,
        timeout=_GIT_TIMEOUT_SECONDS,
        env=worktree_git_env(cwd),
    )


def _decode(raw: bytes) -> str:
    # Git emits paths as raw bytes; under -z it does no quoting. surrogateescape
    # keeps a non-UTF-8 path round-trippable instead of dropping the finding.
    return raw.decode("utf-8", errors="surrogateescape")


def _split_nul(raw: bytes) -> list[str]:
    return [_decode(chunk) for chunk in raw.split(b"\0") if chunk]


def _unanalyzable(reason: str, **extra: object) -> dict:
    return {
        "status": "unanalyzable",
        "reason": reason,
        "git_root": None,
        "tracked_files": None,
        "candidates": None,
        "negation_excluded": None,
        "per_path_confirmed": False,
        "violations": [],
        **extra,
    }


def scan_tracked_ignored(start: Path | str | None = None) -> dict:
    """Return the tracked-but-ignored report for the git repo containing *start*.

    Pure function: takes an explicit directory, changes no global state, and
    is safe to call from parallel test workers. ``status`` is one of
    ``"clean"`` / ``"violation"`` / ``"unanalyzable"`` — there is no fourth
    value and no silent fallback to clean.
    """
    cwd = Path(start) if start is not None else Path.cwd()
    if not cwd.is_dir():
        return _unanalyzable(f"{cwd} is not a directory")

    # The repository root comes from git itself, NOT from roam's
    # find_project_root(): that helper trusts any `.git` entry it finds while
    # walking up, and a stray empty `.git` has hijacked whole test runs in this
    # repo before. For a question about git's own index, `git rev-parse
    # --show-toplevel` is the only authority — and the answer is published in
    # the envelope as `git_root` so the caller can see which repo was read.
    try:
        top = _run_git(["rev-parse", "--show-toplevel"], cwd=cwd)
    except FileNotFoundError:
        return _unanalyzable("git executable not found on PATH")
    except subprocess.TimeoutExpired:
        return _unanalyzable(f"git rev-parse timed out after {_GIT_TIMEOUT_SECONDS}s")
    except OSError as exc:
        return _unanalyzable(f"could not launch git: {exc}")

    if top.returncode != 0 or not top.stdout.strip():
        detail = _decode(top.stderr).strip().splitlines()
        hint = detail[0] if detail else f"git rev-parse exited {top.returncode}"
        return _unanalyzable(f"not a git repository (or git refused to answer): {hint}")

    root = Path(_decode(top.stdout).strip())

    try:
        listed = _run_git(["ls-files", "-z"], cwd=root)
        if listed.returncode != 0:
            hint = (_decode(listed.stderr).strip().splitlines() or [f"exited {listed.returncode}"])[0]
            return _unanalyzable(f"git ls-files failed: {hint}", git_root=str(root))
        tracked_raw = listed.stdout
        tracked = _split_nul(tracked_raw)

        # --no-index is LOAD-BEARING and must never be "simplified" away.
        #
        # Without it, `git check-ignore` skips every path that is in the index
        # — which is every path `git ls-files` can emit — so the whole pipeline
        # returns zero hits on a repo that is full of violations. It reports
        # CLEAN on precisely the defect it is hunting. Measured on the fixture
        # in tests/test_ignore_drift.py: the naive `git ls-files | xargs git
        # check-ignore` form finds 0 where this form finds 1, and
        # test_naive_form_without_no_index_finds_nothing pins that contrast so
        # a future contributor cannot restore the false all-clear.
        #
        # Two calls, deliberately:
        #   * plain -z  -> the authoritative ignored set. Git already drops
        #                  negation matches here (a `!rule` path is NOT
        #                  ignored), so this set is the verdict.
        #   * -z -v     -> rule provenance (source, line, pattern) for every
        #                  path that matched ANY rule, negations included.
        # Reporting the intersection is what keeps `!`-rule paths out of the
        # output while still naming `.gitignore:183` for the real ones.
        confirmed_run = _run_git(["check-ignore", "--no-index", "--stdin", "-z"], cwd=root, stdin=tracked_raw)
        verbose_run = _run_git(["check-ignore", "--no-index", "--stdin", "-z", "-v"], cwd=root, stdin=tracked_raw)
    except subprocess.TimeoutExpired:
        return _unanalyzable(f"git check-ignore timed out after {_GIT_TIMEOUT_SECONDS}s", git_root=str(root))
    except OSError as exc:
        return _unanalyzable(f"could not launch git: {exc}", git_root=str(root))

    # check-ignore: 0 = at least one path ignored, 1 = none. Anything else
    # (128 = fatal) is a failure to measure, not a clean answer.
    for label, proc in (("check-ignore", confirmed_run), ("check-ignore -v", verbose_run)):
        if proc.returncode not in (0, 1):
            hint = (_decode(proc.stderr).strip().splitlines() or [f"exited {proc.returncode}"])[0]
            return _unanalyzable(f"git {label} failed: {hint}", git_root=str(root))

    ignored_paths = set(_split_nul(confirmed_run.stdout))

    # -z -v emits flat NUL-separated 4-tuples: source, linenum, pattern, path.
    fields = _split_nul(verbose_run.stdout)
    provenance: dict[str, dict] = {}
    for i in range(0, len(fields) - 3, 4):
        source, linenum, pattern, path = fields[i : i + 4]
        provenance[path] = {"source": source, "line": linenum, "pattern": pattern}

    candidates = len(provenance)
    negation_excluded = sum(1 for path in provenance if path not in ignored_paths)

    reported = sorted(path for path in ignored_paths if path in provenance)
    # A path git calls ignored but for which -v named no rule cannot be
    # attributed, so it is reported without provenance rather than dropped.
    unattributed = sorted(path for path in ignored_paths if path not in provenance)

    per_path_confirmed = len(reported) + len(unattributed) <= _PER_PATH_CONFIRM_CAP
    violations: list[dict] = []
    for path in [*reported, *unattributed]:
        if per_path_confirmed:
            # Second opinion, per path, exactly as prescribed: `-q` exits 0
            # only when the path is genuinely ignored. This is what keeps a
            # negation-matched path out of the report even if a future git
            # changes what the batch form prints.
            try:
                probe = _run_git(["check-ignore", "--no-index", "-q", "--", path], cwd=root)
            except (OSError, subprocess.TimeoutExpired):
                per_path_confirmed = False
            else:
                if probe.returncode != 0:
                    negation_excluded += 1
                    continue
        rule = provenance.get(path)
        violations.append(
            {
                "path": path,
                "rule_source": rule["source"] if rule else None,
                "rule_line": rule["line"] if rule else None,
                "rule_pattern": rule["pattern"] if rule else None,
                "rule": (f"{rule['source']}:{rule['line']}" if rule else "unattributed"),
                "remediation": f"git rm --cached -- {path}",
                "message": (
                    f"{path} is tracked but "
                    + (
                        f"{rule['source']}:{rule['line']} ({rule['pattern']}) claims to exclude it"
                        if rule
                        else "git reports it as ignored (no rule could be attributed)"
                    )
                ),
            }
        )

    return {
        "status": "violation" if violations else "clean",
        "reason": None,
        "git_root": str(root),
        "tracked_files": len(tracked),
        "candidates": candidates,
        "negation_excluded": negation_excluded,
        "per_path_confirmed": per_path_confirmed,
        "violations": violations,
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _build_verdict(report: dict) -> str:
    status = report["status"]
    if status == "unanalyzable":
        return (
            f"UNANALYZABLE: the tracked-but-ignored check did not run ({report['reason']}). This is NOT a clean result."
        )
    tracked = report["tracked_files"]
    if status == "clean":
        verdict = f"CLEAN: 0 of {tracked} tracked files match an active ignore rule"
        if not report["per_path_confirmed"]:
            verdict += " — per-path re-confirmation was skipped, so this is a batch-only result"
        return verdict
    n = len(report["violations"])
    noun = "file" if n == 1 else "files"
    return f"VIOLATION: {n} tracked {noun} of {tracked} match an ignore rule that claims to exclude them"


@roam_capability(
    name="ignore-drift",
    category="reports",
    summary="Find files git tracks despite a .gitignore rule that claims to exclude them",
    maturity="stable",
    # No wrapper in mcp_server.py yet, and claiming exposure without one would
    # make `roam surface` report a tool that is not reachable.
    mcp_expose=False,
    mcp_preset=(),
    side_effect=False,
    task_required=False,
    destructive=False,
    # Nothing here reads the roam index, so nothing here can be stale.
    stale_sensitive=False,
    ai_safe=True,
    requires_index=False,
    inputs=("git_index", "gitignore_rules"),
    outputs=("tracked_ignored_report",),
    examples=(
        "roam ignore-drift",
        "roam ignore-drift --fail-on-found",
        "roam --json ignore-drift",
    ),
    tags=("git", "hygiene", "leak", "ci-gate"),
)
@click.command("ignore-drift")
@click.option(
    "--fail-on-found",
    is_flag=True,
    help="Exit with code 5 on VIOLATION or UNANALYZABLE (for CI / pre-push gates)",
)
@click.pass_context
def ignore_drift(ctx: click.Context, fail_on_found: bool) -> None:
    """Report files git tracks despite a `.gitignore` rule that claims to exclude them.

    A rule added after a file was committed never untracks it, so the file keeps
    publishing on every push while every reader believes it is excluded. The
    usual audit (`git ls-files | xargs git check-ignore`) reports clean on this
    defect always, because `check-ignore` skips indexed paths without
    `--no-index`.

    Reports only. It never touches your index — the fix it prints is yours to run.
    """
    json_mode = ctx.obj.get("json") if ctx.obj else False
    token_budget = ctx.obj.get("budget", 0) if ctx.obj else 0

    report = scan_tracked_ignored(Path.cwd())
    verdict = _build_verdict(report)
    status = report["status"]
    violations = report["violations"]
    # Named for the canonical disclosure token, not shortened: the
    # json and text branches must spell this signal the same way, or a
    # reader (and the W1331 scanner) cannot tell the text branch
    # discloses it at all.
    scan_incomplete = status == "unanalyzable"

    facts = [verdict]
    if not scan_incomplete:
        facts.append(f"{report['tracked_files']} tracked paths scanned")
        facts.append(f"{len(violations)} tracked files flagged as ignored")
        facts.append(f"{report['negation_excluded']} candidate paths excluded as negation rules")

    if json_mode:
        envelope = json_envelope(
            "ignore-drift",
            summary={
                "verdict": verdict,
                "status": status,
                "violations": len(violations),
                "tracked_files": report["tracked_files"],
                # Paths that matched ANY rule, negations included. `violations`
                # is what survived; the difference is published so a reader can
                # see the negation filter did work rather than assume it.
                "candidates": report["candidates"],
                "negation_excluded": report["negation_excluded"],
                "per_path_confirmed": report["per_path_confirmed"],
                "git_root": report["git_root"],
                "unanalyzable_reason": report["reason"],
                "scan_incomplete": scan_incomplete,
                "partial_success": scan_incomplete,
                "remediation": _REMEDIATION_NOTE,
            },
            budget=token_budget,
            findings=violations,
        )
        click.echo(to_json(envelope))
        if fail_on_found and (violations or scan_incomplete):
            from roam.exit_codes import GateFailureError

            raise GateFailureError(verdict)
        return

    click.echo(f"VERDICT: {verdict}")
    click.echo()
    if scan_incomplete:
        click.echo("  Nothing was measured, so nothing is proven clean.")
        click.echo("  Run this inside a git worktree with `git` on PATH and retry.")
        return

    click.echo(f"  repo: {report['git_root']}")
    if violations:
        click.echo()
        click.echo(
            format_table(
                ["Tracked path", "Claimed excluded by", "Rule"],
                [[v["path"], v["rule"], v["rule_pattern"] or "-"] for v in violations],
            )
        )
        click.echo()
        click.echo(f"  {_REMEDIATION_NOTE}")
        click.echo()
        click.echo("  Suggested (review before running — this command changed nothing):")
        for v in violations:
            click.echo(f"    {v['remediation']}")
    else:
        click.echo(f"  No tracked file matches an active ignore rule ({report['tracked_files']} tracked paths).")

    click.echo()
    # W1331: the text branch discloses the same caveats the envelope does — a
    # gap that reaches only --json is a gap the human reader never sees.
    click.echo(
        f"  {report['candidates']} tracked paths matched some rule; "
        f"{report['negation_excluded']} excluded because the matching rule is a negation (!pattern)."
    )
    if not report["per_path_confirmed"]:
        click.echo(
            "  NOTE: per-path re-confirmation was skipped (too many candidates or git refused) — "
            "these rows are a batch-only result."
        )

    if fail_on_found and violations:
        from roam.exit_codes import GateFailureError

        raise GateFailureError(verdict)
