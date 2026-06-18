"""W805-PP - empty-corpus Pattern-2 smoke for ``roam dashboard``.

Forty-second-in-batch of the W805 Pattern-2 audit sweep. Sibling of:

- W805-833 (cmd_health)   REAL BUG (silent "HEALTHY 100/100" on empty corpus)
- W805-836 (cmd_doctor)   REAL BUG (silent "all checks passed" aggregator)

``cmd_dashboard`` is the natural third sibling — a flagship multi-signal
aggregator that composes ``health`` + ``hotspots`` + ``bus-factor`` +
``dead`` + ``vibe-check`` into a single concise view. The W805 sweep was
specifically designed to catch this class of "aggregator says SAFE on
an empty index" Pattern-2 silent-fallback bug; cmd_health and cmd_doctor
both pinned CRITICAL, and cmd_dashboard composes the same upstream
signals.

W978 first-hypothesis re-run BEFORE writing any test
============================================================

``cmd_dashboard`` DOES NOT delegate to ``_compound_envelope`` (the MCP
aggregator behind ``for_bug_fix``/``for_refactor``/``pr_prep``). It is a
single-process direct CLI command that:

1. Calls ``ensure_index()`` + ``open_db(readonly=True)``
2. Queries ``files``, ``symbols``, ``edges``, ``clusters``, ``file_stats``
   directly via SQL — no per-subcommand recipe orchestration
3. Composes its own envelope at ``cmd_dashboard.py:401-488``

Empty-corpus probe (1-file repo, 0 symbols, 0 edges):

    $ touch empty.py && roam init && roam --json dashboard
    {
      "summary": {
        "verdict": "Codebase is HEALTHY (health 100/100, AI rot 0/100)",
        "health_score": 100,
        "files": 1,
        "symbols": 0,
        "edges": 0,
        ...
      },
      "health": {"score": 100, "label": "HEALTHY", ...},
      ...
    }

This is the canonical Pattern-2 silent-SAFE shape:

- ``verdict`` reads "Codebase is HEALTHY" identical to a real healthy
  corpus — but the corpus contains zero symbols (uncoded / not yet
  written / index broken / wrong cwd). A consumer cannot distinguish
  "healthy because well-built" from "healthy because there's nothing
  TO be unhealthy about".
- ``summary.partial_success: False`` — no disclosure of the degraded
  empty-corpus axis.
- ``summary.state`` ABSENT — no explicit empty-corpus state.
- ``health.label == "HEALTHY"`` via ``_health_label(100)`` at
  ``cmd_dashboard.py:232-241`` since ``collect_metrics`` returns
  ``health_score=100`` on an empty graph (no cycles, no god components,
  no bottlenecks because there's nothing).

This is the SAME bug class as W805-833 (cmd_health): empty corpus +
``_health_label`` band threshold > 80 + no empty-corpus state guard ==
silent verdict-level Pattern-2 violation. An agent prompt-cached on
``summary.verdict`` reads HEALTHY 100/100 and proceeds as if the
repository is well-indexed and clean.

CLAUDE.md Pattern-2 invariant:

    "Never emit verdict: 'SAFE' / 'completed' / 'non-conformant' when
     the underlying check failed or didn't run. Make absent state
     explicit: ``state: 'not_initialized'``, not ``state: 'broken'``."

Root-cause location: ``src/roam/commands/cmd_dashboard.py:394-399``
constructs the verdict purely from the numeric health-score band; no
guard for ``overview['symbols'] == 0``. The fix is the same template as
W805-K / W833: detect the empty-corpus axis BEFORE the verdict-band
lookup and emit ``state="empty_corpus"`` + verdict prefix EMPTY
(``"Codebase has 0 symbols indexed (empty corpus)"``) +
``partial_success=True``.

Test split (mirrors W805-A / W805-B baseline-plus-xfail-pin discipline):

1. SMOKE (always-on assertions):
   * Empty corpus must not crash (Pattern-1A regression baseline)
   * Envelope shape (``command``, ``summary.verdict``)
   * LAW 6: verdict is standalone single-line
2. CLEAN-CORPUS BASELINE: real symbols on a real index emit real
   HEALTHY-band signal (W978 negative control: confirms the empty-corpus
   pin is empty-corpus-specific, not class-wide).
3. PATTERN-2 PIN (xfail-strict):
   * ``no_silent_dashboard_clean_on_empty``: verdict MUST NOT read
     "HEALTHY" when ``summary.symbols == 0``
   * ``empty_corpus_partial_success_set``: ``partial_success: True`` on
     0-symbol corpus
   * ``empty_corpus_state_explicit``: ``state`` key present + names the
     empty-corpus axis explicitly

The W805-PP fix lives in a separate wave; this module is intentionally
test-only per the accumulate-only constraint + W978 ("re-run before
declaring a fix"). Bundled fix wave with W805-833 (cmd_health) resolves
both since the bug shape is identical (HEALTHY-band silent on empty
corpus).
"""

