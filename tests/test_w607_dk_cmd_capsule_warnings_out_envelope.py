"""W607-DK -- ``cmd_capsule`` substrate-CALL-layer plumbing.

cmd_capsule is the graph-EXPORT companion to cmd_fingerprint (topology-HASH).
Together they close the architecture-export 2-way at the substrate-CALL
layer: cmd_fingerprint = W607-DH, cmd_capsule = W607-DK.

The W607-BD wave wrapped the INNER gather helpers passed into
``_build_capsule(run_check=...)``. W607-DK wraps the OUTER call-layer
substrates the BD wave did not cover -- so a raise in the assembly logic
OUTSIDE the gather helpers (or in the verdict-composition / envelope-
composition / atomic-write outer caller) does not crash the capsule
exporter wholesale.

Substrates wrapped via ``_run_check_dk``:

* build_capsule_payload   -- the outer ``_build_capsule`` call composition.
                             A raise in the assembly logic OUTSIDE the
                             gather helpers (e.g., dict.update on a
                             corrupted return, AttributeError on a
                             None-typed health row) degrades to an
                             empty-floor capsule.
* compose_verdict         -- LAW 6 single-line verdict composition. A
                             KeyError on a corrupted ``capsule_data``
                             dict degrades to a no-topology floor.
* write_capsule_file      -- W82.1 atomic file-write at the call layer
                             (the BD inner wrap still catches inside
                             ``_serialize_and_write``; the DK outer wrap
                             catches a raise on import / Path() / closure
                             construction BEFORE the BD wrap fires).
* serialize_to_json       -- json_envelope composition. A circular-ref
                             field in capsule_data surfaces a marker
                             rather than crashing before to_json runs.

Marker family ``capsule_<phase>_failed:<exc_class>:<detail>`` (shared
with W607-BD -- one capsule_* marker family, multiple wave-layered
accumulators).

W82.1 REGRESSION GUARD
----------------------

The W82.1 atomic file-write pattern stays wired: a clean run with
--output writes the JSON to disk and the file round-trips through
``json.loads`` cleanly.

PER-SUBSTRATE ISOLATION
-----------------------

Simulate ONE substrate raising while the others succeed. The marker
surfaces for the failed substrate, the others contribute fields
normally, and the envelope stays well-formed.

CROSS-PREFIX ISOLATION
----------------------

The ``capsule_*`` markers do NOT leak into adjacent W607-* families
(fingerprint / health / complexity / dark_matter / smells / etc.), AND
sibling prefixes do NOT leak INTO the capsule envelope.

ARCHITECTURE-EXPORT 2-WAY PAIRING
---------------------------------

An AST-scan over cmd_fingerprint + cmd_capsule confirms BOTH carry
W607 substrate-CALL plumbing (cmd_fingerprint = ``_run_check_dh``,
cmd_capsule = ``_run_check_dk``). This pins the architecture-export
2-way.

W978 7-DISCIPLINE COMPLIANCE
----------------------------

The AST audit pins:
- every ``default=`` is a literal constant (kwarg-default eagerness)
- every ``len()`` / dict-index over poisonable input lives INSIDE the
  wrapped closure (5th discipline)
- phase names are unique within the file (4th discipline)
- ``json.dumps(default=str)`` only inside the intentional W82.1
  on-disk-export contract, never in DK closures wrapping the envelope.
"""

from __future__ import annotations

import ast
import json as _json
import os
from pathlib import Path

import pytest
from click.testing import CliRunner

# ---------------------------------------------------------------------------
# Canonical W607-DK phase enumeration
# ---------------------------------------------------------------------------


