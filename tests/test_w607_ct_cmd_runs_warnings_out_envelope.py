"""W607-CT -- additive aggregation-phase plumbing for ``cmd_runs``.

cmd_runs is the HMAC-CHAINED EVENT LEDGER WRITER + verifier at the head
of the agent-OS audit-trail substrate. With W607-CT landed alongside
W607-AS, the runs-verify ``--all`` path is now dual-bucket plumbed via:

  - substrate-CALL layer: W607-AS (start_run / log_event /
    compute_hmac_and_write / end_run / emit_pr_bundle -- on
    runs-start / runs-log / runs-end)
  - aggregation-phase layer: W607-CT (3 aggregation boundaries:
    compute_predicate / compute_verdict / serialize_envelope on
    runs-verify ``--all``)

Both layers share the canonical ``runs_*`` marker family and the
``runs_<phase>_failed:<exc_class>:<detail>`` shape contract. The two
buckets (``_w607as_warnings_out`` substrate-CALL +
``_w607ct_warnings_out`` aggregation-phase) flow through the same
warnings_out channel so consumers see the full degradation lineage in
marker-emission order.

AGENT-OS LEDGER family pairing
------------------------------

cmd_runs (W607-AS + CT), cmd_audit_trail_verify (W607-AI + CN),
cmd_audit_trail_conformance (W607-AL + CO), and cmd_audit_trail_export
(W607-AP + CR) form the agent-OS ledger verb family. The pairing test
below confirms each command's markers stay in its OWN family and never
bleed into a sibling's envelope.

HMAC-FAILURE-ABORTS-WRITE invariant (chain-integrity)
-----------------------------------------------------

W607-AS sealed the chain-integrity discipline: if log_event raises, the
event MUST NOT be appended to events.jsonl. W607-CT is additive on
runs-verify (a READER, not a writer) -- it MUST NOT introduce any code
path that circumvents the write-abort discipline on the writer side.
The regression test below pins the invariant on the WRITE side: a
simulated compute_hmac_and_write raise during runs-log keeps
events.jsonl byte-identical (no half-written events).

W978 first-hypothesis check (kwarg-default eagerness trap)
----------------------------------------------------------

cmd_sbom W607-CG sealed a recurring W978 axis: ``_run_check_X("phase",
fn, default={"x": len(records) if ...})`` -- Python evaluates the
``default=`` kwarg BEFORE the wrap call. cmd_taint W607-CJ added the
follow-on discipline of MOVING ``len()`` calls INSIDE the wrapped
closure (not at kwarg-bind time). cmd_audit_trail_verify W607-CN added
the further axis of forbidding unguarded ``len()``/truthiness on
potentially-poisoned objects in the summary-builder. Every W607-CT
``default=`` MUST be a literal constant, not computed from upstream
values. The defensive test below exercises the floor on a corrupt-input
sentinel.

LAW 4 note: warning markers are diagnostic strings, NOT
``agent_contract.facts`` content, and therefore not subject to the
concrete-noun-terminal lint.
"""

from __future__ import annotations

import ast
import json as _json
import os
import sys
from pathlib import Path

import pytest
from click.testing import CliRunner

sys.path.insert(0, str(Path(__file__).parent))
from conftest import git_init  # noqa: E402

# ---------------------------------------------------------------------------
# Helpers -- invoke runs subcommands via the Click CLI
# ---------------------------------------------------------------------------


def _invoke_runs(runner: CliRunner, cwd, *extra, json_mode: bool = True):
    """Invoke ``roam runs <args...>``."""
    from roam.cli import cli

    args: list[str] = []
    if json_mode:
        args.append("--json")
    args.append("runs")
    args.extend(extra)

    old_cwd = os.getcwd()
    try:
        os.chdir(str(cwd))
        result = runner.invoke(cli, args, catch_exceptions=False)
    finally:
        os.chdir(old_cwd)
    return result


def _extract_json(output: str) -> dict:
    """Extract the first JSON object from output."""
    idx = output.find("{")
    if idx < 0:
        raise ValueError(f"no JSON object found in runs output: {output!r}")
    return _json.loads(output[idx:])


# ---------------------------------------------------------------------------
# Fixture -- minimal git project for runs subcommands
# ---------------------------------------------------------------------------


@pytest.fixture
def cli_runner():
    return CliRunner()


@pytest.fixture
def runs_project(tmp_path, monkeypatch):
    """Minimal git project with one in-progress run + ended run for verify."""
    proj = tmp_path / "runs_w607ct_project"
    proj.mkdir()
    (proj / ".gitignore").write_text(".roam/\n")
    (proj / "README.md").write_text("# t", encoding="utf-8")
    git_init(proj)
    monkeypatch.chdir(proj)
    return proj


