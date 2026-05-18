"""W607-AF -- ``cmd_cga`` substrate-boundary plumbing.

Thirtieth-in-batch W607 consumer-layer arc. Fresh-plumbing wave: cmd_cga had
NO prior W607 instrumentation, so the canonical fresh template applies (one
accumulator + one ``_run_check_af`` helper, in BOTH the ``emit`` and
``verify`` subcommands).

cmd_cga is the CRYPTOGRAPHIC core of the W805 cross-artifact-consistency
family. It emits the Code Graph Attestation (in-toto v1 Statement, predicate
type ``roam-code.com/spec/CodeGraph/v1``) and verifies it. Each substrate
boundary -- build_cga_statement / serialize_statement / cosign_sign_statement
/ atomic_write_text / emit_vsa_sibling / verify_cga_statement /
cosign_verify_statement -- can raise; prior to W607-AF a raise crashed the
whole emit / verify path wholesale.

W805 cross-artifact consistency family
--------------------------------------

cmd_cga is the FIRST artifact in the W805-KKKKK / OOOOO / PPPPP / RRRRR /
SSSSS family chain (CGA / VSA / Rekor pipeline). Closes the cryptographic-
attestation triad together with W607-AD (cmd_attest) and W607-AE
(cmd_pr_bundle in-flight). The W607-AF markers fire AT RUNTIME when an
emission boundary raises, complementing the W805 xfail-strict pins that catch
structural inconsistency at the dataclass level.

W489-A sibling parity
---------------------

cmd_cga's ``cga-emit`` envelope already carried a ``summary.warnings_out``
field for the W489-A qualified_only rules-lint axis (a rules-shape disclosure
that fires when ``--include-taint`` loads a rule pack with bare-name
sanitizers). W607-AF is ADDITIVE: substrate-CALL markers merge into the SAME
``summary.warnings_out`` list, and ``partial_success`` flips when EITHER
bucket is non-empty. The marker PREFIX disambiguates them downstream
(``qualified_only lint flagged ...`` vs ``cga_<phase>_failed:*``).

W978 first-hypothesis check
---------------------------

Each W607-AF-wrapped substrate has a documented empty-floor default matching
its happy-path return shape so a raise degrades cleanly. Dominant raise axes
are: cosign binary missing (``cosign_sign_statement`` /
``cosign_verify_statement``), graph row inconsistency
(``build_cga_statement``), filesystem refusal (``atomic_write_text``), and
malformed statement (``serialize_statement`` / ``verify_cga_statement``).

W907 verify-cycle check
-----------------------

No "duplicated to avoid cycle" docstrings added. Every substrate is referenced
by its imported module-level name and patched via ``monkeypatch.setattr`` on
``cmd_cga`` at test time.

Marker prefix discipline
------------------------

Marker family is ``cga_<phase>_failed:<exc_class>:<detail>``. Hard distinction
from sibling W607-* layers (``attest_*``, ``diff_*``, ``critique_*``, etc.)
preserved by the prefix-discipline test.

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
# Helpers -- invoke cga emit / verify via the Click group
# ---------------------------------------------------------------------------


def _invoke_cga_emit(runner: CliRunner, cwd, json_mode: bool = True, *extra):
    """Invoke ``roam cga emit`` through the group so ``--json`` is honoured."""
    from roam.cli import cli

    args: list[str] = []
    if json_mode:
        args.append("--json")
    args.extend(["cga", "emit", "--allow-dirty"])
    args.extend(extra)

    old_cwd = os.getcwd()
    try:
        os.chdir(str(cwd))
        result = runner.invoke(cli, args, catch_exceptions=False)
    finally:
        os.chdir(old_cwd)
    return result


# ---------------------------------------------------------------------------
# Fixture -- indexed corpus committed clean so cga emit reaches the
# build_cga_statement substrate boundary.
# ---------------------------------------------------------------------------


@pytest.fixture
def cli_runner():
    return CliRunner()


@pytest.fixture
def cga_project_indexed(tmp_path, monkeypatch):
    """Indexed corpus committed clean so cga emit exercises every W607-AF
    substrate boundary (build_cga_statement / serialize_statement /
    atomic_write_text). Tests pass ``--allow-dirty`` so the dirty-tree
    short-circuit doesn't fire even if the index changes the working tree.
    """
    proj = tmp_path / "cga_w607af_project"
    proj.mkdir()
    (proj / ".gitignore").write_text(".roam/\n")
    src = proj / "src"
    src.mkdir()
    (src / "main.py").write_text(
        "def main():\n    helper()\n    return 1\n\ndef helper():\n    return 42\n",
        encoding="utf-8",
    )
    git_init(proj)
    monkeypatch.chdir(proj)
    out, rc = index_in_process(proj, "--force")
    assert rc == 0, f"index failed:\n{out}"
    return proj


# ---------------------------------------------------------------------------
# (1) Happy path -- envelope omits W607-AF substrate-CALL markers
# ---------------------------------------------------------------------------


def test_cga_emit_clean_envelope_omits_w607af_markers(cli_runner, cga_project_indexed):
    """Clean cga-emit -> no W607-AF substrate markers.

    Byte-identical-on-happy-path: an empty W607-AF bucket on the success
    path must NOT introduce ``cga_<phase>_failed:`` markers on the
    envelope. The pre-existing W489-A ``qualified_only`` bucket only fires
    with ``--include-taint`` so it's quiet here too.
    """
    result = _invoke_cga_emit(cli_runner, cga_project_indexed, True, "--no-write")
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)
    assert data["command"] == "cga-emit"
    verdict = data["summary"]["verdict"]
    assert isinstance(verdict, str) and verdict, verdict

    top_wo = data.get("warnings_out") or []
    summary_wo = data["summary"].get("warnings_out") or []
    af_markers = [m for m in (list(top_wo) + list(summary_wo)) if "_failed:" in m and m.startswith("cga_")]
    assert not af_markers, (
        f"clean cga-emit must NOT surface W607-AF substrate markers; got top={top_wo!r}, summary={summary_wo!r}"
    )


# ---------------------------------------------------------------------------
# (2) build_cga_statement failure -> structured marker + clean degraded envelope
# ---------------------------------------------------------------------------


def test_cga_build_cga_statement_failure_marker_format(cli_runner, cga_project_indexed, monkeypatch):
    """If ``build_cga_statement`` raises, surface the W607-AF marker.

    The CGA Statement construction is the central substrate boundary -- a
    raise here previously crashed the whole emit path. W607-AF surfaces
    it as a structured ``cga_build_cga_statement_failed:<exc>:<detail>``
    marker and emits a structured ``build_failed`` envelope rather than
    crashing.
    """
    from roam.commands import cmd_cga

    def _raise(*args, **kwargs):
        raise RuntimeError("synthetic-build-from-W607-AF")

    monkeypatch.setattr(cmd_cga, "build_cga_statement", _raise)

    result = _invoke_cga_emit(cli_runner, cga_project_indexed, True, "--no-write")
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    top_wo = data.get("warnings_out") or []
    summary_wo = data["summary"].get("warnings_out") or []
    all_wo = list(top_wo) + list(summary_wo)
    markers = [m for m in all_wo if m.startswith("cga_build_cga_statement_failed:")]
    assert markers, f"expected cga_build_cga_statement_failed: marker; got {all_wo!r}"
    assert any("RuntimeError" in m for m in markers), markers
    assert any("synthetic-build-from-W607-AF" in m for m in markers), markers
    # Envelope flips partial_success on the build-failed degraded path.
    assert data["summary"].get("partial_success") is True, (
        f"build-failed degraded envelope must flip partial_success; got summary = {data['summary']!r}"
    )


# ---------------------------------------------------------------------------
# (3) serialize_statement failure -> structured marker
# ---------------------------------------------------------------------------


def test_cga_serialize_statement_failure_marker_format(cli_runner, cga_project_indexed, monkeypatch):
    """If ``serialize_statement`` raises, surface the W607-AF marker.

    Canonical-JSON serialization is the boundary between the in-memory
    statement and the on-disk artifact. A raise here would crash the emit
    path before reaching the write boundary; W607-AF degrades gracefully.
    """
    from roam.commands import cmd_cga

    def _raise(*args, **kwargs):
        raise RuntimeError("synthetic-serialize-from-W607-AF")

    monkeypatch.setattr(cmd_cga, "serialize_statement", _raise)

    result = _invoke_cga_emit(cli_runner, cga_project_indexed, True, "--no-write")
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    top_wo = data.get("warnings_out") or []
    markers = [m for m in top_wo if m.startswith("cga_serialize_statement_failed:")]
    assert markers, f"expected cga_serialize_statement_failed: marker; got {top_wo!r}"
    assert any("RuntimeError" in m for m in markers), markers


# ---------------------------------------------------------------------------
# (4) cosign_sign_statement failure -> CRYPTOGRAPHIC boundary marker (W805 bonus)
# ---------------------------------------------------------------------------


def test_cga_sign_statement_failure_marker_format(cli_runner, cga_project_indexed, tmp_path, monkeypatch):
    """W805-family bonus: simulated cosign_sign_statement raise.

    The cosign-signing boundary is the cryptographic-attestation core -- a
    raise here means the statement is on disk but the signature is missing.
    W607-AF surfaces the marker AND the envelope still emits with
    ``partial_success: true`` rather than crashing the bundle build
    wholesale (the W805 cross-artifact-consistency property).
    """
    from roam.commands import cmd_cga

    def _raise(*args, **kwargs):
        raise RuntimeError("synthetic-sign-from-W607-AF")

    monkeypatch.setattr(cmd_cga, "cosign_sign_statement", _raise)

    out_path = tmp_path / "cga.intoto.json"
    result = _invoke_cga_emit(
        cli_runner,
        cga_project_indexed,
        True,
        "--output",
        str(out_path),
        "--sign",
    )
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    top_wo = data.get("warnings_out") or []
    markers = [m for m in top_wo if m.startswith("cga_cosign_sign_statement_failed:")]
    assert markers, f"expected cga_cosign_sign_statement_failed: marker (W805 bonus); got {top_wo!r}"
    assert any("RuntimeError" in m for m in markers), markers
    # W805 invariant: envelope still emits cleanly with partial_success.
    assert data["summary"].get("partial_success") is True, (
        f"sign-failed path must flip partial_success "
        f"(W805 cross-artifact-consistency invariant); "
        f"got summary = {data['summary']!r}"
    )


# ---------------------------------------------------------------------------
# (5) warnings_out lands in envelope (top-level AND summary mirror)
# ---------------------------------------------------------------------------


def test_cga_w607af_warnings_in_envelope(cli_runner, cga_project_indexed, monkeypatch):
    """Non-empty W607-AF bucket -> both top-level AND summary.warnings_out."""
    from roam.commands import cmd_cga

    def _raise(*args, **kwargs):
        raise RuntimeError("synthetic-mirror-from-W607-AF")

    monkeypatch.setattr(cmd_cga, "serialize_statement", _raise)

    result = _invoke_cga_emit(cli_runner, cga_project_indexed, True, "--no-write")
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    assert data.get("warnings_out"), (
        f"top-level warnings_out missing on W607-AF disclosure path; keys = {sorted(data.keys())!r}"
    )
    assert data["summary"].get("warnings_out"), (
        f"summary.warnings_out missing on W607-AF disclosure path; got summary = {data['summary']!r}"
    )
    markers = [m for m in data["warnings_out"] if m.startswith("cga_serialize_statement_failed:")]
    assert markers, f"expected cga_serialize_statement_failed: marker; got {data['warnings_out']!r}"


# ---------------------------------------------------------------------------
# (6) partial_success flips when W607-AF substrate raises
# ---------------------------------------------------------------------------


def test_partial_success_set_when_w607af_helper_raises(cli_runner, cga_project_indexed, monkeypatch):
    """Any non-empty W607-AF bucket -> summary.partial_success = True."""
    from roam.commands import cmd_cga

    def _raise(*args, **kwargs):
        raise RuntimeError("synthetic-partial-success-from-W607-AF")

    monkeypatch.setattr(cmd_cga, "serialize_statement", _raise)

    result = _invoke_cga_emit(cli_runner, cga_project_indexed, True, "--no-write")
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)
    assert data["summary"].get("partial_success") is True, (
        f"non-empty W607-AF warnings_out must flip summary.partial_success=True; got summary = {data['summary']!r}"
    )


# ---------------------------------------------------------------------------
# (7) Three-segment marker shape -- prefix:exc_class:detail
# ---------------------------------------------------------------------------


def test_three_segment_marker_shape(cli_runner, cga_project_indexed, monkeypatch):
    """Marker must have three colon-separated segments.

    Shape contract: ``<prefix>:<exc_class>:<detail>`` so downstream
    consumers can parse the exception class without regex gymnastics.
    Mirrors W607-A..AD contracts.
    """
    from roam.commands import cmd_cga

    def _raise(*args, **kwargs):
        raise PermissionError("synthetic-shape-detail-from-W607-AF")

    monkeypatch.setattr(cmd_cga, "serialize_statement", _raise)

    result = _invoke_cga_emit(cli_runner, cga_project_indexed, True, "--no-write")
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    top_wo = data.get("warnings_out") or []
    failure_markers = [m for m in top_wo if m.startswith("cga_serialize_statement_failed:")]
    assert failure_markers, f"expected cga_serialize_statement_failed: marker; got {top_wo!r}"

    marker = failure_markers[0]
    parts = marker.split(":", 2)
    assert len(parts) == 3, f"marker must have three colon-separated segments (prefix:exc_class:detail); got {marker!r}"
    assert parts[0] == "cga_serialize_statement_failed", parts
    assert parts[1] == "PermissionError", parts
    assert parts[2], parts


# ---------------------------------------------------------------------------
# (8) Marker-prefix discipline -- W607-AF stays in ``cga_*`` family
# ---------------------------------------------------------------------------


def test_w607af_marker_prefix_stays_in_cga_family(cli_runner, cga_project_indexed, monkeypatch):
    """Every W607-AF substrate marker uses the canonical ``cga_*`` prefix.

    cmd_cga is the CGA emit/verify pipeline -- distinct from sibling W607-*
    layers. Marker prefix MUST stay ``cga_*`` and MUST NOT leak into other
    family prefixes (``attest_*``, ``diff_*``, etc.).
    """
    from roam.commands import cmd_cga

    def _raise(*args, **kwargs):
        raise PermissionError("synthetic-prefix-discipline-from-W607-AF")

    monkeypatch.setattr(cmd_cga, "build_cga_statement", _raise)

    result = _invoke_cga_emit(cli_runner, cga_project_indexed, True, "--no-write")
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)
    top_wo = data.get("warnings_out") or []
    summary_wo = data["summary"].get("warnings_out") or []
    all_wo = list(top_wo) + list(summary_wo)
    substrate_markers = [m for m in all_wo if "_failed:" in m]
    assert substrate_markers, "expected non-empty substrate markers for prefix-consistency check"
    for marker in substrate_markers:
        assert marker.startswith("cga_"), (
            f"every surfaced W607-AF marker must use the ``cga_*`` prefix family (cmd_cga scope); got {marker!r}"
        )
        # Hard distinction from sibling W607-* layers.
        for forbidden_prefix, sibling in (
            ("attest_", "cmd_attest W607-AD"),
            ("diff_", "cmd_diff W607-Z"),
            ("critique_", "cmd_critique W607-Y"),
            ("pr_risk_", "cmd_pr_risk W607-Q / W607-AB"),
            ("relate_", "cmd_relate W607-W"),
            ("deps_", "cmd_deps W607-V"),
            ("uses_", "cmd_uses W607-U"),
            ("impact_", "cmd_impact W607-T"),
            ("diagnose_", "cmd_diagnose W607-S"),
            ("preflight_", "cmd_preflight W607-R"),
            ("audit_", "cmd_audit W607-P"),
            ("dashboard_", "cmd_dashboard W607-O"),
            ("doctor_", "cmd_doctor W607-N"),
            ("health_", "cmd_health W607-M"),
            ("describe_", "cmd_describe W607-K"),
            ("minimap_", "cmd_minimap W607-L"),
        ):
            assert not marker.startswith(forbidden_prefix), (
                f"marker leaked into ``{forbidden_prefix}*`` family ({sibling} scope); got {marker!r}"
            )


# ---------------------------------------------------------------------------
# (9) Source-level guard: cmd_cga carries the W607-AF accumulator
# ---------------------------------------------------------------------------


def test_cmd_cga_carries_w607af_accumulator():
    """AST-level guard: cmd_cga source carries the W607-AF accumulator.

    Pins the canonical anchors so a future refactor that removes the
    W607-AF instrumentation fails this guard rather than silently
    regressing every other test on dynamic envelope shape.
    """
    src_path = Path(__file__).parent.parent / "src" / "roam" / "commands" / "cmd_cga.py"
    assert src_path.exists(), f"cmd_cga.py missing at {src_path}"
    src = src_path.read_text(encoding="utf-8")
    assert "_w607af_warnings_out" in src, (
        "W607-AF accumulator missing from cmd_cga; the substrate-CALL marker plumbing has been removed."
    )
    assert "_run_check_af" in src, (
        "W607-AF ``_run_check_af`` helper missing from cmd_cga; the per-substrate wrapper has been refactored away."
    )
    # Parse-tree level: confirm _run_check_af is defined inside cmd_cga.
    tree = ast.parse(src)
    found_run_check_af = False
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "_run_check_af":
            found_run_check_af = True
            break
    assert found_run_check_af, (
        "W607-AF ``_run_check_af`` helper not found in cmd_cga AST; the per-substrate wrapper has been refactored away."
    )


# ---------------------------------------------------------------------------
# (10) Each W607-AF substrate phase is wrapped (source-level)
# ---------------------------------------------------------------------------


def test_all_w607af_substrate_phases_wrapped_in_source():
    """Source-level guard: every W607-AF substrate boundary is wrapped.

    W607-AF substrate inventory (cmd_cga -- emit + verify subcommands):

    EMIT path:
    * git_dirty_hash         -- _git_dirty_hash(project_root) (dirty-tree gate)
    * run_taint              -- run_taint(conn, rules) (optional, --include-taint)
    * build_cga_statement    -- the central CGA Statement construction
    * serialize_statement    -- canonical-JSON serialization boundary
    * atomic_write_text      -- on-disk write boundary
    * cosign_sign_statement  -- CRYPTOGRAPHIC signing boundary (W805 bonus)
    * emit_vsa_sibling       -- W486 VSA sibling helper

    VERIFY path:
    * verify_cga_statement   -- re-derive merkle/edge digest
    * cosign_verify_statement -- CRYPTOGRAPHIC verification boundary

    If a future wave introduces a new substrate boundary, this guard
    needs to know about it -- add the phase name here. Accepts multiple
    indent depths because the call sites span ``with open_db(...)``
    blocks and conditional branches (8/12/16/20/24 spaces).
    """
    src_path = Path(__file__).parent.parent / "src" / "roam" / "commands" / "cmd_cga.py"
    src = src_path.read_text(encoding="utf-8")
    expected_phases = [
        "git_dirty_hash",
        "run_taint",
        "build_cga_statement",
        "serialize_statement",
        "atomic_write_text",
        "cosign_sign_statement",
        "emit_vsa_sibling",
        "verify_cga_statement",
        "cosign_verify_statement",
    ]
    for phase in expected_phases:
        same_line = f'_run_check_af("{phase}"' in src
        # Multi-line variant: phase string on the next line, indented at
        # 8/12/16/20/24 spaces depending on nesting depth.
        multi_line = (
            f'_run_check_af(\n        "{phase}"' in src
            or f'_run_check_af(\n            "{phase}"' in src
            or f'_run_check_af(\n                "{phase}"' in src
            or f'_run_check_af(\n                    "{phase}"' in src
            or f'_run_check_af(\n                        "{phase}"' in src
        )
        assert same_line or multi_line, (
            f"W607-AF _run_check_af wrap missing for phase {phase!r}; substrate boundary is no longer caught."
        )
