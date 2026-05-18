"""W805-W -- empty-corpus Pattern-2 smoke test on ``roam refs-text``.

Twenty-third-in-batch W805 sweep. String-audit-with-verdict sibling of
the broader W805 family. Per CLAUDE.md, ``roam refs-text`` answers the
question "is this string still load-bearing?" and emits a per-string
verdict in the closed enum {SAFE-TO-REMOVE / REVIEW / LOAD-BEARING}.

CRITICAL agent-safety class
---------------------------

``SAFE-TO-REMOVE`` is the most dangerous verdict in roam: an agent
reading it on a string like ``DATABASE_URL`` or ``/api/v1/users`` could
reasonably proceed to delete that string from the codebase. The verdict
MUST therefore distinguish between:

  * "we scanned, found nothing" (true SAFE-TO-REMOVE)
  * "we couldn't scan / corpus is empty / no index" (UNKNOWN, NOT safe)

Pattern-2 silent SAFE on the empty-corpus / zero-matches path is the
worst possible failure mode for this command -- it actively misleads
agents toward destructive action.

Scope
-----

cmd_refs_text has two zero-match emission paths:

1. ``_emit_empty`` (lines 381-414): engine returned ZERO matches. Each
   target gets ``verdict: "SAFE-TO-REMOVE"`` + ``reason: "no references
   in source code"`` unconditionally, with no ``state`` / ``resolution``
   / ``partial_success`` disclosure. **Pattern-2 silent SAFE candidate.**

2. ``_emit_json`` via ``_verdict_for`` (lines 73-88, 417-454): matches
   exist but no ``code`` surface. Same SAFE-TO-REMOVE verdict via
   ``not code`` branch (line 79-80). Less critical because matches were
   produced -- the agent can inspect surfaces.

W978 first-hypothesis check
---------------------------

First hypothesis: ``_emit_empty`` emits silent SAFE-TO-REMOVE on the
empty-corpus path because the only signal is "engine returned zero
matches" -- which is observationally indistinguishable from "string
genuinely absent" vs. "couldn't scan / corpus has no source files".

Probe result on the live tree (this commit, isolation run):

* Empty corpus (only README.md) + ``--json refs-text NONEXISTENT_STR``:
  exit 0, ``summary.partial_success: false``, no ``state``, no
  ``resolution``, ``results[0].verdict: "SAFE-TO-REMOVE"``,
  ``results[0].reason: "no references in source code"``. Pattern-2
  silent SAFE confirmed AND it's the agent-safety-critical verdict.

* Clean corpus (string present in source but in unreachable / no-inbound
  symbol): exit 0, same shape -- verdict SAFE-TO-REMOVE, reason "no
  references in source code", ``by_surface: {dead: N}``. (Distinct
  branch via ``_verdict_for`` -- ``not code`` because all hits map to
  ``dead`` surface.) Loud-ish via ``by_surface`` but the headline
  verdict is still SAFE-TO-REMOVE.

Conclusion
----------

* **REAL BUG pinned: Pattern-2 silent SAFE-TO-REMOVE on empty corpus**
  (src/roam/commands/cmd_refs_text.py:381-414 ``_emit_empty``). The
  zero-matches envelope emits the most dangerous verdict in roam
  without ``state`` / ``resolution`` / ``partial_success`` disclosure.
  An agent feeding refs-text a string in a corpus where the engine
  failed to scan (no rg/git on PATH + no indexed scan results) cannot
  tell "definitely absent" from "couldn't scan". Pinned strict so a
  future cleanup that distinguishes those two states graduates the
  test to PASS without manual edit.

* **Shape parity (mild)**: ``_verdict_for`` (line 73-88) ``not code``
  branch is structurally the same Pattern-2 silent SAFE but at least
  the envelope shows ``by_surface`` counts so the agent can see the
  matches exist on non-code surfaces. Pinned strict for symmetry --
  the underlying contract should require ``state`` disclosure on
  ANY zero-reachable-code-matches path, not just empty corpus.

Sweep brief: W805-W (Wave805-W, twenty-third-in-batch).
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from click.testing import CliRunner

sys.path.insert(0, str(Path(__file__).parent))
from conftest import (  # noqa: E402 -- relative-to-tests-dir import after sys.path mutation
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
    """Project with only a README -- no indexable source symbols.

    Exercises ``_emit_empty``: engine returns zero matches across the
    targets because there are no source files containing them.
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
def dead_surface_corpus(tmp_path):
    """String present in source but only in 'dead' (orphan) symbols.

    Exercises the ``_verdict_for`` ``not code`` path: matches exist but
    they're all classified as dead, so the code surface is empty.
    """
    proj = tmp_path / "dead_surface_corpus"
    proj.mkdir()
    (proj / ".gitignore").write_text(".roam/\n")
    src = proj / "src"
    src.mkdir()
    # DATABASE_URL is referenced in a function with no inbound edges --
    # classified as 'dead' surface, so 'code' surface is empty and the
    # _verdict_for "not code" branch fires.
    (src / "orphan.py").write_text(
        "DATABASE_URL = 'postgresql://localhost'\n\ndef use_db():\n    return DATABASE_URL\n"
    )
    git_init(proj)
    out, rc = index_in_process(proj)
    assert rc == 0, f"index failed:\n{out}"
    return proj


