"""W805-I - empty-corpus smoke for ``roam describe`` (W805 Pattern 2 sweep).

Ninth-in-batch of the W805 sweep. Prior cohort outcomes:

- A (cmd_owner)            REAL BUG (silent ``"top owner: ?"``)
- B (cmd_minimap)          REAL BUG (silent ``"minimap rendered (148 chars)"``)
- C (cmd_oracle)           REAL BUG (verdict/metadata mismatch)
- D (cmd_workflow)         NO REAL BUG (inspector, static catalog)
- E (cmd_path_coverage)    NO REAL BUG (W807-hardened)
- F (cmd_for_bug_fix)      REAL BUG (compound aggregator gap; lifts 6 compounds)
- G (cmd_pr_prep)          in flight
- H (cmd_explain_command)  NO REAL BUG (static-metadata; milder LAW 4/6 gaps pinned)
- I (cmd_describe, this wave)

cmd_describe is a **flagship 5-verb command** per CLAUDE.md. CLAUDE.md cites
it in the canonical "Codebase navigation with roam" tutorial. If it silently
emits a confident project description on a 0-symbol corpus, that is a critical
flagship-class bug (same severity as W834 cmd_health, W805-A cmd_owner).

W978 first-hypothesis re-run BEFORE writing the test
============================================================
cmd_describe is a *project-level* description tool. It takes NO target
argument (no symbol/file/path positional), so Pattern-1 Variant D
(degraded resolution: symbol -> file -> unresolved fallback) does NOT
apply here -- there's no resolution chain to silently degrade. The
relevant probe is Pattern-2 (silent SAFE on empty corpus).

Probed empirically. Three branches:

1. **DEFAULT MODE on 0-symbol corpus** (``src/roam/commands/cmd_describe.py``
   L958-1012). Verdict on a corpus of 2 .py files / 0 symbols / 0 edges:
   ``"python project, 2 files, 1 languages"``, ``partial_success=False``,
   no ``state`` field. Reads as a confident project description, but
   the underlying corpus has 0 symbols -- ``_section_key_abstractions``
   silently emits "No graph metrics available.", ``_section_complexity_guide``
   silently returns the section header with no body, ``_section_domain``
   silently emits 0 keywords. All sections that depend on symbols
   silently produce nothing yet verdict claims success. **REAL BUG.**

2. **DEFAULT MODE on 0-language corpus** (only non-source files indexed):
   Verdict: ``"unknown project, 2 files, 0 languages"`` with
   ``partial_success=False``. Even more egregious -- the verdict literally
   contains the sentinel ``"unknown project"`` AND ``"0 languages"`` yet
   declares success. **REAL BUG.**

3. **--agent-prompt MODE on 0-symbol corpus** (``cmd_describe.py`` L934-956).
   Verdict: ``"empty_proj: 2 files, python | health=N/A"`` with
   ``partial_success=False`` and ``health=N/A``, ``key_abstractions=[]``,
   ``hotspots=[]``, ``cycles=N/A`` (all sentinel). Even though every
   field reads as a sentinel ("N/A" / empty list), verdict declares
   confident success. **REAL BUG.**

The bug is the same shape across all 3 branches: cmd_describe never
inspects ``total_symbols`` / ``total_edges`` before constructing the
verdict, so 0-symbol corpora produce verdicts indistinguishable from
populated corpora. The Pattern-2 fix: emit a degraded envelope with
``partial_success=True`` and ``state="no_symbols"`` /
``state="empty_corpus"`` when the underlying read is empty.

Asserted contract (current state pinned; bug-pin xfail-strict markers
queue the real fix for a separate wave):

- exit 0, parseable envelope, no crash (sealed today)
- verdict present + LAW 6 standalone (sealed today)
- ``summary.partial_success`` key present (sealed today via auto-inject)
- ``agent_contract.facts`` non-empty (sealed today via auto-derive)
- Clean-corpus emits a real description (regression baseline)
- No-target Pattern-1 Variant D: N/A, cmd_describe takes no target

xfail-strict markers (pin the BUG; will go green when fix lands):

- ``summary.state`` explicitly discloses ``empty_corpus`` / ``no_symbols``
- ``summary.partial_success: True`` on the 0-symbol branch
- Verdict mentions empty / no-symbols (LAW 6: self-descriptive)
- Agent-prompt mode discloses ``health=N/A`` <-> degraded state coupling

Run isolation:
    python -m pytest tests/test_w805_i_cmd_describe_empty_corpus.py -x -n 0

Regression baseline:
    python -m pytest tests/test_describe.py tests/test_describe_stack_leak.py -x -n 0

W805-I verdict: REAL BUG (flagship-class Pattern-2 silent-SAFE on 3 branches).
"""

