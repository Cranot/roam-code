"""Tests for `roam complete` left-anchored prefix matching.

Pre-fix: the ``prefix`` argument promised prefix-match but the FTS5
search returned fuzzy/substring hits. The argument name lied — a
vocabulary breach.

Post-fix: matches are literal left-anchored prefix only.
``use`` -> ``useFoo``, ``useBar``. ``use`` MUST NOT match ``MyUseFoo``.
"""

from __future__ import annotations

import pytest

from tests.conftest import git_init, index_in_process, invoke_cli, parse_json_output


@pytest.fixture
def prefix_project(tmp_path):
    """Project with mixed prefix/substring naming for prefix-vs-substring tests.

    - ``useFoo`` / ``useBar`` — should match prefix ``use``.
    - ``MyUseFoo`` — should NOT match prefix ``use`` (substring only).
    """
    proj = tmp_path / "complete_prefix_proj"
    proj.mkdir()
    (proj / ".gitignore").write_text(".roam/\n")
    src = proj / "src"
    src.mkdir()
    (src / "hooks.py").write_text(
        "def useFoo():\n"
        '    """Hook foo."""\n'
        "    return 1\n"
        "\n"
        "def useBar():\n"
        '    """Hook bar."""\n'
        "    return 2\n"
        "\n"
        "def MyUseFoo():\n"
        '    """Has `use` as a substring but not at the start."""\n'
        "    return 3\n"
    )
    git_init(proj)
    index_in_process(proj)
    return proj


def _symbol_completions(data):
    """Pull the symbols list out of a complete envelope, defensive against shape drift."""
    results = data.get("results") or {}
    return set(results.get("symbols") or [])


class TestCompletePrefix:
    def test_prefix_matches_use_prefixed_symbols(self, cli_runner, prefix_project, monkeypatch):
        """``roam complete use`` returns ``useFoo`` and ``useBar``."""
        monkeypatch.chdir(prefix_project)
        result = invoke_cli(cli_runner, ["complete", "use"], cwd=prefix_project, json_mode=True)
        data = parse_json_output(result, "complete")
        symbols = _symbol_completions(data)
        assert "useFoo" in symbols, f"prefix `use` should match `useFoo`; got symbols: {symbols}"
        assert "useBar" in symbols, f"prefix `use` should match `useBar`; got symbols: {symbols}"

    def test_prefix_does_not_match_internal_substring(self, cli_runner, prefix_project, monkeypatch):
        """``MyUseFoo`` has ``use`` as a substring but not as a prefix.

        A literal-prefix matcher must reject it. This is the contract
        the ``prefix`` argument name promises.
        """
        monkeypatch.chdir(prefix_project)
        result = invoke_cli(cli_runner, ["complete", "use"], cwd=prefix_project, json_mode=True)
        data = parse_json_output(result, "complete")
        symbols = _symbol_completions(data)
        assert "MyUseFoo" not in symbols, (
            f"prefix `use` should NOT match `MyUseFoo` (substring, not prefix); got symbols: {symbols}"
        )

    def test_verdict_declares_prefix_mode(self, cli_runner, prefix_project, monkeypatch):
        """JSON envelope should declare ``match_mode == 'prefix'``.

        Anchors the contract in the wire format so downstream
        consumers can read what kind of matching they got.
        """
        monkeypatch.chdir(prefix_project)
        result = invoke_cli(cli_runner, ["complete", "use"], cwd=prefix_project, json_mode=True)
        data = parse_json_output(result, "complete")
        summary = data["summary"]
        assert summary.get("match_mode") == "prefix", f"expected summary.match_mode == 'prefix', got {summary!r}"
