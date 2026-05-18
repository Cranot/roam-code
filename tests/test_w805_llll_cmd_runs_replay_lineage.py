"""W805-LLLL -- runs-replay-axis Pattern-1-V-D lineage-disclosure pin for ``cmd_runs``.

Ninetieth-in-batch W805 sweep, ``cmd_runs.py``. FIFTH member of the
counterfactual / snapshot-state / lineage-disclosure family alongside:

- W805-BBBB cmd_simulate    (counterfactual TARGET-side resolution)
- W805-DDDD cmd_orchestrate (partition output vacuous)
- W805-GGGG cmd_capsule     (snapshot freshness disclosure)
- W805-IIII cmd_fingerprint (cross-repo fingerprint compare lineage)

Hypothesis from W805-IIII agent: ``cmd_runs`` reads the sealed HMAC-chained
ledger at ``.roam/runs/<id>/``. ``runs show`` echoes raw events. ``runs verify``
validates the cryptographic chain. NEITHER consults the symbol/file index to
disclose whether the artefacts referenced in events (``event.target``) still
resolve against the live index OR whether the index in use today is the same
index the run was logged against. The HMAC chain says "the bytes haven't been
tampered with"; the read-side envelope conflates that with "the run's
references are still meaningful." This is the canonical Pattern-1-V-D failure:
a degraded-resolution success indistinguishable from a fully-resolved one,
plus the CP45/CP46 "make fallback chains loud" / lineage-disclosure rule.

W978 first-hypothesis discipline (re-run BEFORE writing any test)
=================================================================

1. **HMAC orthogonality probe.** Confirmed by source read (cmd_runs.py:786-819
   `_verify_one_run` + ``roam.runs.signing.verify_chain``): the verify path
   walks the events bytestream against the ledger key. It NEVER calls
   ``ensure_index`` / opens the SQLite DB / resolves any ``event.target``.
   HMAC verification is correctly orthogonal to artefact resolution by
   construction — the bug class is artefact-resolution silently absent
   from the read-side envelope, NOT HMAC validation incorrect.

2. **Live probe: runs show with drifted target.** Created a project, indexed
   it, started a run, logged ``--action preflight --target
   symbol_that_does_not_exist``, ended the run, ran ``roam --json runs show``.
   Result: exit 0, ``state="completed"``, ``partial_success=False``, events
   array echoes ``target: "symbol_that_does_not_exist"`` verbatim. ZERO
   disclosure that the target is unresolved against the live index. Envelope
   keys: ``_meta / agent_contract / command / events / project / run /
   schema / schema_version / summary / version``. Summary keys: ``run_id /
   state / total / verdict / partial_success``. ZERO lineage fields
   (no git_sha_at_run / current_git_sha / run_indexed_at /
   current_indexed_at / artefacts_resolved / drift_detected).

3. **Live probe: runs verify with drifted target.** Same run as above:
   ``roam --json runs verify <id>`` returns ``state="ok"``,
   ``partial_success=False``, verdict
   ``"run X verified (1 event, all signatures match)"``. HMAC chain
   correctly validates (signature is recomputable). NO disclosure that
   ``event.target`` no longer resolves. Cryptographically clean +
   artefact-drift silent = exact Pattern-1-V-D shape.

4. **Live probe: bogus run_id.** ``roam --json runs show <bogus>`` returns
   ``state="unknown_run"`` + ``partial_success=True`` + exit 2.
   ``roam --json runs verify <bogus>`` returns the same. **No bug on
   this axis** — bogus-id resolution is exemplary.

5. **Sister W805-WW reading.** ``test_w805_ww_cmd_runs_empty_corpus.py``
   pinned the EMPTY-events.jsonl branch (``verify_chain`` trivial-pass on
   0 events emitting state="ok"). Our axis is DIFFERENT: NON-empty events,
   referenced artefacts drifted. The two pins are orthogonal.

W907 verify-cycle check
=======================

grep -i 'avoid.*cycle|circular import|kept local|would create a cycle' on
``src/roam/commands/cmd_runs.py`` + ``src/roam/runs/`` == NO MATCHES.
The lazy ``from roam.commands.cmd_pr_bundle import ...`` at cmd_runs.py:502
is a benign deferred import (cmd_pr_bundle imports only
``roam.runs.helpers.auto_log``, NOT ``cmd_runs``, so no real cycle exists
and the lazy form is not hedging — it's correctly deferring an optional
heavy import for the ``--with-pr-bundle-emit`` path only). W907 clean.

Pinned via ``xfail(strict=True)`` so a future fix is detected
(xpass -> test failure -> unwrap and seal).

Run isolation:
    python -m pytest tests/test_w805_llll_cmd_runs_replay_lineage.py -x -n 0

Regression baseline:
    python -m pytest tests/test_runs_ledger.py tests/test_runs_end_with_bundle.py \\
        tests/test_runs_auto_log.py -x -n 0

Sister parity:
    python -m pytest tests/test_w805_iiii_cmd_fingerprint_snapshot_state.py \\
        tests/test_w805_gggg_cmd_capsule_snapshot_state.py -x -n 0
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

_CMD_RUNS_SPEC = importlib.util.find_spec("roam.commands.cmd_runs")
_RUNS_LEDGER_SPEC = importlib.util.find_spec("roam.runs.ledger")
_RUNS_SIGNING_SPEC = importlib.util.find_spec("roam.runs.signing")


def test_command_and_substrate_exist():
    """W978/W907 gate: cmd_runs + ledger + signing substrates import cleanly."""
    if _CMD_RUNS_SPEC is None:
        pytest.skip("roam.commands.cmd_runs not installed")
    assert _RUNS_LEDGER_SPEC is not None, "roam.runs.ledger missing"
    assert _RUNS_SIGNING_SPEC is not None, "roam.runs.signing missing"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def cli_runner():
    return CliRunner()


def _make_repo(tmp_path: Path, name: str, files: dict) -> Path:
    proj = tmp_path / name
    proj.mkdir()
    (proj / ".gitignore").write_text(".roam/\n", encoding="utf-8")
    for rel, content in files.items():
        fp = proj / rel
        fp.parent.mkdir(parents=True, exist_ok=True)
        fp.write_text(content, encoding="utf-8")
    git_init(proj)
    return proj


@pytest.fixture
def drifted_run_project(tmp_path, monkeypatch):
    """Indexed project + a completed run whose ``event.target`` references
    a symbol that does NOT exist in the current index.

    Drives the artefact-resolution drift axis: the HMAC chain is clean
    (one signed preflight event), but the target does not resolve. Returns
    ``(proj, run_id)``.
    """
    proj = _make_repo(
        tmp_path,
        "drifted_run_llll",
        {"app.py": "def alpha():\n    return 1\n"},
    )
    monkeypatch.chdir(proj)
    out, rc = index_in_process(proj, "--force")
    assert rc == 0, f"index failed: {out}"

    from roam.cli import cli

    runner = CliRunner()
    # Start
    sr = runner.invoke(cli, ["--json", "runs", "start", "--agent", "w805llll"], catch_exceptions=False)
    assert sr.exit_code == 0, sr.output
    run_id = json.loads(sr.output)["summary"]["run_id"]
    # Log an event whose target is a symbol that does NOT exist in the index.
    lr = runner.invoke(
        cli,
        [
            "--json",
            "runs",
            "log",
            "--run-id",
            run_id,
            "--action",
            "preflight",
            "--target",
            "symbol_that_does_not_exist_in_index",
            "--verdict",
            "ok",
        ],
        catch_exceptions=False,
    )
    assert lr.exit_code == 0, lr.output
    # End
    er = runner.invoke(cli, ["--json", "runs", "end", "--run-id", run_id], catch_exceptions=False)
    assert er.exit_code == 0, er.output
    return proj, run_id


@pytest.fixture
def reindexed_run_project(tmp_path, monkeypatch):
    """Indexed project + completed run, then index is regenerated AFTER the
    run closed. Drives the index-lineage axis: was the index against which
    the run was logged the same index a subsequent reader sees?

    Returns ``(proj, run_id)``.
    """
    proj = _make_repo(
        tmp_path,
        "reindexed_run_llll",
        {"app.py": "def alpha():\n    return 1\n"},
    )
    monkeypatch.chdir(proj)
    out, rc = index_in_process(proj, "--force")
    assert rc == 0, f"index failed at t0: {out}"

    from roam.cli import cli

    runner = CliRunner()
    sr = runner.invoke(cli, ["--json", "runs", "start", "--agent", "w805llll"], catch_exceptions=False)
    assert sr.exit_code == 0, sr.output
    run_id = json.loads(sr.output)["summary"]["run_id"]
    lr = runner.invoke(
        cli,
        [
            "--json",
            "runs",
            "log",
            "--run-id",
            run_id,
            "--action",
            "preflight",
            "--target",
            "alpha",
            "--verdict",
            "ok",
        ],
        catch_exceptions=False,
    )
    assert lr.exit_code == 0, lr.output
    er = runner.invoke(cli, ["--json", "runs", "end", "--run-id", run_id], catch_exceptions=False)
    assert er.exit_code == 0, er.output
    # Regenerate the index — the run-logged-state is now divorced from the
    # current index in time (even if symbols still exist, the lineage chain
    # has no field linking the two).
    out, rc = index_in_process(proj, "--force")
    assert rc == 0, f"index failed at t1: {out}"
    return proj, run_id


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _invoke(runner, cwd: Path, args, json_mode: bool = True):
    from roam.cli import cli

    full = []
    if json_mode:
        full.append("--json")
    full.extend(args)
    old_cwd = os.getcwd()
    try:
        os.chdir(str(cwd))
        result = runner.invoke(cli, full, catch_exceptions=False)
    finally:
        os.chdir(old_cwd)
    return result


def _parse_json(result):
    assert result.exit_code in (0, 2), f"unexpected exit={result.exit_code}:\n{result.output}"
    raw = (result.output or "").lstrip()
    assert raw.startswith("{"), f"expected JSON envelope, got:\n{result.output!r}"
    decoder = json.JSONDecoder()
    obj, _end = decoder.raw_decode(raw)
    return obj


def _all_envelope_keys(data: dict) -> set:
    keys = set(data.keys())
    keys |= set((data.get("summary") or {}).keys())
    keys |= set((data.get("run") or {}).keys())
    # also fold per-event keys for the resolution axis
    for ev in data.get("events", []) or []:
        if isinstance(ev, dict):
            keys |= {f"event.{k}" for k in ev.keys()}
    return keys


_LINEAGE_KEYS = {
    # Run-side lineage: when was the run logged, against what index/commit?
    "run_indexed_at",
    "indexed_at_run",
    "indexed_at",
    "git_sha_at_run",
    "git_head_at_run",
    "commit_sha_at_run",
    "index_schema_version_at_run",
    "index_manifest_at_run",
    # Reader-side lineage: what does the index look like NOW?
    "current_indexed_at",
    "current_git_sha",
    "current_git_head",
    "index_drift",
    "index_lineage",
    "lineage_drift",
}

_RESOLUTION_KEYS = {
    # Per-event resolution disclosure: do the referenced artefacts still
    # resolve against the current index?
    "artefacts_resolved",
    "artifacts_resolved",
    "targets_resolved",
    "target_resolution",
    "drifted_targets",
    "resolution",
    "unresolved_targets",
    "missing_artefacts",
    "missing_artifacts",
    # Also accept the equivalents stamped onto each event dict:
    "event.resolution",
    "event.target_resolved",
    "event.target_resolution",
}


# ---------------------------------------------------------------------------
# SMOKE — pin the existing-good axes (bogus run_id resolution + HMAC chain
# orthogonality from artefact resolution). These must stay green forever.
# ---------------------------------------------------------------------------


class TestBogusRunIdResolutionExemplary:
    """``runs show <bogus>`` + ``runs verify <bogus>`` are exemplary today.

    Pin them as a regression guard — a future "fix" that breaks this
    happy-path would be a worse bug than the lineage-disclosure miss.
    """

    def test_show_bogus_run_id(self, tmp_path, monkeypatch, cli_runner):
        proj = _make_repo(
            tmp_path,
            "bogus_show_llll",
            {"app.py": "def f():\n    return 0\n"},
        )
        monkeypatch.chdir(proj)
        r = _invoke(cli_runner, proj, ["runs", "show", "run_99999999_zzzzzz"])
        assert r.exit_code == 2, f"bogus show should exit 2; got {r.exit_code}\n{r.output}"
        data = _parse_json(r)
        assert (data.get("summary") or {}).get("state") == "unknown_run"
        assert (data.get("summary") or {}).get("partial_success") is True

    def test_verify_bogus_run_id(self, tmp_path, monkeypatch, cli_runner):
        proj = _make_repo(
            tmp_path,
            "bogus_verify_llll",
            {"app.py": "def f():\n    return 0\n"},
        )
        monkeypatch.chdir(proj)
        r = _invoke(cli_runner, proj, ["runs", "verify", "run_99999999_zzzzzz"])
        assert r.exit_code == 2, f"bogus verify should exit 2; got {r.exit_code}\n{r.output}"
        data = _parse_json(r)
        assert (data.get("summary") or {}).get("state") == "unknown_run"


class TestHmacChainOrthogonalFromArtefactResolution:
    """HMAC verification correctly walks bytes only — NOT artefact resolution.

    This is the W978 orthogonality invariant: a future fix that makes
    ``runs verify`` reach into the live index would *change the meaning* of
    the chain check from "the bytes weren't tampered with" to "the bytes
    weren't tampered with AND every referenced artefact still resolves",
    which would silently break callers that depend on the byte-only
    semantics. The fix-template for the lineage-disclosure bug must add
    NEW fields, not change the meaning of ``state="ok"``.
    """

    def test_verify_chain_signature_is_byte_only(self):
        """``verify_chain`` is a pure HMAC walker — it MUST NOT depend on
        the SQLite index. Probe the import surface to confirm."""
        import inspect

        from roam.runs.signing import verify_chain

        src = inspect.getsource(verify_chain)
        # No DB / index imports must appear inside the verify function.
        forbidden = ("from roam.db", "from roam.commands.resolve", "ensure_index")
        for needle in forbidden:
            assert needle not in src, (
                f"W805-LLLL orthogonality invariant broken: verify_chain "
                f"contains {needle!r}; the HMAC layer must stay decoupled "
                f"from artefact resolution. Fix the lineage-disclosure bug "
                f"by ADDING a new resolution-disclosure field, not by "
                f"changing HMAC semantics."
            )

    def test_verify_with_drifted_target_still_state_ok(self, drifted_run_project, cli_runner):
        """HMAC orthogonality: a run whose event references a non-existent
        symbol still has ``state="ok"`` for the HMAC chain check. The bug
        is that NOTHING ELSE in the envelope discloses the drift, not that
        HMAC validation is wrong."""
        proj, run_id = drifted_run_project
        r = _invoke(cli_runner, proj, ["runs", "verify", run_id])
        assert r.exit_code == 0, r.output
        data = _parse_json(r)
        summary = data.get("summary") or {}
        # HMAC chain IS valid — this is correct.
        assert summary.get("state") == "ok"
        assert summary.get("first_tamper_at_seq") is None
        assert summary.get("events_verified") == 1


# ---------------------------------------------------------------------------
# Sister-family invariant cross-checks (must stay green; do NOT re-assert
# the sister files' xfail-strict claims to avoid collision).
# ---------------------------------------------------------------------------


class TestW805IiiiInvariantsPreserved:
    """W805-IIII (cmd_fingerprint snapshot-state) sister cross-check.

    Baseline: ``roam fingerprint`` emits a parseable envelope. We do NOT
    re-assert W805-IIII's xfail-strict lineage pins.
    """

    def test_fingerprint_baseline_parseable(self, tmp_path, monkeypatch, cli_runner):
        proj = _make_repo(
            tmp_path,
            "w805_iiii_parity_llll",
            {"app.py": "def x():\n    return 1\n"},
        )
        monkeypatch.chdir(proj)
        out, rc = index_in_process(proj, "--force")
        assert rc == 0, f"index failed: {out}"
        r = _invoke(cli_runner, proj, ["fingerprint"])
        assert r.exit_code == 0, r.output
        data = _parse_json(r)
        assert "summary" in data
        assert "verdict" in data["summary"]


class TestW805GgggInvariantsPreserved:
    """W805-GGGG (cmd_capsule snapshot-state) sister cross-check.

    Baseline: ``roam capsule`` emits a ``capsule.generated`` wall-clock.
    We do NOT re-assert W805-GGGG's xfail-strict pins.
    """

    def test_capsule_baseline_parseable(self, tmp_path, monkeypatch, cli_runner):
        proj = _make_repo(
            tmp_path,
            "w805_gggg_parity_llll",
            {"app.py": "def x():\n    return 1\n"},
        )
        monkeypatch.chdir(proj)
        out, rc = index_in_process(proj, "--force")
        assert rc == 0, f"index failed: {out}"

        from roam.commands.cmd_capsule import capsule

        old_cwd = os.getcwd()
        try:
            os.chdir(str(proj))
            r = cli_runner.invoke(capsule, [], obj={"json": True}, catch_exceptions=False)
        finally:
            os.chdir(old_cwd)
        assert r.exit_code == 0
        data = json.loads(r.output)
        assert "summary" in data
        assert "verdict" in data["summary"]
        assert "generated" in data.get("capsule", {})


# ---------------------------------------------------------------------------
# REAL BUG — Pattern-1-V-D + CP45/CP46 lineage-disclosure rule
# Pinned xfail(strict=True): fix will flip to xpass → test failure → unwrap.
# ---------------------------------------------------------------------------


@pytest.mark.xfail(
    strict=True,
    reason=(
        "W805-LLLL Pattern-1-V-D bug: cmd_runs.py:686-777 (runs show) and "
        "cmd_runs.py:822-1133 (runs verify) emit success envelopes on runs "
        "whose event.target references no longer resolve against the live "
        "index — and on runs logged against an older index now superseded "
        "by a reindex. The HMAC chain correctly validates (verify_chain is "
        "byte-only by design — see TestHmacChainOrthogonalFromArtefactResolution); "
        "the bug is that NEITHER read path discloses ANY lineage "
        "(no git_sha_at_run / run_indexed_at / current_indexed_at / "
        "index_drift) NOR resolution state (no artefacts_resolved / "
        "drifted_targets / target_resolution per-event field). A run whose "
        "target was 'symbol_X' (since renamed) is structurally "
        "indistinguishable from a run whose target still resolves. "
        "Fix: stamp run_indexed_at + git_sha_at_run into meta.json at "
        "start_run (W14.2-style additive extension; meta.extra already "
        "exists for forward-compat fields); on the read side, resolve each "
        "event.target against the live index and surface drifted_targets[] "
        "+ partial_success=True when ANY target fails to resolve. "
        "See CLAUDE.md Pattern-1-V-D + 'Make fallback chains loud' (CP45/CP46) "
        "+ W805-IIII sister pin (cross-repo fingerprint comparison)."
    ),
)
class TestRunsLineageAndResolutionDisclosureBug:
    def test_runs_show_discloses_target_resolution(self, drifted_run_project, cli_runner):
        """Pattern-1-V-D: ``runs show`` must disclose whether each event's
        target still resolves against the current index. Today the events
        array echoes the target string verbatim with no resolution flag,
        so an agent reading the envelope cannot tell a stale reference
        from a live one."""
        proj, run_id = drifted_run_project
        r = _invoke(cli_runner, proj, ["runs", "show", run_id])
        assert r.exit_code == 0, r.output
        data = _parse_json(r)
        keys = _all_envelope_keys(data)
        overlap = _RESOLUTION_KEYS & keys
        assert overlap, (
            f"Pattern-1-V-D: runs show envelope discloses NO target "
            f"resolution for a drifted reference. Looked for one of "
            f"{sorted(_RESOLUTION_KEYS)}; envelope had {sorted(keys)}."
        )

    def test_runs_show_discloses_index_lineage(self, drifted_run_project, cli_runner):
        """CP45 lineage rule: ``runs show`` must record WHEN/AGAINST-WHAT
        the run was logged. Today nothing in the envelope ties the run to
        an index manifest / git commit / indexed_at timestamp."""
        proj, run_id = drifted_run_project
        r = _invoke(cli_runner, proj, ["runs", "show", run_id])
        assert r.exit_code == 0, r.output
        data = _parse_json(r)
        keys = _all_envelope_keys(data)
        overlap = _LINEAGE_KEYS & keys
        assert overlap, (
            f"CP45 lineage: runs show envelope has NO run_indexed_at / "
            f"git_sha_at_run / current_indexed_at / index_drift field. "
            f"Looked for one of {sorted(_LINEAGE_KEYS)}; envelope had "
            f"{sorted(keys)}."
        )

    def test_runs_verify_flags_drifted_targets(self, drifted_run_project, cli_runner):
        """Pattern-1-V-D: ``runs verify`` emits ``state="ok"`` +
        ``partial_success=False`` on a run whose event.target references
        a symbol that does not exist in the live index. HMAC integrity is
        correctly preserved (see TestHmacChainOrthogonalFromArtefactResolution),
        but the overall envelope MUST signal artefact drift via a new
        field — never by changing HMAC semantics."""
        proj, run_id = drifted_run_project
        r = _invoke(cli_runner, proj, ["runs", "verify", run_id])
        assert r.exit_code == 0, r.output
        data = _parse_json(r)
        summary = data.get("summary") or {}
        keys = _all_envelope_keys(data)
        # A fix would either flip partial_success=True, add an explicit
        # artefacts_resolved-style summary field, OR surface drifted
        # targets on a new envelope key. ``state`` must stay "ok" (HMAC
        # orthogonality); the disclosure is additive.
        drift_signal = (
            summary.get("partial_success") is True
            or bool(_RESOLUTION_KEYS & keys)
            or summary.get("artefacts_resolved") is False
            or summary.get("artifacts_resolved") is False
            or summary.get("targets_resolved") is False
            or bool(summary.get("drifted_targets") or [])
        )
        assert drift_signal, (
            f"Pattern-1-V-D: runs verify on a drifted-artefact run emits "
            f"state={summary.get('state')!r}, "
            f"partial_success={summary.get('partial_success')!r}, with no "
            f"artefacts_resolved / drifted_targets / target_resolution "
            f"field. envelope keys={sorted(keys)}."
        )

    def test_runs_show_across_reindex_discloses_drift(self, reindexed_run_project, cli_runner):
        """Pattern-1-V-D + CP45: a run logged against the index at t0 and
        read after a reindex at t1 must disclose the index-lineage drift.
        Even when targets still resolve (the symbol wasn't renamed), the
        index manifest changed — an agent that depends on run-time
        snapshots of the index needs to know."""
        proj, run_id = reindexed_run_project
        r = _invoke(cli_runner, proj, ["runs", "show", run_id])
        assert r.exit_code == 0, r.output
        data = _parse_json(r)
        summary = data.get("summary") or {}
        keys = _all_envelope_keys(data)
        lineage_signal = (
            bool(_LINEAGE_KEYS & keys)
            or summary.get("index_drift") is True
            or summary.get("partial_success") is True
            or "indexed_at" in str(data.get("run") or {})
        )
        assert lineage_signal, (
            f"Pattern-1-V-D + CP45: runs show across a reindex discloses "
            f"no index-lineage drift. summary={summary}, envelope "
            f"keys={sorted(keys)}."
        )

    def test_runs_verify_envelope_carries_lineage(self, drifted_run_project, cli_runner):
        """CP45 lineage: ``runs verify`` envelope must record the index/git
        state both at run-time AND at verify-time so an auditor can confirm
        the two are aligned (or explicitly know they're not)."""
        proj, run_id = drifted_run_project
        r = _invoke(cli_runner, proj, ["runs", "verify", run_id])
        assert r.exit_code == 0, r.output
        data = _parse_json(r)
        keys = _all_envelope_keys(data)
        overlap = _LINEAGE_KEYS & keys
        assert overlap, (
            f"CP45 lineage: runs verify envelope has NO run_indexed_at / "
            f"git_sha_at_run / current_indexed_at / index_drift field. "
            f"Looked for one of {sorted(_LINEAGE_KEYS)}; envelope had "
            f"{sorted(keys)}."
        )


# ---------------------------------------------------------------------------
# Advisory probe (passing today) — documents the current pass-through
# semantics the fix MUST preserve (additive-only fix).
# ---------------------------------------------------------------------------


def test_runs_show_today_echoes_event_target_verbatim(drifted_run_project, cli_runner):
    """The today-shape of ``runs show`` echoes each event's target string
    verbatim under ``events[].target``. The fix must KEEP this field
    untouched (additive disclosure only — never mutate the historical
    event record)."""
    proj, run_id = drifted_run_project
    r = _invoke(cli_runner, proj, ["runs", "show", run_id])
    assert r.exit_code == 0, r.output
    data = _parse_json(r)
    events = data.get("events") or []
    assert len(events) == 1
    assert events[0].get("target") == "symbol_that_does_not_exist_in_index"
    assert events[0].get("action") == "preflight"