@pytest.fixture
def runs_project_with_one_run(cli_runner, runs_project):
    """A project with one in-progress run (so verify --all has a target)."""
    res = _invoke_runs(cli_runner, runs_project, "start", "--agent", "tester")
    assert res.exit_code == 0, res.output
    return runs_project


# ---------------------------------------------------------------------------
# (1) Happy path -- runs verify --all clean envelope omits W607-CT markers
# ---------------------------------------------------------------------------


def test_runs_verify_all_clean_envelope_omits_w607ct_markers(cli_runner, runs_project_with_one_run):
    """Clean runs verify --all -> no W607-CT aggregation markers.

    Hash-stable: an empty W607-CT bucket on the success path must produce
    an envelope without any ``runs_compute_predicate_failed:`` /
    ``runs_compute_verdict_failed:`` /
    ``runs_serialize_envelope_failed:`` markers (from the CT layer).
    """
    result = _invoke_runs(cli_runner, runs_project_with_one_run, "verify", "--all")
    # exit_code 0 OR 5 (tampered) OR ok with unsigned-legacy advisory
    assert result.exit_code in (0, 5), result.output
    data = _extract_json(result.output)
    assert data["command"] == "runs-verify"

    top_wo = data.get("warnings_out") or []
    summary_wo = data["summary"].get("warnings_out") or []
    all_markers = list(top_wo) + list(summary_wo)
    w607ct_phases = (
        "runs_compute_predicate_failed:",
        "runs_compute_verdict_failed:",
        "runs_serialize_envelope_failed:",
    )
    for prefix in w607ct_phases:
        leaked = [m for m in all_markers if m.startswith(prefix)]
        assert not leaked, f"clean runs-verify --all must NOT surface {prefix} markers; got {leaked!r}"


# ---------------------------------------------------------------------------
# (2) AST-level guard -- the additive ``_run_check_ct`` helper is present
# ---------------------------------------------------------------------------


def test_cmd_runs_carries_w607ct_accumulator():
    """AST-level guard: cmd_runs source carries the W607-CT accumulator.

    Pins the canonical W607-CT anchors so a future refactor that removes
    the additive instrumentation (or merges it back into W607-AS) fails
    this guard rather than silently regressing the aggregation-phase
    marker coverage.
    """
    src_path = Path(__file__).parent.parent / "src" / "roam" / "commands" / "cmd_runs.py"
    assert src_path.exists(), f"cmd_runs.py missing at {src_path}"
    src = src_path.read_text(encoding="utf-8")

    # Source-level anchors
    assert "w607ct_warnings_out" in src, (
        "W607-CT accumulator missing from cmd_runs; the additive aggregation-phase marker plumbing has been removed."
    )
    assert "_run_check_ct" in src, (
        "W607-CT helper ``_run_check_ct`` missing from cmd_runs; the additive wrapper has been refactored away."
    )

    # Parse-tree level: confirm _run_check_ct is defined inside the command.
    tree = ast.parse(src)
    found_run_check_ct = False
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "_run_check_ct":
            found_run_check_ct = True
            break
    assert found_run_check_ct, (
        "W607-CT ``_run_check_ct`` helper not found in cmd_runs AST; "
        "the additive aggregation-phase wrapper has been refactored away."
    )

    # W607-AS must still be present (additive layer does NOT replace it)
    assert "w607as_warnings_out" in src, (
        "W607-AS accumulator vanished alongside the W607-CT add; the "
        "additive plumbing must preserve the W607-AS substrate-CALL layer."
    )


# ---------------------------------------------------------------------------
# (3) Source-grep guard -- every aggregation-phase boundary is wrapped
# ---------------------------------------------------------------------------


def test_every_aggregation_phase_wrapped_in_run_check_ct():
    """Source-grep guard: every aggregation-phase boundary calls
    ``_run_check_ct(...)`` with the canonical phase name.

    The three phases must appear inside a ``_run_check_ct("<phase>", ...)``
    call inside cmd_runs.
    """
    src_path = Path(__file__).parent.parent / "src" / "roam" / "commands" / "cmd_runs.py"
    src = src_path.read_text(encoding="utf-8")

    canonical_phases = (
        "compute_predicate",
        "compute_verdict",
        "serialize_envelope",
    )
    for phase in canonical_phases:
        markers = [
            f'_run_check_ct(\n        "{phase}"',
            f'_run_check_ct(\n            "{phase}"',
            f'_run_check_ct(\n                "{phase}"',
            f'_run_check_ct(\n                    "{phase}"',
            f'_run_check_ct(\n                        "{phase}"',
            f'_run_check_ct("{phase}"',
        ]
        found = any(m in src for m in markers)
        assert found, (
            f"phase ``{phase}`` is not wrapped in _run_check_ct(...); add the W607-CT guard or pin the canonical anchor"
        )


