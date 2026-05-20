"""F3 (DOGFOOD-CORE-2026-05-20, MED): ``roam fan`` test/prod role split.

``roam fan`` ranks symbols/files by fan-in/fan-out but historically had
no test/prod role split, so on roam-code its #1 fan-in was a TEST-ONLY
conftest fixture (``invoke_cli``, ~2438 refs) — pure test noise crowding
out real production coupling. ``cmd_uses`` was the best-in-class
prod/test split (per-consumer ``scope`` field + production_consumers /
test_consumers in its summary, both via the canonical ``is_test_file``
helper). This suite pins that the SAME mechanism now applies to ``fan``:

* every ranked item carries a ``scope`` field (``test`` / ``production``);
* test-role rows are dropped from the headline ranking by default so a
  test fixture is no longer the unqualified #1;
* the drop is disclosed (``test_filtered`` / ``test_items`` /
  ``production_items``) so nothing is silently lost (Pattern-1-D /
  Pattern-2 lineage);
* ``--include-tests`` opts the test-role rows back into the ranking.

Mechanism mirrors ``tests/test_uses_cmd.py`` (production/test scope split).
"""

from __future__ import annotations

import pytest

from tests.conftest import (
    git_init,
    index_in_process,
    invoke_cli,
    parse_json_output,
)

# ---------------------------------------------------------------------------
# Fixture: a prod hub (moderate fan-in) + a test fixture with HIGHER fan-in.
# Without the split, the test fixture tops the ranking; with it, the prod
# symbol is the headline #1.
# ---------------------------------------------------------------------------


@pytest.fixture
def fan_split_project(tmp_path):
    """Project where a test-role symbol out-ranks a prod symbol on fan-in.

    Layout (project-root files + a ``tests/`` dir so ``is_test_file``
    classifies the latter):

        core.py            -- prod_hub(): called from 3 prod files.
        consumer_a/b/c.py  -- each calls prod_hub().
        tests/conftest.py  -- test_fixture(): called from 5 test files.
        tests/test_*.py    -- 5 test modules each calling test_fixture().

    test_fixture's fan-in (5) exceeds prod_hub's (3), so without the
    role split test_fixture would be the unqualified #1 fan-in.
    """
    proj = tmp_path / "fan_split_proj"
    proj.mkdir()
    (proj / ".gitignore").write_text(".roam/\n")

    (proj / "core.py").write_text("def prod_hub():\n    return 1\n")
    for name in ("consumer_a", "consumer_b", "consumer_c"):
        (proj / f"{name}.py").write_text(f"from core import prod_hub\n\n\ndef use_{name}():\n    return prod_hub()\n")

    tests_dir = proj / "tests"
    tests_dir.mkdir()
    (tests_dir / "conftest.py").write_text("def test_fixture():\n    return 2\n")
    for i in range(5):
        (tests_dir / f"test_mod_{i}.py").write_text(
            f"from tests.conftest import test_fixture\n\n\ndef test_case_{i}():\n    return test_fixture()\n"
        )

    git_init(proj)
    index_in_process(proj)
    return proj


def _items(data):
    return data.get("items", [])


# ---------------------------------------------------------------------------
# Headline ranking: prod symbol wins, test fixture is excluded by default.
# ---------------------------------------------------------------------------


