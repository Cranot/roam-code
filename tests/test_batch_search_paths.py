"""Tests for `roam batch-search` symbol-name-only default.

Pre-fix: the SQL matched both symbol name AND file path, so a query
``useAccountBalance`` would return ``setup`` from
``tests/composables/transactions/useAccountBalance.test.ts`` because the
path matched. Post-fix: default matches symbol name + qualified name
only; ``--include-paths`` restores the old wide-match behaviour.
"""

from __future__ import annotations

import pytest

from tests.conftest import git_init, index_in_process, invoke_cli, parse_json_output


@pytest.fixture
def path_leak_project(tmp_path):
    """Project shaped to reproduce the path-match leak.

    ``setup`` is a generic function name that lives under a path
    containing ``Probe`` — the query ``Probe`` should NOT match
    ``setup`` by default, even though its path does.
    """
    proj = tmp_path / "batch_search_paths"
    proj.mkdir()
    (proj / ".gitignore").write_text(".roam/\n")
    src = proj / "src"
    src.mkdir()
    composables = src / "composables" / "Probe"
    composables.mkdir(parents=True)
    # File with the query string IN ITS PATH but a generic function name.
    (composables / "useProbe.py").write_text(
        "def setup():\n"
        '    """Fixture setup — name unrelated to Probe."""\n'
        "    return {}\n"
    )
    # File with the actual symbol — the legitimate match for "Probe".
    (src / "probes.py").write_text(
        "def Probe():\n"
        '    """The real Probe function."""\n'
        "    return True\n"
    )
    git_init(proj)
    index_in_process(proj)
    return proj


class TestBatchSearchPaths:
    def test_default_does_not_match_path_only(
        self, cli_runner, path_leak_project, monkeypatch
    ):
        """Default ``batch-search Probe`` must NOT return ``setup``.

        ``setup`` lives at ``src/composables/Probe/useProbe.py`` — its
        path contains the query but its symbol name does not. Pre-fix
        wide-match SQL returned it as a hit; post-fix it must not.
        """
        monkeypatch.chdir(path_leak_project)
        result = invoke_cli(
            cli_runner, ["batch-search", "Probe"], cwd=path_leak_project, json_mode=True
        )
        data = parse_json_output(result, "batch-search")
        hits = data["results"].get("Probe", [])
        names = {h["name"] for h in hits}
        assert "setup" not in names, (
            "default batch-search should not match `setup` whose only "
            f"connection to 'Probe' is the file path; got hits: {hits}"
        )
        # Sanity: the real Probe symbol IS found.
        assert "Probe" in names, (
            f"expected to find the actual Probe symbol; got hits: {hits}"
        )

    def test_include_paths_restores_wide_match(
        self, cli_runner, path_leak_project, monkeypatch
    ):
        """``--include-paths`` opts back into the legacy behaviour.

        Users who genuinely want a fixture/path-shaped lookup can pass
        the flag; without it, only symbol-name matches come back.
        """
        monkeypatch.chdir(path_leak_project)
        result = invoke_cli(
            cli_runner,
            ["batch-search", "Probe", "--include-paths"],
            cwd=path_leak_project,
            json_mode=True,
        )
        data = parse_json_output(result, "batch-search")
        hits = data["results"].get("Probe", [])
        names = {h["name"] for h in hits}
        assert "setup" in names, (
            "with --include-paths, batch-search should match symbols "
            f"whose file path contains the query; got hits: {hits}"
        )
        # And the symbol-name match still works.
        assert "Probe" in names
