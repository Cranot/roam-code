"""Hook-body history seeder regressions."""

from __future__ import annotations

import hashlib
import re

from scripts import seed_hook_body_shas


def test_git_history_is_decoded_as_utf8_not_host_locale(monkeypatch):
    observed = {}

    class Result:
        stdout = "history\n"

    def fake_run(argv, **kwargs):
        observed["argv"] = argv
        observed["kwargs"] = kwargs
        return Result()

    monkeypatch.setattr(seed_hook_body_shas.subprocess, "run", fake_run)

    assert seed_hook_body_shas.git("log", "--format=%H") == "history\n"
    assert observed["argv"][:3] == ["git", "-C", str(seed_hook_body_shas.REPO)]
    assert observed["kwargs"] == {
        "capture_output": True,
        "text": True,
        "encoding": "utf-8",
        "errors": "strict",
        "check": True,
    }


def test_every_body_in_current_git_history_is_registered(capsys):
    from roam.commands.cmd_hooks import (
        _CLAUDE_STOP_HOOK_SCRIPT,
        _CLAUDE_UPS_HOOK_SCRIPT,
        _KNOWN_HOOK_BODY_SHAS,
    )

    assert seed_hook_body_shas.main() == 0
    generated = set(re.findall(r'"([0-9a-f]{64})"', capsys.readouterr().out))
    current = {
        hashlib.sha256(_CLAUDE_UPS_HOOK_SCRIPT.encode("utf-8")).hexdigest(),
        hashlib.sha256(_CLAUDE_STOP_HOOK_SCRIPT.encode("utf-8")).hexdigest(),
    }

    assert generated
    assert generated - current <= _KNOWN_HOOK_BODY_SHAS
