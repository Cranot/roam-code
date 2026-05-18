"""W805-GGG - Empty-corpus Pattern-2 smoke for ``roam_for_new_feature``.

Fifty-ninth-in-batch W805 sweep. Fourth peer of the compound trinity
(W805-F ``for_bug_fix``, W805-KK ``for_refactor``, W805-LL
``for_security_review``). ``for_new_feature`` is an MCP-only compound
exposed via ``@_tool(name="roam_for_new_feature")`` at
``src/roam/mcp_server.py:6326-6373`` -- there is no
``cmd_for_new_feature.py`` under ``src/roam/commands/``; the recipe
lives entirely in the MCP server and dispatches via
``_safe_run([_cr(<key>), ...])``.

Recipe composition (mcp_server.py:6351-6367): two unconditional
subcommands plus up to two conditional subcommands:

  - ``understand``                          (always, corpus-wide)
  - ``complexity --limit 10``                (always, corpus-wide)
  - ``search <area>``                        (only when ``area`` set)
  - ``context <anchor>``                     (only when search matched)

The compound aggregator is ``_compound_envelope`` at
``src/roam/mcp_server.py:4432``, same shared substrate as the trinity.

W978 first-hypothesis probe (run BEFORE writing tests):

OBSERVED behavior under in-process probe on empty corpus, ``area=""``::

    compound.summary.partial_success    = False             # SILENT-SAFE BUG
    compound.summary.failed_subcommands = []                # SILENT-SAFE BUG
    compound.summary.sections           = ['understand', 'complexity_report']
    compound.summary.state              = None              # MISSING

    child understand.summary.partial_success         = False
    child understand.summary.state                   = None
    child complexity_report.summary.partial_success  = False  # CHILD DOESN'T DISCLOSE
    child complexity_report.summary.state            = 'no_complexity_data'  # DISCLOSED
    child complexity_report.summary.verdict          = 'No complexity data - re-index ...'

This is the W805-F-class aggregator bug exposed on a third disclosure
mechanism: ``child.summary.state`` (e.g. ``no_complexity_data``) WITHOUT
``child.summary.partial_success: true``. The trinity exposed:

  - W805-F  (``for_bug_fix``):      ``resolution=unresolved`` + partial_success
  - W805-KK (``for_refactor``):     ``resolution=unresolved`` + partial_success
  - W805-LL (``for_security_review``): ``summary.state='empty_corpus'`` + partial_success

W805-GGG exposes the BROADER class: a child carries a non-empty
``summary.state`` token indicating degraded execution (no_complexity_data)
but does NOT set ``partial_success: true``. The aggregator at
mcp_server.py:4448-4470 reads ONLY top-level ``error`` keys, so the
child is placed in ``sections`` (the success bucket). Even a fix that
lifts ``child.summary.partial_success`` would miss this child because
the child itself doesn't disclose partial_success -- it discloses
state-only.

Concrete agent-safety impact: an agent prompt-cached on
``compound.summary.partial_success`` / ``failed_subcommands`` reads
``False`` / ``[]`` and assumes BOTH ``understand`` AND
``complexity_report`` ran cleanly. The compound's aggregate verdict
even SAYS "complexity_report: No complexity data" inline but the
machine-readable flags assert SAFE. For a ``for_new_feature`` compound
this lets an agent proceed with feature planning on an un-indexed
workspace, having read a "complexity is fine" verdict that actually
means "complexity could not be computed because zero symbols exist."

Compare CLAUDE.md Pattern-2 canonical:

    "Never emit verdict: 'SAFE' / 'completed' / 'non-conformant' when
     the underlying check failed or didn't run. Make absent state
     explicit: ``state: 'not_initialized'``, not ``state: 'broken'``."

Today the child IS explicit (``state: 'no_complexity_data'``); the
compound aggregator is the gap.

PIN STRATEGY (W978 + accumulate-only constraint):

1. SMOKE (always-on): no crash + envelope shape + LAW 6 verdict +
   per-child sections present + ``understand`` + ``complexity_report``
   appear in sections regardless of ``area``.
2. POSITIVE BASELINE: clean corpus -> complexity_report child does NOT
   disclose ``state: 'no_complexity_data'``.
3. PATTERN-2 PIN (xfail-strict): on empty corpus, complexity_report
   child discloses ``state: 'no_complexity_data'`` yet the compound's
   ``failed_subcommands`` list does NOT include ``complexity_report``.

The fix-forward (separate wave, bundled with W805-F/KK/LL): at
mcp_server.py:4470, also flip ``partial_success`` to True AND add to
``failed_subcommands`` whenever any child envelope's
``summary.state`` is in a closed-enum degradation set (e.g.
``empty_corpus`` / ``no_complexity_data`` / ``not_found`` /
``not_initialized``). Per W978: do NOT fix this wave; pin only.

Run isolation: ``python -m pytest tests/test_w805_ggg_cmd_for_new_feature_empty_corpus.py -x -n 0``
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
    from roam.mcp_server import for_new_feature  # noqa: E402
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
    ``complexity_report`` child discloses ``state: 'no_complexity_data'``
    while ``understand`` produces a healthy verdict on the structural
    1-language metadata. This is the canonical empty-corpus shape the
    W805 sweep exercises."""
    repo = tmp_path / "empty-for-new-feature-repo"
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
    repo = tmp_path / "clean-for-new-feature-repo"
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


