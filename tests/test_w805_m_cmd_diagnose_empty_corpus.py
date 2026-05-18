"""W805-M - empty-corpus smoke for ``roam diagnose`` (W805 Pattern 2 sweep).

Thirteenth-in-batch of the W805 Pattern-2 audit. Prior cohort:

- A (cmd_owner)            REAL BUG (silent "top owner: ?")
- B (cmd_minimap)          REAL BUG (silent "minimap rendered (148 chars)")
- C (cmd_oracle)           REAL BUG (verdict/metadata mismatch)
- D (cmd_workflow)         NO REAL BUG (static-metadata inspector)
- E (cmd_path_coverage)    NO REAL BUG (W807-hardened)
- F (cmd_for_bug_fix)      REAL BUG (_compound_envelope)
- G (cmd_pr_prep)          REAL BUG (silent READY + ASCII)
- H (cmd_explain_command)  NO REAL BUG (static-metadata)
- I (cmd_describe)         REAL BUG (3 silent-SAFE branches)
- J (cmd_understand)       REAL BUG (silent 100/100)
- K (cmd_module)           REAL BUG (3 shapes: Pattern-1B/C + 1D + 2)
- L (cmd_preflight)        in flight
- M (cmd_diagnose, this wave)

cmd_diagnose is the next **comprehension-class flagship** -- "Debugging a
failure: roam diagnose <name>" per CLAUDE.md. Root-cause ranking is the
single most-cited use of diagnose in the dogfood corpus, so a silent-SAFE
verdict on empty input is exactly the failure class W805 targets.

W978 first-hypothesis re-run BEFORE writing the test
============================================================

``cmd_diagnose`` IS a target-resolving DB-querying command. The W978 first
hypothesis ("probably has silent-SAFE on empty corpus + Pattern-1 Variant D
risk") was probed empirically. Three relevant branches in
``src/roam/commands/cmd_diagnose.py``:

1. **Unresolved-symbol branch** (``cmd_diagnose.py:352-377``). On an empty
   corpus, ``find_symbol_with_alternatives`` returns (None, []) and the
   command emits a clean envelope:

       {"summary": {"verdict": "Symbol 'X' not found",
                    "partial_success": true,
                    "state": "not_found",
                    "resolution": "unresolved"}}

   This is W1272-correct: exit 0, structured envelope, closed-enum state
   disclosure, resolution=unresolved. **NO BUG on this branch.**

2. **Empty-batch branch** (``cmd_diagnose.py:255-343``). When the batch
   input has 0 names (empty stdin) the command emits:

       {"summary": {"verdict": "0 symbol(s) diagnosed",
                    "count": 0,
                    "partial_success": false}}

   No ``state`` field. Verdict "0 symbol(s) diagnosed" reads as a confident
   success (zero work successfully completed) but it's the canonical
   Pattern-2 silent-SAFE shape: a consumer cannot distinguish "empty input
   intentionally" from "input file unreadable" from "all names stripped to
   empty". **REAL BUG.**

3. **No-suspects branch** (``cmd_diagnose.py:509-519``). When a symbol
   resolves cleanly but has 0 upstream/downstream callers within the
   depth range (a 1-symbol corpus, or any leaf symbol), the verdict is
   ``"No upstream/downstream symbols found within depth range."`` with
   ``partial_success: false`` and no ``state`` field. The ENTIRE point of
   the command is to rank suspects -- a zero-suspect result is by
   definition a degraded diagnosis. **REAL BUG.**

4. **Graph-isolation branch** (``cmd_diagnose.py:398-438``). When a symbol
   resolves but is not in the call graph, the W1244 fix correctly emits
   ``state="isolated_in_graph"`` + ``partial_success: true``. **NO BUG.**

Pattern-1 Variant D (degraded fuzzy resolution) is **already** handled
correctly: W1244 / W1249 stamp ``_resolution_tier`` on the row and emit
``resolution`` + ``partial_success`` disclosure across both single and
batch modes. See ``tests/test_cmd_diagnose_resolution.py`` for the
existing W1244 contract -- this file does NOT re-test it.

Test split (mirrors W805-K baseline-plus-xfail-pin discipline):

1. SMOKE (always-on assertions):
   * Empty corpus + unresolved name = no crash, parseable envelope
   * Unresolved-name envelope already discloses state=not_found (W1272)
   * LAW 6: verdict is standalone single-line ASCII
   * Empty-batch shape: exit 0, parseable envelope, count=0
   * No-suspect shape: exit 0, parseable envelope, target resolved

2. PATTERN-2 PIN (xfail-strict): empty batch silent SAFE
   * Empty batch sets ``partial_success: true``
   * Empty batch discloses ``state`` (closed-enum)
   * Empty batch verdict mentions the empty state explicitly

3. PATTERN-2 PIN (xfail-strict): no-suspects silent SAFE
   * 0-suspect diagnosis sets ``partial_success: true``
   * 0-suspect diagnosis discloses ``state`` (closed-enum)

4. PATTERN-1 VARIANT D NEGATIVE: confirms the existing W1244 disclosure
   for the unresolved-target branch is intact (not re-test of
   ``test_cmd_diagnose_resolution.py``, just a regression baseline).

The W805-M fix lives in a separate wave; this module is intentionally
test-only per the accumulate-only constraint.
"""

