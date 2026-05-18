"""W805-OOO - Empty-corpus Pattern-2 smoke for ``roam_prepare_change``.

Sixty-seventh-in-batch W805 sweep. Sixth peer of the compound quintet
(W805-F ``for_bug_fix``, W805-KK ``for_refactor``, W805-LL
``for_security_review``, W805-GGG ``for_new_feature``, W805-KKK
``diagnose_issue``). ``prepare_change`` is an MCP-only compound
exposed via ``@_tool(name="roam_prepare_change")`` at
``src/roam/mcp_server.py:4627-4696`` -- there is no
``cmd_prepare_change.py`` under ``src/roam/commands/``; the recipe
lives entirely in the MCP server and dispatches via plain
``_run_roam([<key>, ...])``.

Recipe composition (mcp_server.py:4666-4695): three subcommands:

  - ``preflight <symbol> [--staged]``                  (always)
  - ``context <symbol> --task refactor [...]``          (gated on symbol)
  - ``effects <symbol>``                                (gated on symbol)

The compound aggregator is ``_compound_envelope`` at
``src/roam/mcp_server.py:4432``, same shared substrate as the quintet.

W978 first-hypothesis probe (run BEFORE writing tests):

OBSERVED behavior under in-process probe on empty corpus, symbol that
does NOT resolve to any indexed name::

    compound.summary.partial_success    = False             # SILENT-SAFE BUG
    compound.summary.failed_subcommands = []                # SILENT-SAFE BUG
    compound.summary.sections           = ['preflight', 'context', 'effects']
    compound.summary.state              = None              # MISSING
    compound.summary.verdict            = "preflight: target not found ... | context: Symbol 'X' not found | effects: no effects classified"

    child preflight.summary.partial_success = True          # CHILD DISCLOSES
    child preflight.summary.resolution      = 'unresolved'  # CHILD DISCLOSES
    child preflight has no top-level 'error' key            # AGGREGATOR MISS

    child context.summary.partial_success   = True          # CHILD DISCLOSES
    child context.summary.state             = 'not_found'   # CHILD DISCLOSES
    child context.summary.resolution        = 'unresolved'  # CHILD DISCLOSES
    child context has no top-level 'error' key              # AGGREGATOR MISS

This is the canonical W805-F/KK/LL/GGG/KKK-class aggregator bug exposed
on TWO simultaneously degraded children (preflight + context). The
``effects`` child is genuinely "no effects classified" on the empty
corpus -- not degraded. The OOO peer is the second multi-channel
disclosure case (alongside KKK's three-channel finding) but here the
degradation surfaces on TWO independent children, each with their own
disclosure mix:

  - W805-F   (``for_bug_fix``):             ``resolution=unresolved``
  - W805-KK  (``for_refactor``):            ``resolution=unresolved``
  - W805-LL  (``for_security_review``):    ``state='empty_corpus'``
  - W805-GGG (``for_new_feature``):        ``state='no_complexity_data'``
  - W805-KKK (``diagnose_issue``):         3-channel on ONE child (diagnose)
  - W805-OOO (``prepare_change``):         3-channel on preflight + 3-channel
                                            on context (TWO degraded children
                                            simultaneously, strongest peer yet)

Agent-safety impact: an agent prompt-cached on
``compound.summary.partial_success`` / ``failed_subcommands`` reads
``False`` / ``[]`` and assumes ALL THREE of ``preflight``, ``context``,
and ``effects`` ran cleanly. The compound's aggregate verdict literally
says "preflight: target not found" and "context: Symbol 'X' not found"
inline but the machine-readable flags assert SAFE. For a PRE-CHANGE
safety bundle compound this is the worst class: ``prepare_change`` is
EXACTLY the gate an agent calls before modifying code. A silent SAFE
verdict here lets the agent proceed to edit a symbol that does not exist
in the index, with no blast-radius, no context files, no real safety
data, having read "prepare-change ran cleanly".

Compare CLAUDE.md Pattern-2 canonical:

    "Never emit verdict: 'SAFE' / 'completed' / 'non-conformant' when
     the underlying check failed or didn't run. Make absent state
     explicit: ``state: 'not_initialized'``, not ``state: 'broken'``."

Today both child compounds ARE explicit on every channel; the compound
aggregator is the gap.

PIN STRATEGY (W978 + accumulate-only constraint):

1. SMOKE (always-on): no crash + envelope shape + LAW 6 verdict +
   ``preflight`` + ``context`` + ``effects`` sections present.
2. POSITIVE BASELINE: clean corpus + resolvable symbol -> preflight +
   context children do NOT disclose partial_success / not_found /
   unresolved.
3. PATTERN-2 PIN (xfail-strict): on empty corpus with unresolved
   symbol, preflight + context children disclose partial_success=True
   AND (state='not_found' on context) AND resolution='unresolved', yet
   the compound's ``failed_subcommands`` list omits both.

The fix-forward (separate wave, bundled with W805-F/KK/LL/GGG/KKK): at
mcp_server.py:4470, also flip ``partial_success`` to True AND add to
``failed_subcommands`` whenever any child envelope's
``summary.partial_success`` is True OR ``summary.state`` is in a
closed-enum degradation set (``empty_corpus`` / ``no_complexity_data``
/ ``not_found`` / ``not_initialized``) OR ``summary.resolution`` is
in ``{'unresolved', 'fuzzy'}``. Per W978: do NOT fix this wave; pin
only.

Run isolation: ``python -m pytest tests/test_w805_ooo_prepare_change_empty_corpus.py -x -n 0``
Cross-sibling: ``python -m pytest tests/test_w805_kkk*.py tests/test_w805_ggg*.py tests/test_w805_f*.py -x -n 0``
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
    from roam.mcp_server import prepare_change  # noqa: E402
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
    symbols. The compound's three children all run; ``preflight`` and
    ``context`` each disclose ``partial_success=True`` and
    ``resolution='unresolved'`` (context also sets ``state='not_found'``)
    when called on an unresolvable symbol name. This is the canonical
    empty-corpus shape the W805 sweep exercises (TWO degraded children
    in one compound, strongest multi-channel peer yet)."""
    repo = tmp_path / "empty-prepare-change-repo"
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
    repo = tmp_path / "clean-prepare-change-repo"
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


