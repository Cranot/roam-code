"""W805-C - Empty-corpus Pattern-2 smoke for ``roam oracle`` (W805 sweep).

Third-in-batch continuation of the W802-W836 Pattern-2 audit beyond the
original cohort. ``cmd_oracle`` already carries the W1079 closest-match
fix for unknown oracle names; the W905 lazy-import false-hedge was
verified genuine (a deferred-to-first-use import inside an exception
block, not a cycle hedge). The remaining unverified axis is the empty-
corpus / no-data branch on each oracle's pure-implementation function.

Oracle inventory (5 boolean oracles + 1 batch runner):
- ``symbol-exists``         (cmd_oracle.py:135-151)
- ``route-exists``          (cmd_oracle.py:203-258)
- ``is-test-only``          (cmd_oracle.py:261-340)
- ``is-reachable-from-entry`` (cmd_oracle.py:343-453)
- ``is-clone-of``           (cmd_oracle.py:456-485)

W978 first-hypothesis probe (run BEFORE writing tests):

REAL BUG (Pattern-2 silent SAFE) FOUND on ``route-exists`` at
``cmd_oracle.py:244-250``. On an empty corpus the code returns:

    OracleResult(
        False,                          # <-- value=False
        "no route-handler symbols indexed; try `roam ws resolve` first",
        "indeterminate_no_data",        # <-- reason_class=indeterminate!
        "low",                          # <-- confidence=low!
    )

The ``reason_class`` and ``confidence`` say "we don't know" but
``value=False`` collapses to ``verdict="false"`` in ``_emit`` (line
512-516). An agent reading only ``summary.verdict`` cannot distinguish
"definitively no route" from "no data was indexed to check against".
Compare with the sibling ``is-test-only`` orphan branch
(cmd_oracle.py:320-325) which returns ``OracleResult(None, ...)`` and
emits ``verdict="indeterminate"`` correctly.

The other four oracles are LEGITIMATELY answerable on empty corpus
(W978 "first hypothesis is often wrong" applied — empty graph really
does mean "no symbol named foo" / "no clone siblings" / etc.):

- ``symbol-exists`` on missing name: ``value=False`` is correct (no
  such symbol exists, full stop).
- ``is-test-only`` / ``is-reachable-from-entry`` on missing name:
  ``value=False`` is correct (cannot be test-only / reachable if it
  doesn't exist).
- ``is-clone-of`` on missing name: ``value=False`` is correct (no
  persisted clone pairs reference it).

The bug is narrowly: the ``route-exists`` "no route-handler symbols
indexed" branch is the ONE inconsistent shape — reason_class +
confidence disclose indeterminate but value+verdict say false.

PIN STRATEGY (W978 + accumulate-only constraint):

1. SMOKE (always-on): no crash + envelope + verdict + LAW 6 standalone
   + LAW 4 facts. These cover all five oracles uniformly.
2. PATTERN-2 PIN (xfail-strict): the ``route-exists`` value/verdict vs
   reason_class/confidence inconsistency. One xfail = one canonical bug.
3. Regression baseline: W1079 closest-match still works.

The fix-forward (separate wave) is one-line: change
``OracleResult(False, ...)`` to ``OracleResult(None, ...)`` on
cmd_oracle.py:246 so verdict becomes ``"indeterminate"`` matching the
disclosed reason_class.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from click.testing import CliRunner

sys.path.insert(0, str(Path(__file__).parent))
from conftest import index_in_process  # noqa: E402

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _git_init_committed(repo: Path) -> None:
    """Init a git repo and commit current files. No further history."""
    env = {
        **os.environ,
        "GIT_AUTHOR_NAME": "test",
        "GIT_AUTHOR_EMAIL": "t@t",
        "GIT_COMMITTER_NAME": "test",
        "GIT_COMMITTER_EMAIL": "t@t",
    }
    subprocess.run(["git", "init", "-q"], cwd=str(repo), capture_output=True, env=env, check=True)
    subprocess.run(["git", "config", "user.email", "t@t.com"], cwd=str(repo), capture_output=True, env=env)
    subprocess.run(["git", "config", "user.name", "test"], cwd=str(repo), capture_output=True, env=env)
    subprocess.run(["git", "add", "."], cwd=str(repo), capture_output=True, env=env)
    subprocess.run(
        ["git", "commit", "-q", "-m", "init"],
        cwd=str(repo),
        capture_output=True,
        env=env,
        check=True,
    )


@pytest.fixture
def empty_corpus(tmp_path, monkeypatch):
    """A git repo with a single empty .py file.

    The indexer runs cleanly but populates zero function/class/method
    symbols, zero edges, zero entry points. Every oracle is forced down
    its empty-data path.
    """
    repo = tmp_path / "empty-oracle-repo"
    repo.mkdir()
    (repo / ".gitignore").write_text(".roam/\n", encoding="utf-8")
    (repo / "empty.py").write_text("", encoding="utf-8")
    _git_init_committed(repo)
    monkeypatch.chdir(repo)
    out, rc = index_in_process(repo, "--force")
    assert rc == 0, f"roam index failed:\n{out}"
    return repo


@pytest.fixture
def clean_corpus(tmp_path, monkeypatch):
    """A git repo with a real function + caller for the happy-path
    positive-coverage assertion."""
    repo = tmp_path / "clean-oracle-repo"
    repo.mkdir()
    (repo / ".gitignore").write_text(".roam/\n", encoding="utf-8")
    (repo / "auth.py").write_text(
        "def handle_login(user):\n    return user\n\ndef main():\n    return handle_login('alice')\n",
        encoding="utf-8",
    )
    _git_init_committed(repo)
    monkeypatch.chdir(repo)
    out, rc = index_in_process(repo, "--force")
    assert rc == 0, f"roam index failed:\n{out}"
    return repo


# ---------------------------------------------------------------------------
# Invocation helpers
# ---------------------------------------------------------------------------


def _invoke_oracle(*subargs, json_mode: bool = True):
    """Run ``roam [--json] oracle <subargs>`` in-process and return result."""
    from roam.cli import cli

    runner = CliRunner()
    args: list[str] = []
    if json_mode:
        args.append("--json")
    args.append("oracle")
    args.extend(subargs)
    return runner.invoke(cli, args, catch_exceptions=False)


def _parse_envelope(result):
    """Parse the runner's stdout as a JSON envelope."""
    raw = result.output.strip()
    assert raw.startswith("{"), f"expected JSON envelope, got:\n{result.output}"
    return json.loads(raw)


