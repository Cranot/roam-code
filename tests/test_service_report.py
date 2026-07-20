"""Tests for ``roam service-report`` — the productised service-engagement command.

``service-report`` is the sibling of ``roam pr-replay``: it turns the four
services-report templates (due-diligence, AI-readiness, reachability-triage,
post-incident) into one-command deliverables. Each ``--type`` runs the right
existing Roam primitives, aggregates their JSON envelopes, and emits a
buyer-facing Markdown narrative.

Tests focus on:
* Each ``--type`` renders a distinct, well-formed report (fast, via stubbed
  primitives — the real subprocess path is exercised by one slow test).
* Every report carries the honest disclaimer banner and is wording-guard
  clean (no ``certifies`` / ``guaranteed`` / ``compliant`` outside a
  negation window — the W184 / W203 discipline).
* The JSON envelope is parseable and carries the expected keys.
* ``--output`` writes the markdown; ``--type`` is required.
* The engagement ledger appends, not overwrites.

The primitive calls (``_run_roam_json`` / ``_run_postmortem``) are stubbed
with canned envelopes shaped like the real command output captured from
roam-code, so the render/dispatch/envelope path is tested deterministically
and fast. One ``@pytest.mark.slow`` test runs the real end-to-end path.
"""

from __future__ import annotations

import io as _io
import json as _json
import os as _os
import subprocess as _subprocess
import sys as _sys
import time as _time
from pathlib import Path

import pytest
from click.testing import CliRunner

from tests._helpers.wording_lint import scan_for_overclaims

# ---------------------------------------------------------------------------
# Canned envelopes — shaped like the real ``roam --json <cmd>`` output.
# ---------------------------------------------------------------------------

_CANNED: dict[str, dict] = {
    "health": {
        "summary": {
            "verdict": "Fair codebase (75/100) — 47 critical, 9 warnings",
            "health_score": 75,
            "cycles_total": 10,
            "cycles_actionable": 2,
            "god_components": 50,
            "tangle_ratio": 0.0,
        }
    },
    "bus-factor": {
        "summary": {
            "verdict": "bus factor 1 (min), 58 high-risk, 63 single-owner modules",
            "high_risk": 58,
            "solo_authored_count": 63,
            "directories_analyzed": 63,
        }
    },
    "complexity": {
        "summary": {
            "verdict": "avg complexity 3.7, 867 critical, 781 high",
            "average_complexity": 3.7,
            "p90_complexity": 9.0,
            "critical_count": 867,
            "total_analyzed": 25537,
        }
    },
    "dead": {
        "summary": {
            "verdict": "579 dead export(s): 31 safe, 384 review, 164 intentional",
            "files_affected": 162,
            "total_effort_hours": 1225.6,
        }
    },
    "clones": {
        "summary": {
            "verdict": "115 clone clusters found (646 functions, 79% avg similarity)",
            "estimated_reducible_lines": 36681,
        }
    },
    "smells": {
        "summary": {
            "verdict": "Needs refactoring: 4367 smells (113 critical, 2508 warning) in 957 files",
            "total_smells": 4367,
            "files_affected": 957,
        }
    },
    "test-pyramid": {
        "summary": {
            "verdict": "MOSTLY-UNSTRUCTURED — 1130 of 1145 test files have no kind hint",
            "total": 1145,
            "unit": 0,
            "integration": 5,
            "e2e": 4,
        }
    },
    "sbom": {
        "summary": {
            "verdict": "4 reachable (4 direct, 0 heuristic), 19 phantom",
            "total_dependencies": 23,
            "reachable_count": 4,
            "reachable_direct_count": 4,
            "phantom_count": 19,
        }
    },
    "supply-chain": {
        "summary": {
            "verdict": "Supply chain risky (36/100) -- 0 unpinned dependencies",
            "risk_score": 36,
            "pin_coverage_pct": 0.0,
            "unpinned_count": 0,
            "total_dependencies": 23,
        }
    },
    "vulns": {
        "summary": {
            "verdict": "no vulnerability scan available (vulnerabilities table is empty)",
            "total": 0,
        }
    },
    "vuln-reach": {
        "summary": {
            "verdict": "No vulnerabilities ingested. Run vuln-map first.",
            "reachable_count": 0,
            "total_vulns": 0,
        }
    },
    "taint": {
        "summary": {
            "verdict": "No taint findings across 22 rule(s)",
            "findings": 0,
            "rules": 22,
            "risk_score": 0,
        }
    },
    "secrets": {
        "summary": {
            "verdict": "No secrets found",
            "total_findings": 0,
        }
    },
    "architecture-drift": {
        "summary": {
            "verdict": "Need at least 2 snapshots within window — found 1",
            "state": "insufficient_snapshots",
        }
    },
    "ai-readiness": {
        "summary": {
            "verdict": "AI Readiness 57/100 -- FAIR",
            "score": 57,
            "label": "FAIR",
        },
        "dimensions": [
            {
                "label": "Naming consistency",
                "name": "naming_consistency",
                "score": 100,
                "weight": 15,
                "contribution": 15.0,
            },
            {"label": "Module coupling", "name": "module_coupling", "score": 100, "weight": 20, "contribution": 20.0},
            {"label": "Dead code noise", "name": "dead_code_noise", "score": 0, "weight": 15, "contribution": 0.0},
            {
                "label": "Test signal strength",
                "name": "test_signal_strength",
                "score": 9,
                "weight": 20,
                "contribution": 1.8,
            },
        ],
        "recommendations": [
            "Remove 469 dead exports to reduce agent confusion",
            "Increase test coverage mapping (currently 9%)",
            "Break 10 dependency cycles",
        ],
    },
    "ai-ratio": {
        "summary": {
            "verdict": "~59% estimated AI-generated code (confidence: HIGH)",
            "ai_ratio": 0.59,
            "confidence": "HIGH",
            "commits_analyzed": 406,
        }
    },
    "agent-score": {
        "summary": {
            "verdict": "Scored 16 agents; top: recheck 99.6/100 over 2 runs",
            "agents_scored": 16,
            "count": 16,
        }
    },
    "mode": {
        "summary": {
            "verdict": "active mode: safe_edit (13 allowed commands)",
            "active_mode": "safe_edit",
            "allowed_count": 13,
            "policy_source": "default+constitution",
            "persisted": False,
        }
    },
    "audit-trail-verify": {
        "summary": {
            "verdict": "chain valid (9 records)",
            "chain_valid": True,
            "chain_tier": "CHAIN_VERIFIED",
            "total_records": 9,
            "unsigned_events": 0,
        }
    },
}

