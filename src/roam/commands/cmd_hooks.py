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
import shlex
import stat
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

import click

from roam.atomic_io import atomic_write_bytes
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
# Legacy Compile Code releases rewrote only roam invocation lines, never this
# stamp. Roam v11 owns the audited maintenance override in its canonical body;
# Compile Code 0.2+ no longer rewrites hooks or installed Roam source.
# v3 (2026-07-16): Stop hook gained the opt-in Loop-B whole-repo report refresh
# (ROAM_HOOK_REPORT_REFRESH). Deployed v2 bodies heal to v3 on `hooks claude
# --write`, which is how this new body reaches already-wired installs.
# v4 (2026-07-16): UPS hook tries the S2-lite warm compile daemon first
# (.roam/compile-daemon.json, ~10 ms connect budget) and falls back to the
# cold `roam --json compile` spawn on any failure. Deployed v3 bodies heal.
# v5 (2026-07-16): prompt and stop hooks share a per-turn episode id, append
# counts-only lifecycle events, and forward episode_id + turn_seq through both
# warm-daemon and cold compile paths. Deployed v4 bodies heal.
# v6 (2026-07-16): events stamp provenance + hook version and Stop emits a
# closed verification-health state. Deployed v5 bodies heal.
# v7 (2026-07-17): edited stops fail closed when Verify is unavailable,
# malformed, or incomplete; FAIL-without-findings also blocks. Deployed v6
# bodies heal so every wired client receives the proof-completeness contract.
# v8 (2026-07-17): correction continuations re-run Verify instead of silently
# allowing the second stop. Verify envelopes are bound to their process exit
# code and strict count/completeness invariants. Claude Code itself caps
# consecutive Stop-hook continuations, preserving loop safety without granting
# an unverified completion.
# v9 (2026-07-17): UserPromptSubmit records the episode base HEAD and a
# policy/hook/settings digest outside the repository. Stop binds Verify to an
# exact receipt-v2 filename/content snapshot, covers commits made during the
# turn, and rejects any pre/post verification mutation before allowing.
# v10 (2026-07-17): require Verify receipt v3's before/after byte digests and
# stable file-identity proof. Deployed v9 bodies heal so same-byte replacement
# during Verify can never satisfy the Stop gate.
# v12 (2026-07-19): treat a hook evidence directory on another Windows volume
# as safely outside the repository. ``os.path.commonpath`` raises ValueError
# across drive letters; v11 accidentally collapsed that safe case into an
# unavailable evidence root and dropped episode identity.
_HOOK_BODY_VERSION = 12
_HOOK_VERSION_MARKER = "# roam-hook-version:"