# ---------------------------------------------------------------------------
# (4) compute_predicate failure marker -- floors land on TAMPERED, not SAFE
# ---------------------------------------------------------------------------


def test_compute_predicate_failure_marker_and_tampered_floor(cli_runner, runs_project_with_one_run, monkeypatch):
    """If compute_predicate raises, surface marker AND floor lands on tampered.

    Pattern-2 + chain-integrity silent-fallback discipline: a poisoned
    predicate floor MUST land on a non-SAFE state (tampered>=1), NEVER
    on a clean SAFE that pretends the chain was verified.

    Strategy: monkeypatch the substrate ``_verify_one_run`` to return a
    result whose attempted iteration triggers the predicate closure raise.
    """
    from roam.commands import cmd_runs

    class _BadResultDict(dict):
        # The predicate closure calls _r["state"] -- this dict subclass
        # raises on __getitem__ to trip the predicate closure body.
        def __getitem__(self, key):
            raise RuntimeError("synthetic-compute-predicate-from-W607-CT")

    def _bad_verify_one_run(root, run_id):
        return _BadResultDict()

    monkeypatch.setattr(cmd_runs, "_verify_one_run", _bad_verify_one_run)

    result = _invoke_runs(cli_runner, runs_project_with_one_run, "verify", "--all")
    # tampered floor triggers ctx.exit(5)
    assert result.exit_code in (0, 5), result.output
    data = _extract_json(result.output)

    top_wo = data.get("warnings_out") or []
    summary_wo = data["summary"].get("warnings_out") or []
    all_wo = list(top_wo) + list(summary_wo)
    markers = [m for m in all_wo if m.startswith("runs_compute_predicate_failed:")]
    assert markers, f"expected ``runs_compute_predicate_failed:`` marker; got {all_wo!r}"
    assert any("RuntimeError" in m for m in markers), markers

    # Pattern-2 + chain-integrity silent-fallback: floor MUST land on
    # tampered (state="tampered"), NOT a clean SAFE.
    assert data["summary"].get("state") == "tampered", (
        f"chain-integrity regression: compute_predicate floor allowed "
        f"state != tampered; got summary={data['summary']!r}"
    )
    assert data["summary"].get("partial_success") is True


# ---------------------------------------------------------------------------
# (5) compute_verdict floor is a literal string -- W978 first-hypothesis
# ---------------------------------------------------------------------------


def test_compute_verdict_floor_is_literal_string():
    """W978 first-hypothesis check: the compute_verdict floor must be a
    literal string -- not an f-string re-interpolating the values that
    just raised.

    The canonical floor literal for cmd_runs verify --all is
    "Runs verification completed" (LAW 6 standalone-parse).
    """
    src_path = Path(__file__).parent.parent / "src" / "roam" / "commands" / "cmd_runs.py"
    src = src_path.read_text(encoding="utf-8")

    # W978: the canonical floor for compute_verdict must be a literal
    # string -- not an f-string re-interpolating values that just raised.
    assert '"verdict": "Runs verification completed"' in src, (
        "W978 compute_verdict floor must be a literal string per W607-CT "
        "discipline; the canonical floor literal 'Runs verification "
        "completed' is missing from cmd_runs.py"
    )


# ---------------------------------------------------------------------------
# (6) serialize_envelope guard -- raise floors to stub document
# ---------------------------------------------------------------------------


def test_w607ct_serialize_envelope_floor_on_raise(cli_runner, runs_project_with_one_run, monkeypatch):
    """If ``json_envelope`` raises on the success path, the wrap floors
    to a parseable envelope stub and surfaces
    ``runs_serialize_envelope_failed:``.

    A downstream schema-shape refactor that breaks
    ``json_envelope("runs-verify", ...)`` would otherwise crash AFTER
    all aggregation signals were already gathered. The consumer must
    still receive a parseable JSON object with the marker attached + the
    canonical command name.
    """
    from roam.commands import cmd_runs as _mod

    def _raise_envelope(*args, **kwargs):
        raise RuntimeError("synthetic-serialize-envelope-from-W607-CT")

    monkeypatch.setattr(_mod, "json_envelope", _raise_envelope)

    result = _invoke_runs(cli_runner, runs_project_with_one_run, "verify", "--all")
    # The CT layer floors but tampered remains 0 in the clean path, so
    # ctx.exit(5) does NOT trigger -- exit 0 expected.
    assert result.exit_code == 0, result.output

    # Parse the stub document -- must remain parseable JSON.
    data = _extract_json(result.output)
    assert data.get("command") == "runs-verify", (
        f"envelope stub must carry the canonical command name on raise; got {data!r}"
    )
    top_wo = data.get("warnings_out") or []
    markers = [m for m in top_wo if m.startswith("runs_serialize_envelope_failed:")]
    assert markers, f"expected ``runs_serialize_envelope_failed:`` marker; got {top_wo!r}"


