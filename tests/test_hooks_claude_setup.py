"""W-CC-SETUP — `roam hooks claude` Claude Code installer (2026-06-10).

Makes the compile-prefix channel available to plain Claude Code CLI users:
a UserPromptSubmit hook running `roam --json compile` per prompt. The
Fable 5 A/B numbers (turns -83%) were measured on plain `claude -p` with
this exact prefix-injection shape, so vanilla-CLI parity is evidence-backed.
"""

from __future__ import annotations

import json
import os

import pytest
from click.testing import CliRunner

from roam.commands.cmd_hooks import (
    _CLAUDE_UPS_HOOK_FILENAME,
    _merge_ups_entry,
    _remove_ups_entry,
    _ups_entry_present,
    hooks,
)


@pytest.fixture
def in_tmp(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    return tmp_path


def _invoke(*args):
    return CliRunner().invoke(hooks, list(args), obj={"json": False})


class TestClaudeSetupLifecycle:
    def test_dry_run_writes_nothing(self, in_tmp):
        res = _invoke("claude")
        assert res.exit_code == 0
        assert "Would install" in res.output and "dry-run" in res.output
        assert not (in_tmp / ".claude").exists()

    def test_write_creates_script_and_settings(self, in_tmp):
        res = _invoke("claude", "--write")
        assert res.exit_code == 0
        hook = in_tmp / ".claude" / "hooks" / _CLAUDE_UPS_HOOK_FILENAME
        assert hook.exists()
        assert os.access(hook, os.X_OK)
        settings = json.loads((in_tmp / ".claude" / "settings.json").read_text())
        assert _ups_entry_present(settings)

    def test_write_is_idempotent(self, in_tmp):
        _invoke("claude", "--write")
        res = _invoke("claude", "--write")
        # freshly-written bodies are current -> "wired + current", no re-heal
        assert "wired + current" in res.output
        settings = json.loads((in_tmp / ".claude" / "settings.json").read_text())
        assert len(settings["hooks"]["UserPromptSubmit"]) == 1

    def test_write_preserves_existing_settings(self, in_tmp):
        cdir = in_tmp / ".claude"
        cdir.mkdir()
        (cdir / "settings.json").write_text(
            json.dumps(
                {
                    "permissions": {"allow": ["Bash(ls:*)"]},
                    "hooks": {"PreToolUse": [{"hooks": [{"type": "command", "command": "echo hi"}]}]},
                }
            )
        )
        _invoke("claude", "--write")
        settings = json.loads((cdir / "settings.json").read_text())
        assert settings["permissions"]["allow"] == ["Bash(ls:*)"]
        assert settings["hooks"]["PreToolUse"]  # untouched
        assert _ups_entry_present(settings)
        # backup was taken
        assert (cdir / "settings.json.bak").exists()

    def test_uninstall_removes_entry_and_script(self, in_tmp):
        _invoke("claude", "--write")
        res = _invoke("claude", "--uninstall", "--write")
        assert "Removed" in res.output
        settings = json.loads((in_tmp / ".claude" / "settings.json").read_text())
        assert not _ups_entry_present(settings)
        assert not (in_tmp / ".claude" / "hooks" / _CLAUDE_UPS_HOOK_FILENAME).exists()


class TestSettingsMergeHelpers:
    def test_merge_then_remove_roundtrip(self):
        s = {}
        _merge_ups_entry(s, f"python3 /x/{_CLAUDE_UPS_HOOK_FILENAME}")
        assert _ups_entry_present(s)
        assert _remove_ups_entry(s)
        assert not _ups_entry_present(s)
        assert "UserPromptSubmit" not in s.get("hooks", {})

    def test_remove_keeps_foreign_entries(self):
        s = {
            "hooks": {
                "UserPromptSubmit": [
                    {"hooks": [{"type": "command", "command": "other-tool"}]},
                    {"hooks": [{"type": "command", "command": f"python3 /x/{_CLAUDE_UPS_HOOK_FILENAME}"}]},
                ]
            }
        }
        assert _remove_ups_entry(s)
        remaining = s["hooks"]["UserPromptSubmit"]
        assert len(remaining) == 1
        assert remaining[0]["hooks"][0]["command"] == "other-tool"


class TestHookScriptFailOpen:
    def test_hook_script_fails_open_on_garbage_stdin(self, in_tmp):
        import subprocess
        import sys

        _invoke("claude", "--write")
        hook = in_tmp / ".claude" / "hooks" / _CLAUDE_UPS_HOOK_FILENAME
        proc = subprocess.run([sys.executable, str(hook)], input="NOT JSON", capture_output=True, text=True, timeout=30)
        assert proc.returncode == 0
        assert proc.stdout == ""

    def test_hook_script_skips_tiny_prompts(self, in_tmp):
        import subprocess
        import sys

        _invoke("claude", "--write")
        hook = in_tmp / ".claude" / "hooks" / _CLAUDE_UPS_HOOK_FILENAME
        proc = subprocess.run(
            [sys.executable, str(hook)], input='{"prompt": "hi"}', capture_output=True, text=True, timeout=30
        )
        assert proc.returncode == 0
        assert proc.stdout == ""


class TestVerifyStopHook:
    """W-CC-VERIFY — the post-edit verify half of the MVP loop."""

    def test_write_installs_both_hooks(self, in_tmp):
        from roam.commands.cmd_hooks import _CLAUDE_STOP_HOOK_FILENAME

        _invoke("claude", "--write")
        settings = json.loads((in_tmp / ".claude" / "settings.json").read_text())
        assert set(settings["hooks"].keys()) == {"UserPromptSubmit", "Stop"}
        assert (in_tmp / ".claude" / "hooks" / _CLAUDE_STOP_HOOK_FILENAME).exists()

    def test_no_verify_installs_compile_only(self, in_tmp):
        from roam.commands.cmd_hooks import _CLAUDE_STOP_HOOK_FILENAME

        _invoke("claude", "--write", "--no-verify")
        settings = json.loads((in_tmp / ".claude" / "settings.json").read_text())
        assert "Stop" not in settings["hooks"]
        assert not (in_tmp / ".claude" / "hooks" / _CLAUDE_STOP_HOOK_FILENAME).exists()

    def test_uninstall_sweeps_both(self, in_tmp):
        _invoke("claude", "--write")
        res = _invoke("claude", "--uninstall", "--write")
        assert "Removed" in res.output
        settings = json.loads((in_tmp / ".claude" / "settings.json").read_text())
        assert settings.get("hooks", {}) == {}

    def test_stop_hook_fails_open_and_respects_loop_guard(self, in_tmp):
        import subprocess
        import sys

        from roam.commands.cmd_hooks import _CLAUDE_STOP_HOOK_FILENAME

        _invoke("claude", "--write")
        hook = in_tmp / ".claude" / "hooks" / _CLAUDE_STOP_HOOK_FILENAME
        # loop guard: stop_hook_active → instant silent exit
        proc = subprocess.run(
            [sys.executable, str(hook)], input='{"stop_hook_active": true}', capture_output=True, text=True, timeout=30
        )
        assert proc.returncode == 0 and proc.stdout == ""
        # garbage stdin → fail open
        proc = subprocess.run([sys.executable, str(hook)], input="NOT JSON", capture_output=True, text=True, timeout=30)
        assert proc.returncode == 0 and proc.stdout == ""


_PASS_ENVELOPE = {"summary": {"verdict": "PASS"}, "violations": []}

# Python stub standing in for the `roam` binary: appends an invocation marker
# and prints the canned envelope. Pure Python so it is hermetic on every OS
# (a bare-name `roam` PATH stub is NOT: Windows CreateProcess resolves
# `roam` to a real roam.exe on PATH and never to a stub roam.bat, so on dev
# machines with roam installed a PATH stub silently tests the real binary).
_ROAM_STUB_PY = """\
import os, sys
here = os.path.dirname(os.path.abspath(__file__))
with open(os.path.join(here, "roam-called.txt"), "a", encoding="utf-8") as fh:
    fh.write("called" + chr(10))
with open(os.path.join(here, "envelope.json"), encoding="utf-8") as fh:
    sys.stdout.write(fh.read())
"""

# Driver that runs the UNMODIFIED Stop-hook script text with a `subprocess`
# module shim: argv ["roam", ...] is redirected to the Python stub above;
# everything else (git ...) passes through to the real subprocess module.
_STOP_HOOK_DRIVER_PY = """\
import os, sys, types, subprocess as _real_subprocess

_STUB = sys.argv[1]
_SCRIPT = sys.argv[2]
_POPEN_LOG = os.path.join(os.path.dirname(_STUB), "popen-called.txt")

shim = types.ModuleType("subprocess")


def _run(args, **kwargs):
    if args and args[0] == "roam":
        args = [sys.executable, _STUB] + list(args[1:])
    return _real_subprocess.run(args, **kwargs)


def _popen(args, **kwargs):
    # record roam detached spawns (Loop-B report refresh) WITHOUT actually
    # launching a background process — keeps the test deterministic.
    if args and args[0] == "roam":
        with open(_POPEN_LOG, "a", encoding="utf-8") as fh:
            fh.write(" ".join(args) + chr(10))

        class _Dummy:
            pass

        return _Dummy()
    return _real_subprocess.Popen(args, **kwargs)


shim.run = _run
shim.Popen = _popen
shim.DEVNULL = _real_subprocess.DEVNULL
shim.PIPE = _real_subprocess.PIPE
shim.TimeoutExpired = _real_subprocess.TimeoutExpired
sys.modules["subprocess"] = shim
with open(_SCRIPT, encoding="utf-8") as fh:
    code = fh.read()
sys.argv = [_SCRIPT]
exec(compile(code, _SCRIPT, "exec"), {"__name__": "__main__"})
"""


def _install_roam_verify_stub(stub_root, envelope):
    """Write the roam stub + canned envelope. Returns (stub_dir, marker)."""
    stub_dir = stub_root / "bin"
    stub_dir.mkdir()
    (stub_dir / "roam-stub.py").write_text(_ROAM_STUB_PY, encoding="utf-8")
    (stub_dir / "envelope.json").write_text(json.dumps(envelope), encoding="utf-8")
    return stub_dir, stub_dir / "roam-called.txt"


def _run_stop_hook(repo, stub_dir):
    """Run the Stop-hook script (via the subprocess-shim driver) with
    cwd=repo. Script files live OUTSIDE the repo so the tree stays clean."""
    import subprocess
    import sys

    from roam.commands.cmd_hooks import _CLAUDE_STOP_HOOK_SCRIPT

    hook = repo.parent / "stop-hook.py"
    hook.write_text(_CLAUDE_STOP_HOOK_SCRIPT, encoding="utf-8")
    driver = repo.parent / "stop-hook-driver.py"
    driver.write_text(_STOP_HOOK_DRIVER_PY, encoding="utf-8")
    return subprocess.run(
        [sys.executable, str(driver), str(stub_dir / "roam-stub.py"), str(hook)],
        input="{}",
        capture_output=True,
        text=True,
        timeout=60,
        cwd=str(repo),
    )


class TestStopHookFastExitAndTelemetry:
    """2026-07-11 — clean-tree fast-exit (skip verify on pure Q&A stops) +
    counts-only block-rate telemetry in `.roam/hook-stops.jsonl`."""

    @staticmethod
    def _git_repo(tmp_path):
        """Committed git repo with a `.roam/` dir (telemetry destination)."""
        import subprocess

        repo = tmp_path / "repo"
        repo.mkdir()
        (repo / "tracked.txt").write_text("tracked\n", encoding="utf-8")
        for args in (
            ["git", "init", "-q"],
            ["git", "config", "user.email", "t@t"],
            ["git", "config", "user.name", "t"],
            ["git", "add", "-A"],
            ["git", "commit", "-q", "-m", "init"],
        ):
            subprocess.run(args, cwd=repo, check=True, capture_output=True)
        (repo / ".roam").mkdir()
        return repo

    @staticmethod
    def _stop_rows(repo):
        log = repo / ".roam" / "hook-stops.jsonl"
        assert log.exists(), "hook-stops.jsonl was not written"
        return [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]

    def test_fast_exit_skips_verify_on_clean_tree(self, tmp_path):
        repo = self._git_repo(tmp_path)
        stub_dir, marker = _install_roam_verify_stub(tmp_path, _PASS_ENVELOPE)
        proc = _run_stop_hook(repo, stub_dir)
        assert proc.returncode == 0 and proc.stdout == ""
        assert not marker.exists(), "verify subprocess ran despite a clean tree"
        row = self._stop_rows(repo)[-1]
        assert row["skipped_no_edit"] is True
        assert row["blocked"] is False
        assert row["verify_ms"] == 0

    def test_fast_exit_does_not_fire_on_tracked_edit(self, tmp_path):
        repo = self._git_repo(tmp_path)
        (repo / "tracked.txt").write_text("changed\n", encoding="utf-8")
        stub_dir, marker = _install_roam_verify_stub(tmp_path, _PASS_ENVELOPE)
        proc = _run_stop_hook(repo, stub_dir)
        assert proc.returncode == 0 and proc.stdout == ""  # PASS stays quiet
        assert marker.exists(), "verify subprocess should run on a dirty tree"
        row = self._stop_rows(repo)[-1]
        assert row["skipped_no_edit"] is False

    def test_fast_exit_does_not_fire_on_new_untracked_file(self, tmp_path):
        repo = self._git_repo(tmp_path)
        (repo / "brand_new.py").write_text("x = 1\n", encoding="utf-8")
        stub_dir, marker = _install_roam_verify_stub(tmp_path, _PASS_ENVELOPE)
        _run_stop_hook(repo, stub_dir)
        assert marker.exists(), "verify should run when a new untracked file exists"
        assert self._stop_rows(repo)[-1]["skipped_no_edit"] is False

    def test_blocked_decision_logs_counts_not_text(self, tmp_path):
        repo = self._git_repo(tmp_path)
        (repo / "tracked.txt").write_text("changed\n", encoding="utf-8")
        envelope = {
            "summary": {"verdict": "FAIL"},
            "violations": [
                {
                    "severity": "FAIL",
                    "category": "imports",
                    "file": "tracked.txt",
                    "line": 1,
                    "message": "hallucinated import super_secret_module",
                }
            ],
        }
        stub_dir, marker = _install_roam_verify_stub(tmp_path, envelope)
        proc = _run_stop_hook(repo, stub_dir)
        assert proc.returncode == 0
        assert marker.exists()
        decision = json.loads(proc.stdout)
        assert decision["decision"] == "block"
        row = self._stop_rows(repo)[-1]
        assert row["blocked"] is True
        assert row["findings"] == 1
        assert row["advisory_findings"] == 0
        assert row["skipped_no_edit"] is False
        log_text = (repo / ".roam" / "hook-stops.jsonl").read_text(encoding="utf-8")
        assert "super_secret_module" not in log_text  # counts only, never finding text


class TestHookBodyHeal:
    """C3: refresh a stale roam-written hook BODY that the settings-based
    installer skips (its settings entry is present but the on-disk script is
    frozen at an older version), while leaving user/external bodies alone."""

    def _install(self, in_tmp):
        _invoke("claude", "--write")
        return in_tmp / ".claude" / "hooks" / _CLAUDE_UPS_HOOK_FILENAME

    def test_version_stamp_present_and_parsed(self):
        from roam.commands.cmd_hooks import (
            _CLAUDE_UPS_HOOK_SCRIPT,
            _HOOK_BODY_VERSION,
            _hook_body_version,
        )

        assert _hook_body_version(_CLAUDE_UPS_HOOK_SCRIPT) == _HOOK_BODY_VERSION

    @staticmethod
    def _register(monkeypatch, *bodies: str):
        """Add synthetic bodies to the shipped-registry for a test."""
        import hashlib

        from roam.commands import cmd_hooks

        extra = {hashlib.sha256(b.encode("utf-8")).hexdigest() for b in bodies}
        monkeypatch.setattr(cmd_hooks, "_KNOWN_HOOK_BODY_SHAS", cmd_hooks._KNOWN_HOOK_BODY_SHAS | extra)

    def test_hook_bodies_compile(self):
        """A syntax error in a body constant makes every absence-asserting hook
        test pass vacuously (the broken hook spawns nothing) — guard the whole
        class of failures with an explicit compile check."""
        from roam.commands.cmd_hooks import _CLAUDE_STOP_HOOK_SCRIPT, _CLAUDE_UPS_HOOK_SCRIPT

        compile(_CLAUDE_UPS_HOOK_SCRIPT, "roam-compile-ups.py", "exec")
        compile(_CLAUDE_STOP_HOOK_SCRIPT, "roam-verify-stop.py", "exec")

    def test_registry_is_seeded(self):
        """F1 guard: an empty/garbled registry silently disables pre-stamp healing."""
        from roam.commands.cmd_hooks import _KNOWN_HOOK_BODY_SHAS

        assert len(_KNOWN_HOOK_BODY_SHAS) >= 13
        assert all(len(s) == 64 and set(s) <= set("0123456789abcdef") for s in _KNOWN_HOOK_BODY_SHAS)

    def test_heal_state_classification(self, monkeypatch):
        from roam.commands.cmd_hooks import _CLAUDE_UPS_HOOK_SCRIPT, _HOOK_BODY_VERSION, _hook_heal_state

        canonical = _CLAUDE_UPS_HOOK_SCRIPT
        cur = f"# roam-hook-version: {_HOOK_BODY_VERSION}"
        old = f"# roam-hook-version: {_HOOK_BODY_VERSION - 1}"
        assert _hook_heal_state(canonical, canonical) == "current"
        # an older stamp alone proves NOTHING: unknown content -> modified
        older_unknown = canonical.replace(cur, old) + "# my edit\n"
        assert _hook_heal_state(older_unknown, canonical) == "modified"
        # a REGISTERED older body (roam provably shipped it) -> healable
        older_known = canonical.replace(cur, old)
        self._register(monkeypatch, older_known)
        from roam.commands.cmd_hooks import _hook_heal_state as heal_state

        assert heal_state(older_known, canonical) == "heal"
        # a registered UNSTAMPED body (pre-stamp era) -> healable
        prestamp = "#!/usr/bin/env python3\n# an old shipped body\n"
        self._register(monkeypatch, prestamp)
        assert heal_state(prestamp, canonical) == "heal"
        # an unstamped, unrecognized body -> foreign (never auto-overwrite)
        assert heal_state("#!/usr/bin/env python3\nprint('mine')\n", canonical) == "foreign"

    def test_prestamp_registered_body_is_healed(self, in_tmp, monkeypatch):
        """F1: the pre-stamp fleet heals via the SHA registry, no marker needed."""
        hook = self._install(in_tmp)
        prestamp = "#!/usr/bin/env python3\n# roam body from before the stamp era\n"
        hook.write_text(prestamp, encoding="utf-8")
        self._register(monkeypatch, prestamp)
        res = _invoke("claude", "--write")
        assert "healed 1 stale hook body" in res.output
        from roam.commands.cmd_hooks import _HOOK_BODY_VERSION, _hook_body_version

        assert _hook_body_version(hook.read_text(encoding="utf-8")) == _HOOK_BODY_VERSION
        # F9: the replaced body is preserved as .bak
        bak = hook.parent / (hook.name + ".bak")
        assert bak.read_text(encoding="utf-8") == prestamp

    def test_older_stamped_modified_body_not_healed(self, in_tmp):
        """F3: a stamped-but-edited body is never silently overwritten."""
        hook = self._install(in_tmp)
        from roam.commands.cmd_hooks import _HOOK_BODY_VERSION

        edited = (
            hook.read_text(encoding="utf-8").replace(
                f"# roam-hook-version: {_HOOK_BODY_VERSION}", f"# roam-hook-version: {_HOOK_BODY_VERSION - 1}"
            )
            + "# my customization\n"
        )
        hook.write_text(edited, encoding="utf-8")
        res = _invoke("claude", "--write")
        assert "user-modified" in res.output and "(modified)" in res.output
        assert hook.read_text(encoding="utf-8") == edited  # untouched
        # --force overwrites, keeping a .bak
        res = _invoke("claude", "--write", "--force")
        assert "force-refreshed 1 body(ies)" in res.output
        assert (hook.parent / (hook.name + ".bak")).read_text(encoding="utf-8") == edited

    def test_truncated_stamped_body_is_not_current(self, in_tmp):
        """F6a: a truncated body behind an intact stamp must not read as current."""
        hook = self._install(in_tmp)
        truncated = "\n".join(hook.read_text(encoding="utf-8").split("\n")[:3]) + "\n"
        hook.write_text(truncated, encoding="utf-8")
        res = _invoke("claude", "--write")
        assert "wired + current" not in res.output
        assert "need attention" in res.output or "user-modified" in res.output

    def test_missing_body_with_wired_entry_is_reinstalled(self, in_tmp):
        """F6b: entry present + body file deleted -> body comes back on --write."""
        hook = self._install(in_tmp)
        hook.unlink()
        res = _invoke("claude", "--write")
        assert "healed 1 stale hook body" in res.output
        assert hook.exists()

    def test_unreadable_body_no_traceback(self, in_tmp):
        """F5: a UTF-16 hook body must degrade to a report, never a traceback."""
        hook = self._install(in_tmp)
        hook.write_bytes("#!/usr/bin/env python3\nprint('x')\n".encode("utf-16"))
        res = _invoke("claude", "--write")
        assert res.exit_code == 0
        assert res.exception is None
        assert "(unreadable)" in res.output
        # untouched without --force
        assert hook.read_bytes().startswith(b"\xff\xfe")
        res = _invoke("claude", "--write", "--force")
        assert "force-refreshed 1 body(ies)" in res.output
        hook.read_text(encoding="utf-8")  # now valid UTF-8

    def test_wiped_settings_does_not_overwrite_foreign_body(self, in_tmp):
        """F4: a missing settings entry must not become a license to clobber."""
        hook = self._install(in_tmp)
        mine = "#!/usr/bin/env python3\n# my own hook\nprint('custom')\n"
        hook.write_text(mine, encoding="utf-8")
        (in_tmp / ".claude" / "settings.json").unlink()
        res = _invoke("claude", "--write")
        assert res.exit_code == 0
        settings = json.loads((in_tmp / ".claude" / "settings.json").read_text())
        assert _ups_entry_present(settings)  # entry rewired...
        assert hook.read_text(encoding="utf-8") == mine  # ...body preserved
        assert "NOTE:" in res.output

    def test_foreign_body_not_healed_but_reported(self, in_tmp):
        hook = self._install(in_tmp)
        mine = "#!/usr/bin/env python3\n# my own customized hook\nprint('custom')\n"
        hook.write_text(mine, encoding="utf-8")
        res = _invoke("claude", "--write")
        assert "user-modified" in res.output and "(foreign)" in res.output
        assert hook.read_text(encoding="utf-8") == mine  # untouched

    def test_force_overwrites_foreign_body(self, in_tmp):
        hook = self._install(in_tmp)
        hook.write_text("#!/usr/bin/env python3\nprint('custom')\n", encoding="utf-8")
        res = _invoke("claude", "--write", "--force")
        assert "force-refreshed 1 body(ies)" in res.output
        from roam.commands.cmd_hooks import _HOOK_BODY_VERSION, _hook_body_version

        assert _hook_body_version(hook.read_text(encoding="utf-8")) == _HOOK_BODY_VERSION

    def test_dry_run_reports_heal_without_writing(self, in_tmp, monkeypatch):
        hook = self._install(in_tmp)
        prestamp = "#!/usr/bin/env python3\n# roam body from before the stamp era\n"
        hook.write_text(prestamp, encoding="utf-8")
        self._register(monkeypatch, prestamp)
        res = _invoke("claude")  # no --write
        assert "heal stale/missing body" in res.output and "dry-run" in res.output
        assert hook.read_text(encoding="utf-8") == prestamp  # unchanged

    def test_dry_run_force_labels_foreign_distinctly(self, in_tmp):
        """F10: dry-run --force must not call a foreign body a stale heal."""
        hook = self._install(in_tmp)
        hook.write_text("#!/usr/bin/env python3\nprint('custom')\n", encoding="utf-8")
        res = _invoke("claude", "--force")  # no --write
        assert "force-overwrite unrecognized body" in res.output
        assert "print('custom')" in hook.read_text(encoding="utf-8")  # untouched

    def test_surgered_current_body_classifies_current(self):
        """compile-code's mode-override surgery on the CURRENT body stays quiet
        (its SHA is registered as a shipped variant)."""
        import re as _re

        from roam.commands.cmd_hooks import _CLAUDE_STOP_HOOK_SCRIPT, _hook_heal_state

        # verbatim copy of compile-code cli.py _override_hook_maintenance_commands
        script = _CLAUDE_STOP_HOOK_SCRIPT.replace(
            '["roam", "--json", *args]',
            '["roam", *(["--override-mode"] if args and args[0] in {"verify", "index"} else []), "--json", *args]',
        )
        script = _re.sub(
            r'(["\']roam["\']\s*,\s*)(["\'])(verify|index)\2',
            r'\1"--override-mode", \2\3\2',
            script,
        )
        assert script != _CLAUDE_STOP_HOOK_SCRIPT  # surgery actually bites
        assert _hook_heal_state(script, _CLAUDE_STOP_HOOK_SCRIPT) == "current"

    def test_doctor_reports_hook_body_state(self, in_tmp, monkeypatch):
        """F7: doctor surfaces non-current bodies (advisory)."""
        from roam.commands import cmd_hooks
        from roam.commands.cmd_doctor import _check_claude_hook_bodies

        # isolate the user level from the real box
        monkeypatch.setattr(
            cmd_hooks,
            "_claude_hook_dir",
            lambda user_level: in_tmp / "userhooks" if user_level else in_tmp / ".claude" / "hooks",
        )
        monkeypatch.setattr(
            cmd_hooks,
            "_claude_settings_path",
            lambda user_level: (
                in_tmp / "userhooks" / "settings.json" if user_level else in_tmp / ".claude" / "settings.json"
            ),
        )
        _invoke("claude", "--write")
        assert _check_claude_hook_bodies()["passed"] is True
        hook = in_tmp / ".claude" / "hooks" / _CLAUDE_UPS_HOOK_FILENAME
        hook.write_text("#!/usr/bin/env python3\nprint('custom')\n", encoding="utf-8")
        result = _check_claude_hook_bodies()
        assert result["passed"] is False
        assert "foreign" in result["detail"]
        # entry wired + body deleted -> reported, not silently fine
        hook.unlink()
        result = _check_claude_hook_bodies()
        assert result["passed"] is False
        assert "missing" in result["detail"]


class TestLoopBReportRefresh:
    """C5 / T2b: the Stop hook (opt-in) spawns a DETACHED, THROTTLED whole-repo
    `verify --report --persist` on edit-stops so the next compile's
    known_findings is fresh — never blocking, never --diff-only into the
    whole-repo report path."""

    @staticmethod
    def _git_repo(tmp_path):
        import subprocess

        repo = tmp_path / "repo"
        repo.mkdir()
        (repo / "tracked.txt").write_text("tracked\n", encoding="utf-8")
        for args in (
            ["git", "init", "-q"],
            ["git", "config", "user.email", "t@t"],
            ["git", "config", "user.name", "t"],
            ["git", "add", "-A"],
            ["git", "commit", "-q", "-m", "init"],
        ):
            subprocess.run(args, cwd=repo, check=True, capture_output=True)
        (repo / ".roam").mkdir()
        return repo

    def _run(self, repo, stub_dir, env_extra=None, cwd=None):
        import os
        import subprocess
        import sys

        from roam.commands.cmd_hooks import _CLAUDE_STOP_HOOK_SCRIPT

        hook = repo.parent / "stop-hook.py"
        hook.write_text(_CLAUDE_STOP_HOOK_SCRIPT, encoding="utf-8")
        driver = repo.parent / "stop-hook-driver.py"
        driver.write_text(_STOP_HOOK_DRIVER_PY, encoding="utf-8")
        env = {**os.environ, **(env_extra or {})}
        subprocess.run(
            [sys.executable, str(driver), str(stub_dir / "roam-stub.py"), str(hook)],
            input="{}",
            capture_output=True,
            text=True,
            timeout=60,
            cwd=str(cwd or repo),
            env=env,
        )
        log = stub_dir / "popen-called.txt"
        return log.read_text(encoding="utf-8") if log.exists() else ""

    def test_default_off_no_refresh_spawn(self, tmp_path):
        repo = self._git_repo(tmp_path)
        (repo / "tracked.txt").write_text("changed\n", encoding="utf-8")  # edit-stop
        stub_dir, _ = _install_roam_verify_stub(tmp_path, _PASS_ENVELOPE)
        popen_log = self._run(repo, stub_dir)  # no opt-in env
        assert "verify" not in popen_log  # default OFF -> no background refresh

    def test_optin_spawns_whole_repo_report_refresh(self, tmp_path):
        repo = self._git_repo(tmp_path)
        (repo / "tracked.txt").write_text("changed\n", encoding="utf-8")  # edit-stop
        stub_dir, _ = _install_roam_verify_stub(tmp_path, _PASS_ENVELOPE)
        popen_log = self._run(repo, stub_dir, {"ROAM_HOOK_REPORT_REFRESH": "1"})
        # whole-repo (report+persist), NOT --diff-only (that would poison the report)
        assert "roam verify --auto --report --persist" in popen_log
        assert "--diff-only" not in popen_log

    def test_throttled_when_report_fresh(self, tmp_path):
        import time

        repo = self._git_repo(tmp_path)
        (repo / "tracked.txt").write_text("changed\n", encoding="utf-8")
        # a FRESH report exists -> refresh must be throttled (not respawned)
        report = repo / ".roam" / "verify-report.json"
        report.write_text("{}", encoding="utf-8")
        os_utime_now = time.time()
        import os

        os.utime(report, (os_utime_now, os_utime_now))
        stub_dir, _ = _install_roam_verify_stub(tmp_path, _PASS_ENVELOPE)
        popen_log = self._run(repo, stub_dir, {"ROAM_HOOK_REPORT_REFRESH": "1"})
        assert "verify" not in popen_log  # fresh -> throttled

    def test_no_refresh_on_clean_tree(self, tmp_path):
        repo = self._git_repo(tmp_path)  # no edits -> fast-exit, no refresh
        stub_dir, _ = _install_roam_verify_stub(tmp_path, _PASS_ENVELOPE)
        popen_log = self._run(repo, stub_dir, {"ROAM_HOOK_REPORT_REFRESH": "1"})
        assert "verify" not in popen_log

    def test_claim_marker_is_single_flight(self, tmp_path):
        """Review MAJOR-1/2: repeated edit-stops during the in-flight window (or
        with a persist that never lands) must spawn exactly ONE verify."""
        repo = self._git_repo(tmp_path)
        (repo / "tracked.txt").write_text("changed\n", encoding="utf-8")
        stub_dir, _ = _install_roam_verify_stub(tmp_path, _PASS_ENVELOPE)
        first = self._run(repo, stub_dir, {"ROAM_HOOK_REPORT_REFRESH": "1"})
        assert first.count("roam verify --auto --report --persist") == 1
        assert (repo / ".roam" / "verify-refresh-claim").exists()  # claim taken
        # second stop, report still absent (persist "never landed") -> no respawn
        (repo / "tracked.txt").write_text("changed again\n", encoding="utf-8")
        second = self._run(repo, stub_dir, {"ROAM_HOOK_REPORT_REFRESH": "1"})
        assert second.count("roam verify --auto --report --persist") == 1  # log unchanged

    def test_stale_claim_respawns(self, tmp_path):
        import os as _os
        import time as _time

        repo = self._git_repo(tmp_path)
        (repo / "tracked.txt").write_text("changed\n", encoding="utf-8")
        (repo / ".roam").mkdir(exist_ok=True)
        claim = repo / ".roam" / "verify-refresh-claim"
        claim.write_text("pid=0 time=0\n", encoding="utf-8")
        old = _time.time() - 7200  # 2h-old claim: well past the 30-min window
        _os.utime(claim, (old, old))
        stub_dir, _ = _install_roam_verify_stub(tmp_path, _PASS_ENVELOPE)
        popen_log = self._run(repo, stub_dir, {"ROAM_HOOK_REPORT_REFRESH": "1"})
        assert "roam verify --auto --report --persist" in popen_log

    def test_throttle_anchored_at_repo_root(self, tmp_path):
        """Review MAJOR-3: a hook running in a repo SUBDIR must see the root
        report (where the spawned verify persists), not a cwd-relative path."""
        import os as _os
        import time as _time

        repo = self._git_repo(tmp_path)
        sub = repo / "pkg"
        sub.mkdir()
        (repo / "tracked.txt").write_text("changed\n", encoding="utf-8")
        report = repo / ".roam" / "verify-report.json"
        report.write_text("{}", encoding="utf-8")
        now = _time.time()
        _os.utime(report, (now, now))  # fresh AT THE ROOT
        stub_dir, _ = _install_roam_verify_stub(tmp_path, _PASS_ENVELOPE)
        popen_log = self._run(repo, stub_dir, {"ROAM_HOOK_REPORT_REFRESH": "1"}, cwd=sub)
        assert "verify --auto --report --persist" not in popen_log  # root throttle honored