_CANNED_POSTMORTEM = {
    "summary": {
        "verdict": "11 of 20 commits would have surfaced findings",
        "commits_scanned": 20,
        "commits_with_findings": 11,
    },
    "commits": [
        {
            "date": "2026-05-21",
            "short_sha": "4b8c61c9",
            "subject": "refactor: make fallback chains loud",
            "high": 0,
            "medium": 12,
            "kinds": ["impact x12"],
        },
        {
            "date": "2026-05-22",
            "short_sha": "83f5c44c",
            "subject": "fix: dogfood-v2 defects",
            "high": 1,
            "medium": 3,
            "kinds": ["impact x2", "intent x1"],
        },
        {
            "date": "2026-05-21",
            "short_sha": "2852c675",
            "subject": "chore(release): v13.4",
            "high": 0,
            "medium": 0,
            "kinds": [],
        },
    ],
}


@pytest.fixture
def stub_primitives(monkeypatch):
    """Stub the primitive-invocation helpers with canned envelopes.

    Makes the render/dispatch/envelope path deterministic and fast — no
    subprocess, no index build. ``ensure_index`` is neutralised so the
    command doesn't try to (re)build an index during the render test.
    """
    from roam.commands import cmd_service_report as mod

    def _fake_run_roam_json(args, **_kwargs):
        return _CANNED.get(args[0], {})

    def _fake_run_postmortem(commit_range, *, limit=100):
        return _CANNED_POSTMORTEM

    monkeypatch.setattr(mod, "_run_roam_json", _fake_run_roam_json)
    monkeypatch.setattr(mod, "_run_postmortem", _fake_run_postmortem)
    monkeypatch.setattr(mod, "ensure_index", lambda *a, **k: None)
    return mod


def _invoke(*args: str, json_mode: bool = False) -> tuple[int, str]:
    from roam.cli import cli

    runner = CliRunner()
    cli_args = (["--json"] if json_mode else []) + ["service-report", *args]
    result = runner.invoke(cli, cli_args, catch_exceptions=False)
    return result.exit_code, result.output


ALL_TYPES = ["due-diligence", "ai-readiness", "reachability-triage", "post-incident"]


# ---------------------------------------------------------------------------
# Smoke tests — every type produces a valid, banner-carrying report.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("rtype", ALL_TYPES)
def test_each_type_renders_a_report(stub_primitives, rtype):
    code, out = _invoke("--type", rtype)
    assert code == 0, f"{rtype} exited non-zero: {out[:300]}"
    # Title line
    assert out.startswith("# "), f"{rtype}: report must start with an H1 title"
    # Honest disclaimer banner
    assert "Engineering evidence, not an attestation." in out
    assert "does not certify" in out
    # Tool attribution line
    assert f"roam service-report --type {rtype}" in out
    # "What this does not cover" section present
    assert "## What this report does not cover" in out
    # Paid framing present
    assert "About this engagement" in out


@pytest.mark.parametrize("rtype", ALL_TYPES)
def test_each_type_is_wording_guard_clean(stub_primitives, rtype):
    """No compliance-overclaim wording outside a negation window (W184/W203)."""
    code, out = _invoke("--type", rtype)
    assert code == 0
    violations = scan_for_overclaims(out)
    assert not violations, f"{rtype} report has wording-guard violations: {violations}"


def test_due_diligence_surfaces_health_verdict(stub_primitives):
    code, out = _invoke("--type", "due-diligence")
    assert code == 0
    assert "Codebase Due Diligence Report" in out
    assert "health 75/100" in out
    assert "roam health" in out
    assert "roam bus-factor" in out
    # Real numbers from the canned health/clones envelopes land in the report
    assert "36681" in out  # estimated reducible lines from clones


def test_reachability_triage_leads_with_the_wedge(stub_primitives):
    code, out = _invoke("--type", "reachability-triage")
    assert code == 0
    assert "Security Reachability Triage" in out
    assert "reachable from scanner noise" in out
    # Dependency reachability signal (4 of 23) present
    assert "4 of 23" in out or ("Reachable | 4" in out and "Total dependencies | 23" in out)


def test_ai_readiness_renders_dimension_table(stub_primitives):
    code, out = _invoke("--type", "ai-readiness")
    assert code == 0
    assert "AI Adoption Readiness Audit" in out
    assert "57/100" in out
    # Dimension rows land
    assert "Naming consistency" in out
    assert "Module coupling" in out
    # Recommendations land
    assert "Break 10 dependency cycles" in out


def test_post_incident_replays_the_range(stub_primitives):
    code, out = _invoke("--type", "post-incident", "--range", "HEAD~20..HEAD")
    assert code == 0
    assert "Post-Incident Replay Report" in out
    assert "HEAD~20..HEAD" in out
    assert "Commits replayed: **20**" in out
    # A flagged commit from the canned postmortem lands in the table
    assert "4b8c61c9" in out
    # Audit-trail integrity section present
    assert "chain valid (9 records)" in out
    assert "CHAIN_VERIFIED" in out


# ---------------------------------------------------------------------------
# JSON envelope
# ---------------------------------------------------------------------------


def test_json_envelope_is_well_formed(stub_primitives):
    code, out = _invoke("--type", "reachability-triage", json_mode=True)
    assert code == 0
    envelope = _json.loads(out[out.find("{") :])
    assert envelope["command"] == "service-report"
    assert "summary" in envelope
    summary = envelope["summary"]
    assert summary["report_type"] == "reachability-triage"
    assert "verdict" in summary
    assert "generated_at" in summary
    assert "sections_present" in summary
    assert summary["sections_failed"] == []
    assert summary["sections_degraded"] == []
    assert summary["state"] == "complete"
    assert summary["partial_success"] is False
    # Body
    assert isinstance(envelope.get("sections"), dict)
    assert isinstance(envelope.get("report_markdown"), str)
    assert envelope["report_markdown"].startswith("# ")


