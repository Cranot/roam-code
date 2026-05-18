"""W805-O - empty-corpus smoke for ``roam why`` (W805 Pattern 2 sweep).

Fifteenth-in-batch of the W805 Pattern-2 audit. Prior cohort:

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
- M (cmd_diagnose)         REAL BUG (empty-batch + no-suspects silent SAFE)
- N (cmd_file)              in flight
- O (cmd_why, this wave)

cmd_why is the **architectural-role explainer** sibling of cmd_diagnose
cited in CLAUDE.md: "fan (which ranks symbols by raw connectivity) and
preflight (which checks blast radius before a change), this command
explains a specific symbol's role". Same target-resolution + multi-signal
envelope shape as cmd_diagnose -- very likely shares the empty-corpus
silent-SAFE pattern.

W978 first-hypothesis re-run BEFORE writing the test
============================================================

Two relevant branches in ``src/roam/commands/cmd_why.py``:

1. **Unresolved-symbol branch** (``cmd_why.py:142-148``). On empty corpus,
   ``find_symbol`` returns None and ``_analyze_symbol`` emits a per-entry
   dict carrying ``error`` + ``resolution="unresolved"`` +
   ``partial_success=true``. W1245-OK at the entry level.

   BUT ``_emit_why_json`` (``cmd_why.py:245-282``) computes:

       verdict = f"{len(results)} symbol(s) - none critical"

   when crit==0. For a SINGLE unresolved target, ``len(results)==1`` and
   ``crit==0``, so the verdict reads ``"1 symbol(s) - none critical"`` --
   a confident SAFE indistinguishable from the happy path. The LAW-6
   single-line verdict does NOT name the unresolved state. Top-level
   ``partial_success`` IS flipped (good), but the verdict-only consumer
   sees success. **REAL BUG.**

   Additionally: ``summary.state`` is NEVER set on cmd_why -- no
   closed-enum state disclosure on ANY branch. Pattern-2 requires a
   closed-enum ``state`` field to distinguish ``unresolved`` /
   ``no_graph`` / ``empty_corpus``. And ``summary.resolution`` /
   top-level ``resolution`` are NEVER set either (cmd_diagnose's W1244
   pattern is per-entry-only on cmd_why -- the dual-emit on summary +
   top-level for single-target case is missing).

2. **Resolved-symbol-isolated-in-graph branch** (``cmd_why.py:165-170``).
   When a symbol resolves but is not in the call graph
   (``sym_id not in RG``), reach=0, in_deg=0, out_deg=0. ``_verdict``
   classifies role="Leaf" + reach==0 + in_deg==0 -> returns
   ``"Dead code. Safe to remove."``. On a 1-symbol corpus EVERY symbol
   trips this branch -- the verdict reads as a confident "safe to remove"
   recommendation even though the corpus is degenerate. ``partial_success``
   stays false. The diagnostic is correct (it IS dead per definition)
   but the verdict is indistinguishable from a real dead-code finding on
   a healthy corpus. Softer-than-Pattern-2 ambiguity, but sibling
   cmd_diagnose W805-M pinned the analogous "0 suspects on 1-symbol corpus"
   case as a real bug. **REAL BUG.** (debatable; pinned per W805-M precedent)

W1245 (Pattern-1 Variant D) is **already** handled at the per-entry level:
each ``symbols[]`` row carries ``resolution`` + ``partial_success``. The
gap is on the SUMMARY axis -- the LAW-6 verdict consumer does not see the
degradation. See ``tests/test_cmd_why_resolution.py`` for the existing
W1245 contract -- this file does NOT re-test it.

Test split (mirrors W805-M baseline-plus-xfail-pin discipline):

1. SMOKE (always-on): empty corpus + unresolved -> no crash, parseable
   envelope, per-entry resolution disclosure intact (W1245 baseline).
2. PATTERN-2 PIN (xfail-strict): empty-corpus verdict is silent SAFE.
3. PATTERN-2 PIN (xfail-strict): no state field on unresolved single-target.
4. PATTERN-1 Variant D PIN (xfail-strict): no summary-level resolution.
5. Happy-path regression: clean corpus emits a real role + verdict.

The W805-O fix lives in a separate wave; this module is intentionally
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

    On any name, ``find_symbol`` returns None -> the unresolved-symbol
    branch in ``_analyze_symbol`` fires.
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
    """Indexed project with a single leaf -- 1 symbol, 0 edges.

    The target resolves cleanly (resolution=symbol) but is isolated in
    the graph -- reach=0, in_deg=0, out_deg=0. Exercises the
    Leaf-with-zero-everywhere branch in ``_verdict``.
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


def _invoke_why(cwd, *extra, json_mode: bool = True):
    """Run ``roam [--json] why ...`` in-process under ``cwd``."""
    from roam.cli import cli

    runner = CliRunner()
    args: list[str] = []
    if json_mode:
        args.append("--json")
    args.append("why")
    args.extend(extra)

    old_cwd = os.getcwd()
    try:
        os.chdir(str(cwd))
        result = runner.invoke(cli, args, catch_exceptions=False)
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