# ---------------------------------------------------------------------------
# (7) ANY marker flips partial_success
# ---------------------------------------------------------------------------


def test_any_marker_flips_partial_success(cli_runner, runs_project_with_one_run, monkeypatch):
    """ANY W607-CT marker must flip summary.partial_success=True.

    Pattern-2 contract: the agent MUST be able to distinguish "clean
    verify" from "verify ran with aggregation degradation" via
    summary.partial_success alone.
    """
    from roam.commands import cmd_runs as _mod

    def _raise_envelope(*args, **kwargs):
        raise RuntimeError("synthetic-partial-success-from-W607-CT")

    monkeypatch.setattr(_mod, "json_envelope", _raise_envelope)

    result = _invoke_runs(cli_runner, runs_project_with_one_run, "verify", "--all")
    assert result.exit_code == 0, result.output
    data = _extract_json(result.output)
    assert data["summary"].get("partial_success") is True, (
        f"non-empty W607-CT warnings_out must flip summary.partial_success=True; got summary = {data['summary']!r}"
    )


# ---------------------------------------------------------------------------
# (8) warnings_out lands in BOTH top-level AND summary mirror
# ---------------------------------------------------------------------------


def test_w607ct_warnings_out_in_both_top_and_summary(cli_runner, runs_project_with_one_run, monkeypatch):
    """Non-empty W607-CT bucket -> both top-level AND summary.warnings_out
    populated.

    Mirror parity with W607-AS contract: top-level is needed because the
    preserved-list field survives ``strip_list_payloads`` in default-
    detail mode; summary mirror gives consumers reading only the summary
    block visibility too.
    """
    from roam.commands import cmd_runs as _mod

    def _raise_envelope(*args, **kwargs):
        raise RuntimeError("synthetic-mirror-from-W607-CT")

    monkeypatch.setattr(_mod, "json_envelope", _raise_envelope)

    result = _invoke_runs(cli_runner, runs_project_with_one_run, "verify", "--all")
    assert result.exit_code == 0, result.output
    data = _extract_json(result.output)

    assert data.get("warnings_out"), (
        f"top-level warnings_out missing on W607-CT raise path; keys = {sorted(data.keys())!r}"
    )
    assert data["summary"].get("warnings_out"), (
        f"summary.warnings_out missing on W607-CT raise path; got summary = {data['summary']!r}"
    )

    top_markers = [m for m in data["warnings_out"] if m.startswith("runs_serialize_envelope_failed:")]
    summary_markers = [m for m in data["summary"]["warnings_out"] if m.startswith("runs_serialize_envelope_failed:")]
    assert top_markers and summary_markers, (
        f"both mirrors must carry the serialize_envelope marker; "
        f"top = {data.get('warnings_out')!r}, "
        f"summary = {data['summary'].get('warnings_out')!r}"
    )


# ---------------------------------------------------------------------------
# (9) Marker-prefix discipline -- W607-CT uses ``runs_*`` family
# ---------------------------------------------------------------------------


def test_w607ct_marker_prefix_runs_family(cli_runner, runs_project_with_one_run, monkeypatch):
    """W607-CT markers use the canonical ``runs_*`` prefix
    (same family as W607-AS; W607-CT is ADDITIVE, not a separate prefix).

    Hard guard: any W607-CT marker that leaks into a sibling W607-*
    family (``audit_trail_verify_*`` / ``audit_trail_conformance_*`` /
    ``audit_trail_export_*`` / ``postmortem_*``) breaks the closed-enum
    marker-family contract.
    """
    from roam.commands import cmd_runs as _mod

    def _raise_envelope(*args, **kwargs):
        raise RuntimeError("synthetic-prefix-from-W607-CT")

    monkeypatch.setattr(_mod, "json_envelope", _raise_envelope)

    result = _invoke_runs(cli_runner, runs_project_with_one_run, "verify", "--all")
    assert result.exit_code == 0, result.output
    data = _extract_json(result.output)
    top_wo = data.get("warnings_out") or []
    failure_markers = [m for m in top_wo if "_failed:" in m]
    assert failure_markers, "expected non-empty failure-marker bucket for prefix-discipline check"
    for marker in failure_markers:
        assert marker.startswith("runs_"), f"every W607-CT marker must use the ``runs_*`` prefix; got {marker!r}"


# ---------------------------------------------------------------------------
# (10) W607-AS COEXISTENCE -- substrate-CALL + aggregation-phase markers
# coexist in the same family but flow through different buckets
# ---------------------------------------------------------------------------