def test_compound_exists_or_skip():
    """``for_new_feature`` is importable from ``roam.mcp_server``.

    Module-level import already gated this with ``pytest.skip``; this
    test reaffirms the precondition so a future refactor that renames
    or moves the entry point fails loudly here rather than silently
    skipping the entire module."""
    from roam import mcp_server  # noqa: F401

    assert callable(for_new_feature), type(for_new_feature)


# ---------------------------------------------------------------------------
# SMOKE (always-on)
# ---------------------------------------------------------------------------


class TestForNewFeatureEmptyCorpusSmoke:
    """Pattern-2 baseline assertions on the compound envelope shape."""

    def test_empty_corpus_no_crash(self, empty_corpus):
        """``for_new_feature`` must return a dict envelope, never raise."""
        r = for_new_feature(area="", root=".")
        assert isinstance(r, dict), f"expected dict, got {type(r).__name__}"

    def test_empty_corpus_envelope_has_verdict(self, empty_corpus):
        """``summary.verdict`` is a non-empty string (Pattern-2 always-emit)."""
        r = for_new_feature(area="", root=".")
        summary = r.get("summary") or {}
        verdict = summary.get("verdict") or ""
        assert isinstance(verdict, str) and verdict, f"summary.verdict must be non-empty string; got {verdict!r}"

    def test_empty_corpus_command_field_set(self, empty_corpus):
        """Compound envelope identifies itself as ``for-new-feature``."""
        r = for_new_feature(area="", root=".")
        assert r.get("command") == "for-new-feature", r.get("command")

    def test_empty_corpus_situation_target_meta(self, empty_corpus):
        """Compound carries the situation + target meta-fields."""
        r = for_new_feature(area="", root=".")
        s = r.get("summary") or {}
        assert s.get("situation") == "new_feature", s
        # Empty area resolves to the empty-string target.
        assert s.get("target") == "", s

    def test_empty_corpus_law6_verdict_standalone(self, empty_corpus):
        """LAW 6: verdict is single-line; readable without other fields."""
        r = for_new_feature(area="", root=".")
        verdict = r["summary"]["verdict"]
        assert "\n" not in verdict, f"verdict has embedded newline: {verdict!r}"

    def test_empty_corpus_partial_success_key_present(self, empty_corpus):
        """``summary.partial_success`` is always emitted as a bool."""
        r = for_new_feature(area="", root=".")
        s = r.get("summary") or {}
        assert "partial_success" in s, list(s.keys())
        assert isinstance(s["partial_success"], bool), type(s["partial_success"])

    def test_empty_corpus_failed_subcommands_list_present(self, empty_corpus):
        """``summary.failed_subcommands`` is always emitted as a list."""
        r = for_new_feature(area="", root=".")
        s = r.get("summary") or {}
        assert isinstance(s.get("failed_subcommands"), list), s.get("failed_subcommands")

    def test_empty_corpus_unconditional_sections_present(self, empty_corpus):
        """``understand`` + ``complexity_report`` always present in sections.

        The conditional ``search`` + ``context`` children only run when
        ``area`` matches. With empty ``area``, only the unconditional
        pair should appear.
        """
        r = for_new_feature(area="", root=".")
        sections = (r.get("summary") or {}).get("sections") or []
        for expected in ("understand", "complexity_report"):
            assert expected in sections, f"missing {expected!r} in {sections}"

    def test_empty_corpus_no_usage_error(self, empty_corpus):
        """Empty ``area`` is explicitly allowed (docstring contract);
        the compound must NOT return a USAGE_ERROR envelope."""
        r = for_new_feature(area="", root=".")
        assert r.get("error_code") != "USAGE_ERROR", r


# ---------------------------------------------------------------------------
# Clean-corpus positive baseline (W978 negative control)
# ---------------------------------------------------------------------------


