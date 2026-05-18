"""W805-KKK - Empty-corpus Pattern-2 smoke for ``roam_diagnose_issue``.

Sixty-third-in-batch W805 sweep. Fifth peer of the compound quartet
(W805-F ``for_bug_fix``, W805-KK ``for_refactor``, W805-LL
``for_security_review``, W805-GGG ``for_new_feature``).
``diagnose_issue`` is an MCP-only compound exposed via
``@_tool(name="roam_diagnose_issue")`` at
``src/roam/mcp_server.py:4751-4789`` -- there is no
``cmd_diagnose_issue.py`` under ``src/roam/commands/``; the recipe
lives entirely in the MCP server and dispatches via plain
``_run_roam([<key>, ...])``.

Recipe composition (mcp_server.py:4777-4789): two unconditional
subcommands::

  - ``diagnose <symbol> --depth <depth>``        (always)
  - ``effects <symbol>``                          (always)

The compound aggregator is ``_compound_envelope`` at
``src/roam/mcp_server.py:4432``, same shared substrate as the quartet.

W978 first-hypothesis probe (run BEFORE writing tests):

OBSERVED behavior under in-process probe on empty corpus, symbol that
does NOT resolve to any indexed name::

    compound.summary.partial_success    = False             # SILENT-SAFE BUG
    compound.summary.failed_subcommands = []                # SILENT-SAFE BUG
    compound.summary.sections           = ['diagnose', 'effects']
    compound.summary.state              = None              # MISSING
    compound.summary.verdict            = "diagnose: Symbol 'X' not found | effects: no effects classified"

    child diagnose.summary.partial_success = True           # CHILD DISCLOSES
    child diagnose.summary.state           = 'not_found'    # CHILD DISCLOSES
    child diagnose.summary.resolution      = 'unresolved'   # CHILD DISCLOSES
    child diagnose has no top-level 'error' key             # AGGREGATOR MISS

This is the canonical W805-F/KK/LL/GGG-class aggregator bug exposed
on THREE child-disclosure channels simultaneously:

  - W805-F  (``for_bug_fix``):           ``resolution=unresolved`` + partial_success
  - W805-KK (``for_refactor``):          ``resolution=unresolved`` + partial_success
  - W805-LL (``for_security_review``):   ``summary.state='empty_corpus'`` + partial_success
  - W805-GGG (``for_new_feature``):      ``summary.state='no_complexity_data'`` (state-only)
  - W805-KKK (``diagnose_issue``):       partial_success=True AND state='not_found'
                                          AND resolution='unresolved' ALL set on child

W805-KKK is the strongest peer yet: the child correctly discloses on
EVERY known channel and the compound STILL silently SAFE-flags.
Agent-safety impact: an agent prompt-cached on
``compound.summary.partial_success`` / ``failed_subcommands`` reads
``False`` / ``[]`` and assumes BOTH ``diagnose`` AND ``effects`` ran
cleanly. The compound's aggregate verdict literally says "Symbol 'X'
not found" inline but the machine-readable flags assert SAFE. For a
debug-bundle compound this lets an agent proceed with a root-cause
narrative on a symbol that doesn't exist, having read a "diagnose
ran" verdict that actually means "the symbol didn't resolve."

Compare CLAUDE.md Pattern-2 canonical:

    "Never emit verdict: 'SAFE' / 'completed' / 'non-conformant' when
     the underlying check failed or didn't run. Make absent state
     explicit: ``state: 'not_initialized'``, not ``state: 'broken'``."

Today the child IS explicit on all three channels; the compound
aggregator is the gap.

PIN STRATEGY (W978 + accumulate-only constraint):

1. SMOKE (always-on): no crash + envelope shape + LAW 6 verdict +
   ``diagnose`` + ``effects`` sections present.
2. POSITIVE BASELINE: clean corpus + resolvable symbol -> diagnose
   child does NOT disclose partial_success / not_found / unresolved.
3. PATTERN-2 PIN (xfail-strict): on empty corpus with unresolved
   symbol, diagnose child discloses partial_success=True AND
   state='not_found' AND resolution='unresolved', yet the compound's
   ``failed_subcommands`` list does NOT include ``diagnose``.

The fix-forward (separate wave, bundled with W805-F/KK/LL/GGG): at
mcp_server.py:4470, also flip ``partial_success`` to True AND add to
``failed_subcommands`` whenever any child envelope's
``summary.partial_success`` is True OR ``summary.state`` is in a
closed-enum degradation set (``empty_corpus`` / ``no_complexity_data``
/ ``not_found`` / ``not_initialized``) OR ``summary.resolution`` is
in ``{'unresolved', 'fuzzy'}``. Per W978: do NOT fix this wave; pin
only.

Run isolation: ``python -m pytest tests/test_w805_kkk_diagnose_issue_empty_corpus.py -x -n 0``
Cross-sibling: ``python -m pytest tests/test_w805_ggg*.py tests/test_w805_f*.py -x -n 0``
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
# ``test_situation_compounds.py`` does -- that incorrectly skips here
# even when the compound is callable. Probe the actual entry point
# instead and skip iff that itself fails.
try:
    from roam.mcp_server import diagnose_issue  # noqa: E402
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
    symbols. The compound's two unconditional children run; the
    ``diagnose`` child discloses ``partial_success=True`` AND
    ``state='not_found'`` AND ``resolution='unresolved'`` when called
    on an unresolvable symbol name. This is the canonical empty-corpus
    shape the W805 sweep exercises (strongest channel-disclosure peer
    yet)."""
    repo = tmp_path / "empty-diagnose-issue-repo"
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
    repo = tmp_path / "clean-diagnose-issue-repo"
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


