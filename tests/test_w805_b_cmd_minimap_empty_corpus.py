"""W805-B - empty-corpus smoke for ``roam minimap`` (W805 Pattern 2 sweep).

Probes whether ``roam minimap --json`` on an empty corpus discloses the
empty/degraded state explicitly, or silently emits a "minimap rendered"
verdict indistinguishable from a healthy-corpus success (Pattern 2).

W978 first-hypothesis result (probed before writing tests):
    REAL BUG. On a corpus with 0 indexed symbols, ``cmd_minimap.py``
    line 588-605 emits the SAME stdout-mode verdict shape as a healthy
    corpus:
        "minimap rendered (148 chars) - wrap in CLAUDE.md with
         --update-claude"
    There is no inspection of symbol counts / file counts / cluster /
    layer counts before the verdict is constructed; ``partial_success``
    is auto-injected as ``False``; there is no ``state`` key disclosing
    the empty corpus. A consuming agent has no signal that the
    rendered block is just a stack line + a 2-entry directory tree +
    "mixed conventions" derived from zero symbols.

Asserted contract (current state pinned; bug-pin xfail-strict markers
queue the real fix for a separate wave):

- exit 0, parseable envelope, no crash (sealed today)
- verdict present + LAW 6 standalone (sealed today)
- ``summary.partial_success`` key present (sealed today by auto-inject)
- ``agent_contract.facts`` non-empty (sealed today)
- Clean-corpus emits a real minimap (regression baseline)

xfail-strict markers (pin the BUG; will go green when fix lands):

- ``summary.state`` explicitly discloses ``empty_corpus`` /
  ``no_symbols`` / ``not_initialized``
- ``summary.partial_success: True`` on the empty branch
- Verdict mentions empty / no-symbols / no-data (LAW 6: standalone)

The fix-forward (separate wave): make ``_render_minimap`` / the
``minimap`` command count symbols + edges + clusters BEFORE rendering;
when the corpus is empty, emit a degraded envelope with
``partial_success=True``, ``state="empty_corpus"`` or ``no_symbols``,
and a verdict like ``"minimap empty: no symbols indexed - run roam
index first"``.
"""

from __future__ import annotations

import json as _json
import os

import pytest
from click.testing import CliRunner

from tests.conftest import git_init, index_in_process

# ---------------------------------------------------------------------------
# Helpers - invoke minimap via the Click group (uses --json flag on group)
# ---------------------------------------------------------------------------


def _invoke_minimap(runner: CliRunner, cwd, json_mode: bool = True, *extra):
    """Invoke ``roam minimap`` through the group so ``--json`` is honoured."""
    from roam.cli import cli

    args = []
    if json_mode:
        args.append("--json")
    args.append("minimap")
    args.extend(extra)

    old_cwd = os.getcwd()
    try:
        os.chdir(str(cwd))
        result = runner.invoke(cli, args, catch_exceptions=False)
    finally:
        os.chdir(old_cwd)
    return result


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def cli_runner():
    return CliRunner()


@pytest.fixture
def empty_corpus(tmp_path, monkeypatch):
    """Indexed project with a single empty .py file.

    The indexer runs cleanly but produces zero function/class/method
    symbols, zero edges, zero clusters and zero layers. ``minimap`` is
    forced down its no-data path.
    """
    proj = tmp_path / "empty_corpus"
    proj.mkdir()
    (proj / ".gitignore").write_text(".roam/\n")
    # Empty .py file: indexer sees a file but extracts no symbols.
    (proj / "empty.py").write_text("")
    git_init(proj)
    monkeypatch.chdir(proj)
    out, rc = index_in_process(proj, "--force")
    assert rc == 0, f"index failed: {out}"
    return proj


@pytest.fixture
def clean_corpus(tmp_path, monkeypatch):
    """Indexed project with real symbols - regression baseline."""
    proj = tmp_path / "clean_corpus"
    proj.mkdir()
    (proj / ".gitignore").write_text(".roam/\n")
    src = proj / "src"
    src.mkdir()
    (src / "main.py").write_text(
        "def main():\n    helper()\n\ndef helper():\n    pass\n",
        encoding="utf-8",
    )
    (src / "utils.py").write_text(
        "class Config:\n    pass\n\ndef load_config():\n    return Config()\n",
        encoding="utf-8",
    )
    git_init(proj)
    monkeypatch.chdir(proj)
    out, rc = index_in_process(proj, "--force")
    assert rc == 0, f"index failed: {out}"
    return proj


# ---------------------------------------------------------------------------
# Sealed-today contracts (no current bug here)
# ---------------------------------------------------------------------------