# Oracle kinds we sweep for the parametrized smoke tests. Each entry is
# ``(subcommand, args_after_subcommand)``. ``args_after_subcommand`` is a
# missing-name / missing-path argument that exercises the empty-corpus
# branch of the underlying oracle implementation.
_ORACLE_KINDS: list[tuple[str, list[str]]] = [
    ("symbol-exists", ["zzMissingSymbol"]),
    ("route-exists", ["/api/missing"]),
    ("is-test-only", ["zzMissingSymbol"]),
    ("is-reachable-from-entry", ["zzMissingSymbol"]),
    ("is-clone-of", ["zzMissingSymbol"]),
]


# ---------------------------------------------------------------------------
# SMOKE (always-on)
# ---------------------------------------------------------------------------


class TestOracleEmptyCorpusSmoke:
    """Pattern-2 always-emit baseline assertions across all five oracles."""

    @pytest.mark.parametrize("subcmd,oargs", _ORACLE_KINDS)
    def test_empty_corpus_no_crash(self, empty_corpus, subcmd, oargs):
        """Each oracle exits 0 on empty corpus (no crash, no SystemExit)."""
        result = _invoke_oracle(subcmd, *oargs, json_mode=True)
        assert result.exit_code == 0, (
            f"oracle {subcmd} exit {result.exit_code} on empty corpus; output:\n{result.output}"
        )

    @pytest.mark.parametrize("subcmd,oargs", _ORACLE_KINDS)
    def test_empty_corpus_envelope_has_verdict(self, empty_corpus, subcmd, oargs):
        """Every oracle emits ``summary.verdict`` non-empty (Pattern-2
        always-emit). The verdict is one of ``true | false | indeterminate``
        per the tri-state contract."""
        result = _invoke_oracle(subcmd, *oargs, json_mode=True)
        envelope = _parse_envelope(result)
        assert envelope["command"] == f"oracle:{subcmd}", envelope["command"]
        summary = envelope.get("summary") or {}
        verdict = summary.get("verdict") or ""
        assert isinstance(verdict, str) and verdict, (
            f"oracle {subcmd}: summary.verdict must be non-empty string; got {verdict!r}"
        )
        assert verdict in ("true", "false", "indeterminate"), (
            f"oracle {subcmd}: verdict {verdict!r} not in closed enum (true|false|indeterminate)"
        )

    @pytest.mark.parametrize("subcmd,oargs", _ORACLE_KINDS)
    def test_empty_corpus_law6_verdict_standalone(self, empty_corpus, subcmd, oargs):
        """LAW 6: verdict line is single-line ASCII; works without any
        other field. Reads as a meaningful tri-state token."""
        result = _invoke_oracle(subcmd, *oargs, json_mode=True)
        envelope = _parse_envelope(result)
        verdict = envelope["summary"]["verdict"]
        assert "\n" not in verdict, f"verdict has embedded newline: {verdict!r}"
        assert verdict.isascii(), f"verdict not plain ASCII: {verdict!r}"

    @pytest.mark.parametrize("subcmd,oargs", _ORACLE_KINDS)
    def test_empty_corpus_partial_success_present(self, empty_corpus, subcmd, oargs):
        """``summary.partial_success`` is present (W817 auto-inject).

        The current contract auto-injects ``partial_success=False`` when
        the oracle doesn't set it. The key must exist so consumers can
        branch on it; we assert presence, not the bool value (the bool
        value is the subject of the Pattern-2 pin below).
        """
        result = _invoke_oracle(subcmd, *oargs, json_mode=True)
        envelope = _parse_envelope(result)
        assert "partial_success" in envelope["summary"], (
            f"oracle {subcmd}: summary.partial_success key missing; got {list(envelope['summary'].keys())}"
        )

    @pytest.mark.parametrize("subcmd,oargs", _ORACLE_KINDS)
    def test_empty_corpus_agent_contract_facts_present(self, empty_corpus, subcmd, oargs):
        """``agent_contract.facts`` is a non-empty list (LAW 4 anchoring +
        Pattern-2 always-emit). ``json_envelope`` auto-derives facts from
        summary keys when the caller doesn't override them."""
        result = _invoke_oracle(subcmd, *oargs, json_mode=True)
        envelope = _parse_envelope(result)
        contract = envelope.get("agent_contract") or {}
        facts = contract.get("facts") or []
        assert isinstance(facts, list) and facts, (
            f"oracle {subcmd}: agent_contract.facts must be non-empty; got {facts!r}"
        )

    @pytest.mark.parametrize("subcmd,oargs", _ORACLE_KINDS)
    def test_empty_corpus_reason_class_disclosed(self, empty_corpus, subcmd, oargs):
        """Every oracle emits ``summary.reason_class`` (closed-enum tag).
        This is the Pattern-2 disclosure axis that distinguishes
        ``definitive_no`` from ``indeterminate_no_data`` even when
        ``verdict="false"``.
        """
        result = _invoke_oracle(subcmd, *oargs, json_mode=True)
        envelope = _parse_envelope(result)
        rc = envelope["summary"].get("reason_class")
        assert rc and isinstance(rc, str), f"oracle {subcmd}: summary.reason_class missing or empty; got {rc!r}"


