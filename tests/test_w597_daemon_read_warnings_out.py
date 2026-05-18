"""W597 — ``daemon_state`` (+ bonus ``daemon_running``) plumbs ``warnings_out``.

W448 added ``warnings_out`` to ``read_lease``. W589/W592 closed the lease
sibling cluster. W593/W595 closed the permits cluster. W596 closed the
runs-ledger cluster (``read_run_meta`` + bonus ``read_run_events``). W597
closes the runtime-daemon cluster: ``daemon_state`` previously swallowed
``(OSError, json.JSONDecodeError)`` with a bare ``return None`` and
converted "daemon.json not on disk" (legitimate "not running" sentinel)
/ "daemon.json unreadable" / "malformed JSON" / "top-level not a dict"
into one indistinguishable None.

The W597-bonus plumb covers ``daemon_running`` (sibling reader in the
same file with the SAME silent-False shape — PID-file read OSError /
non-int contents / Win32 stat OSError).

Marker shape mirrors W595's ``read_permit`` / W596's ``read_run_meta``
closed-enum shape with a ``daemon_state_`` / ``daemon_pidfile_`` prefix
so a caller threading the same bucket through multiple substrate read
sites sees a uniform marker vocabulary.

``daemon_state`` closed-enum kinds:

  * ``daemon_state_not_found:<path>``
  * ``daemon_state_read_failed:<path>:<exc_class>:<detail>``
  * ``daemon_state_corrupt:<path>:JSONDecodeError``
  * ``daemon_state_corrupt:<path>:NotAJsonObject``

``daemon_running`` (bonus) closed-enum kinds:

  * ``daemon_pidfile_read_failed:<path>:<exc_class>:<detail>``
  * ``daemon_pidfile_corrupt:<path>:ValueError:<detail>``
  * ``daemon_pidfile_stat_failed:<path>:<exc_class>:<detail>`` (Win32)

The ``None`` (state) / ``False`` (running) returns are PRESERVED — the
existing caller contracts (``status_summary`` + ``test_v12_2``) are
unchanged. ``warnings_out=None`` (default) preserves the pre-W597
silent-drop behaviour.

LAW 4 note: warning kinds are NOT ``agent_contract.facts`` strings and
therefore not subject to the concrete-noun-terminal lint. They are
internal diagnostic markers (same discipline as W589 / W592 / W593 /
W595 / W596).
"""

from __future__ import annotations

import ast
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
from _helpers.repo_root import repo_root  # noqa: E402