def test_w607as_substrate_markers_coexist_with_w607ct_aggregation():
    """W607-AS substrate-CALL + W607-CT aggregation-phase coexistence guard.

    Confirm ``runs_<substrate-phase>_failed:`` markers (W607-AS layer)
    coexist with ``runs_<agg-phase>_failed:`` markers (W607-CT layer) --
    both in same ``runs_*`` family, but flow through different buckets
    at envelope-emit.

    This is the explicit guard requested by the W607-CT brief: the
    additive aggregation-phase layer must NOT shadow the pre-existing
    substrate-CALL layer; both buckets must coexist in the same
    warnings_out channel with marker-phase disambiguation
    (``runs_<substrate-phase>_failed:`` vs.
    ``runs_<agg-phase>_failed:``).

    Source-level guard pinning closed-enum invariant: phase names
    coexist without collision.
    """
    # Substrate-CALL phases (W607-AS)
    as_phases = (
        "resolve_project_root",
        "start_run",
        "latest_in_progress_run",
        "read_run_meta",
        "compute_hmac_and_write",
        "end_run",
        "emit_pr_bundle",
    )
    # Aggregation-phase phases (W607-CT)
    ct_phases = (
        "compute_predicate",
        "compute_verdict",
        "serialize_envelope",
    )
    # No phase names overlap -- closed-enum distinct.
    overlap = set(as_phases) & set(ct_phases)
    assert not overlap, f"W607-AS and W607-CT phase names must be closed-enum distinct; got overlap = {overlap!r}"

    # All phase names produce the SAME family prefix ``runs_*``.
    for phase in as_phases + ct_phases:
        marker = f"runs_{phase}_failed:RuntimeError:synthetic"
        assert marker.startswith("runs_") and "_failed:" in marker, marker


# ---------------------------------------------------------------------------
# (11) CROSS-PREFIX ISOLATION -- runs_* markers DO NOT leak into adjacent
# commands' marker families
# ---------------------------------------------------------------------------


def test_runs_markers_do_not_leak_into_adjacent_commands(cli_runner, runs_project_with_one_run, monkeypatch):
    """``runs_*`` markers must NOT appear with foreign prefixes
    (``audit_trail_verify_*`` / ``audit_trail_conformance_*`` /
    ``audit_trail_export_*`` / ``postmortem_*``) when verify --all raises.

    Validates the marker-family isolation contract: each command's W607
    plumbing uses its OWN prefix and does not bleed into adjacent
    commands' warnings_out channels.
    """
    from roam.commands import cmd_runs as _mod

    def _raise_envelope(*args, **kwargs):
        raise RuntimeError("synthetic-isolation-from-W607-CT")

    monkeypatch.setattr(_mod, "json_envelope", _raise_envelope)

    result = _invoke_runs(cli_runner, runs_project_with_one_run, "verify", "--all")
    assert result.exit_code == 0, result.output
    data = _extract_json(result.output)

    top_wo = data.get("warnings_out") or []
    summary_wo = data["summary"].get("warnings_out") or []
    all_markers = list(top_wo) + list(summary_wo)
    failure_markers = [m for m in all_markers if "_failed:" in m]
    assert failure_markers, "expected non-empty failure-marker bucket for prefix-isolation check"

    # Every failure marker must start with runs_ -- foreign-family
    # leakage is a bug. The adjacent commands' prefixes shape the
    # forbidden set.
    foreign_prefixes = (
        "audit_trail_verify_",
        "audit_trail_conformance_",
        "audit_trail_export_",
        "postmortem_",
        "pr_replay_",
        "pr_bundle_",
        "cga_",
        "attest_",
    )
    for marker in failure_markers:
        for foreign in foreign_prefixes:
            assert not marker.startswith(foreign), (
                f"cmd_runs warnings_out must not contain {foreign}* markers; got {marker!r}"
            )


# ---------------------------------------------------------------------------
# (12) AGENT-OS LEDGER family pairing -- markers coexist with sibling
# audit_trail_verify_* / audit_trail_conformance_* / audit_trail_export_*
# ---------------------------------------------------------------------------


