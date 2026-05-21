"""W805-LL — Empty-corpus Pattern-2 smoke for ``roam_for_security_review``.

Thirty-eighth-in-batch W805 sweep. Sibling compound to ``for_bug_fix``
(W805-F aggregator-bug pin) and ``for_refactor`` (W805-KK in-flight).
``for_security_review`` is an MCP-only compound exposed via
``@_tool(name="roam_for_security_review")`` at
``src/roam/mcp_server.py:6473-6516`` — there is no
``cmd_for_security_review.py`` under ``src/roam/commands/``; the recipe
lives entirely in the MCP server and dispatches via
``_safe_run([_cr(<key>), …])``.

Historical note (CLAUDE.md Pattern 5): the ``vuln``/``vulns`` typo
originally lived in THIS compound — caller had ``roam vuln`` (CLI key
is ``vulns``). Sealed by the ``_COMPOUND_REGISTRY`` + ``_cr()``
indirection at mcp_server.py:6264-6323 plus the import-time gate at
``_verify_compound_registry()``. This module does not re-pin that
seal; it pins the next-level aggregator gap.

Recipe composition (mcp_server.py:6499-6510): four subcommands, in
order — ``taint`` → ``vulns list`` → ``critique`` → ``adversarial``.
The compound aggregator is ``_compound_envelope`` at
``src/roam/mcp_server.py:4432``, same shared substrate as
``for_bug_fix`` (W805-F) and ``for_refactor``.

W978 first-hypothesis probe (run BEFORE writing tests). The dict
below is the PRE-W805-OCTET-seal snapshot — see the post-seal note
that follows it::

    compound.summary.partial_success    = False             # SILENT-SAFE BUG
    compound.summary.failed_subcommands = []                # SILENT-SAFE BUG
    compound.summary.sections           = ['taint', 'vulns', 'critique', 'adversarial']
    compound.summary.state              = None              # MISSING

    child taint.summary.partial_success = True              # DISCLOSED
    child taint.summary.state           = 'empty_corpus'    # DISCLOSED
    child taint.summary.verdict         = 'no symbols to analyze (corpus empty; 22 rules loaded but not run ...)'

POST-W805-OCTET-SEAL: the ``_compound_envelope`` aggregator now also
routes a child carrying ``isError: true`` into ``failed_subcommands``.
``vulns`` + ``critique`` carry ``isError`` on an empty corpus, so they
move out of the success bucket — ``sections`` is now
``['taint', 'adversarial']``. ``taint`` carries NO ``isError`` flag,
only the nested ``summary.partial_success`` / ``summary.state``
disclosure, so the seal does NOT catch it and it stays wrongly in
``sections``. That residual partial_success-axis gap is exactly what
this module pins; see ``test_empty_corpus_four_child_sections_present``
for the current verified bucket split.

This is the canonical W805-F-class aggregator bug, on a different
child: the ``taint`` child returns a structured envelope with NO
top-level ``error`` key while self-disclosing
``summary.partial_success: true`` + ``summary.state: 'empty_corpus'``.
The aggregator at ``src/roam/mcp_server.py:4448-4470`` reads top-level
``error`` keys only — so ``taint`` is placed in ``sections`` (the
success bucket) rather than in ``failed_subcommands``, and the
compound emits ``partial_success: false`` while a child analyzer
disclosed zero symbols analyzed.

Concrete agent-safety impact (CRITICAL): an agent prompt-cached on
``compound.summary.partial_success`` / ``failed_subcommands`` (the
canonical Pattern-2 keys agents are explicitly told to branch on)
reads ``False`` / ``[]`` and assumes all four security checks ran
cleanly. For a SECURITY-REVIEW compound this is the worst-case
silent-SAFE class: an agent could read the compound verdict, see
``taint`` listed in ``sections`` (the success bucket), and assume
the codebase has been taint-checked when in fact zero symbols were
analyzed. Worst-case: the agent commits insecure code after a
``for_security_review`` that ran on a not-yet-indexed workspace.

(Note: a standalone-script probe outside pytest produced
``partial_success: True`` with ``vulns`` + ``critique`` in
``failed_subcommands`` due to those children erroring on the fresh
repo's missing state. The pytest in-process shape — all 4 children
clean except the taint internal disclosure — is the stricter agent-
safety axis and the one this module pins.)

Compare CLAUDE.md Pattern-2 §2 canonical statement:

    "Never emit verdict: 'completed' / 'SAFE' / 'non-conformant'
     when the underlying check failed or didn't run. ... subcommand
     failure must set partial_success: True AND name the failed
     subcommands."

The current behavior IS the bug pattern — same root cause as W805-F,
exposed on a different child (taint, not diagnose/affected_tests/
context) and with a different child-disclosure mechanism (state +
partial_success on a non-symbol-anchored child, rather than
resolution=unresolved on a symbol-anchored child).

PIN STRATEGY (W978 + accumulate-only constraint):

1. SMOKE (always-on): no crash + envelope shape + LAW 6 verdict +
   per-child sections present + USAGE_ERROR path absent (this
   compound accepts empty ``symbol``).
2. POSITIVE BASELINE: clean corpus → ``taint`` child does NOT
   disclose ``state: 'empty_corpus'``; partial_success on the
   compound is still True (vulns/critique fail anyway) but taint
   is NOT a partial-success child.
3. PATTERN-2 PIN (xfail-strict): on empty corpus, ``taint`` child
   discloses ``partial_success: true`` + ``state: 'empty_corpus'``
   yet the compound's ``failed_subcommands`` list does NOT include
   ``taint``. Same root fix as W805-F at mcp_server.py:4470.

The fix-forward (separate wave, bundled with W805-F): at
mcp_server.py:4470, also flip ``partial_success`` to True AND add to
``failed_subcommands`` whenever any child envelope's
``summary.partial_success`` is True (regardless of top-level
``error`` key absence). Compound bookkeeping must lift the nested
signal, not only the top-level ``error`` key. Per W978: do NOT fix
this wave; pin only.
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
    from roam.mcp_server import for_security_review  # noqa: E402
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
    symbols. The compound's four children run; the symbol-agnostic
    ``taint`` child discloses ``state: 'empty_corpus'`` while the
    ``vulns`` + ``critique`` children error out (no vuln store / no
    diff). This is the canonical empty-corpus shape the W805 sweep
    exercises.
    """
    repo = tmp_path / "empty-for-sec-rev-repo"
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
    repo = tmp_path / "clean-for-sec-rev-repo"
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
# Existence check (W978 + W907 — verify before pinning)
# ---------------------------------------------------------------------------