from roam.runtime.daemon import (  # noqa: E402
    _daemon_state_path,
    daemon_running,
    daemon_state,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def daemon_project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A minimal project dir with ``.roam/`` pre-created.

    daemon.py uses ``Path('.roam') / 'daemon.json'`` directly — relative
    to the process CWD. ``monkeypatch.chdir`` is the only correct
    isolation primitive for this file (no repo_root param).
    """
    proj = tmp_path / "w597_daemonproj"
    proj.mkdir()
    (proj / ".roam").mkdir()
    monkeypatch.chdir(proj)
    return proj


# ===========================================================================
# (1) daemon_state — happy path: clean read emits no warning
# ===========================================================================


def test_clean_emits_no_warning(daemon_project: Path) -> None:
    """A normal read on a clean daemon.json appends nothing to ``warnings_out``.

    Sanity check that the W597 plumbing only fires on degenerate paths.
    """
    state_path = daemon_project / ".roam" / "daemon.json"
    state_path.write_text(json.dumps({"pid": 1234, "started_at": "now"}), encoding="utf-8")

    warnings: list[str] = []
    loaded = daemon_state(warnings_out=warnings)

    assert loaded is not None, "clean read must return a dict"
    assert loaded == {"pid": 1234, "started_at": "now"}
    assert warnings == [], f"clean daemon_state must NOT emit warnings; got {warnings!r}"


# ===========================================================================
# (2) daemon_state — missing file emits ``daemon_state_not_found:`` marker
# ===========================================================================


def test_missing_file_emits_not_found_marker(daemon_project: Path) -> None:
    """Read on a non-existent daemon.json emits ``daemon_state_not_found:<path>``.

    Marker shape mirrors W595's ``permit_not_found:`` / W596's
    ``run_meta_not_found:``. Operators distinguish "no daemon" from
    "corrupt daemon state" without parsing free-form text. The ``None``
    return is preserved — the None still semantically means "no
    daemon."
    """
    state_path = daemon_project / ".roam" / "daemon.json"
    assert not state_path.exists()

    warnings: list[str] = []
    result = daemon_state(warnings_out=warnings)

    assert result is None, "missing daemon.json must still return None (existing contract)"
    assert len(warnings) == 1, f"expected exactly one warning on missing daemon.json; got {len(warnings)}: {warnings!r}"
    msg = warnings[0]
    assert msg.startswith("daemon_state_not_found:"), msg
    assert str(_daemon_state_path()) in msg, msg


# ===========================================================================
# (3) daemon_state — corrupt JSON emits ``daemon_state_corrupt:...:JSONDecodeError``
# ===========================================================================


def test_corrupt_file_emits_corrupt_marker(daemon_project: Path) -> None:
    """Malformed JSON emits ``daemon_state_corrupt:<path>:JSONDecodeError``.

    Marker prefix mirrors W595's ``permit_corrupt:`` / W596's
    ``run_meta_corrupt:`` shape so a caller grepping substrate warnings
    sees one uniform vocabulary.
    """
    state_path = daemon_project / ".roam" / "daemon.json"
    state_path.write_text("{not valid json", encoding="utf-8")

    warnings: list[str] = []
    result = daemon_state(warnings_out=warnings)

    assert result is None, "corrupt daemon.json must return None (existing contract)"
    assert len(warnings) == 1, f"expected one corrupt-daemon warning; got {len(warnings)}: {warnings!r}"
    msg = warnings[0]
    assert msg.startswith("daemon_state_corrupt:"), msg
    assert "JSONDecodeError" in msg, msg


# ===========================================================================
# (4) daemon_state — OSError on read emits ``daemon_state_read_failed:`` marker
# ===========================================================================


def test_other_oserror_emits_read_failed_marker(daemon_project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A non-decode OSError on read_text emits ``daemon_state_read_failed:``.

    Monkeypatches ``Path.read_text`` to raise ``PermissionError`` for the
    daemon.json path. The file EXISTS on disk (so we get past the
    ``not p.is_file()`` short-circuit) but read fails.
    """
    state_path = daemon_project / ".roam" / "daemon.json"
    state_path.write_text(json.dumps({"pid": 7}), encoding="utf-8")
    state_path_resolved = state_path.resolve()

    original_read_text = Path.read_text

    def _raising_read_text(self, *args, **kwargs):
        try:
            resolved = self.resolve()
        except OSError:
            resolved = self
        if resolved == state_path_resolved:
            raise PermissionError("synthetic-EACCES from W597 test")
        return original_read_text(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", _raising_read_text)

    warnings: list[str] = []
    result = daemon_state(warnings_out=warnings)

    assert result is None, "read_text failure must preserve None return; got non-None"
    assert len(warnings) == 1, f"expected one daemon_state_read_failed warning; got {len(warnings)}: {warnings!r}"
    msg = warnings[0]
    assert msg.startswith("daemon_state_read_failed:"), msg
    assert "PermissionError" in msg, msg
    assert "synthetic-EACCES from W597 test" in msg, msg


# ===========================================================================
# (5) daemon_state — non-dict top-level emits ``...:NotAJsonObject``
# ===========================================================================


def test_non_dict_top_level_emits_corrupt_marker(daemon_project: Path) -> None:
    """Top-level JSON array emits ``daemon_state_corrupt:<path>:NotAJsonObject``.

    Mirrors W595's fourth corrupt sub-case (top-level value is valid
    JSON but not a dict). Distinct structured marker so an operator can
    grep the bucket.
    """
    state_path = daemon_project / ".roam" / "daemon.json"
    state_path.write_text("[1, 2, 3]", encoding="utf-8")

    warnings: list[str] = []
    result = daemon_state(warnings_out=warnings)

    assert result is None
    assert len(warnings) == 1, warnings
    msg = warnings[0]
    assert msg.startswith("daemon_state_corrupt:"), msg
    assert "NotAJsonObject" in msg, msg


# ===========================================================================
# (6) daemon_state — default ``warnings_out=None`` preserves silent behaviour
# ===========================================================================


def test_default_none_no_crash(daemon_project: Path) -> None:
    """Default ``warnings_out=None`` returns None cleanly, no crash, no warnings.

    Existing callers (``status_summary`` in the same file + the
    ``test_v12_2.TestDaemonScaffold`` suite) call ``daemon_state()``
    with no kwargs — they must NOT regress on any failure mode covered
    by the W597 plumb.
    """
    state_path = daemon_project / ".roam" / "daemon.json"

    # (a) Missing daemon.json — the most common silent-None path.
    result = daemon_state()
    assert result is None

    # (b) Corrupt JSON — the second silent-None path.
    state_path.write_text("{not valid json", encoding="utf-8")
    result = daemon_state()
    assert result is None

    # (c) Non-dict top-level — the third silent-None path.
    state_path.write_text("[1, 2, 3]", encoding="utf-8")
    result = daemon_state()
    assert result is None

    # (d) Happy path with default-None still returns the dict.
    state_path.write_text(json.dumps({"pid": 99}), encoding="utf-8")
    loaded = daemon_state()
    assert loaded is not None
    assert loaded == {"pid": 99}


# ===========================================================================
# (7) daemon_running bonus — happy path: missing PID file emits no warning
# ===========================================================================


def test_running_missing_pidfile_emits_no_warning(daemon_project: Path) -> None:
    """No PID file is the legitimate "not running" sentinel — no warning.

    Unlike daemon_state's missing-file path (which IS warned because
    operators may want to disambiguate from corrupt state), the missing
    PID file is the documented "no daemon" signal. Warning here would
    train operators to ignore real warnings.
    """
    pid_path = daemon_project / ".roam" / "daemon.pid"
    assert not pid_path.exists()

    warnings: list[str] = []
    result = daemon_running(warnings_out=warnings)

    assert result is False
    assert warnings == [], f"missing PID file must NOT warn; got {warnings!r}"


# ===========================================================================
# (8) daemon_running bonus — corrupt PID contents emit ``daemon_pidfile_corrupt:``
# ===========================================================================


def test_running_corrupt_pid_emits_corrupt_marker(daemon_project: Path) -> None:
    """PID file with non-int contents emits ``daemon_pidfile_corrupt:<path>:ValueError:<detail>``.

    Distinct closed-enum so an operator inspecting ``warnings_out`` sees
    "the pidfile exists but is corrupt" — a different failure mode from
    "no daemon".
    """
    pid_path = daemon_project / ".roam" / "daemon.pid"
    pid_path.write_text("not-a-pid", encoding="utf-8")

    warnings: list[str] = []
    result = daemon_running(warnings_out=warnings)

    assert result is False, "corrupt PID file must preserve False return"
    assert len(warnings) == 1, warnings
    msg = warnings[0]
    assert msg.startswith("daemon_pidfile_corrupt:"), msg
    assert "ValueError" in msg, msg


# ===========================================================================
# (9) daemon_running bonus — OSError on PID read emits ``daemon_pidfile_read_failed:``
# ===========================================================================


def test_running_oserror_on_read_emits_read_failed_marker(
    daemon_project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """OSError on PID file ``read_text`` emits ``daemon_pidfile_read_failed:``.

    Monkeypatches ``Path.read_text`` to raise ``PermissionError`` for the
    daemon.pid path. The PID file exists (so we get past the
    ``not pid_path.is_file()`` short-circuit) but read fails.
    """
    pid_path = daemon_project / ".roam" / "daemon.pid"
    pid_path.write_text("1234", encoding="utf-8")
    pid_path_resolved = pid_path.resolve()

    original_read_text = Path.read_text

    def _raising_read_text(self, *args, **kwargs):
        try:
            resolved = self.resolve()
        except OSError:
            resolved = self
        if resolved == pid_path_resolved:
            raise PermissionError("synthetic-EACCES PID from W597 test")
        return original_read_text(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", _raising_read_text)

    warnings: list[str] = []
    result = daemon_running(warnings_out=warnings)

    assert result is False
    assert len(warnings) == 1, warnings
    msg = warnings[0]
    assert msg.startswith("daemon_pidfile_read_failed:"), msg
    assert "PermissionError" in msg, msg
    assert "synthetic-EACCES PID from W597 test" in msg, msg


# ===========================================================================
# (10) daemon_running bonus — default ``warnings_out=None`` preserves behaviour
# ===========================================================================


def test_running_default_none_no_crash(daemon_project: Path) -> None:
    """Default ``warnings_out=None`` preserves the pre-W597 silent behaviour.

    Existing callers (``status_summary`` in the same file +
    ``test_v12_2.TestDaemonScaffold``) call ``daemon_running()`` with no
    kwargs — they must NOT regress.
    """
    pid_path = daemon_project / ".roam" / "daemon.pid"

    # (a) Missing PID file — legitimate "not running"; no crash.
    assert daemon_running() is False

    # (b) Corrupt PID contents — preserve False, no crash.
    pid_path.write_text("not-a-pid", encoding="utf-8")
    assert daemon_running() is False


# ===========================================================================
# (11) Caller-side audit: AST-check no caller has been silently rewired
# ===========================================================================


def test_callers_unmodified() -> None:
    """AST-check that daemon.py's silent-None readers were not accidentally
    rewired into a write-path or new export surface.

    The W597 plumb is read-only — it adds a keyword-only ``warnings_out``
    parameter and an inline ``_emit`` closure. The two changed functions
    (``daemon_state`` and ``daemon_running``) must still be plain
    function defs (NOT async, NOT class methods) and must still return
    via ``return`` statements only — no yields, no raises that escape
    the documented exception sets.
    """
    src_path = repo_root() / "src" / "roam" / "runtime" / "daemon.py"
    tree = ast.parse(src_path.read_text(encoding="utf-8"))

    target_names = {"daemon_state", "daemon_running"}
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name in target_names:
            found.add(node.name)
            # No yields — the readers must remain non-generator.
            for child in ast.walk(node):
                if isinstance(child, (ast.Yield, ast.YieldFrom)):
                    raise AssertionError(
                        f"{node.name} contains a yield — W597 must not turn the silent-None reader into a generator"
                    )
            # Must have a ``warnings_out`` kw-only parameter.
            kwonly_names = [a.arg for a in node.args.kwonlyargs]
            assert "warnings_out" in kwonly_names, (
                f"{node.name} must declare ``warnings_out`` as a kw-only parameter (got kwonly={kwonly_names!r})"
            )
        elif isinstance(node, ast.AsyncFunctionDef) and node.name in target_names:
            raise AssertionError(f"{node.name} became async — W597 must not change the synchronous-call contract")

    assert found == target_names, f"expected to find both {target_names} as plain function defs; got {found}"


# ===========================================================================
# (12) Watch-loop NOT touched — verify the write paths are untouched
# ===========================================================================


def test_watch_loop_write_paths_untouched() -> None:
    """W597 is read-only. daemon.py has no write paths (Phase 1 scaffold);
    confirm by AST that no new ``write_text`` / ``write_bytes`` / ``open``
    calls were introduced alongside the W597 plumb.

    daemon.py's documented surface is read-only (``daemon_state``,
    ``daemon_running``, ``status_summary``, ``acquire_lock_for_command``).
    A write-path appearing here would indicate the watch/notification
    loop was accidentally touched.
    """
    src_path = repo_root() / "src" / "roam" / "runtime" / "daemon.py"
    tree = ast.parse(src_path.read_text(encoding="utf-8"))

    forbidden = {"write_text", "write_bytes"}
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and node.attr in forbidden:
            raise AssertionError(
                f"daemon.py introduced a {node.attr!r} call at line "
                f"{node.lineno} — W597 is read-only; watch-loop / write "
                f"paths must remain untouched"
            )