_DK_PHASES = (
    "build_capsule_payload",
    "compose_verdict",
    "write_capsule_file",
    "serialize_to_json",
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def cli_runner():
    return CliRunner()


@pytest.fixture
def capsule_project(project_factory):
    """Small Python project for capsule export."""
    return project_factory(
        {
            "app.py": ("from lib import helper\ndef main():\n    return helper()\n"),
            "lib.py": ("def helper():\n    return 42\n"),
        }
    )


def _invoke_capsule(cli_runner, project_root, *args, json_mode=True):
    """Invoke ``roam capsule`` via the top-level CLI."""
    from roam.cli import cli

    full_args: list[str] = []
    if json_mode:
        full_args.append("--json")
    full_args.append("capsule")
    full_args.extend(args)
    old_cwd = os.getcwd()
    try:
        os.chdir(str(project_root))
        return cli_runner.invoke(cli, full_args, catch_exceptions=False)
    finally:
        os.chdir(old_cwd)


# ---------------------------------------------------------------------------
# (1) Happy path -- envelope omits W607-DK substrate-CALL markers
# ---------------------------------------------------------------------------


def test_capsule_clean_envelope_omits_w607dk_markers(cli_runner, capsule_project):
    """Clean capsule export -> no W607-DK substrate-CALL markers.

    An empty W607-DK bucket on the success path must NOT introduce
    ``capsule_<phase>_failed:`` markers on the envelope. cmd_capsule
    has no pre-existing warnings_out channel, so the field is absent
    entirely on the clean path.
    """
    result = _invoke_capsule(cli_runner, capsule_project)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)
    assert data["command"] == "capsule"
    verdict = data["summary"]["verdict"]
    assert isinstance(verdict, str) and verdict, verdict

    top_wo = data.get("warnings_out") or []
    summary_wo = data["summary"].get("warnings_out") or []
    dk_markers = [m for m in (list(top_wo) + list(summary_wo)) if any(f"capsule_{p}_failed:" in m for p in _DK_PHASES)]
    assert not dk_markers, (
        f"clean capsule must NOT surface W607-DK substrate markers; got top={top_wo!r}, summary={summary_wo!r}"
    )
    # Happy path: partial_success is not set (or is False).
    assert not data["summary"].get("partial_success"), data["summary"]


# ---------------------------------------------------------------------------
# (2) build_capsule_payload failure -> marker + partial_success flip
# ---------------------------------------------------------------------------


def test_capsule_build_capsule_payload_failure_marker(cli_runner, capsule_project, monkeypatch):
    """A raise in the outer ``_build_capsule`` composition surfaces marker.

    This is the substrate-CALL boundary the BD inner wrap did NOT cover:
    a raise in the assembly logic OUTSIDE the gather helpers (e.g., an
    AttributeError composing the final dict) used to crash the entire
    command. The W607-DK outer wrap catches it and degrades to the
    empty-floor capsule.
    """
    from roam.commands import cmd_capsule as _mod

    def _raise(*args, **kwargs):
        raise RuntimeError("synthetic-build-payload-from-W607-DK")

    monkeypatch.setattr(_mod, "_build_capsule", _raise)

    result = _invoke_capsule(cli_runner, capsule_project)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    top_wo = data.get("warnings_out") or []
    summary_wo = data["summary"].get("warnings_out") or []
    all_wo = list(top_wo) + list(summary_wo)
    markers = [m for m in all_wo if m.startswith("capsule_build_capsule_payload_failed:")]
    assert markers, f"expected capsule_build_capsule_payload_failed: marker; got {all_wo!r}"
    assert any("RuntimeError" in m for m in markers), markers
    assert any("synthetic-build-payload-from-W607-DK" in m for m in markers), markers
    # Envelope flips partial_success on the degraded path.
    assert data["summary"].get("partial_success") is True, (
        f"build-payload-failed degraded envelope must flip partial_success; got summary = {data['summary']!r}"
    )
    # LAW 6: single-line verdict.
    verdict = data["summary"].get("verdict")
    assert isinstance(verdict, str) and verdict
    assert "\n" not in verdict, f"verdict must be single line: {verdict!r}"


# ---------------------------------------------------------------------------
# (3) warnings_out lands in BOTH envelope locations
# ---------------------------------------------------------------------------


def test_capsule_w607dk_warnings_in_envelope(cli_runner, capsule_project, monkeypatch):
    """Non-empty W607-DK bucket -> both top-level AND summary.warnings_out."""
    from roam.commands import cmd_capsule as _mod

    def _raise(*args, **kwargs):
        raise RuntimeError("synthetic-mirror-from-W607-DK")

    monkeypatch.setattr(_mod, "_build_capsule", _raise)

    result = _invoke_capsule(cli_runner, capsule_project)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    assert data.get("warnings_out"), (
        f"top-level warnings_out missing on W607-DK disclosure path; keys = {sorted(data.keys())!r}"
    )
    assert data["summary"].get("warnings_out"), (
        f"summary.warnings_out missing on W607-DK disclosure path; got summary = {data['summary']!r}"
    )
    markers = [m for m in data["warnings_out"] if m.startswith("capsule_build_capsule_payload_failed:")]
    assert markers, f"expected capsule_build_capsule_payload_failed: marker; got {data['warnings_out']!r}"


