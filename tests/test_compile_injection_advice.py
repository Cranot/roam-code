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
import subprocess
import sys

from roam.plan.compiler import injection_advice


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


class TestHookHonorsAdvice:
    def test_hook_script_injects_nothing_on_skip_advice(self, tmp_path, monkeypatch):
        """Run the installed hook script with a stubbed `roam` that returns a
        skip-advice envelope: the hook must print nothing."""
        from roam.commands.cmd_hooks import _CLAUDE_UPS_HOOK_SCRIPT

        hook = tmp_path / "hook.py"
        hook.write_text(_CLAUDE_UPS_HOOK_SCRIPT)

        envelope = {
            "summary": {"procedure": "synthesis_query", "injection_advice": "skip_generation_task"},
            "artifact": {"plan": {"named_paths": ["a.py"], "prefetched_facts": {"x": 1}}},
        }
        stub_dir = tmp_path / "bin"
        stub_dir.mkdir()
        stub = stub_dir / "roam"
        stub.write_text(f"#!/bin/sh\ncat <<'EOF'\n{json.dumps(envelope)}\nEOF\n")
        stub.chmod(0o755)
        monkeypatch.setenv("PATH", f"{stub_dir}:{__import__('os').environ['PATH']}")

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
        hook.write_text(_CLAUDE_UPS_HOOK_SCRIPT)

        envelope = {
            "summary": {"procedure": "structural_callers", "injection_advice": "inject"},
            "artifact": {"plan": {"named_paths": ["a.py"], "prefetched_facts": {"callers": [1]}}},
        }
        stub_dir = tmp_path / "bin"
        stub_dir.mkdir()
        stub = stub_dir / "roam"
        stub.write_text(f"#!/bin/sh\ncat <<'EOF'\n{json.dumps(envelope)}\nEOF\n")
        stub.chmod(0o755)
        monkeypatch.setenv("PATH", f"{stub_dir}:{__import__('os').environ['PATH']}")

        proc = subprocess.run(
            [sys.executable, str(hook)],
            input=json.dumps({"prompt": "who calls open_db in this repo"}),
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert proc.returncode == 0
        assert "PRE-COMPUTED PLAN" in proc.stdout
