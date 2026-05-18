"""W805-R - empty-corpus Pattern-2 smoke for ``roam adversarial`` (W805 sweep).

Eighteenth-in-batch of the W805 sweep. cmd_adversarial is a multi-signal
compound that composes cycles + clusters + layers + catalog + dead +
complexity — the same shape as cmd_preflight (W805-L) where 7 distinct
disclosure gaps were pinned. Per CLAUDE.md the "adversarial architecture
review" command frames structural issues as challenges; if it silently
emits ``"changes look clean"`` on a corpus where every check was a no-op
(empty input, no symbols, no edges), an agent reading the verdict will
proceed under a false-clean signal.

W978 first-hypothesis re-run BEFORE writing any test
============================================================
Direct probe of ``roam --json adversarial --staged`` on:

1. An empty-file corpus (``empty.py`` with no symbols) + a staged change
2. A 1-symbol corpus (isolated ``isolated_fn`` with 0 edges, no callers)
   + a staged change

Both probes returned the same envelope shape:

    summary.partial_success      : false
    summary.check_status         : every check reports "ran"
    summary.state                : "all_checks_ran"
    summary.failed_checks        : []
    summary.verdict              : "No architectural challenges found -- changes look clean"
    agent_contract.facts[0]      : (mirrors the verdict)

These are silent passes. The W1259-sealed Pattern-2 guard correctly
detects ``errored:`` outcomes — but it does NOT detect ``"ran"``
outcomes that ran-on-nothing. The 6 helper status branches stamp
``status[name] = "ran"`` even when the helper returned ``[]`` because
its input ``changed_sym_ids`` was empty / there were no overlap symbols
/ the graph had 0 edges. So the orchestrator concludes "all checks ran
cleanly" when in reality "all checks ran on empty input."

W978 findings: 5 Pattern-2 disclosure gaps, ranked by agent-impact
============================================================

1. **CRITICAL: verdict ``"changes look clean"`` cascades from 6 silent
   no-ops** (``cmd_adversarial.py`` L872). When the input set is empty
   (no symbols in changed files / no edges in the graph), every
   ``_check_*`` helper returns ``[]`` and stamps status=``"ran"``. The
   verdict-builder reads ``not challenges and not partial_success`` and
   emits the canonical clean verdict — indistinguishable from "real
   review found nothing wrong." Agents proceed under false clean.

2. **HIGH: each ``_check_*`` helper conflates "ran with data" vs "ran on
   empty input."** The ``status[name] = "ran"`` stamps fire AFTER the
   ``run_detectors`` / ``find_cycles`` / ``detect_layers`` calls
   succeed, regardless of whether those calls had anything to operate
   on. Reference lines:

   - ``_check_new_cycles``      L119-120 (after ``find_cycles`` returns)
   - ``_check_layer_violations`` L196-197 (after ``detect_layers`` returns)
   - ``_check_anti_patterns``   L267-268 (after ``run_detectors`` returns)
   - ``_check_cross_cluster``    L360-361 (after ``detect_clusters`` returns)
   - ``_check_orphaned_symbols`` L446-447 (after ``batched_in`` returns)
   - ``_check_high_fan_out``     L520-521 (after ``batched_in`` returns)

   Fix template: emit ``status[name] = "ran:no_data"`` when the helper
   ran but the underlying graph / detector set / row set was empty;
   keep ``"ran"`` only when at least one substantive row was inspected.

3. **HIGH: ``summary.partial_success=False`` on a no-data corpus**
   (L855). Same shape as W805-L #4 (preflight cascade). When every
   signal silently no-ops, ``errored_checks`` is empty so
   ``partial_success`` defaults to ``False``. Fix template: count
   signals that had real input; when zero, set
   ``partial_success=True`` + ``state="insufficient_signal_data"``.

4. **MEDIUM: ``summary.state="all_checks_ran"`` says nothing about
   data presence** (L930). Closed enum is currently
   ``{partial_adversarial, all_checks_ran}``. Both states are emitted
   on the no-data corpus today as ``all_checks_ran``. Fix template:
   add a third enum member ``no_data_in_corpus`` or
   ``insufficient_input``.

5. **LOW: ``agent_contract.facts[0]`` mirrors the silent-clean verdict**
   (L895). LAW 4 anchoring is correct (``challenges``) but the fact
   itself is a lie when the corpus had nothing to challenge. Cascade
   from #1 — sealing #1 seals this as a side effect.

The clean-corpus path is verified by the
``test_clean_corpus_emits_real_adversarial`` positive coverage below.

This wave pins #1-#4 via xfail-strict. #5 is a downstream cascade.

DO NOT FIX this wave - accumulate xfail-strict pins only.

Run isolation:
    python -m pytest tests/test_w805_r_cmd_adversarial_empty_corpus.py -x -n 0

Regression baseline:
    python -m pytest tests/test_adversarial*.py -x -n 0
"""