def test_json_envelope_sections_present_lists_run_commands(stub_primitives):
    code, out = _invoke("--type", "due-diligence", json_mode=True)
    assert code == 0
    envelope = _json.loads(out[out.find("{") :])
    present = envelope["summary"]["sections_present"]
    assert "health" in present
    assert "clones" in present


def test_component_failure_is_visible_in_text_and_json(stub_primitives, monkeypatch):
    """One absent primitive cannot collapse into a successful-looking report."""
    mod = stub_primitives

    def _partially_failed(args, **_kwargs):
        if args[0] == "health":
            return mod._component_failure("health", "component_timeout", "timed out after 180s")
        return _CANNED.get(args[0], {})

    monkeypatch.setattr(mod, "_run_roam_json", _partially_failed)
    code, text_report = _invoke("--type", "due-diligence")
    assert code == 0
    assert "**Partial report:**" in text_report
    assert "`health`" in text_report

    code, output = _invoke("--type", "due-diligence", json_mode=True)
    assert code == 0
    envelope = _json.loads(output[output.find("{") :])
    summary = envelope["summary"]
    assert summary["partial_success"] is True
    assert summary["state"] == "component_failure"
    assert summary["sections_failed"] == ["health"]
    assert "partial report" in summary["verdict"]
    assert envelope["sections"]["health"]["isError"] is True


@pytest.mark.parametrize("raises", [False, True])
def test_pdf_render_failure_is_an_explicit_partial_undelivered_artifact(
    stub_primitives,
    tmp_path,
    monkeypatch,
    raises,
):
    mod = stub_primitives
    requested = tmp_path / "buyer-report.pdf"
    leaked_detail = "renderer failed at C:\\private\\buyer with token-secret"

    def _fail_render(*_args, **_kwargs):
        if raises:
            raise RuntimeError(leaked_detail)
        return False, leaked_detail

    monkeypatch.setattr(mod, "_render_pdf", _fail_render)

    code, output = _invoke(
        "--type",
        "ai-readiness",
        "--pdf",
        str(requested),
        "--no-track-engagement",
        json_mode=True,
    )

    assert code == 0
    assert leaked_detail not in output
    assert "requested artifact was not delivered" in output
    envelope = _json.loads(output[output.find("{") :])
    summary = envelope["summary"]
    assert summary["state"] == "artifact_failure"
    assert summary["partial_success"] is True
    assert summary["pdf_requested_path"] == str(requested)
    assert summary["pdf_path"] is None
    assert summary["pdf_backend"] is None
    assert summary["pdf_state"] == "render_failed"
    assert summary["pdf_failure"] == "pdf_render_failed"
    assert summary["artifact_failures"] == ["pdf_render_failed"]
    assert "partial delivery" in summary["verdict"]
    assert not requested.exists()


# ---------------------------------------------------------------------------
# Component process boundary
# ---------------------------------------------------------------------------


def _pid_is_alive(pid: int) -> bool:
    if _os.name != "nt":
        try:
            _os.kill(pid, 0)
            return True
        except ProcessLookupError:
            return False
        except OSError:
            return True
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel32.WaitForSingleObject.restype = wintypes.DWORD
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL
    handle = kernel32.OpenProcess(0x00100000, False, pid)  # SYNCHRONIZE
    if not handle:
        return False
    try:
        return kernel32.WaitForSingleObject(handle, 0) == 0x00000102  # WAIT_TIMEOUT
    finally:
        kernel32.CloseHandle(handle)


def _force_kill_pid(pid: int) -> None:
    if not _pid_is_alive(pid):
        return
    if _os.name != "nt":
        import signal

        try:
            _os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        return
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
    kernel32.TerminateProcess.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL
    handle = kernel32.OpenProcess(0x0001, False, pid)  # PROCESS_TERMINATE
    if handle:
        try:
            kernel32.TerminateProcess(handle, 1)
        finally:
            kernel32.CloseHandle(handle)


def _component_test_env(mod) -> dict[str, str]:
    """Make source-checkout subprocesses importable on every test host."""
    env = dict(_os.environ)
    source_root = str(mod.Path(mod.__file__).resolve().parents[2])
    env["PYTHONPATH"] = _os.pathsep.join(value for value in (source_root, env.get("PYTHONPATH")) if value)
    return env


def test_component_runner_uses_literal_isolated_argv(monkeypatch):
    from roam.commands import cmd_service_report as mod

    # This test isolates argv construction with a fake Popen. The real Linux
    # supervisor boundary is exercised by the escaped-descendant regression.
    if mod._sys.platform.startswith("linux"):
        monkeypatch.setattr(mod._sys, "platform", "darwin")

    seen = {}

    class _Process:
        returncode = 0
        pid = 12345
        stdin = _io.BytesIO()
        stdout = _io.BytesIO(b'progress\n{"summary":{"verdict":"ok"}}')
        stderr = _io.BytesIO()

        def poll(self):
            return self.returncode

        def communicate(self, timeout):  # pragma: no cover - regression sentinel
            raise AssertionError(f"post-hoc communicate({timeout}) must not be used")

    def _popen(argv, **kwargs):
        seen["argv"] = argv
        seen["kwargs"] = kwargs
        return _Process()

    monkeypatch.setattr(mod._subprocess, "Popen", _popen)
    monkeypatch.setattr(mod, "_create_windows_component_job", lambda: 99)
    monkeypatch.setattr(mod, "_assign_windows_component_job", lambda handle, proc: handle == 99)
    monkeypatch.setattr(mod, "_close_windows_component_job_handle", lambda handle: handle == 99)
    monkeypatch.setattr(mod, "_terminate_component_process_tree", lambda proc: True)
    result = mod._run_roam_json(["version"])

    assert seen["argv"][-4:] == ["-m", "roam", "--json", "version"]
    assert seen["kwargs"]["shell"] is False
    expected_stdin = mod._subprocess.PIPE if mod._os.name == "nt" else mod._subprocess.DEVNULL
    assert seen["kwargs"]["stdin"] is expected_stdin
    assert result["summary"]["verdict"] == "ok"