class TestForNewFeatureCleanCorpusBaseline:
    """Real symbol on a real index: the complexity_report child runs
    cleanly + does NOT disclose ``state: 'no_complexity_data'``. Confirms
    the empty-corpus pin below is NOT a class-wide compound defect -- it
    is specifically the empty-corpus axis on the complexity_report child."""

    def test_clean_corpus_emits_real_compound(self, clean_corpus):
        r = for_new_feature(area="", root=".")
        assert r.get("command") == "for-new-feature"
        s = r["summary"]
        assert "understand" in s["sections"], s["sections"]
        assert "complexity_report" in s["sections"], s["sections"]

    def test_clean_corpus_complexity_child_not_no_data(self, clean_corpus):
        """Clean corpus: the complexity_report child's summary.state is
        NOT 'no_complexity_data' (mirror of the empty-corpus shape,
        opposite value)."""
        r = for_new_feature(area="", root=".")
        cr = r.get("complexity_report") or {}
        cs = cr.get("summary") or {}
        assert cs.get("state") != "no_complexity_data", f"clean corpus reports state='no_complexity_data': {cs!r}"


# ---------------------------------------------------------------------------
# W978 first-hypothesis sanity: the complexity_report child DOES disclose
# the no-data state. This proves the next test below is pinning the
# COMPOUND aggregator gap, not a missing child-level disclosure.
# ---------------------------------------------------------------------------


class TestForNewFeatureEmptyComplexityChildDisclosesState:
    """Sanity: on empty corpus, the ``complexity_report`` child DOES
    emit ``summary.state: 'no_complexity_data'``.

    If this class ever fails, the bug has shifted -- the complexity
    detector has regressed (or the state field has been renamed).
    The compound pin below ASSUMES this disclosure is in place; mutate
    the pin if these break."""

    def test_complexity_child_discloses_no_complexity_data_state(self, empty_corpus):
        r = for_new_feature(area="", root=".")
        cr = r.get("complexity_report") or {}
        cs = cr.get("summary") or {}
        assert cs.get("state") == "no_complexity_data", cs

    def test_complexity_child_verdict_names_no_data(self, empty_corpus):
        """The child verdict literally says 'No complexity data'."""
        r = for_new_feature(area="", root=".")
        cr = r.get("complexity_report") or {}
        cs = cr.get("summary") or {}
        verdict = (cs.get("verdict") or "").lower()
        assert "no complexity data" in verdict, cs


# ---------------------------------------------------------------------------
# PATTERN-2 PIN (xfail-strict) -- the compound aggregator gap (W805-F peer)
# ---------------------------------------------------------------------------


@pytest.mark.xfail(
    strict=True,
    reason=(
        "W805-GGG REAL BUG - agent-safety HIGH "
        "(Pattern-2 silent fallback / Variant-D silent success on "
        "degraded child resolution). Same root cause as W805-F/KK/LL: "
        "_compound_envelope at src/roam/mcp_server.py:4448-4470 "
        "computes failed_subcommands ONLY from per-child top-level "
        "'error' keys. The complexity_report child returns a "
        "structured envelope with NO top-level error AND no "
        "summary.partial_success flag, but DOES disclose "
        "summary.state='no_complexity_data' -- i.e. self-disclosing "
        "degraded execution via the state-only channel. The "
        "aggregator never reads the nested state signal, so "
        "complexity_report is placed in 'sections' (the success "
        "bucket) rather than in failed_subcommands. Agent-safety "
        "HIGH: an agent reading the compound verdict on an empty / "
        "not-yet-indexed workspace sees 'complexity_report: No "
        "complexity data' inline AND complexity_report in sections, "
        "and may proceed with feature planning assuming the "
        "complexity check ran cleanly. Fix: at mcp_server.py:4470, "
        "also add child to failed_subcommands whenever child.summary."
        "state is in a closed-enum degradation set (empty_corpus / "
        "no_complexity_data / not_found / not_initialized). Bundled "
        "with W805-F/KK/LL fix wave; separate from this pin per W978 "
        "+ accumulate-only constraint."
    ),
)
def test_no_silent_no_feature_planning_on_empty(empty_corpus):
    """Pin: compound must lift complexity_report child's
    no_complexity_data disclosure into failed_subcommands.

    The complexity_report child correctly discloses
    ``state: 'no_complexity_data'``. The compound aggregator must
    propagate that signal into ``failed_subcommands``, OR an agent
    prompt-cached on ``compound.summary.failed_subcommands`` reads
    ``[]`` and proceeds with feature planning whose complexity check
    actually ran on zero symbols.
    """
    r = for_new_feature(area="", root=".")
    s = r["summary"]
    failed = set(s.get("failed_subcommands") or [])
    assert "complexity_report" in failed, (
        f"compound.summary.failed_subcommands={failed} omits "
        f"'complexity_report' despite child disclosing "
        f"state='no_complexity_data'. Agent-safety HIGH: agent "
        f"reads failed_subcommands and assumes complexity_report "
        f"ran cleanly while in fact zero symbols were analyzed."
    )