from __future__ import annotations

import json as _json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from click.testing import CliRunner

sys.path.insert(0, str(Path(__file__).parent))
from conftest import index_in_process  # noqa: E402

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _git_init_with_baseline(proj: Path) -> None:
    """Initialize a git repo and commit the baseline files."""
    subprocess.run(["git", "init", "-q"], cwd=proj, check=True)
    subprocess.run(
        ["git", "-c", "user.email=t@t", "-c", "user.name=t", "add", "."],
        cwd=proj,
        check=True,
    )
    subprocess.run(
        ["git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "init"],
        cwd=proj,
        check=True,
    )


def _stage_change(proj: Path, file_path: str, new_content: str) -> None:
    """Stage a content change to ``file_path`` so ``adversarial --staged``
    has a non-empty changeset to operate on.
    """
    (proj / file_path).write_text(new_content, encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=proj, check=True)


def _invoke_adversarial(runner: CliRunner, cwd: Path, *extra, json_mode: bool = True):
    """Invoke ``roam adversarial`` via the Click group so the top-level
    ``--json`` flag is honoured by ``ctx.obj``.
    """
    from roam.cli import cli

    args: list[str] = []
    if json_mode:
        args.append("--json")
    args.append("adversarial")
    args.extend(extra)

    old_cwd = os.getcwd()
    try:
        os.chdir(str(cwd))
        result = runner.invoke(cli, args, catch_exceptions=False)
    finally:
        os.chdir(old_cwd)
    return result


def _parse_envelope(result) -> dict:
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
    """Indexed project with a single empty .py file + a staged change.

    Indexer runs cleanly and extracts zero function/class/method symbols.
    ``changed_sym_ids`` resolves to ``set()``. Every adversarial helper
    short-circuits on the empty-input branch yet stamps ``status="ran"``.
    """
    proj = tmp_path / "empty_adv_corpus"
    proj.mkdir()
    (proj / ".gitignore").write_text(".roam/\n")
    (proj / "empty.py").write_text("")
    _git_init_with_baseline(proj)
    _stage_change(proj, "empty.py", "x = 1\n")
    monkeypatch.chdir(proj)
    out, rc = index_in_process(proj, "--force")
    assert rc == 0, f"index failed: {out}"
    return proj


@pytest.fixture
def isolated_symbol_corpus(tmp_path, monkeypatch):
    """Indexed project with 1 file containing 1 isolated function + a
    staged change.

    The function has 0 incoming edges (orphan), 0 outgoing edges (no
    callees), is not in any cycle/cluster/layer, and ``run_detectors``
    finds no anti-patterns. Every adversarial helper executes but
    operates on an effectively trivial graph — the canonical
    no-real-signal empty-state probe.
    """
    proj = tmp_path / "isolated_adv_corpus"
    proj.mkdir()
    (proj / ".gitignore").write_text(".roam/\n")
    (proj / "lone.py").write_text(
        "def isolated_fn():\n    return 1\n",
        encoding="utf-8",
    )
    _git_init_with_baseline(proj)
    _stage_change(proj, "lone.py", "def isolated_fn():\n    return 2\n")
    monkeypatch.chdir(proj)
    out, rc = index_in_process(proj, "--force")
    assert rc == 0, f"index failed: {out}"
    return proj


@pytest.fixture
def clean_corpus(tmp_path, monkeypatch):
    """Indexed project with real symbols + edges - regression baseline."""
    proj = tmp_path / "clean_adv_corpus"
    proj.mkdir()
    (proj / ".gitignore").write_text(".roam/\n")
    (proj / "app.py").write_text(
        "def helper():\n    return 1\n\ndef caller():\n    return helper()\n",
        encoding="utf-8",
    )
    _git_init_with_baseline(proj)
    _stage_change(
        proj,
        "app.py",
        "def helper():\n    return 1\n\ndef caller():\n    return helper() + 1\n",
    )
    monkeypatch.chdir(proj)
    out, rc = index_in_process(proj, "--force")
    assert rc == 0, f"index failed: {out}"
    return proj


# ---------------------------------------------------------------------------
# Sealed-today contracts (always-on smoke)
# ---------------------------------------------------------------------------


class TestAdversarialEmptyCorpusSealed:
    """Properties already satisfied by the current cmd_adversarial envelope."""

    def test_empty_corpus_no_crash(self, cli_runner, empty_corpus):
        """``roam adversarial --staged`` on empty corpus exits 0, no crash."""
        result = _invoke_adversarial(cli_runner, empty_corpus, "--staged", json_mode=True)
        assert result.exit_code == 0, f"expected exit 0, got {result.exit_code}; output:\n{result.output}"
        # Pattern-1C: stdout MUST be non-empty in --json mode.
        assert result.output.strip(), "stdout must NOT be empty in --json mode"

    def test_empty_corpus_envelope_has_verdict(self, cli_runner, empty_corpus):
        """Envelope carries ``command=adversarial`` + non-empty verdict."""
        result = _invoke_adversarial(cli_runner, empty_corpus, "--staged", json_mode=True)
        envelope = _parse_envelope(result)
        assert envelope["command"] == "adversarial"
        summary = envelope.get("summary") or {}
        verdict = summary.get("verdict") or ""
        assert isinstance(verdict, str) and verdict, f"summary.verdict must be a non-empty string, got {verdict!r}"

    def test_empty_corpus_law6_verdict_standalone(self, cli_runner, empty_corpus):
        """LAW 6: verdict line stands alone (single line, non-placeholder)."""
        result = _invoke_adversarial(cli_runner, empty_corpus, "--staged", json_mode=True)
        envelope = _parse_envelope(result)
        verdict = envelope["summary"]["verdict"]
        assert "\n" not in verdict, f"verdict has embedded newline: {verdict!r}"
        assert verdict.strip() not in ("", "?", "verdict", "OK", "ok"), f"verdict is a placeholder: {verdict!r}"

    def test_empty_corpus_check_status_present(self, cli_runner, empty_corpus):
        """The W1259 Pattern-2 substrate stays wired: check_status is populated."""
        result = _invoke_adversarial(cli_runner, empty_corpus, "--staged", json_mode=True)
        envelope = _parse_envelope(result)
        summary = envelope["summary"]
        check_status = summary.get("check_status")
        assert isinstance(check_status, dict), f"summary.check_status must be a dict; got {check_status!r}"
        # All 6 canonical checks must appear in the status map.
        for name in (
            "new_cycles",
            "layer_violations",
            "anti_patterns",
            "cross_cluster",
            "orphaned_symbols",
            "high_fan_out",
        ):
            assert name in check_status, f"check_status missing {name!r}; got {check_status!r}"

    def test_clean_corpus_emits_real_adversarial(self, cli_runner, clean_corpus):
        """Happy-path positive coverage: a populated corpus emits a real envelope.

        Every signal slot must be present, status must be ``"ran"``, the
        envelope shape must include the canonical summary fields, and
        the verdict must take one of the non-partial branches.
        """
        result = _invoke_adversarial(cli_runner, clean_corpus, "--staged", json_mode=True)
        assert result.exit_code == 0, result.output
        envelope = _parse_envelope(result)
        summary = envelope["summary"]
        # Canonical summary fields all present.
        for field in (
            "challenges",
            "critical",
            "high",
            "warning",
            "info",
            "changed_files",
            "partial_success",
            "failed_checks",
            "check_status",
            "state",
            "verdict",
        ):
            assert field in summary, f"clean envelope missing summary field {field!r}; got {sorted(summary.keys())}"
        # The 6 check statuses are all "ran" on a clean populated corpus.
        check_status = summary["check_status"]
        for name in (
            "new_cycles",
            "layer_violations",
            "anti_patterns",
            "cross_cluster",
            "orphaned_symbols",
            "high_fan_out",
        ):
            assert check_status.get(name) == "ran", (
                f"clean corpus: {name!r} status must be 'ran'; got {check_status.get(name)!r}"
            )
        # changed_files reflects the staged change.
        assert summary["changed_files"] >= 1


# ---------------------------------------------------------------------------
# Pattern-2 BUG PIN #1 - CRITICAL: silent "changes look clean" on empty corpus
# ---------------------------------------------------------------------------


@pytest.mark.xfail(
    strict=True,
    reason=(
        "W805-R REAL BUG (CRITICAL): cmd_adversarial.py L872 emits "
        "'No architectural challenges found -- changes look clean' "
        "when every check ran on empty input (empty.py with 0 symbols, "
        "0 edges, 0 detector findings). The 6 helpers stamp "
        "status='ran' after their underlying graph/detector call "
        "returns regardless of input size, so the orchestrator "
        "concludes 'all 6 checks ran cleanly' when reality is 'all 6 "
        "checks ran on empty input'. An agent reading 'changes look "
        "clean' proceeds under a false-clean signal. Fix template: "
        "emit verdict='no signal in changed files (corpus has 0 "
        "indexed symbols)' OR set partial_success=True + "
        "state='insufficient_signal_data' when changed_sym_ids is "
        "empty. Separate fix wave."
    ),
)
def test_empty_corpus_no_silent_clean_verdict(cli_runner, empty_corpus):
    """Pin: empty corpus must NOT emit the canonical clean verdict.

    The cascade: ``empty.py`` indexes to 0 symbols -> ``changed_sym_ids
    = set()`` -> every helper hits the "no input" branch and stamps
    status='ran' -> orchestrator sees ``challenges=[]`` and
    ``partial_success=False`` -> emits the canonical clean verdict
    indistinguishable from a real reviewed-and-clean state.
    """
    result = _invoke_adversarial(cli_runner, empty_corpus, "--staged", json_mode=True)
    envelope = _parse_envelope(result)
    verdict = envelope["summary"]["verdict"].lower()
    # The bug: verdict claims clean. The fix surfaces no-data state.
    assert (
        "clean" not in verdict or "no" in verdict and ("signal" in verdict or "data" in verdict or "indexed" in verdict)
    ), (
        f"empty corpus must NOT emit the silent-clean verdict; got "
        f"{envelope['summary']['verdict']!r}. Agents reading 'changes "
        f"look clean' proceed under a false-clean signal."
    )


# ---------------------------------------------------------------------------
# Pattern-2 BUG PIN #2 - HIGH: check_status="ran" conflates ran-with-data
# vs ran-on-empty-input
# ---------------------------------------------------------------------------


@pytest.mark.xfail(
    strict=True,
    reason=(
        "W805-R REAL BUG (HIGH): each _check_* helper in "
        "cmd_adversarial.py stamps status='ran' after its underlying "
        "call (find_cycles / detect_layers / run_detectors / "
        "detect_clusters / batched_in) returns, regardless of whether "
        "the input set was empty. On the isolated_symbol_corpus the "
        "graph has 1 node + 0 edges, run_detectors finds 0 findings, "
        "detect_clusters returns 0 clusters, detect_layers returns "
        "{1:0}. Every helper returns [] but stamps 'ran'. The "
        "orchestrator cannot distinguish 'helper ran on real graph + "
        "found 0 violations' from 'helper ran on trivial graph + had "
        "nothing to inspect'. Fix template: emit 'ran:no_data' or "
        "'ran:empty_input' when the underlying result set was empty. "
        "Reference lines: L119, L196, L267, L360, L446, L520. "
        "Separate fix wave."
    ),
)
def test_check_status_discloses_no_data_state(cli_runner, isolated_symbol_corpus):
    """Pin: check_status entries must disclose ran-with-data vs ran-empty.

    The isolated_symbol_corpus has 1 symbol + 0 edges. Every check
    runs against effectively empty input (cycles requires SCCs of
    size>=2; layers requires edges; clusters requires edges;
    anti_patterns requires detector findings; high_fan_out requires
    >10 out-edges). The status map should mark these as
    'ran:no_data' or similar, not bare 'ran'.
    """
    result = _invoke_adversarial(cli_runner, isolated_symbol_corpus, "--staged", json_mode=True)
    envelope = _parse_envelope(result)
    check_status = envelope["summary"]["check_status"]
    # At least one of the 6 checks must disclose its no-data state.
    no_data_disclosed = any(
        isinstance(s, str)
        and (s.startswith("ran:") or s.endswith(":no_data") or s.endswith(":no_input") or s == "no_data")
        for s in check_status.values()
    )
    assert no_data_disclosed, (
        f"check_status must disclose no-data state for at least one of "
        f"the 6 helpers when the graph is trivial; got {check_status!r}. "
        f"Agents reading 'ran' cannot distinguish 'real check, 0 "
        f"violations' from 'check had no input to inspect'."
    )


# ---------------------------------------------------------------------------
# Pattern-2 BUG PIN #3 - HIGH: summary.partial_success=False on no-data corpus
# ---------------------------------------------------------------------------


@pytest.mark.xfail(
    strict=True,
    reason=(
        "W805-R REAL BUG (HIGH): cmd_adversarial.py L855 sets "
        "partial_success=bool(errored_checks). On the empty corpus, "
        "errored_checks is [] (every check 'ran' on empty input), so "
        "partial_success=False. Same cascading silent fallback as "
        "W805-L #4 (preflight cascade). The verdict 'changes look "
        "clean' is being driven by 6 silent no-ops while the envelope "
        "claims partial_success=False. Fix template: count helpers "
        "whose underlying result set had real signal; when 0 of 6 had "
        "real signal AND challenges=[], set partial_success=True + "
        "state='insufficient_signal_data'. Separate fix wave."
    ),
)
def test_empty_corpus_partial_success_when_no_signal(cli_runner, empty_corpus):
    """Pin: when 6/6 checks ran on empty input, partial_success=True.

    The empty corpus has 0 symbols. Every check returns immediately
    because changed_sym_ids is empty. partial_success=False here is
    the canonical Pattern-2 cascade — there's no underlying data to
    certify cleanness from.
    """
    result = _invoke_adversarial(cli_runner, empty_corpus, "--staged", json_mode=True)
    envelope = _parse_envelope(result)
    summary = envelope["summary"]
    assert summary.get("partial_success") is True, (
        f"empty corpus has 0 symbols and 0 underlying signals; partial_success MUST be True. Got summary={summary!r}"
    )


# ---------------------------------------------------------------------------
# Pattern-2 BUG PIN #4 - MEDIUM: summary.state="all_checks_ran" hides no-data
# ---------------------------------------------------------------------------


@pytest.mark.xfail(
    strict=True,
    reason=(
        "W805-R REAL BUG (MEDIUM): cmd_adversarial.py L930 emits "
        "summary.state='all_checks_ran' on the empty corpus, "
        "indistinguishable from the state emitted by a populated "
        "corpus where every check ran cleanly. The closed enum today "
        "is {partial_adversarial, all_checks_ran}; neither member "
        "discloses 'all 6 checks ran on empty input'. Fix template: "
        "add a third enum member 'no_data_in_corpus' or "
        "'insufficient_input' and emit it when changed_sym_ids is "
        "empty OR when all 6 helpers report ran:no_data. Separate "
        "fix wave."
    ),
)
def test_empty_corpus_state_discloses_no_data(cli_runner, empty_corpus):
    """Pin: summary.state must distinguish 'all checks ran cleanly' from
    'all checks ran on empty input'.

    The current closed enum collapses both into 'all_checks_ran'.
    """
    result = _invoke_adversarial(cli_runner, empty_corpus, "--staged", json_mode=True)
    envelope = _parse_envelope(result)
    state = envelope["summary"].get("state")
    assert state in {
        "no_data_in_corpus",
        "insufficient_input",
        "no_signal_data",
        "empty_input",
    }, (
        f"empty corpus summary.state must disclose the no-data state; "
        f"got {state!r}. Agents reading 'all_checks_ran' cannot "
        f"distinguish a real clean review from a no-data cascade."
    )


# ---------------------------------------------------------------------------
# Drift guard: the W1259-sealed Pattern-2 (errored:) substrate
# ---------------------------------------------------------------------------


def test_w1259_errored_substrate_still_wired(cli_runner, empty_corpus, monkeypatch):
    """Drift guard: the W1259 errored-check substrate stays wired.

    If a check raises, it must record an ``errored:<reason>`` status
    AND set partial_success=True AND name the failed check in
    failed_checks AND emit a PARTIAL verdict. This is the sealed
    contract from W1259 (see test_adversarial_pattern2_guard.py). We
    re-assert it here so a regression on the empty-corpus path can't
    silently break the errored-path too.
    """
    import roam.commands.cmd_adversarial as mod

    def fake_new_cycles(_conn, _ids, status=None):
        if status is not None:
            status["new_cycles"] = "errored:build_symbol_graph:RuntimeError"
        return []

    monkeypatch.setattr(mod, "_check_new_cycles", fake_new_cycles)

    result = _invoke_adversarial(cli_runner, empty_corpus, "--staged", json_mode=True)
    assert result.exit_code == 0, result.output
    envelope = _parse_envelope(result)
    summary = envelope["summary"]
    assert summary.get("partial_success") is True
    assert "new_cycles" in summary["failed_checks"]
    assert summary["state"] == "partial_adversarial"
    assert summary["verdict"].startswith("PARTIAL"), summary["verdict"]
