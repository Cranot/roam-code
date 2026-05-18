"""W805-OO — Empty-corpus Pattern-2 smoke for ``roam dogfood``.

Forty-first-in-batch W805 sweep. ``dogfood`` is the top-of-funnel
compound aggregator: it composes ``audit`` + ``pr-analyze`` (against
uncommitted diff) + (optionally) ``audit-trail-conformance-check``
into a single envelope, exposed via the standalone Click command at
``src/roam/commands/cmd_dogfood.py``. Unlike the MCP-only compound
trinity (W805-F ``for_bug_fix``, W805-KK ``for_refactor``, W805-LL
``for_security_review``), ``dogfood`` lives in the CLI surface and
has its OWN aggregator at ``cmd_dogfood.py:191`` — it does NOT share
the ``_compound_envelope`` substrate at ``mcp_server.py:4448-4470``.
Same Pattern-2 bug class on a DIFFERENT aggregator.

Aggregator under test (``cmd_dogfood.py:178-215``):

    failed_sections = sorted(
        k for k, v in sections.items()
        if isinstance(v, dict) and v.get("_subcommand_failed")
    )
    ...
    if failed_sections:
        summary["partial_success"] = True
        summary["failed_sections"] = failed_sections

The aggregator only flips ``partial_success`` to True when a child's
top-level ``_subcommand_failed`` sentinel is set (JSON-parse failure
or non-zero exit captured by ``_run_subcommand`` at lines 35-56).
Children that succeed in producing a parseable JSON envelope BUT
self-disclose ``summary.partial_success: True`` + ``summary.state:
'<degraded>'`` are silently treated as success — same root cause as
W805-F/KK/LL but in the standalone CLI compound rather than the MCP
shared aggregator.

W978 first-hypothesis probe (run BEFORE writing tests) — empty-repo
fixture (single empty .py file, fresh ``roam index --force``),
invocation ``roam --json dogfood --no-audit-trail`` (avoid touching
``.roam/audit-trail.jsonl`` per accumulate-only constraint)::

    compound.summary.partial_success = False                # SILENT-SAFE BUG
    compound.summary.failed_sections = None                 # SILENT-SAFE BUG
    compound.summary.state           = None                 # MISSING
    compound.summary.sections_run    = ['audit', 'pr_analyze']

    child sections['pr_analyze'].summary.partial_success = True   # DISCLOSED
    child sections['pr_analyze'].summary.state           = 'no_changes'  # DISCLOSED
    child sections['audit'].summary.partial_success      = False  # NOT-DISCLOSED
    child sections['audit'].summary.health_score         = None   # implicit empty signal

The canonical W805-F-class aggregator bug, exercised on the
``cmd_dogfood`` standalone aggregator: the ``pr_analyze`` child
correctly self-discloses ``state: 'no_changes'`` +
``partial_success: True`` (no diff to analyze on an empty corpus —
this is a structured degraded-execution signal, NOT a sentinel
``_subcommand_failed=True``). The aggregator reads only
``_subcommand_failed`` so it lifts nothing onto the compound; the
compound emits ``partial_success: False`` while a child analyzer
ran on a degraded input.

Concrete agent-safety impact: an agent prompt-cached on
``compound.summary.partial_success`` reads False on the first-touch
``roam dogfood`` invocation against a freshly-indexed empty / not-
yet-fully-indexed workspace and assumes the v2 stack (audit +
pr-analyze) ran cleanly. dogfood is explicitly designed as the
"first-touch demo / new-user onboarding" surface per the module
docstring — silent SAFE on this command is the exact path a new
agent or user will hit first.

(Note: a separate concern on the ``audit`` child — it self-emits
``partial_success: False`` despite ``health_score: None`` on an
empty corpus. That is the ``cmd_audit`` Pattern-2 axis, NOT pinned
here. This module pins only the compound's failure to lift the
``pr_analyze`` child's explicit disclosure.)

Compare CLAUDE.md Pattern-2 §2 canonical statement:

    "Never emit verdict: 'completed' / 'SAFE' / 'non-conformant'
     when the underlying check failed or didn't run. ... subcommand
     failure must set partial_success: True AND name the failed
     subcommands."

The current behavior IS the bug pattern — same root cause as
W805-F/KK/LL, exposed on the CLI-side standalone aggregator on a
different child mechanism (state='no_changes' from pr-analyze rather
than state='empty_corpus' from taint).

PIN STRATEGY (W978 + accumulate-only constraint):

1. SMOKE (always-on): no crash + envelope shape + LAW 6 verdict +
   sections_run present + warnings_out absent on clean path.
2. POSITIVE BASELINE: clean corpus → audit child reports a real
   health_score (or partial_success without state='no_changes'),
   sections_run includes both 'audit' and 'pr_analyze'.
3. PATTERN-2 PIN (xfail-strict): on empty corpus, ``pr_analyze``
   child discloses ``partial_success: true`` + ``state:
   'no_changes'`` yet the compound's ``partial_success`` stays
   False AND ``failed_sections`` stays missing. Same root fix
   shape as W805-F/KK/LL on a sibling aggregator.

The fix-forward (separate wave): at ``cmd_dogfood.py:191``, also
flip ``partial_success`` to True AND include child name in
``failed_sections`` whenever any child envelope's
``summary.partial_success`` is True (regardless of
``_subcommand_failed`` absence). Per W978: do NOT fix this wave;
pin only.

W805 sweep update: 22 / 33 with this pin (W805-OO).
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from click.testing import CliRunner

sys.path.insert(0, str(Path(__file__).parent))
from conftest import index_in_process  # noqa: E402

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
    symbols. The ``audit`` child runs with health_score=None; the
    ``pr-analyze`` child runs but discloses ``state: 'no_changes'``
    + ``partial_success: True`` (no uncommitted diff on a freshly-
    committed repo). The canonical empty-corpus shape this W805
    sweep exercises.

    NOTE: uses ``--no-audit-trail`` at invocation time to prevent
    writes to ``.roam/audit-trail.jsonl`` per the accumulate-only +
    "DO NOT write to internal/dogfood/" constraints. The audit-trail
    branch is also skipped because ``DEFAULT_AUDIT_TRAIL_PATH``
    doesn't exist on the fresh repo.
    """
    repo = tmp_path / "empty-dogfood-repo"
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
    """A git repo with a real Python function for happy-path coverage."""
    repo = tmp_path / "clean-dogfood-repo"
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