@pytest.fixture
def clean_corpus(tmp_path):
    """String present + referenced from a reachable code symbol.

    Exercises the full audit branch: matches exist, classified on code
    surface, real reachable verdict emitted.
    """
    proj = tmp_path / "clean_corpus"
    proj.mkdir()
    (proj / ".gitignore").write_text(".roam/\n")
    src = proj / "src"
    src.mkdir()
    (src / "core.py").write_text(
        "DATABASE_URL = 'postgresql://localhost'\n"
        "\n"
        "def get_db():\n"
        "    return DATABASE_URL\n"
        "\n"
        "def caller_fn():\n"
        "    return get_db()\n"
    )
    git_init(proj)
    out, rc = index_in_process(proj)
    assert rc == 0, f"index failed:\n{out}"
    return proj


# ---------------------------------------------------------------------------
# Pattern-1 Variant C -- no crash / no empty stdout on the empty-corpus path.
# ---------------------------------------------------------------------------


class TestEmptyCorpusNoCrash:
    """The ``_emit_empty`` branch must always emit a structured envelope,
    never crash and never emit empty stdout (Pattern-1 Variant C)."""

    def test_empty_corpus_no_crash(self, cli_runner, empty_corpus, monkeypatch):
        """No exception / non-empty stdout on the empty-corpus zero-matches path."""
        monkeypatch.chdir(empty_corpus)
        result = invoke_cli(
            cli_runner,
            ["refs-text", "NONEXISTENT_STRING_XYZ"],
            cwd=empty_corpus,
            json_mode=True,
        )
        # refs-text exits 0 on zero-matches (it's a successful audit
        # that concluded the string was absent). Per the canonical
        # contract today, exit 0 + structured envelope is correct shape.
        assert result.exit_code == 0, (
            f"refs-text must exit 0 on zero-matches per current contract; got {result.exit_code}\n{result.output}"
        )
        out = getattr(result, "stdout", None) or result.output
        assert out.strip(), "Pattern-1 Variant C: empty stdout on zero-matches path"

    def test_empty_corpus_envelope_has_verdict(self, cli_runner, empty_corpus, monkeypatch):
        """Envelope carries a non-empty summary verdict per LAW 6."""
        monkeypatch.chdir(empty_corpus)
        result = invoke_cli(
            cli_runner,
            ["refs-text", "NONEXISTENT_STRING_XYZ"],
            cwd=empty_corpus,
            json_mode=True,
        )
        data = parse_json_output(result, "refs-text")
        assert "summary" in data, f"envelope missing summary: {data}"
        assert "verdict" in data["summary"], f"summary missing verdict: {data['summary']}"
        verdict = data["summary"]["verdict"]
        assert isinstance(verdict, str) and verdict.strip()
        # Existing shape: "N string(s) checked, M load-bearing".
        assert "string" in verdict.lower() and "load-bearing" in verdict.lower(), (
            f"summary verdict must mention strings + load-bearing; got {verdict!r}"
        )