# ---------------------------------------------------------------------------
# W978 first-hypothesis SANITY: clean-corpus positives
# ---------------------------------------------------------------------------


class TestOracleCleanCorpusPositives:
    """Happy-path: the oracles emit real answers when symbols + edges
    exist. Counterpart to the empty-corpus smoke — proves the empty path
    is empty by virtue of corpus state, not by virtue of a broken oracle."""

    def test_clean_corpus_symbol_exists_emits_true(self, clean_corpus):
        result = _invoke_oracle("symbol-exists", "handle_login", json_mode=True)
        assert result.exit_code == 0, result.output
        envelope = _parse_envelope(result)
        assert envelope["summary"]["verdict"] == "true"
        assert envelope["summary"]["value"] is True

    def test_clean_corpus_symbol_exists_missing_emits_false(self, clean_corpus):
        """The definitive-no branch: corpus has symbols but not THIS one."""
        result = _invoke_oracle("symbol-exists", "zzMissingForSure", json_mode=True)
        assert result.exit_code == 0, result.output
        envelope = _parse_envelope(result)
        # Definitive no — verdict + reason_class agree (no Pattern-2 shape).
        assert envelope["summary"]["verdict"] == "false"
        assert envelope["summary"]["reason_class"] == "definitive_no"
        assert envelope["summary"]["confidence"] == "high"


