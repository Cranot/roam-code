"""W805-GGGG — snapshot-state disclosure pin for ``cmd_capsule``.

Eighty-fifth-in-batch W805 sweep, ``cmd_capsule.py``. W805-HHH already
pinned the *vacuous-health* axis (empty corpus → SAFE-shaped verdict +
health_score=100). This file probes a strictly orthogonal axis:
**snapshot-state / freshness disclosure**.

Hypothesis (W805-DDDD agent recommendation, counterfactual family
sibling): the capsule is a portable graph snapshot intended for replay
/ external review. The substrate has rich freshness data
(``index_manifest.indexed_at``, ``git_head``, ``git_dirty_hash``,
``schema_version`` via :mod:`roam.index.manifest`) AND a stale-index
detector (``roam.commands.stale_index.check_stale``). But
``cmd_capsule._build_capsule`` calls **neither**. The capsule's
``capsule.generated`` field is wall-clock-now, not the indexed_at; a
capture against a 30-day-old stale index emits an envelope structurally
indistinguishable from a capture against a fresh-indexed graph.

This is **Pattern-1 variant D** ("silent success on degraded
resolution") at the corpus / snapshot axis — the capsule succeeds even
when the underlying graph it captures is stale. It is also a CP45/CP46
"loud-fix-over-silent-fallback" lineage rule violation: no
``freshness`` / ``staleness`` / ``index_state`` field discloses that
the snapshot's evidence value depends on index freshness the command
never verified.

W978 first-hypothesis check (re-run before declaring): probe was run
empirically against a fresh corpus and confirmed ZERO of
``indexed_at`` / ``index_freshness`` / ``staleness`` / ``freshness`` /
``captured_at`` / ``git_head`` / ``git_dirty_hash`` / ``index_state``
appear anywhere in the JSON envelope (summary, capsule meta, root).
This is NOT the W805-HHH bug (which targets verdict + partial_success
+ health_score on empty-corpus); it is a distinct disclosure axis.

W907 verify-cycle: ``_build_capsule`` does a deferred ``from roam
import __version__`` import inside the function. Grep confirms no
"avoid circular import" / "would create a cycle" docstring hedging,
so this is a benign lazy import (defer heavy import cost), not a
cargo-cult false cycle. No W907 violation.

Pinned via ``xfail(strict=True)`` so a future fix is detected
(xpass → test failure → unwrap).

Run isolation:
  python -m pytest tests/test_w805_gggg_cmd_capsule_snapshot_state.py -x -n 0
"""

from __future__ import annotations

import importlib.util
import json
import os
import sys
import time
from pathlib import Path

import pytest
from click.testing import CliRunner

sys.path.insert(0, str(Path(__file__).parent))
from conftest import git_init, index_in_process  # noqa: E402

# ---------------------------------------------------------------------------
# Module existence gate (W978 + W907 — verify before hypothesising)
# ---------------------------------------------------------------------------

_CMD_CAPSULE_SPEC = importlib.util.find_spec("roam.commands.cmd_capsule")
_MANIFEST_SPEC = importlib.util.find_spec("roam.index.manifest")
_STALE_INDEX_SPEC = importlib.util.find_spec("roam.commands.stale_index")


def test_command_and_freshness_substrate_exist():
    """W978/W907 gate: capsule + manifest + stale-index substrates all import."""
    if _CMD_CAPSULE_SPEC is None:
        pytest.skip("roam.commands.cmd_capsule not installed")
    # The freshness substrate the capsule SHOULD be consulting:
    assert _MANIFEST_SPEC is not None, "roam.index.manifest missing — bug pin assumes manifest substrate exists"
    assert _STALE_INDEX_SPEC is not None, "roam.commands.stale_index missing — bug pin assumes stale-detector exists"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def cli_runner():
    return CliRunner()


