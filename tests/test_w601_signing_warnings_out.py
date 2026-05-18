"""W601 — ``ensure_ledger_key`` / ``key_file_mode`` plumb ``warnings_out``.

W448 added ``warnings_out`` to ``read_lease``. W589/W592 closed the lease
sibling cluster. W593/W595 closed the permits cluster. W596 closed the
runs-ledger cluster (``read_run_meta`` + ``read_run_events``). W597
closed the runtime-daemon cluster (``daemon_state`` + ``daemon_running``).
W598 closed the pr-analyze-cache reader (``_load_cache``). W599 closed
the trace-ingest readers. W600 closed the W210 ChangeEvidence
config-hash substrate. W601 closes the run-ledger HMAC SIGNING substrate
— the verification-side counterpart to ``start_run``'s stamping. If a
run's HMAC signing-key write-side silently fails on chmod, or the
read-side ``key_file_mode`` silently returns ``None`` on a missing or
stat-broken key, the agent-OS chain-verify gate degrades silently with
no operator-visible signal.

W978 first-hypothesis decision (read source IN FULL, then decide):

* ``ensure_ledger_key`` at lines 132-137 of pre-W601 signing.py: the
  silent-pass site is the ``except OSError: pass`` chmod permission-
  tighten clause. The read-side paths at lines 112-122 already raise
  ``ValueError`` LOUDLY — no silent fallback to plumb. So the marker
  enum on this function is exactly 1 kind (write-side chmod only).
* ``key_file_mode`` at lines 141-157: TWO silent-pass returns —
  ``return None`` on missing file AND ``return None`` on ``stat()``
  failure. The Windows ``os.name == "nt"`` branch is INTENTIONAL non-
  applicability (st_mode isn't POSIX-meaningful on NTFS), so it stays
  silent per W597 daemon-discipline. Enum on this function is 2 kinds.

Closed-enum markers (per W978 first-hypothesis discipline — only paths
that actually exist in the code get markers):

  * ``signing_key_perm_tighten_failed:<rel_path>:<exc_class>:<detail>``
    (``ensure_ledger_key`` write-side chmod failure — operational
    anomaly; key is still usable).
  * ``signing_key_not_found:<rel_path>``
    (``key_file_mode`` informational — file absent; mirrors W596's
    ``run_meta_not_found`` informational missing-state marker).
  * ``signing_key_stat_failed:<rel_path>:<exc_class>:<detail>``
    (``key_file_mode`` operational anomaly — ``Path.stat`` raised on a
    file the ``exists()`` check just saw).

Intentional-absence decisions (W978 + "Make fallback chains loud"):

* Windows ``key_file_mode`` non-applicability returns ``None`` SILENTLY
  — st_mode isn't POSIX-meaningful on NTFS, so emitting a marker on
  every call would be noise, not signal. Mirrors W597's
  ``daemon_state_not_found`` silent-on-deliberate-cold-state choice.

* ``ensure_ledger_key`` read-side ``ValueError`` paths are LOUD already
  (raise on unreadable file, raise on wrong-length file). They are not
  silent-pass sites and therefore not in the W601 plumb.

* Verify-side ``verify_chain`` already emits closed-enum state strings
  via its return dict (``"ok"`` / ``"tampered"`` / ``"unsigned"``);
  it has no silent-pass site. Out of scope for W601.

The return semantic is PRESERVED — both functions still return the same
values they did pre-W601 (32-byte key / Optional[int]); only the
warnings_out channel gets disclosure. ``warnings_out=None`` (default)
preserves silent behaviour for the 4 live callers (``runs/ledger.py``
at lines 239 and 332, ``evidence/collector.py`` at lines 1303 and 1985,
``cmd_runs.py`` at line 805 — none thread the bucket today).

W210 evidence completeness Q7 ("what verified it?") dependency: the new
markers can feed into ``ChangeEvidence.verifications[]`` when the
collector threads ``warnings_out`` through its ``verify_chain``
gathering path, OR into the ``runs verify`` envelope's
``partial_success`` flag when ``cmd_runs.py`` threads it. This wave is
AUDIT-ONLY on those consumers — no producer modifications.

LAW 4 note: warning kinds are NOT ``agent_contract.facts`` strings and
therefore not subject to the concrete-noun-terminal lint. They are
internal diagnostic markers (same discipline as W589/W592/W593/W595/
W596/W597/W598/W599/W600).
"""