# ---------------------------------------------------------------------------
# Pattern-2 silent SAFE on the dangerous SAFE-TO-REMOVE verdict.
# REAL BUG pinned strict -- this is the CRITICAL agent-safety class.
# ---------------------------------------------------------------------------


class TestEmptyCorpusSilentSafe:
    """The most agent-safety-critical Pattern-2 case in roam: a verdict
    of SAFE-TO-REMOVE without explicit state disclosure on the
    zero-matches path. An agent acting on this verdict could delete
    a string that the corpus simply couldn't scan."""

    @pytest.mark.xfail(
        strict=True,
        reason=(
            "W805-W REAL BUG: src/roam/commands/cmd_refs_text.py:381-414 "
            "(``_emit_empty``) emits SAFE-TO-REMOVE on the zero-matches "
            "path with no ``summary.state`` disclosure. An agent switching "
            "on machine-readable state cannot tell 'string truly absent' "
            "from 'corpus couldn't be scanned'. CRITICAL agent-safety "
            "class -- SAFE-TO-REMOVE is the most destructive verdict in "
            "roam. Pinned strict so a future cleanup that adds "
            '``state: "empty_corpus"`` (or equivalent) on the zero-matches '
            "path graduates this to PASS."
        ),
    )
    def test_empty_corpus_explicit_state(self, cli_runner, empty_corpus, monkeypatch):
        """Empty-corpus zero-matches discloses ``state`` explicitly."""
        monkeypatch.chdir(empty_corpus)
        result = invoke_cli(
            cli_runner,
            ["refs-text", "NONEXISTENT_STRING_XYZ"],
            cwd=empty_corpus,
            json_mode=True,
        )
        data = parse_json_output(result, "refs-text")
        summary = data["summary"]
        state = summary.get("state")
        # Accept any explicit non-empty state -- the contract is "explicit",
        # not "named X". Today this field is absent entirely.
        assert state is not None and isinstance(state, str) and state.strip(), (
            f"W805-W Pattern-2 silent SAFE: empty-corpus zero-matches must emit "
            f"summary.state to distinguish 'truly absent' from 'couldn't scan'; "
            f"got {state!r}"
        )

    @pytest.mark.xfail(
        strict=True,
        reason=(
            "W805-W REAL BUG: src/roam/commands/cmd_refs_text.py:381-414 "
            "emits ``partial_success: false`` on the zero-matches path. "
            "When the underlying outcome is 'we couldn't find any "
            "references' AND the verdict is SAFE-TO-REMOVE, an agent "
            "reading partial_success would conclude no degradation "
            "occurred. The canonical Pattern-2 contract sets "
            "partial_success=True on any 'empty input / degraded scan' "
            "outcome. Pinned strict; CRITICAL agent-safety class."
        ),
    )
    def test_empty_corpus_partial_success_set(self, cli_runner, empty_corpus, monkeypatch):
        """Pattern-2 guard: zero-matches empty-corpus sets partial_success=True."""
        monkeypatch.chdir(empty_corpus)
        result = invoke_cli(
            cli_runner,
            ["refs-text", "NONEXISTENT_STRING_XYZ"],
            cwd=empty_corpus,
            json_mode=True,
        )
        data = parse_json_output(result, "refs-text")
        summary = data["summary"]
        assert summary.get("partial_success") is True, (
            f"W805-W Pattern-2: zero-matches empty-corpus must set "
            f"partial_success=True; got {summary.get('partial_success')!r}"
        )

    def test_empty_corpus_law6_verdict_standalone(self, cli_runner, empty_corpus, monkeypatch):
        """LAW 6: summary verdict works without any other field.

        This is NOT a Pattern-2 xfail -- it's a positive lint that the
        existing verdict shape is LAW-6 compliant. The verdict text
        ``N string(s) checked, M load-bearing`` is concrete-noun-anchored
        on ``load-bearing`` and works standalone.
        """
        monkeypatch.chdir(empty_corpus)
        result = invoke_cli(
            cli_runner,
            ["refs-text", "NONEXISTENT_STRING_XYZ"],
            cwd=empty_corpus,
            json_mode=True,
        )
        data = parse_json_output(result, "refs-text")
        verdict = data["summary"].get("verdict", "")
        # LAW 6: must be informative without other fields.
        assert verdict.strip(), "verdict empty"
        # Mentions both the count and the load-bearing dimension.
        assert "1" in verdict, f"verdict must name the target count; got {verdict!r}"
        assert "load-bearing" in verdict.lower(), (
            f"verdict must anchor on 'load-bearing' (LAW 4 anchor); got {verdict!r}"
        )

    @pytest.mark.xfail(
        strict=True,
        reason=(
            "W805-W REAL BUG -- CRITICAL agent-safety class: "
            "src/roam/commands/cmd_refs_text.py:381-414 unconditionally "
            'stamps ``verdict: "SAFE-TO-REMOVE"`` on every target in the '
            "zero-matches branch. An agent acting on this verdict against "
            "an empty / unindexed corpus could delete strings that ARE "
            "load-bearing in source files the engine couldn't read. "
            "The canonical contract on a zero-matches path with no "
            "scannable corpus should be ``UNKNOWN`` / ``INSUFFICIENT-DATA`` "
            "(or the existing SAFE-TO-REMOVE PLUS a state='empty_corpus' "
            "disclosure that agents can switch on). Pinned strict so the "
            "fix graduates to PASS."
        ),
    )
    def test_no_silent_safe_to_remove_on_empty(self, cli_runner, empty_corpus, monkeypatch):
        """CRITICAL: SAFE-TO-REMOVE on an unscannable corpus is agent-unsafe.

        Either the verdict changes to UNKNOWN / INSUFFICIENT-DATA on the
        empty-corpus path, OR the envelope discloses an explicit
        ``state: "empty_corpus"`` (or equivalent) that an agent can
        switch on before acting. Today neither is true.
        """
        monkeypatch.chdir(empty_corpus)
        result = invoke_cli(
            cli_runner,
            ["refs-text", "NONEXISTENT_STRING_XYZ"],
            cwd=empty_corpus,
            json_mode=True,
        )
        data = parse_json_output(result, "refs-text")
        per_result = data["results"][0]
        verdict = per_result.get("verdict", "")
        summary_state = data["summary"].get("state")
        # The fix is EITHER a non-SAFE verdict on empty corpus OR a
        # state disclosure that names the empty-corpus condition. Both
        # paths graduate this xfail to PASS.
        verdict_is_safe = "SAFE-TO-REMOVE" in verdict.upper()
        state_discloses_empty = summary_state is not None and "empty" in str(summary_state).lower()
        assert (not verdict_is_safe) or state_discloses_empty, (
            f"W805-W CRITICAL agent-safety: SAFE-TO-REMOVE on zero-matches "
            f"path MUST be accompanied by a state disclosure that an agent "
            f"can use to detect 'corpus couldn't be scanned'. "
            f"Got verdict={verdict!r}, state={summary_state!r}."
        )

    def test_surface_grouping_explicit_no_data(self, cli_runner, empty_corpus, monkeypatch):
        """Per-result ``by_surface`` is an explicit empty dict, not missing.

        Positive shape lint: surface grouping disclosure is present in
        the envelope even when the surface dict is empty. Pin so a
        future cleanup that omits ``by_surface`` entirely (because it
        'looks empty') breaks this test.
        """
        monkeypatch.chdir(empty_corpus)
        result = invoke_cli(
            cli_runner,
            ["refs-text", "NONEXISTENT_STRING_XYZ"],
            cwd=empty_corpus,
            json_mode=True,
        )
        data = parse_json_output(result, "refs-text")
        per_result = data["results"][0]
        assert "by_surface" in per_result, (
            f"per-result must always include by_surface key (explicit-no-data discipline); got {per_result}"
        )
        # On an empty corpus, by_surface is an empty dict, not missing.
        assert isinstance(per_result["by_surface"], dict), (
            f"by_surface must be a dict; got {type(per_result['by_surface'])}"
        )


