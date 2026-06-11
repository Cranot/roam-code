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
import stat
import subprocess
import sys
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

    hooks_dir = _find_git_hooks_dir()
    if hooks_dir is None:
        msg = "No git repository found. Run `git init` first."
        if json_mode:
            click.echo(
                to_json(
                    json_envelope(
                        "hooks",
                        summary={"verdict": msg, "installed": [], "skipped": [], "errors": []},
                        hooks_dir=None,
                    )
                )
            )
        else:
            click.echo(f"VERDICT: {msg}")
        ctx.exit(1)
        return

    hooks_dir.mkdir(parents=True, exist_ok=True)

    results: dict[str, str] = {}
    installed: list[str] = []
    skipped: list[str] = []
    errors: list[dict] = []

    for hook_name in _HOOK_NAMES:
        hook_path = hooks_dir / hook_name
        action, error = _install_hook(hook_path, force=force)
        results[hook_name] = action
        if error:
            errors.append({"hook": hook_name, "error": error})
        elif action in ("created", "appended", "overwritten"):
            installed.append(hook_name)
        else:
            skipped.append(hook_name)

    if errors:
        verdict = f"Installed {len(installed)} hook(s) with {len(errors)} error(s)."
    elif installed:
        verdict = f"Installed {len(installed)} hook(s): {', '.join(installed)}."
    else:
        verdict = f"All hooks already installed ({len(skipped)} skipped). Use --force to refresh."

    if json_mode:
        click.echo(
            to_json(
                json_envelope(
                    "hooks",
                    summary={
                        "verdict": verdict,
                        "installed": installed,
                        "skipped": skipped,
                        "errors": errors,
                    },
                    hooks_dir=str(hooks_dir),
                    results=results,
                )
            )
        )
        return

    click.echo(f"VERDICT: {verdict}")
    click.echo(f"Hooks directory: {hooks_dir}")
    for hook_name in _HOOK_NAMES:
        action = results.get(hook_name, "?")
        click.echo(f"  {hook_name:20s} {action}")
    if skipped:
        click.echo("Tip: use --force to overwrite existing roam hook sections.")


# ---------------------------------------------------------------------------
# uninstall
# ---------------------------------------------------------------------------


@hooks.command("uninstall")
@click.pass_context
def uninstall(ctx):
    """Remove roam git hooks (or the roam section from shared hooks)."""
    json_mode = ctx.obj.get("json") if ctx.obj else False

    hooks_dir = _find_git_hooks_dir()
    if hooks_dir is None:
        msg = "No git repository found."
        if json_mode:
            click.echo(
                to_json(
                    json_envelope(
                        "hooks",
                        summary={"verdict": msg, "removed": [], "not_installed": [], "errors": []},
                        hooks_dir=None,
                    )
                )
            )
        else:
            click.echo(f"VERDICT: {msg}")
        ctx.exit(1)
        return

    results: dict[str, str] = {}
    removed: list[str] = []
    not_installed: list[str] = []
    errors: list[dict] = []

    for hook_name in _HOOK_NAMES:
        hook_path = hooks_dir / hook_name
        action, error = _uninstall_hook(hook_path)
        results[hook_name] = action
        if error:
            errors.append({"hook": hook_name, "error": error})
        elif action in ("removed", "deleted"):
            removed.append(hook_name)
        else:
            not_installed.append(hook_name)

    if removed:
        verdict = f"Removed roam hooks from: {', '.join(removed)}."
    else:
        verdict = "No roam hooks found to remove."

    if json_mode:
        click.echo(
            to_json(
                json_envelope(
                    "hooks",
                    summary={
                        "verdict": verdict,
                        "removed": removed,
                        "not_installed": not_installed,
                        "errors": errors,
                    },
                    hooks_dir=str(hooks_dir),
                    results=results,
                )
            )
        )
        return

    click.echo(f"VERDICT: {verdict}")
    for hook_name in _HOOK_NAMES:
        action = results.get(hook_name, "?")
        click.echo(f"  {hook_name:20s} {action}")


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

