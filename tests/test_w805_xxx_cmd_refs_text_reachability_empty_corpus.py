"""W805-XXX -- empty-corpus reachability-axis Pattern-1-V-D / Pattern-2 smoke
on ``roam refs-text``.

Seventy-seventh-in-batch W805 sweep. Reachability-axis sibling of the
W805-W empty-corpus state-disclosure pin AND mirror of the W805-UUU
``cmd_grep`` reachability-axis finding at ``cmd_grep.py:399-401``.

W978 first-hypothesis: distinct axis from W805-W + W607-I
---------------------------------------------------------

Before writing this file, audited ``cmd_refs_text.py`` head-to-tail.
The empty-corpus zero-matches early-return at ``cmd_refs_text.py:327-329``
fires BEFORE the reachability resolver at ``cmd_refs_text.py:348-389``.
Three pre-existing axes are SEPARATELY pinned and remain orthogonal:

  * **W805-W axis (state-on-empty-corpus)**: pins the missing
    ``state``/``partial_success``/``UNKNOWN`` disclosure on empty corpus
    when NO ``--reachable-from`` flag is passed. Lives in
    ``tests/test_w805_w_cmd_refs_text_empty_corpus.py``.
  * **W607-I axis (subprocess-degrade)**: pins the missing
    ``warnings_out`` lineage on subprocess failures / engine fan-out
    fallthrough. Lives in
    ``tests/test_w607_i_cmd_refs_text_warnings_out_envelope.py``.
  * **W805-XXX axis (reachability bypass on empty corpus -- THIS FILE)**:
    pins the missing Pattern-1D ``state="unresolved_entry"`` /
    ``resolution="unresolved"`` / ``warnings_out`` disclosure that
    SHOULD fire when ``--reachable-from <bogus_symbol>`` is passed but
    is silently dropped because ``_emit_empty`` short-circuits before
    the resolver block runs.

Reachability axis isolation
---------------------------

The `--reachable-from` flag's Pattern-1D state-disclosure lives at
``cmd_refs_text.py:348-389``. On the clean-corpus path it correctly
emits ``state="unresolved_entry"`` + ``resolution="unresolved"`` +
``warnings_out=["refs_text_reachability_degraded:..."]`` + exit 1.
On the empty-corpus path the early ``_emit_empty()`` return at line
327-329 fires FIRST, dropping ALL of that disclosure.

Probe results (this commit, isolation run on /tmp test corpora):

* Empty corpus + ``--reachable-from unresolved_xyz NONEXISTENT``:
  exit 0, ``partial_success: false``, no ``state``, no ``resolution``,
  no ``warnings_out``, ``verdict: SAFE-TO-REMOVE``. **The user's
  ``--reachable-from`` flag is silently dropped on the floor.**

* Clean corpus + ``--reachable-from unresolved_xyz HELLO_WORLD``:
  exit 1, ``state="unresolved_entry"``, ``resolution="unresolved"``,
  ``warnings_out=["refs_text_reachability_degraded:..."]``,
  ``verdict: "entry symbol 'unresolved_xyz' not found in index"``.
  Pattern-1D contract is honored when matches exist.

CRITICAL agent-safety class
---------------------------

This is HIGHER severity than W805-W in one specific scenario:

  An agent passes ``refs-text X --reachable-from <name>`` expecting
  Pattern-1D protection against typo'd / unresolved entry symbols.
  On a corpus where ``X`` is absent (empty corpus, gitignored
  sub-tree, fresh index, etc.) the agent receives ``SAFE-TO-REMOVE``
  with no indication that the requested reachability filter was
  dropped. The agent then deletes ``X`` from the codebase under
  the false belief that "no reachable code references the string."

The fix template (NOT applied per "DO NOT fix; pin via xfail-strict"):
move the reachability resolution / unresolved-entry guard BEFORE the
``_emit_empty`` early-return, OR thread the unresolved-entry state into
``_emit_empty`` so the zero-matches envelope discloses
``state="unresolved_entry"`` + ``warnings_out`` when the filter was
specified but never applied.

W978 + W907 compliance
----------------------

* W978: probed in isolation, confirmed the reachability axis is
  distinct from the subprocess (W607-I) and empty-corpus-state
  (W805-W) axes.
* W907: no false-cycle docstrings. ``_emit_empty`` early-return is a
  real architectural choice, not a defensive duplication.

Sweep brief: W805-XXX (Wave805-XXX, seventy-seventh-in-batch).
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


def _parse_json_any_exit(result):
    """Parse JSON output from a CliRunner result without asserting exit code.

    ``parse_json_output`` asserts ``exit_code == 0``, which conflicts with the
    Pattern-1D contract (clean-corpus + unresolved-entry exits 1). This helper
    parses the JSON regardless of exit code so tests can independently assert
    on exit code AND envelope shape.
    """
    import json as _json

    raw = getattr(result, "stdout", None) or result.output
    return _json.loads(raw)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def cli_runner():
    return CliRunner()


@pytest.fixture
def empty_corpus(tmp_path):
    """Project with only a README -- no indexable source symbols.

    Engine returns zero matches across the targets because there are no
    source files containing them. Exercises the ``_emit_empty`` early
    return at ``cmd_refs_text.py:327-329``.
    """
    proj = tmp_path / "empty_corpus_xxx"
    proj.mkdir()
    (proj / ".gitignore").write_text(".roam/\n")
    (proj / "README.md").write_text("Empty corpus project for W805-XXX.\n")
    git_init(proj)
    out, rc = index_in_process(proj)
    assert rc == 0, f"index failed:\n{out}"
    return proj


@pytest.fixture
def clean_corpus(tmp_path):
    """String present + referenced from a reachable code symbol.

    Used to assert that Pattern-1D ``unresolved_entry`` disclosure
    works when matches exist (the orthogonal positive control).
    """
    proj = tmp_path / "clean_corpus_xxx"
    proj.mkdir()
    (proj / ".gitignore").write_text(".roam/\n")
    src = proj / "src"
    src.mkdir()
    (src / "core.py").write_text(
        "API_ROUTE_USERS = '/api/v1/users'\n"
        "\n"
        "def get_users():\n"
        "    return API_ROUTE_USERS\n"
        "\n"
        "def caller_fn():\n"
        "    return get_users()\n"
    )
    git_init(proj)
    out, rc = index_in_process(proj)
    assert rc == 0, f"index failed:\n{out}"
    return proj


# ---------------------------------------------------------------------------
# Positive control: clean corpus + --reachable-from <unresolved> honors
# Pattern-1D disclosure. This MUST pass today to anchor the bug as
# axis-specific (empty-corpus only), not flag-wide.
# ---------------------------------------------------------------------------


class TestCleanCorpusUnresolvedDisclosure:
    """Positive control: when matches exist, the Pattern-1D
    ``state="unresolved_entry"`` disclosure fires correctly. Anchors
    the W805-XXX bug as empty-corpus-axis-only."""

    def test_clean_corpus_unresolved_entry_pattern_1d_state(self, cli_runner, clean_corpus, monkeypatch):
        """Clean corpus + bogus --reachable-from emits Pattern-1D state."""
        monkeypatch.chdir(clean_corpus)
        result = invoke_cli(
            cli_runner,
            ["refs-text", "API_ROUTE_USERS", "--reachable-from", "no_such_symbol_xyz"],
            cwd=clean_corpus,
            json_mode=True,
        )
        # Pattern-1D exit code per cmd_refs_text.py:389.
        assert result.exit_code == 1, (
            f"clean-corpus unresolved-entry must exit 1; got {result.exit_code}\n{result.output}"
        )
        data = _parse_json_any_exit(result)
        summary = data["summary"]
        assert summary.get("state") == "unresolved_entry", (
            f"positive control: clean corpus unresolved-entry must set "
            f"state='unresolved_entry'; got {summary.get('state')!r}"
        )
        assert summary.get("resolution") == "unresolved", (
            f"positive control: must set resolution='unresolved'; got {summary.get('resolution')!r}"
        )
        assert summary.get("partial_success") is True, (
            f"positive control: must set partial_success=True; got {summary.get('partial_success')!r}"
        )


# ---------------------------------------------------------------------------
# Pattern-1-V-D + Pattern-2: empty corpus + --reachable-from <unresolved>
# silently drops the reachability resolver's state disclosure.
# REAL BUG pinned strict -- distinct axis from W805-W + W607-I.
# ---------------------------------------------------------------------------


class TestEmptyCorpusReachabilityBypass:
    """The CRITICAL W805-XXX finding: ``--reachable-from <bogus>`` on
    empty corpus is silently dropped because ``_emit_empty`` returns
    before the resolver block runs. Higher severity than W805-W because
    an agent can deliberately pass a bogus entry expecting Pattern-1D
    protection and still receive SAFE-TO-REMOVE."""

    @pytest.mark.xfail(
        strict=True,
        reason=(
            "W805-XXX REAL BUG: src/roam/commands/cmd_refs_text.py:327-329 "
            "(``_emit_empty`` early-return) fires BEFORE the reachability "
            "resolver at line 348-389. When the user passes "
            "``--reachable-from <unresolved_symbol>`` on an empty corpus, "
            "the Pattern-1D ``state='unresolved_entry'`` / "
            "``resolution='unresolved'`` disclosure that the clean-corpus "
            "path emits is silently dropped. Pinned strict so a future "
            "fix that hoists the resolver above ``_emit_empty`` (or "
            "threads ``unresolved_entry`` state into the zero-matches "
            "envelope) graduates this to PASS. Mirror of W805-UUU "
            "(cmd_grep.py:399-401) on the cmd_refs_text reachability axis."
        ),
    )
    def test_empty_corpus_reachable_from_unresolved_pattern_1d_disclosure(self, cli_runner, empty_corpus, monkeypatch):
        """Empty corpus + bogus --reachable-from MUST disclose Pattern-1D state."""
        monkeypatch.chdir(empty_corpus)
        result = invoke_cli(
            cli_runner,
            [
                "refs-text",
                "NONEXISTENT_STRING_XYZ",
                "--reachable-from",
                "totally_unresolved_symbol_qqq",
            ],
            cwd=empty_corpus,
            json_mode=True,
        )
        data = parse_json_output(result, "refs-text")
        summary = data["summary"]
        # Acceptance: either an explicit unresolved-entry state OR a
        # warnings_out marker the agent can switch on. Both indicate the
        # filter was at least acknowledged. Today neither is true.
        state = summary.get("state")
        warnings_out = summary.get("warnings_out") or []
        has_unresolved_state = state is not None and "unresolved" in str(state).lower()
        has_reachability_warning = any(
            "reachability" in str(w).lower() or "unresolved" in str(w).lower() for w in warnings_out
        )
        assert has_unresolved_state or has_reachability_warning, (
            f"W805-XXX CRITICAL agent-safety: --reachable-from <unresolved> "
            f"on empty corpus MUST disclose the unresolved-entry state via "
            f"summary.state OR summary.warnings_out so the agent knows the "
            f"reachability filter was dropped. Today the filter is silently "
            f"ignored on the zero-matches path. "
            f"Got state={state!r}, warnings_out={warnings_out!r}, "
            f"partial_success={summary.get('partial_success')!r}, "
            f"verdict={summary.get('verdict')!r}."
        )

    @pytest.mark.xfail(
        strict=True,
        reason=(
            "W805-XXX REAL BUG: when --reachable-from is passed but never "
            "applied (empty-corpus early-return at cmd_refs_text.py:327-329), "
            "the Pattern-2 contract requires ``partial_success=True`` because "
            "the requested reachability filter was silently dropped. Today "
            "the empty-corpus path emits ``partial_success: false`` "
            "unconditionally. Pinned strict; CRITICAL safety class because "
            "an agent reading partial_success=false would conclude no "
            "degradation occurred."
        ),
    )
    def test_empty_corpus_reachable_from_partial_success_set(self, cli_runner, empty_corpus, monkeypatch):
        """When --reachable-from is dropped, partial_success must be True."""
        monkeypatch.chdir(empty_corpus)
        result = invoke_cli(
            cli_runner,
            [
                "refs-text",
                "NONEXISTENT_STRING_XYZ",
                "--reachable-from",
                "totally_unresolved_symbol_qqq",
            ],
            cwd=empty_corpus,
            json_mode=True,
        )
        data = parse_json_output(result, "refs-text")
        assert data["summary"].get("partial_success") is True, (
            f"W805-XXX Pattern-2: empty-corpus path with --reachable-from "
            f"dropped MUST set partial_success=True; got "
            f"{data['summary'].get('partial_success')!r}"
        )

    @pytest.mark.xfail(
        strict=True,
        reason=(
            "W805-XXX REAL BUG -- CRITICAL agent-safety: empty corpus + "
            "--reachable-from <bogus> emits SAFE-TO-REMOVE with no "
            "reachability-degrade lineage. An agent passing --reachable-from "
            "with a typo'd symbol expects Pattern-1D protection (exit 1, "
            "unresolved-entry state). On empty corpus the protection is "
            "silently bypassed and the most destructive verdict in roam "
            "is returned with exit 0. Higher severity than W805-W because "
            "the agent explicitly opted into a safety check. Pinned strict; "
            "fix hoists the resolver above ``_emit_empty`` or denies "
            "SAFE-TO-REMOVE when the filter was dropped."
        ),
    )
    def test_empty_corpus_reachable_from_no_silent_safe_to_remove(self, cli_runner, empty_corpus, monkeypatch):
        """CRITICAL: SAFE-TO-REMOVE on dropped-reachability-filter is agent-unsafe."""
        monkeypatch.chdir(empty_corpus)
        result = invoke_cli(
            cli_runner,
            [
                "refs-text",
                "NONEXISTENT_STRING_XYZ",
                "--reachable-from",
                "totally_unresolved_symbol_qqq",
            ],
            cwd=empty_corpus,
            json_mode=True,
        )
        data = parse_json_output(result, "refs-text")
        per_result = data["results"][0]
        verdict = per_result.get("verdict", "")
        summary = data["summary"]
        # Fix accepted EITHER as a non-SAFE verdict OR a lineage marker
        # the agent can switch on. Both graduate this xfail to PASS.
        verdict_is_safe = "SAFE-TO-REMOVE" in verdict.upper()
        state_discloses_unresolved = (
            summary.get("state") is not None and "unresolved" in str(summary.get("state")).lower()
        )
        warnings_out = summary.get("warnings_out") or []
        has_reachability_warning = any("reachability" in str(w).lower() for w in warnings_out)
        assert (not verdict_is_safe) or state_discloses_unresolved or has_reachability_warning, (
            f"W805-XXX CRITICAL: empty-corpus + --reachable-from <bogus> "
            f"MUST NOT silently emit SAFE-TO-REMOVE. Either the verdict "
            f"changes (UNKNOWN / INSUFFICIENT-DATA / RESOLUTION-FAILED) "
            f"OR the envelope discloses unresolved-entry state OR a "
            f"reachability-degrade warning. Got verdict={verdict!r}, "
            f"state={summary.get('state')!r}, warnings_out={warnings_out!r}."
        )


# ---------------------------------------------------------------------------
# Pattern-1 Variant C parity -- no crash / no empty stdout (positive lint).
# ---------------------------------------------------------------------------


class TestEmptyCorpusReachabilityNoCrash:
    """The reachability-axis path must always emit a structured envelope,
    never crash and never emit empty stdout."""

    def test_empty_corpus_reachable_from_no_crash(self, cli_runner, empty_corpus, monkeypatch):
        """No exception / non-empty stdout on the empty-corpus + reachability path."""
        monkeypatch.chdir(empty_corpus)
        result = invoke_cli(
            cli_runner,
            [
                "refs-text",
                "NONEXISTENT_STRING_XYZ",
                "--reachable-from",
                "totally_unresolved_symbol_qqq",
            ],
            cwd=empty_corpus,
            json_mode=True,
        )
        # Today this path exits 0 (which is part of the bug — it should
        # exit 1 like the clean-corpus path). Accept either for the
        # no-crash lint; the exit-code axis is pinned via the strict
        # xfail above through state/verdict checks.
        assert result.exit_code in (0, 1), (
            f"refs-text must exit 0 or 1 on zero-matches + reachability path; got {result.exit_code}\n{result.output}"
        )
        out = getattr(result, "stdout", None) or result.output
        assert out.strip(), "Pattern-1 Variant C: empty stdout on zero-matches + reachability path"

    def test_empty_corpus_reachable_from_envelope_has_verdict(self, cli_runner, empty_corpus, monkeypatch):
        """LAW 6: summary verdict works without any other field."""
        monkeypatch.chdir(empty_corpus)
        result = invoke_cli(
            cli_runner,
            [
                "refs-text",
                "NONEXISTENT_STRING_XYZ",
                "--reachable-from",
                "totally_unresolved_symbol_qqq",
            ],
            cwd=empty_corpus,
            json_mode=True,
        )
        data = parse_json_output(result, "refs-text")
        verdict = data.get("summary", {}).get("verdict", "")
        assert isinstance(verdict, str) and verdict.strip(), f"LAW 6: verdict must be non-empty string; got {verdict!r}"


# ---------------------------------------------------------------------------
# W607-I subprocess-axis orthogonality: reachability-bypass failures
# must NOT emit any ``refs_text_*`` engine markers. The two axes are
# distinct contracts.
# ---------------------------------------------------------------------------


class TestW607ISubprocessAxisOrthogonality:
    """Confirm the W805-XXX reachability-axis bug is structurally
    distinct from the W607-I subprocess-degrade axis. Empty-corpus
    reachability-bypass must NOT spuriously emit engine-pin /
    fan-out-fallback / ripgrep-failed markers."""

    def test_no_w607_i_subprocess_markers_on_reachability_axis(self, cli_runner, empty_corpus, monkeypatch):
        """W607-I engine markers absent on a pure reachability-axis bypass.

        The W607-I markers are ``refs_text_engine_pin_missing``,
        ``refs_text_engine_fanout_fallback``, ``refs_text_ripgrep_failed``,
        ``refs_text_git_grep_failed``, ``refs_text_indexed_scan_failed``,
        ``refs_text_engine_failed``. The W805-XXX reachability axis
        marker (if/when added) is
        ``refs_text_reachability_degraded:unresolved_entry:...`` —
        already emitted on the CLEAN-corpus + unresolved-entry path,
        which is the clean-axis precedent.
        """
        # Ensure no engine-pin spurious trigger.
        monkeypatch.delenv("ROAM_GREP_ENGINE", raising=False)
        monkeypatch.chdir(empty_corpus)
        result = invoke_cli(
            cli_runner,
            [
                "refs-text",
                "NONEXISTENT_STRING_XYZ",
                "--reachable-from",
                "totally_unresolved_symbol_qqq",
            ],
            cwd=empty_corpus,
            json_mode=True,
        )
        data = parse_json_output(result, "refs-text")
        warnings_out = data.get("summary", {}).get("warnings_out") or []
        # The W607-I engine-degrade markers MUST NOT appear here:
        # this is a pure reachability-axis bypass, not a subprocess
        # degrade. If/when the W805-XXX fix lands and adds a
        # ``refs_text_reachability_degraded:`` marker on this path,
        # that's the W805-XXX axis, not the W607-I axis — explicitly
        # allow the reachability marker.
        engine_markers = [
            "refs_text_engine_pin_missing",
            "refs_text_engine_fanout_fallback",
            "refs_text_ripgrep_failed",
            "refs_text_git_grep_failed",
            "refs_text_indexed_scan_failed",
            "refs_text_engine_failed",
        ]
        for w in warnings_out:
            for marker in engine_markers:
                assert not str(w).startswith(marker + ":"), (
                    f"W607-I orthogonality breach: pure reachability-axis "
                    f"bypass spuriously emitted engine-degrade marker "
                    f"{marker!r}. The W805-XXX reachability axis is "
                    f"distinct from the W607-I subprocess axis. "
                    f"Got warnings_out={warnings_out!r}."
                )
