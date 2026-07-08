"""W607-DJ -- score_classify extension on the W607-CY aggregation plumbing.

W607-CY shipped 3 aggregation phases on ``cmd_fan`` (compute_predicate /
compute_verdict / serialize_envelope). The canonical 4-phase template
(cmd_dark_matter W607-CZ) additionally wraps a ``score_classify`` phase
that buckets the run output into a state label.

W607-DJ extends the EXISTING W607-CY accumulator (``_w607cy_warnings_out``)
and EXISTING helper (``_run_check_cy``) with the 4th phase. The
source-level prefix stays ``W607-CY`` on the cmd_fan.py side; the
``W607-DJ`` label is reserved for the test-file naming axis so the wave
is auditable in isolation.

Closes the symbol-relations FIVE-WAY at the agg-layer:
  - cmd_uses     -> W607-U  substrate + W607-DE agg
  - cmd_relate   -> W607-W  substrate + W607-DA agg
  - cmd_deps     -> W607-V  substrate + W607-DB agg
  - cmd_describe -> W607-K  substrate + W607-DG agg
  - cmd_fan      -> W607-X  substrate + W607-CY agg + W607-DJ extension

Phase semantics for cmd_fan ``score_classify``
----------------------------------------------

Buckets the per-item flag distribution (HIGH-RISK / hub / spreader /
local-hub / local-spreader / empty) into a state label:

  - HIGH_RISK_DETECTED            -- any HIGH-RISK row
  - HUBS_AND_SPREADERS_DETECTED   -- both hubs and spreaders present
  - HUBS_DETECTED                 -- hubs only
  - SPREADERS_DETECTED            -- spreaders only
  - LOCAL_ONLY                    -- local-hub / local-spreader only
                                     (symbol mode -- file mode collapses
                                     this branch into BALANCED)
  - BALANCED                      -- all empty flags
  - DEGRADED                      -- floor on raise inside the wrap

Floor shape mirrors cmd_dark_matter W607-CZ's ``{"state": ..., "scanned":
..., ...}`` contract.

W978 7-discipline pre-fix audit
-------------------------------

1. f-string verdict floor -- the ``compute_verdict`` floor is still the
   LITERAL ``"fan analysis completed"`` (W607-CY contract); the new
   ``score_classify`` floor is a LITERAL dict ``{"state": "DEGRADED",
   "scanned": <int>}``.
2. kwarg-default eagerness -- the ``score_classify`` ``default=`` arg is
   an ast.Dict of ast.Constant values (plus the inexpensive
   ``len(items)`` capture done BEFORE the wrap).
3. json.dumps(default=str) sentinel -- not used; markers carry
   ``str(exc)`` directly.
4. Phase-name collision -- ``score_classify`` does NOT collide with any
   W607-X substrate-CALL phase name OR with the existing 3 W607-CY
   aggregation phases.
5. len() at kwarg-bind -- ``len(symbol_items)`` / ``len(file_items)`` is
   captured into an int BEFORE being passed through ``_run_check_cy``;
   no poisoned-object ``len()`` at the wrap call-site.
6. Unguarded len()/if x on poisoned object -- score_classify floor
   dict carries concrete int + str defaults; downstream readers do
   bare ``["state"]`` lookups, never ``len()`` on a sentinel.
7. dict.get(key, expensive_default) eager-eval -- the summary uses
   ``_score_dict["state"]`` directly (floor dict guarantees the key),
   NOT ``dict.get("state", expensive_default)``.

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
# Helpers + fixture (mirror of W607-CY test)
# ---------------------------------------------------------------------------


def _invoke_fan(runner: CliRunner, cwd, *extra, json_mode: bool = True):
    from roam.cli import cli

    args: list[str] = []
    if json_mode:
        args.append("--json")
    args.append("fan")
    args.extend(extra)

    old_cwd = os.getcwd()
    try:
        os.chdir(str(cwd))
        result = runner.invoke(cli, args, catch_exceptions=False)
    finally:
        os.chdir(old_cwd)
    return result


@pytest.fixture
def cli_runner():
    return CliRunner()


@pytest.fixture
def fan_project(tmp_path, monkeypatch):
    """Indexed corpus with cross-file edges for fan analysis."""
    proj = tmp_path / "fan_w607dj_project"
    proj.mkdir()
    (proj / ".gitignore").write_text(".roam/\n")
    src = proj / "src"
    src.mkdir()
    (src / "__init__.py").write_text("", encoding="utf-8")
    (src / "core.py").write_text(
        "def shared_helper():\n    return 1\n\ndef secondary_helper():\n    return shared_helper()\n",
        encoding="utf-8",
    )
    (src / "consumer_a.py").write_text(
        "from src.core import shared_helper, secondary_helper\n\n"
        "def use_a():\n"
        "    shared_helper()\n"
        "    return secondary_helper()\n",
        encoding="utf-8",
    )
    (src / "consumer_b.py").write_text(
        "from src.core import shared_helper\n\ndef use_b():\n    return shared_helper()\n",
        encoding="utf-8",
    )
    git_init(proj)
    monkeypatch.chdir(proj)
    out, rc = index_in_process(proj, "--force")
    assert rc == 0, f"index failed:\n{out}"
    return proj


def _src_path():
    return Path(__file__).parent.parent / "src" / "roam" / "commands" / "cmd_fan.py"


# ---------------------------------------------------------------------------
# (1) AST guard -- 4 W607-CY phases now wrap inside cmd_fan
# ---------------------------------------------------------------------------


def test_w607dj_four_phases_wrapped_in_run_check_cy():
    """The W607-CY aggregation layer must now wrap 4 phases:
    ``score_classify`` (new) + ``compute_predicate`` / ``compute_verdict``
    / ``serialize_envelope`` (pre-existing). Each must appear inside a
    ``_run_check_cy("<phase>", ...)`` call inside cmd_fan.
    """
    src = _src_path().read_text(encoding="utf-8")

    canonical_phases = (
        "score_classify",  # W607-DJ extension
        "compute_predicate",
        "compute_verdict",
        "serialize_envelope",
    )
    for phase in canonical_phases:
        same_line = f'_run_check_cy("{phase}"' in src
        multi_line = any(f'_run_check_cy(\n{" " * indent}"{phase}"' in src for indent in (4, 8, 12, 16, 20, 24, 28, 32))
        assert same_line or multi_line, (
            f"phase ``{phase}`` is not wrapped in _run_check_cy(...); add the W607-CY guard or pin the canonical anchor"
        )


def test_w607dj_score_classify_wraps_in_both_modes():
    """``score_classify`` must wrap in BOTH symbol AND file mode so the
    4-phase template is symmetric across the dual-mode aggregator.

    Count the number of ``_run_check_cy("score_classify", ...)`` calls in
    the AST; should be >=2 (symbol-mode branch + file-mode branch).
    """
    src = _src_path().read_text(encoding="utf-8")
    tree = ast.parse(src)
    count = 0
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if not (isinstance(node.func, ast.Name) and node.func.id == "_run_check_cy"):
            continue
        if not node.args:
            continue
        phase_arg = node.args[0]
        if isinstance(phase_arg, ast.Constant) and phase_arg.value == "score_classify":
            count += 1
    assert count >= 2, (
        f"expected score_classify to wrap in BOTH symbol AND file mode; "
        f"found {count} call(s) -- the dual-mode aggregator must be "
        f"symmetric"
    )


# ---------------------------------------------------------------------------
# (2) Accumulator + helper still single-source (no W607-DJ-prefixed twins)
# ---------------------------------------------------------------------------


def test_w607dj_extends_w607cy_accumulator_no_new_bucket():
    """W607-DJ must REUSE the existing ``_w607cy_warnings_out`` bucket
    and ``_run_check_cy`` helper. No new ``_w607dj_warnings_out`` /
    ``_run_check_dj`` twin allowed -- that would split the marker
    channel and break the brief.
    """
    src = _src_path().read_text(encoding="utf-8")
    assert "w607cy_warnings_out" in src, (
        "W607-CY accumulator must still be the single bucket for the 4-phase aggregation layer"
    )
    assert "_run_check_cy" in src, (
        "W607-CY helper must still be the single wrap-helper for the 4-phase aggregation layer"
    )
    forbidden_twins = (
        "w607dj_warnings_out",
        "_run_check_dj",
        "_w607dj_helper",
    )
    for sym in forbidden_twins:
        assert sym not in src, (
            f"W607-DJ must EXTEND the W607-CY layer, not fork it; found forbidden twin {sym!r} in cmd_fan.py"
        )


# ---------------------------------------------------------------------------
# (3) Happy-path: clean fan symbol-mode -> no score_classify marker
# ---------------------------------------------------------------------------


def test_score_classify_clean_path_no_marker_symbol(cli_runner, fan_project):
    """Clean fan symbol-mode -> envelope omits any
    ``fan_score_classify_failed:`` marker. partial_success must NOT
    flip.
    """
    result = _invoke_fan(cli_runner, fan_project, "symbol")
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)
    assert data["command"] == "fan"

    top_wo = data.get("warnings_out") or []
    summary_wo = data["summary"].get("warnings_out") or []
    all_markers = list(top_wo) + list(summary_wo)
    leaked = [m for m in all_markers if m.startswith("fan_score_classify_failed:")]
    assert not leaked, f"clean fan symbol-mode must NOT surface fan_score_classify_failed:; got {leaked!r}"
    assert data["summary"].get("partial_success") is not True, data["summary"]


def test_score_classify_clean_path_no_marker_file(cli_runner, fan_project):
    """File-mode parity for the clean-path guard."""
    result = _invoke_fan(cli_runner, fan_project, "file")
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    top_wo = data.get("warnings_out") or []
    summary_wo = data["summary"].get("warnings_out") or []
    all_markers = list(top_wo) + list(summary_wo)
    leaked = [m for m in all_markers if m.startswith("fan_score_classify_failed:")]
    assert not leaked, leaked
    assert data["summary"].get("partial_success") is not True, data["summary"]


# ---------------------------------------------------------------------------
# (4) Clean path surfaces summary.run_state populated from score_classify
# ---------------------------------------------------------------------------


def test_score_classify_clean_path_surfaces_run_state_symbol(cli_runner, fan_project):
    """Clean fan symbol-mode -> ``summary.run_state`` populated from
    the score_classify result.

    The state must be one of the documented closed-enum buckets:
    HIGH_RISK_DETECTED / HUBS_AND_SPREADERS_DETECTED / HUBS_DETECTED /
    SPREADERS_DETECTED / LOCAL_ONLY / BALANCED. DEGRADED appears ONLY
    on the floor path -- and the floor only floors on raise, which
    the clean path doesn't hit.
    """
    result = _invoke_fan(cli_runner, fan_project, "symbol")
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)
    summary = data["summary"]
    assert "run_state" in summary, (
        f"clean fan symbol-mode must surface summary.run_state; got summary keys = {sorted(summary.keys())!r}"
    )
    valid_states = {
        "HIGH_RISK_DETECTED",
        "HUBS_AND_SPREADERS_DETECTED",
        "HUBS_DETECTED",
        "SPREADERS_DETECTED",
        "LOCAL_ONLY",
        "BALANCED",
    }
    assert summary["run_state"] in valid_states, (
        f"summary.run_state must be one of {valid_states!r} on the clean path; got {summary['run_state']!r}"
    )


def test_score_classify_clean_path_surfaces_run_state_file(cli_runner, fan_project):
    """File-mode parity."""
    result = _invoke_fan(cli_runner, fan_project, "file")
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)
    summary = data["summary"]
    assert "run_state" in summary, summary
    valid_states = {
        "HIGH_RISK_DETECTED",
        "HUBS_AND_SPREADERS_DETECTED",
        "HUBS_DETECTED",
        "SPREADERS_DETECTED",
        "BALANCED",
    }
    assert summary["run_state"] in valid_states, summary


# ---------------------------------------------------------------------------
# (5) score_classify raise -> marker surfaces (synthetic-raise wrap test)
# ---------------------------------------------------------------------------


def test_score_classify_failure_marker_format_symbol(cli_runner, fan_project, monkeypatch):
    """If the score_classify closure raises (symbol mode), surface the
    marker AND floor the run_state to ``DEGRADED``.

    Drive the failure by replacing ``_scope_flag`` so the flag column
    on the items list is a poisoned object whose equality comparison
    raises inside the score_classify closure.

    Pure path: patch the score_classify call via the items list.
    """
    from roam.commands import cmd_fan

    class _BadFlag:
        def __eq__(self, other):
            raise RuntimeError("synthetic-score-classify-symbol-from-W607-DJ")

        def __hash__(self):
            return 0

    real_scope_flag = cmd_fan._scope_flag

    def _poisoned_scope_flag(meta_entry, in_deg, out_deg):
        # First call returns the real flag, subsequent calls return
        # the poisoned sentinel so the items-list contains at least
        # one item that trips the bucket-equality check.
        _ = real_scope_flag(meta_entry, in_deg, out_deg)
        # Wrap the real string in the poisoned proxy so the items list
        # equality check raises.
        return _BadFlag()

    monkeypatch.setattr(cmd_fan, "_scope_flag", _poisoned_scope_flag)

    result = _invoke_fan(cli_runner, fan_project, "symbol")
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    top_wo = data.get("warnings_out") or []
    summary_wo = data["summary"].get("warnings_out") or []
    all_wo = list(top_wo) + list(summary_wo)
    markers = [m for m in all_wo if m.startswith("fan_score_classify_failed:")]
    assert markers, f"expected ``fan_score_classify_failed:`` marker; got {all_wo!r}"
    assert any("RuntimeError" in m for m in markers), markers

    # Floor must surface as DEGRADED on the envelope
    assert data["summary"].get("run_state") == "DEGRADED", (
        f"W978 floor discipline: run_state must floor to DEGRADED on "
        f"score_classify raise; got {data['summary'].get('run_state')!r}"
    )
    # partial_success must flip
    assert data["summary"].get("partial_success") is True, data["summary"]


def test_score_classify_failure_marker_format_file(cli_runner, fan_project, monkeypatch):
    """File-mode parity: score_classify raise -> marker + DEGRADED floor.

    Patch ``_file_flag`` on cmd_fan so the items-list contains a
    poisoned object whose equality check raises inside the closure.
    """
    from roam.commands import cmd_fan

    class _BadFlag:
        def __eq__(self, other):
            raise RuntimeError("synthetic-score-classify-file-from-W607-DJ")

        def __hash__(self):
            return 0

    # _file_flag is a NESTED function inside fan() -- we can't
    # monkeypatch it directly. Instead, patch the flag values
    # inside the items list at the score_classify call point by
    # monkeypatching ``_run_check_cy`` to inject a poisoned items
    # arg on the score_classify boundary.

    # Approach: monkeypatch the closure indirectly by patching the
    # ``_scope_flag`` module-level helper. But _file_flag is nested.
    # The cleanest tripwire is to patch the bucket-equality at the
    # comparison site -- we override ``str.__eq__`` is impossible, so
    # instead we drive the failure via the more direct path:
    # patch the inner closure's ``len`` to raise (the closure calls
    # ``len(items_local)``).
    real_len = len

    raise_count = {"n": 0}

    def _len_that_raises_inside_score_classify(*args, **kwargs):
        # We don't want EVERY len() to raise; gate by call-count.
        # The score_classify closure calls len(items_local) AT MOST
        # ONCE per invocation. The cmd does many other len() calls
        # before reaching there. So count first N calls succeed,
        # then raise the (N+1)th to land inside score_classify.
        # Simpler: just raise on ALL len() calls inside cmd_fan but
        # only when invoked through the _run_check_cy wrap path.
        raise_count["n"] += 1
        if raise_count["n"] > 200:
            raise RuntimeError("synthetic-score-classify-file-from-W607-DJ")
        return real_len(*args, **kwargs)

    # Simpler: patch the file_flag values to make the equality check
    # raise. Inject by patching the file_items construction via
    # _run_check (the W607-X substrate hook). Cleanest path: monkey-
    # patch ``max`` to raise WHEN called on the items list -- but
    # that lands in compute_predicate, not score_classify.
    #
    # Best path: monkeypatch the ``_run_check_cy`` helper so the
    # score_classify call (and ONLY the score_classify call) raises.
    for attr in dir(cmd_fan):
        if attr == "fan":
            break

    # The cleanest synthetic-raise: replace the entire fan() command's
    # score_classify wrap by injecting the failure at the closure-
    # level via a SetItem-style monkeypatch on the items list passed
    # in. Easiest: just patch the inner closure-callable indirectly
    # by making one item.__getitem__("flag") raise.
    #
    # Implementation: monkeypatch fan_items list-construction via the
    # _scope_flag-like helper at module level. file mode uses
    # nested _file_flag; we can't reach it. So we drive failure via
    # the _run_check_cy wrap itself: patch it to raise ON the
    # score_classify phase only.

    # Instead, patch _w607cy_warnings_out append by patching the
    # helper inside the fan() closure scope. Cleanest: subclass the
    # cmd at runtime is infeasible. So we use the same pattern as
    # the symbol-mode test: items have poisoned flag values.
    #
    # In file mode, file_items["flag"] = _file_flag(fan_in, fan_out)
    # is built INSIDE the closure. We patch the ``_run_check`` call
    # for ``fetch_file_rows`` so it returns rows with poisoned
    # values that propagate into _file_flag and then into the
    # score_classify closure.

    # The simplest correct path: substitute the score_classify
    # behavior by patching ``len`` (used inside the closure) once we
    # are deep enough into the call. Use a context-counter.
    raise_count = {"n": 0}

    def _len_late_raise(obj):
        raise_count["n"] += 1
        # The closure calls len() near the end. Many other len()
        # calls precede it. We use sys._getframe to gate by frame.
        import sys as _sys

        frame = _sys._getframe(1)
        if frame.f_code.co_name in (
            "_score_classify_symbol",
            "_score_classify_file",
        ):
            raise RuntimeError("synthetic-score-classify-file-from-W607-DJ")
        return real_len(obj)

    monkeypatch.setattr("builtins.len", _len_late_raise)

    result = _invoke_fan(cli_runner, fan_project, "file")
    # Restore len BEFORE asserting (the test framework may need it).
    monkeypatch.undo()

    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)
    top_wo = data.get("warnings_out") or []
    summary_wo = data["summary"].get("warnings_out") or []
    all_wo = list(top_wo) + list(summary_wo)
    markers = [m for m in all_wo if m.startswith("fan_score_classify_failed:")]
    assert markers, f"expected ``fan_score_classify_failed:`` marker (file mode); got {all_wo!r}"
    assert any("RuntimeError" in m for m in markers), markers
    assert data["summary"].get("run_state") == "DEGRADED", data["summary"]


# ---------------------------------------------------------------------------
# (6) score_classify marker uses canonical ``fan_*`` family prefix
# ---------------------------------------------------------------------------


def test_score_classify_marker_uses_fan_family_prefix(cli_runner, fan_project, monkeypatch):
    """W607-DJ marker must use ``fan_score_classify_failed:<exc>:<detail>``
    -- same canonical family as the other W607-CY phases.
    """
    from roam.commands import cmd_fan

    class _BadFlag:
        def __eq__(self, other):
            raise PermissionError("synthetic-prefix-discipline-from-W607-DJ")

        def __hash__(self):
            return 0

    real_scope_flag = cmd_fan._scope_flag

    def _poisoned_scope_flag(meta_entry, in_deg, out_deg):
        real_scope_flag(meta_entry, in_deg, out_deg)
        return _BadFlag()

    monkeypatch.setattr(cmd_fan, "_scope_flag", _poisoned_scope_flag)

    result = _invoke_fan(cli_runner, fan_project, "symbol")
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)
    top_wo = data.get("warnings_out") or []
    failure_markers = [m for m in top_wo if "_score_classify_failed:" in m]
    assert failure_markers, f"expected fan_score_classify_failed: marker; got {top_wo!r}"
    for marker in failure_markers:
        assert marker.startswith("fan_"), (
            f"W607-DJ score_classify marker must use the canonical ``fan_*`` family; got {marker!r}"
        )


# ---------------------------------------------------------------------------
# (7) W607-CY 3-phase coexistence with W607-DJ extension
# ---------------------------------------------------------------------------


def test_w607cy_three_phases_still_wrap_after_dj_extension(cli_runner, fan_project):
    """The prior 3 W607-CY phases (compute_predicate / compute_verdict /
    serialize_envelope) must STILL wrap correctly after the W607-DJ
    extension. The 4th phase is additive, not a replacement.

    Source-grep guard: all 3 prior phases must still appear inside a
    ``_run_check_cy("<phase>", ...)`` call.
    """
    src = _src_path().read_text(encoding="utf-8")
    prior_phases = (
        "compute_predicate",
        "compute_verdict",
        "serialize_envelope",
    )
    for phase in prior_phases:
        same_line = f'_run_check_cy("{phase}"' in src
        multi_line = any(f'_run_check_cy(\n{" " * indent}"{phase}"' in src for indent in (4, 8, 12, 16, 20, 24, 28, 32))
        assert same_line or multi_line, (
            f"prior W607-CY phase ``{phase}`` no longer wraps after the "
            f"W607-DJ extension; W607-DJ must EXTEND, not REPLACE"
        )


# ---------------------------------------------------------------------------
# (8) W607-X substrate coexistence (no shadowing by W607-DJ)
# ---------------------------------------------------------------------------


def test_w607x_substrate_layer_still_wraps_after_dj_extension(cli_runner, fan_project):
    """W607-X substrate-CALL layer (6 phases) must still wrap after the
    W607-DJ extension. Both ``_w607x_warnings_out`` and ``_run_check``
    must still be present and the canonical substrate phase names must
    still be reachable.
    """
    src = _src_path().read_text(encoding="utf-8")
    assert "w607x_warnings_out" in src, (
        "W607-X accumulator vanished after W607-DJ extension; the "
        "additive plumbing must preserve the substrate-CALL layer"
    )

    w607x_phases = (
        "fetch_symbol_rows",
        "filter_tooling",
        "file_scope_metrics",
        "emit_findings_symbol",
        "fetch_file_rows",
        "emit_findings_file",
    )
    for phase in w607x_phases:
        # _run_check (not _run_check_cy) is the W607-X helper
        same_line = f'_run_check("{phase}"' in src
        multi_line = any(f'_run_check(\n{" " * indent}"{phase}"' in src for indent in (4, 8, 12, 16, 20, 24, 28, 32))
        assert same_line or multi_line, (
            f"W607-X substrate phase ``{phase}`` no longer wraps after "
            f"the W607-DJ extension; substrate layer must be preserved"
        )


# ---------------------------------------------------------------------------
# (9) FIVE-WAY pairing pin -- agg-layer closure
# ---------------------------------------------------------------------------


def test_five_way_pairing_pin_substrate_plus_agg():
    """AST-level pin: each of the 5 symbol-relations consumer modules
    must carry BOTH its substrate-layer prefix AND its agg-layer prefix.

    Closes the FIVE-WAY at the agg-layer:
      - cmd_uses     -> substrate W607-U  + agg W607-DE
      - cmd_relate   -> substrate W607-W  + agg W607-DA
      - cmd_deps     -> substrate W607-V  + agg W607-DB
      - cmd_describe -> substrate W607-K  + agg W607-DG
      - cmd_fan      -> substrate W607-X  + agg W607-CY (extended by W607-DJ)
    """
    commands_dir = Path(__file__).parent.parent / "src" / "roam" / "commands"
    expected = {
        "cmd_uses.py": ("W607-U", "W607-DE"),
        "cmd_relate.py": ("W607-W", "W607-DA"),
        "cmd_deps.py": ("W607-V", "W607-DB"),
        "cmd_describe.py": ("W607-K", "W607-DG"),
        "cmd_fan.py": ("W607-X", "W607-CY"),
    }
    for filename, (substrate, agg) in expected.items():
        path = commands_dir / filename
        assert path.exists(), f"{filename} missing at {path}"
        src = path.read_text(encoding="utf-8")
        # Use word-boundary substring check (W607-U must not match W607-UU)
        # Simple form: search for "W607-U\b" via substring scan
        for prefix, role in ((substrate, "substrate"), (agg, "agg-layer")):
            # Match "W607-X" but not "W607-XYZ" -- check followed by non-letter
            found = False
            i = 0
            while True:
                idx = src.find(prefix, i)
                if idx < 0:
                    break
                end = idx + len(prefix)
                if end >= len(src) or not ("A" <= src[end] <= "Z" or "a" <= src[end] <= "z"):
                    found = True
                    break
                i = end
            assert found, (
                f"{filename} missing {role} prefix {prefix!r}; the FIVE-WAY "
                f"agg-layer pairing pin requires both substrate + agg-layer "
                f"prefixes present"
            )


# ---------------------------------------------------------------------------
# (10) W978 7-discipline AST audit on the W607-DJ extension
# ---------------------------------------------------------------------------


def test_w607dj_w978_seven_discipline_ast_audit():
    """AST audit: the W607-DJ score_classify extension honours all 7
    W978 first-hypothesis disciplines.
    """
    src = _src_path().read_text(encoding="utf-8")
    tree = ast.parse(src)

    # Collect all _run_check_cy("score_classify", ...) calls.
    score_classify_calls = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if not (isinstance(node.func, ast.Name) and node.func.id == "_run_check_cy"):
            continue
        if not node.args:
            continue
        phase_arg = node.args[0]
        if isinstance(phase_arg, ast.Constant) and phase_arg.value == "score_classify":
            score_classify_calls.append(node)
    assert len(score_classify_calls) >= 2, (
        f"expected >=2 score_classify wraps (symbol + file); got {len(score_classify_calls)}"
    )

    # Discipline 1: f-string verdict floor (n/a for score_classify --
    # its floor is a dict, not an f-string). The compute_verdict floor
    # is checked by the W607-CY test; here we assert score_classify's
    # floor is NOT an f-string.
    for call in score_classify_calls:
        for kw in call.keywords:
            if kw.arg == "default":
                assert not isinstance(kw.value, ast.JoinedStr), (
                    f"W978 discipline 1: score_classify floor must NOT be "
                    f"an f-string; got JoinedStr at line {kw.value.lineno}"
                )

    # Discipline 2: kwarg-default eagerness -- default= MUST NOT be an
    # ast.Call (eager evaluation of an expensive default).
    for call in score_classify_calls:
        for kw in call.keywords:
            if kw.arg == "default":
                assert not isinstance(kw.value, ast.Call), (
                    f"W978 discipline 2: score_classify default= must not be an ast.Call; got {ast.dump(kw.value)!r}"
                )
                # Default must be an ast.Dict with concrete-constant values
                assert isinstance(kw.value, ast.Dict), (
                    f"W978 discipline 2: score_classify default= must be a "
                    f"literal ast.Dict; got {type(kw.value).__name__}"
                )
                for v in kw.value.values:
                    if isinstance(v, ast.Call):
                        # Only allow len(name) per the W607-CY convention
                        assert isinstance(v.func, ast.Name) and v.func.id == "len", (
                            f"W978 discipline 2: score_classify floor may "
                            f"only call ``len(name)`` as a default "
                            f"computation; got {ast.dump(v)!r}"
                        )
                    elif isinstance(v, ast.Name):
                        # W978 5th-discipline ANCHOR: a pre-captured
                        # int Name reference (e.g. _symbol_items_count
                        # = len(symbol_items) captured BEFORE the wrap
                        # call) is the SAFE form -- it does not
                        # re-evaluate ``len()`` at kwarg-bind time on
                        # a potentially poisoned object. Pattern is
                        # explicit in the score_classify wrap.
                        assert v.id.endswith("_count") or v.id.endswith("_n"), (
                            f"W978 discipline 5/6: score_classify floor "
                            f"Name references must be pre-captured ``_*_count``"
                            f" or ``_*_n`` (e.g. ``_symbol_items_count``); "
                            f"got {v.id!r}"
                        )
                    else:
                        assert isinstance(v, ast.Constant), (
                            f"W978 discipline 6: score_classify floor "
                            f"values must be ast.Constant, len(name), or "
                            f"pre-captured ``_*_count`` Name; got "
                            f"{type(v).__name__}"
                        )

    # Discipline 3: json.dumps(default=str) sentinel NOT used inside
    # the W607-CY helper. (Same as W607-CY test.)
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "_run_check_cy":
            helper_src = ast.unparse(node)
            assert "json.dumps" not in helper_src, "W978 discipline 3: _run_check_cy must not call json.dumps"

    # Discipline 4: phase-name collision check. ``score_classify`` must
    # NOT collide with any W607-X substrate-CALL phase name, AND must
    # NOT collide with any prior W607-CY aggregation phase name.
    score_classify_phase = "score_classify"
    w607cy_existing_phases = {
        "compute_predicate",
        "compute_verdict",
        "serialize_envelope",
    }
    w607x_phases = {
        "fetch_symbol_rows",
        "filter_tooling",
        "file_scope_metrics",
        "emit_findings_symbol",
        "fetch_file_rows",
        "emit_findings_file",
    }
    assert score_classify_phase not in w607cy_existing_phases, (
        "W978 discipline 4: score_classify collides with an existing W607-CY phase name"
    )
    assert score_classify_phase not in w607x_phases, (
        "W978 discipline 4: score_classify collides with a W607-X substrate-CALL phase name"
    )

    # Discipline 5: len() at kwarg-bind. The closure must be passed
    # ``symbol_items`` / ``file_items`` as a raw positional arg, NOT
    # ``len(symbol_items)`` inline. (len() may be called INSIDE the
    # closure -- that's the safe path.)
    for call in score_classify_calls:
        # call.args = [phase_string, closure, items_arg]
        assert len(call.args) >= 3, (
            f"W978 discipline 5: score_classify wrap must pass the items "
            f"list as a positional arg; got args = {[ast.dump(a) for a in call.args]!r}"
        )
        items_arg = call.args[2]
        # Must be a Name reference (symbol_items / file_items), NOT a
        # Call like ``len(symbol_items)``.
        assert isinstance(items_arg, ast.Name), (
            f"W978 discipline 5: score_classify items arg must be a bare "
            f"Name reference (symbol_items / file_items); got "
            f"{type(items_arg).__name__}: {ast.dump(items_arg)!r}"
        )

    # Discipline 6: floor values are concrete defaults -- enforced above
    # in the Discipline 2 check (ast.Constant / len(name)).

    # Discipline 7: dict.get(key, expensive_default) eager-eval. The
    # summary line that reads ``_score_dict["state"]`` must use bare
    # subscript, NOT ``_score_dict.get("state", expensive_call)``. We
    # check there's NO ``_score_dict.get("state"`` substring in source.
    assert "_score_dict.get(" not in src, (
        "W978 discipline 7: _score_dict must use bare subscript "
        '``_score_dict["state"]`` lookup, NOT ``.get()``; floor dict '
        "guarantees the key"
    )


# ---------------------------------------------------------------------------
# (11) Cross-prefix isolation -- W607-DJ marker uses fan_* not dj_*
# ---------------------------------------------------------------------------


def test_w607dj_marker_does_not_leak_dj_prefix(cli_runner, fan_project, monkeypatch):
    """W607-DJ EXTENDS the W607-CY layer, so the marker family stays
    ``fan_*``. No ``dj_*`` / ``w607dj_*`` prefix leaks allowed.
    """
    from roam.commands import cmd_fan

    class _BadFlag:
        def __eq__(self, other):
            raise RuntimeError("synthetic-prefix-check-from-W607-DJ")

        def __hash__(self):
            return 0

    real_scope_flag = cmd_fan._scope_flag

    def _poisoned_scope_flag(meta_entry, in_deg, out_deg):
        real_scope_flag(meta_entry, in_deg, out_deg)
        return _BadFlag()

    monkeypatch.setattr(cmd_fan, "_scope_flag", _poisoned_scope_flag)

    result = _invoke_fan(cli_runner, fan_project, "symbol")
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)
    top_wo = data.get("warnings_out") or []
    summary_wo = data["summary"].get("warnings_out") or []
    all_markers = list(top_wo) + list(summary_wo)
    failure_markers = [m for m in all_markers if "_failed:" in m]
    assert failure_markers, "expected non-empty failure-marker bucket"

    forbidden_prefixes = ("dj_", "w607dj_", "DJ_")
    for marker in failure_markers:
        for forbidden in forbidden_prefixes:
            assert forbidden not in marker.split(":")[0], (
                f"W607-DJ marker must use ``fan_*`` family; found forbidden prefix {forbidden!r} in marker {marker!r}"
            )


# ---------------------------------------------------------------------------
# (12) Phase total -- exactly 4 distinct phases registered under W607-CY
# ---------------------------------------------------------------------------


def test_w607cy_extended_to_exactly_four_phases():
    """After W607-DJ lands, the W607-CY aggregation layer must wrap
    exactly 4 DISTINCT phase names (no more, no less).

    This is the canonical template-conformance check: the 4-phase
    cmd_dark_matter W607-CZ template -> cmd_fan W607-CY template.
    """
    src = _src_path().read_text(encoding="utf-8")
    tree = ast.parse(src)

    distinct_phases = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if not (isinstance(node.func, ast.Name) and node.func.id == "_run_check_cy"):
            continue
        if not node.args:
            continue
        phase_arg = node.args[0]
        if isinstance(phase_arg, ast.Constant) and isinstance(phase_arg.value, str):
            distinct_phases.add(phase_arg.value)

    expected_phases = {
        "score_classify",
        "compute_predicate",
        "compute_verdict",
        "serialize_envelope",
    }
    assert distinct_phases == expected_phases, (
        f"W607-CY (extended by W607-DJ) must wrap exactly the 4 canonical "
        f"phases; expected {expected_phases!r}, got {distinct_phases!r}"
    )
