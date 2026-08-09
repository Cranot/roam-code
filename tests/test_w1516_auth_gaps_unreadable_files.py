"""W1516 — `auth-gaps` reported "No auth gaps detected" for files it never opened.

Three identical ``source = _read_source(abs_path); if source is None: continue``
sites (routes, service providers, controllers) dropped an unreadable file on the
floor.  ``_read_source`` returns ``None`` on ``OSError`` with no channel back, so
7 gaps (6 high) collapsed to ``0 auth gap(s) found`` / ``run_state:
NO_AUTH_GAPS`` / ``partial_success: false``, while ``routes_scanned`` and
``controllers_scanned`` stayed ``true`` — those two mean only "the
--routes-only/--controllers-only flag did not skip this scope", so they became
actively false claims rather than merely uninformative ones.

The must-not-fire half is the point: a file that reads fine and yields zero gaps
is a clean measurement and must stay out of the unreadable set.  ``_read_source``
uses ``errors="replace"``, so decoding never fails and the unreadable set is
exactly the ``OSError`` set — keying on "produced no findings" instead would make
every ordinary repo report ``scan_incomplete``.
"""

from __future__ import annotations

import pytest

from tests.conftest import git_init, index_in_process, invoke_cli, parse_json_output

_ROUTES = """<?php

use Illuminate\\Support\\Facades\\Route;
use App\\Http\\Controllers\\AdminController;

Route::get('/admin/users', [AdminController::class, 'index']);
Route::post('/admin/users', [AdminController::class, 'store']);
Route::delete('/admin/users/{id}', [AdminController::class, 'destroy']);
"""

_CONTROLLER = """<?php

namespace App\\Http\\Controllers;

use Illuminate\\Http\\Request;

class AdminController extends Controller
{
    public function index()
    {
        return User::all();
    }

    public function store(Request $request)
    {
        return User::create($request->all());
    }

    public function destroy($id)
    {
        return User::destroy($id);
    }
}
"""


@pytest.fixture
def laravel_project(tmp_path):
    proj = tmp_path / "laravel_proj"
    (proj / "routes").mkdir(parents=True)
    (proj / "app" / "Http" / "Controllers").mkdir(parents=True)
    (proj / ".gitignore").write_text(".roam/\n")
    (proj / "routes" / "api.php").write_text(_ROUTES)
    (proj / "app" / "Http" / "Controllers" / "AdminController.php").write_text(_CONTROLLER)
    git_init(proj)
    index_in_process(proj)
    return proj


def _deny_reads(monkeypatch, suffixes):
    """Make ``open`` raise PermissionError for the named path suffixes.

    Portable stand-in for the `icacls /deny` used to find this: the defect is
    keyed on the ``OSError`` from ``open``, not on any OS-specific ACL.
    """
    import roam.commands.cmd_auth_gaps as mod

    real_open = open

    def _refusing_open(file, *args, **kwargs):
        norm = str(file).replace("\\", "/")
        if any(norm.endswith(s) for s in suffixes):
            raise PermissionError(13, "Permission denied")
        return real_open(file, *args, **kwargs)

    monkeypatch.setattr(mod, "open", _refusing_open, raising=False)


# ---------------------------------------------------------------------------
# MUST FIRE
# ---------------------------------------------------------------------------


def test_baseline_finds_the_gaps(cli_runner, laravel_project, monkeypatch):
    """Control: with every file readable the detector finds real gaps."""
    monkeypatch.chdir(laravel_project)
    result = invoke_cli(cli_runner, ["auth-gaps"], cwd=laravel_project, json_mode=True)
    assert result.exit_code == 0, result.output
    data = parse_json_output(result)
    summary = data["summary"]
    assert summary["total"] > 0, "fixture must produce gaps, or the repro proves nothing"
    assert summary["high"] > 0
    assert summary["scan_incomplete"] is False
    assert summary["files_read"] == summary["files_discovered"] > 0


def test_unreadable_files_are_not_no_auth_gaps(cli_runner, laravel_project, monkeypatch):
    """Every discovered file unreadable: 0 gaps must be disclosed, not published as clean."""
    monkeypatch.chdir(laravel_project)
    _deny_reads(monkeypatch, ("routes/api.php", "Controllers/AdminController.php"))

    result = invoke_cli(cli_runner, ["auth-gaps"], cwd=laravel_project, json_mode=True)
    assert result.exit_code == 0, "no gate exists on this command; the exit code must not move"
    summary = parse_json_output(result)["summary"]

    assert summary["total"] == 0
    assert summary["scan_incomplete"] is True
    assert summary["partial_success"] is True
    assert summary["run_state"] == "SCAN_INCOMPLETE", "NO_AUTH_GAPS is a claim this run cannot make"
    assert summary["files_read"] == 0
    assert summary["files_discovered"] >= 2
    assert summary["files_unreadable"] >= 2
    assert any("api.php" in p for p in summary["unreadable_files"])
    assert "could not be read" in summary["verdict"]

    # routes_scanned / controllers_scanned keep their historical meaning and
    # must NOT be silently redefined into read-coverage booleans.
    assert summary["routes_scanned"] is True
    assert summary["controllers_scanned"] is True
    assert summary["routes_files_read"] == 0
    assert summary["routes_files_discovered"] >= 1


