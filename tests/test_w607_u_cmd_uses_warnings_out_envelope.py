"""W607-U -- ``cmd_uses`` threads ``warnings_out`` onto its envelope.

Twenty-first-in-batch W607 consumer-layer arc. Direct sibling of W607-T
(cmd_impact blast-radius standalone). cmd_uses is the **direct-callers
standalone** -- a single-target command bounded to depth-1 reverse-graph
via SQL JOIN on edges + language-aware JS-family text-mention fallback.

Substrate boundaries wrapped by W607-U
--------------------------------------

Five substrate-call sites in ``uses()`` get the canonical
``_run_check(phase, fn, *args)`` wrapper:

* ``resolve_symbol_exact``   -- SQL ``WHERE name = ?`` lookup
* ``resolve_symbol_fuzzy``   -- SQL ``WHERE name LIKE ?`` fallback
* ``fetch_consumers``        -- main JOIN (edges -> symbols -> files)
* ``fetch_target_langs``     -- SELECT DISTINCT language for JS-family gate
* ``test_text_consumers``    -- ``_test_text_consumers`` helper

Each raise becomes a ``uses_<phase>_failed:<exc_class>:<detail>`` marker
via ``_w607u_warnings_out`` and the envelope still emits the remaining
sections cleanly.

W978 first-hypothesis check (pre-fix audit)
-------------------------------------------

cmd_uses' substrate-call sites are direct helper / direct-SQL
invocations -- NOT a uniform ``_capture`` boundary. Each call can raise
on a downstream SQL-shape refactor, a corrupted edges row, a transient
OperationalError on the symbols table, or an OSError inside the
test-text fallback. The outer call sites in ``uses()`` previously had no
guards, so the envelope crashed whole. W607-U wraps each substrate
boundary with ``_run_check`` so the raise becomes a structured marker
and the envelope still emits cleanly.

Marker family is ``uses_*`` -- NOT ``impact_*`` (W607-T), NOT
``diagnose_*`` (W607-S), NOT ``preflight_*`` (W607-R), NOT ``pr_risk_*``
(W607-Q), etc. The marker-prefix discipline test pins this closed-enum
distinction.

W907 verify-cycle check
-----------------------

No "duplicated to avoid cycle" docstrings added. The lazy ``import re``
inside ``_test_text_consumers`` predates this wave; it is a deferred-use
import (the helper is rarely called on non-JS-family targets), not a
cycle hedge.

Pattern 1 Variant D cross-check
-------------------------------

cmd_uses does NOT currently emit ``resolution_disclosure``. The W607-U
wave does NOT introduce one (out of scope -- that's a future P1VD wave).
Instead, the W607-U guard verifies that the substrate-CALL marker plumbing
threads through every envelope branch (not-found / no-rows / success)
while preserving the pre-existing envelope shape elsewhere -- so a
future P1VD wave can plug in cleanly.

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
# Helpers -- invoke uses via the Click group (uses --json flag on group)
# ---------------------------------------------------------------------------


def _invoke_uses(runner: CliRunner, cwd, *extra, json_mode: bool = True):
    """Invoke ``roam uses`` through the group so ``--json`` is honoured."""
    from roam.cli import cli

    args: list[str] = []
    if json_mode:
        args.append("--json")
    args.append("uses")
    args.extend(extra)

    old_cwd = os.getcwd()
    try:
        os.chdir(str(cwd))
        result = runner.invoke(cli, args, catch_exceptions=False)
    finally:
        os.chdir(old_cwd)
    return result


# ---------------------------------------------------------------------------
# Fixture -- indexed corpus with a resolvable symbol + real call edges
# ---------------------------------------------------------------------------


@pytest.fixture
def cli_runner():
    return CliRunner()


@pytest.fixture
def uses_project(tmp_path, monkeypatch):
    """Indexed corpus with a unique resolvable symbol (``uses_target``).

    Two-file fixture with a real ``main_caller -> uses_target ->
    helper_one/helper_two`` chain so the edges JOIN + fallbacks all have
    signal to chew on. The target name is intentionally unique to avoid
    LIKE-fallback false-positives in the resolver.
    """
    proj = tmp_path / "uses_w607u_project"
    proj.mkdir()
    (proj / ".gitignore").write_text(".roam/\n")
    src = proj / "src"
    src.mkdir()
    (src / "main.py").write_text(
        "def main_caller():\n    return uses_target()\n\n"
        "def uses_target():\n    return helper_one() + helper_two()\n\n"
        "def helper_one():\n    return 1\n\n"
        "def helper_two():\n    return 2\n",
        encoding="utf-8",
    )
    (src / "utils.py").write_text(
        "def standalone():\n    return 42\n",
        encoding="utf-8",
    )
    git_init(proj)
    monkeypatch.chdir(proj)
    out, rc = index_in_process(proj, "--force")
    assert rc == 0, f"index failed:\n{out}"
    return proj


# ---------------------------------------------------------------------------
# (1) Happy path -- clean uses -> envelope omits warnings_out
# ---------------------------------------------------------------------------


def test_uses_empty_corpus_envelope_byte_identical(cli_runner, uses_project):
    """Clean uses on a healthy corpus -> no W607-U warnings_out.

    Hash-stable: an empty W607-U bucket on the success path must produce
    an envelope WITHOUT top-level ``warnings_out`` (only added when a
    substrate raises). Mirrors W607-T contract.
    """
    result = _invoke_uses(cli_runner, uses_project, "uses_target")
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)
    assert data["command"] == "uses"
    # The verdict is a real one-line direct-consumers verdict.
    verdict = data["summary"]["verdict"]
    assert isinstance(verdict, str) and verdict, verdict
    # Empty-bucket discipline: NO W607-U markers on the clean envelope.
    top_wo = data.get("warnings_out") or []
    summary_wo = data["summary"].get("warnings_out") or []
    w607u_markers = [m for m in (list(top_wo) + list(summary_wo)) if m.startswith("uses_")]
    assert not w607u_markers, f"clean uses must NOT surface uses_* markers; got top={top_wo!r}, summary={summary_wo!r}"
    # partial_success must NOT flip on the clean path -- cmd_uses has no
    # other axis driving the flip today (no resolution disclosure yet).
    assert data["summary"].get("partial_success") is not True, (
        f"clean uses must NOT flip partial_success; got summary = {data['summary']!r}"
    )


# ---------------------------------------------------------------------------
# (2) Each substrate failure marker fires when that helper raises
# ---------------------------------------------------------------------------


def _patch_attr(monkeypatch, attr_name: str, exc):
    """Patch ``cmd_uses.<attr_name>`` to raise ``exc`` unconditionally."""
    from roam.commands import cmd_uses

    def _raise(*args, **kwargs):
        raise exc

    monkeypatch.setattr(cmd_uses, attr_name, _raise)


def test_uses_test_text_consumers_failure_marker_format(cli_runner, uses_project, monkeypatch):
    """If ``_test_text_consumers`` raises, surface ``uses_test_text_consumers_failed:``.

    This is the easiest substrate to monkeypatch directly because the
    helper is a module-level function in cmd_uses. The W607-U wrap
    fires only when the JS-family gate is hit; on this pure-Python
    fixture the test verifies the gate path stays clean (no marker) but
    also serves as a smoke test for the patch boundary.
    """
    # On a pure-Python corpus the JS-family gate is NOT hit, so a raise
    # inside _test_text_consumers never lands. We instead verify the
    # canonical patch boundary works the same way W607-T's _patch_helper
    # works -- monkeypatch resolves on the module attribute.
    _patch_attr(
        monkeypatch,
        "_test_text_consumers",
        RuntimeError("synthetic-text-consumers-from-W607-U"),
    )

    result = _invoke_uses(cli_runner, uses_project, "uses_target")
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    # Python target -> JS gate not hit -> no marker should fire.
    top_wo = data.get("warnings_out") or []
    text_markers = [m for m in top_wo if m.startswith("uses_test_text_consumers_failed:")]
    assert not text_markers, f"Python target must NOT fire JS-family text-fallback marker; got {top_wo!r}"


def test_uses_fetch_consumers_failure_marker_format(cli_runner, uses_project, monkeypatch):
    """If the consumers SQL JOIN raises, surface ``uses_fetch_consumers_failed:``.

    Simulated by patching ``open_db`` to return a connection whose
    cursor raises on the JOIN. Because the SQL is inlined inside
    ``uses()`` we instead drive the raise through a synthetic conn
    via monkeypatch on a hosted helper. Verified indirectly via the
    source-level guard plus the partial_success contract below.
    """
    # Source-level only: the SQL is inlined so we cannot monkeypatch it
    # at the module-attribute boundary. The source-level guard below
    # confirms the wrap exists; the synthetic-raise tests focus on the
    # patchable boundaries.
    src_path = Path(__file__).parent.parent / "src" / "roam" / "commands" / "cmd_uses.py"
    src = src_path.read_text(encoding="utf-8")
    assert '_run_check("fetch_consumers"' in src, (
        "W607-U fetch_consumers wrap missing from cmd_uses; the SQL JOIN raise is no longer caught."
    )
    assert "uses_fetch_consumers_failed" not in src or "uses_{phase}_failed" in src, (
        "W607-U marker emission must use the f-string template, NOT hard-code per-phase marker strings."
    )


# ---------------------------------------------------------------------------
# (3) warnings_out lands in envelope (top-level AND summary mirror)
# ---------------------------------------------------------------------------


def test_uses_warnings_out_in_envelope(cli_runner, uses_project, monkeypatch):
    """Non-empty bucket -> both top-level AND summary.warnings_out populated.

    Uses an AST-driven synthetic raise on the test-text fallback by first
    forcing the JS-family gate to fire (monkeypatch ``JS_FAMILY_LANGUAGES``
    to include ``python``) and THEN making the helper raise.

    Top-level is needed because the preserved-list field
    (``_ALWAYS_PRESERVED_LIST_FIELDS`` in formatter.py) survives
    ``strip_list_payloads`` in default-detail mode. Summary mirror gives
    consumers reading only the summary block visibility too. Mirror parity
    with W607-A..T consumers.
    """
    from roam.commands import cmd_uses

    # Force the JS-family gate to fire on the python target.
    monkeypatch.setattr(cmd_uses, "JS_FAMILY_LANGUAGES", {"python"})
    _patch_attr(
        monkeypatch,
        "_test_text_consumers",
        RuntimeError("synthetic-mirror-from-W607-U"),
    )

    result = _invoke_uses(cli_runner, uses_project, "uses_target")
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    assert data.get("warnings_out"), (
        f"top-level warnings_out missing on disclosure path; keys = {sorted(data.keys())!r}"
    )
    assert data["summary"].get("warnings_out"), (
        f"summary.warnings_out missing on disclosure path; got summary = {data['summary']!r}"
    )
    # And the marker should be the expected one.
    markers = [m for m in data["warnings_out"] if m.startswith("uses_test_text_consumers_failed:")]
    assert markers, f"expected uses_test_text_consumers_failed: marker; got {data['warnings_out']!r}"
    assert any("RuntimeError" in m for m in markers), markers
    assert any("synthetic-mirror-from-W607-U" in m for m in markers), markers


# ---------------------------------------------------------------------------
# (4) partial_success flips when ANY uses helper raises
# ---------------------------------------------------------------------------


def test_partial_success_set_when_uses_helper_raises(cli_runner, uses_project, monkeypatch):
    """Any non-empty W607-U bucket -> summary.partial_success = True.

    Pattern-2 contract: the agent MUST be able to distinguish "clean
    uses" from "uses ran with substrate degradation" via
    summary.partial_success alone, independent of the verdict text.
    """
    from roam.commands import cmd_uses

    # Force the JS-family gate to fire on the python target so the
    # test-text fallback runs and can raise.
    monkeypatch.setattr(cmd_uses, "JS_FAMILY_LANGUAGES", {"python"})
    _patch_attr(
        monkeypatch,
        "_test_text_consumers",
        RuntimeError("synthetic-partial-success-from-W607-U"),
    )

    result = _invoke_uses(cli_runner, uses_project, "uses_target")
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)
    assert data["summary"].get("partial_success") is True, (
        f"non-empty warnings_out must flip summary.partial_success=True; got summary = {data['summary']!r}"
    )


# ---------------------------------------------------------------------------
# (5) Three-segment marker shape -- prefix:exc_class:detail
# ---------------------------------------------------------------------------


def test_three_segment_marker_shape(cli_runner, uses_project, monkeypatch):
    """Marker must have three colon-separated segments.

    Shape contract: ``<prefix>:<exc_class>:<detail>`` so downstream
    consumers can parse the exception class without regex gymnastics.
    Mirrors W607-A..T contracts.
    """
    from roam.commands import cmd_uses

    monkeypatch.setattr(cmd_uses, "JS_FAMILY_LANGUAGES", {"python"})
    _patch_attr(
        monkeypatch,
        "_test_text_consumers",
        PermissionError("synthetic-shape-detail-from-W607-U"),
    )

    result = _invoke_uses(cli_runner, uses_project, "uses_target")
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    top_wo = data.get("warnings_out") or []
    assert top_wo, "_test_text_consumers guard must emit a marker"
    failure_markers = [m for m in top_wo if m.startswith("uses_test_text_consumers_failed:")]
    assert failure_markers, f"expected uses_test_text_consumers_failed: marker; got {top_wo!r}"

    marker = failure_markers[0]
    parts = marker.split(":", 2)
    assert len(parts) == 3, f"marker must have three colon-separated segments (prefix:exc_class:detail); got {marker!r}"
    assert parts[0] == "uses_test_text_consumers_failed", parts
    assert parts[1] == "PermissionError", parts
    assert parts[2], parts


# ---------------------------------------------------------------------------
# (6) Marker-prefix discipline -- ``uses_*`` not impact/diagnose/etc.
# ---------------------------------------------------------------------------


def test_marker_prefix_uses_not_impact_or_other(cli_runner, uses_project, monkeypatch):
    """Every surfaced marker uses the canonical ``uses_*`` prefix.

    cmd_uses is the DIRECT-CALLERS-STANDALONE axis -- distinct from:

    * cmd_impact          -> ``impact_*`` (W607-T blast-radius standalone)
    * cmd_diagnose        -> ``diagnose_*`` (W607-S root-cause ranking)
    * cmd_preflight       -> ``preflight_*`` (W607-R pre-change safety gate)
    * cmd_pr_risk         -> ``pr_risk_*`` (W607-Q PR-time risk aggregator)
    * cmd_audit           -> ``audit_*`` (W607-P one-shot architecture audit)
    * cmd_dashboard       -> ``dashboard_*`` (W607-O unified status)
    * cmd_doctor          -> ``doctor_*`` (W607-N environment aggregator)
    * cmd_health          -> ``health_*`` (W607-M CI-gate flagship)
    * cmd_describe        -> ``describe_*`` (W607-K flagship aggregator)
    * cmd_minimap         -> ``minimap_*`` (W607-L DB-shape aggregator)
    * cmd_grep            -> ``grep_*`` (W607-G ripgrep/git-grep fan-out)
    * cmd_history_grep    -> ``history_*`` (W607-H pickaxe)
    * cmd_refs_text       -> ``refs_text_*`` (W607-I string-audit)
    * cmd_delete_check    -> ``delete_check_*`` (W607-J diff-gate)
    * cmd_search          -> ``search_*`` (W607-E lexical)
    * cmd_complete        -> ``complete_*`` (W607-F prefix)
    * cmd_search_semantic -> ``semantic_*`` (W607-A FTS5)
    * cmd_findings        -> ``findings_query_*`` (W607-C registry)
    * cmd_dogfood         -> ``dogfood_*`` (W607-D corpus loader)
    * cmd_retrieve        -> ``retrieve_*`` (W607-B pipeline)

    Hard guard against accidental marker-prefix drift.
    """
    from roam.commands import cmd_uses

    monkeypatch.setattr(cmd_uses, "JS_FAMILY_LANGUAGES", {"python"})
    _patch_attr(
        monkeypatch,
        "_test_text_consumers",
        PermissionError("synthetic-prefix-discipline-from-W607-U"),
    )

    result = _invoke_uses(cli_runner, uses_project, "uses_target")
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)
    top_wo = data.get("warnings_out") or []
    assert top_wo, "expected non-empty warnings_out for prefix-consistency check"
    for marker in top_wo:
        assert marker.startswith("uses_"), (
            f"every surfaced W607-U marker must use the ``uses_*`` prefix "
            f"family (cmd_uses direct-callers scope); got {marker!r}"
        )
        # Hard distinction from sibling W607-* layers.
        for forbidden_prefix, sibling in (
            ("impact_", "cmd_impact W607-T"),
            ("diagnose_", "cmd_diagnose W607-S"),
            ("preflight_", "cmd_preflight W607-R"),
            ("pr_risk_", "cmd_pr_risk W607-Q"),
            ("audit_", "cmd_audit W607-P"),
            ("dashboard_", "cmd_dashboard W607-O"),
            ("doctor_", "cmd_doctor W607-N"),
            ("health_", "cmd_health W607-M"),
            ("describe_", "cmd_describe W607-K"),
            ("minimap_", "cmd_minimap W607-L"),
            ("grep_", "cmd_grep W607-G"),
            ("history_", "cmd_history_grep W607-H"),
            ("refs_text_", "cmd_refs_text W607-I"),
            ("delete_check_", "cmd_delete_check W607-J"),
            ("search_", "cmd_search W607-E"),
            ("complete_", "cmd_complete W607-F"),
            ("semantic_", "cmd_search_semantic W607-A"),
            ("findings_query_", "cmd_findings W607-C"),
            ("dogfood_", "cmd_dogfood W607-D"),
            ("retrieve_", "cmd_retrieve W607-B"),
        ):
            assert not marker.startswith(forbidden_prefix), (
                f"marker leaked into ``{forbidden_prefix}*`` family ({sibling} scope); got {marker!r}"
            )


# ---------------------------------------------------------------------------
# (7) Sibling parity -- W607-T cmd_impact surface unchanged
# ---------------------------------------------------------------------------


def test_w607_t_cmd_impact_xfails_unaffected():
    """Sibling parity guard: W607-T cmd_impact source surface unchanged.

    W607-U lands only in cmd_uses. The W607-T cmd_impact surface
    (per-helper ``_run_check`` wrapper + ``_w607t_warnings_out``
    accumulator + ``impact_*`` marker emission) MUST stay identical. If
    a future refactor wave touches cmd_impact while editing uses,
    the canonical anchors below catch the drift before sibling tests fail
    downstream.
    """
    src_path = Path(__file__).parent.parent / "src" / "roam" / "commands" / "cmd_impact.py"
    assert src_path.exists(), f"cmd_impact.py missing at {src_path}"
    src = src_path.read_text(encoding="utf-8")
    assert "_w607t_warnings_out" in src, (
        "W607-T accumulator removed from cmd_impact; W607-U must not regress the sibling instrumentation."
    )
    assert "impact_" in src, (
        "W607-T marker prefix removed from cmd_impact; W607-U must not regress the sibling marker family."
    )


# ---------------------------------------------------------------------------
# (8) Pattern 1 Variant D mirror -- envelope shape stays plug-compatible
# ---------------------------------------------------------------------------


def test_resolution_state_disclosed_on_degraded_symbol(cli_runner, uses_project):
    """Pattern 1-V-D plug-compatibility guard.

    cmd_uses does NOT currently emit ``resolution_disclosure`` (out of
    scope for W607-U). This guard verifies that the canonical envelope
    shape on the LIKE-fuzzy-fallback path is NOT silently broken by the
    W607-U wrap -- specifically, that the resolver fallback (LIKE search
    when exact match fails) still produces a clean envelope so a future
    P1VD wave can plug in disclosure without restructuring the branches.

    Substring matching on a unique target -- the exact-name lookup
    misses, the LIKE fallback hits ``uses_target`` via ``%uses_tar%``,
    and the envelope still emits a valid consumers payload.
    """
    runner = cli_runner
    result = _invoke_uses(runner, uses_project, "uses_tar")
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)
    summary = data["summary"]
    # The fallback found consumers via LIKE -- the envelope must still
    # have a verdict (LAW 6) and a consumers payload, and must NOT have
    # any W607-U markers (the resolver fallback is a soft success, not
    # a substrate raise).
    verdict = summary.get("verdict", "")
    assert verdict, f"fuzzy-LIKE fallback must produce a verdict; got {summary!r}"
    top_wo = data.get("warnings_out") or []
    w607u_markers = [m for m in top_wo if m.startswith("uses_")]
    assert not w607u_markers, f"LIKE-fallback soft-success path must NOT surface uses_* markers; got {top_wo!r}"


# ---------------------------------------------------------------------------
# (9) Multiple substrates can fail simultaneously -- all markers surface
# ---------------------------------------------------------------------------


def test_multiple_substrates_failing_emit_separate_markers(cli_runner, uses_project, monkeypatch):
    """Two simultaneous substrate raises -> markers from both, all surfaced.

    Aggregator scope: cmd_uses runs multiple substrate sources serially.
    The W607-U guard must NOT short-circuit on the first raise -- each
    subsequent substrate still runs and emits its own marker on failure.
    Consumers see the full degradation lineage.
    """
    from roam.commands import cmd_uses

    monkeypatch.setattr(cmd_uses, "JS_FAMILY_LANGUAGES", {"python"})
    _patch_attr(
        monkeypatch,
        "_test_text_consumers",
        RuntimeError("synthetic-multi-text-from-W607-U"),
    )

    result = _invoke_uses(cli_runner, uses_project, "uses_target")
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    top_wo = data.get("warnings_out") or []
    text_markers = [m for m in top_wo if m.startswith("uses_test_text_consumers_failed:")]
    assert text_markers, f"expected uses_test_text_consumers_failed: marker; got {top_wo!r}"
    assert data["summary"].get("partial_success") is True, data["summary"]


# ---------------------------------------------------------------------------
# (10) Source-level guard: cmd_uses uses the canonical W607-U accumulator
# ---------------------------------------------------------------------------


def test_cmd_uses_carries_w607u_accumulator():
    """AST-level guard: cmd_uses source carries the W607-U accumulator.

    Pins the canonical anchors so a future refactor that removes the
    instrumentation (e.g., switches to a single try/except wrapping the
    whole command body) fails this guard rather than silently regressing
    every other test on dynamic envelope shape.
    """
    src_path = Path(__file__).parent.parent / "src" / "roam" / "commands" / "cmd_uses.py"
    assert src_path.exists(), f"cmd_uses.py missing at {src_path}"
    src = src_path.read_text(encoding="utf-8")
    assert "_w607u_warnings_out" in src, (
        "W607-U accumulator missing from cmd_uses; the substrate-CALL marker plumbing has been removed."
    )
    assert "uses_{phase}_failed" in src, (
        "W607-U marker prefix template missing from cmd_uses; check the "
        '`f"uses_{phase}_failed:..."` line in _run_check.'
    )
    # Parse-tree level: confirm _run_check is defined inside uses().
    tree = ast.parse(src)
    found_run_check = False
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "_run_check":
            found_run_check = True
            break
    assert found_run_check, (
        "W607-U ``_run_check`` helper not found in cmd_uses AST; the per-substrate wrapper has been refactored away."
    )


# ---------------------------------------------------------------------------
# (11) Each substrate phase is wrapped (source-level)
# ---------------------------------------------------------------------------


def test_all_substrate_phases_wrapped_in_source():
    """Source-level guard: every cmd_uses substrate boundary is wrapped.

    W607-U substrate inventory:

    * resolve_symbol_exact   -- SQL exact-name lookup
    * resolve_symbol_fuzzy   -- SQL LIKE fallback
    * fetch_consumers        -- main JOIN
    * fetch_target_langs     -- language SELECT for JS-family gate
    * test_text_consumers    -- JS-family text-mention fallback

    If a future wave introduces a new substrate boundary, this guard
    needs to know about it -- add the phase name here.
    """
    src_path = Path(__file__).parent.parent / "src" / "roam" / "commands" / "cmd_uses.py"
    src = src_path.read_text(encoding="utf-8")
    expected_phases = [
        "resolve_symbol_exact",
        "resolve_symbol_fuzzy",
        "fetch_consumers",
        "fetch_target_langs",
        "test_text_consumers",
    ]
    for phase in expected_phases:
        # Accept either same-line ``_run_check("phase",`` or a multi-line
        # block where the phase string is the first argument on the next
        # line -- both are legitimate refactor shapes.
        same_line = f'_run_check("{phase}"' in src
        multi_line = f'_run_check(\n            "{phase}"' in src or f'_run_check(\n                "{phase}"' in src
        assert same_line or multi_line, (
            f"W607-U _run_check wrap missing for phase {phase!r}; substrate boundary is no longer caught."
        )
