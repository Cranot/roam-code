"""W36 — three more user-task probes:
W36a sibling-test (synthesis_query + "write a pytest for X")
W36b path-comparison (≥2 named paths + compare vocab)
W36c symbol-pickaxe (history vocab + backticked symbol)
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
from conftest import SYMLINK_SKIP_REASON  # noqa: E402

from roam.plan.compiler import (  # noqa: E402
    _COMPARE_RE,
    _SYMBOL_PICKAXE_RE,
    _TEST_WRITE_RE,
    _diff_operand_is_private,
    _probe_path_comparison_for_task,
    _probe_sibling_test_for_task,
    _probe_symbol_pickaxe_for_task,
    _resolve_sibling_test_path,
    compile_for_artifact,
    compile_plan,
)

# ---- regex sanity ----


def test_w36a_test_write_re_matches_common_phrasings():
    for s in [
        "write a pytest for compile_plan",
        "write a test for foo",
        "add a test for bar",
        "create a pytest",
        "test for the new logic",
    ]:
        assert _TEST_WRITE_RE.search(s), s


def test_w36a_test_write_re_does_not_misfire():
    for s in [
        "what does test_compile.py do",  # describes a test file
        "list all tests in the suite",  # 'tests' plural is not 'test for'
        "explain the test discovery flow",
    ]:
        assert not _TEST_WRITE_RE.search(s), s


def test_w36b_compare_re_matches():
    for s in [
        "compare a and b",
        "diff between cmd_a and cmd_b",
        "what's different in the two files",
        "side-by-side comparison",
    ]:
        assert _COMPARE_RE.search(s), s


def test_w36c_pickaxe_re_matches():
    for s in [
        "when did foo get added",
        "who removed bar",
        "when was baz introduced",
        "first commit of qux",
    ]:
        assert _SYMBOL_PICKAXE_RE.search(s), s


# ---- W36a sibling test resolution ----


def test_w36a_resolve_python_convention(tmp_path):
    src = tmp_path / "src" / "pkg"
    src.mkdir(parents=True)
    (src / "thing.py").write_text("x = 1\n")
    tests = tmp_path / "tests"
    tests.mkdir()
    (tests / "test_thing.py").write_text("def test_x(): pass\n")
    out = _resolve_sibling_test_path("src/pkg/thing.py", cwd=str(tmp_path))
    assert out == "tests/test_thing.py"


def test_w36a_glob_fallback_python(tmp_path):
    src = tmp_path / "src" / "pkg"
    src.mkdir(parents=True)
    (src / "thing.py").write_text("x = 1\n")
    tests = tmp_path / "tests"
    tests.mkdir()
    # Non-exact name (test_thing_consolidation.py rather than test_thing.py)
    (tests / "test_thing_extended.py").write_text("def test_x(): pass\n")
    out = _resolve_sibling_test_path("src/pkg/thing.py", cwd=str(tmp_path))
    assert out == "tests/test_thing_extended.py"


def test_w36a_go_convention(tmp_path):
    src = tmp_path / "pkg"
    src.mkdir()
    (src / "foo.go").write_text("package pkg\n")
    (src / "foo_test.go").write_text("package pkg\n")
    out = _resolve_sibling_test_path("pkg/foo.go", cwd=str(tmp_path))
    assert out == "pkg/foo_test.go"


def test_w36a_js_dot_test_convention(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    (src / "thing.ts").write_text("export const x = 1\n")
    (src / "thing.test.ts").write_text("test('x', () => {})\n")
    out = _resolve_sibling_test_path("src/thing.ts", cwd=str(tmp_path))
    assert out == "src/thing.test.ts"


def test_w36a_no_sibling_returns_none(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    (src / "thing.py").write_text("x = 1\n")
    assert _resolve_sibling_test_path("src/thing.py", cwd=str(tmp_path)) is None


# ---- W36a probe end-to-end ----


def test_w36a_probe_embeds_excerpt(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    (src / "x.py").write_text("def foo(): pass\n")
    tests = tmp_path / "tests"
    tests.mkdir()
    (tests / "test_x.py").write_text("\n".join(f"# line {i}" for i in range(50)))

    out = _probe_sibling_test_for_task(
        "write a pytest for src/x.py",
        named_paths=["src/x.py"],
        cwd=str(tmp_path),
    )
    assert out is not None
    s = out["sibling_test_excerpt"]
    assert s["src_path"] == "src/x.py"
    assert s["test_path"] == "tests/test_x.py"
    assert s["lines_shown"] == 50


def test_w36a_symlink_sibling_outside_repo_is_not_read(tmp_path):
    """Regression: sibling discovery (os.path.exists / glob) FOLLOWS symlinks,
    so a repo-tracked `tests/test_x.py` symlink whose target lives OUTSIDE the
    repo would otherwise leak the first 60 lines of an out-of-repo file into
    the compile envelope. The probe must re-resolve the sibling's real path
    under cwd and bail when it escapes, instead of opening it.
    """
    src = tmp_path / "src"
    src.mkdir()
    (src / "x.py").write_text("def foo(): pass\n")
    # An out-of-repo file (a sibling of the repo root, so its realpath is NOT
    # under cwd). 60+ lines so a leak would be observable in the excerpt.
    outside = tmp_path.parent / f"outside_secret_{tmp_path.name}.py"
    outside.write_text("TOP_SECRET_LEAKED_LINE = 1\n" * 80)
    tests = tmp_path / "tests"
    tests.mkdir()
    # Repo-local symlink that points outside the repo. os.symlink needs
    # privilege on Windows (WinError 1314) — skip there; the probe's
    # containment gate is cross-platform and runs in full on Linux CI.
    try:
        os.symlink(outside, tests / "test_x.py")
    except (OSError, NotImplementedError):
        pytest.skip(SYMLINK_SKIP_REASON)

    out = _probe_sibling_test_for_task(
        "write a pytest for src/x.py",
        named_paths=["src/x.py"],
        cwd=str(tmp_path),
    )
    # Probe bails rather than embedding the escaped target's contents.
    assert out is None
    # Defense-in-depth: even if a future change returned a payload, the secret
    # bytes must never reach the envelope.
    assert out is None or "TOP_SECRET_LEAKED_LINE" not in str(out)


def test_w36a_no_test_write_trigger_returns_none(tmp_path):
    """Probe must not fire when the task isn't a test-write request."""
    src = tmp_path / "src"
    src.mkdir()
    (src / "x.py").write_text("x = 1\n")
    tests = tmp_path / "tests"
    tests.mkdir()
    (tests / "test_x.py").write_text("# t\n")
    out = _probe_sibling_test_for_task(
        "what does src/x.py do",
        named_paths=["src/x.py"],
        cwd=str(tmp_path),
    )
    assert out is None


