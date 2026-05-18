"""W805-QQQ - Empty-corpus Pattern-2 smoke for ``roam_review_change``.

Sixty-ninth-in-batch W805 sweep. Seventh peer of the compound sextet
(W805-F ``for_bug_fix``, W805-KK ``for_refactor``, W805-LL
``for_security_review``, W805-GGG ``for_new_feature``, W805-KKK
``diagnose_issue``, W805-OOO ``prepare_change``). ``review_change`` is
an MCP-only compound exposed via ``@_tool(name="roam_review_change")``
at ``src/roam/mcp_server.py:4699-4748`` -- there is no
``cmd_review_change.py`` under ``src/roam/commands/``; the recipe lives
entirely in the MCP server and dispatches via plain
``_run_roam([<key>, ...])``.

Recipe composition (mcp_server.py:4724-4747): three subcommands::

  - ``pr-risk [--staged]``                                (always)
  - ``breaking [<commit_range>]``                         (always)
  - ``pr-diff [--staged] [--range <commit_range>]``        (always)

The compound aggregator is ``_compound_envelope`` at
``src/roam/mcp_server.py:4432``, same shared substrate as the sextet.

W978 first-hypothesis probe (run BEFORE writing tests):

OBSERVED behavior under in-process probe on empty corpus, default
arguments (no staged, no commit_range), no diff at all::

    compound.summary.partial_success    = False
    compound.summary.failed_subcommands = []
    compound.summary.sections           = ['pr_risk', 'breaking_changes', 'pr_diff']
    compound.summary.state              = None
    compound.summary.verdict            = "pr_risk: no-changes | breaking_changes: no breaking changes vs HEAD~1 | pr_diff: no changes detected"

    child pr_risk.summary.partial_success         = False  # NOT degraded
    child pr_risk.summary.verdict                  = "no-changes"
    child pr_risk.summary.risk_score               = 0
    child breaking_changes.summary.partial_success = False  # NOT degraded
    child breaking_changes.summary.verdict         = "no breaking changes vs HEAD~1"
    child pr_diff.summary.partial_success          = False  # NOT degraded
    child pr_diff.summary.metric_deltas_available  = False  # disclosed via field
    child pr_diff.summary.verdict                  = "no changes detected"

KEY septet finding (NEW AXIS confirmed): unlike the sextet
(W805-F/KK/LL/GGG/KKK/OOO) which had children that explicitly set
``summary.partial_success=True`` on degraded execution, the QQQ
children all report ``partial_success=False``. They DO disclose the
no-changes state, but ONLY via the verdict-string channel (and
``metric_deltas_available=False`` on pr_diff). There is no
machine-readable ``partial_success`` / ``state`` / ``resolution``
disclosure on any child.

Even more concerning, a bogus ``commit_range`` (e.g.
``nonexistent_branch..HEAD``) returns
``verdict: "no breaking changes vs nonexistent_branch..HEAD"`` with
``partial_success=False`` on ``breaking_changes``. Either ``breaking``
silently falls back when the ref doesn't resolve, or it doesn't
validate the range. Combined with the empty-diff axis, this is the
fresh axis the W805-OOO agent hypothesized: ``review_change`` operates
on a "no-staged / empty commit_range" degradation state with NO
machine-readable disclosure.

Bug class summary table (septet peer comparison):

  - W805-F   (``for_bug_fix``):           ``resolution=unresolved``
  - W805-KK  (``for_refactor``):          ``resolution=unresolved``
  - W805-LL  (``for_security_review``):   ``state='empty_corpus'``
  - W805-GGG (``for_new_feature``):       ``state='no_complexity_data'``
  - W805-KKK (``diagnose_issue``):        3-channel on ONE child
  - W805-OOO (``prepare_change``):        3-channel on TWO children
  - W805-QQQ (``review_change``):         NO-DIFF axis: verdict-channel
                                          disclosure ONLY; no
                                          partial_success/state/resolution
                                          channel emitted by ANY child

Agent-safety impact (MEDIUM, softer than sextet): an agent prompt-
cached on ``compound.summary.partial_success`` / ``failed_subcommands``
reads ``False`` / ``[]`` and concludes "review-change ran cleanly --
PR is safe to commit". The compound verdict literally says "no-changes
| no breaking changes | no changes detected" which a verdict-string-
reading agent could parse correctly, BUT a machine-flag-reading agent
sees a clean SAFE envelope. For a PR-review compound (the canonical
"call before committing or opening a PR" tool per the docstring at
mcp_server.py:4707), a clean SAFE on "no diff at all" or "ref doesn't
exist" is exactly the Pattern-2 silent fallback class.

Compare CLAUDE.md Pattern-2 canonical:

    "Never emit verdict: 'SAFE' / 'completed' / 'non-conformant' when
     the underlying check failed or didn't run. Make absent state
     explicit: ``state: 'not_initialized'``, not ``state: 'broken'``."

Today the compound verdict on no-diff is "no breaking changes vs HEAD~1"
-- close to a SAFE verdict, but absent state is NOT explicit via the
machine-readable channel.

PIN STRATEGY (W978 + accumulate-only constraint):

1. SMOKE (always-on): no crash + envelope shape + LAW 6 verdict +
   ``pr_risk`` + ``breaking_changes`` + ``pr_diff`` sections present.
2. POSITIVE BASELINE: clean corpus + real diff -> pr_risk reports
   meaningful risk and pr_diff reports footprint > 0.
3. PATTERN-2 PINS (xfail-strict): on empty/no-diff corpus, the
   compound's machine-readable ``summary.state`` is missing and
   ``summary.partial_success`` is False despite the underlying
   no-data degradation. Also pin the bogus-commit-range silent
   fallback on ``breaking`` child.

The fix-forward (separate wave, bundled with W805-F/KK/LL/GGG/KKK/OOO):
either (a) at mcp_server.py:4470, also flip ``partial_success`` to
True AND add to ``failed_subcommands`` whenever any child envelope's
``summary.verdict`` matches the no-data pattern (``no-changes`` /
``no changes detected`` / ``no breaking changes vs <ref>``), OR (b)
make ``pr_risk`` / ``breaking`` / ``pr_diff`` children themselves set
``summary.partial_success=True`` and ``summary.state='no_data'`` /
``'empty_diff'`` on the no-diff path. The (b) option aligns better
with the sextet's child-disclosure-then-aggregator-propagation
template. Per W978: do NOT fix this wave; pin only.

Run isolation: ``python -m pytest tests/test_w805_qqq_review_change_empty_corpus.py -x -n 0``
Cross-sibling: ``python -m pytest tests/test_w805_ooo*.py tests/test_w805_kkk*.py -x -n 0``
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
from conftest import index_in_process  # noqa: E402

# Import the compound directly (mirrors W805-OOO probing pattern).
try:
    from roam.mcp_server import review_change  # noqa: E402
except Exception as _exc:  # pragma: no cover - guarded environments only
    pytest.skip(
        f"roam.mcp_server import failed: {_exc!r}; MCP compound tests require the MCP server module to be importable.",
        allow_module_level=True,
    )


# ---------------------------------------------------------------------------
# Test hygiene: disable the large-response handle-off so envelope inspection
# reads the full compound dict directly.
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
    """A git repo with a single empty .py file and no diff.

    The indexer runs cleanly but produces zero function/class/method
    symbols. The compound's three children all run; none have any
    diff to analyze. This is the canonical empty / no-diff shape
    the W805-QQQ sweep exercises."""
    repo = tmp_path / "empty-review-change-repo"
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
    """A git repo with a real function for happy-path coverage.

    No diff against HEAD by construction (initial commit only).
    """
    repo = tmp_path / "clean-review-change-repo"
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


def test_review_change_compound_exists_or_skip():
    """``review_change`` is importable from ``roam.mcp_server``.

    Module-level import already gated this with ``pytest.skip``; this
    test reaffirms the precondition so a future refactor that renames
    or moves the entry point fails loudly here rather than silently
    skipping the entire module."""
    from roam import mcp_server  # noqa: F401

    assert callable(review_change), type(review_change)


# ---------------------------------------------------------------------------
# SMOKE (always-on)
# ---------------------------------------------------------------------------


class TestReviewChangeEmptyCorpusSmoke:
    """Pattern-2 baseline assertions on the compound envelope shape."""

    def test_empty_corpus_no_crash(self, empty_corpus):
        """``review_change`` must return a dict envelope, never raise."""
        r = review_change(root=".")
        assert isinstance(r, dict), f"expected dict, got {type(r).__name__}"

    def test_empty_corpus_envelope_has_verdict(self, empty_corpus):
        """``summary.verdict`` is a non-empty string (Pattern-2 always-emit)."""
        r = review_change(root=".")
        summary = r.get("summary") or {}
        verdict = summary.get("verdict") or ""
        assert isinstance(verdict, str) and verdict, f"summary.verdict must be non-empty string; got {verdict!r}"

    def test_empty_corpus_command_field_set(self, empty_corpus):
        """Compound envelope identifies itself as ``review-change``."""
        r = review_change(root=".")
        assert r.get("command") == "review-change", r.get("command")

    def test_empty_corpus_law6_verdict_standalone(self, empty_corpus):
        """LAW 6: verdict is single-line; readable without other fields."""
        r = review_change(root=".")
        verdict = r["summary"]["verdict"]
        assert "\n" not in verdict, f"verdict has embedded newline: {verdict!r}"

    def test_empty_corpus_partial_success_key_present(self, empty_corpus):
        """``summary.partial_success`` is always emitted as a bool."""
        r = review_change(root=".")
        s = r.get("summary") or {}
        assert "partial_success" in s, list(s.keys())
        assert isinstance(s["partial_success"], bool), type(s["partial_success"])

    def test_empty_corpus_failed_subcommands_list_present(self, empty_corpus):
        """``summary.failed_subcommands`` is always emitted as a list."""
        r = review_change(root=".")
        s = r.get("summary") or {}
        assert isinstance(s.get("failed_subcommands"), list), s.get("failed_subcommands")

    def test_empty_corpus_unconditional_sections_present(self, empty_corpus):
        """``pr_risk`` + ``breaking_changes`` + ``pr_diff`` all present.

        All three children run unconditionally (no symbol gate; the
        compound only varies argv based on ``staged`` / ``commit_range``)."""
        r = review_change(root=".")
        sections = (r.get("summary") or {}).get("sections") or []
        for expected in ("pr_risk", "breaking_changes", "pr_diff"):
            assert expected in sections, f"missing {expected!r} in {sections}"


# ---------------------------------------------------------------------------
# Clean-corpus positive baseline (W978 negative control). On a clean
# corpus with no diff (initial commit only), the compound still emits
# a clean envelope -- there's no degraded execution; the "no changes"
# state is the true state.
# ---------------------------------------------------------------------------


class TestReviewChangeCleanCorpusBaseline:
    """Clean corpus, no diff: compound emits a clean aggregate but the
    no-diff state is the actual state. This confirms the QQQ pins
    target the machine-readable-disclosure gap, not the underlying
    detector behavior."""

    def test_clean_corpus_emits_real_compound(self, clean_corpus):
        r = review_change(root=".")
        assert r.get("command") == "review-change"
        s = r["summary"]
        for expected in ("pr_risk", "breaking_changes", "pr_diff"):
            assert expected in s["sections"], s["sections"]

    def test_clean_corpus_no_errors(self, clean_corpus):
        """Children all run cleanly; no top-level errors."""
        r = review_change(root=".")
        assert r["summary"].get("errors") == 0


# ---------------------------------------------------------------------------
# W978 first-hypothesis sanity: verify the CURRENT shape children emit.
# These tests document what is observed today (verdict-channel disclosure
# only; no partial_success/state/resolution disclosure). If they ever
# fail, the bug has shifted -- a child now sets a machine-readable
# degradation flag.
# ---------------------------------------------------------------------------


class TestReviewChangeEmptyChildrenDiscloseOnlyOnVerdictChannel:
    """Sanity: on empty/no-diff corpus, children disclose the no-diff
    state ONLY via the verdict string. None set partial_success=True
    or state= or resolution=. This is the W805-QQQ fresh axis: a
    septet peer where children themselves under-disclose on the
    machine-readable channel, unlike the sextet."""

    def test_pr_risk_child_verdict_discloses_no_changes(self, empty_corpus):
        r = review_change(root=".")
        pr = r.get("pr_risk") or {}
        ps = pr.get("summary") or {}
        # Verdict-channel disclosure is in place today.
        assert "no-changes" in (ps.get("verdict") or "").lower(), ps

    def test_pr_diff_child_metric_deltas_available_is_false(self, empty_corpus):
        """``pr_diff`` self-discloses no-data via the
        ``metric_deltas_available`` field on its summary."""
        r = review_change(root=".")
        pd = r.get("pr_diff") or {}
        ps = pd.get("summary") or {}
        assert ps.get("metric_deltas_available") is False, ps

    def test_pr_diff_child_footprint_pct_zero(self, empty_corpus):
        """``pr_diff`` self-discloses no diff via ``footprint_pct=0.0``."""
        r = review_change(root=".")
        pd = r.get("pr_diff") or {}
        ps = pd.get("summary") or {}
        assert ps.get("footprint_pct") == 0.0, ps

    def test_breaking_child_zero_counts(self, empty_corpus):
        """``breaking`` self-discloses no-changes via zero counts."""
        r = review_change(root=".")
        br = r.get("breaking_changes") or {}
        ps = br.get("summary") or {}
        assert ps.get("removed") == 0, ps
        assert ps.get("renamed") == 0, ps
        assert ps.get("signature_changed") == 0, ps


# ---------------------------------------------------------------------------
# PATTERN-2 PINS (xfail-strict) -- the QQQ fresh axis
# ---------------------------------------------------------------------------


@pytest.mark.xfail(
    strict=True,
    reason=(
        "W805-QQQ REAL BUG - agent-safety MEDIUM "
        "(Pattern-2 silent fallback on the no-diff axis). The QQQ "
        "septet peer differs from the W805-F/KK/LL/GGG/KKK/OOO sextet "
        "in that NONE of the three children (pr_risk, breaking_changes, "
        "pr_diff) set summary.partial_success=True on the no-diff "
        "degradation. The children disclose the no-diff state ONLY via "
        "the verdict string ('no-changes' / 'no changes detected' / "
        "'no breaking changes vs HEAD~1') and one field "
        "(pr_diff.summary.metric_deltas_available=False). The compound "
        "aggregator at _compound_envelope (mcp_server.py:4448-4470) "
        "computes failed_subcommands ONLY from per-child top-level "
        "'error' keys, so the compound surfaces "
        "partial_success=False / failed_subcommands=[] on what is "
        "actually a 'review on insufficient input' situation. Agent "
        "safety MEDIUM: this is the canonical 'call before committing "
        "or opening a PR' compound (docstring at mcp_server.py:4707). "
        "An agent prompt-cached on compound.summary.partial_success "
        "reads False on a no-diff workspace and concludes 'review "
        "passed cleanly -- safe to commit', missing the fact that "
        "there was no diff to review in the first place. Fix template "
        "(b): pr-risk / breaking / pr-diff children should set "
        "summary.partial_success=True AND summary.state='no_data' or "
        "'empty_diff' when the underlying diff is empty -- aligns with "
        "the sextet's child-disclosure-then-aggregator-propagation "
        "template. Per W978: do NOT fix this wave; pin only."
    ),
)
def test_no_silent_review_complete_on_empty(empty_corpus):
    """Pin: compound must NOT report a clean SAFE-looking envelope when
    no diff exists.

    Today ``summary.partial_success=False`` AND
    ``summary.failed_subcommands=[]`` on a no-diff workspace, despite
    NO underlying review actually being performed. An agent reading
    the machine-readable channel concludes 'review passed cleanly'.
    """
    r = review_change(root=".")
    s = r["summary"]
    # On a no-diff workspace, the compound MUST disclose insufficient
    # input on the machine-readable channel -- either via
    # partial_success=True OR via failed_subcommands listing the
    # no-data children OR via an explicit summary.state field.
    machine_readable_disclosure = (
        s.get("partial_success") is True
        or bool(s.get("failed_subcommands"))
        or s.get("state") in {"no_data", "empty_diff", "no_changes", "insufficient_input"}
    )
    assert machine_readable_disclosure, (
        f"compound.summary={s!r} reports a clean SAFE envelope on a "
        f"no-diff workspace. Agent prompt-cached on partial_success "
        f"reads False and concludes 'review passed cleanly' on what "
        f"was actually 'no review performed -- no diff to analyze'. "
        f"Pattern-2 silent fallback class."
    )


@pytest.mark.xfail(
    strict=True,
    reason=(
        "W805-QQQ partial_success aggregator pin (sextet septet "
        "peer axis): the compound summary.partial_success MUST flip "
        "True when ALL THREE children disclose no-data state (even if "
        "only via the verdict-string channel today). Today it stays "
        "False because the aggregator reads only top-level 'error' "
        "keys. Bundled with the no-diff disclosure fix; separate wave "
        "per W978."
    ),
)
def test_empty_corpus_partial_success_set(empty_corpus):
    """Pin: ``summary.partial_success`` is True on a no-diff workspace
    because the underlying review couldn't analyze anything."""
    r = review_change(root=".")
    s = r["summary"]
    assert s.get("partial_success") is True, (
        f"compound.summary.partial_success={s.get('partial_success')} "
        f"on no-diff workspace despite all three children disclosing "
        f"no-data state (pr_risk: 'no-changes', breaking: 'no breaking "
        f"changes', pr_diff: 'no changes detected')"
    )


