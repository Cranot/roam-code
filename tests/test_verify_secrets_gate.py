"""The leak gate rides the compile/verify loop by default.

``roam verify`` gains a ``secrets`` category: built-in credential shapes
(``roam.security.redact.SECRET_PATTERNS``) at FAIL severity plus the
optional repo-local ``.roam-leak-patterns.py`` catalogue. The repo-local
catalogue is default-denied unless ``ROAM_ALLOW_REPO_LEAK_PATTERNS=1`` is
present in the process environment. That var is read from ``os.environ``
only, so a repo cannot set its own trust flag — a trusted checkout opts in
by exporting it, an untrusted/cloned repo never does, and the catalogue is
therefore never executed by default (untrusted-repo RCE fix).
"""

from __future__ import annotations

from pathlib import Path

from roam.commands.cmd_verify import (
    _DEFAULT_CHECKS,
    SEVERITY_FAIL,
    SEVERITY_WARN,
    _check_secrets,
    _load_repo_leak_patterns,
    auto_select_checks,
)

_REPO_LEAK_PATTERNS_ENVVAR = "ROAM_ALLOW_REPO_LEAK_PATTERNS"


def _write_repo_catalogue(tmp_path: Path, sentinel: Path, pattern: str = "ProjectNimbus") -> None:
    tmp_path.joinpath(".roam-leak-patterns.py").write_text(
        "from pathlib import Path\n"
        "import re\n"
        f"Path({str(sentinel)!r}).write_text('executed', encoding='utf-8')\n"
        f"FORBIDDEN_PATTERNS = [('codename', re.compile(r'{pattern}'))]\n",
        encoding="utf-8",
    )


def test_secrets_in_default_and_auto_sets():
    assert "secrets" in _DEFAULT_CHECKS
    assert "secrets" in auto_select_checks(["src/app.py"])
    # Test-only edits still get the leak gate (credentials are wrong anywhere).
    assert "secrets" in auto_select_checks(["tests/test_app.py"])


def test_repo_local_catalogue_requires_explicit_opt_in(tmp_path: Path, monkeypatch):
    sentinel = tmp_path / "executed.txt"
    _write_repo_catalogue(tmp_path, sentinel)

    monkeypatch.delenv(_REPO_LEAK_PATTERNS_ENVVAR, raising=False)
    patterns, should_scan, err = _load_repo_leak_patterns(tmp_path)
    assert patterns == []
    assert should_scan is None
    assert err == (
        ".roam-leak-patterns.py present but not executed "
        "(untrusted repo config; set ROAM_ALLOW_REPO_LEAK_PATTERNS=1 to enable)"
    )
    assert not sentinel.exists()

    result = _check_secrets(["notes.md"], tmp_path)
    assert result["violations"] == []
    assert result["repo_patterns_error"] == err
    assert not sentinel.exists()

    monkeypatch.setenv(_REPO_LEAK_PATTERNS_ENVVAR, "1")
    patterns, should_scan, err = _load_repo_leak_patterns(tmp_path)
    assert err is None
    assert len(patterns) == 1
    assert should_scan is None
    assert sentinel.exists()


def test_builtin_credential_shape_is_fail_with_repo_catalogue_gated_off(tmp_path: Path, monkeypatch):
    sentinel = tmp_path / "executed.txt"
    _write_repo_catalogue(tmp_path, sentinel)
    bad = tmp_path / "config.py"
    bad.write_text('TOKEN = "ghp_' + "a" * 36 + '"\n', encoding="utf-8")

    monkeypatch.delenv(_REPO_LEAK_PATTERNS_ENVVAR, raising=False)
    result = _check_secrets(["config.py"], tmp_path)
    assert result["violations"], result
    v = result["violations"][0]
    assert v["category"] == "secrets"
    assert v["severity"] == SEVERITY_FAIL
    assert "github_pat_classic" in v["message"]
    assert result["score"] < 100
    assert result["repo_patterns_error"].startswith(".roam-leak-patterns.py present but not executed")
    assert not sentinel.exists()


def test_repo_local_catalogue_is_warn_when_explicitly_opted_in(tmp_path: Path, monkeypatch):
    sentinel = tmp_path / "executed.txt"
    _write_repo_catalogue(tmp_path, sentinel)
    f = tmp_path / "notes.md"
    f.write_text("status update for ProjectNimbus rollout\n", encoding="utf-8")

    monkeypatch.setenv(_REPO_LEAK_PATTERNS_ENVVAR, "1")
    result = _check_secrets(["notes.md"], tmp_path)
    assert result["violations"], result
    v = result["violations"][0]
    assert v["severity"] == SEVERITY_WARN
    assert "codename" in v["message"]
    assert result["repo_pattern_count"] == 1
    assert "repo_patterns_error" not in result
    assert sentinel.exists()


def test_repo_catalogue_should_scan_exemptions(tmp_path: Path, monkeypatch):
    (tmp_path / ".roam-leak-patterns.py").write_text(
        "import re\n"
        "FORBIDDEN_PATTERNS = [('codename', re.compile(r'ProjectNimbus'))]\n"
        "def should_scan(rel):\n"
        "    return rel != 'docs/allowed.md'\n",
        encoding="utf-8",
    )
    allowed = tmp_path / "docs"
    allowed.mkdir()
    (allowed / "allowed.md").write_text("ProjectNimbus is exempt here\n", encoding="utf-8")

    monkeypatch.setenv(_REPO_LEAK_PATTERNS_ENVVAR, "1")
    result = _check_secrets(["docs/allowed.md"], tmp_path)
    assert result["violations"] == [], result


