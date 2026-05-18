"""W805-VVV - Empty-corpus Pattern-2 smoke for ``roam_explore``.

Seventy-fifth-in-batch W805 sweep. EIGHTH peer of the compound septet
(W805-F ``for_bug_fix``, W805-KK ``for_refactor``, W805-LL
``for_security_review``, W805-GGG ``for_new_feature``, W805-KKK
``diagnose_issue``, W805-OOO ``prepare_change``, W805-QQQ
``review_change``). ``explore`` is the LAST untested ``_compound_envelope``
caller -- this pin completes the OCTET (8/8 compound aggregators
documented). It is an MCP-only compound exposed via
``@_tool(name="roam_explore")`` at ``src/roam/mcp_server.py:4547-4624``
-- there is no ``cmd_explore.py`` under ``src/roam/commands/``; the
recipe lives entirely in the MCP server and dispatches via plain
``_run_roam([<key>, ...])``.

Recipe composition is BRANCHING (distinct shape from the septet):

  - No-symbol branch (mcp_server.py:4603-4606):
      ``understand``                          (only)
  - Symbol branch (mcp_server.py:4608-4622):
      ``understand``                          (always)
      ``context <symbol> --task understand``   (gated on symbol truthiness)

The compound aggregator is ``_compound_envelope`` at
``src/roam/mcp_server.py:4432``, same shared substrate as the septet.

W978 first-hypothesis probe (run BEFORE writing tests):

OBSERVED behavior under in-process probe on empty corpus::

  NO-SYMBOL branch (just understand):
    compound.summary.partial_success    = False
    compound.summary.failed_subcommands = []
    compound.summary.sections           = ['understand']
    compound.summary.state              = None
    compound.summary.verdict            = "understand: healthy 1-lang project (100/100), 0 clusters, 1 hotspots"
    child understand.partial_success    = False       # NOT degraded
    child understand.state              = None
    child understand.resolution         = None
    -> understand reports a real "healthy 1-lang project" envelope. The
       empty-file repo has 1 language tracked and the indexer reports it
       cleanly; not Pattern-2 degraded. No bug on this branch.

  SYMBOL branch (understand + context, bogus symbol):
    compound.summary.partial_success    = False            # SILENT-SAFE BUG
    compound.summary.failed_subcommands = []               # SILENT-SAFE BUG
    compound.summary.sections           = ['understand', 'context']
    compound.summary.state              = None             # MISSING
    compound.summary.verdict            = "understand: healthy ... | context: Symbol 'nonexistent_sym' not found"
    child understand.partial_success    = False            # genuinely clean
    child context.partial_success       = True             # CHILD DISCLOSES
    child context.state                 = 'not_found'      # CHILD DISCLOSES
    child context.resolution            = 'unresolved'     # CHILD DISCLOSES
    child context has no top-level 'error' key             # AGGREGATOR MISS

This is the canonical W805-F/KK/LL/GGG/KKK/OOO/QQQ-class aggregator bug
exposed on the context child via THREE simultaneous channels (the
OOO-class multi-channel-on-one-child shape, scoped to the context
child only since understand runs cleanly here).

Bug class summary table (octet peer comparison):

  - W805-F   (``for_bug_fix``):           ``resolution=unresolved``
  - W805-KK  (``for_refactor``):          ``resolution=unresolved``
  - W805-LL  (``for_security_review``):   ``state='empty_corpus'``
  - W805-GGG (``for_new_feature``):       ``state='no_complexity_data'``
  - W805-KKK (``diagnose_issue``):        3-channel on ONE child
  - W805-OOO (``prepare_change``):        3-channel on TWO children
  - W805-QQQ (``review_change``):         NO-DIFF axis: verdict-channel only
  - W805-VVV (``explore``):               3-channel on ONE child (context),
                                          BRANCHING-recipe peer (no-symbol
                                          branch genuinely clean -- this
                                          is the bug-only-on-symbol-branch
                                          axis, distinct from sextet)

Agent-safety impact (MEDIUM, comparable to KKK + OOO): an agent prompt-
cached on ``compound.summary.partial_success`` / ``failed_subcommands``
reads ``False`` / ``[]`` and assumes BOTH ``understand`` and ``context``
ran cleanly. The compound aggregate verdict literally says "context:
Symbol 'nonexistent_sym' not found" inline but the machine-readable
flags assert SAFE. ``explore`` is the canonical "call FIRST when starting
work on a new codebase" tool per the docstring at mcp_server.py:4563.
A silent SAFE on a symbol that doesn't exist lets the agent proceed to
analyze a symbol that hasn't yet been indexed (or that genuinely doesn't
exist), having read "exploration ran cleanly".

Compare CLAUDE.md Pattern-2 canonical:

    "Never emit verdict: 'SAFE' / 'completed' / 'non-conformant' when
     the underlying check failed or didn't run. Make absent state
     explicit: ``state: 'not_initialized'``, not ``state: 'broken'``."

Today the context child IS explicit on every channel; the compound
aggregator is the gap.

PIN STRATEGY (W978 + accumulate-only constraint):

1. SMOKE (always-on): no crash + envelope shape + LAW 6 verdict +
   sections present on both branches.
2. NO-SYMBOL BRANCH BASELINE: empty corpus, no symbol -> understand
   child clean; compound aggregate clean. No bug here.
3. POSITIVE BASELINE: clean corpus + resolvable symbol -> understand
   + context children both clean; compound aggregate clean.
4. PATTERN-2 PINS (xfail-strict): empty corpus + bogus symbol ->
   context child discloses partial_success=True / state='not_found' /
   resolution='unresolved', yet the compound's failed_subcommands
   omits 'context'.
5. OCTET CROSS-SIBLING CHECK: re-run sextet+septet xfails stay green
   (no test-isolation drift).

The fix-forward (separate wave, bundled with the octet): at
mcp_server.py:4470, also flip ``partial_success`` to True AND add to
``failed_subcommands`` whenever any child envelope's
``summary.partial_success`` is True OR ``summary.state`` is in a
closed-enum degradation set (``empty_corpus`` / ``no_complexity_data``
/ ``not_found`` / ``not_initialized`` / ``no_data``) OR
``summary.resolution`` is in ``{'unresolved', 'fuzzy'}``. Per W978: do
NOT fix this wave; pin only.

Run isolation: ``python -m pytest tests/test_w805_vvv_explore_compound_empty_corpus.py -x -n 0``
Cross-sibling: ``python -m pytest tests/test_w805_f*.py tests/test_w805_kk*.py tests/test_w805_ll*.py tests/test_w805_ggg*.py tests/test_w805_kkk*.py tests/test_w805_ooo*.py tests/test_w805_qqq*.py -x -n 0``
"""