def test_text_channel_carries_the_same_disclosure(cli_runner, laravel_project, monkeypatch):
    """Both channels must read the same boolean, not two hand-written copies."""
    monkeypatch.chdir(laravel_project)
    _deny_reads(monkeypatch, ("routes/api.php", "Controllers/AdminController.php"))

    result = invoke_cli(cli_runner, ["auth-gaps"], cwd=laravel_project, json_mode=False)
    assert result.exit_code == 0, result.output
    out = getattr(result, "stdout", None) or result.output

    assert "This is NOT 'no auth gaps'." in out
    assert "could not be read" in out
    assert "No auth gaps detected." not in out
    assert "(none found)" not in out, "the bare '(none found)' asserts a result over unread files"


# ---------------------------------------------------------------------------
# MUST NOT FIRE
# ---------------------------------------------------------------------------


def test_readable_and_clean_is_still_clean(cli_runner, tmp_path, monkeypatch):
    """A route file that reads fine and declares only guarded routes stays clean.

    This is the over-eager-fix guard: a discovered file that yields no findings
    is a clean measurement, not a coverage gap.
    """
    proj = tmp_path / "guarded_proj"
    (proj / "routes").mkdir(parents=True)
    (proj / ".gitignore").write_text(".roam/\n")
    (proj / "routes" / "api.php").write_text(
        "<?php\n\nRoute::middleware('auth:sanctum')->group(function () {\n"
        "    Route::get('/me', [ProfileController::class, 'show']);\n});\n"
    )
    git_init(proj)
    index_in_process(proj)
    monkeypatch.chdir(proj)

    result = invoke_cli(cli_runner, ["auth-gaps"], cwd=proj, json_mode=True)
    assert result.exit_code == 0, result.output
    summary = parse_json_output(result)["summary"]

    assert summary["scan_incomplete"] is False, "a readable file with no findings is not a coverage gap"
    assert summary["files_unreadable"] == 0
    assert summary["unreadable_files"] == []
    assert summary["files_read"] == summary["files_discovered"]
    assert summary["run_state"] != "SCAN_INCOMPLETE"

    text = invoke_cli(cli_runner, ["auth-gaps"], cwd=proj, json_mode=False)
    out = getattr(text, "stdout", None) or text.output
    assert "This is NOT 'no auth gaps'." not in out


def test_empty_file_reads_clean_and_is_not_a_coverage_gap(cli_runner, tmp_path, monkeypatch):
    """An EMPTY route file parses to zero findings and must count as read.

    ``_read_source_status`` returns ``("", None)`` here.  Keying the unreadable
    set on falsiness rather than on the error channel would put this file in the
    gap count.
    """
    proj = tmp_path / "empty_routes_proj"
    (proj / "routes").mkdir(parents=True)
    (proj / ".gitignore").write_text(".roam/\n")
    (proj / "routes" / "api.php").write_text("")
    (proj / "keep.py").write_text("def noop():\n    return None\n")
    git_init(proj)
    index_in_process(proj)
    monkeypatch.chdir(proj)

    from roam.commands.cmd_auth_gaps import _read_source_status

    source, error = _read_source_status(str(proj / "routes" / "api.php"))
    assert source == "", "an empty file is readable"
    assert error is None, "an empty file is a clean measurement, not a read failure"

    result = invoke_cli(cli_runner, ["auth-gaps"], cwd=proj, json_mode=True)
    assert result.exit_code == 0, result.output
    summary = parse_json_output(result)["summary"]
    assert summary["scan_incomplete"] is False
    assert summary["files_unreadable"] == 0


def test_read_source_status_reports_the_oserror(tmp_path):
    """The error channel is exactly the OSError set — decoding never fails."""
    from roam.commands.cmd_auth_gaps import _read_source, _read_source_status

    missing = tmp_path / "nope.php"
    source, error = _read_source_status(str(missing))
    assert source is None
    assert error and "FileNotFoundError" in error

    # errors="replace" means even undecodable bytes read successfully.
    binary = tmp_path / "bin.php"
    binary.write_bytes(b"<?php\n\xff\xfe\x00garbage\n")
    source, error = _read_source_status(str(binary))
    assert error is None, "a byte sequence that does not decode is still a successful read"
    assert source is not None

    # `_read_source` stays the single reader and keeps its historical
    # signature for the callers that legitimately do not report coverage.
    assert _read_source(str(binary)) == source
    assert _read_source(str(missing)) is None
