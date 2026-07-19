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


def _symlink_or_skip(target, link, *, directory=False):
    try:
        link.symlink_to(target, target_is_directory=directory)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks are unavailable on this platform")


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


class TestClaudeSetupPathSecurity:
    def test_rejects_symlinked_claude_directory(self, in_tmp):
        outside = in_tmp / "outside"
        outside.mkdir()
        _symlink_or_skip(outside, in_tmp / ".claude", directory=True)

        res = _invoke("claude", "--write")
        assert res.exit_code == 1
        assert "unsafe" in res.output.lower()
        assert list(outside.iterdir()) == []

    def test_rejects_symlinked_hooks_directory(self, in_tmp):
        cdir = in_tmp / ".claude"
        cdir.mkdir()
        outside = in_tmp / "outside"
        outside.mkdir()
        _symlink_or_skip(outside, cdir / "hooks", directory=True)

        res = _invoke("claude", "--write")
        assert res.exit_code == 1
        assert "unsafe" in res.output.lower()
        assert list(outside.iterdir()) == []

    def test_rejects_symlinked_settings_file(self, in_tmp):
        cdir = in_tmp / ".claude"
        cdir.mkdir()
        outside = in_tmp / "outside-settings.json"
        outside.write_text('{"sentinel": true}\n', encoding="utf-8")
        _symlink_or_skip(outside, cdir / "settings.json")

        res = _invoke("claude", "--write")
        assert res.exit_code == 1
        assert outside.read_text(encoding="utf-8") == '{"sentinel": true}\n'

    def test_force_never_follows_symlinked_hook_body(self, in_tmp):
        assert _invoke("claude", "--write").exit_code == 0
        hook = in_tmp / ".claude" / "hooks" / _CLAUDE_UPS_HOOK_FILENAME
        hook.unlink()
        outside = in_tmp / "outside-hook.py"
        outside.write_text("sentinel\n", encoding="utf-8")
        _symlink_or_skip(outside, hook)

        res = _invoke("claude", "--write", "--force")
        assert res.exit_code == 1
        assert outside.read_text(encoding="utf-8") == "sentinel\n"

    def test_force_never_rewrites_hard_linked_hook_body(self, in_tmp):
        assert _invoke("claude", "--write").exit_code == 0
        hook = in_tmp / ".claude" / "hooks" / _CLAUDE_UPS_HOOK_FILENAME
        hook.unlink()
        outside = in_tmp / "outside-hook.py"
        outside.write_text("sentinel\n", encoding="utf-8")
        try:
            os.link(outside, hook)
        except (OSError, NotImplementedError):
            pytest.skip("hard links are unavailable on this platform")

        res = _invoke("claude", "--write", "--force")
        assert res.exit_code == 1
        assert outside.read_text(encoding="utf-8") == "sentinel\n"

    def test_rejects_hard_linked_settings(self, in_tmp):
        cdir = in_tmp / ".claude"
        cdir.mkdir()
        outside = in_tmp / "outside-settings.json"
        outside.write_text("{}\n", encoding="utf-8")
        try:
            os.link(outside, cdir / "settings.json")
        except (OSError, NotImplementedError):
            pytest.skip("hard links are unavailable on this platform")

        res = _invoke("claude", "--write")
        assert res.exit_code == 1
        assert outside.read_text(encoding="utf-8") == "{}\n"

    def test_rejects_duplicate_settings_keys(self, in_tmp):
        cdir = in_tmp / ".claude"
        cdir.mkdir()
        settings = cdir / "settings.json"
        original = '{"hooks": {}, "hooks": {}}\n'
        settings.write_text(original, encoding="utf-8")

        res = _invoke("claude", "--write")
        assert res.exit_code == 1
        assert "duplicate JSON key" in res.output
        assert settings.read_text(encoding="utf-8") == original

    def test_rejects_oversized_settings(self, in_tmp):
        from roam.commands.cmd_hooks import _MAX_CLAUDE_SETTINGS_BYTES

        cdir = in_tmp / ".claude"
        cdir.mkdir()
        settings = cdir / "settings.json"
        settings.write_bytes(b"x" * (_MAX_CLAUDE_SETTINGS_BYTES + 1))

        res = _invoke("claude", "--write")
        assert res.exit_code == 1
        assert "byte limit" in res.output
        assert settings.stat().st_size == _MAX_CLAUDE_SETTINGS_BYTES + 1

    def test_rejects_symlinked_settings_backup(self, in_tmp):
        cdir = in_tmp / ".claude"
        cdir.mkdir()
        (cdir / "settings.json").write_text("{}\n", encoding="utf-8")
        outside = in_tmp / "outside-backup.json"
        outside.write_text("sentinel\n", encoding="utf-8")
        _symlink_or_skip(outside, cdir / "settings.json.bak")

        res = _invoke("claude", "--write")
        assert res.exit_code == 1
        assert outside.read_text(encoding="utf-8") == "sentinel\n"
        hooks_dir = cdir / "hooks"
        assert not hooks_dir.exists() or list(hooks_dir.iterdir()) == []

    def test_rejects_hard_linked_settings_backup(self, in_tmp):
        cdir = in_tmp / ".claude"
        cdir.mkdir()
        (cdir / "settings.json").write_text("{}\n", encoding="utf-8")
        outside = in_tmp / "outside-backup.json"
        outside.write_text("sentinel\n", encoding="utf-8")
        try:
            os.link(outside, cdir / "settings.json.bak")
        except (OSError, NotImplementedError):
            pytest.skip("hard links are unavailable on this platform")

        res = _invoke("claude", "--write")
        assert res.exit_code == 1
        assert outside.read_text(encoding="utf-8") == "sentinel\n"
        hooks_dir = cdir / "hooks"
        assert not hooks_dir.exists() or list(hooks_dir.iterdir()) == []

    def test_force_rejects_hard_linked_hook_backup(self, in_tmp):
        assert _invoke("claude", "--write").exit_code == 0
        hook = in_tmp / ".claude" / "hooks" / _CLAUDE_UPS_HOOK_FILENAME
        custom = "#!/usr/bin/env python3\nprint('custom')\n"
        hook.write_text(custom, encoding="utf-8")
        outside = in_tmp / "outside-hook-backup.py"
        outside.write_text("sentinel\n", encoding="utf-8")
        try:
            os.link(outside, hook.parent / (hook.name + ".bak"))
        except (OSError, NotImplementedError):
            pytest.skip("hard links are unavailable on this platform")

        res = _invoke("claude", "--write", "--force")
        assert res.exit_code == 1
        assert outside.read_text(encoding="utf-8") == "sentinel\n"
        assert hook.read_text(encoding="utf-8").replace("\r\n", "\n") == custom

    def test_concurrent_settings_change_is_not_clobbered(self, in_tmp, monkeypatch):
        from roam.commands import cmd_hooks

        cdir = in_tmp / ".claude"
        cdir.mkdir()
        settings = cdir / "settings.json"
        settings.write_text('{"permissions": {"allow": []}}\n', encoding="utf-8")
        concurrent = '{"permissions": {"allow": ["concurrent"]}}\n'
        real_scan = cmd_hooks._scan_hook_bodies

        def mutate_after_load(*args, **kwargs):
            settings.write_text(concurrent, encoding="utf-8")
            return real_scan(*args, **kwargs)

        monkeypatch.setattr(cmd_hooks, "_scan_hook_bodies", mutate_after_load)
        res = _invoke("claude", "--write")
        assert res.exit_code == 1
        assert "changed since" in res.output
        assert settings.read_text(encoding="utf-8").replace("\r\n", "\n") == concurrent
        hooks_dir = cdir / "hooks"
        assert not hooks_dir.exists() or list(hooks_dir.iterdir()) == []

    def test_concurrent_hook_change_is_not_clobbered(self, in_tmp, monkeypatch):
        from roam.commands import cmd_hooks

        assert _invoke("claude", "--write").exit_code == 0
        hook = in_tmp / ".claude" / "hooks" / _CLAUDE_UPS_HOOK_FILENAME
        first = "#!/usr/bin/env python3\nprint('first custom')\n"
        concurrent = "#!/usr/bin/env python3\nprint('concurrent custom')\n"
        hook.write_text(first, encoding="utf-8")
        real_validate = cmd_hooks._validate_claude_layout
        calls = 0

        def mutate_after_scan(*args, **kwargs):
            nonlocal calls
            result = real_validate(*args, **kwargs)
            calls += 1
            if calls == 2:
                hook.write_text(concurrent, encoding="utf-8")
            return result

        monkeypatch.setattr(cmd_hooks, "_validate_claude_layout", mutate_after_scan)
        res = _invoke("claude", "--write", "--force")
        assert res.exit_code == 1
        assert "changed after inspection" in res.output
        assert hook.read_text(encoding="utf-8").replace("\r\n", "\n") == concurrent


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