from __future__ import annotations

import asyncio
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
# ``test_situation_compounds.py`` does -- that incorrectly skips here
# even when the compound is callable. Probe the actual entry point
# instead and skip iff that itself fails.
try:
    from roam.mcp_server import explore  # noqa: E402
except Exception as _exc:  # pragma: no cover - guarded environments only
    pytest.skip(
        f"roam.mcp_server import failed: {_exc!r}; MCP compound tests require the MCP server module to be importable.",
        allow_module_level=True,
    )


# ---------------------------------------------------------------------------
# Test hygiene: disable the large-response handle-off so envelope inspection
# reads the full compound dict directly (mirrors octet siblings).
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _disable_handle_off(monkeypatch):
    monkeypatch.setenv("ROAM_MCP_HANDLE_KB", "0")
    yield


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _call(**kwargs) -> dict:
    """``explore`` is ``async``; run it synchronously for pytest."""
    return asyncio.run(explore(**kwargs))


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

    The indexer runs cleanly. ``understand`` reports a real
    ``healthy 1-lang project`` envelope (NOT degraded). ``context`` on
    an unresolvable symbol discloses ``partial_success=True``,
    ``state='not_found'``, and ``resolution='unresolved'``. This is the
    canonical empty-corpus shape the W805-VVV pin exercises -- the
    context child is the sole degraded sibling (branching-recipe peer)."""
    repo = tmp_path / "empty-explore-repo"
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
    """A git repo with a real function for happy-path coverage."""
    repo = tmp_path / "clean-explore-repo"
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
# Existence check (W978 + W907 - verify before pinning)
# ---------------------------------------------------------------------------


def test_explore_compound_exists_or_skip():
    """``explore`` is importable from ``roam.mcp_server``.

    Module-level import already gated this with ``pytest.skip``; this
    test reaffirms the precondition so a future refactor that renames
    or moves the entry point fails loudly here rather than silently
    skipping the entire module."""
    from roam import mcp_server  # noqa: F401

    assert callable(explore), type(explore)


# ---------------------------------------------------------------------------
# SMOKE (always-on) - NO-SYMBOL BRANCH
# ---------------------------------------------------------------------------


class TestExploreNoSymbolBranchSmoke:
    """Pattern-2 baseline assertions on the no-symbol branch.

    No-symbol branch: only ``understand`` runs. On the empty-file
    corpus, ``understand`` reports a real envelope and is NOT degraded
    -- there's no Pattern-2 bug to pin on this branch (the indexer
    truly tracked 1 language, and that's the real state)."""

    def test_no_symbol_no_crash(self, empty_corpus):
        """``explore`` (no symbol) must return a dict envelope, never raise."""
        r = _call(symbol="", root=".")
        assert isinstance(r, dict), f"expected dict, got {type(r).__name__}"

    def test_no_symbol_envelope_has_verdict(self, empty_corpus):
        """``summary.verdict`` is non-empty (Pattern-2 always-emit)."""
        r = _call(symbol="", root=".")
        v = (r.get("summary") or {}).get("verdict") or ""
        assert isinstance(v, str) and v, f"summary.verdict must be non-empty string; got {v!r}"

    def test_no_symbol_command_field_set(self, empty_corpus):
        """Compound envelope identifies itself as ``explore``."""
        r = _call(symbol="", root=".")
        assert r.get("command") == "explore", r.get("command")

    def test_no_symbol_law6_verdict_standalone(self, empty_corpus):
        """LAW 6: verdict is single-line; readable without other fields."""
        r = _call(symbol="", root=".")
        v = r["summary"]["verdict"]
        assert "\n" not in v, f"verdict has embedded newline: {v!r}"

    def test_no_symbol_sections_only_understand(self, empty_corpus):
        """No-symbol branch: only ``understand`` is in sections."""
        r = _call(symbol="", root=".")
        sections = (r.get("summary") or {}).get("sections") or []
        assert sections == ["understand"], sections

    def test_no_symbol_branch_understand_child_clean(self, empty_corpus):
        """No-symbol branch baseline: ``understand`` child is NOT
        Pattern-2 degraded on the empty-file corpus.

        The indexer tracks 1 language and reports a healthy envelope.
        This is the negative control for the symbol-branch pin below
        -- it confirms the bug is specifically on the symbol-resolution
        axis, not class-wide compound defect."""
        r = _call(symbol="", root=".")
        u = (r.get("understand") or {}).get("summary") or {}
        assert u.get("partial_success") is not True, u
        assert u.get("state") not in {"not_found", "empty_corpus", "no_data"}, u
        assert u.get("resolution") not in {"unresolved", "fuzzy"}, u


