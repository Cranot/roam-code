"""W805-RR — Empty-corpus Pattern-2 smoke for ``roam audit``.

Forty-fourth-in-batch W805 sweep. ``audit`` is the multi-section
codebase architecture audit aggregator. It composes ``health``, ``debt``,
``dead``, ``test-pyramid``, ``api``, ``stats``, ``hotspots --danger``,
and ``stale-refs`` into a single structured-JSON envelope.

This module is the sibling pin of W805-OO (``cmd_dogfood``) — ``audit``
is the immediate child the ``dogfood`` compound delegates to (see
``cmd_dogfood.py``: dogfood's compound calls ``audit`` + ``pr-analyze``).
W805-OO's drive-by surfaced that ``audit`` itself leaks ``health_score:
null`` + ``partial_success: False`` on an empty corpus — a separate
Pattern-2 axis on the ``audit`` aggregator, distinct from the dogfood
propagation pin sealed by W805-OO.

Aggregator under test (``cmd_audit.py:137-183``):

    health_score = _summary_field(health, "health_score", "score")  # → None
    ...
    summary = {
        "verdict": verdict,
        "health_score": health_score,         # ← leaks None silently
        ...
    }

The aggregator's verdict-builder (``cmd_audit.py:149-169``) ONLY adds
pressures when ``health_score < 60`` (an int/float check that skips
None). When ``health_score is None`` it falls through to the catch-all
verdict ``"AUDIT — health ?/100, ..."``. The compound NEVER lifts the
``health`` child's ``partial_success=True`` + ``state='empty_corpus'``
disclosure into its own ``summary``. Result: an agent prompt-cached on
``audit.summary.partial_success`` sees False on the empty corpus and
assumes a clean audit.

W978 first-hypothesis probe (run BEFORE pinning) — empty-repo fixture
(single empty .py file, fresh ``roam index --force``)::

    audit.summary.verdict          = "AUDIT — health ?/100, 2 files, 0 symbols, 0 public-API symbols"
    audit.summary.health_score     = None                # SILENT-NULL LEAK
    audit.summary.partial_success  = False               # SILENT-SAFE BUG
    audit.summary.state            = None                # MISSING

    child sections['health'].summary.partial_success      = True             # DISCLOSED
    child sections['health'].summary.state                = 'empty_corpus'   # DISCLOSED
    child sections['test_pyramid'].summary.partial_success = True            # DISCLOSED
    child sections['test_pyramid'].summary.state           = 'no_test_files' # DISCLOSED

W978 negative control (clean corpus with 1 file, 2 symbols)::

    audit.summary.verdict       = "AUDIT — pressures: 1 danger-zone file(s)"
    audit.summary.health_score  = 99
    audit.summary.symbol_total  = 2

Proves the bug is empty-corpus-specific and not a global regression.

Same Pattern-2 / Variant-D root cause as W805-F/KK/LL/OO: a child
explicitly self-discloses degraded execution (partial_success=True +
state='empty_corpus'), the aggregator reads ONLY the legacy sentinel
shape (``_subcommand_failed`` / explicit numeric check on the lifted
field), and the compound emits the auto-injected ``partial_success:
False`` default from ``json_envelope`` while children disclosed True.

Concrete agent-safety impact: ``audit`` is the canonical "one-shot
architecture audit" surface — the documented PR Replay backbone
(``cmd_audit.py:5-6``). An agent invoking ``roam --json audit`` on a
newly-bootstrapped repo (where the indexer has run but the codebase is
still empty / scaffolded / pre-first-commit) reads ``health_score:
null`` + ``partial_success: false`` and either crashes on null
arithmetic or assumes a clean audit. Both outcomes break agent-readiness
contracts.

PIN STRATEGY (W978 + accumulate-only constraint):

1. SMOKE (always-on): no crash + envelope shape + LAW 6 verdict +
   sections present + ``partial_success`` key is bool.
2. POSITIVE BASELINE: clean corpus → real ``health_score`` (numeric).
3. PATTERN-2 PINS (xfail-strict):
   (a) ``test_no_silent_null_health_on_empty`` — health_score=None
       AND partial_success=False is the silent-null-leak signal.
   (b) ``test_empty_corpus_state_explicit`` — compound state missing.
   (c) ``test_empty_corpus_partial_success_set_to_true`` — compound
       must propagate child partial_success when any child disclosed.

The fix-forward (separate wave): at ``cmd_audit.py:171``, also flip
``partial_success: True`` AND emit ``summary.state`` (closed enum:
``empty_corpus`` / ``no_data`` / ``not_initialized``) whenever any
child envelope's ``summary.partial_success`` is True OR when
``health_score is None`` and ``symbol_total == 0``. Per W978: do NOT
fix this wave; pin only.

W805 sweep update: 23 / 34 with this pin (W805-RR).
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
    symbols. The ``health`` child reports ``health_score=None`` +
    ``state='empty_corpus'``; the ``test_pyramid`` child reports
    ``state='no_test_files'``. The compound's silent-null axis: it
    surfaces ``health_score=None`` and ``partial_success=False``
    without disclosing any of this state.
    """
    repo = tmp_path / "empty-audit-repo"
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
    repo = tmp_path / "clean-audit-repo"
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


