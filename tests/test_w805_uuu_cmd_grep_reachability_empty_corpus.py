"""W805-UUU — Empty-corpus reachability-axis smoke test for ``roam grep``.

Seventy-third-in-batch W805 sweep. ``cmd_grep`` already has the W607-G
subprocess-axis ``warnings_out`` plumbing (grep_engine_pin_missing /
grep_engine_fanout_fallback / grep_ripgrep_failed / grep_git_grep_failed /
grep_indexed_scan_failed). W805-UUU probes the *distinct* INDEX-AWARE
REACHABILITY axis — ``--reachable-from <entry>`` / ``--unreachable``
filters on an empty corpus.

W978 first-hypothesis check (pre-test audit)
--------------------------------------------

Read ``src/roam/commands/cmd_grep.py`` head-to-tail (834 lines) plus
``roam.commands.grep_helpers.build_reachable_set`` and
``build_orphan_set``. Two distinct failure axes confirmed:

* **Subprocess axis (W607-G already sealed).** Engine fan-out between
  ripgrep / git grep / indexed_file_scan. Markers all begin
  ``grep_*`` (``grep_engine_pin_missing``, ``grep_engine_fanout_fallback``,
  ``grep_ripgrep_failed``, ``grep_git_grep_failed``,
  ``grep_indexed_scan_failed``). Pinned at ``cmd_grep.py:306-383``.
* **Reachability axis (W805-UUU scope).** Distinct: lives at lines
  414-465, after the engine fan-out has already finished. Uses the DB
  call-graph (``build_reachable_set`` / ``build_orphan_set``) not a
  subprocess.

W805-UUU pins the reachability axis in isolation:

1. ``build_reachable_set(conn, "nonexistent_sym")`` returns ``None`` →
   cmd_grep.py:419-445 emits ``state="unresolved_entry"`` +
   ``resolution="unresolved"`` + ``partial_success=True``. This branch
   IS sealed (Pattern-1-V-D disclosure shipped).
2. **REAL BUG (Pattern-2 + Pattern-1-V-D silent SAFE)**: when grep
   produces zero matches AND ``--reachable-from <entry>`` is set, the
   ``if not matches:`` early-return at ``cmd_grep.py:399-401`` fires
   BEFORE the reachability resolver at lines 417-445 ever runs. The
   user's unresolved entry symbol is silently dropped on the floor. The
   envelope reads ``verdict: "no matches for 'foo'"`` with
   ``partial_success: false`` — indistinguishable from a fully-resolved
   "found nothing" success.
3. Same shape on ``--unreachable`` with an empty / no-symbol corpus:
   the early-return drops the filter and emits a silent SAFE.

Marker family in the W805-UUU pin envelope: ``reachability_*``
(``reachability_filter_unreached``, ``reachability_unresolved_entry`` —
to be coined when the fix lands). Distinct from W607-G's ``grep_*``
subprocess-axis family. The orthogonality test
``test_w607_g_markers_not_triggered`` below asserts the two axes stay
independent — a W805-UUU empty-corpus reachability test MUST NOT
produce W607-G subprocess-axis markers (and vice versa).

W907 verify-cycle check
-----------------------

No "duplicated to avoid cycle" docstrings introduced. Local helper
functions only (``_make_repo``, ``_index``). No shared module created
or hoisted.

LAW 4 note: warning markers are diagnostic strings, NOT
``agent_contract.facts`` content, and therefore not subject to the
concrete-noun-terminal lint.
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
    """Init a git repo, commit current files, no history beyond init."""
    env = {
        **os.environ,
        "GIT_AUTHOR_NAME": "test",
        "GIT_AUTHOR_EMAIL": "t@t",
        "GIT_COMMITTER_NAME": "test",
        "GIT_COMMITTER_EMAIL": "t@t",
    }
    subprocess.run(["git", "init", "-q"], cwd=str(repo), check=True, env=env)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=str(repo), check=True, env=env)
    subprocess.run(["git", "config", "user.name", "test"], cwd=str(repo), check=True, env=env)
    subprocess.run(["git", "add", "."], cwd=str(repo), check=True, env=env)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=str(repo), check=True, env=env)


@pytest.fixture
def empty_grep_corpus(tmp_path, monkeypatch):
    """A git repo with a single empty committed Python file.

    The file is indexed (lands in the ``files`` table) but the empty
    body means zero ``symbols`` rows and zero ``edges`` rows — the
    canonical empty-corpus reachability shape (no entry symbol can ever
    resolve; the orphan-set is empty).
    """
    repo = tmp_path / "grep-empty-reach-repo"
    repo.mkdir()
    (repo / ".gitignore").write_text(".roam/\n", encoding="utf-8")
    (repo / "empty.py").write_text("", encoding="utf-8")
    _git_init_committed(repo)
    monkeypatch.chdir(repo)
    out, rc = index_in_process(repo, "--force")
    assert rc == 0, f"roam index failed:\n{out}"
    return repo


@pytest.fixture
def clean_grep_corpus(tmp_path, monkeypatch):
    """A populated corpus where reachability filters CAN produce real signal.

    ``main`` calls ``helper`` which references ``foo``. Reachability
    from ``main`` covers both functions; ``--unreachable`` would
    produce nothing because every symbol has an inbound edge from main.
    """
    repo = tmp_path / "grep-clean-reach-repo"
    repo.mkdir()
    (repo / ".gitignore").write_text(".roam/\n", encoding="utf-8")
    (repo / "main.py").write_text(
        "def main():\n    helper()\n\ndef helper():\n    return foo\n",
        encoding="utf-8",
    )
    _git_init_committed(repo)
    monkeypatch.chdir(repo)
    out, rc = index_in_process(repo, "--force")
    assert rc == 0, f"roam index failed:\n{out}"
    return repo


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _invoke_grep(*args, json_mode=True):
    invocation = ["--json", "grep"] if json_mode else ["grep"]
    invocation.extend(args)
    return CliRunner().invoke(
        __import__("roam.cli", fromlist=["cli"]).cli,
        invocation,
        catch_exceptions=False,
    )


def _parse_envelope(result):
    assert result.exit_code in (0, 1, 5), f"unexpected exit code {result.exit_code}; out={result.output[:600]!r}"
    return json.loads(result.output)


# ---------------------------------------------------------------------------
# SMOKE — always-on assertions
# ---------------------------------------------------------------------------


def test_clean_corpus_emits_real_reachability(clean_grep_corpus):
    """Sanity: on a populated corpus, ``--reachable-from main`` produces
    a real reachability-annotated envelope.

    This is the positive contrast for the empty-corpus pins below: when
    the corpus has indexed symbols AND the entry resolves, reachability
    works as advertised — no Pattern-2 silent SAFE shape.
    """
    result = _invoke_grep("helper", "--reachable-from", "main")
    envelope = _parse_envelope(result)
    summary = envelope["summary"]
    assert summary["total"] >= 1, f"expected at least one match; got {summary!r}"
    assert "reachable from main" in summary["verdict"], (
        f"verdict should disclose reachability filter; got {summary['verdict']!r}"
    )
    assert envelope.get("reachable_from") == "main", (
        f"top-level reachable_from must echo the filter arg; got {envelope.get('reachable_from')!r}"
    )


def test_unresolved_entry_already_sealed_pattern_1_v_d(empty_grep_corpus):
    """Sanity: when grep DOES match but the entry symbol does NOT
    resolve, cmd_grep.py:419-445 already discloses Pattern-1-V-D.

    Reach this branch by grepping the literal ``.gitignore`` newline
    (which exists in every empty-corpus file) — there's a match to
    enrich, the unresolved-entry resolver fires, the structured
    envelope MUST disclose ``state="unresolved_entry"`` +
    ``resolution="unresolved"``.

    NOTE: this branch IS already sealed in cmd_grep — including it as
    a positive contrast for the silent-SAFE pin below.
    """
    # Drop a tiny file with one matchable token + zero call-graph edges
    # so build_reachable_set returns None for a made-up entry name.
    (empty_grep_corpus / "leaf.py").write_text("TOKEN_W805_UUU = 1\n", encoding="utf-8")
    _git_init_committed_again = None  # noqa: F841 (kept for clarity)
    env = {
        **os.environ,
        "GIT_AUTHOR_NAME": "test",
        "GIT_AUTHOR_EMAIL": "t@t",
        "GIT_COMMITTER_NAME": "test",
        "GIT_COMMITTER_EMAIL": "t@t",
    }
    subprocess.run(["git", "add", "."], cwd=str(empty_grep_corpus), check=True, env=env)
    subprocess.run(
        ["git", "commit", "-q", "-m", "add-leaf"],
        cwd=str(empty_grep_corpus),
        check=True,
        env=env,
    )
    out, rc = index_in_process(empty_grep_corpus, "--force")
    assert rc == 0, f"reindex failed:\n{out}"

    result = _invoke_grep("TOKEN_W805_UUU", "--reachable-from", "nonexistent_sym")
    # Sealed branch: exits 1 with structured disclosure.
    assert result.exit_code == 1, f"expected exit 1 on unresolved entry; got {result.exit_code}"
    envelope = json.loads(result.output)
    summary = envelope["summary"]
    assert summary.get("state") == "unresolved_entry", (
        f"unresolved entry resolver branch must set state='unresolved_entry'; got {summary!r}"
    )
    assert summary.get("resolution") == "unresolved", (
        f"unresolved entry resolver branch must set resolution='unresolved'; got {summary!r}"
    )
    assert summary.get("partial_success") is True, (
        f"unresolved entry resolver branch must set partial_success=True; got {summary!r}"
    )


def test_w607_g_markers_not_triggered(empty_grep_corpus):
    """W805-UUU orthogonality: empty-corpus reachability tests MUST NOT
    trip W607-G subprocess-axis markers.

    The two axes are independent. A reachability-axis bug pin should
    surface ``reachability_*`` markers (when the fix lands), NEVER
    ``grep_engine_*`` / ``grep_*_failed`` / ``grep_indexed_scan_failed``
    markers. If a W805-UUU test accidentally triggers a W607-G marker,
    the test is wrong (not isolating the right axis) — re-audit.

    Today (pre-fix), the empty-corpus reachability path produces a
    silent-SAFE envelope with no warnings_out at all. Once the fix
    lands, the reachability-axis markers will appear. NEITHER timeline
    should produce a subprocess-axis marker.
    """
    result = _invoke_grep("foo", "--reachable-from", "main")
    envelope = json.loads(result.output)
    top_wo = envelope.get("warnings_out") or []
    summary_wo = envelope.get("summary", {}).get("warnings_out") or []
    all_markers = top_wo + summary_wo
    forbidden_prefixes = (
        "grep_engine_pin_missing",
        "grep_engine_fanout_fallback",
        "grep_ripgrep_failed",
        "grep_git_grep_failed",
        "grep_indexed_scan_failed",
        "grep_engine_failed",
    )
    leaked = [m for m in all_markers if any(m.startswith(p) for p in forbidden_prefixes)]
    assert not leaked, (
        f"W805-UUU reachability-axis test must NOT trigger W607-G "
        f"subprocess-axis markers; got leaked={leaked!r}, full markers={all_markers!r}"
    )


def test_empty_corpus_no_crash(empty_grep_corpus):
    """Empty corpus + reachability filter must not crash.

    Pattern-1-V-C / Pattern-2 baseline: ANY empty-state branch must
    emit a structured JSON envelope, never a raw exception or empty
    stdout.
    """
    result = _invoke_grep("foo", "--reachable-from", "nonexistent_sym")
    assert result.exit_code in (0, 1, 5), f"unexpected exit code {result.exit_code}"
    assert result.output.strip(), "no output produced — Pattern-1-V-C empty-stdout"
    envelope = json.loads(result.output)
    assert envelope["command"] == "grep"
    assert "summary" in envelope and "verdict" in envelope["summary"]


# ---------------------------------------------------------------------------
# PATTERN-2 + PATTERN-1-V-D PINS — xfail-strict until fix lands
# ---------------------------------------------------------------------------


@pytest.mark.xfail(
    strict=True,
    reason=(
        "W805-UUU REAL BUG: cmd_grep.py:399-401 early-returns via "
        "_emit_empty BEFORE the reachability resolver at lines 417-445 "
        "ever runs. When grep produces zero matches AND --reachable-from "
        "<unresolved_entry> is set, the unresolved entry is silently "
        "dropped — envelope reads 'no matches for X' with "
        "partial_success=false, indistinguishable from a fully-resolved "
        "success. Pattern-1-V-D: silent success on degraded resolution. "
        "Fix: move the reachability resolver UP, before the early-return "
        "guard, so unresolved entries always disclose state + resolution. "
        "Separate fix wave."
    ),
)
def test_empty_corpus_reachable_from_unresolved_disclosure(empty_grep_corpus):
    """Pin (Pattern-1-V-D): unresolved entry must be disclosed even when
    grep itself produces zero matches.

    The current code path resolves the engine, finds zero matches,
    early-returns via ``_emit_empty`` at cmd_grep.py:400 — the
    reachability resolver at line 418 is never reached. The user's
    ``--reachable-from nonexistent_sym`` is silently dropped.

    Expected (post-fix): summary.state should be ``unresolved_entry``,
    summary.resolution should be ``unresolved``, summary.partial_success
    should be True.
    """
    result = _invoke_grep("foo", "--reachable-from", "nonexistent_sym")
    envelope = json.loads(result.output)
    summary = envelope["summary"]
    # Pin: at least one of these three disclosure markers must be set.
    state = summary.get("state")
    resolution = summary.get("resolution")
    partial = summary.get("partial_success")
    silent_safe = state is None and resolution is None and partial is False
    assert not silent_safe, (
        f"silent-SAFE Pattern-1-V-D shape detected: state={state!r}, "
        f"resolution={resolution!r}, partial_success={partial!r}, "
        f"verdict={summary.get('verdict')!r}. The unresolved entry "
        f"'nonexistent_sym' was silently dropped on the floor."
    )


@pytest.mark.xfail(
    strict=True,
    reason=(
        "W805-UUU REAL BUG: cmd_grep.py:399-401 early-returns BEFORE "
        "the reachability filter at lines 446-465 is applied. When grep "
        "produces zero matches AND --reachable-from is set, the verdict "
        "'no matches for X' reads as confident no-result success — the "
        "envelope does NOT name the active reachability filter. "
        "Pattern-2 silent SAFE: filter is silently ignored. Fix: "
        "thread reachable_from into _emit_empty so the empty-result "
        "verdict discloses the active filter. Separate fix wave."
    ),
)
def test_empty_corpus_no_silent_zero_reachable(empty_grep_corpus):
    """Pin (Pattern-2): empty-corpus envelope must not silently drop the
    ``--reachable-from`` filter from the verdict.

    Anti-shape: ``verdict: "no matches for 'foo'"`` with
    ``reachable_from`` echoed neither at top level nor in the verdict.
    The verdict should say something like ``"no matches for 'foo' —
    reachable from main"`` OR the envelope should expose
    ``reachable_from`` at top level so consumers can see the filter
    was honored.
    """
    result = _invoke_grep("foo", "--reachable-from", "main")
    envelope = json.loads(result.output)
    summary = envelope["summary"]
    verdict = (summary.get("verdict") or "").lower()
    # The empty-result envelope today does not echo the reachable_from
    # filter anywhere. Post-fix, EITHER the verdict mentions "reachable"
    # OR the top-level envelope echoes reachable_from.
    verdict_discloses = "reachable" in verdict
    top_level_echoes = envelope.get("reachable_from") is not None
    silent_safe = not (verdict_discloses or top_level_echoes)
    assert not silent_safe, (
        f"silent-SAFE Pattern-2: empty result swallowed reachable_from "
        f"filter. verdict={summary.get('verdict')!r}, "
        f"top-level reachable_from={envelope.get('reachable_from')!r}"
    )


@pytest.mark.xfail(
    strict=True,
    reason=(
        "W805-UUU REAL BUG: cmd_grep.py:399-401 early-returns BEFORE "
        "the --unreachable filter at lines 450-463 is applied. When "
        "grep produces zero matches AND --unreachable is set, the "
        "envelope does NOT disclose the active filter state — verdict "
        "reads 'no matches for X' with no mention of unreachable-only "
        "scope. Pattern-2 silent SAFE: filter is silently ignored. "
        "Fix: thread the unreachable flag into _emit_empty so the "
        "empty-result envelope discloses the filter. Separate fix wave."
    ),
)
def test_empty_corpus_unreachable_filter_state(empty_grep_corpus):
    """Pin (Pattern-2): empty-corpus envelope must not silently drop the
    ``--unreachable`` filter from the verdict / top-level envelope.

    Anti-shape: ``verdict: "no matches for 'foo'"`` with
    ``unreachable`` set neither in the verdict nor at top level. The
    envelope MUST surface that an unreachable-only filter was active.
    """
    result = _invoke_grep("foo", "--unreachable")
    envelope = json.loads(result.output)
    summary = envelope["summary"]
    verdict = (summary.get("verdict") or "").lower()
    verdict_discloses = "unreachable" in verdict or "dead" in verdict
    top_level_echoes = envelope.get("unreachable") is True
    silent_safe = not (verdict_discloses or top_level_echoes)
    assert not silent_safe, (
        f"silent-SAFE Pattern-2: empty result swallowed unreachable "
        f"filter. verdict={summary.get('verdict')!r}, "
        f"top-level unreachable={envelope.get('unreachable')!r}"
    )


@pytest.mark.xfail(
    strict=True,
    reason=(
        "W805-UUU REAL BUG: cmd_grep.py:399-401 _emit_empty path emits "
        "summary.partial_success=False when --reachable-from is set with "
        "an unresolved entry. The unresolved entry MEANS the resolution "
        "was degraded — partial_success MUST be True. Pattern-1-V-D + "
        "Pattern-2 hybrid. Separate fix wave."
    ),
)
def test_partial_success_set_on_empty_filter(empty_grep_corpus):
    """Pin: empty-corpus + unresolved-filter envelope must set
    ``summary.partial_success=True``.

    The empty-corpus + unresolved-entry path is a degraded-resolution
    branch: the filter could not be applied because the entry didn't
    resolve. That is NOT a fully-resolved zero-result success.
    """
    result = _invoke_grep("foo", "--reachable-from", "nonexistent_sym")
    envelope = json.loads(result.output)
    summary = envelope["summary"]
    assert summary.get("partial_success") is True, (
        f"unresolved-entry empty branch must set partial_success=True; got summary={summary!r}"
    )
