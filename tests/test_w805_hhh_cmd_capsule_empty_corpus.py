"""W805-HHH — Pattern-2 silent-SAFE pin for `cmd_capsule` on empty corpus.

Sixtieth-in-batch W805 sweep. `cmd_capsule.py` (single-symbol distillation
aggregator framing per W805-EEE agent recommendation) was untested for the
verdict-band / degraded-corpus axis.

Probe finding (reproducible, W978 re-run confirmed):
  Empty corpus (0 symbols, 0 edges from a doc-only repo) emits:
    summary.verdict          = "capsule exported (N files, 0 symbols, 0 edges)"
    summary.partial_success  = false
    summary.health_score     = 100   (vacuous denominator)
    summary["state"]         = (absent)
    agent_contract.facts[]   = "health score 100"

This is a Pattern-2 silent fallback: a degenerate input (no analyzable code)
produces a verdict indistinguishable from a healthy capsule export. The
canonical fix per CLAUDE.md "Six systemic anti-patterns" section 2:
make absent state explicit, never emit a SAFE-shaped verdict on a degraded
corpus.

This file pins the bug via ``xfail(strict=True)`` so a future fix will be
detected (xpass → test failure → unwrap the xfail). Several positive
companion tests assert the wrapper does not crash and the envelope shape
remains parseable.

Run isolation:
  python -m pytest tests/test_w805_hhh_cmd_capsule_empty_corpus.py -x -n 0
"""

from __future__ import annotations

import importlib.util
import json
import os
import sys
from pathlib import Path

import pytest
from click.testing import CliRunner

sys.path.insert(0, str(Path(__file__).parent))
from conftest import git_init, index_in_process  # noqa: E402

# ---------------------------------------------------------------------------
# Module existence gate (W978 + W907 — verify before hypothesising)
# ---------------------------------------------------------------------------

_CMD_CAPSULE_SPEC = importlib.util.find_spec("roam.commands.cmd_capsule")


def test_command_exists_or_skip():
    """W978/W907 existence gate: cmd_capsule module must be importable."""
    if _CMD_CAPSULE_SPEC is None:
        pytest.skip("roam.commands.cmd_capsule not installed in this environment")
    # Hard assertion — the module is part of the shipped surface.
    assert _CMD_CAPSULE_SPEC is not None


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def cli_runner():
    return CliRunner()


@pytest.fixture
def empty_corpus_project(tmp_path, monkeypatch):
    """Indexed project containing no source-code symbols.

    A README.md + .gitignore is enough to satisfy ``git init`` and roam's
    index pipeline, but the corpus has 0 symbols and 0 edges (markdown is
    not a symbol-producing language for roam). This is the canonical
    "degenerate corpus" axis the W805 sweep targets.
    """
    proj = tmp_path / "empty_repo"
    proj.mkdir()
    (proj / ".gitignore").write_text(".roam/\n")
    (proj / "README.md").write_text("# empty repo for W805-HHH probe\n")
    git_init(proj)
    monkeypatch.chdir(proj)
    out, rc = index_in_process(proj, "--force")
    assert rc == 0, f"index failed on empty corpus: {out}"
    return proj


@pytest.fixture
def clean_corpus_project(tmp_path, monkeypatch):
    """Indexed project with real Python source — positive control."""
    proj = tmp_path / "clean_repo"
    proj.mkdir()
    (proj / ".gitignore").write_text(".roam/\n")
    (proj / "app.py").write_text("def hello():\n    return 'world'\n\ndef greet(name):\n    return hello() + name\n")
    git_init(proj)
    monkeypatch.chdir(proj)
    out, rc = index_in_process(proj, "--force")
    assert rc == 0, f"index failed on clean corpus: {out}"
    return proj


# ---------------------------------------------------------------------------
# Invoke helper (mirrors test_capsule.py since capsule is invoked directly)
# ---------------------------------------------------------------------------