from __future__ import annotations

import json as _json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from click.testing import CliRunner

sys.path.insert(0, str(Path(__file__).parent))
from conftest import index_in_process  # noqa: E402

from roam.cli import cli  # noqa: E402

# ---------------------------------------------------------------------------
# Existence guard (BAIL-if-absent shape per W978 + W907)
# ---------------------------------------------------------------------------


def test_command_exists_or_skip():
    """``cmd_dashboard`` module + ``dashboard`` Click command resolve."""
    try:
        from roam.commands import cmd_dashboard
    except ImportError as exc:  # pragma: no cover - guarded environments only
        pytest.skip(f"roam.commands.cmd_dashboard import failed: {exc!r}")
    assert hasattr(cmd_dashboard, "dashboard"), "roam.commands.cmd_dashboard.dashboard missing"
    assert callable(cmd_dashboard.dashboard), "dashboard is not a callable"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _git_init_committed(repo: Path) -> None:
    """Init a git repo + commit all current files. Quiet."""
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
    """Indexed corpus with one empty .py file.

    Indexer runs cleanly but produces zero function/class/method symbols
    and zero edges. The dashboard's direct SQL queries return:

    - ``files == 1``
    - ``symbols == 0``
    - ``edges == 0``
    - ``collect_metrics`` → ``health_score == 100`` (no cycles, no
      god components, no bottlenecks)
    - ``_health_label(100)`` → ``"HEALTHY"``
    - ``vibe_check`` → ``score == 0`` (no AI rot patterns to detect)

    Hence the silent ``"Codebase is HEALTHY (health 100/100, AI rot
    0/100)"`` verdict — the empty-corpus Pattern-2 axis.
    """
    repo = tmp_path / "empty-dashboard-repo"
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
    """Indexed corpus with real functions + call edges.

    W978 negative-control: confirms the empty-corpus pin below is the
    empty-corpus axis specifically, not a class-wide dashboard defect.
    """
    repo = tmp_path / "clean-dashboard-repo"
    repo.mkdir()
    (repo / ".gitignore").write_text(".roam/\n", encoding="utf-8")
    (repo / "a.py").write_text(
        "def f():\n    return g()\n\n\ndef g():\n    return 1\n",
        encoding="utf-8",
    )
    (repo / "b.py").write_text(
        "from a import f\n\n\ndef h():\n    return f()\n",
        encoding="utf-8",
    )
    _git_init_committed(repo)
    monkeypatch.chdir(repo)
    out, rc = index_in_process(repo, "--force")
    assert rc == 0, f"roam index failed:\n{out}"
    return repo


# ---------------------------------------------------------------------------
# JSON-output extraction helper
# ---------------------------------------------------------------------------