@pytest.mark.xfail(
    strict=True,
    reason=(
        "W805-QQQ state-disclosure pin (Pattern-2 fix template): the "
        "compound envelope SHOULD carry an explicit summary.state "
        "field naming the no-diff / empty-input shape (e.g. "
        "'no_data' / 'empty_diff' / 'no_changes'). Today the compound "
        "emits no state key at all. Closed-enum state-disclosure is "
        "the Pattern-2 canonical fix per CLAUDE.md Pattern-2. Bundled "
        "with the partial_success / failed_subcommands propagation "
        "fix; separate wave per W978."
    ),
)
def test_empty_corpus_state_explicit(empty_corpus):
    """Pin: compound discloses no-data / empty-diff state on the
    no-diff path. Today the key is absent."""
    r = review_change(root=".")
    state = (r["summary"] or {}).get("state")
    assert state is not None, "compound.summary.state missing on no-diff workspace"
    # Closed-enum disclosure: one of these tokens.
    assert state in {
        "no_data",
        "empty_diff",
        "no_changes",
        "insufficient_input",
        "empty_corpus",
    }, f"compound.summary.state={state!r} not in closed-enum"


@pytest.mark.xfail(
    strict=True,
    reason=(
        "W805-QQQ child-partial-success propagation pin (sextet "
        "axis). When ANY child discloses no-data state via "
        "summary.partial_success=True OR summary.state in a closed-enum "
        "degradation set OR -- in QQQ's case -- via the verdict-string "
        "no-data pattern, the compound's failed_subcommands MUST "
        "include that child name. Today no child sets "
        "partial_success=True so this pin holds even if the QQQ "
        "child-disclosure layer ships first."
    ),
)
def test_empty_corpus_child_partial_success_propagates(empty_corpus):
    """Pin (sextet axis, broadened for QQQ): every child whose summary
    discloses no-data state must be in compound.summary.failed_subcommands.

    QQQ exercises the verdict-string channel today (children don't
    set machine flags). The fix must propagate ALL three children's
    no-data state into failed_subcommands once children start
    setting partial_success=True / state= flags."""
    _NO_DATA_STATES = {
        "no_data",
        "empty_diff",
        "no_changes",
        "insufficient_input",
        "empty_corpus",
    }
    _NO_DATA_VERDICT_TOKENS = ("no-changes", "no changes detected", "no breaking changes")
    r = review_change(root=".")
    s = r["summary"]
    no_data_children = []
    for name in s.get("sections") or []:
        child = r.get(name) or {}
        psum = child.get("summary") or {}
        if psum.get("partial_success") is True:
            no_data_children.append(name)
        elif psum.get("state") in _NO_DATA_STATES:
            no_data_children.append(name)
        else:
            v = (psum.get("verdict") or "").lower()
            if any(tok in v for tok in _NO_DATA_VERDICT_TOKENS):
                no_data_children.append(name)
    failed = set(s.get("failed_subcommands") or [])
    assert failed.issuperset(set(no_data_children)), (
        f"compound.summary.failed_subcommands={failed} omits "
        f"no-data children {no_data_children} (W805-QQQ no-diff "
        f"axis: verdict-string channel disclosure today, plus the "
        f"sextet's partial_success / state channels)"
    )