# ---------------------------------------------------------------------------
# SMOKE (always-on) - SYMBOL BRANCH
# ---------------------------------------------------------------------------


class TestExploreSymbolBranchSmoke:
    """Pattern-2 baseline assertions on the symbol branch."""

    def test_symbol_no_crash(self, empty_corpus):
        """``explore(symbol=...)`` must return a dict envelope, never raise."""
        r = _call(symbol="nonexistent_sym", root=".")
        assert isinstance(r, dict), f"expected dict, got {type(r).__name__}"

    def test_symbol_envelope_has_verdict(self, empty_corpus):
        """``summary.verdict`` is non-empty."""
        r = _call(symbol="nonexistent_sym", root=".")
        v = (r.get("summary") or {}).get("verdict") or ""
        assert isinstance(v, str) and v, v

    def test_symbol_command_field_set(self, empty_corpus):
        """Compound envelope identifies itself as ``explore``."""
        r = _call(symbol="nonexistent_sym", root=".")
        assert r.get("command") == "explore", r.get("command")

    def test_symbol_law6_verdict_standalone(self, empty_corpus):
        """LAW 6: verdict is single-line."""
        r = _call(symbol="nonexistent_sym", root=".")
        v = r["summary"]["verdict"]
        assert "\n" not in v, f"verdict has embedded newline: {v!r}"

    def test_symbol_partial_success_key_present(self, empty_corpus):
        """``summary.partial_success`` is always emitted as a bool."""
        r = _call(symbol="nonexistent_sym", root=".")
        s = r.get("summary") or {}
        assert "partial_success" in s, list(s.keys())
        assert isinstance(s["partial_success"], bool), type(s["partial_success"])

    def test_symbol_failed_subcommands_list_present(self, empty_corpus):
        """``summary.failed_subcommands`` is always emitted as a list."""
        r = _call(symbol="nonexistent_sym", root=".")
        s = r.get("summary") or {}
        assert isinstance(s.get("failed_subcommands"), list), s.get("failed_subcommands")

    def test_symbol_branch_sections_both_present(self, empty_corpus):
        """Symbol branch: both ``understand`` + ``context`` in sections."""
        r = _call(symbol="nonexistent_sym", root=".")
        sections = (r.get("summary") or {}).get("sections") or []
        for expected in ("understand", "context"):
            assert expected in sections, f"missing {expected!r} in {sections}"