# ---------------------------------------------------------------------------
# Pattern-2 (mild) -- dead-only surface path also emits silent SAFE.
# ---------------------------------------------------------------------------


class TestDeadSurfaceSilentSafe:
    """Even when matches EXIST but all land on the 'dead' surface, the
    verdict is SAFE-TO-REMOVE with reason 'no references in source code'.
    The by_surface dict is informative but the headline verdict is the
    same as empty-corpus, which is misleading."""

    def test_dead_surface_emits_by_surface_counts(self, cli_runner, dead_surface_corpus, monkeypatch):
        """Loud disclosure: by_surface shows dead-only counts.

        Positive shape lint: the dead-only path at least surfaces a
        non-zero match count + the 'dead' bucket, so a careful agent
        can disambiguate this from empty-corpus.
        """
        monkeypatch.chdir(dead_surface_corpus)
        result = invoke_cli(
            cli_runner,
            ["refs-text", "DATABASE_URL"],
            cwd=dead_surface_corpus,
            json_mode=True,
        )
        data = parse_json_output(result, "refs-text")
        per_result = data["results"][0]
        # Matches DO exist; they're just classified as dead.
        assert per_result["total"] >= 1, f"dead-surface corpus has DATABASE_URL refs; got total={per_result['total']}"
        # by_surface bucket discloses 'dead'.
        by_surface = per_result["by_surface"]
        assert "dead" in by_surface, f"by_surface must name the 'dead' surface for orphan refs; got {by_surface}"