def _invoke_audit_json(extra_args: list[str] | None = None) -> dict:
    """Invoke ``roam --json audit`` and parse the JSON envelope."""
    from roam.cli import cli

    args = ["--json", "audit"]
    if extra_args:
        args.extend(extra_args)
    runner = CliRunner()
    result = runner.invoke(cli, args, catch_exceptions=False)
    assert result.exit_code == 0, f"audit failed: rc={result.exit_code}\n{result.output[:2000]}"
    return json.loads(result.output)


# ---------------------------------------------------------------------------
# Existence check (W978 + W907 — verify before pinning)
# ---------------------------------------------------------------------------


def test_command_exists_or_skip():
    """``audit`` is registered in the CLI command table."""
    from roam.cli import _COMMANDS

    assert "audit" in _COMMANDS, (
        f"audit missing from cli._COMMANDS — module may have been "
        f"renamed or deleted. Available: {sorted(_COMMANDS.keys())[:20]}..."
    )
    module_path, attr = _COMMANDS["audit"]
    assert module_path == "roam.commands.cmd_audit", module_path
    assert attr == "audit", attr


# ---------------------------------------------------------------------------
# SMOKE (always-on)
# ---------------------------------------------------------------------------


class TestAuditEmptyCorpusSmoke:
    """Pattern-2 baseline assertions on the audit envelope shape."""

    def test_empty_corpus_no_crash(self, empty_corpus):
        """``roam --json audit`` returns a parseable dict envelope."""
        env = _invoke_audit_json()
        assert isinstance(env, dict), f"expected dict, got {type(env).__name__}"

    def test_empty_corpus_envelope_has_verdict(self, empty_corpus):
        """``summary.verdict`` is a non-empty string (Pattern-2 always-emit)."""
        env = _invoke_audit_json()
        summary = env.get("summary") or {}
        verdict = summary.get("verdict") or ""
        assert isinstance(verdict, str) and verdict, f"summary.verdict must be non-empty string; got {verdict!r}"

    def test_empty_corpus_command_field_set(self, empty_corpus):
        """Envelope identifies itself as the ``audit`` command."""
        env = _invoke_audit_json()
        assert env.get("command") == "audit", env.get("command")

    def test_empty_corpus_law6_verdict_standalone(self, empty_corpus):
        """LAW 6: verdict is a single line, readable standalone."""
        env = _invoke_audit_json()
        verdict = env["summary"]["verdict"]
        assert "\n" not in verdict, f"verdict has embedded newline: {verdict!r}"
        # Verdict starts with the canonical "AUDIT — " prefix per
        # cmd_audit.py:163/165. The em-dash here is a U+2014 in source;
        # W937 covers em-dash discipline elsewhere. We only assert single
        # line + non-empty here.
        assert verdict.startswith("AUDIT"), f"verdict prefix unexpected: {verdict!r}"

    def test_empty_corpus_partial_success_set(self, empty_corpus):
        """Smoke baseline (NOT a bug pin): ``summary.partial_success`` is
        always emitted as a bool.

        W978 re-probe finding: ``json_envelope`` at
        ``src/roam/output/formatter.py:975-976`` auto-injects
        ``summary.partial_success: False`` when the caller omits it, so
        the always-emit axis is already satisfied by the substrate. The
        BUG is the VALUE on the empty-corpus path (False when the
        health child disclosed True), pinned in the xfail block below.
        """
        env = _invoke_audit_json()
        s = env.get("summary") or {}
        assert "partial_success" in s, list(s.keys())
        assert isinstance(s["partial_success"], bool), type(s["partial_success"])

    def test_empty_corpus_sections_present(self, empty_corpus):
        """``sections`` is always a dict and contains the canonical 7 children."""
        env = _invoke_audit_json()
        sections = env.get("sections") or {}
        assert isinstance(sections, dict), type(sections)
        # cmd_audit.py:185-193 composes exactly these 7 sections.
        assert "health" in sections, list(sections.keys())
        assert "test_pyramid" in sections, list(sections.keys())