class TestUpsDaemonPath:
    """S2-lite (v4): the UPS hook tries the warm daemon before cold-spawning."""

    _ENVELOPE = {
        "summary": {"procedure": "daemon_served_proc", "classifier_confidence": 0.9},
        "artifact": {"plan": {"named_paths": ["x.py"], "prefetched_facts": {"fact": "value"}}},
    }

    def _fake_daemon(self, in_tmp, envelope):
        """Loopback thread serving one canned envelope, daemon-file discovery
        exactly as `roam compile-daemon start` writes it."""
        import json as _json
        import socket
        import threading

        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.bind(("127.0.0.1", 0))
        srv.listen(1)
        port = srv.getsockname()[1]
        (in_tmp / ".roam").mkdir(exist_ok=True)
        (in_tmp / ".roam" / "compile-daemon.json").write_text(
            _json.dumps({"port": port, "token": "tok", "pid": 1}), encoding="utf-8"
        )
        got: dict = {}

        def serve():
            conn, _ = srv.accept()
            with conn:
                conn.settimeout(5)
                data = b""
                while b"\n" not in data:
                    b = conn.recv(65536)
                    if not b:
                        break
                    data += b
                got.update(_json.loads(data.decode("utf-8")))
                conn.sendall((_json.dumps(envelope) + "\n").encode("utf-8"))
            srv.close()

        threading.Thread(target=serve, daemon=True).start()
        return got

    def _run_hook(self, in_tmp, stdin_payload, env_extra=None):
        import os as _os
        import subprocess
        import sys

        hook = in_tmp / ".claude" / "hooks" / _CLAUDE_UPS_HOOK_FILENAME
        env = {**_os.environ, **(env_extra or {})}
        return subprocess.run(
            [sys.executable, str(hook)],
            input=stdin_payload,
            capture_output=True,
            text=True,
            timeout=30,
            cwd=str(in_tmp),
            env=env,
        )

    def test_daemon_served_envelope_is_injected(self, in_tmp):
        _invoke("claude", "--write")
        (in_tmp / ".git").mkdir()  # _repo_root anchor
        got = self._fake_daemon(in_tmp, self._ENVELOPE)
        proc = self._run_hook(in_tmp, '{"prompt": "investigate why login is slow", "session_id": "s-77"}')
        assert proc.returncode == 0
        assert "daemon_served_proc" in proc.stdout  # served warm, not cold-spawned
        assert got["op"] == "compile" and got["token"] == "tok"
        assert got["session_id"] == "s-77"  # telemetry join key rides the socket
        assert got["episode_id"].startswith("ep_")
        assert got["turn_seq"] == "1"
        event = json.loads((in_tmp / ".roam" / "episodes.jsonl").read_text().splitlines()[-1])
        assert event["event_type"] == "prompt_submitted"
        assert event["episode_id"] == got["episode_id"]
        assert event["turn_seq"] == 1
        assert "investigate why login is slow" not in (in_tmp / ".roam" / "episodes.jsonl").read_text()

    def test_daemon_error_falls_back_without_injecting_junk(self, in_tmp):
        """A daemon refusal (wrong_repo/bad_token) must never be injected; with
        no roam on PATH the cold fallback fails open -> empty output, exit 0."""
        _invoke("claude", "--write")
        (in_tmp / ".git").mkdir()
        self._fake_daemon(in_tmp, {"error": "wrong_repo"})
        proc = self._run_hook(in_tmp, '{"prompt": "investigate why login is slow"}', {"PATH": ""})
        assert proc.returncode == 0
        assert proc.stdout == ""

    def test_dead_daemon_config_fails_open(self, in_tmp):
        """Config present but nothing listening: connect fails inside the 10 ms
        budget -> cold path; with no roam on PATH -> quiet exit 0."""
        import json as _json

        _invoke("claude", "--write")
        (in_tmp / ".git").mkdir()
        (in_tmp / ".roam").mkdir(exist_ok=True)
        (in_tmp / ".roam" / "compile-daemon.json").write_text(
            _json.dumps({"port": 1, "token": "tok", "pid": 1}), encoding="utf-8"
        )
        proc = self._run_hook(in_tmp, '{"prompt": "investigate why login is slow"}', {"PATH": ""})
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

    def test_stop_hook_fails_closed_on_invalid_input(self, in_tmp):
        import subprocess
        import sys

        from roam.commands.cmd_hooks import _CLAUDE_STOP_HOOK_FILENAME

        _invoke("claude", "--write")
        hook = in_tmp / ".claude" / "hooks" / _CLAUDE_STOP_HOOK_FILENAME
        # garbage stdin cannot silently allow an unvalidated stop
        proc = subprocess.run([sys.executable, str(hook)], input="NOT JSON", capture_output=True, text=True, timeout=30)
        assert proc.returncode == 0
        assert json.loads(proc.stdout)["decision"] == "block"