def test_prepare_change_compound_exists_or_skip():
    """``prepare_change`` is importable from ``roam.mcp_server``.

    Module-level import already gated this with ``pytest.skip``; this
    test reaffirms the precondition so a future refactor that renames
    or moves the entry point fails loudly here rather than silently
    skipping the entire module."""
    from roam import mcp_server  # noqa: F401

    assert callable(prepare_change), type(prepare_change)


# ---------------------------------------------------------------------------
# SMOKE (always-on)
# ---------------------------------------------------------------------------


class TestPrepareChangeEmptyCorpusSmoke:
    """Pattern-2 baseline assertions on the compound envelope shape."""

    def test_empty_corpus_no_crash(self, empty_corpus):
        """``prepare_change`` must return a dict envelope, never raise."""
        r = prepare_change(symbol="nonexistent_sym", root=".")
        assert isinstance(r, dict), f"expected dict, got {type(r).__name__}"

    def test_empty_corpus_envelope_has_verdict(self, empty_corpus):
        """``summary.verdict`` is a non-empty string (Pattern-2 always-emit)."""
        r = prepare_change(symbol="nonexistent_sym", root=".")
        summary = r.get("summary") or {}
        verdict = summary.get("verdict") or ""
        assert isinstance(verdict, str) and verdict, f"summary.verdict must be non-empty string; got {verdict!r}"

    def test_empty_corpus_command_field_set(self, empty_corpus):
        """Compound envelope identifies itself as ``prepare-change``."""
        r = prepare_change(symbol="nonexistent_sym", root=".")
        assert r.get("command") == "prepare-change", r.get("command")

    def test_empty_corpus_law6_verdict_standalone(self, empty_corpus):
        """LAW 6: verdict is single-line; readable without other fields."""
        r = prepare_change(symbol="nonexistent_sym", root=".")
        verdict = r["summary"]["verdict"]
        assert "\n" not in verdict, f"verdict has embedded newline: {verdict!r}"

    def test_empty_corpus_partial_success_key_present(self, empty_corpus):
        """``summary.partial_success`` is always emitted as a bool."""
        r = prepare_change(symbol="nonexistent_sym", root=".")
        s = r.get("summary") or {}
        assert "partial_success" in s, list(s.keys())
        assert isinstance(s["partial_success"], bool), type(s["partial_success"])

    def test_empty_corpus_failed_subcommands_list_present(self, empty_corpus):
        """``summary.failed_subcommands`` is always emitted as a list."""
        r = prepare_change(symbol="nonexistent_sym", root=".")
        s = r.get("summary") or {}
        assert isinstance(s.get("failed_subcommands"), list), s.get("failed_subcommands")

    def test_empty_corpus_unconditional_sections_present(self, empty_corpus):
        """``preflight`` + ``context`` + ``effects`` all present in sections.

        When ``symbol`` is non-empty, all three children run (preflight
        always, context + effects gated on truthiness of symbol)."""
        r = prepare_change(symbol="nonexistent_sym", root=".")
        sections = (r.get("summary") or {}).get("sections") or []
        for expected in ("preflight", "context", "effects"):
            assert expected in sections, f"missing {expected!r} in {sections}"


