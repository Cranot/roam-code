"""W607-AE -- ``cmd_pr_bundle`` (emit) threads ``warnings_out`` onto its envelope.

Thirty-first-in-batch W607 consumer-layer arc. Direct sibling of W607-AA
(cmd_pr_analyze diff-text-substrate axis) and W607-AB (cmd_pr_risk
helper-axis). cmd_pr_bundle is the **producer at the heart of the W805
cross-artifact consistency family** -- it composes the proof-bundle JSON
envelope (artifact 1 of the W805 6-artifact family) AND drives emission
of VSA, run-ledger-root, cosign signature triplet, Rekor entry, and
Fulcio cert (artifacts 2-6 via subprocess).

Substrate boundaries wrapped by W607-AE
---------------------------------------

Seven substrate-call sites in ``pr_bundle_emit()`` get the canonical
``_run_check_ae(phase, fn, *args)`` wrapper:

* ``resolve_actor_block``  -- _resolve_actor_block(...) (W189 actor block)
* ``mode_blocks_emit``     -- _mode_blocks_emit(root) (W14.2 mode soft-gate)
* ``auto_collect``         -- _auto_collect(bundle, root) (envelope folding)
* ``causal_diff_pass``     -- _run_causal_diff_pass(bundle) (W15.3 causal diff;
                              replaces the prior silent ``try/except``)
* ``atomic_write_bundle``  -- _atomic_write_bundle(path, bundle) (disk persist)
* ``build_envelope``       -- _build_envelope(...) (composition)
* ``emit_slsa_l3``         -- _emit_slsa_l3_attestations(...) (W805 6-artifact
                              emitter: VSA + run-ledger root + cosign +
                              Rekor + Fulcio)

Each raise becomes a ``pr_bundle_<phase>_failed:<exc_class>:<detail>``
marker via ``_w607ae_warnings_out`` and the envelope still emits cleanly.

W978 first-hypothesis check (pre-fix audit)
-------------------------------------------

cmd_pr_bundle's emit substrate-call sites are direct function invocations
on module-level helpers. The dominant raise axis is the helper-CALL
boundary -- consistent with W607-N..AD. Each helper can raise on a
mode-file read error, a corrupted bundle JSON, a subprocess failure in
cosign / Rekor, a missing run-ledger HMAC chain, a YAML schema drift in
the permits / leases sub-directories, or a network failure during
keyless OIDC signing. The pre-existing bare ``try / except Exception``
around ``_run_causal_diff_pass`` already swallowed one of those axes
silently -- W607-AE replaces that swallow with a structured marker on
``warnings_out`` so the disclosure channel names what crashed.

W805 cross-artifact-consistency bridge
--------------------------------------

The W805-CONSOLIDATE family pins structural drift between the 6
artifacts (bundle envelope, VSA, run-ledger root, cosign sig, Rekor
entry, Fulcio cert) via parametrised consistency tests (W805-KKKKK
etc.). W607-AE's runtime markers
(e.g. ``pr_bundle_emit_slsa_l3_failed:RuntimeError:...``) feed the SAME
family from the runtime side -- when an artifact emission RAISES, the
per-artifact disclosure surfaces in ``warnings_out`` while the
structural pins catch silent drift. The two families compose:
structural pins catch silent drift, W607 markers catch raised drift.

Marker family is ``pr_bundle_*`` -- NOT ``pr_analyze_*`` (W607-AA), NOT
``pr_risk_*`` (W607-AB / W607-Q), NOT ``diff_*`` (W607-Z), etc. The
marker-prefix discipline test pins this closed-enum distinction.

W907 verify-cycle check
-----------------------

No "duplicated to avoid cycle" docstrings added. cmd_pr_bundle has
several lazy local imports (e.g. ``from roam.attest.emit_vsa import
emit_pr_bundle_slsa_l3`` inside ``_emit_slsa_l3_attestations``) which
are genuine deferred-load imports (heavy attest machinery only needed
on ``--slsa-l3``), NOT cargo-cult cycle hedges. Left untouched per W907.

Evidence-compiler note
----------------------

cmd_pr_bundle is the producer at the centre of the agentic-assurance
pipeline. A W607-AE marker surfaces THROUGH any evidence collector that
downstream consumes the envelope because the marker rides
``warnings_out`` on the same JSON document. The pre-existing
``partial_success`` flag is already canonical in the evidence layer.

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
# Helpers -- invoke pr-bundle init + emit via the Click group
# ---------------------------------------------------------------------------


def _invoke_pr_bundle(runner: CliRunner, cwd, *extra, json_mode: bool = True):
    """Invoke ``roam pr-bundle <subcommand>`` through the group."""
    from roam.cli import cli

    args: list[str] = []
    if json_mode:
        args.append("--json")
    args.append("pr-bundle")
    args.extend(extra)

    old_cwd = os.getcwd()
    try:
        os.chdir(str(cwd))
        result = runner.invoke(cli, args, catch_exceptions=False)
    finally:
        os.chdir(old_cwd)
    return result


# ---------------------------------------------------------------------------
# Fixture -- indexed corpus + initialised bundle
# ---------------------------------------------------------------------------


@pytest.fixture
def cli_runner():
    return CliRunner()


@pytest.fixture
def pr_bundle_project(tmp_path, monkeypatch):
    """Indexed corpus with an initialised pr-bundle on the current branch."""
    proj = tmp_path / "pr_bundle_w607ae_project"
    proj.mkdir()
    (proj / ".gitignore").write_text(".roam/\n")
    src = proj / "src"
    src.mkdir()
    (src / "__init__.py").write_text("", encoding="utf-8")
    (src / "auth.py").write_text(
        "def verify_token(t):\n    return t == 'ok'\n",
        encoding="utf-8",
    )
    git_init(proj)
    monkeypatch.chdir(proj)
    out, rc = index_in_process(proj, "--force")
    assert rc == 0, f"index failed:\n{out}"

    # Initialise the bundle so `emit` has something to load.
    runner = CliRunner()
    init_result = _invoke_pr_bundle(
        runner,
        proj,
        "init",
        "--intent",
        "W607-AE smoke",
    )
    assert init_result.exit_code == 0, init_result.output
    return proj


# ---------------------------------------------------------------------------
# (1) Happy path -- clean envelope omits W607-AE substrate markers
# ---------------------------------------------------------------------------


def test_pr_bundle_emit_clean_envelope_omits_w607ae_markers(cli_runner, pr_bundle_project):
    """Clean pr-bundle emit -> no W607-AE substrate markers.

    Hash-stable: an empty W607-AE bucket on the success path must produce
    an envelope without substrate markers. The envelope shape stays
    byte-identical to the pre-W607-AE producer when no helper raised.
    """
    result = _invoke_pr_bundle(cli_runner, pr_bundle_project, "emit", "--no-auto-collect")
    # pr-bundle emit can exit 0 (clean) or 5 (gated incomplete); both are
    # fine for this test. We only care about marker shape.
    assert result.exit_code in (0, 5), result.output
    data = _json.loads(result.output)
    assert data["command"] == "pr-bundle"
    verdict = data["summary"]["verdict"]
    assert isinstance(verdict, str) and verdict, verdict
    # Empty-bucket discipline: NO W607-AE substrate markers on the clean envelope.
    top_wo = data.get("warnings_out") or []
    summary_wo = data["summary"].get("warnings_out") or []
    substrate_markers = [m for m in (list(top_wo) + list(summary_wo)) if m.startswith("pr_bundle_") and "_failed:" in m]
    assert not substrate_markers, (
        f"clean pr-bundle emit must NOT surface pr_bundle_<phase>_failed: "
        f"markers; got top={top_wo!r}, summary={summary_wo!r}"
    )


# ---------------------------------------------------------------------------
# (2) auto_collect failure -> pr_bundle_auto_collect_failed marker
# ---------------------------------------------------------------------------


def test_pr_bundle_auto_collect_failure_marker_format(cli_runner, pr_bundle_project, monkeypatch):
    """If _auto_collect raises, surface ``pr_bundle_auto_collect_failed:``."""
    from roam.commands import cmd_pr_bundle

    def _raise(*args, **kwargs):
        raise RuntimeError("synthetic-auto-collect-from-W607-AE")

    monkeypatch.setattr(cmd_pr_bundle, "_auto_collect", _raise)

    # --auto-collect is the default, but pass explicitly for clarity.
    result = _invoke_pr_bundle(cli_runner, pr_bundle_project, "emit", "--auto-collect")
    assert result.exit_code in (0, 5), result.output
    data = _json.loads(result.output)

    top_wo = data.get("warnings_out") or []
    ac_markers = [m for m in top_wo if m.startswith("pr_bundle_auto_collect_failed:")]
    assert ac_markers, f"expected pr_bundle_auto_collect_failed: marker; got {top_wo!r}"
    assert any("RuntimeError" in m for m in ac_markers), ac_markers
    assert any("synthetic-auto-collect-from-W607-AE" in m for m in ac_markers), ac_markers


# ---------------------------------------------------------------------------
# (3) causal_diff_pass failure -> pr_bundle_causal_diff_pass_failed marker
# ---------------------------------------------------------------------------


def test_pr_bundle_causal_diff_pass_failure_marker_format(cli_runner, pr_bundle_project, monkeypatch):
    """If _run_causal_diff_pass raises, surface the canonical marker.

    Pre-W607-AE this raise was silently swallowed to a degraded
    ``state: "diff_failed"`` payload (bare try/except). W607-AE replaces
    the swallow with a structured marker on ``warnings_out`` -- the
    degraded payload still ships, but the disclosure channel now names
    what crashed.
    """
    from roam.commands import cmd_pr_bundle

    def _raise(*args, **kwargs):
        raise RuntimeError("synthetic-causal-from-W607-AE")

    monkeypatch.setattr(cmd_pr_bundle, "_run_causal_diff_pass", _raise)

    result = _invoke_pr_bundle(cli_runner, pr_bundle_project, "emit", "--no-auto-collect")
    assert result.exit_code in (0, 5), result.output
    data = _json.loads(result.output)

    top_wo = data.get("warnings_out") or []
    cd_markers = [m for m in top_wo if m.startswith("pr_bundle_causal_diff_pass_failed:")]
    assert cd_markers, f"expected pr_bundle_causal_diff_pass_failed: marker; got {top_wo!r}"


# ---------------------------------------------------------------------------
# (4) build_envelope failure -> marker + envelope still completes
# ---------------------------------------------------------------------------


def test_pr_bundle_build_envelope_failure_marker_format(cli_runner, pr_bundle_project, monkeypatch):
    """If _build_envelope raises, surface the marker AND emit a fallback envelope.

    Pattern 2 discipline: a raise inside the composer must still produce
    a structured envelope (with ``partial_success: True``), not crash
    the CLI.
    """
    from roam.commands import cmd_pr_bundle

    def _raise(*args, **kwargs):
        raise RuntimeError("synthetic-build-env-from-W607-AE")

    monkeypatch.setattr(cmd_pr_bundle, "_build_envelope", _raise)

    result = _invoke_pr_bundle(cli_runner, pr_bundle_project, "emit", "--no-auto-collect")
    assert result.exit_code in (0, 5), result.output
    data = _json.loads(result.output)

    top_wo = data.get("warnings_out") or []
    be_markers = [m for m in top_wo if m.startswith("pr_bundle_build_envelope_failed:")]
    assert be_markers, f"expected pr_bundle_build_envelope_failed: marker; got {top_wo!r}"
    # The fallback envelope still surfaces a verdict + partial_success.
    assert data["summary"].get("partial_success") is True, data["summary"]


# ---------------------------------------------------------------------------
# (5) W805 6-artifact bonus: emit_slsa_l3 raise -> marker + bundle completes
# ---------------------------------------------------------------------------


def test_pr_bundle_emit_slsa_l3_failure_w805_bridge(cli_runner, pr_bundle_project, monkeypatch):
    """The W805 6-artifact bridge: emit_slsa_l3 raise -> marker + bundle still ships.

    cmd_pr_bundle's --slsa-l3 path drives emission of artifacts 2-6 of
    the W805 family (VSA, run-ledger root, cosign sig, Rekor entry,
    Fulcio cert) via subprocess. A raise inside that emitter previously
    crashed the entire bundle build wholesale. W607-AE wraps the
    emitter so a raise surfaces a ``pr_bundle_emit_slsa_l3_failed:``
    marker on ``warnings_out`` AND the bundle envelope still completes
    with ``slsa_l3: null`` + ``partial_success: True``.

    This is the W805 cross-artifact-consistency family's runtime
    twin: structural pins (W805-KKKKK etc.) catch silent drift,
    W607-AE markers catch raised drift.
    """
    from roam.commands import cmd_pr_bundle

    def _raise(*args, **kwargs):
        raise RuntimeError("synthetic-slsa-l3-from-W607-AE")

    monkeypatch.setattr(cmd_pr_bundle, "_emit_slsa_l3_attestations", _raise)

    result = _invoke_pr_bundle(
        cli_runner,
        pr_bundle_project,
        "emit",
        "--no-auto-collect",
        "--slsa-l3",
    )
    assert result.exit_code in (0, 5), result.output
    data = _json.loads(result.output)

    top_wo = data.get("warnings_out") or []
    slsa_markers = [m for m in top_wo if m.startswith("pr_bundle_emit_slsa_l3_failed:")]
    assert slsa_markers, f"expected pr_bundle_emit_slsa_l3_failed: marker; got {top_wo!r}"
    # The bundle envelope still completes; slsa_l3 is the absent-artifact
    # disclosure (Pattern 2: explicit absence beats silence).
    assert data.get("slsa_l3") is None, (
        f"emit_slsa_l3 raised; expected slsa_l3=None on the envelope, got {data.get('slsa_l3')!r}"
    )
    assert data["summary"].get("partial_success") is True, (
        f"emit_slsa_l3 raise must flip partial_success=True; got summary = {data['summary']!r}"
    )


# ---------------------------------------------------------------------------
# (6) warnings_out lands in both summary AND top-level envelope
# ---------------------------------------------------------------------------


def test_pr_bundle_warnings_out_in_envelope(cli_runner, pr_bundle_project, monkeypatch):
    """Non-empty bucket -> BOTH top-level AND summary.warnings_out populated."""
    from roam.commands import cmd_pr_bundle

    def _raise(*args, **kwargs):
        raise RuntimeError("synthetic-mirror-from-W607-AE")

    monkeypatch.setattr(cmd_pr_bundle, "_auto_collect", _raise)

    result = _invoke_pr_bundle(cli_runner, pr_bundle_project, "emit", "--auto-collect")
    assert result.exit_code in (0, 5), result.output
    data = _json.loads(result.output)

    assert data.get("warnings_out"), (
        f"top-level warnings_out missing on disclosure path; keys = {sorted(data.keys())!r}"
    )
    assert data["summary"].get("warnings_out"), (
        f"summary.warnings_out missing on disclosure path; got summary = {data['summary']!r}"
    )
    markers = [m for m in data["warnings_out"] if m.startswith("pr_bundle_auto_collect_failed:")]
    assert markers, f"expected pr_bundle_auto_collect_failed: marker; got {data['warnings_out']!r}"
    assert any("synthetic-mirror-from-W607-AE" in m for m in markers), markers


# ---------------------------------------------------------------------------
# (7) partial_success flips when ANY emit-side helper raises
# ---------------------------------------------------------------------------


def test_partial_success_set_when_pr_bundle_helper_raises(cli_runner, pr_bundle_project, monkeypatch):
    """Any non-empty W607-AE bucket -> summary.partial_success = True."""
    from roam.commands import cmd_pr_bundle

    def _raise(*args, **kwargs):
        raise RuntimeError("synthetic-partial-success-from-W607-AE")

    monkeypatch.setattr(cmd_pr_bundle, "_auto_collect", _raise)

    result = _invoke_pr_bundle(cli_runner, pr_bundle_project, "emit", "--auto-collect")
    assert result.exit_code in (0, 5), result.output
    data = _json.loads(result.output)
    assert data["summary"].get("partial_success") is True, (
        f"non-empty warnings_out must flip summary.partial_success=True; got summary = {data['summary']!r}"
    )


# ---------------------------------------------------------------------------
# (8) Three-segment marker shape -- prefix:exc_class:detail
# ---------------------------------------------------------------------------


def test_three_segment_marker_shape(cli_runner, pr_bundle_project, monkeypatch):
    """Marker must have three colon-separated segments.

    Shape contract: ``<prefix>:<exc_class>:<detail>`` so downstream
    consumers can parse the exception class without regex gymnastics.
    Mirrors W607-A..AD contracts.
    """
    from roam.commands import cmd_pr_bundle

    def _raise(*args, **kwargs):
        raise PermissionError("synthetic-shape-detail-from-W607-AE")

    monkeypatch.setattr(cmd_pr_bundle, "_auto_collect", _raise)

    result = _invoke_pr_bundle(cli_runner, pr_bundle_project, "emit", "--auto-collect")
    assert result.exit_code in (0, 5), result.output
    data = _json.loads(result.output)

    top_wo = data.get("warnings_out") or []
    assert top_wo, "auto_collect guard must emit a marker"
    failure_markers = [m for m in top_wo if m.startswith("pr_bundle_auto_collect_failed:")]
    assert failure_markers, f"expected pr_bundle_auto_collect_failed: marker; got {top_wo!r}"

    marker = failure_markers[0]
    parts = marker.split(":", 2)
    assert len(parts) == 3, f"marker must have three colon-separated segments (prefix:exc_class:detail); got {marker!r}"
    assert parts[0] == "pr_bundle_auto_collect_failed", parts
    assert parts[1] == "PermissionError", parts
    assert parts[2], parts


# ---------------------------------------------------------------------------
# (9) Marker-prefix discipline -- ``pr_bundle_*`` not pr_analyze/pr_risk/etc.
# ---------------------------------------------------------------------------


def test_marker_prefix_pr_bundle_not_pr_analyze_or_pr_risk(cli_runner, pr_bundle_project, monkeypatch):
    """Every surfaced W607-AE marker uses the canonical ``pr_bundle_*`` prefix.

    cmd_pr_bundle is the producer at the heart of the W805 family --
    distinct from sibling W607-* layers. Hard guard against accidental
    marker-prefix drift.
    """
    from roam.commands import cmd_pr_bundle

    def _raise(*args, **kwargs):
        raise PermissionError("synthetic-prefix-discipline-from-W607-AE")

    monkeypatch.setattr(cmd_pr_bundle, "_auto_collect", _raise)

    result = _invoke_pr_bundle(cli_runner, pr_bundle_project, "emit", "--auto-collect")
    assert result.exit_code in (0, 5), result.output
    data = _json.loads(result.output)
    top_wo = data.get("warnings_out") or []
    # Filter to substrate-CALL markers (have ``_failed:`` in the middle).
    substrate_markers = [m for m in top_wo if "_failed:" in m]
    assert substrate_markers, "expected non-empty substrate markers for prefix-consistency check"
    for marker in substrate_markers:
        assert marker.startswith("pr_bundle_"), (
            f"every surfaced W607-AE marker must use the ``pr_bundle_*`` "
            f"prefix family (cmd_pr_bundle scope); got {marker!r}"
        )
        # Hard distinction from sibling W607-* layers.
        for forbidden_prefix, sibling in (
            ("pr_analyze_", "cmd_pr_analyze W607-AA"),
            ("pr_risk_", "cmd_pr_risk W607-AB/Q"),
            ("diff_", "cmd_diff W607-Z"),
            ("critique_", "cmd_critique W607-Y"),
            ("relate_", "cmd_relate W607-W"),
        ):
            assert not marker.startswith(forbidden_prefix), (
                f"marker leaked into ``{forbidden_prefix}*`` family ({sibling} scope); got {marker!r}"
            )


# ---------------------------------------------------------------------------
# (10) Sibling parity -- W607-AA cmd_pr_analyze surface unchanged
# ---------------------------------------------------------------------------


def test_w607_aa_cmd_pr_analyze_unaffected():
    """Sibling parity guard: W607-AA cmd_pr_analyze source surface unchanged.

    W607-AE lands only in cmd_pr_bundle. The W607-AA cmd_pr_analyze
    surface (``_w607aa_warnings_out`` accumulator + ``pr_analyze_*``
    marker emission) MUST stay identical.
    """
    src_path = Path(__file__).parent.parent / "src" / "roam" / "commands" / "cmd_pr_analyze.py"
    assert src_path.exists(), f"cmd_pr_analyze.py missing at {src_path}"
    src = src_path.read_text(encoding="utf-8")
    assert "w607aa_warnings_out" in src, (
        "W607-AA accumulator removed from cmd_pr_analyze; W607-AE must not regress the sibling instrumentation."
    )
    assert "pr_analyze_{phase}_failed" in src, (
        "W607-AA marker prefix template removed from cmd_pr_analyze; "
        "W607-AE must not regress the sibling marker family."
    )


# ---------------------------------------------------------------------------
# (11) Source-level guard: cmd_pr_bundle carries the canonical W607-AE accumulator
# ---------------------------------------------------------------------------


def test_cmd_pr_bundle_carries_w607ae_accumulator():
    """AST-level guard: cmd_pr_bundle source carries the W607-AE accumulator.

    Pins the canonical anchors so a future refactor that removes the
    instrumentation (e.g. switches to a single try/except wrapping the
    whole command body) fails this guard rather than silently regressing
    every other test on dynamic envelope shape.
    """
    src_path = Path(__file__).parent.parent / "src" / "roam" / "commands" / "cmd_pr_bundle.py"
    assert src_path.exists(), f"cmd_pr_bundle.py missing at {src_path}"
    src = src_path.read_text(encoding="utf-8")
    assert "w607ae_warnings_out" in src, (
        "W607-AE accumulator missing from cmd_pr_bundle; the substrate-CALL marker plumbing has been removed."
    )
    assert "pr_bundle_{phase}_failed" in src, (
        "W607-AE marker prefix template missing from cmd_pr_bundle; check the "
        '`f"pr_bundle_{phase}_failed:..."` line in _run_check_ae.'
    )
    # Parse-tree level: confirm _run_check_ae is defined inside pr_bundle_emit().
    tree = ast.parse(src)
    found_run_check = False
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "_run_check_ae":
            found_run_check = True
            break
    assert found_run_check, (
        "W607-AE ``_run_check_ae`` helper not found in cmd_pr_bundle AST; "
        "the per-substrate wrapper has been refactored away."
    )


# ---------------------------------------------------------------------------
# (12) Each substrate phase is wrapped (source-level)
# ---------------------------------------------------------------------------


def test_all_substrate_phases_wrapped_in_source():
    """Source-level guard: every cmd_pr_bundle emit substrate boundary is wrapped.

    W607-AE substrate inventory (emit-path boundaries, in order of
    importance for the W805 6-artifact family):

    * resolve_actor_block  -- _resolve_actor_block(...)   (W189)
    * mode_blocks_emit     -- _mode_blocks_emit(root)     (W14.2)
    * auto_collect         -- _auto_collect(bundle, root) (envelope fold)
    * causal_diff_pass     -- _run_causal_diff_pass(...)  (W15.3)
    * atomic_write_bundle  -- _atomic_write_bundle(...)   (disk persist)
    * build_envelope       -- _build_envelope(...)        (composer)
    * emit_slsa_l3         -- _emit_slsa_l3_attestations  (W805 6-artifact)

    If a future wave introduces a new substrate boundary, this guard
    needs to know about it -- add the phase name here.
    """
    src_path = Path(__file__).parent.parent / "src" / "roam" / "commands" / "cmd_pr_bundle.py"
    src = src_path.read_text(encoding="utf-8")
    expected_phases = [
        "resolve_actor_block",
        "mode_blocks_emit",
        "auto_collect",
        "causal_diff_pass",
        "atomic_write_bundle",
        "build_envelope",
        "emit_slsa_l3",
    ]
    for phase in expected_phases:
        # Accept either same-line ``_run_check_ae("phase",`` or a multi-line
        # block where the phase string is the first argument on the next
        # line -- both are legitimate refactor shapes. The actual file
        # indentation depth varies (8/12/16/20/24 spaces) depending on the
        # site's nesting; accept any of the canonical depths.
        same_line = f'_run_check_ae("{phase}"' in src
        multi_line = (
            f'_run_check_ae(\n        "{phase}"' in src
            or f'_run_check_ae(\n            "{phase}"' in src
            or f'_run_check_ae(\n                "{phase}"' in src
            or f'_run_check_ae(\n                    "{phase}"' in src
            or f'_run_check_ae(\n                        "{phase}"' in src
        )
        assert same_line or multi_line, (
            f"W607-AE _run_check_ae wrap missing for phase {phase!r}; substrate boundary is no longer caught."
        )