from __future__ import annotations

import json as _json
import os

import pytest
from click.testing import CliRunner

from tests.conftest import git_init, index_in_process

# ---------------------------------------------------------------------------
# Helpers - invoke describe via the Click group so the top-level --json flag
# is honoured by ctx.obj.
# ---------------------------------------------------------------------------


def _invoke_describe(runner: CliRunner, cwd, *extra, json_mode: bool = True):
    """Invoke ``roam describe`` through the group."""
    from roam.cli import cli

    args: list[str] = []
    if json_mode:
        args.append("--json")
    args.append("describe")
    args.extend(extra)

    old_cwd = os.getcwd()
    try:
        os.chdir(str(cwd))
        result = runner.invoke(cli, args, catch_exceptions=False)
    finally:
        os.chdir(old_cwd)
    return result


def _parse_envelope(result) -> dict:
    """Parse the first JSON object from stdout (tolerant of trailing prose)."""
    raw = (result.output or "").lstrip()
    assert raw.startswith("{"), f"expected JSON envelope, got:\n{result.output!r}"
    decoder = _json.JSONDecoder()
    obj, _end = decoder.raw_decode(raw)
    return obj


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def cli_runner():
    return CliRunner()


@pytest.fixture
def empty_corpus(tmp_path, monkeypatch):
    """Indexed project with a single empty .py file - 0 symbols, 0 edges."""
    proj = tmp_path / "empty_corpus"
    proj.mkdir()
    (proj / ".gitignore").write_text(".roam/\n")
    (proj / "empty.py").write_text("")
    git_init(proj)
    monkeypatch.chdir(proj)
    out, rc = index_in_process(proj, "--force")
    assert rc == 0, f"index failed: {out}"
    return proj


@pytest.fixture
def no_source_corpus(tmp_path, monkeypatch):
    """Indexed project with only non-source files (0 languages detected).

    The corpus contains a README and a .gitignore -- both lack a language
    tag in the ``files`` table. ``_top_lang`` falls through to "unknown"
    and ``_n_langs`` is 0. The describe verdict shape on this branch is
    ``"unknown project, 2 files, 0 languages"`` -- the canonical W805-I
    silent-SAFE shape on the 0-language sub-branch.
    """
    proj = tmp_path / "no_source_corpus"
    proj.mkdir()
    (proj / ".gitignore").write_text(".roam/\n")
    (proj / "README").write_text("hi")
    git_init(proj)
    monkeypatch.chdir(proj)
    out, rc = index_in_process(proj, "--force")
    assert rc == 0, f"index failed: {out}"
    return proj


@pytest.fixture
def populated_corpus(tmp_path, monkeypatch):
    """Indexed project with real symbols - regression / uniformity baseline."""
    proj = tmp_path / "populated_corpus"
    proj.mkdir()
    (proj / ".gitignore").write_text(".roam/\n")
    src = proj / "src"
    src.mkdir()
    (src / "main.py").write_text(
        "def main():\n    helper()\n    return 1\n\ndef helper():\n    return 42\n",
        encoding="utf-8",
    )
    (src / "utils.py").write_text(
        'def format_name(first, last):\n    return f"{first} {last}"\n',
        encoding="utf-8",
    )
    git_init(proj)
    monkeypatch.chdir(proj)
    out, rc = index_in_process(proj, "--force")
    assert rc == 0, f"index failed: {out}"
    return proj


# ---------------------------------------------------------------------------
# Sealed-today contracts (always-on smoke)
# ---------------------------------------------------------------------------