# ---------------------------------------------------------------------------
# Clean-corpus positive baseline (W978 negative control)
# ---------------------------------------------------------------------------


class TestExploreCleanCorpusBaseline:
    """Real symbol on a real index: both children run cleanly + do NOT
    disclose partial_success / not_found / unresolved.
    Confirms the empty-corpus pin below is NOT a class-wide compound
    defect -- it is specifically the unresolved-symbol axis on the
    context child."""

    def test_clean_corpus_emits_real_compound(self, clean_corpus):
        r = _call(symbol="handle_login", root=".")
        assert r.get("command") == "explore"
        s = r["summary"]
        for expected in ("understand", "context"):
            assert expected in s["sections"], s["sections"]

    def test_clean_corpus_understand_child_clean(self, clean_corpus):
        """Clean corpus: ``understand`` is NOT degraded."""
        r = _call(symbol="handle_login", root=".")
        u = (r.get("understand") or {}).get("summary") or {}
        assert u.get("partial_success") is not True, u

    def test_clean_corpus_context_child_resolved(self, clean_corpus):
        """Clean corpus + resolvable symbol: context child does NOT
        report resolution='unresolved' / state='not_found'."""
        r = _call(symbol="handle_login", root=".")
        cs = (r.get("context") or {}).get("summary") or {}
        assert cs.get("resolution") != "unresolved", f"clean corpus reports context resolution='unresolved': {cs!r}"
        assert cs.get("state") != "not_found", f"clean corpus reports context state='not_found': {cs!r}"

    def test_clean_corpus_compound_clean(self, clean_corpus):
        """Clean corpus + resolvable symbol: aggregate compound is
        genuinely clean (partial_success=False, no failed children)."""
        r = _call(symbol="handle_login", root=".")
        s = r["summary"]
        assert s.get("partial_success") is False, s
        assert s.get("failed_subcommands") == [], s


# ---------------------------------------------------------------------------
# W978 first-hypothesis sanity: context child DOES disclose degraded
# execution on the partial_success + state + resolution channels.
# This proves the pin below targets the COMPOUND aggregator gap, not a
# missing child-level disclosure.
# ---------------------------------------------------------------------------


