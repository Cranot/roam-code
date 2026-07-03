"""Regression: audit-intent side-effect scan must not follow symlinks that
escape ``cwd``.

A repo can plant ``src/foo.py -> /outside/file.py``. Before the containment
guard, ``_audit_source_files_in_dir`` enumerated the symlink (it has a
``.py`` suffix and ``is_file()`` follows the link) and ``_file_import_effects``
read the out-of-repo target, reporting its module-load labels as if they
belonged to the project. The realpath-under-cwd guard at both the
enumeration layer and the leaf reader seals that.
"""

from __future__ import annotations

import os
from pathlib import Path

from roam.plan.import_audit import (
    _audit_file_contained,
    _audit_source_files_in_dir,
    _file_import_effects,
)


def _repo_with_symlink(tmp_path: Path) -> tuple[Path, Path, Path, Path]:
    """Build a cwd with one real source file, one escaping symlink, and one
    contained symlink. Returns (cwd, real_file, escaping_link, contained_link)."""
    cwd = tmp_path / "repo"
    src_dir = cwd / "src"
    src_dir.mkdir(parents=True)

    real_file = src_dir / "real.py"
    real_file.write_text('import subprocess\nsubprocess.run(["ls"])\n', encoding="utf-8")

    # Target lives OUTSIDE cwd.
    outside = tmp_path / "outside"
    outside.mkdir()
    outside_target = outside / "secret.py"
    outside_target.write_text('import os\nos.makedirs("/pwned")\n', encoding="utf-8")

    escaping_link = src_dir / "escaping.py"
    os.symlink(outside_target, escaping_link)

    # A second real file inside cwd that the contained symlink points at.
    inner_target = src_dir / "inner_target.py"
    inner_target.write_text('import os\nos.makedirs("/ok")\n', encoding="utf-8")
    contained_link = src_dir / "contained.py"
    os.symlink(inner_target, contained_link)

    return cwd, real_file, escaping_link, contained_link


def test_audit_file_contained_rejects_escaping_symlink(tmp_path):
    cwd, _real, escaping, _contained = _repo_with_symlink(tmp_path)
    assert _audit_file_contained(escaping, str(cwd)) is False


def test_audit_file_contained_accepts_real_and_intra_repo_symlink(tmp_path):
    cwd, real, _escaping, contained = _repo_with_symlink(tmp_path)
    assert _audit_file_contained(real, str(cwd)) is True
    # Symlink whose target also resolves under cwd is allowed.
    assert _audit_file_contained(contained, str(cwd)) is True


def test_audit_source_files_in_dir_drops_escaping_symlink(tmp_path):
    """Enumeration never hands an escaping symlink to the leaf reader."""
    cwd, real, escaping, contained = _repo_with_symlink(tmp_path)
    enumerated = _audit_source_files_in_dir(cwd / "src", str(cwd))
    names = {fp.name for fp in enumerated}
    assert "real.py" in names
    assert "contained.py" in names
    # The escaping symlink must be filtered out before it reaches the reader.
    assert "escaping.py" not in names
    assert escaping not in enumerated


def test_file_import_effects_reads_nothing_outside_cwd(tmp_path):
    """Defense-in-depth: even if handed an escaping symlink directly, the leaf
    reader returns no labels (never reads the out-of-repo target)."""
    cwd, real, escaping, _contained = _repo_with_symlink(tmp_path)
    # Positive control: a contained file still produces its label.
    real_hits = _file_import_effects(real, str(cwd))
    assert any("process" in h for h in real_hits)
    # The escaping symlink yields nothing.
    assert _file_import_effects(escaping, str(cwd)) == []


def test_file_import_effects_follows_safe_intra_repo_symlink(tmp_path):
    """A symlink whose target stays under cwd is still scanned."""
    cwd, _real, _escaping, contained = _repo_with_symlink(tmp_path)
    hits = _file_import_effects(contained, str(cwd))
    assert any("io_write" in h for h in hits)
    # Reported path is the repo-relative link path, not the target.
    assert all(h.startswith("src/contained.py") for h in hits)
