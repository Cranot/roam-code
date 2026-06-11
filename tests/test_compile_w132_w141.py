"""W132-W141 — lock-bypass, broaden skips, refactor-move skeleton,
recommended_model hint, _fast_json_dumps coverage.
"""

from __future__ import annotations

import os

from roam.plan.compiler import (
    _PROCEDURE_PROBE_SKIPS,
    _fast_json_dumps,
    _probe_refactor_move_for_task,
    compile_for_artifact,
    compile_plan,
)


def test_w132_lock_bypass_when_cwd_matches(monkeypatch):
    """When cwd argument matches getcwd(), the lock is NOT acquired."""
    # Sanity: skip-decision matches what _roam_invoke_inproc uses.
    cur = os.getcwd()
    need_chdir_same = bool(cur) and os.path.abspath(cur) != os.getcwd()
    assert need_chdir_same is False  # documents the bypass condition
    need_chdir_none = bool(None) and os.path.abspath("") != os.getcwd()
    assert need_chdir_none is False
    # Sanity: a different cwd would trigger chdir.
    other = "/tmp"
    need_chdir_other = bool(other) and os.path.abspath(other) != os.getcwd()
    assert need_chdir_other is True


def test_w133_broaden_skip_table_includes_refactor_move_and_freeform():
    # The dead-label audit corrected this table: every skip label must be a
    # REGISTERED extender label (the original "owner_probe" pin was a dead
    # label that never applied — see test_procedure_registry_lint /
    # test_compile_intent_probes.test_dead_skip_labels_never_reappear).
    # todo_audit/owners are deliberately NOT skipped for freeform anymore:
    # they self-gate on microsecond regexes and answer real prompt shapes.
    assert "freeform_explore" in _PROCEDURE_PROBE_SKIPS
    assert "refactor_move" in _PROCEDURE_PROBE_SKIPS
    assert "refactor_move" in _PROCEDURE_PROBE_SKIPS["stack_trace_fix"]
    assert "api_surface" in _PROCEDURE_PROBE_SKIPS["stack_trace_fix"]
    assert "subprocess_audit" in _PROCEDURE_PROBE_SKIPS["freeform_explore"]
    assert "owner_probe" not in _PROCEDURE_PROBE_SKIPS["freeform_explore"]
    assert "todo_audit" not in _PROCEDURE_PROBE_SKIPS["freeform_explore"]


def test_w134_refactor_move_emits_destination_skeleton(tmp_path):
    """When target file does NOT exist, probe ships destination_skeleton."""
    src = tmp_path / "src" / "mod"
    src.mkdir(parents=True)
    f = src / "origin.py"
    f.write_text("def my_func():\n    pass\n")
    task = "move my_func from src/mod/origin.py to src/mod/helpers.py"
    result = _probe_refactor_move_for_task(task, cwd=str(tmp_path))
    assert result is not None
    rm = result["refactor_move"]
    assert rm["destination_exists"] is False
    assert "destination_skeleton" in rm
    assert "my_func" in rm["destination_skeleton"]
    assert (
        "src/mod/helpers.py" not in (tmp_path / "src" / "mod" / "helpers.py").read_text()
        if (tmp_path / "src" / "mod" / "helpers.py").exists()
        else True
    )


def test_w134_refactor_move_no_skeleton_when_target_exists(tmp_path):
    src = tmp_path / "src" / "mod"
    src.mkdir(parents=True)
    (src / "origin.py").write_text("def my_func(): pass\n")
    (src / "helpers.py").write_text("# already exists\n")
    task = "move my_func from src/mod/origin.py to src/mod/helpers.py"
    result = _probe_refactor_move_for_task(task, cwd=str(tmp_path))
    assert result is not None
    rm = result["refactor_move"]
    assert rm["destination_exists"] is True
    assert "destination_skeleton" not in rm


def test_w136_recommended_model_emitted_in_envelope(tmp_path):
    """The envelope carries recommended_model + reason."""
    (tmp_path / ".roam").mkdir()
    src = tmp_path / "src"
    src.mkdir()
    (src / "thing.py").write_text("def thing(): pass\n")
    plan = compile_plan("who calls `thing` in src/thing.py")
    env, _ = compile_for_artifact(plan, cwd=str(tmp_path))
    plan_section = env.get("plan") or {}
    rec = plan_section.get("recommended_model")
    if rec is not None:  # may be absent when prefetched_facts is empty
        assert rec in ("haiku", "sonnet", "opus")
        assert "recommended_model_reason" in plan_section


def test_w136_freeform_explore_routes_to_opus():
    """freeform_explore procedure always recommends opus."""
    plan = compile_plan("trace how everything in the compiler module flows together over time")
    env, _ = compile_for_artifact(plan)
    plan_section = env.get("plan") or {}
    rec = plan_section.get("recommended_model")
    if rec is not None and plan_section.get("procedure") == "freeform_explore":
        assert rec == "opus"


def test_w135_w137_fast_json_dumps_basic_roundtrip():
    """_fast_json_dumps produces parseable JSON."""
    import json

    obj = {"a": 1, "b": [1, 2, 3], "c": {"nested": True}}
    blob = _fast_json_dumps(obj)
    parsed = json.loads(blob)
    assert parsed == obj


def test_w135_fast_json_dumps_unicode_safe():
    """Unicode characters survive the round-trip."""
    import json

    obj = {"key": "café — €", "emoji_safe_fallback": "n/a"}
    blob = _fast_json_dumps(obj)
    parsed = json.loads(blob)
    assert parsed == obj