def test_compound_exists_or_skip():
    """``for_security_review`` is importable from ``roam.mcp_server``.

    Module-level import already gated this with ``pytest.skip``; this
    test reaffirms the precondition so a future refactor that renames
    or moves the entry point fails loudly here rather than silently
    skipping the entire module."""
    from roam import mcp_server  # noqa: F401

    assert callable(for_security_review), type(for_security_review)


# ---------------------------------------------------------------------------
# SMOKE (always-on)
# ---------------------------------------------------------------------------


class TestForSecurityReviewEmptyCorpusSmoke:
    """Pattern-2 baseline assertions on the compound envelope shape."""

    def test_empty_corpus_no_crash(self, empty_corpus):
        """``for_security_review`` must return a dict envelope, never raise."""
        r = for_security_review(symbol="", root=".")
        assert isinstance(r, dict), f"expected dict, got {type(r).__name__}"

    def test_empty_corpus_envelope_has_verdict(self, empty_corpus):
        """``summary.verdict`` is a non-empty string (Pattern-2 always-emit)."""
        r = for_security_review(symbol="", root=".")
        summary = r.get("summary") or {}
        verdict = summary.get("verdict") or ""
        assert isinstance(verdict, str) and verdict, f"summary.verdict must be non-empty string; got {verdict!r}"

    def test_empty_corpus_command_field_set(self, empty_corpus):
        """Compound envelope identifies itself as ``for-security-review``."""
        r = for_security_review(symbol="", root=".")
        assert r.get("command") == "for-security-review", r.get("command")

    def test_empty_corpus_situation_target_meta(self, empty_corpus):
        """Compound carries the situation + target meta-fields."""
        r = for_security_review(symbol="", root=".")
        s = r.get("summary") or {}
        assert s.get("situation") == "security_review", s
        # Empty symbol resolves to the broad-sweep marker.
        assert s.get("target") == "(full repo)", s

    def test_empty_corpus_law6_verdict_standalone(self, empty_corpus):
        """LAW 6: verdict is single-line ASCII; readable without other fields."""
        r = for_security_review(symbol="", root=".")
        verdict = r["summary"]["verdict"]
        assert "\n" not in verdict, f"verdict has embedded newline: {verdict!r}"
        # Compound's PARTIAL prefix uses an em-dash (Unicode U+2014) so
        # the verdict is intentionally non-ASCII. LAW 6 cares about
        # single-line; ASCII purity is enforced separately by W937.

    def test_empty_corpus_partial_success_key_present(self, empty_corpus):
        """``summary.partial_success`` is always emitted as a bool."""
        r = for_security_review(symbol="", root=".")
        s = r.get("summary") or {}
        assert "partial_success" in s, list(s.keys())
        assert isinstance(s["partial_success"], bool), type(s["partial_success"])

    def test_empty_corpus_failed_subcommands_list_present(self, empty_corpus):
        """``summary.failed_subcommands`` is always emitted as a list."""
        r = for_security_review(symbol="", root=".")
        s = r.get("summary") or {}
        assert isinstance(s.get("failed_subcommands"), list), s.get("failed_subcommands")

    def test_empty_corpus_four_child_sections_present(self, empty_corpus):
        """All four subcommand children appear in the compound envelope.

        Post the W805-OCTET seal, the `_compound_envelope` aggregator
        also routes a child carrying ``isError: true`` into
        ``failed_subcommands``. On an empty corpus the four children
        therefore split across the two buckets: ``taint`` +
        ``adversarial`` run and land in ``sections``; the ``isError``
        children (``vulns`` + ``critique``) correctly land in
        ``failed_subcommands``. The union still accounts for all four —
        and ``taint`` staying in ``sections`` is exactly the W805-F /
        W805-LL aggregator gap the xfail-strict pins below still target
        (taint self-discloses ``partial_success`` with no ``isError``
        flag, so the seal does not catch it)."""
        r = for_security_review(symbol="", root=".")
        s = r.get("summary") or {}
        sections = s.get("sections") or []
        failed = s.get("failed_subcommands") or []
        accounted = set(sections) | set(failed)
        # The union of both buckets accounts for all four children.
        for expected in ("taint", "vulns", "critique", "adversarial"):
            assert expected in accounted, f"missing {expected!r} in sections={sections} failed_subcommands={failed}"
        # taint + adversarial always run cleanly (no external state) —
        # this confirms the pins below exercise the W805-F-class
        # aggregator gap (child summary.partial_success NOT lifted)
        # and NOT a top-level-error / isError path.
        assert "taint" in sections, sections
        assert "adversarial" in sections, sections