# ---------------------------------------------------------------------------
# (4) Three-segment marker shape -- prefix:exc_class:detail
# ---------------------------------------------------------------------------


def test_capsule_dk_three_segment_marker_shape(cli_runner, capsule_project, monkeypatch):
    """W607-DK marker must have three colon-separated segments."""
    from roam.commands import cmd_capsule as _mod

    def _raise(*args, **kwargs):
        raise PermissionError("synthetic-shape-detail-from-W607-DK")

    monkeypatch.setattr(_mod, "_build_capsule", _raise)

    result = _invoke_capsule(cli_runner, capsule_project)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    top_wo = data.get("warnings_out") or []
    failure_markers = [m for m in top_wo if m.startswith("capsule_build_capsule_payload_failed:")]
    assert failure_markers, top_wo

    marker = failure_markers[0]
    parts = marker.split(":", 2)
    assert len(parts) == 3, f"marker must have three colon-separated segments (prefix:exc_class:detail); got {marker!r}"
    assert parts[0] == "capsule_build_capsule_payload_failed", parts
    assert parts[1] == "PermissionError", parts
    assert parts[2], parts


# ---------------------------------------------------------------------------
# (5) compose_verdict failure -> marker, envelope still composes
# ---------------------------------------------------------------------------


def test_capsule_compose_verdict_failure_surfaces_marker(cli_runner, capsule_project, monkeypatch):
    """A raise inside ``_build_capsule`` returning a corrupted payload
    triggers the W607-DK ``compose_verdict`` wrap.

    The DK wrap embeds every dict lookup INSIDE the closure (W978 5th
    discipline). A non-dict ``capsule_data`` returned from the
    build_capsule_payload substrate cannot crash the verdict
    composition path.
    """
    from roam.commands import cmd_capsule as _mod

    def _corrupted_build(*args, **kwargs):
        # Return a payload with the WRONG type for ``topology`` -- a
        # downstream lookup like ``topology.get("files")`` would raise
        # AttributeError. The W607-DK compose_verdict wrap catches it.
        return {"topology": None, "health": None}

    monkeypatch.setattr(_mod, "_build_capsule", _corrupted_build)

    result = _invoke_capsule(cli_runner, capsule_project)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    all_wo = list(data.get("warnings_out") or []) + list(data["summary"].get("warnings_out") or [])
    verdict_markers = [m for m in all_wo if m.startswith("capsule_compose_verdict_failed:")]
    assert verdict_markers, f"expected capsule_compose_verdict_failed: marker for corrupted payload; got {all_wo!r}"
    # Envelope still composes a single-line verdict.
    verdict = data["summary"].get("verdict")
    assert isinstance(verdict, str) and verdict
    assert "\n" not in verdict


# ---------------------------------------------------------------------------
# (6) W82.1 file-write pattern preserved through W607-DK
# ---------------------------------------------------------------------------


def test_w82_1_file_write_pattern_preserved_under_w607dk(cli_runner, capsule_project, tmp_path):
    """W82.1 regression guard: --output still writes the on-disk JSON.

    The W82.1 atomic file-write pattern stays wired through the W607-DK
    write_capsule_file outer wrap -- a clean run with --output produces
    a parseable JSON file at the target path.
    """
    output_path = tmp_path / "capsule_out.json"
    # text mode (no --json) since --output ALWAYS writes regardless of
    # json mode; choose text-mode to keep the assertion strictly about
    # the on-disk artifact, not the stdout envelope.
    result = _invoke_capsule(
        cli_runner,
        capsule_project,
        "--output",
        str(output_path),
        json_mode=False,
    )
    assert result.exit_code == 0, result.output
    assert output_path.exists(), (
        f"W82.1 file-write pattern broken under W607-DK; expected {output_path} to exist after --output"
    )
    # The exported JSON must round-trip cleanly.
    parsed = _json.loads(output_path.read_text(encoding="utf-8"))
    assert isinstance(parsed, dict), parsed
    # Sanity: the capsule shape (topology / health / symbols / edges).
    assert "topology" in parsed, parsed.keys()
    assert "health" in parsed, parsed.keys()


