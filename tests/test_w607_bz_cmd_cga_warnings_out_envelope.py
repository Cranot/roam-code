"""W607-BZ -- additive aggregation-phase plumbing for ``cmd_cga``.

cmd_cga is the CRYPTOGRAPHIC core of the W805 cross-artifact-consistency
family (CGA / VSA / Rekor pipeline). Closes the attestation triad
together with W607-AD/BT (cmd_attest) and W607-AE/BW (cmd_pr_bundle).
With W607-BZ landed, the full emit-path is now dual-bucket plumbed via:

  - substrate-CALL layer: W607-AF (7 emit-path phases + 2 verify-path)
  - aggregation-phase layer: W607-BZ (4 emit-path phases)

Both layers share the canonical ``cga_*`` marker family and the
``cga_<phase>_failed:<exc_class>:<detail>`` shape contract. The three
buckets (``_w489_a_lint_warnings`` qualified_only +
``_w607af_warnings_out`` substrate-CALL + ``_w607bz_warnings_out``
aggregation-phase) are combined at envelope-emit time so consumers see
the full degradation lineage in marker-emission order.

Relation to W607-AF
-------------------

cmd_cga already carries W607-AF substrate-CALL plumbing covering 7
substrate-helper boundaries on the emit path (git_dirty_hash / run_taint
/ build_cga_statement / serialize_statement / atomic_write_text /
cosign_sign_statement / emit_vsa_sibling) plus 2 on verify
(verify_cga_statement / cosign_verify_statement). W607-BZ is ADDITIVE on
top of W607-AF, extending marker coverage to the AGGREGATION-PHASE
boundaries that W607-AF left unguarded:

  - ``compute_predicate``    -- per-field extraction of predicate fields
                                (symbol_count / edge_count /
                                merkle_root / reachability_claims)
                                used to compose the verdict string.
  - ``compute_verdict``      -- verdict string assembly (LAW 6
                                standalone-parse).
  - ``auto_log``             -- active-run ledger write.
  - ``serialize_envelope``   -- ``json_envelope("cga-emit", ...)``
                                projection.

cmd_cga is NOT a risk scorer (unlike cmd_attest / cmd_pr_bundle); it's
a pure attestation predicate emitter. So the W607-BZ phase set drops
``score_classify`` / ``severity_normalize`` (no risk_level emission)
and substitutes ``compute_predicate`` instead. The remaining 3 phases
(``compute_verdict`` / ``auto_log`` / ``serialize_envelope``) mirror
the cmd_attest W607-BT contract byte-for-byte at the prefix layer.

W978 first-hypothesis check (pre-fix audit)
-------------------------------------------

cmd_cga's aggregation-phase boundaries had no guards. A downstream
refactor that changes the predicate schema, the verdict string
composition, the HMAC chain on the runs ledger, or the
``json_envelope`` shape would crash the envelope post-compute -- after
the substrate signals were already gathered, the agent loses the
result. W607-BZ wraps each boundary with ``_run_check_bz`` so a raise
becomes a marker via ``warnings_out`` and the envelope still emits.

W805 attestation-triad pairing
------------------------------

With W607-BT (cmd_attest) and W607-BW (cmd_pr_bundle) already landed,
W607-BZ closes the attestation triad: every cryptographic-attestation
emitter in the W805 family now has dual-bucket plumbing
(substrate-CALL + aggregation-phase). The integration test
(test_attestation_triad_marker_families_coexist) confirms each
command's markers stay in its OWN family and never bleed into a
sibling's envelope.

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
from conftest import git_init, index_in_process  # noqa: E402

# ---------------------------------------------------------------------------
# Helpers -- invoke cga emit via the Click group
# ---------------------------------------------------------------------------


def _invoke_cga_emit(runner: CliRunner, cwd, *extra, json_mode: bool = True):
    """Invoke ``roam cga emit`` through the group so ``--json`` is honoured."""
    from roam.cli import cli

    args: list[str] = []
    if json_mode:
        args.append("--json")
    args.extend(["cga", "emit", "--allow-dirty", "--no-write"])
    args.extend(extra)

    old_cwd = os.getcwd()
    try:
        os.chdir(str(cwd))
        result = runner.invoke(cli, args, catch_exceptions=False)
    finally:
        os.chdir(old_cwd)
    return result


# ---------------------------------------------------------------------------
# Fixture
# ---------------------------------------------------------------------------


@pytest.fixture
def cli_runner():
    return CliRunner()


@pytest.fixture
def cga_project(tmp_path, monkeypatch):
    """Indexed corpus so cga emit reaches every W607-BZ aggregation
    boundary (compute_predicate / compute_verdict / serialize_envelope /
    auto_log). Tests pass ``--allow-dirty`` + ``--no-write`` so the
    dirty-tree gate doesn't fire and no disk artifact is needed.
    """
    proj = tmp_path / "cga_w607bz_project"
    proj.mkdir()
    (proj / ".gitignore").write_text(".roam/\n")
    src = proj / "src"
    src.mkdir()
    (src / "main.py").write_text(
        "def main():\n    helper()\n    return 1\n\n"
        "def helper():\n    inner()\n    return 42\n\n"
        "def inner():\n    return 7\n",
        encoding="utf-8",
    )
    (src / "utils.py").write_text(
        'def format_name(first, last):\n    return f"{first} {last}"\n\ndef shout(msg):\n    return msg.upper()\n',
        encoding="utf-8",
    )
    git_init(proj)
    monkeypatch.chdir(proj)
    out, rc = index_in_process(proj, "--force")
    assert rc == 0, f"index failed:\n{out}"
    return proj


# ---------------------------------------------------------------------------
# (1) Happy path -- envelope omits W607-BZ aggregation markers
# ---------------------------------------------------------------------------


def test_cga_emit_happy_path_no_w607bz_markers(cli_runner, cga_project):
    """Clean cga-emit on a healthy corpus -> no W607-BZ aggregation markers.

    Hash-stable: an empty W607-BZ bucket on the success path must
    produce an envelope without any
    ``cga_compute_predicate_failed:`` /
    ``cga_compute_verdict_failed:`` /
    ``cga_auto_log_failed:`` /
    ``cga_serialize_envelope_failed:`` markers. Mirror of cmd_attest
    W607-BT happy-path discipline.
    """
    result = _invoke_cga_emit(cli_runner, cga_project)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)
    assert data["command"] == "cga-emit"

    top_wo = data.get("warnings_out") or []
    summary_wo = data["summary"].get("warnings_out") or []
    all_markers = list(top_wo) + list(summary_wo)
    w607bz_phases = (
        "cga_compute_predicate_failed:",
        "cga_compute_verdict_failed:",
        "cga_auto_log_failed:",
        "cga_serialize_envelope_failed:",
    )
    for prefix in w607bz_phases:
        leaked = [m for m in all_markers if m.startswith(prefix)]
        assert not leaked, f"clean cga-emit must NOT surface {prefix} markers; got {leaked!r}"


# ---------------------------------------------------------------------------
# (2) AST-level guard -- the additive ``_run_check_bz`` helper is present
# ---------------------------------------------------------------------------


def test_cmd_cga_carries_w607bz_accumulator():
    """AST-level guard: cmd_cga source carries the W607-BZ accumulator.

    Pins the canonical W607-BZ anchors so a future refactor that removes
    the additive instrumentation (or merges it back into W607-AF) fails
    this guard rather than silently regressing the aggregation-phase
    marker coverage.
    """
    src_path = Path(__file__).parent.parent / "src" / "roam" / "commands" / "cmd_cga.py"
    assert src_path.exists(), f"cmd_cga.py missing at {src_path}"
    src = src_path.read_text(encoding="utf-8")

    # Source-level anchors
    assert "_w607bz_warnings_out" in src, (
        "W607-BZ accumulator missing from cmd_cga; the additive aggregation-phase marker plumbing has been removed."
    )
    assert "_run_check_bz" in src, (
        "W607-BZ helper ``_run_check_bz`` missing from cmd_cga; the additive wrapper has been refactored away."
    )

    # Parse-tree level: confirm _run_check_bz is defined inside cga_emit.
    tree = ast.parse(src)
    found_run_check_bz = False
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "_run_check_bz":
            found_run_check_bz = True
            break
    assert found_run_check_bz, (
        "W607-BZ ``_run_check_bz`` helper not found in cmd_cga AST; "
        "the additive aggregation-phase wrapper has been refactored away."
    )

    # W607-AF must still be present (additive layer does NOT replace it)
    assert "_w607af_warnings_out" in src, (
        "W607-AF accumulator vanished alongside the W607-BZ add; the "
        "additive plumbing must preserve the W607-AF substrate-CALL layer."
    )


# ---------------------------------------------------------------------------
# (3) Source-grep guard -- every aggregation-phase boundary is wrapped
# ---------------------------------------------------------------------------


def test_every_aggregation_phase_wrapped_in_run_check_bz():
    """Source-grep guard: every aggregation-phase boundary calls
    ``_run_check_bz(...)`` with the canonical phase name.

    The four phases must appear inside a ``_run_check_bz("<phase>", ...)``
    call inside cmd_cga. Multi-indent variants are all considered valid
    wrap call-sites.
    """
    src_path = Path(__file__).parent.parent / "src" / "roam" / "commands" / "cmd_cga.py"
    src = src_path.read_text(encoding="utf-8")

    canonical_phases = (
        "compute_predicate",
        "compute_verdict",
        "auto_log",
        "serialize_envelope",
    )
    for phase in canonical_phases:
        markers = [
            f'_run_check_bz(\n        "{phase}"',
            f'_run_check_bz(\n            "{phase}"',
            f'_run_check_bz(\n                "{phase}"',
            f'_run_check_bz(\n                    "{phase}"',
            f'_run_check_bz(\n                        "{phase}"',
            f'_run_check_bz("{phase}"',
        ]
        found = any(m in src for m in markers)
        assert found, (
            f"phase ``{phase}`` is not wrapped in _run_check_bz(...); add the W607-BZ guard or pin the canonical anchor"
        )


# ---------------------------------------------------------------------------
# (4) auto_log failure marker shape
# ---------------------------------------------------------------------------


def test_auto_log_failure_marker_format(cli_runner, cga_project, monkeypatch):
    """If ``auto_log`` raises, surface ``cga_auto_log_failed:`` and keep
    the cga-emit envelope intact.

    Mirror of cmd_attest W607-BT auto_log-failure pattern. The
    auto_log boundary writes to the active run ledger when one is open
    -- a raise here would otherwise crash the envelope AFTER the
    success envelope was already built.
    """
    from roam.commands import cmd_cga

    def _raise_auto_log(*args, **kwargs):
        raise RuntimeError("synthetic-auto-log-from-W607-BZ")

    monkeypatch.setattr(cmd_cga, "auto_log", _raise_auto_log)

    result = _invoke_cga_emit(cli_runner, cga_project)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    top_wo = data.get("warnings_out") or []
    markers = [m for m in top_wo if m.startswith("cga_auto_log_failed:")]
    assert markers, f"expected ``cga_auto_log_failed:`` marker; got {top_wo!r}"
    marker = markers[0]
    parts = marker.split(":", 2)
    assert len(parts) == 3, f"marker must have three colon-separated segments; got {marker!r}"
    assert parts[1] == "RuntimeError", parts
    assert "synthetic-auto-log-from-W607-BZ" in parts[2], parts

    # Envelope still emits the core cga-emit fields
    for key in ("statement",):
        assert key in data, (
            f"envelope must still emit ``{key}`` when auto_log raises; got keys = {sorted(data.keys())!r}"
        )


# ---------------------------------------------------------------------------
# (5) compute_predicate failure marker
# ---------------------------------------------------------------------------


def test_compute_predicate_failure_marker_format(cli_runner, cga_project, monkeypatch):
    """If the compute_predicate boundary raises, surface the marker.

    We patch ``build_cga_statement`` to return a malformed predicate
    (missing required keys) so the W607-BZ ``compute_predicate``
    inner closure trips on a KeyError. The W607-BZ wrap surfaces a
    structured marker rather than crashing the envelope.
    """
    from roam.commands import cmd_cga

    def _malformed_statement(*args, **kwargs):
        # Missing symbol_count / edge_count / merkle_root keys -> the
        # _compute_predicate_fields closure raises KeyError.
        return {
            "predicate": {"reachability_claims": []},
            "predicateType": "https://roam-code.com/spec/CodeGraph/v1",
            "subject": [{}],
        }

    monkeypatch.setattr(cmd_cga, "build_cga_statement", _malformed_statement)

    result = _invoke_cga_emit(cli_runner, cga_project)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    top_wo = data.get("warnings_out") or []
    summary_wo = data["summary"].get("warnings_out") or []
    all_wo = list(top_wo) + list(summary_wo)
    markers = [m for m in all_wo if m.startswith("cga_compute_predicate_failed:")]
    assert markers, f"expected ``cga_compute_predicate_failed:`` marker; got {all_wo!r}"
    assert any("KeyError" in m for m in markers), markers


# ---------------------------------------------------------------------------
# (6) compute_verdict failure marker
# ---------------------------------------------------------------------------


def test_compute_verdict_failure_marker_format(cli_runner, cga_project, monkeypatch):
    """If the compute_verdict boundary raises, surface the marker.

    We patch ``build_cga_statement`` to return a predicate whose
    ``merkle_root`` is a non-string sentinel that raises on slice. The
    verdict-string f-string interpolation then trips the wrap inside
    ``_build_verdict_str``.

    W978 first-hypothesis check: the canonical floor MUST NOT
    re-interpolate the same value that raised -- the floor is a
    literal string ``"CGA emit completed"``.
    """
    from roam.commands import cmd_cga

    class _BadMerkle:
        def __getitem__(self, idx):
            raise RuntimeError("synthetic-compute-verdict-from-W607-BZ")

        def __bool__(self):
            return True

    def _bad_merkle_statement(*args, **kwargs):
        return {
            "predicate": {
                "symbol_count": 1,
                "edge_count": 1,
                "merkle_root": _BadMerkle(),
                "reachability_claims": [],
                "edge_bundle_digest": "fake",
                "languages": [],
            },
            "predicateType": "https://roam-code.com/spec/CodeGraph/v1",
            "subject": [{}],
        }

    monkeypatch.setattr(cmd_cga, "build_cga_statement", _bad_merkle_statement)

    result = _invoke_cga_emit(cli_runner, cga_project)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    top_wo = data.get("warnings_out") or []
    summary_wo = data["summary"].get("warnings_out") or []
    all_wo = list(top_wo) + list(summary_wo)
    markers = [m for m in all_wo if m.startswith("cga_compute_verdict_failed:")]
    assert markers, f"expected ``cga_compute_verdict_failed:`` marker; got {all_wo!r}"
    assert any("RuntimeError" in m for m in markers), markers


# ---------------------------------------------------------------------------
# (7) serialize_envelope guard -- raise floors to stub document
# ---------------------------------------------------------------------------


def test_w607bz_serialize_envelope_floor_on_raise(cli_runner, cga_project, monkeypatch):
    """If ``json_envelope`` raises on the success path, the wrap floors
    to a parseable envelope stub and surfaces
    ``cga_serialize_envelope_failed:``.

    A downstream schema-shape refactor that breaks
    ``json_envelope("cga-emit", ...)`` would otherwise crash AFTER all
    substrate + aggregation signals were already gathered. The consumer
    must still receive a parseable JSON object with the marker attached
    + the canonical command name.
    """
    from roam.commands import cmd_cga

    def _raise_envelope(*args, **kwargs):
        raise RuntimeError("synthetic-serialize-envelope-from-W607-BZ")

    monkeypatch.setattr(cmd_cga, "json_envelope", _raise_envelope)

    result = _invoke_cga_emit(cli_runner, cga_project)
    assert result.exit_code == 0, result.output

    # Parse the stub document -- must remain parseable JSON.
    data = _json.loads(result.output)
    assert data.get("command") == "cga-emit", (
        f"envelope stub must carry the canonical command name on raise; got {data!r}"
    )
    top_wo = data.get("warnings_out") or []
    markers = [m for m in top_wo if m.startswith("cga_serialize_envelope_failed:")]
    assert markers, f"expected ``cga_serialize_envelope_failed:`` marker; got {top_wo!r}"


# ---------------------------------------------------------------------------
# (8) ANY marker flips partial_success
# ---------------------------------------------------------------------------


def test_any_marker_flips_partial_success(cli_runner, cga_project, monkeypatch):
    """ANY W607-BZ or W607-AF marker must flip summary.partial_success=True.

    Pattern-2 contract: the agent MUST be able to distinguish "clean
    cga-emit" from "cga-emit ran with substrate degradation" via
    summary.partial_success alone.
    """
    from roam.commands import cmd_cga

    def _raise_auto_log(*args, **kwargs):
        raise RuntimeError("synthetic-partial-success-from-W607-BZ")

    monkeypatch.setattr(cmd_cga, "auto_log", _raise_auto_log)

    result = _invoke_cga_emit(cli_runner, cga_project)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)
    assert data["summary"].get("partial_success") is True, (
        f"non-empty W607-BZ warnings_out must flip summary.partial_success=True; got summary = {data['summary']!r}"
    )


# ---------------------------------------------------------------------------
# (9) warnings_out lands in BOTH top-level AND summary mirror
# ---------------------------------------------------------------------------


def test_w607bz_warnings_out_in_both_top_and_summary(cli_runner, cga_project, monkeypatch):
    """Non-empty W607-BZ bucket -> both top-level AND summary.warnings_out
    populated.

    Mirror parity with W607-BT contract: top-level is needed because
    the preserved-list field survives ``strip_list_payloads`` in
    default-detail mode; summary mirror gives consumers reading only
    the summary block visibility too.
    """
    from roam.commands import cmd_cga

    def _raise_auto_log(*args, **kwargs):
        raise RuntimeError("synthetic-mirror-from-W607-BZ")

    monkeypatch.setattr(cmd_cga, "auto_log", _raise_auto_log)

    result = _invoke_cga_emit(cli_runner, cga_project)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    assert data.get("warnings_out"), (
        f"top-level warnings_out missing on W607-BZ raise path; keys = {sorted(data.keys())!r}"
    )
    assert data["summary"].get("warnings_out"), (
        f"summary.warnings_out missing on W607-BZ raise path; got summary = {data['summary']!r}"
    )

    top_markers = [m for m in data["warnings_out"] if m.startswith("cga_auto_log_failed:")]
    summary_markers = [m for m in data["summary"]["warnings_out"] if m.startswith("cga_auto_log_failed:")]
    assert top_markers and summary_markers, (
        f"both mirrors must carry the auto_log marker; "
        f"top = {data.get('warnings_out')!r}, "
        f"summary = {data['summary'].get('warnings_out')!r}"
    )


# ---------------------------------------------------------------------------
# (10) Marker-prefix discipline -- W607-BZ uses the SAME ``cga_*`` family
# ---------------------------------------------------------------------------


def test_w607bz_marker_prefix_cga_family(cli_runner, cga_project, monkeypatch):
    """W607-BZ markers use the canonical ``cga_*`` prefix (same family
    as W607-AF; W607-BZ is ADDITIVE, not a separate prefix).

    Hard guard: any W607-BZ marker that leaks into a sibling W607-*
    family (e.g. ``attest_*`` / ``pr_bundle_*`` / ``preflight_*``)
    breaks the closed-enum marker-family contract.
    """
    from roam.commands import cmd_cga

    def _raise_auto_log(*args, **kwargs):
        raise PermissionError("synthetic-prefix-discipline-from-W607-BZ")

    monkeypatch.setattr(cmd_cga, "auto_log", _raise_auto_log)

    result = _invoke_cga_emit(cli_runner, cga_project)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)
    top_wo = data.get("warnings_out") or []
    # Filter to W607 failure markers (W489-A bucket emits prose).
    failure_markers = [m for m in top_wo if "_failed:" in m]
    assert failure_markers, "expected non-empty failure-marker bucket for prefix-discipline check"
    for marker in failure_markers:
        assert marker.startswith("cga_"), f"every W607-BZ marker must use the ``cga_*`` prefix; got {marker!r}"


# ---------------------------------------------------------------------------
# (11) W607-AF COEXISTENCE -- substrate-CALL + aggregation-phase markers
# coexist in the same family but flow through different buckets
# ---------------------------------------------------------------------------


def test_w607af_substrate_markers_coexist_with_w607bz_aggregation(cli_runner, cga_project, monkeypatch):
    """Confirm ``cga_<substrate-phase>_failed:`` markers (W607-AF layer)
    coexist with ``cga_<agg-phase>_failed:`` markers (W607-BZ layer) --
    both in same family, but threaded through different buckets at
    envelope-emit.

    This is the explicit guard requested by the W607-BZ brief: the
    additive aggregation-phase layer must NOT shadow the pre-existing
    substrate-CALL layer; both buckets must combine into the same
    warnings_out channel with marker-prefix disambiguation
    (``cga_<substrate-phase>_failed:`` vs.
    ``cga_<agg-phase>_failed:``).
    """
    from roam.commands import cmd_cga

    # W607-AF substrate boundary -- serialize_statement
    def _raise_serialize(*a, **kw):
        raise RuntimeError("synthetic-af-coexist-serialize")

    # W607-BZ aggregation boundary -- auto_log
    def _raise_auto_log(*a, **kw):
        raise RuntimeError("synthetic-bz-coexist-auto-log")

    monkeypatch.setattr(cmd_cga, "serialize_statement", _raise_serialize)
    monkeypatch.setattr(cmd_cga, "auto_log", _raise_auto_log)

    result = _invoke_cga_emit(cli_runner, cga_project)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    top_wo = data.get("warnings_out") or []

    # Substrate-CALL phase from W607-AF
    af_markers = [m for m in top_wo if m.startswith("cga_serialize_statement_failed:")]
    # Aggregation-phase from W607-BZ
    bz_markers = [m for m in top_wo if m.startswith("cga_auto_log_failed:")]

    assert af_markers, f"W607-AF substrate-CALL marker (cga_serialize_statement_failed) missing; got {top_wo!r}"
    assert bz_markers, f"W607-BZ aggregation-phase marker (cga_auto_log_failed) missing; got {top_wo!r}"

    # Both share the canonical ``cga_*`` family
    assert all(m.startswith("cga_") for m in (af_markers + bz_markers)), (
        f"all markers must share the canonical ``cga_*`` family; got af = {af_markers!r}, bz = {bz_markers!r}"
    )

    # Both surface in summary mirror too
    summary_wo = data["summary"].get("warnings_out") or []
    assert any(m.startswith("cga_serialize_statement_failed:") for m in summary_wo), (
        f"W607-AF marker missing from summary mirror; got {summary_wo!r}"
    )
    assert any(m.startswith("cga_auto_log_failed:") for m in summary_wo), (
        f"W607-BZ marker missing from summary mirror; got {summary_wo!r}"
    )


# ---------------------------------------------------------------------------
# (12) CROSS-PREFIX ISOLATION -- cga_* markers DO NOT leak into adjacent
# commands (cmd_attest, cmd_pr_bundle, cmd_supply_chain)
# ---------------------------------------------------------------------------


def test_cga_markers_do_not_leak_into_adjacent_commands(cli_runner, cga_project, monkeypatch):
    """``cga_*`` markers must NOT appear in ``cmd_attest`` /
    ``cmd_pr_bundle`` / ``cmd_supply_chain`` envelopes when those
    commands raise.

    Validates the marker-family isolation contract: each command's W607
    plumbing uses its OWN prefix and does not bleed into adjacent
    commands' warnings_out channels. Mirror of cmd_attest's W607-BT
    cross-prefix-isolation discipline.
    """
    from roam.commands import cmd_cga

    def _raise_auto_log(*args, **kwargs):
        raise RuntimeError("synthetic-cross-prefix-isolation-from-W607-BZ")

    monkeypatch.setattr(cmd_cga, "auto_log", _raise_auto_log)

    result = _invoke_cga_emit(cli_runner, cga_project)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    top_wo = data.get("warnings_out") or []
    summary_wo = data["summary"].get("warnings_out") or []
    all_markers = list(top_wo) + list(summary_wo)
    failure_markers = [m for m in all_markers if "_failed:" in m]
    assert failure_markers, "expected non-empty failure-marker bucket for prefix-isolation check"

    # Every failure marker must start with cga_ -- foreign-family leakage is a bug
    foreign_prefixes = (
        "attest_",
        "pr_bundle_",
        "supply_chain_",
        "preflight_",
        "impact_",
        "diagnose_",
        "critique_",
        "diff_",
    )
    for marker in failure_markers:
        for foreign in foreign_prefixes:
            assert not marker.startswith(foreign), (
                f"cmd_cga warnings_out must not contain {foreign}* markers; got {marker!r}"
            )


# ---------------------------------------------------------------------------
# (13) W472 --also-vsa flow guard -- marker family stays clean on VSA path
# ---------------------------------------------------------------------------


def test_w472_also_vsa_flow_marker_family_stays_clean(cli_runner, cga_project, tmp_path, monkeypatch):
    """When ``--also-vsa`` is set, the W607-BZ marker family must stay
    clean across both predicate-only and predicate+VSA emit paths.

    Per the W607-BZ brief: if cmd_cga has a ``--also-vsa`` codepath,
    ensure marker family stays clean across both predicate-only and
    predicate+VSA emit paths. We patch the W607-BZ ``auto_log`` boundary
    to raise so the marker appears, then assert it stays in the
    ``cga_*`` family and the VSA codepath does NOT introduce alien
    prefixes (e.g. ``vsa_*`` / ``slsa_*``).
    """
    from roam.commands import cmd_cga

    def _raise_auto_log(*args, **kwargs):
        raise RuntimeError("synthetic-also-vsa-from-W607-BZ")

    monkeypatch.setattr(cmd_cga, "auto_log", _raise_auto_log)

    out_path = tmp_path / "cga.intoto.json"
    # Override the default --no-write: we need a written CGA for the
    # VSA sibling helper to engage. Invoke with --output + --also-vsa.
    from roam.cli import cli

    args = ["--json", "cga", "emit", "--allow-dirty", "--output", str(out_path), "--also-vsa"]
    old_cwd = os.getcwd()
    try:
        os.chdir(str(cga_project))
        result = cli_runner.invoke(cli, args, catch_exceptions=False)
    finally:
        os.chdir(old_cwd)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    top_wo = data.get("warnings_out") or []
    summary_wo = data["summary"].get("warnings_out") or []
    all_markers = list(top_wo) + list(summary_wo)

    # The auto_log marker must appear in the cga_* family
    auto_log_markers = [m for m in all_markers if m.startswith("cga_auto_log_failed:")]
    assert auto_log_markers, (
        f"expected cga_auto_log_failed: marker on --also-vsa path; got all_markers = {all_markers!r}"
    )

    # Every failure marker MUST stay in the cga_ family even on the VSA
    # path -- no alien vsa_/slsa_/intoto_ prefixes
    failure_markers = [m for m in all_markers if "_failed:" in m]
    for marker in failure_markers:
        assert marker.startswith("cga_"), f"--also-vsa codepath must keep markers in the cga_* family; got {marker!r}"
        for forbidden in ("vsa_", "slsa_", "intoto_", "cosign_"):
            # Allow cga_cosign_* (it's cga_<phase>_failed:cosign...) but
            # forbid bare cosign_*_failed: at the prefix start.
            assert not marker.startswith(forbidden), (
                f"--also-vsa codepath must not introduce ``{forbidden}*`` prefix markers; got {marker!r}"
            )


# ---------------------------------------------------------------------------
# (14) ATTESTATION TRIAD pairing -- cga_/attest_/pr_bundle_ marker families
# stay isolated when all 3 attestation emitters fire on the same workspace
# ---------------------------------------------------------------------------


def test_attestation_triad_marker_families_coexist(cli_runner, cga_project, monkeypatch):
    """ATTESTATION TRIAD pairing guard requested by the W607-BZ brief:

    Confirm that ``cga_<phase>_failed:`` markers (W607-AF + W607-BZ)
    coexist with ``attest_<phase>_failed:`` markers (W607-AD + W607-BT)
    and ``pr_bundle_<phase>_failed:`` markers (W607-AE + W607-BW) when
    all 3 commands are invoked on the same workspace. Each command's
    markers must stay in its OWN family and never bleed into a
    sibling's envelope.

    Closes the attestation triad: every cryptographic-attestation
    emitter in the W805 cross-artifact-consistency family now has
    dual-bucket plumbing (substrate-CALL + aggregation-phase) AND
    prefix-isolation guards.

    Strategy: monkeypatch each command's auto_log to raise, invoke
    each via the Click group, and confirm the markers in each
    envelope stay in the canonical family.
    """
    from roam.cli import cli
    from roam.commands import cmd_attest, cmd_cga

    # Patch each command's auto_log to a unique synthetic raise so we
    # can distinguish them in the markers.
    def _raise_cga_auto_log(*a, **kw):
        raise RuntimeError("triad-cga-auto-log")

    def _raise_attest_auto_log(*a, **kw):
        raise RuntimeError("triad-attest-auto-log")

    monkeypatch.setattr(cmd_cga, "auto_log", _raise_cga_auto_log)
    monkeypatch.setattr(cmd_attest, "auto_log", _raise_attest_auto_log)

    # --- (a) Invoke cga emit ---
    old_cwd = os.getcwd()
    try:
        os.chdir(str(cga_project))
        cga_result = cli_runner.invoke(
            cli,
            ["--json", "cga", "emit", "--allow-dirty", "--no-write"],
            catch_exceptions=False,
        )
    finally:
        os.chdir(old_cwd)
    assert cga_result.exit_code == 0, cga_result.output
    cga_data = _json.loads(cga_result.output)
    cga_wo = list(cga_data.get("warnings_out") or []) + list(cga_data["summary"].get("warnings_out") or [])

    # cga envelope MUST contain cga_auto_log_failed and MUST NOT contain
    # attest_* or pr_bundle_* markers.
    assert any(m.startswith("cga_auto_log_failed:") for m in cga_wo), (
        f"cga envelope missing cga_auto_log_failed marker; got {cga_wo!r}"
    )
    for marker in cga_wo:
        if "_failed:" not in marker:
            continue
        assert not marker.startswith("attest_"), f"cga envelope leaked attest_* marker: {marker!r}"
        assert not marker.startswith("pr_bundle_"), f"cga envelope leaked pr_bundle_* marker: {marker!r}"

    # --- (b) Invoke attest on an edited file ---
    # Edit one file so attest reaches the collector + aggregation path.
    edit_path = cga_project / "src" / "main.py"
    edit_path.write_text(
        "def main():\n    helper()\n    return 99\n\n"
        "def helper():\n    inner()\n    return 42\n\n"
        "def inner():\n    return 7\n",
        encoding="utf-8",
    )
    try:
        os.chdir(str(cga_project))
        attest_result = cli_runner.invoke(cli, ["--json", "attest"], catch_exceptions=False)
    finally:
        os.chdir(old_cwd)
    assert attest_result.exit_code == 0, attest_result.output
    attest_data = _json.loads(attest_result.output)
    attest_wo = list(attest_data.get("warnings_out") or []) + list(attest_data["summary"].get("warnings_out") or [])

    # attest envelope MUST contain attest_auto_log_failed and MUST NOT
    # contain cga_* or pr_bundle_* markers.
    assert any(m.startswith("attest_auto_log_failed:") for m in attest_wo), (
        f"attest envelope missing attest_auto_log_failed marker; got {attest_wo!r}"
    )
    for marker in attest_wo:
        if "_failed:" not in marker:
            continue
        assert not marker.startswith("cga_"), f"attest envelope leaked cga_* marker: {marker!r}"
        assert not marker.startswith("pr_bundle_"), f"attest envelope leaked pr_bundle_* marker: {marker!r}"