class TestExploreEmptyContextChildDisclosesDegradation:
    """Sanity: on empty corpus with an unresolvable symbol, the context
    child emits degraded-execution disclosure on THREE channels.

    If any of these ever fail, the bug has shifted -- the underlying
    context detector regressed (or a disclosure field has been renamed).
    The compound pins below ASSUME this disclosure is in place; mutate
    the pin if these break."""

    def test_context_child_discloses_partial_success(self, empty_corpus):
        r = _call(symbol="nonexistent_sym", root=".")
        cs = (r.get("context") or {}).get("summary") or {}
        assert cs.get("partial_success") is True, cs

    def test_context_child_discloses_not_found_state(self, empty_corpus):
        r = _call(symbol="nonexistent_sym", root=".")
        cs = (r.get("context") or {}).get("summary") or {}
        assert cs.get("state") == "not_found", cs

    def test_context_child_discloses_unresolved_resolution(self, empty_corpus):
        r = _call(symbol="nonexistent_sym", root=".")
        cs = (r.get("context") or {}).get("summary") or {}
        assert cs.get("resolution") == "unresolved", cs


# ---------------------------------------------------------------------------
# PATTERN-2 PINS (xfail-strict) -- the compound aggregator gap
# (W805-F/KK/LL/GGG/KKK/OOO/QQQ peer)
# ---------------------------------------------------------------------------


@pytest.mark.xfail(
    strict=True,
    reason=(
        "W805-VVV REAL BUG - agent-safety MEDIUM "
        "(Pattern-2 silent fallback / Variant-D silent success on "
        "degraded child resolution). Same root cause as "
        "W805-F/KK/LL/GGG/KKK/OOO/QQQ: _compound_envelope at "
        "src/roam/mcp_server.py:4448-4470 computes failed_subcommands "
        "ONLY from per-child top-level 'error' keys. The context child "
        "returns a structured envelope with NO top-level error but DO "
        "disclose summary.partial_success=True AND summary.state="
        "'not_found' AND summary.resolution='unresolved' -- self-"
        "disclosing degraded execution on three channels. The "
        "aggregator never reads any of the nested signals, so context "
        "is placed in 'sections' (the success bucket) rather than in "
        "failed_subcommands. Agent-safety MEDIUM: explore is the "
        "canonical 'call FIRST when starting work on a new codebase' "
        "tool per docstring at mcp_server.py:4563. An agent prompt-"
        "cached on compound.summary.partial_success reads False on an "
        "empty / not-yet-indexed workspace + bogus symbol and proceeds "
        "to analyze a symbol that doesn't exist in the index. "
        "BRANCHING-recipe peer: distinct from sextet in that the bug "
        "ONLY manifests on the symbol branch (no-symbol branch's "
        "understand child runs cleanly on the empty-file corpus). "
        "Fix: at mcp_server.py:4470, also add child to "
        "failed_subcommands whenever child.summary.partial_success is "
        "True OR child.summary.state is in a closed-enum degradation "
        "set OR child.summary.resolution is in {'unresolved', 'fuzzy'}. "
        "Bundled with W805-F/KK/LL/GGG/KKK/OOO/QQQ fix wave; separate "
        "from this pin per W978 + accumulate-only constraint."
    ),
)
def test_no_silent_explore_complete_on_empty_with_bogus_symbol(empty_corpus):
    """Pin: compound must lift context child's degraded-execution
    disclosure into failed_subcommands.

    Context child correctly discloses partial_success=True AND
    state='not_found' AND resolution='unresolved'. The compound
    aggregator must propagate at least one of those signals into
    ``failed_subcommands``, OR an agent prompt-cached on
    ``compound.summary.failed_subcommands`` reads ``[]`` and proceeds
    to analyze a symbol that does not exist in the index.
    """
    r = _call(symbol="nonexistent_sym", root=".")
    s = r["summary"]
    failed = set(s.get("failed_subcommands") or [])
    assert "context" in failed, (
        f"compound.summary.failed_subcommands={failed} omits 'context' "
        f"despite child disclosing partial_success=True AND "
        f"state='not_found' AND resolution='unresolved'. Agent-safety "
        f"MEDIUM: agent reads failed_subcommands and assumes "
        f"exploration ran cleanly while in fact the target symbol "
        f"didn't resolve."
    )


