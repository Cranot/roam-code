"""W35 — tests for user-task probes (stack-trace, body-embed, recent-change).

Each probe maps a real user-shape request to compile-time data embedded
in the L1 envelope, so the agent answers from the envelope instead of
running tools.
"""

from __future__ import annotations

import subprocess

from roam.plan.compiler import (
    _classifier_confidence,
    _classify,
    _extract_stack_frames,
    _looks_like_stack_trace,
    _probe_freeform_augment_for_task,
    _probe_stack_trace_for_task,
    _read_file_slice,
    compile_for_artifact,
    compile_plan,
)

# ----------------------------- W35a -----------------------------

PY_TRACEBACK = """Traceback (most recent call last):
  File "src/roam/plan/compiler.py", line 145, in _classify
    return procedure, rejected
  File "src/roam/cli.py", line 10, in main
    raise SystemExit(rc)
ValueError: bad value"""

GENERIC_TRACE = """tests/test_w35.py:42: AssertionError: expected 1 got 2
Error in test_a"""


def test_w35a_extract_python_frames():
    frames = _extract_stack_frames(PY_TRACEBACK)
    assert ("src/roam/plan/compiler.py", 145) in frames
    assert ("src/roam/cli.py", 10) in frames


def test_w35a_extract_generic_frames():
    frames = _extract_stack_frames(GENERIC_TRACE)
    assert ("tests/test_w35.py", 42) in frames


def test_w35a_looks_like_stack_trace_requires_error_context():
    # Plain file:line WITHOUT an error word → NOT a stack trace
    assert not _looks_like_stack_trace("see src/foo.py:42 for details")
    # WITH an error word → YES
    assert _looks_like_stack_trace("Error in src/foo.py:42")


def test_w35a_classifier_picks_stack_trace_fix():
    proc, _ = _classify(PY_TRACEBACK)
    assert proc == "stack_trace_fix"
    assert _classifier_confidence(PY_TRACEBACK, proc) >= 0.90


def test_w35a_stack_trace_does_not_misroute_to_synthesis():
    # "raised" appears in PY_TRACEBACK but it should NOT route to synthesis
    proc, _ = _classify(PY_TRACEBACK)
    assert proc != "synthesis_query"


def test_w35a_probe_reads_slice(tmp_path):
    f = tmp_path / "buggy.py"
    f.write_text("\n".join(f"line {i}" for i in range(1, 21)) + "\n")
    task = f'Error: File "{f}", line 10, in foo'
    out = _probe_stack_trace_for_task(task, cwd=None)
    assert out is not None
    assert "stack_frames" in out
    frame = out["stack_frames"][0]
    assert frame["line"] == 10
    assert ">> " in frame["excerpt"]  # the marker for the failing line
    assert "line 10" in frame["excerpt"]


def test_w35a_probe_skips_missing_files(tmp_path):
    task = 'Error: File "/nonexistent/path/foo.py", line 1, in bar'
    out = _probe_stack_trace_for_task(task, cwd=None)
    assert out is None  # no readable frames → no probe data


def test_w35a_compile_for_artifact_routes_l1_probe(tmp_path):
    f = tmp_path / "broken.py"
    f.write_text("a = 1\nb = 2\nc = 3\n")
    task = f'Traceback:\n  File "{f}", line 2, in x\nValueError'
    plan = compile_plan(task)
    env, label = compile_for_artifact(plan, cwd=None)
    assert label == "l1_probe"
    pre = env["plan"]["prefetched_facts"]
    assert "stack_frames" in pre


def test_w35a_read_file_slice_marker_only_on_target_line(tmp_path):
    f = tmp_path / "x.py"
    f.write_text("a\nb\nc\nd\ne\n")
    out = _read_file_slice(str(f), 3, cwd=None, before=2, after=2)
    assert out is not None
    lines = out["excerpt"].splitlines()
    target_lines = [ln for ln in lines if ln.startswith(">> ")]
    assert len(target_lines) == 1
    assert " 3 " in target_lines[0]