# ---------------------------------------------------------------------------
# Clean-corpus positive baseline (W978 negative control)
# ---------------------------------------------------------------------------


class TestPrepareChangeCleanCorpusBaseline:
    """Real symbol on a real index: preflight + context children run
    cleanly + do NOT disclose partial_success / not_found / unresolved.
    Confirms the empty-corpus pin below is NOT a class-wide compound
    defect -- it is specifically the unresolved-symbol axis on the
    preflight + context children."""

    def test_clean_corpus_emits_real_compound(self, clean_corpus):
        r = prepare_change(symbol="handle_login", root=".")
        assert r.get("command") == "prepare-change"
        s = r["summary"]
        for expected in ("preflight", "context", "effects"):
            assert expected in s["sections"], s["sections"]

    def test_clean_corpus_preflight_child_resolved(self, clean_corpus):
        """Clean corpus + resolvable symbol: preflight child does NOT
        report resolution='unresolved'."""
        r = prepare_change(symbol="handle_login", root=".")
        pf = r.get("preflight") or {}
        ps = pf.get("summary") or {}
        assert ps.get("resolution") != "unresolved", f"clean corpus reports preflight resolution='unresolved': {ps!r}"

    def test_clean_corpus_context_child_no_not_found_state(self, clean_corpus):
        """Clean corpus: the context child's summary.state is NOT
        'not_found' (mirror of the empty-corpus shape, opposite value)."""
        r = prepare_change(symbol="handle_login", root=".")
        cx = r.get("context") or {}
        cs = cx.get("summary") or {}
        assert cs.get("state") != "not_found", f"clean corpus reports context state='not_found': {cs!r}"


# ---------------------------------------------------------------------------
# W978 first-hypothesis sanity: preflight + context children DO disclose
# degraded execution on the partial_success + state + resolution channels.
# This proves the pin below targets the COMPOUND aggregator gap, not a
# missing child-level disclosure.
# ---------------------------------------------------------------------------


class TestPrepareChangeEmptyChildrenDiscloseDegradation:
    """Sanity: on empty corpus with an unresolvable symbol, preflight +
    context children each emit degraded-execution disclosure.

    If any of these ever fail, the bug has shifted -- the underlying
    detector regressed (or a disclosure field has been renamed). The
    compound pins below ASSUME this disclosure is in place; mutate the
    pin if these break."""

    def test_preflight_child_discloses_partial_success(self, empty_corpus):
        r = prepare_change(symbol="nonexistent_sym", root=".")
        pf = r.get("preflight") or {}
        ps = pf.get("summary") or {}
        assert ps.get("partial_success") is True, ps

    def test_preflight_child_discloses_unresolved_resolution(self, empty_corpus):
        r = prepare_change(symbol="nonexistent_sym", root=".")
        pf = r.get("preflight") or {}
        ps = pf.get("summary") or {}
        assert ps.get("resolution") == "unresolved", ps

    def test_context_child_discloses_partial_success(self, empty_corpus):
        r = prepare_change(symbol="nonexistent_sym", root=".")
        cx = r.get("context") or {}
        cs = cx.get("summary") or {}
        assert cs.get("partial_success") is True, cs

    def test_context_child_discloses_not_found_state(self, empty_corpus):
        r = prepare_change(symbol="nonexistent_sym", root=".")
        cx = r.get("context") or {}
        cs = cx.get("summary") or {}
        assert cs.get("state") == "not_found", cs

    def test_context_child_discloses_unresolved_resolution(self, empty_corpus):
        r = prepare_change(symbol="nonexistent_sym", root=".")
        cx = r.get("context") or {}
        cs = cx.get("summary") or {}
        assert cs.get("resolution") == "unresolved", cs


