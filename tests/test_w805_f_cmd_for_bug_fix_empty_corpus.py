"""W805-F — Empty-corpus Pattern-2 smoke for ``roam_for_bug_fix``.

Sixth-in-batch W805 sweep. Unlike A-E which probed leaf commands, this
module probes the *compound recipe* layer. ``for_bug_fix`` is an
MCP-only compound exposed via ``@_tool(name="roam_for_bug_fix")`` at
``src/roam/mcp_server.py:6376-6420`` — there is no ``cmd_for_bug_fix.py``
under ``src/roam/commands/``; the recipe lives entirely in the MCP
server and dispatches via ``_safe_run([_cr(<key>), …])``.

Recipe composition (mcp_server.py:6406-6414): four subcommands, in
order — ``diagnose`` → ``affected-tests`` → ``diff`` → ``context``.
Each runs in-process via Click and returns a JSON envelope. The
compound aggregator is ``_compound_envelope`` at mcp_server.py:4432.

W978 first-hypothesis probe (run BEFORE writing tests):

REAL BUG (Pattern-2 silent SAFE / Variant-D silent success on
degraded resolution) found in ``_compound_envelope`` at
``src/roam/mcp_server.py:4448-4470``. The aggregator computes::

    for name, data in sub_results:
        if not data or "error" in data:
            err_msg = data.get("error", "empty result") if data else "empty result"
            errors.append({"command": name, "error": err_msg})
        ...
    partial_success = bool(failed_subcommands)

This treats a child envelope as "succeeded" iff its top-level ``error``
key is absent. But children that resolved a target degraded (e.g.
``resolution: "unresolved"`` + ``summary.partial_success: true`` +
``summary.state: "not_found"``) return a structured envelope with NO
top-level ``error`` key — they self-disclose partial success in
``summary.partial_success`` instead. The aggregator never reads that
nested signal, so the compound emits ``summary.partial_success: false``
while 3/4 children disclose ``summary.partial_success: true``.

Concrete observation (probed on empty corpus, symbol ``zzMissing``):

    compound.summary.partial_success = False                  # WRONG
    compound.summary.failed_subcommands = []                  # WRONG
    diagnose.summary.partial_success = True (state=not_found)
    affected_tests.summary.partial_success = True (state=not_found)
    diff.summary.partial_success = False (legitimately empty)
    context.summary.partial_success = True (state=not_found)

Compare with the CLAUDE.md canonical statement (Pattern-2 §2):

    "Never emit verdict: 'completed' when the underlying check failed
     or didn't run. … subcommand failure must set partial_success: True
     and name the failed subcommands."

The current behavior IS the bug pattern: the compound returns a
verdict reading like four successful sub-runs while each child
discloses degraded resolution. An agent prompt-cached on
``compound.summary.partial_success`` (the bool key it's explicitly
told to branch on per SYNTHESIS Pattern-2) reads False and proceeds
as if the bug-fix bundle resolved cleanly.

PIN STRATEGY (W978 + accumulate-only constraint):

1. SMOKE (always-on): no crash + envelope shape + LAW 6 verdict +
   per-child sections present + W978 USAGE_ERROR path intact.
2. POSITIVE BASELINE: real symbol on clean corpus → compound
   ``partial_success: false`` and all children ``partial_success: false``.
3. PATTERN-2 PIN (xfail-strict): on empty corpus / unresolved symbol,
   the compound MUST disclose ``partial_success: true`` because 3/4
   children disclose ``partial_success: true``.

The fix-forward (separate wave) is one-line at mcp_server.py:4470 —
also flip ``partial_success`` to True whenever any child envelope's
``summary.partial_success`` is True. Compound bookkeeping must lift
the nested signal, not only the top-level ``error`` key. Per W978:
do NOT fix this wave; pin only.
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
# we cannot use ``pytest.importorskip("fastmcp", ...)`` like
# ``test_situation_compounds.py`` does — that incorrectly skips here
# even when the compound is callable. Probe the actual entry point
# instead and skip iff that itself fails.
try:
    from roam.mcp_server import for_bug_fix  # noqa: E402
except Exception as _exc:  # pragma: no cover - guarded environments only
    pytest.skip(
        f"roam.mcp_server import failed: {_exc!r}; MCP compound tests require the MCP server module to be importable.",
        allow_module_level=True,
    )


# ---------------------------------------------------------------------------
# Test hygiene: disable the large-response handle-off so envelope inspection
# reads the full compound dict directly (mirrors test_situation_compounds).
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _disable_handle_off(monkeypatch):
    monkeypatch.setenv("ROAM_MCP_HANDLE_KB", "0")
    yield


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
    symbols. The compound's four children all resolve the target
    symbol to ``not_found`` (resolution=unresolved). This is the
    canonical empty-corpus / unresolved-target shape the W805 sweep
    exercises.
    """
    repo = tmp_path / "empty-for-bug-fix-repo"
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
    repo = tmp_path / "clean-for-bug-fix-repo"
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