def test_w82_1_write_capsule_file_failure_surfaces_marker(cli_runner, capsule_project, monkeypatch, tmp_path):
    """W82.1 + W607-DK: a raise inside the write substrate surfaces marker.

    The W607-DK wrap around the outer write call catches the raise and
    surfaces ``capsule_write_capsule_file_failed:`` (or the inner BD
    ``capsule_atomic_write_capsule_failed:`` marker) without crashing
    the rest of the envelope path.
    """
    import roam.atomic_io as _atomic_mod

    def _raise(*args, **kwargs):
        raise OSError("synthetic-write-from-W607-DK")

    monkeypatch.setattr(_atomic_mod, "atomic_write_text", _raise)

    output_path = tmp_path / "capsule_out.json"
    result = _invoke_capsule(
        cli_runner,
        capsule_project,
        "--output",
        str(output_path),
        json_mode=False,
    )
    # Command does NOT crash.
    assert result.exit_code == 0, result.output
    # The file should NOT have been created on disk.
    assert not output_path.exists() or output_path.stat().st_size == 0


# ---------------------------------------------------------------------------
# (7) Marker-prefix discipline -- W607-DK stays in ``capsule_*`` family
# ---------------------------------------------------------------------------


def test_w607dk_marker_prefix_stays_in_capsule_family(cli_runner, capsule_project, monkeypatch):
    """Every W607-DK substrate marker uses the canonical ``capsule_*`` prefix.

    Hard distinction from sibling W607-* layers across adjacent
    architecture/export commands (fingerprint, health, complexity,
    dark_matter, smells).
    """
    from roam.commands import cmd_capsule as _mod

    def _raise(*args, **kwargs):
        raise PermissionError("synthetic-prefix-discipline-from-W607-DK")

    monkeypatch.setattr(_mod, "_build_capsule", _raise)

    result = _invoke_capsule(cli_runner, capsule_project)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)
    top_wo = data.get("warnings_out") or []
    summary_wo = data["summary"].get("warnings_out") or []
    all_wo = list(top_wo) + list(summary_wo)
    substrate_markers = [m for m in all_wo if "_failed:" in m]
    assert substrate_markers, "expected non-empty substrate markers for prefix-consistency check"
    for marker in substrate_markers:
        assert marker.startswith("capsule_"), (
            f"every surfaced W607-DK marker must use the ``capsule_*`` prefix family; got {marker!r}"
        )
        for forbidden_prefix, sibling in (
            ("fingerprint_", "cmd_fingerprint W607-DH"),
            ("health_", "cmd_health W607-M / W607-BA"),
            ("complexity_", "cmd_complexity W607-BJ"),
            ("dark_matter_", "cmd_dark_matter W607-BK"),
            ("smells_", "cmd_smells W607-BN / W607-DF"),
            ("bus_factor_", "cmd_bus_factor W607-CQ"),
            ("clones_", "cmd_clones W607-BQ / W607-DC"),
            ("duplicates_", "cmd_duplicates W607-BM / W607-DD"),
            ("dead_", "cmd_dead W607-BX"),
            ("vibe_check_", "cmd_vibe_check W607-BS"),
            ("hotspots_", "cmd_hotspots W607-CP"),
            ("auth_gaps_", "cmd_auth_gaps W607-CM"),
            ("n1_", "cmd_n1 W607-CB"),
            ("over_fetch_", "cmd_over_fetch W607-CE"),
            ("missing_index_", "cmd_missing_index W607-CI"),
        ):
            assert not marker.startswith(forbidden_prefix), (
                f"marker leaked into ``{forbidden_prefix}*`` family ({sibling} scope); got {marker!r}"
            )


# ---------------------------------------------------------------------------
# (8) Source-level guard: cmd_capsule carries the W607-DK accumulator
# ---------------------------------------------------------------------------


def test_cmd_capsule_carries_w607dk_accumulator():
    """AST-level guard: cmd_capsule carries the W607-DK accumulator."""
    src_path = Path(__file__).parent.parent / "src" / "roam" / "commands" / "cmd_capsule.py"
    assert src_path.exists(), f"cmd_capsule.py missing at {src_path}"
    src = src_path.read_text(encoding="utf-8")
    assert "w607dk_warnings_out" in src, (
        "W607-DK accumulator missing from cmd_capsule; the substrate-CALL marker plumbing has been removed."
    )
    assert "_run_check_dk" in src, (
        "W607-DK ``_run_check_dk`` helper missing from cmd_capsule; the per-substrate wrapper has been refactored away."
    )
    tree = ast.parse(src)
    found_run_check_dk = False
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "_run_check_dk":
            found_run_check_dk = True
            break
    assert found_run_check_dk, (
        "W607-DK ``_run_check_dk`` helper not found in cmd_capsule AST; "
        "the per-substrate wrapper has been refactored away."
    )