class TestMinimapEmptyCorpusSealed:
    """Properties already satisfied by the current minimap envelope."""

    def test_empty_corpus_no_crash(self, cli_runner, empty_corpus):
        """``roam minimap --json`` on an empty corpus exits 0."""
        result = _invoke_minimap(cli_runner, empty_corpus, json_mode=True)
        assert result.exit_code == 0, f"Expected exit 0, got {result.exit_code}:\n{result.output}"
        # Non-empty stdout (Pattern 1 variant C).
        assert result.output.strip(), "stdout must NOT be empty in --json mode"

    def test_empty_corpus_envelope_has_verdict(self, cli_runner, empty_corpus):
        """Envelope carries a non-empty verdict string."""
        result = _invoke_minimap(cli_runner, empty_corpus, json_mode=True)
        assert result.exit_code == 0
        payload = _json.loads(result.output)
        assert payload.get("command") == "minimap"
        summary = payload.get("summary") or {}
        verdict = summary.get("verdict")
        assert isinstance(verdict, str) and verdict, f"summary.verdict must be a non-empty string; got {verdict!r}"

    def test_empty_corpus_partial_success_key_present(self, cli_runner, empty_corpus):
        """``summary.partial_success`` key is auto-injected and present.

        Pattern 2 / Pattern 1 variant D: even on the empty branch, the
        envelope must DISCLOSE its partial-success state so consumers
        never have to guess from absence. The value may legitimately be
        ``False`` today; only the KEY presence is the sealed contract.
        """
        result = _invoke_minimap(cli_runner, empty_corpus, json_mode=True)
        assert result.exit_code == 0
        payload = _json.loads(result.output)
        summary = payload.get("summary") or {}
        assert "partial_success" in summary, (
            f"summary.partial_success must be present (auto-injected); got summary keys = {sorted(summary.keys())}"
        )

    def test_empty_corpus_law6_verdict_standalone(self, cli_runner, empty_corpus):
        """LAW 6: the verdict must work without any other field.

        The minimap verdict on the empty branch reads like
        ``"minimap rendered (N chars) - wrap in CLAUDE.md with
        --update-claude"``. That string is self-describing as imperative
        prose even though it doesn't yet disclose the empty-corpus
        condition (see xfail tests below for the bug-pin).
        """
        result = _invoke_minimap(cli_runner, empty_corpus, json_mode=True)
        assert result.exit_code == 0
        payload = _json.loads(result.output)
        verdict = payload.get("summary", {}).get("verdict", "")
        # LAW 6: must contain the command identifier so the verdict makes
        # sense standalone (without re-reading the ``command`` field).
        assert "minimap" in verdict.lower(), f"LAW 6: verdict must be self-describing standalone; got {verdict!r}"

    def test_empty_corpus_agent_contract_facts_non_empty(self, cli_runner, empty_corpus):
        """``agent_contract.facts`` is non-empty even on the empty branch."""
        result = _invoke_minimap(cli_runner, empty_corpus, json_mode=True)
        assert result.exit_code == 0
        payload = _json.loads(result.output)
        contract = payload.get("agent_contract") or {}
        facts = contract.get("facts") or []
        assert isinstance(facts, list) and facts, (
            f"agent_contract.facts must be non-empty on empty corpus; got {facts!r}"
        )

    def test_clean_corpus_emits_real_minimap(self, cli_runner, clean_corpus):
        """Regression baseline: a real-symbol corpus emits a non-trivial
        minimap with stack + tree + verdict referencing the rendered
        block.
        """
        result = _invoke_minimap(cli_runner, clean_corpus, json_mode=True)
        assert result.exit_code == 0, f"clean corpus minimap failed: {result.output}"
        payload = _json.loads(result.output)
        assert payload.get("command") == "minimap"
        content = payload.get("content") or ""
        # The rendered block must mention the source files we created.
        assert "main.py" in content or "utils.py" in content, (
            f"clean corpus minimap should reference source files; got:\n{content[:500]}"
        )
        verdict = payload.get("summary", {}).get("verdict", "")
        assert "minimap rendered" in verdict


# ---------------------------------------------------------------------------
# W978-confirmed REAL BUG -- pinned via xfail-strict; will go green when fix lands.
#
# Bug: cmd_minimap.py L588-605 (the stdout JSON-mode emit) does NOT
# inspect symbol/edge/cluster counts before assembling the verdict. On
# an empty corpus the verdict reads identically to a healthy corpus's
# "minimap rendered (N chars) - wrap in CLAUDE.md with --update-claude"
# and there is no ``state`` field disclosing the empty condition. This
# is exactly Pattern 2 (silent fallback) - SAFE/healthy verdict when
# the underlying check produced no useful data.
# ---------------------------------------------------------------------------


