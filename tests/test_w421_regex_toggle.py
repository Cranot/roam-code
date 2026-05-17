"""W421 — regex toggle on ``roam history-grep`` and ``roam refs-text``.

Both commands previously treated input as literal strings only. The
underlying engines (git pickaxe ``-G`` and ripgrep without ``-F``)
already support regex, but it wasn't CLI-exposed. W421 adds
``-E`` / ``--regexp`` as an opt-in toggle.

Tests cover:
  - cmd_refs_text: alternation regex matches multiple identifiers in
    one pass under ``-E``, but matches NOTHING under the default literal
    mode (since "foo|bar" is not a literal substring of any line).
  - cmd_history_grep: ``-E`` switches git pickaxe from ``-S`` to ``-G``;
    an alternation regex finds commits introducing either alternative.
  - Default mode (no ``-E``) preserves fixed-string semantics so existing
    callers are unaffected.
  - ``-E`` + ``--polarity`` on history-grep still annotates introduced.
"""

from __future__ import annotations

import textwrap

import pytest

from tests.conftest import assert_json_envelope, invoke_cli, parse_json_output

# ---------------------------------------------------------------------------
# Fixture: a tiny project with two distinct identifiers an alternation regex
# can pick up in one pass.
# ---------------------------------------------------------------------------


@pytest.fixture
def regex_project(project_factory):
    return project_factory(
        {
            "src/__init__.py": "",
            "src/storage.py": textwrap.dedent(
                """\
                def write_session(key, value):
                    return setItem(key, value)

                def clear_session(key):
                    return removeItem(key)

                def setItem(k, v):  # local stub
                    return (k, v)

                def removeItem(k):  # local stub
                    return k

                def main():
                    write_session("a", "b")
                    clear_session("a")
                """
            ),
        }
    )


# ---------------------------------------------------------------------------
# refs-text: --regexp toggle
# ---------------------------------------------------------------------------


class TestRefsTextRegexToggle:
    def test_regexp_mode_matches_alternation(self, cli_runner, regex_project, monkeypatch):
        """With -E, `setItem|removeItem` covers BOTH identifiers in one pass."""
        monkeypatch.chdir(regex_project)
        result = invoke_cli(
            cli_runner,
            ["refs-text", "-E", "setItem|removeItem"],
            cwd=regex_project,
            json_mode=True,
        )
        data = parse_json_output(result, "refs-text")
        assert_json_envelope(data, "refs-text")
        # One target string was passed; engine returns matches for both setItem and removeItem
        results = data["results"]
        assert len(results) == 1
        assert results[0]["string"] == "setItem|removeItem"
        # Total references should be > 0 — both identifiers appear in src/storage.py
        assert results[0]["total"] > 0, f"Regex alternation should match both setItem and removeItem; got {results[0]}"

    def test_default_literal_mode_does_not_match_alternation(self, cli_runner, regex_project, monkeypatch):
        """Without -E, `setItem|removeItem` is a literal substring — no source line contains the pipe."""
        monkeypatch.chdir(regex_project)
        result = invoke_cli(
            cli_runner,
            ["refs-text", "setItem|removeItem"],
            cwd=regex_project,
            json_mode=True,
        )
        data = parse_json_output(result, "refs-text")
        results = data["results"]
        assert len(results) == 1
        # Literal mode: the alternation pipe is taken verbatim. No source line
        # contains the substring "setItem|removeItem".
        assert results[0]["total"] == 0, f"Default literal mode should NOT match alternation; got {results[0]}"
        assert results[0]["verdict"] == "SAFE-TO-REMOVE"

    def test_default_literal_mode_still_matches_plain_identifier(self, cli_runner, regex_project, monkeypatch):
        """Default fixed-string semantics preserved for plain identifiers."""
        monkeypatch.chdir(regex_project)
        result = invoke_cli(
            cli_runner,
            ["refs-text", "setItem"],
            cwd=regex_project,
            json_mode=True,
        )
        data = parse_json_output(result, "refs-text")
        assert data["results"][0]["total"] > 0