class TestDescribeEmptyCorpusSealed:
    """Properties already satisfied by the current cmd_describe envelope."""

    def test_empty_corpus_no_crash(self, cli_runner, empty_corpus):
        """``roam describe`` on empty corpus exits 0 (no crash)."""
        result = _invoke_describe(cli_runner, empty_corpus, json_mode=True)
        assert result.exit_code == 0, f"expected exit 0, got {result.exit_code}; output:\n{result.output}"
        # Pattern-1C: stdout MUST be non-empty in --json mode.
        assert result.output.strip(), "stdout must NOT be empty in --json mode"

    def test_empty_corpus_envelope_has_verdict(self, cli_runner, empty_corpus):
        """Envelope carries ``command=describe`` + non-empty verdict."""
        result = _invoke_describe(cli_runner, empty_corpus, json_mode=True)
        envelope = _parse_envelope(result)
        assert envelope["command"] == "describe"
        summary = envelope.get("summary") or {}
        verdict = summary.get("verdict") or ""
        assert isinstance(verdict, str) and verdict, f"summary.verdict must be a non-empty string, got {verdict!r}"

    def test_empty_corpus_law6_verdict_standalone(self, cli_runner, empty_corpus):
        """LAW 6: verdict line stands alone (single line, ASCII)."""
        result = _invoke_describe(cli_runner, empty_corpus, json_mode=True)
        envelope = _parse_envelope(result)
        verdict = envelope["summary"]["verdict"]
        assert "\n" not in verdict, f"verdict has embedded newline: {verdict!r}"
        assert verdict.isascii(), f"verdict is not plain ASCII: {verdict!r}"
        assert verdict.strip() not in ("", "?", "verdict", "OK", "ok"), f"verdict is a placeholder: {verdict!r}"

    def test_empty_corpus_partial_success_key_present(self, cli_runner, empty_corpus):
        """``summary.partial_success`` key is auto-injected on every envelope.

        Sealed-today contract: present + boolean. Whether the value is True
        (which would mean the bug is fixed) is pinned by the xfail block
        below. This test passes today because json_envelope auto-injects
        ``partial_success=False`` when the producer doesn't set it.
        """
        result = _invoke_describe(cli_runner, empty_corpus, json_mode=True)
        envelope = _parse_envelope(result)
        summary = envelope.get("summary") or {}
        assert "partial_success" in summary, (
            f"summary.partial_success key must be auto-injected; got summary keys = {sorted(summary.keys())}"
        )
        assert isinstance(summary["partial_success"], bool)

    def test_empty_corpus_agent_contract_facts_present(self, cli_runner, empty_corpus):
        """``agent_contract.facts`` is a non-empty list (auto-derived)."""
        result = _invoke_describe(cli_runner, empty_corpus, json_mode=True)
        envelope = _parse_envelope(result)
        contract = envelope.get("agent_contract") or {}
        facts = contract.get("facts") or []
        assert isinstance(facts, list) and facts, f"agent_contract.facts must be a non-empty list; got {facts!r}"

    def test_clean_corpus_emits_real_description(self, cli_runner, populated_corpus):
        """Happy-path positive coverage: a populated corpus produces a real
        verdict mentioning the dominant language + correct file count."""
        result = _invoke_describe(cli_runner, populated_corpus, json_mode=True)
        assert result.exit_code == 0, result.output
        envelope = _parse_envelope(result)
        summary = envelope["summary"]
        verdict = summary["verdict"]
        assert "python" in verdict.lower(), f"happy-path verdict missing 'python': {verdict!r}"
        # markdown payload is the canonical describe output -- must contain
        # real content rather than only section headers + "No X" sentinels.
        markdown = envelope.get("markdown") or ""
        assert "main" in markdown or "helper" in markdown or "format_name" in markdown, (
            f"happy-path markdown should name at least one real symbol; got first 400 chars:\n{markdown[:400]}"
        )

    def test_no_silent_describe_success_on_empty_anti_shape(self, cli_runner, empty_corpus):
        """Anti-shape: verdict must NOT be a known silent-SAFE token.

        Drift guard so a future refactor doesn't accidentally introduce one.
        """
        result = _invoke_describe(cli_runner, empty_corpus, json_mode=True)
        envelope = _parse_envelope(result)
        verdict = (envelope["summary"]["verdict"] or "").lower()
        forbidden = (
            "describe successful",
            "describe completed",
            "non-conformant",
            "compound operation completed",
            "see details",
        )
        for token in forbidden:
            assert token not in verdict, (
                f"Pattern-2 silent SAFE shape detected (verdict contains {token!r}): {verdict!r}"
            )

    def test_agent_prompt_mode_smoke(self, cli_runner, empty_corpus):
        """--agent-prompt empty-corpus smoke: no crash, parseable envelope.

        This is the second branch in cmd_describe (L934-956) which uses
        a different verdict construction. Sealed-today: it exits 0 with
        a parseable envelope; the silent-SAFE semantics are pinned by
        the xfail block below.
        """
        result = _invoke_describe(cli_runner, empty_corpus, "--agent-prompt", json_mode=True)
        assert result.exit_code == 0, result.output
        envelope = _parse_envelope(result)
        assert envelope["command"] == "describe"
        assert envelope["summary"]["mode"] == "agent-prompt"
        verdict = envelope["summary"]["verdict"]
        assert isinstance(verdict, str) and verdict


