"""W805-V - empty-corpus smoke for ``roam trace`` (W805 Pattern-2 sweep).

Twentieth-in-batch of the W805 Pattern-2 audit. ``cmd_trace`` is a
resolver-bearing TWO-target command (source + target), making it a
prime candidate for both Pattern-1 Variant D (silent fuzzy fallback)
AND Pattern-2 (silent SAFE on 0 paths). The W805-V probe is a
DEFENSIVE pin: zero new bugs in scope -- W1248 + W1249 + W1250 already
sealed every shape this probe could expose.

W978 first-hypothesis re-run BEFORE writing any test
============================================================

``cmd_trace`` resolves TWO targets via ``find_symbol_id_with_tier``
(W1249), the closed-enum tiered helper returning
``{"symbol", "fuzzy", "unresolved"}``. The command takes the
most-degraded tier across the two via ``_combine_resolution`` for the
top-level disclosure, then surfaces both via ``src_resolution`` /
``tgt_resolution`` extension fields so consumers can distinguish "both
fuzzy" from "source fuzzy, target exact".

What this means for the four bug-shapes in the brief:

1. **Pattern-1 Variant D (unresolved source).** ALREADY SEALED at
   ``cmd_trace.py:336-365``. ``find_symbol_id_with_tier`` returns
   ``([], "unresolved")``; the command emits a full structured envelope
   with ``partial_success=True``, ``resolution="unresolved"``,
   ``src_resolution="unresolved"``, ``tgt_resolution="unknown"`` AND
   exits 1 via ``raise SystemExit(1)``. The verdict
   ``"symbol not found: 'foo'"`` names the absent symbol explicitly --
   LAW 6 standalone-readable.

2. **Pattern-1 Variant D (unresolved target, source OK).** ALREADY
   SEALED at ``cmd_trace.py:367-396``. Same envelope shape with the
   asymmetric disclosure ``src_resolution=<src_tier>``,
   ``tgt_resolution="unresolved"`` -- consumers can tell which side
   failed.

3. **Pattern-2 (silent SAFE on 0 paths).** ALREADY SEALED at
   ``cmd_trace.py:466-527``. Two closed-enum states distinguish the
   bounded-vs-exhaustive cause:

   * ``"no_path_within_hops"`` -- bounded BFS exhausted ``--max-hops``
     budget. ``partial_success=True`` (a longer path may exist).
   * ``"no_path"`` -- exhaustive Yen's search returned 0 paths.
     Definitive negative result; ``partial_success=False`` UNLESS
     resolution was degraded (``combined_tier != "symbol"``), in
     which case OR-combine kicks in via W1250.

4. **LAW 6 verdict standalone.** All four error/empty branches emit
   verdicts that name the absent state explicitly:

   * Unresolved source: ``"symbol not found: 'foo'"``
   * Unresolved target: ``"symbol not found: 'NOPE'"``
   * No path within hops: ``"no path between X and Y within
     --max-hops=N; increase --max-hops or pass --exhaustive"``
   * No path exhaustive: ``"no path found between X and Y (exhaustive)"``

REAL BUGs found in scope: **0**

The W805-V test file therefore captures positive-coverage regression
pins for the already-sealed contract. It serves the W805 sweep audit
trail (resolver-bearing two-target shape uniform with cmd_impact /
cmd_preflight / cmd_diagnose) and prevents silent regression of the
four Pattern-1 Variant D + Pattern-2 branches W1248/W1249/W1250 made
explicit.

No xfails -- all assertions pass on HEAD.
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
def empty_corpus_repo(tmp_path, monkeypatch):
    """Indexed corpus with a single empty .py -- no symbols, no edges."""
    repo = tmp_path / "w805v-empty"
    repo.mkdir()
    (repo / ".gitignore").write_text(".roam/\n", encoding="utf-8")
    (repo / "empty.py").write_text("", encoding="utf-8")
    _git_init_committed(repo)
    monkeypatch.chdir(repo)
    out, rc = index_in_process(repo)
    assert rc == 0, f"roam init failed:\n{out}"
    return repo


@pytest.fixture
def disconnected_resolved_repo(tmp_path, monkeypatch):
    """Indexed corpus where both source + target resolve EXACTLY but no path exists.

    Drives ``cmd_trace`` into the ``no_path_within_hops`` branch (bounded
    BFS default) and ``no_path`` branch (with ``--exhaustive``).
    """
    repo = tmp_path / "w805v-disconnected"
    repo.mkdir()
    (repo / ".gitignore").write_text(".roam/\n", encoding="utf-8")
    src = repo / "src"
    src.mkdir()
    (src / "alpha.py").write_text("def alpha():\n    return 1\n", encoding="utf-8")
    (src / "beta.py").write_text("def beta():\n    return 2\n", encoding="utf-8")
    _git_init_committed(repo)
    monkeypatch.chdir(repo)
    out, rc = index_in_process(repo, "--force")
    assert rc == 0, f"roam index failed:\n{out}"
    return repo


@pytest.fixture
def real_trace_repo(tmp_path, monkeypatch):
    """Indexed corpus with a real source -> target call chain.

    ``caller`` calls ``callee``: a 1-edge path exists. Drives the
    happy-path verdict ("trace: 2 hops caller->callee, 1 path found,
    strong (direct call chain)").
    """
    repo = tmp_path / "w805v-real"
    repo.mkdir()
    (repo / ".gitignore").write_text(".roam/\n", encoding="utf-8")
    src = repo / "src"
    src.mkdir()
    (src / "__init__.py").write_text("", encoding="utf-8")
    (src / "mod.py").write_text(
        "def callee():\n    return 1\n\ndef caller():\n    return callee()\n",
        encoding="utf-8",
    )
    _git_init_committed(repo)
    monkeypatch.chdir(repo)
    out, rc = index_in_process(repo, "--force")
    assert rc == 0, f"roam index failed:\n{out}"
    return repo


# ---------------------------------------------------------------------------
# Invocation helpers
# ---------------------------------------------------------------------------


def _invoke_trace(*extra, json_mode: bool = True):
    """Run ``roam [--json] trace [extra...]`` in-process."""
    from roam.cli import cli

    runner = CliRunner()
    args: list[str] = []
    if json_mode:
        args.append("--json")
    args.append("trace")
    args.extend(extra)
    # ``catch_exceptions=False`` lets SystemExit propagate cleanly through
    # CliRunner; the runner captures the exit code into ``result.exit_code``.
    return runner.invoke(cli, args, catch_exceptions=False)


def _parse_envelope(result):
    raw = (result.output or "").lstrip()
    assert raw.startswith("{"), f"expected JSON envelope, got:\n{result.output!r}"
    return _json.JSONDecoder().raw_decode(raw)[0]


# ---------------------------------------------------------------------------
# SMOKE (always-on) -- empty corpus
# ---------------------------------------------------------------------------


class TestTraceEmptyCorpusSmoke:
    """Pattern-1 + Pattern-2 always-emit baseline for ``roam trace``.

    Empty corpus means BOTH source + target resolve to ``unresolved``.
    The W1248 + W1249 contract: source-unresolved is checked FIRST and
    short-circuits with a structured envelope before the target lookup.
    """

    def test_empty_corpus_no_crash(self, empty_corpus_repo):
        """``roam trace foo bar`` on empty corpus emits non-empty JSON.

        Exit code is 1 (canonical W1248 unresolved-source signal) but
        stdout MUST carry a parseable envelope. The Pattern-1 variant-C
        guard: never empty stdout, even on the failure path.
        """
        result = _invoke_trace("foo", "bar", json_mode=True)
        # Pattern-1 Variant D contract: exit non-zero for the resolver
        # miss BUT emit structured JSON on stdout.
        assert result.exit_code == 1, (
            f"expected exit 1 on unresolved source, got {result.exit_code}; output:\n{result.output}"
        )
        assert result.output.strip(), "stdout must NOT be empty on resolver miss"

    def test_empty_corpus_envelope_has_verdict(self, empty_corpus_repo):
        """``roam trace`` empty-corpus envelope has command=trace + non-empty verdict."""
        result = _invoke_trace("foo", "bar", json_mode=True)
        env = _parse_envelope(result)
        assert env["command"] == "trace"
        summary = env.get("summary") or {}
        verdict = summary.get("verdict") or ""
        assert isinstance(verdict, str) and verdict, f"summary.verdict must be a non-empty string, got {verdict!r}"

    def test_empty_corpus_explicit_state(self, empty_corpus_repo):
        """Empty-corpus envelope discloses ``resolution: "unresolved"``.

        Pattern-1 Variant D contract: the resolution state is named
        explicitly (closed-enum). Consumers can distinguish "symbol
        miss" from "no path" without parsing the human-readable
        verdict.
        """
        result = _invoke_trace("foo", "bar", json_mode=True)
        env = _parse_envelope(result)
        summary = env.get("summary") or {}
        # Top-level disclosure says unresolved.
        assert summary.get("resolution") == "unresolved", (
            f"empty-corpus must emit resolution='unresolved'; got {summary.get('resolution')!r}"
        )
        # Per-target disclosure: source unresolved, target unknown
        # (lookup short-circuits on source miss before target query).
        assert summary.get("src_resolution") == "unresolved", (
            f"empty-corpus must emit src_resolution='unresolved'; got {summary.get('src_resolution')!r}"
        )
        assert summary.get("tgt_resolution") == "unknown", (
            f"empty-corpus must emit tgt_resolution='unknown' "
            f"(short-circuit on source miss); got {summary.get('tgt_resolution')!r}"
        )

    def test_empty_corpus_partial_success_set(self, empty_corpus_repo):
        """Pattern-2: empty-corpus branch emits ``partial_success: True``.

        The success verdict MUST NOT be indistinguishable from a fully
        resolved trace. ``partial_success`` is the closed-enum boolean
        agents key off of to gate downstream actions.
        """
        result = _invoke_trace("foo", "bar", json_mode=True)
        env = _parse_envelope(result)
        summary = env.get("summary") or {}
        assert summary.get("partial_success") is True, (
            f"empty-corpus branch must set partial_success=True; got summary={summary!r}"
        )
        # Top-level partial_success matches summary.partial_success
        # (both surfaces of the same signal -- W1248 envelope contract).
        assert env.get("partial_success") is True, (
            f"top-level partial_success must mirror summary; got {env.get('partial_success')!r}"
        )

    def test_empty_corpus_law6_verdict_standalone(self, empty_corpus_repo):
        """LAW 6: verdict works without any other field.

        Asserts: single line, names the absent symbol, not a placeholder.
        """
        result = _invoke_trace("foo", "bar", json_mode=True)
        env = _parse_envelope(result)
        verdict = env["summary"]["verdict"]
        assert "\n" not in verdict, f"verdict has embedded newline: {verdict!r}"
        assert verdict.strip() not in ("", "?", "verdict"), f"verdict is a placeholder: {verdict!r}"
        # LAW 6 + LAW 4: the absent symbol name must appear in the
        # verdict so an agent reading only the verdict knows what failed.
        assert "foo" in verdict, f"verdict must name the unresolved symbol 'foo'; got {verdict!r}"


# ---------------------------------------------------------------------------
# RESOLUTION-TIER DISCLOSURE (Pattern-1 Variant D)
# ---------------------------------------------------------------------------


class TestTraceResolutionDisclosure:
    """Per-target resolver-tier disclosure regression pins.

    ``cmd_trace`` resolves TWO targets via the W1249 tiered helper.
    The asymmetric outcomes (one resolved, the other not) MUST be
    distinguishable in the envelope so consumers can tell which side
    failed.
    """

    def test_unresolved_source_explicit_resolution(self, disconnected_resolved_repo):
        """Source unresolved on a non-empty corpus: top-level + per-target tiers.

        Both ``alpha`` and ``beta`` exist; ``GHOST`` does not match any
        symbol (including LIKE fallback). Source is checked first, so
        the envelope discloses src_resolution='unresolved' +
        tgt_resolution='unknown' (target lookup short-circuited).
        """
        result = _invoke_trace("GHOST_SOURCE_XYZ", "alpha", json_mode=True)
        assert result.exit_code == 1, result.output
        env = _parse_envelope(result)
        summary = env["summary"]
        assert summary.get("resolution") == "unresolved"
        assert summary.get("src_resolution") == "unresolved"
        assert summary.get("tgt_resolution") == "unknown", (
            f"target lookup must be short-circuited on source miss; "
            f"got tgt_resolution={summary.get('tgt_resolution')!r}"
        )
        assert summary.get("partial_success") is True
        verdict = summary["verdict"]
        assert "GHOST_SOURCE_XYZ" in verdict, f"verdict must name the unresolved source; got {verdict!r}"

    def test_unresolved_target_explicit_resolution(self, disconnected_resolved_repo):
        """Target unresolved (source OK): asymmetric per-target disclosure.

        Source ``alpha`` resolves to ``symbol`` tier. Target
        ``GHOST_TARGET_XYZ`` falls off the resolver. The envelope MUST
        surface BOTH ``src_resolution="symbol"`` AND
        ``tgt_resolution="unresolved"`` so consumers can distinguish
        this from the empty-corpus case (where source also missed).
        """
        result = _invoke_trace("alpha", "GHOST_TARGET_XYZ", json_mode=True)
        assert result.exit_code == 1, result.output
        env = _parse_envelope(result)
        summary = env["summary"]
        # Top-level says unresolved (most-degraded tier wins per W1248).
        assert summary.get("resolution") == "unresolved"
        # Asymmetric per-target tiers: source resolved exactly, target did not.
        assert summary.get("src_resolution") == "symbol", (
            f"source must surface src_resolution='symbol'; got {summary.get('src_resolution')!r}"
        )
        assert summary.get("tgt_resolution") == "unresolved", (
            f"target must surface tgt_resolution='unresolved'; got {summary.get('tgt_resolution')!r}"
        )
        verdict = summary["verdict"]
        assert "GHOST_TARGET_XYZ" in verdict, f"verdict must name the unresolved target; got {verdict!r}"


# ---------------------------------------------------------------------------
# 0-PATHS PATTERN-2 PINS
# ---------------------------------------------------------------------------


class TestTraceNoPathsExplicitState:
    """When BOTH targets resolve but no path connects them: explicit state.

    Pre-W1248, 0-paths could collapse to a generic ``"completed"``
    verdict (Pattern-2 silent SAFE). The W1248 contract distinguishes:

    * ``"no_path_within_hops"`` -- bounded BFS exhausted budget
      (partial_success=True; a longer path may exist).
    * ``"no_path"`` -- exhaustive Yen's returned 0 paths
      (partial_success=False; definitive negative).
    """

    def test_no_paths_explicit_state(self, disconnected_resolved_repo):
        """Default (bounded) trace on disconnected resolved symbols.

        ``alpha`` and ``beta`` both resolve to ``symbol`` tier but live
        in unrelated files with no edges between them. Envelope MUST
        emit ``state: "no_path_within_hops"`` + ``partial_success: True``,
        NOT a silent SAFE / completed verdict.
        """
        result = _invoke_trace("alpha", "beta", json_mode=True)
        assert result.exit_code == 0, (
            f"0-paths on resolved targets must exit 0 (clean envelope); "
            f"got {result.exit_code}; output:\n{result.output}"
        )
        env = _parse_envelope(result)
        summary = env["summary"]
        assert summary.get("state") == "no_path_within_hops", (
            f"bounded 0-paths must emit state='no_path_within_hops'; got {summary.get('state')!r}"
        )
        assert summary.get("partial_success") is True, (
            f"bounded 0-paths must set partial_success=True (longer path may exist); "
            f"got {summary.get('partial_success')!r}"
        )
        assert summary.get("paths") == 0
        # Per-target tiers prove BOTH resolved exactly -- the partial
        # signal is from no-path, not from a degraded resolution.
        assert summary.get("src_resolution") == "symbol"
        assert summary.get("tgt_resolution") == "symbol"

    def test_no_silent_no_paths_safe(self, disconnected_resolved_repo):
        """Pattern-2: 0-paths verdict MUST NOT collapse to a silent SAFE.

        This is the canonical Pattern-2 regression pin: the success
        verdict on a no-path result must NOT be indistinguishable from
        a real path-found verdict. The W1248 contract is that the
        verdict mentions the missing-path cause AND state is set.
        """
        result = _invoke_trace("alpha", "beta", json_mode=True)
        env = _parse_envelope(result)
        summary = env["summary"]
        verdict = (summary.get("verdict") or "").lower()
        # The verdict must NOT read as a successful path-found result.
        # Real-trace verdicts read "trace: N hops X->Y, M paths found, ..."
        # No-path verdicts read "no path between X and Y within --max-hops=N; ..."
        assert "no path" in verdict, f"0-paths verdict must mention 'no path' explicitly; got {verdict!r}"
        # Specifically NOT a "completed" / "safe" verdict.
        assert "completed" not in verdict, (
            f"0-paths verdict MUST NOT read 'completed' (Pattern-2 silent SAFE); got {verdict!r}"
        )
        # state field is set (closed-enum), not absent / "ok".
        assert summary.get("state") not in (None, "", "ok"), (
            f"0-paths state must be a closed-enum empty signal; got {summary.get('state')!r}"
        )


# ---------------------------------------------------------------------------
# CLEAN-CORPUS POSITIVE BASELINE
# ---------------------------------------------------------------------------


class TestTraceCleanCorpusBaseline:
    """Happy-path positive coverage: real source -> target chain.

    Asserts the inverse of the Pattern-1/Pattern-2 pins: on a real
    corpus where the trace ran cleanly, partial_success is False,
    state is 'ok', and both resolutions are 'symbol'.
    """

    def test_clean_corpus_emits_real_trace(self, real_trace_repo):
        """Real call chain: caller -> callee. Verdict mentions hops + path count."""
        result = _invoke_trace("caller", "callee", json_mode=True)
        assert result.exit_code == 0, result.output
        env = _parse_envelope(result)
        summary = env["summary"]
        verdict = summary["verdict"]
        # Real-signal verdict: contains 'hops' + 'path' + 'found'.
        verdict_lower = verdict.lower()
        assert "hops" in verdict_lower, f"happy-path verdict must mention hops; got {verdict!r}"
        assert "path" in verdict_lower and "found" in verdict_lower, (
            f"happy-path verdict must mention paths found; got {verdict!r}"
        )
        # state=ok, no partial_success, both resolutions exact.
        assert summary.get("state") == "ok"
        assert summary.get("partial_success") is False, (
            f"happy-path partial_success must be False; got {summary.get('partial_success')!r}"
        )
        assert summary.get("src_resolution") == "symbol"
        assert summary.get("tgt_resolution") == "symbol"
        assert summary.get("paths") >= 1
        # paths key is a non-empty list on success.
        paths = env.get("paths") or []
        assert isinstance(paths, list) and len(paths) >= 1, f"happy-path paths must be non-empty list; got {paths!r}"