@pytest.mark.xfail(
    strict=True,
    reason=(
        "W805-VVV partial_success aggregator pin: the compound "
        "summary.partial_success MUST flip True whenever ANY child "
        "discloses degraded execution via partial_success / state / "
        "resolution channel. Today it stays False because the "
        "aggregator only reads top-level 'error' keys. Bundled with "
        "the failed_subcommands propagation fix; separate wave per "
        "W978."
    ),
)
def test_empty_corpus_partial_success_set(empty_corpus):
    """Pin: ``summary.partial_success`` is True on empty corpus when
    the underlying symbol does not resolve."""
    r = _call(symbol="nonexistent_sym", root=".")
    s = r["summary"]
    assert s.get("partial_success") is True, (
        f"compound.summary.partial_success={s.get('partial_success')} "
        f"on empty corpus despite context disclosing "
        f"partial_success=True / state='not_found' / "
        f"resolution='unresolved'"
    )


@pytest.mark.xfail(
    strict=True,
    reason=(
        "W805-VVV state-disclosure pin (Pattern-2 fix template): the "
        "compound envelope SHOULD carry an explicit summary.state "
        "field naming the not-found / unresolved shape (e.g. "
        "'not_found' / 'unresolved' / 'empty_corpus'). Today the "
        "compound emits no state key at all -- only the context child "
        "does. Closed-enum state-disclosure is the Pattern-2 canonical "
        "fix per CLAUDE.md Pattern-2. Bundled with the "
        "partial_success / failed_subcommands propagation fix; "
        "separate wave per W978."
    ),
)
def test_empty_corpus_state_explicit(empty_corpus):
    """Pin: compound discloses not_found / no_data / empty_corpus
    state on the empty-corpus + bogus-symbol path."""
    r = _call(symbol="nonexistent_sym", root=".")
    state = (r["summary"] or {}).get("state")
    assert state is not None, "compound.summary.state missing on empty corpus + bogus symbol"
    assert state in {
        "no_data",
        "not_initialized",
        "empty_corpus",
        "not_found",
        "unresolved",
    }, f"compound.summary.state={state!r} not in closed-enum"


@pytest.mark.xfail(
    strict=True,
    reason=(
        "W805-VVV child-partial-success propagation pin "
        "(W805-F/KK/LL/GGG/KKK/OOO/QQQ axis). When ANY child discloses "
        "degraded execution via summary.partial_success=True, the "
        "compound's failed_subcommands MUST include that child name. "
        "VVV's context child sets partial_success=True; the aggregator "
        "must propagate. Bundled fix wave."
    ),
)
def test_empty_corpus_child_partial_success_propagates(empty_corpus):
    """Pin: every child whose summary discloses degraded execution
    must be named in compound.summary.failed_subcommands."""
    _DEGRADED_STATES = {
        "empty_corpus",
        "no_complexity_data",
        "not_found",
        "not_initialized",
        "no_data",
    }
    _DEGRADED_RESOLUTIONS = {"unresolved", "fuzzy"}
    r = _call(symbol="nonexistent_sym", root=".")
    s = r["summary"]
    degraded_children = []
    for name in s.get("sections") or []:
        child = r.get(name) or {}
        psum = child.get("summary") or {}
        if psum.get("partial_success") is True:
            degraded_children.append(name)
        elif psum.get("state") in _DEGRADED_STATES:
            degraded_children.append(name)
        elif psum.get("resolution") in _DEGRADED_RESOLUTIONS:
            degraded_children.append(name)
    failed = set(s.get("failed_subcommands") or [])
    assert failed.issuperset(set(degraded_children)), (
        f"compound.summary.failed_subcommands={failed} omits "
        f"degraded children {degraded_children} (W805-F/KK/LL/GGG/KKK/"
        f"OOO/QQQ axis, three-channel disclosure on context child)"
    )