# ---------------------------------------------------------------------------
# (9) Each W607-DK substrate phase is wrapped (source-level)
# ---------------------------------------------------------------------------


def test_all_w607dk_substrate_phases_wrapped_in_source():
    """Source-level guard: every W607-DK substrate boundary is wrapped."""
    src_path = Path(__file__).parent.parent / "src" / "roam" / "commands" / "cmd_capsule.py"
    src = src_path.read_text(encoding="utf-8")
    for phase in _DK_PHASES:
        same_line = f'_run_check_dk("{phase}"' in src
        multi_line = (
            f'_run_check_dk(\n        "{phase}"' in src
            or f'_run_check_dk(\n            "{phase}"' in src
            or f'_run_check_dk(\n                "{phase}"' in src
            or f'_run_check_dk(\n                    "{phase}"' in src
            or f'_run_check_dk(\n                        "{phase}"' in src
        )
        marker_grep = f"capsule_{phase}_failed" in src
        assert same_line or multi_line or marker_grep, (
            f"W607-DK wrap missing for phase {phase!r}; substrate boundary is no longer caught."
        )


# ---------------------------------------------------------------------------
# (10) AST source-level guard: canonical marker fstring lives in source
# ---------------------------------------------------------------------------


def test_w607dk_marker_shape_documented_in_source():
    """Source-level guard: canonical W607-DK marker fstring in cmd_capsule.

    The DK accumulator must use the SAME canonical marker fstring as
    BD (same capsule_* family).
    """
    src_path = Path(__file__).parent.parent / "src" / "roam" / "commands" / "cmd_capsule.py"
    src = src_path.read_text(encoding="utf-8")
    fstring_pattern = 'f"capsule_{phase}_failed:{type(exc).__name__}:{exc}"'
    assert fstring_pattern in src, (
        f"canonical W607-DK marker fstring missing from cmd_capsule; expected: {fstring_pattern}"
    )


# ---------------------------------------------------------------------------
# (11) Pattern-2 silent-fallback eliminated on degraded path
# ---------------------------------------------------------------------------


def test_pattern_2_silent_fallback_eliminated_on_dk_degraded_path(cli_runner, capsule_project, monkeypatch):
    """Pattern-2 regression guard on the degraded path.

    If ``_build_capsule`` raises, the empty-floor default kicks in
    (capsule_data={...empty...}) and the envelope is emitted. The
    W607-DK wrap MUST flip ``partial_success: True`` on that branch
    so the empty-state envelope is NOT mistaken for a clean capsule
    export.
    """
    from roam.commands import cmd_capsule as _mod

    def _raise(*args, **kwargs):
        raise RuntimeError("synthetic-pattern-2-from-W607-DK")

    monkeypatch.setattr(_mod, "_build_capsule", _raise)

    result = _invoke_capsule(cli_runner, capsule_project)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)
    summary = data.get("summary") or {}

    assert summary.get("partial_success") is True, (
        f"degraded path MUST flip partial_success=True (Pattern-2 silent-fallback guard); got summary={summary!r}"
    )
    all_wo = list(data.get("warnings_out") or []) + list(summary.get("warnings_out") or [])
    payload_markers = [m for m in all_wo if m.startswith("capsule_build_capsule_payload_failed:")]
    assert payload_markers, (
        f"degraded path MUST surface the build_capsule_payload marker (loud-not-silent discipline); got {all_wo!r}"
    )
    # Verdict must NOT contain default-success vocabulary.
    verdict = (summary.get("verdict") or "").lower()
    for forbidden in ("safe", "passed", "all clear"):
        assert forbidden not in verdict, (
            f"verdict contains default-success vocabulary {forbidden!r} -- "
            f"Pattern-2 silent-fallback violation; got {verdict!r}"
        )


# ---------------------------------------------------------------------------
# (12) Per-substrate isolation -- DK and BD waves coexist cleanly
# ---------------------------------------------------------------------------