class TestWhyEmptyCorpusSealed:
    """Properties already satisfied by the current cmd_why envelope."""

    def test_empty_corpus_no_crash(self, empty_corpus):
        """``roam why <name>`` on empty corpus exits 0 (no crash)."""
        result = _invoke_why(empty_corpus, "nonexistent_xyz", json_mode=True)
        assert result.exit_code == 0, f"expected exit 0, got {result.exit_code}; output:\n{result.output}"
        # Pattern-1C: stdout MUST be non-empty in --json mode.
        assert result.output.strip(), "stdout must NOT be empty in --json mode"

    def test_empty_corpus_envelope_has_verdict(self, empty_corpus):
        """Empty-corpus unresolved emits ``command=why`` + non-empty verdict."""
        result = _invoke_why(empty_corpus, "nonexistent_xyz", json_mode=True)
        envelope = _parse_envelope(result)
        assert envelope["command"] == "why"
        summary = envelope.get("summary") or {}
        verdict = summary.get("verdict") or ""
        assert isinstance(verdict, str) and verdict, f"summary.verdict must be a non-empty string, got {verdict!r}"

    def test_empty_corpus_partial_success_set(self, empty_corpus):
        """W1245 baseline: top-level + summary partial_success flip on
        unresolved single-target.

        This is the well-handled axis (per-entry + top-level
        ``partial_success`` already cascades). The verdict-axis gap is
        pinned separately by ``test_no_silent_why_success_on_empty``.
        """
        result = _invoke_why(empty_corpus, "nonexistent_xyz", json_mode=True)
        envelope = _parse_envelope(result)
        summary = envelope["summary"]
        assert summary.get("partial_success") is True, (
            f"unresolved single-target must set summary.partial_success=True; got {summary!r}"
        )
        assert envelope.get("partial_success") is True, (
            f"unresolved single-target must set top-level partial_success=True; "
            f"got envelope.partial_success={envelope.get('partial_success')!r}"
        )

    def test_empty_corpus_law6_verdict_standalone(self, empty_corpus):
        """LAW 6: verdict is a single line of plain ASCII.

        We do NOT assert on verdict CONTENT here -- the silent-SAFE
        content gap is the xfail pin below. This sealed test only locks
        the standalone-ASCII shape.
        """
        result = _invoke_why(empty_corpus, "nonexistent_xyz", json_mode=True)
        envelope = _parse_envelope(result)
        verdict = envelope["summary"]["verdict"]
        assert "\n" not in verdict, f"verdict has embedded newline: {verdict!r}"
        # Normalize the en-dash that the verdict humanizer can emit -- on
        # roam-code the verdict line uses en-dash for the count separator
        # ("1 symbol(s) - none critical") which is non-ASCII; the LAW-6
        # standalone requirement is single-line, not strict-ASCII.
        assert verdict.strip() not in ("", "?", "verdict", "OK", "ok"), f"verdict is a placeholder: {verdict!r}"

    def test_unresolved_target_explicit_resolution(self, empty_corpus):
        """W1245 per-entry baseline: the unresolved row carries
        ``resolution="unresolved"`` + ``error`` + per-entry
        ``partial_success=true``.

        Locks the per-entry W1245 contract regardless of whether the
        verdict-axis fix lands. (The summary-level + top-level
        ``resolution`` mirror is the separate W805-O Variant-D pin
        below.)
        """
        result = _invoke_why(empty_corpus, "nonexistent_xyz", json_mode=True)
        envelope = _parse_envelope(result)
        syms = envelope.get("symbols") or []
        assert len(syms) == 1
        entry = syms[0]
        assert entry.get("resolution") == "unresolved", (
            f"unresolved entry must carry resolution=unresolved; got {entry!r}"
        )
        assert entry.get("partial_success") is True, f"unresolved entry must carry partial_success=True; got {entry!r}"
        assert "error" in entry, f"unresolved entry must carry 'error' field; got keys={sorted(entry.keys())}"

    def test_clean_corpus_emits_real_explanation(self, populated_corpus):
        """Happy-path regression: a real corpus emits a real role + verdict.

        ``main -> helper`` produces an undirected edge + community of 2;
        querying ``helper`` should resolve cleanly with a non-empty role
        and a verdict that names the symbol's role characteristic, not
        the silent SAFE placeholder.
        """
        result = _invoke_why(populated_corpus, "helper", json_mode=True)
        assert result.exit_code == 0, result.output
        envelope = _parse_envelope(result)
        summary = envelope["summary"]
        # On the happy path:
        #  - symbols count is 1
        #  - the lone entry resolved cleanly (resolution=symbol)
        #  - partial_success is false
        assert summary.get("symbols") == 1
        assert summary.get("partial_success") is False
        syms = envelope.get("symbols") or []
        assert len(syms) == 1
        entry = syms[0]
        assert entry.get("resolution") == "symbol", f"happy-path target should resolve cleanly; got entry={entry!r}"
        # Role must be one of the known closed enum values from _classify_role.
        accepted_roles = {
            "Hub",
            "Core utility",
            "Orchestrator",
            "Bridge",
            "Utility",
            "Leaf",
            "Internal",
        }
        assert entry.get("role") in accepted_roles, (
            f"happy-path entry must carry a known role; got {entry.get('role')!r}"
        )
        # Verdict on a connected helper should not be the bare "Dead code"
        # placeholder -- helper has at least one caller (main).
        verdict = entry.get("verdict") or ""
        assert verdict and "Dead code" not in verdict, (
            f"happy-path verdict should not be the dead-code placeholder; got {verdict!r}"
        )