class TestForBugFixEmptyCorpusSmoke:
    """Pattern-2 baseline assertions on the compound envelope shape."""

    def test_empty_corpus_no_crash(self, empty_corpus):
        """``for_bug_fix`` must return a dict envelope, never raise."""
        r = for_bug_fix(symbol="zzMissingSymbol", root=".")
        assert isinstance(r, dict), f"expected dict, got {type(r).__name__}"

    def test_empty_corpus_envelope_has_verdict(self, empty_corpus):
        """``summary.verdict`` is a non-empty string (Pattern-2 always-emit)."""
        r = for_bug_fix(symbol="zzMissingSymbol", root=".")
        summary = r.get("summary") or {}
        verdict = summary.get("verdict") or ""
        assert isinstance(verdict, str) and verdict, f"summary.verdict must be non-empty string; got {verdict!r}"

    def test_empty_corpus_command_field_set(self, empty_corpus):
        """Compound envelope identifies itself as ``for-bug-fix``."""
        r = for_bug_fix(symbol="zzMissingSymbol", root=".")
        assert r.get("command") == "for-bug-fix", r.get("command")

    def test_empty_corpus_situation_target_meta(self, empty_corpus):
        """Compound carries the situation + target meta-fields."""
        r = for_bug_fix(symbol="zzMissingSymbol", root=".")
        s = r.get("summary") or {}
        assert s.get("situation") == "bug_fix", s
        assert s.get("target") == "zzMissingSymbol", s

    def test_empty_corpus_four_child_sections_present(self, empty_corpus):
        """All four subcommands are accounted for in the envelope —
        either as a successful ``sections`` entry or as a failed
        subcommand.

        Post W805-OCTET seal: on empty corpus the ``affected_tests``
        child returns ``isError: True`` (no top-level ``error`` key), so
        the widened ``_compound_envelope`` aggregator routes it to
        ``failed_subcommands`` rather than ``sections``. The four
        subcommands are still all present — split across the two
        buckets — so this test asserts the UNION covers all four."""
        r = for_bug_fix(symbol="zzMissingSymbol", root=".")
        summary = r.get("summary") or {}
        sections = summary.get("sections") or []
        failed = summary.get("failed_subcommands") or []
        accounted = set(sections) | set(failed)
        for expected in ("diagnose", "affected_tests", "diff", "context"):
            assert expected in accounted, f"missing {expected!r} in sections={sections} + failed={failed}"

    def test_empty_corpus_law6_verdict_standalone(self, empty_corpus):
        """LAW 6: verdict is single-line ASCII; readable without other fields."""
        r = for_bug_fix(symbol="zzMissingSymbol", root=".")
        verdict = r["summary"]["verdict"]
        assert "\n" not in verdict, f"verdict has embedded newline: {verdict!r}"
        assert verdict.isascii(), f"verdict not plain ASCII: {verdict!r}"

    def test_empty_corpus_partial_success_key_present(self, empty_corpus):
        """``summary.partial_success`` is always emitted as a bool."""
        r = for_bug_fix(symbol="zzMissingSymbol", root=".")
        s = r.get("summary") or {}
        assert "partial_success" in s, list(s.keys())
        assert isinstance(s["partial_success"], bool), type(s["partial_success"])

    def test_empty_corpus_failed_subcommands_list_present(self, empty_corpus):
        """``summary.failed_subcommands`` is always emitted as a list."""
        r = for_bug_fix(symbol="zzMissingSymbol", root=".")
        s = r.get("summary") or {}
        assert isinstance(s.get("failed_subcommands"), list), s.get("failed_subcommands")


# ---------------------------------------------------------------------------
# USAGE_ERROR path (Pattern-1 regression baseline)
# ---------------------------------------------------------------------------


class TestForBugFixUsageError:
    """The empty-symbol / unresolved-target USAGE_ERROR branch must remain
    structured. Pattern-1 silent-crash regression coverage."""

    def test_unresolved_target_pattern_1(self, empty_corpus):
        """Passing ``symbol=""`` returns a USAGE_ERROR envelope, never crashes."""
        r = for_bug_fix(symbol="", root=".")
        # Structured-error shape, not a compound envelope.
        assert r.get("isError") is True, r
        assert "USAGE_ERROR" in (r.get("error_code") or ""), r.get("error_code")
        # Full and storm-trimmed errors both name the parameter the caller missed.
        message = r.get("error") or r.get("first_error_message") or ""
        assert "symbol" in message.lower(), message
        # The hint channel remains actionable on full or trimmed envelopes.
        hint = r.get("hint") or r.get("suggested_action") or r.get("trimmed_hint") or ""
        assert hint and isinstance(hint, str), hint


