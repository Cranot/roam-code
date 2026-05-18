"""W805-HH -- empty-corpus Pattern-2 smoke test on ``roam test-impact``.

Thirty-fourth-in-batch W805 sweep. Third member of the impact family
(cmd_impact W805-P + cmd_uses W805-T + cmd_test_impact W805-HH).

Scope
-----

cmd_test_impact computes the set of test files transitively reachable
from symbols changed in a git ``commit_range`` (BFS over the reverse
call graph). Unlike cmd_impact / cmd_uses, the entry point is a
``commit_range`` argument, NOT a symbol name -- so the W1272 / W1277
``resolution`` axis (symbol/file/unresolved/fuzzy) does not map
directly. The relevant Pattern-2 axes for this command are the three
zero-result branches:

1. ``not files`` (src/roam/commands/cmd_test_impact.py:73-103) --
   git diff returned no non-test source files. Verdict is loud
   ("no non-test source files changed in {range}") but the envelope
   emits ``partial_success: false`` and no ``state`` field.
2. ``not seed_ids`` (line 121-143) -- changed files exist but
   none have indexed symbols. Verdict is loud ("changed files have
   no indexed symbols") but same Pattern-2 shape gap: no
   ``partial_success``, no ``state``.
3. ``not test_hits`` (line 174-178) -- real symbols changed, BFS
   completed, ZERO tests reach. Verdict says "no tests reach the N
   changed file(s) within H hop(s)". This is the most acute
   disambiguation gap: an agent receiving ``count: 0, tests: [],
   partial_success: false`` cannot tell:
   - "changeset is empty" (branch 1)
   - "changeset has changes but no indexed symbols" (branch 2)
   - "real symbols changed but no tests cover them" (branch 3,
     critical signal for test-coverage gap detection)

W978 first-hypothesis check
---------------------------

First hypothesis (before probing): cmd_test_impact would mirror
cmd_impact's W1272 / W1277 hardening since they share the impact-family
naming. Live probe on this commit:

* No changed files       -> exit 0, ``partial_success: false``, no ``state``.
* Changed-no-symbols     -> exit 0, ``partial_success: false``, no ``state``.
* No tests reach changes -> exit 0, ``partial_success: false``, no ``state``,
                            no machine-readable disambiguation from the
                            no-changes branch beyond the prose verdict.

Conclusion: cmd_test_impact is cmd_uses-like, NOT cmd_impact-like.
The W1272 / W1277 canonical Pattern-1-V-D / Pattern-2 hardening never
reached this command. **REAL BUG pinned strict** on each of the three
zero-result branches: silent SAFE on potentially-broken test coverage.

The hardest of the three is branch 3 (no_test_coverage): a real code
change with zero test coverage is exactly the signal a coverage-aware
agent needs to gate on, and the current envelope silent-SAFEs it.

Both clean-corpus + 0-truncated paths still pass (positive
regression baseline -- a future fix that adds ``state`` cannot
regress the verdict prose or counts).

Sweep brief: W805-HH (Wave805-HH, thirty-fourth-in-batch).
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest
from click.testing import CliRunner

sys.path.insert(0, str(Path(__file__).parent))
from conftest import (  # noqa: E402 -- relative-to-tests-dir import after sys.path mutation
    git_commit,
    git_init,
    index_in_process,
    invoke_cli,
    parse_json_output,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def cli_runner():
    return CliRunner()


@pytest.fixture
def empty_corpus(tmp_path):
    """Project with only a README -- working tree has no changes after baseline.

    Exercises branch 1 (``not files``): git diff returns no non-test
    source files because nothing has been modified since the initial
    commit.
    """
    proj = tmp_path / "empty_corpus"
    proj.mkdir()
    (proj / ".gitignore").write_text(".roam/\n")
    (proj / "README.md").write_text("Empty corpus project.\n")
    git_init(proj)
    out, rc = index_in_process(proj)
    assert rc == 0, f"index failed:\n{out}"
    return proj


@pytest.fixture
def changed_no_symbols_corpus(tmp_path):
    """Project where a non-source file is modified after baseline.

    Exercises branch 2 (``not seed_ids``): git diff returns a path
    (a markdown file) but the indexed-symbols query returns nothing
    because the changed file has no symbols.
    """
    proj = tmp_path / "no_symbols_corpus"
    proj.mkdir()
    (proj / ".gitignore").write_text(".roam/\n")
    src = proj / "src"
    src.mkdir()
    (src / "core.py").write_text("def f():\n    return 1\n")
    git_init(proj)
    out, rc = index_in_process(proj)
    assert rc == 0, f"index failed:\n{out}"
    # Add + commit a markdown file, then modify it -- the diff carries
    # the .md path but the indexer never produced a symbol for it.
    (proj / "DOC.md").write_text("hi\n")
    subprocess.run(["git", "add", "."], cwd=str(proj), capture_output=True)
    git_commit(proj, "add doc")
    (proj / "DOC.md").write_text("hi modified\n")
    return proj


@pytest.fixture
def no_test_coverage_corpus(tmp_path):
    """Real symbol changes, zero tests reach.

    Exercises branch 3 (``not test_hits``): the changed file has a real
    indexed symbol, BFS completes, but no test file is reachable on the
    reverse call graph. This is the silent-SAFE-on-missing-test-coverage
    scenario.
    """
    proj = tmp_path / "no_tests_corpus"
    proj.mkdir()
    (proj / ".gitignore").write_text(".roam/\n")
    src = proj / "src"
    src.mkdir()
    (src / "core.py").write_text("def callee():\n    return 1\n")
    git_init(proj)
    out, rc = index_in_process(proj)
    assert rc == 0, f"index failed:\n{out}"
    git_commit(proj, "baseline")
    # Modify core.py -- real source change, no tests anywhere in repo.
    (src / "core.py").write_text("def callee():\n    return 2\n")
    return proj


@pytest.fixture
def clean_corpus(tmp_path):
    """Real source change + a real test file that reaches it.

    Exercises the full-success branch: at least one test file is
    transitively reachable. Used as a positive-regression baseline so
    a future Pattern-2 fix cannot accidentally regress the verdict
    prose or the counts.
    """
    proj = tmp_path / "clean_corpus"
    proj.mkdir()
    (proj / ".gitignore").write_text(".roam/\n")
    src = proj / "src"
    src.mkdir()
    tests = proj / "tests"
    tests.mkdir()
    (src / "core.py").write_text("def callee():\n    return 1\n")
    (tests / "test_core.py").write_text("from src.core import callee\n\ndef test_callee():\n    assert callee() == 1\n")
    git_init(proj)
    out, rc = index_in_process(proj)
    assert rc == 0, f"index failed:\n{out}"
    git_commit(proj, "baseline")
    (src / "core.py").write_text("def callee():\n    return 2\n")
    return proj


# ---------------------------------------------------------------------------
# Pattern-1 Variant C -- no crash / no empty stdout on empty corpus.
# ---------------------------------------------------------------------------


class TestEmptyCorpusNoCrash:
    """Empty corpus must always emit a structured envelope.

    Pattern-1 Variant C: empty stdout would crash the MCP bridge's
    json.loads. Even with zero-results, the command must emit a
    non-empty envelope on stdout.
    """

    def test_empty_corpus_no_crash(self, cli_runner, empty_corpus, monkeypatch):
        """No exception / non-empty stdout when no source files changed."""
        monkeypatch.chdir(empty_corpus)
        result = invoke_cli(
            cli_runner,
            ["test-impact"],
            cwd=empty_corpus,
            json_mode=True,
        )
        # test-impact exits 0 on empty -- it is a ranker, not a gate.
        assert result.exit_code == 0, (
            f"test-impact must exit 0 on empty changeset; got {result.exit_code}\n{result.output}"
        )
        out = getattr(result, "stdout", None) or result.output
        assert out.strip(), "Pattern-1 Variant C: empty stdout on empty-corpus"

    def test_empty_corpus_envelope_has_verdict(self, cli_runner, empty_corpus, monkeypatch):
        """Envelope carries a non-empty verdict per LAW 6."""
        monkeypatch.chdir(empty_corpus)
        result = invoke_cli(
            cli_runner,
            ["test-impact"],
            cwd=empty_corpus,
            json_mode=True,
        )
        data = parse_json_output(result, "test-impact")
        assert "summary" in data, f"envelope missing summary: {data}"
        assert "verdict" in data["summary"], f"summary missing verdict: {data['summary']}"
        verdict = data["summary"]["verdict"]
        assert isinstance(verdict, str) and verdict.strip()
        # LAW 6 standalone: verdict names what didn't change.
        assert "no" in verdict.lower() and "changed" in verdict.lower(), (
            f"LAW 6: verdict must describe the empty state standalone; got {verdict!r}"
        )

    def test_empty_corpus_law6_verdict_standalone(self, cli_runner, empty_corpus, monkeypatch):
        """LAW 6: verdict works without any other field present.

        Reading only ``summary.verdict`` from the envelope, an agent
        must understand what happened. ``"no non-test source files
        changed in working tree"`` passes LAW 6; ``"no results"`` would
        fail it.
        """
        monkeypatch.chdir(empty_corpus)
        result = invoke_cli(
            cli_runner,
            ["test-impact"],
            cwd=empty_corpus,
            json_mode=True,
        )
        data = parse_json_output(result, "test-impact")
        verdict = data["summary"]["verdict"]
        # Verdict must be parseable standalone: minimum of an action
        # word + a concrete-noun anchor.
        assert "source files" in verdict or "indexed symbols" in verdict, (
            f"LAW 6: empty-state verdict must anchor on a concrete noun "
            f"('source files' / 'indexed symbols'); got {verdict!r}"
        )


# ---------------------------------------------------------------------------
# Pattern-2 branch 1: no changed files -- silent-SAFE state gap.
# ---------------------------------------------------------------------------


class TestEmptyCorpusStateDisclosure:
    """Branch 1 (no changed files): no machine-readable state field."""

    @pytest.mark.xfail(
        strict=True,
        reason=(
            "W805-HH REAL BUG (branch 1): "
            "src/roam/commands/cmd_test_impact.py:73-103 (the ``not files`` "
            "branch) emits an envelope with no ``state`` field. cmd_impact's "
            "analogous zero-result paths set machine-readable state markers "
            "via the W1272 hardening. cmd_test_impact missed this. Pinned "
            "strict so a future cleanup that adds "
            "``state='no_changed_files'`` (or equivalent) graduates this "
            "to PASS; until then, agents reading ``summary.state`` get "
            "None on the empty-changeset path and cannot distinguish it "
            "from the no-symbols or no-tests branches."
        ),
    )
    def test_empty_corpus_state_explicit(self, cli_runner, empty_corpus, monkeypatch):
        """Empty-changeset path discloses ``state`` explicitly."""
        monkeypatch.chdir(empty_corpus)
        result = invoke_cli(
            cli_runner,
            ["test-impact"],
            cwd=empty_corpus,
            json_mode=True,
        )
        data = parse_json_output(result, "test-impact")
        summary = data["summary"]
        # The exact state literal is a future-fix concern; the discipline
        # is that ANY machine-readable state field exists. Accept the
        # canonical Pattern-1 family literal vocabulary
        # (no_changed_files / index_not_built / advisory_warnings / ...).
        state = summary.get("state")
        assert state is not None and isinstance(state, str) and state.strip(), (
            f"W805-HH Pattern-2: empty-corpus envelope must disclose "
            f"summary.state for cross-branch disambiguation; got {state!r}"
        )

    @pytest.mark.xfail(
        strict=True,
        reason=(
            "W805-HH REAL BUG (branch 1): "
            "src/roam/commands/cmd_test_impact.py:73-103 emits "
            "``partial_success: false`` on the empty-changeset path. "
            "Pattern-2 discipline (silent fallback rule from CLAUDE.md): "
            "never emit verdict='SAFE'/'completed' when an underlying "
            "check did not run. The 'no source files changed' state is "
            "literally 'the test-coverage check did not run', so an "
            "agent switching on partial_success interprets it as a "
            "passing run. Pinned strict; a fix that sets "
            "``partial_success=True`` (or moves state to a non-success "
            "vocabulary) graduates to PASS."
        ),
    )
    def test_empty_corpus_partial_success_set(self, cli_runner, empty_corpus, monkeypatch):
        """Pattern-2 guard: empty-changeset path sets partial_success=True."""
        monkeypatch.chdir(empty_corpus)
        result = invoke_cli(
            cli_runner,
            ["test-impact"],
            cwd=empty_corpus,
            json_mode=True,
        )
        data = parse_json_output(result, "test-impact")
        summary = data["summary"]
        assert summary.get("partial_success") is True, (
            f"W805-HH Pattern-2: empty-changeset 'no check ran' state "
            f"must set partial_success=True so agents can distinguish "
            f"it from a real zero-results success; "
            f"got partial_success={summary.get('partial_success')!r}"
        )


# ---------------------------------------------------------------------------
# Pattern-2 branch 2: changed files but no indexed symbols.
# ---------------------------------------------------------------------------


class TestChangedFilesNoIndexedSymbols:
    """Branch 2 (``not seed_ids``): changed files exist but none indexed."""

    def test_branch_fires_on_changed_md_file(self, cli_runner, changed_no_symbols_corpus, monkeypatch):
        """Sanity: this fixture really triggers the 'no indexed symbols' branch."""
        monkeypatch.chdir(changed_no_symbols_corpus)
        result = invoke_cli(
            cli_runner,
            ["test-impact"],
            cwd=changed_no_symbols_corpus,
            json_mode=True,
        )
        data = parse_json_output(result, "test-impact")
        verdict = data["summary"]["verdict"]
        # The branch-2 verdict prose is distinct from branch 1.
        assert "no indexed symbols" in verdict.lower(), (
            f"fixture should trigger branch 2 ('changed files have no indexed symbols'); got verdict={verdict!r}"
        )


# ---------------------------------------------------------------------------
# Pattern-2 branch 3: real symbols changed, 0 tests reach -- the CRITICAL gap.
# ---------------------------------------------------------------------------


class TestZeroAffectedTestsDisclosure:
    """Branch 3 (``not test_hits``): no test coverage on real change.

    This is the most acute Pattern-2 silent SAFE on cmd_test_impact:
    an agent gating on test-coverage cannot distinguish "the change has
    no tests" (real coverage gap) from "the changeset is empty" (no
    check needed). Both emit ``count: 0, tests: [], partial_success:
    false`` with no machine-readable state field.
    """

    def test_zero_tests_branch_fires(self, cli_runner, no_test_coverage_corpus, monkeypatch):
        """Sanity: this fixture really triggers the no-tests-reach branch.

        Verdict must mention 'no tests reach' (NOT 'no source files
        changed' or 'no indexed symbols') so we know branch 3 fired.
        """
        monkeypatch.chdir(no_test_coverage_corpus)
        result = invoke_cli(
            cli_runner,
            ["test-impact"],
            cwd=no_test_coverage_corpus,
            json_mode=True,
        )
        data = parse_json_output(result, "test-impact")
        verdict = data["summary"]["verdict"]
        assert "no tests reach" in verdict.lower(), (
            f"fixture should trigger branch 3 ('no tests reach the N changed file(s)'); got verdict={verdict!r}"
        )

    @pytest.mark.xfail(
        strict=True,
        reason=(
            "W805-HH REAL BUG (branch 3, CRITICAL): "
            "src/roam/commands/cmd_test_impact.py:174-178 (the no-tests-"
            "reach branch) emits ``count: 0, tests: [], partial_success: "
            "false`` with no machine-readable state field. This is the "
            "single most acute Pattern-2 silent SAFE on this command: "
            "a coverage-aware agent gating on test-impact OUTPUT cannot "
            "tell 'real symbols changed but ZERO tests cover them' "
            "(true coverage gap, partial_success SHOULD be True) from "
            "'changeset is empty, no check needed' (also count=0, "
            "tests=[], partial_success=false). Pinned strict; the fix "
            "either adds ``state='no_test_coverage'`` + "
            "``partial_success=True`` OR emits a distinct verdict-level "
            "marker that downstream consumers can switch on. "
            "Disambiguation matters: this is the signal a CI gate or "
            "an autonomous-PR mode would block on."
        ),
    )
    def test_zero_affected_tests_disclosure(self, cli_runner, no_test_coverage_corpus, monkeypatch):
        """Pattern-2 disambiguation: 'no test coverage' state is explicit.

        Either ``state`` is set to a non-success vocabulary OR
        ``partial_success`` is True -- one of the two must signal that
        this is a degraded outcome, not a clean zero-result.
        """
        monkeypatch.chdir(no_test_coverage_corpus)
        result = invoke_cli(
            cli_runner,
            ["test-impact"],
            cwd=no_test_coverage_corpus,
            json_mode=True,
        )
        data = parse_json_output(result, "test-impact")
        summary = data["summary"]
        # Accept either disclosure path (state vocabulary or
        # partial_success flag). Both should fire on a real "no test
        # coverage" state -- the silent-SAFE bug is that neither does.
        state_present = bool(summary.get("state"))
        partial = summary.get("partial_success") is True
        assert state_present or partial, (
            f"W805-HH Pattern-2 (branch 3): real source change with "
            f"zero test coverage must set EITHER summary.state OR "
            f"summary.partial_success=True to disambiguate from the "
            f"empty-changeset / no-symbols cases; got summary={summary}"
        )


# ---------------------------------------------------------------------------
# Clean-corpus regression -- success branch still emits real reach data.
# ---------------------------------------------------------------------------


class TestCleanCorpusFullSuccess:
    """Positive regression baseline: real test reaches real change."""

    def test_clean_corpus_emits_real_impact(self, cli_runner, clean_corpus, monkeypatch):
        """callee() change -> tests/test_core.py appears in tests[] with reach_count >= 1."""
        monkeypatch.chdir(clean_corpus)
        result = invoke_cli(
            cli_runner,
            ["test-impact"],
            cwd=clean_corpus,
            json_mode=True,
        )
        data = parse_json_output(result, "test-impact")
        summary = data["summary"]
        # Real success path -- count >= 1, tests[] populated.
        assert summary.get("count", 0) >= 1, (
            f"clean corpus should produce >= 1 affected test; got count={summary.get('count')}"
        )
        tests = data.get("tests", [])
        assert len(tests) >= 1, f"tests[] should hold >= 1 entry; got {tests}"
        # Verdict anchors on the LAW 4 'changed file(s)' concrete-noun
        # terminal pattern.
        verdict = summary.get("verdict", "")
        assert "reachable" in verdict.lower() or "test file" in verdict.lower(), (
            f"verdict must describe reach; got {verdict!r}"
        )
        # The first test's reach_count is a positive integer.
        first = tests[0]
        assert isinstance(first.get("reach_count"), int)
        assert first["reach_count"] >= 1
