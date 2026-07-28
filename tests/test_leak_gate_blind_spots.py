"""Regression guards: things the anti-leak gates used to be unable to SEE.

Two proven defect shapes, both of which made a gate print a clean verdict
over content it had never correctly read. Neither produced a diagnostic, so
neither was distinguishable from a real pass.

1. VACUOUS PASS ON AN EMPTY INVENTORY. The whole-tree scanners shape their
   verdict as "hits among the enumerated tracked files" and never checked
   that the enumeration produced anything. ``git ls-files`` honours
   ``GIT_INDEX_FILE`` / ``GIT_DIR`` / ``GIT_WORK_TREE``; pointed at another
   index it prints NOTHING and exits 0. These scanners run from
   ``.githooks/pre-commit`` and ``.githooks/pre-push`` -- exactly the context
   where Git exports those variables -- and from CI.

2. UTF-16 IS INVISIBLE TO A PATTERN CATALOGUE. ``read_text(encoding="utf-8")``
   reads a UTF-16 file WRONG rather than failing on it: every NUL padding
   byte is itself valid UTF-8, so nothing is dropped and an ASCII payload
   arrives as ``g\\x00h\\x00p\\x00_\\x00...``, matching no pattern. Concrete
   on this repository, not theoretical: PowerShell's ``>`` redirect and
   ``Out-File`` emit UTF-16LE by DEFAULT and roam-code is developed on
   Windows, so ``gh auth token > token.txt`` followed by an accidental
   ``git add`` yields a tracked live credential the gate reports clean.

Every fixture string here is assembled at RUNTIME. The gates correctly block
literals -- and a test that plants a forbidden pattern verbatim would block
the very commit that hardens the gate.
"""

from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
from pathlib import Path

import pytest

from tests._helpers.repo_root import repo_root

REPO_ROOT = repo_root()
SCANNER = REPO_ROOT / "scripts" / "scan_internal_language.py"
SECRET_SCANNER = REPO_ROOT / "scripts" / "secret_scan.py"

# A real entry in FORBIDDEN_PATTERNS ("Old GH Pages docs URL"), split so this
# file does not itself carry the contiguous pattern the gate forbids.
_LEAK_LINE = "see https://cranot" + ".github.io/roam-code/ for docs"

# Vendor-shaped but synthetic: ghp_ + 36 alphanumerics. Split for the same
# reason, and named for its SHAPE rather than for what it imitates, so no
# name-keyed generic-assignment pattern matches this module either.
_VENDOR_PREFIX = "gh" + "p_"
_VENDOR_TAIL = "A9f2Kd8Lm3" + "Qp7Rt1Zx5V" + "b6Nc4Ye0Wu" + "2Ij8Hq"


def _run_git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


def _make_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _run_git(repo, "init", "-q")
    _run_git(repo, "config", "user.email", "gate@example.invalid")
    _run_git(repo, "config", "user.name", "gate")
    return repo


def _run_scanner(repo: Path, *args: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(SCANNER), *args],
        cwd=repo,
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )


def _empty_index(tmp_path: Path) -> Path:
    """A valid Git index file describing zero tracked paths."""
    donor = tmp_path / "donor"
    donor.mkdir()
    _run_git(donor, "init", "-q")
    _run_git(donor, "config", "user.email", "gate@example.invalid")
    _run_git(donor, "config", "user.name", "gate")
    _run_git(donor, "commit", "-q", "--allow-empty", "-m", "empty")
    index = donor / ".git" / "index"
    if not index.exists():  # pragma: no cover - git writes it on first commit
        index.write_bytes(b"")
    return index


# ---------------------------------------------------------------------------
# Defect 1 -- an empty inventory must never read as a pass.
# ---------------------------------------------------------------------------


def test_whole_tree_scan_fails_closed_on_an_empty_inventory(tmp_path: Path) -> None:
    """Zero tracked files means the inventory did not compute, not "clean".

    Pre-fix: ``_tracked_paths`` returned ``[]``, ``_collect_hits`` looped over
    nothing, and ``main`` returned 0 -- a PASS with no file opened and nothing
    in the output saying so.
    """
    repo = _make_repo(tmp_path)
    proc = _run_scanner(repo, "--all")
    assert proc.returncode == 1, f"empty tracked tree reported clean:\n{proc.stdout}{proc.stderr}"
    assert "zero tracked files" in proc.stderr


def test_whole_tree_scan_ignores_an_inherited_index_redirection(tmp_path: Path) -> None:
    """``GIT_INDEX_FILE`` must not be able to empty the inventory.

    Git exports repository-local ``GIT_*`` controls to hooks, and this
    scanner runs from them. Pre-fix, an ambient ``GIT_INDEX_FILE`` pointed at
    another index made ``git ls-files`` print nothing, exit 0, and the gate
    pass over a tree that plainly holds a leak.
    """
    repo = _make_repo(tmp_path)
    (repo / "leak.md").write_text(_LEAK_LINE, encoding="utf-8")
    _run_git(repo, "add", "--", "leak.md")
    _run_git(repo, "commit", "-q", "-m", "leak")

    env = {**os.environ, "GIT_INDEX_FILE": str(_empty_index(tmp_path))}
    proc = _run_scanner(repo, "--all", env=env)
    assert proc.returncode == 1, f"redirected index produced a vacuous pass:\n{proc.stdout}{proc.stderr}"
    assert "leak.md" in proc.stderr