# ---------------------------------------------------------------------------
# W1079 regression baseline
# ---------------------------------------------------------------------------


class TestUnknownOracleClosestMatch:
    """W1079 regression baseline: unknown oracle names in batch mode
    still produce difflib closest-match suggestions."""

    def test_unknown_oracle_name_w1079_intact_typo(self, empty_corpus):
        """``symbol_exists`` (underscore typo) → ``symbol-exists`` suggestion."""
        runner = CliRunner()
        from roam.cli import cli

        payload = json.dumps({"oracle": "symbol_exists", "args": {"name": "foo"}}) + "\n"
        result = runner.invoke(cli, ["--json", "oracle", "batch", "--input", "-"], input=payload)
        assert result.exit_code == 0, result.output
        env = json.loads(result.output)
        rows = env["results"]
        assert len(rows) == 1
        row = rows[0]
        assert "error" in row
        assert row["did_you_mean"] == ["symbol-exists"]

    def test_unknown_oracle_name_no_close_match_empty_list(self, empty_corpus):
        """Unrelated input → empty ``did_you_mean`` list, not absent."""
        runner = CliRunner()
        from roam.cli import cli

        payload = json.dumps({"oracle": "zzz_unrelated_garbage", "args": {"name": "x"}}) + "\n"
        result = runner.invoke(cli, ["--json", "oracle", "batch", "--input", "-"], input=payload)
        assert result.exit_code == 0, result.output
        env = json.loads(result.output)
        row = env["results"][0]
        assert "error" in row
        assert row["did_you_mean"] == []


# ---------------------------------------------------------------------------
# PATTERN-2 PIN (xfail-strict): the route-exists value/verdict inconsistency
# ---------------------------------------------------------------------------


@pytest.mark.xfail(
    strict=True,
    reason=(
        "W805-C REAL BUG: cmd_oracle.py:244-250 ``route-exists`` no-route "
        "branch returns OracleResult(value=False, reason_class="
        "'indeterminate_no_data', confidence='low'). _emit collapses "
        "value=False to verdict='false', so an agent reading only "
        "summary.verdict cannot distinguish 'definitively no route' from "
        "'no route-handler symbols indexed to check against'. The sibling "
        "is-test-only orphan branch (cmd_oracle.py:320-325) returns "
        "OracleResult(value=None, ...) and emits verdict='indeterminate' "
        "correctly. Fix: cmd_oracle.py:246 OracleResult(False, ...) -> "
        "OracleResult(None, ...). Separate fix wave."
    ),
)
def test_oracle_no_data_distinguishable_from_false_route_exists(empty_corpus):
    """Pin: ``route-exists`` on empty corpus must NOT emit a verdict
    indistinguishable from a definitive no.

    The bug is the value/verdict vs reason_class/confidence
    inconsistency: the metadata fields disclose indeterminate, but the
    primary verdict says false. An agent prompt-cached on verdict alone
    receives the wrong signal.

    Acceptance: when ``reason_class == 'indeterminate_no_data'``, the
    verdict must NOT be ``"false"`` — it must be ``"indeterminate"`` so
    the two axes agree.
    """
    result = _invoke_oracle("route-exists", "/api/missing", json_mode=True)
    envelope = _parse_envelope(result)
    summary = envelope["summary"]
    rc = summary.get("reason_class") or ""
    verdict = summary.get("verdict") or ""
    # The Pattern-2 silent-SAFE shape: indeterminate reason_class but a
    # definitive verdict. The fix collapses one or the other; we assert
    # the agreement.
    if "indeterminate" in rc:
        assert verdict == "indeterminate", (
            f"route-exists: reason_class={rc!r} discloses indeterminate "
            f"but verdict={verdict!r} reads as definitive — Pattern-2 "
            "silent SAFE shape."
        )