# ---------------------------------------------------------------------------
# history-grep: --regexp toggle
# ---------------------------------------------------------------------------


class TestHistoryGrepRegexToggle:
    def _make_project(self, project_factory):
        return project_factory(
            {
                "src/main.py": "def hello():\n    return 'placeholder'\n",
            },
            extra_commits=[
                (
                    {"src/main.py": "def hello():\n    return 'FOO_BEACON'\n"},
                    "introduce FOO_BEACON",
                ),
                (
                    {"src/main.py": "def hello():\n    return 'BAR_BEACON'\n"},
                    "introduce BAR_BEACON",
                ),
            ],
        )

    def test_regexp_mode_finds_both_alternatives(self, cli_runner, project_factory, monkeypatch):
        """With -E, an alternation regex finds commits introducing either FOO_BEACON or BAR_BEACON."""
        proj = self._make_project(project_factory)
        monkeypatch.chdir(proj)
        result = invoke_cli(
            cli_runner,
            ["history-grep", "-E", "FOO_BEACON|BAR_BEACON"],
            cwd=proj,
            json_mode=True,
        )
        data = parse_json_output(result, "history-grep")
        assert_json_envelope(data, "history-grep")
        commits = data["results"][0]["commits"]
        summaries = " ".join(c["summary"] for c in commits)
        # Both introducing commits should appear in pickaxe -G output
        assert "FOO_BEACON" in summaries and "BAR_BEACON" in summaries, (
            f"Regex alternation should pick up both commits; got {summaries!r}"
        )

    def test_default_literal_mode_does_not_match_alternation(self, cli_runner, project_factory, monkeypatch):
        """Without -E, `FOO_BEACON|BAR_BEACON` is a literal substring — no commit diff contains the pipe."""
        proj = self._make_project(project_factory)
        monkeypatch.chdir(proj)
        result = invoke_cli(
            cli_runner,
            ["history-grep", "FOO_BEACON|BAR_BEACON"],
            cwd=proj,
            json_mode=True,
        )
        data = parse_json_output(result, "history-grep")
        # Default -S looks for the literal pipe-bearing substring — nothing in this repo has that
        assert data["results"][0]["commits"] == []

    def test_default_literal_mode_still_finds_plain_string(self, cli_runner, project_factory, monkeypatch):
        """Default fixed-string semantics preserved for plain strings.

        Both the introducing commit AND the commit that removed FOO_BEACON
        (by replacing it with BAR_BEACON) change the count of "FOO_BEACON"
        in the working tree, so git pickaxe -S returns both. The literal
        mode is working as designed; the assertion just checks that the
        introducing commit is somewhere in the result set.
        """
        proj = self._make_project(project_factory)
        monkeypatch.chdir(proj)
        result = invoke_cli(
            cli_runner,
            ["history-grep", "FOO_BEACON"],
            cwd=proj,
            json_mode=True,
        )
        data = parse_json_output(result, "history-grep")
        commits = data["results"][0]["commits"]
        assert len(commits) >= 1
        summaries = " ".join(c["summary"] for c in commits)
        assert "FOO_BEACON" in summaries

    def test_regexp_combines_with_polarity(self, cli_runner, project_factory, monkeypatch):
        """W421 — -E and --polarity compose: regex matches still get introduced/removed tagging."""
        proj = self._make_project(project_factory)
        monkeypatch.chdir(proj)
        result = invoke_cli(
            cli_runner,
            ["history-grep", "-E", "FOO_BEACON|BAR_BEACON", "--polarity"],
            cwd=proj,
            json_mode=True,
        )
        data = parse_json_output(result, "history-grep")
        commits = data["results"][0]["commits"]
        polarities = {c.get("polarity") for c in commits}
        # At least one of the introducing commits should be polarity-tagged "introduced"
        assert "introduced" in polarities, (
            f"Expected at least one introduced commit under -E + --polarity; got {polarities}"
        )