def _invoke_dogfood_json(extra_args: list[str] | None = None) -> dict:
    """Invoke ``roam --json dogfood --no-audit-trail`` and parse envelope.

    The ``--no-audit-trail`` flag is mandatory in this module — without
    it, dogfood appends to ``.roam/audit-trail.jsonl`` and then runs
    the audit-trail-conformance-check subcommand. We never want either
    side effect from a pin-only test module.
    """
    from roam.cli import cli

    args = ["--json", "dogfood", "--no-audit-trail"]
    if extra_args:
        args.extend(extra_args)
    runner = CliRunner()
    result = runner.invoke(cli, args, catch_exceptions=False)
    assert result.exit_code == 0, f"dogfood failed: rc={result.exit_code}\n{result.output[:2000]}"
    return json.loads(result.output)


# ---------------------------------------------------------------------------
# Existence check (W978 + W907 — verify before pinning)
# ---------------------------------------------------------------------------


def test_command_exists_or_skip():
    """``dogfood`` is registered in the CLI command table."""
    from roam.cli import _COMMANDS

    assert "dogfood" in _COMMANDS, (
        f"dogfood missing from cli._COMMANDS — module may have been "
        f"renamed or deleted. Available: {sorted(_COMMANDS.keys())[:20]}..."
    )
    module_path, attr = _COMMANDS["dogfood"]
    assert module_path == "roam.commands.cmd_dogfood", module_path
    assert attr == "dogfood", attr


# ---------------------------------------------------------------------------
# SMOKE (always-on)
# ---------------------------------------------------------------------------