# ---------------------------------------------------------------------------
# Pattern-2 BUG PIN: xfail-strict on the empty-corpus silent-SAFE shape.
# Each marker pins a distinct facet of the bug; all go green when the fix
# lands (separate wave per the W805 accumulate-only constraint).
# ---------------------------------------------------------------------------


@pytest.mark.xfail(
    strict=True,
    reason=(
        "W805-I REAL BUG: cmd_describe.py L982-1012 (default JSON branch) "
        "emits verdict='python project, 2 files, 1 languages' with "
        "summary.partial_success=False when the corpus has 0 indexed "
        "symbols. The Pattern-2 silent-SAFE shape: verdict reads as a "
        "confident project description even though _section_key_abstractions "
        "/ _section_complexity_guide / _section_domain all silently emit "
        "nothing because there are no symbols to draw from. Fix: count "
        "total_symbols + total_edges BEFORE building the verdict; on "
        "0-symbol corpora set summary.partial_success=True and "
        "summary.state='no_symbols'. Separate fix wave."
    ),
)
def test_empty_corpus_partial_success_set(cli_runner, empty_corpus):
    """Pin: ``summary.partial_success`` should be True on 0-symbol corpus."""
    result = _invoke_describe(cli_runner, empty_corpus, json_mode=True)
    envelope = _parse_envelope(result)
    summary = envelope["summary"]
    assert summary.get("partial_success") is True, (
        f"0-symbol corpus must set partial_success=True; got summary={summary!r}"
    )


@pytest.mark.xfail(
    strict=True,
    reason=(
        "W805-I REAL BUG: cmd_describe.py L982-1012 does not emit a "
        "summary.state field on the empty branch. Pattern-2 requires "
        "closed-enum state disclosure ('no_symbols' / 'empty_corpus' / "
        "'no_indexed_symbols'). Cohort precedent: cmd_owner emits "
        "state='path_not_found'; cmd_path_coverage emits state from "
        "{'no_entry_points','no_sinks','no_paths_connecting','ok'}; "
        "explain-command emits state='unknown_command'. Separate fix wave."
    ),
)
def test_empty_corpus_explicit_state(cli_runner, empty_corpus):
    """Pin: ``summary.state`` should disclose the empty-corpus state."""
    result = _invoke_describe(cli_runner, empty_corpus, json_mode=True)
    envelope = _parse_envelope(result)
    summary = envelope["summary"]
    state = summary.get("state") or envelope.get("state")
    accepted = {"no_symbols", "empty_corpus", "no_indexed_symbols", "not_initialized"}
    assert state in accepted, (
        f"summary.state should disclose absent-symbol state, got {state!r}; expected one of {accepted}"
    )


@pytest.mark.xfail(
    strict=True,
    reason=(
        "W805-I REAL BUG: cmd_describe.py L982 builds the verdict as "
        "f'{_top_lang} project, {_total_files} files, {_n_langs} languages' "
        "regardless of whether symbols/edges exist. On a 0-symbol corpus "
        "this reads as a confident description (LAW 6 violation: verdict "
        "must be honest standalone). Fix: when total_symbols==0, emit a "
        "verdict like 'no symbols indexed - run roam index first' OR "
        "'empty corpus: 2 files, 0 symbols indexed'. Separate fix wave."
    ),
)
def test_empty_corpus_verdict_discloses_empty(cli_runner, empty_corpus):
    """Pin: the verdict must name the empty-corpus state.

    LAW 6: verdict works without any other field. ``"python project,
    2 files, 1 languages"`` reads as a confident description on a
    0-symbol corpus, hiding the empty state. The fix surfaces it in
    the verdict directly.
    """
    result = _invoke_describe(cli_runner, empty_corpus, json_mode=True)
    envelope = _parse_envelope(result)
    verdict = (envelope["summary"]["verdict"] or "").lower()
    disclosure_tokens = (
        "no symbols",
        "empty",
        "0 symbols",
        "not indexed",
        "no indexed",
        "no data",
    )
    matched = any(tok in verdict for tok in disclosure_tokens)
    assert matched, (
        f"verdict must disclose the empty-corpus state, got {verdict!r}; expected one of {disclosure_tokens}"
    )


