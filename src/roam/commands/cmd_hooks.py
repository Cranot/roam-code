"""Git hook integration for automatic re-indexing after git operations.

Output formats: text (default), ``--json``. SARIF is deliberately NOT
emitted because ``roam hooks`` is a setup/bootstrap command — its
output is human-facing setup status (hooks installed/uninstalled into
``.git/hooks``), not analysis findings with file:line coordinates.
SARIF is reserved for scanning results. See action.yml
_SUPPORTED_SARIF allowlist + W1175-RESEARCH propagation plan +
W1148 audit memo.
"""

from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

import click

from roam.capability import roam_capability
from roam.output.formatter import json_envelope, to_json

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Names of hooks managed by roam
_HOOK_NAMES = ("post-merge", "post-checkout", "post-rewrite")

# Marker lines used to delimit the roam section when appending to existing hooks
_MARKER_BEGIN = "# BEGIN roam-code auto-indexing"
_MARKER_END = "# END roam-code auto-indexing"

# The roam section content (without markers)
_ROAM_HOOK_BODY = """\
if command -v roam >/dev/null 2>&1; then
    roam index --quiet 2>/dev/null &
fi"""

# Full standalone hook script (written when the hook file does not exist yet)
_HOOK_SCRIPT_TEMPLATE = """\
#!/bin/sh
{begin}
{body}
{end}
"""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _find_git_hooks_dir() -> Path | None:
    """Locate the .git/hooks directory for the current project.

    Tries ``git rev-parse --git-dir`` first, then falls back to walking up the
    directory tree looking for a ``.git`` directory.  Returns *None* if no git
    repository is found.
    """
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--git-dir"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        if result.returncode == 0:
            git_dir = Path(result.stdout.strip())
            if not git_dir.is_absolute():
                git_dir = Path.cwd() / git_dir
            return git_dir / "hooks"
    except FileNotFoundError:
        pass  # git not installed — fall through

    # Manual walk-up fallback
    current = Path.cwd()
    for parent in [current, *current.parents]:
        candidate = parent / ".git"
        if candidate.is_dir():
            return candidate / "hooks"
        if candidate.is_file():
            # Worktree or submodule: .git is a file pointing to the real dir
            try:
                content = candidate.read_text(encoding="utf-8").strip()
                if content.startswith("gitdir:"):
                    real_git = Path(content[len("gitdir:") :].strip())
                    if not real_git.is_absolute():
                        real_git = parent / real_git
                    return real_git / "hooks"
            except OSError as _exc:
                # An unreadable worktree/submodule .git file is skipped —
                # surface lineage so a missed hooks dir has a cause.
                from roam.observability import log_swallowed

                log_swallowed("cmd_hooks:worktree_gitdir", _exc)

    return None