_CLAUDE_UPS_HOOK_FILENAME = "roam-compile-ups.py"
_CLAUDE_UPS_HOOK_SCRIPT = '''#!/usr/bin/env python3
"""roam compile -> Claude Code UserPromptSubmit context injection.

Installed by `roam hooks claude --write`. FAIL-OPEN: any error prints
nothing and exits 0 (a broken roam install must never block a turn).
"""
import json
import hashlib
import os
import stat
import subprocess
import sys
import time

_COMPILE_TIMEOUT_S = 6.0
_MIN_PROMPT_CHARS = 8
_EPISODE_SCHEMA = 1
_LOCK_ATTEMPTS = 20
_LOCK_SLEEP_S = 0.005
_LOCK_STALE_S = 30.0
# S2-lite warm daemon: startup dominates this chain (~346 ms cold vs ~2 ms
# envelope compute on a cache hit), so try the loopback daemon first. The
# connect budget is tiny on purpose: an absent daemon must cost ~nothing.
_DAEMON_CONNECT_TIMEOUT_S = 0.01
_POLICY_MAX_FILE_BYTES = 4 * 1024 * 1024
_POLICY_RELATIVE_PATHS = (
    ".roam/verify.yaml",
    ".roam/verify-baseline.json",
    ".roam/constitution.yml",
    ".roam/active_mode",
    ".roam-suppressions.yml",
    ".roam-gates.yml",
    ".roam-leak-patterns.py",
    ".claude/settings.json",
    ".claude/settings.local.json",
    ".claude/hooks/roam-compile-ups.py",
    ".claude/hooks/roam-verify-stop.py",
)


def _repo_root():
    d = os.getcwd()
    while True:
        if os.path.exists(os.path.join(d, ".git")):
            return d
        parent = os.path.dirname(d)
        if parent == d:
            return os.getcwd()
        d = parent


def _evidence_base(root, create):
    """Return a restrictive state directory that is never inside the repo."""
    override = (os.environ.get("ROAM_HOOK_EVIDENCE_DIR") or "").strip()
    if override:
        base = os.path.abspath(os.path.expanduser(override))
    elif os.name == "nt" and os.environ.get("LOCALAPPDATA"):
        base = os.path.join(os.environ["LOCALAPPDATA"], "roam", "hook-evidence")
    else:
        state_home = os.environ.get("XDG_STATE_HOME") or os.path.join(os.path.expanduser("~"), ".local", "state")
        base = os.path.join(state_home, "roam", "hook-evidence")
    try:
        repo_real = os.path.normcase(os.path.realpath(root))
        base_real = os.path.normcase(os.path.realpath(base))
        try:
            base_is_inside_repo = os.path.commonpath([repo_real, base_real]) == repo_real
        except ValueError:
            # Different Windows drive letters have no common path and are, by
            # construction, outside one another.
            base_is_inside_repo = False
        if base_is_inside_repo:
            return ""
        if create:
            os.makedirs(base_real, mode=0o700, exist_ok=True)
            if os.name != "nt":
                os.chmod(base_real, 0o700)
        if not os.path.isdir(base_real):
            return ""
        return base_real
    except OSError:
        return ""


def _episode_state_path(root, session_key, create):
    base = _evidence_base(root, create)
    if not base:
        return ""
    repo_key = hashlib.sha256(os.path.normcase(os.path.realpath(root)).encode("utf-8", "replace")).hexdigest()[:24]
    state_root = os.path.join(base, repo_key)
    try:
        if create:
            os.makedirs(state_root, mode=0o700, exist_ok=True)
            if os.name != "nt":
                os.chmod(state_root, 0o700)
        if not os.path.isdir(state_root):
            return ""
        return os.path.join(state_root, session_key + ".json")
    except OSError:
        return ""


def _git_base_head(root):
    try:
        inside = subprocess.run(
            ["git", "rev-parse", "--is-inside-work-tree"], cwd=root,
            capture_output=True, text=True, timeout=10,
        )
        if inside.returncode != 0 or inside.stdout.strip() != "true":
            return "", "unavailable"
        head = subprocess.run(
            ["git", "rev-parse", "--verify", "HEAD"], cwd=root,
            capture_output=True, text=True, timeout=10,
        )
        if head.returncode != 0:
            return "", "unborn"
        value = head.stdout.strip().lower()
        if len(value) not in (40, 64) or any(ch not in "0123456789abcdef" for ch in value):
            return "", "unavailable"
        return value, "present"
    except Exception:
        return "", "unavailable"


def _same_file_state(left, right, cross_handle=False):
    fields = ["st_dev", "st_ino", "st_mode", "st_size", "st_mtime_ns", "st_ctime_ns"]
    if cross_handle and os.name == "nt":
        fields.remove("st_ctime_ns")
    return all(getattr(left, field) == getattr(right, field) for field in fields)


def _policy_file_state(path):
    """Hash one policy file without following repo-controlled symlinks."""
    try:
        before = os.lstat(path)
    except FileNotFoundError:
        return "missing", True
    except OSError:
        return "unreadable", False
    if stat.S_ISLNK(before.st_mode):
        return "unsafe_symlink", False
    if not stat.S_ISREG(before.st_mode):
        return "not_regular", False
    if before.st_size > _POLICY_MAX_FILE_BYTES:
        return "too_large", False
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0)
    try:
        fd = os.open(path, flags)
    except OSError:
        return "unreadable", False
    digest = hashlib.sha256()
    read_n = 0
    try:
        opened = os.fstat(fd)
        if not stat.S_ISREG(opened.st_mode) or not _same_file_state(before, opened, cross_handle=True):
            return "changed_during_hash", False
        while True:
            chunk = os.read(fd, 128 * 1024)
            if not chunk:
                break
            read_n += len(chunk)
            if read_n > _POLICY_MAX_FILE_BYTES:
                return "too_large", False
            digest.update(chunk)
        opened_after = os.fstat(fd)
        try:
            after = os.lstat(path)
        except OSError:
            return "changed_during_hash", False
        if (
            read_n != opened.st_size
            or not _same_file_state(opened, opened_after)
            or not _same_file_state(before, after)
        ):
            return "changed_during_hash", False
    finally:
        os.close(fd)
    return "sha256:" + digest.hexdigest(), True


def _policy_paths(root):
    paths = {"repo:" + rel: os.path.join(root, *rel.split("/")) for rel in _POLICY_RELATIVE_PATHS}
    runtime_file = os.path.abspath(globals().get("__file__") or sys.argv[0])
    runtime_dir = os.path.dirname(runtime_file)
    paths["runtime:user_prompt_hook"] = os.path.join(runtime_dir, "roam-compile-ups.py")
    paths["runtime:stop_hook"] = os.path.join(runtime_dir, "roam-verify-stop.py")
    paths["runtime:settings"] = os.path.join(os.path.dirname(runtime_dir), "settings.json")
    paths["user:settings"] = os.path.join(os.path.expanduser("~"), ".claude", "settings.json")
    paths["user:settings_local"] = os.path.join(os.path.expanduser("~"), ".claude", "settings.local.json")
    return paths


def _policy_snapshot(root):
    manifest = []
    complete = True
    for label, path in sorted(_policy_paths(root).items()):
        state, ok = _policy_file_state(path)
        manifest.append([label, state])
        complete = complete and ok
    env_values = sorted(
        [name, value]
        for name, value in os.environ.items()
        if name == "ROAM_COMPILE_VERIFY" or name == "ROAM_REPO_LEAK_PATTERNS" or name.startswith("ROAM_VERIFY_")
    )
    manifest.append(["environment", env_values])
    payload = json.dumps(manifest, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest(), complete


def _utc_now():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _session_key(payload):
    session_id = str(payload.get("session_id") or "").strip()
    transcript_path = str(payload.get("transcript_path") or "").strip()
    stable = session_id or transcript_path
    if not stable:
        return "", session_id, transcript_path
    return hashlib.sha256(stable.encode("utf-8", "replace")).hexdigest()[:24], session_id, transcript_path


def _acquire_lock(path):
    for _ in range(_LOCK_ATTEMPTS):
        try:
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            os.write(fd, str(os.getpid()).encode("ascii", "replace"))
            return fd
        except FileExistsError:
            try:
                if time.time() - os.path.getmtime(path) > _LOCK_STALE_S:
                    os.unlink(path)
                    continue
            except OSError:
                pass
            time.sleep(_LOCK_SLEEP_S)
        except OSError:
            return None
    return None


def _release_lock(path, fd):
    if fd is None:
        return
    try:
        os.close(fd)
    except OSError:
        pass
    try:
        os.unlink(path)
    except OSError:
        pass


def _atomic_json(path, value):
    tmp = path + ".tmp-%d-%d" % (os.getpid(), time.time_ns())
    try:
        fd = os.open(tmp, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(value, fh, sort_keys=True, separators=(",", ":"))
            fh.write(chr(10))
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
        if os.name != "nt":
            os.chmod(path, 0o600)
    finally:
        try:
            if os.path.exists(tmp):
                os.unlink(tmp)
        except OSError:
            pass


def _append_episode_event(root, event):
    log_dir = os.path.join(root, ".roam")
    if not os.path.isdir(log_dir):
        return
    log_path = os.path.join(log_dir, "episodes.jsonl")
    try:
        if os.path.exists(log_path) and os.path.getsize(log_path) > 10 * 1024 * 1024:
            return
    except OSError:
        return
    lock_path = log_path + ".lock"
    fd = _acquire_lock(lock_path)
    if fd is None:
        return
    try:
        with open(log_path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(event, sort_keys=True, separators=(",", ":")) + chr(10))
    finally:
        _release_lock(lock_path, fd)


def _start_episode(payload, prompt):
    root = _repo_root()
    session_key, session_id, transcript_path = _session_key(payload)
    if not session_key:
        return session_id, "", ""
    try:
        state_path = _episode_state_path(root, session_key, True)
        if not state_path:
            return session_id, "", ""
        lock_path = state_path + ".lock"
        fd = _acquire_lock(lock_path)
        if fd is None:
            return session_id, "", ""
        try:
            state = {}
            try:
                with open(state_path, encoding="utf-8") as fh:
                    loaded = json.load(fh)
                    if isinstance(loaded, dict):
                        state = loaded
            except (OSError, ValueError):
                state = {}
            turn_seq = int(state.get("last_turn_seq") or 0) + 1
            started_at_ms = int(time.time() * 1000)
            prompt_hash = hashlib.sha256(prompt.encode("utf-8", "replace")).hexdigest()
            seed = "%s\\0%d\\0%s\\0%d\\0%d" % (
                session_key,
                turn_seq,
                prompt_hash,
                time.time_ns(),
                os.getpid(),
            )
            episode_id = "ep_" + hashlib.sha256(seed.encode("utf-8")).hexdigest()[:24]
            base_head, base_head_state = _git_base_head(root)
            policy_sha256, policy_complete = _policy_snapshot(root)
            state = {
                "state_schema": "roam.hook.episode.v2",
                "repo_sha256": hashlib.sha256(
                    os.path.normcase(os.path.realpath(root)).encode("utf-8", "replace")
                ).hexdigest(),
                "session_id": session_id,
                "last_turn_seq": turn_seq,
                "active": {
                    "episode_id": episode_id,
                    "started_at_ms": started_at_ms,
                    "turn_seq": turn_seq,
                    "base_head": base_head,
                    "base_head_state": base_head_state,
                    "policy_sha256": policy_sha256,
                    "policy_complete": bool(policy_complete),
                },
            }
            _atomic_json(state_path, state)
        finally:
            _release_lock(lock_path, fd)
        event = {
            "schema_version": _EPISODE_SCHEMA,
            "hook_version": 12,
            "evidence_source": "live_hook",
            "event_id": "evt_" + hashlib.sha256((episode_id + ":start").encode("utf-8")).hexdigest()[:24],
            "episode_id": episode_id,
            "event_type": "prompt_submitted",
            "ts": _utc_now(),
            "session_id": session_id,
            "turn_seq": turn_seq,
            "terminal": False,
            "outcome": "pending",
            "prompt_sha256": prompt_hash,
            "prompt_chars": len(prompt),
            "transcript_path_sha256": (
                hashlib.sha256(transcript_path.encode("utf-8", "replace")).hexdigest()
                if transcript_path
                else ""
            ),
            "permission_mode": str(payload.get("permission_mode") or ""),
            "compile_expected": len(prompt) >= _MIN_PROMPT_CHARS,
            "health_state": "unknown",
            "base_head": base_head,
            "base_head_state": base_head_state,
            "policy_snapshot_complete": bool(policy_complete),
        }
        _append_episode_event(root, event)
        return session_id, episode_id, str(turn_seq)
    except Exception:
        return session_id, "", ""


def _try_daemon(prompt, session_id, episode_id, turn_seq):
    """Compile via the repo's warm daemon; None on ANY failure (-> cold spawn)."""
    try:
        import socket
        cfg_path = os.path.join(_repo_root(), ".roam", "compile-daemon.json")
        with open(cfg_path, encoding="utf-8") as fh:
            cfg = json.load(fh)
        s = socket.create_connection(("127.0.0.1", int(cfg["port"])),
                                     timeout=_DAEMON_CONNECT_TIMEOUT_S)
        try:
            s.settimeout(_COMPILE_TIMEOUT_S)
            req = {"token": cfg.get("token"), "op": "compile",
                   "args": [prompt], "cwd": os.getcwd(),
                   "session_id": session_id,
                   "episode_id": episode_id,
                   "turn_seq": turn_seq}
            s.sendall((json.dumps(req) + chr(10)).encode("utf-8"))
            s.shutdown(socket.SHUT_WR)
            chunks = []
            while True:
                b = s.recv(65536)
                if not b:
                    break
                chunks.append(b)
        finally:
            s.close()
        d = json.loads(b"".join(chunks).decode("utf-8"))
        if not isinstance(d, dict) or d.get("error") or "summary" not in d:
            return None  # daemon refused (wrong repo / bad token) or junk
        return d
    except Exception:
        return None  # fail open into the cold spawn


def main():
    try:
        payload = json.load(sys.stdin)
        prompt = (payload.get("prompt") or "").strip()
        session_id, episode_id, turn_seq = _start_episode(payload, prompt)
        if len(prompt) < _MIN_PROMPT_CHARS:
            return
        # Forward hook correlation state to the compiler. The privacy boundary
        # persists only the opaque episode join key; session and turn values
        # remain transient inputs used by the hook lifecycle.
        d = _try_daemon(prompt, session_id, episode_id, turn_seq)
        if d is None:
            env = os.environ.copy()
            if session_id:
                env["ROAM_SESSION_ID"] = session_id
            if episode_id:
                env["ROAM_EPISODE_ID"] = episode_id
            if turn_seq:
                env["ROAM_TURN_SEQ"] = turn_seq
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
# script: scoped `roam verify --auto --diff-only`, fail-closed when edited
# evidence is unavailable, quiet on a complete PASS.
_CLAUDE_STOP_HOOK_FILENAME = "roam-verify-stop.py"
_CLAUDE_STOP_HOOK_SCRIPT = '''#!/usr/bin/env python3
"""roam verify -> Claude Code Stop-hook post-edit check.

Installed by `roam hooks claude --write`. QUIET-ON-COMPLETE-PASS: optional
reviewers fail open, but an edited stop blocks when the required Verify call
times out, returns malformed output, or reports incomplete evidence.

Fast-exit + block-rate telemetry (2026-07-11): a stop whose prompt-base
episode has no net source delta (committed, staged, unstaged, or untracked)
skips the verify subprocess. A commit made during the episode remains in scope
even when the current working tree is clean. Every decision appends a counts-only JSON line to
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
  ROAM_HOOK_REPORT_REFRESH (default 0) -- Loop B: on an edit-stop, spawn a
      DETACHED, THROTTLED (>= 6h) whole-repo `verify --report --persist` so the
      next compile's known_findings is fresh. Never blocks the stop; opt-in
      because it runs a background whole-repo verify.
"""
import json
import hashlib
import errno
import os
import secrets
import stat
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
_EPISODE_SCHEMA = 1
_EPISODE_HOOK_VERSION = 12
_EPISODE_LOCK_ATTEMPTS = 20
_EPISODE_LOCK_SLEEP_S = 0.005
_EPISODE_LOCK_STALE_S = 30.0
_POLICY_MAX_FILE_BYTES = 4 * 1024 * 1024
_VERIFY_MAX_FILE_BYTES = 64 * 1024 * 1024
_VERIFY_MAX_TOTAL_BYTES = 256 * 1024 * 1024
_VERIFY_MAX_TARGETS = 4096
_VERIFY_MAX_ARG_CHARS = 128 * 1024
_POLICY_RELATIVE_PATHS = (
    ".roam/verify.yaml",
    ".roam/verify-baseline.json",
    ".roam/constitution.yml",
    ".roam/active_mode",
    ".roam-suppressions.yml",
    ".roam-gates.yml",
    ".roam-leak-patterns.py",
    ".claude/settings.json",
    ".claude/settings.local.json",
    ".claude/hooks/roam-compile-ups.py",
    ".claude/hooks/roam-verify-stop.py",
)


def _evidence_base(root, create=False):
    """Return the same out-of-repository evidence root as UserPromptSubmit."""
    override = (os.environ.get("ROAM_HOOK_EVIDENCE_DIR") or "").strip()
    if override:
        base = os.path.abspath(os.path.expanduser(override))
    elif os.name == "nt" and os.environ.get("LOCALAPPDATA"):
        base = os.path.join(os.environ["LOCALAPPDATA"], "roam", "hook-evidence")
    else:
        state_home = os.environ.get("XDG_STATE_HOME") or os.path.join(os.path.expanduser("~"), ".local", "state")
        base = os.path.join(state_home, "roam", "hook-evidence")
    try:
        repo_real = os.path.normcase(os.path.realpath(root))
        base_real = os.path.normcase(os.path.realpath(base))
        try:
            base_is_inside_repo = os.path.commonpath([repo_real, base_real]) == repo_real
        except ValueError:
            # Different Windows drive letters have no common path and are, by
            # construction, outside one another.
            base_is_inside_repo = False
        if base_is_inside_repo:
            return ""
        if create:
            os.makedirs(base_real, mode=0o700, exist_ok=True)
            if os.name != "nt":
                os.chmod(base_real, 0o700)
        if not os.path.isdir(base_real):
            return ""
        return base_real
    except OSError:
        return ""


def _episode_state_path(root, session_key):
    base = _evidence_base(root)
    if not base:
        return ""
    repo_key = hashlib.sha256(os.path.normcase(os.path.realpath(root)).encode("utf-8", "replace")).hexdigest()[:24]
    state_root = os.path.join(base, repo_key)
    if not os.path.isdir(state_root):
        return ""
    return os.path.join(state_root, session_key + ".json")


def _same_file_state(left, right, cross_handle=False):
    fields = ["st_dev", "st_ino", "st_mode", "st_size", "st_mtime_ns", "st_ctime_ns"]
    if cross_handle and os.name == "nt":
        fields.remove("st_ctime_ns")
    return all(getattr(left, field) == getattr(right, field) for field in fields)


