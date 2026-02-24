"""Git hook integration for automatic re-indexing after git operations."""

from __future__ import annotations

import os
import stat
import subprocess
import sys
from pathlib import Path

import click

from roam.output.formatter import to_json, json_envelope

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
                    real_git = Path(content[len("gitdir:"):].strip())
                    if not real_git.is_absolute():
                        real_git = parent / real_git
                    return real_git / "hooks"
            except OSError:
                pass

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


@click.group("hooks")
@click.pass_context
def hooks(ctx):
    """Manage git hook integration for automatic re-indexing."""
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
            click.echo(to_json(json_envelope("hooks",
                summary={"verdict": msg, "installed": [], "skipped": [], "errors": []},
                hooks_dir=None,
            )))
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
        click.echo(to_json(json_envelope("hooks",
            summary={
                "verdict": verdict,
                "installed": installed,
                "skipped": skipped,
                "errors": errors,
            },
            hooks_dir=str(hooks_dir),
            results=results,
        )))
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
            click.echo(to_json(json_envelope("hooks",
                summary={"verdict": msg, "removed": [], "not_installed": [], "errors": []},
                hooks_dir=None,
            )))
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
        click.echo(to_json(json_envelope("hooks",
            summary={
                "verdict": verdict,
                "removed": removed,
                "not_installed": not_installed,
                "errors": errors,
            },
            hooks_dir=str(hooks_dir),
            results=results,
        )))
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
            hook_statuses.append({
                "hook": hook_name,
                "installed": present,
                "file_exists": exists,
                "path": str(hook_path) if exists else None,
            })
    else:
        for hook_name in _HOOK_NAMES:
            hook_statuses.append({
                "hook": hook_name,
                "installed": False,
                "file_exists": False,
                "path": None,
            })

    if hooks_dir is None:
        verdict = "Not in a git repository."
    elif installed_count == len(_HOOK_NAMES):
        verdict = f"All {len(_HOOK_NAMES)} roam hooks installed."
    elif installed_count == 0:
        verdict = "No roam hooks installed. Run `roam hooks install` to set up."
    else:
        verdict = f"{installed_count}/{len(_HOOK_NAMES)} roam hooks installed."

    if json_mode:
        click.echo(to_json(json_envelope("hooks",
            summary={
                "verdict": verdict,
                "installed_count": installed_count,
                "total_hooks": len(_HOOK_NAMES),
                "all_installed": installed_count == len(_HOOK_NAMES),
            },
            hooks_dir=str(hooks_dir) if hooks_dir else None,
            hooks=hook_statuses,
        )))
        return

    click.echo(f"VERDICT: {verdict}")
    if hooks_dir:
        click.echo(f"Hooks directory: {hooks_dir}")
    for entry in hook_statuses:
        state = "installed" if entry["installed"] else ("present" if entry["file_exists"] else "missing")
        click.echo(f"  {entry['hook']:20s} {state}")
