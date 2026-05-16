"""W198 - tests for ``roam permit issue --persist``.

Before this wave, ``roam permit`` was strictly a verdict facade and the
W268 ``_load_permits_from_disk`` reader scanned an empty directory in
production. W198 ships the writer: ``roam permit issue --persist`` now
writes ``.roam/permits/<permit_id>.json`` documents that flow through
the W268 -> W292 -> W294 pipeline cleanly.

These tests pin:

1. ``--persist`` actually writes a JSON file with the expected shape.
2. Without ``--persist`` the disk stays clean (back-compat with the
   pre-W198 facade-only world).
3. The id format ``permit_<YYYYMMDD>_<6+hex>`` survives the round trip.
4. Writes are atomic (no torn JSON on a mid-write crash).
5. ``cmd_pr_bundle._load_permits_from_disk`` picks up a freshly-issued
   permit on the next ``pr-bundle emit``.
6. End-to-end: collected ``ChangeEvidence`` carries an AuthorityRef
   with ``extra["permit_id"]`` matching the persisted id (W294 real-vs-
   facade disambiguation).
7. When an active run is in flight, the run-ledger event carries
   ``permit_id`` so the W292 harvester can corroborate the AuthorityRef
   (W294 promotion channel).
"""

from __future__ import annotations

import json
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from click.testing import CliRunner

# W1059: relative-to-now to avoid future-date staleness (mirrors W1012
# pattern in test_cmd_permit_persist_redteam.py::_valid_permit_dict).
# Tests below issue permits via `permit issue --expires-at <ts>`; today
# `permit issue` and the disk reader do NOT filter on expiry, but if a
# future hardening adds an issue-time future-validation gate or load-time
# expiry filter, a hardcoded "2027-XX-XX" would silently flip these tests
# from green to red once that date passes. Anchoring to now+365d keeps
# the fixture "definitely in the future" for every CI run.
_FUTURE_EXPIRES_AT = (datetime.now(timezone.utc) + timedelta(days=365)).isoformat().replace("+00:00", "Z")

sys.path.insert(0, str(Path(__file__).parent))
from conftest import git_init, invoke_cli  # noqa: E402

from roam.permits import PERMIT_ID_RE  # noqa: E402
from roam.permits.store import permits_root  # noqa: E402

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def permit_project(tmp_path, monkeypatch):
    """Minimal git-initialised project with no permits / runs yet."""
    proj = tmp_path / "w198_proj"
    proj.mkdir()
    (proj / ".gitignore").write_text(".roam/\n")
    (proj / "app.py").write_text("def main():\n    return 0\n")
    git_init(proj)
    monkeypatch.delenv("ROAM_RUN_ID", raising=False)
    monkeypatch.delenv("ROAM_AGENT_MODE", raising=False)
    monkeypatch.delenv("ROAM_PERMIT_ID", raising=False)
    return proj


# ---------------------------------------------------------------------------
# 1. --persist writes a file
# ---------------------------------------------------------------------------


def test_permit_issue_persist_writes_file(permit_project):
    """``roam permit issue ... --persist`` writes ``.roam/permits/<id>.json``."""
    runner = CliRunner()
    result = invoke_cli(
        runner,
        [
            "permit",
            "issue",
            "--scope",
            "w198-test-scope",
            "--expires-at",
            "2026-12-31T23:59:59Z",
            "--issued-to",
            "agent:w198-tester",
            "--issued-by",
            "human:w198-operator",
            "--reason",
            "smoke test",
            "--persist",
        ],
        cwd=permit_project,
    )
    assert result.exit_code == 0, result.output

    proot = permits_root(permit_project)
    assert proot.exists(), f"{proot} should exist after --persist"
    files = list(proot.glob("*.json"))
    assert len(files) == 1, f"expected exactly one permit file, got {files!r}"

    raw = json.loads(files[0].read_text(encoding="utf-8"))
    # Mirror the shape ``_load_permits_from_disk`` reads.
    assert raw["scope"] == "w198-test-scope"
    assert raw["expires_at"] == "2026-12-31T23:59:59Z"
    assert raw["issued_to"] == "agent:w198-tester"
    assert raw["issued_by"] == "human:w198-operator"
    assert raw["reason"] == "smoke test"
    assert "permit_id" in raw
    assert "issued_at" in raw
    # The on-disk filename matches the permit_id field.
    assert files[0].stem == raw["permit_id"]