# ---------------------------------------------------------------------------
# Clean-corpus positive baseline (W978 negative control)
# ---------------------------------------------------------------------------


class TestForSecurityReviewCleanCorpusBaseline:
    """Real symbol on a real index: the taint child runs cleanly + does
    not disclose ``state: 'empty_corpus'``. Confirms the empty-corpus
    pin below is NOT a class-wide compound defect — it is specifically
    the empty-corpus axis on the taint child."""

    def test_clean_corpus_emits_real_compound(self, clean_corpus):
        r = for_security_review(symbol="", root=".")
        assert r.get("command") == "for-security-review"
        s = r["summary"]
        # The taint + adversarial children always run (no external
        # state required). vulns + critique still fail in a freshly
        # initialized repo; that's a separate condition.
        assert "taint" in s["sections"], s["sections"]
        assert "adversarial" in s["sections"], s["sections"]

    def test_clean_corpus_taint_child_not_empty_state(self, clean_corpus):
        """Clean corpus: the taint child's summary.state is NOT
        'empty_corpus' (mirror of the empty-corpus shape, opposite
        value)."""
        r = for_security_review(symbol="", root=".")
        taint = r.get("taint") or {}
        tsum = taint.get("summary") or {}
        assert tsum.get("state") != "empty_corpus", f"clean corpus reports state='empty_corpus': {tsum!r}"