def test_component_runner_terminates_process_tree_on_timeout(monkeypatch):
    from roam.commands import cmd_service_report as mod

    class _TimedOutProcess:
        returncode = None
        stdout = _io.BytesIO()
        stderr = _io.BytesIO()

    proc = _TimedOutProcess()
    monkeypatch.setattr(mod, "_start_component_process", lambda *a, **k: proc)
    monkeypatch.setattr(mod, "_wait_for_component_root", lambda *a, **k: "timeout")
    terminated = []
    monkeypatch.setattr(mod, "_terminate_component_process_tree", lambda value: terminated.append(value) or True)

    result = mod._run_roam_json(["clones"])

    assert terminated == [proc]
    assert result["isError"] is True
    assert result["summary"]["state"] == "component_timeout"
    assert result["summary"]["partial_success"] is True


def test_component_runner_enforces_one_bounded_budget_across_both_pipes(monkeypatch):
    from roam.commands import cmd_service_report as mod

    read_sizes = []

    class _TrackingStream(_io.BytesIO):
        def read1(self, size=-1):
            read_sizes.append(size)
            return self.read(size)

    class _Process:
        returncode = 0
        stdout = _TrackingStream(b"o" * 700)
        stderr = _TrackingStream(b"e" * 700)

        def poll(self):
            return self.returncode

        def communicate(self, timeout):  # pragma: no cover - regression sentinel
            raise AssertionError(f"post-hoc communicate({timeout}) must not be used")

    proc = _Process()
    terminated = []
    monkeypatch.setattr(mod, "_COMPONENT_MAX_OUTPUT_BYTES", 1024)
    monkeypatch.setattr(mod, "_COMPONENT_CAPTURE_CHUNK_BYTES", 128)
    monkeypatch.setattr(mod, "_start_component_process", lambda *a, **k: proc)
    monkeypatch.setattr(mod, "_terminate_component_process_tree", lambda value: terminated.append(value) or True)

    result = mod._run_roam_json(["health"])

    assert result["summary"]["state"] == "component_output_oversized"
    assert terminated == [proc]
    assert read_sizes
    assert max(read_sizes) == 128


def test_component_runner_stops_live_noisy_child_before_post_hoc_timeout(monkeypatch):
    from roam.commands import cmd_service_report as mod

    real_popen = mod._subprocess.Popen
    children = []
    script = (
        "import sys,time; "
        "sys.stdout.buffer.write(b'o'*768); sys.stdout.buffer.flush(); "
        "sys.stderr.buffer.write(b'e'*768); sys.stderr.buffer.flush(); "
        "time.sleep(30)"
    )

    def _noisy_start(_argv, *, cwd, env):
        proc = real_popen(
            [mod._sys.executable, "-c", script],
            cwd=cwd,
            env=env,
            shell=False,
            stdin=mod._subprocess.DEVNULL,
            stdout=mod._subprocess.PIPE,
            stderr=mod._subprocess.PIPE,
            close_fds=True,
            **mod._component_popen_kwargs(),
        )
        children.append(proc)
        return proc

    def _terminate(proc):
        proc.kill()
        proc.wait(timeout=5)
        return True

    monkeypatch.setattr(mod, "_COMPONENT_MAX_OUTPUT_BYTES", 1024)
    monkeypatch.setattr(mod, "_COMPONENT_TIMEOUT_SECONDS", 2)
    monkeypatch.setattr(mod, "_start_component_process", _noisy_start)
    monkeypatch.setattr(mod, "_terminate_component_process_tree", _terminate)

    result = mod._run_roam_json(["health"])

    assert result["summary"]["state"] == "component_output_oversized"
    assert "process tree terminated" in result["summary"]["verdict"]
    assert len(children) == 1
    assert children[0].poll() is not None


def test_component_capture_kills_pipe_inheriting_descendant_after_root_exit(tmp_path):
    from roam.commands import cmd_service_report as mod

    pid_path = tmp_path / "descendant.pid"
    child_code = "import time; time.sleep(3)"
    root_code = (
        "import pathlib,subprocess,sys; "
        "child=subprocess.Popen([sys.executable,'-c',sys.argv[1]], "
        "stdout=sys.stdout,stderr=sys.stderr,close_fds=False); "
        "pathlib.Path(sys.argv[2]).write_text(str(child.pid),encoding='ascii'); "
        "print(child.pid,flush=True)"
    )
    child_pid = None
    try:
        proc = mod._start_component_process(
            [mod._sys.executable, "-c", root_code, child_code, str(pid_path)],
            cwd=str(tmp_path),
            env=_component_test_env(mod),
        )
        launch_deadline = _time.perf_counter() + 15.0
        while not pid_path.exists() and proc.poll() is None and _time.perf_counter() < launch_deadline:
            _time.sleep(0.02)
        assert pid_path.exists(), proc.returncode
        capture_started = _time.perf_counter()
        stdout, stderr, state, tree_terminated, capture_error = mod._capture_component_output(
            proc,
            timeout_seconds=1.0,
        )
        child_pid = int(stdout.decode("ascii").strip())

        assert state == "completed"
        assert tree_terminated is True
        assert capture_error is None
        assert stderr == b""
        assert _time.perf_counter() - capture_started < 2.5
        deadline = _time.perf_counter() + 1.0
        while _pid_is_alive(child_pid) and _time.perf_counter() < deadline:
            _time.sleep(0.02)
        assert not _pid_is_alive(child_pid)
    finally:
        if child_pid is None and pid_path.exists():
            child_pid = int(pid_path.read_text(encoding="ascii"))
        if child_pid is not None:
            _force_kill_pid(child_pid)


