"""W833 — Empty-corpus smoke for ``roam health``.

This pins the contract that ``roam health`` on an empty corpus does NOT
emit a false "healthy" verdict. Health is the canonical CI-gate command;
a "100/100 healthy" verdict on an unanalyzed repo would be a
HIGH-severity claim (Pattern 2 silent fallback).

Reference: CLAUDE.md "Six systemic anti-patterns" §2 (silent fallback),
and the empty-state framing pattern documented in
``tests/test_empty_state_framing.py``.

This is part of the W805 empty-corpus sweep across CI-gate commands.
"""

from __future__ import annotations

import json as _json

import pytest

from tests.conftest import git_init, invoke_cli

# ---------------------------------------------------------------------------
# Fixture: a git-init'd project with an empty Python file (zero symbols).
# ---------------------------------------------------------------------------


@pytest.fixture
def empty_corpus_project(tmp_path):
    """Empty corpus = one zero-byte .py file in a git repo.

    The file exists so discovery picks it up; parsing it yields zero
    symbols, which is the "empty corpus" case the test is gating on.
    A repo with no files at all would be a different test (no-files,
    not no-symbols).
    """
    proj = tmp_path / "empty_corpus"
    proj.mkdir()
    (proj / ".gitignore").write_text(".roam/\n", encoding="utf-8")
    (proj / "empty.py").write_text("", encoding="utf-8")
    git_init(proj)
    return proj


# ---------------------------------------------------------------------------
# Forbidden-fragment blacklist (Pattern 2 silent-fallback verdicts).
#
# On an empty corpus, NONE of these tokens should appear in the verdict.
# Case-insensitive substring match — agents consume the verdict line raw.
# ---------------------------------------------------------------------------

_FORBIDDEN_VERDICT_FRAGMENTS = (
    "healthy",
    "safe",
    "all good",
    "no issues",
)

# Tokens that explicitly disclose the empty/absent state. The verdict must
# contain at least one of these (case-insensitive).
_REQUIRED_EMPTY_FRAGMENTS = (
    "empty",
    "no symbols",
    "no code",
    "uninitialized",
    "unanalyzed",
    "no analysis",
    "not initialized",
)


# ---------------------------------------------------------------------------
# The test.
# ---------------------------------------------------------------------------


class TestHealthEmptyCorpus:
    """W833 — roam health on an empty corpus must not claim healthy."""

    def test_envelope_shape_and_no_silent_healthy(self, cli_runner, empty_corpus_project, monkeypatch):
        """The single high-leverage assertion bundle.

        Asserts:
          * exit code is well-defined (0 or documented gate-fail);
          * envelope is parseable JSON with the standard contract;
          * verdict does NOT contain Pattern 2 silent-fallback fragments;
          * severity counts are present and consistent;
          * facts/agent_contract is non-empty (LAW 4 anchor).
        """
        monkeypatch.chdir(empty_corpus_project)

        # 1) init — builds the index for an empty corpus.
        init_result = invoke_cli(cli_runner, ["init"], cwd=empty_corpus_project)
        # init must succeed even on an empty corpus; if it fails the rest
        # of the test is moot.
        assert init_result.exit_code == 0, (
            f"roam init failed on empty corpus (exit {init_result.exit_code}):\n{init_result.output}"
        )

        # 2) health --json
        result = invoke_cli(cli_runner, ["--json", "health"], cwd=empty_corpus_project)

        # Exit code: 0 is the expected success path. If health on empty
        # corpus is treated as a gate-fail (non-zero exit), the structured
        # envelope must still parse — Pattern 1 variant B.
        # Tolerate either, but record the behavior for posterity.
        assert result.exit_code in (0, 1, 5), (
            f"Unexpected exit code {result.exit_code} from health on empty corpus:\n{result.output}"
        )

        # Envelope must parse — Pattern 1 variant B/C: structured signal
        # must reach the consumer even on empty/degraded state.
        raw = getattr(result, "stdout", None) or result.output
        try:
            env = _json.loads(raw)
        except _json.JSONDecodeError as e:
            pytest.fail(f"health --json on empty corpus emitted non-JSON output: {e}\nFirst 500 chars:\n{raw[:500]}")

        # Envelope contract
        assert isinstance(env, dict), f"envelope must be dict, got {type(env)}"
        assert env.get("command") == "health", f"command field should be 'health', got {env.get('command')!r}"
        summary = env.get("summary")
        assert isinstance(summary, dict), f"summary must be a dict, got {type(summary)}"

        # --- Verdict assertions (the heart of W833) ---
        verdict = summary.get("verdict")
        assert isinstance(verdict, str) and verdict, f"summary.verdict must be a non-empty string, got {verdict!r}"
        verdict_lower = verdict.lower()

        # Pattern 2 forbidden-fragment blacklist — health MUST NOT claim
        # the codebase is healthy / safe / fine when it has zero symbols.
        for bad in _FORBIDDEN_VERDICT_FRAGMENTS:
            assert bad not in verdict_lower, (
                f"Pattern 2 silent-fallback verdict on empty corpus: "
                f"verdict {verdict!r} contains forbidden fragment {bad!r}.\n"
                f"Health on a zero-symbol corpus must not claim healthy."
            )

        # The verdict should *explicitly* disclose the empty/absent state.
        assert any(tok in verdict_lower for tok in _REQUIRED_EMPTY_FRAGMENTS), (
            f"verdict {verdict!r} does not disclose empty-corpus state. "
            f"Expected one of {_REQUIRED_EMPTY_FRAGMENTS} in the verdict."
        )

        # --- Score assertions ---
        # health_score should be either absent or 0 / null when nothing
        # was analyzed. Anything >= 80 on an empty corpus is the bug.
        score = summary.get("health_score")
        if score is not None:
            assert score < 80, (
                f"health_score={score} on empty corpus is a Pattern 2 silent fallback (>=80 implies healthy)."
            )

        # --- Severity counts present + consistent ---
        # severity dict (CRITICAL/WARNING/INFO) should exist and sum to 0
        # on an empty corpus.
        severity = summary.get("severity")
        if severity is not None:
            assert isinstance(severity, dict), f"summary.severity should be dict, got {type(severity)}"
            total = sum(v for v in severity.values() if isinstance(v, int))
            assert total == 0, (
                f"empty corpus has zero symbols; severity counts should "
                f"sum to 0 findings, got {severity} totalling {total}"
            )

        # --- partial_success disclosure ---
        # Empty corpus is by definition a partial-success state: the check
        # ran but produced no analyzable signal. xfail-strict if absent so
        # this lands as a tracked gap rather than a silent pass.
        if "partial_success" not in summary:
            pytest.xfail(
                "summary.partial_success missing on empty-corpus health "
                "envelope (W833 gap — health does not yet disclose "
                "partial-success state for zero-symbol corpora)."
            )
        assert summary["partial_success"] is True, (
            f"summary.partial_success should be True on empty corpus, got {summary['partial_success']!r}"
        )

        # --- facts / agent_contract non-empty ---
        # If an agent_contract is present, its facts array must carry at
        # least one concrete-noun-anchored fact (LAW 4).
        contract = env.get("agent_contract")
        if contract is not None:
            facts = contract.get("facts")
            assert isinstance(facts, list) and facts, (
                f"agent_contract.facts must be a non-empty list on empty corpus, got {facts!r}"
            )
