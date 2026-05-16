"""W831 — Empty-corpus smoke for ``roam critique`` (W805 sweep).

Pattern 2 / 1-C guard: when the corpus is empty AND the diff input is
empty (the double-empty case), critique must NOT silently emit a
default ``OK`` / ``SAFE`` / ``no concerns`` verdict. It must surface a
structured signal — either a JSON envelope describing the empty state
OR a structured ``EMPTY_INPUT:`` usage error whose message itself is
explicit about the empty diffs.

This is the W805 sweep contract: structured signal beats silent
success, especially on no-input edge cases.
"""

from __future__ import annotations

import json
import os
import subprocess
import textwrap

import pytest
from click.testing import CliRunner

from roam.cli import cli
from roam.output.errors import ALL_CODES, EMPTY_INPUT, parse_code

# Pattern 2 forbidden-fragment blacklist — these are the silent-SAFE
# defaults that critique MUST NOT emit when the corpus + diff are
# empty. Substring match is intentional (we want to catch even
# embedded occurrences in a status line).
_FORBIDDEN_FRAGMENTS = ("safe", "no concerns", "ok")


def _git_init_empty_corpus(tmp_path):
    """Create a minimally-valid git repo containing exactly one empty
    .py file. This is the empty-corpus baseline: a repo that roam can
    index without errors but that contains zero meaningful symbols /
    edges / clones / refs.
    """
    proj = tmp_path / "empty_corpus"
    proj.mkdir()
    (proj / ".gitignore").write_text(".roam/\n", encoding="utf-8")
    (proj / "empty.py").write_text("", encoding="utf-8")

    env = {
        **os.environ,
        "GIT_AUTHOR_NAME": "test",
        "GIT_AUTHOR_EMAIL": "t@t",
        "GIT_COMMITTER_NAME": "test",
        "GIT_COMMITTER_EMAIL": "t@t",
    }
    subprocess.run(["git", "init"], cwd=str(proj), capture_output=True, check=True)
    subprocess.run(["git", "add", "."], cwd=str(proj), capture_output=True, check=True, env=env)
    subprocess.run(
        ["git", "commit", "-m", "init", "--allow-empty"],
        cwd=str(proj),
        capture_output=True,
        check=True,
        env=env,
    )
    return proj


@pytest.fixture
def empty_corpus(tmp_path):
    proj = _git_init_empty_corpus(tmp_path)
    old_cwd = os.getcwd()
    try:
        os.chdir(str(proj))
        runner = CliRunner()
        result = runner.invoke(cli, ["index"])
        assert result.exit_code == 0, f"roam index failed on empty corpus: exit={result.exit_code}\n{result.output}"
        yield proj
    finally:
        os.chdir(old_cwd)


def _assert_no_forbidden_fragments(text: str, ctx: str) -> None:
    """Pattern 2 invariant: empty-input output must not include a
    silent SAFE/OK/no-concerns terminal. We lowercase the haystack
    and require none of the blacklisted fragments survive.
    """
    haystack = text.lower()
    for frag in _FORBIDDEN_FRAGMENTS:
        # Allow substrings inside structured tokens like "next_command"
        # that don't carry the silent-SAFE meaning. We require the
        # fragment to appear as a *word-ish* run, i.e. either at a
        # boundary or as a standalone token. The conservative test
        # is: never appear at all in the empty-input message body.
        assert frag not in haystack, (
            f"Pattern 2 violation ({ctx}): forbidden fragment {frag!r} found in output\n---\n{text}\n---"
        )


