"""A directory the enumerator could not open is a blind spot, not an empty one.

``_git_ls_files`` runs ``git ls-files --cached --others --exclude-standard``,
captures stderr, and gates only on ``returncode != 0``.  ``--others`` walks the
filesystem; on a directory it cannot open it prints

    warning: could not open directory 'X/': Permission denied

on stderr, skips everything underneath, and **exits 0**.  The warning was
discarded, so a short list was indistinguishable from a complete one.

Measured on the tree that shipped, three arms over one untracked file holding a
live-shaped AWS key::

    A  credential in an ordinary directory     rc 5  "1 secrets found"
    B  credential in an unenumerable directory rc 0  "No secrets found (1 files scanned)"
    C  no credential at all                    rc 0  "No secrets found (1 files scanned)"

B and C were byte-identical -- same exit code, same verdict, every disclosure
field false or zero, including the one named ``files_undiscoverable``.  A fires,
so the pattern was never dead; only the file was invisible.

Reached through three independent doors, so this is not an encoding curiosity:
a mode-000 directory on Linux under any non-root user (git 2.43.0 -- root
bypasses the mode and cannot reproduce it), and on Windows a trailing-dot name
or a lone-surrogate name, both legal on NTFS.  Four such directories appeared
unprompted in a sibling checkout on 2026-08-08 between 11:22 and 11:26.

Same defect as W1466 one file over -- see ``test_discovery_size_cap_blind_spot``
and the ``SKIP_OVERSIZED`` comment block -- and it takes the same shape of fix:
the skip stays, it becomes countable.  The difference is where the declining
happens.  W1466 was roam's own filter dropping a path it had already seen; here
the enumerator never handed the path over at all, and said so only on a stream
nobody read.

WHY THIS TEST VERIFIES ITS OWN PREMISE
--------------------------------------
An unenumerable directory cannot be created everywhere: root bypasses mode 000,
and a container that runs tests as root would make every assertion below pass
over a directory git read perfectly well.  That is this very defect wearing a
test's clothing -- an absent measurement reported as a benign default.  So each
test first proves git actually warned and actually omitted the path, and skips
with the reason when it could not set the condition up.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from roam.index.discovery import SKIP_UNENUMERABLE, discover_files, discover_files_with_skips

#: Vendor-shaped but synthetic: the AWS prefix plus 16 uppercase alphanumerics,
#: matching the shipped `AKIA[0-9A-Z]{16}` pattern at RUNTIME while never
#: appearing as one literal in this source.  Same reason
#: `test_discovery_size_cap_blind_spot.py` splits its own fixture: this
#: repository's pre-push secret scan reads the committed bytes, so a test
#: proving the scanner catches a credential must not ship one it would catch.
_VENDOR_PREFIX = "AK" + "IA"
_VENDOR_TAIL = "3KJ7QWZX" + "CVBNMLKJ"
AWS_KEY_LINE = f'AWS_KEY = "{_VENDOR_PREFIX}{_VENDOR_TAIL}"\n'

HIDDEN = "hidden"


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(
        ["git", *args],
        cwd=cwd,
        check=True,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
    )


def _run(cwd: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "roam", *args],
        cwd=cwd,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=600,
        env=dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUTF8="1", NO_COLOR="1"),
    )


def _init(cwd: Path) -> None:
    """``roam init``, then drop the ``.roamignore`` it writes.

    That file is discovered but never indexed, so it makes every fresh fixture
    report one unindexed file -- a pre-existing false positive on a DIFFERENT
    axis, which would let an assertion about THIS one pass for the wrong
    reason.  Same removal, same reasoning, as the W1466 fixture.
    """
    built = _run(cwd, "init")
    assert built.returncode == 0, built.stdout[-800:] + built.stderr[-800:]
    (cwd / ".roamignore").unlink(missing_ok=True)


def _make_unenumerable(repo: Path) -> bool:
    """Put ``HIDDEN/leak.py`` where ``git ls-files --others`` cannot reach it.

    Returns False when the condition cannot be established on this host, which
    the caller must turn into a skip rather than a pass.
    """
    if os.name == "nt":
        # A trailing dot is legal on NTFS only through the device path.  Plain
        # ASCII on purpose: nothing here should depend on an encoding accident.
        dev = "\\\\?\\" + str(repo).replace("/", "\\") + "\\" + HIDDEN + "."
        try:
            os.mkdir(dev)
            with open(dev + "\\leak.py", "w", encoding="utf-8") as fh:
                fh.write(AWS_KEY_LINE)
        except OSError:
            return False
        return True

    target = repo / HIDDEN
    target.mkdir()
    (target / "leak.py").write_text(AWS_KEY_LINE, encoding="utf-8")
    target.chmod(0o000)
    # Root ignores the mode, so the directory would still be enumerable and
    # every assertion downstream would hold vacuously.
    return not os.access(target, os.R_OK) or os.geteuid() != 0


def _git_confirms_it_could_not_look(repo: Path) -> bool:
    """The premise, measured: git warned AND omitted the path, at rc 0."""
    result = subprocess.run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard"],
        cwd=repo,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        stdin=subprocess.DEVNULL,
        timeout=60,
    )
    listed = {p.strip() for p in result.stdout.splitlines() if p.strip()}
    return (
        result.returncode == 0
        and "could not open directory" in result.stderr
        and not any(p.startswith(HIDDEN) for p in listed)
    )


def _repo_with_unenumerable_dir(root: Path) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    _git(root, "init", "-q", ".")
    _git(root, "config", "user.email", "fixture@example.invalid")
    _git(root, "config", "user.name", "fixture")
    (root / "a.py").write_text("def helper(x):\n    return x + 1\n", encoding="utf-8")
    _git(root, "add", "a.py")
    _git(root, "commit", "-q", "-m", "fixture")

    if not _make_unenumerable(root):
        pytest.skip("cannot create a directory git is unable to open on this host")
    if not _git_confirms_it_could_not_look(root):
        pytest.skip("git enumerated the directory anyway -- the premise does not hold here")
    return root


# ---------------------------------------------------------------------------
# Discovery: the gap must be reportable, not only suffered
# ---------------------------------------------------------------------------


def test_discovery_reports_the_directory_it_could_not_enumerate(tmp_path: Path) -> None:
    repo = _repo_with_unenumerable_dir(tmp_path / "repo")

    discovered, skips = discover_files_with_skips(repo)

    assert "a.py" in discovered
    assert not any(p.startswith(HIDDEN) for p in discovered), (
        "the file genuinely is unreachable -- this fix counts it, it does not recover it"
    )
    assert skips.get(SKIP_UNENUMERABLE), (
        f"a directory the enumerator could not open must be reported, not dropped: {skips}"
    )


def test_the_bare_discovery_helper_is_unchanged(tmp_path: Path) -> None:
    """The ~20 callers that only want paths must not have to change."""
    repo = _repo_with_unenumerable_dir(tmp_path / "repo")
    discovered, _skips = discover_files_with_skips(repo)
    assert discover_files(repo) == discovered


def test_an_ordinary_repository_reports_no_such_gap(tmp_path: Path) -> None:
    """The must-not-fire control.

    Without it the fix could mark every repository partial, and a signal that
    fires everywhere gets tuned out until it means nothing.
    """
    repo = tmp_path / "ordinary"
    repo.mkdir()
    _git(repo, "init", "-q", ".")
    _git(repo, "config", "user.email", "fixture@example.invalid")
    _git(repo, "config", "user.name", "fixture")
    (repo / "a.py").write_text("x = 1\n", encoding="utf-8")
    _git(repo, "add", "a.py")
    _git(repo, "commit", "-q", "-m", "fixture")

    _discovered, skips = discover_files_with_skips(repo)
    assert SKIP_UNENUMERABLE not in skips, skips
    assert skips == {}, f"an ordinary tree must report no coverage gap at all: {skips}"


# ---------------------------------------------------------------------------
# secrets: the verdict a user acts on
# ---------------------------------------------------------------------------


def test_secrets_does_not_certify_a_repo_it_could_not_fully_enumerate(tmp_path: Path) -> None:
    repo = _repo_with_unenumerable_dir(tmp_path / "repo")
    _init(repo)

    run = _run(repo, "secrets", "--fail-on-found")

    assert run.returncode != 0, (
        "`roam secrets --fail-on-found` certified a repository holding a credential "
        "in a directory it was never able to open.\n"
        f"stdout: {run.stdout.strip()[:800]!r}"
    )
    assert "No secrets found" not in run.stdout, run.stdout[:800]


def test_the_secrets_envelope_names_the_gap(tmp_path: Path) -> None:
    repo = _repo_with_unenumerable_dir(tmp_path / "repo")
    _init(repo)

    run = _run(repo, "--json", "secrets")
    summary = json.loads(run.stdout[run.stdout.find("{") :])["summary"]

    assert summary["scan_incomplete"] is True, summary
    assert summary["partial_success"] is True, summary
    assert summary["unenumerable_dirs"] >= 1, summary
    assert summary["unenumerable_paths"], (
        f"the envelope must say WHERE it could not look, not merely that it could not: {summary}"
    )
    assert "NOT PROVEN CLEAN" in summary["verdict"], summary
    # A count of directories is not a count of files, and no field may let a
    # reader treat it as one.
    assert "unenumerable_files" not in summary, summary


def test_a_clean_repository_still_passes_unchanged(tmp_path: Path) -> None:
    """The second must-not-fire control, at the verdict rather than the skip.

    The gate that protects this repository runs on every push; a fix that made
    it refuse everywhere would be discovered by being switched off.
    """
    repo = tmp_path / "ordinary"
    repo.mkdir()
    _git(repo, "init", "-q", ".")
    _git(repo, "config", "user.email", "fixture@example.invalid")
    _git(repo, "config", "user.name", "fixture")
    (repo / "a.py").write_text("def helper(x):\n    return x + 1\n", encoding="utf-8")
    _git(repo, "add", "a.py")
    _git(repo, "commit", "-q", "-m", "fixture")
    _init(repo)

    run = _run(repo, "--json", "secrets", "--fail-on-found")
    assert run.returncode == 0, run.stdout[:800] + run.stderr[:800]
    summary = json.loads(run.stdout[run.stdout.find("{") :])["summary"]
    assert summary["scan_incomplete"] is False, summary
    assert summary["partial_success"] is False, summary
