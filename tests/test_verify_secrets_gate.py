"""The leak gate rides the compile/verify loop by default.

``roam verify`` gains a ``secrets`` category: built-in credential shapes
(``roam.security.redact.SECRET_PATTERNS``) at FAIL severity plus the
optional repo-local ``.roam-leak-patterns.py`` catalogue at WARN. It is in
``_DEFAULT_CHECKS`` and the ``--auto`` set, so the Claude Code Stop hook
installed by `roam hooks claude` / `compile wire claude` runs it on every
edit round with zero configuration.
"""

from __future__ import annotations

from pathlib import Path

from roam.commands.cmd_verify import (
    _DEFAULT_CHECKS,
    SEVERITY_FAIL,
    SEVERITY_WARN,
    _check_secrets,
    auto_select_checks,
)


def test_secrets_in_default_and_auto_sets():
    assert "secrets" in _DEFAULT_CHECKS
    assert "secrets" in auto_select_checks(["src/app.py"])
    # Test-only edits still get the leak gate (credentials are wrong anywhere).
    assert "secrets" in auto_select_checks(["tests/test_app.py"])


def test_builtin_credential_shape_is_fail(tmp_path: Path):
    bad = tmp_path / "config.py"
    bad.write_text('TOKEN = "ghp_' + "a" * 36 + '"\n')
    result = _check_secrets(["config.py"], tmp_path)
    assert result["violations"], result
    v = result["violations"][0]
    assert v["category"] == "secrets"
    assert v["severity"] == SEVERITY_FAIL
    assert "github_pat_classic" in v["message"]
    assert result["score"] < 100


def test_clean_file_scores_100(tmp_path: Path):
    ok = tmp_path / "app.py"
    ok.write_text("def main():\n    return 0\n")
    result = _check_secrets(["app.py"], tmp_path)
    assert result["violations"] == []
    assert result["score"] == 100


def test_repo_local_catalogue_is_warn(tmp_path: Path):
    (tmp_path / ".roam-leak-patterns.py").write_text(
        "import re\nFORBIDDEN_PATTERNS = [('codename', re.compile(r'ProjectNimbus'))]\n"
    )
    f = tmp_path / "notes.md"
    f.write_text("status update for ProjectNimbus rollout\n")
    result = _check_secrets(["notes.md"], tmp_path)
    assert result["violations"], result
    v = result["violations"][0]
    assert v["severity"] == SEVERITY_WARN
    assert "codename" in v["message"]
    assert result["repo_pattern_count"] == 1


def test_repo_catalogue_should_scan_exemptions(tmp_path: Path):
    (tmp_path / ".roam-leak-patterns.py").write_text(
        "import re\n"
        "FORBIDDEN_PATTERNS = [('codename', re.compile(r'ProjectNimbus'))]\n"
        "def should_scan(rel):\n"
        "    return rel != 'docs/allowed.md'\n"
    )
    allowed = tmp_path / "docs"
    allowed.mkdir()
    (allowed / "allowed.md").write_text("ProjectNimbus is exempt here\n")
    result = _check_secrets(["docs/allowed.md"], tmp_path)
    assert result["violations"] == [], result


def test_broken_repo_catalogue_fails_open_with_disclosure(tmp_path: Path):
    (tmp_path / ".roam-leak-patterns.py").write_text("raise RuntimeError('boom')\n")
    f = tmp_path / "app.py"
    f.write_text("x = 1\n")
    result = _check_secrets(["app.py"], tmp_path)
    # Never crashes the gate; the failure is disclosed, not silent.
    assert "repo_patterns_error" in result
    assert result["violations"] == []


def test_roam_codes_own_shim_loads():
    """The repo's own .roam-leak-patterns.py exposes the shared catalogue."""
    from roam.commands.cmd_verify import _load_repo_leak_patterns
    from tests._helpers.repo_root import repo_root

    patterns, should_scan, err = _load_repo_leak_patterns(repo_root())
    assert err is None
    assert len(patterns) >= 20  # the full internal-language catalogue
    assert callable(should_scan)
    # The exemplar suite is exempt via the catalogue's own whitelist.
    assert should_scan("src/roam/cli.py") is True
    assert should_scan("tests/test_leak_gate_exemplars.py") is False


def test_credential_fail_floors_verdict_below_pass(tmp_path: Path, monkeypatch):
    """A FAIL credential can never be averaged into a quiet PASS."""
    import json as _json

    proj = tmp_path / "leakrepo"
    proj.mkdir()
    (proj / ".gitignore").write_text(".roam/\n")
    (proj / "app.py").write_text("def main():\n    return 0\n")
    import sys as _sys

    _sys.path.insert(0, str(Path(__file__).parent))
    from click.testing import CliRunner
    from conftest import git_init, index_in_process, invoke_cli  # noqa: E402

    git_init(proj)
    index_in_process(proj)
    (proj / "app.py").write_text('def main():\n    return 0\nTOKEN = "ghp_' + "a" * 36 + '"\n')
    monkeypatch.chdir(proj)
    res = invoke_cli(CliRunner(), ["--json", "verify", "--auto"], cwd=proj)
    assert res.exit_code in (0, 1, 5), res.output
    d = _json.loads(res.output)
    verdict = str(d["summary"]["verdict"])
    assert not verdict.upper().startswith("PASS"), verdict


def test_stop_hook_blocks_with_autofix_directive(tmp_path, monkeypatch):
    """On findings the shipped Stop hook emits a decision-block with the
    AUTO-FIX directive (on by default); quiet on PASS."""
    import json as _json
    import os
    import subprocess
    import sys as _sys

    from roam.commands.cmd_hooks import _CLAUDE_STOP_HOOK_SCRIPT

    hook = tmp_path / "stop.py"
    hook.write_text(_CLAUDE_STOP_HOOK_SCRIPT)

    envelope = {
        "summary": {"verdict": "WARN 79/100"},
        "categories": {
            "secrets": {
                "score": 75,
                "violations": [
                    {
                        "category": "secrets",
                        "severity": "FAIL",
                        "file": "app.py",
                        "line": 3,
                        "message": "credential-shaped string (github_pat_classic) in `app.py`",
                        "fix": "Remove the credential and rotate it",
                    }
                ],
            }
        },
    }
    stub_dir = tmp_path / "bin"
    stub_dir.mkdir()
    stub = stub_dir / "roam"
    stub.write_text(f"#!/bin/sh\ncat <<'EOF'\n{_json.dumps(envelope)}\nEOF\n")
    stub.chmod(0o755)
    monkeypatch.setenv("PATH", f"{stub_dir}:{os.environ['PATH']}")

    proc = subprocess.run(
        [_sys.executable, str(hook)],
        input=_json.dumps({"stop_hook_active": False}),
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert proc.returncode == 0
    out = _json.loads(proc.stdout)
    assert out["decision"] == "block"
    assert "AUTO-FIX" in out["reason"]
    assert "app.py:3" in out["reason"]

    # And the loop guard: when stop_hook_active, stay silent.
    proc2 = subprocess.run(
        [_sys.executable, str(hook)],
        input=_json.dumps({"stop_hook_active": True}),
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert proc2.stdout.strip() == ""