@pytest.mark.xfail(
    strict=True,
    reason=(
        "W805-QQQ child-state propagation pin (W805-GGG/OOO state-only "
        "channel axis). When ANY child's summary.state is in a "
        "closed-enum no-data set, the compound's failed_subcommands "
        "MUST include that child. Today no child sets a state field on "
        "the no-diff path -- this pin assumes the QQQ child-disclosure "
        "layer ships state= on pr-risk / breaking / pr-diff. Bundled "
        "fix wave."
    ),
)
def test_empty_corpus_child_state_propagates(empty_corpus):
    """Pin (W805-GGG/OOO state-only channel axis): every child whose
    summary.state names a no-data token must be in
    compound.summary.failed_subcommands. Today no child sets state on
    the no-diff path; this pin holds for the post-fix shape."""
    _NO_DATA_STATES = {
        "no_data",
        "empty_diff",
        "no_changes",
        "insufficient_input",
        "empty_corpus",
    }
    r = review_change(root=".")
    s = r["summary"]
    state_no_data = []
    for name in s.get("sections") or []:
        child = r.get(name) or {}
        psum = child.get("summary") or {}
        if psum.get("state") in _NO_DATA_STATES:
            state_no_data.append(name)
    failed = set(s.get("failed_subcommands") or [])
    # Once the fix lands, child(ren) will set state and the aggregator
    # will list them in failed_subcommands. Today: no child sets state
    # so state_no_data=[] and failed=set() -- the issuperset check
    # would pass trivially. To make this pin meaningful and strict,
    # also require state_no_data to be non-empty (which is the post-fix
    # shape we're pinning toward).
    assert state_no_data, (
        "no child sets summary.state on the no-diff path; expected "
        "pr_risk / breaking_changes / pr_diff to set state='no_data' "
        "or 'empty_diff' (W805-QQQ child-disclosure layer)"
    )
    assert failed.issuperset(set(state_no_data)), (
        f"compound.summary.failed_subcommands={failed} omits "
        f"state-no-data children {state_no_data} (W805-GGG/OOO "
        f"state-only channel axis)"
    )


