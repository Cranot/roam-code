"""A file discovery never opened because it was too big is a blind spot.

``_filter_files`` dropped any path over ``MAX_FILE_SIZE`` (1 MB) with a bare
``continue``. The file was then in NEITHER the discovered set nor the indexed
set, so ``cmd_secrets._count_unindexed`` -- which computes
``discovered - indexed`` -- subtracted it to zero and every blind-spot
counter read clean.

Measured against the tree that shipped, on a repo of two tracked files where
only the oversized one holds a credential::

    small.py      39 B   def helper(x): ...
    big.py     1.4 MB    ... AWS_KEY = "<a live-shaped AWS key>"  (last line)

    $ roam index && roam secrets --fail-on-found
      VERDICT: No secrets found (1 files scanned)
      No hardcoded secrets detected (1 files scanned).                rc 0
    $ roam --json secrets
      files_scanned 1, files_filtered 0, files_undiscoverable 0,
      files_unindexed 0, files_unreadable 0,
      scan_incomplete false, partial_success false, total_findings 0

Positive control: the identical key in the 39-byte file IS reported, so the
pattern fires -- only the oversized file was invisible.

WHY THE FIX MEASURES INSTEAD OF REFUSING
----------------------------------------
Commit 93f9655e chose ``discover_files`` as the yardstick on the premise
that "files discovery deliberately excludes are not blind spots". That is
true of generated artefacts -- a scope DECISION -- and false of a resource
cap, which is roam declining to look. But the cap is an INDEXER bound:
building a symbol graph over a multi-megabyte file is expensive, and a
line-oriented regex sweep is not. So ``secrets`` now recovers and scans
those files rather than refusing to answer, which is the "measure what was
claimed" arm of the rule rather than the "say what was measured" arm.

Measured on roam-code itself: 3 tracked files exceed the 1 MB cap
(CHANGELOG.md, the generated changelog.html, one JSON fixture); all three
are now read, and the scan still finds nothing, so the repo's own CI gate
is unchanged.

A second, much larger bound (``SECRETS_MAX_RECOVERABLE_SIZE``) still
applies -- and unlike the first one it is LOUD: a file past it is named,
counted, and makes ``--fail-on-found`` refuse.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from roam.index.discovery import (
    MAX_FILE_SIZE,
    SKIP_OVERSIZED,
    discover_files,
    discover_files_with_skips,
)

#: Vendor-shaped but synthetic: the AWS prefix plus 16 uppercase alphanumerics,
#: matching the shipped `AKIA[0-9A-Z]{16}` pattern at RUNTIME while never
#: appearing as one literal in this source. Split for the same reason
#: `test_leak_gate_blind_spots.py` splits its own fixture: this repository's
#: pre-push secret scan reads the committed bytes, so a test that proves the
#: scanner catches a credential must not itself ship one it would catch. The
#: assembled value is the real shape -- the fixture is not weakened, only its
#: spelling in the file is.
_VENDOR_PREFIX = "AK" + "IA"
_VENDOR_TAIL = "3KJ7QWZX" + "CVBNMLKJ"
AWS_KEY_LINE = f'AWS_KEY = "{_VENDOR_PREFIX}{_VENDOR_TAIL}"\n'


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(
        ["git", *args],
        cwd=cwd,
        check=True,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
    )


def _init(cwd: Path) -> None:
    """`roam init`, then drop the .roamignore it writes.

    That file is discovered but never indexed (no language), so it makes
    ``_count_unindexed`` report 1 in every freshly-initialised fixture --
    a pre-existing false positive on a DIFFERENT axis than the one under
    test here. Removing it keeps the unindexed term at zero so an assertion
    about the SIZE-CAP term cannot pass or fail for the wrong reason.
    """
    built = _run(cwd, "init")
    assert built.returncode == 0, built.stdout[-800:] + built.stderr[-800:]
    (cwd / ".roamignore").unlink(missing_ok=True)


def _run(cwd: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "roam", *args],
        cwd=cwd,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=600,
        env=dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUTF8="1", NO_COLOR="1"),
    )


def _oversized_repo(root: Path, *, small_has_key: bool) -> Path:
    """A committed repo whose big.py holds an AWS key past the 1 MB cap."""
    root.mkdir(parents=True, exist_ok=True)
    _git(root, "init", "-q", ".")
    _git(root, "config", "user.email", "fixture@example.invalid")
    _git(root, "config", "user.name", "fixture")
    (root / "small.py").write_text(
        AWS_KEY_LINE if small_has_key else "def helper(x):\n    return x + 1\n",
        encoding="utf-8",
    )
    padding = "x = 1\n" * 200_000
    (root / "big.py").write_text(padding + AWS_KEY_LINE, encoding="utf-8")
    assert (root / "big.py").stat().st_size > MAX_FILE_SIZE
    _git(root, "add", "small.py", "big.py")
    _git(root, "commit", "-q", "-m", "fixture")
    return root


# ---------------------------------------------------------------------------
# Discovery: the skip must be reportable, not only applied
# ---------------------------------------------------------------------------


def test_discovery_reports_the_size_skip_it_applies(tmp_path: Path) -> None:
    repo = _oversized_repo(tmp_path / "repo", small_has_key=False)

    discovered, skips = discover_files_with_skips(repo)

    assert "small.py" in discovered
    assert "big.py" not in discovered, "the cap must still apply -- this fix counts it, it does not remove it"
    assert skips.get(SKIP_OVERSIZED) == ["big.py"], skips


def test_the_bare_discovery_helper_is_unchanged(tmp_path: Path) -> None:
    """The 20-odd callers that only want paths must not have to change."""
    repo = _oversized_repo(tmp_path / "repo", small_has_key=False)
    discovered, _skips = discover_files_with_skips(repo)
    assert discover_files(repo) == discovered


def test_scope_decisions_are_not_recorded_as_skips(tmp_path: Path) -> None:
    """Only resource caps are blind spots.

    A path excluded by extension, ignore pattern or generated-content marker
    is a decision about relevance; recording those would make the signal
    fire on every repository and be tuned out.
    """
    repo = tmp_path / "scoped"
    repo.mkdir()
    _git(repo, "init", "-q", ".")
    _git(repo, "config", "user.email", "fixture@example.invalid")
    _git(repo, "config", "user.name", "fixture")
    (repo / "a.py").write_text("x = 1\n", encoding="utf-8")
    (repo / "logo.png").write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 32)
    (repo / "thing_pb2.py").write_text("x = 2\n", encoding="utf-8")
    _git(repo, "add", "a.py", "logo.png", "thing_pb2.py")
    _git(repo, "commit", "-q", "-m", "fixture")

    discovered, skips = discover_files_with_skips(repo)
    assert "logo.png" not in discovered
    assert "thing_pb2.py" not in discovered
    assert skips == {}, f"scope exclusions must not be reported as coverage gaps: {skips}"


# ---------------------------------------------------------------------------
# secrets: the credential in the oversized file
# ---------------------------------------------------------------------------


def test_secrets_finds_the_key_in_the_oversized_file(tmp_path: Path) -> None:
    repo = _oversized_repo(tmp_path / "repo", small_has_key=False)
    _init(repo)

    run = _run(repo, "secrets", "--fail-on-found")

    assert run.returncode != 0, (
        "`roam secrets --fail-on-found` certified a repo whose only credential "
        f"sits in a file the size cap hid.\nstdout: {run.stdout.strip()[:800]!r}"
    )
    assert "No secrets found" not in run.stdout, run.stdout[:800]
    assert "big.py" in run.stdout, run.stdout[:800]


def test_secrets_envelope_names_the_recovered_files(tmp_path: Path) -> None:
    repo = _oversized_repo(tmp_path / "repo", small_has_key=False)
    _init(repo)

    run = _run(repo, "--json", "secrets")
    summary = json.loads(run.stdout[run.stdout.find("{") :])["summary"]

    assert summary["files_oversized_recovered"] == 1, summary
    assert summary["oversized_recovered_paths"] == ["big.py"], summary
    assert summary["files_oversized_unscanned"] == 0, summary
    assert summary["total_findings"] == 1, summary


def test_the_positive_control_still_fires(tmp_path: Path) -> None:
    """The identical key in a small file was always reported.

    Without this the "big.py is invisible" claim could just be a pattern
    that never matches.
    """
    repo = _oversized_repo(tmp_path / "repo", small_has_key=True)
    _init(repo)

    run = _run(repo, "--json", "secrets")
    payload = json.loads(run.stdout[run.stdout.find("{") :])
    files = {f["value"]["file"] for f in payload["findings"]}
    assert files == {"small.py", "big.py"}, files


def test_past_the_scanner_bound_the_file_is_named_not_silently_dropped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The second cap is kept, and it is loud.

    The bound is lowered here rather than writing a 25 MB fixture: the
    property is "a file past the scanner's own bound refuses instead of
    certifying", not the particular number.
    """
    from roam.commands import cmd_secrets

    repo = _oversized_repo(tmp_path / "repo", small_has_key=False)
    _init(repo)
    monkeypatch.setattr(cmd_secrets, "SECRETS_MAX_RECOVERABLE_SIZE", MAX_FILE_SIZE)

    from click.testing import CliRunner

    from roam.cli import cli

    monkeypatch.chdir(repo)
    result = CliRunner().invoke(cli, ["--json", "secrets", "--fail-on-found"])

    assert result.exit_code != 0, result.output[:800]
    # The gate's refusal message follows the envelope on the same stream, so
    # decode the first object rather than the whole buffer.
    payload, _end = json.JSONDecoder().raw_decode(result.output[result.output.find("{") :])
    summary = payload["summary"]
    assert summary["files_oversized_recovered"] == 0, summary
    assert summary["files_oversized_unscanned"] == 1, summary
    assert summary["oversized_unscanned_paths"] == ["big.py"], summary
    assert summary["scan_incomplete"] is True, summary
    assert summary["partial_success"] is True, summary
    assert "NOT PROVEN CLEAN" in summary["verdict"], summary


def test_a_repo_with_no_oversized_files_is_unchanged(tmp_path: Path) -> None:
    """The must-not-fire control.

    Without it the fix could mark every ordinary repository partial.
    """
    repo = tmp_path / "ordinary"
    repo.mkdir()
    _git(repo, "init", "-q", ".")
    _git(repo, "config", "user.email", "fixture@example.invalid")
    _git(repo, "config", "user.name", "fixture")
    (repo / "a.py").write_text("def helper(x):\n    return x + 1\n", encoding="utf-8")
    _git(repo, "add", "a.py")
    _git(repo, "commit", "-q", "-m", "fixture")
    _init(repo)

    run = _run(repo, "--json", "secrets", "--fail-on-found")
    assert run.returncode == 0, run.stdout[:800] + run.stderr[:800]
    summary = json.loads(run.stdout[run.stdout.find("{") :])["summary"]
    assert summary["files_oversized_recovered"] == 0, summary
    assert summary["files_oversized_unscanned"] == 0, summary
    assert summary["scan_incomplete"] is False, summary
    assert summary["partial_success"] is False, summary