_CLAUDE_UPS_HOOK_FILENAME = "roam-compile-ups.py"
_CLAUDE_UPS_HOOK_SCRIPT = '''#!/usr/bin/env python3
"""roam compile -> Claude Code UserPromptSubmit context injection.

Installed by `roam hooks claude --write`. FAIL-OPEN: any error prints
nothing and exits 0 (a broken roam install must never block a turn).
"""
import json
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
        proc = subprocess.run(
            ["roam", "--json", "compile", prompt],
            capture_output=True, text=True, timeout=_COMPILE_TIMEOUT_S,
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
"""
import json
import subprocess
import sys

_VERIFY_TIMEOUT_S = 90


def main():
    try:
        payload = json.load(sys.stdin)
        if payload.get("stop_hook_active"):
            return  # already inside a stop-hook continuation; never loop
        proc = subprocess.run(
            ["roam", "--json", "verify", "--auto", "--diff-only"],
            capture_output=True, text=True, timeout=_VERIFY_TIMEOUT_S,
        )
        if not proc.stdout.strip():
            return
        d = json.loads(proc.stdout)
        summary = d.get("summary") or {}
        verdict = str(summary.get("verdict") or "")
        if not verdict or verdict.upper().startswith("PASS"):
            return  # quiet on pass — signal, not noise
        lines = [f"roam verify (post-edit, changed lines vs HEAD): {verdict}"]
        findings = []
        for cat, res in (d.get("categories") or {}).items():
            for v in (res or {}).get("violations") or []:
                findings.append(v)
        for v in findings[:8]:
            loc = f"{v.get('file')}:{v.get('line')}" if v.get("line") else str(v.get("file"))
            lines.append(f"  - [{v.get('category')}] {loc} -- {v.get('message')}")
            if v.get("fix"):
                lines.append(f"      fix: {v['fix']}")
        if len(findings) > 8:
            lines.append(f"  ... and {len(findings) - 8} more")
        # AUTO-FIX directive — on by default. The block makes the agent
        # resolve findings on lines it just touched instead of stopping;
        # the stop_hook_active guard above bounds it to one fix round.
        lines.append(
            "AUTO-FIX: resolve these now. EDIT the file(s) to fix each "
            "finding on a line your change touched; a genuine false "
            "positive goes in .roam-suppressions.yml (rule/file/symbol or "
            "line + reason); only clearly pre-existing, unrelated findings "
            "may be left. Verify re-runs automatically after your fix."
        )
        print(json.dumps({"decision": "block", "reason": "\\n".join(lines)}))
    except Exception:
        return  # fail open


main()
'''


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


def _claude_install_hooks(settings: dict, settings_path: Path, hook_dir: Path, to_install: list) -> str:
    """Write the hook scripts + merge settings entries (settings.json backed up)."""
    if settings_path.exists():
        backup = settings_path.with_suffix(".json.bak")
        backup.write_text(settings_path.read_text(encoding="utf-8"), encoding="utf-8")
    hook_dir.mkdir(parents=True, exist_ok=True)
    for event, filename, script in to_install:
        hook_path = hook_dir / filename
        hook_path.write_text(script, encoding="utf-8")
        _make_executable(hook_path)
        _merge_hook_entry(settings, event, f"python3 {hook_path}")
    settings_path.parent.mkdir(parents=True, exist_ok=True)
    settings_path.write_text(json.dumps(settings, indent=2) + "\n", encoding="utf-8")
    wired = " + ".join(e for e, _f, _s in to_install)
    return f"Wired roam compile+verify into Claude Code ({wired}): {settings_path}"


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
@click.pass_context
def claude_setup(ctx, write, user_level, do_uninstall, no_verify):
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
    if not to_install:
        verdict = f"roam Claude Code hooks already wired in {settings_path}"
    elif write:
        verdict = _claude_install_hooks(settings, settings_path, hook_dir, to_install)
    else:
        names = ", ".join(f for _e, f, _s in to_install)
        verdict = (
            f"Would write {names} under {hook_dir} and merge hook entries into {settings_path} (dry-run; add --write)"
        )

    text_lines = []
    if to_install and not write:
        text_lines = ["  hook script : " + str(hook_dir / f) for _e, f, _s in to_install]
        text_lines.append("  settings    : " + str(settings_path))
        text_lines.append("  apply with  : roam hooks claude --write" + (" --user" if user_level else ""))
    _emit_hooks_verdict(
        json_mode,
        verdict,
        {"already_installed": not to_install, "applied": bool(write and to_install)},
        {"settings_path": str(settings_path), "hook_dir": str(hook_dir)},
        text_lines,
    )