def test_diagnose_issue_compound_exists_or_skip():
    """``diagnose_issue`` is importable from ``roam.mcp_server``.

    Module-level import already gated this with ``pytest.skip``; this
    test reaffirms the precondition so a future refactor that renames
    or moves the entry point fails loudly here rather than silently
    skipping the entire module."""
    from roam import mcp_server  # noqa: F401

    assert callable(diagnose_issue), type(diagnose_issue)


# ---------------------------------------------------------------------------
# SMOKE (always-on)
# ---------------------------------------------------------------------------


class TestDiagnoseIssueEmptyCorpusSmoke:
    """Pattern-2 baseline assertions on the compound envelope shape."""

    def test_empty_corpus_no_crash(self, empty_corpus):
        """``diagnose_issue`` must return a dict envelope, never raise."""
        r = diagnose_issue(symbol="nonexistent_sym", root=".")
        assert isinstance(r, dict), f"expected dict, got {type(r).__name__}"

    def test_empty_corpus_envelope_has_verdict(self, empty_corpus):
        """``summary.verdict`` is a non-empty string (Pattern-2 always-emit)."""
        r = diagnose_issue(symbol="nonexistent_sym", root=".")
        summary = r.get("summary") or {}
        verdict = summary.get("verdict") or ""
        assert isinstance(verdict, str) and verdict, f"summary.verdict must be non-empty string; got {verdict!r}"

    def test_empty_corpus_command_field_set(self, empty_corpus):
        """Compound envelope identifies itself as ``diagnose-issue``."""
        r = diagnose_issue(symbol="nonexistent_sym", root=".")
        assert r.get("command") == "diagnose-issue", r.get("command")

    def test_empty_corpus_target_meta(self, empty_corpus):
        """Compound carries the target meta-field naming the input symbol."""
        r = diagnose_issue(symbol="nonexistent_sym", root=".")
        s = r.get("summary") or {}
        assert s.get("target") == "nonexistent_sym", s

    def test_empty_corpus_law6_verdict_standalone(self, empty_corpus):
        """LAW 6: verdict is single-line; readable without other fields."""
        r = diagnose_issue(symbol="nonexistent_sym", root=".")
        verdict = r["summary"]["verdict"]
        assert "\n" not in verdict, f"verdict has embedded newline: {verdict!r}"

    def test_empty_corpus_partial_success_key_present(self, empty_corpus):
        """``summary.partial_success`` is always emitted as a bool."""
        r = diagnose_issue(symbol="nonexistent_sym", root=".")
        s = r.get("summary") or {}
        assert "partial_success" in s, list(s.keys())
        assert isinstance(s["partial_success"], bool), type(s["partial_success"])

    def test_empty_corpus_failed_subcommands_list_present(self, empty_corpus):
        """``summary.failed_subcommands`` is always emitted as a list."""
        r = diagnose_issue(symbol="nonexistent_sym", root=".")
        s = r.get("summary") or {}
        assert isinstance(s.get("failed_subcommands"), list), s.get("failed_subcommands")

    def test_empty_corpus_unconditional_sections_present(self, empty_corpus):
        """``diagnose`` + ``effects`` always present in sections.

        Both children are unconditional for this compound (no area /
        conditional gating like ``for_new_feature``)."""
        r = diagnose_issue(symbol="nonexistent_sym", root=".")
        sections = (r.get("summary") or {}).get("sections") or []
        for expected in ("diagnose", "effects"):
            assert expected in sections, f"missing {expected!r} in {sections}"