def test_w36a_no_named_paths_returns_none():
    out = _probe_sibling_test_for_task(
        "write a pytest for foo",
        named_paths=[],
        cwd=None,
    )
    assert out is None


# ---- W36b path-comparison probe ----


def test_w36b_diff_embedded(tmp_path):
    a = tmp_path / "a.py"
    a.write_text("x = 1\ny = 2\n")
    b = tmp_path / "b.py"
    b.write_text("x = 1\ny = 99\n")
    out = _probe_path_comparison_for_task(
        "compare a.py and b.py",
        named_paths=["a.py", "b.py"],
        cwd=str(tmp_path),
    )
    assert out is not None
    c = out["path_comparison"]
    assert not c["identical"]
    assert "y = 99" in c["diff"]


def test_w36b_identical_files(tmp_path):
    a = tmp_path / "a.py"
    a.write_text("x = 1\n")
    b = tmp_path / "b.py"
    b.write_text("x = 1\n")
    out = _probe_path_comparison_for_task(
        "compare a.py and b.py",
        named_paths=["a.py", "b.py"],
        cwd=str(tmp_path),
    )
    assert out is not None
    assert out["path_comparison"]["identical"]


def test_w36b_no_compare_vocab_returns_none(tmp_path):
    a = tmp_path / "a.py"
    a.write_text("x = 1\n")
    b = tmp_path / "b.py"
    b.write_text("x = 2\n")
    out = _probe_path_comparison_for_task(
        "explain a.py and b.py",
        named_paths=["a.py", "b.py"],
        cwd=str(tmp_path),
    )
    assert out is None


