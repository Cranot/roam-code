"""STANDING FIX — default-deny gate against internal-doc leaks.

Two internal criticals reached the public index the week of 2026-07-06 (a
planning doc + an evidence memo committed by accident). ``.gitignore`` shields
KNOWN internal docs, but a NEW tracked file under ``dev/`` or ``internal/`` slips
through silently. This test inverts the default: every git-tracked file under
those directories MUST be explicitly listed in ``dev/PUBLIC_ALLOWLIST.txt``.
A new tracked internal file therefore fails CI until a human vets it.

Regenerate the baseline (after vetting) with::

    git ls-files dev/ internal/ | sort -u  # add any genuinely-public new paths
"""

from __future__ import annotations

import subprocess
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
_ALLOWLIST = _REPO_ROOT / "dev" / "PUBLIC_ALLOWLIST.txt"


def _load_allowlist() -> set[str]:
    entries: set[str] = set()
    for raw in _ALLOWLIST.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        entries.add(line.replace("\\", "/"))
    return entries


def _tracked_internal_files() -> list[str]:
    proc = subprocess.run(
        ["git", "ls-files", "dev/", "internal/"],
        cwd=str(_REPO_ROOT),
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    if proc.returncode != 0:
        return []
    return [line.strip().replace("\\", "/") for line in proc.stdout.splitlines() if line.strip()]


def test_allowlist_file_exists() -> None:
    assert _ALLOWLIST.is_file(), "dev/PUBLIC_ALLOWLIST.txt is missing — the leak gate is disarmed"


def test_every_tracked_internal_file_is_allowlisted() -> None:
    tracked = _tracked_internal_files()
    if not tracked:
        # No git available (e.g. sdist build) — nothing to assert.
        return
    allow = _load_allowlist()
    unlisted = sorted(set(tracked) - allow)
    assert not unlisted, (
        "New tracked file(s) under dev/ or internal/ are NOT in "
        "dev/PUBLIC_ALLOWLIST.txt — vet them for internal-content leaks, then "
        "add them explicitly:\n  " + "\n  ".join(unlisted)
    )


def test_allowlist_has_no_stale_entries() -> None:
    # Keep the allowlist honest: an entry that no longer exists as a tracked
    # file is dead weight that should be pruned (non-fatal signal — skip when
    # git is unavailable).
    tracked = set(_tracked_internal_files())
    if not tracked:
        return
    allow = _load_allowlist()
    stale = sorted(allow - tracked)
    assert not stale, "dev/PUBLIC_ALLOWLIST.txt lists paths no longer tracked:\n  " + "\n  ".join(stale)