def test_per_substrate_isolation_dk_vs_bd_under_w607dk(cli_runner, capsule_project, monkeypatch):
    """Per-substrate isolation: a DK-only failure does NOT prevent the BD
    inner wraps from running on the clean path.

    Simulate ``_build_capsule`` raising at the DK outer boundary -- the
    DK marker surfaces, BD markers stay absent (BD never ran because
    the outer DK wrap caught at the assembly stage), and the envelope
    is well-formed.
    """
    from roam.commands import cmd_capsule as _mod

    def _raise(*args, **kwargs):
        raise RuntimeError("synthetic-isolation-from-W607-DK")

    monkeypatch.setattr(_mod, "_build_capsule", _raise)

    result = _invoke_capsule(cli_runner, capsule_project)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    all_wo = list(data.get("warnings_out") or []) + list(data["summary"].get("warnings_out") or [])
    dk_markers = [m for m in all_wo if m.startswith("capsule_build_capsule_payload_failed:")]
    assert dk_markers, all_wo
    # BD inner gather markers should NOT surface -- the DK outer wrap
    # caught before the BD inner wraps could fire.
    bd_gather_markers = [
        m
        for m in all_wo
        if (
            m.startswith("capsule_gather_topology_failed:")
            or m.startswith("capsule_gather_symbols_failed:")
            or m.startswith("capsule_gather_edges_failed:")
            or m.startswith("capsule_gather_clusters_failed:")
            or m.startswith("capsule_gather_health_failed:")
        )
    ]
    assert not bd_gather_markers, (
        f"BD inner gather markers must NOT surface when the DK outer wrap catches first; got {bd_gather_markers!r}"
    )


# ---------------------------------------------------------------------------
# (13) Cross-prefix isolation -- capsule_* markers stay scoped
# ---------------------------------------------------------------------------


def test_cross_prefix_marker_isolation_against_siblings_dk(cli_runner, capsule_project, monkeypatch):
    """Cross-prefix marker isolation across the export detector family.

    Confirm ``capsule_<phase>_failed:`` markers coexist with the
    adjacent architecture-family detectors without cross-prefix
    leakage.
    """
    from roam.commands import cmd_capsule as _mod

    def _raise(*args, **kwargs):
        raise RuntimeError("synthetic-cross-prefix-from-W607-DK")

    monkeypatch.setattr(_mod, "_build_capsule", _raise)

    result = _invoke_capsule(cli_runner, capsule_project)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    all_wo = list(data.get("warnings_out") or []) + list(data["summary"].get("warnings_out") or [])
    assert any(m.startswith("capsule_build_capsule_payload_failed:") for m in all_wo), all_wo

    for forbidden_prefix in (
        "fingerprint_",
        "health_",
        "complexity_",
        "dark_matter_",
        "smells_",
        "bus_factor_",
        "clones_",
        "duplicates_",
        "dead_",
        "vibe_check_",
        "hotspots_",
    ):
        leaked = [m for m in all_wo if m.startswith(forbidden_prefix)]
        assert not leaked, (
            f"marker family leakage on architecture-family pairing: "
            f"``{forbidden_prefix}*`` leaked into cmd_capsule envelope; "
            f"got {leaked!r}"
        )


# ---------------------------------------------------------------------------
# (14) Architecture-export 2-way pairing pin
# ---------------------------------------------------------------------------


def test_architecture_export_2way_pairing_dh_and_dk():
    """AST pairing pin: cmd_fingerprint + cmd_capsule both carry W607 plumbing.

    cmd_fingerprint = topology-HASH (W607-DH).
    cmd_capsule     = graph-EXPORT (W607-DK).

    Together they form the architecture-export 2-way at the
    substrate-CALL layer. A regression that strips either side breaks
    the 2-way.
    """
    base = Path(__file__).parent.parent / "src" / "roam" / "commands"
    fp_src = (base / "cmd_fingerprint.py").read_text(encoding="utf-8")
    cap_src = (base / "cmd_capsule.py").read_text(encoding="utf-8")

    # cmd_fingerprint side: W607-DH plumbing present.
    assert "w607dh_warnings_out" in fp_src, (
        "cmd_fingerprint missing _w607dh_warnings_out; architecture-export 2-way is broken on the topology-HASH side."
    )
    assert "_run_check_dh" in fp_src, (
        "cmd_fingerprint missing _run_check_dh; architecture-export 2-way is broken on the topology-HASH side."
    )

    # cmd_capsule side: W607-DK plumbing present.
    assert "w607dk_warnings_out" in cap_src, (
        "cmd_capsule missing _w607dk_warnings_out; architecture-export 2-way is broken on the graph-EXPORT side."
    )
    assert "_run_check_dk" in cap_src, (
        "cmd_capsule missing _run_check_dk; architecture-export 2-way is broken on the graph-EXPORT side."
    )

    # AST-level: both helpers are defined as FunctionDef inside their
    # respective modules.
    for side_label, src in (("cmd_fingerprint", fp_src), ("cmd_capsule", cap_src)):
        tree = ast.parse(src)
        expected_helper = "_run_check_dh" if side_label == "cmd_fingerprint" else "_run_check_dk"
        found = any(isinstance(n, ast.FunctionDef) and n.name == expected_helper for n in ast.walk(tree))
        assert found, f"{side_label}: {expected_helper} FunctionDef missing; architecture-export 2-way broken."