# ----------------------------- W35b -----------------------------


def test_w35b_explain_question_classifies_freeform(tmp_path):
    # W-LIFT (2026-06-02): "what does <file> do" with a CONCRETE FILE PATH now
    # routes to the dedicated `describe_file` procedure (a tight l1_probe of the
    # file skeleton/summary) instead of the broad low-confidence freeform dump.
    # Usage telemetry showed this file-purpose shape was ~the largest mislabeled
    # cluster in freeform. The body is still embedded (describe_file reuses the
    # freeform skeleton probe).
    proc, _ = _classify("what does src/roam/atomic_io.py do")
    assert proc == "describe_file"
    # No file path → abstract explain question still falls to freeform.
    proc2, _ = _classify("what does the authentication flow do")
    assert proc2 == "freeform_explore"


def test_w35b_probe_embeds_file_excerpt(tmp_path):
    f = tmp_path / "small.py"
    f.write_text("\n".join(f"# line {i}" for i in range(100)))
    out = _probe_freeform_augment_for_task(
        f"explain what {f} does",
        named_paths=[str(f)],
        cwd=None,
    )
    assert out is not None
    assert "file_excerpt" in out
    assert out["file_excerpt"]["lines_shown"] == 80
    assert "# line 0" in out["file_excerpt"]["content"]


def test_w35b_probe_skips_when_no_explain_word():
    # "files coupled to X" is NOT an explain question — no excerpt expected
    out = _probe_freeform_augment_for_task(
        "files coupled to src/roam/cli.py",
        named_paths=["src/roam/cli.py"],
        cwd=None,
    )
    # may return None or just recent_commits if history words present;
    # but file_excerpt must NOT be present
    if out is not None:
        assert "file_excerpt" not in out


def test_w35b_no_named_paths_returns_none():
    out = _probe_freeform_augment_for_task(
        "explain what this codebase does",
        named_paths=[],
        cwd=None,
    )
    assert out is None


# ----------------------------- W35c -----------------------------


def test_w35c_history_question_in_freeform(tmp_path, monkeypatch):
    # Create a git repo with one commit touching a file
    monkeypatch.chdir(tmp_path)
    subprocess.run(["git", "init", "-q"], check=True)
    subprocess.run(["git", "config", "user.email", "t@t"], check=True)
    subprocess.run(["git", "config", "user.name", "t"], check=True)
    f = tmp_path / "x.py"
    f.write_text("a = 1\n")
    subprocess.run(["git", "add", "x.py"], check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], check=True)

    out = _probe_freeform_augment_for_task(
        "what changed in x.py recently",
        named_paths=["x.py"],
        cwd=str(tmp_path),
    )
    assert out is not None
    assert "recent_commits" in out
    assert "init" in out["recent_commits"]


def test_w35c_no_history_word_skips_probe(tmp_path):
    out = _probe_freeform_augment_for_task(
        "explain what src/x.py does",
        named_paths=["src/x.py"],
        cwd=None,
    )
    # may have file_excerpt if file exists, but must NOT have recent_commits
    if out is not None:
        assert "recent_commits" not in out


# ----------------------------- routing integration -----------------------------


def test_w35a_stack_trace_in_compile_plan_envelope(tmp_path):
    f = tmp_path / "fail.py"
    f.write_text("x\ny\nz\n")
    task = f'TypeError: File "{f}", line 2, in fn'
    plan = compile_plan(task)
    env, label = compile_for_artifact(plan, cwd=None)
    assert label == "l1_probe"
    assert plan.procedure == "stack_trace_fix"
    # the answer_contract should anchor on the embedded slice, not on
    # re-Reading files. W39 B1 made bullet[0] an explicit anti-Read
    # directive; any of the W35a wordings is acceptable.
    bullet0 = env["plan"].get("answer_contract", [""])[0]
    acceptable = ("DO NOT", "Identify", "Read the embedded")
    assert any(p in bullet0 for p in acceptable), f"bullet[0] should anchor on embedded slice; got {bullet0!r}"
