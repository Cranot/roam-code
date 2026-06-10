"""File-scoping for `run_detectors` (the biggest single run_detectors lever).

The python-idiom detectors regex-scan EVERY Python file's full text — measured
~70% of a project-wide run on roam-code. `run_detectors(scope_file_ids=...)`
restricts the run to a file-id set (via `set_idiom_scope` for the idiom
detectors + a findings-level filter for the catalog detectors) so a caller that
already knows the changed fileset — e.g. `roam adversarial` — pays only for
those files. These pin: (1) scoping narrows findings to the scoped file,
(2) scoped findings are a strict subset of the full run, (3) the module-global
idiom scope is reset afterward so it can't silently narrow a later unscoped run.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from roam.catalog import python_idioms
from roam.catalog.detectors import run_detectors
from roam.db.connection import open_db
from roam.index.indexer import Indexer

# Each file carries a bare-except + except-pass (reliable idiom-detector fires).
_BAD = "def f():\n    try:\n        x = 1\n        return x\n    except:\n        pass\n"


def _index_two_file_project(work_dir: Path) -> None:
    (work_dir / "alpha.py").write_text(_BAD)
    (work_dir / "beta.py").write_text(_BAD)
    subprocess.run(["git", "init", "-q"], cwd=work_dir, check=True)
    subprocess.run(["git", "add", "."], cwd=work_dir, check=True)
    subprocess.run(
        ["git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "-m", "init"], cwd=work_dir, check=True
    )
    Indexer().run(quiet=True)


def _files_in(findings) -> set[str]:
    out: set[str] = set()
    for f in findings:
        loc = f.get("location", "")
        if ":" in loc:
            out.add(Path(loc.rsplit(":", 1)[0]).name)
    return out


def test_run_detectors_file_scope_narrows_and_resets(tmp_path, monkeypatch):
    python_idioms._clear_file_text_cache()
    monkeypatch.chdir(tmp_path)
    _index_two_file_project(tmp_path)

    with open_db(readonly=False) as conn:
        alpha_id = conn.execute("SELECT id FROM files WHERE path = 'alpha.py'").fetchone()[0]

        full = run_detectors(conn)
        full_files = _files_in(full)
        assert {"alpha.py", "beta.py"} <= full_files  # both files fire unscoped

        scoped = run_detectors(conn, scope_file_ids=[alpha_id])
        scoped_files = _files_in(scoped)
        assert scoped_files == {"alpha.py"}  # scoped to alpha only
        assert "beta.py" not in scoped_files

        # Scoped findings are a strict subset of the full run.
        full_sids = {f.get("symbol_id") for f in full}
        assert {f.get("symbol_id") for f in scoped} <= full_sids
        assert len(scoped) < len(full)

        # No leak: the module-global idiom scope is reset, so a later unscoped
        # run sees both files again.
        assert python_idioms._SCOPE_FILE_IDS is None
        after = run_detectors(conn)
        assert {"alpha.py", "beta.py"} <= _files_in(after)
