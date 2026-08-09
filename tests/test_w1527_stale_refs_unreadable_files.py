"""W1527 -- `stale-refs --gate` said "all refs resolve" about files it never read.

The defect. A file the scan could not read was dropped from the numerator AND
the denominator, with no counter anywhere, so the broken link it held simply
ceased to exist. Both causes reproduce as an exit-code flip from 5 to 0, with
JSON written outside the scanned tree so the denominator is unambiguous.

ARM 1 -- the 1 MB resource cap. `docs/big.md` holds
`[the setup doc](docs/does-not-exist-A.md)`::

    CONTROL (file small)          rc 5   refs_checked 2   stale_refs 1
    DEFECT  (grown to 1,394,052)  rc 0   refs_checked 1   stale_refs 0
      verdict "all refs resolve - 1 checked - 6 files"

ARM 2 -- genuine unreadability. `docs/locked.md` under an icacls deny, direct
`open()` confirmed to raise PermissionError errno 13::

    CONTROL  rc 5   refs_checked 2   stale_refs 1
    DEFECT   rc 0   refs_checked 1   stale_refs 0

`partial_success` was False in both defect runs, and `next_steps` still said
"Add `roam stale-refs --gate --diff` to CI to keep the repo this clean".

MECHANISM -- and the finding that reported this had it half wrong, which
matters because it changes the fix. Only ARM 2 goes through
`_read_text_safe`'s bare `None`. ARM 1 never reaches it: the 1 MB cap lives in
`roam.index.discovery`, and the path is gone before `_scan_project` sees it.
Patching only the reader left `files_unreadable: 0` on the oversize arm --
measured, which is how the second cause was found.

Discovery had ALREADY built the mechanism for exactly this, under W1466, after
`roam secrets --fail-on-found` exited 0 over a tracked file holding a live AWS
key: `discover_files_with_skips` returns `(paths, skips)` keyed by
`SKIP_OVERSIZED` / `SKIP_UNSTATABLE`, and `discover_files`'s own docstring says
"anything that reports coverage must use the ``_with_skips`` form". A gate that
certifies "all refs resolve" is reporting coverage. stale-refs was calling the
lossy projection.

So the fix has two sources feeding one list: discovery's resource-cap skips
(filtered to scannable, non-ignored paths, because a scope DECISION is not a
blind spot) and the reader's own OSError, which now names its cause so the two
remedies stay distinguishable -- `--ignore` versus fixing permissions.
The gate then routes through `gate_should_fail(..., scan_incomplete=...)`.

WHAT MUST NOT FIRE. A repo where every scannable file was read is untouched:
"all refs resolve", `partial_success: false`, no `unreadable_files` key, exit
0. And a file skipped by SCOPE -- a non-scannable extension, or one the caller
excluded with `--ignore` -- is a decision, not a blind spot, and must
never make the gate refuse.
"""

from __future__ import annotations

import json
import subprocess

import pytest

from roam.commands.cmd_stale_refs import _read_text_with_reason
from tests.conftest import invoke_cli

_OVERSIZE_BYTES = 1_100_000


def _git(cwd, *args):
    subprocess.run(
        ["git", *args],
        cwd=str(cwd),
        check=True,
        capture_output=True,
        text=True,
        stdin=subprocess.DEVNULL,
    )


@pytest.fixture
def refs_project(tmp_path):
    """A repo with one broken doc link, and nothing else stale."""
    proj = tmp_path / "w1527_proj"
    (proj / "docs").mkdir(parents=True)
    (proj / "src").mkdir()
    (proj / ".gitignore").write_text(".roam/\n", encoding="utf-8")
    (proj / "src" / "a.py").write_text("def alpha(x):\n    return x + 1\n", encoding="utf-8")
    (proj / "README.md").write_text("# Readme\n\nSee [a](src/a.py).\n", encoding="utf-8")
    (proj / "docs" / "big.md").write_text("# Big\n\n[the setup doc](docs/does-not-exist-A.md)\n", encoding="utf-8")
    _git(proj, "init", "-q", ".")
    _git(proj, "config", "user.email", "fixture@example.invalid")
    _git(proj, "config", "user.name", "fixture")
    _git(proj, "add", ".")
    _git(proj, "commit", "-q", "-m", "fixture")
    return proj