from __future__ import annotations

import ast
import os
import stat
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
from _helpers.repo_root import repo_root  # noqa: E402

from roam.runs.signing import (  # noqa: E402
    LEDGER_KEY_BYTES,
    LEDGER_KEY_FILE,
    ensure_ledger_key,
    key_file_mode,
    ledger_key_path,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def fresh_repo(tmp_path: Path) -> Path:
    """A tmp_path with no ``.roam/`` directory — clean cold state."""
    return tmp_path


@pytest.fixture
def keyed_repo(tmp_path: Path) -> Path:
    """A tmp_path with a freshly-generated ledger key on disk."""
    ensure_ledger_key(tmp_path)
    return tmp_path


# ===========================================================================
# (1) Happy path — clean key generation emits no warning
# ===========================================================================


def test_clean_happy_path_no_warnings(fresh_repo: Path) -> None:
    """First-call key generation on a clean repo → no warnings.

    Sanity check that W601 plumbing only fires on degenerate paths.
    The 32-byte key must be generated and returned regardless.
    """
    warnings: list[str] = []
    key = ensure_ledger_key(fresh_repo, warnings_out=warnings)
    assert warnings == [], f"clean ensure_ledger_key on a fresh repo must NOT emit warnings; got {warnings!r}"
    assert len(key) == LEDGER_KEY_BYTES
    assert isinstance(key, bytes)


def test_subsequent_read_no_warnings(keyed_repo: Path) -> None:
    """Reading an existing key → no warnings, same bytes returned.

    The second call short-circuits before the chmod path, so even if the
    fixture's first chmod failed (it shouldn't on tmp_path), the second
    call has no failure mode to disclose.
    """
    first_warnings: list[str] = []
    second_warnings: list[str] = []
    # Re-read the key that the fixture wrote.
    key1 = ensure_ledger_key(keyed_repo, warnings_out=first_warnings)
    key2 = ensure_ledger_key(keyed_repo, warnings_out=second_warnings)
    assert key1 == key2
    # Both subsequent reads are silent (no chmod path).
    assert first_warnings == []
    assert second_warnings == []


# ===========================================================================
# (2) chmod-failure write-side emits perm_tighten_failed marker
# ===========================================================================


def test_chmod_fail_emits_perm_tighten_marker(fresh_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A ``PermissionError`` on chmod emits ``signing_key_perm_tighten_failed:``.

    Monkeypatches ``os.chmod`` to raise during first-key generation.
    The key file must still be written and the 32 bytes returned
    (caller contract preserved). The warnings bucket gets exactly one
    closed-enum marker.
    """

    def _raising_chmod(*args, **kwargs):
        raise PermissionError("synthetic-EACCES from W601 test")

    monkeypatch.setattr(os, "chmod", _raising_chmod)

    warnings: list[str] = []
    key = ensure_ledger_key(fresh_repo, warnings_out=warnings)

    # Caller contract preserved — key still returned with correct length.
    assert len(key) == LEDGER_KEY_BYTES
    # File still written to disk.
    assert ledger_key_path(fresh_repo).exists()

    # Exactly one closed-enum marker.
    assert len(warnings) == 1, f"expected one perm_tighten marker; got {warnings!r}"
    msg = warnings[0]
    assert msg.startswith("signing_key_perm_tighten_failed:"), msg
    assert LEDGER_KEY_FILE in msg, msg
    assert "PermissionError" in msg, msg
    assert "synthetic-EACCES from W601 test" in msg, msg


def test_chmod_oserror_emits_perm_tighten_marker(fresh_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A generic ``OSError`` on chmod also funnels through the marker.

    Confirms the ``except OSError`` clause catches the broader family —
    not just ``PermissionError``. Read-only FS / unsupported operation /
    etc. all emit the same closed-enum kind.
    """

    def _raising_chmod(*args, **kwargs):
        raise OSError("synthetic generic chmod failure")

    monkeypatch.setattr(os, "chmod", _raising_chmod)

    warnings: list[str] = []
    key = ensure_ledger_key(fresh_repo, warnings_out=warnings)

    assert len(key) == LEDGER_KEY_BYTES
    assert len(warnings) == 1, warnings
    msg = warnings[0]
    assert msg.startswith("signing_key_perm_tighten_failed:"), msg
    assert "OSError" in msg, msg


# ===========================================================================
# (3) key_file_mode: missing-file emits informational marker
# ===========================================================================


def test_key_file_mode_missing_emits_marker(fresh_repo: Path) -> None:
    """``key_file_mode`` on a repo with no key → ``signing_key_not_found:`` marker.

    The function still returns ``None`` (caller contract preserved). The
    marker is informational — missing key is the COMMON case before
    ``ensure_ledger_key`` has ever run.
    """
    warnings: list[str] = []
    mode = key_file_mode(fresh_repo, warnings_out=warnings)

    # Caller contract preserved — None on missing.
    assert mode is None
    # Exactly one informational marker.
    assert len(warnings) == 1, f"expected one not_found marker; got {warnings!r}"
    msg = warnings[0]
    assert msg.startswith("signing_key_not_found:"), msg
    assert LEDGER_KEY_FILE in msg, msg


# ===========================================================================
# (4) key_file_mode: stat-fail emits stat_failed marker
# ===========================================================================


@pytest.mark.skipif(
    os.name == "nt",
    reason="Windows branch returns None silently before reaching stat() — no stat-fail path to exercise.",
)
def test_key_file_mode_stat_fail_emits_marker(keyed_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A ``PermissionError`` on ``Path.stat`` emits ``signing_key_stat_failed:``.

    The function still returns ``None`` (caller contract preserved).
    Skipped on Windows because the NT branch short-circuits before
    reaching ``stat()`` — the path doesn't apply there.
    """
    key_path = ledger_key_path(keyed_repo)
    target_resolved = key_path.resolve()
    original_stat = Path.stat

    def _raising_stat(self, *args, **kwargs):
        try:
            resolved = self.resolve()
        except OSError:
            resolved = self
        if resolved == target_resolved:
            raise PermissionError("synthetic-EACCES on stat")
        return original_stat(self, *args, **kwargs)

    monkeypatch.setattr(Path, "stat", _raising_stat)

    warnings: list[str] = []
    mode = key_file_mode(keyed_repo, warnings_out=warnings)

    assert mode is None
    assert len(warnings) == 1, warnings
    msg = warnings[0]
    assert msg.startswith("signing_key_stat_failed:"), msg
    assert LEDGER_KEY_FILE in msg, msg
    assert "PermissionError" in msg, msg


# ===========================================================================
# (5) key_file_mode Windows branch is intentionally silent
# ===========================================================================


@pytest.mark.skipif(
    os.name != "nt",
    reason="NT-only test — exercises the intentional-non-applicability silent path.",
)
def test_key_file_mode_windows_no_marker(keyed_repo: Path) -> None:
    """Windows NTFS branch returns ``None`` SILENTLY — no marker emitted.

    Per W597 daemon-discipline intentional-absence pattern: returning
    ``None`` because st_mode isn't POSIX-meaningful on NTFS is a
    DELIBERATE non-applicability decision, not a silent failure. The
    marker enum reserves ``signing_key_*`` kinds for real failure paths.
    """
    warnings: list[str] = []
    mode = key_file_mode(keyed_repo, warnings_out=warnings)

    assert mode is None  # NT branch
    # No marker on the intentional non-applicability path.
    assert warnings == [], (
        f"NT branch must NOT emit a marker; got {warnings!r}. The "
        f"Windows non-applicability is a deliberate design decision, "
        f"not a silent failure."
    )


# ===========================================================================
# (6) On POSIX with successful stat, key_file_mode returns the bits
# ===========================================================================


@pytest.mark.skipif(
    os.name == "nt",
    reason="Windows branch returns None before reaching the stat() path.",
)
def test_key_file_mode_posix_happy_path_no_marker(keyed_repo: Path) -> None:
    """POSIX happy path → integer permission bits, no warning emitted.

    Confirms the W601 plumb only fires on the silent-fail paths and
    does not contaminate the success path with spurious markers.
    """
    warnings: list[str] = []
    mode = key_file_mode(keyed_repo, warnings_out=warnings)

    assert isinstance(mode, int)
    # ensure_ledger_key chmods to 0o600 on POSIX.
    assert mode == (stat.S_IRUSR | stat.S_IWUSR)
    assert warnings == []


# ===========================================================================
# (7) Default warnings_out=None preserves silent behaviour
# ===========================================================================


def test_default_none_no_crash(fresh_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Calling without ``warnings_out`` still works on every degenerate path.

    The 5 live callers of ``ensure_ledger_key`` (`runs/ledger.py:239`,
    `runs/ledger.py:332`, `evidence/collector.py:1303`,
    `evidence/collector.py:1985`, `cmd_runs.py:805`) and any caller of
    ``key_file_mode`` all use the no-kwargs form and must NOT regress
    on any failure mode covered by the W601 plumb.
    """

    # (a) Default-args happy path on a fresh repo.
    key = ensure_ledger_key(fresh_repo)
    assert len(key) == LEDGER_KEY_BYTES

    # (b) Default-args call with chmod monkeypatched to fail (silent).
    fresh2 = fresh_repo.parent / "fresh2"
    fresh2.mkdir()

    def _raising_chmod(*args, **kwargs):
        raise PermissionError("default-call chmod fail")

    monkeypatch.setattr(os, "chmod", _raising_chmod)
    key2 = ensure_ledger_key(fresh2)  # no warnings_out kwarg
    assert len(key2) == LEDGER_KEY_BYTES

    # (c) Default-args key_file_mode on a missing-key repo (silent).
    fresh3 = fresh_repo.parent / "fresh3"
    fresh3.mkdir()
    mode = key_file_mode(fresh3)
    assert mode is None


# ===========================================================================
# (8) Caller audit — no live caller threads warnings_out today
# ===========================================================================


def test_callers_unmodified() -> None:
    """AST-check the live callers of ``ensure_ledger_key`` + ``key_file_mode``.

    W601 is additive — kw-only ``warnings_out`` params with default
    ``None``. The 5 live callers of ``ensure_ledger_key`` call with no
    kwarg; ``key_file_mode`` has 1+ callers (tests + cmd_runs surface).
    All must remain unchanged by W601 — this test pins the
    "audit-only, caller unmodified" contract.

    A future refactor can opt a caller into threading the bucket; this
    test must be updated when that handoff is intentional.
    """
    consumers = [
        repo_root() / "src" / "roam" / "runs" / "ledger.py",
        repo_root() / "src" / "roam" / "evidence" / "collector.py",
        repo_root() / "src" / "roam" / "commands" / "cmd_runs.py",
    ]
    target_funcs = {"ensure_ledger_key", "key_file_mode"}
    total_calls = 0
    for src_path in consumers:
        assert src_path.exists(), f"expected to find {src_path}"
        tree = ast.parse(src_path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                func = node.func
                name = None
                if isinstance(func, ast.Name):
                    name = func.id
                elif isinstance(func, ast.Attribute):
                    name = func.attr
                if name in target_funcs:
                    total_calls += 1
                    kwarg_names = [kw.arg for kw in node.keywords if kw.arg is not None]
                    assert "warnings_out" not in kwarg_names, (
                        f"caller in {src_path.name} at line {node.lineno} now "
                        f"threads warnings_out into {name}; W601 was audit-"
                        f"only — update this test if intentionally opted in."
                    )
    assert total_calls >= 4, (
        f"expected at least 4 ensure_ledger_key/key_file_mode callsites "
        f"across consumers; found {total_calls}. If a consumer was "
        f"removed, update this test."
    )


# ===========================================================================
# (9) HMAC chain verification UNTOUCHED — regression guard
# ===========================================================================


def test_verify_chain_untouched(fresh_repo: Path) -> None:
    """The W596 ``read_run_events`` + ``verify_chain`` HMAC chain must
    still verify successfully end-to-end through ``log_event``.

    This is a smoke test: end-to-end signing-and-verification on a
    minimal git-initialised project. If W601's plumb broke any chain
    semantic, this test would catch the regression.
    """
    # Build a minimal git-initialised proj so start_run accepts it.
    proj = fresh_repo / "proj"
    proj.mkdir()
    (proj / ".gitignore").write_text(".roam/\n")
    (proj / "app.py").write_text("x = 1\n")
    import subprocess

    for cmd in [
        ["git", "init", "-q"],
        ["git", "config", "user.email", "t@t"],
        ["git", "config", "user.name", "t"],
        ["git", "add", "."],
        ["git", "commit", "-q", "-m", "init"],
    ]:
        subprocess.run(cmd, cwd=proj, check=True, capture_output=True)

    # Import these here so the test file imports cleanly even when the
    # ledger substrate is being refactored alongside W601.
    from roam.runs.ledger import (
        end_run,
        log_event,
        read_run_events,
        start_run,
    )
    from roam.runs.signing import ensure_ledger_key, verify_chain

    meta = start_run(proj, agent="w601-test")
    log_event(proj, meta.run_id, action="preflight", target="foo")
    log_event(proj, meta.run_id, action="impact", target="foo")
    end_run(proj, meta.run_id, status="completed")

    events = list(read_run_events(proj, meta.run_id))
    # ``end_run`` updates meta.json (status/ended_at) but does NOT log a
    # new event; we expect exactly the 2 ``log_event`` writes.
    assert len(events) == 2, events
    key = ensure_ledger_key(proj)
    result = verify_chain(events, key)
    assert result["state"] == "ok", result
    assert result["partial_success"] is False


# ===========================================================================
# (10) Closed-enum subset — W978 first-hypothesis discipline
# ===========================================================================


def test_w978_closed_enum_subset() -> None:
    """AST-check ``signing.py`` for the exact closed-enum marker set.

    W978 first-hypothesis discipline: every emitted marker must
    correspond to a real silent-fail code path. Inventing markers
    that no path can ever emit (e.g., ``signing_key_corrupt:`` when no
    parse step exists) adds dead vocabulary that contaminates the
    audit-trail surface.

    The expected closed enum after W601:

      * ``signing_key_perm_tighten_failed:`` (ensure_ledger_key chmod)
      * ``signing_key_not_found:``           (key_file_mode missing)
      * ``signing_key_stat_failed:``         (key_file_mode stat fail)

    Any new ``signing_key_*`` marker shape that lands without a
    corresponding test addition trips this check.
    """
    src_path = repo_root() / "src" / "roam" / "runs" / "signing.py"
    source = src_path.read_text(encoding="utf-8")

    expected_markers = {
        "signing_key_perm_tighten_failed:",
        "signing_key_not_found:",
        "signing_key_stat_failed:",
    }
    # Disallowed (the parse-step / verify-side markers we deliberately
    # did NOT invent because no code path emits them).
    forbidden_markers = {
        "signing_key_corrupt:",
        "signing_verify_mismatch:",
        "signing_signature_not_found:",
        "signing_key_read_failed:",  # read-side raises ValueError loudly
    }

    for marker in expected_markers:
        assert marker in source, (
            f"expected marker prefix {marker!r} missing from signing.py — did the W601 plumb get reverted?"
        )
    for marker in forbidden_markers:
        assert marker not in source, (
            f"forbidden marker prefix {marker!r} present in signing.py — "
            f"this marker has no corresponding silent-pass code path. "
            f"W978 first-hypothesis discipline: only plumb markers for "
            f"paths that actually exist."
        )


# ===========================================================================
# (11) Function-signature audit — kw-only warnings_out on both functions
# ===========================================================================


def test_signatures_carry_kw_only_warnings_out() -> None:
    """AST-check both functions declare ``warnings_out`` as kw-only.

    Mirrors W598/W599/W600 signature-audit patterns. Kw-only declaration
    is the back-compat-preserving signal that existing positional
    callers (4 of them on ``ensure_ledger_key``, 1+ on
    ``key_file_mode``) are unaffected.
    """
    src_path = repo_root() / "src" / "roam" / "runs" / "signing.py"
    tree = ast.parse(src_path.read_text(encoding="utf-8"))

    targets = {"ensure_ledger_key", "key_file_mode"}
    found = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name in targets:
            found.add(node.name)
            kwonly_names = [a.arg for a in node.args.kwonlyargs]
            assert "warnings_out" in kwonly_names, (
                f"{node.name} must declare ``warnings_out`` as a kw-only parameter (got kwonly={kwonly_names!r})"
            )
            for child in ast.walk(node):
                if isinstance(child, (ast.Yield, ast.YieldFrom)):
                    raise AssertionError(
                        f"{node.name} contains a yield — W601 must not turn the signing helpers into generators"
                    )

    missing = targets - found
    assert not missing, f"expected to find functions {missing!r} in signing.py"
