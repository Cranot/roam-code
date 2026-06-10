"""`roam verify <dir>` must scan the files UNDER the directory, not silently PASS.

Bug (2026-06-05): passing a directory (`roam verify src/roam/`) resolved the bare
directory string against the index DB, whose resolver only does exact-path +
basename-suffix matching. A directory matches neither → `file_map` empty →
`files_checked == 0` → false-green PASS (the Pattern-2 silent-fallback class this
repo explicitly guards against). Fix: `_expand_dir_targets` rewrites each on-disk
directory target into the indexed files beneath it. Pins the expander + the
end-to-end "directory arg surfaces a violation in a nested file" behaviour.
"""

from __future__ import annotations

import json
import os

from click.testing import CliRunner

from roam.cli import cli
from roam.commands.cmd_verify import _expand_dir_targets


def _indexed_tree(tmp_path):
    proj = tmp_path / "proj"
    (proj / "pkg" / "sub").mkdir(parents=True)
    (proj / ".git").mkdir(exist_ok=True)  # isolate index root from any stray /tmp/.git
    # Establish a snake_case convention so a lone PascalCase fn is a violation
    # (the naming check is convention-relative, not absolute).
    snake = "\n\n".join(f"def helper_{i}():\n    return {i}" for i in range(10))
    (proj / "pkg" / "__init__.py").write_text(
        "from __future__ import annotations\n\n\n" + snake + "\n", encoding="utf-8"
    )
    # A naming violation (PascalCase fn) buried two dirs deep.
    (proj / "pkg" / "sub" / "mod.py").write_text(
        "from __future__ import annotations\n\n\ndef BadName():\n    return 1\n", encoding="utf-8"
    )
    old = os.getcwd()
    try:
        os.chdir(str(proj))
        from roam.index.indexer import Indexer

        Indexer(project_root=proj).run(force=True, quiet=True, progress_bar=False)
    finally:
        os.chdir(old)
    return proj


def test_expand_dir_targets_unit(tmp_path):
    proj = _indexed_tree(tmp_path)
    old = os.getcwd()
    try:
        os.chdir(str(proj))
        from pathlib import Path

        expanded = _expand_dir_targets(["pkg"], Path("."))
    finally:
        os.chdir(old)
    # The directory string is gone; the files beneath it are present.
    assert "pkg" not in expanded
    assert any(p.endswith("pkg/sub/mod.py") for p in expanded), expanded
    # A non-directory target passes through untouched.
    assert _expand_dir_targets(["pkg/sub/mod.py"], Path(".")) == ["pkg/sub/mod.py"]


def test_syntax_skips_data_languages(tmp_path, monkeypatch):
    """A yaml/json/markdown file is NOT a syntax 'parse failure'.

    yaml has a roam-index grammar but no `parse_file` wiring → it returns None.
    The syntax check must SKIP data/markup languages (verify gates code, not
    config), so a tree full of taint-rule + CI yaml does not emit false parse-
    failure findings. Real code returning None is still disclosed (W-Pattern2,
    pinned in test_pattern2_silent_success_disclosure.py)."""
    from roam.commands import cmd_verify

    class _FakeConn:
        def execute(self, *_a, **_k):
            class _Cur:
                def fetchall(_self):
                    return []

            return _Cur()

    (tmp_path / "rules.yaml").write_text("sources:\n  - x\n", encoding="utf-8")
    monkeypatch.setattr(
        cmd_verify, "batched_in", lambda conn, sql, ids: [{"id": 1, "path": "rules.yaml", "language": "yaml"}]
    )
    result = cmd_verify._check_syntax(_FakeConn(), [1], tmp_path)
    assert result.get("parse_failures", 0) == 0, result
    assert result["violations"] == [], result["violations"]


def test_naming_carveouts_reused_from_conventions():
    """verify's naming check reuses the canonical W162 false-positive carve-outs.

    Three classes were re-flagged by verify (Pattern-4 detector inconsistency)
    even though the standalone `conventions` command already excludes them:
    UPPER_SNAKE constants, Python PascalCase type aliases, and non-code-language
    (yaml/CI-template) keys. Pin the shared `_naming_group_or_skip` resolver."""
    from roam.commands.cmd_verify import _naming_group_or_skip

    # UPPER_SNAKE constant reported as a variable/property → routed to constants.
    assert _naming_group_or_skip("VERSION", "property", "python", None) == "constants"
    assert _naming_group_or_skip("MAX_RETRIES", "variable", "python", None) == "constants"
    # Python PascalCase type alias stored as kind=variable → skipped.
    assert _naming_group_or_skip("PathLike", "variable", "python", "PathLike = Union[str, os.PathLike]") is None
    assert _naming_group_or_skip("LockMode", "variable", "python", 'LockMode = Literal["read", "write"]') is None
    # yaml/CI-template keys → skipped (non-code language).
    assert _naming_group_or_skip("checkout", "class", "yaml", None) is None
    # A genuinely mis-cased function is NOT skipped (still classified).
    assert _naming_group_or_skip("BadFn", "function", "python", None) == "functions"


def test_verify_directory_arg_scans_tree(tmp_path):
    proj = _indexed_tree(tmp_path)
    runner = CliRunner()
    old = os.getcwd()
    try:
        os.chdir(str(proj))
        res = runner.invoke(cli, ["--json", "verify", "pkg", "--checks", "naming"], env={"ROAM_COMPILE_VERIFY": "1"})
    finally:
        os.chdir(old)
    out = res.output
    env = json.loads(out[out.index("{") :])
    # No longer false-green: the buried PascalCase fn is found.
    assert env["summary"]["files_checked"] >= 1, env["summary"]
    syms = {v["symbol"] for v in env["categories"]["naming"]["violations"]}
    assert "BadName" in syms, env["categories"]["naming"]