# ---------------------------------------------------------------------------
# W978 first-hypothesis sanity: health + test_pyramid children DO disclose.
# This proves the next test below is pinning the COMPOUND aggregator gap,
# not a missing child-level disclosure.
# ---------------------------------------------------------------------------


class TestAuditEmptyChildrenDiscloseState:
    """Sanity: on empty corpus, the ``health`` and ``test_pyramid`` children
    DO emit ``summary.partial_success: true`` + a ``summary.state`` value.

    If this class ever fails, the bug has shifted — a child detector has
    regressed (or the state field renamed). The compound pin below
    ASSUMES this disclosure is in place."""

    def test_health_child_discloses_partial_success(self, empty_corpus):
        env = _invoke_audit_json()
        sections = env.get("sections") or {}
        h = sections.get("health") or {}
        hsum = h.get("summary") or {}
        assert hsum.get("partial_success") is True, f"health child summary missing partial_success=True: {hsum}"

    def test_health_child_discloses_empty_corpus_state(self, empty_corpus):
        env = _invoke_audit_json()
        sections = env.get("sections") or {}
        h = sections.get("health") or {}
        hsum = h.get("summary") or {}
        assert hsum.get("state") == "empty_corpus", f"health child summary state != 'empty_corpus': {hsum}"

    def test_test_pyramid_child_discloses_no_test_files(self, empty_corpus):
        env = _invoke_audit_json()
        sections = env.get("sections") or {}
        tp = sections.get("test_pyramid") or {}
        tpsum = tp.get("summary") or {}
        assert tpsum.get("partial_success") is True, f"test_pyramid child summary missing partial_success=True: {tpsum}"
        assert tpsum.get("state") == "no_test_files", f"test_pyramid child summary state != 'no_test_files': {tpsum}"


# ---------------------------------------------------------------------------
# PATTERN-2 PINS (xfail-strict) — the aggregator gap (W805-RR REAL BUGS)
# ---------------------------------------------------------------------------


@pytest.mark.xfail(
    strict=True,
    reason=(
        "W805-RR REAL BUG (silent-null-leak axis) — Pattern-2 null "
        "leak. On empty corpus, cmd_audit.py:138 reads "
        "health_score=None from the health child (the child correctly "
        "discloses partial_success=True + state='empty_corpus'), then "
        "cmd_audit.py:173 surfaces health_score=None onto the compound "
        "summary while cmd_audit.py:171/176 leaves partial_success at "
        "the auto-injected default of False (json_envelope substrate "
        "guarantee at formatter.py:975-976). An agent reading "
        "audit.summary.health_score gets a null value with no "
        "indication that the underlying corpus produced no symbols. "
        "Agent-safety: audit is the documented PR Replay backbone "
        "(cmd_audit.py:5-6); a CI runner consuming audit.summary."
        "health_score on a freshly-bootstrapped repo either crashes on "
        "null arithmetic (e.g. `if health_score < 60`) or assumes "
        "clean audit. Fix: at cmd_audit.py:171, when health_score is "
        "None AND symbol_total == 0, set partial_success=True AND emit "
        "summary.state='empty_corpus'. Per W978: do NOT fix this wave; "
        "pin only."
    ),
)
def test_no_silent_null_health_on_empty(empty_corpus):
    """Pin: compound must NOT leak ``health_score: None`` silently.

    Either the compound emits a defensible numeric health score on the
    empty corpus (e.g. 100 with state='empty_corpus'), OR it sets
    ``partial_success: True`` so callers know the null is a degraded
    state-disclosure marker rather than a clean numeric absence.
    """
    env = _invoke_audit_json()
    s = env["summary"]
    health_score = s.get("health_score")
    partial = s.get("partial_success")
    # The bug shape: health_score is None AND partial_success is False.
    # The fix shape (either branch is acceptable):
    #   (a) health_score becomes numeric; OR
    #   (b) partial_success becomes True (state-disclosure path).
    is_silent_null = (health_score is None) and (partial is False)
    assert not is_silent_null, (
        f"silent null-leak on audit empty-corpus path: "
        f"health_score={health_score!r}, partial_success={partial!r}. "
        f"Either set health_score numerically or flip partial_success=True."
    )