def _invoke_capsule(runner, args=None, cwd=None, json_mode=False):
    from roam.commands.cmd_capsule import capsule

    full_args = list(args or [])
    old_cwd = os.getcwd()
    try:
        if cwd:
            os.chdir(str(cwd))
        result = runner.invoke(
            capsule,
            full_args,
            obj={"json": json_mode},
            catch_exceptions=False,
        )
    finally:
        os.chdir(old_cwd)
    return result


def _parse_json(result):
    assert result.exit_code == 0, f"capsule exit={result.exit_code}:\n{result.output}"
    try:
        return json.loads(result.output)
    except json.JSONDecodeError as e:
        pytest.fail(f"Invalid JSON: {e}\nOutput head:\n{result.output[:500]}")


# ---------------------------------------------------------------------------
# Positive tests — empty-corpus envelope must remain parseable + crash-free
# (Pattern-1 variant C — never emit empty stdout)
# ---------------------------------------------------------------------------


class TestEmptyCorpusEnvelopeShape:
    def test_empty_corpus_no_crash(self, empty_corpus_project, cli_runner):
        """capsule on empty corpus must not crash, regardless of degraded signal."""
        result = _invoke_capsule(cli_runner, cwd=empty_corpus_project, json_mode=True)
        assert result.exit_code == 0, f"capsule crashed on empty corpus (Pattern-1 variant C):\n{result.output}"

    def test_empty_corpus_envelope_has_verdict(self, empty_corpus_project, cli_runner):
        """JSON envelope must carry a summary.verdict string (LAW 6)."""
        result = _invoke_capsule(cli_runner, cwd=empty_corpus_project, json_mode=True)
        data = _parse_json(result)
        summary = data.get("summary", {})
        assert "verdict" in summary, f"summary missing 'verdict': {summary}"
        assert isinstance(summary["verdict"], str) and summary["verdict"], "verdict must be a non-empty string"

    def test_empty_corpus_law6_verdict_standalone(self, empty_corpus_project, cli_runner):
        """LAW 6 — verdict must be self-contained (no 'see X' indirections)."""
        result = _invoke_capsule(cli_runner, cwd=empty_corpus_project, json_mode=True)
        data = _parse_json(result)
        verdict = data.get("summary", {}).get("verdict", "")
        lowered = verdict.lower()
        assert "see " not in lowered and "details" not in lowered, f"LAW 6 violation — verdict indirects: {verdict!r}"


# ---------------------------------------------------------------------------
# REAL BUG — Pattern-2 silent SAFE on empty corpus
# Pinned xfail(strict=True): a fix will flip these to xpass → test failure.
# ---------------------------------------------------------------------------