def test_w36b_single_path_returns_none(tmp_path):
    a = tmp_path / "a.py"
    a.write_text("x = 1\n")
    out = _probe_path_comparison_for_task(
        "compare a.py",
        named_paths=["a.py"],
        cwd=str(tmp_path),
    )
    assert out is None


def test_w36b_missing_file_returns_none(tmp_path):
    a = tmp_path / "a.py"
    a.write_text("x\n")
    out = _probe_path_comparison_for_task(
        "compare a.py and missing.py",
        named_paths=["a.py", "missing.py"],
        cwd=str(tmp_path),
    )
    assert out is None


def test_w36b_rejects_uncontained_diff_operands_with_cwd(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "a.py").write_text("x = 1\n")
    (repo / "b.py").write_text("x = 2\n")
    outside = tmp_path / "outside.py"
    outside.write_text("SECRET_OUTSIDE_REPO = 1\n")

    assert (
        _probe_path_comparison_for_task(
            "compare absolute a.py and b.py",
            named_paths=[str(repo / "a.py"), "b.py"],
            cwd=str(repo),
        )
        is None
    )
    assert (
        _probe_path_comparison_for_task(
            "compare outside.py and b.py",
            named_paths=["../outside.py", "b.py"],
            cwd=str(repo),
        )
        is None
    )
    link = repo / "link.py"
    try:
        link.symlink_to(outside)
    except OSError:
        link = None
    if link is not None:
        assert (
            _probe_path_comparison_for_task(
                "compare link.py and b.py",
                named_paths=["link.py", "b.py"],
                cwd=str(repo),
            )
            is None
        )


def test_w36b_private_operand_not_embedded(tmp_path):
    # A private operand (internal/) must never be diffed — embedding the
    # unified diff would leak the private file's lines into the envelope.
    internal = tmp_path / "internal"
    internal.mkdir()
    priv = internal / "prism.py"
    priv.write_text("SECRET_PRIVATE_LINE = 42\n")
    pub = tmp_path / "public.py"
    pub.write_text("PUBLIC = 1\n")
    out = _probe_path_comparison_for_task(
        "compare internal/prism.py and public.py",
        named_paths=["internal/prism.py", "public.py"],
        cwd=str(tmp_path),
    )
    assert out is None
    # The same probe on two public files still embeds a diff (guard is
    # scoped, not a blanket disable).
    pub2 = tmp_path / "public2.py"
    pub2.write_text("PUBLIC = 2\n")
    out2 = _probe_path_comparison_for_task(
        "compare public.py and public2.py",
        named_paths=["public.py", "public2.py"],
        cwd=str(tmp_path),
    )
    assert out2 is not None
    assert "PUBLIC = 2" in out2["path_comparison"]["diff"]


def test_w36b_diff_operand_is_private_helper(tmp_path):
    assert _diff_operand_is_private(
        "internal/planning/prism.py", str(tmp_path / "internal/planning/prism.py"), str(tmp_path)
    )
    assert _diff_operand_is_private(".env", str(tmp_path / ".env"), str(tmp_path))
    assert not _diff_operand_is_private(
        "src/roam/plan/compiler.py", str(tmp_path / "src/roam/plan/compiler.py"), str(tmp_path)
    )