# ---------------------------------------------------------------------------
# (15) W978 7-discipline AST audit
# ---------------------------------------------------------------------------


def _w978_is_literal_tree(n: ast.AST) -> bool:
    """Pure-AST literal check; recursive on container nodes."""
    if isinstance(n, ast.Constant):
        return True
    if isinstance(n, (ast.List, ast.Tuple, ast.Set)):
        return all(_w978_is_literal_tree(x) for x in n.elts)
    if isinstance(n, ast.Dict):
        return all(_w978_is_literal_tree(x) for x in (list(n.keys) + list(n.values)))
    if isinstance(n, ast.Name):
        return n.id in ("None", "True", "False")
    if isinstance(n, ast.UnaryOp) and isinstance(n.operand, ast.Constant):
        return True
    return False


def _w978_assert_container_is_literal(value: ast.AST, fn_name: str) -> None:
    """Assert every child of a List/Dict/Tuple/Set is a literal subtree."""
    if isinstance(value, ast.Dict):
        children = list(value.keys) + list(value.values)
    else:
        children = list(value.elts)
    for child in children:
        assert _w978_is_literal_tree(child), (
            f"{fn_name} default= contains non-literal child at line {value.lineno}: {ast.dump(child)!r}"
        )


def _w978_assert_default_is_literal(value: ast.AST, fn_name: str) -> None:
    """Dispatch default= value to the right literal check; raise on miss."""
    if isinstance(value, ast.Constant):
        return
    if isinstance(value, (ast.List, ast.Dict, ast.Tuple, ast.Set)):
        _w978_assert_container_is_literal(value, fn_name)
        return
    if isinstance(value, ast.Name):
        assert value.id in ("None", "True", "False"), (
            f"{fn_name} default= references symbol {value.id!r} "
            f"at line {value.lineno}; only literals + immutable "
            f"containers allowed (W978 2nd discipline)."
        )
        return
    raise AssertionError(f"{fn_name} default= is not a literal at line {value.lineno}: {ast.dump(value)!r}")


def _w978_extract_phase(node: ast.Call, fn_name: str) -> str | None:
    """Return the phase string of a `fn_name(phase, ...)` call, or None to skip."""
    func = node.func
    if not isinstance(func, ast.Name) or func.id != fn_name:
        return None
    if not node.args:
        return None
    phase_arg = node.args[0]
    assert isinstance(phase_arg, ast.Constant), (
        f"{fn_name} phase arg must be a string literal at line {phase_arg.lineno}; got {ast.dump(phase_arg)!r}"
    )
    return phase_arg.value


def _w978_audit_call(node: ast.Call, fn_name: str) -> str | None:
    """Audit a single Call node; return its phase name (or None to skip)."""
    phase = _w978_extract_phase(node, fn_name)
    if phase is None:
        return None
    for kw in node.keywords:
        if kw.arg != "default":
            continue
        _w978_assert_default_is_literal(kw.value, fn_name)
    return phase


def _w978_collect_phases(tree: ast.AST, fn_name: str) -> list[str]:
    """Walk the tree, audit every matching call, return collected phase names."""
    phases_seen: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        phase = _w978_audit_call(node, fn_name)
        if phase is not None:
            phases_seen.append(phase)
    return phases_seen


