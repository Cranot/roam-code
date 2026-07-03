"""Import-time side-effect audit — file-IO cluster for audit/security freeform
compiles.

Extracted from ``plan/compiler.py`` (feature-envy: the compiler reached into the
filesystem to enumerate source files and scan their module-load effects). The
compiler now calls a single entry point, :func:`scan_named_dirs_import_effects`;
everything else here is module-internal.

Every reader is realpath-containment guarded so a repo symlink such as
``src/foo.py -> /outside/file.py`` can never make the scan read or label
out-of-repo content. See ``tests/test_audit_symlink_containment.py``.
"""

from __future__ import annotations

import os
from pathlib import Path

_AUDIT_SCAN_EXTS = {".py", ".ts", ".js", ".tsx", ".jsx", ".go", ".rb", ".java"}


def _audit_file_contained(fp: Path, cwd: str) -> bool:
    """True when ``fp``'s resolved real path stays under ``cwd``.

    Blocks repo symlinks such as ``src/foo.py -> /outside/file.py`` from
    reaching the audit-intent side-effect scan: without this check the scan
    would follow the link, read the out-of-repo file, and report its
    module-load labels as if they belonged to the project. Mirrors the
    realpath-containment idiom used by the forbidden-path resolver."""
    try:
        root = os.path.realpath(cwd)
        real = os.path.realpath(fp)
    except OSError:
        return False
    return real == root or real.startswith(root + os.sep)


def _file_import_effects(fp: Path, cwd: str) -> list[str]:
    """Module-load io_write/process effects for one file (empty on IO error
    or when the file resolves outside ``cwd`` — defense-in-depth against
    repo symlinks that escape the project root)."""
    if not _audit_file_contained(fp, cwd):
        return []
    try:
        if fp.stat().st_size > 200 * 1024:
            return []
        src = fp.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    from roam.world_model.side_effects import scan_module_init_effects

    rel = os.path.relpath(fp, cwd)
    return [
        f"{rel}:L{ln} {kind} ({label})"
        for ln, kind, label in scan_module_init_effects(src)
        if kind in ("io_write", "process")
    ]


def _audit_source_files_in_dir(base: Path, cwd: str) -> list[Path]:
    """Source files (by audit extension) directly in ``base``, sorted.

    Files whose real path escapes ``cwd`` (e.g. a symlink ``foo.py`` ->
    ``/outside/file.py``) are dropped so the audit scan never reads or
    labels out-of-repo content."""
    if not base.is_dir():
        return []
    return [
        fp
        for fp in sorted(base.glob("*"))
        if fp.suffix in _AUDIT_SCAN_EXTS and fp.is_file() and _audit_file_contained(fp, cwd)
    ]


def _collect_audit_files(named_paths: list[str], cwd: str, cap: int = 40) -> list[Path]:
    """Source files in the directories of the named paths, capped at `cap`."""
    dirs = {os.path.dirname(p) for p in named_paths[:6] if isinstance(p, str) and not p.startswith("@pack/")}
    files: list[Path] = []
    for d in sorted(dirs):
        files.extend(_audit_source_files_in_dir(Path(cwd) / d, cwd))
        if len(files) >= cap:
            return files[:cap]
    return files


def scan_named_dirs_import_effects(named_paths: list[str], cwd: str) -> list[str]:
    """Bounded import-time side-effect scan over the directories of the named
    files (audit aid). Capped so it never blows the compile budget."""
    hits: list[str] = []
    for fp in _collect_audit_files(named_paths, cwd):
        hits.extend(_file_import_effects(fp, cwd))
    return hits[:20]