# ---------------------------------------------------------------------------
# Clean-corpus positive baseline (W978 negative control)
# ---------------------------------------------------------------------------


class TestForBugFixCleanCorpusBaseline:
    """Real symbol on a real index: every child resolves, compound emits a
    clean partial_success=False. Confirms the empty-corpus shape below is
    NOT a class-wide compound defect — it is the unresolved-target axis."""

    def test_clean_corpus_emits_real_compound(self, clean_corpus):
        r = for_bug_fix(symbol="handle_login", root=".")
        assert r.get("command") == "for-bug-fix"
        s = r["summary"]
        # All four subcommands must run cleanly on a real symbol.
        for expected in ("diagnose", "affected_tests", "diff", "context"):
            assert expected in s["sections"], f"missing {expected!r}"
        # Compound partial_success must be False on the clean path.
        assert s["partial_success"] is False, s
        assert s["failed_subcommands"] == [], s

    def test_clean_corpus_children_all_resolved(self, clean_corpus):
        """Every child whose envelope exposes ``resolution`` must read
        ``"symbol"`` (i.e. fully resolved). Mirror of the unresolved
        empty-corpus shape: same axis, opposite value."""
        r = for_bug_fix(symbol="handle_login", root=".")
        for child_name in ("diagnose", "affected_tests", "context"):
            child = r.get(child_name) or {}
            res = (child.get("summary") or {}).get("resolution") or child.get("resolution")
            # ``diff`` doesn't carry resolution; the three symbol-anchored
            # children do. None of them should read "unresolved".
            assert res in (None, "symbol"), f"clean-corpus {child_name}: resolution={res!r} (expected 'symbol')"


# ---------------------------------------------------------------------------
# W978 first-hypothesis sanity: each child DOES disclose partial_success
# on the empty corpus. This proves the next test below is pinning the
# COMPOUND aggregator gap, not a missing child-level disclosure.
# ---------------------------------------------------------------------------


class TestForBugFixEmptyChildrenDiscloseDegradedResolution:
    """Sanity: on empty corpus + unresolved target, the symbol-anchored
    children (``diagnose`` / ``affected_tests`` / ``context``) DO emit
    ``summary.partial_success: true`` and ``resolution: 'unresolved'``.

    If this class ever fails, the bug has shifted — the child detectors
    have regressed. The compound pin below ASSUMES this disclosure is
    in place; mutate the pin if these break."""

    def test_diagnose_child_discloses_partial(self, empty_corpus):
        r = for_bug_fix(symbol="zzMissingSymbol", root=".")
        child = r.get("diagnose") or {}
        psum = child.get("summary") or {}
        assert psum.get("partial_success") is True, psum
        assert psum.get("resolution") == "unresolved", psum

    def test_affected_tests_child_discloses_partial(self, empty_corpus):
        """The ``affected_tests`` child discloses degraded resolution.

        Post W805-OCTET seal: unlike ``diagnose`` / ``context`` (which
        disclose ONLY ``summary.partial_success`` and stay in
        ``sections``), the ``affected_tests`` child returns a top-level
        ``isError: True`` envelope. The widened ``_compound_envelope``
        aggregator therefore routes it into ``failed_subcommands`` +
        ``_errors`` rather than merging the full child envelope to the
        top-level ``affected_tests`` key. Assert the child is correctly
        classified as failed AND the surfaced error message is the
        child's actionable verdict (not the opaque ``empty result``
        sentinel — the err_msg fallback chain lifts ``summary.verdict``)."""
        r = for_bug_fix(symbol="zzMissingSymbol", root=".")
        failed = (r.get("summary") or {}).get("failed_subcommands") or []
        assert "affected_tests" in failed, f"affected_tests (isError child) not classified as failed: {failed}"
        errors = r.get("_errors") or []
        at_err = next((e for e in errors if e.get("command") == "affected_tests"), None)
        assert at_err is not None, f"affected_tests absent from _errors: {errors!r}"
        msg = at_err.get("error") or ""
        assert msg and msg != "empty result", (
            f"affected_tests error message is the opaque sentinel — the "
            f"err_msg fallback should lift summary.verdict. Got: {msg!r}"
        )
        assert "zzMissingSymbol" in msg, (
            f"affected_tests error message does not name the unresolved symbol. Got: {msg!r}"
        )

    def test_context_child_discloses_partial(self, empty_corpus):
        r = for_bug_fix(symbol="zzMissingSymbol", root=".")
        child = r.get("context") or {}
        psum = child.get("summary") or {}
        assert psum.get("partial_success") is True, psum
        assert psum.get("resolution") == "unresolved", psum


