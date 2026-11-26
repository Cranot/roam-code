"""`roam coupling` calls a co-changing pair HIDDEN only when no code links the files.

`structural?` used to count a file link only when it carried two or more
symbols, so a module that imports and calls ONE function of the file it always
changes with was reported as hidden coupling. `roam dark-matter`, which
answers the same question, already counted any link; the two disagreed on the
same pair.
"""

from __future__ import annotations

import json

from tests.conftest import git_commit, git_init, index_in_process, invoke_cli


def _pair_repo(tmp_path, *, linked: bool):
    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / "a.py").write_text("def a():\n    return 1\n", encoding="utf-8")
    body = "from a import a\n\n\ndef b():\n    return a()\n" if linked else "def b():\n    return 2\n"
    (proj / "b.py").write_text(body, encoding="utf-8")
    git_init(proj)
    for i in range(4):
        for name in ("a.py", "b.py"):
            with (proj / name).open("a", encoding="utf-8") as fh:
                fh.write(f"# change {i}\n")
        git_commit(proj, f"pair {i}")
    out, code = index_in_process(proj)
    assert code == 0, out
    return proj


def _structural(cli_runner, proj) -> bool:
    result = invoke_cli(cli_runner, ["coupling"], cwd=proj, json_mode=True)
    assert result.exit_code == 0, result.output
    pairs = json.loads(result.output)["pairs"]
    assert len(pairs) == 1, pairs
    return pairs[0]["has_structural_edge"]


def test_a_single_symbol_import_is_a_structural_link(tmp_path, cli_runner):
    proj = _pair_repo(tmp_path, linked=True)
    assert _structural(cli_runner, proj) is True


def test_unlinked_co_changing_files_stay_hidden(tmp_path, cli_runner):
    proj = _pair_repo(tmp_path, linked=False)
    assert _structural(cli_runner, proj) is False