# ---------------------------------------------------------------------------
# Clean-corpus positive baseline (W978 negative control)
# ---------------------------------------------------------------------------


class TestDiagnoseIssueCleanCorpusBaseline:
    """Real symbol on a real index: the diagnose child runs cleanly +
    does NOT disclose partial_success / not_found / unresolved.
    Confirms the empty-corpus pin below is NOT a class-wide compound
    defect -- it is specifically the unresolved-symbol axis on the
    diagnose child."""

    def test_clean_corpus_emits_real_compound(self, clean_corpus):
        r = diagnose_issue(symbol="handle_login", root=".")
        assert r.get("command") == "diagnose-issue"
        s = r["summary"]
        assert "diagnose" in s["sections"], s["sections"]
        assert "effects" in s["sections"], s["sections"]

    def test_clean_corpus_diagnose_child_resolved(self, clean_corpus):
        """Clean corpus + resolvable symbol: diagnose child reports
        resolution='symbol' (not 'unresolved')."""
        r = diagnose_issue(symbol="handle_login", root=".")
        diag = r.get("diagnose") or {}
        ds = diag.get("summary") or {}
        assert ds.get("resolution") != "unresolved", f"clean corpus reports resolution='unresolved': {ds!r}"

    def test_clean_corpus_diagnose_child_no_not_found_state(self, clean_corpus):
        """Clean corpus: the diagnose child's summary.state is NOT
        'not_found' (mirror of the empty-corpus shape, opposite value)."""
        r = diagnose_issue(symbol="handle_login", root=".")
        diag = r.get("diagnose") or {}
        ds = diag.get("summary") or {}
        assert ds.get("state") != "not_found", f"clean corpus reports state='not_found': {ds!r}"


# ---------------------------------------------------------------------------
# W978 first-hypothesis sanity: the diagnose child DOES disclose
# degraded execution on ALL THREE channels (partial_success / state /
# resolution). This proves the pin below targets the COMPOUND aggregator
# gap, not a missing child-level disclosure.
# ---------------------------------------------------------------------------


