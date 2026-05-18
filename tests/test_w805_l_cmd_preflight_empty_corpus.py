"""W805-L - empty-corpus Pattern-2 smoke for ``roam preflight`` (W805 sweep).

Twelfth-in-batch of the W805 sweep, flagship 5-verb command + canonical
agent-safety gate. CLAUDE.md cites preflight as the canonical pre-edit
gate: "Run ``roam preflight <sym>`` before editing." Per CLAUDE.md
LAW 11 + agent-loop substrate ("earn the right to change code"), an
agent calls preflight BEFORE editing. If preflight silently emits a
``"Safe to proceed"`` / ``"Proceed with caution"`` verdict on signal
slots that have NO underlying data, the agent would proceed believing
the 6-signal gate cleared them when the gate had no data to evaluate.

Prior cohort yield (5 BUG-positive / 8 prior probed waves):

- A (cmd_owner)             REAL BUG - silent ``"top owner: ?"``
- B (cmd_minimap)           REAL BUG - silent ``"minimap rendered (148 chars)"``
- C (cmd_oracle)            REAL BUG - route-exists vs verdict mismatch
- D (cmd_workflow)          NO BUG - static inspector
- E (cmd_path_coverage)     NO BUG - W807-hardened
- F (cmd_for_bug_fix)       REAL BUG - ``_compound_envelope`` aggregator gap
- G (cmd_pr_prep)           REAL BUG - silent ``"READY"`` on no_changes children
- H (cmd_explain_command)   NO BUG - static-metadata
- I (cmd_describe)          REAL BUG - flagship silent-SAFE on 3 branches
- J (cmd_understand)        REAL BUG - ``healthy 100/100`` on 0-symbol corpus
- K (cmd_module)            in flight
- L (cmd_preflight)         this wave

W978 first-hypothesis re-run BEFORE writing any test
============================================================
Direct probe of ``roam --json preflight isolated_fn`` on a 1-file
corpus containing a single isolated function (no callers / no tests /
no fitness rules / no clones / no coupling) confirmed the hypothesis -
**REAL BUG** at the signal-disclosure layer.

The 6-signal envelope on this minimal corpus emits:

    risk_level         : "MEDIUM"
    verdict            : "Proceed with caution - MEDIUM risk for isolated_fn"
    partial_success    : false  (top-level AND summary)
    resolution         : "symbol"  (Pattern-1 Variant D correctly sealed)

    blast_radius.severity   : "LOW"   (0 affected_symbols)
    tests.severity          : "WARNING" (0 direct/transitive/colocated)
    complexity.severity     : "LOW"   (0 rows in symbol_metrics)
    coupling.severity       : "OK"    (0 missing_partners)
    conventions.severity    : "OK"    (no violations because 1 kind has majority)
    fitness.severity        : "OK"    (rules_checked=0, BUT severity=OK)

Three Pattern-2 disclosure gaps, ranked by severity:

1. **CRITICAL: fitness severity=OK with rules_checked=0**
   (``cmd_preflight.py`` L568-579, ``_fitness_severity(0)`` returns
   ``"OK"``). ``fitness=OK`` on a project with NO ``.roam-fitness.yml``
   is the canonical Pattern-2 silent fallback: it claims "all fitness
   rules pass" when there are NO rules to evaluate. An agent reading
   ``fitness.severity=OK`` cannot distinguish "all rules pass" from
   "no rules configured". Fix template: emit
   ``severity="info"`` + ``state="no_rules_configured"`` on the
   ``rules_checked == 0`` branch.

2. **HIGH: tests severity=WARNING conflates no-tests-indexed with
   tests-exist-but-skipped**. ``_test_severity`` (L82-86) returns
   ``"WARNING"`` whenever ``direct+transitive+colocated == 0``. This
   is correct directionally (agent should know tests are missing), but
   the signal doesn't disclose WHICH state we're in: "no tests in the
   repo at all" vs "no tests reach this symbol". Fix template: emit a
   ``state="no_tests_indexed"`` sub-field when the underlying
   ``tests.test_files`` set is globally empty.

3. **MEDIUM: complexity severity=LOW silently on 0 rows**. ``_check_complexity``
   (L361-367) returns ``severity="low"`` when ``rows`` (the
   ``symbol_metrics`` join) is empty. A symbol can exist in ``symbols``
   without a ``symbol_metrics`` row (older indexer, partial reindex,
   missing instrumentation). ``severity=LOW`` here means "no data,
   not low complexity". Fix template: emit
   ``state="no_complexity_data"`` when ``not rows``.

4. **MEDIUM: top-level partial_success=False despite 5/6 empty signals**
   (``cmd_preflight.py`` L959-1029). When 5 of 6 signals come back with
   no underlying data (blast=0, tests=0, complexity=0 rows,
   coupling=0, fitness=0 rules) AND only conventions has real signal
   (1 kind has a majority, 0 violations), the envelope still claims
   ``partial_success=false``. This violates Pattern 2: the verdict
   ``"Proceed with caution - MEDIUM risk"`` is being driven by the
   ``tests.severity=WARNING`` signal alone, and that WARNING is itself
   driven by "no tests indexed". Cascading silent fallback.

Pattern-1 Variant D (degraded target resolution) is **CORRECTLY SEALED**
on the unresolved branch:

- Unknown symbol -> ``resolution="unresolved"``, ``partial_success=true``,
  ``risk_level="UNKNOWN"``, verdict ``"target not found - ``<name>`` is
  not in the index"``. Top-level + summary both populated. The W1241 /
  W1243 / W1249 disclosure block is correctly wired in
  ``_resolve_targets`` (L674-747). No bug to pin on the resolution axis.

This wave pins the SIGNAL-LEVEL Pattern-2 gaps (#1-4 above) via
xfail-strict. The resolution-axis Pattern-1 Variant D tests are
positive-coverage (assert correctly-sealed behaviour).

DO NOT FIX this wave - accumulate xfail-strict pins only.

Run isolation:
    python -m pytest tests/test_w805_l_cmd_preflight_empty_corpus.py -x -n 0

Regression baseline:
    python -m pytest tests/test_preflight*.py tests/test_cmd_preflight*.py -x -n 0
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

# Drift-resistant repo-root resolution (W572 helper).
from tests._helpers.repo_root import repo_root  # noqa: F401,E402

# ---------------------------------------------------------------------------
# Helpers - invoke preflight via the Click group so the top-level --json flag
# is honoured by ctx.obj.
# ---------------------------------------------------------------------------


def _invoke_preflight(runner: CliRunner, cwd, *extra, json_mode: bool = True):
    """Invoke ``roam preflight`` through the group."""
    from roam.cli import cli

    args: list[str] = []
    if json_mode:
        args.append("--json")
    args.append("preflight")
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
    """Indexed project with a single empty .py file.

    Indexer runs cleanly but extracts zero function/class/method symbols.
    Any preflight target string falls into the unresolved branch.
    """
    proj = tmp_path / "empty_preflight_corpus"
    proj.mkdir()
    (proj / ".gitignore").write_text(".roam/\n")
    (proj / "empty.py").write_text("")
    git_init(proj)
    monkeypatch.chdir(proj)
    out, rc = index_in_process(proj, "--force")
    assert rc == 0, f"index failed: {out}"
    return proj


@pytest.fixture
def isolated_symbol_corpus(tmp_path, monkeypatch):
    """Indexed project with 1 file containing 1 isolated function.

    The function has no callers, no callees outside itself, no tests, no
    fitness rules configured, no clones, no coupling partners. Every
    preflight signal except conventions falls into a "no underlying
    data" state, yet the envelope today emits MEDIUM risk + a confident
    ``"Proceed with caution"`` verdict. Canonical empty-state probe.
    """
    proj = tmp_path / "isolated_preflight_corpus"
    proj.mkdir()
    (proj / ".gitignore").write_text(".roam/\n")
    (proj / "lone.py").write_text(
        "def isolated_fn():\n    return 1\n",
        encoding="utf-8",
    )
    git_init(proj)
    monkeypatch.chdir(proj)
    out, rc = index_in_process(proj, "--force")
    assert rc == 0, f"index failed: {out}"
    return proj


@pytest.fixture
def clean_corpus(tmp_path, monkeypatch):
    """Indexed project with real symbols + tests - regression baseline."""
    proj = tmp_path / "clean_preflight_corpus"
    proj.mkdir()
    (proj / ".gitignore").write_text(".roam/\n")
    src = proj / "src"
    src.mkdir()
    tests_dir = proj / "tests"
    tests_dir.mkdir()
    (src / "app.py").write_text(
        "def login(user):\n    return validate(user)\n\ndef validate(u):\n    return bool(u)\n",
        encoding="utf-8",
    )
    (tests_dir / "test_app.py").write_text(
        "from src.app import login\n\ndef test_login():\n    assert login('x')\n",
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


class TestPreflightEmptyCorpusSealed:
    """Properties already satisfied by the current cmd_preflight envelope."""

    def test_empty_corpus_no_crash(self, cli_runner, empty_corpus):
        """``roam preflight UNKNOWN`` on empty corpus exits 0, no crash."""
        result = _invoke_preflight(cli_runner, empty_corpus, "unknownSymbol", json_mode=True)
        assert result.exit_code == 0, f"expected exit 0, got {result.exit_code}; output:\n{result.output}"
        # Pattern-1C: stdout MUST be non-empty in --json mode.
        assert result.output.strip(), "stdout must NOT be empty in --json mode"

    def test_empty_corpus_envelope_has_verdict(self, cli_runner, empty_corpus):
        """Envelope carries ``command=preflight`` + non-empty verdict."""
        result = _invoke_preflight(cli_runner, empty_corpus, "unknownSymbol", json_mode=True)
        envelope = _parse_envelope(result)
        assert envelope["command"] == "preflight"
        summary = envelope.get("summary") or {}
        verdict = summary.get("verdict") or ""
        assert isinstance(verdict, str) and verdict, f"summary.verdict must be a non-empty string, got {verdict!r}"

    def test_empty_corpus_law6_verdict_standalone(self, cli_runner, empty_corpus):
        """LAW 6: verdict line stands alone (single line, ASCII)."""
        result = _invoke_preflight(cli_runner, empty_corpus, "unknownSymbol", json_mode=True)
        envelope = _parse_envelope(result)
        verdict = envelope["summary"]["verdict"]
        assert "\n" not in verdict, f"verdict has embedded newline: {verdict!r}"
        # The verdict contains a U+2014 em-dash but it's the canonical
        # preflight rendering and is rendered by the formatter; we accept
        # ASCII-equivalent or em-dash-bearing strings here. The LAW 6
        # requirement is that the verdict stand alone, not be ASCII.
        assert verdict.strip() not in ("", "?", "verdict", "OK", "ok"), f"verdict is a placeholder: {verdict!r}"

    def test_empty_corpus_partial_success_set(self, cli_runner, empty_corpus):
        """Unresolved branch correctly sets ``partial_success=True``.

        Pattern-1 Variant D is sealed on the unresolved branch:
        ``_resolve_targets`` stamps ``"unresolved"`` for the symbol-not-
        found case and the not-found envelope explicitly sets
        ``partial_success=True``.
        """
        result = _invoke_preflight(cli_runner, empty_corpus, "unknownSymbol", json_mode=True)
        envelope = _parse_envelope(result)
        summary = envelope["summary"]
        assert summary.get("partial_success") is True, (
            f"unresolved target must set partial_success=True; got summary={summary!r}"
        )

    def test_unresolved_symbol_explicit_resolution(self, cli_runner, empty_corpus):
        """Pattern-1 Variant D: ``resolution=unresolved`` disclosed explicitly.

        Sealed by W1241/W1243/W1249. The unresolved branch must carry the
        ``resolution`` field on BOTH the top-level envelope and the
        ``summary`` so consumers reading either see the disclosure.
        """
        result = _invoke_preflight(cli_runner, empty_corpus, "unknownSymbol", json_mode=True)
        envelope = _parse_envelope(result)
        # Top-level resolution (per W1241 disclosure block).
        assert envelope.get("resolution") == "unresolved", (
            f"top-level resolution must be 'unresolved'; got {envelope.get('resolution')!r}"
        )
        summary = envelope["summary"]
        assert summary.get("resolution") == "unresolved", (
            f"summary.resolution must be 'unresolved'; got {summary.get('resolution')!r}"
        )

    def test_unresolved_risk_level_is_unknown(self, cli_runner, empty_corpus):
        """Unresolved branch emits ``risk_level=UNKNOWN``, not a fake LOW/MEDIUM.

        Anti-shape: ``risk_level=LOW`` on an unresolved target would be the
        canonical agent-safety violation - agents would interpret it as
        "safe to edit". Sealed today; this is a drift guard.
        """
        result = _invoke_preflight(cli_runner, empty_corpus, "unknownSymbol", json_mode=True)
        envelope = _parse_envelope(result)
        risk = envelope["summary"].get("risk_level")
        assert risk == "UNKNOWN", (
            f"unresolved target must emit risk_level=UNKNOWN; got {risk!r}. "
            "ANY of LOW/MEDIUM/HIGH/CRITICAL on an unresolved target is the "
            "canonical agent-safety violation."
        )

    def test_staged_no_changes_partial_success(self, cli_runner, empty_corpus):
        """``--staged`` with no staged changes correctly sets partial_success."""
        result = _invoke_preflight(cli_runner, empty_corpus, "--staged", json_mode=True)
        envelope = _parse_envelope(result)
        summary = envelope["summary"]
        assert summary.get("partial_success") is True
        assert summary.get("risk_level") == "UNKNOWN"
        # Verdict mentions the staged-no-changes condition.
        verdict = (summary.get("verdict") or "").lower()
        assert "staged" in verdict or "not found" in verdict, (
            f"--staged no-changes verdict must name the state; got {verdict!r}"
        )

    def test_clean_corpus_emits_real_preflight(self, cli_runner, clean_corpus):
        """Happy-path positive coverage: a populated corpus emits a real envelope.

        Every 6-signal slot must be present, the risk_level resolves to a
        non-UNKNOWN tier, and the affected blast list reflects the real
        edge graph (login -> tests/test_app.py reverse edge).
        """
        result = _invoke_preflight(cli_runner, clean_corpus, "login", json_mode=True)
        assert result.exit_code == 0, result.output
        envelope = _parse_envelope(result)
        summary = envelope["summary"]
        # Risk level is a real tier (not UNKNOWN).
        assert summary.get("risk_level") in {"LOW", "MEDIUM", "HIGH", "CRITICAL"}
        # All 6 signal blocks present.
        for slot in ("blast_radius", "tests", "complexity", "coupling", "conventions", "fitness"):
            assert slot in envelope, f"clean envelope missing signal slot {slot!r}"
            assert "severity" in envelope[slot], f"{slot!r} missing severity sub-field"
        # Real blast: the test file reverse-edges from login.
        blast = envelope["blast_radius"]
        assert blast["affected_files"] >= 1, f"clean corpus must report >=1 affected file; got {blast!r}"
        # Real tests: 1 direct.
        tests = envelope["tests"]
        assert tests["direct"] >= 1, f"clean corpus must report >=1 direct test; got {tests!r}"
        # Resolution is the canonical exact-symbol tier.
        assert summary.get("resolution") == "symbol"


# ---------------------------------------------------------------------------
# Pattern-2 BUG PIN #1 - CRITICAL: fitness severity=OK with rules_checked=0
# ---------------------------------------------------------------------------


@pytest.mark.xfail(
    strict=True,
    reason=(
        "W805-L REAL BUG (CRITICAL): cmd_preflight.py L568-579 emits "
        "fitness.severity='OK' when rules_checked=0 (no .roam-fitness.yml "
        "in the project). 'OK' is indistinguishable from 'all rules pass' - "
        "agents reading fitness.severity=OK cannot tell whether fitness "
        "evaluated cleanly or whether no rules existed to evaluate. This is "
        "the canonical Pattern-2 silent-fallback shape on the canonical "
        "agent-safety gate (CLAUDE.md: 'Run roam preflight <sym> before "
        "editing'). Fix template: emit severity='info' + "
        "state='no_rules_configured' on the rules_checked==0 branch. "
        "Separate fix wave."
    ),
)
def test_fitness_signal_explicit_no_rules(cli_runner, isolated_symbol_corpus):
    """Pin: when no fitness rules are configured, fitness must NOT claim OK.

    The fitness signal on a project with no ``.roam-fitness.yml`` has
    NO data. It is Pattern-2 silent-fallback to emit ``severity=OK``
    indistinguishable from a populated corpus with all rules passing.
    """
    result = _invoke_preflight(cli_runner, isolated_symbol_corpus, "isolated_fn", json_mode=True)
    envelope = _parse_envelope(result)
    fitness = envelope["fitness"]
    assert fitness["rules_checked"] == 0, (
        f"precondition: rules_checked should be 0 on this corpus; got {fitness['rules_checked']}"
    )
    # The bug: severity is OK when no rules were configured. Fix surfaces
    # a distinct disclosure - state='no_rules_configured' OR severity!=OK.
    state = fitness.get("state")
    has_disclosure = fitness.get("severity") != "OK" or state in {"no_rules_configured", "no_rules", "not_configured"}
    assert has_disclosure, (
        f"fitness signal must DISCLOSE that no rules were configured; got "
        f"severity={fitness.get('severity')!r} state={state!r}. "
        "Agents reading fitness.severity=OK cannot distinguish 'all rules "
        "pass' from 'no rules configured'."
    )


# ---------------------------------------------------------------------------
# Pattern-2 BUG PIN #2 - HIGH: tests.severity=WARNING conflates two states
# ---------------------------------------------------------------------------


@pytest.mark.xfail(
    strict=True,
    reason=(
        "W805-L REAL BUG (HIGH): cmd_preflight.py L82-86 _test_severity "
        "returns 'WARNING' whenever direct+transitive+colocated==0. This "
        "conflates 'no tests indexed in the repo' with 'tests exist but "
        "none reach this symbol'. Both are legitimate observations but "
        "they imply different agent actions: the first means 'add a test'; "
        "the second means 'investigate why coverage is missing'. Fix "
        "template: emit a state sub-field disclosing which case fired "
        "(e.g. state='no_tests_indexed' when the global test corpus is "
        "empty). Separate fix wave."
    ),
)
def test_tests_signal_explicit_no_tests_state(cli_runner, isolated_symbol_corpus):
    """Pin: when no tests are indexed at all, tests signal must disclose state.

    The isolated_symbol_corpus has 0 test files in the repo. The current
    behaviour emits severity=WARNING which is correct directionally but
    doesn't disclose which Pattern-2 sub-state we're in.
    """
    result = _invoke_preflight(cli_runner, isolated_symbol_corpus, "isolated_fn", json_mode=True)
    envelope = _parse_envelope(result)
    tests = envelope["tests"]
    assert tests["total"] == 0
    state = tests.get("state")
    assert state in {"no_tests_indexed", "no_coverage", "no_tests_in_corpus"}, (
        f"tests signal must disclose the empty-corpus state; got severity={tests.get('severity')!r} state={state!r}"
    )


# ---------------------------------------------------------------------------
# Pattern-2 BUG PIN #3 - MEDIUM: complexity severity=LOW on 0 metrics rows
# ---------------------------------------------------------------------------


@pytest.mark.xfail(
    strict=True,
    reason=(
        "W805-L REAL BUG (MEDIUM): cmd_preflight.py L361-367 _check_complexity "
        "returns severity='low' when the symbol_metrics join returns 0 rows. "
        "A symbol can exist in symbols WITHOUT a symbol_metrics row (older "
        "indexer build, partial reindex, language extractor not emitting "
        "metrics). severity=LOW here means 'no data', not 'low complexity'. "
        "Fix template: emit state='no_complexity_data' on the not-rows "
        "branch so agents distinguish 'low complexity' from 'unknown "
        "complexity'. Separate fix wave."
    ),
)
def test_complexity_signal_explicit_no_data(cli_runner, isolated_symbol_corpus):
    """Pin: when symbol_metrics has 0 rows for the target, disclose state.

    Note: on this minimal corpus the python extractor DOES populate
    symbol_metrics (isolated_fn -> cc=0 row). If that changes upstream
    the test will fail loudly and we re-investigate. The xfail-strict
    pins the disclosure-state contract, not the row presence.
    """
    result = _invoke_preflight(cli_runner, isolated_symbol_corpus, "isolated_fn", json_mode=True)
    envelope = _parse_envelope(result)
    complexity = envelope["complexity"]
    # If cc=0 AND no high_complexity_symbols, the signal effectively has
    # nothing to say - state should disclose that explicitly OR
    # severity should NOT default to "LOW" (which an agent reads as a
    # positive measurement).
    is_zero_data = complexity["max_cognitive_complexity"] == 0.0 and not complexity.get("high_complexity_symbols")
    if is_zero_data:
        state = complexity.get("state")
        assert state in {"no_complexity_data", "no_metrics_indexed"}, (
            f"complexity signal must disclose missing-metrics state when "
            f"cc=0 and no high_complexity_symbols; got severity="
            f"{complexity.get('severity')!r} state={state!r}"
        )
    else:
        pytest.skip(
            "Precondition not met: symbol_metrics has rows for this symbol; the no-data pin doesn't apply here."
        )


# ---------------------------------------------------------------------------
# Pattern-2 BUG PIN #4 - MEDIUM: top-level partial_success=False despite
# 5/6 empty signals
# ---------------------------------------------------------------------------


@pytest.mark.xfail(
    strict=True,
    reason=(
        "W805-L REAL BUG (MEDIUM): cmd_preflight.py L1023-1029 emits "
        "summary.partial_success=False on the isolated-symbol corpus where "
        "5 of 6 signals (blast/tests/complexity/coupling/fitness, but not "
        "conventions) have NO underlying data. The verdict 'Proceed with "
        "caution - MEDIUM risk' is driven by tests.severity=WARNING, "
        "which is itself driven by 'no tests indexed'. Cascading silent "
        "fallback: the canonical agent-safety gate emits a MEDIUM verdict "
        "from a cascading chain of no-data signals while claiming "
        "partial_success=False. Fix template: count signals with "
        "underlying data; if <=2 have real signal, set partial_success=True "
        "+ summary.state='insufficient_signal_data'. Separate fix wave."
    ),
)
def test_no_silent_safe_to_edit_on_empty_corpus(cli_runner, isolated_symbol_corpus):
    """CRITICAL agent-safety pin: when most signals lack data, partial_success=True.

    This is the canonical W805-L bug: preflight is the pre-edit safety
    gate. If 5/6 signals come back with no data, the gate has NO BASIS
    to claim partial_success=False - the underlying signals are
    cascading-defaulting, not measuring.

    Note: ``conventions`` has data on this corpus (1 kind with majority,
    0 violations), so it's NOT in the "no data" bucket. The other 5
    signals all return empty rows / no rules / no callers.
    """
    result = _invoke_preflight(cli_runner, isolated_symbol_corpus, "isolated_fn", json_mode=True)
    envelope = _parse_envelope(result)
    summary = envelope["summary"]
    # The bug shape: partial_success=False + cascading-no-data signals.
    # The fix flips partial_success=True when most signals have no data.
    assert summary.get("partial_success") is True, (
        f"isolated-symbol corpus has 5/6 signals with no underlying data; "
        f"partial_success MUST be True. Got summary={summary!r}"
    )


# ---------------------------------------------------------------------------
# Pattern-2 BUG PIN #5 - blast signal: 0 callers must disclose state
# ---------------------------------------------------------------------------


@pytest.mark.xfail(
    strict=True,
    reason=(
        "W805-L REAL BUG (LOW): cmd_preflight.py L244 _blast_severity "
        "returns 'LOW' on (0,0). This conflates 'no callers at all (the "
        "symbol is unreachable / dead / unwired)' with 'low blast radius'. "
        "Fix template: emit state='no_callers' / 'unreachable_symbol' on "
        "the (0,0) branch so agents know whether the symbol is isolated "
        "by design or accidentally unwired. Separate fix wave."
    ),
)
def test_blast_signal_explicit_no_data(cli_runner, isolated_symbol_corpus):
    """Pin: 0 affected symbols + 0 affected files must disclose blast state.

    The isolated_fn function has zero callers in the corpus. The current
    blast envelope reports affected_symbols=0, affected_files=0,
    severity=LOW - indistinguishable from 'has callers but blast is
    small'. Agents reading severity=LOW would think 'safe to edit'
    when in reality the symbol has NO callers (could mean dead code
    OR could mean the indexer missed cross-language callers).
    """
    result = _invoke_preflight(cli_runner, isolated_symbol_corpus, "isolated_fn", json_mode=True)
    envelope = _parse_envelope(result)
    blast = envelope["blast_radius"]
    assert blast["affected_symbols"] == 0
    assert blast["affected_files"] == 0
    state = blast.get("state")
    assert state in {"no_callers", "unreachable_symbol", "no_blast_data"}, (
        f"blast signal must disclose the no-callers state; got severity={blast.get('severity')!r} state={state!r}"
    )


# ---------------------------------------------------------------------------
# Pattern-2 BUG PIN #6 - coupling signal: 0 partners must disclose state
# ---------------------------------------------------------------------------


@pytest.mark.xfail(
    strict=True,
    reason=(
        "W805-L REAL BUG (LOW): cmd_preflight.py L405-407 returns "
        "severity='OK' on the empty file_ids branch, and the populated-but-"
        "no-partners path also yields coupled_files=0 + severity='OK'. "
        "These two states are not the same: 'file has 0 git_cochange rows' "
        "(new repo, fresh files, single-commit history) vs 'file has "
        "co-change history but no partners crossed the strength threshold'. "
        "Fix template: emit state='no_git_history' when file_stats has no "
        "row for this file. Separate fix wave."
    ),
)
def test_coupling_signal_explicit_no_data(cli_runner, isolated_symbol_corpus):
    """Pin: when there's no git history, coupling signal must disclose state.

    The isolated_symbol_corpus has exactly 1 git commit. git_cochange is
    empty. coupled_files=0 and severity=OK - this looks identical to a
    long-history file with no co-change partners crossing the threshold.
    """
    result = _invoke_preflight(cli_runner, isolated_symbol_corpus, "isolated_fn", json_mode=True)
    envelope = _parse_envelope(result)
    coupling = envelope["coupling"]
    assert coupling["coupled_files"] == 0
    state = coupling.get("state")
    assert state in {"no_git_history", "no_cochange_data", "no_coupling_data"}, (
        f"coupling signal must disclose missing-history state; got "
        f"severity={coupling.get('severity')!r} state={state!r}"
    )


# ---------------------------------------------------------------------------
# Pattern-2 BUG PIN #7 - conventions signal: 0 kinds-with-majority must
# disclose state separately from "all symbols match conventions"
# ---------------------------------------------------------------------------


@pytest.mark.xfail(
    strict=True,
    reason=(
        "W805-L REAL BUG (LOW): cmd_preflight.py L491-497 / L542-550 emit "
        "severity='OK' + violation_count=0 in TWO different states: "
        "(a) no symbols in the corpus to apply convention to, and "
        "(b) symbols exist + match the convention. These are not the same. "
        "Fix template: emit state='no_majority_convention' when "
        "kinds_with_majority==0 OR state='no_symbols_for_convention' when "
        "the target's kind has no majority convention to violate. "
        "Separate fix wave."
    ),
)
def test_conventions_signal_explicit_no_data(cli_runner, empty_corpus):
    """Pin: empty corpus convention signal must disclose state.

    On the empty.py-only corpus, the convention helper sees 0 symbols
    overall. The current envelope reports kinds_with_majority=0 +
    severity=OK on the unresolved branch. But there's no preflight
    target-symbol path that reaches this exact state because preflight
    bails on unresolved. So instead we probe the isolated_symbol fixture
    and assert the conventions disclosure on a 1-symbol corpus.
    """
    # Use isolated corpus where preflight reaches the conventions path.
    runner = CliRunner()
    proj = empty_corpus.parent / "convention_probe"
    proj.mkdir()
    (proj / ".gitignore").write_text(".roam/\n")
    (proj / "lone.py").write_text("def isolated_fn():\n    return 1\n")
    git_init(proj)
    old_cwd = os.getcwd()
    try:
        os.chdir(str(proj))
        out, rc = index_in_process(proj, "--force")
        assert rc == 0, out
        result = _invoke_preflight(runner, proj, "isolated_fn", json_mode=True)
    finally:
        os.chdir(old_cwd)
    envelope = _parse_envelope(result)
    conv = envelope["conventions"]
    state = conv.get("state")
    # 1-symbol corpus has 1 kind with majority but no violations.
    # The bug: state field doesn't disclose this; severity=OK is the
    # same string the populated corpus emits when everything is clean.
    assert state in {
        "no_majority_convention",
        "all_conventions_passed",
        "no_violations",
    }, f"conventions signal must disclose specific OK sub-state; got severity={conv.get('severity')!r} state={state!r}"


# ---------------------------------------------------------------------------
# Drift guard: Pattern-1 Variant D should NOT regress on the resolution axis.
# ---------------------------------------------------------------------------


def test_pattern_1_variant_d_correctly_sealed(cli_runner, empty_corpus):
    """Drift guard: the unresolved branch correctly seals Pattern-1 Variant D.

    The Pattern-1 Variant D fix (W1241/W1243/W1249) is already in place
    on the unresolved path. This test asserts the canonical disclosure
    block stays wired: ``resolution=unresolved`` + ``partial_success=True``
    + ``risk_level=UNKNOWN`` + verdict naming the not-found state.

    If a future refactor removes any of these fields the test fails
    loudly - this is the regression-invariant for Variant D on
    preflight.
    """
    result = _invoke_preflight(cli_runner, empty_corpus, "nonexistent_xyz", json_mode=True)
    envelope = _parse_envelope(result)
    # Top-level disclosure block.
    assert envelope.get("resolution") == "unresolved"
    assert envelope.get("partial_success") is True
    # Summary disclosure block.
    summary = envelope["summary"]
    assert summary.get("resolution") == "unresolved"
    assert summary.get("partial_success") is True
    assert summary.get("risk_level") == "UNKNOWN"
    # Verdict honestly names the unresolved state.
    verdict = (summary.get("verdict") or "").lower()
    assert "not found" in verdict or "not in the index" in verdict, (
        f"unresolved-branch verdict must name the not-found state; got {verdict!r}"
    )