def _policy_file_state(path):
    try:
        before = os.lstat(path)
    except FileNotFoundError:
        return "missing", True
    except OSError:
        return "unreadable", False
    if stat.S_ISLNK(before.st_mode):
        return "unsafe_symlink", False
    if not stat.S_ISREG(before.st_mode):
        return "not_regular", False
    if before.st_size > _POLICY_MAX_FILE_BYTES:
        return "too_large", False
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0)
    try:
        fd = os.open(path, flags)
    except OSError:
        return "unreadable", False
    digest = hashlib.sha256()
    read_n = 0
    try:
        opened = os.fstat(fd)
        if not stat.S_ISREG(opened.st_mode) or not _same_file_state(before, opened, cross_handle=True):
            return "changed_during_hash", False
        while True:
            chunk = os.read(fd, 128 * 1024)
            if not chunk:
                break
            read_n += len(chunk)
            if read_n > _POLICY_MAX_FILE_BYTES:
                return "too_large", False
            digest.update(chunk)
        opened_after = os.fstat(fd)
        try:
            after = os.lstat(path)
        except OSError:
            return "changed_during_hash", False
        if (
            read_n != opened.st_size
            or not _same_file_state(opened, opened_after)
            or not _same_file_state(before, after)
        ):
            return "changed_during_hash", False
    finally:
        os.close(fd)
    return "sha256:" + digest.hexdigest(), True


def _policy_paths(root):
    paths = {"repo:" + rel: os.path.join(root, *rel.split("/")) for rel in _POLICY_RELATIVE_PATHS}
    runtime_file = os.path.abspath(globals().get("__file__") or sys.argv[0])
    runtime_dir = os.path.dirname(runtime_file)
    paths["runtime:user_prompt_hook"] = os.path.join(runtime_dir, "roam-compile-ups.py")
    paths["runtime:stop_hook"] = os.path.join(runtime_dir, "roam-verify-stop.py")
    paths["runtime:settings"] = os.path.join(os.path.dirname(runtime_dir), "settings.json")
    paths["user:settings"] = os.path.join(os.path.expanduser("~"), ".claude", "settings.json")
    paths["user:settings_local"] = os.path.join(os.path.expanduser("~"), ".claude", "settings.local.json")
    return paths