class TestDogfoodEmptyCorpusSmoke:
    """Pattern-2 baseline assertions on the compound envelope shape."""

    def test_empty_corpus_no_crash(self, empty_corpus):
        """``roam --json dogfood`` returns a parseable dict envelope."""
        env = _invoke_dogfood_json()
        assert isinstance(env, dict), f"expected dict, got {type(env).__name__}"

    def test_empty_corpus_envelope_has_verdict(self, empty_corpus):
        """``summary.verdict`` is a non-empty string (Pattern-2 always-emit)."""
        env = _invoke_dogfood_json()
        summary = env.get("summary") or {}
        verdict = summary.get("verdict") or ""
        assert isinstance(verdict, str) and verdict, f"summary.verdict must be non-empty string; got {verdict!r}"

    def test_empty_corpus_command_field_set(self, empty_corpus):
        """Envelope identifies itself as the ``dogfood`` command."""
        env = _invoke_dogfood_json()
        assert env.get("command") == "dogfood", env.get("command")

    def test_empty_corpus_law6_verdict_standalone(self, empty_corpus):
        """LAW 6: verdict is a single line, readable standalone."""
        env = _invoke_dogfood_json()
        verdict = env["summary"]["verdict"]
        assert "\n" not in verdict, f"verdict has embedded newline: {verdict!r}"
        # Verdict must be ASCII per W937 (no em-dashes; cmd_dogfood
        # composes its verdict from parts joined by ' \xc2\xb7 ' (middle-dot)
        # — middle-dot is U+00B7. W937 explicitly targets em-dashes
        # (U+2014), NOT middle-dots; so we assert single-line only.

    def test_empty_corpus_partial_success_key_present(self, empty_corpus):
        """``summary.partial_success`` is emitted only on the partial path.

        Today the aggregator at ``cmd_dogfood.py:213`` conditionally
        injects ``partial_success: True`` only when ``failed_sections``
        is non-empty (or ``warnings_out`` has markers). When neither
        condition fires the key is ABSENT — this is also the Pattern-2
        always-emit-the-key axis but it is a SEPARATE pin from the
        propagation pin below; here we only test that IF the key is
        present, it is a bool.
        """
        env = _invoke_dogfood_json()
        s = env.get("summary") or {}
        if "partial_success" in s:
            assert isinstance(s["partial_success"], bool), type(s["partial_success"])

    def test_empty_corpus_sections_run_present(self, empty_corpus):
        """``summary.sections_run`` is always a list."""
        env = _invoke_dogfood_json()
        s = env.get("summary") or {}
        assert isinstance(s.get("sections_run"), list), s.get("sections_run")
        # The audit + pr_analyze sections always run (no external state
        # required). The conformance section is skipped because
        # ``DEFAULT_AUDIT_TRAIL_PATH`` doesn't exist on the fresh repo.
        sr = s["sections_run"]
        assert "audit" in sr, f"missing 'audit' in {sr}"
        assert "pr_analyze" in sr, f"missing 'pr_analyze' in {sr}"


# ---------------------------------------------------------------------------
# W978 first-hypothesis sanity: pr_analyze child DOES disclose state.
# This proves the next test below is pinning the COMPOUND aggregator gap,
# not a missing child-level disclosure.
# ---------------------------------------------------------------------------


class TestDogfoodEmptyPrAnalyzeChildDisclosesState:
    """Sanity: on empty corpus, the ``pr_analyze`` child DOES emit
    ``summary.partial_success: true`` + ``summary.state: 'no_changes'``.

    If this class ever fails, the bug has shifted — the pr-analyze
    detector has regressed (or the no_changes state field has been
    renamed). The compound pin below ASSUMES this disclosure is in
    place; mutate the pin if these break."""

    def test_pr_analyze_child_discloses_partial_success(self, empty_corpus):
        env = _invoke_dogfood_json()
        sections = env.get("sections") or {}
        pr = sections.get("pr_analyze") or {}
        pr_sum = pr.get("summary") or {}
        assert pr_sum.get("partial_success") is True, f"pr_analyze child summary missing partial_success=True: {pr_sum}"

    def test_pr_analyze_child_discloses_no_changes_state(self, empty_corpus):
        env = _invoke_dogfood_json()
        sections = env.get("sections") or {}
        pr = sections.get("pr_analyze") or {}
        pr_sum = pr.get("summary") or {}
        assert pr_sum.get("state") == "no_changes", f"pr_analyze child summary missing state='no_changes': {pr_sum}"


# ---------------------------------------------------------------------------
# PATTERN-2 PIN (xfail-strict) — the compound aggregator gap (W805-F peer)
# ---------------------------------------------------------------------------