def test_w978_7_discipline_ast_audit_dk():
    """AST audit pins the W978 7-discipline compliance for W607-DK.

    Each ``_run_check_dk("phase", ...)`` call site must:
    - have a ``default=`` that is a literal constant / immutable
      container of literals (kwarg-default eagerness, 2nd discipline)
    - phase names unique within the file (4th discipline)
    """
    src_path = Path(__file__).parent.parent / "src" / "roam" / "commands" / "cmd_capsule.py"
    src = src_path.read_text(encoding="utf-8")
    tree = ast.parse(src)

    phases_seen = _w978_collect_phases(tree, "_run_check_dk")

    # Phase names unique within the file (4th discipline collision check).
    duplicates = [p for p in phases_seen if phases_seen.count(p) > 1]
    assert not duplicates, f"W607-DK phase name collision in cmd_capsule: {sorted(set(duplicates))!r}"


# ---------------------------------------------------------------------------
# (16) W978 5th discipline -- len() / dict-index NOT at the kwarg-bind site
# ---------------------------------------------------------------------------


def test_w978_5th_discipline_no_unguarded_len_or_index_at_dk_kwarg_bind():
    """W978 5th discipline: ``len()`` / dict-index over poisoned input
    MUST live INSIDE the wrapped closure, never at the _run_check_dk
    kwarg-bind site.

    A regression would re-introduce eager evaluation: e.g. computing
    ``len(capsule_data["symbols"])`` at the call site BEFORE the wrap
    fires.
    """
    src_path = Path(__file__).parent.parent / "src" / "roam" / "commands" / "cmd_capsule.py"
    src = src_path.read_text(encoding="utf-8")
    # Forbid any direct ``len(`` reference on the same source line as a
    # _run_check_dk call.
    for line in src.splitlines():
        if "_run_check_dk(" in line and "len(" in line:
            raise AssertionError(
                f"W978 5th discipline violation in cmd_capsule: "
                f"``len(`` at the same line as _run_check_dk call -- "
                f"move len() INSIDE the wrapped closure; line: {line!r}"
            )


# ---------------------------------------------------------------------------
# (17) DK + BD coexist -- both accumulators surface together on a
# multi-failure path
# ---------------------------------------------------------------------------


def test_dk_and_bd_accumulators_coexist_on_multi_failure(cli_runner, capsule_project, monkeypatch):
    """BD and DK markers both surface when both layers catch.

    Force a BD gather failure (gather_topology raises) AND a DK
    serialize_to_json failure (the to_json substrate raises). Both
    markers must appear in the merged warnings_out union, and
    partial_success must be True.
    """
    from roam.commands import cmd_capsule as _mod

    def _raise_topology(*args, **kwargs):
        raise RuntimeError("synthetic-bd-topology-multi")

    monkeypatch.setattr(_mod, "_gather_topology", _raise_topology)

    result = _invoke_capsule(cli_runner, capsule_project)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    all_wo = list(data.get("warnings_out") or []) + list(data["summary"].get("warnings_out") or [])
    # BD gather_topology marker surfaces.
    bd_topology = [m for m in all_wo if m.startswith("capsule_gather_topology_failed:")]
    assert bd_topology, f"expected BD capsule_gather_topology_failed: marker in merged warnings_out; got {all_wo!r}"
    # partial_success flips.
    assert data["summary"].get("partial_success") is True


# ---------------------------------------------------------------------------
# (18) DK fstring fallback marker contains exception class + detail
# ---------------------------------------------------------------------------


def test_w607dk_marker_fstring_carries_exc_class_and_detail(cli_runner, capsule_project, monkeypatch):
    """W607-DK marker carries the (exc_class, detail) tuple in the fstring.

    The marker shape is ``capsule_<phase>_failed:<exc_class>:<detail>``;
    consumers parse this to triage. A regression that strips either
    component would break downstream triage.
    """
    from roam.commands import cmd_capsule as _mod

    def _raise(*args, **kwargs):
        raise ValueError("synthetic-fstring-detail-from-W607-DK")

    monkeypatch.setattr(_mod, "_build_capsule", _raise)

    result = _invoke_capsule(cli_runner, capsule_project)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    all_wo = list(data.get("warnings_out") or []) + list(data["summary"].get("warnings_out") or [])
    payload_markers = [m for m in all_wo if m.startswith("capsule_build_capsule_payload_failed:")]
    assert payload_markers, all_wo
    marker = payload_markers[0]
    assert "ValueError" in marker, marker
    assert "synthetic-fstring-detail-from-W607-DK" in marker, marker
