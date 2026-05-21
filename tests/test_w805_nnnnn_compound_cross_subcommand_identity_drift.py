"""W805-NNNNN -- LAW 9 cross-subcommand RESOLVED-SYMBOL identity drift on
``for_security_review``.

A NEW axis (distinct from W805-LL's aggregator-gap empty-corpus pin):
this module probes whether the compound's per-subcommand arg threading
preserves the user-supplied symbol identity END-TO-END, or whether the
compound silently drops / mistranslates the symbol on the way through to
its children.

The compound lives at ``src/roam/mcp_server.py:6473-6516`` (there is no
``cmd_for_security_review.py``; the recipe is MCP-only). Recipe order:

    sections = [
        ("taint",      _safe_run([_cr("taint")],            root)),  # no symbol
        ("vulns",      _safe_run([_cr("vulns"), "list"],    root)),  # !!
        ("critique",   _safe_run([_cr("critique")],         root)),  # no symbol
    ]
    adv_args = [_cr("adversarial")]
    if symbol:
        adv_args.append(symbol)                                       # !!
    sections.append(("adversarial", _safe_run(adv_args, root)))

LAW 9 (CLAUDE.md, canonical statement): "Coupling lives in what steps SAY,
not output format specs. Compound recipes should compose by **shared
input/output types**, not by string-templated arg passing. The
for_bug_fix / diagnose_issue divergence on handleSave resolution
(different file picked!) is exactly this bug -- they share a name
string, not a resolved symbol id."

In ``for_security_review`` only ONE child (``adversarial``) is supposed
to consume the user-supplied symbol; the other three are repo-wide
sweeps. So inter-subcommand symbol-id drift is structurally moot
(different drift axis -- the children don't all consume the same
identity to begin with). What IS observable on this compound is a
NEAR-class fault: the symbol gets passed to a subcommand whose CLI
signature doesn't accept a positional. The symbol is effectively
DROPPED on the floor AND the resulting USAGE_ERROR is misclassified
as success by the aggregator. From an agent's perspective this is
WORSE than identity drift: the symbol is silently honoured-then-
discarded, and the verdict doesn't say so.

W978 first-hypothesis probe (run BEFORE writing tests):

OBSERVED behaviour under pytest harness, ``symbol='handleAuth'`` on
a minimal single-file corpus::

    >>> r = for_security_review(symbol='handleAuth', root='.')
    >>> r['vulns']
    {'isError': True, 'error_code': 'USAGE_ERROR', ...,
     'first_error_message': "...Got unexpected extra argument (list)"}
    >>> r['adversarial']
    {'isError': True, 'error_code': 'USAGE_ERROR', ...,
     'first_error_message': "...Got unexpected extra argument (list)"}
    >>> r['summary']['failed_subcommands']
    ['vulns', 'critique']               # !! 'adversarial' absent
    >>> r['summary']['sections']
    ['taint', 'adversarial']            # !! 'adversarial' in SUCCESS bucket

Two distinct bugs surface on the same code path:

* **Bug A (LAW 9 / Pattern-1D class -- silent-success on degraded
  resolution).** ``adversarial`` does NOT accept a positional symbol
  argument (see ``src/roam/commands/cmd_adversarial.py:698`` -- the
  click decorator declares only ``--staged``, ``--range``,
  ``--severity``, ``--fail-on-critical``, ``--format``; no
  ``click.argument`` is registered). When the compound appends
  ``symbol`` to ``adv_args``, click raises::
      Got unexpected extra argument (handleAuth)
  i.e. the user's symbol is silently dropped from the analysis scope.
  The compound's docstring lies: "Optional -- when provided, scopes
  the adversarial scan to this symbol".

* **Bug B (Pattern-5 class -- compound recipe internal command-name
  drift).** ``vulns`` is a single click ``@click.command`` (NOT a
  click group), so ``_safe_run([_cr("vulns"), "list"], root)`` passes
  ``"list"`` as a positional and click rejects it::
      Got unexpected extra argument (list)
  This is the EXACT class of bug that motivated the W805-class
  ``_COMPOUND_REGISTRY`` (vuln vs vulns typo) -- the registry caught
  the COMMAND name but NOT the surface shape of that command's args.
  The recipe assumes a subcommand-style ``vulns list`` invocation
  that doesn't exist.

Pattern-1D / Pattern-2 framing: both bugs cause the compound to emit
its security-review verdict with TWO of FOUR children silently
errored at the USAGE_ERROR level. Because of the error-storm trim
substrate at ``src/roam/mcp_server.py:3580-3627``, the trimmed
envelope for ``adversarial`` shows ``first_error_message`` from the
PRIOR vulns failure (both share ``error_code='USAGE_ERROR'``), and
the aggregator at ``mcp_server.py:4448-4470`` only checks for the
top-level ``error`` key (absent from trimmed envelopes) -- so
``adversarial`` lands in the success bucket. An agent prompt-cached
on ``failed_subcommands`` and ``sections`` reads
``failed=['vulns', 'critique']`` + ``sections=['taint', 'adversarial']``
and concludes that the adversarial scan ran cleanly on
``handleAuth``, when in fact zero adversarial work was performed.

Worst-case agent impact: an agent runs ``for_security_review`` over a
suspect symbol, reads the success-coded ``adversarial`` section,
finds no challenges, and commits the change believing it has been
adversarially reviewed.

PIN STRATEGY (W978 + accumulate-only constraint per W805 sweep):

1. SMOKE (always-on): compound returns an envelope with all four
   sections regardless of how children fared.
2. CONFIRMING SANITY (always-on): on a NON-EMPTY corpus with a
   non-empty ``symbol``, the ``adversarial`` and ``vulns`` children
   both emit ``isError: True`` + ``error_code: 'USAGE_ERROR'`` --
   confirms the bugs reproduce.
3. PATTERN-1D PIN (xfail-strict): the ``adversarial`` child should
   NOT land in ``sections`` when it raised USAGE_ERROR. Today the
   error-storm trimmed envelope omits the top-level ``error`` key so
   the aggregator misclassifies it as success.
4. LAW 9 PIN (xfail-strict): the compound's verdict / sections must
   disclose that the user-supplied symbol was DROPPED rather than
   honoured. Today nothing in the compound envelope names this
   degradation -- LAW 9 silent-fallback / Pattern-2 silent-SAFE.
5. PATTERN-5 PIN (xfail-strict): ``vulns list`` is wrong; the recipe
   should call ``vulns`` (no positional) or be updated when ``vulns``
   gains a real ``list`` subcommand.

The fix-forwards (separate wave): patch
``src/roam/mcp_server.py:6499-6510`` so
   (a) the ``adversarial`` invocation either drops the symbol (matching
       the real CLI surface) or routes the symbol through a future
       ``--symbol`` option on ``adversarial`` once added, AND
   (b) the ``vulns list`` invocation collapses to plain ``vulns`` --
   plus widen ``_compound_envelope`` at line 4448-4470 to detect
   ``isError: True`` (the trimmed-envelope shape) in addition to
   top-level ``error`` keys. Per W978: pin only this wave.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
from conftest import index_in_process  # noqa: E402

# Import the compound directly. Mirrors W805-LL's import strategy --
# guarded import because ``fastmcp`` is an optional dep and some hosts
# fail with transitive import errors.
try:
    from roam.mcp_server import (
        _reset_error_storm,  # noqa: E402
        for_security_review,  # noqa: E402
    )
except Exception as _exc:  # pragma: no cover - guarded environments only
    pytest.skip(
        f"roam.mcp_server import failed: {_exc!r}; MCP compound tests require the MCP server module to be importable.",
        allow_module_level=True,
    )


# ---------------------------------------------------------------------------
# Test hygiene: disable handle-off and reset the error-storm counter so each
# test sees clean state. The error-storm coalescer at mcp_server.py:3580-3627
# carries state across calls -- without a reset, the per-test trimmed-envelope
# shape depends on test-execution order.
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _disable_handle_off(monkeypatch):
    monkeypatch.setenv("ROAM_MCP_HANDLE_KB", "0")
    yield


@pytest.fixture(autouse=True)
def _reset_storm():
    _reset_error_storm()
    yield
    _reset_error_storm()


# ---------------------------------------------------------------------------
# Fixture: minimal corpus with a SINGLE handleAuth (no ambiguity). The bug
# triggers regardless of ambiguity because the root cause is positional-arg
# rejection at the click layer, not symbol resolution. A single-file fixture
# is enough; an ambiguous-name fixture would only test the same path.
# ---------------------------------------------------------------------------


def _git_init_committed(repo: Path) -> None:
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
def single_symbol_corpus(tmp_path, monkeypatch):
    """A repo with a single ``handleAuth`` symbol in one file.

    Used as the "no-ambiguity" baseline: even without homonyms, the
    LAW 9 / Pattern-5 bugs still fire because the failure is at the
    click-arg-parse layer, not the symbol-resolution layer.
    """
    repo = tmp_path / "law9-single-corpus"
    repo.mkdir()
    (repo / ".gitignore").write_text(".roam/\n", encoding="utf-8")
    (repo / "auth.py").write_text("def handleAuth(user):\n    return user\n", encoding="utf-8")
    _git_init_committed(repo)
    monkeypatch.chdir(repo)
    out, rc = index_in_process(repo, "--force")
    assert rc == 0, f"roam index failed:\n{out}"
    return repo


@pytest.fixture
def ambiguous_symbol_corpus(tmp_path, monkeypatch):
    """A repo with ``handleAuth`` defined in 3 files.

    The OPTIMISTIC framing of this wave (per task spec): each
    subcommand might re-resolve the string ``handleAuth`` independently
    and pick a different concrete row. The pessimistic framing (and
    the one this wave actually pins): only ``adversarial`` consumes
    the symbol, so the "different subcommands pick different rows"
    drift is structurally impossible on THIS compound. The bug here
    is one layer further out: the symbol is silently DROPPED before
    any resolver runs.
    """
    repo = tmp_path / "law9-ambiguous-corpus"
    repo.mkdir()
    (repo / ".gitignore").write_text(".roam/\n", encoding="utf-8")
    (repo / "auth_a.py").write_text("def handleAuth(user):\n    return user\n", encoding="utf-8")
    (repo / "auth_b.py").write_text("def handleAuth(token):\n    return token\n", encoding="utf-8")
    (repo / "auth_c.py").write_text("def handleAuth():\n    return None\n", encoding="utf-8")
    _git_init_committed(repo)
    monkeypatch.chdir(repo)
    out, rc = index_in_process(repo, "--force")
    assert rc == 0, f"roam index failed:\n{out}"
    return repo


# ---------------------------------------------------------------------------
# SMOKE (always-on)
# ---------------------------------------------------------------------------


class TestForSecurityReviewSmoke:
    """Sanity: the compound returns a structured envelope regardless of
    how the children fare under the LAW 9 / Pattern-5 bug surface."""

    def test_compound_returns_dict(self, single_symbol_corpus):
        r = for_security_review(symbol="handleAuth", root=".")
        assert isinstance(r, dict)

    def test_compound_carries_command_field(self, single_symbol_corpus):
        r = for_security_review(symbol="handleAuth", root=".")
        assert r.get("command") == "for-security-review", r.get("command")

    def test_compound_summary_carries_target(self, single_symbol_corpus):
        """The user-supplied symbol IS preserved on the compound meta
        (this is the bait -- agents see ``target='handleAuth'`` and
        assume the underlying analysis used it)."""
        r = for_security_review(symbol="handleAuth", root=".")
        assert (r.get("summary") or {}).get("target") == "handleAuth"


# ---------------------------------------------------------------------------
# Confirming sanity (always-on): the bugs reproduce. If THESE assertions
# break in the future, the bugs may have been fixed and the xfail-strict
# pins below need to be re-evaluated.
# ---------------------------------------------------------------------------


class TestForSecurityReviewBugsReproduce:
    """The two bugs (Bug A: adversarial USAGE_ERROR / Bug B: vulns list
    USAGE_ERROR) are reproducible under the pytest harness. These
    always-on assertions exist so that any future fix to either
    underlying CLI surface (or to the compound recipe) trips a real
    failure here BEFORE the agent-safety pins below silently flip
    from xfail to pass without surfacing the change."""

    def test_vulns_child_errors_on_unexpected_list_arg(self, single_symbol_corpus):
        """Bug B: ``vulns`` is a single command (NOT a click group),
        so ``_safe_run([_cr('vulns'), 'list'], root)`` rejects ``list``
        as an unexpected positional. The aggregator places error-
        bearing children under ``r['_errors']`` (NOT at the top-level
        ``r['vulns']`` key -- that key is reserved for successful
        sections via ``result.update(sections)`` at
        mcp_server.py:4508). So we look in ``_errors`` AND
        ``failed_subcommands`` for the vulns error."""
        r = for_security_review(symbol="handleAuth", root=".")
        # The aggregator stashes errored children under _errors.
        errors = r.get("_errors") or []
        failed = (r.get("summary") or {}).get("failed_subcommands") or []
        vulns_err = next((e for e in errors if e.get("command") == "vulns"), None)
        bug_b_visible = bool(vulns_err) or "vulns" in failed
        assert bug_b_visible, (
            f"Bug B regression-window: vulns child looks healthy on a "
            f"compound that shells to 'vulns list'. Either 'vulns' "
            f"gained a 'list' subcommand (good, please update the "
            f"compound) OR the recipe was fixed (good, please remove "
            f"this assertion). _errors={errors!r} "
            f"failed_subcommands={failed!r}"
        )
        # And the error text names the unexpected positional.
        if vulns_err:
            assert "unexpected" in (vulns_err.get("error") or "").lower(), (
                f"vulns error didn't mention 'unexpected extra "
                f"argument'; underlying CLI surface may have changed. "
                f"Got: {vulns_err!r}"
            )

    def test_adversarial_child_errors_on_positional_symbol(self, single_symbol_corpus):
        """Bug A: ``adversarial`` doesn't take a positional symbol.
        The compound appends one anyway, click rejects it.

        Post W805-OCTET seal: the widened ``_compound_envelope`` aggregator
        routes the errored ``adversarial`` child into ``_errors`` +
        ``failed_subcommands`` (no longer merged to the top-level
        ``adversarial`` key). Mirror ``test_vulns_child_errors_on_
        unexpected_list_arg`` and look there."""
        r = for_security_review(symbol="handleAuth", root=".")
        errors = r.get("_errors") or []
        failed = (r.get("summary") or {}).get("failed_subcommands") or []
        adv_err = next((e for e in errors if e.get("command") == "adversarial"), None)
        bug_a_visible = bool(adv_err) or "adversarial" in failed
        assert bug_a_visible, (
            f"Bug A regression-window: adversarial child looks healthy "
            f"despite being passed a positional symbol. Either "
            f"adversarial gained a positional-arg surface (good, "
            f"please update this assertion) OR the compound was fixed "
            f"(good, please remove this assertion). "
            f"_errors={errors!r} failed_subcommands={failed!r}"
        )

    def test_adversarial_succeeds_when_symbol_is_empty(self, single_symbol_corpus):
        """Negative control: without a symbol, the compound omits the
        positional, ``adversarial`` runs cleanly. This proves the bug
        is symbol-passing, not adversarial itself."""
        r = for_security_review(symbol="", root=".")
        adv = r.get("adversarial") or {}
        # Clean run: no error envelope shape.
        assert not adv.get("error"), adv.get("error")
        assert not adv.get("isError"), adv


# ---------------------------------------------------------------------------
# Pattern-1D / silent-success aggregator pin (xfail-strict)
# ---------------------------------------------------------------------------


def test_adversarial_usage_error_propagates_to_failed_subcommands(
    single_symbol_corpus,
):
    """Pin — SEALED (W805-OCTET seal wave): a USAGE_ERROR'd child must NOT
    land in the compound's success-bucket 'sections' list, EVEN WHEN the
    error-storm coalescer trimmed the envelope to the 'isError' shape (no
    top-level 'error' key).

    The W805-TTTTT fix-forward widened ``_compound_envelope`` to classify
    any ``isError: True`` child (trimmed or not) as a failed subcommand,
    so the ``adversarial`` USAGE_ERROR now correctly surfaces in
    ``failed_subcommands``. Plain assert (was xfail-strict pre-fix)."""
    r = for_security_review(symbol="handleAuth", root=".")
    summary = r.get("summary") or {}
    sections = summary.get("sections") or []
    failed = summary.get("failed_subcommands") or []
    assert "adversarial" not in sections, (
        f"adversarial child silently classified as success despite USAGE_ERROR. sections={sections} failed={failed}"
    )
    assert "adversarial" in failed, (
        f"adversarial child should be in failed_subcommands. sections={sections} failed={failed}"
    )


def test_summary_failed_subcommands_includes_adversarial(
    single_symbol_corpus,
):
    """Pin — SEALED (W805-OCTET seal wave): failed_subcommands names every
    child whose envelope discloses error state, including trimmed isError
    envelopes.

    The W805-TTTTT widening means any child emitting ``isError: True``
    (trimmed or not) flips ``partial_success`` True AND is named in
    ``failed_subcommands``. Plain assert (was xfail-strict pre-fix)."""
    r = for_security_review(symbol="handleAuth", root=".")
    failed = set((r.get("summary") or {}).get("failed_subcommands") or [])
    # vulns + critique already land here today (top-level 'error' key
    # present); adversarial does not (trimmed-envelope shape).
    assert {"vulns", "adversarial"}.issubset(failed), (
        f"failed_subcommands={failed} missing adversarial despite "
        f"adversarial.isError=True. Trimmed-envelope leak past "
        f"aggregator."
    )


# ---------------------------------------------------------------------------
# LAW 9 / Pattern-2 silent-fallback pin: the dropped symbol is undisclosed
# ---------------------------------------------------------------------------


@pytest.mark.xfail(
    strict=True,
    reason=(
        "W805-NNNNN LAW 9 silent-fallback pin: the compound docstring "
        "promises 'when provided, scopes the adversarial scan to this "
        "symbol' but the underlying CLI doesn't accept the symbol. "
        "The symbol is silently dropped (USAGE_ERROR from click) and "
        "the compound verdict NEVER discloses that the user-supplied "
        "scope was not honoured. LAW 9 canonical statement: 'Coupling "
        "lives in what steps SAY... compound recipes should compose "
        "by shared input/output types, not by string-templated arg "
        "passing.' The compound passes the symbol as a string and "
        "assumes the receiver consumes it; the receiver rejects it; "
        "the compound's success-bucket misclassification (Pattern-1D "
        "pin above) means the rejection never surfaces. Pattern-2 "
        "fix template (CLAUDE.md): disclose the degradation -- e.g. "
        "summary.scope='full_repo (symbol dropped: adversarial does "
        "not accept --symbol)' + summary.partial_success=True. Today "
        "summary.target lies: it carries the user-supplied symbol as "
        "if it were the analysis scope. Bundled with the recipe-fix "
        "wave."
    ),
)
def test_dropped_symbol_disclosed_in_verdict(single_symbol_corpus):
    """Pin: when the user-supplied symbol is not honoured by ANY child,
    the compound verdict must disclose the drop (LAW 9 / Pattern-2)."""
    r = for_security_review(symbol="handleAuth", root=".")
    summary = r.get("summary") or {}
    verdict = (summary.get("verdict") or "").lower()
    # The disclosure could take many shapes -- ``scope_dropped``,
    # ``symbol not honoured``, ``adversarial scope fell back``, etc.
    # The pin asserts SOMETHING in the verdict names the drop, not
    # the specific wording.
    disclosure_tokens = (
        "dropped",
        "fell back",
        "not honoured",
        "not honored",
        "ignored",
        "scope",
        "not scoped",
        "broad sweep",
    )
    assert any(tok in verdict for tok in disclosure_tokens), (
        f"compound verdict {verdict!r} does NOT disclose that the "
        f"user-supplied symbol 'handleAuth' was dropped from the "
        f"adversarial scope. LAW 9 silent-fallback: agent reads "
        f"target='handleAuth' on the compound and assumes the "
        f"underlying analysis used that scope, when in fact the "
        f"adversarial child errored on the positional arg."
    )


# ---------------------------------------------------------------------------
# Negative-axis confirmation: ambiguous-symbol corpus reproduces the SAME
# bug shape. The original task hypothesised that the bug was per-subcommand
# re-resolution drift on ambiguous names; the actual bug is one layer
# further out (positional rejection). This test PROVES the ambiguity
# axis is moot by showing the bug fires identically on the ambiguous
# corpus.
# ---------------------------------------------------------------------------


class TestForSecurityReviewAmbiguousCorpusBugIsIdentical:
    """Sanity: the ambiguous-name corpus (handleAuth defined in 3 files)
    exhibits the SAME bug shape as the single-file corpus.

    Why this matters: it disconfirms the original task framing
    ('different subcommands pick different rows on ambiguous names')
    and confirms the real bug ('symbol is silently dropped before
    any resolver runs')."""

    def test_ambiguous_corpus_adversarial_errors_same_way(self, ambiguous_symbol_corpus):
        """The 3-file ambiguous corpus surfaces the SAME USAGE_ERROR
        as the single-file corpus -- so the bug is not ambiguity-
        triggered.

        Post W805-OCTET seal: the errored ``adversarial`` child is routed
        to ``_errors`` + ``failed_subcommands`` by the widened aggregator,
        not merged to the top-level ``adversarial`` key."""
        r = for_security_review(symbol="handleAuth", root=".")
        errors = r.get("_errors") or []
        failed = (r.get("summary") or {}).get("failed_subcommands") or []
        adv_err = next((e for e in errors if e.get("command") == "adversarial"), None)
        bug_a_visible = bool(adv_err) or "adversarial" in failed
        assert bug_a_visible, (
            f"Ambiguous-name corpus did NOT trigger the bug. This "
            f"means the bug is ambiguity-sensitive (rare). "
            f"_errors={errors!r} failed_subcommands={failed!r}"
        )

    def test_ambiguous_corpus_no_per_subcommand_id_drift_observable(self, ambiguous_symbol_corpus):
        """No two children consume the symbol -- so 'cross-subcommand
        identity drift' is structurally impossible on this compound.

        This is the DISCONFIRMING evidence for the original task
        framing: when only ONE subcommand receives the symbol, you
        cannot have cross-subcommand resolution drift. The bug is
        elsewhere (positional rejection, above)."""
        r = for_security_review(symbol="handleAuth", root=".")
        # Inspect each child's summary for any field naming a resolved
        # symbol or file id. Only adversarial would conceivably carry
        # one -- and adversarial errored, so it carries none.
        resolved_ids: set[str] = set()
        for child in ("taint", "vulns", "critique", "adversarial"):
            block = r.get(child) or {}
            bsum = block.get("summary") or {}
            for k in ("resolved_symbol", "resolved_file", "target_id", "symbol_id", "file_id"):
                v = bsum.get(k)
                if v:
                    resolved_ids.add(f"{child}:{k}={v}")
        # Structural fact: zero or one child resolves the symbol.
        # If >1 ever do, this test surfaces it for inspection.
        assert len(resolved_ids) <= 1, (
            f"Multiple children resolved the symbol -- LAW 9 "
            f"cross-subcommand identity-drift axis is now LIVE on "
            f"this compound. Resolved ids: {resolved_ids}"
        )
