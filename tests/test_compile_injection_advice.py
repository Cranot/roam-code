"""Generation-shaped tasks advise the injection channel to skip.

The 2026-06-09/10 Fable 5 A/B measured write-pytest cells at the SAME turn
count with +25% input tokens under injection (and the lean-envelope variant
lost too) — for code-WRITING tasks the envelope is pure cache-read overhead.
The compiler now stamps ``summary.injection_advice`` and the Claude Code
UserPromptSubmit hook injects nothing when it says skip. Explicit
``roam compile`` callers still get the full envelope.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys

from roam.plan.compiler import injection_advice


def _install_roam_stub(tmp_path, monkeypatch, envelope: dict) -> None:
    """Put a fake ``roam`` on PATH that prints ``envelope`` as JSON.

    Cross-platform: the hook shells out to ``roam --json compile ...`` via a
    bare-name PATH lookup, so the stub must be launchable by name on every OS.
    On POSIX we drop a ``#!/bin/sh`` script + chmod; on Windows (which has no
    shebang support and won't execute an extensionless file) we drop a
    ``roam.bat`` that Windows finds via PATHEXT. PATH is prepended with
    ``os.pathsep`` (``:`` on POSIX, ``;`` on Windows), not a hardcoded ``:``.
    """
    stub_dir = tmp_path / "bin"
    stub_dir.mkdir()
    payload = json.dumps(envelope)
    if sys.platform == "win32":
        # No shebang support and an extensionless file won't execute, so drop a
        # roam.bat (found via PATHEXT). It cats a payload file to stdout through
        # the real interpreter — avoids batch-quoting the JSON inline.
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
    monkeypatch.setenv("PATH", f"{stub_dir}{os.pathsep}{os.environ['PATH']}")


class TestInjectionAdvice:
    def test_write_pytest_advises_skip(self):
        assert injection_advice("synthesis_query", "write a pytest for compile_plan") == "skip_generation_task"

    def test_write_docstring_advises_skip(self):
        assert injection_advice("synthesis_query", "write a docstring for `open_db`") == "skip_generation_task"

    def test_implement_advises_skip(self):
        assert injection_advice("synthesis_query", "implement retry backoff in the client") == "skip_generation_task"

    def test_refactor_proposal_still_injects(self):
        # Impact/caller facts feed proposal answers — keep injecting.
        assert injection_advice("synthesis_query", "propose a refactor of cmd_verify") == "inject"

    def test_unified_diff_still_injects(self):
        assert injection_advice("synthesis_query", "produce a unified diff splitting cli.py") == "inject"

    def test_non_synthesis_procedures_always_inject(self):
        assert injection_advice("structural_callers", "write a pytest for X") == "inject"
        assert injection_advice("freeform_explore", "what calls open_db") == "inject"


class TestStackTraceEditTrim:
    """S4 edit-trim lever: stack_trace_fix injection is opt-in (default OFF) —
    the file:line already rides the pasted trace, but the claude arm measured
    the opposite sign, so the skip ships as a flag, never a default."""

    def test_default_off_still_injects(self, monkeypatch):
        monkeypatch.delenv("ROAM_SKIP_EDIT_ENVELOPE", raising=False)
        assert injection_advice("stack_trace_fix", "fix this TypeError at foo.py:42") == "inject"

    def test_flag_on_skips(self, monkeypatch):
        monkeypatch.setenv("ROAM_SKIP_EDIT_ENVELOPE", "1")
        assert injection_advice("stack_trace_fix", "fix this TypeError at foo.py:42") == "skip_edit_task"

    def test_flag_only_affects_stack_trace_fix(self, monkeypatch):
        monkeypatch.setenv("ROAM_SKIP_EDIT_ENVELOPE", "1")
        # other procedures are untouched by this flag
        assert injection_advice("structural_callers", "fix foo.py:42") == "inject"
        assert injection_advice("synthesis_query", "propose a refactor") == "inject"

    def test_skip_value_honored_by_deployed_hooks(self):
        # deployed UPS hooks gate on startswith("skip"), so the new value works
        # without redeployment — guard that the returned token keeps that prefix
        import os

        os.environ["ROAM_SKIP_EDIT_ENVELOPE"] = "on"
        try:
            assert injection_advice("stack_trace_fix", "x").startswith("skip")
        finally:
            del os.environ["ROAM_SKIP_EDIT_ENVELOPE"]


class TestHookHonorsAdvice:
    def test_hook_script_injects_nothing_on_skip_advice(self, tmp_path, monkeypatch):
        """Run the installed hook script with a stubbed `roam` that returns a
        skip-advice envelope: the hook must print nothing."""
        from roam.commands.cmd_hooks import _CLAUDE_UPS_HOOK_SCRIPT

        hook = tmp_path / "hook.py"
        # The hook script contains a non-ASCII em-dash; on Windows the default
        # write_text encoding is the system codepage (not UTF-8), which corrupts
        # it and makes the subprocess fail to parse the file. Force UTF-8.
        hook.write_text(_CLAUDE_UPS_HOOK_SCRIPT, encoding="utf-8")

        envelope = {
            "summary": {"procedure": "synthesis_query", "injection_advice": "skip_generation_task"},
            "artifact": {"plan": {"named_paths": ["a.py"], "prefetched_facts": {"x": 1}}},
        }
        _install_roam_stub(tmp_path, monkeypatch, envelope)

        proc = subprocess.run(
            [sys.executable, str(hook)],
            input=json.dumps({"prompt": "write a pytest for compile_plan please"}),
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert proc.returncode == 0
        assert proc.stdout.strip() == ""

    def test_hook_script_injects_on_inject_advice(self, tmp_path, monkeypatch):
        from roam.commands.cmd_hooks import _CLAUDE_UPS_HOOK_SCRIPT

        hook = tmp_path / "hook.py"
        hook.write_text(_CLAUDE_UPS_HOOK_SCRIPT, encoding="utf-8")

        envelope = {
            "summary": {"procedure": "structural_callers", "injection_advice": "inject"},
            "artifact": {"plan": {"named_paths": ["a.py"], "prefetched_facts": {"callers": [1]}}},
        }
        _install_roam_stub(tmp_path, monkeypatch, envelope)

        proc = subprocess.run(
            [sys.executable, str(hook)],
            input=json.dumps({"prompt": "who calls open_db in this repo"}),
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert proc.returncode == 0
        assert "PRE-COMPUTED PLAN" in proc.stdout