@pytest.mark.xfail(
    strict=True,
    reason=(
        "W805-B BUG: cmd_minimap.py L588-605 does not disclose "
        "empty-corpus state. Verdict says 'minimap rendered (N chars)' "
        "identically on healthy and 0-symbol corpora. Pattern 2 silent "
        "fallback; awaiting separate fix wave."
    ),
)
def test_empty_corpus_partial_success_set(cli_runner, empty_corpus):
    """``summary.partial_success`` should be True on the empty branch.

    Pattern 2 (CLAUDE.md): an empty-data outcome must disclose
    partial_success=True. Today the auto-inject defaults to False on
    every minimap envelope, including the no-symbol case, because the
    command never sets it explicitly.
    """
    result = _invoke_minimap(cli_runner, empty_corpus, json_mode=True)
    assert result.exit_code == 0
    payload = _json.loads(result.output)
    summary = payload.get("summary") or {}
    assert summary.get("partial_success") is True, (
        f"partial_success should be True on empty corpus; got {summary.get('partial_success')!r}"
    )


@pytest.mark.xfail(
    strict=True,
    reason=(
        "W805-B BUG: cmd_minimap.py L588-605 does not emit summary.state. "
        "Empty corpus and healthy corpus are indistinguishable in the "
        "envelope state field. Pattern 2 silent fallback; awaiting fix wave."
    ),
)
def test_empty_corpus_explicit_state(cli_runner, empty_corpus):
    """``summary.state`` should disclose the empty condition explicitly.

    Acceptable values (closed enum): ``empty_corpus``, ``no_symbols``,
    ``not_initialized``. Today the key is absent entirely.
    """
    result = _invoke_minimap(cli_runner, empty_corpus, json_mode=True)
    assert result.exit_code == 0
    payload = _json.loads(result.output)
    summary = payload.get("summary") or {}
    state = summary.get("state")
    assert state in ("empty_corpus", "no_symbols", "not_initialized"), (
        f"summary.state must disclose empty condition; got {state!r}; summary keys = {sorted(summary.keys())}"
    )


@pytest.mark.xfail(
    strict=True,
    reason=(
        "W805-B BUG: cmd_minimap.py L596-598 emits 'minimap rendered "
        "(N chars)' identically on healthy and empty corpora. The verdict "
        "must mention 'empty' / 'no symbols' / 'no data' on the empty "
        "branch (LAW 6 standalone). Pattern 2 silent fallback; awaiting "
        "fix wave."
    ),
)
def test_empty_corpus_no_silent_healthy_minimap(cli_runner, empty_corpus):
    """Verdict on the empty branch should NOT match the healthy-corpus
    'minimap rendered (N chars)' shape - it must call out the empty
    condition explicitly so a consuming agent doesn't act on a 148-char
    fake minimap as if it were real.
    """
    result = _invoke_minimap(cli_runner, empty_corpus, json_mode=True)
    assert result.exit_code == 0
    payload = _json.loads(result.output)
    verdict = payload.get("summary", {}).get("verdict", "").lower()
    # The bug: today verdict is literally
    #   "minimap rendered (148 chars) - wrap in CLAUDE.md with --update-claude"
    # which is the success shape. Empty-state vocabulary must appear.
    empty_tokens = ("empty", "no symbol", "no data", "no files", "not initialized", "no index")
    assert any(t in verdict for t in empty_tokens), f"verdict must disclose empty-corpus state; got {verdict!r}"


@pytest.mark.xfail(
    strict=True,
    reason=(
        "W805-B BUG: cmd_minimap.py never emits an explicit no_symbols "
        "state. Same root cause as test_empty_corpus_explicit_state; "
        "kept as a separate test so the fix wave can verify the "
        "no_symbols branch independently. Pattern 2 silent fallback."
    ),
)
def test_no_symbols_emits_explicit_no_symbols_state(cli_runner, empty_corpus):
    """A corpus with zero indexed symbols should emit either
    ``state == "no_symbols"`` explicitly, OR a verdict that names the
    no-symbols condition (LAW 6 standalone).
    """
    result = _invoke_minimap(cli_runner, empty_corpus, json_mode=True)
    assert result.exit_code == 0
    payload = _json.loads(result.output)
    summary = payload.get("summary") or {}
    state = summary.get("state", "")
    verdict = summary.get("verdict", "").lower()
    no_symbols_signalled = (state == "no_symbols") or ("no symbol" in verdict) or ("no symbols indexed" in verdict)
    assert no_symbols_signalled, (
        f"no-symbols condition must be disclosed via state or verdict; state={state!r}, verdict={verdict!r}"
    )
