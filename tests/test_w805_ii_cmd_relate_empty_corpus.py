"""W805-II - empty-corpus + dual-resolver smoke for ``roam relate`` (W805 Pattern-2 sweep).

Thirty-fifth-in-batch of the W805 Pattern-2 audit. ``cmd_relate`` is a
resolver-bearing MULTI-target command (variadic ``symbols`` argument +
``--path`` resolver) and a structural peer of cmd_trace (W805-V):
both resolve >=2 symbols, both can degrade per-target, both can emit
"no relation found" / "no path" terminal envelopes. Where cmd_trace
hardened its dual-resolver disclosure at W1248/W1249/W1250 and emits
closed-enum ``state`` ("no_path_within_hops" | "no_path" | "ok"),
``cmd_relate`` only adopted **part** of the W1245 pattern.

W978 first-hypothesis re-run (in-process probes)
============================================================

Probe 1 (empty corpus, ``--json relate foo bar``):
    rc=1, stdout = ``"No symbols to analyze.\\n  Tip: ..."``
    -> NOT a JSON envelope. Pattern-1 Variant C violation.

Probe 2 (one resolved + one unresolved, ``--json relate alpha GHOST``):
    rc=0, JSON envelope present, ``resolution="unresolved"``,
    ``partial_success=True``. W1245 per-target tier disclosure works
    for the partial-resolution case (resolutions[] array populated).

Probe 3 (both resolved, no relation between them, ``--json relate alpha beta``):
    rc=0, JSON envelope present, ``resolution="symbol"``,
    ``partial_success=False``, ``state=None``, verdict reads
    ``"2 symbols analyzed, cohesion 0.00, 0 direct edges, 0 conflict risks"``.
    -> NO closed-enum ``state`` distinguishes "no relation" from
    "weak relation"; silent SAFE on relation-not-found.

Two REAL BUGs pinned via xfail(strict=True)
============================================================

**BUG #1 — Pattern-1 Variant C (empty-corpus JSON-mode plain-text emission).**
    Site: ``src/roam/commands/cmd_relate.py:274-280``. In ``--json``
    mode, when no input_ids resolve, the command emits plain-text
    "No symbols to analyze." then ``SystemExit(1)``. The MCP
    wrapper-bridge (W325) try-parses stdout as JSON; this branch
    yields parse failure -> wrapped as generic COMMAND_FAILED, losing
    the structured "no symbols" signal the agent needs to plan a
    recovery (re-run ``roam search``, ``roam init``, etc.).

    Fix template: emit ``json_envelope("relate", summary={...state:
    "no_input_resolved", partial_success: True, verdict: "no input
    symbols resolved: <names>" ...}, resolutions=per_input_resolutions)``
    even when ``not input_ids``, then exit 1.

**BUG #2 — Pattern-2 (silent SAFE on no-relation-found, both resolved).**
    Site: ``src/roam/commands/cmd_relate.py:403-409``. When both
    targets resolve exactly and all pairwise relationships have
    ``distance=None`` (NO PATH), the verdict reads
    "N symbols analyzed, cohesion X.XX, 0 direct edges, 0 conflict
    risks" with ``partial_success=False`` and NO ``state`` field. An
    agent reading only the verdict cannot tell "no relation found"
    apart from "weak but present relation". cmd_trace's W1248
    contract emits closed-enum ``state="no_path"`` / "no_path_within_hops"
    on the same shape; cmd_relate has no analogous closed-enum.

    Fix template: when both inputs resolved exactly AND
    ``len(direct_edges) == 0`` AND every pairwise distance is None,
    emit ``state="no_relation"`` + adjust verdict to "no relation
    found between <names> within depth <N>".

Cross-sibling parity (cmd_trace W805-V at tests/test_w805_v_cmd_trace_empty_corpus.py):
    cmd_trace's resolver was hardened at W1248-W1250. cmd_relate
    inherits the same W1245 per-target tier helper but its terminal
    states are not yet at parity. This file is the regression pin
    for closing that gap.

Run isolation: ``python -m pytest tests/test_w805_ii_cmd_relate_empty_corpus.py -x -n 0``
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
    env = {
        **os.environ,
        "GIT_AUTHOR_NAME": "test",
        "GIT_AUTHOR_EMAIL": "t@t",
        "GIT_COMMITTER_NAME": "test",
        "GIT_COMMITTER_EMAIL": "t@t",
    }
    subprocess.run(["git", "init", "-q"], cwd=str(repo), capture_output=True, env=env, check=True)
    subprocess.run(["git", "config", "user.email", "t@t.com"], cwd=str(repo), capture_output=True, env=env)
    subprocess.run(["git", "config", "user.name", "test"], cwd=str(repo), capture_output=True, env=env)
    subprocess.run(["git", "add", "."], cwd=str(repo), capture_output=True, env=env)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=str(repo), capture_output=True, env=env, check=True)


@pytest.fixture
def empty_corpus_repo(tmp_path, monkeypatch):
    """Indexed corpus with a single empty .py -- no symbols, no edges."""
    repo = tmp_path / "w805ii-empty"
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
    """Indexed corpus where BOTH inputs resolve EXACTLY but no edges connect them.

    Drives cmd_relate into the "all relationships NO PATH" branch.
    """
    repo = tmp_path / "w805ii-disconnected"
    repo.mkdir()
    (repo / ".gitignore").write_text(".roam/\n", encoding="utf-8")
    src = repo / "src"
    src.mkdir()
    (src / "__init__.py").write_text("", encoding="utf-8")
    (src / "alpha.py").write_text("def alpha():\n    return 1\n", encoding="utf-8")
    (src / "beta.py").write_text("def beta():\n    return 2\n", encoding="utf-8")
    _git_init_committed(repo)
    monkeypatch.chdir(repo)
    out, rc = index_in_process(repo, "--force")
    assert rc == 0, f"roam index failed:\n{out}"
    return repo


@pytest.fixture
def real_relate_repo(tmp_path, monkeypatch):
    """Indexed corpus with a real call chain ``caller -> callee``.

    Drives the happy-path verdict where a DIRECT edge exists between
    the two inputs.
    """
    repo = tmp_path / "w805ii-real"
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


def _invoke_relate(*extra, json_mode: bool = True):
    """Run ``roam [--json] relate [extra...]`` in-process."""
    from roam.cli import cli

    runner = CliRunner()
    args: list[str] = []
    if json_mode:
        args.append("--json")
    args.append("relate")
    args.extend(extra)
    return runner.invoke(cli, args, catch_exceptions=False)


def _parse_envelope(result):
    raw = (result.output or "").lstrip()
    assert raw.startswith("{"), f"expected JSON envelope, got:\n{result.output!r}"
    return _json.JSONDecoder().raw_decode(raw)[0]


# ---------------------------------------------------------------------------
# Always-on regression pins (already correct on HEAD)
# ---------------------------------------------------------------------------


class TestRelateAlwaysOn:
    """Pins for behavior that IS correct on HEAD -- guards against regression."""

    def test_empty_corpus_no_crash(self, empty_corpus_repo):
        """Empty corpus must not crash with traceback."""
        result = _invoke_relate("foo", "bar", json_mode=True)
        # Whatever envelope shape, the command must exit cleanly (no Python traceback).
        assert "Traceback" not in (result.output or ""), result.output
        # Exit code is 1 (no input resolved) -- canonical pattern.
        assert result.exit_code == 1, f"exit code {result.exit_code}; output:\n{result.output}"

    def test_one_target_unresolved_disclosure(self, disconnected_resolved_repo):
        """One resolved + one unresolved on a non-empty corpus -> partial_success disclosure.

        The W1245 per-input tier disclosure ALREADY works for this case:
        ``alpha`` resolves to ``symbol`` tier, ``GHOST_XYZ`` resolves to
        ``unresolved``, and the envelope surfaces both via
        ``resolutions[]`` plus top-level ``resolution="unresolved"``.
        """
        result = _invoke_relate("alpha", "GHOST_XYZ", json_mode=True)
        assert result.exit_code == 0, result.output
        env = _parse_envelope(result)
        summary = env["summary"]
        # Top-level disclosure: most-degraded wins.
        assert summary.get("resolution") == "unresolved", (
            f"one-unresolved must emit resolution='unresolved'; got {summary.get('resolution')!r}"
        )
        # Partial success because one input is unresolved.
        assert summary.get("partial_success") is True, f"one-unresolved must set partial_success=True; got {summary!r}"
        # Per-input tier disclosure proves WHICH input failed.
        resolutions = env.get("resolutions") or []
        assert len(resolutions) == 2
        tiers_by_input = {r["input"]: r["tier"] for r in resolutions}
        assert tiers_by_input.get("alpha") == "symbol", (
            f"alpha must be 'symbol' tier; got {tiers_by_input.get('alpha')!r}"
        )
        assert tiers_by_input.get("GHOST_XYZ") == "unresolved", (
            f"GHOST_XYZ must be 'unresolved' tier; got {tiers_by_input.get('GHOST_XYZ')!r}"
        )

    def test_both_targets_resolved_no_partial_success(self, disconnected_resolved_repo):
        """Both inputs resolve EXACTLY -> partial_success=False on tier axis.

        This pins the W1245 OR-combine logic: when no resolver
        degradation, the resolution flag does NOT spuriously fire.
        Note: this case is the SETUP for BUG #2; partial_success=False
        is correct on the resolution axis but elides the no-relation
        signal entirely.
        """
        result = _invoke_relate("alpha", "beta", json_mode=True)
        assert result.exit_code == 0, result.output
        env = _parse_envelope(result)
        summary = env["summary"]
        assert summary.get("resolution") == "symbol"
        # On the resolution axis, partial_success is False; the bug
        # (test below) is that no OTHER axis fires either.
        assert summary.get("partial_success") is False

    def test_clean_corpus_emits_real_relation(self, real_relate_repo):
        """Happy path: caller -> callee has a DIRECT edge, real relation surfaces."""
        result = _invoke_relate("caller", "callee", json_mode=True)
        assert result.exit_code == 0, result.output
        env = _parse_envelope(result)
        summary = env["summary"]
        assert summary.get("symbol_count") == 2
        # Both resolved exactly.
        assert summary.get("resolution") == "symbol"
        assert summary.get("partial_success") is False
        # A real direct edge exists.
        assert summary.get("direct_edges") >= 1, f"caller -> callee must surface as a direct edge; got {summary!r}"
        # Relationship list has a DIRECT entry.
        rels = env.get("relationships") or []
        direct = [r for r in rels if "DIRECT" in (r.get("kind") or "")]
        assert len(direct) >= 1, f"expected DIRECT relationship; got {rels!r}"


# ---------------------------------------------------------------------------
# REAL-BUG PINS (xfail strict)
# ---------------------------------------------------------------------------


class TestRelateRealBugsXfail:
    """Pin REAL bugs found by W978 probe; xfail(strict=True) means flip-to-pass on fix.

    BUG #1 (Pattern-1 Variant C/D, empty-corpus JSON-mode emission) is now FIXED
    in ``cmd_relate.py``: the ``not input_ids`` branch emits a JSON envelope, and
    when names were given but unresolved it carries ``state="no_input_resolved"``
    + ``partial_success=True`` + a verdict naming the inputs. The two
    BUG-#1 tests below are therefore plain (non-xfail) regression pins now.
    BUG #2 (Pattern-2 silent SAFE on no-relation-found) remains xfail(strict).
    """

    def test_empty_corpus_emits_json_envelope(self, empty_corpus_repo):
        """Empty corpus + --json mode MUST emit a parseable JSON envelope.

        Pattern-1 Variant C contract: structured stdout even on the
        failure path so the MCP wrapper-bridge can pass through.
        """
        result = _invoke_relate("foo", "bar", json_mode=True)
        # Whatever the exit code, stdout must be parseable JSON in --json mode.
        env = _parse_envelope(result)
        assert env["command"] == "relate", env

    def test_empty_corpus_envelope_state_partial_success_verdict(self, empty_corpus_repo):
        """Empty corpus envelope MUST disclose state + partial_success + named verdict."""
        result = _invoke_relate("foo", "bar", json_mode=True)
        env = _parse_envelope(result)
        summary = env.get("summary") or {}
        # State must be a closed-enum no-input signal.
        assert summary.get("state") in (
            "no_input_resolved",
            "no_input",
            "unresolved",
        ), f"empty-corpus state must be a closed-enum no-input signal; got {summary.get('state')!r}"
        assert summary.get("partial_success") is True, f"empty-corpus must set partial_success=True; got {summary!r}"
        verdict = summary.get("verdict") or ""
        # LAW 6: verdict standalone names the unresolved input.
        assert "foo" in verdict or "bar" in verdict, (
            f"empty-corpus verdict must name an unresolved input; got {verdict!r}"
        )
        # LAW 6: single-line, non-placeholder.
        assert "\n" not in verdict and verdict.strip() not in ("", "?", "verdict"), (
            f"verdict must be single-line, non-placeholder; got {verdict!r}"
        )

    @pytest.mark.xfail(
        strict=True,
        reason=(
            "BUG #2 Pattern-2 silent SAFE: cmd_relate.py:403-409 emits a generic "
            "'N symbols analyzed, cohesion X.XX, 0 direct edges, 0 conflict risks' "
            "verdict with partial_success=False and NO closed-enum state when "
            "BOTH inputs resolve exactly AND no relation connects them. cmd_trace "
            "(structural peer) emits state='no_path_within_hops' on the same "
            "shape (W1248). Fix: when direct_edges=0 AND every pairwise distance "
            "is None, emit state='no_relation' + adjust verdict to name the "
            "absent relation."
        ),
    )
    def test_no_relation_disambiguation(self, disconnected_resolved_repo):
        """Both resolved exactly + no edges between them MUST emit closed-enum state.

        Without a state field, an agent reading only the verdict cannot
        tell 'no relation found' from 'weak relation found'. This is
        the canonical Pattern-2 silent SAFE pattern cmd_trace fixed at
        W1248 -- cmd_relate needs to do the same.
        """
        result = _invoke_relate("alpha", "beta", json_mode=True)
        assert result.exit_code == 0, result.output
        env = _parse_envelope(result)
        summary = env["summary"]
        # Both resolved exactly -- the partial signal is from no-relation,
        # not from a degraded resolution.
        assert summary.get("resolution") == "symbol"
        # closed-enum state distinguishing no-relation from weak-relation.
        assert summary.get("state") in ("no_relation", "no_path", "disconnected"), (
            f"no-relation case must emit closed-enum state; got {summary.get('state')!r}"
        )
        # Verdict mentions the missing-relation cause -- not silent SAFE.
        verdict = (summary.get("verdict") or "").lower()
        assert "no relation" in verdict or "no path" in verdict or "disconnect" in verdict, (
            f"no-relation verdict must mention the absent relation; got {verdict!r}"
        )
