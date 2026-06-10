"""W-ENTRY+ — declared console-script entry point (2026-06-10).

The authoritative CLI entry point is the `[project.scripts]` console script
in pyproject.toml (what `pip install` puts on PATH), not the highest-fan-out
indexed function. The entry_point_where probe now surfaces it first.
"""

from __future__ import annotations

from roam.plan.compiler import _declared_console_scripts


class TestDeclaredConsoleScripts:
    def test_reads_project_scripts_and_maps_src_layout(self, tmp_path):
        (tmp_path / "pyproject.toml").write_text(
            '[project]\nname = "demo"\n\n[project.scripts]\ndemo = "demo.cli:main"\n'
        )
        (tmp_path / "src" / "demo").mkdir(parents=True)
        (tmp_path / "src" / "demo" / "cli.py").write_text("def main(): ...\n")
        got = _declared_console_scripts(str(tmp_path))
        assert got == [{"name": "demo", "target": "demo.cli:main", "file": "src/demo/cli.py"}]

    def test_maps_flat_layout(self, tmp_path):
        (tmp_path / "pyproject.toml").write_text('[project.scripts]\ntool = "tool:run"\n')
        (tmp_path / "tool.py").write_text("def run(): ...\n")
        got = _declared_console_scripts(str(tmp_path))
        assert got[0]["file"] == "tool.py"

    def test_target_without_resolvable_file_still_returned(self, tmp_path):
        (tmp_path / "pyproject.toml").write_text('[project.scripts]\nx = "ghost.mod:fn"\n')
        got = _declared_console_scripts(str(tmp_path))
        assert got == [{"name": "x", "target": "ghost.mod:fn"}]

    def test_no_pyproject_returns_none(self, tmp_path):
        assert _declared_console_scripts(str(tmp_path)) is None

    def test_no_scripts_section_returns_none(self, tmp_path):
        (tmp_path / "pyproject.toml").write_text('[project]\nname = "x"\n')
        assert _declared_console_scripts(str(tmp_path)) is None

    def test_none_cwd_returns_none(self):
        assert _declared_console_scripts(None) is None

    def test_resolves_this_repo(self):
        import os

        if not os.path.exists("pyproject.toml"):
            import pytest

            pytest.skip("not in repo root")
        got = _declared_console_scripts(".")
        assert got and any(e["target"] == "roam.cli:cli" for e in got)