@pytest.fixture
def fresh_indexed_project(tmp_path, monkeypatch):
    """Indexed project with real Python symbols — fresh index."""
    proj = tmp_path / "fresh_repo"
    proj.mkdir()
    (proj / ".gitignore").write_text(".roam/\n")
    (proj / "app.py").write_text("def alpha():\n    return 1\n\ndef beta(x):\n    return alpha() + x\n")
    git_init(proj)
    monkeypatch.chdir(proj)
    out, rc = index_in_process(proj, "--force")
    assert rc == 0, f"index failed: {out}"
    return proj


@pytest.fixture
def stale_indexed_project(tmp_path, monkeypatch):
    """Indexed project where the index is *demonstrably stale*.

    Strategy: index the repo, then back-date the SQLite DB mtime so the
    stale detector treats it as old. The graph data hasn't changed but
    the on-disk state reads as "old index" — which is exactly the
    Pattern-1-V-D scenario: capsule captures a graph the command never
    verified is current.
    """
    proj = tmp_path / "stale_repo"
    proj.mkdir()
    (proj / ".gitignore").write_text(".roam/\n")
    (proj / "app.py").write_text("def hello():\n    return 'world'\n")
    git_init(proj)
    monkeypatch.chdir(proj)
    out, rc = index_in_process(proj, "--force")
    assert rc == 0, f"index failed: {out}"

    # Back-date the DB and manifest to 30 days ago.
    db_path = proj / ".roam" / "roam.db"
    if db_path.exists():
        old_ts = time.time() - (30 * 24 * 3600)
        os.utime(db_path, (old_ts, old_ts))

    return proj


# ---------------------------------------------------------------------------
# Invoke helper
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


def _all_keys(data: dict) -> set:
    """Union of root, summary, and capsule-meta key sets."""
    keys = set(data.keys())
    keys |= set(data.get("summary", {}).keys())
    keys |= set(data.get("capsule", {}).keys())
    return keys


_FRESHNESS_KEYS = {
    "indexed_at",
    "index_indexed_at",
    "index_freshness",
    "freshness",
    "staleness",
    "stale",
    "is_stale",
    "captured_at",  # distinct from `generated` because it implies "captured against index state X at time Y"
    "capture_state",
    "index_state",
    "snapshot_state",
}

_INDEX_LINEAGE_KEYS = {
    "git_head",
    "indexed_git_head",
    "git_dirty_hash",
    # NOTE: the roam envelope already carries a top-level ``schema_version``
    # (the envelope schema version, currently "1.1.0"). That is NOT what we
    # mean by index lineage; the field we want would be the index's
    # PRAGMA user_version (the SQLite schema version at index time). So we
    # check for the more specific names only, not the generic
    # ``schema_version``.
    "index_schema_version",
    "roam_version_at_index",
    "index_roam_version",
    "manifest",
    "index_manifest",
}


# ---------------------------------------------------------------------------
# Positive shape tests — capsule must remain parseable on both axes
# ---------------------------------------------------------------------------


class TestCapsuleEnvelopeBaseline:
    def test_fresh_capsule_envelope_parses(self, fresh_indexed_project, cli_runner):
        """Sanity: a fresh-indexed capsule still emits parseable JSON."""
        result = _invoke_capsule(cli_runner, cwd=fresh_indexed_project, json_mode=True)
        data = _parse_json(result)
        assert "summary" in data
        assert "verdict" in data["summary"]

    def test_stale_capsule_envelope_parses(self, stale_indexed_project, cli_runner):
        """Stale capture must not crash — Pattern-1 variant C guard."""
        result = _invoke_capsule(cli_runner, cwd=stale_indexed_project, json_mode=True)
        data = _parse_json(result)
        assert "summary" in data, f"stale capture lost envelope shape: {data}"

    def test_capsule_generated_field_present(self, fresh_indexed_project, cli_runner):
        """``capsule.generated`` is the wall-clock capture time (already present)."""
        result = _invoke_capsule(cli_runner, cwd=fresh_indexed_project, json_mode=True)
        data = _parse_json(result)
        meta = data.get("capsule", {})
        assert "generated" in meta, f"capsule meta missing 'generated': {meta}"


