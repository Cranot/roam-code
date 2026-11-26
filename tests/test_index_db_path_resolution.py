"""Every reader of a project's index finds it where `roam index` wrote it.

With ``ROAM_DB_DIR`` (or the ``db_dir`` config key) the index lives outside
``.roam/``. Code that built ``.roam/index.db`` by hand then saw no index: MCP
completions returned nothing, PR replay skipped its vuln-reach and test-impact
envelopes, and the compiler's index checks stat'ed a missing file.
"""

from __future__ import annotations

import re
import sqlite3
from pathlib import Path

from tests._helpers.repo_root import repo_root

ROOT = repo_root()

# Workspace mode resolves one index per member repository; ROAM_DB_DIR names
# a single directory, so those two files keep the per-repository default.
_ALLOWED = {
    "src/roam/commands/cmd_ws.py",
    "src/roam/workspace/config.py",
}

# A path built from the two parts: Path(".roam") / "index.db",
# x / ".roam" / "index.db", os.path.join(x, ".roam", "index.db").
_HAND_BUILT = re.compile(r"""["']\.roam["']\s*\)?\s*(?:/|,)\s*["']index\.db["']""")


def test_plan_cache_index_path_follows_roam_db_dir(tmp_path, monkeypatch):
    from roam.plan.plan_cache import _index_db_path

    db_dir = tmp_path / "external"
    monkeypatch.setenv("ROAM_DB_DIR", str(db_dir))
    assert Path(_index_db_path(str(tmp_path / "proj"))) == db_dir / "index.db"
    assert not db_dir.exists(), "resolving the path must not create the directory"


def test_mcp_completions_open_an_index_kept_in_roam_db_dir(tmp_path, monkeypatch):
    from roam.mcp_extras import completions

    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / ".git").mkdir()
    db_dir = tmp_path / "external"
    db_dir.mkdir()
    sqlite3.connect(db_dir / "index.db").close()
    monkeypatch.setenv("ROAM_DB_DIR", str(db_dir))
    with completions._open_index(str(proj)) as conn:
        assert conn is not None


def test_no_source_file_builds_the_index_path_by_hand():
    offenders = []
    for path in sorted((ROOT / "src" / "roam").rglob("*.py")):
        rel = path.relative_to(ROOT).as_posix()
        if rel in _ALLOWED:
            continue
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if _HAND_BUILT.search(line):
                offenders.append(f"{rel}:{number}: {line.strip()}")
    assert not offenders, "resolve the index with roam.db.connection.get_db_path:\n" + "\n".join(offenders)


def test_mcp_completions_fallback_opens_a_relative_roam_db_dir(tmp_path, monkeypatch):
    from roam.db import connection
    from roam.mcp_extras import completions

    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / ".git").mkdir()
    (tmp_path / "cache").mkdir()
    sqlite3.connect(tmp_path / "cache" / "index.db").close()
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("ROAM_DB_DIR", "cache")

    def refuse(*args, **kwargs):
        raise RuntimeError("older index layout")

    monkeypatch.setattr(connection, "open_db", refuse)
    with completions._open_index(str(proj)) as conn:
        assert conn is not None