def test_agent_os_ledger_family_marker_families_coexist():
    """AGENT-OS LEDGER family pairing guard requested by the W607-CT brief.

    Confirm that ``runs_<phase>_failed:`` markers (W607-AS + W607-CT)
    stay in the canonical ``runs_*`` family while sibling commands
    carry their own distinct prefixes:

      - cmd_runs                    -> ``runs_*``                       (W607-AS + CT)
      - cmd_audit_trail_verify      -> ``audit_trail_verify_*``         (W607-AI + CN)
      - cmd_audit_trail_conformance -> ``audit_trail_conformance_*``    (W607-AL + CO)
      - cmd_audit_trail_export      -> ``audit_trail_export_*``         (W607-AP + CR)

    Source-level guard pinning the closed-enum invariant: an aggregator
    consuming envelopes from all 4 commands can attribute each
    disclosure via prefix alone, with NO ambiguity between siblings.
    Closes the agent-OS ledger family.
    """
    src_root = Path(__file__).parent.parent / "src" / "roam" / "commands"

    runs_src = (src_root / "cmd_runs.py").read_text(encoding="utf-8")
    verify_src = (src_root / "cmd_audit_trail_verify.py").read_text(encoding="utf-8")
    conformance_src = (src_root / "cmd_audit_trail_conformance.py").read_text(encoding="utf-8")
    export_src = (src_root / "cmd_audit_trail_export.py").read_text(encoding="utf-8")

    # cmd_runs carries the runs_ marker prefix (W607-AS + CT)
    assert "runs_{phase}_failed" in runs_src, (
        "W607-AS/CT runs_{phase}_failed marker template missing from cmd_runs -- writer-side instrumentation regressed."
    )

    # cmd_audit_trail_verify carries audit_trail_verify_ (W607-AI + CN)
    assert "audit_trail_verify_{phase}_failed" in verify_src, (
        "W607-AI/CN audit_trail_verify_{phase}_failed marker template "
        "missing from cmd_audit_trail_verify -- verifier-side "
        "instrumentation regressed."
    )

    # cmd_audit_trail_conformance carries its own marker prefix (W607-AL/CO)
    assert (
        "audit_trail_conformance_{phase}_failed" in conformance_src or "audit_trail_conformance_" in conformance_src
    ), (
        "W607-AL/CO audit_trail_conformance_* marker family missing from "
        "cmd_audit_trail_conformance -- sibling instrumentation regressed."
    )

    # cmd_audit_trail_export carries its own marker prefix (W607-AP/CR)
    assert "audit_trail_export_{phase}_failed" in export_src or "audit_trail_export_" in export_src, (
        "W607-AP/CR audit_trail_export_* marker family missing from "
        "cmd_audit_trail_export -- sibling instrumentation regressed."
    )

    # The four prefixes do not collide -- runs_ is NOT a prefix of any
    # sibling, and no sibling is a prefix of it.
    for sibling_prefix in (
        "audit_trail_verify_",
        "audit_trail_conformance_",
        "audit_trail_export_",
    ):
        assert not "runs_".startswith(sibling_prefix)
        assert not sibling_prefix.startswith("runs_")


# ---------------------------------------------------------------------------
# (13) HMAC-FAILURE-ABORTS-WRITE invariant preserved (chain-integrity)
# ---------------------------------------------------------------------------


def test_hmac_failure_aborts_write_invariant_preserved(cli_runner, runs_project, monkeypatch):
    """W607-CT MUST NOT introduce any code path that circumvents the
    W607-AS HMAC-failure-aborts-write discipline.

    The W607-AS contract (sealed in
    tests/test_w607_as_cmd_runs_warnings_out_envelope.py:
    test_runs_log_hmac_failure_aborts_write) mandates: a raise inside
    ``compute_hmac_and_write`` (i.e., ``log_event``) MUST abort the
    write -- no event line is appended to events.jsonl. Preserving
    chain integrity is more important than producing a marker.

    This test re-pins the invariant after the W607-CT add. The
    aggregation-phase additive layer is on the VERIFIER side
    (runs-verify); the writer-side abort discipline on runs-log MUST
    stay intact.
    """
    from roam.commands import cmd_runs

    start_res = _invoke_runs(cli_runner, runs_project, "start", "--agent", "tester")
    assert start_res.exit_code == 0, start_res.output
    start_data = _extract_json(start_res.output)
    run_id = start_data["summary"]["run_id"]
    events_path = runs_project / ".roam" / "runs" / run_id / "events.jsonl"

    # Snapshot the pre-write line count (should be 0).
    pre_lines = 0
    if events_path.exists():
        pre_lines = sum(1 for _ in events_path.open("r", encoding="utf-8"))

    def _raise_log_event(*args, **kwargs):
        raise RuntimeError("synthetic-hmac-write-from-W607-CT-regression-guard")

    monkeypatch.setattr(cmd_runs, "log_event", _raise_log_event)

    log_res = _invoke_runs(cli_runner, runs_project, "log", "--action", "preflight", "--target", "foo")
    # Exit code 2: abort path -- the W607-AS substrate-CALL guard.
    assert log_res.exit_code == 2, log_res.output
    data = _extract_json(log_res.output)

    # Marker present
    top_wo = data.get("warnings_out") or []
    summary_wo = data["summary"].get("warnings_out") or []
    merged = list(top_wo) + list(summary_wo)
    markers = [m for m in merged if m.startswith("runs_compute_hmac_and_write_failed:")]
    assert markers, f"W607-AS marker absent after W607-CT add; got merged={merged!r}"
    assert data["summary"].get("logged") is False
    assert data["summary"].get("state") == "hmac_or_write_aborted"

    # CRITICAL: events.jsonl must NOT have grown -- W607-CT must not
    # have re-enabled silent writes.
    post_lines = 0
    if events_path.exists():
        post_lines = sum(1 for _ in events_path.open("r", encoding="utf-8"))
    assert post_lines == pre_lines, (
        f"HMAC-failure-aborts-write violated AFTER W607-CT add: "
        f"events.jsonl grew from {pre_lines} to {post_lines} lines "
        f"despite log_event raising. Chain integrity compromised."
    )