@pytest.mark.xfail(
    strict=True,
    reason=(
        "W805-I REAL BUG (no-source branch): cmd_describe.py L979-982 "
        "emits verdict='unknown project, 2 files, 0 languages' with "
        "partial_success=False on a 0-language corpus. Verdict literally "
        "contains 'unknown' AND '0 languages' yet declares success. "
        "Anti-shape: verdict must NOT start with 'unknown project' while "
        "claiming partial_success=False. Separate fix wave."
    ),
)
def test_no_source_corpus_unknown_project_partial_success(cli_runner, no_source_corpus):
    """Pin: a 0-language corpus must set partial_success=True.

    The 0-language branch is structurally the same Pattern-2 silent-SAFE
    shape as the 0-symbol branch -- verdict reports 'unknown project'
    and '0 languages' as if those were valid description fields rather
    than disclosure of absent data.
    """
    result = _invoke_describe(cli_runner, no_source_corpus, json_mode=True)
    envelope = _parse_envelope(result)
    summary = envelope["summary"]
    verdict = (summary.get("verdict") or "").lower()
    # The current bug-shape: verdict contains "unknown project" AND
    # "0 languages" AND partial_success=False. The fix flips partial_success
    # to True on this branch.
    silent_safe_shape = (
        "unknown project" in verdict and "0 languages" in verdict and summary.get("partial_success") is False
    )
    assert not silent_safe_shape, (
        f"silent-SAFE shape detected: verdict={verdict!r}, "
        f"partial_success={summary.get('partial_success')!r}. "
        "When no source languages are detected, set partial_success=True "
        "and surface the absent-language state in summary.state."
    )


@pytest.mark.xfail(
    strict=True,
    reason=(
        "W805-I REAL BUG (agent-prompt branch): cmd_describe.py L934-956 "
        "emits verdict='<project>: 2 files, python | health=N/A' with "
        "partial_success=False on a 0-symbol corpus. health=N/A + "
        "key_abstractions=[] + hotspots=[] + cycles=N/A are ALL sentinel "
        "values yet verdict declares success. Pattern-2 silent-SAFE: a "
        "verdict containing '=N/A' must be coupled with "
        "partial_success=True. Separate fix wave."
    ),
)
def test_agent_prompt_empty_corpus_partial_success_coupled_to_na(cli_runner, empty_corpus):
    """Pin: --agent-prompt verdict containing 'N/A' must set partial_success.

    The agent-prompt verdict literally embeds the sentinel ``health=N/A``
    when graph metrics aren't computable (0-symbol corpus). The current
    code still reports ``partial_success=False`` -- the sentinel in the
    verdict and the partial_success bool disagree about success.
    """
    result = _invoke_describe(cli_runner, empty_corpus, "--agent-prompt", json_mode=True)
    envelope = _parse_envelope(result)
    summary = envelope["summary"]
    verdict = summary.get("verdict") or ""
    # If the verdict contains an N/A sentinel, partial_success MUST be True.
    if "N/A" in verdict or "n/a" in verdict.lower():
        assert summary.get("partial_success") is True, (
            f"verdict embeds N/A sentinel ({verdict!r}) but partial_success="
            f"{summary.get('partial_success')!r}; sentinel and success flag disagree"
        )
    else:
        # Fail loudly if the verdict shape changed (the underlying bug may
        # have been fixed but in a different way -- re-investigate).
        pytest.fail(
            f"agent-prompt verdict no longer contains an N/A sentinel; re-investigate W805-I shape. verdict={verdict!r}"
        )


# ---------------------------------------------------------------------------
# Pattern-1 Variant D not applicable to cmd_describe.
# Documented here as a deliberate non-test so future readers don't add a
# fuzzy-match-degraded test that would be a category error.
# ---------------------------------------------------------------------------


def test_pattern_1_variant_d_not_applicable_to_describe(cli_runner, populated_corpus):
    """cmd_describe takes NO target argument - resolution chain N/A.

    Pattern-1 Variant D (silent success on degraded resolution: symbol ->
    file -> unresolved fallback) requires the command to RESOLVE a target.
    cmd_describe (``@click.command()`` decorator with only --write, --force,
    --agent-prompt, -o/--output flags) operates project-wide and has no
    symbol/file/path positional. The probe axis for W805-I is therefore
    Pattern-2 (silent SAFE) only, not Variant D.

    Drift guard: if a future refactor adds a positional target argument,
    this assertion will need to be inverted and the Variant D probe
    added in a follow-up wave.
    """
    from roam.commands.cmd_describe import describe as describe_cmd

    # Click params include the flags but no positional Argument.
    positionals = [p for p in describe_cmd.params if p.__class__.__name__ == "Argument"]
    assert positionals == [], (
        f"cmd_describe gained a positional argument: {positionals!r}. "
        "Variant D probe must be added to this test file -- this is "
        "the W805-I drift guard."
    )
