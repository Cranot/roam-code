"""W805-KK - Empty-corpus Pattern-2 smoke for ``roam_for_refactor``.

Thirty-seventh-in-batch W805 sweep. Sibling of W805-F (``for_bug_fix``).
``for_refactor`` is an MCP-only compound exposed via
``@_tool(name="roam_for_refactor")`` at
``src/roam/mcp_server.py:6423-6470`` -- there is no
``cmd_for_refactor.py`` under ``src/roam/commands/``; the recipe lives
entirely in the MCP server and dispatches via
``_safe_run([_cr(<key>), ...])``.

Recipe composition (mcp_server.py:6457-6464): four subcommands --

  - ``preflight  <symbol>``    (symbol-anchored)
  - ``impact     <symbol>``    (symbol-anchored)
  - ``complexity --limit 5``    (NOT symbol-anchored; corpus-wide)
  - ``clones     --top 20``    (NOT symbol-anchored; corpus-wide)

Each runs in-process via Click and returns a JSON envelope. The
compound aggregator is ``_compound_envelope`` at mcp_server.py:4432.

W978 first-hypothesis probe (run BEFORE writing tests):

REAL BUG (Pattern-2 silent SAFE / Variant-D silent success on
degraded resolution) CONFIRMED -- same bug class as W805-F. The
aggregator at ``src/roam/mcp_server.py:4448-4470`` computes::

    for name, data in sub_results:
        if not data or "error" in data:
            err_msg = data.get("error", "empty result") if data else "empty result"
            errors.append({"command": name, "error": err_msg})
        ...
    partial_success = bool(failed_subcommands)

This treats a child envelope as "succeeded" iff its top-level ``error``
key is absent. Children that resolved a target degraded (e.g.
``resolution: "unresolved"`` + ``summary.partial_success: true``)
return structured envelopes with NO top-level ``error`` key -- they
self-disclose partial success in ``summary.partial_success``. The
aggregator never reads that nested signal.

Concrete observation (probed on empty corpus, symbol ``zzMissingSymbol``):

    compound.summary.partial_success     = False                  # WRONG
    compound.summary.failed_subcommands  = []                      # WRONG
    preflight.summary.partial_success    = True (resolution=unresolved)
    impact.summary.partial_success       = True (state=not_found)
    complexity_report.summary.partial_success = False
        (state=no_complexity_data -- legitimately empty)
    clones.summary.partial_success       = False
        (legitimately empty)

So 2/4 children disclose partial_success=True on this axis (vs 3/4 in
W805-F), but the bug shape is identical: the compound silently masks
the nested signal. An agent prompt-cached on
``compound.summary.partial_success`` reads False and proceeds as if
the refactor bundle resolved cleanly. Per CLAUDE.md Pattern-2:

    "Never emit verdict: 'completed' / 'non-conformant' when the
     underlying check failed or didn't run. ... subcommand failure
     must set partial_success: True and name the failed subcommands."

PIN STRATEGY (W978 + accumulate-only constraint):

1. SMOKE (always-on): no crash + envelope shape + LAW 6 verdict +
   per-child sections present + USAGE_ERROR path intact.
2. POSITIVE BASELINE: real symbol on clean corpus -> compound
   ``partial_success: false`` and all children ``partial_success: false``.
3. PATTERN-2 PIN (xfail-strict): on empty corpus / unresolved symbol,
   the compound MUST disclose ``partial_success: true`` because 2/4
   symbol-anchored children disclose ``partial_success: true``.

The fix-forward (separate wave, NOT this one) is one-line at
mcp_server.py:4470 -- also flip ``partial_success`` to True whenever
any child envelope's ``summary.partial_success`` is True. Same fix as
W805-F; both compounds unblock from the same one-line aggregator
patch. Per W978: do NOT fix this wave; pin only.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
from conftest import index_in_process  # noqa: E402

# Import the compound directly. ``roam.mcp_server`` imports only the
# specific fastmcp submodules it needs (NOT the top-level ``fastmcp``
# package which has transitive import errors on some environments), so
# we cannot use ``pytest.importorskip("fastmcp", ...)`` -- it incorrectly
# skips here even when the compound is callable. Probe the actual entry
# point instead and skip iff that itself fails.
try:
    from roam.mcp_server import for_refactor  # noqa: E402
except Exception as _exc:  # pragma: no cover - guarded environments only
    pytest.skip(
        f"roam.mcp_server import failed: {_exc!r}; MCP compound tests require the MCP server module to be importable.",
        allow_module_level=True,
    )


# ---------------------------------------------------------------------------
# Test hygiene: disable the large-response handle-off so envelope inspection
# reads the full compound dict directly (mirrors W805-F + test_situation_compounds).
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _disable_handle_off(monkeypatch):
    monkeypatch.setenv("ROAM_MCP_HANDLE_KB", "0")
    yield


@pytest.fixture(autouse=True)
def _reset_mcp_state():
    """Reset the MCP error-storm counter + result cache before each test.

    Without this, an earlier test in the same xdist worker can push the
    USAGE_ERROR storm counter past its threshold, and ``_structured_error``
    returns a TRIMMED envelope (``first_error_message`` instead of
    ``error``). The W805-KK USAGE_ERROR assertions read ``error``, so
    they need the storm counter reset on entry. Mirrors the isolation
    pattern in tests/test_mcp_json_parse_defense.py.
    """
    from roam.mcp_server import _ROAM_RESULT_CACHE, _reset_error_storm

    _ROAM_RESULT_CACHE.clear()
    _reset_error_storm()
    yield
    _ROAM_RESULT_CACHE.clear()
    _reset_error_storm()


# ---------------------------------------------------------------------------
# Compound-existence guard (BAIL-if-absent shape)
# ---------------------------------------------------------------------------


def test_compound_exists_or_skip():
    """``for_refactor`` is registered on the MCP server module surface."""
    from roam import mcp_server as srv

    assert hasattr(srv, "for_refactor"), "roam.mcp_server.for_refactor missing; compound recipe was removed?"
    assert callable(srv.for_refactor), "for_refactor is not callable"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _git_init_committed(repo: Path) -> None:
    """Init a git repo and commit current files. No further history."""
    env = {
        **os.environ,
        "GIT_AUTHOR_NAME": "test",
        "GIT_AUTHOR_EMAIL": "t@t",
        "GIT_COMMITTER_NAME": "test",
        "GIT_COMMITTER_EMAIL": "t@t",
    }
    subprocess.run(["git", "init", "-q"], cwd=str(repo), capture_output=True, env=env, check=True)
    subprocess.run(
        ["git", "config", "user.email", "t@t.com"],
        cwd=str(repo),
        capture_output=True,
        env=env,
    )
    subprocess.run(
        ["git", "config", "user.name", "test"],
        cwd=str(repo),
        capture_output=True,
        env=env,
    )
    subprocess.run(["git", "add", "."], cwd=str(repo), capture_output=True, env=env)
    subprocess.run(
        ["git", "commit", "-q", "-m", "init"],
        cwd=str(repo),
        capture_output=True,
        env=env,
        check=True,
    )


@pytest.fixture
def empty_corpus(tmp_path, monkeypatch):
    """A git repo with a single empty .py file.

    The indexer runs cleanly but produces zero function/class/method
    symbols. The compound's two symbol-anchored children resolve the
    target symbol to ``not_found`` (resolution=unresolved). The two
    corpus-wide children (``complexity``, ``clones``) legitimately
    return empty results."""
    repo = tmp_path / "empty-for-refactor-repo"
    repo.mkdir()
    (repo / ".gitignore").write_text(".roam/\n", encoding="utf-8")
    (repo / "empty.py").write_text("", encoding="utf-8")
    _git_init_committed(repo)
    monkeypatch.chdir(repo)
    out, rc = index_in_process(repo, "--force")
    assert rc == 0, f"roam index failed:\n{out}"
    return repo


@pytest.fixture
def clean_corpus(tmp_path, monkeypatch):
    """A git repo with a real function + caller for happy-path coverage."""
    repo = tmp_path / "clean-for-refactor-repo"
    repo.mkdir()
    (repo / ".gitignore").write_text(".roam/\n", encoding="utf-8")
    (repo / "auth.py").write_text(
        "def handle_login(user):\n    return user\n\ndef main():\n    return handle_login('alice')\n",
        encoding="utf-8",
    )
    _git_init_committed(repo)
    monkeypatch.chdir(repo)
    out, rc = index_in_process(repo, "--force")
    assert rc == 0, f"roam index failed:\n{out}"
    return repo


# ---------------------------------------------------------------------------
# SMOKE (always-on)
# ---------------------------------------------------------------------------


class TestForRefactorEmptyCorpusSmoke:
    """Pattern-2 baseline assertions on the compound envelope shape."""

    def test_empty_corpus_no_crash(self, empty_corpus):
        """``for_refactor`` must return a dict envelope, never raise."""
        r = for_refactor(symbol="zzMissingSymbol", root=".")
        assert isinstance(r, dict), f"expected dict, got {type(r).__name__}"

    def test_empty_corpus_envelope_has_verdict(self, empty_corpus):
        """``summary.verdict`` is a non-empty string (Pattern-2 always-emit)."""
        r = for_refactor(symbol="zzMissingSymbol", root=".")
        summary = r.get("summary") or {}
        verdict = summary.get("verdict") or ""
        assert isinstance(verdict, str) and verdict, f"summary.verdict must be non-empty string; got {verdict!r}"

    def test_empty_corpus_command_field_set(self, empty_corpus):
        """Compound envelope identifies itself as ``for-refactor``."""
        r = for_refactor(symbol="zzMissingSymbol", root=".")
        assert r.get("command") == "for-refactor", r.get("command")

    def test_empty_corpus_situation_target_meta(self, empty_corpus):
        """Compound carries the situation + target meta-fields."""
        r = for_refactor(symbol="zzMissingSymbol", root=".")
        s = r.get("summary") or {}
        assert s.get("situation") == "refactor", s
        assert s.get("target") == "zzMissingSymbol", s

    def test_empty_corpus_four_child_sections_present(self, empty_corpus):
        """All four subcommand sections appear in the envelope."""
        r = for_refactor(symbol="zzMissingSymbol", root=".")
        sections = (r.get("summary") or {}).get("sections") or []
        for expected in ("preflight", "impact", "complexity_report", "clones"):
            assert expected in sections, f"missing {expected!r} in {sections}"

    def test_empty_corpus_law6_verdict_standalone(self, empty_corpus):
        """LAW 6: verdict is single-line ASCII-compatible; readable alone."""
        r = for_refactor(symbol="zzMissingSymbol", root=".")
        verdict = r["summary"]["verdict"]
        assert "\n" not in verdict, f"verdict has embedded newline: {verdict!r}"
        # The verdict joins sub-verdicts; one of the children's verdicts
        # may include a unicode em-dash. LAW 6 only requires single-line +
        # standalone-readable; do not assert .isascii() here.

    def test_empty_corpus_state_explicit_or_absent(self, empty_corpus):
        """``summary.state`` is either absent or a non-empty string.

        Pattern-2 disclosure prefers an explicit state; the compound
        today emits no state key (a separate pin below xfail-pins
        that). Smoke only asserts the key, if present, is well-formed."""
        r = for_refactor(symbol="zzMissingSymbol", root=".")
        s = r.get("summary") or {}
        if "state" in s:
            assert isinstance(s["state"], str) and s["state"], s

    def test_empty_corpus_partial_success_key_present(self, empty_corpus):
        """``summary.partial_success`` is always emitted as a bool."""
        r = for_refactor(symbol="zzMissingSymbol", root=".")
        s = r.get("summary") or {}
        assert "partial_success" in s, list(s.keys())
        assert isinstance(s["partial_success"], bool), type(s["partial_success"])

    def test_empty_corpus_failed_subcommands_list_present(self, empty_corpus):
        """``summary.failed_subcommands`` is always emitted as a list."""
        r = for_refactor(symbol="zzMissingSymbol", root=".")
        s = r.get("summary") or {}
        assert isinstance(s.get("failed_subcommands"), list), s.get("failed_subcommands")


# ---------------------------------------------------------------------------
# USAGE_ERROR path (Pattern-1 regression baseline)
# ---------------------------------------------------------------------------


class TestForRefactorUsageError:
    """The empty-symbol USAGE_ERROR branch must remain structured.
    Pattern-1 silent-crash regression coverage."""

    def test_empty_symbol_pattern_1(self, empty_corpus):
        """Passing ``symbol=""`` returns a USAGE_ERROR envelope, never crashes."""
        r = for_refactor(symbol="", root=".")
        # Structured-error shape, not a compound envelope.
        assert r.get("isError") is True, r
        assert "USAGE_ERROR" in (r.get("error_code") or ""), r.get("error_code")
        # The error string names the parameter the caller missed.
        assert "symbol" in (r.get("error") or "").lower(), r.get("error")
        # The hint is imperative + actionable (LAW 2).
        hint = r.get("hint") or ""
        assert hint and isinstance(hint, str), hint


# ---------------------------------------------------------------------------
# Clean-corpus positive baseline (W978 negative control)
# ---------------------------------------------------------------------------


class TestForRefactorCleanCorpusBaseline:
    """Real symbol on a real index: every child resolves, compound emits a
    clean partial_success=False. Confirms the empty-corpus shape below is
    NOT a class-wide compound defect -- it is the unresolved-target axis."""

    def test_clean_corpus_emits_real_aggregate(self, clean_corpus):
        r = for_refactor(symbol="handle_login", root=".")
        assert r.get("command") == "for-refactor"
        s = r["summary"]
        # All four subcommands must run cleanly on a real symbol.
        for expected in ("preflight", "impact", "complexity_report", "clones"):
            assert expected in s["sections"], f"missing {expected!r}"
        # Compound partial_success must be False on the clean path.
        assert s["partial_success"] is False, s
        assert s["failed_subcommands"] == [], s

    def test_clean_corpus_symbol_children_resolve(self, clean_corpus):
        """The two symbol-anchored children (preflight, impact) must
        resolve to ``resolution: 'symbol'`` on a real symbol. Mirror of
        the unresolved empty-corpus shape: same axis, opposite value."""
        r = for_refactor(symbol="handle_login", root=".")
        for child_name in ("preflight", "impact"):
            child = r.get(child_name) or {}
            res = (child.get("summary") or {}).get("resolution") or child.get("resolution")
            assert res in (None, "symbol"), f"clean-corpus {child_name}: resolution={res!r} (expected 'symbol' or None)"


# ---------------------------------------------------------------------------
# W978 first-hypothesis sanity: each symbol-anchored child DOES disclose
# partial_success on the empty corpus. This proves the next test below is
# pinning the COMPOUND aggregator gap, not a missing child-level disclosure.
# ---------------------------------------------------------------------------


class TestForRefactorEmptyChildrenDiscloseDegradedResolution:
    """Sanity: on empty corpus + unresolved target, the symbol-anchored
    children (``preflight`` / ``impact``) DO emit
    ``summary.partial_success: true``. The corpus-wide children
    (``complexity_report`` / ``clones``) legitimately emit
    partial_success=False (no symbol-resolution involved).

    If this class ever fails, the bug has shifted -- the child detectors
    have regressed. The compound pin below ASSUMES this disclosure is
    in place; mutate the pin if these break."""

    def test_preflight_child_discloses_partial(self, empty_corpus):
        r = for_refactor(symbol="zzMissingSymbol", root=".")
        child = r.get("preflight") or {}
        psum = child.get("summary") or {}
        assert psum.get("partial_success") is True, psum

    def test_impact_child_discloses_partial(self, empty_corpus):
        r = for_refactor(symbol="zzMissingSymbol", root=".")
        child = r.get("impact") or {}
        psum = child.get("summary") or {}
        assert psum.get("partial_success") is True, psum

    def test_complexity_corpus_wide_legitimately_clean(self, empty_corpus):
        """``complexity`` is corpus-wide; on empty corpus it legitimately
        emits ``partial_success: false`` + ``state: 'no_complexity_data'``.
        This is NOT a bug; it is correct disclosure-by-state. Pinning here
        documents the asymmetry between symbol-anchored and corpus-wide
        children in this compound."""
        r = for_refactor(symbol="zzMissingSymbol", root=".")
        child = r.get("complexity_report") or {}
        psum = child.get("summary") or {}
        # The child SHOULD be partial_success=False (corpus-wide; no
        # symbol resolution to fail). State disclosure carries the
        # empty-corpus signal instead.
        assert psum.get("partial_success") is False, psum


# ---------------------------------------------------------------------------
# PATTERN-2 PIN (xfail-strict) -- the compound aggregator gap
# Same bug class as W805-F. Re-pinned here for sweep coverage; both
# pins resolve under the same one-line aggregator fix.
# ---------------------------------------------------------------------------


@pytest.mark.xfail(
    strict=True,
    reason=(
        "W805-KK REAL BUG (Pattern-2 silent SAFE / Variant-D silent "
        "success on degraded resolution): _compound_envelope at "
        "src/roam/mcp_server.py:4448-4470 computes partial_success ONLY "
        "from per-child top-level 'error' keys. Children that returned a "
        "structured envelope with no top-level error but "
        "summary.partial_success=True (resolution=unresolved / "
        "state=not_found) are NOT propagated upward. On empty corpus + "
        "unresolved target, preflight + impact each disclose "
        "partial_success=True while the compound emits "
        "partial_success=False -- canonical Variant-D bug, same class as "
        "W805-F (for_bug_fix). Fix: at mcp_server.py:4470, also flip "
        "partial_success to True whenever any sections[name]['summary']"
        "['partial_success'] is True. Bundled fix wave resolves W805-F "
        "+ W805-KK together. Separate fix wave per W978 + accumulate-only "
        "constraint."
    ),
)
def test_empty_corpus_partial_success_set(empty_corpus):
    """Pin (Pattern-2 axis): compound must disclose partial_success=True
    when symbol-anchored children disclose partial_success=True.

    Currently fails: compound silently reports False while preflight
    and impact each report True. An agent reading only the compound
    summary proceeds as if the refactor bundle resolved cleanly.
    """
    r = for_refactor(symbol="zzMissingSymbol", root=".")
    s = r["summary"]
    assert s["partial_success"] is True, (
        "compound.summary.partial_success=False while symbol-anchored "
        "children (preflight, impact) disclose partial_success=True"
    )


@pytest.mark.xfail(
    strict=True,
    reason=(
        "W805-KK aggregator-axis pin: when child envelopes disclose "
        "summary.partial_success=True with NO top-level error key, "
        "_compound_envelope at mcp_server.py:4448-4470 fails to propagate "
        "the nested signal into compound.summary.failed_subcommands. "
        "Same bug class as W805-F; bundled fix."
    ),
)
def test_empty_corpus_child_partial_success_propagates(empty_corpus):
    """Pin (aggregator-bug axis): failed_subcommands names the children
    that disclosed partial_success=True.

    Without this propagation, an agent that branches on
    ``failed_subcommands`` for action selection misses the
    degraded-resolution disclosure entirely.
    """
    r = for_refactor(symbol="zzMissingSymbol", root=".")
    s = r["summary"]
    failed = set(s.get("failed_subcommands") or [])
    # Both preflight and impact disclose partial_success on the
    # empty-corpus path; the compound must name at least both.
    assert {"preflight", "impact"}.issubset(failed), (
        f"compound.summary.failed_subcommands={failed} does not name the partial children (preflight, impact)"
    )


@pytest.mark.xfail(
    strict=True,
    reason=(
        "W805-KK verdict-prefix pin: when symbol-anchored children "
        "disclose unresolved resolution, the compound verdict MUST NOT "
        "read like four clean successes. The current verdict joins the "
        'per-child verdicts ("preflight: target not found | impact: '
        'Symbol not found | complexity_report: ... | clones: ...") -- '
        "technically truthful per child, but the absence of the "
        "PARTIAL prefix (which _compound_envelope:4498-4502 only adds "
        "when failed_subcommands is non-empty) reads as a successful "
        "refactor bundle. Fix bundled with the partial_success "
        "propagation fix above."
    ),
)
def test_no_silent_no_refactor_opportunities_on_empty(empty_corpus):
    """Pin (silent-SAFE axis): compound verdict must not read like a
    clean success when symbol-anchored children disclose unresolved
    resolution.

    Acceptance: the verdict starts with the ``PARTIAL (...)`` prefix
    that ``_compound_envelope`` adds at line 4498-4502 when
    ``failed_subcommands`` is non-empty (i.e., the cascade flows
    through once partial_success propagation lands).
    """
    r = for_refactor(symbol="zzMissingSymbol", root=".")
    verdict = r["summary"]["verdict"]
    assert verdict.startswith("PARTIAL"), f"compound verdict does not flag partial-success: {verdict!r}"