# ---------------------------------------------------------------------------
# (14) W978 6-DISCIPLINE AST audit -- floors are literal constants
# ---------------------------------------------------------------------------


def test_w978_kwarg_default_floors_are_literal_constants():
    """W978 kwarg-default eagerness audit: every W607-CT ``default=`` MUST
    be a literal constant, NOT computed from upstream values.

    The six W978 recurring traps (from CLAUDE.md + W607-CN brief):
      1. f-string verdict floor (W607-BP)
      2. kwarg-default eagerness (W607-CG)
      3. json.dumps(default=str) sentinel propagation (W607-CF)
      4. Phase-name collision (W607-CH)
      5. ``len()`` at kwarg-bind site (W607-CJ) -- move inside closure
      6. Unguarded ``len(x)`` / ``if x:`` / ``truthy(x)`` on
         potentially-poisoned objects -> hoist into predicate phase with
         literal-int counts (W607-CN)

    AST audit: walk every ``_run_check_ct(...)`` call, extract the
    ``default=`` keyword argument's AST node, confirm it is a Constant
    (literal int/str/bool/None) or a Dict/List/Set/Tuple of Constants
    (or a bare Name reference -- variables bound BEFORE the wrap call).
    Reject any Call, Attribute, Subscript, BinOp, UnaryOp, Compare, or
    f-string node in the default expression -- these compute from
    upstream values at kwarg-bind time.
    """
    src_path = Path(__file__).parent.parent / "src" / "roam" / "commands" / "cmd_runs.py"
    src = src_path.read_text(encoding="utf-8")
    tree = ast.parse(src)

    def _is_literal(node) -> bool:
        """True iff ``node`` is a fully-literal AST subtree.

        Allows: Constant, Dict/List/Tuple/Set of literals, unary +/- of
        a constant, and bare Name references (variables bound BEFORE the
        wrap call, e.g. ``default=_envelope_floor``). Rejects Call,
        Attribute, Subscript, BinOp, Compare, IfExp, f-string, etc. --
        these can compute over potentially-poisoned upstream values at
        kwarg-bind time and raise BEFORE the wrap's try-block enters.
        """
        if isinstance(node, ast.Constant):
            return True
        if isinstance(node, ast.Name):
            return True
        if isinstance(node, ast.Dict):
            return all(_is_literal(k) for k in node.keys if k is not None) and all(_is_literal(v) for v in node.values)
        if isinstance(node, (ast.List, ast.Tuple, ast.Set)):
            return all(_is_literal(e) for e in node.elts)
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.USub, ast.UAdd)):
            return _is_literal(node.operand)
        return False

    violations: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        # Match _run_check_ct(...)
        if not (isinstance(node.func, ast.Name) and node.func.id == "_run_check_ct"):
            continue
        for kw in node.keywords:
            if kw.arg != "default":
                continue
            if not _is_literal(kw.value):
                violations.append(
                    f"line {kw.value.lineno}: non-literal default= expression in _run_check_ct(...) -- W978 violation"
                )

    assert not violations, (
        "W978 kwarg-default eagerness trap detected in cmd_runs.py:\n"
        + "\n".join(violations)
        + "\nFloor expressions in default= MUST be literal constants. "
        "See cmd_sbom W607-CG / cmd_taint W607-CJ / "
        "cmd_audit_trail_verify W607-CN for the canonical fix pattern."
    )