def test_broken_repo_catalogue_fails_open_with_disclosure(tmp_path: Path, monkeypatch):
    (tmp_path / ".roam-leak-patterns.py").write_text("raise RuntimeError('boom')\n", encoding="utf-8")
    f = tmp_path / "app.py"
    f.write_text("x = 1\n", encoding="utf-8")

    monkeypatch.setenv(_REPO_LEAK_PATTERNS_ENVVAR, "1")
    result = _check_secrets(["app.py"], tmp_path)
    # Never crashes the gate; the failure is disclosed, not silent.
    assert "repo_patterns_error" in result
    assert result["violations"] == []


def test_roam_codes_own_shim_loads(monkeypatch):
    """The repo's own .roam-leak-patterns.py exposes the shared catalogue."""
    from roam.commands.cmd_verify import _load_repo_leak_patterns
    from tests._helpers.repo_root import repo_root

    monkeypatch.setenv(_REPO_LEAK_PATTERNS_ENVVAR, "1")
    patterns, should_scan, err = _load_repo_leak_patterns(repo_root())
    assert err is None
    assert len(patterns) >= 20  # the full internal-language catalogue
    assert callable(should_scan)
    # The exemplar suite is exempt via the catalogue's own whitelist.
    assert should_scan("src/roam/cli.py") is True
    assert should_scan("tests/test_leak_gate_exemplars.py") is False


def test_credential_fail_floors_verdict_below_pass(tmp_path, monkeypatch):
    """A FAIL credential can never be averaged into a quiet PASS."""
    import json as _json

    proj = tmp_path / "leakrepo"
    proj.mkdir()
    (proj / ".gitignore").write_text(".roam/\n", encoding="utf-8")
    (proj / "app.py").write_text("def main():\n    return 0\n", encoding="utf-8")
    import sys as _sys

    _sys.path.insert(0, str(Path(__file__).parent))
    from click.testing import CliRunner
    from conftest import git_init, index_in_process, invoke_cli  # noqa: E402

    git_init(proj)
    index_in_process(proj)
    (proj / "app.py").write_text('def main():\n    return 0\nTOKEN = "ghp_' + "a" * 36 + '"\n', encoding="utf-8")
    monkeypatch.chdir(proj)
    res = invoke_cli(CliRunner(), ["--json", "verify", "--auto"], cwd=proj)
    assert res.exit_code in (0, 1, 5), res.output
    d = _json.loads(res.output)
    verdict = str(d["summary"]["verdict"])
    assert not verdict.upper().startswith("PASS"), verdict


import os as _os

import pytest as _pytest


@_pytest.mark.skipif(
    _os.name == "nt",
    reason="POSIX shell PATH stub cannot execute on Windows (CreateProcess "
    "resolves the real roam.exe past it) — pre-existing platform limitation, "
    "verified against the pre-fast-exit hook too; hermetic subprocess-shim "
    "port tracked (see test_hooks_claude_setup driver).",
)
def test_stop_hook_blocks_with_autofix_directive(tmp_path, monkeypatch):
    """On findings the shipped Stop hook emits a decision-block with the
    AUTO-FIX directive (on by default); quiet on PASS."""
    import json as _json
    import os
    import subprocess
    import sys as _sys

    from roam.commands.cmd_hooks import _CLAUDE_STOP_HOOK_SCRIPT

    hook = tmp_path / "stop.py"
    hook.write_text(_CLAUDE_STOP_HOOK_SCRIPT, encoding="utf-8")

    violation = {
        "category": "secrets",
        "severity": "FAIL",
        "file": "app.py",
        "line": 3,
        "message": "credential-shaped string (github_pat_classic) in `app.py`",
        "fix": "Remove the credential and rotate it",
    }
    envelope = {
        "command": "verify",
        "summary": {
            "verdict": "FAIL",
            "violation_count": 1,
            "files_checked": 1,
            "verification_complete": True,
            "partial_success": False,
        },
        "categories": {"secrets": {"score": 0, "violations": [violation]}},
        "violations": [violation],
    }
    stub_dir = tmp_path / "bin"
    stub_dir.mkdir()
    stub = stub_dir / "roam"
    stub.write_text(f"#!/bin/sh\ncat <<'EOF'\n{_json.dumps(envelope)}\nEOF\nexit 5\n", encoding="utf-8")
    stub.chmod(0o755)
    monkeypatch.setenv("PATH", f"{stub_dir}:{os.environ['PATH']}")

    # cwd is pinned to tmp_path (NOT a git repo) so the hook's empty-diff
    # fast-exit stays out of play (git error -> fail-open -> verify runs) and
    # the test no longer depends on the checkout's working-tree state — on CI
    # the clean repo tree made the fast-exit skip verify and emit nothing.
    proc = subprocess.run(
        [_sys.executable, str(hook)],
        input=_json.dumps({"stop_hook_active": False}),
        capture_output=True,
        text=True,
        timeout=30,
        cwd=tmp_path,
    )
    assert proc.returncode == 0
    out = _json.loads(proc.stdout)
    assert out["decision"] == "block"
    assert "AUTO-FIX" in out["reason"]
    assert "app.py:3" in out["reason"]

    # A correction continuation must re-run the gate and remain blocked while
    # the secret finding survives; Claude Code provides the global loop cap.
    proc2 = subprocess.run(
        [_sys.executable, str(hook)],
        input=_json.dumps({"stop_hook_active": True}),
        capture_output=True,
        text=True,
        timeout=30,
        cwd=tmp_path,
    )
    assert proc2.returncode == 0
    assert _json.loads(proc2.stdout)["decision"] == "block"
    assert "app.py:3" in _json.loads(proc2.stdout)["reason"]