def _grow_past_the_cap(proj):
    """Push docs/big.md over the 1 MB discovery cap, link intact."""
    path = proj / "docs" / "big.md"
    text = path.read_text(encoding="utf-8")
    padding = ("x" * 80 + "\n") * (_OVERSIZE_BYTES // 81)
    path.write_text(text + padding, encoding="utf-8")
    assert path.stat().st_size > 1_000_000
    assert "docs/does-not-exist-A.md" in path.read_text(encoding="utf-8")


def _summary(result):
    return json.loads(result.output)["summary"]


# ---------------------------------------------------------------------------
# The reader helper -- the two causes must stay distinguishable
# ---------------------------------------------------------------------------


def test_reader_names_the_oversize_cause(tmp_path):
    big = tmp_path / "big.md"
    big.write_text("x" * 2048, encoding="utf-8")
    content, reason = _read_text_with_reason(big, max_bytes=1024)
    assert content is None
    assert "oversize" in reason


def test_reader_names_the_unreadable_cause(tmp_path):
    content, reason = _read_text_with_reason(tmp_path / "does-not-exist.md")
    assert content is None
    assert "unreadable" in reason


def test_reader_succeeds_normally(tmp_path):
    ok = tmp_path / "ok.md"
    ok.write_text("hello", encoding="utf-8")
    assert _read_text_with_reason(ok) == ("hello", None)


# ---------------------------------------------------------------------------
# MUST FIRE -- a file that was not read is not a file that resolved
# ---------------------------------------------------------------------------


def test_control_the_broken_link_is_found_when_readable(cli_runner, refs_project):
    """The positive control the whole finding rests on."""
    result = invoke_cli(cli_runner, ["stale-refs", "--gate", "--json"], cwd=refs_project)
    assert result.exit_code == 5, result.output
    summary = _summary(result)
    assert summary["stale_refs"] == 1
    assert summary["files_unreadable"] == 0


def test_oversize_file_does_not_silently_clear_the_gate(cli_runner, refs_project):
    """ARM 1: the cap is applied in discovery, and must now be countable."""
    _grow_past_the_cap(refs_project)
    result = invoke_cli(cli_runner, ["stale-refs", "--gate", "--json"], cwd=refs_project)
    assert result.exit_code == 5, result.output
    data = json.loads(result.output)
    summary = data["summary"]
    assert summary["files_unreadable"] == 1
    assert summary["scan_incomplete"] is True
    assert summary["partial_success"] is True
    assert "all refs resolve" not in summary["verdict"]
    assert [item["file"] for item in data["unreadable_files"]] == ["docs/big.md"]
    assert data["unreadable_files"][0]["reason"] == "oversized"


def test_unreadable_file_does_not_silently_clear_the_gate(cli_runner, refs_project, monkeypatch):
    """ARM 2: an OSError on read, the path the bare `None` collapsed."""
    import roam.commands.cmd_stale_refs as mod

    real = mod._read_text_with_reason

    def fake(path, max_bytes=1_000_000):
        if path.name == "big.md":
            return None, "unreadable (PermissionError)"
        return real(path, max_bytes)

    monkeypatch.setattr(mod, "_read_text_with_reason", fake)
    result = invoke_cli(cli_runner, ["stale-refs", "--gate", "--json"], cwd=refs_project)
    assert result.exit_code == 5, result.output
    data = json.loads(result.output)
    assert data["summary"]["files_unreadable"] == 1
    assert data["summary"]["scan_incomplete"] is True
    assert "PermissionError" in data["unreadable_files"][0]["reason"]


def test_text_channel_discloses_the_skip(cli_runner, refs_project):
    """The text channel must not tell a quieter story than --json."""
    _grow_past_the_cap(refs_project)
    result = invoke_cli(cli_runner, ["stale-refs", "--gate"], cwd=refs_project)
    assert result.exit_code == 5, result.output
    assert "SCAN INCOMPLETE" in result.output
    assert "docs/big.md" in result.output
    assert "all targets exist" not in result.output


def test_verdict_no_longer_says_all_refs_resolve(cli_runner, refs_project):
    """ "all refs resolve - 0 checked" was the sentence that had to go."""
    _grow_past_the_cap(refs_project)
    result = invoke_cli(cli_runner, ["stale-refs", "--json"], cwd=refs_project)
    verdict = _summary(result)["verdict"]
    assert "all refs resolve" not in verdict
    assert "could not be read" in verdict


# ---------------------------------------------------------------------------
# MUST NOT FIRE -- scope decisions are not blind spots
# ---------------------------------------------------------------------------


@pytest.fixture
def clean_project(tmp_path):
    proj = tmp_path / "w1527_clean"
    (proj / "src").mkdir(parents=True)
    (proj / ".gitignore").write_text(".roam/\n", encoding="utf-8")
    (proj / "src" / "a.py").write_text("def alpha(x):\n    return x + 1\n", encoding="utf-8")
    (proj / "README.md").write_text("# Readme\n\nSee [a](src/a.py).\n", encoding="utf-8")
    _git(proj, "init", "-q", ".")
    _git(proj, "config", "user.email", "fixture@example.invalid")
    _git(proj, "config", "user.name", "fixture")
    _git(proj, "add", ".")
    _git(proj, "commit", "-q", "-m", "fixture")
    return proj


def test_clean_repo_still_passes_and_says_so(cli_runner, clean_project):
    """THE outage boundary: a fully-read repo is unchanged in every field."""
    result = invoke_cli(cli_runner, ["stale-refs", "--gate", "--json"], cwd=clean_project)
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    summary = data["summary"]
    assert "all refs resolve" in summary["verdict"]
    assert summary["files_unreadable"] == 0
    assert summary["scan_incomplete"] is False
    assert summary["partial_success"] is False
    # The payload key must be ABSENT, not an empty list, on a clean run.
    assert "unreadable_files" not in data


def test_clean_repo_text_channel_unchanged(cli_runner, clean_project):
    result = invoke_cli(cli_runner, ["stale-refs", "--gate"], cwd=clean_project)
    assert result.exit_code == 0, result.output
    assert "all targets exist" in result.output
    assert "SCAN INCOMPLETE" not in result.output


def test_oversize_file_of_a_non_scannable_kind_is_not_a_blind_spot(cli_runner, clean_project):
    """A 2 MB binary/data blob was never going to be scanned for refs.

    Counting scope decisions as blind spots would make the gate refuse on
    essentially every real repo -- the over-refusal this fix must avoid.
    """
    blob = clean_project / "data.bin"
    blob.write_bytes(b"\x00" * 2_000_000)
    _git(clean_project, "add", "data.bin")
    _git(clean_project, "commit", "-q", "-m", "blob")
    result = invoke_cli(cli_runner, ["stale-refs", "--gate", "--json"], cwd=clean_project)
    assert result.exit_code == 0, result.output
    summary = _summary(result)
    assert summary["files_unreadable"] == 0
    assert summary["partial_success"] is False


def test_ignore_source_keeps_the_gate_green(cli_runner, refs_project):
    """An explicitly excluded oversize file is the caller's decision.

    `--ignore` is the documented remedy for the oversize case, so it has to
    actually work -- otherwise the refusal has no exit.
    """
    _grow_past_the_cap(refs_project)
    result = invoke_cli(
        cli_runner,
        ["stale-refs", "--gate", "--ignore", "docs/*", "--json"],
        cwd=refs_project,
    )
    assert result.exit_code == 0, result.output
    summary = _summary(result)
    assert summary["files_unreadable"] == 0
    assert summary["partial_success"] is False


def test_without_the_gate_flag_an_incomplete_scan_still_exits_zero(cli_runner, refs_project):
    """No gate requested means disclose, not refuse."""
    _grow_past_the_cap(refs_project)
    result = invoke_cli(cli_runner, ["stale-refs", "--json"], cwd=refs_project)
    assert result.exit_code == 0, result.output
    assert _summary(result)["scan_incomplete"] is True