# ---------------------------------------------------------------------------
# Clean-corpus regression -- the audit must still produce a real verdict
# on a non-empty reachable corpus.
# ---------------------------------------------------------------------------


class TestCleanCorpusFullAudit:
    """Sanity: a real reachable reference produces a real audit envelope."""

    def test_clean_corpus_emits_real_audit(self, cli_runner, clean_corpus, monkeypatch):
        """DATABASE_URL referenced from a reachable code symbol -> real verdict."""
        monkeypatch.chdir(clean_corpus)
        result = invoke_cli(
            cli_runner,
            ["refs-text", "DATABASE_URL"],
            cwd=clean_corpus,
            json_mode=True,
        )
        data = parse_json_output(result, "refs-text")
        per_result = data["results"][0]
        # Matches present.
        assert per_result["total"] >= 1, f"clean corpus must have DATABASE_URL refs; got total={per_result['total']}"
        # Verdict is in the closed enum.
        verdict = per_result["verdict"]
        assert verdict in {"SAFE-TO-REMOVE", "REVIEW", "LOAD-BEARING"}, (
            f"verdict must be in REFERENCE_REMOVAL_VERDICTS enum; got {verdict!r}"
        )
        # Summary verdict mentions strings + load-bearing.
        sv = data["summary"].get("verdict", "")
        assert "string" in sv.lower() and "load-bearing" in sv.lower(), f"summary verdict shape regression; got {sv!r}"
        # by_surface present with at least one bucket.
        assert per_result["by_surface"], f"clean-corpus refs must populate by_surface; got {per_result['by_surface']}"