# ---------------------------------------------------------------------------
# W978 negative-control (sanity): the OTHER oracles are legitimately
# answerable on empty corpus when the symbol/path is genuinely missing.
# These tests document that the route-exists bug is a NARROW Pattern-2
# inconsistency, not a class-wide oracle defect.
# ---------------------------------------------------------------------------


class TestEmptyCorpusLegitimateAnswers:
    """Confirms that ``symbol-exists`` / ``is-test-only`` /
    ``is-reachable-from-entry`` / ``is-clone-of`` on a missing name with
    an empty corpus emit consistent ``(verdict, reason_class)`` pairs —
    i.e. they are NOT Pattern-2 silent SAFE."""

    def test_symbol_exists_consistent(self, empty_corpus):
        """Missing symbol + empty corpus: verdict=false + reason_class=
        definitive_no. The two axes agree (definitive answer)."""
        result = _invoke_oracle("symbol-exists", "zzNope", json_mode=True)
        envelope = _parse_envelope(result)
        s = envelope["summary"]
        assert s["verdict"] == "false"
        assert s["reason_class"] == "definitive_no"
        assert s["confidence"] == "high"

    def test_is_test_only_missing_consistent(self, empty_corpus):
        """Missing symbol: verdict=false + reason_class=definitive_no.
        Cannot be test-only if it doesn't exist."""
        result = _invoke_oracle("is-test-only", "zzNope", json_mode=True)
        envelope = _parse_envelope(result)
        s = envelope["summary"]
        assert s["verdict"] == "false"
        assert s["reason_class"] == "definitive_no"

    def test_is_reachable_missing_consistent(self, empty_corpus):
        """Missing symbol: verdict=false + reason_class=definitive_no.
        Cannot be reachable if it doesn't exist."""
        result = _invoke_oracle("is-reachable-from-entry", "zzNope", json_mode=True)
        envelope = _parse_envelope(result)
        s = envelope["summary"]
        assert s["verdict"] == "false"
        assert s["reason_class"] == "definitive_no"

    def test_is_clone_of_missing_consistent(self, empty_corpus):
        """Missing symbol + no clone_pairs rows: verdict=false +
        reason_class=definitive_no. Either there's no clone table (handled
        by the OperationalError branch) or no matching qname (handled by
        the count==0 branch). Both paths are internally consistent."""
        result = _invoke_oracle("is-clone-of", "zzNope", json_mode=True)
        envelope = _parse_envelope(result)
        s = envelope["summary"]
        # On the empty-corpus path, the clone_pairs table either exists
        # (post-W414b schema) or doesn't (older). Either is fine — we
        # assert no value/verdict inconsistency.
        rc = s["reason_class"]
        verdict = s["verdict"]
        if rc == "indeterminate_no_data":
            assert verdict == "indeterminate", f"is-clone-of: rc={rc!r} but verdict={verdict!r}"
        elif rc == "definitive_no":
            assert verdict == "false", f"is-clone-of: rc={rc!r} but verdict={verdict!r}"