from __future__ import annotations

import json as _json
import os
import sys
from pathlib import Path

import pytest
from click.testing import CliRunner

sys.path.insert(0, str(Path(__file__).parent))
from conftest import git_init, index_in_process  # noqa: E402

# Forward-compatibility import per the W805 sweep checklist (the helper is
# the canonical resolver of the repo root for tests that touch repo files;
# this file's fixtures are tmp_path-only so the import is a no-op today,
# but importing it pins the dependency for future drift).
from tests._helpers.repo_root import repo_root  # noqa: F401, E402

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def empty_corpus(tmp_path, monkeypatch):
    """Indexed project with a single empty .py file -- 0 symbols, 0 edges.

    This is the canonical empty-corpus shape: ``find_symbol_with_alternatives``
    on any name returns (None, []), exercising the unresolved branch at
    ``cmd_diagnose.py:352``.
    """
    proj = tmp_path / "empty_corpus"
    proj.mkdir()
    (proj / ".gitignore").write_text(".roam/\n", encoding="utf-8")
    (proj / "empty.py").write_text("", encoding="utf-8")
    git_init(proj)
    monkeypatch.chdir(proj)
    out, rc = index_in_process(proj, "--force")
    assert rc == 0, f"index failed: {out}"
    return proj


@pytest.fixture
def single_symbol_corpus(tmp_path, monkeypatch):
    """Indexed project with a single leaf symbol -- 1 symbol, 0 edges.

    Triggers the no-suspects branch at ``cmd_diagnose.py:509-519``: the
    target resolves cleanly (resolution=symbol) but has 0 upstream and 0
    downstream symbols within any depth range.
    """
    proj = tmp_path / "single_symbol_corpus"
    proj.mkdir()
    (proj / ".gitignore").write_text(".roam/\n", encoding="utf-8")
    (proj / "only.py").write_text(
        "def lonely():\n    return 1\n",
        encoding="utf-8",
    )
    git_init(proj)
    monkeypatch.chdir(proj)
    out, rc = index_in_process(proj, "--force")
    assert rc == 0, f"index failed: {out}"
    return proj


@pytest.fixture
def populated_corpus(tmp_path, monkeypatch):
    """Indexed project with real call edges -- regression baseline."""
    proj = tmp_path / "populated_corpus"
    proj.mkdir()
    (proj / ".gitignore").write_text(".roam/\n", encoding="utf-8")
    src = proj / "src"
    src.mkdir()
    (src / "main.py").write_text(
        "def main():\n    helper()\n    return 1\n\ndef helper():\n    return 42\n",
        encoding="utf-8",
    )
    git_init(proj)
    monkeypatch.chdir(proj)
    out, rc = index_in_process(proj, "--force")
    assert rc == 0, f"index failed: {out}"
    return proj


# ---------------------------------------------------------------------------
# Invocation helpers
# ---------------------------------------------------------------------------