def _policy_snapshot(root):
    manifest = []
    complete = True
    for label, path in sorted(_policy_paths(root).items()):
        state, ok = _policy_file_state(path)
        manifest.append([label, state])
        complete = complete and ok
    env_values = sorted(
        [name, value]
        for name, value in os.environ.items()
        if name == "ROAM_COMPILE_VERIFY" or name == "ROAM_REPO_LEAK_PATTERNS" or name.startswith("ROAM_VERIFY_")
    )
    manifest.append(["environment", env_values])
    payload = json.dumps(manifest, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest(), complete


def _episode_session_key(payload):
    session_id = str(payload.get("session_id") or "").strip()
    transcript_path = str(payload.get("transcript_path") or "").strip()
    stable = session_id or transcript_path
    if not stable:
        return "", session_id
    return hashlib.sha256(stable.encode("utf-8", "replace")).hexdigest()[:24], session_id


def _episode_lock(path):
    for _ in range(_EPISODE_LOCK_ATTEMPTS):
        try:
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            os.write(fd, str(os.getpid()).encode("ascii", "replace"))
            return fd
        except FileExistsError:
            try:
                if time.time() - os.path.getmtime(path) > _EPISODE_LOCK_STALE_S:
                    os.unlink(path)
                    continue
            except OSError:
                pass
            time.sleep(_EPISODE_LOCK_SLEEP_S)
        except OSError:
            return None
    return None


def _episode_unlock(path, fd):
    if fd is None:
        return
    try:
        os.close(fd)
    except OSError:
        pass
    try:
        os.unlink(path)
    except OSError:
        pass


def _active_episode(payload):
    session_key, session_id = _episode_session_key(payload)
    if not session_key:
        return {"session_id": session_id, "evidence_required": False}
    root = _hook_repo_root()
    state_path = _episode_state_path(root, session_key)
    if not state_path:
        return {
            "session_id": session_id,
            "evidence_required": True,
            "evidence_error": "protected_episode_state_unavailable",
        }
    try:
        state_meta = os.lstat(state_path)
        if not stat.S_ISREG(state_meta.st_mode) or state_meta.st_size > 1024 * 1024:
            raise OSError("unsafe protected episode state")
        if os.name != "nt" and (
            state_meta.st_uid != os.getuid() or stat.S_IMODE(state_meta.st_mode) & 0o077
        ):
            raise OSError("unprotected episode state permissions")
    except OSError:
        return {
            "session_id": session_id,
            "evidence_required": True,
            "evidence_error": "protected_episode_state_unsafe",
        }
    lock_path = state_path + ".lock"
    fd = _episode_lock(lock_path)
    if fd is None:
        return {
            "session_id": session_id,
            "evidence_required": True,
            "evidence_error": "protected_episode_state_locked",
        }
    try:
        try:
            with open(state_path, encoding="utf-8") as fh:
                state = json.load(fh)
        except (OSError, ValueError):
            state = {}
        expected_repo = hashlib.sha256(
            os.path.normcase(os.path.realpath(root)).encode("utf-8", "replace")
        ).hexdigest()
        if (
            not isinstance(state, dict)
            or state.get("state_schema") != "roam.hook.episode.v2"
            or state.get("repo_sha256") != expected_repo
        ):
            return {
                "session_id": session_id,
                "evidence_required": True,
                "evidence_error": "protected_episode_state_invalid",
            }
        active = state.get("active") if isinstance(state, dict) else {}
        if not isinstance(active, dict):
            return {
                "session_id": session_id,
                "evidence_required": True,
                "evidence_error": "protected_episode_state_missing",
            }
        return {
            "session_id": str((state or {}).get("session_id") or session_id),
            "episode_id": str(active.get("episode_id") or ""),
            "turn_seq": active.get("turn_seq"),
            "started_at_ms": active.get("started_at_ms"),
            "base_head": active.get("base_head"),
            "base_head_state": active.get("base_head_state"),
            "policy_sha256": active.get("policy_sha256"),
            "policy_complete": active.get("policy_complete"),
            "evidence_required": True,
            "_state_path": state_path,
            "_state": state,
        }
    finally:
        _episode_unlock(lock_path, fd)


def _clear_active_episode(active):
    state_path = str(active.get("_state_path") or "")
    episode_id = str(active.get("episode_id") or "")
    if not state_path or not episode_id:
        return
    lock_path = state_path + ".lock"
    fd = _episode_lock(lock_path)
    if fd is None:
        return
    try:
        try:
            with open(state_path, encoding="utf-8") as fh:
                state = json.load(fh)
        except (OSError, ValueError):
            state = {}
        current = state.get("active") if isinstance(state, dict) else {}
        if not isinstance(current, dict) or str(current.get("episode_id") or "") != episode_id:
            return
        state["active"] = None
        tmp = state_path + ".tmp-%d-%d" % (os.getpid(), time.time_ns())
        try:
            tmp_fd = os.open(tmp, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            with os.fdopen(tmp_fd, "w", encoding="utf-8") as fh:
                json.dump(state, fh, sort_keys=True, separators=(",", ":"))
                fh.write(chr(10))
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp, state_path)
            if os.name != "nt":
                os.chmod(state_path, 0o600)
        finally:
            try:
                if os.path.exists(tmp):
                    os.unlink(tmp)
            except OSError:
                pass
    finally:
        _episode_unlock(lock_path, fd)


def _append_episode_event(event):
    root = _hook_repo_root()
    log_dir = os.path.join(root, ".roam")
    if not os.path.isdir(log_dir):
        return
    log_path = os.path.join(log_dir, "episodes.jsonl")
    try:
        if os.path.exists(log_path) and os.path.getsize(log_path) > 10 * 1024 * 1024:
            return
    except OSError:
        return
    lock_path = log_path + ".lock"
    fd = _episode_lock(lock_path)
    if fd is None:
        return
    try:
        with open(log_path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(event, sort_keys=True, separators=(",", ":")) + chr(10))
    finally:
        _episode_unlock(lock_path, fd)


def _record_episode_stop(payload, active, outcome, terminal, blocked, verify_ms, skipped_no_edit, diff_identity):
    episode_id = str(active.get("episode_id") or "")
    if not episode_id:
        seed = "%s:%d:%d" % (str(payload.get("session_id") or "orphan"), time.time_ns(), os.getpid())
        episode_id = "orphan_" + hashlib.sha256(seed.encode("utf-8", "replace")).hexdigest()[:20]
    now_ms = int(time.time() * 1000)
    try:
        duration_ms = max(0, now_ms - int(active.get("started_at_ms")))
    except (TypeError, ValueError):
        duration_ms = None
    event_type = "stop_continuation" if payload.get("stop_hook_active") else "stop_decision"
    if outcome == "verified_clean":
        health_state = "verification_passed"
    elif outcome in ("verification_blocked", "verification_failed_without_findings"):
        health_state = "verification_failed"
    elif outcome in (
        "verify_unavailable",
        "policy_evidence_unavailable",
        "policy_tampering",
        "verification_race",
    ):
        health_state = "verification_unavailable"
    elif outcome == "continued_after_block":
        health_state = "continuation_unverified"
    else:
        health_state = "not_applicable"
    event = {
        "schema_version": _EPISODE_SCHEMA,
        "hook_version": _EPISODE_HOOK_VERSION,
        "evidence_source": "live_hook",
        "event_id": "evt_" + hashlib.sha256(
            ("%s:%s:%d" % (episode_id, event_type, time.time_ns())).encode("utf-8")
        ).hexdigest()[:24],
        "episode_id": episode_id,
        "event_type": event_type,
        "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "session_id": str(active.get("session_id") or payload.get("session_id") or ""),
        "turn_seq": active.get("turn_seq"),
        "terminal": bool(terminal),
        "outcome": outcome,
        "duration_ms": duration_ms,
        "blocked": bool(blocked),
        "verify_ms": int(verify_ms),
        "skipped_no_edit": bool(skipped_no_edit),
        "changed_files": diff_identity.get("changed_files"),
        "diff_sha256": str(diff_identity.get("diff_sha256") or ""),
        "base_head": str(active.get("base_head") or ""),
        "base_head_state": str(active.get("base_head_state") or ""),
        "health_state": health_state,
    }
    _append_episode_event(event)
    if terminal:
        _clear_active_episode(active)
    return event


def _env_on(name, default):
    return (os.environ.get(name, default) or "").strip().lower() not in ("", "0", "false", "no", "off")


_REPORT_REFRESH_HOURS = 6.0
_REFRESH_CLAIM_MINUTES = 30.0  # single-flight window: at most one spawn per claim age


def _hook_repo_root():
    """Nearest ancestor (cwd included) containing .git; cwd when none found.

    The spawned verify persists at the PROJECT ROOT (find_project_root walks
    up to .git) — the throttle and claim must anchor to the same directory,
    or a hook run from a repo subdir respawns on every stop forever.
    """
    d = os.getcwd()
    while True:
        if os.path.exists(os.path.join(d, ".git")):
            return d
        parent = os.path.dirname(d)
        if parent == d:
            return os.getcwd()
        d = parent


def _git_current_head(root):
    try:
        inside = subprocess.run(
            ["git", "rev-parse", "--is-inside-work-tree"], cwd=root,
            capture_output=True, text=True, timeout=_GIT_CHECK_TIMEOUT_S,
        )
        if inside.returncode != 0 or inside.stdout.strip() != "true":
            return "", "unavailable"
        head = subprocess.run(
            ["git", "rev-parse", "--verify", "HEAD"], cwd=root,
            capture_output=True, text=True, timeout=_GIT_CHECK_TIMEOUT_S,
        )
        if head.returncode != 0:
            return "", "unborn"
        value = head.stdout.strip().lower()
        if len(value) not in (40, 64) or any(ch not in "0123456789abcdef" for ch in value):
            return "", "unavailable"
        return value, "present"
    except Exception:
        return "", "unavailable"


def _decode_git_paths(raw):
    paths = []
    for value in raw.split(bytes((0,))):
        if not value:
            continue
        path = os.fsdecode(value).replace("\\\\", "/")
        if any(0xD800 <= ord(ch) <= 0xDFFF for ch in path):
            return None
        if path.startswith("/") or os.path.isabs(path) or ".." in path.split("/"):
            return None
        if path == ".roam" or path.startswith(".roam/") or path == ".claude" or path.startswith(".claude/"):
            continue
        paths.append(path)
    return paths


def _git_name_bytes(root, args):
    try:
        proc = subprocess.run(
            ["git", *args], cwd=root, capture_output=True,
            timeout=_GITDIFF_TIMEOUT_S,
        )
    except Exception:
        return None
    return proc.stdout if proc.returncode == 0 else None


def _verification_scope_paths(root, active):
    """Resolve the full episode delta, including commits made after prompt."""
    if active.get("evidence_required"):
        if active.get("evidence_error"):
            return None, str(active.get("evidence_error"))
        base_head = active.get("base_head")
        base_state = active.get("base_head_state")
        if base_state == "present":
            if (
                not isinstance(base_head, str)
                or len(base_head) not in (40, 64)
                or any(ch not in "0123456789abcdef" for ch in base_head)
            ):
                return None, "episode_base_head_invalid"
        elif base_state == "unborn":
            base_head = ""
        else:
            return None, "episode_base_head_unavailable"
    else:
        base_head, base_state = _git_current_head(root)
        if base_state == "unavailable":
            return None, "git_head_unavailable"

    raw_paths = []
    if base_state == "present":
        changed = _git_name_bytes(
            root,
            ["diff", "--no-ext-diff", "--name-only", "-z", "--no-renames", base_head, "--"],
        )
        if changed is None:
            return None, "episode_diff_unavailable"
        decoded = _decode_git_paths(changed)
        if decoded is None:
            return None, "episode_path_decode_failed"
        raw_paths.extend(decoded)
    else:
        # An unborn base means every currently tracked path was created during
        # the episode. `ls-files --cached` continues to cover it after a commit.
        tracked = _git_name_bytes(root, ["ls-files", "--cached", "-z"])
        if tracked is None:
            return None, "episode_index_scope_unavailable"
        decoded = _decode_git_paths(tracked)
        if decoded is None:
            return None, "episode_path_decode_failed"
        raw_paths.extend(decoded)

    untracked = _git_name_bytes(root, ["ls-files", "--others", "--exclude-standard", "-z"])
    if untracked is None:
        return None, "episode_untracked_scope_unavailable"
    decoded = _decode_git_paths(untracked)
    if decoded is None:
        return None, "episode_path_decode_failed"
    raw_paths.extend(decoded)
    paths = sorted(set(path.strip() for path in raw_paths if path.strip()))
    if len(paths) > _VERIFY_MAX_TARGETS or sum(len(path) + 1 for path in paths) > _VERIFY_MAX_ARG_CHARS:
        return None, "episode_scope_too_large"
    return paths, None


def _scope_sha256(paths):
    payload = json.dumps(sorted(set(paths)), ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _verification_content_sha256(root, paths):
    """Mirror Verify receipt-v3 hashing: exact bytes, missing markers, no symlinks."""
    try:
        canonical_root = os.path.realpath(root)
        if not os.path.isdir(canonical_root):
            return None, "verification_root_unavailable"
    except OSError:
        return None, "verification_root_unavailable"
    manifest = []
    total_bytes = 0
    for relative_path in sorted(set(paths)):
        candidate = os.path.join(canonical_root, *relative_path.split("/"))
        try:
            if os.path.commonpath([canonical_root, os.path.abspath(candidate)]) != canonical_root:
                return None, "scope_path_outside_root"
        except ValueError:
            return None, "scope_path_outside_root"
        parent = os.path.dirname(candidate)
        if not os.path.exists(parent):
            manifest.append([relative_path, "missing"])
            continue
        try:
            canonical_parent = os.path.realpath(parent)
            if os.path.commonpath([canonical_root, canonical_parent]) != canonical_root:
                return None, "scope_path_outside_root"
        except (OSError, ValueError):
            return None, "scope_file_unreadable"
        try:
            path_before = os.lstat(candidate)
        except FileNotFoundError:
            manifest.append([relative_path, "missing"])
            continue
        except OSError:
            return None, "scope_file_unreadable"
        if stat.S_ISLNK(path_before.st_mode):
            return None, "scope_file_symlink"
        if not stat.S_ISREG(path_before.st_mode):
            return None, "scope_file_not_regular"
        if path_before.st_size > _VERIFY_MAX_FILE_BYTES:
            return None, "scope_file_too_large"
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0)
        try:
            fd = os.open(candidate, flags)
        except OSError as exc:
            return None, "scope_file_symlink" if exc.errno == errno.ELOOP else "scope_file_unreadable"
        digest = hashlib.sha256()
        bytes_read = 0
        try:
            opened_before = os.fstat(fd)
            if not stat.S_ISREG(opened_before.st_mode) or not _same_file_state(
                path_before, opened_before, cross_handle=True
            ):
                return None, "scope_file_changed_during_hash"
            while True:
                chunk = os.read(fd, 256 * 1024)
                if not chunk:
                    break
                bytes_read += len(chunk)
                if bytes_read > _VERIFY_MAX_FILE_BYTES:
                    return None, "scope_file_too_large"
                digest.update(chunk)
            opened_after = os.fstat(fd)
            try:
                path_after = os.lstat(candidate)
            except OSError:
                return None, "scope_file_changed_during_hash"
            if (
                bytes_read != opened_before.st_size
                or not _same_file_state(opened_before, opened_after)
                or not _same_file_state(path_before, path_after)
            ):
                return None, "scope_file_changed_during_hash"
        finally:
            os.close(fd)
        total_bytes += bytes_read
        if total_bytes > _VERIFY_MAX_TOTAL_BYTES:
            return None, "verification_scope_too_large"
        manifest.append([relative_path, "sha256:" + digest.hexdigest()])
    payload = json.dumps(manifest, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest(), None


def _scope_status_sha256(root, paths):
    head, head_state = _git_current_head(root)
    if head_state == "unavailable":
        return None, "git_head_unavailable"
    try:
        proc = subprocess.run(
            ["git", "status", "--porcelain=v1", "-z", "--untracked-files=all", "--", *paths],
            cwd=root, capture_output=True, timeout=_GITDIFF_TIMEOUT_S,
        )
    except Exception:
        return None, "git_status_unavailable"
    if proc.returncode != 0:
        return None, "git_status_unavailable"
    digest = hashlib.sha256()
    digest.update(head_state.encode("ascii"))
    digest.update(bytes((0,)))
    digest.update(head.encode("ascii"))
    digest.update(bytes((0,)))
    digest.update(proc.stdout)
    return digest.hexdigest(), None


def _verification_snapshot(root, active):
    paths, error = _verification_scope_paths(root, active)
    if error:
        return None, error
    scope_sha = _scope_sha256(paths)
    content_sha, error = _verification_content_sha256(root, paths)
    if error:
        return None, error
    status_sha, error = _scope_status_sha256(root, paths)
    if error:
        return None, error
    return {
        "paths": paths,
        "scope_sha256": scope_sha,
        "content_sha256": content_sha,
        "status_sha256": status_sha,
        "target_file_count": len(paths),
    }, None


def _same_verification_snapshot(left, right):
    return bool(left) and bool(right) and all(
        left.get(key) == right.get(key)
        for key in ("paths", "scope_sha256", "content_sha256", "status_sha256", "target_file_count")
    )


def _maybe_refresh_whole_repo_report():
    """Loop B: keep .roam/verify-report.json fresh so `compile`'s known_findings
    probe can embed the repo's OPEN findings for the edited file.

    DETACHED + THROTTLED + SINGLE-FLIGHT + fail-open, by hard requirement:
      - detached: a whole-repo `verify --report` is ~minutes; running it inline
        would blow the Stop-hook budget (it is the exact stall the diff-only
        scope avoids). We fire-and-forget and never read its result here.
      - never --diff-only: that persists a diff-SCOPED view into the whole-repo
        report path, poisoning known_findings. This is a SEPARATE whole-repo run.
      - throttled: skip if the report is younger than _REPORT_REFRESH_HOURS.
      - single-flight: a claim marker bounds spawning to one verify per
        _REFRESH_CLAIM_MINUTES per repo. The report mtime alone cannot close
        the in-flight window (it only moves AFTER the ~minutes-long verify
        persists), and it never moves at all when the persist cannot land
        (empty targets, missing DB, crash) — without the claim every edit-stop
        would respawn a whole-repo verify, each re-running ensure_index().
    Opt-in (ROAM_HOOK_REPORT_REFRESH, default OFF): it spawns a background
    whole-repo verify, so enabling it is the user's call; enabling closes Loop B.
    """
    if not _env_on("ROAM_HOOK_REPORT_REFRESH", "0"):
        return
    try:
        root = _hook_repo_root()
        report = os.path.join(root, ".roam", "verify-report.json")
        try:
            if (time.time() - os.path.getmtime(report)) / 3600.0 < _REPORT_REFRESH_HOURS:
                return  # fresh enough — don't respawn every edit-stop
        except OSError:
            pass  # missing/unreadable -> refresh
        claim = os.path.join(root, ".roam", "verify-refresh-claim")
        try:
            if (time.time() - os.path.getmtime(claim)) / 60.0 < _REFRESH_CLAIM_MINUTES:
                return  # a refresh is (or was recently) in flight
        except OSError:
            pass  # no claim yet
        os.makedirs(os.path.join(root, ".roam"), exist_ok=True)
        with open(claim, "w") as fh:
            fh.write("pid=%d time=%d" % (os.getpid(), int(time.time())))  # observability crumb
        kwargs = dict(stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if os.name == "nt":
            kwargs["creationflags"] = 0x00000008 | 0x00000200  # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
        else:
            kwargs["start_new_session"] = True
        # whole-repo (NO --diff-only) --report --persist -> <root>/.roam/verify-report.json
        # cwd=root pins the spawned verify's project-root resolution to the SAME
        # directory the throttle checked.
        subprocess.Popen(
            ["roam", "--override-mode", "verify", "--auto", "--report", "--persist"],
            cwd=root,
            **kwargs,
        )
    except Exception:
        return  # a refresh we could not spawn is never the user's problem


def _run_roam(args, timeout, env=None):
    """Run `roam --json <args>`; return the parsed envelope or None."""
    try:
        # Verify/index are hook-owned maintenance operations. Put the global
        # override before the subcommand so they remain callable under a
        # user's restrictive mode without weakening any other hook query.
        maintenance_override = ["--override-mode"] if args and args[0] in {"verify", "index"} else []
        proc = subprocess.run(
            ["roam", *maintenance_override, "--json", *args],
            capture_output=True, text=True, timeout=timeout,
            env=env,
        )
        if not proc.stdout.strip():
            return None
        envelope = json.loads(proc.stdout)
        if not isinstance(envelope, dict):
            return None
        # Bind structured evidence to the process outcome. Without this, a
        # stale or malformed wrapper could print PASS while exiting with the
        # gate-failure code (or print FAIL while exiting zero), and the Stop
        # hook would trust only whichever half looked convenient.
        envelope["_hook_process_returncode"] = proc.returncode
        return envelope
    except Exception:
        return None


def _verify_protocol_state(envelope, expected_receipt):
    """Classify required Verify evidence as one closed protocol transaction."""
    if not isinstance(envelope, dict) or envelope.get("command") != "verify":
        return "unavailable"
    summary = envelope.get("summary")
    if not isinstance(summary, dict):
        return "unavailable"
    verdict = str(summary.get("verdict") or "").strip().upper().split(":", 1)[0].split(None, 1)[0]
    issue_count = summary.get("violation_count")
    files_checked = summary.get("files_checked")
    violations = envelope.get("violations")
    returncode = envelope.get("_hook_process_returncode")
    receipt = summary.get("verification_receipt")
    if verdict not in {"PASS", "WARN", "FAIL"}:
        return "unavailable"
    if type(issue_count) is not int or issue_count < 0:
        return "unavailable"
    if type(files_checked) is not int or files_checked < 0:
        return "unavailable"
    if not isinstance(violations, list) or len(violations) != issue_count:
        return "unavailable"
    if any(not isinstance(item, dict) for item in violations):
        return "unavailable"
    category_findings = []
    categories = envelope.get("categories")
    if categories is not None:
        if not isinstance(categories, dict):
            return "unavailable"
        for result in categories.values():
            if not isinstance(result, dict):
                return "unavailable"
            nested = result.get("violations", [])
            if not isinstance(nested, list) or any(not isinstance(item, dict) for item in nested):
                return "unavailable"
            category_findings.extend(nested)
    evidence_findings = violations + category_findings
    if not isinstance(returncode, int):
        return "unavailable"
    if summary.get("verification_complete") is not True or summary.get("partial_success") is not False:
        return "incomplete"
    if not isinstance(receipt, dict) or not isinstance(expected_receipt, dict):
        return "unavailable"
    if (
        receipt.get("schema") != "roam.verify.receipt.v3"
        or receipt.get("request_nonce") != expected_receipt.get("request_nonce")
        or receipt.get("scope_sha256") != expected_receipt.get("scope_sha256")
        or receipt.get("content_sha256") != expected_receipt.get("content_sha256")
        or receipt.get("content_sha256_before") != expected_receipt.get("content_sha256")
        or receipt.get("content_sha256_after") != expected_receipt.get("content_sha256")
        or receipt.get("target_file_count") != expected_receipt.get("target_file_count")
        or receipt.get("scope_stable") is not True
        or receipt.get("request_match") is not True
        or files_checked != expected_receipt.get("target_file_count")
    ):
        return "unavailable"
    has_fail_finding = any(
        str(item.get("severity") or "").upper() == "FAIL"
        for item in evidence_findings
    )
    if verdict == "PASS":
        if returncode != 0 or has_fail_finding:
            return "unavailable"
        # Verify intentionally keeps advisory detector findings outside its
        # gate verdict. A complete rc0 PASS may therefore carry WARN rows, but
        # only from the closed advisory category set; any other contradiction
        # is malformed evidence and can never be treated as a pass.
        if any(
            str(item.get("severity") or "").upper() != "WARN"
            or str(item.get("category") or "") not in _ADVISORY_CATEGORIES
            for item in evidence_findings
        ):
            return "unavailable"
        return "passed"
    if verdict == "WARN":
        if returncode != 0 or has_fail_finding:
            return "unavailable"
        return "failed"
    if returncode != 5:
        return "unavailable"
    return "failed"


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
    """Working-tree diff vs HEAD for the optional critique reviewer only."""
    try:
        proc = subprocess.run(
            ["git", "diff", "HEAD"], capture_output=True, text=True,
            timeout=_GITDIFF_TIMEOUT_S,
        )
        return proc.stdout if proc.returncode == 0 else ""
    except Exception:
        return ""


def _log_stop_event(
    blocked,
    findings_n,
    advisory_n,
    verify_ms,
    skipped_no_edit,
    *,
    episode_event=None,
):
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
            "episode_id": str((episode_event or {}).get("episode_id") or ""),
            "session_id": str((episode_event or {}).get("session_id") or ""),
            "turn_seq": (episode_event or {}).get("turn_seq"),
            "outcome": str((episode_event or {}).get("outcome") or ""),
            "terminal": bool((episode_event or {}).get("terminal")),
            "changed_files": (episode_event or {}).get("changed_files"),
            "diff_sha256": str((episode_event or {}).get("diff_sha256") or ""),
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
        active = _active_episode(payload)
        root = _hook_repo_root()
        security_issue = None
        policy_before, policy_before_complete = _policy_snapshot(root)
        if active.get("evidence_required"):
            if active.get("evidence_error"):
                security_issue = "policy_evidence_unavailable"
            elif (
                active.get("policy_complete") is not True
                or not isinstance(active.get("policy_sha256"), str)
                or len(active.get("policy_sha256")) != 64
                or not policy_before_complete
            ):
                security_issue = "policy_evidence_unavailable"
            elif active.get("policy_sha256") != policy_before:
                security_issue = "policy_tampering"
        elif not policy_before_complete:
            # Standalone/manual Stop invocations have no prompt episode to
            # compare against, but still require a complete pre-Verify policy
            # snapshot and protect it across the verification transaction.
            security_issue = "policy_evidence_unavailable"

        snapshot = None
        snapshot_error = None
        if security_issue is None:
            snapshot, snapshot_error = _verification_snapshot(root, active)
            if snapshot_error:
                security_issue = "verification_snapshot_unavailable"
        skipped_no_edit = bool(snapshot is not None and not snapshot["paths"] and security_issue is None)
        diff_identity = {
            "changed_files": snapshot.get("target_file_count") if snapshot else None,
            "diff_sha256": snapshot.get("content_sha256") if snapshot else "",
        }
        verify_ms = 0
        d = None
        expected_receipt = None
        if not skipped_no_edit and security_issue is None:
            nonce = secrets.token_hex(16)
            expected_receipt = {
                "schema": "roam.verify.receipt.v3",
                "request_nonce": nonce,
                "scope_sha256": snapshot["scope_sha256"],
                "content_sha256": snapshot["content_sha256"],
                "content_sha256_before": snapshot["content_sha256"],
                "content_sha256_after": snapshot["content_sha256"],
                "target_file_count": snapshot["target_file_count"],
                "scope_stable": True,
                "request_match": True,
            }
            verify_env = {**os.environ, **_ADVISORY_ENV}
            verify_env.update({
                "ROAM_VERIFY_REQUEST_NONCE": nonce,
                "ROAM_VERIFY_SCOPE_SHA256": snapshot["scope_sha256"],
                "ROAM_VERIFY_CONTENT_SHA256": snapshot["content_sha256"],
                "ROAM_VERIFY_SCOPE_COUNT": str(snapshot["target_file_count"]),
            })
            verify_t0 = time.perf_counter()
            d = _run_roam(
                ["verify", "--auto", "--diff-only", "--", *snapshot["paths"]],
                _VERIFY_TIMEOUT_S,
                env=verify_env,
            )
            verify_ms = int((time.perf_counter() - verify_t0) * 1000)
            post_snapshot, post_error = _verification_snapshot(root, active)
            policy_after, policy_after_complete = _policy_snapshot(root)
            if not policy_after_complete or policy_after != policy_before:
                security_issue = "policy_tampering"
            elif post_error or not _same_verification_snapshot(snapshot, post_snapshot):
                security_issue = "verification_race"
        summary = (d or {}).get("summary") or {}
        verdict = str(summary.get("verdict") or "")
        verify_state = (
            "skipped"
            if skipped_no_edit
            else _verify_protocol_state(d, expected_receipt)
            if security_issue is None
            else "unavailable"
        )
        verify_failed = verify_state == "failed"
        verify_unavailable = security_issue is not None or verify_state in {"unavailable", "incomplete"}
        findings = _collect_findings(d) if d else []
        # BUG#62: advisory detectors (default-ON here) emit WARN-only
        # findings that never enter the verdict. Route them out of the
        # blocking set and surface them as a NON-BLOCKING transcript notice
        # (stderr) even on PASS -- never a decision:block.
        advisory = [
            v for v in findings
            if v.get("category") in _ADVISORY_CATEGORIES
            and str(v.get("severity") or "").upper() == "WARN"
        ]
        advisory_ids = {id(v) for v in advisory}
        findings = [v for v in findings if id(v) not in advisory_ids]
        if advisory:
            notice = [f"roam verify advisory (non-blocking, episode scope): {len(advisory)} finding(s)"]
            for v in advisory[:_MAX_OTHER_SHOWN]:
                notice.extend(_fmt(v))
            if len(advisory) > _MAX_OTHER_SHOWN:
                notice.append(f"  ... and {len(advisory) - _MAX_OTHER_SHOWN} more")
            notice.append("  (advisory only -- review now; change suppressions only in a separate approved episode)")
            print(chr(10).join(notice), file=sys.stderr)

        # Optional reviewers run inside the same transaction. A final exact
        # snapshot below catches any reviewer or concurrent agent mutation.
        extra = _second_opinion_lines() if security_issue is None else []
        if security_issue is None:
            final_snapshot, final_error = _verification_snapshot(root, active)
            policy_final, policy_final_complete = _policy_snapshot(root)
            if not policy_final_complete or policy_final != policy_before:
                security_issue = "policy_tampering"
            elif final_error or not _same_verification_snapshot(snapshot, final_snapshot):
                security_issue = "verification_race"
            if security_issue is not None:
                verify_unavailable = True

        if not skipped_no_edit and security_issue is None:
            # Run only after the exact verification transaction has closed.
            # The refresh writes internal `.roam/` state, excluded from source
            # scope, and can no longer race the evidence used for this stop.
            _maybe_refresh_whole_repo_report()

        lines = []
        if security_issue == "policy_tampering":
            lines.extend([
                "BLOCKING [policy_tampering]: Verify policy, suppression, baseline, hook, or Claude settings changed during this episode.",
                "Restore the prompt-start policy state. Make policy changes only in a separate user-approved episode; they cannot satisfy this stop.",
            ])
        elif security_issue == "policy_evidence_unavailable":
            lines.extend([
                "BLOCKING [policy_evidence_unavailable]: the protected prompt-start policy snapshot is missing or incomplete.",
                "Start a fresh Claude turn with the current roam hooks installed, then rerun Verify before stopping.",
            ])
        elif security_issue == "verification_race":
            lines.extend([
                "BLOCKING [verification_race]: filenames, bytes, Git status, or HEAD changed while post-edit evidence was being produced.",
                "Stop concurrent edits and rerun the automatically bound Verify transaction on the final bytes.",
            ])
        elif security_issue == "verification_snapshot_unavailable":
            lines.extend([
                "BLOCKING [verification_snapshot_unavailable]: the exact episode filename/content snapshot could not be proven "
                f"({snapshot_error or 'unknown_snapshot_error'}).",
                "Restore readable regular files and Git status, then rerun Verify before stopping.",
            ])
        elif verify_unavailable:
            lines.extend([
                "roam verify could not produce complete post-edit evidence (receipt v3 required).",
                "BLOCKING: keep this edit open; run `roam verify --auto --diff-only` "
                "successfully before stopping. Do not bypass the gate with policy, "
                "baseline, or suppression edits.",
            ])
        elif verify_failed and findings:
            lines.append(f"roam verify (post-edit, prompt-base episode scope): {verdict}")

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
            # every continuation re-runs Verify, while Claude Code's own
            # consecutive-block cap provides the loop bound.
            lines.append(
                "AUTO-FIX: resolve these now. EDIT the file(s) to fix each "
                "finding on a line your change touched. Do not edit Verify "
                "configuration, baselines, or suppressions during automatic "
                "correction; report a genuine false positive to the user. Only "
                "clearly pre-existing, unrelated findings may be left. Verify "
                "re-runs automatically after your fix."
            )
        elif verify_failed:
            lines.extend([
                f"roam verify failed without actionable findings: {verdict}",
                "BLOCKING: run `roam verify --auto --diff-only --verbose`, repair "
                "the gate failure, and rerun Verify before stopping.",
            ])

        # Opt-in second-opinion reviewers are advisory, but their execution was
        # included inside the final byte/status comparison above.
        if extra:
            if lines:
                lines.append("")
            lines.append(
                "SECOND OPINION (opt-in roam reviewers -- advisory; address or "
                "justify, they do not auto-block):"
            )
            lines.extend(extra)

        blocked = bool(lines)
        if security_issue is not None:
            outcome = security_issue
        elif skipped_no_edit:
            outcome = "no_edit"
        elif blocked and verify_unavailable:
            outcome = "verify_unavailable"
        elif blocked and verify_failed and findings:
            outcome = "verification_blocked"
        elif blocked and verify_failed:
            outcome = "verification_failed_without_findings"
        elif blocked:
            outcome = "second_opinion_blocked"
        elif verify_failed:
            outcome = "verification_failed_without_findings"
        else:
            outcome = "verified_clean"
        episode_event = _record_episode_stop(
            payload,
            active,
            outcome,
            not blocked,
            blocked,
            verify_ms,
            skipped_no_edit,
            diff_identity,
        )
        _log_stop_event(
            blocked,
            len(findings),
            len(advisory),
            verify_ms,
            skipped_no_edit,
            episode_event=episode_event,
        )
        if not lines:
            return  # quiet: clean verify and nothing from any enabled reviewer
        print(json.dumps({"decision": "block", "reason": chr(10).join(lines)}))
    except Exception:
        print(json.dumps({
            "decision": "block",
            "reason": (
                "roam verify Stop hook could not validate this stop. "
                "Run `roam verify --auto --diff-only` successfully before stopping."
            ),
        }))


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
# the shipping commit stamped, raw literal before that), including historical
# integration variants from before Roam owned maintenance-mode overrides.
# A deployed body whose hash is here is KNOWN to be roam's own output — safe to
# heal without a marker. A stamped body whose hash is NOT here has been edited
# by hand (or by an unknown external transform): report, never auto-overwrite.
# Regenerate with scripts/seed_hook_body_shas.py after ANY body change — an
# empty or stale set silently disables healing for pre-stamp installs (the
# defect that shipped in #77).
_KNOWN_HOOK_BODY_SHAS: frozenset[str] = frozenset(
    {
        "6ab4f91dc4c407c2fa82cb4e51bb5fd5268563150598650e4928618834a59a04",  # ups v11 pristine (canonical override ownership)
        "391fd8b9044037b20000fc301415137647b51f7bf60bfcf35a84a05ec94a3bb8",  # stop v11 pristine (canonical override ownership)
        "62e71df1b62b44860b5eccb181022bb5d1dfa1a751d5c0593c787c99d13e59ed",  # ups v10 pristine (2026-07-17 receipt-v3 identity binding)
        "731d9593da3bf3fefb513e5dd89d44337bc474305da705f74462c00c630968d8",  # stop v10 pristine (2026-07-17 receipt-v3 identity binding)
        "170b382530bb56b1301f069981a6404d8bf4441ae2a29e7c8b5716037e04ceeb",  # stop v10 surgered (2026-07-17 receipt-v3 identity binding)
        "73cecf9fb98139944257c5193a10f53e10bc14dc819ac93dd6d9e0793d0e0510",  # ups v9 pristine (2026-07-17 receipt-v2 episode binding)
        "335f74c850f8ce0651268571d17152308ecdd4c651ac561c9b8981230bc47848",  # stop v9 pristine (2026-07-17 receipt-v2 episode binding)
        "3a74a4e09cef342e6c9490292c0b0adab626180592ccf3bc06f68fba03c4604b",  # stop v9 surgered (2026-07-17 receipt-v2 episode binding)
        "187c9b317434ce85f2593b4d1c6043a083432d1430f355f5daa9001e6ab1de03",  # ups v8 pristine (2026-07-17 strict continuation verify)
        "515d432ae0171628ff6d9a46589f733f4395d86e0aacb6dd291d6c2d78ca3f49",  # stop v8 pristine (2026-07-17 strict continuation verify)
        "6e7bd67b651c0ae2dac0bf3739d7b1a0525e3c069467bcc57ab14befbbfb825e",  # stop v8 surgered (2026-07-17 strict continuation verify)
        "bafb2a1af1735f754645a80caa967c5b3c1c0692c78a79b0d09d44fbd3dd71aa",  # ups v7 pristine (2026-07-17 fail-closed verify)
        "c9bd8df743d83ae21a21b5f2825e6df3dceefe4b723cc088877437f3dcb4e29c",  # stop v7 pristine (2026-07-17 fail-closed verify)
        "07915b8913ee53f387ef0d74a57306191f7a66358cfd11bf702ee71a8ffa00c8",  # stop v7 surgered (2026-07-17 fail-closed verify)
        "d3ed7a41d1836445eed526d4ae7929af3e90b288b89db1d8f9796fa4b2fad3fb",  # ups v6 pristine (2026-07-16 evidence states)
        "a8a68ecec99484aafa0049b6b16c5ff35cb813fbace746395f01e47907a5884a",  # stop v6 pristine (2026-07-16 evidence states)
        "35a7e01d539c4febd3ee903ba2efc034fdca3e7173db823cd9960efdcd4c5a63",  # stop v6 surgered (2026-07-16 evidence states)
        "2957cd0432e95cfd8b1117972863b3b54ef5ae3afea5dd5851a7d8aa66adcc81",  # ups v5 pristine (2026-07-16 episodes)
        "d72e7f56aebe28579348a0ff4b9205e91fa651795e2e6e019d9f35ad35e8d931",  # stop v5 pristine (2026-07-16 episodes)
        "c9f030f130dae5dd1b76e79850479b59898375d3868ba97da13201e2633450b5",  # stop v5 surgered (2026-07-16 episodes)
        "25492394429ce7416b7ba3f80b0f2c38accb79136f04a47e28fb51d828a0cc08",  # ups v4 pristine (2026-07-16 s2lite)
        "9d6d69d97cc29639f27b3c45b8a02d4488712bf8fdece9a49cf2d250a219a378",  # stop v4 pristine (2026-07-16 s2lite)
        "3a9db464c1480afabfc3cf20f474c75184c5088083085d763592804e7ea6422e",  # stop v4 surgered (2026-07-16 s2lite)
        "b28bcb7a414f92e1694ecbeb54ff1d5e69b8a4c46d4ee035e6b88975712e0805",  # stop v3 pristine (2026-07-16 loop-b)
        "fa76db1e06bb44a947084ed10f94b553aa68289d60511cb246b67f5f85acfd44",  # stop v3 surgered (2026-07-16 loop-b)
        "18e19f503c957e09850ec4173fc451b078c7a0356eb6c964d6406b9e5a8300a5",  # ups v3 pristine (2026-07-16 loop-b)
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

    - "current"    : byte-identical, or a KNOWN same-version roam-shipped
                     legacy integration variant.
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
        # Same-version known variant = a legacy integration body Roam shipped:
        # leave it alone. Anything older that Roam shipped is stale.
        if dver is not None and cver is not None and dver >= cver:
            return "current"
        return "heal"
    return "modified" if dver is not None else "foreign"


_MAX_CLAUDE_SETTINGS_BYTES = 1024 * 1024
_MAX_CLAUDE_HOOK_BYTES = 2 * 1024 * 1024
_CAPTURE_CURRENT_CONTENT = object()


class _UnsafeClaudePathError(OSError):
    """A Claude control-plane path is redirected or not privately owned."""


class _HookBodyStates(dict[str, str]):
    """Mapping-compatible scan result carrying exact pre-write-check bytes."""

    def __init__(self) -> None:
        super().__init__()
        self.contents: dict[str, bytes | None] = {}


def _is_claude_reparse_point(path: Path) -> bool:
    if path.is_symlink():
        return True
    is_junction = getattr(path, "is_junction", None)
    return bool(is_junction and is_junction())


def _validate_concrete_directory(path: Path, *, allow_missing: bool) -> os.stat_result | None:
    """Require a concrete directory leaf without following links/junctions."""
    try:
        info = path.lstat()
    except FileNotFoundError:
        if allow_missing:
            return None
        raise _UnsafeClaudePathError(f"required directory is missing: {path}") from None
    if _is_claude_reparse_point(path):
        raise _UnsafeClaudePathError(f"links and junctions are not accepted: {path}")
    if not stat.S_ISDIR(info.st_mode):
        raise _UnsafeClaudePathError(f"expected a directory: {path}")
    expected = path.parent.resolve(strict=True) / path.name
    if path.resolve(strict=True) != expected:
        raise _UnsafeClaudePathError(f"directory escaped its lexical parent: {path}")
    return info


def _validate_existing_claude_file(
    path: Path,
    *,
    label: str,
    max_bytes: int,
) -> os.stat_result | None:
    """Reject links, non-files, hard links, and oversized control files."""
    try:
        info = path.lstat()
    except FileNotFoundError:
        return None
    if _is_claude_reparse_point(path):
        raise _UnsafeClaudePathError(f"unsafe {label}: links are not accepted ({path})")
    if not stat.S_ISREG(info.st_mode):
        raise _UnsafeClaudePathError(f"unsafe {label}: expected a regular file ({path})")
    if info.st_nlink != 1:
        raise _UnsafeClaudePathError(f"unsafe {label}: hard-linked files are not accepted ({path})")
    if info.st_size > max_bytes:
        raise ValueError(f"{label} exceeds the {max_bytes}-byte limit: {path}")
    return info


def _same_file_snapshot(before: os.stat_result, after: os.stat_result) -> bool:
    """Compare identity and mutation fields across a bounded read."""
    before_inode = (before.st_dev, before.st_ino)
    after_inode = (after.st_dev, after.st_ino)
    identity_matches = before_inode == after_inode or not all(before_inode + after_inode)
    return bool(
        identity_matches
        and before.st_size == after.st_size
        and before.st_mtime_ns == after.st_mtime_ns
        and stat.S_ISREG(after.st_mode)
        and after.st_nlink == 1
    )


def _read_claude_file(path: Path, *, label: str, max_bytes: int) -> bytes | None:
    """Read one stable, bounded regular file through a no-follow descriptor."""
    before = _validate_existing_claude_file(path, label=label, max_bytes=max_bytes)
    if before is None:
        return None
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(str(path), flags)
    try:
        opened = os.fstat(fd)
        if not _same_file_snapshot(before, opened):
            raise _UnsafeClaudePathError(f"{label} changed before it could be read safely: {path}")
        chunks: list[bytes] = []
        total = 0
        while True:
            chunk = os.read(fd, min(64 * 1024, max_bytes + 1 - total))
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)
            if total > max_bytes:
                raise ValueError(f"{label} exceeds the {max_bytes}-byte limit: {path}")
        after = os.fstat(fd)
        if not _same_file_snapshot(opened, after):
            raise _UnsafeClaudePathError(f"{label} changed while it was being read: {path}")
        return b"".join(chunks)
    finally:
        os.close(fd)


def _validate_claude_layout(settings_path: Path, hook_dir: Path, *, create: bool) -> None:
    """Validate and optionally create the concrete settings/hooks directory tree."""
    settings_parent = Path(os.path.abspath(settings_path.parent))
    hooks_path = Path(os.path.abspath(hook_dir))
    try:
        hook_relative = hooks_path.relative_to(settings_parent)
    except ValueError as exc:
        raise _UnsafeClaudePathError("Claude hook directory escaped the settings directory") from exc

    parent_info = _validate_concrete_directory(settings_parent, allow_missing=True)
    if parent_info is None and create:
        settings_parent.mkdir(mode=0o700)
        _validate_concrete_directory(settings_parent, allow_missing=False)

    current = settings_parent
    for component in hook_relative.parts:
        current = current / component
        info = _validate_concrete_directory(current, allow_missing=True)
        if info is None and create:
            current.mkdir(mode=0o700)
            _validate_concrete_directory(current, allow_missing=False)

    _validate_existing_claude_file(
        settings_path,
        label="Claude settings",
        max_bytes=_MAX_CLAUDE_SETTINGS_BYTES,
    )


def _capture_claude_write_state(
    path: Path,
    *,
    label: str,
    max_bytes: int,
    expected_content: bytes | None | object,
) -> bytes | None:
    """Validate the parent and resolve the expected pre-write-check bytes."""
    _validate_concrete_directory(path.parent, allow_missing=False)
    initial_content = _read_claude_file(path, label=label, max_bytes=max_bytes)
    if expected_content is _CAPTURE_CURRENT_CONTENT:
        return initial_content
    if initial_content != expected_content:
        raise _UnsafeClaudePathError(f"{label} changed since it was loaded: {path}")
    return expected_content


def _atomic_write_claude_file(
    path: Path,
    payload: bytes,
    *,
    label: str,
    max_bytes: int,
    mode: int,
    expected_content: bytes | None | object = _CAPTURE_CURRENT_CONTENT,
) -> None:
    """Atomically update one validated Claude control file after a final content check."""
    if len(payload) > max_bytes:
        raise ValueError(f"{label} exceeds the {max_bytes}-byte limit: {path}")
    expected_content = _capture_claude_write_state(
        path,
        label=label,
        max_bytes=max_bytes,
        expected_content=expected_content,
    )

    def prepare_temp(fd: int, tmp_name: str) -> None:
        if hasattr(os, "fchmod"):
            os.fchmod(fd, mode)
        else:  # pragma: no cover - Windows lacks fchmod on some Python builds
            os.chmod(tmp_name, mode)

    def check_unchanged() -> None:
        _validate_concrete_directory(path.parent, allow_missing=False)
        current_content = _read_claude_file(path, label=label, max_bytes=max_bytes)
        if current_content != expected_content:
            raise _UnsafeClaudePathError(f"{label} changed during the update: {path}")

    atomic_write_bytes(
        path,
        payload,
        prepare_temp_fd=prepare_temp,
        before_replace=check_unchanged,
        durable=True,
        create_parents=False,
    )
    _validate_existing_claude_file(path, label=label, max_bytes=max_bytes)


def _backup_existing_claude_file(
    source: Path,
    backup: Path,
    *,
    label: str,
    max_bytes: int,
) -> None:
    payload = _read_claude_file(source, label=label, max_bytes=max_bytes)
    if payload is None:
        return
    _atomic_write_claude_file(
        backup,
        payload,
        label=f"{label} backup",
        max_bytes=max_bytes,
        mode=0o600,
    )


def _unlink_claude_file(path: Path, *, label: str, max_bytes: int) -> None:
    if _validate_existing_claude_file(path, label=label, max_bytes=max_bytes) is not None:
        path.unlink()


def _strict_json_object_pairs(pairs):
    out = {}
    for key, value in pairs:
        if key in out:
            raise ValueError(f"duplicate JSON key: {key}")
        out[key] = value
    return out


def _validate_claude_settings_shape(settings: object) -> dict:
    if not isinstance(settings, dict):
        raise ValueError("Claude settings root must be a JSON object")
    hooks_block = settings.get("hooks")
    if hooks_block is None:
        return settings
    if not isinstance(hooks_block, dict):
        raise ValueError("Claude settings hooks must be an object")
    for event, rules in hooks_block.items():
        if not isinstance(rules, list):
            raise ValueError(f"Claude settings hooks.{event} must be an array")
        for rule in rules:
            if not isinstance(rule, dict):
                raise ValueError(f"Claude settings hooks.{event} contains a non-object rule")
            entries = rule.get("hooks", [])
            if not isinstance(entries, list):
                raise ValueError(f"Claude settings hooks.{event}.hooks must be an array")
            for entry in entries:
                if not isinstance(entry, dict):
                    raise ValueError(f"Claude settings hooks.{event}.hooks contains a non-object entry")
                command = entry.get("command")
                if command is not None and not isinstance(command, str):
                    raise ValueError(f"Claude settings hooks.{event} command must be a string")
    return settings


def _write_claude_settings(
    settings_path: Path,
    settings: dict,
    *,
    expected_content: bytes | None,
) -> None:
    payload = (json.dumps(settings, indent=2, ensure_ascii=False) + "\n").encode("utf-8")
    _atomic_write_claude_file(
        settings_path,
        payload,
        label="Claude settings",
        max_bytes=_MAX_CLAUDE_SETTINGS_BYTES,
        mode=0o600,
        expected_content=expected_content,
    )


def _assert_claude_settings_unchanged(settings_path: Path, expected_content: bytes | None) -> None:
    current = _read_claude_file(
        settings_path,
        label="Claude settings",
        max_bytes=_MAX_CLAUDE_SETTINGS_BYTES,
    )
    if current != expected_content:
        raise _UnsafeClaudePathError(f"Claude settings changed since they were loaded: {settings_path}")


def _claude_hook_command(hook_path: Path) -> str:
    argv = [sys.executable, str(hook_path)]
    if os.name == "nt":
        return subprocess.list2cmdline(argv)
    return " ".join(shlex.quote(part) for part in argv)


def _scan_hook_bodies(hook_dir: Path, managed: list) -> dict[str, str]:
    """{filename: heal_state} for each managed hook.

    Adds two states beyond :func:`_hook_heal_state`:
    - "missing"    : file absent (reinstall when the settings entry exists).
    - "unreadable" : not valid UTF-8 (e.g. saved as UTF-16 by PowerShell) —
                     report; replaceable only with --force. Never a traceback:
                     this scan runs inside `compile claude`'s wire path too.
    - "unsafe"     : link, junction, hard link, or redirected control tree;
                     never replaceable with --force.
    """
    out = _HookBodyStates()
    try:
        _validate_concrete_directory(hook_dir.parent, allow_missing=True)
        _validate_concrete_directory(hook_dir, allow_missing=True)
    except _UnsafeClaudePathError:
        for _event, filename, _canonical in managed:
            out[filename] = "unsafe"
        return out
    for _event, filename, canonical in managed:
        p = hook_dir / filename
        try:
            raw = _read_claude_file(p, label="Claude hook body", max_bytes=_MAX_CLAUDE_HOOK_BYTES)
            out.contents[filename] = raw
            out[filename] = _classify_hook_content(raw, canonical)
        except _UnsafeClaudePathError:
            out[filename] = "unsafe"
            continue
        except (OSError, ValueError):
            out[filename] = "unreadable"
            continue
    return out


def _classify_hook_content(raw: bytes | None, canonical: str) -> str:
    if raw is None:
        return "missing"
    try:
        # Match Path.read_text(newline=None)'s historical universal-newline
        # semantics so registered LF bodies remain recognizable when an
        # editor wrote the same script with CRLF on Windows.
        deployed = raw.decode("utf-8").replace("\r\n", "\n").replace("\r", "\n")
    except UnicodeError:
        return "unreadable"
    return _hook_heal_state(deployed, canonical)


def _write_hook_body(
    hook_path: Path,
    script: str,
    *,
    expected_state: str | None = None,
    expected_content: bytes | None | object = _CAPTURE_CURRENT_CONTENT,
) -> None:
    """Overwrite a hook body atomically, preserving the prior content as .bak.

    Atomic (temp + os.replace) so a crash mid-write can never leave a
    truncated body behind a valid version stamp; .bak so heal/--force is
    recoverable (settings.json already gets the same courtesy).
    """
    payload = script.encode("utf-8")
    old = _read_claude_file(hook_path, label="Claude hook body", max_bytes=_MAX_CLAUDE_HOOK_BYTES)
    if expected_content is not _CAPTURE_CURRENT_CONTENT and old != expected_content:
        raise _UnsafeClaudePathError(f"Claude hook body changed after inspection: {hook_path}")
    current_state = _classify_hook_content(old, script)
    if expected_state is not None and current_state != expected_state:
        raise _UnsafeClaudePathError(
            f"Claude hook body changed after inspection ({expected_state} -> {current_state}): {hook_path}"
        )
    if old is not None and old != payload:
        _atomic_write_claude_file(
            hook_path.parent / (hook_path.name + ".bak"),
            old,
            label="Claude hook body backup",
            max_bytes=_MAX_CLAUDE_HOOK_BYTES,
            mode=0o600,
        )
    _atomic_write_claude_file(
        hook_path,
        payload,
        label="Claude hook body",
        max_bytes=_MAX_CLAUDE_HOOK_BYTES,
        mode=0o700,
        expected_content=old,
    )


def _expected_hook_content(body_states: dict[str, str] | None, filename: str) -> bytes | None | object:
    contents = getattr(body_states, "contents", None)
    if isinstance(contents, dict) and filename in contents:
        return contents[filename]
    return _CAPTURE_CURRENT_CONTENT


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


def _load_claude_settings(settings_path: Path) -> tuple[dict, str | None, bytes | None]:
    """Parse settings.json and retain the exact bytes for the final pre-write check."""
    try:
        _validate_concrete_directory(settings_path.parent, allow_missing=True)
        raw = _read_claude_file(
            settings_path,
            label="Claude settings",
            max_bytes=_MAX_CLAUDE_SETTINGS_BYTES,
        )
        if raw is None:
            return {}, None, None
        parsed = json.loads(raw.decode("utf-8"), object_pairs_hook=_strict_json_object_pairs)
        return _validate_claude_settings_shape(parsed), None, raw
    except (OSError, ValueError, UnicodeError, RecursionError) as exc:
        return {}, f"Cannot parse {settings_path}: {exc}", None


def _emit_hooks_verdict(json_mode: bool, verdict: str, summary: dict, extra: dict, text_lines: list) -> None:
    """Single output point for the claude subcommand (JSON envelope or text)."""
    if json_mode:
        click.echo(to_json(json_envelope("hooks", summary={"verdict": verdict, **summary}, **extra)))
        return
    click.echo(f"VERDICT: {verdict}")
    for line in text_lines:
        click.echo(line)


def _claude_uninstall_hooks(
    settings: dict,
    settings_path: Path,
    hook_dir: Path,
    write: bool,
    *,
    expected_settings_content: bytes | None,
) -> tuple[str, bool]:
    """Sweep BOTH managed hooks (regardless of --no-verify). (verdict, removed_any)."""
    removed_any = False
    bodies_to_remove: list[Path] = []
    for event, filename in (
        ("UserPromptSubmit", _CLAUDE_UPS_HOOK_FILENAME),
        ("Stop", _CLAUDE_STOP_HOOK_FILENAME),
    ):
        if _remove_hook_entry(settings, event, filename):
            removed_any = True
            bodies_to_remove.append(hook_dir / filename)
    if write and removed_any:
        _validate_claude_layout(settings_path, hook_dir, create=False)
        _assert_claude_settings_unchanged(settings_path, expected_settings_content)
        for body_path in bodies_to_remove:
            _validate_existing_claude_file(
                body_path,
                label="Claude hook body",
                max_bytes=_MAX_CLAUDE_HOOK_BYTES,
            )
        _backup_existing_claude_file(
            settings_path,
            settings_path.with_suffix(".json.bak"),
            label="Claude settings",
            max_bytes=_MAX_CLAUDE_SETTINGS_BYTES,
        )
        # Unwire first. A later unlink failure leaves only an inert body, never
        # a settings entry that points at a missing script.
        _write_claude_settings(
            settings_path,
            settings,
            expected_content=expected_settings_content,
        )
        for body_path in bodies_to_remove:
            _unlink_claude_file(
                body_path,
                label="Claude hook body",
                max_bytes=_MAX_CLAUDE_HOOK_BYTES,
            )
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
    expected_settings_content: bytes | None = None,
) -> tuple[str, list[str]]:
    """Write the hook scripts + merge settings entries (settings.json backed up).

    A hook whose settings entry is missing but whose FILE on disk is a body
    roam cannot recognize (user-modified / foreign / unreadable) gets its entry
    wired while the body is PRESERVED unless --force — a wiped settings.json
    must not become a license to overwrite a customized body. Returns
    (verdict_part, preserved_filenames).
    """
    _validate_claude_layout(settings_path, hook_dir, create=True)
    _assert_claude_settings_unchanged(settings_path, expected_settings_content)
    _backup_existing_claude_file(
        settings_path,
        settings_path.with_suffix(".json.bak"),
        label="Claude settings",
        max_bytes=_MAX_CLAUDE_SETTINGS_BYTES,
    )
    preserved: list[str] = []
    for event, filename, script in to_install:
        hook_path = hook_dir / filename
        state = (body_states or {}).get(filename)
        if state == "unsafe":
            raise _UnsafeClaudePathError(f"unsafe Claude hook body: {hook_path}")
        if state in ("modified", "foreign", "unreadable") and not force:
            preserved.append(filename)
        else:
            _write_hook_body(
                hook_path,
                script,
                expected_state=state,
                expected_content=_expected_hook_content(body_states, filename),
            )
        _merge_hook_entry(settings, event, _claude_hook_command(hook_path))
    _write_claude_settings(
        settings_path,
        settings,
        expected_content=expected_settings_content,
    )
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
    `.roam-leak-patterns.py` catalogue) — and on findings emits an AUTO-FIX
    directive so the agent resolves them before stopping. Verify re-runs after
    each correction and stays quiet only on complete PASS. Compile context
    injection remains fail-open; edited Stop verification fails closed. Claude
    Code bounds consecutive continuations. `--no-verify` installs only the
    compile hook.
    """
    json_mode = ctx.obj.get("json") if ctx.obj else False
    settings_path = _claude_settings_path(user_level)
    hook_dir = _claude_hook_dir(user_level)
    # (event, filename, script) per managed hook.
    managed = [("UserPromptSubmit", _CLAUDE_UPS_HOOK_FILENAME, _CLAUDE_UPS_HOOK_SCRIPT)]
    if not no_verify:
        managed.append(("Stop", _CLAUDE_STOP_HOOK_FILENAME, _CLAUDE_STOP_HOOK_SCRIPT))

    try:
        _validate_claude_layout(settings_path, hook_dir, create=False)
    except (OSError, ValueError) as exc:
        verdict = f"Unsafe Claude hook layout: {exc}"
        _emit_hooks_verdict(
            json_mode,
            verdict,
            {"partial_success": False, "state": "unsafe_path"},
            {"settings_path": str(settings_path), "hook_dir": str(hook_dir)},
            [],
        )
        ctx.exit(1)
        return

    settings, load_error, settings_content = _load_claude_settings(settings_path)
    if load_error:
        _emit_hooks_verdict(json_mode, load_error, {"partial_success": False}, {}, [])
        ctx.exit(1)
        return

    if do_uninstall:
        try:
            verdict, removed_any = _claude_uninstall_hooks(
                settings,
                settings_path,
                hook_dir,
                write,
                expected_settings_content=settings_content,
            )
        except (OSError, ValueError) as exc:
            _emit_hooks_verdict(
                json_mode,
                f"Claude hook uninstall stopped safely: {exc}",
                {"removed": False, "partial_success": False, "state": "write_failed"},
                {"settings_path": str(settings_path), "hook_dir": str(hook_dir)},
                [],
            )
            ctx.exit(1)
            return
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
    unsafe_bodies = sorted(filename for filename, state in body_states.items() if state == "unsafe")
    if unsafe_bodies:
        verdict = f"Unsafe Claude hook body path(s): {', '.join(unsafe_bodies)}"
        _emit_hooks_verdict(
            json_mode,
            verdict,
            {"partial_success": False, "state": "unsafe_path", "unsafe_bodies": unsafe_bodies},
            {"settings_path": str(settings_path), "hook_dir": str(hook_dir)},
            [],
        )
        ctx.exit(1)
        return
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
    attention = {
        f: st
        for f, st in body_states.items()
        if st in ("modified", "foreign", "unreadable", "unsafe") and (not force or st == "unsafe")
    }

    if not to_install and not heal and not forced:
        verdict = f"roam Claude Code hooks wired + current in {settings_path}"
        if attention:
            verdict = (
                f"roam Claude Code hooks wired in {settings_path}; {len(attention)} body(ies) need attention (see NOTE)"
            )
    elif write:
        parts = []
        try:
            _validate_claude_layout(settings_path, hook_dir, create=True)
            if to_install:
                installed_verdict, _preserved = _claude_install_hooks(
                    settings,
                    settings_path,
                    hook_dir,
                    to_install,
                    body_states=body_states,
                    force=force,
                    expected_settings_content=settings_content,
                )
                parts.append(installed_verdict)
            for _e, filename, script in heal + forced:
                _write_hook_body(
                    hook_dir / filename,
                    script,
                    expected_state=body_states.get(filename),
                    expected_content=_expected_hook_content(body_states, filename),
                )
        except (OSError, ValueError) as exc:
            _emit_hooks_verdict(
                json_mode,
                f"Claude hook update stopped safely: {exc}",
                {
                    "partial_success": bool(parts),
                    "state": "write_failed",
                    "body_states": body_states,
                },
                {"settings_path": str(settings_path), "hook_dir": str(hook_dir)},
                [],
            )
            ctx.exit(1)
            return
        if heal:
            parts.append(f"healed {len(heal)} stale hook body(ies): {', '.join(f for _e, f, _s in heal)}")
        if forced:
            parts.append(f"force-refreshed {len(forced)} body(ies): {', '.join(f for _e, f, _s in forced)}")
        verdict = "; ".join(parts)
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
            f"unreadable/unsafe ({detail}); not healed. Use --force only for recognized regular files "
            f"(a .bak is kept); unsafe paths must be replaced manually."
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