_PASS_ENVELOPE = {
    "command": "verify",
    "summary": {
        "verdict": "PASS",
        "violation_count": 0,
        "files_checked": 1,
        "verification_complete": True,
        "partial_success": False,
    },
    "violations": [],
}

# Python stub standing in for the `roam` binary: appends an invocation marker
# and prints the canned envelope. Pure Python so it is hermetic on every OS
# (a bare-name `roam` PATH stub is NOT: Windows CreateProcess resolves
# `roam` to a real roam.exe on PATH and never to a stub roam.bat, so on dev
# machines with roam installed a PATH stub silently tests the real binary).
_ROAM_STUB_PY = """\
import json, os, sys
here = os.path.dirname(os.path.abspath(__file__))
with open(os.path.join(here, "roam-called.txt"), "a", encoding="utf-8") as fh:
    fh.write("called" + chr(10))
with open(os.path.join(here, "request.json"), "w", encoding="utf-8") as fh:
    json.dump({
        "argv": sys.argv[1:],
        "nonce": os.environ.get("ROAM_VERIFY_REQUEST_NONCE"),
        "scope_sha256": os.environ.get("ROAM_VERIFY_SCOPE_SHA256"),
        "content_sha256": os.environ.get("ROAM_VERIFY_CONTENT_SHA256"),
        "scope_count": os.environ.get("ROAM_VERIFY_SCOPE_COUNT"),
    }, fh, sort_keys=True)
with open(os.path.join(here, "envelope.json"), encoding="utf-8") as fh:
    raw = fh.read()
try:
    envelope = json.loads(raw)
except ValueError:
    envelope = None
if isinstance(envelope, dict) and envelope.get("command") == "verify":
    summary = envelope.get("summary")
    if isinstance(summary, dict) and "verification_receipt" not in summary:
        summary["verification_receipt"] = {
            "schema": "roam.verify.receipt.v3",
            "request_nonce": os.environ.get("ROAM_VERIFY_REQUEST_NONCE"),
            "scope_sha256": os.environ.get("ROAM_VERIFY_SCOPE_SHA256"),
            "content_sha256": os.environ.get("ROAM_VERIFY_CONTENT_SHA256"),
            "content_sha256_before": os.environ.get("ROAM_VERIFY_CONTENT_SHA256"),
            "content_sha256_after": os.environ.get("ROAM_VERIFY_CONTENT_SHA256"),
            "target_file_count": int(os.environ.get("ROAM_VERIFY_SCOPE_COUNT", "-1")),
            "scope_stable": True,
            "request_match": True,
        }
    raw = json.dumps(envelope)
mutation = os.environ.get("ROAM_STUB_MUTATE_PATH")
if mutation:
    target = mutation if os.path.isabs(mutation) else os.path.join(os.getcwd(), mutation)
    action = os.environ.get("ROAM_STUB_MUTATE_ACTION", "write")
    if action == "delete":
        os.unlink(target)
    else:
        os.makedirs(os.path.dirname(target), exist_ok=True)
        mode = "a" if action == "append" else "w"
        with open(target, mode, encoding="utf-8") as fh:
            fh.write(os.environ.get("ROAM_STUB_MUTATE_CONTENT", "mutated by verifier" + chr(10)))
sys.stdout.write(raw)
override = os.environ.get("ROAM_STUB_EXIT_CODE")
if override is not None:
    raise SystemExit(int(override))
try:
    verdict = str((json.loads(raw).get("summary") or {}).get("verdict") or "").upper()
except (AttributeError, ValueError):
    verdict = ""
raise SystemExit(5 if verdict.startswith("FAIL") else 0)
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
exec(compile(code, _SCRIPT, "exec"), {"__name__": "__main__", "__file__": _SCRIPT})
"""