class TestDiagnoseIssueEmptyChildDisclosesDegradation:
    """Sanity: on empty corpus with an unresolvable symbol, the
    ``diagnose`` child emits degraded-execution disclosure on every
    available channel.

    If any of these ever fail, the bug has shifted -- the diagnose
    detector has regressed (or a disclosure field has been renamed).
    The compound pin below ASSUMES this triple disclosure is in place;
    mutate the pin if these break."""

    def test_diagnose_child_discloses_partial_success(self, empty_corpus):
        r = diagnose_issue(symbol="nonexistent_sym", root=".")
        diag = r.get("diagnose") or {}
        ds = diag.get("summary") or {}
        assert ds.get("partial_success") is True, ds

    def test_diagnose_child_discloses_not_found_state(self, empty_corpus):
        r = diagnose_issue(symbol="nonexistent_sym", root=".")
        diag = r.get("diagnose") or {}
        ds = diag.get("summary") or {}
        assert ds.get("state") == "not_found", ds

    def test_diagnose_child_discloses_unresolved_resolution(self, empty_corpus):
        r = diagnose_issue(symbol="nonexistent_sym", root=".")
        diag = r.get("diagnose") or {}
        ds = diag.get("summary") or {}
        assert ds.get("resolution") == "unresolved", ds

    def test_diagnose_child_verdict_names_not_found(self, empty_corpus):
        """The child verdict literally says 'not found'."""
        r = diagnose_issue(symbol="nonexistent_sym", root=".")
        diag = r.get("diagnose") or {}
        ds = diag.get("summary") or {}
        verdict = (ds.get("verdict") or "").lower()
        assert "not found" in verdict, ds


# ---------------------------------------------------------------------------
# PATTERN-2 PINS (xfail-strict) -- the compound aggregator gap (W805-F/KK/LL/GGG peer)
# ---------------------------------------------------------------------------


@pytest.mark.xfail(
    strict=True,
    reason=(
        "W805-KKK REAL BUG - agent-safety HIGH "
        "(Pattern-2 silent fallback / Variant-D silent success on "
        "degraded child resolution). Same root cause as "
        "W805-F/KK/LL/GGG: _compound_envelope at "
        "src/roam/mcp_server.py:4448-4470 computes failed_subcommands "
        "ONLY from per-child top-level 'error' keys. The diagnose "
        "child returns a structured envelope with NO top-level error "
        "but DOES disclose summary.partial_success=True AND "
        "summary.state='not_found' AND summary.resolution='unresolved' "
        "-- self-disclosing degraded execution on THREE channels "
        "simultaneously (strongest peer yet). The aggregator never "
        "reads any of the nested signals, so diagnose is placed in "
        "'sections' (the success bucket) rather than in "
        "failed_subcommands. Agent-safety HIGH: an agent reading the "
        "compound verdict on an empty / not-yet-indexed workspace "
        "sees 'diagnose: Symbol X not found' inline AND diagnose in "
        "sections, and may proceed with a root-cause narrative on a "
        "symbol that doesn't exist. Fix: at mcp_server.py:4470, also "
        "add child to failed_subcommands whenever child.summary."
        "partial_success is True OR child.summary.state is in a "
        "closed-enum degradation set OR child.summary.resolution is "
        "in {'unresolved', 'fuzzy'}. Bundled with W805-F/KK/LL/GGG "
        "fix wave; separate from this pin per W978 + accumulate-only "
        "constraint."
    ),
)
def test_no_silent_no_diagnosis_on_empty(empty_corpus):
    """Pin: compound must lift diagnose child's degraded-execution
    disclosure into failed_subcommands.

    The diagnose child correctly discloses partial_success=True,
    state='not_found', AND resolution='unresolved'. The compound
    aggregator must propagate at least one of those signals into
    ``failed_subcommands``, OR an agent prompt-cached on
    ``compound.summary.failed_subcommands`` reads ``[]`` and proceeds
    with a root-cause narrative whose diagnose check actually ran on
    an unresolvable symbol.
    """
    r = diagnose_issue(symbol="nonexistent_sym", root=".")
    s = r["summary"]
    failed = set(s.get("failed_subcommands") or [])
    assert "diagnose" in failed, (
        f"compound.summary.failed_subcommands={failed} omits "
        f"'diagnose' despite child disclosing partial_success=True, "
        f"state='not_found', AND resolution='unresolved'. "
        f"Agent-safety HIGH: agent reads failed_subcommands and "
        f"assumes diagnose ran cleanly while in fact the symbol "
        f"didn't resolve."
    )