@pytest.mark.xfail(
    strict=True,
    reason=(
        "W805-VVV child-state propagation pin (W805-GGG state-only "
        "channel). Even if partial_success / resolution propagation "
        "ships, a defensive parallel pin on the state-only channel "
        "stays warranted: when ANY child's summary.state is in a "
        "closed-enum degradation set, the compound's "
        "failed_subcommands MUST include that child. VVV's context "
        "child sets state='not_found' which exercises this channel. "
        "Bundled fix wave."
    ),
)
def test_empty_corpus_child_state_propagates(empty_corpus):
    """Pin (W805-GGG state-only channel axis): every child whose
    summary.state names a degradation token must be in
    compound.summary.failed_subcommands."""
    _DEGRADED_STATES = {
        "empty_corpus",
        "no_complexity_data",
        "not_found",
        "not_initialized",
        "no_data",
    }
    r = _call(symbol="nonexistent_sym", root=".")
    s = r["summary"]
    state_degraded = []
    for name in s.get("sections") or []:
        child = r.get(name) or {}
        psum = child.get("summary") or {}
        if psum.get("state") in _DEGRADED_STATES:
            state_degraded.append(name)
    failed = set(s.get("failed_subcommands") or [])
    assert failed.issuperset(set(state_degraded)), (
        f"compound.summary.failed_subcommands={failed} omits "
        f"state-degraded children {state_degraded} (W805-GGG "
        f"state-only channel axis)"
    )


@pytest.mark.xfail(
    strict=True,
    reason=(
        "W805-VVV child-resolution propagation pin (W805-F/KK/KKK "
        "resolution-only channel axis). Even if partial_success / "
        "state propagation ships, a defensive parallel pin on the "
        "resolution-only channel stays warranted: when ANY child's "
        "summary.resolution is in {'unresolved', 'fuzzy'}, the "
        "compound's failed_subcommands MUST include that child. VVV's "
        "context child sets resolution='unresolved'. Bundled fix wave."
    ),
)
def test_empty_corpus_child_resolution_propagates(empty_corpus):
    """Pin (W805-F/KK/KKK resolution-only channel axis): every child
    whose summary.resolution names a degradation token must be in
    compound.summary.failed_subcommands."""
    _DEGRADED_RESOLUTIONS = {"unresolved", "fuzzy"}
    r = _call(symbol="nonexistent_sym", root=".")
    s = r["summary"]
    resolution_degraded = []
    for name in s.get("sections") or []:
        child = r.get(name) or {}
        psum = child.get("summary") or {}
        if psum.get("resolution") in _DEGRADED_RESOLUTIONS:
            resolution_degraded.append(name)
    failed = set(s.get("failed_subcommands") or [])
    assert failed.issuperset(set(resolution_degraded)), (
        f"compound.summary.failed_subcommands={failed} omits "
        f"resolution-degraded children {resolution_degraded} "
        f"(W805-F/KK/KKK resolution-only channel axis)"
    )


# ---------------------------------------------------------------------------
# OCTET cross-sibling sanity: no-symbol branch on empty corpus is NOT
# Pattern-2 degraded. This is the branching-recipe-peer axis: the bug
# is gated on the symbol branch only.
# ---------------------------------------------------------------------------


def test_no_symbol_branch_not_pattern_2_bug(empty_corpus):
    """Cross-sibling: no-symbol branch on empty corpus is genuinely
    clean (no Pattern-2 disclosure gap). The bug is gated on the
    symbol-resolution child only -- the BRANCHING-recipe peer axis.

    This documents the structural difference between explore and the
    seven prior octet members: prepare_change / review_change /
    diagnose_issue / for_* recipes all run their full child set
    unconditionally; explore alone has a no-symbol short-path. This
    test asserts the short-path stays clean."""
    r = _call(symbol="", root=".")
    s = r["summary"]
    assert s.get("partial_success") is False, s
    assert s.get("failed_subcommands") == [], s
    assert s.get("sections") == ["understand"], s


# ---------------------------------------------------------------------------
# Clean-corpus positive baseline - final aggregate sanity (W978 control)
# ---------------------------------------------------------------------------


def test_clean_corpus_emits_real_aggregate(clean_corpus):
    """End-to-end clean-corpus sanity: compound returns a real envelope
    with verdict + sections. On a clean corpus with a resolvable symbol,
    both children produce real output and the compound stays clean."""
    r = _call(symbol="handle_login", root=".")
    assert r.get("command") == "explore"
    s = r["summary"]
    assert isinstance(s.get("verdict"), str) and s["verdict"], s
    for expected in ("understand", "context"):
        assert expected in s["sections"], s
    assert s.get("partial_success") is False, s
    assert s.get("failed_subcommands") == [], s