@pytest.mark.xfail(
    strict=True,
    reason=(
        "W805-QQQ child-no-diff disclosure pin -- the fresh axis "
        "specific to QQQ. The pr-risk / breaking / pr-diff children "
        "must each set summary.partial_success=True when their "
        "respective inputs are empty (no diff). Today all three return "
        "partial_success=False on the no-diff path; the no-data state "
        "is disclosed only via the verdict string. The sextet's "
        "child-disclosure template (set partial_success=True on "
        "degraded execution) is the canonical fix. Bundled fix wave."
    ),
)
def test_empty_corpus_no_diff_disclosure(empty_corpus):
    """Pin (W805-QQQ fresh axis per W805-OOO agent hypothesis): every
    child must set ``summary.partial_success=True`` on the no-diff
    path. Today all three children report ``partial_success=False``
    and disclose the no-diff state only via the verdict string."""
    r = review_change(root=".")
    children_to_check = ("pr_risk", "breaking_changes", "pr_diff")
    not_disclosing = []
    for name in children_to_check:
        child = r.get(name) or {}
        psum = child.get("summary") or {}
        if psum.get("partial_success") is not True:
            not_disclosing.append(name)
    assert not not_disclosing, (
        f"children {not_disclosing} do NOT set "
        f"summary.partial_success=True on the no-diff path. They "
        f"disclose the no-data state only via the verdict-string "
        f"channel ('no-changes' / 'no changes detected' / 'no "
        f"breaking changes'). The sextet's machine-readable "
        f"disclosure template is the canonical fix."
    )