def _install_roam_verify_stub(stub_root, envelope):
    """Write the roam stub + canned envelope. Returns (stub_dir, marker)."""
    stub_dir = stub_root / "bin"
    stub_dir.mkdir()
    (stub_dir / "roam-stub.py").write_text(_ROAM_STUB_PY, encoding="utf-8")
    (stub_dir / "envelope.json").write_text(json.dumps(envelope), encoding="utf-8")
    return stub_dir, stub_dir / "roam-called.txt"


def _hook_runtime(repo):
    from roam.commands.cmd_hooks import _CLAUDE_STOP_HOOK_SCRIPT, _CLAUDE_UPS_HOOK_SCRIPT

    runtime = repo.parent / "managed-hooks"
    runtime.mkdir(exist_ok=True)
    ups = runtime / "roam-compile-ups.py"
    stop = runtime / "roam-verify-stop.py"
    ups.write_text(_CLAUDE_UPS_HOOK_SCRIPT, encoding="utf-8")
    stop.write_text(_CLAUDE_STOP_HOOK_SCRIPT, encoding="utf-8")
    return ups, stop


def _hook_test_env(repo, env=None):
    fake_home = repo.parent / "hook-home"
    fake_home.mkdir(exist_ok=True)
    return {
        **os.environ,
        "ROAM_HOOK_EVIDENCE_DIR": str(repo.parent / "hook-evidence"),
        "HOME": str(fake_home),
        "USERPROFILE": str(fake_home),
        **(env or {}),
    }


def _run_prompt_hook(repo, payload, env=None):
    import shutil
    import subprocess
    import sys

    ups, _ = _hook_runtime(repo)
    git_bin = shutil.which("git")
    hook_path = os.path.dirname(git_bin) if git_bin else os.environ.get("PATH", "")
    return subprocess.run(
        [sys.executable, str(ups)],
        input=payload,
        capture_output=True,
        text=True,
        timeout=30,
        cwd=str(repo),
        env={**_hook_test_env(repo, env), "PATH": hook_path},
    )