# ---------------------------------------------------------------------------
# W978 first-hypothesis sanity: taint child DOES disclose the empty-corpus
# state. This proves the next test below is pinning the COMPOUND aggregator
# gap, not a missing child-level disclosure.
# ---------------------------------------------------------------------------


class TestForSecurityReviewEmptyTaintChildDisclosesState:
    """Sanity: on empty corpus, the symbol-agnostic ``taint`` child DOES
    emit ``summary.partial_success: true`` and ``summary.state:
    'empty_corpus'``.

    If this class ever fails, the bug has shifted — the taint detector
    has regressed (or the empty-corpus state field has been renamed).
    The compound pin below ASSUMES this disclosure is in place; mutate
    the pin if these break."""

    def test_taint_child_discloses_partial_success(self, empty_corpus):
        r = for_security_review(symbol="", root=".")
        taint = r.get("taint") or {}
        tsum = taint.get("summary") or {}
        assert tsum.get("partial_success") is True, tsum

    def test_taint_child_discloses_empty_corpus_state(self, empty_corpus):
        r = for_security_review(symbol="", root=".")
        taint = r.get("taint") or {}
        tsum = taint.get("summary") or {}
        assert tsum.get("state") == "empty_corpus", tsum


# ---------------------------------------------------------------------------
# PATTERN-2 PIN (xfail-strict) — the compound aggregator gap (W805-F peer)
# ---------------------------------------------------------------------------


@pytest.mark.xfail(
    strict=True,
    reason=(
        "W805-LL REAL BUG — agent-safety CRITICAL "
        "(Pattern-2 silent fallback / Variant-D silent success on "
        "degraded child resolution). Same root cause as W805-F: "
        "_compound_envelope at src/roam/mcp_server.py:4448-4470 "
        "computes failed_subcommands ONLY from per-child top-level "
        "'error' keys. The taint child returns a structured envelope "
        "with NO top-level error but summary.partial_success=True + "
        "summary.state='empty_corpus' — i.e. self-disclosing degraded "
        "execution. The aggregator never reads the nested signal, so "
        "taint is placed in 'sections' (the success bucket) rather "
        "than in failed_subcommands. Agent-safety CRITICAL: an agent "
        "reading the compound verdict on an empty / not-yet-indexed "
        "workspace sees 'taint: no symbols to analyze' inline AND "
        "taint in sections, and may proceed assuming the codebase "
        "has been security-checked. For a for_security_review "
        "compound this is the worst-case silent-SAFE class — could "
        "let an agent commit insecure code thinking it had been "
        "taint-checked. Fix: at mcp_server.py:4470, also add child to "
        "failed_subcommands whenever child.summary.partial_success "
        "is True. Bundled with W805-F fix wave; separate from this "
        "pin per W978 + accumulate-only constraint."
    ),
)
def test_no_silent_no_security_findings_on_empty(empty_corpus):
    """Pin: compound must lift taint child's empty_corpus disclosure
    into failed_subcommands.

    The taint child correctly discloses ``state: 'empty_corpus'`` +
    ``partial_success: true``. The compound aggregator must propagate
    that signal into ``failed_subcommands``, OR an agent prompt-cached
    on ``compound.summary.failed_subcommands`` reads
    ``['vulns', 'critique']`` and proceeds with a security-review
    bundle whose taint analyzer actually ran on zero symbols.
    """
    r = for_security_review(symbol="", root=".")
    s = r["summary"]
    failed = set(s.get("failed_subcommands") or [])
    # The compound aggregator already includes the top-level-error
    # children (vulns + critique). The W805-F-class bug shape: taint
    # disclosed partial_success=True with state=empty_corpus, so it
    # MUST be in failed_subcommands too.
    assert "taint" in failed, (
        f"compound.summary.failed_subcommands={failed} omits 'taint' "
        f"despite child disclosing partial_success=True + "
        f"state='empty_corpus'. Agent-safety CRITICAL: agent reads "
        f"failed_subcommands and assumes taint ran cleanly while in "
        f"fact zero symbols were analyzed."
    )


