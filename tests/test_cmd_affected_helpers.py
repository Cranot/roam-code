"""Gap-filling tests for `roam affected` (`cmd_affected.py`).

`test_affected.py` already covers the happy-path text + JSON surface. This file
adds the pieces that file leaves untested:

- the pure `_group_by_module` helper (module-name derivation, the
  changed/affected exclusion rule, sorting, plain-dict conversion);
- hop-bucket classification correctness (service.py is 1-hop, api.py is 2+);
- the `--depth 0` boundary;
- colocated-test detection (a sibling test that imports nothing still lands in
  affected_tests purely by directory colocation);
- entry-point item shape;
- the text-mode "No changes detected." path; and
- the no-results JSON envelope contract (Pattern-1: never empty stdout).
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from click.testing import CliRunner

sys.path.insert(0, str(Path(__file__).parent))
from conftest import assert_json_envelope, invoke_cli, parse_json_output

from roam.commands.cmd_affected import _group_by_module


@pytest.fixture
def cli_runner():
    return CliRunner()


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def chain_project(project_factory):
    """models.py <- service.py <- api.py, plus an unrelated utils.py.

    Second commit edits models.py so models.py is DIRECT, service.py is 1-hop,
    api.py is 2-hop. (Mirrors test_affected.py's fixture but kept local so this
    file is self-contained.)
    """
    return project_factory(
        {
            "models.py": ("class User:\n    def __init__(self, name):\n        self.name = name\n"),
            "service.py": ("from models import User\n\ndef create_user(name):\n    return User(name)\n"),
            "api.py": ("from service import create_user\n\ndef handle_request(name):\n    return create_user(name)\n"),
            "utils.py": ("def format_name(name):\n    return name.strip().title()\n"),
        },
        extra_commits=[
            (
                {
                    "models.py": (
                        "class User:\n"
                        '    def __init__(self, name, email=""):\n'
                        "        self.name = name\n"
                        "        self.email = email\n"
                    ),
                },
                "add email to User",
            ),
        ],
    )


@pytest.fixture
def colocated_project(project_factory):
    """A changed source file with a sibling test that imports NOTHING.

    `pkg/test_widget.py` is in the same directory as the changed `pkg/widget.py`
    but has no import edge to it, so the ONLY way it can appear in
    affected_tests is via `_find_colocated_test_files`.
    """
    return project_factory(
        {
            "pkg/widget.py": ("def build():\n    return 1\n"),
            "pkg/test_widget.py": ("def test_nothing():\n    assert True\n"),
        },
        extra_commits=[
            (
                {"pkg/widget.py": ("def build():\n    return 2\n")},
                "bump widget",
            ),
        ],
    )


# ---------------------------------------------------------------------------
# _group_by_module — pure unit tests (no DB)
# ---------------------------------------------------------------------------


class TestGroupByModule:
    def test_root_files_group_under_root_label(self):
        res = _group_by_module({"models.py"}, set())
        assert "(root)" in res
        assert res["(root)"]["changed"] == 1

    def test_top_level_dir_becomes_module_key(self):
        res = _group_by_module({"pkg/a.py"}, {"pkg/b.py"})
        assert res["pkg/"]["changed"] == 1
        assert res["pkg/"]["affected"] == 1

    def test_affected_excludes_files_also_in_changed(self):
        # pkg/a.py is both changed and "affected"; it must count once, as changed.
        res = _group_by_module({"pkg/a.py"}, {"pkg/a.py", "pkg/b.py"})
        assert res["pkg/"]["changed"] == 1
        assert res["pkg/"]["affected"] == 1  # only b.py, not a.py

    def test_backslash_paths_are_normalised(self):
        res = _group_by_module({"pkg\\a.py"}, set())
        assert "pkg/" in res
        assert "pkg\\" not in res

    def test_keys_are_sorted(self):
        res = _group_by_module({"z/a.py", "a/b.py", "m.py"}, set())
        assert list(res.keys()) == sorted(res.keys())

    def test_empty_inputs_give_empty_dict(self):
        assert _group_by_module(set(), set()) == {}

    def test_returns_plain_dict_not_defaultdict(self):
        # The command converts the internal defaultdict via dict(sorted(...)),
        # so a missing key must raise rather than auto-vivify a zero row.
        res = _group_by_module({"a.py"}, set())
        with pytest.raises(KeyError):
            res["does/not/exist/"]

    def test_separate_dirs_are_separate_modules(self):
        res = _group_by_module({"a/x.py", "b/y.py"}, set())
        assert res["a/"]["changed"] == 1
        assert res["b/"]["changed"] == 1


# ---------------------------------------------------------------------------
# Hop-bucket classification correctness
# ---------------------------------------------------------------------------


class TestHopClassification:
    def test_one_hop_dependent_lands_in_transitive_1(self, cli_runner, chain_project, monkeypatch):
        monkeypatch.chdir(chain_project)
        result = invoke_cli(cli_runner, ["affected"], cwd=chain_project, json_mode=True)
        data = parse_json_output(result, "affected")
        t1_files = [e["file"] for e in data["affected_transitive_1"]]
        assert any(f.endswith("service.py") for f in t1_files)

    def test_two_hop_dependent_lands_in_transitive_2plus_not_1(self, cli_runner, chain_project, monkeypatch):
        monkeypatch.chdir(chain_project)
        result = invoke_cli(cli_runner, ["affected"], cwd=chain_project, json_mode=True)
        data = parse_json_output(result, "affected")
        t1_files = [e["file"] for e in data["affected_transitive_1"]]
        t2_files = [e["file"] for e in data["affected_transitive_2plus"]]
        assert any(f.endswith("api.py") for f in t2_files)
        assert not any(f.endswith("api.py") for f in t1_files)

    def test_unrelated_file_is_absent(self, cli_runner, chain_project, monkeypatch):
        monkeypatch.chdir(chain_project)
        result = invoke_cli(cli_runner, ["affected"], cwd=chain_project, json_mode=True)
        data = parse_json_output(result, "affected")
        all_files = (
            list(data["changed_files"])
            + [e["file"] for e in data["affected_transitive_1"]]
            + [e["file"] for e in data["affected_transitive_2plus"]]
        )
        assert not any(f.endswith("utils.py") for f in all_files)

    def test_changed_files_equals_affected_direct(self, cli_runner, chain_project, monkeypatch):
        # The command populates both keys from the same list.
        monkeypatch.chdir(chain_project)
        result = invoke_cli(cli_runner, ["affected"], cwd=chain_project, json_mode=True)
        data = parse_json_output(result, "affected")
        assert data["changed_files"] == data["affected_direct"]

    def test_root_module_changed_count(self, cli_runner, chain_project, monkeypatch):
        monkeypatch.chdir(chain_project)
        result = invoke_cli(cli_runner, ["affected"], cwd=chain_project, json_mode=True)
        data = parse_json_output(result, "affected")
        # Only models.py changed, and it lives at repo root.
        assert data["by_module"]["(root)"]["changed"] == 1


# ---------------------------------------------------------------------------
# --depth boundary
# ---------------------------------------------------------------------------


class TestDepthZero:
    def test_depth_zero_yields_no_transitive(self, cli_runner, chain_project, monkeypatch):
        monkeypatch.chdir(chain_project)
        result = invoke_cli(cli_runner, ["affected", "--depth", "0"], cwd=chain_project, json_mode=True)
        data = parse_json_output(result, "affected")
        assert data["affected_transitive_1"] == []
        assert data["affected_transitive_2plus"] == []
        # total_affected collapses to just the changed files.
        assert data["summary"]["total_affected"] == data["summary"]["changed_files"]


# ---------------------------------------------------------------------------
# Colocated-test detection
# ---------------------------------------------------------------------------


class TestColocatedTests:
    def test_sibling_test_detected_by_colocation(self, cli_runner, colocated_project, monkeypatch):
        monkeypatch.chdir(colocated_project)
        result = invoke_cli(cli_runner, ["affected"], cwd=colocated_project, json_mode=True)
        data = parse_json_output(result, "affected")
        tests = data["affected_tests"]
        assert any(t.endswith("pkg/test_widget.py") for t in tests)
        # The colocated test must not be miscounted as a changed file.
        assert not any(c.endswith("test_widget.py") for c in data["changed_files"])


# ---------------------------------------------------------------------------
# Entry-point item shape
# ---------------------------------------------------------------------------


class TestEntryPointShape:
    def test_each_entry_point_has_required_keys(self, cli_runner, chain_project, monkeypatch):
        monkeypatch.chdir(chain_project)
        result = invoke_cli(cli_runner, ["affected"], cwd=chain_project, json_mode=True)
        data = parse_json_output(result, "affected")
        for ep in data["affected_entry_points"]:
            assert {"name", "kind", "file", "line"}.issubset(ep.keys())


# ---------------------------------------------------------------------------
# No-results paths
# ---------------------------------------------------------------------------


class TestNoChanges:
    def test_text_mode_reports_no_changes(self, cli_runner, chain_project, monkeypatch):
        # HEAD..HEAD is an empty range -> the text branch.
        monkeypatch.chdir(chain_project)
        result = invoke_cli(cli_runner, ["affected", "--base", "HEAD"], cwd=chain_project)
        assert result.exit_code == 0
        assert "No changes detected" in result.output

    def test_json_no_changes_emits_full_empty_envelope(self, cli_runner, chain_project, monkeypatch):
        # Pattern-1: a no-results run must still emit a complete, non-empty envelope.
        monkeypatch.chdir(chain_project)
        result = invoke_cli(cli_runner, ["affected", "--base", "HEAD"], cwd=chain_project, json_mode=True)
        data = parse_json_output(result, "affected")
        assert_json_envelope(data, "affected")
        assert data["summary"]["total_affected"] == 0
        assert data["summary"]["changed_files"] == 0
        assert data["affected_direct"] == []
        assert data["affected_transitive_1"] == []
        assert data["affected_transitive_2plus"] == []
        assert data["affected_tests"] == []
        assert data["affected_entry_points"] == []
        assert data["by_module"] == {}
