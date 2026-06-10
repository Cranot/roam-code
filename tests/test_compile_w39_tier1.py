"""W39 Tier-1 gap closures — tests for:
B1 stack_trace_fix contract has anti-Read directive at position 0
B2 write_pytest probe embeds src + conftest excerpts (not just sibling test)
C2 structural_blast backtick-fallback fires when user names symbol in backticks
D1 compile telemetry sidecar appends one JSONL entry per call
"""

from __future__ import annotations

import json
import os

from roam.plan.compiler import (
    _PROCEDURE_CONTRACTS,
    _probe_blast_backtick_for_task,
    _probe_sibling_test_for_task,
    compile_for_artifact,
    compile_plan,
)

# ---- B1 ----


def test_w39_b1_stack_trace_contract_starts_with_anti_read():
    contract = _PROCEDURE_CONTRACTS["stack_trace_fix"]
    assert contract, "stack_trace_fix must have a contract"
    first = contract[0].lower()
    assert "do not" in first or "do not call" in first, f"First bullet should ban re-Reading; got: {contract[0]!r}"
    assert "read" in first, f"First bullet should mention Read; got: {contract[0]!r}"


# ---- B2 ----


def test_w39_b2_write_pytest_embeds_src_under_test(tmp_path):
    src_dir = tmp_path / "src"
    src_dir.mkdir()
    src_file = src_dir / "thing.py"
    src_file.write_text("\n".join(f"def fn_{i}(): pass" for i in range(40)))
    tests_dir = tmp_path / "tests"
    tests_dir.mkdir()
    (tests_dir / "test_thing.py").write_text("# imports + fixtures\n")
    (tests_dir / "conftest.py").write_text("import pytest\n@pytest.fixture\ndef fix():\n    pass\n")
    out = _probe_sibling_test_for_task(
        f"write a pytest for {src_file}",
        named_paths=[str(src_file)],
        cwd=str(tmp_path),
    )
    assert out is not None
    assert "src_under_test_excerpt" in out
    assert "conftest_excerpt" in out
    assert "sibling_test_excerpt" in out
    src = out["src_under_test_excerpt"]
    assert src["lines_shown"] > 0
    assert "fn_0" in src["content"]


def test_w39_b2_no_conftest_still_embeds_other_two(tmp_path):
    src_dir = tmp_path / "src"
    src_dir.mkdir()
    (src_dir / "thing.py").write_text("def x(): pass\n")
    tests_dir = tmp_path / "tests"
    tests_dir.mkdir()
    (tests_dir / "test_thing.py").write_text("# t\n")
    out = _probe_sibling_test_for_task(
        f"write a pytest for {src_dir / 'thing.py'}",
        named_paths=[str(src_dir / "thing.py")],
        cwd=str(tmp_path),
    )
    assert out is not None
    assert "sibling_test_excerpt" in out
    assert "src_under_test_excerpt" in out
    assert "conftest_excerpt" not in out


# ---- C2 ----


def test_w39_c2_blast_backtick_returns_none_without_backticks():
    # No backticked symbol → no probe
    out = _probe_blast_backtick_for_task(
        "what is the blast radius of the auth flow",
        cwd=None,
    )
    assert out is None


def test_w39_c2_blast_backtick_routes_l1_for_backticked_symbol(monkeypatch):
    """End-to-end: backticked symbol triggers L1 envelope routing."""
    repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if not os.path.exists(os.path.join(repo, ".roam", "index.db")):
        # Skip when not running in roam-indexed repo
        import pytest

        pytest.skip("requires .roam/index.db in cwd")
    plan = compile_plan("what's the blast radius of `compile_plan`")
    env, label = compile_for_artifact(plan, cwd=repo)
    assert plan.procedure == "structural_blast"
    pre = env["plan"].get("prefetched_facts", {})
    # Must have impact data via backtick fallback
    assert "impact_count" in pre, f"expected impact_count, got: {sorted(pre.keys())}"
    assert pre["impact_count"] > 0
    assert label == "l1_probe", f"expected l1_probe routing, got {label}"


# ---- D1 ----


def test_w39_d1_telemetry_writes_jsonl_line(tmp_path):
    """When cwd has a .roam/ dir, compile_for_artifact appends one line."""
    (tmp_path / ".roam").mkdir()
    plan = compile_plan("what does some_module.py do")
    env, label = compile_for_artifact(plan, cwd=str(tmp_path))
    log = tmp_path / ".roam" / "compile-runs.jsonl"
    assert log.exists()
    lines = log.read_text().splitlines()
    assert len(lines) == 1
    entry = json.loads(lines[0])
    for field in (
        "ts",
        "task_hash",
        "task_prefix",
        "procedure",
        "classifier_conf",
        "art_label",
        "prefetched_keys",
        "envelope_bytes",
        "compile_ms",
    ):
        assert field in entry, f"missing telemetry field: {field}"
    assert entry["art_label"] == label
    assert entry["procedure"] == plan.procedure


def test_w39_d1_telemetry_silently_skips_when_no_roam_dir(tmp_path):
    """No .roam/ → telemetry helper writes nothing.

    Tests the helper directly so any subprocess side-effects from
    real probes don't pollute the assertion.
    """
    from roam.plan.compiler import _maybe_append_compile_telemetry

    plan = compile_plan("what does X do")
    # tmp_path has NO .roam dir → helper must skip
    _maybe_append_compile_telemetry(plan, {}, "full", 1.0, str(tmp_path))
    assert not (tmp_path / ".roam").exists()
    assert not (tmp_path / ".roam" / "compile-runs.jsonl").exists()


def test_w39_d1_telemetry_silently_skips_when_cwd_none():
    """cwd=None → helper short-circuits without error."""
    from roam.plan.compiler import _maybe_append_compile_telemetry

    plan = compile_plan("what does X do")
    # No raise, no side-effect
    _maybe_append_compile_telemetry(plan, {}, "full", 1.0, None)


def test_w39_d1_telemetry_skips_when_log_exceeds_10mb(tmp_path):
    (tmp_path / ".roam").mkdir()
    log = tmp_path / ".roam" / "compile-runs.jsonl"
    # Write 11 MB of placeholder so the rotation trip-wire kicks in
    log.write_bytes(b"x" * (11 * 1024 * 1024))
    size_before = log.stat().st_size
    plan = compile_plan("what is X")
    compile_for_artifact(plan, cwd=str(tmp_path))
    assert log.stat().st_size == size_before, "should not append once >10MB"
