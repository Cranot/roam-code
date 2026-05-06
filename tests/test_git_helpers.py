"""Tests for the shared git_helpers module.

These tests assert the *contract* (returns string sentinels on failure,
returns stripped output on success) without depending on a specific
git repo state. Subprocess invocations are mocked so the tests run
fast and are independent of the host's git config.
"""

from __future__ import annotations

import re
import subprocess
from unittest.mock import patch

from roam.commands import git_helpers


def _ok(stdout: str = "value") -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(args=[], returncode=0, stdout=stdout, stderr="")


def _fail() -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(args=[], returncode=128, stdout="", stderr="fatal")


def test_git_actor_returns_email_when_set():
    with patch.object(git_helpers, "_run_git", return_value="dev@example.com"):
        assert git_helpers.git_actor() == "dev@example.com"


def test_git_actor_falls_back_to_unknown_when_neither_set():
    with patch.object(git_helpers, "_run_git", return_value=""):
        assert git_helpers.git_actor() == "<unknown>"


def test_git_actor_falls_back_to_user_name():
    # Email empty, name set -- second invocation returns name
    side = iter(["", "Alice Dev"])
    with patch.object(git_helpers, "_run_git", side_effect=lambda *_: next(side)):
        assert git_helpers.git_actor() == "Alice Dev"


def test_git_origin_url_returns_empty_on_failure():
    with patch("subprocess.run", side_effect=OSError("git not found")):
        assert git_helpers.git_origin_url() == ""


def test_git_origin_url_returns_stripped_value():
    with patch("subprocess.run", return_value=_ok("https://github.com/o/r.git\n")):
        assert git_helpers.git_origin_url() == "https://github.com/o/r.git"


def test_git_head_sha_returns_empty_on_nonzero():
    with patch("subprocess.run", return_value=_fail()):
        assert git_helpers.git_head_sha() == ""


def test_git_branch_returns_value():
    with patch("subprocess.run", return_value=_ok("main\n")):
        assert git_helpers.git_branch() == "main"


def test_git_metadata_omits_empty_keys():
    side = iter([_ok("abc123\n"), _fail(), _ok("https://x\n")])
    with patch("subprocess.run", side_effect=lambda *a, **kw: next(side)):
        meta = git_helpers.git_metadata()
    assert meta["git_sha"] == "abc123"
    assert "git_branch" not in meta  # second call failed, key omitted
    assert meta["git_origin"] == "https://x"


def test_git_metadata_returns_empty_dict_when_all_fail():
    with patch("subprocess.run", return_value=_fail()):
        assert git_helpers.git_metadata() == {}


def test_detect_roam_version_returns_string():
    v = git_helpers.detect_roam_version()
    assert isinstance(v, str)
    assert v != ""


def test_utc_timestamp_format_is_stable():
    ts = git_helpers.utc_timestamp()
    # Format: YYYY-MM-DDTHH:MM:SS.ffffffZ
    assert re.match(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{6}Z$", ts), ts


def test_utc_timestamp_ends_with_z_not_offset():
    ts = git_helpers.utc_timestamp()
    assert ts.endswith("Z")
    assert "+00:00" not in ts


def test_run_git_handles_subprocess_error():
    with patch("subprocess.run", side_effect=subprocess.SubprocessError("boom")):
        assert git_helpers._run_git(["git", "status"]) == ""


def test_run_git_returns_empty_on_blank_output():
    with patch("subprocess.run", return_value=_ok("   \n")):
        assert git_helpers._run_git(["git", "status"]) == ""