@pytest.mark.xfail(
    strict=True,
    reason=(
        "W805-LL state-disclosure pin (Pattern-2 fix template): the "
        "compound envelope SHOULD carry an explicit summary.state "
        "field naming the empty-data shape (e.g. 'no_data' / "
        "'empty_corpus'). Today the compound emits no state key at "
        "all — only its children do. Closed-enum state-disclosure "
        "is the Pattern-2 canonical fix per CLAUDE.md §Pattern-2. "
        "Bundled with the partial_success / failed_subcommands "
        "propagation fix; separate wave per W978."
    ),
)
def test_empty_corpus_state_explicit(empty_corpus):
    """Pin: compound discloses no_data / empty_corpus state on the
    empty-corpus path. Today the key is absent."""
    r = for_security_review(symbol="", root=".")
    state = (r["summary"] or {}).get("state")
    assert state is not None, "compound.summary.state missing on empty corpus"
    # Closed-enum disclosure: one of these tokens.
    assert state in {"no_data", "not_initialized", "empty_corpus"}, (
        f"compound.summary.state={state!r} not in closed-enum"
    )


@pytest.mark.xfail(
    strict=True,
    reason=(
        "W805-LL child-partial-success propagation pin (W805-F axis). "
        "When ANY child (here: taint) discloses summary."
        "partial_success=True, the compound's failed_subcommands MUST "
        "include that child name. This is the same one-line fix as "
        "W805-F's compound_partial_success_propagation pin, exercised "
        "on a different child mechanism (state-disclosure rather than "
        "resolution=unresolved). Bundled fix wave."
    ),
)
def test_empty_corpus_child_partial_success_propagates(empty_corpus):
    """Pin (W805-F axis): every child whose summary.partial_success is
    True must be named in compound.summary.failed_subcommands."""
    r = for_security_review(symbol="", root=".")
    s = r["summary"]
    child_partials = []
    for name in s.get("sections") or []:
        child = r.get(name) or {}
        psum = child.get("summary") or {}
        if psum.get("partial_success") is True:
            child_partials.append(name)
    failed = set(s.get("failed_subcommands") or [])
    assert failed.issuperset(set(child_partials)), (
        f"compound.summary.failed_subcommands={failed} omits partial-success children {child_partials} (W805-F axis)"
    )


# ---------------------------------------------------------------------------
# Clean-corpus positive baseline — final aggregate sanity (W978 control)
# ---------------------------------------------------------------------------


def test_clean_corpus_emits_real_aggregate(clean_corpus):
    """End-to-end clean-corpus sanity: compound returns a real envelope
    with verdict + sections + situation + target. The compound is
    intentionally PARTIAL on a fresh repo (no vuln store / no staged
    diff), but the always-on children produce real output."""
    r = for_security_review(symbol="", root=".")
    assert r.get("command") == "for-security-review"
    s = r["summary"]
    # Verdict aggregates child verdicts; non-empty.
    assert isinstance(s.get("verdict"), str) and s["verdict"], s
    # situation/target carried.
    assert s.get("situation") == "security_review"
    assert s.get("target") == "(full repo)"
    # taint + adversarial sections always present (children always
    # complete enough to be placed in sections, even if they later
    # disclose partial_success).
    assert "taint" in s["sections"], s
    assert "adversarial" in s["sections"], s