def test_linux_component_capture_kills_setsid_descendant_with_redirected_pipes(tmp_path):
    from roam.commands import cmd_service_report as mod

    if not mod._sys.platform.startswith("linux"):
        pytest.skip("Linux subreaper and pidfd regression")

    identity_path = tmp_path / "escaped-descendant.identity"
    child_code = """
import os
import pathlib
import sys
import time

os.setsid()
devnull = os.open(os.devnull, os.O_RDWR)
for stream_fd in (0, 1, 2):
    os.dup2(devnull, stream_fd)
if devnull > 2:
    os.close(devnull)
pathlib.Path(sys.argv[1]).write_text(
    f"{os.getpid()}:{os.getsid(0)}",
    encoding="ascii",
)
time.sleep(30)
"""
    root_code = """
import pathlib
import subprocess
import sys
import time

identity_path = pathlib.Path(sys.argv[2])
child = subprocess.Popen(
    [sys.executable, "-c", sys.argv[1], str(identity_path)],
    close_fds=True,
)
deadline = time.monotonic() + 2
while not identity_path.exists() and time.monotonic() < deadline:
    time.sleep(0.01)
print(child.pid, flush=True)
"""
    child_pid = None
    started = _time.perf_counter()
    try:
        proc = mod._start_component_process(
            [mod._sys.executable, "-c", root_code, child_code, str(identity_path)],
            cwd=str(tmp_path),
            env=_component_test_env(mod),
        )
        launch_deadline = _time.perf_counter() + 15.0
        while not identity_path.exists() and proc.poll() is None and _time.perf_counter() < launch_deadline:
            _time.sleep(0.02)
        launch_error = b""
        if not identity_path.exists() and proc.poll() is not None:
            launch_error = proc.stderr.read()
        assert identity_path.exists(), (proc.returncode, launch_error)
        stdout, stderr, state, tree_terminated, capture_error = mod._capture_component_output(
            proc,
            timeout_seconds=3.0,
        )
        assert state == "completed", (state, tree_terminated, capture_error, stderr)
        assert tree_terminated is True
        assert capture_error is None
        assert stderr == b""
        assert stdout
        child_pid = int(stdout.decode("ascii").strip())
        recorded_pid, recorded_sid = (int(value) for value in identity_path.read_text(encoding="ascii").split(":"))

        assert recorded_pid == child_pid
        assert recorded_sid == child_pid
        assert _time.perf_counter() - started < 20.0
        deadline = _time.perf_counter() + 1.0
        while _pid_is_alive(child_pid) and _time.perf_counter() < deadline:
            _time.sleep(0.02)
        assert not _pid_is_alive(child_pid)
    finally:
        if child_pid is None and identity_path.exists():
            child_pid = int(identity_path.read_text(encoding="ascii").split(":", 1)[0])
        if child_pid is not None:
            _force_kill_pid(child_pid)


def test_linux_pidfd_identity_rejects_recycled_numeric_pid(monkeypatch):
    from roam.commands import cmd_service_report as mod

    stats = iter(((7, 111, "S"), (7, 222, "S")))
    pidfd, keepalive_fd = _os.pipe()
    monkeypatch.setattr(mod, "_linux_process_stat", lambda _pid: next(stats))
    monkeypatch.setattr(mod._os, "pidfd_open", lambda _pid, _flags: pidfd, raising=False)
    try:
        assert mod._linux_open_pidfd_identity(4242) is None
        with pytest.raises(OSError):
            _os.fstat(pidfd)
    finally:
        _os.close(keepalive_fd)


def test_linux_pidfd_identity_rejects_exited_bound_process(monkeypatch):
    from roam.commands import cmd_service_report as mod

    pidfd, keepalive_fd = _os.pipe()
    monkeypatch.setattr(mod, "_linux_process_stat", lambda _pid: (7, 111, "S"))
    monkeypatch.setattr(mod._os, "pidfd_open", lambda _pid, _flags: pidfd, raising=False)
    monkeypatch.setattr(mod, "_linux_pidfd_is_alive", lambda _pidfd: False)
    try:
        assert mod._linux_open_pidfd_identity(4242) is None
        with pytest.raises(OSError):
            _os.fstat(pidfd)
    finally:
        _os.close(keepalive_fd)


def test_linux_cleanup_receipt_reports_explicit_degraded_reason():
    from roam.commands import cmd_service_report as mod

    status_fd, writer_fd = _os.pipe()

    class _ExitedSupervisor:
        _roam_component_status_fd = status_fd

    proc = _ExitedSupervisor()
    try:
        _os.write(writer_fd, mod._LINUX_CONTAINMENT_CLEANUP_FAILED)
    finally:
        _os.close(writer_fd)

    assert mod._read_linux_supervisor_receipt(proc) is False
    assert proc._roam_component_status_fd is None
    assert proc._roam_component_containment_error == "linux_containment_cleanup_failed"


def test_component_capture_never_closes_pipes_under_live_readers(monkeypatch):
    from roam.commands import cmd_service_report as mod

    release = mod._threading.Event()

    class _BlockingStream:
        close_called = False

        def read1(self, _size):
            release.wait(2)
            return b""

        read = read1

        def close(self):
            self.close_called = True
            raise AssertionError("cross-thread pipe close must not run")

    class _ExitedRoot:
        returncode = 0
        stdout = _BlockingStream()
        stderr = _BlockingStream()

        def poll(self):
            return self.returncode

    proc = _ExitedRoot()
    monkeypatch.setattr(mod, "_COMPONENT_CLEANUP_SECONDS", 0.02)
    # Even a lying/buggy lower-level terminator must not become a true receipt
    # while inherited writer handles prove that the tree is still alive.
    monkeypatch.setattr(mod, "_terminate_component_process_tree", lambda value: True)
    started = _time.perf_counter()
    try:
        _stdout, _stderr, state, tree_terminated, error = mod._capture_component_output(
            proc,
            timeout_seconds=0.02,
        )
        assert _time.perf_counter() - started < 0.5
        assert state == "timeout"
        assert tree_terminated is False
        assert error == "process_tree_unverified"
        assert proc.stdout.close_called is False
        assert proc.stderr.close_called is False
    finally:
        release.set()


def test_posix_group_cleanup_targets_saved_group_after_root_exit(monkeypatch):
    from roam.commands import cmd_service_report as mod

    calls = []

    def _killpg(pgid, sig):
        calls.append((pgid, sig))
        if sig == 0:
            raise ProcessLookupError

    class _ExitedRoot:
        returncode = 0
        _roam_component_pgid = 4242

        def poll(self):
            return self.returncode

    monkeypatch.setattr(mod._os, "killpg", _killpg, raising=False)
    monkeypatch.setattr(mod._signal, "SIGKILL", 9, raising=False)

    assert mod._terminate_posix_component_group(_ExitedRoot()) is True
    assert calls == [(4242, 9), (4242, 0)]