def _invoke_diagnose(cwd, *extra, json_mode: bool = True, stdin: str | None = None):
    """Run ``roam [--json] diagnose ...`` in-process under ``cwd``."""
    from roam.cli import cli

    runner = CliRunner()
    args: list[str] = []
    if json_mode:
        args.append("--json")
    args.append("diagnose")
    args.extend(extra)

    old_cwd = os.getcwd()
    try:
        os.chdir(str(cwd))
        result = runner.invoke(cli, args, input=stdin, catch_exceptions=False)
    finally:
        os.chdir(old_cwd)
    return result


def _parse_envelope(result):
    """Parse stdout as a JSON envelope (tolerant of trailing prose)."""
    raw = (result.output or "").lstrip()
    assert raw.startswith("{"), f"expected JSON envelope on stdout, got:\n{result.output!r}"
    decoder = _json.JSONDecoder()
    obj, _end = decoder.raw_decode(raw)
    return obj


# ---------------------------------------------------------------------------
# SMOKE: always-on assertions (sealed today)
# ---------------------------------------------------------------------------


class TestDiagnoseEmptyCorpusSealed:
    """Properties already satisfied by the current cmd_diagnose envelope."""

    def test_empty_corpus_no_crash(self, empty_corpus):
        """``roam diagnose <name>`` on empty corpus exits 0 (no crash)."""
        result = _invoke_diagnose(empty_corpus, "nonexistent_xyz", json_mode=True)
        assert result.exit_code == 0, f"expected exit 0, got {result.exit_code}; output:\n{result.output}"
        # Pattern-1C: stdout MUST be non-empty in --json mode.
        assert result.output.strip(), "stdout must NOT be empty in --json mode"

    def test_empty_corpus_envelope_has_verdict(self, empty_corpus):
        """Empty-corpus unresolved emits ``command=diagnose`` + non-empty verdict."""
        result = _invoke_diagnose(empty_corpus, "nonexistent_xyz", json_mode=True)
        envelope = _parse_envelope(result)
        assert envelope["command"] == "diagnose"
        summary = envelope.get("summary") or {}
        verdict = summary.get("verdict") or ""
        assert isinstance(verdict, str) and verdict, f"summary.verdict must be a non-empty string, got {verdict!r}"

    def test_empty_corpus_explicit_state(self, empty_corpus):
        """W1272 baseline: unresolved on empty corpus discloses
        ``state="not_found"`` + ``resolution="unresolved"``.

        This is the well-handled branch -- assertion locks the W1272 contract
        in place so a future refactor doesn't silently drop the disclosure.
        """
        result = _invoke_diagnose(empty_corpus, "nonexistent_xyz", json_mode=True)
        envelope = _parse_envelope(result)
        summary = envelope["summary"]
        assert summary.get("state") == "not_found", (
            f"unresolved branch should disclose state=not_found; got {summary!r}"
        )
        assert summary.get("resolution") == "unresolved", (
            f"unresolved branch should disclose resolution=unresolved; got {summary!r}"
        )

    def test_empty_corpus_partial_success_set(self, empty_corpus):
        """W1272 baseline: unresolved branch sets ``partial_success: true``."""
        result = _invoke_diagnose(empty_corpus, "nonexistent_xyz", json_mode=True)
        envelope = _parse_envelope(result)
        summary = envelope["summary"]
        assert summary.get("partial_success") is True, (
            f"unresolved branch must set partial_success=True; got {summary!r}"
        )

    def test_empty_corpus_law6_verdict_standalone(self, empty_corpus):
        """LAW 6: verdict is a single line of ASCII that stands alone."""
        result = _invoke_diagnose(empty_corpus, "nonexistent_xyz", json_mode=True)
        envelope = _parse_envelope(result)
        verdict = envelope["summary"]["verdict"]
        assert "\n" not in verdict, f"verdict has embedded newline: {verdict!r}"
        assert verdict.isascii(), f"verdict is not plain ASCII: {verdict!r}"
        assert verdict.strip() not in ("", "?", "verdict", "OK", "ok"), f"verdict is a placeholder: {verdict!r}"
        # The unresolved verdict explicitly names the target -- LAW 6 standalone.
        assert "nonexistent_xyz" in verdict or "not found" in verdict.lower(), (
            f"unresolved verdict must name the target or 'not found'; got {verdict!r}"
        )

    def test_unresolved_target_explicit_resolution(self, empty_corpus):
        """Pattern-1 Variant D negative-baseline: confirm the W1244 contract.

        ``roam diagnose <name>`` on an unindexed name surfaces
        ``resolution="unresolved"`` at BOTH the summary block and the
        top-level envelope (per the W1244 single-mode pattern). This is
        not a re-test of test_cmd_diagnose_resolution.py -- it locks the
        cross-axis disclosure in place when the corpus itself is empty.
        """
        result = _invoke_diagnose(empty_corpus, "nonexistent_xyz", json_mode=True)
        envelope = _parse_envelope(result)
        # Summary axis.
        assert envelope["summary"]["resolution"] == "unresolved"
        # Top-level axis (W1244 dual-emit).
        assert envelope.get("resolution") == "unresolved", (
            f"top-level resolution must mirror summary; got envelope={list(envelope.keys())}"
        )

    def test_clean_corpus_emits_real_diagnosis(self, populated_corpus):
        """Happy-path regression: a real corpus emits a real ranking.

        ``main -> helper`` produces an upstream/downstream graph; the
        verdict should name a top suspect by score+complexity rather than
        falling back to the no-suspects placeholder.
        """
        result = _invoke_diagnose(populated_corpus, "helper", json_mode=True)
        assert result.exit_code == 0, result.output
        envelope = _parse_envelope(result)
        summary = envelope["summary"]
        verdict = summary["verdict"]
        # Either there's a real top-suspect verdict OR the depth window
        # yielded nothing (which would itself be the no-suspects bug shape,
        # caught by the dedicated xfail below). On main->helper we expect
        # at least one upstream entry.
        upstream_count = summary.get("upstream_count", 0)
        downstream_count = summary.get("downstream_count", 0)
        assert (upstream_count + downstream_count) >= 1, (
            f"happy-path expected >=1 suspect for 'helper'; got verdict={verdict!r}, summary={summary!r}"
        )
        # Top-suspect verdict has the canonical "Top suspect:" prefix.
        assert "Top suspect:" in verdict or "risk=" in verdict, (
            f"happy-path verdict should name a top suspect; got {verdict!r}"
        )


