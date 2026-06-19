"""Regression: `roam verify` must re-index NEW and EDITED targets before checking.

verify reads symbols FROM the index DB, and `ensure_index()` is a no-op once the
DB already exists. So without the auto-reindex guard in `cmd_verify`, two cases go
false-green:

  1. A just-written file isn't in the DB → resolves to zero symbols →
     files_checked=0 → a PASS on code that was never actually checked.
  2. A newly-added symbol inside an ALREADY-indexed file is invisible (the file is
     in the DB so a naive absence check passes, but its new symbols aren't
     indexed) → verify checks STALE symbols and misses the violation.

Case 2 is the common agent loop (edit an existing file), and the first version of
the fix only handled case 1 — it shipped a false-green on edits until the guard
was widened to the indexer's own `get_changed_files` (added + modified). These
tests pin BOTH halves against regression, plus a source-level guard so the
reindex block can't be silently dropped.

The bug only manifests when the DB ALREADY EXISTS but is stale, so each test
pre-builds the index, THEN writes/edits — exactly the state `ensure_index()`
short-circuits.
"""

from __future__ import annotations

import json
import os

from click.testing import CliRunner

from roam.cli import cli

# Strong snake_case majority so the naming detector's dominant convention is
# unambiguous (≥90% → a PascalCase function flags as a FAIL, not a coin-flip).
_SNAKE_BASELINE = (
    "from __future__ import annotations\n\n\n"
    + "\n\n".join(f"def helper_{i}():\n    return {i}" for i in range(12))
    + "\n"
)


def _build_indexed_project(tmp_path):
    """A tmp project with a built index (DB exists → ensure_index will no-op)."""
    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / ".git").mkdir(exist_ok=True)  # isolate index root from any stray /tmp/.git
    (proj / "lib.py").write_text(_SNAKE_BASELINE, encoding="utf-8")
    old = os.getcwd()
    try:
        os.chdir(str(proj))
        from roam.index.indexer import Indexer

        Indexer(project_root=proj).run(force=True, quiet=True, progress_bar=False)
    finally:
        os.chdir(old)
    return proj


def _verify(proj, target):
    """Invoke `roam --json verify <target> --checks naming,syntax` inside proj."""
    runner = CliRunner()
    old = os.getcwd()
    try:
        os.chdir(str(proj))
        res = runner.invoke(
            cli,
            ["--json", "verify", target, "--checks", "naming,syntax"],
            env={"ROAM_COMPILE_VERIFY": "1"},
        )
    finally:
        os.chdir(old)
    assert res.exit_code in (0, 4, 5), f"unexpected exit {res.exit_code}: {res.output}"
    # --json mode emits exactly one envelope (the auto-reindex runs quiet), so the
    # output is the JSON object; parse from the first brace defensively.
    out = res.output
    return json.loads(out[out.index("{") :])


def _naming_symbols(envelope):
    return {v["symbol"] for v in envelope["categories"]["naming"]["violations"]}


def test_verify_indexes_new_file_target(tmp_path):
    """Case 1: a brand-new file (absent from the DB) must be indexed and checked,
    not waved through as files_checked=0."""
    proj = _build_indexed_project(tmp_path)
    (proj / "new_mod.py").write_text(
        "from __future__ import annotations\n\n\ndef NewBadName():\n    return 1\n",
        encoding="utf-8",
    )
    env = _verify(proj, "new_mod.py")
    assert env["summary"]["files_checked"] >= 1, f"new file was not indexed/checked: {env['summary']}"
    assert "NewBadName" in _naming_symbols(env), (
        f"PascalCase fn in a new file was missed (stale-index false green): {env['categories']['naming']}"
    )


def test_verify_reindexes_edited_indexed_file(tmp_path):
    """Case 2 (the common agent loop + the original false-green): a symbol added to
    an ALREADY-indexed file must trigger a reindex so it's actually checked."""
    proj = _build_indexed_project(tmp_path)
    # lib.py is already in the DB; append a PascalCase function (content changes →
    # hash differs -> get_index_changed_files reports it 'modified').
    with open(proj / "lib.py", "a", encoding="utf-8") as fh:
        fh.write("\n\ndef EditBadName():\n    return 99\n")
    env = _verify(proj, "lib.py")
    assert env["summary"]["files_checked"] >= 1, env["summary"]
    assert "EditBadName" in _naming_symbols(env), (
        "newly-added symbol in an already-indexed file was missed — the absence-only "
        f"guard regressed back in: {env['categories']['naming']}"
    )


def test_verify_source_pins_stale_target_reindex():
    """Source-level guard: the reindex block must use the indexer's own change
    detector (added + modified), not a bare absence check, and must run the
    incremental indexer when a target is stale. Belt-and-braces against an
    environment quirk silently short-circuiting the runtime tests above."""
    import inspect

    from roam.commands import cmd_verify

    src = inspect.getsource(cmd_verify)
    assert "get_index_changed_files" in src, (
        "verify must detect stale targets via get_index_changed_files (added+modified), "
        "not an absence-only check — the latter false-greens on edits"
    )
    assert "Indexer().run(" in src, (
        "verify must run the incremental indexer to refresh stale targets before reading symbols from the DB"
    )