def test_w36b_diff_never_shells_out_to_external_binary(tmp_path, monkeypatch):
    # SEAL (TARGET compiler.py:4880): the path-comparison probe must compute
    # its unified diff in-process via difflib. It must NEVER spawn a
    # PATH-resolved `diff` binary — that would put repo operands on the
    # option-parsing boundary and reintroduce a PATH-lookup surface. The
    # behavior tests above still pass if the probe reverts to
    # `subprocess.run(["diff", ...])` on a host that has `diff` installed;
    # this test fails outright, so the invariant cannot silently regress.
    import roam.plan.compiler as mod

    a = tmp_path / "a.py"
    a.write_text("x = 1\ny = 2\n")
    b = tmp_path / "b.py"
    b.write_text("x = 1\ny = 99\n")

    def _no_subprocess_run(*args, **kwargs):
        raise AssertionError(
            "path-comparison probe must not shell out to an external "
            f"binary (subprocess.run args={args!r}); use difflib.unified_diff "
            "in-process on repo-contained, private-checked operands."
        )

    monkeypatch.setattr(mod.subprocess, "run", _no_subprocess_run)

    out = _probe_path_comparison_for_task(
        "compare a.py and b.py",
        named_paths=["a.py", "b.py"],
        cwd=str(tmp_path),
    )
    # Reaching here proves zero subprocess dependency for this path; assert
    # the in-process diff is still correct.
    assert out is not None
    assert not out["path_comparison"]["identical"]
    assert "y = 99" in out["path_comparison"]["diff"]


# ---- W36c symbol-pickaxe probe ----


def test_w36c_pickaxe_finds_introducing_commit(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    subprocess.run(["git", "init", "-q"], check=True)
    subprocess.run(["git", "config", "user.email", "t@t"], check=True)
    subprocess.run(["git", "config", "user.name", "t"], check=True)
    f = tmp_path / "thing.py"
    f.write_text("def my_special_symbol():\n    return 1\n")
    subprocess.run(["git", "add", "thing.py"], check=True)
    subprocess.run(["git", "commit", "-q", "-m", "add my_special_symbol"], check=True)

    out = _probe_symbol_pickaxe_for_task(
        "when was `my_special_symbol` added",
        cwd=str(tmp_path),
    )
    assert out is not None
    h = out["symbol_history"]
    assert h["symbol"] == "my_special_symbol"
    assert "add my_special_symbol" in h["commits"]


def test_w36c_no_backticked_returns_none():
    # vocab matches but no backticked identifier → no probe
    out = _probe_symbol_pickaxe_for_task(
        "when was the auth flow added",
        cwd=None,
    )
    assert out is None


def test_w36c_no_pickaxe_vocab_returns_none():
    out = _probe_symbol_pickaxe_for_task(
        "explain `compile_plan`",
        cwd=None,
    )
    assert out is None


# ---- end-to-end integration ----


def test_w36a_end_to_end_compile_plan(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    (src / "x.py").write_text("def foo(): pass\n")
    tests = tmp_path / "tests"
    tests.mkdir()
    (tests / "test_x.py").write_text("# sibling\n")
    plan = compile_plan(f"write a pytest for {src / 'x.py'}")
    env, label = compile_for_artifact(plan, cwd=str(tmp_path))
    # W-GENLEAN (2026-06-10): test-write synthesis now emits LEAN, not
    # l1_probe. Fable 5 A/B (n=2-3): the rich envelope was token-NEGATIVE
    # (+25%) with an IDENTICAL tool path to vanilla — agents re-read the
    # source regardless, so the sibling/skeleton payload was pure input
    # overhead. Lean keeps forbidden_paths + the "SKIP roam for content
    # writing" starter. (The W36a sibling probe still serves NON-test
    # generation synthesis, which keeps the full/L1 path.)
    assert label == "lean"
    plan_obj = env.get("plan", {})
    assert "SKIP roam for content writing" in plan_obj.get("recommended_first_command", "")
    assert "prefetched_facts" not in plan_obj