# ---------------------------------------------------------------------------
# PATTERN-2 PINS (xfail-strict) -- the compound aggregator gap
# (W805-F/KK/LL/GGG/KKK peer)
# ---------------------------------------------------------------------------


@pytest.mark.xfail(
    strict=True,
    reason=(
        "W805-OOO REAL BUG - agent-safety HIGH "
        "(Pattern-2 silent fallback / Variant-D silent success on "
        "degraded child resolution). Same root cause as "
        "W805-F/KK/LL/GGG/KKK: _compound_envelope at "
        "src/roam/mcp_server.py:4448-4470 computes failed_subcommands "
        "ONLY from per-child top-level 'error' keys. The preflight + "
        "context children return structured envelopes with NO top-level "
        "error but DO disclose summary.partial_success=True (both) AND "
        "summary.resolution='unresolved' (both) AND summary.state="
        "'not_found' (context) -- self-disclosing degraded execution on "
        "TWO independent children, each multi-channel (strongest peer "
        "yet on the breadth axis, alongside KKK's depth axis). The "
        "aggregator never reads any of the nested signals, so preflight "
        "+ context are placed in 'sections' (the success bucket) rather "
        "than in failed_subcommands. Agent-safety HIGH-PLUS: this is "
        "the PRE-CHANGE safety gate (the canonical 'call before any "
        "non-trivial edit' compound per CLAUDE.md). An agent prompt-"
        "cached on compound.summary.partial_success reads False on an "
        "empty / not-yet-indexed workspace and proceeds to edit a "
        "symbol that does not exist in the index. Fix: at "
        "mcp_server.py:4470, also add child to failed_subcommands "
        "whenever child.summary.partial_success is True OR child."
        "summary.state is in a closed-enum degradation set OR child."
        "summary.resolution is in {'unresolved', 'fuzzy'}. Bundled "
        "with W805-F/KK/LL/GGG/KKK fix wave; separate from this pin "
        "per W978 + accumulate-only constraint."
    ),
)
def test_no_silent_prepare_complete_on_empty(empty_corpus):
    """Pin: compound must lift preflight + context children's degraded-
    execution disclosure into failed_subcommands.

    Preflight + context children correctly disclose partial_success=
    True AND resolution='unresolved'; context also discloses state=
    'not_found'. The compound aggregator must propagate at least one of
    those signals into ``failed_subcommands``, OR an agent prompt-
    cached on ``compound.summary.failed_subcommands`` reads ``[]`` and
    proceeds to edit a symbol that does not exist in the index.
    """
    r = prepare_change(symbol="nonexistent_sym", root=".")
    s = r["summary"]
    failed = set(s.get("failed_subcommands") or [])
    # Both preflight and context disclose degradation; at MINIMUM
    # preflight (the safety-gate child) must be in failed_subcommands.
    assert "preflight" in failed, (
        f"compound.summary.failed_subcommands={failed} omits "
        f"'preflight' despite child disclosing partial_success=True "
        f"AND resolution='unresolved'. Agent-safety HIGH-PLUS: agent "
        f"reads failed_subcommands and assumes pre-change safety gate "
        f"ran cleanly while in fact the target symbol didn't resolve."
    )