def test_non_linux_posix_cleanup_returns_degraded_receipt(monkeypatch):
    from roam.commands import cmd_service_report as mod

    proc = object()
    monkeypatch.setattr(mod._os, "name", "posix")
    monkeypatch.setattr(mod._sys, "platform", "darwin")
    monkeypatch.setattr(mod, "_terminate_posix_component_group", lambda value: value is proc)
    monkeypatch.setattr(
        mod,
        "_terminate_linux_component_supervisor",
        lambda _value: pytest.fail("non-Linux POSIX cleanup must not use the Linux supervisor"),
    )
    monkeypatch.setattr(
        mod,
        "_terminate_windows_component_job",
        lambda _value: pytest.fail("POSIX cleanup must not use a Windows Job"),
    )

    assert mod._terminate_component_process_tree(proc) is None


def test_non_linux_posix_degraded_cleanup_preserves_success(monkeypatch):
    from roam.commands import cmd_service_report as mod

    class _Process:
        returncode = 0
        stdout = _io.BytesIO(b'{"summary":{"verdict":"ok"}}')
        stderr = _io.BytesIO()

        def poll(self):
            return self.returncode

    proc = _Process()
    monkeypatch.setattr(mod, "_start_component_process", lambda *args, **kwargs: proc)
    monkeypatch.setattr(mod, "_terminate_component_process_tree", lambda value: None if value is proc else False)

    result = mod._run_roam_json(["health"])

    assert result["summary"]["verdict"] == "ok"
    assert result["_meta"]["service_report_component_cleanup"] == (
        "process_group_terminated_descendant_proof_unavailable"
    )


def test_platform_degraded_cleanup_reaches_markdown_and_outer_json(stub_primitives, monkeypatch):
    mod = stub_primitives

    def _degraded_component(args, **_kwargs):
        value = _json.loads(_json.dumps(_CANNED.get(args[0], {})))
        if args[0] == "ai-readiness":
            value["_meta"] = {
                "service_report_component_cleanup": ("process_group_terminated_descendant_proof_unavailable")
            }
        return value

    monkeypatch.setattr(mod, "_run_roam_json", _degraded_component)

    code, markdown = _invoke("--type", "ai-readiness")
    assert code == 0
    assert "**Degraded evidence:**" in markdown
    assert "`readiness`" in markdown

    code, output = _invoke("--type", "ai-readiness", json_mode=True)
    assert code == 0
    envelope = _json.loads(output[output.find("{") :])
    summary = envelope["summary"]
    assert summary["sections_degraded"] == ["readiness"]
    assert summary["state"] == "component_degraded"
    assert summary["partial_success"] is True
    assert "degraded evidence" in summary["verdict"]


def test_non_linux_posix_degraded_timeout_reports_limited_proof(monkeypatch):
    from roam.commands import cmd_service_report as mod

    proc = object()
    monkeypatch.setattr(mod, "_start_component_process", lambda *args, **kwargs: proc)
    monkeypatch.setattr(
        mod,
        "_capture_component_output",
        lambda value, *, timeout_seconds: (b"", b"", "timeout", None, None),
    )

    result = mod._run_roam_json(["health"])

    assert result["summary"]["state"] == "component_timeout"
    assert "process group terminated; descendant proof unavailable" in result["summary"]["verdict"]
    assert "process tree terminated" not in result["summary"]["verdict"]


def test_component_runner_refuses_launch_after_report_deadline(monkeypatch):
    from roam.commands import cmd_service_report as mod

    monkeypatch.setattr(mod._time, "monotonic", lambda: 100.0)
    monkeypatch.setattr(
        mod,
        "_start_component_process",
        lambda *a, **k: pytest.fail("expired report component must not launch"),
    )

    result = mod._run_roam_json(["smells"], deadline=110.0)

    assert result["isError"] is True
    assert result["summary"]["state"] == "report_deadline_exhausted"
    assert "before component launch" in result["summary"]["verdict"]


def test_component_runner_uses_remaining_report_budget_and_cleans_tree(monkeypatch):
    from roam.commands import cmd_service_report as mod

    seen = {}

    class _TimedOutProcess:
        returncode = None

    proc = _TimedOutProcess()
    monkeypatch.setattr(mod._time, "monotonic", lambda: 100.0)
    monkeypatch.setattr(mod, "_start_component_process", lambda *a, **k: proc)

    def _capture(value, *, timeout_seconds):
        assert value is proc
        seen["timeout"] = timeout_seconds
        return b"", b"", "timeout", True, None

    monkeypatch.setattr(mod, "_capture_component_output", _capture)

    result = mod._run_roam_json(["sbom"], deadline=150.0)

    assert seen["timeout"] == 50.0 - mod._DEADLINE_CLEANUP_RESERVE_SECONDS
    assert result["isError"] is True
    assert result["summary"]["state"] == "report_deadline_exhausted"
    assert "process tree terminated" in result["summary"]["verdict"]


def test_due_diligence_uses_one_bounded_report_deadline(monkeypatch):
    from roam.commands import cmd_service_report as mod

    deadlines = []

    def _fake_gather(components, *, max_workers=mod._COMPONENT_MAX_WORKERS, deadline=None):
        del max_workers
        deadlines.append(deadline)
        return {key: {"command": args[0], "summary": {"verdict": f"{key} evidence"}} for key, args in components}

    monkeypatch.setattr(mod._time, "monotonic", lambda: 100.0)
    monkeypatch.setattr(mod, "_gather_components", _fake_gather)

    result = mod._gather_due_diligence()

    assert tuple(result) == tuple(key for key, _args in mod._DUE_DILIGENCE_COMPONENTS)
    assert deadlines == [100.0 + mod._DUE_DILIGENCE_BUDGET_SECONDS] * 5


@pytest.mark.parametrize(
    ("payload", "state"),
    [
        (b'{"summary":{"verdict":"first"},"summary":{"verdict":"last"}}', "component_malformed_output"),
        (b'{"summary":' + b"[" * 200 + b"0" + b"]" * 200 + b"}", "component_malformed_output"),
        (b"[]", "component_empty_output"),
        (b"no json here", "component_empty_output"),
    ],
)
def test_component_runner_rejects_ambiguous_or_invalid_envelopes(monkeypatch, payload, state):
    from roam.commands import cmd_service_report as mod

    class _Process:
        returncode = 0
        stdout = _io.BytesIO(payload)
        stderr = _io.BytesIO()

        def poll(self):
            return self.returncode

    monkeypatch.setattr(mod, "_start_component_process", lambda *a, **k: _Process())
    monkeypatch.setattr(mod, "_terminate_component_process_tree", lambda proc: True)
    result = mod._run_roam_json(["health"])
    assert result["isError"] is True
    assert result["summary"]["state"] == state