@pytest.mark.xfail(
    strict=True,
    reason=(
        "W805-RR REAL BUG (state-disclosure pin, Pattern-2 fix "
        "template) — the audit compound envelope SHOULD carry an "
        "explicit summary.state field naming the empty-data shape "
        "(e.g. 'empty_corpus' / 'no_data' / 'not_initialized'). Today "
        "the compound emits no state key at all — only its children "
        "do (health → 'empty_corpus', test_pyramid → 'no_test_files'). "
        "Closed-enum state-disclosure is the Pattern-2 canonical fix "
        "per CLAUDE.md §Pattern-2. The compound at cmd_audit.py:171-"
        "183 builds summary but never inspects child-section state "
        "fields to lift the disclosure. Fix-forward: when any child "
        "section's summary.state is set, lift the most pressing one "
        "to compound.summary.state. Bundled with the partial_success "
        "propagation fix; separate wave per W978."
    ),
)
def test_empty_corpus_state_explicit(empty_corpus):
    """Pin: compound discloses empty_corpus / no_data state on the
    empty-corpus path. Today the key is absent on the compound."""
    env = _invoke_audit_json()
    state = (env["summary"] or {}).get("state")
    assert state is not None, "compound.summary.state missing on empty corpus"
    assert state in {"no_data", "not_initialized", "empty_corpus", "no_changes"}, (
        f"compound.summary.state={state!r} not in closed-enum"
    )


@pytest.mark.xfail(
    strict=True,
    reason=(
        "W805-RR REAL BUG (partial_success propagation axis) — same "
        "root cause as W805-F/KK/LL/OO on the audit aggregator. On "
        "empty corpus the health child discloses partial_success=True "
        "+ state='empty_corpus' and the test_pyramid child discloses "
        "partial_success=True + state='no_test_files'. The cmd_audit "
        "aggregator at cmd_audit.py:171-183 NEVER inspects child "
        "summary.partial_success — it only computes top-level fields "
        "(verdict, health_score, debt_total, ...). The auto-injected "
        "compound.summary.partial_success defaults to False (json_"
        "envelope substrate guarantee at formatter.py:975-976), so "
        "the compound emits partial_success=False while two children "
        "disclosed True. Fix: at cmd_audit.py:171, also flip "
        "partial_success=True whenever any child section's "
        "summary.partial_success is True. Per W978: do NOT fix this "
        "wave; pin only. Bundled with W805-F/KK/LL/OO propagation fix."
    ),
)
def test_empty_corpus_partial_success_set_to_true(empty_corpus):
    """Pin: compound lifts child partial_success disclosure.

    health + test_pyramid children both disclose partial_success=True
    on empty corpus. The compound must propagate that signal into its
    own summary.partial_success, OR an agent prompt-cached on the
    compound's value reads False and assumes a clean audit.
    """
    env = _invoke_audit_json()
    s = env["summary"]
    assert s.get("partial_success") is True, (
        f"compound.summary.partial_success={s.get('partial_success')!r} "
        f"despite ≥2 child sections (health, test_pyramid) disclosing "
        f"partial_success=True. Agent-safety: agent reads partial_success "
        f"and assumes audit ran cleanly while in fact zero symbols were "
        f"indexed and zero test files were detected."
    )


# ---------------------------------------------------------------------------
# Clean-corpus positive baseline (W978 negative control)
# ---------------------------------------------------------------------------


def test_clean_corpus_emits_real_audit(clean_corpus):
    """End-to-end clean-corpus sanity: audit returns a real envelope
    with verdict + sections + numeric health_score. Proves the bug is
    empty-corpus-specific, not a regression of the happy path."""
    env = _invoke_audit_json()
    assert env.get("command") == "audit"
    s = env["summary"]
    assert isinstance(s.get("verdict"), str) and s["verdict"], s
    # On a non-empty corpus the audit compound surfaces a real numeric
    # health_score from the health child.
    health_score = s.get("health_score")
    assert isinstance(health_score, (int, float)), (
        f"audit on clean corpus shows non-numeric health_score: {health_score!r}"
    )
    # And reports a real symbol count.
    assert s.get("symbol_total", 0) > 0, f"audit on clean corpus shows symbol_total={s.get('symbol_total')!r}"