# ---------------------------------------------------------------------------
# PATTERN-2 PIN (xfail-strict) — the compound aggregator gap
# ---------------------------------------------------------------------------


@pytest.mark.xfail(
    strict=True,
    reason=(
        "W805-F REAL BUG (Pattern-2 silent SAFE / Variant-D silent success "
        "on degraded resolution): _compound_envelope at "
        "src/roam/mcp_server.py:4448-4470 computes partial_success ONLY "
        "from per-child top-level 'error' keys. Children that returned a "
        "structured envelope with no top-level error but "
        "summary.partial_success=True (resolution=unresolved, "
        "state=not_found) are NOT propagated upward. On empty corpus + "
        "unresolved target, 3/4 children disclose partial_success=True "
        "while the compound emits partial_success=False — the canonical "
        "Variant-D bug. Fix: at mcp_server.py:4470, also flip "
        "partial_success to True whenever any sections[name]['summary']"
        "['partial_success'] is True. Separate fix wave per W978 + "
        "accumulate-only constraint."
    ),
)
def test_empty_corpus_compound_partial_success_propagation(empty_corpus):
    """Pin: compound must lift child-disclosed partial_success.

    The child detectors correctly disclose unresolved targets via
    ``summary.partial_success: true`` + ``resolution: 'unresolved'``.
    The compound aggregator must propagate that signal upward, OR an
    agent prompt-cached on ``compound.summary.partial_success`` reads
    False and proceeds with a bug-fix bundle whose three symbol-
    anchored children all failed to find the symbol.
    """
    r = for_bug_fix(symbol="zzMissingSymbol", root=".")
    s = r["summary"]
    # When ≥1 child discloses partial_success=True, the compound must
    # also disclose partial_success=True. Currently fails: compound
    # silently reports False while diagnose/affected_tests/context
    # each report True.
    child_partials = []
    for name in s.get("sections") or []:
        child = r.get(name) or {}
        psum = child.get("summary") or {}
        if psum.get("partial_success") is True:
            child_partials.append(name)
    assert s["partial_success"] is True, (
        f"compound.summary.partial_success=False while children disclose partial_success=True: {child_partials}"
    )
    # And the failed_subcommands list must name them.
    failed = set(s.get("failed_subcommands") or [])
    assert failed.issuperset(set(child_partials)), (
        f"compound.summary.failed_subcommands={failed} omits partial children {child_partials}"
    )


def test_empty_corpus_no_silent_fix_ready(empty_corpus):
    """Pin — SEALED (W805-OCTET seal wave): compound verdict must not read
    like a clean success when children disclose unresolved resolution.

    Acceptance: the verdict starts with the ``PARTIAL (...)`` prefix that
    ``_compound_envelope`` adds when ``failed_subcommands`` is non-empty.

    The W805-TTTTT widening means the ``affected_tests`` child (which
    returns ``isError: True`` on empty corpus) is now classified as a
    failed subcommand, so ``failed_subcommands`` is non-empty and the
    PARTIAL prefix fires. Plain assert (was xfail-strict pre-fix). NOTE:
    the broader bug — ``diagnose`` / ``context`` disclose ONLY
    ``summary.partial_success`` with no ``isError`` and are still NOT
    lifted — remains pinned xfail by
    ``test_empty_corpus_compound_partial_success_propagation``.
    """
    r = for_bug_fix(symbol="zzMissingSymbol", root=".")
    verdict = r["summary"]["verdict"]
    assert verdict.startswith("PARTIAL"), f"compound verdict does not flag partial-success: {verdict!r}"


@pytest.mark.xfail(
    strict=True,
    reason=(
        "W805-F state-disclosure pin: the compound envelope SHOULD "
        "carry an explicit summary.state field naming the empty-data "
        "shape (e.g. 'no_data' / 'unresolved_target'). Today the "
        "compound emits no state key at all — only its children do. "
        "Closed-enum state-disclosure is the Pattern-2 fix template "
        "(CLAUDE.md §Pattern-2). Bundled with the partial_success "
        "propagation fix; separate wave."
    ),
)
def test_empty_corpus_explicit_state(empty_corpus):
    """Pin: compound discloses no_data / unresolved_target state on the
    empty-corpus path. Today the key is absent."""
    r = for_bug_fix(symbol="zzMissingSymbol", root=".")
    state = (r["summary"] or {}).get("state")
    assert state is not None, "compound.summary.state missing on empty corpus"
    # Closed-enum disclosure: one of these tokens.
    assert state in {"no_data", "not_initialized", "unresolved_target"}, (
        f"compound.summary.state={state!r} not in closed-enum"
    )