@pytest.mark.xfail(
    strict=True,
    reason=(
        "W805-OOO partial_success aggregator pin: the compound "
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
    r = prepare_change(symbol="nonexistent_sym", root=".")
    s = r["summary"]
    assert s.get("partial_success") is True, (
        f"compound.summary.partial_success={s.get('partial_success')} "
        f"on empty corpus despite preflight + context disclosing "
        f"partial_success=True / state='not_found' / "
        f"resolution='unresolved'"
    )


@pytest.mark.xfail(
    strict=True,
    reason=(
        "W805-OOO state-disclosure pin (Pattern-2 fix template): the "
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
    r = prepare_change(symbol="nonexistent_sym", root=".")
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
        "W805-OOO child-partial-success propagation pin "
        "(W805-F/KK/LL/GGG/KKK quintet axis). When ANY child discloses "
        "degraded execution via summary.partial_success=True OR "
        "summary.state in a closed-enum degradation set OR "
        "summary.resolution in {'unresolved', 'fuzzy'}, the "
        "compound's failed_subcommands MUST include that child name. "
        "OOO is the BREADTH peer: TWO independent children "
        "(preflight + context) simultaneously degraded -- the "
        "aggregator must propagate BOTH. Bundled fix wave."
    ),
)
def test_empty_corpus_child_partial_success_propagates(empty_corpus):
    """Pin (W805-F/KK/LL/GGG/KKK axis, BREADTH-broadened): every child
    whose summary discloses degraded execution must be named in
    compound.summary.failed_subcommands. OOO exercises TWO degraded
    children at once -- the aggregator must list BOTH."""
    _DEGRADED_STATES = {
        "empty_corpus",
        "no_complexity_data",
        "not_found",
        "not_initialized",
        "no_data",
    }
    _DEGRADED_RESOLUTIONS = {"unresolved", "fuzzy"}
    r = prepare_change(symbol="nonexistent_sym", root=".")
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
        f"degraded children {degraded_children} (W805-F/KK/LL/GGG/KKK "
        f"axis, three-channel + BREADTH broadening: partial_success / "
        f"state / resolution across TWO simultaneously degraded "
        f"children)"
    )


@pytest.mark.xfail(
    strict=True,
    reason=(
        "W805-OOO child-state propagation pin (W805-GGG state-only "
        "channel). Even if partial_success / resolution propagation "
        "ships, a defensive parallel pin on the state-only channel "
        "stays warranted: when ANY child's summary.state is in a "
        "closed-enum degradation set, the compound's "
        "failed_subcommands MUST include that child. OOO's context "
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
    r = prepare_change(symbol="nonexistent_sym", root=".")
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
        "W805-OOO child-resolution propagation pin (W805-KKK 3-channel "
        "resolution-only axis). Even if partial_success / state "
        "propagation ships, a defensive parallel pin on the "
        "resolution-only channel stays warranted: when ANY child's "
        "summary.resolution is in {'unresolved', 'fuzzy'}, the "
        "compound's failed_subcommands MUST include that child. OOO's "
        "preflight AND context children BOTH set resolution="
        "'unresolved' -- the aggregator must list BOTH. Bundled fix "
        "wave."
    ),
)
def test_empty_corpus_child_resolution_propagates(empty_corpus):
    """Pin (W805-KKK resolution-only channel axis): every child whose
    summary.resolution names a degradation token must be in
    compound.summary.failed_subcommands."""
    _DEGRADED_RESOLUTIONS = {"unresolved", "fuzzy"}
    r = prepare_change(symbol="nonexistent_sym", root=".")
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
        f"(W805-KKK resolution-only channel axis -- OOO is the breadth "
        f"peer; TWO children disclose 'unresolved' simultaneously)"
    )


# ---------------------------------------------------------------------------
# Clean-corpus positive baseline - final aggregate sanity (W978 control)
# ---------------------------------------------------------------------------


def test_clean_corpus_emits_real_aggregate(clean_corpus):
    """End-to-end clean-corpus sanity: compound returns a real envelope
    with verdict + sections. On a clean corpus with a resolvable symbol,
    all three children produce real output and the compound stays
    clean."""
    r = prepare_change(symbol="handle_login", root=".")
    assert r.get("command") == "prepare-change"
    s = r["summary"]
    # Verdict aggregates child verdicts; non-empty.
    assert isinstance(s.get("verdict"), str) and s["verdict"], s
    # All three unconditional-when-symbol-set children present.
    for expected in ("preflight", "context", "effects"):
        assert expected in s["sections"], s
    # On clean corpus + resolvable symbol, compound is genuinely clean.
    assert s.get("partial_success") is False, s
    assert s.get("failed_subcommands") == [], s