# ---------------------------------------------------------------------------
# SMOKE: empty-batch and no-suspects sealed baselines
# ---------------------------------------------------------------------------


class TestDiagnoseEmptyBatchSealed:
    """Empty-batch parse baseline (always-on)."""

    def test_empty_batch_no_crash(self, empty_corpus):
        """``roam diagnose --batch -`` with empty stdin exits 0, emits JSON."""
        result = _invoke_diagnose(empty_corpus, "--batch", "-", json_mode=True, stdin="")
        assert result.exit_code == 0, f"empty batch should exit 0; got {result.exit_code}; output:\n{result.output}"
        assert result.output.strip(), "empty batch stdout must NOT be empty"

    def test_empty_batch_envelope_command(self, empty_corpus):
        """Empty batch emits ``command=diagnose.batch`` + count=0."""
        result = _invoke_diagnose(empty_corpus, "--batch", "-", json_mode=True, stdin="")
        envelope = _parse_envelope(result)
        assert envelope["command"] == "diagnose.batch"
        summary = envelope.get("summary") or {}
        assert summary.get("count") == 0
        # ``results`` should be present as a list (even if empty).
        assert isinstance(envelope.get("results"), list)

    def test_empty_batch_law6_verdict_standalone(self, empty_corpus):
        """LAW 6: empty-batch verdict is single-line ASCII."""
        result = _invoke_diagnose(empty_corpus, "--batch", "-", json_mode=True, stdin="")
        envelope = _parse_envelope(result)
        verdict = envelope["summary"]["verdict"]
        assert "\n" not in verdict
        assert verdict.isascii()
        assert verdict.strip() not in ("", "?", "verdict", "OK", "ok")


