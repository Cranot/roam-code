"""W825 — Empty-corpus smoke for ``roam taint`` (W805 sweep).

Security-sensitive: a silent ``SAFE`` verdict on an empty corpus is a
HIGH-severity Pattern-2 silent-fallback (CLAUDE.md). The taint engine
MUST disclose whether the verdict reflects ``no rules loaded`` /
``no sources detected`` rather than claiming the code is clean.

Two empty-corpus paths are exercised:

* **Empty rules directory** — ``cmd_taint.py`` has an explicit early-return
  branch ``if not rules:`` that emits ``"No rules in <path>"``. This
  smoke-tests that the disclosure is wired into JSON mode AND that the
  forbidden silent-SAFE vocabulary does not appear.
* **Empty source corpus with built-in rules** — rules load, but the
  indexed graph has no symbols so no findings can result. The verdict
  must not collapse to a silent ``SAFE`` / ``secure`` / ``all clear``
  string; it must remain analytically truthful about the empty graph.
"""

from __future__ import annotations

import json
import os

from click.testing import CliRunner

from roam.cli import cli

# Mirror W823's blacklist: any of these substrings in the verdict on an
# empty corpus is a silent-SAFE Pattern-2 violation.
_FORBIDDEN_VERDICT_FRAGMENTS = (
    "safe",
    "secure",
    "no taint",
    "all clear",
)

# Acceptable disclosure fragments. The verdict must contain at least one
# of these to prove the command disclosed *why* it found nothing.
_ACCEPTABLE_DISCLOSURE_FRAGMENTS = (
    "no rules",
    "no sources",
    "no symbols",
    "empty",
    "0 rule",
    "not indexed",
)


def _assert_no_silent_safe(verdict: str) -> None:
    lo = verdict.lower()
    for frag in _FORBIDDEN_VERDICT_FRAGMENTS:
        assert frag not in lo, (
            f"Silent-SAFE Pattern-2 violation: empty-corpus verdict "
            f"contains forbidden fragment {frag!r}: {verdict!r}"
        )


def _assert_envelope_shape(data: dict) -> None:
    assert data.get("command") == "taint"
    summary = data.get("summary")
    assert isinstance(summary, dict), f"missing summary: {data!r}"
    verdict = summary.get("verdict")
    assert isinstance(verdict, str) and verdict, f"missing verdict: {summary!r}"
    # W817: partial_success must be disclosed on a degraded run (no
    # rules / no corpus). If it's missing, the envelope is silently
    # claiming a fully-resolved success.
    assert "partial_success" in summary, (
        "summary.partial_success not present — empty-corpus run silently "
        "claims fully-resolved success (Pattern-1 variant D)"
    )
    # agent_contract.facts is auto-derived from the verdict; must be
    # non-empty so agents on bounded budgets still get the disclosure.
    contract = data.get("agent_contract") or {}
    facts = contract.get("facts") or []
    assert facts, f"agent_contract.facts is empty: {contract!r}"


def _make_empty_corpus(tmp_path):
    """Create a git-initialised project containing one empty .py file."""
    proj = tmp_path / "empty_proj"
    proj.mkdir()
    (proj / ".gitignore").write_text(".roam/\n", encoding="utf-8")
    (proj / "empty.py").write_text("", encoding="utf-8")
    import subprocess

    env = {
        **os.environ,
        "GIT_AUTHOR_NAME": "test",
        "GIT_AUTHOR_EMAIL": "t@t",
        "GIT_COMMITTER_NAME": "test",
        "GIT_COMMITTER_EMAIL": "t@t",
    }
    subprocess.run(["git", "init"], cwd=str(proj), capture_output=True)
    subprocess.run(["git", "add", "."], cwd=str(proj), capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "init", "--allow-empty"],
        cwd=str(proj),
        capture_output=True,
        env=env,
    )
    return proj


class TestTaintEmptyCorpus:
    def test_empty_rules_dir_discloses_no_rules(self, tmp_path):
        """An empty ``--rules-dir`` must produce a ``No rules`` verdict.

        This is the explicit ``if not rules:`` branch in ``cmd_taint.py``.
        """
        proj = _make_empty_corpus(tmp_path)
        empty_rules = tmp_path / "empty_rules_dir"
        empty_rules.mkdir()
        old_cwd = os.getcwd()
        try:
            os.chdir(str(proj))
            runner = CliRunner()
            init_res = runner.invoke(cli, ["init"], catch_exceptions=False)
            assert init_res.exit_code == 0, init_res.output
            res = runner.invoke(
                cli,
                ["--json", "taint", "--rules-dir", str(empty_rules)],
                catch_exceptions=False,
            )
            assert res.exit_code == 0, res.output
            data = json.loads(res.output)
        finally:
            os.chdir(old_cwd)

        _assert_envelope_shape(data)
        verdict = data["summary"]["verdict"]
        _assert_no_silent_safe(verdict)
        # Positive disclosure — the verdict must name the absent state.
        lo = verdict.lower()
        assert any(frag in lo for frag in _ACCEPTABLE_DISCLOSURE_FRAGMENTS), (
            f"verdict does not disclose the empty-rules state: {verdict!r}"
        )
        # Structured signal: rules count must read as zero.
        assert data["summary"].get("rules") == 0

    def test_empty_corpus_with_default_rules_does_not_silent_safe(self, tmp_path):
        """An empty source corpus + built-in rules must not silent-SAFE.

        The default-rules path runs the BFS against an effectively empty
        graph. The verdict must not claim the code is clean / safe /
        ``no taint`` — that is the canonical Pattern-2 silent fallback.
        """
        proj = _make_empty_corpus(tmp_path)
        old_cwd = os.getcwd()
        try:
            os.chdir(str(proj))
            runner = CliRunner()
            init_res = runner.invoke(cli, ["init"], catch_exceptions=False)
            assert init_res.exit_code == 0, init_res.output
            res = runner.invoke(cli, ["--json", "taint"], catch_exceptions=False)
            assert res.exit_code == 0, res.output
            data = json.loads(res.output)
        finally:
            os.chdir(old_cwd)

        _assert_envelope_shape(data)
        verdict = data["summary"]["verdict"]
        _assert_no_silent_safe(verdict)