def _make_executable(path: Path) -> None:
    """Set executable bit on *path* (no-op on Windows)."""
    if sys.platform != "win32":
        current_mode = path.stat().st_mode
        path.chmod(current_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def _roam_section_present(content: str) -> bool:
    """Return True if the roam marker section is already in *content*."""
    return _MARKER_BEGIN in content and _MARKER_END in content


def _insert_roam_section(content: str) -> str:
    """Append the roam section to existing hook *content* (with markers)."""
    section = f"\n{_MARKER_BEGIN}\n{_ROAM_HOOK_BODY}\n{_MARKER_END}\n"
    if not content.endswith("\n"):
        content += "\n"
    return content + section


def _remove_roam_section(content: str) -> str:
    """Strip the roam marker section from *content* (if present)."""
    if not _roam_section_present(content):
        return content
    lines = content.splitlines(keepends=True)
    out: list[str] = []
    inside = False
    for line in lines:
        stripped = line.rstrip("\r\n")
        if stripped == _MARKER_BEGIN:
            inside = True
            # Also remove the preceding blank line if we added one
            if out and out[-1].strip() == "":
                out.pop()
            continue
        if stripped == _MARKER_END:
            inside = False
            continue
        if not inside:
            out.append(line)
    return "".join(out)


def _hook_has_roam(hook_path: Path) -> bool:
    """Return True if *hook_path* contains the roam section marker."""
    if not hook_path.exists():
        return False
    try:
        content = hook_path.read_text(encoding="utf-8", errors="replace")
        return _roam_section_present(content)
    except OSError:
        return False


def _install_hook(hook_path: Path, force: bool) -> tuple[str, str | None]:
    """Install roam auto-indexing into a single hook file.

    Returns a tuple of (action, error):
      action -- one of "created", "appended", "skipped", "overwritten"
      error  -- None on success, error string on failure
    """
    try:
        if not hook_path.exists():
            # Write a fresh standalone script
            content = _HOOK_SCRIPT_TEMPLATE.format(
                begin=_MARKER_BEGIN,
                body=_ROAM_HOOK_BODY,
                end=_MARKER_END,
            )
            hook_path.write_text(content, encoding="utf-8")
            _make_executable(hook_path)
            return "created", None

        # File exists
        existing = hook_path.read_text(encoding="utf-8", errors="replace")

        if _roam_section_present(existing):
            if force:
                # Remove old section and re-append (refreshes the script body)
                updated = _remove_roam_section(existing)
                updated = _insert_roam_section(updated)
                hook_path.write_text(updated, encoding="utf-8")
                _make_executable(hook_path)
                return "overwritten", None
            return "skipped", None

        # Append our section
        updated = _insert_roam_section(existing)
        hook_path.write_text(updated, encoding="utf-8")
        _make_executable(hook_path)
        return "appended", None

    except OSError as exc:
        return "error", str(exc)


def _uninstall_hook(hook_path: Path) -> tuple[str, str | None]:
    """Remove the roam section from a single hook file.

    Returns (action, error):
      action -- "removed", "not-installed", "deleted" (file now empty/shebang-only), "skipped"
      error  -- None on success, error string on failure
    """
    if not hook_path.exists():
        return "not-installed", None

    try:
        content = hook_path.read_text(encoding="utf-8", errors="replace")
        if not _roam_section_present(content):
            return "not-installed", None

        updated = _remove_roam_section(content)

        # If the remaining content is just a shebang (or empty), remove the file
        stripped = updated.strip()
        if not stripped or stripped in ("#!/bin/sh", "#!/bin/bash"):
            hook_path.unlink()
            return "deleted", None

        hook_path.write_text(updated, encoding="utf-8")
        _make_executable(hook_path)
        return "removed", None

    except OSError as exc:
        return "error", str(exc)


def _collect_exclusive_hook_outcomes(
    hooks_dir: Path,
    operation: Callable[[Path], tuple[str, str | None]],
    success_actions: tuple[str, ...],
) -> tuple[dict[str, str], list[str], list[str], list[dict[str, str]]]:
    """Run a hook operation while assigning each managed hook to one outcome bucket."""
    results: dict[str, str] = {}
    successful: list[str] = []
    skipped: list[str] = []
    errors: list[dict[str, str]] = []

    for hook_name in _HOOK_NAMES:
        hook_path = hooks_dir / hook_name
        action, error = operation(hook_path)
        results[hook_name] = action
        if error:
            errors.append({"hook": hook_name, "error": error})
        elif action in success_actions:
            successful.append(hook_name)
        else:
            skipped.append(hook_name)

    return results, successful, skipped, errors


_HookVerdictBuilder = Callable[[list[str], list[str], list[dict[str, str]]], str]


def _run_hook_operation_preserving_cli_contract(
    ctx: click.Context,
    *,
    json_mode: bool,
    missing_repo_verdict: str,
    operation: Callable[[Path], tuple[str, str | None]],
    success_actions: tuple[str, ...],
    primary_summary_key: str,
    secondary_summary_key: str,
    build_verdict: _HookVerdictBuilder,
    create_hooks_dir: bool = False,
    show_hooks_dir: bool = False,
    secondary_tip: str | None = None,
) -> None:
    """Share hook command plumbing while preserving each command's public contract."""
    hooks_dir = _find_git_hooks_dir()
    if hooks_dir is None:
        if json_mode:
            click.echo(
                to_json(
                    json_envelope(
                        "hooks",
                        summary={
                            "verdict": missing_repo_verdict,
                            primary_summary_key: [],
                            secondary_summary_key: [],
                            "errors": [],
                        },
                        hooks_dir=None,
                    )
                )
            )
        else:
            click.echo(f"VERDICT: {missing_repo_verdict}")
        ctx.exit(1)
        return

    if create_hooks_dir:
        hooks_dir.mkdir(parents=True, exist_ok=True)

    results, primary, secondary, errors = _collect_exclusive_hook_outcomes(
        hooks_dir,
        operation,
        success_actions=success_actions,
    )
    verdict = build_verdict(primary, secondary, errors)

    if json_mode:
        click.echo(
            to_json(
                json_envelope(
                    "hooks",
                    summary={
                        "verdict": verdict,
                        primary_summary_key: primary,
                        secondary_summary_key: secondary,
                        "errors": errors,
                    },
                    hooks_dir=str(hooks_dir),
                    results=results,
                )
            )
        )
        return

    click.echo(f"VERDICT: {verdict}")
    if show_hooks_dir:
        click.echo(f"Hooks directory: {hooks_dir}")
    for hook_name in _HOOK_NAMES:
        action = results.get(hook_name, "?")
        click.echo(f"  {hook_name:20s} {action}")
    if secondary_tip and secondary:
        click.echo(secondary_tip)


def _verdict_for_git_reindex_install(installed: list[str], skipped: list[str], errors: list[dict[str, str]]) -> str:
    """Summarize install outcomes around refreshing git-triggered indexing."""
    if errors:
        return f"Installed {len(installed)} hook(s) with {len(errors)} error(s)."
    if installed:
        return f"Installed {len(installed)} hook(s): {', '.join(installed)}."
    return f"All hooks already installed ({len(skipped)} skipped). Use --force to refresh."


def _verdict_for_git_reindex_uninstall(
    removed: list[str], _not_installed: list[str], _errors: list[dict[str, str]]
) -> str:
    """Summarize uninstall outcomes around disabling git-triggered indexing."""
    if removed:
        return f"Removed roam hooks from: {', '.join(removed)}."
    return "No roam hooks found to remove."


# ---------------------------------------------------------------------------
# Click group
# ---------------------------------------------------------------------------


@roam_capability(
    name="hooks",
    category="getting-started",
    summary="Manage git hook integration for automatic re-indexing",
    maturity="stable",
    mcp_expose=False,
    mcp_preset=("core",),
    side_effect=True,
    task_required=False,
    destructive=False,
    stale_sensitive=False,
    ai_safe=False,
    requires_index=False,
)
@click.group("hooks")
@click.pass_context
def hooks(ctx):
    """Manage git hook integration for automatic re-indexing.

    Unlike ``init`` (which creates the roam index and database), this
    command installs git hooks that keep the index up to date automatically.
    """
    pass


# ---------------------------------------------------------------------------
# install
# ---------------------------------------------------------------------------


@hooks.command("install")
@click.option("--force", is_flag=True, help="Overwrite existing roam hook sections.")
@click.pass_context
def install(ctx, force):
    """Install git hooks for automatic re-indexing after merge/checkout/rebase."""
    json_mode = ctx.obj.get("json") if ctx.obj else False

    _run_hook_operation_preserving_cli_contract(
        ctx,
        json_mode=json_mode,
        missing_repo_verdict="No git repository found. Run `git init` first.",
        operation=lambda hook_path: _install_hook(hook_path, force=force),
        success_actions=("created", "appended", "overwritten"),
        primary_summary_key="installed",
        secondary_summary_key="skipped",
        build_verdict=_verdict_for_git_reindex_install,
        create_hooks_dir=True,
        show_hooks_dir=True,
        secondary_tip="Tip: use --force to overwrite existing roam hook sections.",
    )


# ---------------------------------------------------------------------------
# uninstall
# ---------------------------------------------------------------------------


@hooks.command("uninstall")
@click.pass_context
def uninstall(ctx):
    """Remove roam git hooks (or the roam section from shared hooks)."""
    json_mode = ctx.obj.get("json") if ctx.obj else False

    _run_hook_operation_preserving_cli_contract(
        ctx,
        json_mode=json_mode,
        missing_repo_verdict="No git repository found.",
        operation=_uninstall_hook,
        success_actions=("removed", "deleted"),
        primary_summary_key="removed",
        secondary_summary_key="not_installed",
        build_verdict=_verdict_for_git_reindex_uninstall,
    )


# ---------------------------------------------------------------------------
# status
# ---------------------------------------------------------------------------


@hooks.command("status")
@click.pass_context
def status(ctx):
    """Show which roam hooks are installed."""
    json_mode = ctx.obj.get("json") if ctx.obj else False

    hooks_dir = _find_git_hooks_dir()

    hook_statuses: list[dict] = []
    installed_count = 0

    if hooks_dir is not None:
        for hook_name in _HOOK_NAMES:
            hook_path = hooks_dir / hook_name
            present = _hook_has_roam(hook_path)
            exists = hook_path.exists()
            if present:
                installed_count += 1
            hook_statuses.append(
                {
                    "hook": hook_name,
                    "installed": present,
                    "file_exists": exists,
                    "path": str(hook_path) if exists else None,
                }
            )
    else:
        for hook_name in _HOOK_NAMES:
            hook_statuses.append(
                {
                    "hook": hook_name,
                    "installed": False,
                    "file_exists": False,
                    "path": None,
                }
            )

    if hooks_dir is None:
        verdict = "Not in a git repository."
    elif installed_count == len(_HOOK_NAMES):
        verdict = f"All {len(_HOOK_NAMES)} roam hooks installed."
    elif installed_count == 0:
        verdict = "No roam hooks installed. Run `roam hooks install` to set up."
    else:
        verdict = f"{installed_count}/{len(_HOOK_NAMES)} roam hooks installed."

    if json_mode:
        click.echo(
            to_json(
                json_envelope(
                    "hooks",
                    summary={
                        "verdict": verdict,
                        "installed_count": installed_count,
                        "total_hooks": len(_HOOK_NAMES),
                        "all_installed": installed_count == len(_HOOK_NAMES),
                    },
                    hooks_dir=str(hooks_dir) if hooks_dir else None,
                    hooks=hook_statuses,
                )
            )
        )
        return

    click.echo(f"VERDICT: {verdict}")
    if hooks_dir:
        click.echo(f"Hooks directory: {hooks_dir}")
    for entry in hook_statuses:
        state = "installed" if entry["installed"] else ("present" if entry["file_exists"] else "missing")
        click.echo(f"  {entry['hook']:20s} {state}")


# ---------------------------------------------------------------------------
# Claude Code hook integration (W-CC-SETUP, 2026-06-10)
# ---------------------------------------------------------------------------
# Makes the compiler's prompt-time channel available to ANY Claude Code CLI
# user out of the box: a UserPromptSubmit hook that runs `roam --json compile`
# on each prompt and prints the envelope as injected context. Fail-open by
# design — any error inside the hook prints nothing and exits 0, so a broken
# roam install can never block the user's turn. Evidence basis: the Fable 5
# A/B numbers (turns -83%) were measured on plain `claude -p` with exactly
# this prefix-injection shape — no orchestration layer required.

# Hook-body generation version. BUMP when either _CLAUDE_*_HOOK_SCRIPT changes
# materially, and append the SHA of the prior stamped body to
# _KNOWN_HOOK_BODY_SHAS so `hooks claude --write` heals the now-stale deployed
# copies. The stamp is a bare comment line inserted after the shebang:
# compile-code's mode-override surgery rewrites only the roam INVOCATION lines,
# never this, so a healed body keeps its version marker.
_HOOK_BODY_VERSION = 2
_HOOK_VERSION_MARKER = "# roam-hook-version:"

_CLAUDE_UPS_HOOK_FILENAME = "roam-compile-ups.py"
_CLAUDE_UPS_HOOK_SCRIPT = '''#!/usr/bin/env python3
"""roam compile -> Claude Code UserPromptSubmit context injection.

Installed by `roam hooks claude --write`. FAIL-OPEN: any error prints
nothing and exits 0 (a broken roam install must never block a turn).
"""
import json
import os
import subprocess
import sys

_COMPILE_TIMEOUT_S = 6.0
_MIN_PROMPT_CHARS = 8


def main():
    try:
        payload = json.load(sys.stdin)
        prompt = (payload.get("prompt") or "").strip()
        if len(prompt) < _MIN_PROMPT_CHARS:
            return
        # Forward Claude's session id so the compile telemetry row carries a
        # join key to the session's downstream outcome. Fail-open: an absent id
        # just leaves the key empty, never breaks the hook.
        env = os.environ.copy()
        session_id = str(payload.get("session_id") or "").strip()
        if session_id:
            env["ROAM_SESSION_ID"] = session_id
        # Stamp real hook traffic as 'hook' so it is distinguishable from the
        # mixed 'unknown' bucket in compile-stats. setdefault, not assign: an
        # explicit ROAM_AGENT_MODE (a policy mode the user set) is preserved.
        env.setdefault("ROAM_AGENT_MODE", "hook")
        proc = subprocess.run(
            ["roam", "--json", "compile", prompt],
            capture_output=True, text=True, timeout=_COMPILE_TIMEOUT_S,
            env=env,
        )
        if proc.returncode != 0 or not proc.stdout.strip():
            return
        d = json.loads(proc.stdout)
        summary = d.get("summary") or {}
        # Generation-shaped tasks (write a test / implement X): measured
        # net-negative to inject — the envelope is cache-re-read every turn
        # while the agent must read/edit/run regardless. Compiler advises.
        if str(summary.get("injection_advice") or "").startswith("skip"):
            return
        plan = (d.get("artifact") or {}).get("plan") or {}
        facts = {k: v for k, v in (plan.get("prefetched_facts") or {}).items()
                 if not k.startswith("_")}
        block = {
            "procedure": summary.get("procedure"),
            "confidence": summary.get("classifier_confidence"),
            "named_paths": (plan.get("named_paths") or [])[:6],
            "recommended_first": plan.get("recommended_first_command"),
            "answer_contract": plan.get("answer_contract"),
            "prefetched_facts": facts,
        }
        block = {k: v for k, v in block.items() if v}
        if not block:
            return
        print("PRE-COMPUTED PLAN (roam compile -- answer from these "
              "embedded facts; do not re-gather what is already answered):")
        print(json.dumps(block, ensure_ascii=False))
    except Exception:
        return  # fail open


main()
'''


def _claude_settings_path(user_level: bool) -> Path:
    if user_level:
        return Path.home() / ".claude" / "settings.json"
    return Path.cwd() / ".claude" / "settings.json"


def _claude_hook_dir(user_level: bool) -> Path:
    if user_level:
        return Path.home() / ".claude" / "hooks"
    return Path.cwd() / ".claude" / "hooks"


# W-CC-VERIFY (2026-06-10) — the post-edit verify half of the decision-doc
# MVP loop (compile before the model acts, verify after it edits). Stop-hook
# script: scoped `roam verify --auto --diff-only`, fail-open, quiet on PASS.
_CLAUDE_STOP_HOOK_FILENAME = "roam-verify-stop.py"
_CLAUDE_STOP_HOOK_SCRIPT = '''#!/usr/bin/env python3
"""roam verify -> Claude Code Stop-hook post-edit check.

Installed by `roam hooks claude --write`. FAIL-OPEN and QUIET-ON-PASS:
any error or a clean verdict prints nothing; only real findings surface.

Fast-exit + block-rate telemetry (2026-07-11): a stop with a clean working
tree (no tracked edits, no new untracked files -- a pure Q&A session) skips
the verify subprocess entirely (~10ms git check instead of a 90s-budget
verify run). Every decision appends a counts-only JSON line to
`.roam/hook-stops.jsonl` so the block rate is measurable -- see
_log_stop_event.

Feedback layer (all env-gated, all fail-open; defaults reproduce the
prior shipped behaviour when WHYFAIL+DOCDRIFT off and PROMINENT off):
  ROAM_HOOK_PROMINENT (default 1) -- present BLOCK-level (FAIL-severity)
      findings first and never truncate them, so a breaking-change /
      failed-test / hallucinated-import block is never buried behind
      style WARNs. Set 0 for the legacy flat findings[:8] list.
  ROAM_HOOK_WHYFAIL  (default 1) -- when an impacted test FAILED, attach
      roam why-fail's root-cause frame (recently-changed symbols the
      failing test transitively reaches, ranked by recency x PageRank)
      so the fix loop is tight instead of just "FAILED". Reuses the
      existing why-fail command; points at `roam diagnose` for a deeper
      single-symbol root cause.
  ROAM_HOOK_DOCDRIFT (default 0) -- when a public signature changed, add
      a soft advisory that docs/README may be stale (reuse:
      `roam doc-staleness` / `roam api-drift`). Advisory only -- it rides
      an existing block, never turns a clean PASS into noise.

Second-opinion reviewers (ALL default 0 / OFF, all fail-open, all
silent-when-clean, each reuses an existing roam command). They run
INDEPENDENTLY of the verify verdict so a diff that passes scoped verify
still gets the second opinion when an operator opts in. With every flag
at its default the hook output is byte-identical to the prior shipped
behaviour.
  ROAM_HOOK_CRITIQUE (default 0) -- pipe `git diff HEAD` to
      `roam critique`: a patch-vs-graph review (clone-siblings that need
      the same edit + high-blast-radius symbols) that scoped verify
      misses. Surfaces medium+ findings only. Closes F2.
  ROAM_HOOK_PRRISK   (default 0) -- attach `roam pr-risk`'s 0-100 diff
      risk score (blast x hotspot x bus-factor x coupling) when the diff
      ranks high/critical. Closes F19.
  ROAM_HOOK_ADVERSARIAL (default 0) -- attach `roam adversarial`'s high+
      architecture challenges (new cycles / layer violations) on the
      change. Closes F19.
  ROAM_HOOK_VIBE     (default 0) -- attach `roam vibe-check`'s AI-rot
      score when it crosses ROAM_HOOK_VIBE_THRESHOLD (default 50).
      Repo-scoped (not diff-scoped), so advisory only. Closes F23.
"""
import json
import os
import subprocess
import sys
import time

_VERIFY_TIMEOUT_S = 90
_WHYFAIL_TIMEOUT_S = 20
_CRITIQUE_TIMEOUT_S = 45
_PRRISK_TIMEOUT_S = 45
_ADVERSARIAL_TIMEOUT_S = 45
_VIBE_TIMEOUT_S = 60
_GITDIFF_TIMEOUT_S = 15
_GIT_CHECK_TIMEOUT_S = 10
_MAX_FAIL_SHOWN = 12
_MAX_OTHER_SHOWN = 8
_MAX_WHYFAIL_TESTS = 3
_MAX_WHYFAIL_SUSPECTS = 5
_MAX_CRITIQUE_SHOWN = 6
_MAX_ADVERSARIAL_SHOWN = 6
_PRRISK_WARN_RANK = 3  # warn only on high(3)/critical(4) diffs
_SIGCHANGE_CATS = ("breaking", "api", "api_drift", "api_changes")
# canonical severity ordering (roam.output._severity): higher = worse.
_SEV_RANK = {"info": 1, "note": 1, "low": 1, "medium": 2, "moderate": 2,
             "warning": 2, "high": 3, "error": 3, "critical": 4}
# BUG#62: the diff-scoped advisory detectors ship OFF by default, so the
# Stop-hook verify used to run "dark". Force the cheap, WARN-only advisory
# flags ON (heavy / hard-block-risk detectors -- CLONES / DELETE_CHECK /
# TAINT -- stay OFF). These never enter the verdict; they surface as a
# non-blocking notice.
_ADVISORY_ENV = {
    "ROAM_VERIFY_N1": "1",
    "ROAM_VERIFY_OVER_FETCH": "1",
    "ROAM_VERIFY_DEAD": "1",
    "ROAM_VERIFY_MAGIC_NUMBERS": "1",
    "ROAM_VERIFY_LLM_SMELLS": "1",
    "ROAM_VERIFY_TEST_HERMETICITY": "1",
    "ROAM_VERIFY_SMELLS": "1",
}
# Categories emitted by the advisory detectors above -- surfaced as a
# non-blocking notice, never as a decision:block.
_ADVISORY_CATEGORIES = frozenset({
    "n1", "over_fetch", "dead", "magic_numbers",
    "llm_smells", "test_hermeticity", "smells",
})


def _env_on(name, default):
    return (os.environ.get(name, default) or "").strip().lower() not in ("", "0", "false", "no", "off")


def _run_roam(args, timeout, env=None):
    """Run `roam --json <args>`; return the parsed envelope or None. Fail-open."""
    try:
        proc = subprocess.run(
            ["roam", "--json", *args],
            capture_output=True, text=True, timeout=timeout,
            env=env,
        )
        if not proc.stdout.strip():
            return None
        return json.loads(proc.stdout)
    except Exception:
        return None


def _run_roam_stdin(args, stdin_text, timeout):
    """Like _run_roam but pipes a diff to the command's stdin (e.g.
    `git diff | roam critique`). Returns the parsed envelope or None. Fail-open."""
    try:
        proc = subprocess.run(
            ["roam", "--json", *args], input=stdin_text,
            capture_output=True, text=True, timeout=timeout,
        )
        if not proc.stdout.strip():
            return None
        return json.loads(proc.stdout)
    except Exception:
        return None


def _git_diff_head():
    """Working-tree diff vs HEAD -- the same scope verify uses with
    --diff-only. Returns '' on any error (so callers stay silent)."""
    try:
        proc = subprocess.run(
            ["git", "diff", "HEAD"], capture_output=True, text=True,
            timeout=_GITDIFF_TIMEOUT_S,
        )
        return proc.stdout if proc.returncode == 0 else ""
    except Exception:
        return ""


def _tree_has_no_edits():
    """Fast-exit pre-check (~10ms): True only when the working tree has no
    tracked changes vs HEAD AND no new untracked files -- a pure Q&A stop
    with nothing for `verify --diff-only` to inspect. Any git error (not a
    repo, no HEAD yet, timeout) returns False so the hook falls back to the
    full verify path (fail-open)."""
    try:
        diff = subprocess.run(
            ["git", "diff", "--quiet", "HEAD"],
            capture_output=True, timeout=_GIT_CHECK_TIMEOUT_S,
        )
        if diff.returncode != 0:  # 1 = tracked changes; 128+ = git error
            return False
        untracked = subprocess.run(
            ["git", "ls-files", "--others", "--exclude-standard"],
            capture_output=True, text=True, timeout=_GIT_CHECK_TIMEOUT_S,
        )
        if untracked.returncode != 0:
            return False
        # Roam's own state files (.roam/hook-stops.jsonl, .claude/settings)
        # are often untracked in target repos -- without this filter the
        # first telemetry write would defeat the fast-exit forever. verify
        # --diff-only has nothing to say about them anyway.
        new_files = [
            ln for ln in untracked.stdout.splitlines()
            if ln.strip() and not ln.startswith((".roam/", ".claude/"))
        ]
        return not new_files
    except Exception:
        return False


def _log_stop_event(blocked, findings_n, advisory_n, verify_ms, skipped_no_edit):
    """Best-effort block-rate telemetry: one counts-only JSON line per
    Stop-hook decision appended to `.roam/hook-stops.jsonl` (same dir
    convention as `.roam/compile-runs.jsonl`: cwd-relative, skip when
    `.roam/` doesn't exist, never grow past 10 MB). decision:block outcomes
    are the only place the compile stack spends real model tokens (each
    block = one extra full agent turn), so the block rate must be
    measurable. Finding TEXT stays out of the log (privacy) -- counts only.
    Never breaks the hook."""
    try:
        log_dir = os.path.join(os.getcwd(), ".roam")
        if not os.path.isdir(log_dir):
            return
        log_path = os.path.join(log_dir, "hook-stops.jsonl")
        if os.path.exists(log_path) and os.path.getsize(log_path) > 10 * 1024 * 1024:
            return  # rotate manually; never grow unbounded
        entry = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "blocked": bool(blocked),
            "findings": int(findings_n),
            "advisory_findings": int(advisory_n),
            "verify_ms": int(verify_ms),
            "skipped_no_edit": bool(skipped_no_edit),
        }
        with open(log_path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry) + chr(10))  # chr(10): the enclosing
            # module embeds this script in a non-raw string literal
    except Exception:
        pass  # telemetry must never break the hook


def _collect_findings(d):
    """Prefer the flat, severity-sorted top-level `violations` list (verify
    already ranks FAIL first, then by blast radius); fall back to the
    per-category lists for older verify envelopes."""
    flat = d.get("violations")
    if isinstance(flat, list) and flat:
        return list(flat)
    out = []
    for _cat, res in (d.get("categories") or {}).items():
        out.extend((res or {}).get("violations") or [])
    return out


def _fmt(v):
    loc = f"{v.get('file')}:{v.get('line')}" if v.get("line") else str(v.get("file"))
    head = f"  - [{v.get('severity') or '?'}/{v.get('category')}] {loc} -- {v.get('message')}"
    if v.get("fix"):
        return [head, f"      fix: {v['fix']}"]
    return [head]


def _test_fail_targets(findings):
    """Test files of impacted-test FAILures (dedup, order-preserving)."""
    targets = []
    for v in findings:
        if v.get("category") != "tests":
            continue
        if v.get("severity") == "FAIL" or "FAILED" in str(v.get("message") or ""):
            f = v.get("file")
            if f and f not in targets:
                targets.append(f)
    return targets


def _whyfail_lines(targets):
    """Attach roam why-fail's root-cause frame for each failing test file."""
    body = []
    for tgt in targets[:_MAX_WHYFAIL_TESTS]:
        d = _run_roam(["why-fail", tgt], _WHYFAIL_TIMEOUT_S)
        if not d:
            continue
        suspects = d.get("suspects") or []
        if not suspects:
            continue
        verdict = str((d.get("summary") or {}).get("verdict") or "")
        body.append(f"  why-fail {tgt}: {verdict}")
        for s in suspects[:_MAX_WHYFAIL_SUSPECTS]:
            body.append(f"      suspect: {s.get('name')} ({s.get('kind')}) in {s.get('file') or '?'}")
        top = suspects[0].get("name")
        if top:
            body.append(f"      deeper root cause: roam diagnose {top}")
    if body:
        body.insert(0, "WHY-FAIL (recently-changed symbols the failing test reaches -- start the fix here):")
    return body


def _has_signature_change(findings):
    for v in findings:
        if str(v.get("category") or "").lower() in _SIGCHANGE_CATS:
            return True
        msg = str(v.get("message") or "").lower()
        if "signature" in msg or "breaking change" in msg:
            return True
    return False


def _critique_lines():
    """roam critique: patch-vs-graph second opinion on the diff (F2).
    Reads the unified diff from stdin; medium+ findings only."""
    diff = _git_diff_head()
    if not diff.strip():
        return []
    d = _run_roam_stdin(["critique"], diff, _CRITIQUE_TIMEOUT_S)
    if not d:
        return []
    notable = [f for f in (d.get("findings") or [])
               if _SEV_RANK.get(str(f.get("severity") or "").lower(), 0) >= 2]
    if not notable:
        return []
    out = [f"  critique ({len(notable)} finding(s) -- patch vs graph: "
           "clones-to-co-edit + blast radius):"]
    for f in notable[:_MAX_CRITIQUE_SHOWN]:
        out.append(f"      [{str(f.get('severity') or '?').upper()}/{f.get('check')}] {f.get('title')}")
    if len(notable) > _MAX_CRITIQUE_SHOWN:
        out.append(f"      ... and {len(notable) - _MAX_CRITIQUE_SHOWN} more")
    return out


def _prrisk_lines():
    """roam pr-risk: 0-100 diff risk score; warn only on high/critical (F19)."""
    d = _run_roam(["pr-risk"], _PRRISK_TIMEOUT_S)
    if not d:
        return []
    s = d.get("summary") or {}
    try:
        rank = int(s.get("risk_rank"))
    except (TypeError, ValueError):
        rank = 0
    if rank < _PRRISK_WARN_RANK:
        return []
    level = s.get("risk_level_canonical") or s.get("risk_level") or "?"
    return [f"  pr-risk: score {s.get('risk_score')}/100 ({level}) -- blast x "
            "hotspot x bus-factor x coupling; review the blast radius before finishing."]


def _adversarial_lines():
    """roam adversarial: high+ architecture challenges on the change (F19)."""
    d = _run_roam(["adversarial", "--severity", "high"], _ADVERSARIAL_TIMEOUT_S)
    if not d:
        return []
    challenges = d.get("challenges") or []
    if not challenges:
        return []
    out = [f"  adversarial ({len(challenges)} architecture challenge(s) at high+):"]
    for c in challenges[:_MAX_ADVERSARIAL_SHOWN]:
        out.append(f"      [{str(c.get('severity') or '?').upper()}/{c.get('type')}] {c.get('title')}")
    if len(challenges) > _MAX_ADVERSARIAL_SHOWN:
        out.append(f"      ... and {len(challenges) - _MAX_ADVERSARIAL_SHOWN} more")
    return out


def _vibe_lines():
    """roam vibe-check: repo-level AI-rot score, thresholded (F23)."""
    d = _run_roam(["vibe-check"], _VIBE_TIMEOUT_S)
    if not d:
        return []
    s = d.get("summary") or {}
    score = s.get("ai_rot_score")
    if score is None:
        score = s.get("score")
    try:
        score = int(score)
    except (TypeError, ValueError):
        return []
    try:
        thr = int(os.environ.get("ROAM_HOOK_VIBE_THRESHOLD", "50") or "50")
    except ValueError:
        thr = 50
    if score < thr:
        return []
    return [f"  vibe-check: AI-rot score {score}/100 (>= {thr}, repo-level) -- "
            "run `roam vibe-check` for the rot inventory."]


def _second_opinion_lines():
    """Run the opt-in second-opinion reviewers (all default-OFF, fail-open,
    silent-when-clean). Each reuses an existing roam command and runs
    independently of the verify verdict. Returns [] when none are enabled
    or none have anything to say (so the default hook stays byte-identical)."""
    out = []
    if _env_on("ROAM_HOOK_CRITIQUE", "0"):
        out.extend(_critique_lines())
    if _env_on("ROAM_HOOK_PRRISK", "0"):
        out.extend(_prrisk_lines())
    if _env_on("ROAM_HOOK_ADVERSARIAL", "0"):
        out.extend(_adversarial_lines())
    if _env_on("ROAM_HOOK_VIBE", "0"):
        out.extend(_vibe_lines())
    return out


def main():
    try:
        payload = json.load(sys.stdin)
        if payload.get("stop_hook_active"):
            return  # already inside a stop-hook continuation; never loop
        # Fast-exit (2026-07-11): pure Q&A stops (no tracked edits, no new
        # untracked files) have nothing for `verify --diff-only` to check --
        # skip the verify subprocess entirely. Any git error falls back to
        # the full verify path below (fail-open). Downstream code is
        # unchanged: d=None yields the same quiet "allow" outcome (and the
        # same opt-in second-opinion behaviour) as a clean verify.
        skipped_no_edit = _tree_has_no_edits()
        verify_ms = 0
        d = None
        if not skipped_no_edit:
            verify_t0 = time.perf_counter()
            d = _run_roam(["verify", "--auto", "--diff-only"], _VERIFY_TIMEOUT_S,
                          env={**os.environ, **_ADVISORY_ENV})
            verify_ms = int((time.perf_counter() - verify_t0) * 1000)
        summary = (d or {}).get("summary") or {}
        verdict = str(summary.get("verdict") or "")
        verify_failed = bool(d) and bool(verdict) and not verdict.upper().startswith("PASS")
        findings = _collect_findings(d) if d else []
        # BUG#62: advisory detectors (default-ON here) emit WARN-only
        # findings that never enter the verdict. Route them out of the
        # blocking set and surface them as a NON-BLOCKING transcript notice
        # (stderr) even on PASS -- never a decision:block.
        advisory = [v for v in findings if v.get("category") in _ADVISORY_CATEGORIES]
        findings = [v for v in findings if v.get("category") not in _ADVISORY_CATEGORIES]
        if advisory:
            notice = [f"roam verify advisory (non-blocking, changed lines vs HEAD): {len(advisory)} finding(s)"]
            for v in advisory[:_MAX_OTHER_SHOWN]:
                notice.extend(_fmt(v))
            if len(advisory) > _MAX_OTHER_SHOWN:
                notice.append(f"  ... and {len(advisory) - _MAX_OTHER_SHOWN} more")
            notice.append("  (advisory only -- review, or add to .roam-suppressions.yml; does not block)")
            print(chr(10).join(notice), file=sys.stderr)

        lines = []
        if verify_failed and findings:
            lines.append(f"roam verify (post-edit, changed lines vs HEAD): {verdict}")

            if _env_on("ROAM_HOOK_PROMINENT", "1"):
                # Partition so BLOCK-level (FAIL) findings -- failed tests,
                # hallucinated imports, breaking changes -- are shown first and
                # never truncated away behind style WARNs.
                fails = [v for v in findings if v.get("severity") == "FAIL"]
                rest = [v for v in findings if v.get("severity") != "FAIL"]
                if fails:
                    lines.append(f"BLOCKING -- {len(fails)} must-fix finding(s):")
                    for v in fails[:_MAX_FAIL_SHOWN]:
                        lines.extend(_fmt(v))
                    if len(fails) > _MAX_FAIL_SHOWN:
                        lines.append(f"  ... and {len(fails) - _MAX_FAIL_SHOWN} more blocking")
                if rest:
                    lines.append(f"OTHER -- {len(rest)} finding(s):")
                    for v in rest[:_MAX_OTHER_SHOWN]:
                        lines.extend(_fmt(v))
                    if len(rest) > _MAX_OTHER_SHOWN:
                        lines.append(f"  ... and {len(rest) - _MAX_OTHER_SHOWN} more")
            else:
                for v in findings[:8]:
                    lines.extend(_fmt(v))
                if len(findings) > 8:
                    lines.append(f"  ... and {len(findings) - 8} more")

            if _env_on("ROAM_HOOK_WHYFAIL", "1"):
                targets = _test_fail_targets(findings)
                if targets:
                    lines.extend(_whyfail_lines(targets))

            if _env_on("ROAM_HOOK_DOCDRIFT", "0") and _has_signature_change(findings):
                lines.append(
                    "DOC-DRIFT (advisory): a public signature changed -- docs/README "
                    "may be stale. Reuse: `roam doc-staleness` / `roam api-drift`."
                )

            # AUTO-FIX directive -- on by default. The block makes the agent
            # resolve findings on lines it just touched instead of stopping;
            # the stop_hook_active guard above bounds it to one fix round.
            lines.append(
                "AUTO-FIX: resolve these now. EDIT the file(s) to fix each "
                "finding on a line your change touched; a genuine false "
                "positive goes in .roam-suppressions.yml (rule/file/symbol or "
                "line + reason); only clearly pre-existing, unrelated findings "
                "may be left. Verify re-runs automatically after your fix."
            )

        # Opt-in second-opinion reviewers (all default-OFF; byte-identical
        # default output when none are enabled). Independent of the verify
        # verdict and advisory -- they never carry the hard AUTO-FIX directive.
        extra = _second_opinion_lines()
        if extra:
            if lines:
                lines.append("")
            lines.append(
                "SECOND OPINION (opt-in roam reviewers -- advisory; address or "
                "justify, they do not auto-block):"
            )
            lines.extend(extra)

        _log_stop_event(bool(lines), len(findings), len(advisory), verify_ms, skipped_no_edit)
        if not lines:
            return  # quiet: clean verify and nothing from any enabled reviewer
        print(json.dumps({"decision": "block", "reason": chr(10).join(lines)}))
    except Exception:
        return  # fail open


main()
'''


def _with_version_stamp(body: str) -> str:
    """Insert the ``# roam-hook-version: N`` marker after the shebang line.

    Post-processing (not an f-string) keeps the body literals free of brace-
    escaping — the hook code is full of ``{...}`` dict comprehensions."""
    lines = body.split("\n", 1)
    shebang = lines[0]
    rest = lines[1] if len(lines) > 1 else ""
    return f"{shebang}\n{_HOOK_VERSION_MARKER} {_HOOK_BODY_VERSION}\n{rest}"


# Rebind the deployed bodies to their stamped form. The constant NAMES are
# unchanged, so `managed`/tests keep referring to the same symbols — they now
# carry the version marker.
_CLAUDE_UPS_HOOK_SCRIPT = _with_version_stamp(_CLAUDE_UPS_HOOK_SCRIPT)
_CLAUDE_STOP_HOOK_SCRIPT = _with_version_stamp(_CLAUDE_STOP_HOOK_SCRIPT)


# SHA-256 of every hook body roam ever shipped, in DEPLOYED form (stamped where
# the shipping commit stamped, raw literal before that), plus the deterministic
# variant compile-code's mode-override surgery produces (cli.py:266-283 there).
# A deployed body whose hash is here is KNOWN to be roam's own output — safe to
# heal without a marker. A stamped body whose hash is NOT here has been edited
# by hand (or by an unknown external transform): report, never auto-overwrite.
# Regenerate with scripts/seed_hook_body_shas.py after ANY body change — an
# empty or stale set silently disables healing for pre-stamp installs (the
# defect that shipped in #77).
_KNOWN_HOOK_BODY_SHAS: frozenset[str] = frozenset(
    {
        "0313b8d53749fa9d188c9e6554b37826ff677cdd166627ab5b613538bb4b4573",  # stop v2 pristine (2026-07-16 18326816)
        "2c81e646c1102ebd010b6f470d6a153d8b47d68921584e55806de9052da13fa7",  # stop v2 surgered (2026-07-16 18326816)
        "fd8a7522fe488b6429f159146523524dcab6465ddbdc09aa91a3515a89bf58a2",  # stop pre-stamp (2026-06-10 ffa51bb1)
        "23dc563a465af1e5e11f698ce2e8f1aa2cbae0959b7fbbd2ade9ce7abd7bebd6",  # stop pre-stamp (2026-06-11 16871343)
        "6f95e9afb5f19c6479ca72647ffca014effe453bceaeba47d06d83a898bac3fc",  # stop pre-stamp (2026-07-08 118dcf55)
        "929f2e2bc35b1b75874194d9e1843b8868a4f8795c8dbe47da29c9fe841fbf32",  # stop pre-stamp (2026-07-16 19e74bd5)
        "cc1b6fdda85ce004c620ff01f0b7096b910395c224c0f723947d5c6d127343c2",  # stop pre-stamp surgered (07-08 118dcf55)
        "2fe9800b212926cb332a903114534a9891ddb790d54ec1b7aeb90b740bf67b68",  # stop pre-stamp surgered (07-16 19e74bd5)
        "0a33b73872a9e507521aa8feea09a9b14525ebc7f91ccc6ae5cce0c9cd83c224",  # ups v2 pristine (2026-07-16 18326816)
        "c01d848b2da0503ca91460858da9a926851c0e6ce2d6a253b7a1f28fdc96aa8d",  # ups pre-stamp (2026-06-10 ffa51bb1)
        "849c787f92d385f6eb2e2ca832a5cd85b2f29691dd3dc590381db2a05642fd09",  # ups pre-stamp (2026-07-11 dcf7b2af)
        "527471d9c46f89825d79196bb092340b019d3f42ffbfcc96023ecda8c07d5433",  # ups pre-stamp (2026-07-16 19e74bd5)
        "9bae32c06f5b850a6faa92ae926294fedc1036f651282d648f9858c6bcd07e41",  # ups pre-stamp (2026-07-16 30801aad)
    }
)


def _hook_body_version(text: str) -> int | None:
    """Parse the ``# roam-hook-version: N`` marker; None if unstamped."""
    for line in text.split("\n")[:5]:
        s = line.strip()
        if s.startswith(_HOOK_VERSION_MARKER):
            try:
                return int(s[len(_HOOK_VERSION_MARKER) :].strip())
            except ValueError:
                return None
    return None


def _hook_heal_state(deployed: str, canonical: str) -> str:
    """Classify a deployed hook body vs the current canonical body.

    - "current"    : byte-identical, or a KNOWN roam-shipped variant of the
                     current version (compile-code's surgered form).
    - "heal"       : a KNOWN roam-shipped body (stamped or pre-stamp, pristine
                     or surgered) that is out of date — safe to refresh.
    - "modified"   : carries our version marker but the content is NOT any
                     body roam ever shipped — a hand-edited (or truncated)
                     roam body. Report; overwrite only with --force.
    - "foreign"    : no marker and unrecognized SHA — a user customization or
                     an external manager. Report; overwrite only with --force.

    Content is verified by SHA against the shipped-body registry in every
    non-identical case — a version marker alone proves nothing about the rest
    of the file (a truncated heal or a user edit keeps the marker intact).
    """
    if deployed == canonical:
        return "current"
    import hashlib

    dver = _hook_body_version(deployed)
    cver = _hook_body_version(canonical)
    known = hashlib.sha256(deployed.encode("utf-8")).hexdigest() in _KNOWN_HOOK_BODY_SHAS
    if known:
        # Same-version known variant = compile-code's surgery on the current
        # body: leave it alone. Anything else roam shipped is stale.
        if dver is not None and cver is not None and dver >= cver:
            return "current"
        return "heal"
    return "modified" if dver is not None else "foreign"


def _scan_hook_bodies(hook_dir: Path, managed: list) -> dict[str, str]:
    """{filename: heal_state} for each managed hook.

    Adds two states beyond :func:`_hook_heal_state`:
    - "missing"    : file absent (reinstall when the settings entry exists).
    - "unreadable" : not valid UTF-8 (e.g. saved as UTF-16 by PowerShell) —
                     report; replaceable only with --force. Never a traceback:
                     this scan runs inside `compile claude`'s wire path too.
    """
    out: dict[str, str] = {}
    for _event, filename, canonical in managed:
        p = hook_dir / filename
        if not p.exists():
            out[filename] = "missing"
            continue
        try:
            deployed = p.read_text(encoding="utf-8")
        except OSError:
            continue  # transient FS error: report nothing, touch nothing
        except ValueError:
            out[filename] = "unreadable"
            continue
        out[filename] = _hook_heal_state(deployed, canonical)
    return out


def _write_hook_body(hook_path: Path, script: str) -> None:
    """Overwrite a hook body atomically, preserving the prior content as .bak.

    Atomic (temp + os.replace) so a crash mid-write can never leave a
    truncated body behind a valid version stamp; .bak so heal/--force is
    recoverable (settings.json already gets the same courtesy).
    """
    if hook_path.exists():
        try:
            old = hook_path.read_bytes()
        except OSError:
            old = None
        if old is not None and old != script.encode("utf-8"):
            backup = hook_path.parent / (hook_path.name + ".bak")
            backup.write_bytes(old)
    tmp = hook_path.parent / (hook_path.name + ".tmp")
    tmp.write_text(script, encoding="utf-8")
    os.replace(tmp, hook_path)


def _hook_entry_present(settings: dict, event: str, filename: str) -> bool:
    for rule in (settings.get("hooks") or {}).get(event, []):
        for hk in rule.get("hooks", []):
            if filename in (hk.get("command") or ""):
                return True
    return False


def _merge_hook_entry(settings: dict, event: str, hook_cmd: str) -> dict:
    hooks_block = settings.setdefault("hooks", {})
    rules = hooks_block.setdefault(event, [])
    rules.append({"hooks": [{"type": "command", "command": hook_cmd}]})
    return settings


def _remove_hook_entry(settings: dict, event: str, filename: str) -> bool:
    rules = (settings.get("hooks") or {}).get(event)
    if not rules:
        return False
    kept = []
    removed = False
    for rule in rules:
        cmds = [hk.get("command") or "" for hk in rule.get("hooks", [])]
        if any(filename in c for c in cmds):
            removed = True
            continue
        kept.append(rule)
    if removed:
        settings["hooks"][event] = kept
        if not kept:
            del settings["hooks"][event]
    return removed


# Back-compat wrappers — the original UPS-specific names are part of the
# tested surface (tests/test_hooks_claude_setup.py imports them).
def _ups_entry_present(settings: dict) -> bool:
    return _hook_entry_present(settings, "UserPromptSubmit", _CLAUDE_UPS_HOOK_FILENAME)


def _merge_ups_entry(settings: dict, hook_cmd: str) -> dict:
    return _merge_hook_entry(settings, "UserPromptSubmit", hook_cmd)


def _remove_ups_entry(settings: dict) -> bool:
    return _remove_hook_entry(settings, "UserPromptSubmit", _CLAUDE_UPS_HOOK_FILENAME)


def _load_claude_settings(settings_path: Path) -> tuple[dict, str | None]:
    """Parse settings.json. Returns (settings, error_message-or-None)."""
    if not settings_path.exists():
        return {}, None
    try:
        return json.loads(settings_path.read_text(encoding="utf-8")), None
    except (OSError, ValueError) as exc:
        return {}, f"Cannot parse {settings_path}: {exc}"


def _emit_hooks_verdict(json_mode: bool, verdict: str, summary: dict, extra: dict, text_lines: list) -> None:
    """Single output point for the claude subcommand (JSON envelope or text)."""
    if json_mode:
        click.echo(to_json(json_envelope("hooks", summary={"verdict": verdict, **summary}, **extra)))
        return
    click.echo(f"VERDICT: {verdict}")
    for line in text_lines:
        click.echo(line)


def _claude_uninstall_hooks(settings: dict, settings_path: Path, hook_dir: Path, write: bool) -> tuple[str, bool]:
    """Sweep BOTH managed hooks (regardless of --no-verify). (verdict, removed_any)."""
    removed_any = False
    for event, filename in (
        ("UserPromptSubmit", _CLAUDE_UPS_HOOK_FILENAME),
        ("Stop", _CLAUDE_STOP_HOOK_FILENAME),
    ):
        if _remove_hook_entry(settings, event, filename):
            removed_any = True
            if write and (hook_dir / filename).exists():
                (hook_dir / filename).unlink()
    if write and removed_any:
        settings_path.write_text(json.dumps(settings, indent=2) + "\n", encoding="utf-8")
    verdict = "Removed roam Claude Code hooks" if removed_any else "No roam Claude Code hooks found"
    if not write and removed_any:
        verdict += " (dry-run; re-run with --write to apply)"
    return verdict, removed_any


def _claude_install_hooks(
    settings: dict,
    settings_path: Path,
    hook_dir: Path,
    to_install: list,
    body_states: dict[str, str] | None = None,
    force: bool = False,
) -> tuple[str, list[str]]:
    """Write the hook scripts + merge settings entries (settings.json backed up).

    A hook whose settings entry is missing but whose FILE on disk is a body
    roam cannot recognize (user-modified / foreign / unreadable) gets its entry
    wired while the body is PRESERVED unless --force — a wiped settings.json
    must not become a license to overwrite a customized body. Returns
    (verdict_part, preserved_filenames).
    """
    if settings_path.exists():
        backup = settings_path.with_suffix(".json.bak")
        backup.write_text(settings_path.read_text(encoding="utf-8"), encoding="utf-8")
    hook_dir.mkdir(parents=True, exist_ok=True)
    preserved: list[str] = []
    for event, filename, script in to_install:
        hook_path = hook_dir / filename
        state = (body_states or {}).get(filename)
        if state in ("modified", "foreign", "unreadable") and not force:
            preserved.append(filename)
        else:
            _write_hook_body(hook_path, script)
            _make_executable(hook_path)
        _merge_hook_entry(settings, event, f"python3 {hook_path}")
    settings_path.parent.mkdir(parents=True, exist_ok=True)
    settings_path.write_text(json.dumps(settings, indent=2) + "\n", encoding="utf-8")
    wired = " + ".join(e for e, _f, _s in to_install)
    return f"Wired roam compile+verify into Claude Code ({wired}): {settings_path}", preserved


@hooks.command("claude")
@click.option("--write", is_flag=True, help="Apply: write the hook scripts + merge settings.json.")
@click.option("--user", "user_level", is_flag=True, help="Install user-global (~/.claude) instead of project-local.")
@click.option("--uninstall", "do_uninstall", is_flag=True, help="Remove the roam hook entries + scripts.")
@click.option(
    "--no-verify",
    "no_verify",
    is_flag=True,
    help="Install only the compile hook; skip the post-edit verify Stop hook.",
)
@click.option(
    "--force",
    is_flag=True,
    help="Refresh hook bodies even if unrecognized (user-modified / externally managed). "
    "Roam-written stale bodies auto-heal on --write; --force overrides the foreign-body guard.",
)
@click.pass_context
def claude_setup(ctx, write, user_level, do_uninstall, no_verify, force):
    """Wire the roam compile+verify loop into Claude Code via hooks.

    Run `roam hooks claude` to preview, `--write` to apply. Two hooks:
    UserPromptSubmit runs `roam --json compile` on every prompt (p50 ~92ms)
    and injects the envelope as context — the compile-prefix channel
    measured at -83%% turns on Claude. Stop runs scoped
    `roam verify --auto --diff-only` after the agent finishes editing —
    including the default-on leak gate (credential shapes + the repo's
    `.roam-leak-patterns.py` catalogue) — and on findings blocks once with
    an AUTO-FIX directive so the agent resolves them before stopping;
    quiet on PASS. Both fail-open; `--no-verify` installs the compile
    hook alone.
    """
    json_mode = ctx.obj.get("json") if ctx.obj else False
    settings_path = _claude_settings_path(user_level)
    hook_dir = _claude_hook_dir(user_level)
    # (event, filename, script) per managed hook.
    managed = [("UserPromptSubmit", _CLAUDE_UPS_HOOK_FILENAME, _CLAUDE_UPS_HOOK_SCRIPT)]
    if not no_verify:
        managed.append(("Stop", _CLAUDE_STOP_HOOK_FILENAME, _CLAUDE_STOP_HOOK_SCRIPT))

    settings, load_error = _load_claude_settings(settings_path)
    if load_error:
        _emit_hooks_verdict(json_mode, load_error, {"partial_success": True}, {}, [])
        ctx.exit(1)
        return

    if do_uninstall:
        verdict, removed_any = _claude_uninstall_hooks(settings, settings_path, hook_dir, write)
        _emit_hooks_verdict(json_mode, verdict, {"removed": removed_any, "settings_path": str(settings_path)}, {}, [])
        return

    to_install = [(e, f, s) for e, f, s in managed if not _hook_entry_present(settings, e, f)]

    # C3 heal: a hook whose settings entry is present but whose BODY on disk is a
    # stale roam-written version (frozen at an older install) is invisible to the
    # settings-based `to_install` above — so it never refreshes and misses new
    # behaviour (session-id join key, agent-mode stamp). Scan bodies and refresh
    # the ones roam provably shipped; reinstall bodies that vanished while their
    # entry stayed wired; leave modified/foreign/unreadable bodies untouched
    # (reported below; --force overwrites, with a .bak).
    body_states = _scan_hook_bodies(hook_dir, managed)
    heal = [
        (e, f, s) for e, f, s in managed if body_states.get(f) in ("heal", "missing") and (e, f, s) not in to_install
    ]
    forced = []
    if force:
        forced = [
            (e, f, s)
            for e, f, s in managed
            if body_states.get(f) in ("modified", "foreign", "unreadable")
            and (e, f, s) not in to_install
            and (e, f, s) not in heal
        ]
    attention = {f: st for f, st in body_states.items() if st in ("modified", "foreign", "unreadable") and not force}

    if not to_install and not heal and not forced:
        verdict = f"roam Claude Code hooks wired + current in {settings_path}"
        if attention:
            verdict = (
                f"roam Claude Code hooks wired in {settings_path}; {len(attention)} body(ies) need attention (see NOTE)"
            )
    elif write:
        parts = []
        preserved: list[str] = []
        if to_install:
            installed_verdict, preserved = _claude_install_hooks(
                settings, settings_path, hook_dir, to_install, body_states=body_states, force=force
            )
            parts.append(installed_verdict)
        surgery_reset = False
        for _e, filename, script in heal + forced:
            hook_path = hook_dir / filename
            try:
                prior = hook_path.read_text(encoding="utf-8") if hook_path.exists() else ""
            except (OSError, ValueError):
                prior = ""
            if "--override-mode" in prior and "--override-mode" not in script:
                surgery_reset = True
            _write_hook_body(hook_path, script)
            _make_executable(hook_path)
        if heal:
            parts.append(f"healed {len(heal)} stale hook body(ies): {', '.join(f for _e, f, _s in heal)}")
        if forced:
            parts.append(f"force-refreshed {len(forced)} body(ies): {', '.join(f for _e, f, _s in forced)}")
        verdict = "; ".join(parts)
        # preserved ⊆ attention already (same states, force=False) — the NOTE
        # below reports them; nothing to overwrite here.
        if surgery_reset:
            verdict += "; mode-override surgery reset — re-run `compile claude` to re-apply"
    else:
        actions = []
        if to_install:
            actions.append(f"install {', '.join(f for _e, f, _s in to_install)}")
        if heal:
            actions.append(f"heal stale/missing body {', '.join(f for _e, f, _s in heal)}")
        if forced:
            actions.append(f"force-overwrite unrecognized body {', '.join(f for _e, f, _s in forced)}")
        verdict = f"Would {' and '.join(actions)} (dry-run; add --write)"

    text_lines = []
    if (to_install or heal or forced) and not write:
        text_lines = ["  hook script : " + str(hook_dir / f) for _e, f, _s in (to_install + heal + forced)]
        text_lines.append("  settings    : " + str(settings_path))
        text_lines.append("  apply with  : roam hooks claude --write" + (" --user" if user_level else ""))
    if attention:
        detail = ", ".join(f"{f} ({st})" for f, st in sorted(attention.items()))
        text_lines.append(
            f"  NOTE: {len(attention)} hook body(ies) are user-modified, externally managed, or "
            f"unreadable ({detail}); not healed. Use --force to overwrite (a .bak is kept)."
        )
    _emit_hooks_verdict(
        json_mode,
        verdict,
        {
            "already_installed": not to_install,
            "applied": bool(write and (to_install or heal or forced)),
            "healed": [f for _e, f, _s in heal],
            "forced": [f for _e, f, _s in forced],
            "foreign_bodies": sorted(attention),
            "body_states": body_states,
            "hook_body_version": _HOOK_BODY_VERSION,
        },
        {"settings_path": str(settings_path), "hook_dir": str(hook_dir)},
        text_lines,
    )