class TestDiagnoseNoSuspectsSealed:
    """Resolved-target / no-suspects parse baseline (always-on)."""

    def test_no_suspects_no_crash(self, single_symbol_corpus):
        """``roam diagnose lonely`` on 1-symbol corpus exits 0, emits JSON."""
        result = _invoke_diagnose(single_symbol_corpus, "lonely", json_mode=True)
        assert result.exit_code == 0
        assert result.output.strip()

    def test_no_suspects_envelope_target_resolved(self, single_symbol_corpus):
        """1-symbol corpus: target resolves (``resolution=symbol``)."""
        result = _invoke_diagnose(single_symbol_corpus, "lonely", json_mode=True)
        envelope = _parse_envelope(result)
        summary = envelope["summary"]
        # The symbol resolved -- this is NOT the unresolved branch.
        assert summary.get("resolution") == "symbol", f"1-symbol corpus: target should resolve cleanly; got {summary!r}"
        assert summary.get("upstream_count") == 0
        assert summary.get("downstream_count") == 0

    def test_no_suspects_law6_verdict_standalone(self, single_symbol_corpus):
        """LAW 6: no-suspects verdict is single-line ASCII."""
        result = _invoke_diagnose(single_symbol_corpus, "lonely", json_mode=True)
        envelope = _parse_envelope(result)
        verdict = envelope["summary"]["verdict"]
        assert "\n" not in verdict
        assert verdict.isascii()
        assert verdict.strip() not in ("", "?", "verdict", "OK", "ok")


# ---------------------------------------------------------------------------
# PATTERN-2 PIN 1: empty-batch silent SAFE
# (cmd_diagnose.py:319-332 emits "0 symbol(s) diagnosed" + partial_success=false)
# ---------------------------------------------------------------------------


@pytest.mark.xfail(
    strict=True,
    reason=(
        "W805-M REAL BUG 1 (Pattern-2): cmd_diagnose.py:319-332 emits "
        "verdict 'N symbol(s) diagnosed' with partial_success=false on "
        "empty batch input. A consumer cannot distinguish 'empty input "
        "intentionally' from 'all names stripped to empty' from 'input "
        "file unreadable'. Fix: when len(names)==0, set "
        "partial_success=true + state='empty_batch' + verdict that names "
        "the empty state. Separate fix wave."
    ),
)
def test_empty_batch_partial_success_set(empty_corpus):
    """Pin: empty-batch must set ``partial_success: true``."""
    result = _invoke_diagnose(empty_corpus, "--batch", "-", json_mode=True, stdin="")
    envelope = _parse_envelope(result)
    summary = envelope.get("summary") or {}
    assert summary.get("partial_success") is True, f"empty batch must set partial_success=True; got summary={summary!r}"


@pytest.mark.xfail(
    strict=True,
    reason=(
        "W805-M REAL BUG 1 (Pattern-2): cmd_diagnose.py:319-332 does not "
        "emit a summary.state field on the empty-batch branch. Pattern-2 "
        "requires closed-enum state disclosure ('empty_batch' / "
        "'no_input' / 'empty_input'). Sibling precedent: cmd_describe "
        "W805-I emits state='no_symbols'; cmd_module W805-K emits "
        "state='path_not_found'. Separate fix wave."
    ),
)
def test_empty_batch_explicit_state(empty_corpus):
    """Pin: empty-batch discloses ``summary.state`` via closed enum."""
    result = _invoke_diagnose(empty_corpus, "--batch", "-", json_mode=True, stdin="")
    envelope = _parse_envelope(result)
    summary = envelope.get("summary") or {}
    state = summary.get("state") or envelope.get("state")
    accepted = {"empty_batch", "no_input", "empty_input", "no_names"}
    assert state in accepted, (
        f"empty-batch must disclose state via closed enum; got {state!r}; expected one of {accepted}"
    )