@pytest.mark.xfail(
    strict=True,
    reason=(
        "W805-GGG partial_success aggregator pin: the compound "
        "summary.partial_success MUST flip True whenever ANY child "
        "discloses a degraded state token (no_complexity_data / "
        "empty_corpus / not_found). Today it stays False because "
        "the aggregator only reads top-level 'error' keys. Bundled "
        "with the failed_subcommands propagation fix; separate wave "
        "per W978."
    ),
)
def test_empty_corpus_partial_success_set(empty_corpus):
    """Pin: ``summary.partial_success`` is True on empty corpus."""
    r = for_new_feature(area="", root=".")
    s = r["summary"]
    assert s.get("partial_success") is True, (
        f"compound.summary.partial_success={s.get('partial_success')} "
        f"on empty corpus despite complexity_report disclosing "
        f"state='no_complexity_data'"
    )


@pytest.mark.xfail(
    strict=True,
    reason=(
        "W805-GGG state-disclosure pin (Pattern-2 fix template): the "
        "compound envelope SHOULD carry an explicit summary.state "
        "field naming the empty-data shape (e.g. 'no_data' / "
        "'no_complexity_data' / 'empty_corpus'). Today the compound "
        "emits no state key at all -- only its children do. "
        "Closed-enum state-disclosure is the Pattern-2 canonical fix "
        "per CLAUDE.md Pattern-2. Bundled with the partial_success / "
        "failed_subcommands propagation fix; separate wave per W978."
    ),
)
def test_empty_corpus_state_explicit(empty_corpus):
    """Pin: compound discloses no_data / empty_corpus state on the
    empty-corpus path. Today the key is absent."""
    r = for_new_feature(area="", root=".")
    state = (r["summary"] or {}).get("state")
    assert state is not None, "compound.summary.state missing on empty corpus"
    # Closed-enum disclosure: one of these tokens.
    assert state in {"no_data", "not_initialized", "empty_corpus", "no_complexity_data"}, (
        f"compound.summary.state={state!r} not in closed-enum"
    )


@pytest.mark.xfail(
    strict=True,
    reason=(
        "W805-GGG child-partial-success / state propagation pin "
        "(W805-F/KK/LL axis). When ANY child discloses degraded "
        "execution via summary.partial_success=True OR "
        "summary.state in a closed-enum degradation set, the "
        "compound's failed_subcommands MUST include that child "
        "name. This is the broadest one-line fix at "
        "mcp_server.py:4470. Bundled fix wave."
    ),
)
def test_empty_corpus_child_partial_success_propagates(empty_corpus):
    """Pin (W805-F/KK/LL axis, broadened): every child whose summary
    discloses degraded execution must be named in
    compound.summary.failed_subcommands."""
    _DEGRADED_STATES = {
        "empty_corpus",
        "no_complexity_data",
        "not_found",
        "not_initialized",
        "no_data",
    }
    r = for_new_feature(area="", root=".")
    s = r["summary"]
    degraded_children = []
    for name in s.get("sections") or []:
        child = r.get(name) or {}
        psum = child.get("summary") or {}
        if psum.get("partial_success") is True:
            degraded_children.append(name)
        elif psum.get("state") in _DEGRADED_STATES:
            degraded_children.append(name)
    failed = set(s.get("failed_subcommands") or [])
    assert failed.issuperset(set(degraded_children)), (
        f"compound.summary.failed_subcommands={failed} omits "
        f"degraded children {degraded_children} (W805-F/KK/LL axis, "
        f"state-channel broadening)"
    )


# ---------------------------------------------------------------------------
# Clean-corpus positive baseline - final aggregate sanity (W978 control)
# ---------------------------------------------------------------------------


def test_clean_corpus_emits_real_aggregate(clean_corpus):
    """End-to-end clean-corpus sanity: compound returns a real envelope
    with verdict + sections + situation + target. On a clean corpus
    with no ``area`` set, both unconditional children produce real
    output and the compound stays clean."""
    r = for_new_feature(area="", root=".")
    assert r.get("command") == "for-new-feature"
    s = r["summary"]
    # Verdict aggregates child verdicts; non-empty.
    assert isinstance(s.get("verdict"), str) and s["verdict"], s
    # situation/target carried.
    assert s.get("situation") == "new_feature"
    assert s.get("target") == ""
    # Both unconditional children present.
    assert "understand" in s["sections"], s
    assert "complexity_report" in s["sections"], s
    # On clean corpus, compound is genuinely clean.
    assert s.get("partial_success") is False, s
    assert s.get("failed_subcommands") == [], s