def _invoke_dashboard_json() -> dict:
    """Run ``roam --json dashboard`` in-process and decode the envelope.

    The index step emits trailing stdout noise even after init has run;
    ``ensure_index`` may also re-emit progress lines if the index is
    stale. Use ``raw_decode`` from the first ``{`` to tolerate prefix
    noise without forcing tests to silence the indexer.
    """
    runner = CliRunner()
    result = runner.invoke(cli, ["--json", "dashboard"], catch_exceptions=False)
    assert result.exit_code == 0, f"dashboard exited {result.exit_code}; stdout:\n{result.output}"
    out = result.output
    idx = out.find("{")
    assert idx >= 0, f"no JSON envelope in dashboard output:\n{out!r}"
    dec = _json.JSONDecoder()
    data, _end = dec.raw_decode(out[idx:])
    return data


# ---------------------------------------------------------------------------
# SMOKE (always-on) - Pattern-1A regression baseline
# ---------------------------------------------------------------------------


class TestDashboardEmptyCorpusSmoke:
    """Pattern-1A always-emit envelope + LAW 6 standalone verdict."""

    def test_empty_corpus_no_crash(self, empty_corpus):
        """Dashboard must return a structured envelope on 0-symbol corpus."""
        data = _invoke_dashboard_json()
        assert isinstance(data, dict), f"expected dict, got {type(data).__name__}"

    def test_empty_corpus_envelope_has_verdict(self, empty_corpus):
        """``summary.verdict`` is a non-empty string."""
        data = _invoke_dashboard_json()
        summary = data.get("summary") or {}
        verdict = summary.get("verdict") or ""
        assert isinstance(verdict, str) and verdict, f"summary.verdict must be non-empty string; got {verdict!r}"

    def test_empty_corpus_command_field_set(self, empty_corpus):
        """Envelope identifies itself as ``dashboard``."""
        data = _invoke_dashboard_json()
        assert data.get("command") == "dashboard", data.get("command")

    def test_empty_corpus_law6_verdict_standalone(self, empty_corpus):
        """LAW 6: verdict is single-line, standalone-readable."""
        data = _invoke_dashboard_json()
        verdict = data["summary"]["verdict"]
        assert "\n" not in verdict, f"verdict has embedded newline: {verdict!r}"
        # Non-empty so a consumer can act on the verdict in isolation.
        assert len(verdict) > 10, f"verdict too short to be informative: {verdict!r}"

    def test_empty_corpus_summary_block_shape(self, empty_corpus):
        """Summary carries the canonical numeric fields the dashboard
        envelope advertises (health_score, files, symbols, edges,
        danger_zone_count)."""
        data = _invoke_dashboard_json()
        s = data.get("summary") or {}
        for field in ("health_score", "files", "symbols", "edges", "danger_zone_count"):
            assert field in s, f"summary missing {field!r}: keys={list(s.keys())}"

    def test_empty_corpus_symbols_actually_zero(self, empty_corpus):
        """W978 sanity: the empty_corpus fixture really has 0 symbols.

        If this fails, the indexer behavior changed and the rest of the
        empty-corpus pins below are testing the wrong axis."""
        data = _invoke_dashboard_json()
        s = data["summary"]
        assert s["symbols"] == 0, f"empty corpus has {s['symbols']} symbols (expected 0)"
        assert s["edges"] == 0, f"empty corpus has {s['edges']} edges (expected 0)"


# ---------------------------------------------------------------------------
# Clean-corpus positive baseline (W978 negative control)
# ---------------------------------------------------------------------------


class TestDashboardCleanCorpusBaseline:
    """Real symbols on a real index: dashboard emits real HEALTHY signal.
    Confirms the empty-corpus pins below are NOT class-wide dashboard
    defects -- they pin the empty-corpus axis."""

    def test_clean_corpus_emits_real_dashboard(self, clean_corpus):
        """Real symbols → real envelope with non-zero symbol/edge counts."""
        data = _invoke_dashboard_json()
        assert data.get("command") == "dashboard"
        s = data["summary"]
        assert s["symbols"] > 0, s
        # ``f`` calls ``g``; ``h`` calls ``f``. At least 1 edge.
        assert s["edges"] > 0, s
        # Clean corpus → HEALTHY band is a legitimate verdict.
        assert s["health_score"] >= 80, s

    def test_clean_corpus_health_label_legitimate_healthy(self, clean_corpus):
        """On a real clean corpus, the HEALTHY label is the correct verdict.
        The pin below is specifically about HEALTHY-with-0-symbols, not
        HEALTHY-in-general."""
        data = _invoke_dashboard_json()
        assert data["health"]["label"] == "HEALTHY", data["health"]