class TestFanSymbolSplit:
    def test_test_fixture_not_unqualified_top(self, cli_runner, fan_split_project, monkeypatch):
        """The test-role fixture must not be the default #1 fan-in."""
        monkeypatch.chdir(fan_split_project)
        result = invoke_cli(cli_runner, ["fan", "symbol", "-n", "20"], cwd=fan_split_project, json_mode=True)
        data = parse_json_output(result, "fan")
        names = [it["name"] for it in _items(data)]
        assert "test_fixture" not in names, f"test-role fixture leaked into the default headline ranking: {names}"
        assert "prod_hub" in names, f"prod_hub missing from production ranking: {names}"

    def test_every_item_has_scope(self, cli_runner, fan_split_project, monkeypatch):
        """Each ranked item carries a closed-enum ``scope`` field (mirror of uses)."""
        monkeypatch.chdir(fan_split_project)
        result = invoke_cli(cli_runner, ["fan", "symbol", "-n", "20"], cwd=fan_split_project, json_mode=True)
        data = parse_json_output(result, "fan")
        items = _items(data)
        assert items, "expected at least one ranked item"
        for it in items:
            assert it.get("scope") in {"test", "production"}, f"item missing scope: {it}"
        # Default headline carries only production-scope items.
        assert all(it["scope"] == "production" for it in items)

    def test_summary_discloses_the_split(self, cli_runner, fan_split_project, monkeypatch):
        """The split is loud: test_filtered names the dropped test-role rows."""
        monkeypatch.chdir(fan_split_project)
        result = invoke_cli(cli_runner, ["fan", "symbol", "-n", "20"], cwd=fan_split_project, json_mode=True)
        data = parse_json_output(result, "fan")
        summary = data["summary"]
        assert summary.get("test_split") is True
        assert summary.get("include_tests") is False
        # The test fixture(s) were filtered out of the headline — disclosed,
        # not silently dropped (Pattern-1-D / Pattern-2).
        assert summary.get("test_filtered", 0) >= 1, summary
        assert summary.get("production_items", 0) >= 1, summary

    def test_include_tests_brings_back_test_role(self, cli_runner, fan_split_project, monkeypatch):
        """--include-tests re-admits test-role symbols into the ranking."""
        monkeypatch.chdir(fan_split_project)
        result = invoke_cli(
            cli_runner,
            ["fan", "symbol", "-n", "20", "--include-tests"],
            cwd=fan_split_project,
            json_mode=True,
        )
        data = parse_json_output(result, "fan")
        names = [it["name"] for it in _items(data)]
        assert "test_fixture" in names, f"--include-tests should show test_fixture: {names}"
        summary = data["summary"]
        assert summary.get("include_tests") is True
        assert summary.get("test_filtered", -1) == 0, "nothing filtered when tests included"
        assert summary.get("test_items", 0) >= 1


# ---------------------------------------------------------------------------
# File mode mirrors the same split.
# ---------------------------------------------------------------------------


class TestFanFileSplit:
    def test_file_items_have_scope(self, cli_runner, fan_split_project, monkeypatch):
        """File-mode items carry the same ``scope`` annotation."""
        monkeypatch.chdir(fan_split_project)
        result = invoke_cli(cli_runner, ["fan", "file", "-n", "30"], cwd=fan_split_project, json_mode=True)
        data = parse_json_output(result, "fan")
        items = _items(data)
        for it in items:
            assert it.get("scope") in {"test", "production"}, f"file item missing scope: {it}"
        # Default headline carries only production-scope files.
        assert all(it["scope"] == "production" for it in items)
        # Test files (tests/conftest.py, tests/test_mod_*.py) are dropped.
        paths = [it["path"] for it in items]
        assert not any(p.replace("\\", "/").startswith("tests/") for p in paths), paths

    def test_file_summary_discloses_split(self, cli_runner, fan_split_project, monkeypatch):
        """File mode discloses the same test_split / test_filtered fields."""
        monkeypatch.chdir(fan_split_project)
        result = invoke_cli(cli_runner, ["fan", "file", "-n", "30"], cwd=fan_split_project, json_mode=True)
        data = parse_json_output(result, "fan")
        summary = data["summary"]
        assert summary.get("test_split") is True
        assert summary.get("include_tests") is False
        assert "test_filtered" in summary


# ---------------------------------------------------------------------------
# Text-mode parity: the scope column + filter NOTE surface in plain output.
# ---------------------------------------------------------------------------


class TestFanTextParity:
    def test_text_has_scope_column(self, cli_runner, fan_split_project, monkeypatch):
        """Text output exposes a ``scope`` column so non-JSON consumers see it."""
        monkeypatch.chdir(fan_split_project)
        result = invoke_cli(cli_runner, ["fan", "symbol", "-n", "20"], cwd=fan_split_project)
        assert result.exit_code == 0
        assert "scope" in result.output
        # test_fixture must not appear as a headline row in text mode either.
        assert "test_fixture" not in result.output
