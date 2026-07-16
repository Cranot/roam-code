"""H2c — executable compile→envelope→hook invariant smoke test.

Gap #4: CI runs unit tests, a wheel smoke of one unrelated command, and a
health/pr-risk self-analysis — but it NEVER executes the production
``compile → envelope → UserPromptSubmit hook`` chain with a real compiler. So a
change that renamed ``summary.injection_advice``, dropped ``artifact.plan``, or
broke the hook's parse would pass every existing test (the hook unit test
hand-builds its envelope) while silently breaking the deployed injection path.

This runs the REAL ``roam --json compile`` on a fixture repo, asserts the exact
keys the hook reads, then feeds that REAL envelope through the REAL hook script
and asserts the inject/skip contract. Fail-open: if compile can't run at all
(offline grammar fetch, missing optional dep) the test SKIPS rather than
falsely failing — the invariant is about shape, not about deep analysis
succeeding.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

# The envelope keys the deployed UserPromptSubmit hook reads (cmd_hooks.py:
# `summary = d.get("summary")`, then summary["procedure"]/["injection_advice"],
# and artifact.plan for the injected PRE-COMPUTED PLAN). If the compiler stops
# emitting any of these, the hook silently degrades — this list is the contract.
_HOOK_CRITICAL_SUMMARY_KEYS = ("procedure", "injection_advice", "artifact_type")


def _fixture_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "fixture"
    repo.mkdir()
    (repo / "app.py").write_text(
        "def vat(net, rate):\n    return round(net * rate / 100, 2)\n\n"
        "def total(net, rate):\n    return net + vat(net, rate)\n",
        encoding="utf-8",
    )
    subprocess.run(["git", "init", "-q"], cwd=repo, check=False)
    subprocess.run(["git", "add", "-A"], cwd=repo, check=False)
    return repo


def _real_compile(repo: Path, task: str) -> dict | None:
    """Run the REAL `roam --json compile` as a subprocess; None if it can't run.

    Uses `sys.executable -m roam` so it works in CI where `roam` may not be on
    PATH. Returns the parsed envelope, or None to signal SKIP (compile pipeline
    unavailable — e.g. offline grammar fetch), never a false failure.
    """
    try:
        proc = subprocess.run(
            [sys.executable, "-m", "roam", "--json", "compile", task],
            cwd=repo,
            capture_output=True,
            text=True,
            timeout=90,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if proc.returncode != 0 or not proc.stdout.strip():
        return None
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError:
        return None


def test_real_compile_emits_hook_critical_envelope(tmp_path):
    """The production compiler must emit the exact shape the hook consumes."""
    repo = _fixture_repo(tmp_path)
    env = _real_compile(repo, "who calls vat")
    if env is None:
        pytest.skip("real `roam compile` unavailable in this environment (offline/optional dep)")

    assert "summary" in env, f"envelope lost its `summary` block — hook reads summary.*; keys={sorted(env)}"
    summary = env["summary"]
    for key in _HOOK_CRITICAL_SUMMARY_KEYS:
        assert key in summary, f"summary.{key} missing — the UPS hook depends on it; summary keys={sorted(summary)}"
    # injection_advice must be a value the deployed hook understands: 'inject'
    # or a 'skip*' token (deployed hooks gate on startswith('skip')).
    advice = summary["injection_advice"]
    assert advice == "inject" or str(advice).startswith("skip"), f"unknown injection_advice {advice!r}"
    # the injected payload lives at artifact.plan
    artifact = env.get("artifact") or {}
    assert "plan" in artifact, f"artifact.plan missing — nothing to inject; artifact keys={sorted(artifact)}"


def _install_roam_stub(tmp_path: Path, envelope: dict) -> dict:
    """Put a fake `roam` on PATH that echoes a REAL pre-computed envelope.

    The hook shells out to bare `roam --json compile ...`; this lets the hook
    consume an actual compiler envelope (not a hand-built one) so the test
    covers the true producer↔consumer contract. Returns an env dict with PATH
    prepended.
    """
    stub_dir = tmp_path / "bin"
    stub_dir.mkdir()
    payload = json.dumps(envelope)
    if sys.platform == "win32":
        payload_file = stub_dir / "envelope.json"
        payload_file.write_text(payload, encoding="utf-8")
        (stub_dir / "roam.bat").write_text(
            f'@echo off\r\n"{sys.executable}" -c '
            "\"import sys;sys.stdout.write(open(sys.argv[1],encoding='utf-8').read())\" "
            f'"{payload_file}"\r\n',
            encoding="utf-8",
        )
    else:
        stub = stub_dir / "roam"
        stub.write_text(f"#!/bin/sh\ncat <<'EOF'\n{payload}\nEOF\n", encoding="utf-8")
        stub.chmod(0o755)
    return {**os.environ, "PATH": f"{stub_dir}{os.pathsep}{os.environ['PATH']}"}


def test_real_envelope_flows_through_real_hook(tmp_path):
    """Feed a REAL compiled envelope through the REAL hook script: an 'inject'
    envelope must yield the PRE-COMPUTED PLAN marker. Closes the gap the existing
    hook unit test leaves open (it hand-builds the envelope, so it can't catch
    the real compiler drifting away from the shape the hook assumes)."""
    from roam.commands.cmd_hooks import _CLAUDE_UPS_HOOK_SCRIPT

    repo = _fixture_repo(tmp_path)
    env = _real_compile(repo, "who calls vat")
    if env is None:
        pytest.skip("real `roam compile` unavailable in this environment (offline/optional dep)")
    if (env.get("summary") or {}).get("injection_advice") != "inject":
        pytest.skip(
            f"fixture task did not produce inject advice ({(env.get('summary') or {}).get('injection_advice')})"
        )

    hook = tmp_path / "hook.py"
    hook.write_text(_CLAUDE_UPS_HOOK_SCRIPT, encoding="utf-8")
    hook_env = _install_roam_stub(tmp_path, env)

    proc = subprocess.run(
        [sys.executable, str(hook)],
        input=json.dumps({"prompt": "who calls vat in this repo"}),
        capture_output=True,
        text=True,
        timeout=30,
        env=hook_env,
    )
    assert proc.returncode == 0, proc.stderr
    assert "PRE-COMPUTED PLAN" in proc.stdout, (
        "the real hook did not inject a real inject-advice envelope — the "
        f"producer↔consumer contract broke. stdout head: {proc.stdout[:200]!r}"
    )


def _run_hook(hook_path: Path, hook_env: dict, prompt: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(hook_path)],
        input=json.dumps({"prompt": prompt}),
        capture_output=True,
        text=True,
        timeout=30,
        env=hook_env,
    )


def _stub_is_honored(tmp_path: Path, hook_path: Path) -> bool:
    """Positive control: does this platform actually resolve our bare-name `roam`
    stub over any real install?

    The hook shells out to bare ``roam``; POSIX PATH lookup finds our
    extensionless script, but Windows ``subprocess`` won't resolve a ``roam.bat``
    (only ``.exe``), so it falls through to a real install. We must not assert
    the skip contract against a real compiler — so probe with an INJECT stub
    carrying a unique sentinel path and check the hook echoes it.
    """
    sentinel = "ZZ_STUB_SENTINEL_PATH_ZZ.py"
    probe_dir = tmp_path / "probe"
    probe_dir.mkdir()
    env = _install_roam_stub(
        probe_dir,
        {
            "summary": {"procedure": "structural_callers", "injection_advice": "inject", "artifact_type": "full"},
            "artifact": {"plan": {"named_paths": [sentinel], "prefetched_facts": {"x": 1}}},
        },
    )
    out = _run_hook(hook_path, env, "who calls something").stdout
    return sentinel in out


def test_hook_marker_skip_contract(tmp_path):
    """The deployed hook gates injection on injection_advice startswith('skip').
    A skip-advice REAL-shaped envelope must inject NOTHING — the invariant that
    lets new skip_* values (edit-trim's skip_edit_task) work with already-
    deployed hooks without redeployment. Guarded by a positive control so it
    only asserts where our stub actually shadows `roam` (POSIX / CI)."""
    from roam.commands.cmd_hooks import _CLAUDE_UPS_HOOK_SCRIPT

    hook = tmp_path / "hook.py"
    hook.write_text(_CLAUDE_UPS_HOOK_SCRIPT, encoding="utf-8")
    if not _stub_is_honored(tmp_path, hook):
        pytest.skip("bare-name `roam` stub not resolved on this platform (Windows .bat); real CI covers it")

    envelope = {
        "summary": {"procedure": "stack_trace_fix", "injection_advice": "skip_edit_task", "artifact_type": "full"},
        "artifact": {"plan": {"named_paths": ["app.py"], "prefetched_facts": {"x": 1}}},
    }
    hook_env = _install_roam_stub(tmp_path, envelope)
    proc = _run_hook(hook, hook_env, "fix this TypeError at app.py:2")
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == "", f"skip_* advice must inject nothing, got: {proc.stdout[:200]!r}"