# ---------------------------------------------------------------------------
# PATTERN-2 PIN (xfail-strict) — empty-corpus silent SAFE
# ---------------------------------------------------------------------------


# GRADUATED 2026-06-18: cmd_dashboard now guards overview['symbols']==0 at the
# _summary_block construction — emits an empty-corpus verdict + state="empty_corpus"
# + partial_success=True instead of the silent numeric HEALTHY band.
def test_no_silent_dashboard_clean_on_empty(empty_corpus):
    """Pin (Pattern-2 silent-SAFE axis): verdict MUST NOT read 'HEALTHY'
    when ``summary.symbols == 0``.

    Currently fails: dashboard emits 'Codebase is HEALTHY (health
    100/100, AI rot 0/100)' on a 1-file/0-symbol empty corpus, which
    an agent prompt-cached on the verdict reads as a healthy
    repository. Same agent-safety class as W805-833 (cmd_health) and
    W805-836 (cmd_doctor).
    """
    data = _invoke_dashboard_json()
    s = data["summary"]
    verdict = s["verdict"]
    # Empty corpus → verdict must NOT read as a clean healthy bill.
    # The fix-forward emits an explicit empty-corpus signal (e.g.
    # "0 symbols", "empty corpus", "no symbols indexed") in the
    # verdict. Accept any of those terminals; reject the silent HEALTHY.
    is_silent_healthy = (
        "HEALTHY" in verdict
        and "empty" not in verdict.lower()
        and "0 symbols" not in verdict.lower()
        and "no symbols" not in verdict.lower()
    )
    assert not is_silent_healthy, f"dashboard emits silent HEALTHY on empty corpus (0 symbols): verdict={verdict!r}"


# GRADUATED 2026-06-18: empty-corpus path now sets partial_success=True.
def test_empty_corpus_partial_success_set(empty_corpus):
    """Pin (Pattern-2 partial_success axis): empty corpus → partial_success=True.

    Currently fails: dashboard emits partial_success=False on a corpus
    with zero symbols indexed. The disclosure pattern (see CLAUDE.md
    Pattern-2) is to flip partial_success=True whenever the underlying
    aggregate is degraded; a 0-symbol corpus is the canonical degraded
    state for a codebase-intelligence dashboard.
    """
    data = _invoke_dashboard_json()
    s = data["summary"]
    assert s.get("partial_success") is True, (
        f"dashboard.summary.partial_success={s.get('partial_success')!r} on "
        f"empty corpus (0 symbols); expected True for Pattern-2 disclosure"
    )


# GRADUATED 2026-06-18: empty-corpus path now emits summary.state="empty_corpus".
def test_empty_corpus_state_explicit(empty_corpus):
    """Pin (Pattern-2 state-axis): empty corpus → summary.state names the
    empty-corpus axis explicitly.

    Currently fails: dashboard emits no state key. Consumers must
    infer the empty-corpus axis from ``summary.symbols == 0``, which
    is brittle (agents prompt-cached on the verdict + label never read
    those numeric fields).
    """
    data = _invoke_dashboard_json()
    s = data["summary"]
    state = s.get("state")
    assert state is not None, (
        "dashboard.summary.state missing on empty corpus; "
        "expected explicit 'empty_corpus' / 'no_symbols' / "
        "'not_initialized'-style state"
    )
    assert isinstance(state, str) and state, f"state={state!r} not a string"
    # The state SHOULD name the empty-corpus axis explicitly. Accept
    # any of the canonical disclosure terminals.
    lowered = state.lower()
    assert any(
        token in lowered for token in ("empty", "no_symbols", "no symbols", "not_initialized", "uninitialized")
    ), f"summary.state={state!r} does not name the empty-corpus axis"