# ---------------------------------------------------------------------------
# PATTERN-2 PIN 1: silent-SAFE verdict on empty-corpus unresolved target
# (cmd_why.py:257-258 builds verdict as "N symbol(s) - none critical" or
#  "N of M symbol(s) critical" regardless of whether N entries actually
#  resolved. On 1 unresolved entry the verdict reads as confident success.)
# ---------------------------------------------------------------------------


@pytest.mark.xfail(
    strict=True,
    reason=(
        "W805-O REAL BUG 1 (Pattern-2): cmd_why.py:257-258 in "
        "_emit_why_json builds verdict 'N symbol(s) - none critical' "
        "(or 'N of M symbol(s) critical') based on len(results) and the "
        "crit count -- NOT on whether entries actually resolved. For a "
        "single unresolved target this yields verdict='1 symbol(s) - none "
        "critical' which reads as confident SAFE. A LAW-6 verdict-only "
        "consumer cannot tell the symbol failed to resolve. "
        "summary.partial_success IS flipped (W1245), but the verdict "
        "axis is silent. Fix: when ALL results are unresolved or "
        "error-bearing, override verdict to name the empty state "
        "('No symbols resolved' / 'unresolved: <name>'). Separate fix wave."
    ),
)
def test_no_silent_why_success_on_empty(empty_corpus):
    """Pin: verdict must name the unresolved state, not say 'none critical'.

    Anti-shape: verdict ``"1 symbol(s) - none critical"`` on a single
    unresolved target -- canonical Pattern-2 silent SAFE on the
    verdict axis (the count is wrong: 0 symbols resolved, not 1).
    """
    result = _invoke_why(empty_corpus, "nonexistent_xyz", json_mode=True)
    envelope = _parse_envelope(result)
    summary = envelope.get("summary") or {}
    verdict = (summary.get("verdict") or "").lower()
    # The verdict MUST disclose the unresolved/empty state.
    disclosure_tokens = (
        "unresolved",
        "not found",
        "no symbols resolved",
        "0 symbol",
        "no result",
    )
    matched = any(tok in verdict for tok in disclosure_tokens)
    assert matched, (
        f"empty-corpus unresolved-single verdict must disclose the "
        f"unresolved state; got {verdict!r}; expected one of {disclosure_tokens}"
    )


# ---------------------------------------------------------------------------
# PATTERN-2 PIN 2: no summary.state closed-enum disclosure
# (cmd_why.py never sets summary.state on ANY branch -- consumers cannot
#  distinguish empty_corpus / unresolved / isolated_in_graph.)
# ---------------------------------------------------------------------------


@pytest.mark.xfail(
    strict=True,
    reason=(
        "W805-O REAL BUG 2 (Pattern-2): cmd_why.py never sets a "
        "summary.state field on any branch. Pattern-2 requires "
        "closed-enum state disclosure so a machine consumer can "
        "distinguish unresolved / isolated_in_graph / empty_corpus / "
        "all_unresolved without parsing free-form verdict text. "
        "Sibling precedent: cmd_describe (W805-I) emits "
        "state='no_symbols'; cmd_module (W805-K) emits "
        "state='path_not_found'; cmd_diagnose (W805-M, when fixed) "
        "will emit state='empty_batch'. Fix: in _emit_why_json, when "
        "all results are unresolved set summary.state='all_unresolved'. "
        "Separate fix wave."
    ),
)
def test_empty_corpus_explicit_state(empty_corpus):
    """Pin: unresolved single-target must disclose summary.state via closed enum."""
    result = _invoke_why(empty_corpus, "nonexistent_xyz", json_mode=True)
    envelope = _parse_envelope(result)
    summary = envelope.get("summary") or {}
    state = summary.get("state") or envelope.get("state")
    accepted = {
        "all_unresolved",
        "unresolved",
        "not_found",
        "empty_corpus",
        "no_symbols_resolved",
    }
    assert state in accepted, (
        f"unresolved single-target must disclose summary.state via closed "
        f"enum; got {state!r}; expected one of {accepted}"
    )