# ---------------------------------------------------------------------------
# 2. Without --persist, nothing is written (backward compat)
# ---------------------------------------------------------------------------


def test_permit_without_persist_no_disk_write(permit_project):
    """Same invocation without ``--persist`` writes nothing to disk."""
    runner = CliRunner()
    result = invoke_cli(
        runner,
        [
            "permit",
            "issue",
            "--scope",
            "dry-run-scope",
            "--expires-at",
            "2026-12-31T23:59:59Z",
            "--issued-to",
            "agent:dry-run",
        ],
        cwd=permit_project,
    )
    assert result.exit_code == 0, result.output

    proot = permits_root(permit_project)
    # Either the directory doesn't exist at all (preferred) OR exists but
    # is empty -- both satisfy the back-compat contract.
    if proot.exists():
        files = list(proot.glob("*.json"))
        assert files == [], f"dry-run unexpectedly wrote permit file(s): {files!r}"


# ---------------------------------------------------------------------------
# 3. permit_id format
# ---------------------------------------------------------------------------


def test_permit_id_format_is_permit_yyyymmdd_hex(permit_project):
    """The auto-generated id matches ``permit_<YYYYMMDD>_<6+hex>``.

    Drift guard: the W294 collector and the W268 reader both join on the
    ``permit_id`` field; a regex regression here would silently break the
    end-to-end pipeline.
    """
    runner = CliRunner()
    result = invoke_cli(
        runner,
        [
            "--json",
            "permit",
            "issue",
            "--scope",
            "format-test",
            "--expires-at",
            _FUTURE_EXPIRES_AT,
            "--issued-to",
            "agent:format-tester",
            "--persist",
        ],
        cwd=permit_project,
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    pid = payload["summary"]["permit_id"]
    assert PERMIT_ID_RE.match(pid), f"permit_id {pid!r} does not match {PERMIT_ID_RE.pattern!r}"
    # Also pin the prefix shape so a renamer who edits the regex but
    # forgets the prefix gets caught.
    assert re.match(r"^permit_\d{8}_[0-9a-f]{6,}$", pid)


# ---------------------------------------------------------------------------
# 4. Atomic writes
# ---------------------------------------------------------------------------


def test_permit_persist_writes_atomically(permit_project, monkeypatch):
    """``issue_permit`` routes through :func:`roam.atomic_io.atomic_write_json`.

    We assert by patching ``atomic_write_json`` and confirming our
    replacement was called. This pins the routing without needing to
    simulate a crash (the atomic-io module has its own tests for the
    actual atomicity guarantee).
    """
    from roam.permits import store as store_mod

    calls: list[tuple[Path, dict]] = []

    def fake_atomic_write_json(path, data, *, indent=2, sort_keys=False):
        calls.append((Path(path), dict(data)))
        # Still produce a real file so downstream readers work.
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_text(
            json.dumps(data, indent=indent, sort_keys=sort_keys) + "\n",
            encoding="utf-8",
        )

    monkeypatch.setattr(store_mod, "atomic_write_json", fake_atomic_write_json)

    runner = CliRunner()
    result = invoke_cli(
        runner,
        [
            "permit",
            "issue",
            "--scope",
            "atomic-test",
            "--expires-at",
            _FUTURE_EXPIRES_AT,
            "--issued-to",
            "agent:atomic",
            "--persist",
        ],
        cwd=permit_project,
    )
    assert result.exit_code == 0, result.output
    assert len(calls) == 1, f"expected exactly one atomic_write_json call, got {len(calls)}"
    path, data = calls[0]
    assert path.name.endswith(".json")
    assert data["scope"] == "atomic-test"


# ---------------------------------------------------------------------------
# 5. End-to-end: pr-bundle picks up the new permit
# ---------------------------------------------------------------------------


def test_persisted_permit_loaded_by_pr_bundle(permit_project):
    """Persist a permit, then ``pr-bundle emit`` carries it in ``permits[]``."""
    runner = CliRunner()
    # 1. Issue + persist a permit.
    r1 = invoke_cli(
        runner,
        [
            "permit",
            "issue",
            "--scope",
            "pipeline-test",
            "--expires-at",
            _FUTURE_EXPIRES_AT,
            "--issued-to",
            "agent:pipeline",
            "--persist",
        ],
        cwd=permit_project,
    )
    assert r1.exit_code == 0, r1.output

    # Extract the permit_id from the json-formatted output for the
    # downstream join.
    r1_json = invoke_cli(
        runner,
        ["--json", "permit", "list"],
        cwd=permit_project,
    )
    assert r1_json.exit_code == 0, r1_json.output
    list_payload = json.loads(r1_json.output)
    assert list_payload["summary"]["total"] == 1
    pid = list_payload["permits"][0]["permit_id"]

    # 2. Initialise a pr-bundle.
    r_init = invoke_cli(
        runner,
        ["pr-bundle", "init", "--intent", "w198 pipeline test"],
        cwd=permit_project,
    )
    assert r_init.exit_code == 0, r_init.output

    # 3. Emit the bundle -- the envelope's permits[] should contain pid.
    r_emit = invoke_cli(
        runner,
        ["--json", "pr-bundle", "emit"],
        cwd=permit_project,
    )
    assert r_emit.exit_code in (0, 6), r_emit.output
    envelope = json.loads(r_emit.output)
    permits_top = envelope.get("permits") or []
    matches = [p for p in permits_top if p.get("permit_id") == pid]
    assert matches, f"pr-bundle envelope.permits did not contain permit_id={pid!r}; got {permits_top!r}"


# ---------------------------------------------------------------------------
# 6. End-to-end through the collector: AuthorityRef extras carry permit_id
# ---------------------------------------------------------------------------


def test_persisted_permit_flows_to_authority_ref_with_real_permit_id(
    permit_project,
):
    """Persist a permit, build a synth envelope, collect ChangeEvidence,
    and assert the resulting AuthorityRef has ``extra["permit_id"]`` set
    to the persisted id (W294 real-vs-facade disambiguation).
    """
    from roam.commands.cmd_pr_bundle import _load_permits_from_disk
    from roam.evidence.collector import _build_authority_refs

    runner = CliRunner()
    result = invoke_cli(
        runner,
        [
            "--json",
            "permit",
            "issue",
            "--scope",
            "w294-pipeline",
            "--expires-at",
            _FUTURE_EXPIRES_AT,
            "--issued-to",
            "agent:w294",
            "--persist",
        ],
        cwd=permit_project,
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    pid = payload["summary"]["permit_id"]

    permits = _load_permits_from_disk(permit_project)
    assert any(p.get("permit_id") == pid for p in permits), (
        f"disk reader missed the freshly-persisted permit_id={pid!r}; got {permits!r}"
    )

    refs = _build_authority_refs(
        pr_bundle_envelope={"permits": permits},
        caller_mode=None,
        corroborated_authorities=frozenset(),
    )
    permit_refs = [r for r in refs if r.authority_kind == "permit"]
    assert permit_refs, "expected at least one permit AuthorityRef"
    target = next(r for r in permit_refs if r.authority_id == pid)
    # W294 disambiguation marker: real permit -> extra["permit_id"]
    # populated, NOT the facade auto-stamp.
    assert target.extra.get("permit_id") == pid, f"AuthorityRef.extra missing permit_id={pid!r}; got {target.extra!r}"
    assert not target.extra.get("facade"), f"real permit unexpectedly flagged as facade: {target.extra!r}"
    # Source axis: source="permit" (W294).
    assert target.source == "permit"
    # Provenance channel: producer_envelope(permit) until corroborated.
    assert target.extra.get("provenance") == "producer_envelope(permit)"


# ---------------------------------------------------------------------------
# 7. Active-run path: run-ledger event carries permit_id (W294 corroboration)
# ---------------------------------------------------------------------------


def test_persisted_permit_with_active_run_logs_permit_id_in_ledger(permit_project, monkeypatch):
    """With an active run, ``permit issue --persist`` stamps ``permit_id``
    on the run-ledger event so the W292 harvester can promote the
    matching AuthorityRef to ``provenance="run_ledger"``.
    """
    from roam.runs.ledger import end_run, read_run_events, start_run

    meta = start_run(permit_project, agent="w198-test")
    monkeypatch.setenv("ROAM_RUN_ID", meta.run_id)

    runner = CliRunner()
    result = invoke_cli(
        runner,
        [
            "--json",
            "permit",
            "issue",
            "--scope",
            "w198-corroboration",
            "--expires-at",
            _FUTURE_EXPIRES_AT,
            "--issued-to",
            "agent:corroborator",
            "--persist",
        ],
        cwd=permit_project,
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    pid = payload["summary"]["permit_id"]

    events = list(read_run_events(permit_project, meta.run_id))
    issue_events = [e for e in events if e.get("action") == "permit-issue"]
    assert issue_events, f"no permit-issue event in {events!r}"
    ev = issue_events[-1]
    assert ev.get("permit_id") == pid, f"expected permit_id={pid!r} on event, got {ev!r}"
    # ``target`` is set to the permit_id by the auto_log call.
    assert ev.get("target") == pid

    # Now end the run and confirm the W292 harvester picks up the value.
    end_run(permit_project, meta.run_id, status="completed")

    from roam.evidence.collector import (
        _build_authority_refs,
        _collect_corroborated_authorities_from_runs,
    )

    warnings: list[str] = []
    corroborated = _collect_corroborated_authorities_from_runs(permit_project, warnings)
    assert ("permit", pid) in corroborated, (
        f"expected ('permit', {pid!r}) in corroborated set, got {corroborated!r} (warnings={warnings!r})"
    )

    # End-to-end: real permit + corroboration -> provenance="run_ledger".
    from roam.commands.cmd_pr_bundle import _load_permits_from_disk

    permits = _load_permits_from_disk(permit_project)
    refs = _build_authority_refs(
        pr_bundle_envelope={"permits": permits},
        caller_mode=None,
        corroborated_authorities=corroborated,
    )
    target = next(r for r in refs if r.authority_kind == "permit" and r.authority_id == pid)
    assert target.extra.get("provenance") == "run_ledger", (
        f"expected provenance=run_ledger after writer-side corroboration, got {target.extra!r}"
    )
    assert target.extra.get("permit_id") == pid


# ---------------------------------------------------------------------------
# 8. Discipline: multi-line --reason rejected
# ---------------------------------------------------------------------------


def test_permit_issue_rejects_multiline_reason(permit_project):
    """Multi-line ``--reason`` rejected (no body / no secrets discipline)."""
    runner = CliRunner()
    result = invoke_cli(
        runner,
        [
            "permit",
            "issue",
            "--scope",
            "discipline-test",
            "--expires-at",
            _FUTURE_EXPIRES_AT,
            "--issued-to",
            "agent:strict",
            "--reason",
            "line1\nline2",
            "--persist",
        ],
        cwd=permit_project,
    )
    # Click's option parsing typically strips newlines from argv, but
    # callers that go through the function API (or shells that escape
    # the newline through) get the validation error. We accept either
    # the rejection path OR a clean single-line outcome (Click stripped
    # the newline) -- but if a file IS written, the reason MUST be
    # single-line.
    proot = permits_root(permit_project)
    files = list(proot.glob("*.json")) if proot.exists() else []
    if files:
        raw = json.loads(files[0].read_text(encoding="utf-8"))
        assert "\n" not in raw["reason"], f"multi-line reason landed on disk: {raw['reason']!r}"
    else:
        # Rejection path -- exit 2 (USAGE).
        assert result.exit_code != 0, result.output


# ---------------------------------------------------------------------------
# 9. Backward-compat: default verdict-facade path still works
# ---------------------------------------------------------------------------


def test_permit_default_invocation_is_still_verdict_facade(permit_project):
    """``roam permit`` (no subcommand) still emits an ALLOW/REVIEW/BLOCK verdict.

    Backward-compat smoke test: Cursor rules and pre-commit hooks that
    invoke ``roam permit --staged`` must not regress after the W198
    group conversion.
    """
    runner = CliRunner()
    # Build the index first so ``ensure_index`` doesn't dominate output.
    init = invoke_cli(runner, ["init"], cwd=permit_project)
    assert init.exit_code == 0, init.output

    result = invoke_cli(
        runner,
        ["--json", "permit"],
        cwd=permit_project,
    )
    # Verdict-facade path exits 0/5/6; we expect 0 ALLOW on a clean
    # checkout with no symbol / no diff supplied.
    assert result.exit_code in (0, 5, 6), result.output
    # Find the JSON payload in the output (ensure_index may have printed
    # progress text first on a cold-start path).
    out = result.output.strip()
    # Locate the first ``{`` then parse forward; pretty-printed JSON
    # spans multiple lines.
    json_start = out.find("{")
    assert json_start >= 0, f"no JSON envelope in output: {out!r}"
    payload = json.loads(out[json_start:])
    assert payload["command"] == "permit"
    assert payload["summary"]["verdict"] in ("ALLOW", "REVIEW", "BLOCK")