@pytest.mark.xfail(
    strict=True,
    reason=(
        "W805-HHH Pattern-2 bug: cmd_capsule emits "
        "verdict='capsule exported (N files, 0 symbols, 0 edges)' + "
        "partial_success=false + health_score=100 + no 'state' field on a "
        "degenerate (0-symbol) corpus. Fix: disclose state='empty_corpus' "
        "or similar, set partial_success=true, and downgrade the verdict "
        "from a SAFE-shaped string. See CLAUDE.md 'Six systemic "
        "anti-patterns' section 2."
    ),
)
class TestEmptyCorpusPattern2Bug:
    def test_empty_corpus_state_explicit(self, empty_corpus_project, cli_runner):
        """Pattern-2: empty-corpus envelope must disclose state explicitly."""
        result = _invoke_capsule(cli_runner, cwd=empty_corpus_project, json_mode=True)
        data = _parse_json(result)
        summary = data.get("summary", {})
        state = summary.get("state") or summary.get("resolution")
        assert state, (
            f"Pattern-2 silent SAFE: empty corpus produced summary without "
            f"'state'/'resolution' disclosure. summary={summary!r}"
        )

    def test_empty_corpus_partial_success_set(self, empty_corpus_project, cli_runner):
        """Pattern-2: empty corpus must flag partial_success=True."""
        result = _invoke_capsule(cli_runner, cwd=empty_corpus_project, json_mode=True)
        data = _parse_json(result)
        summary = data.get("summary", {})
        assert summary.get("partial_success") is True, (
            f"Pattern-2 silent SAFE: 0 symbols + 0 edges but partial_success={summary.get('partial_success')!r}"
        )

    def test_no_silent_capsule_distilled_on_empty(self, empty_corpus_project, cli_runner):
        """The verdict must not read like a healthy SAFE export."""
        result = _invoke_capsule(cli_runner, cwd=empty_corpus_project, json_mode=True)
        data = _parse_json(result)
        verdict = data.get("summary", {}).get("verdict", "").lower()
        # SAFE-shaped phrases that must NOT appear on a 0-symbol corpus
        forbidden = ("capsule exported (", "capsule distilled")
        offenders = [p for p in forbidden if p in verdict]
        assert not offenders, (
            f"Pattern-2 silent SAFE: empty-corpus verdict reads as "
            f"successful export: {verdict!r}; offenders={offenders}"
        )

    def test_empty_corpus_health_score_not_vacuous_max(self, empty_corpus_project, cli_runner):
        """health_score=100 on 0 symbols is a degenerate-denominator artifact."""
        result = _invoke_capsule(cli_runner, cwd=empty_corpus_project, json_mode=True)
        data = _parse_json(result)
        summary = data.get("summary", {})
        if summary.get("symbols", 0) == 0:
            assert summary.get("health_score") != 100, (
                "Vacuous-max health: 0 symbols cannot earn a 100/100 score. "
                "Either suppress the score or attach a state='empty_corpus' "
                "qualifier so consumers don't read 100 as 'healthy'."
            )


# ---------------------------------------------------------------------------
# Pattern-1 variant D — degraded resolution disclosure (advisory probe)
# capsule is corpus-wide (not symbol-resolving), so we assert the broader
# "resolution / state field present on degraded input" contract that the
# Pattern-1-V-D fix template prescribes.
# ---------------------------------------------------------------------------


@pytest.mark.xfail(
    strict=True,
    reason=(
        "Pattern-1 variant D: degraded-corpus resolution state not "
        "disclosed via 'resolution' / 'state' field. Same Fix template "
        "as W805-HHH primary bug — surfaced separately because Pattern-1-V-D "
        "is the resolution-axis pin."
    ),
)
def test_missing_target_resolution_disclosed(empty_corpus_project, cli_runner):
    """Pattern-1-V-D: degraded corpus must disclose resolution state."""
    result = _invoke_capsule(cli_runner, cwd=empty_corpus_project, json_mode=True)
    data = _parse_json(result)
    summary = data.get("summary", {})
    # Either summary.resolution OR a top-level resolution field would close it.
    has_resolution = "resolution" in summary or "resolution" in data
    assert has_resolution, (
        f"Pattern-1-V-D: empty corpus produced no resolution disclosure. summary keys={list(summary.keys())}"
    )


# ---------------------------------------------------------------------------
# Positive control — clean corpus must still emit a real capsule
# (guards against an over-eager fix that breaks the healthy path)
# ---------------------------------------------------------------------------


def test_clean_corpus_emits_real_capsule(clean_corpus_project, cli_runner):
    """Clean corpus: real symbols + edges + a verdict that reads as exported."""
    result = _invoke_capsule(cli_runner, cwd=clean_corpus_project, json_mode=True)
    data = _parse_json(result)
    summary = data.get("summary", {})
    assert summary.get("symbols", 0) >= 1, (
        f"clean corpus produced 0 symbols — fixture or indexer regression: {summary!r}"
    )
    verdict = summary.get("verdict", "")
    assert "capsule exported" in verdict.lower(), f"clean corpus lost the 'capsule exported' verdict shape: {verdict!r}"