# ---------------------------------------------------------------------------
# W805-HHH parity (cross-check — must stay green)
# W805-HHH's vacuous-health axis is xfailed; here we only assert the
# baseline invariants that don't overlap with the strict-xfail pins.
# ---------------------------------------------------------------------------


class TestW805HhhInvariantsPreserved:
    """W805-HHH sister-cross-check: empty-corpus baseline must keep working.

    We don't re-assert W805-HHH's xfail-strict claims (those would
    collide); we only assert the non-xfail baseline (no crash, has
    verdict). Re-running W805-HHH's own file remains the authoritative
    parity check.
    """

    def test_capsule_runs_on_empty_corpus_baseline(self, tmp_path, monkeypatch, cli_runner):
        proj = tmp_path / "empty_corpus_w805_hhh"
        proj.mkdir()
        (proj / ".gitignore").write_text(".roam/\n")
        (proj / "README.md").write_text("# empty\n")
        git_init(proj)
        monkeypatch.chdir(proj)
        out, rc = index_in_process(proj, "--force")
        assert rc == 0, f"index failed: {out}"
        result = _invoke_capsule(cli_runner, cwd=proj, json_mode=True)
        # Baseline invariant — must not crash, must emit a verdict.
        # (W805-HHH's xfail-strict pins are NOT re-asserted here.)
        assert result.exit_code == 0
        data = _parse_json(result)
        assert "verdict" in data.get("summary", {})


# ---------------------------------------------------------------------------
# REAL BUG — Pattern-1 variant D + CP45 lineage rule
# Pinned xfail(strict=True): fix will flip to xpass → test failure.
# ---------------------------------------------------------------------------