def test_ci_gate_enumeration_fails_closed_on_an_empty_inventory(tmp_path: Path, monkeypatch) -> None:
    """The same guard on the CI gate's own enumeration.

    Pre-fix ``_git_tracked_files`` returned ``[]`` and
    ``test_no_internal_language_in_tracked_files`` passed having opened zero
    files.
    """
    gate = _load_ci_gate()
    repo = _make_repo(tmp_path)
    monkeypatch.setattr(gate, "REPO_ROOT", repo)
    with pytest.raises(AssertionError, match="zero tracked files"):
        gate._git_tracked_files()


def _load_ci_gate():
    """Load ``tests/test_no_internal_language.py`` as an inspectable module."""
    path = REPO_ROOT / "tests" / "test_no_internal_language.py"
    spec = importlib.util.spec_from_file_location("_ci_leak_gate_under_test", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# ---------------------------------------------------------------------------
# Defect 2 -- UTF-16 content must not be invisible to the catalogue.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("name", "encoded"),
    [
        # What PowerShell's `>` and Out-File actually emit.
        ("bom.md", _LEAK_LINE.encode("utf-16")),
        # BOM-less: every byte is valid UTF-8, so the UTF-8 read SUCCEEDS and
        # silently yields NUL-interleaved text. A BOM check alone cannot see
        # this one.
        ("nobom.md", _LEAK_LINE.encode("utf-16-le")),
    ],
)
def test_whole_tree_scan_sees_utf16_tracked_files(tmp_path: Path, name: str, encoded: bytes) -> None:
    repo = _make_repo(tmp_path)
    (repo / name).write_bytes(encoded)
    _run_git(repo, "add", "--", name)
    _run_git(repo, "commit", "-q", "-m", "utf16")

    proc = _run_scanner(repo, "--all")
    assert proc.returncode == 1, f"UTF-16 leak in {name} was invisible:\n{proc.stdout}{proc.stderr}"
    assert name in proc.stderr


def test_staged_scan_sees_a_utf16_blob(tmp_path: Path) -> None:
    """The pre-commit path had the same blind spot as the pre-push one."""
    repo = _make_repo(tmp_path)
    _run_git(repo, "commit", "-q", "--allow-empty", "-m", "base")
    (repo / "staged.md").write_bytes(_LEAK_LINE.encode("utf-16"))
    _run_git(repo, "add", "--", "staged.md")

    proc = _run_scanner(repo, "--staged")
    assert proc.returncode == 1, f"UTF-16 leak staged for commit was invisible:\n{proc.stdout}{proc.stderr}"
    assert "staged.md" in proc.stderr


def test_pushed_history_scan_sees_a_bom_less_utf16_blob(tmp_path: Path) -> None:
    """The history path decoded by BOM only, so BOM-less UTF-16 evaded it.

    This is the gap compile-code recorded as still open on its own
    ``prepush_leak_scan.py``. roam-code's history reader already honoured a
    BOM; it could not, by construction, see UTF-16 without one.
    """
    repo = _make_repo(tmp_path)
    (repo / "hist.md").write_bytes(_LEAK_LINE.encode("utf-16-le"))
    _run_git(repo, "add", "--", "hist.md")
    _run_git(repo, "commit", "-q", "-m", "history")

    head = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True, check=True
    ).stdout.strip()
    updates = tmp_path / "updates.txt"
    updates.write_text(f"refs/heads/master {head} refs/heads/master {'0' * 40}\n", encoding="utf-8")

    proc = _run_scanner(repo, "--pre-push-updates", str(updates))
    assert proc.returncode == 1, f"BOM-less UTF-16 blob published unseen:\n{proc.stdout}{proc.stderr}"
    assert "hist.md" in proc.stderr


def test_secret_scan_sees_a_bom_less_utf16_credential(tmp_path: Path) -> None:
    """``scripts/secret_scan.py`` shared the BOM-only blind spot."""
    repo = _make_repo(tmp_path)
    body = 'api = "' + _VENDOR_PREFIX + _VENDOR_TAIL + '"\n'
    (repo / "cred.py").write_bytes(body.encode("utf-16-le"))
    _run_git(repo, "add", "--", "cred.py")
    _run_git(repo, "commit", "-q", "-m", "cred")

    proc = subprocess.run(
        [sys.executable, str(SECRET_SCANNER), "--rev-range", "HEAD"],
        cwd=repo,
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 1, f"BOM-less UTF-16 credential was invisible:\n{proc.stdout}{proc.stderr}"
    assert "cred.py" in proc.stderr


def test_utf8_scanning_is_unchanged(tmp_path: Path) -> None:
    """The extra readings must not cost precision on ordinary content.

    A conservation check: plain UTF-8 still produces exactly one hit per
    offending line, so the deduplication across readings actually holds.
    """
    repo = _make_repo(tmp_path)
    (repo / "plain.md").write_text(f"{_LEAK_LINE}\nclean line\n", encoding="utf-8")
    (repo / "ok.md").write_text("nothing forbidden here\n", encoding="utf-8")
    _run_git(repo, "add", "--", "plain.md", "ok.md")
    _run_git(repo, "commit", "-q", "-m", "plain")

    proc = _run_scanner(repo, "--all")
    assert proc.returncode == 1
    assert "1 internal-language leak(s) found" in proc.stderr
    assert "ok.md" not in proc.stderr