# ---------------------------------------------------------------------------
# --output / --type required
# ---------------------------------------------------------------------------


def test_output_writes_markdown_to_file(stub_primitives, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    target = tmp_path / "triage.md"
    code, out = _invoke("--type", "reachability-triage", "--output", str(target))
    assert code == 0
    assert target.exists()
    body = target.read_text(encoding="utf-8")
    assert body.startswith("# Security Reachability Triage")
    assert "Wrote" in out


def test_type_is_required():
    """Missing --type is a clean Click usage error (exit 2), not a traceback."""
    from roam.cli import cli

    runner = CliRunner()
    result = runner.invoke(cli, ["service-report"], catch_exceptions=False)
    assert result.exit_code == 2
    assert "Traceback (most recent call last)" not in (result.output or "")


def test_post_incident_rejects_argv_injection_range(stub_primitives):
    """A --range beginning with '-' is rejected at the CLI boundary."""
    code, out = _invoke("--type", "post-incident", "--range", "--upload-pack=evil")
    assert code != 0
    assert "must not start with" in out


# ---------------------------------------------------------------------------
# Engagement ledger
# ---------------------------------------------------------------------------


def test_engagement_ledger_records_service_report(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    from roam.commands.cmd_service_report import _record_engagement

    rec = _record_engagement(
        report_type="due-diligence",
        client="Acme Inc",
        subject="Acme Inc",
        headline="Fair codebase (75/100)",
        output_path=str(tmp_path / "dd.md"),
        generated_at="2026-07-07 03:31 UTC",
    )
    assert rec is not None
    ledger = tmp_path / ".roam" / "engagements.jsonl"
    assert ledger.exists()
    record = _json.loads(ledger.read_text(encoding="utf-8").strip())
    assert record["kind"] == "service-report"
    assert record["report_type"] == "due-diligence"
    assert record["client"] == "Acme Inc"
    assert record["ledger_schema"] == 1


def test_engagement_ledger_appends_not_overwrites(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    from roam.commands.cmd_service_report import _record_engagement

    _record_engagement(
        report_type="due-diligence",
        client="Acme Inc",
        subject="Acme",
        headline="h1",
        output_path="a.md",
        generated_at="2026-07-07 10:00 UTC",
    )
    _record_engagement(
        report_type="reachability-triage",
        client="Beta Corp",
        subject="Beta",
        headline="h2",
        output_path="b.md",
        generated_at="2026-07-07 11:00 UTC",
    )
    ledger = tmp_path / ".roam" / "engagements.jsonl"
    lines = ledger.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 2
    assert _json.loads(lines[0])["client"] == "Acme Inc"
    assert _json.loads(lines[1])["report_type"] == "reachability-triage"


def test_engagement_ledger_rejects_hardlink_without_mutating_victim_and_discloses_failure(
    stub_primitives,
    tmp_path,
    monkeypatch,
):
    monkeypatch.chdir(tmp_path)
    state = tmp_path / ".roam"
    assert stub_primitives.create_owner_only_directory(state)
    victim = tmp_path / "victim.jsonl"
    original = b'{"private":"unchanged"}\n'
    victim.write_bytes(original)
    ledger = state / "engagements.jsonl"
    try:
        _os.link(victim, ledger)
    except OSError as exc:
        pytest.skip(f"hard links unavailable: {exc}")
    victim_mode_before = victim.stat().st_mode
    victim_owner_only_before = stub_primitives.path_is_owner_only(victim)
    output_path = tmp_path / "report.md"

    code, output = _invoke(
        "--type",
        "ai-readiness",
        "--output",
        str(output_path),
        json_mode=True,
    )

    assert code == 0
    assert victim.read_bytes() == original
    assert ledger.read_bytes() == original
    assert victim.stat().st_mode == victim_mode_before
    assert stub_primitives.path_is_owner_only(victim) is victim_owner_only_before
    envelope = _json.loads(output[output.find("{") :])
    summary = envelope["summary"]
    assert summary["engagement_logged_to"] is None
    assert summary["engagement_ledger_state"] == "unsafe_path"
    assert summary["engagement_ledger_failure"] == "unsafe_path"
    assert summary["state"] == "engagement_persistence_failure"
    assert summary["partial_success"] is True
    assert "engagement ledger unavailable" in summary["verdict"]
    assert "Engagement ledger persistence failed" in output


def test_engagement_ledger_serializes_concurrent_process_writers(tmp_path):
    from roam.commands import cmd_service_report as mod

    source_root = str(Path(mod.__file__).resolve().parents[2])
    env = dict(_os.environ)
    env["PYTHONPATH"] = _os.pathsep.join(value for value in (source_root, env.get("PYTHONPATH")) if value)
    script = """
import sys
from roam.commands.cmd_service_report import _record_engagement

index = sys.argv[1]
result = _record_engagement(
    report_type="due-diligence",
    client=f"client-{index}",
    subject=f"subject-{index}",
    headline=f"headline-{index}",
    output_path=f"report-{index}.md",
    generated_at="2026-07-19 00:00 UTC",
)
raise SystemExit(0 if result is not None else 3)
"""
    processes = [
        _subprocess.Popen(
            [_sys.executable, "-c", script, str(index)],
            cwd=tmp_path,
            env=env,
            stdout=_subprocess.PIPE,
            stderr=_subprocess.PIPE,
            text=True,
        )
        for index in range(12)
    ]
    failures = []
    for process in processes:
        stdout, stderr = process.communicate(timeout=45)
        if process.returncode != 0:
            failures.append((process.returncode, stdout, stderr))
    assert failures == []

    ledger = tmp_path / ".roam" / "engagements.jsonl"
    rows = [_json.loads(line) for line in ledger.read_text(encoding="utf-8").splitlines()]
    assert len(rows) == 12
    assert {row["client"] for row in rows} == {f"client-{index}" for index in range(12)}
    assert all(row["kind"] == "service-report" for row in rows)


def test_engagement_ledger_serializes_mixed_service_and_replay_process_writers(tmp_path):
    from roam.commands import cmd_service_report as mod

    source_root = str(Path(mod.__file__).resolve().parents[2])
    env = dict(_os.environ)
    env["PYTHONPATH"] = _os.pathsep.join(value for value in (source_root, env.get("PYTHONPATH")) if value)
    script = """
import sys
from roam.commands.cmd_pr_replay import _record_engagement as record_replay
from roam.commands.cmd_service_report import _record_engagement as record_service

index = int(sys.argv[1])
if index % 2:
    result = record_replay(
        tier="team",
        client=f"replay-{index}",
        commit_range="HEAD~1..HEAD",
        commits_scanned=1,
        commits_with_findings=0,
        top_detector=None,
        output_path=f"replay-{index}.md",
        generated_at="2026-07-19 00:00 UTC",
    )
else:
    result = record_service(
        report_type="due-diligence",
        client=f"service-{index}",
        subject=f"subject-{index}",
        headline=f"headline-{index}",
        output_path=f"service-{index}.md",
        generated_at="2026-07-19 00:00 UTC",
    )
raise SystemExit(0 if result is not None else 3)
"""
    processes = [
        _subprocess.Popen(
            [_sys.executable, "-c", script, str(index)],
            cwd=tmp_path,
            env=env,
            stdout=_subprocess.PIPE,
            stderr=_subprocess.PIPE,
            text=True,
        )
        for index in range(12)
    ]
    failures = []
    for process in processes:
        stdout, stderr = process.communicate(timeout=45)
        if process.returncode != 0:
            failures.append((process.returncode, stdout, stderr))
    assert failures == []

    ledger = tmp_path / ".roam" / "engagements.jsonl"
    rows = [_json.loads(line) for line in ledger.read_text(encoding="utf-8").splitlines()]
    assert len(rows) == 12
    assert sum(row["kind"] == "service-report" for row in rows) == 6
    assert sum(row["kind"] == "pr-replay" for row in rows) == 6


def test_engagement_ledger_retention_is_bounded_and_disclosed(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    from roam.commands import cmd_service_report as mod

    monkeypatch.setattr(mod, "_ENGAGEMENT_LEDGER_MAX_RECORDS", 3)
    monkeypatch.setattr(mod, "_ENGAGEMENT_LEDGER_MAX_BYTES", 2_048)
    diagnostics = {}
    for index in range(7):
        result = mod._record_engagement(
            report_type="due-diligence",
            client=f"client-{index}",
            subject=f"subject-{index}",
            headline=f"headline-{index}",
            output_path=f"report-{index}.md",
            generated_at="2026-07-19 00:00 UTC",
            diagnostics=diagnostics,
        )
        assert result is not None

    ledger = tmp_path / ".roam" / "engagements.jsonl"
    payload = ledger.read_bytes()
    rows = [_json.loads(line) for line in payload.splitlines()]
    assert len(payload) <= mod._ENGAGEMENT_LEDGER_MAX_BYTES
    assert len(rows) == 3
    assert [row["client"] for row in rows] == ["client-4", "client-5", "client-6"]
    assert mod.path_is_owner_only(tmp_path / ".roam")
    assert mod.path_is_owner_only(ledger)
    assert mod.path_is_owner_only(tmp_path / ".roam" / "engagements.jsonl.lock")
    assert diagnostics == {
        "state": "logged_retention_pruned",
        "retention_pruned": True,
        "records_retained": 3,
    }


def test_no_track_engagement_skips_ledger(stub_primitives, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    output = tmp_path / "r.md"
    code, out = _invoke(
        "--type",
        "reachability-triage",
        "--output",
        str(output),
        "--no-track-engagement",
    )
    assert code == 0
    ledger = tmp_path / ".roam" / "engagements.jsonl"
    if ledger.exists():
        assert ledger.read_text(encoding="utf-8").strip() == ""


# ---------------------------------------------------------------------------
# Report-type registry contract
# ---------------------------------------------------------------------------


def test_report_types_registry_contract():
    from roam.commands.cmd_service_report import _REPORT_TYPES

    assert set(_REPORT_TYPES.keys()) == {
        "due-diligence",
        "ai-readiness",
        "reachability-triage",
        "post-incident",
    }
    required = {"label", "title", "purpose_line", "engagement_price", "lead_commands"}
    for rtype, meta in _REPORT_TYPES.items():
        missing = required - meta.keys()
        assert not missing, f"type '{rtype}' missing keys: {missing}"
        assert isinstance(meta["lead_commands"], list) and meta["lead_commands"]


# ---------------------------------------------------------------------------
# Pure-render unit tests — no CLI, no stubbing needed.
# ---------------------------------------------------------------------------


def test_render_survives_empty_envelopes():
    """A gather that returns {} for every section still renders a report."""
    from roam.commands.cmd_service_report import _REPORT_TYPES, _render

    for rtype in ALL_TYPES:
        meta = {
            "type_meta": _REPORT_TYPES[rtype],
            "report_type": rtype,
            "client": None,
            "index_sha": None,
            "generated_at": "2026-07-07 00:00 UTC",
            "subject": "target repository",
        }
        md = _render(rtype, env={}, meta=meta, commit_range="HEAD~20..HEAD")
        assert md.startswith("# ")
        assert "does not certify" in md
        # Even the empty-data path is wording-guard clean.
        assert not scan_for_overclaims(md)


# ---------------------------------------------------------------------------
# Slow: real end-to-end path (actual primitive subprocesses).
# ---------------------------------------------------------------------------


@pytest.mark.slow
def test_reachability_triage_real_end_to_end():
    """The real path: actual `roam` primitives run against the repo.

    reachability-triage is the fastest real type (no heavy graph walk),
    so it's the one we exercise end-to-end. Asserts a well-formed,
    wording-clean report — proof the stubbed tests match reality.
    """
    code, out = _invoke("--type", "reachability-triage")
    assert code == 0, out[:400]
    assert out.startswith("# Security Reachability Triage")
    assert "Dependency reachability" in out
    assert not scan_for_overclaims(out)