@pytest.mark.xfail(
    strict=True,
    reason=(
        "W805-M REAL BUG 1 (Pattern-2): cmd_diagnose.py:323 builds verdict "
        "as 'N symbol(s) diagnosed' regardless of whether N==0. On empty "
        "input this reads as a confident success ('successfully diagnosed "
        "zero symbols') rather than disclosing the empty-input state. "
        "Fix: when N==0, verdict like 'no symbols to diagnose: empty "
        "batch input' or 'empty batch: 0 symbols supplied'. Separate fix wave."
    ),
)
def test_no_silent_diagnose_success_on_empty(empty_corpus):
    """Pin: empty-batch verdict must name the empty state.

    Anti-shape: verdict ``"0 symbol(s) diagnosed"`` with
    ``partial_success: false`` -- canonical Pattern-2 silent SAFE.
    """
    result = _invoke_diagnose(empty_corpus, "--batch", "-", json_mode=True, stdin="")
    envelope = _parse_envelope(result)
    summary = envelope.get("summary") or {}
    verdict = (summary.get("verdict") or "").lower()
    disclosure_tokens = (
        "empty",
        "no input",
        "no names",
        "no symbols supplied",
        "no symbols to",
    )
    matched = any(tok in verdict for tok in disclosure_tokens)
    assert matched, (
        f"empty-batch verdict must disclose the empty state; got {verdict!r}; expected one of {disclosure_tokens}"
    )


# ---------------------------------------------------------------------------
# PATTERN-2 PIN 2: no-suspects silent SAFE
# (cmd_diagnose.py:509-519 emits "No upstream/downstream symbols found..."
#  with partial_success=false on a clean-resolved 0-suspect diagnosis)
# ---------------------------------------------------------------------------


@pytest.mark.xfail(
    strict=True,
    reason=(
        "W805-M REAL BUG 2 (Pattern-2): cmd_diagnose.py:509-519 emits "
        "verdict 'No upstream/downstream symbols found within depth range.' "
        "with partial_success=false when a target resolves cleanly but has "
        "0 upstream/downstream suspects. The entire purpose of the command "
        "is to rank root-cause suspects; a 0-suspect diagnosis is by "
        "definition a degraded result. Fix: set partial_success=true + "
        "state='no_suspects' (or 'isolated_in_graph' if the depth window "
        "is the limiter). Separate fix wave."
    ),
)
def test_no_root_cause_explicit_state(single_symbol_corpus):
    """Pin: 0-suspect diagnosis discloses ``summary.state``.

    A symbol with 0 callers + 0 callees within the depth window is a
    degraded diagnosis. Pattern-2 requires explicit state disclosure.
    """
    result = _invoke_diagnose(single_symbol_corpus, "lonely", json_mode=True)
    envelope = _parse_envelope(result)
    summary = envelope.get("summary") or {}
    state = summary.get("state") or envelope.get("state")
    accepted = {
        "no_suspects",
        "isolated_in_graph",
        "no_root_cause",
        "no_callers_or_callees",
        "empty_diagnosis",
    }
    assert state in accepted, (
        f"0-suspect diagnosis must disclose state via closed enum; got {state!r}; expected one of {accepted}"
    )


@pytest.mark.xfail(
    strict=True,
    reason=(
        "W805-M REAL BUG 2 (Pattern-2): cmd_diagnose.py:509-519 emits "
        "partial_success=false on the no-suspects branch. A diagnosis "
        "with zero ranked suspects has produced zero actionable signal -- "
        "it is by definition partial-success. Fix: set partial_success=true "
        "whenever upstream_count + downstream_count == 0. Separate fix wave."
    ),
)
def test_no_suspects_partial_success_set(single_symbol_corpus):
    """Pin: 0-suspect diagnosis sets ``partial_success: true``."""
    result = _invoke_diagnose(single_symbol_corpus, "lonely", json_mode=True)
    envelope = _parse_envelope(result)
    summary = envelope.get("summary") or {}
    # Confirm the precondition (target resolved, but 0 suspects).
    assert summary.get("resolution") == "symbol"
    assert summary.get("upstream_count") == 0
    assert summary.get("downstream_count") == 0
    # The pin: partial_success must be true on this branch.
    assert summary.get("partial_success") is True, (
        f"0-suspect diagnosis must set partial_success=True; got summary={summary!r}"
    )