@pytest.mark.xfail(
    strict=True,
    reason=(
        "W805-OO REAL BUG — Pattern-2 silent fallback / Variant-D "
        "silent success on degraded child resolution. Same root cause "
        "as W805-F/KK/LL but on the CLI standalone aggregator at "
        "src/roam/commands/cmd_dogfood.py:191 (NOT the MCP shared "
        "_compound_envelope at mcp_server.py:4448-4470). The aggregator "
        "computes failed_sections ONLY from per-child top-level "
        "_subcommand_failed sentinels (JSON-parse failure or non-zero "
        "exit). The pr_analyze child returns a structured envelope "
        "with NO _subcommand_failed key but summary.partial_success=True "
        "+ summary.state='no_changes' — i.e. self-disclosing degraded "
        "execution. The aggregator never reads the nested signal, so "
        "pr_analyze stays in sections (the implicit success bucket) "
        "and the compound emits partial_success=False (or absent) "
        "while a child analyzer ran on a degraded input. Agent-safety "
        "impact: dogfood is the documented first-touch / new-user "
        "demo surface; an agent or user reading "
        "compound.summary.partial_success on the very first "
        "invocation against a freshly-indexed not-yet-populated "
        "workspace sees False and assumes the v2 stack ran cleanly. "
        "Fix: at cmd_dogfood.py:191, also flip partial_success=True "
        "AND include child name in failed_sections whenever child."
        "summary.partial_success is True. Bundled with W805-F/KK/LL "
        "fix wave; separate from this pin per W978 + accumulate-only."
    ),
)
def test_no_silent_no_findings_on_empty(empty_corpus):
    """Pin: compound must lift pr_analyze child's no_changes disclosure
    into partial_success + failed_sections.

    The pr_analyze child correctly discloses ``state: 'no_changes'`` +
    ``partial_success: true``. The compound aggregator must propagate
    that signal into ``summary.partial_success`` + ``summary.
    failed_sections``, OR an agent prompt-cached on
    ``compound.summary.partial_success`` reads ``False`` and proceeds
    with a first-touch demo whose pr-analyze actually ran on zero
    diff lines.
    """
    env = _invoke_dogfood_json()
    s = env["summary"]
    assert s.get("partial_success") is True, (
        f"compound.summary.partial_success={s.get('partial_success')!r} "
        f"despite child pr_analyze disclosing partial_success=True + "
        f"state='no_changes'. Agent-safety: agent reads partial_success "
        f"and assumes pr-analyze ran cleanly while in fact zero diff "
        f"lines were analyzed."
    )
    failed = s.get("failed_sections") or []
    assert "pr_analyze" in failed, (
        f"compound.summary.failed_sections={failed} omits 'pr_analyze' "
        f"despite child disclosing partial_success=True + state='no_changes'."
    )


@pytest.mark.xfail(
    strict=True,
    reason=(
        "W805-OO state-disclosure pin (Pattern-2 fix template): the "
        "compound envelope SHOULD carry an explicit summary.state "
        "field naming the empty-data shape (e.g. 'no_data' / "
        "'empty_corpus' / 'no_changes'). Today the compound emits no "
        "state key at all — only its children do. Closed-enum state-"
        "disclosure is the Pattern-2 canonical fix per CLAUDE.md "
        "§Pattern-2. Bundled with the partial_success / "
        "failed_sections propagation fix; separate wave per W978."
    ),
)
def test_empty_corpus_state_explicit(empty_corpus):
    """Pin: compound discloses no_data / no_changes state on the empty-
    corpus path. Today the key is absent on the compound."""
    env = _invoke_dogfood_json()
    state = (env["summary"] or {}).get("state")
    assert state is not None, "compound.summary.state missing on empty corpus"
    assert state in {"no_data", "not_initialized", "empty_corpus", "no_changes"}, (
        f"compound.summary.state={state!r} not in closed-enum"
    )


def test_empty_corpus_partial_success_set(empty_corpus):
    """Smoke baseline (NOT a bug pin): ``summary.partial_success`` is
    always emitted as a bool.

    W978 re-probe finding: ``json_envelope`` at
    ``src/roam/output/formatter.py:975-976`` auto-injects
    ``summary.partial_success: False`` when the caller omits it, so
    the always-emit axis is already satisfied by the substrate. The
    BUG is the VALUE on the empty-corpus path (False when the
    pr_analyze child disclosed True), not the key's presence — that
    pin is ``test_no_silent_no_findings_on_empty`` above. This test
    is kept as the smoke baseline asserting the substrate guarantee
    still holds for dogfood.
    """
    env = _invoke_dogfood_json()
    s = env.get("summary") or {}
    assert "partial_success" in s, list(s.keys())
    assert isinstance(s["partial_success"], bool), type(s["partial_success"])


# ---------------------------------------------------------------------------
# Clean-corpus positive baseline (W978 negative control)
# ---------------------------------------------------------------------------


def test_clean_corpus_emits_real_aggregate(clean_corpus):
    """End-to-end clean-corpus sanity: dogfood returns a real envelope
    with verdict + sections + non-empty health signal. The compound is
    still PARTIAL on a fresh repo (no diff for pr-analyze), but the
    audit child produces real output."""
    env = _invoke_dogfood_json()
    assert env.get("command") == "dogfood"
    s = env["summary"]
    assert isinstance(s.get("verdict"), str) and s["verdict"], s
    assert "audit" in s.get("sections_run", []), s
    assert "pr_analyze" in s.get("sections_run", []), s
    # On a non-empty corpus the audit child computes a real health_score
    # (or a defensible numeric); the dogfood compound surfaces that.
    sections = env.get("sections") or {}
    audit = sections.get("audit") or {}
    asum = audit.get("summary") or {}
    # Real corpus has 2 symbols; symbol count > 0 — proves audit ran
    # and produced real signal rather than the empty-corpus shape.
    assert asum.get("symbol_total", 0) > 0 or asum.get("health_score") is not None, (
        f"audit child on clean corpus shows no signal: {asum}"
    )