def _run_stop_hook(repo, stub_dir, payload="{}", env=None):
    """Run the Stop-hook script (via the subprocess-shim driver) with
    cwd=repo. Script files live OUTSIDE the repo so the tree stays clean."""
    import subprocess
    import sys

    _, hook = _hook_runtime(repo)
    driver = repo.parent / "stop-hook-driver.py"
    driver.write_text(_STOP_HOOK_DRIVER_PY, encoding="utf-8")
    return subprocess.run(
        [sys.executable, str(driver), str(stub_dir / "roam-stub.py"), str(hook)],
        input=payload,
        capture_output=True,
        text=True,
        timeout=60,
        cwd=str(repo),
        env=_hook_test_env(repo, env),
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
            "command": "verify",
            "summary": {
                "verdict": "FAIL",
                "violation_count": 1,
                "files_checked": 1,
                "verification_complete": True,
                "partial_success": False,
            },
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

    def test_correction_continuation_reverifies_until_evidence_passes(self, tmp_path):
        repo = self._git_repo(tmp_path)
        (repo / "tracked.txt").write_text("changed\n", encoding="utf-8")
        failing = {
            "command": "verify",
            "summary": {
                "verdict": "FAIL",
                "violation_count": 1,
                "files_checked": 1,
                "verification_complete": True,
                "partial_success": False,
            },
            "violations": [
                {
                    "severity": "FAIL",
                    "category": "syntax",
                    "file": "tracked.txt",
                    "line": 1,
                    "message": "syntax failure",
                }
            ],
        }
        stub_dir, marker = _install_roam_verify_stub(tmp_path, failing)

        first = _run_stop_hook(repo, stub_dir, '{"stop_hook_active": false}')
        second = _run_stop_hook(repo, stub_dir, '{"stop_hook_active": true}')

        assert json.loads(first.stdout)["decision"] == "block"
        assert json.loads(second.stdout)["decision"] == "block"
        assert marker.read_text(encoding="utf-8").splitlines() == ["called", "called"]

        (stub_dir / "envelope.json").write_text(json.dumps(_PASS_ENVELOPE), encoding="utf-8")
        third = _run_stop_hook(repo, stub_dir, '{"stop_hook_active": true}')

        assert third.returncode == 0 and third.stdout == ""
        assert marker.read_text(encoding="utf-8").splitlines() == ["called", "called", "called"]

    def test_exit_verdict_mismatch_is_unavailable_not_pass(self, tmp_path):
        repo = self._git_repo(tmp_path)
        (repo / "tracked.txt").write_text("changed\n", encoding="utf-8")
        stub_dir, marker = _install_roam_verify_stub(tmp_path, _PASS_ENVELOPE)

        proc = _run_stop_hook(repo, stub_dir, env={"ROAM_STUB_EXIT_CODE": "5"})

        assert marker.exists()
        decision = json.loads(proc.stdout)
        assert decision["decision"] == "block"
        assert "complete post-edit evidence" in decision["reason"]

    def test_malformed_verify_envelope_blocks_an_edited_stop(self, tmp_path):
        repo = self._git_repo(tmp_path)
        (repo / "tracked.txt").write_text("changed\n", encoding="utf-8")
        stub_dir, marker = _install_roam_verify_stub(tmp_path, {})

        proc = _run_stop_hook(repo, stub_dir)

        assert proc.returncode == 0
        assert marker.exists()
        decision = json.loads(proc.stdout)
        assert decision["decision"] == "block"
        assert "complete post-edit evidence" in decision["reason"]
        assert self._stop_rows(repo)[-1]["blocked"] is True

    def test_partial_pass_envelope_blocks_an_edited_stop(self, tmp_path):
        repo = self._git_repo(tmp_path)
        (repo / "tracked.txt").write_text("changed\n", encoding="utf-8")
        envelope = {
            "command": "verify",
            "summary": {
                "verdict": "PASS",
                "violation_count": 0,
                "files_checked": 1,
                "verification_complete": False,
                "partial_success": True,
            },
            "violations": [],
        }
        stub_dir, _ = _install_roam_verify_stub(tmp_path, envelope)

        proc = _run_stop_hook(repo, stub_dir)

        decision = json.loads(proc.stdout)
        assert decision["decision"] == "block"
        assert "complete post-edit evidence" in decision["reason"]

    def test_commit_before_stop_is_verified_from_prompt_base_head(self, tmp_path):
        import subprocess

        repo = self._git_repo(tmp_path)
        initial_head = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=repo, check=True, capture_output=True, text=True
        ).stdout.strip()
        payload = json.dumps({"prompt": "repair the committed syntax path", "session_id": "commit-episode"})
        assert _run_prompt_hook(repo, payload).returncode == 0
        state_paths = list((repo.parent / "hook-evidence").rglob("*.json"))
        assert len(state_paths) == 1 and not state_paths[0].is_relative_to(repo)
        protected_state = json.loads(state_paths[0].read_text(encoding="utf-8"))
        assert protected_state["active"]["base_head"] == initial_head
        assert protected_state["active"]["policy_complete"] is True
        (repo / "tracked.txt").write_text("committed during episode\n", encoding="utf-8")
        subprocess.run(["git", "add", "tracked.txt"], cwd=repo, check=True, capture_output=True)
        subprocess.run(["git", "commit", "-q", "-m", "agent edit"], cwd=repo, check=True, capture_output=True)
        assert subprocess.run(["git", "diff", "--quiet", "HEAD"], cwd=repo, capture_output=True).returncode == 0

        stub_dir, marker = _install_roam_verify_stub(tmp_path, _PASS_ENVELOPE)
        proc = _run_stop_hook(repo, stub_dir, payload)

        assert proc.returncode == 0 and proc.stdout == ""
        assert marker.read_text(encoding="utf-8").splitlines() == ["called"]
        request = json.loads((stub_dir / "request.json").read_text(encoding="utf-8"))
        assert "tracked.txt" in request["argv"]
        assert request["scope_count"] == "1"
        assert len(request["nonce"]) == 32

    @pytest.mark.parametrize("path", ["tracked.txt", "draft.py"])
    def test_verifier_mutating_tracked_or_untracked_bytes_blocks(self, tmp_path, path):
        repo = self._git_repo(tmp_path)
        (repo / path).write_text("pre-verify bytes\n", encoding="utf-8")
        stub_dir, marker = _install_roam_verify_stub(tmp_path, _PASS_ENVELOPE)

        proc = _run_stop_hook(
            repo,
            stub_dir,
            env={
                "ROAM_STUB_MUTATE_PATH": path,
                "ROAM_STUB_MUTATE_CONTENT": "post-verify bytes\n",
            },
        )

        assert marker.exists()
        decision = json.loads(proc.stdout)
        assert decision["decision"] == "block"
        assert "[verification_race]" in decision["reason"]

    def test_verifier_mutating_suppressions_is_policy_tampering(self, tmp_path):
        repo = self._git_repo(tmp_path)
        (repo / "tracked.txt").write_text("changed\n", encoding="utf-8")
        suppressions = repo / ".roam-suppressions.yml"
        suppressions.write_text("suppressions: []\n", encoding="utf-8")
        stub_dir, marker = _install_roam_verify_stub(tmp_path, _PASS_ENVELOPE)

        proc = _run_stop_hook(
            repo,
            stub_dir,
            env={
                "ROAM_STUB_MUTATE_PATH": ".roam-suppressions.yml",
                "ROAM_STUB_MUTATE_CONTENT": "suppressions:\n  - rule: syntax\n",
            },
        )

        assert marker.exists()
        decision = json.loads(proc.stdout)
        assert "[policy_tampering]" in decision["reason"]

    def test_session_stop_without_protected_prompt_evidence_blocks_before_verify(self, tmp_path):
        repo = self._git_repo(tmp_path)
        (repo / "tracked.txt").write_text("changed\n", encoding="utf-8")
        stub_dir, marker = _install_roam_verify_stub(tmp_path, _PASS_ENVELOPE)
        payload = json.dumps({"session_id": "missing-prompt-evidence"})

        proc = _run_stop_hook(repo, stub_dir, payload)

        decision = json.loads(proc.stdout)
        assert "[policy_evidence_unavailable]" in decision["reason"]
        assert not marker.exists()

    @pytest.mark.parametrize(
        "relative_path,initial,mutated",
        [
            (".roam/verify.yaml", "enabled: true\n", "enabled: false\n"),
            (".roam-suppressions.yml", "suppressions: []\n", "suppressions:\n  - rule: syntax\n"),
            (".claude/hooks/roam-verify-stop.py", "# managed stop hook\n", "# bypassed stop hook\n"),
            (".claude/settings.json", '{"hooks": {}}\n', '{"hooks": {"Stop": []}}\n'),
        ],
    )
    def test_prompt_start_policy_hook_and_settings_mutation_blocks(self, tmp_path, relative_path, initial, mutated):
        repo = self._git_repo(tmp_path)
        target = repo.joinpath(*relative_path.split("/"))
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(initial, encoding="utf-8")
        payload = json.dumps({"prompt": "apply a source-only correction", "session_id": "policy-episode"})
        assert _run_prompt_hook(repo, payload).returncode == 0
        target.write_text(mutated, encoding="utf-8")
        stub_dir, marker = _install_roam_verify_stub(tmp_path, _PASS_ENVELOPE)

        proc = _run_stop_hook(repo, stub_dir, payload)

        decision = json.loads(proc.stdout)
        assert decision["decision"] == "block"
        assert "[policy_tampering]" in decision["reason"]
        assert not marker.exists(), "policy tampering must block before invoking Verify"

    @pytest.mark.parametrize(
        "schema",
        ["roam.verify.receipt.v1", "roam.verify.receipt.v2", "roam.verify.receipt.v3"],
    )
    def test_stale_or_wrong_receipt_never_satisfies_stop(self, tmp_path, schema):
        repo = self._git_repo(tmp_path)
        (repo / "tracked.txt").write_text("changed\n", encoding="utf-8")
        envelope = json.loads(json.dumps(_PASS_ENVELOPE))
        envelope["summary"]["verification_receipt"] = {
            "schema": schema,
            "request_nonce": "0" * 32,
            "scope_sha256": "1" * 64,
            "content_sha256": "2" * 64,
            "content_sha256_before": "2" * 64,
            "content_sha256_after": "2" * 64,
            "target_file_count": 1,
            "scope_stable": True,
            "request_match": True,
        }
        stub_dir, marker = _install_roam_verify_stub(tmp_path, envelope)

        proc = _run_stop_hook(repo, stub_dir)

        assert marker.exists()
        decision = json.loads(proc.stdout)
        assert decision["decision"] == "block"
        assert "receipt v3 required" in decision["reason"]

    def test_complete_rc0_pass_with_advisory_warn_is_allowed(self, tmp_path):
        repo = self._git_repo(tmp_path)
        (repo / "tracked.txt").write_text("changed\n", encoding="utf-8")
        envelope = json.loads(json.dumps(_PASS_ENVELOPE))
        envelope["summary"]["violation_count"] = 1
        envelope["violations"] = [
            {
                "severity": "WARN",
                "category": "dead",
                "file": "tracked.txt",
                "line": 1,
                "message": "advisory orphan",
            }
        ]
        stub_dir, _ = _install_roam_verify_stub(tmp_path, envelope)

        proc = _run_stop_hook(repo, stub_dir)

        assert proc.returncode == 0 and proc.stdout == ""
        assert "roam verify advisory" in proc.stderr

    @pytest.mark.parametrize("nested_only", [False, True])
    def test_pass_envelope_with_fail_finding_never_allows(self, tmp_path, nested_only):
        repo = self._git_repo(tmp_path)
        (repo / "tracked.txt").write_text("changed\n", encoding="utf-8")
        envelope = json.loads(json.dumps(_PASS_ENVELOPE))
        finding = {
            "severity": "FAIL",
            "category": "dead",
            "file": "tracked.txt",
            "line": 1,
            "message": "hard failure disguised as advisory",
        }
        if nested_only:
            envelope["categories"] = {"dead": {"violations": [finding]}}
        else:
            envelope["summary"]["violation_count"] = 1
            envelope["violations"] = [finding]
        stub_dir, _ = _install_roam_verify_stub(tmp_path, envelope)

        proc = _run_stop_hook(repo, stub_dir)

        decision = json.loads(proc.stdout)
        assert decision["decision"] == "block"
        assert "receipt v3 required" in decision["reason"]

    def test_prompt_and_stop_share_episode_identity(self, tmp_path):
        repo = self._git_repo(tmp_path)
        prompt_payload = json.dumps(
            {
                "prompt": "investigate the repeated login latency",
                "session_id": "joined-session",
                "transcript_path": str(tmp_path / "transcript.jsonl"),
            }
        )
        proc = _run_prompt_hook(repo, prompt_payload)
        assert proc.returncode == 0
        starts = [
            json.loads(line) for line in (repo / ".roam" / "episodes.jsonl").read_text(encoding="utf-8").splitlines()
        ]
        assert len(starts) == 1 and starts[0]["event_type"] == "prompt_submitted"

        stub_dir, _ = _install_roam_verify_stub(tmp_path, _PASS_ENVELOPE)
        stop_payload = json.dumps(
            {
                "session_id": "joined-session",
                "transcript_path": str(tmp_path / "transcript.jsonl"),
            }
        )
        stop = _run_stop_hook(repo, stub_dir, stop_payload)
        assert stop.returncode == 0 and stop.stdout == ""
        events = [
            json.loads(line) for line in (repo / ".roam" / "episodes.jsonl").read_text(encoding="utf-8").splitlines()
        ]
        assert [event["event_type"] for event in events] == ["prompt_submitted", "stop_decision"]
        assert events[0]["episode_id"] == events[1]["episode_id"]
        assert events[1]["terminal"] is True
        assert events[1]["outcome"] == "no_edit"
        assert events[1]["turn_seq"] == 1
        from roam.commands.cmd_hooks import _HOOK_BODY_VERSION

        assert events[0]["hook_version"] == events[1]["hook_version"] == _HOOK_BODY_VERSION
        assert events[0]["evidence_source"] == events[1]["evidence_source"] == "live_hook"
        assert events[1]["health_state"] == "not_applicable"

    def test_prompt_turn_sequence_is_monotonic_and_prompt_private(self, tmp_path):
        repo = self._git_repo(tmp_path)
        secret = "private-marker-should-never-land"
        payload = json.dumps({"prompt": f"investigate {secret}", "session_id": "sequence-session"})
        for _ in range(2):
            proc = _run_prompt_hook(repo, payload)
            assert proc.returncode == 0
        raw = (repo / ".roam" / "episodes.jsonl").read_text(encoding="utf-8")
        events = [json.loads(line) for line in raw.splitlines()]
        assert [event["turn_seq"] for event in events] == [1, 2]
        assert len({event["episode_id"] for event in events}) == 2
        assert secret not in raw


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

    @pytest.mark.parametrize("body_name", ["_CLAUDE_UPS_HOOK_SCRIPT", "_CLAUDE_STOP_HOOK_SCRIPT"])
    def test_cross_volume_evidence_root_is_treated_as_outside_repo(
        self,
        body_name,
        tmp_path,
        monkeypatch,
    ):
        """Windows raises ValueError for commonpath(C:\\..., D:\\...).

        That means the evidence root is outside the repo, not unavailable.
        Simulate the cross-volume result portably for both deployed hooks.
        """
        from roam.commands import cmd_hooks

        deployed = getattr(cmd_hooks, body_name)
        body, marker, tail = deployed.rpartition("\nmain()\n")
        assert marker and tail == ""
        namespace = {
            "__name__": "hook_cross_volume_contract",
            "__file__": str(tmp_path / "managed-hook.py"),
        }
        exec(compile(body, "managed-hook.py", "exec"), namespace)
        repo = tmp_path / "repo"
        repo.mkdir()
        evidence = tmp_path / "outside-evidence"
        monkeypatch.setenv("ROAM_HOOK_EVIDENCE_DIR", str(evidence))

        def different_volumes(_paths):
            raise ValueError("Paths don't have the same drive")

        monkeypatch.setattr(namespace["os"].path, "commonpath", different_volumes)
        assert os.path.normcase(namespace["_evidence_base"](str(repo), True)) == os.path.normcase(
            str(evidence.resolve())
        )

    def test_stop_hook_receipt_v3_digests_match_verify(self, tmp_path):
        from roam.commands import cmd_verify
        from roam.commands.cmd_hooks import _CLAUDE_STOP_HOOK_SCRIPT

        project = tmp_path / "digest-project"
        project.mkdir()
        (project / "alpha.py").write_bytes(b"print('alpha')\n")
        nested = project / "unicode"
        nested.mkdir()
        (nested / "beta.py").write_bytes("value = 'β'\n".encode())
        paths = ["unicode/beta.py", "missing.py", "alpha.py", "alpha.py"]
        body, marker, tail = _CLAUDE_STOP_HOOK_SCRIPT.rpartition("\nmain()\n")
        assert marker and tail == ""
        namespace = {"__name__": "hook_digest_contract", "__file__": str(tmp_path / "roam-verify-stop.py")}
        exec(compile(body, "roam-verify-stop.py", "exec"), namespace)

        hook_content, hook_error = namespace["_verification_content_sha256"](str(project), paths)
        verify_content, verify_error = cmd_verify._verification_content_sha256(project, paths)

        assert hook_error == verify_error is None
        assert hook_content == verify_content
        assert namespace["_scope_sha256"](paths) == cmd_verify._verification_scope_sha256(paths)

    @pytest.mark.parametrize(
        ("field", "value"),
        [
            ("content_sha256_before", "b" * 64),
            ("content_sha256_after", "b" * 64),
            ("scope_stable", False),
            ("scope_stable", None),
        ],
    )
    def test_stop_hook_rejects_unstable_receipt_v3_evidence(self, tmp_path, field, value):
        from roam.commands.cmd_hooks import _CLAUDE_STOP_HOOK_SCRIPT

        body, marker, tail = _CLAUDE_STOP_HOOK_SCRIPT.rpartition("\nmain()\n")
        assert marker and tail == ""
        namespace = {"__name__": "hook_receipt_contract", "__file__": str(tmp_path / "roam-verify-stop.py")}
        exec(compile(body, "roam-verify-stop.py", "exec"), namespace)
        digest = "a" * 64
        expected = {
            "schema": "roam.verify.receipt.v3",
            "request_nonce": "c" * 32,
            "scope_sha256": "d" * 64,
            "content_sha256": digest,
            "content_sha256_before": digest,
            "content_sha256_after": digest,
            "target_file_count": 1,
            "scope_stable": True,
            "request_match": True,
        }
        envelope = json.loads(json.dumps(_PASS_ENVELOPE))
        envelope["summary"]["verification_receipt"] = dict(expected)
        envelope["_hook_process_returncode"] = 0
        assert namespace["_verify_protocol_state"](envelope, expected) == "passed"

        envelope["summary"]["verification_receipt"][field] = value
        assert namespace["_verify_protocol_state"](envelope, expected) == "unavailable"

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

    def test_canonical_stop_body_owns_maintenance_override(self):
        """Roam owns its global-flag ordering; integrators need no surgery."""
        from roam.commands.cmd_hooks import _CLAUDE_STOP_HOOK_SCRIPT

        assert '["roam", "--override-mode", "verify"' in _CLAUDE_STOP_HOOK_SCRIPT
        assert 'maintenance_override = ["--override-mode"]' in _CLAUDE_STOP_HOOK_SCRIPT
        assert '["roam", *maintenance_override, "--json", *args]' in _CLAUDE_STOP_HOOK_SCRIPT

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
        assert "roam --override-mode verify --auto --report --persist" in popen_log
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
        assert first.count("roam --override-mode verify --auto --report --persist") == 1
        assert (repo / ".roam" / "verify-refresh-claim").exists()  # claim taken
        # second stop, report still absent (persist "never landed") -> no respawn
        (repo / "tracked.txt").write_text("changed again\n", encoding="utf-8")
        second = self._run(repo, stub_dir, {"ROAM_HOOK_REPORT_REFRESH": "1"})
        assert second.count("roam --override-mode verify --auto --report --persist") == 1  # log unchanged

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
        assert "roam --override-mode verify --auto --report --persist" in popen_log

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