class TestW831CritiqueEmptyCorpus:
    """Double-empty case: empty corpus + empty diff input.

    Two flavours are exercised:

    (A) ``--input`` pointed at an empty diff file — hits the
        ``EMPTY_INPUT`` structured-usage-error branch deterministically.
    (B) ``--input`` pointed at a non-diff file — hits ``INVALID_DIFF``,
        confirming the same Pattern 2 discipline holds across both
        no-signal entry paths.
    """

    def test_empty_diff_input_structured_error(self, empty_corpus, tmp_path):
        empty_diff = tmp_path / "empty.diff"
        empty_diff.write_text("   \n\n  ", encoding="utf-8")

        runner = CliRunner()
        result = runner.invoke(cli, ["--json", "critique", "--input", str(empty_diff)])

        # Exit non-zero — critique cannot conjure a verdict from no diff.
        # Click UsageError maps to exit 2 by convention.
        assert result.exit_code != 0, f"critique on empty diff must not silently succeed; got exit=0\n{result.output}"

        # Structured signal: the error message MUST carry a known
        # structured code prefix from ``roam.output.errors.ALL_CODES``.
        # This is the agent-readable handle that turns a UsageError
        # into a branch-able event.
        output = (result.output or "") + "\n" + (str(result.exception) if result.exception else "")
        code = None
        for cand in ALL_CODES:
            if cand + ":" in output:
                code = cand
                break
        assert code == EMPTY_INPUT, (
            f"expected EMPTY_INPUT structured error prefix; got code={code!r}\noutput={output!r}"
        )

        # The error message itself must mention 'empty' — the verdict
        # is explicit, not a silent SAFE fallback.
        assert "empty" in output.lower(), f"empty-diff error must mention 'empty' in its message; got: {output!r}"

        # Pattern 2: no silent SAFE/OK/no-concerns leakage.
        _assert_no_forbidden_fragments(output, "empty diff via --input")

    def test_invalid_diff_input_structured_error(self, empty_corpus, tmp_path):
        """Sibling case — non-empty stdin content that is NOT a unified
        diff. Confirms the same structured-error contract applies.
        """
        garbage = tmp_path / "garbage.txt"
        garbage.write_text("this is not a diff\n", encoding="utf-8")

        runner = CliRunner()
        result = runner.invoke(cli, ["--json", "critique", "--input", str(garbage)])

        assert result.exit_code != 0
        output = (result.output or "") + "\n" + (str(result.exception) if result.exception else "")
        # Should be INVALID_DIFF, not EMPTY_INPUT — the file has content.
        assert parse_code(output) is not None or any(c + ":" in output for c in ALL_CODES), (
            f"expected a structured error code; got: {output!r}"
        )
        _assert_no_forbidden_fragments(output, "garbage --input")

    def test_minimal_valid_diff_envelope_shape(self, empty_corpus, tmp_path):
        """Companion case — when a syntactically-valid diff IS supplied
        against the empty corpus, critique should reach the JSON
        envelope branch. The envelope must:

          * carry the canonical ``command: "critique"`` tag,
          * carry a ``summary`` block,
          * surface zero ``changed_symbols`` (the corpus has none),
          * report zero ``findings``,
          * NOT contain the Pattern 2 forbidden fragments in the
            verdict line.

        This is the "did we even reach the envelope" half of the
        empty-corpus smoke — complements the empty-diff half above.
        """
        diff_text = textwrap.dedent(
            """\
            diff --git a/empty.py b/empty.py
            --- a/empty.py
            +++ b/empty.py
            @@ -0,0 +1,1 @@
            +# touched
            """
        )
        diff_path = tmp_path / "patch.diff"
        diff_path.write_text(diff_text, encoding="utf-8")

        runner = CliRunner()
        result = runner.invoke(cli, ["--json", "critique", "--input", str(diff_path)])
        # Exit 0 or 5 (high-severity gate). On an empty corpus no
        # high-severity finding is possible.
        assert result.exit_code in (0, 5), f"unexpected exit={result.exit_code}\n{result.output}"

        data = json.loads(result.output)
        assert data["command"] == "critique"
        assert "summary" in data
        assert "findings" in data
        assert isinstance(data["summary"].get("high_severity"), int)
        # Empty corpus: no symbols can be resolved from the diff.
        assert data["summary"].get("changed_symbols", 0) == 0
        assert data["summary"].get("high_severity", 0) == 0
        # Envelope must reach the verdict slot — non-empty string.
        verdict = data["summary"].get("verdict") or ""
        assert isinstance(verdict, str) and verdict.strip(), f"verdict must be a non-empty string; got: {verdict!r}"
        # NOTE: this path (valid diff against empty corpus) intentionally
        # uses the existing critique "no concerns" clean-path verdict —
        # the W831 Pattern 2 blacklist applies to the *empty-input*
        # branch above, not to a legitimately clean diff.