@pytest.mark.xfail(
    strict=True,
    reason=(
        "W805-GGGG Pattern-1-V-D bug: cmd_capsule.py:174-187 builds the "
        "capsule meta from datetime.now() (capsule.generated) but NEVER "
        "consults roam.index.manifest.latest_manifest() or "
        "roam.commands.stale_index.check_stale(). The envelope contains "
        "NO indexed_at / freshness / staleness / git_head / git_dirty_hash "
        "fields, so a capsule captured against a 30-day-old stale index is "
        "structurally indistinguishable from a capsule captured against a "
        "fresh index. Fix: stamp indexed_at + git_head + freshness "
        "verdict from latest_manifest() into the capsule meta + summary, "
        "and downgrade summary.partial_success/verdict when "
        "check_stale() reports stale. See CLAUDE.md Pattern-1-V-D + "
        "'Make fallback chains loud' (CP45/CP46)."
    ),
)
class TestSnapshotStateDisclosureBug:
    def test_capture_freshness_field_disclosed(self, fresh_indexed_project, cli_runner):
        """Pattern-1-V-D: snapshot-time freshness must surface in envelope."""
        result = _invoke_capsule(cli_runner, cwd=fresh_indexed_project, json_mode=True)
        data = _parse_json(result)
        all_keys = _all_keys(data)
        overlap = _FRESHNESS_KEYS & all_keys
        assert overlap, (
            f"Pattern-1-V-D: no freshness disclosure field present. "
            f"Looked for one of {sorted(_FRESHNESS_KEYS)}; envelope had {sorted(all_keys)}"
        )

    def test_capture_state_vs_index_state_disclosure(self, fresh_indexed_project, cli_runner):
        """The capsule must distinguish *capture time* from *index time*.

        Today ``capsule.generated`` is the wall-clock now; nothing
        records the index's indexed_at. A stale capture is
        indistinguishable from a fresh one.
        """
        result = _invoke_capsule(cli_runner, cwd=fresh_indexed_project, json_mode=True)
        data = _parse_json(result)
        all_keys = _all_keys(data)
        lineage_overlap = _INDEX_LINEAGE_KEYS & all_keys
        assert lineage_overlap, (
            f"CP45 lineage rule: capsule discloses capture time "
            f"(capsule.generated) but NOT index lineage (no indexed_at / "
            f"git_head / git_dirty_hash / schema_version). Consumers cannot "
            f"tell a fresh capture from a stale one. Envelope keys: {sorted(all_keys)}"
        )

    def test_capture_empty_corpus_distinct_from_no_index(self, fresh_indexed_project, cli_runner):
        """Pattern-1-V-D variant: snapshot-state must distinguish
        'captured against fresh index' from 'captured against degraded
        index'. Today the verdict is shape-identical in both cases.

        We assert that a fresh capsule discloses *some* index-state
        signal that a degraded capsule could not legitimately mirror.
        """
        result = _invoke_capsule(cli_runner, cwd=fresh_indexed_project, json_mode=True)
        data = _parse_json(result)
        summary = data.get("summary", {})
        # If snapshot-state were disclosed, a fresh capsule would carry
        # something like state="fresh" / freshness="current" /
        # is_stale=False. Today none of these exist.
        state_signals = (
            summary.get("snapshot_state")
            or summary.get("capture_state")
            or summary.get("freshness")
            or summary.get("index_state")
        )
        assert state_signals is not None, (
            f"Pattern-1-V-D: fresh capture emits no snapshot_state / "
            f"capture_state / freshness / index_state field. A degraded "
            f"capture would emit the same shape. summary={summary}"
        )

    def test_replay_state_of_capture_disclosed(self, stale_indexed_project, cli_runner):
        """A stale-graph capture MUST disclose its degraded lineage.

        ``stale_indexed_project`` back-dates the DB to 30 days ago.
        ``check_stale(sensitivity='medium')`` would return ``(True,
        '<reason>')`` against this fixture; the capsule command never
        calls it, so the envelope says nothing about staleness.
        """
        result = _invoke_capsule(cli_runner, cwd=stale_indexed_project, json_mode=True)
        data = _parse_json(result)
        summary = data.get("summary", {})
        all_keys = _all_keys(data)
        # The fix must surface ONE of: explicit staleness verdict in
        # summary, an is_stale boolean, or a freshness/staleness field.
        has_stale_signal = (
            "stale" in summary.get("verdict", "").lower()
            or summary.get("is_stale") is True
            or summary.get("partial_success") is True  # acceptable: stale → partial
            or bool(_FRESHNESS_KEYS & all_keys)
        )
        assert has_stale_signal, (
            f"Pattern-1-V-D: stale-index capture emits no staleness "
            f"signal. verdict={summary.get('verdict')!r}, "
            f"partial_success={summary.get('partial_success')!r}, "
            f"envelope keys={sorted(all_keys)}"
        )


# ---------------------------------------------------------------------------
# Advisory probe (passing today) — capsule.generated remains wall-clock
# This is a non-bug invariant that documents the current behaviour the
# fix must preserve when it adds the new fields alongside.
# ---------------------------------------------------------------------------


def test_capsule_generated_is_wall_clock_now(fresh_indexed_project, cli_runner):
    """``capsule.generated`` is the capture wall-clock — a fix must keep
    this field semantic stable and ADD ``indexed_at`` / freshness
    alongside, not repurpose ``generated``."""
    before = time.time()
    result = _invoke_capsule(cli_runner, cwd=fresh_indexed_project, json_mode=True)
    after = time.time()
    data = _parse_json(result)
    ts_str = data.get("capsule", {}).get("generated", "")
    assert ts_str.endswith("Z"), f"generated should be UTC-Z: {ts_str!r}"
    # We do not parse the ISO; we just assert it is within a generous
    # window of the wall clock during the test.
    from datetime import datetime, timezone

    parsed = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
    epoch = parsed.replace(tzinfo=timezone.utc).timestamp()
    # +/- 60s window (CI clock skew tolerant)
    assert before - 60 <= epoch <= after + 60, (
        f"capsule.generated drifted from wall clock: epoch={epoch} vs [before={before}, after={after}]"
    )