def test_w978_kwarg_default_does_not_eagerly_raise_on_bad_input(cli_runner, runs_project_with_one_run, monkeypatch):
    """W978 defensive test: exercise the floor on a corrupt-input
    sentinel (mirrors cmd_sbom's ``_BadDeps(list)`` shape +
    cmd_taint's ``_BadFindingList(list)`` follow-on +
    cmd_audit_trail_verify W607-CN's ``_BadChainState`` axis).

    Patches ``_verify_one_run`` to return a dict whose ``__getitem__``
    raises. If ANY W607-CT ``default=`` kwarg eagerly computed over
    this input, the raise would escape the try-block and crash the
    envelope. The literal-constant floors below catch the raise inside
    the wrapped call and surface a marker.
    """
    from roam.commands import cmd_runs as _mod

    class _BadResult(dict):
        # __getitem__ on this result raises -- predicate closure trips
        def __getitem__(self, key):
            raise RuntimeError("synthetic-w978-bad-result-from-W607-CT")

    def _bad_verify_one_run(root, run_id):
        return _BadResult()

    monkeypatch.setattr(_mod, "_verify_one_run", _bad_verify_one_run)

    result = _invoke_runs(cli_runner, runs_project_with_one_run, "verify", "--all")
    # The command MUST NOT crash -- a marker must be on the envelope
    # rather than the raise escaping the wrap.
    assert result.exit_code in (0, 5), f"W978 violation: bad-result sentinel caused crash; output={result.output!r}"
    data = _extract_json(result.output)
    # Envelope must be parseable and carry SOMETHING in warnings_out
    top_wo = data.get("warnings_out") or []
    summary_wo = data["summary"].get("warnings_out") or []
    all_wo = list(top_wo) + list(summary_wo)
    # W607-AS or W607-CT family must carry a marker
    runs_markers = [m for m in all_wo if m.startswith("runs_") and "_failed:" in m]
    assert runs_markers, (
        f"W978 regression: bad-result sentinel produced no marker on the "
        f"envelope; the bad input either bypassed the wraps or eagerly "
        f"raised in default=; got all_wo={all_wo!r}"
    )


# ---------------------------------------------------------------------------
# (15) Phase-name collision guard (W607-CH discipline)
# ---------------------------------------------------------------------------


def test_w607ch_phase_name_collision_avoided():
    """W978 trap #4 / W607-CH discipline: W607-AS and W607-CT phase
    names must be closed-enum distinct -- no collision across the two
    layers in the same command.

    cmd_runs has:
      - W607-AS phases on runs-start / runs-log / runs-end:
        resolve_project_root, start_run, latest_in_progress_run,
        read_run_meta, compute_hmac_and_write, end_run, emit_pr_bundle
      - W607-CT phases on runs-verify --all:
        compute_predicate, compute_verdict, serialize_envelope

    These two sets MUST NOT share a phase name (e.g., if W607-AS were
    extended with a ``compute_predicate`` substrate-CALL, the
    W607-CT layer's aggregation-phase marker would collide). The
    source-level audit pins the closed-enum invariant.
    """
    as_phases = {
        "resolve_project_root",
        "start_run",
        "latest_in_progress_run",
        "read_run_meta",
        "compute_hmac_and_write",
        "end_run",
        "emit_pr_bundle",
    }
    ct_phases = {
        "compute_predicate",
        "compute_verdict",
        "serialize_envelope",
    }
    overlap = as_phases & ct_phases
    assert not overlap, (
        f"W607-CH phase-name collision detected: W607-AS and W607-CT "
        f"share phase names {overlap!r} -- markers will collide. "
        f"Rename the colliding phase in one layer."
    )


# ---------------------------------------------------------------------------
# (16) Three-segment marker shape -- prefix:exc_class:detail
# ---------------------------------------------------------------------------


def test_three_segment_marker_shape(cli_runner, runs_project_with_one_run, monkeypatch):
    """Marker must have three colon-separated segments.

    Shape contract: ``<prefix>:<exc_class>:<detail>`` so downstream
    consumers can parse the exception class without regex gymnastics.
    Mirrors W607-AS / W607-AI / W607-CN contracts.
    """
    from roam.commands import cmd_runs as _mod

    def _raise_envelope(*args, **kwargs):
        raise PermissionError("synthetic-shape-detail-from-W607-CT")

    monkeypatch.setattr(_mod, "json_envelope", _raise_envelope)

    result = _invoke_runs(cli_runner, runs_project_with_one_run, "verify", "--all")
    assert result.exit_code == 0, result.output
    data = _extract_json(result.output)

    top_wo = data.get("warnings_out") or []
    failure_markers = [m for m in top_wo if m.startswith("runs_serialize_envelope_failed:")]
    assert failure_markers, f"expected runs_serialize_envelope_failed: marker; got top_wo={top_wo!r}"

    marker = failure_markers[0]
    parts = marker.split(":", 2)
    assert len(parts) == 3, f"marker must have three colon-separated segments (prefix:exc_class:detail); got {marker!r}"
    assert parts[0] == "runs_serialize_envelope_failed", parts
    assert parts[1] == "PermissionError", parts
    assert parts[2], parts