@pytest.mark.xfail(
    strict=True,
    reason=(
        "W805-KKK partial_success aggregator pin: the compound "
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
    r = diagnose_issue(symbol="nonexistent_sym", root=".")
    s = r["summary"]
    assert s.get("partial_success") is True, (
        f"compound.summary.partial_success={s.get('partial_success')} "
        f"on empty corpus despite diagnose disclosing "
        f"partial_success=True / state='not_found' / "
        f"resolution='unresolved'"
    )


@pytest.mark.xfail(
    strict=True,
    reason=(
        "W805-KKK state-disclosure pin (Pattern-2 fix template): the "
        "compound envelope SHOULD carry an explicit summary.state "
        "field naming the empty-data / not-found shape (e.g. "
        "'no_data' / 'not_found' / 'empty_corpus'). Today the "
        "compound emits no state key at all -- only its children "
        "do. Closed-enum state-disclosure is the Pattern-2 canonical "
        "fix per CLAUDE.md Pattern-2. Bundled with the "
        "partial_success / failed_subcommands propagation fix; "
        "separate wave per W978."
    ),
)
def test_empty_corpus_state_explicit(empty_corpus):
    """Pin: compound discloses not_found / no_data / empty_corpus
    state on the empty-corpus path. Today the key is absent."""
    r = diagnose_issue(symbol="nonexistent_sym", root=".")
    state = (r["summary"] or {}).get("state")
    assert state is not None, "compound.summary.state missing on empty corpus"
    # Closed-enum disclosure: one of these tokens.
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
        "W805-KKK child-partial-success propagation pin "
        "(W805-F/KK/LL/GGG quartet axis). When ANY child discloses "
        "degraded execution via summary.partial_success=True OR "
        "summary.state in a closed-enum degradation set OR "
        "summary.resolution in {'unresolved', 'fuzzy'}, the "
        "compound's failed_subcommands MUST include that child name. "
        "This is the broadest one-line fix at mcp_server.py:4470. "
        "Bundled fix wave."
    ),
)
def test_empty_corpus_child_partial_success_propagates(empty_corpus):
    """Pin (W805-F/KK/LL/GGG axis, broadened): every child whose
    summary discloses degraded execution must be named in
    compound.summary.failed_subcommands."""
    _DEGRADED_STATES = {
        "empty_corpus",
        "no_complexity_data",
        "not_found",
        "not_initialized",
        "no_data",
    }
    _DEGRADED_RESOLUTIONS = {"unresolved", "fuzzy"}
    r = diagnose_issue(symbol="nonexistent_sym", root=".")
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
        f"degraded children {degraded_children} (W805-F/KK/LL/GGG "
        f"axis, three-channel broadening: partial_success / state / "
        f"resolution)"
    )


@pytest.mark.xfail(
    strict=True,
    reason=(
        "W805-KKK child-state propagation pin (W805-GGG state-only "
        "channel). Even if partial_success / resolution propagation "
        "ships, a defensive parallel pin on the state-only channel "
        "stays warranted: when ANY child's summary.state is in a "
        "closed-enum degradation set, the compound's "
        "failed_subcommands MUST include that child. Bundled fix wave."
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
    r = diagnose_issue(symbol="nonexistent_sym", root=".")
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


# ---------------------------------------------------------------------------
# Clean-corpus positive baseline - final aggregate sanity (W978 control)
# ---------------------------------------------------------------------------


def test_clean_corpus_emits_real_aggregate(clean_corpus):
    """End-to-end clean-corpus sanity: compound returns a real envelope
    with verdict + sections + target. On a clean corpus with a
    resolvable symbol, both children produce real output and the
    compound stays clean."""
    r = diagnose_issue(symbol="handle_login", root=".")
    assert r.get("command") == "diagnose-issue"
    s = r["summary"]
    # Verdict aggregates child verdicts; non-empty.
    assert isinstance(s.get("verdict"), str) and s["verdict"], s
    # Target carried.
    assert s.get("target") == "handle_login"
    # Both unconditional children present.
    assert "diagnose" in s["sections"], s
    assert "effects" in s["sections"], s
    # On clean corpus + resolvable symbol, compound is genuinely clean.
    assert s.get("partial_success") is False, s
    assert s.get("failed_subcommands") == [], s
