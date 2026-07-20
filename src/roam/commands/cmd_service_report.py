"""``roam service-report`` — one-command service-engagement deliverables.

Turns Roam's four services-report *templates* into filled, buyer-facing
deliverables, exactly mirroring how ``roam pr-replay`` productises
``roam postmortem``. Each ``--type`` runs the right existing Roam
primitives against the repo, aggregates their JSON envelopes, and emits
a narrative Markdown report ready to hand to a client.

Four report types, all share the same engine:

* ``--type due-diligence`` — codebase-health / M&A technical diligence.
  Runs ``health``, ``bus-factor``, ``complexity``, ``dead``, ``clones``,
  ``smells``, ``test-pyramid``, ``sbom``, ``supply-chain``, ``vulns``,
  ``architecture-drift``.
* ``--type ai-readiness`` — AI adoption readiness. Runs ``ai-readiness``,
  ``ai-ratio``, ``agent-score``, ``mode``.
* ``--type reachability-triage`` — the security wedge: reachable-vs-noise.
  Runs ``sbom``, ``supply-chain``, ``vulns``, ``vuln-reach``, ``taint``,
  ``secrets``.
* ``--type post-incident`` — replay a commit/incident range with
  ``postmortem`` + ``audit-trail-verify`` audit-trail framing.

Usage::

    # Codebase due-diligence report to stdout
    roam service-report --type due-diligence

    # Client-branded reachability triage written to a file + PDF
    roam service-report --type reachability-triage --client "Acme Inc" \
        --output acme-triage.md --pdf acme-triage.pdf

    # Post-incident replay over an explicit incident window
    roam service-report --type post-incident --range v1.0..main --output incident.md

Output formats: Markdown by default; ``roam --json service-report``
returns the full envelope (summary + sections + report_markdown).
SARIF is deliberately NOT emitted — service-report outputs are
invocation-scoped buyer-facing report envelopes composed from the
individual commands' aggregations, not per-location violations. The composed
subcommands emit their own ``--sarif`` when applicable; this command
rolls them up into a narrative report (same rationale as
``cmd_pr_replay``).

Reuses ``cmd_pr_replay``'s render/output/PDF/ledger infrastructure where
it makes sense (``_render_pdf``, ``_git_head_sha``, ``_is_safe_commit_range``,
``_run_postmortem``) — the two commands are siblings in the paid-audit
family.

Wording discipline (W184 / W203): every report says "maps to / supports
evidence for" and never "certifies / guaranteed / compliant" (the
disclaimer "does not certify" is the one allowed negation). See
``tests/_helpers/wording_lint.py``.
"""

from __future__ import annotations

import json as _json
import math as _math
import os as _os
import secrets as _secrets
import signal as _signal
import stat as _stat
import subprocess as _subprocess
import sys as _sys
import threading as _threading
import time as _time
from concurrent.futures import ThreadPoolExecutor as _ThreadPoolExecutor
from contextlib import contextmanager as _contextmanager
from datetime import datetime, timezone
from pathlib import Path

import click

from roam.atomic_io import capture_file_generation, conditional_install_file
from roam.capability import roam_capability

# Reuse pr-replay's render/output infrastructure — genuine sibling reuse,
# not duplication. ``_render_pdf`` is a generic markdown→PDF renderer;
# ``_git_head_sha`` / ``_is_safe_commit_range`` / ``_run_postmortem`` are
# the same helpers the paid-audit family already relies on.
from roam.commands.cmd_pr_replay import (
    _git_head_sha,
    _is_safe_commit_range,
    _render_pdf,
    _run_postmortem,
)
from roam.commands.resolve import ensure_index
from roam.exit_codes import EXIT_SUCCESS
from roam.output.formatter import json_envelope, to_json
from roam.runs.helpers import auto_log
from roam.security.bounded_json import loads_bounded
from roam.security.owner_only import (
    create_owner_only_directory,
    open_new_owner_only_file,
    path_is_owner_only,
    pinned_owner_only_directory,
)

# ---------------------------------------------------------------------------
# Report-type registry — single source of truth for what each type means.
# ---------------------------------------------------------------------------

_REPORT_TYPES: dict[str, dict] = {
    "due-diligence": {
        "label": "Codebase Due Diligence",
        "title": "Codebase Due Diligence Report",
        "purpose_line": (
            "Technical due-diligence pass over the target codebase: health, "
            "key-person risk, complexity, dead code, duplication, test signal, "
            "architecture drift, and security / supply-chain posture — the "
            "engineering evidence an acquirer or investor needs before signing."
        ),
        "engagement_price": "$3,000–$7,500",
        "lead_commands": [
            "health",
            "bus-factor",
            "complexity",
            "dead",
            "clones",
            "smells",
            "test-pyramid",
            "sbom",
            "supply-chain",
            "vulns",
            "architecture-drift",
        ],
    },
    "ai-readiness": {
        "label": "AI Adoption Readiness Audit",
        "title": "AI Adoption Readiness Audit",
        "purpose_line": (
            "Pre-rollout readiness review: how ready is this codebase for "
            "agent-driven and AI-assisted development? Scores structural "
            "readiness dimensions, measures the existing AI footprint, and "
            "reports the governance gates that should be in place before "
            "agents touch production code."
        ),
        "engagement_price": "$1,500–$4,000",
        "lead_commands": ["ai-readiness", "ai-ratio", "agent-score", "mode"],
    },
    "reachability-triage": {
        "label": "Security Reachability Triage",
        "title": "Security Reachability Triage",
        "purpose_line": (
            "Scanner-noise reduction sweep: of everything the scanners flag, "
            "what is actually reachable from a production entry point? "
            "Reachability analysis against the call graph separates the "
            "findings that warrant fix work this sprint from the noise."
        ),
        "engagement_price": "$2,500–$6,000",
        "lead_commands": [
            "sbom",
            "supply-chain",
            "vulns",
            "vuln-reach",
            "taint",
            "secrets",
        ],
    },
    "post-incident": {
        "label": "Post-Incident Replay",
        "title": "Post-Incident Replay Report",
        "purpose_line": (
            "Replay a suspected incident window with the current detector set "
            "and the signed audit trail: which findings would have surfaced "
            "pre-merge, and does the change history verify end-to-end? Turns a "
            "postmortem into a durable prevention artifact."
        ),
        "engagement_price": "$1,500–$4,000",
        "lead_commands": ["postmortem", "audit-trail-verify"],
    },
}


# ---------------------------------------------------------------------------
# Primitive invocation — run ``roam --json <cmd>`` in an isolated child,
# return the parsed envelope. Commands such as ``clones`` create their own
# process pools; invoking them through Click's in-process ``CliRunner`` can
# deadlock at the multiprocessing spawn boundary (observed on Windows) and
# also retains command-global caches across an 11-component report. Literal
# subprocess argv keeps every component independent and lets the parent bound
# time, output, and process-tree cleanup.
# ---------------------------------------------------------------------------


_COMPONENT_TIMEOUT_SECONDS = 180
_COMPONENT_MAX_OUTPUT_BYTES = 32 * 1024 * 1024
_COMPONENT_CAPTURE_CHUNK_BYTES = 64 * 1024
_COMPONENT_CAPTURE_POLL_SECONDS = 0.05
_COMPONENT_CLEANUP_SECONDS = 5
_COMPONENT_MAX_WORKERS = 3
_DUE_DILIGENCE_BUDGET_SECONDS = 240
_DEADLINE_CLEANUP_RESERVE_SECONDS = 15
_WINDOWS_COMPONENT_WRAPPER = (
    "import subprocess,sys\n"
    "if sys.stdin.buffer.read(1) != b'1': raise SystemExit(125)\n"
    "child = subprocess.Popen(sys.argv[1:], stdin=subprocess.DEVNULL, close_fds=True)\n"
    "try:\n"
    "    raise SystemExit(child.wait())\n"
    "except BaseException:\n"
    "    child.kill()\n"
    "    child.wait()\n"
    "    raise\n"
)
_LINUX_COMPONENT_WRAPPER = (
    "import sys\n"
    "from roam.commands.cmd_service_report import _linux_component_supervisor_main\n"
    "raise SystemExit(_linux_component_supervisor_main(int(sys.argv[1]), sys.argv[2:]))\n"
)
_LINUX_CONTAINMENT_VERIFIED = b"1"
_LINUX_CONTAINMENT_PREREQUISITE_FAILED = b"P"
_LINUX_CONTAINMENT_GATE_FAILED = b"G"
_LINUX_CONTAINMENT_LAUNCH_FAILED = b"L"
_LINUX_CONTAINMENT_CLEANUP_FAILED = b"C"
_LINUX_CONTAINMENT_INTERNAL_FAILED = b"E"
_LINUX_CONTAINMENT_ERRORS = {
    _LINUX_CONTAINMENT_PREREQUISITE_FAILED: "linux_containment_prerequisite_failed",
    _LINUX_CONTAINMENT_GATE_FAILED: "linux_containment_gate_failed",
    _LINUX_CONTAINMENT_LAUNCH_FAILED: "linux_containment_launch_failed",
    _LINUX_CONTAINMENT_CLEANUP_FAILED: "linux_containment_cleanup_failed",
    _LINUX_CONTAINMENT_INTERNAL_FAILED: "linux_containment_internal_failed",
}
_LINUX_PR_SET_PDEATHSIG = 1
_LINUX_PR_SET_CHILD_SUBREAPER = 36
_LINUX_PR_GET_CHILD_SUBREAPER = 37


def _component_failure(command: str, state: str, detail: str) -> dict:
    """Return a structured absent-component envelope without raw payloads."""
    return {
        "command": command,
        "status": "hard_failure",
        "isError": True,
        "summary": {
            "verdict": f"{command} evidence unavailable: {detail}",
            "state": state,
            "partial_success": True,
        },
        "error_code": "COMMAND_FAILED",
        "error": detail,
    }


def _strict_json_object_pairs(pairs):
    """Reject ambiguous duplicate keys in a component envelope."""
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _terminate_component_process_tree(proc: _subprocess.Popen) -> bool | None:
    """Terminate the launch boundary and return its verification receipt.

    ``True`` means the platform containment primitive proved the full process
    tree empty. ``None`` means a non-Linux POSIX process group was proved empty
    but descendants that escaped that group cannot be enumerated honestly.
    ``False`` means even the available containment boundary was not verified.
    """
    if _os.name == "nt":
        return _terminate_windows_component_job(proc)
    if _sys.platform.startswith("linux"):
        return _terminate_linux_component_supervisor(proc)
    # Other POSIX systems can verify the saved process group but cannot prove
    # that a setsid(2) descendant did not escape it. Preserve that successful,
    # explicitly degraded receipt instead of turning it into command failure.
    return None if _terminate_posix_component_group(proc) else False


def _component_cleanup_detail(receipt: bool | None) -> str:
    """Render the tri-state cleanup receipt without overstating proof."""
    if receipt is True:
        return "process tree terminated"
    if receipt is None:
        return "process group terminated; descendant proof unavailable"
    return "process-tree cleanup incomplete"


def _create_windows_component_job() -> int | None:
    from roam.sibling_patch.replay_gate import _create_windows_kill_job

    return _create_windows_kill_job()


def _assign_windows_component_job(handle: int, proc: _subprocess.Popen) -> bool:
    from roam.sibling_patch.replay_gate import _assign_windows_kill_job

    return _assign_windows_kill_job(handle, proc)


def _close_windows_component_job_handle(handle: int | None) -> bool:
    from roam.sibling_patch.replay_gate import _close_windows_job_handle

    return _close_windows_job_handle(handle)


def _windows_component_job_active_processes(handle: int) -> int | None:
    """Return the kernel-reported live process count for one Job Object."""
    if _os.name != "nt":
        return None
    try:
        import ctypes
        from ctypes import wintypes

        class _BasicAccountingInformation(ctypes.Structure):
            _fields_ = [
                ("TotalUserTime", ctypes.c_longlong),
                ("TotalKernelTime", ctypes.c_longlong),
                ("ThisPeriodTotalUserTime", ctypes.c_longlong),
                ("ThisPeriodTotalKernelTime", ctypes.c_longlong),
                ("TotalPageFaultCount", wintypes.DWORD),
                ("TotalProcesses", wintypes.DWORD),
                ("ActiveProcesses", wintypes.DWORD),
                ("TotalTerminatedProcesses", wintypes.DWORD),
            ]

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.QueryInformationJobObject.argtypes = [
            wintypes.HANDLE,
            ctypes.c_int,
            ctypes.c_void_p,
            wintypes.DWORD,
            ctypes.POINTER(wintypes.DWORD),
        ]
        kernel32.QueryInformationJobObject.restype = wintypes.BOOL
        info = _BasicAccountingInformation()
        if not kernel32.QueryInformationJobObject(
            wintypes.HANDLE(handle),
            1,  # JobObjectBasicAccountingInformation
            ctypes.byref(info),
            ctypes.sizeof(info),
            None,
        ):
            return None
        return int(info.ActiveProcesses)
    except (AttributeError, OSError, TypeError, ValueError):
        return None


def _terminate_windows_component_job(proc: _subprocess.Popen) -> bool:
    """Terminate a Job Object and prove that it has no active processes."""
    handle = getattr(proc, "_roam_component_job", None)
    if not handle:
        return False
    setattr(proc, "_roam_component_job", None)
    deadline = _time.perf_counter() + _COMPONENT_CLEANUP_SECONDS
    verified = False
    try:
        try:
            import ctypes
            from ctypes import wintypes

            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel32.TerminateJobObject.argtypes = [wintypes.HANDLE, wintypes.UINT]
            kernel32.TerminateJobObject.restype = wintypes.BOOL
            kernel32.TerminateJobObject(wintypes.HANDLE(handle), 1)
        except (AttributeError, OSError, TypeError, ValueError):
            pass

        active_processes: int | None = None
        while _time.perf_counter() < deadline:
            active_processes = _windows_component_job_active_processes(handle)
            if active_processes == 0:
                break
            if active_processes is None:
                break
            _time.sleep(_COMPONENT_CAPTURE_POLL_SECONDS)

        root_reaped = proc.poll() is not None
        if not root_reaped:
            try:
                proc.wait(timeout=max(0.0, deadline - _time.perf_counter()))
                root_reaped = True
            except (OSError, _subprocess.TimeoutExpired):
                root_reaped = False
        verified = active_processes == 0 and root_reaped
    finally:
        closed = _close_windows_component_job_handle(handle)
    # The kill-on-close handle is the final containment fallback. A close
    # failure makes successful teardown unverifiable.
    return verified and closed


def _linux_process_stat(pid: int) -> tuple[int, int, str] | None:
    """Return ``(ppid, start_time, state)`` from procfs for one PID.

    ``start_time`` is Linux's boot-relative process identity token. Callers
    pair it with a pidfd before signalling so a recycled numeric PID can never
    redirect cleanup to an unrelated process.
    """
    try:
        raw = Path(f"/proc/{pid}/stat").read_bytes()
    except (FileNotFoundError, ProcessLookupError):
        return None
    except OSError as exc:
        raise RuntimeError("Linux process identity is unreadable") from exc

    command_end = raw.rfind(b")")
    fields = raw[command_end + 2 :].split() if command_end >= 0 else []
    if len(fields) < 20:
        raise RuntimeError("Linux process identity is malformed")
    try:
        return int(fields[1]), int(fields[19]), fields[0].decode("ascii")
    except (UnicodeDecodeError, ValueError) as exc:
        raise RuntimeError("Linux process identity is malformed") from exc


def _linux_open_pidfd_identity(pid: int) -> tuple[int, int, int] | None:
    """Open an identity-bound pidfd and return ``(pid, start_time, fd)``."""
    pidfd_open = getattr(_os, "pidfd_open", None)
    if not callable(pidfd_open):
        raise RuntimeError("Linux pidfd process identity is unavailable")

    before = _linux_process_stat(pid)
    if before is None:
        return None
    try:
        pidfd = pidfd_open(pid, 0)
    except ProcessLookupError:
        return None
    except OSError as exc:
        raise RuntimeError("Linux pidfd process identity is unavailable") from exc

    try:
        after = _linux_process_stat(pid)
        if after is None or after[1] != before[1] or not _linux_pidfd_is_alive(pidfd):
            _os.close(pidfd)
            return None
    except BaseException:
        _os.close(pidfd)
        raise
    return pid, before[1], pidfd


def _linux_pidfd_is_alive(pidfd: int) -> bool:
    """Use the identity-bound fd rather than a numeric PID for liveness."""
    try:
        import select

        poller = select.poll()
        poller.register(pidfd, select.POLLIN)
        return not poller.poll(0)
    except (AttributeError, OSError, ValueError) as exc:
        raise RuntimeError("Linux pidfd liveness proof is unavailable") from exc


def _linux_task_children(
    pid: int,
    expected_start_time: int,
    identity_pidfd: int | None = None,
) -> set[int]:
    """Read every thread's direct children without crossing a PID reuse."""
    if identity_pidfd is not None and not _linux_pidfd_is_alive(identity_pidfd):
        return set()
    before = _linux_process_stat(pid)
    if before is None or before[1] != expected_start_time:
        return set()
    task_root = Path(f"/proc/{pid}/task")
    try:
        task_ids = tuple(path.name for path in task_root.iterdir() if path.name.isdigit())
    except FileNotFoundError:
        return set()
    except OSError as exc:
        raise RuntimeError("Linux descendant enumeration is unavailable") from exc

    child_pids: set[int] = set()
    for task_id in task_ids:
        try:
            raw_children = (task_root / task_id / "children").read_text(encoding="ascii")
        except FileNotFoundError:
            continue
        except OSError as exc:
            raise RuntimeError("Linux descendant enumeration is unavailable") from exc
        for token in raw_children.split():
            if not token.isdigit():
                raise RuntimeError("Linux descendant enumeration is malformed")
            child_pids.add(int(token))

    after = _linux_process_stat(pid)
    if (
        after is None
        or after[1] != expected_start_time
        or (identity_pidfd is not None and not _linux_pidfd_is_alive(identity_pidfd))
    ):
        # Children of the vanished identity are reparented to our subreaper and
        # will be found from its root on the next bounded scan.
        return set()
    return child_pids


def _linux_descendant_pidfds(
    supervisor_pid: int,
    supervisor_start_time: int,
) -> list[tuple[int, int, int]]:
    """Snapshot all descendants as pidfd-bound process identities."""
    supervisor_stat = _linux_process_stat(supervisor_pid)
    if supervisor_stat is None or supervisor_stat[1] != supervisor_start_time:
        raise RuntimeError("Linux supervisor identity changed during cleanup")

    descendants: list[tuple[int, int, int]] = []
    queue: list[tuple[int, int, int | None]] = [(supervisor_pid, supervisor_start_time, None)]
    seen = {supervisor_pid}
    try:
        while queue:
            parent_pid, parent_start_time, parent_pidfd = queue.pop()
            for child_pid in _linux_task_children(parent_pid, parent_start_time, parent_pidfd):
                if child_pid in seen:
                    continue
                seen.add(child_pid)
                identity = _linux_open_pidfd_identity(child_pid)
                if identity is None:
                    continue
                descendants.append(identity)
                queue.append(identity)
        return descendants
    except BaseException:
        for _pid, _start_time, pidfd in descendants:
            try:
                _os.close(pidfd)
            except OSError:
                pass
        raise


def _linux_pidfd_send_signal(pidfd: int, sig: int) -> None:
    sender = getattr(_signal, "pidfd_send_signal", None)
    if not callable(sender):
        raise RuntimeError("Linux pidfd signalling is unavailable")
    sender(pidfd, sig, None, 0)


def _linux_enable_component_supervision(expected_parent_pid: int) -> bool:
    """Become a subreaper and arm parent-death cleanup before launch."""
    try:
        import ctypes

        libc = ctypes.CDLL(None, use_errno=True)
        prctl = libc.prctl
        prctl.restype = ctypes.c_int
        if prctl(_LINUX_PR_SET_CHILD_SUBREAPER, 1, 0, 0, 0) != 0:
            return False
        current = ctypes.c_int()
        if prctl(_LINUX_PR_GET_CHILD_SUBREAPER, ctypes.byref(current), 0, 0, 0) != 0:
            return False
        if current.value != 1:
            return False
        if prctl(_LINUX_PR_SET_PDEATHSIG, int(_signal.SIGTERM), 0, 0, 0) != 0:
            return False
        # PR_SET_PDEATHSIG has a documented parent-death race. Checking PPID
        # after arming it closes that window before the launch gate is read.
        return _os.getppid() == expected_parent_pid
    except (AttributeError, OSError, TypeError, ValueError):
        return False


def _linux_reap_adopted_children(target: _subprocess.Popen) -> None:
    """Reap subreaper-adopted zombies after the direct target is reaped."""
    if target.poll() is None:
        return
    while True:
        try:
            child_pid, _status = _os.waitpid(-1, _os.WNOHANG)
        except (ChildProcessError, OSError):
            return
        if child_pid == 0:
            return


def _linux_cleanup_supervised_descendants(
    target: _subprocess.Popen,
    supervisor_pid: int,
    supervisor_start_time: int,
) -> bool:
    """Kill, reap, and prove absence of every supervised descendant."""
    deadline = _time.perf_counter() + max(0.1, _COMPONENT_CLEANUP_SECONDS - 0.5)
    empty_scans = 0
    while _time.perf_counter() < deadline:
        try:
            descendants = _linux_descendant_pidfds(supervisor_pid, supervisor_start_time)
        except RuntimeError:
            try:
                if target.poll() is None:
                    target.kill()
            except OSError:
                pass
            return False

        try:
            if descendants:
                empty_scans = 0
                for _pid, _start_time, pidfd in descendants:
                    try:
                        _linux_pidfd_send_signal(pidfd, _signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                    except OSError:
                        # A later zero-descendant scan is the authoritative
                        # proof; transient signal errors cannot create one.
                        pass
            elif target.poll() is not None:
                _linux_reap_adopted_children(target)
                empty_scans += 1
                if empty_scans >= 2:
                    return True
            else:
                # A live direct target missing from procfs makes proof
                # impossible. Kill it through Popen, then fail closed unless a
                # subsequent complete scan observes and reaps it.
                empty_scans = 0
                try:
                    target.kill()
                except OSError:
                    pass
        finally:
            for _pid, _start_time, pidfd in descendants:
                try:
                    _os.close(pidfd)
                except OSError:
                    pass

        if target.poll() is not None:
            _linux_reap_adopted_children(target)
        _time.sleep(_COMPONENT_CAPTURE_POLL_SECONDS)

    try:
        if target.poll() is None:
            target.kill()
    except OSError as exc:
        from roam.observability import log_swallowed

        log_swallowed("cmd_service_report:linux_target_final_kill", exc)
    return False


def _linux_component_supervisor_main(status_fd: int, argv: list[str]) -> int:
    """Supervise one Linux component in a private subreaper process."""
    verified = False
    receipt = _LINUX_CONTAINMENT_PREREQUISITE_FAILED
    target: _subprocess.Popen | None = None
    target_returncode = 125
    stop_requested = False
    supervisor_start_time: int | None = None

    def _request_stop(_signum, _frame) -> None:
        nonlocal stop_requested
        stop_requested = True

    try:
        if not argv:
            return target_returncode
        if not callable(getattr(_os, "pidfd_open", None)) or not callable(getattr(_signal, "pidfd_send_signal", None)):
            return target_returncode
        expected_parent_pid = _os.getppid()
        if not _linux_enable_component_supervision(expected_parent_pid):
            return target_returncode
        supervisor_stat = _linux_process_stat(_os.getpid())
        if supervisor_stat is None:
            return target_returncode
        supervisor_start_time = supervisor_stat[1]

        receipt = _LINUX_CONTAINMENT_GATE_FAILED
        _signal.signal(_signal.SIGTERM, _request_stop)
        _signal.signal(_signal.SIGINT, _request_stop)
        if _os.read(0, 1) != b"1":
            return target_returncode

        receipt = _LINUX_CONTAINMENT_LAUNCH_FAILED
        target = _subprocess.Popen(
            argv,
            stdin=_subprocess.DEVNULL,
            close_fds=True,
        )
        receipt = _LINUX_CONTAINMENT_CLEANUP_FAILED
        while target.poll() is None and not stop_requested:
            _time.sleep(_COMPONENT_CAPTURE_POLL_SECONDS)
        if target.returncode is not None:
            target_returncode = int(target.returncode)

        verified = _linux_cleanup_supervised_descendants(
            target,
            _os.getpid(),
            supervisor_start_time,
        )
        if target.poll() is not None:
            target_returncode = int(target.returncode)
        if not verified:
            return 125
        receipt = _LINUX_CONTAINMENT_VERIFIED
        if 0 <= target_returncode <= 255:
            return target_returncode
        if target_returncode < 0:
            return min(255, 128 + abs(target_returncode))
        return 1
    except BaseException:
        receipt = _LINUX_CONTAINMENT_INTERNAL_FAILED
        if target is not None and supervisor_start_time is not None:
            try:
                verified = _linux_cleanup_supervised_descendants(
                    target,
                    _os.getpid(),
                    supervisor_start_time,
                )
            except (OSError, RuntimeError, TypeError):
                verified = False
            if verified:
                receipt = _LINUX_CONTAINMENT_VERIFIED
        return 125
    finally:
        try:
            _os.write(status_fd, receipt)
        except OSError:
            pass
        try:
            _os.close(status_fd)
        except OSError:
            pass


def _read_linux_supervisor_receipt(proc: _subprocess.Popen) -> bool:
    status_fd = getattr(proc, "_roam_component_status_fd", None)
    setattr(proc, "_roam_component_status_fd", None)
    if not isinstance(status_fd, int) or status_fd < 0:
        setattr(proc, "_roam_component_containment_error", "linux_containment_receipt_missing")
        return False
    try:
        receipt = _os.read(status_fd, 2)
        if receipt == _LINUX_CONTAINMENT_VERIFIED:
            setattr(proc, "_roam_component_containment_error", None)
            return True
        error = _LINUX_CONTAINMENT_ERRORS.get(
            receipt,
            f"linux_containment_receipt_invalid_{receipt.hex() or 'empty'}",
        )
        setattr(proc, "_roam_component_containment_error", error)
        return False
    except OSError:
        setattr(proc, "_roam_component_containment_error", "linux_containment_receipt_unreadable")
        return False
    finally:
        try:
            _os.close(status_fd)
        except OSError:
            pass


def _close_linux_supervisor_pidfd(proc: _subprocess.Popen) -> None:
    pidfd = getattr(proc, "_roam_component_pidfd", None)
    setattr(proc, "_roam_component_pidfd", None)
    if isinstance(pidfd, int) and pidfd >= 0:
        try:
            _os.close(pidfd)
        except OSError:
            pass


def _close_linux_supervisor_status_fd(proc: _subprocess.Popen) -> None:
    status_fd = getattr(proc, "_roam_component_status_fd", None)
    setattr(proc, "_roam_component_status_fd", None)
    if isinstance(status_fd, int) and status_fd >= 0:
        try:
            _os.close(status_fd)
        except OSError:
            pass


def _signal_linux_supervisor(proc: _subprocess.Popen, sig: int) -> bool:
    identity = getattr(proc, "_roam_component_identity", None)
    pidfd = getattr(proc, "_roam_component_pidfd", None)
    if not (
        isinstance(identity, tuple)
        and len(identity) == 2
        and isinstance(identity[0], int)
        and isinstance(identity[1], int)
        and isinstance(pidfd, int)
    ):
        return False
    try:
        if not _linux_pidfd_is_alive(pidfd):
            return False
        current = _linux_process_stat(identity[0])
        if current is None or current[1] != identity[1]:
            return False
        _linux_pidfd_send_signal(pidfd, sig)
        return True
    except (OSError, RuntimeError):
        return False


def _kill_live_linux_supervisor_group(proc: _subprocess.Popen) -> None:
    """Best-effort emergency kill while the original group leader is live."""
    identity = getattr(proc, "_roam_component_identity", None)
    pgid = getattr(proc, "_roam_component_pgid", None)
    if not (isinstance(identity, tuple) and len(identity) == 2 and isinstance(pgid, int) and pgid == identity[0]):
        return
    try:
        current = _linux_process_stat(identity[0])
        if current is not None and current[1] == identity[1]:
            _os.killpg(pgid, _signal.SIGKILL)
    except (OSError, RuntimeError):
        pass


def _terminate_linux_component_supervisor(proc: _subprocess.Popen) -> bool:
    """Request bounded cleanup and verify the supervisor's private receipt."""
    cached = getattr(proc, "_roam_component_tree_verified", None)
    if isinstance(cached, bool):
        return cached

    deadline = _time.perf_counter() + _COMPONENT_CLEANUP_SECONDS
    root_reaped = proc.poll() is not None
    if not root_reaped:
        if not _signal_linux_supervisor(proc, _signal.SIGTERM):
            _signal_linux_supervisor(proc, _signal.SIGKILL)
        try:
            proc.wait(timeout=max(0.0, deadline - _time.perf_counter()))
            root_reaped = True
        except (OSError, _subprocess.TimeoutExpired):
            root_reaped = False

    if not root_reaped:
        _kill_live_linux_supervisor_group(proc)
        try:
            proc.wait(timeout=0.25)
            root_reaped = True
        except (OSError, _subprocess.TimeoutExpired):
            root_reaped = False

    if root_reaped:
        receipt_verified = _read_linux_supervisor_receipt(proc)
    else:
        _close_linux_supervisor_status_fd(proc)
        setattr(proc, "_roam_component_containment_error", "linux_supervisor_unreaped")
        receipt_verified = False
    _close_linux_supervisor_pidfd(proc)
    verified = bool(root_reaped and receipt_verified)
    setattr(proc, "_roam_component_tree_verified", verified)
    return verified


def _posix_component_group_alive(pgid: int) -> bool | None:
    try:
        _os.killpg(pgid, 0)
        return True
    except ProcessLookupError:
        return False
    except OSError:
        return None


def _terminate_posix_component_group(proc: _subprocess.Popen) -> bool:
    """Kill the saved session group even after its original leader exits."""
    pgid = getattr(proc, "_roam_component_pgid", None)
    if not isinstance(pgid, int) or pgid <= 0:
        return False
    try:
        _os.killpg(pgid, _signal.SIGKILL)
    except ProcessLookupError:
        pass
    except OSError:
        return False

    deadline = _time.perf_counter() + _COMPONENT_CLEANUP_SECONDS
    root_reaped = proc.poll() is not None
    if not root_reaped:
        try:
            proc.wait(timeout=max(0.0, deadline - _time.perf_counter()))
            root_reaped = True
        except (OSError, _subprocess.TimeoutExpired):
            root_reaped = False
    while _time.perf_counter() < deadline:
        group_alive = _posix_component_group_alive(pgid)
        if group_alive is False:
            return root_reaped
        if group_alive is None:
            return False
        _time.sleep(_COMPONENT_CAPTURE_POLL_SECONDS)
    return False


def _component_popen_kwargs() -> dict:
    """Return cross-platform process-group isolation for component commands."""
    if _os.name == "nt":
        return {
            "creationflags": (
                getattr(_subprocess, "CREATE_NEW_PROCESS_GROUP", 0x00000200)
                | getattr(_subprocess, "CREATE_NO_WINDOW", 0x08000000)
            )
        }
    return {"start_new_session": True}


def _kill_uncontained_component_root(proc: _subprocess.Popen) -> None:
    """Kill a gated wrapper before it has been allowed to spawn."""
    try:
        if proc.poll() is None:
            proc.kill()
        proc.wait(timeout=_COMPONENT_CLEANUP_SECONDS)
    except (OSError, _subprocess.TimeoutExpired):
        pass


def _start_component_process(
    argv: list[str],
    *,
    cwd: str,
    env: dict[str, str],
) -> _subprocess.Popen:
    """Launch inside a Linux subreaper or Windows kill-on-close job."""
    process_argv = argv
    child_stdin = _subprocess.DEVNULL
    job_handle: int | None = None
    status_read_fd: int | None = None
    status_write_fd: int | None = None
    linux_supervisor = _os.name != "nt" and _sys.platform.startswith("linux")
    linux_gate_opened = False
    popen_extras: dict = {}
    if _os.name == "nt":
        job_handle = _create_windows_component_job()
        if job_handle is None:
            raise RuntimeError("Windows process-tree containment is unavailable")
        process_argv = [_os.path.realpath(_sys.executable), "-c", _WINDOWS_COMPONENT_WRAPPER, *argv]
        child_stdin = _subprocess.PIPE
    elif linux_supervisor:
        if not callable(getattr(_os, "pidfd_open", None)) or not callable(getattr(_signal, "pidfd_send_signal", None)):
            raise RuntimeError("Linux process-tree identity tracking is unavailable")
        if callable(getattr(_os, "pipe2", None)):
            status_read_fd, status_write_fd = _os.pipe2(getattr(_os, "O_CLOEXEC", 0))
        else:  # pragma: no cover - Linux supported Pythons expose pipe2
            status_read_fd, status_write_fd = _os.pipe()
            _os.set_inheritable(status_read_fd, False)
            _os.set_inheritable(status_write_fd, False)
        process_argv = [
            _os.path.realpath(_sys.executable),
            "-c",
            _LINUX_COMPONENT_WRAPPER,
            str(status_write_fd),
            *argv,
        ]
        child_stdin = _subprocess.PIPE
        popen_extras["pass_fds"] = (status_write_fd,)

    proc: _subprocess.Popen | None = None
    try:
        proc = _subprocess.Popen(
            process_argv,
            cwd=cwd,
            env=env,
            shell=False,
            stdin=child_stdin,
            stdout=_subprocess.PIPE,
            stderr=_subprocess.PIPE,
            close_fds=True,
            **popen_extras,
            **_component_popen_kwargs(),
        )
        if status_write_fd is not None:
            _os.close(status_write_fd)
            status_write_fd = None
        if job_handle is not None:
            if not _assign_windows_component_job(job_handle, proc):
                _close_windows_component_job_handle(job_handle)
                job_handle = None
                _kill_uncontained_component_root(proc)
                raise RuntimeError("Windows component could not enter its kill-on-close job")
            setattr(proc, "_roam_component_job", job_handle)
            job_handle = None
            if proc.stdin is None:
                raise RuntimeError("Windows component launch gate is unavailable")
            proc.stdin.write(b"1")
            proc.stdin.close()
        elif linux_supervisor:
            setattr(proc, "_roam_component_pgid", proc.pid)
            identity = _linux_open_pidfd_identity(proc.pid)
            if identity is None:
                raise RuntimeError("Linux supervisor identity could not be established")
            setattr(proc, "_roam_component_identity", (identity[0], identity[1]))
            setattr(proc, "_roam_component_pidfd", identity[2])
            setattr(proc, "_roam_component_status_fd", status_read_fd)
            status_read_fd = None
            if proc.stdin is None:
                raise RuntimeError("Linux component launch gate is unavailable")
            if _os.write(proc.stdin.fileno(), b"1") != 1:
                raise RuntimeError("Linux component launch gate could not be released")
            linux_gate_opened = True
            proc.stdin.close()
        else:
            setattr(proc, "_roam_component_pgid", proc.pid)
        return proc
    except BaseException:
        if proc is not None:
            if getattr(proc, "_roam_component_job", None) or linux_gate_opened:
                _terminate_component_process_tree(proc)
            else:
                if getattr(proc, "stdin", None) is not None:
                    try:
                        proc.stdin.close()
                    except (OSError, ValueError):
                        pass
                _kill_uncontained_component_root(proc)
                _read_linux_supervisor_receipt(proc)
                _close_linux_supervisor_pidfd(proc)
        if job_handle is not None:
            _close_windows_component_job_handle(job_handle)
        for fd in (status_read_fd, status_write_fd):
            if isinstance(fd, int):
                try:
                    _os.close(fd)
                except OSError:
                    pass
        raise


class _BoundedComponentCapture:
    """Thread-safe combined stdout/stderr capture with a hard byte ceiling."""

    def __init__(self, limit: int) -> None:
        self._limit = limit
        self._stdout = bytearray()
        self._stderr = bytearray()
        self._stored = 0
        self._accepting = True
        self._error: str | None = None
        self._lock = _threading.Lock()
        self.oversized = _threading.Event()
        self.failed = _threading.Event()

    def append(self, stream_name: str, chunk: bytes) -> None:
        """Store at most ``limit`` bytes across both streams."""
        with self._lock:
            if not self._accepting or self.oversized.is_set():
                return
            remaining = self._limit - self._stored
            accepted = min(remaining, len(chunk))
            if accepted:
                target = self._stdout if stream_name == "stdout" else self._stderr
                target.extend(chunk[:accepted])
                self._stored += accepted
            if accepted < len(chunk):
                self.oversized.set()

    def note_error(self, exc: BaseException) -> None:
        with self._lock:
            if self._error is None:
                self._error = type(exc).__name__
            self.failed.set()

    def finish(self) -> tuple[bytes, bytes, str | None]:
        with self._lock:
            return bytes(self._stdout), bytes(self._stderr), self._error

    def stop_and_discard(self) -> str | None:
        """Prevent later reader writes and release retained output."""
        with self._lock:
            self._accepting = False
            self._stdout.clear()
            self._stderr.clear()
            return self._error


def _drain_component_pipe(stream, stream_name: str, capture: _BoundedComponentCapture) -> None:
    """Drain one binary pipe in bounded chunks so its sibling cannot deadlock."""
    read_chunk = getattr(stream, "read1", stream.read)
    try:
        while True:
            chunk = read_chunk(_COMPONENT_CAPTURE_CHUNK_BYTES)
            if not chunk:
                return
            if not isinstance(chunk, (bytes, bytearray)):
                raise TypeError("component pipe returned non-bytes output")
            capture.append(stream_name, chunk)
    except (OSError, TypeError, ValueError) as exc:
        capture.note_error(exc)


def _close_component_pipes(
    proc: _subprocess.Popen,
    threads: tuple[_threading.Thread, ...] = (),
) -> bool:
    """Close pipes only after readers exit; cross-thread close can deadlock."""
    if any(thread.is_alive() for thread in threads):
        return False
    closed = True
    for stream_name in ("stdout", "stderr"):
        stream = getattr(proc, stream_name, None)
        if stream is not None:
            try:
                stream.close()
            except (OSError, ValueError):
                closed = False
    return closed


def _wait_for_component_root(
    proc: _subprocess.Popen,
    capture: _BoundedComponentCapture,
    deadline: float,
) -> str:
    """Wait for root exit while remaining immediately responsive to overflow."""
    while True:
        if capture.oversized.is_set():
            return "oversized"
        if capture.failed.is_set():
            return "capture_error"
        try:
            if proc.poll() is not None:
                return "exited"
        except OSError as exc:
            capture.note_error(exc)
            return "wait_error"
        remaining = deadline - _time.perf_counter()
        if remaining <= 0:
            return "timeout"
        capture.oversized.wait(min(_COMPONENT_CAPTURE_POLL_SECONDS, remaining))


def _wait_for_component_drainers(
    threads: tuple[_threading.Thread, ...],
    capture: _BoundedComponentCapture,
    deadline: float,
    *,
    observe_capture_state: bool = True,
) -> str:
    """Wait for pipe EOF without letting inherited handles defeat the deadline."""
    while any(thread.is_alive() for thread in threads):
        if observe_capture_state:
            if capture.oversized.is_set():
                return "oversized"
            if capture.failed.is_set():
                return "capture_error"
        remaining = deadline - _time.perf_counter()
        if remaining <= 0:
            return "timeout"
        join_slice = min(_COMPONENT_CAPTURE_POLL_SECONDS, remaining)
        for thread in threads:
            if thread.is_alive():
                thread.join(join_slice)
                break
    return "oversized" if capture.oversized.is_set() else "completed"


def _capture_component_output(
    proc: _subprocess.Popen,
    *,
    timeout_seconds: float,
) -> tuple[bytes, bytes, str, bool | None, str | None]:
    """Capture both pipes and return the tri-state containment receipt."""
    capture = _BoundedComponentCapture(_COMPONENT_MAX_OUTPUT_BYTES)
    streams = (getattr(proc, "stdout", None), getattr(proc, "stderr", None))
    if any(stream is None for stream in streams):
        try:
            tree_terminated = _terminate_component_process_tree(proc)
        except (OSError, RuntimeError, ValueError):
            tree_terminated = False
        _close_component_pipes(proc)
        return b"", b"", "capture_error", tree_terminated, "missing_pipe"

    threads = tuple(
        _threading.Thread(
            target=_drain_component_pipe,
            args=(stream, stream_name, capture),
            name=f"roam-service-report-{stream_name}",
            daemon=True,
        )
        for stream, stream_name in zip(streams, ("stdout", "stderr"), strict=True)
    )
    deadline = _time.perf_counter() + timeout_seconds
    started_threads: list[_threading.Thread] = []
    try:
        for thread in threads:
            thread.start()
            started_threads.append(thread)
    except RuntimeError as exc:
        capture.note_error(exc)
        capture.stop_and_discard()
        try:
            tree_terminated = _terminate_component_process_tree(proc)
        except (ImportError, OSError, RuntimeError, ValueError):
            tree_terminated = False
        started = tuple(started_threads)
        _wait_for_component_drainers(
            started,
            capture,
            _time.perf_counter() + _COMPONENT_CLEANUP_SECONDS,
            observe_capture_state=False,
        )
        readers_done = not any(thread.is_alive() for thread in started)
        pipes_closed = _close_component_pipes(proc, started)
        if not readers_done or not pipes_closed:
            tree_terminated = False
        return b"", b"", "capture_error", tree_terminated, type(exc).__name__

    state = _wait_for_component_root(proc, capture, deadline)
    tree_terminated: bool | None = None
    cleanup_attempted = False
    if state == "exited":
        # The root's PID is no longer a useful tree anchor. Tear down the
        # durable launch boundary first, then wait a short bounded interval
        # for every inherited writer handle to reach EOF.
        try:
            cleanup_attempted = True
            tree_terminated = _terminate_component_process_tree(proc)
        except (ImportError, OSError, RuntimeError, ValueError):
            tree_terminated = False
        state = _wait_for_component_drainers(
            threads,
            capture,
            _time.perf_counter() + _COMPONENT_CLEANUP_SECONDS,
        )
        if state == "completed" and tree_terminated is False:
            state = "cleanup_error"

    if state != "completed":
        capture.stop_and_discard()
        if not cleanup_attempted:
            try:
                cleanup_attempted = True
                tree_terminated = _terminate_component_process_tree(proc)
            except (ImportError, OSError, RuntimeError, ValueError):
                tree_terminated = False
        cleanup_deadline = _time.perf_counter() + _COMPONENT_CLEANUP_SECONDS
        _wait_for_component_drainers(
            threads,
            capture,
            cleanup_deadline,
            observe_capture_state=False,
        )
        readers_done = not any(thread.is_alive() for thread in threads)
        pipes_closed = _close_component_pipes(proc, threads)
        if not readers_done or not pipes_closed:
            tree_terminated = False
        capture_error = capture.stop_and_discard()
        if tree_terminated is False and capture_error is None:
            capture_error = getattr(proc, "_roam_component_containment_error", None) or "process_tree_unverified"
        return b"", b"", state, tree_terminated, capture_error

    stdout, stderr, capture_error = capture.finish()
    capture.stop_and_discard()
    pipes_closed = _close_component_pipes(proc, threads)
    if not pipes_closed:
        return b"", b"", "cleanup_error", False, "pipe_close_failed"
    if capture_error is not None:
        return b"", b"", "capture_error", tree_terminated, capture_error
    return stdout, stderr, "completed", tree_terminated, None


def _run_roam_json(args: list[str], *, deadline: float | None = None) -> dict:
    """Invoke ``roam --json <args>`` in an isolated, bounded child process.

    Never raises for an expected component failure. Progress / auto-index
    chrome before the JSON payload is tolerated by locating the first ``{``;
    duplicate keys, empty/malformed output, oversized output, launch errors,
    and timeouts become explicit failure envelopes. A valid non-zero command
    envelope is preserved because gate exits can carry useful report evidence.
    """
    command = args[0] if args else "component"
    timeout_seconds: float = _COMPONENT_TIMEOUT_SECONDS
    deadline_limited = False
    if deadline is not None:
        remaining = deadline - _time.monotonic() - _DEADLINE_CLEANUP_RESERVE_SECONDS
        if remaining <= 0:
            return _component_failure(
                command,
                "report_deadline_exhausted",
                "report time budget exhausted before component launch",
            )
        timeout_seconds = min(timeout_seconds, remaining)
        deadline_limited = timeout_seconds < _COMPONENT_TIMEOUT_SECONDS
    argv = [_os.path.realpath(_sys.executable), "-m", "roam", "--json", *args]
    child_env = dict(_os.environ)
    child_env["PYTHONUTF8"] = "1"
    try:
        proc = _start_component_process(argv, cwd=str(Path.cwd()), env=child_env)
    except (OSError, RuntimeError) as exc:
        detail = f"runtime launch failed ({type(exc).__name__})"
        if isinstance(exc, RuntimeError) and str(exc):
            detail = f"{detail}: {exc}"
        return _component_failure(command, "component_unavailable", detail)

    stdout, stderr, capture_state, tree_terminated, capture_error = _capture_component_output(
        proc,
        timeout_seconds=timeout_seconds,
    )
    if capture_state == "oversized":
        cleanup = _component_cleanup_detail(tree_terminated)
        return _component_failure(
            command,
            "component_output_oversized",
            f"output exceeded {_COMPONENT_MAX_OUTPUT_BYTES} bytes; {cleanup}",
        )
    if capture_state == "timeout":
        cleanup = _component_cleanup_detail(tree_terminated)
        state = "report_deadline_exhausted" if deadline_limited else "component_timeout"
        detail = (
            "report time budget exhausted" if deadline_limited else f"timed out after {_COMPONENT_TIMEOUT_SECONDS}s"
        )
        return _component_failure(command, state, f"{detail}; {cleanup}")
    if capture_state != "completed":
        detail = f"output capture failed ({capture_error or capture_state})"
        return _component_failure(
            command,
            "component_unavailable",
            detail,
        )
    text = stdout.decode("utf-8", "replace")
    brace = text.find("{")
    if brace < 0:
        return _component_failure(command, "component_empty_output", "command emitted no JSON envelope")
    try:
        parsed = loads_bounded(text[brace:], object_pairs_hook=_strict_json_object_pairs)
    except (_json.JSONDecodeError, RecursionError, ValueError):
        return _component_failure(command, "component_malformed_output", "command emitted invalid JSON")
    if not isinstance(parsed, dict):
        return _component_failure(command, "component_malformed_output", "command emitted a non-object envelope")
    if tree_terminated is None:
        meta = parsed.get("_meta") if isinstance(parsed.get("_meta"), dict) else {}
        parsed["_meta"] = {
            **meta,
            "service_report_component_cleanup": "process_group_terminated_descendant_proof_unavailable",
        }
    if proc.returncode:
        meta = parsed.get("_meta") if isinstance(parsed.get("_meta"), dict) else {}
        parsed["_meta"] = {**meta, "service_report_component_exit_code": proc.returncode}
    return parsed


def _summary(env: dict) -> dict:
    """Return the ``summary`` sub-dict of an envelope (or ``{}``)."""
    s = env.get("summary") if isinstance(env, dict) else None
    return s if isinstance(s, dict) else {}


def _verdict(env: dict) -> str:
    """Return an envelope's one-line ``summary.verdict`` (or a placeholder)."""
    return str(_summary(env).get("verdict") or "not available")


def _g(env: dict, key: str, default=None):
    """Safe ``summary[key]`` lookup with a default."""
    return _summary(env).get(key, default)


def _cell(value) -> str:
    """Render a scalar for a Markdown table cell (escape the pipe)."""
    if value is None:
        return "—"
    return str(value).replace("|", "/")


def _pct(part, whole) -> str:
    """Format ``part/whole`` as an integer percentage string, guarding /0."""
    try:
        part = float(part)
        whole = float(whole)
    except (TypeError, ValueError):
        return "—"
    if whole <= 0:
        return "—"
    return f"{part * 100 / whole:.0f}%"


# ---------------------------------------------------------------------------
# Shared report chrome — header, disclaimer banner, "not covered", footer.
# Every renderer reuses these so the banner and wording discipline stay
# single-sourced (W184 / W203 clean).
# ---------------------------------------------------------------------------

# The disclaimer banner. "does not certify" is the one allowed negation
# (the wording lint permits a forbidden stem inside a negation window).
_DISCLAIMER_BANNER = (
    "> **Engineering evidence, not an attestation.** This report maps to / "
    "supports evidence for the engineering review below. It does not certify "
    "compliance, replace a professional audit, and its findings depend on "
    "call-graph quality and the declared entry-point inventory. Numbers are "
    "generated from the repository at the index SHA above; review with the "
    "relevant team before acting on them."
)


def _header(
    *,
    type_meta: dict,
    report_type: str,
    client: str | None,
    index_sha: str | None,
    generated_at: str,
    subject: str,
    component_failures: tuple[str, ...] = (),
    component_degraded: tuple[str, ...] = (),
) -> list[str]:
    """Build the shared report header block."""
    out: list[str] = []
    if client:
        out.append(f"# {type_meta['title']} — {client}")
    else:
        out.append(f"# {type_meta['title']}")
    out.append("")
    meta_bits = [
        f"**Type:** {type_meta['label']}",
        f"**Subject:** `{subject}`",
        f"**Index SHA:** `{index_sha or 'unknown'}`",
        f"**Generated:** {generated_at}",
    ]
    out.append(" · ".join(meta_bits) + "  ")
    out.append(f"**Tool:** `roam service-report --type {report_type}`")
    out.append("")
    out.append(_DISCLAIMER_BANNER)
    out.append("")
    if component_failures:
        out.append(
            "> **Partial report:** required evidence is unavailable for "
            + ", ".join(f"`{name}`" for name in component_failures)
            + ". Treat affected conclusions as unresolved."
        )
        out.append("")
    if component_degraded:
        out.append(
            "> **Degraded evidence:** partial results were reported by "
            + ", ".join(f"`{name}`" for name in component_degraded)
            + ". Review those component envelopes before acting."
        )
        out.append("")
    out.append(type_meta["purpose_line"])
    out.append("")
    return out


def _paid_framing(*, type_meta: dict, client: str | None) -> list[str]:
    """Paid-engagement framing block (mirrors pr-replay's tier framing)."""
    out: list[str] = []
    out.append("## About this engagement")
    out.append("")
    who = client or "your team"
    out.append(
        f"This is a **{type_meta['label']}** deliverable prepared for {who}. "
        f"A full paid engagement ({type_meta['engagement_price']}) includes "
        f"founder review of the findings on a call, a written remediation plan, "
        f"and the raw JSON envelopes for every command run. See "
        f"<https://roam-code.com/docs/> or contact services."
    )
    out.append("")
    return out


def _footer(*, report_type: str, generated_at: str, extra_scope: list[str]) -> list[str]:
    """Shared 'what this does not cover' + disclaimer + methodology footer."""
    out: list[str] = []
    out.append("## What this report does not cover")
    out.append("")
    base_scope = [
        "**Semantic correctness** — whether the code does the right thing. "
        "Roam surfaces structural and evidence signals; it does not replace "
        "human or LLM semantic review.",
        "**Legal, financial, or valuation opinion.** This is engineering evidence only.",
    ]
    for item in extra_scope + base_scope:
        out.append(f"- {item}")
    out.append("")
    out.append("## Disclaimer")
    out.append("")
    out.append(
        "Findings are generated by the open-source Roam CLI against the "
        "repository at the index SHA in the header. Reachability and risk "
        "depend on call-graph quality and the declared entry-point inventory; "
        "static analysis can miss dynamically-constructed paths. This report "
        "maps to / supports evidence for an engineering review — it does not "
        "certify compliance and is not a substitute for a professional audit."
    )
    out.append("")
    out.append(
        f"_Generated by `roam service-report --type {report_type}` on "
        f"{generated_at}. Engine: the open-source Roam CLI "
        f"([github.com/Cranot/roam-code](https://github.com/Cranot/roam-code))._"
    )
    out.append("")
    return out


# ---------------------------------------------------------------------------
# Type: due-diligence
# ---------------------------------------------------------------------------


def _gather_components(
    components: tuple[tuple[str, list[str]], ...],
    *,
    max_workers: int = _COMPONENT_MAX_WORKERS,
    deadline: float | None = None,
) -> dict:
    """Run independent read-side components concurrently, preserving order."""
    if not components:
        return {}
    worker_count = min(max(1, max_workers), len(components))
    if worker_count <= 1:
        return {key: _run_roam_json(args, deadline=deadline) for key, args in components}

    results: dict[str, dict] = {}
    with _ThreadPoolExecutor(max_workers=worker_count, thread_name_prefix="roam-service-report") as pool:
        jobs = [(key, pool.submit(_run_roam_json, args, deadline=deadline)) for key, args in components]
        for key, future in jobs:
            try:
                results[key] = future.result()
            except Exception as exc:  # noqa: BLE001 — one component must not erase the report
                results[key] = _component_failure(
                    key.replace("_", "-"),
                    "component_internal_failure",
                    f"component orchestration failed ({type(exc).__name__})",
                )
    return results


_DUE_DILIGENCE_COMPONENTS: tuple[tuple[str, list[str]], ...] = (
    ("health", ["health"]),
    ("bus_factor", ["bus-factor"]),
    ("complexity", ["complexity"]),
    ("dead", ["dead"]),
    ("clones", ["clones"]),
    ("smells", ["smells"]),
    ("test_pyramid", ["test-pyramid"]),
    ("sbom", ["sbom"]),
    ("supply_chain", ["supply-chain"]),
    ("vulns", ["vulns"]),
    ("arch_drift", ["architecture-drift"]),
)


def _gather_due_diligence() -> dict:
    """Run the due-diligence primitives, return {command: envelope}."""
    # Cost-aware scheduling matters more than theoretical parallelism here.
    # ``clones`` owns a ProcessPoolExecutor and must run exclusively; placing
    # it beside the source scanners oversubscribes CPUs and measured slower
    # than serial execution. Lightweight DB summaries can overlap, followed
    # by two compatible source scans and two dependency scans. Reconstruct in
    # registry order so report JSON stays deterministic.
    by_key = dict(_DUE_DILIGENCE_COMPONENTS)
    gathered: dict[str, dict] = {}
    deadline = _time.monotonic() + _DUE_DILIGENCE_BUDGET_SECONDS
    gathered.update(
        _gather_components(
            tuple((key, by_key[key]) for key in ("health", "bus_factor", "complexity", "test_pyramid")),
            deadline=deadline,
        )
    )
    gathered.update(_gather_components((("clones", by_key["clones"]),), max_workers=1, deadline=deadline))
    gathered.update(
        _gather_components(
            tuple((key, by_key[key]) for key in ("dead", "smells")),
            max_workers=2,
            deadline=deadline,
        )
    )
    gathered.update(
        _gather_components(
            tuple((key, by_key[key]) for key in ("sbom", "vulns")),
            max_workers=2,
            deadline=deadline,
        )
    )
    gathered.update(
        _gather_components(
            tuple((key, by_key[key]) for key in ("supply_chain", "arch_drift")),
            max_workers=2,
            deadline=deadline,
        )
    )
    return {key: gathered[key] for key, _args in _DUE_DILIGENCE_COMPONENTS}


def _render_due_diligence(*, env: dict, meta: dict) -> str:
    """Render the due-diligence report (pure — no I/O)."""
    health = env.get("health", {})
    bus = env.get("bus_factor", {})
    cx = env.get("complexity", {})
    dead = env.get("dead", {})
    clones = env.get("clones", {})
    smells = env.get("smells", {})
    pyramid = env.get("test_pyramid", {})
    sbom = env.get("sbom", {})
    supply = env.get("supply_chain", {})
    vulns = env.get("vulns", {})
    drift = env.get("arch_drift", {})

    out: list[str] = _header(**meta)

    # Executive summary — synthesize a conservative verdict from health.
    score = _g(health, "health_score")
    out.append("## 1. Executive summary")
    out.append("")
    if isinstance(score, (int, float)):
        if score >= 75:
            band = "STRONG — investable with routine follow-up"
        elif score >= 55:
            band = "CAUTIONARY — investable with remediation"
        else:
            band = "NEEDS REMEDIATION — material engineering risk"
        out.append(f"**Verdict: {band} (health {score}/100).**")
    else:
        out.append("**Verdict: see sections below (health score unavailable).**")
    out.append("")
    out.append(
        "The sections below are generated directly from the repository. Each "
        "cites the Roam command that produced it so every number is reproducible."
    )
    out.append("")
    out.append(f"- Codebase health: {_verdict(health)}")
    out.append(f"- Key-person risk: {_verdict(bus)}")
    out.append(f"- Duplication: {_verdict(clones)}")
    out.append(f"- Dead code: {_verdict(dead)}")
    out.append("")

    # Health
    out.append("## 2. Codebase health (`roam health`)")
    out.append("")
    out.append("| Metric | Value |")
    out.append("|---|---|")
    out.append(f"| Overall health | {_cell(_g(health, 'health_score'))} / 100 |")
    out.append(f"| Total cycles | {_cell(_g(health, 'cycles_total', _g(health, 'total_cycles')))} |")
    out.append(f"| Actionable cycles | {_cell(_g(health, 'cycles_actionable', _g(health, 'actionable_cycles')))} |")
    out.append(f"| God components | {_cell(_g(health, 'god_components'))} |")
    out.append(f"| Tangle ratio | {_cell(_g(health, 'tangle_ratio'))} |")
    out.append("")

    # Bus factor
    out.append("## 3. Key-person / bus-factor risk (`roam bus-factor`)")
    out.append("")
    out.append(f"{_verdict(bus)}")
    out.append("")
    out.append(f"- High-risk modules: **{_cell(_g(bus, 'high_risk'))}**")
    out.append(f"- Single-owner modules: **{_cell(_g(bus, 'solo_authored_count', _g(bus, 'concentrated')))}**")
    out.append(f"- Directories analyzed: {_cell(_g(bus, 'directories_analyzed'))}")
    out.append("")

    # Complexity + smells
    out.append("## 4. Complexity & maintainability (`roam complexity`, `roam smells`)")
    out.append("")
    out.append("| Signal | Value |")
    out.append("|---|---|")
    out.append(f"| Average cognitive complexity | {_cell(_g(cx, 'average_complexity'))} |")
    out.append(f"| P90 complexity | {_cell(_g(cx, 'p90_complexity'))} |")
    out.append(f"| Critical-complexity symbols | {_cell(_g(cx, 'critical_count'))} |")
    out.append(f"| Symbols analyzed | {_cell(_g(cx, 'total_analyzed'))} |")
    out.append(f"| Total code smells | {_cell(_g(smells, 'total_smells'))} |")
    out.append(f"| Files with smells | {_cell(_g(smells, 'files_affected'))} |")
    out.append("")

    # Dead + clones
    out.append("## 5. Dead code & duplication (`roam dead`, `roam clones`)")
    out.append("")
    out.append(f"- Dead code: {_verdict(dead)}")
    out.append(
        f"  - Files affected: {_cell(_g(dead, 'files_affected'))}, "
        f"estimated remediation: {_cell(_g(dead, 'total_effort_hours'))} hours"
    )
    out.append(f"- Duplication: {_verdict(clones)}")
    reducible = _g(clones, "estimated_reducible_lines")
    if reducible is not None:
        out.append(f"  - Estimated reducible lines: **{_cell(reducible)}**")
    out.append("")

    # Test signal
    out.append("## 6. Test signal (`roam test-pyramid`)")
    out.append("")
    out.append(f"{_verdict(pyramid)}")
    out.append("")
    out.append(
        f"- Test files: {_cell(_g(pyramid, 'total'))} "
        f"(unit {_cell(_g(pyramid, 'unit'))}, integration {_cell(_g(pyramid, 'integration'))}, "
        f"e2e {_cell(_g(pyramid, 'e2e'))})"
    )
    out.append("")

    # Architecture drift
    out.append("## 7. Architecture drift (`roam architecture-drift`)")
    out.append("")
    out.append(f"{_verdict(drift)}")
    out.append("")

    # Security & supply chain
    out.append("## 8. Security & supply chain (`roam vulns`, `roam sbom`, `roam supply-chain`)")
    out.append("")
    out.append("| Source | Signal |")
    out.append("|---|---|")
    out.append(f"| `roam vulns` | {_cell(_verdict(vulns))} |")
    out.append(
        f"| `roam sbom` | {_cell(_g(sbom, 'reachable_count'))} reachable of "
        f"{_cell(_g(sbom, 'total_dependencies'))} deps, {_cell(_g(sbom, 'phantom_count'))} phantom |"
    )
    out.append(
        f"| `roam supply-chain` | risk {_cell(_g(supply, 'risk_score'))}/100, "
        f"pin coverage {_cell(_g(supply, 'pin_coverage_pct'))}% |"
    )
    out.append("")

    # Remediation themes
    out.append("## 9. Remediation themes")
    out.append("")
    out.append(
        "The highest-leverage items surface from sections 2–8 above: break the "
        "actionable cycles, address single-owner concentration in the modules "
        "named by `roam bus-factor`, and reduce the duplication `roam clones` "
        "quantifies. A paid engagement turns these into a costed, sequenced "
        "remediation plan."
    )
    out.append("")

    out.extend(_paid_framing(type_meta=meta["type_meta"], client=meta["client"]))
    out.extend(
        _footer(
            report_type="due-diligence",
            generated_at=meta["generated_at"],
            extra_scope=[
                "**Penetration testing.** Section 8 surfaces structural and reachability signals, not exploit paths.",
                "**Runtime performance profiling.** Complexity is static; it is not a benchmark run.",
            ],
        )
    )
    return "\n".join(out)


# ---------------------------------------------------------------------------
# Type: ai-readiness
# ---------------------------------------------------------------------------


def _gather_ai_readiness() -> dict:
    return _gather_components(
        (
            ("readiness", ["ai-readiness"]),
            ("ai_ratio", ["ai-ratio"]),
            ("agent_score", ["agent-score"]),
            ("mode", ["mode"]),
        )
    )


def _render_ai_readiness(*, env: dict, meta: dict) -> str:
    readiness = env.get("readiness", {})
    ratio = env.get("ai_ratio", {})
    agents = env.get("agent_score", {})
    mode = env.get("mode", {})

    out: list[str] = _header(**meta)

    score = _g(readiness, "score")
    label = _g(readiness, "label")
    out.append("## 1. Executive summary")
    out.append("")
    if score is not None:
        out.append(f"**Readiness verdict: {_cell(score)}/100 — {_cell(label)}.**")
    else:
        out.append("**Readiness verdict: see dimensions below (score unavailable).**")
    out.append("")
    out.append(
        "Readiness is scored across structural dimensions that predict how "
        "safely agents can operate in this codebase, alongside the existing AI "
        "footprint and the governance posture already in place."
    )
    out.append("")

    # Readiness dimensions
    out.append("## 2. Readiness dimensions (`roam ai-readiness`)")
    out.append("")
    dims = readiness.get("dimensions") if isinstance(readiness, dict) else None
    if isinstance(dims, list) and dims:
        out.append("| Dimension | Score | Weight | Contribution |")
        out.append("|---|---:|---:|---:|")
        for d in dims:
            if not isinstance(d, dict):
                continue
            out.append(
                f"| {_cell(d.get('label') or d.get('name'))} | {_cell(d.get('score'))} | "
                f"{_cell(d.get('weight'))} | {_cell(d.get('contribution'))} |"
            )
        out.append("")
    else:
        out.append(f"_{_verdict(readiness)}_")
        out.append("")

    # AI footprint
    out.append("## 3. Existing AI footprint (`roam ai-ratio`)")
    out.append("")
    out.append(f"{_verdict(ratio)}")
    out.append("")
    out.append(
        f"- Estimated AI-generated share: **{_pct(_g(ratio, 'ai_ratio'), 1)}** "
        f"(confidence: {_cell(_g(ratio, 'confidence'))}) across "
        f"{_cell(_g(ratio, 'commits_analyzed'))} commits."
    )
    out.append("")

    # Agent activity
    out.append("## 4. Agent activity (`roam agent-score`)")
    out.append("")
    out.append(f"{_verdict(agents)}")
    out.append("")
    out.append(f"- Agents scored: **{_cell(_g(agents, 'agents_scored', _g(agents, 'count')))}**")
    out.append("")

    # Governance posture
    out.append("## 5. Governance posture (`roam mode`)")
    out.append("")
    out.append("| Gate | Status |")
    out.append("|---|---|")
    out.append(f"| Active mode | {_cell(_g(mode, 'active_mode'))} |")
    out.append(f"| Allowed commands | {_cell(_g(mode, 'allowed_count'))} |")
    out.append(f"| Policy source | {_cell(_g(mode, 'policy_source'))} |")
    out.append(f"| Persisted | {_cell(_g(mode, 'persisted'))} |")
    out.append("")

    # Recommendations
    out.append("## 6. Recommendations")
    out.append("")
    recs = readiness.get("recommendations") if isinstance(readiness, dict) else None
    if isinstance(recs, list) and recs:
        for r in recs[:10]:
            out.append(f"- {_cell(r)}")
    else:
        out.append("- No structured recommendations surfaced; see the dimension scores above.")
    out.append("")

    # Phased rollout
    out.append("## 7. Suggested phased rollout")
    out.append("")
    out.append("| Phase | Scope |")
    out.append("|---|---|")
    out.append("| 1 | Declare an active mode (`roam mode safe_edit`); enforce `roam preflight` pre-commit |")
    out.append("| 2 | Agent edits in the lowest-blast-radius, best-tested zones only |")
    out.append("| 3 | Expand to broader zones under senior review as the readiness score improves |")
    out.append("")

    out.extend(_paid_framing(type_meta=meta["type_meta"], client=meta["client"]))
    out.extend(
        _footer(
            report_type="ai-readiness",
            generated_at=meta["generated_at"],
            extra_scope=[
                "**Team practices & risk appetite.** Readiness scores structural "
                "signals; the rollout decision also depends on team maturity.",
            ],
        )
    )
    return "\n".join(out)


# ---------------------------------------------------------------------------
# Type: reachability-triage
# ---------------------------------------------------------------------------


def _gather_reachability_triage() -> dict:
    return _gather_components(
        (
            ("sbom", ["sbom"]),
            ("supply_chain", ["supply-chain"]),
            ("vulns", ["vulns"]),
            ("vuln_reach", ["vuln-reach"]),
            ("taint", ["taint"]),
            ("secrets", ["secrets"]),
        )
    )


def _render_reachability_triage(*, env: dict, meta: dict) -> str:
    sbom = env.get("sbom", {})
    supply = env.get("supply_chain", {})
    vulns = env.get("vulns", {})
    vuln_reach = env.get("vuln_reach", {})
    taint = env.get("taint", {})
    secrets = env.get("secrets", {})

    out: list[str] = _header(**meta)

    # Executive summary — the reachability wedge.
    total_deps = _g(sbom, "total_dependencies")
    reachable_deps = _g(sbom, "reachable_count")
    taint_findings = _g(taint, "findings", 0)
    secret_findings = _g(secrets, "total_findings", 0)
    reach_vulns = _g(vuln_reach, "reachable_count", 0)

    out.append("## 1. Executive summary")
    out.append("")
    out.append(
        "**The wedge: separate what is reachable from scanner noise.** This "
        "sweep runs the scanners, then filters every finding against the call "
        "graph — only findings reachable from a production entry point warrant "
        "fix work this sprint."
    )
    out.append("")
    if isinstance(total_deps, (int, float)) and isinstance(reachable_deps, (int, float)):
        out.append(
            f"- Dependency reachability: **{_cell(reachable_deps)} of "
            f"{_cell(total_deps)}** dependencies reachable "
            f"({_pct(reachable_deps, total_deps)}); the rest are not reachable "
            f"from the analysed entry points."
        )
    out.append(f"- Reachable known vulnerabilities: **{_cell(reach_vulns)}**")
    out.append(f"- Taint flows: **{_cell(taint_findings)}**")
    out.append(f"- Active secrets: **{_cell(secret_findings)}**")
    out.append("")

    # Reachable vulns
    out.append("## 2. Known vulnerabilities (`roam vulns`, `roam vuln-reach`)")
    out.append("")
    out.append(f"- `roam vulns`: {_verdict(vulns)}")
    out.append(f"- `roam vuln-reach`: {_verdict(vuln_reach)}")
    out.append("")
    if not (_g(vulns, "total") or _g(vuln_reach, "total_vulns")):
        out.append(
            "> No scanner report is ingested for this run. Ingest one with "
            "`roam vulns --import-file <report.json>` (npm-audit, pip-audit, "
            "trivy, or osv) then `roam vuln-map` to populate reachability — the "
            "reachable-vs-raw reduction is the headline number for a paid engagement."
        )
        out.append("")

    # Dependency reachability (the SBOM signal)
    out.append("## 3. Dependency reachability (`roam sbom`)")
    out.append("")
    out.append("| Signal | Value |")
    out.append("|---|---|")
    out.append(f"| Total dependencies | {_cell(_g(sbom, 'total_dependencies'))} |")
    out.append(f"| Reachable | {_cell(_g(sbom, 'reachable_count'))} |")
    out.append(f"| Reachable (direct) | {_cell(_g(sbom, 'reachable_direct_count'))} |")
    out.append(f"| Phantom (declared, not imported) | {_cell(_g(sbom, 'phantom_count'))} |")
    out.append("")
    out.append(f"_{_verdict(sbom)}_")
    out.append("")

    # Taint exposure
    out.append("## 4. Taint exposure (`roam taint`)")
    out.append("")
    out.append(f"{_verdict(taint)}")
    out.append("")
    out.append(
        f"- Findings: **{_cell(_g(taint, 'findings'))}** across "
        f"{_cell(_g(taint, 'rules'))} rule(s); risk score {_cell(_g(taint, 'risk_score'))}."
    )
    out.append("")

    # Secrets
    out.append("## 5. Secrets (`roam secrets`)")
    out.append("")
    out.append(f"{_verdict(secrets)}")
    out.append("")
    out.append(f"- Active secret findings: **{_cell(_g(secrets, 'total_findings'))}**")
    out.append("")

    # Supply chain
    out.append("## 6. Supply chain (`roam supply-chain`)")
    out.append("")
    out.append(f"{_verdict(supply)}")
    out.append("")
    out.append(
        f"- Risk score: {_cell(_g(supply, 'risk_score'))}/100; "
        f"pin coverage {_cell(_g(supply, 'pin_coverage_pct'))}%; "
        f"unpinned {_cell(_g(supply, 'unpinned_count'))} of "
        f"{_cell(_g(supply, 'total_dependencies'))}."
    )
    out.append("")

    # Fix order
    out.append("## 7. Recommended fix order")
    out.append("")
    out.append(
        "1. Any reachable known vulnerability (section 2) — patch first.\n"
        "2. Active secrets (section 5) — rotate, then scrub history.\n"
        "3. Reachable taint flows (section 4) — sanitize the source→sink path.\n"
        "4. Supply-chain pinning (section 6) — pin the unpinned direct deps.\n"
        "5. Defer non-reachable findings; document why in the next scanner baseline."
    )
    out.append("")

    out.extend(_paid_framing(type_meta=meta["type_meta"], client=meta["client"]))
    out.extend(
        _footer(
            report_type="reachability-triage",
            generated_at=meta["generated_at"],
            extra_scope=[
                "**A penetration test or threat model.** Non-reachable findings "
                "may still be exploitable via paths the static graph misses — "
                "review with the security team before deprioritizing.",
            ],
        )
    )
    return "\n".join(out)


# ---------------------------------------------------------------------------
# Type: post-incident
# ---------------------------------------------------------------------------


def _gather_post_incident(commit_range: str) -> dict:
    """Replay a range with postmortem + verify the audit trail."""
    postmortem = _run_postmortem(commit_range, limit=100)
    return {
        "postmortem": postmortem if isinstance(postmortem, dict) else {},
        "audit_trail": _run_roam_json(["audit-trail-verify"]),
    }


def _render_post_incident(*, env: dict, meta: dict, commit_range: str) -> str:
    postmortem = env.get("postmortem", {})
    trail = env.get("audit_trail", {})
    pm_summary = postmortem.get("summary") if isinstance(postmortem, dict) else {}
    pm_summary = pm_summary if isinstance(pm_summary, dict) else {}
    commits = postmortem.get("commits") if isinstance(postmortem, dict) else []
    commits = commits if isinstance(commits, list) else []

    out: list[str] = _header(**meta)

    scanned = pm_summary.get("commits_scanned", len(commits))
    with_findings = pm_summary.get("commits_with_findings", 0)

    out.append("## 1. Incident window")
    out.append("")
    out.append(f"- Replayed range: `{commit_range}`")
    out.append(f"- Commits replayed: **{_cell(scanned)}**")
    out.append(f"- Commits that would have surfaced findings pre-merge: **{_cell(with_findings)}**")
    out.append("")

    # Detector replay
    out.append("## 2. Detector replay (`roam postmortem`)")
    out.append("")
    out.append(
        "Each commit's outgoing diff is replayed against the current detector "
        "set, as if it were a pull request — which findings would have "
        "surfaced before the change merged?"
    )
    out.append("")
    flagged = [
        c for c in commits if isinstance(c, dict) and (int(c.get("high", 0) or 0) + int(c.get("medium", 0) or 0)) > 0
    ]
    if flagged:
        out.append("| Date | SHA | Subject | High | Medium | Top hits |")
        out.append("|---|---|---|---:|---:|---|")
        for c in flagged[:20]:
            subject = (str(c.get("subject") or "")).replace("|", "/")[:60]
            kinds = ", ".join(c.get("kinds") or [])
            out.append(
                f"| {_cell(c.get('date'))} | `{_cell(c.get('short_sha'))}` | {subject} | "
                f"{_cell(c.get('high', 0))} | {_cell(c.get('medium', 0))} | {kinds or '-'} |"
            )
        out.append("")
    else:
        out.append(
            "_No commit in this window would have been flagged by the current "
            "detector set. That is a clean-window observation, not proof of "
            "absence — widen the range or confirm the detector covers the "
            "incident class._"
        )
        out.append("")

    # Audit trail
    out.append("## 3. Audit-trail integrity (`roam audit-trail-verify`)")
    out.append("")
    out.append(f"{_verdict(trail)}")
    out.append("")
    out.append("| Signal | Value |")
    out.append("|---|---|")
    out.append(f"| Chain valid | {_cell(_g(trail, 'chain_valid'))} |")
    out.append(f"| Chain tier | {_cell(_g(trail, 'chain_tier'))} |")
    out.append(f"| Records | {_cell(_g(trail, 'total_records'))} |")
    out.append(f"| Unsigned events | {_cell(_g(trail, 'unsigned_events'))} |")
    out.append("")
    out.append(
        "A verified chain means the run ledger for this window has not been "
        "tampered with — the attribution below rests on a signed record. A "
        'commit with no run record is itself a finding ("shipped without '
        'ledger coverage").'
    )
    out.append("")

    # Prevention
    out.append("## 4. Prevention artifact")
    out.append("")
    out.append(
        "For each detector class that surfaced in section 2, author a rule "
        "under `.roam/rules/` that fails on the incident-introducing change if "
        "reapplied, then wire it into `roam preflight` / `roam critique` so the "
        "same class of change is blocked pre-merge. The durable output of a "
        "post-incident engagement is that rule, not just the narrative."
    )
    out.append("")

    out.extend(_paid_framing(type_meta=meta["type_meta"], client=meta["client"]))
    out.extend(
        _footer(
            report_type="post-incident",
            generated_at=meta["generated_at"],
            extra_scope=[
                "**Full root-cause analysis.** Not every cause is a single "
                "commit, and not every prevention is expressible as a static rule.",
                "**Config-only / infra-only / third-party incidents.** This "
                "replay covers code-change causes tracked in git history.",
            ],
        )
    )
    return "\n".join(out)


# ---------------------------------------------------------------------------
# Dispatch table.
# ---------------------------------------------------------------------------

_GATHER = {
    "due-diligence": lambda commit_range: _gather_due_diligence(),
    "ai-readiness": lambda commit_range: _gather_ai_readiness(),
    "reachability-triage": lambda commit_range: _gather_reachability_triage(),
    "post-incident": lambda commit_range: _gather_post_incident(commit_range),
}


def _render(report_type: str, *, env: dict, meta: dict, commit_range: str) -> str:
    if report_type == "due-diligence":
        return _render_due_diligence(env=env, meta=meta)
    if report_type == "ai-readiness":
        return _render_ai_readiness(env=env, meta=meta)
    if report_type == "reachability-triage":
        return _render_reachability_triage(env=env, meta=meta)
    if report_type == "post-incident":
        return _render_post_incident(env=env, meta=meta, commit_range=commit_range)
    raise ValueError(f"unknown report type: {report_type}")


def _headline(report_type: str, env: dict) -> str:
    """One-line headline for the engagement ledger + envelope summary."""
    if report_type == "due-diligence":
        return _verdict(env.get("health", {}))
    if report_type == "ai-readiness":
        return _verdict(env.get("readiness", {}))
    if report_type == "reachability-triage":
        return _verdict(env.get("sbom", {}))
    if report_type == "post-incident":
        pm = env.get("postmortem", {})
        return _verdict(pm)
    return "not available"


def _component_health(env: dict) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Return explicit failed/degraded component names for report disclosure."""
    failed: list[str] = []
    degraded: list[str] = []
    for name, envelope in env.items():
        if not isinstance(envelope, dict):
            failed.append(name)
            continue
        summary = envelope.get("summary")
        if not isinstance(summary, dict):
            failed.append(name)
            continue
        status = envelope.get("status")
        if envelope.get("isError") is True or status == "hard_failure":
            failed.append(name)
            continue
        meta = envelope.get("_meta")
        cleanup_degraded = isinstance(meta, dict) and meta.get("service_report_component_cleanup") == (
            "process_group_terminated_descendant_proof_unavailable"
        )
        if summary.get("partial_success") is True or status == "soft_failure" or cleanup_degraded:
            degraded.append(name)
    return tuple(failed), tuple(degraded)


# ---------------------------------------------------------------------------
# Engagement ledger — bounded JSONL next to .roam/index.db. Same file
# ``cmd_pr_replay`` writes to; the ``kind`` discriminator distinguishes
# service-report rows from pr-replay rows. Service-report persistence is an
# owner-only atomic rewrite under a private cross-process lock: it never opens
# an existing ledger for append or write, so a hard-linked target cannot turn
# into a write primitive against another pathname.
# ---------------------------------------------------------------------------


_ENGAGEMENT_LEDGER_NAME = "engagements.jsonl"
_ENGAGEMENT_LOCK_NAME = "engagements.jsonl.lock"
_ENGAGEMENT_LEDGER_MAX_BYTES = 2 * 1024 * 1024
_ENGAGEMENT_LEDGER_MAX_RECORDS = 5_000
_ENGAGEMENT_LEDGER_MAX_LINE_BYTES = 64 * 1024
_ENGAGEMENT_LOCK_TIMEOUT_SECONDS = 10.0
_ENGAGEMENT_LOCK_RETRY_SECONDS = 0.01
_ENGAGEMENT_WRITE_CHUNK_BYTES = 64 * 1024
_ENGAGEMENT_THREAD_LOCK = _threading.Lock()
_ENGAGEMENT_LEDGER_STATES = frozenset(
    {
        "not_requested",
        "logged",
        "logged_retention_pruned",
        "unsafe_path",
        "lock_timeout",
        "invalid_ledger",
        "io_failure",
    }
)
_ENGAGEMENT_FAILURE_STATES = frozenset(
    {
        "unsafe_path",
        "lock_timeout",
        "invalid_ledger",
        "io_failure",
    }
)
_FILE_ATTRIBUTE_REPARSE_POINT = 0x00000400


class _EngagementLedgerError(RuntimeError):
    """Internal fixed-vocabulary engagement persistence failure."""

    def __init__(self, state: str):
        if state not in _ENGAGEMENT_FAILURE_STATES:
            raise ValueError(f"unknown engagement ledger failure state: {state}")
        super().__init__(state)
        self.state = state


class _EngagementLockInitializing(RuntimeError):
    """A peer created the lock pathname but has not initialized its byte."""


def _engagement_is_reparse_point(value: _os.stat_result) -> bool:
    return bool(getattr(value, "st_file_attributes", 0) & _FILE_ATTRIBUTE_REPARSE_POINT)


def _engagement_identity(value: _os.stat_result) -> tuple[int, int]:
    return int(value.st_dev), int(value.st_ino)


def _engagement_snapshot(value: _os.stat_result) -> tuple[int, ...]:
    return (
        int(value.st_dev),
        int(value.st_ino),
        int(value.st_size),
        int(getattr(value, "st_mtime_ns", value.st_mtime * 1_000_000_000)),
        int(getattr(value, "st_ctime_ns", value.st_ctime * 1_000_000_000)),
        int(value.st_mode),
        int(value.st_nlink),
    )


def _engagement_resolves_to_itself(path: Path) -> bool:
    return _os.path.normcase(str(path.resolve(strict=True))) == _os.path.normcase(str(path))


def _secure_engagement_directory() -> Path:
    """Return a private, real ``.roam`` directory rooted at the current repo."""

    try:
        root = Path.cwd().resolve(strict=True)
    except OSError as exc:
        raise _EngagementLedgerError("unsafe_path") from exc
    state = root / ".roam"
    if not _os.path.lexists(state):
        if not create_owner_only_directory(state) and not _os.path.lexists(state):
            raise _EngagementLedgerError("io_failure")
    try:
        before = _os.lstat(state)
        if (
            not _stat.S_ISDIR(before.st_mode)
            or _stat.S_ISLNK(before.st_mode)
            or _engagement_is_reparse_point(before)
            or state.parent != root
            or not _engagement_resolves_to_itself(state)
        ):
            raise _EngagementLedgerError("unsafe_path")
        if _os.name == "nt":
            secured = path_is_owner_only(state)
        else:
            flags = _os.O_RDONLY | getattr(_os, "O_DIRECTORY", 0) | getattr(_os, "O_CLOEXEC", 0)
            flags |= getattr(_os, "O_NOFOLLOW", 0)
            directory_fd = _os.open(state, flags)
            try:
                opened = _os.fstat(directory_fd)
                current = _os.lstat(state)
                if (
                    not _stat.S_ISDIR(opened.st_mode)
                    or opened.st_uid != _os.geteuid()
                    or _engagement_identity(opened) != _engagement_identity(before)
                    or _engagement_identity(opened) != _engagement_identity(current)
                ):
                    raise _EngagementLedgerError("unsafe_path")
                secured = not bool(_stat.S_IMODE(opened.st_mode) & 0o077)
            finally:
                _os.close(directory_fd)
        after = _os.lstat(state)
        path_private = path_is_owner_only(state) if _os.name == "nt" else secured
        final = _os.lstat(state)
        if (
            not secured
            or not path_private
            or _engagement_identity(after) != _engagement_identity(before)
            or _engagement_identity(final) != _engagement_identity(before)
        ):
            raise _EngagementLedgerError("unsafe_path")
    except _EngagementLedgerError:
        raise
    except (AttributeError, OSError, TypeError, ValueError) as exc:
        raise _EngagementLedgerError("unsafe_path") from exc
    return state


@_contextmanager
def _pinned_engagement_directory():
    """Pin the private state directory and expose a POSIX-relative handle."""

    state = _secure_engagement_directory()
    try:
        with pinned_owner_only_directory(state):
            if _os.name == "nt":
                yield state, None
                return
            flags = _os.O_RDONLY | getattr(_os, "O_DIRECTORY", 0) | getattr(_os, "O_CLOEXEC", 0)
            flags |= getattr(_os, "O_NOFOLLOW", 0)
            directory_fd = _os.open(state, flags)
            try:
                opened = _os.fstat(directory_fd)
                current = _os.lstat(state)
                if (
                    not _stat.S_ISDIR(opened.st_mode)
                    or opened.st_uid != _os.geteuid()
                    or _stat.S_IMODE(opened.st_mode) & 0o077
                    or _engagement_identity(opened) != _engagement_identity(current)
                ):
                    raise _EngagementLedgerError("unsafe_path")
                yield state, directory_fd
                current_after = _os.lstat(state)
                if _engagement_identity(current_after) != _engagement_identity(opened):
                    raise _EngagementLedgerError("unsafe_path")
            finally:
                _os.close(directory_fd)
    except PermissionError as exc:
        raise _EngagementLedgerError("unsafe_path") from exc


def _engagement_child_stat(state: Path, name: str, directory_fd: int | None) -> _os.stat_result:
    if directory_fd is not None:
        return _os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
    return _os.lstat(state / name)


def _engagement_validate_regular(value: _os.stat_result) -> None:
    if (
        not _stat.S_ISREG(value.st_mode)
        or _stat.S_ISLNK(value.st_mode)
        or _engagement_is_reparse_point(value)
        or value.st_nlink != 1
    ):
        raise _EngagementLedgerError("unsafe_path")


def _engagement_secure_descriptor(
    descriptor: int,
    *,
    state: Path,
    name: str,
    directory_fd: int | None,
) -> _os.stat_result:
    """Validate one child descriptor before any data mutation or read."""

    opened = _os.fstat(descriptor)
    current = _engagement_child_stat(state, name, directory_fd)
    _engagement_validate_regular(opened)
    _engagement_validate_regular(current)
    if _engagement_identity(opened) != _engagement_identity(current):
        raise _EngagementLedgerError("unsafe_path")
    path = state / name
    if _os.name == "nt":
        secured = path_is_owner_only(path)
    else:
        secured = opened.st_uid == _os.geteuid() and not bool(_stat.S_IMODE(opened.st_mode) & 0o077)
    opened_after = _os.fstat(descriptor)
    current_after = _engagement_child_stat(state, name, directory_fd)
    _engagement_validate_regular(opened_after)
    _engagement_validate_regular(current_after)
    if (
        not secured
        or _engagement_identity(opened_after) != _engagement_identity(opened)
        or _engagement_identity(opened_after) != _engagement_identity(current_after)
        or (_os.name == "nt" and not path_is_owner_only(path))
    ):
        raise _EngagementLedgerError("unsafe_path")
    return opened_after


def _engagement_open_existing(
    *,
    state: Path,
    name: str,
    directory_fd: int | None,
    writable: bool,
) -> tuple[int, _os.stat_result]:
    before = _engagement_child_stat(state, name, directory_fd)
    _engagement_validate_regular(before)
    flags = (_os.O_RDWR if writable else _os.O_RDONLY) | getattr(_os, "O_CLOEXEC", 0)
    flags |= getattr(_os, "O_BINARY", 0) | getattr(_os, "O_NOFOLLOW", 0)
    if directory_fd is not None:
        descriptor = _os.open(name, flags, dir_fd=directory_fd)
    else:
        descriptor = _os.open(state / name, flags)
    try:
        opened = _engagement_secure_descriptor(
            descriptor,
            state=state,
            name=name,
            directory_fd=directory_fd,
        )
        if _engagement_identity(opened) != _engagement_identity(before):
            raise _EngagementLedgerError("unsafe_path")
        return descriptor, opened
    except BaseException:
        _os.close(descriptor)
        raise


def _engagement_open_new(
    *,
    state: Path,
    name: str,
    directory_fd: int | None,
    request_delete_access: bool = True,
) -> tuple[int, _os.stat_result]:
    if directory_fd is None:
        descriptor = open_new_owner_only_file(
            state / name,
            request_delete_access=request_delete_access,
        )
    else:
        flags = _os.O_RDWR | _os.O_CREAT | _os.O_EXCL | getattr(_os, "O_CLOEXEC", 0)
        flags |= getattr(_os, "O_NOFOLLOW", 0)
        descriptor = _os.open(name, flags, 0o600, dir_fd=directory_fd)
    try:
        opened = _engagement_secure_descriptor(
            descriptor,
            state=state,
            name=name,
            directory_fd=directory_fd,
        )
        return descriptor, opened
    except BaseException:
        _os.close(descriptor)
        raise


def _engagement_write_all(descriptor: int, payload: bytes) -> None:
    offset = 0
    while offset < len(payload):
        written = _os.write(descriptor, payload[offset : offset + _ENGAGEMENT_WRITE_CHUNK_BYTES])
        if written <= 0:
            raise OSError("engagement ledger write made no progress")
        offset += written


def _open_engagement_lock(state: Path, directory_fd: int | None) -> int:
    try:
        descriptor, _opened = _engagement_open_new(
            state=state,
            name=_ENGAGEMENT_LOCK_NAME,
            directory_fd=directory_fd,
            request_delete_access=False,
        )
    except (FileExistsError, PermissionError) as create_error:
        # A Windows creator pins the lock pathname by denying delete sharing.
        # A concurrent CREATE_NEW can consequently report sharing violation
        # (PermissionError) instead of ERROR_FILE_EXISTS.  Prove and open the
        # already-created owner-only lock before treating that result as
        # contention; an absent or unsafe path still fails closed.
        try:
            descriptor, opened = _engagement_open_existing(
                state=state,
                name=_ENGAGEMENT_LOCK_NAME,
                directory_fd=directory_fd,
                writable=True,
            )
        except FileNotFoundError:
            if isinstance(create_error, PermissionError):
                raise create_error
            raise
        if opened.st_size == 0:
            _os.close(descriptor)
            raise _EngagementLockInitializing
        if opened.st_size != 1:
            _os.close(descriptor)
            raise _EngagementLedgerError("unsafe_path")
        _os.lseek(descriptor, 0, _os.SEEK_SET)
        return descriptor
    try:
        _engagement_write_all(descriptor, b"\0")
        _os.fsync(descriptor)
        initialized = _engagement_secure_descriptor(
            descriptor,
            state=state,
            name=_ENGAGEMENT_LOCK_NAME,
            directory_fd=directory_fd,
        )
        if initialized.st_size != 1:
            raise _EngagementLedgerError("io_failure")
        _os.lseek(descriptor, 0, _os.SEEK_SET)
        return descriptor
    except BaseException:
        _os.close(descriptor)
        raise


def _try_lock_engagement(descriptor: int) -> bool:
    _os.lseek(descriptor, 0, _os.SEEK_SET)
    if _os.name == "nt":
        import msvcrt

        try:
            msvcrt.locking(descriptor, msvcrt.LK_NBLCK, 1)
            return True
        except OSError:
            return False
    import fcntl

    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return True
    except BlockingIOError:
        return False


def _unlock_engagement(descriptor: int) -> None:
    _os.lseek(descriptor, 0, _os.SEEK_SET)
    if _os.name == "nt":
        import msvcrt

        msvcrt.locking(descriptor, msvcrt.LK_UNLCK, 1)
        return
    import fcntl

    fcntl.flock(descriptor, fcntl.LOCK_UN)


@_contextmanager
def _exclusive_engagement_write(state: Path, directory_fd: int | None):
    acquired_thread = _ENGAGEMENT_THREAD_LOCK.acquire(timeout=_ENGAGEMENT_LOCK_TIMEOUT_SECONDS)
    if not acquired_thread:
        raise _EngagementLedgerError("lock_timeout")
    descriptor = -1
    locked = False
    try:
        deadline = _time.monotonic() + _ENGAGEMENT_LOCK_TIMEOUT_SECONDS
        while descriptor < 0:
            try:
                descriptor = _open_engagement_lock(state, directory_fd)
            except _EngagementLockInitializing:
                if _time.monotonic() >= deadline:
                    raise _EngagementLedgerError("lock_timeout") from None
                _time.sleep(_ENGAGEMENT_LOCK_RETRY_SECONDS)
        while not _try_lock_engagement(descriptor):
            if _time.monotonic() >= deadline:
                raise _EngagementLedgerError("lock_timeout")
            _time.sleep(_ENGAGEMENT_LOCK_RETRY_SECONDS)
        locked = True
        locked_value = _engagement_secure_descriptor(
            descriptor,
            state=state,
            name=_ENGAGEMENT_LOCK_NAME,
            directory_fd=directory_fd,
        )
        if locked_value.st_size != 1:
            raise _EngagementLedgerError("unsafe_path")
        yield
    finally:
        if locked:
            try:
                _unlock_engagement(descriptor)
            except OSError:
                pass
        if descriptor >= 0:
            _os.close(descriptor)
        _ENGAGEMENT_THREAD_LOCK.release()


def _engagement_finite_float(value: str) -> float:
    parsed = float(value)
    if not _math.isfinite(parsed):
        raise ValueError("non-finite JSON number")
    return parsed


def _engagement_reject_constant(_value: str):
    raise ValueError("non-finite JSON constant")


def _read_engagement_rows(
    *,
    state: Path,
    directory_fd: int | None,
) -> tuple[list[bytes], bool, tuple[int, ...] | None]:
    try:
        descriptor, opened = _engagement_open_existing(
            state=state,
            name=_ENGAGEMENT_LEDGER_NAME,
            directory_fd=directory_fd,
            writable=False,
        )
    except FileNotFoundError:
        return [], False, None
    try:
        descriptor_snapshot = _engagement_snapshot(opened)
        path_snapshot = _engagement_snapshot(_engagement_child_stat(state, _ENGAGEMENT_LEDGER_NAME, directory_fd))
        read_budget = _ENGAGEMENT_LEDGER_MAX_BYTES + _ENGAGEMENT_LEDGER_MAX_LINE_BYTES + 1
        read_size = min(opened.st_size, read_budget)
        start = opened.st_size - read_size
        _os.lseek(descriptor, start, _os.SEEK_SET)
        chunks: list[bytes] = []
        remaining = read_size
        while remaining:
            chunk = _os.read(descriptor, min(remaining, _ENGAGEMENT_WRITE_CHUNK_BYTES))
            if not chunk:
                raise _EngagementLedgerError("invalid_ledger")
            chunks.append(chunk)
            remaining -= len(chunk)
        payload = b"".join(chunks)
        prefix_pruned = start > 0
        if prefix_pruned:
            boundary = payload.find(b"\n")
            if boundary < 0:
                raise _EngagementLedgerError("invalid_ledger")
            payload = payload[boundary + 1 :]
        opened_after = _os.fstat(descriptor)
        current_after = _engagement_child_stat(state, _ENGAGEMENT_LEDGER_NAME, directory_fd)
        path_private = _os.name != "nt" or path_is_owner_only(state / _ENGAGEMENT_LEDGER_NAME)
        current_final = _engagement_child_stat(state, _ENGAGEMENT_LEDGER_NAME, directory_fd)
        if (
            _engagement_snapshot(opened_after) != descriptor_snapshot
            or _engagement_snapshot(current_after) != path_snapshot
            or _engagement_snapshot(current_final) != path_snapshot
            or _engagement_identity(opened_after) != _engagement_identity(current_after)
            or not path_private
        ):
            raise _EngagementLedgerError("unsafe_path")
    finally:
        _os.close(descriptor)

    rows: list[bytes] = []
    for raw_line in payload.splitlines():
        if not raw_line:
            continue
        if len(raw_line) > _ENGAGEMENT_LEDGER_MAX_LINE_BYTES:
            raise _EngagementLedgerError("invalid_ledger")
        try:
            decoded = raw_line.decode("utf-8", "strict")
            value = loads_bounded(
                decoded,
                object_pairs_hook=_strict_json_object_pairs,
                parse_constant=_engagement_reject_constant,
                parse_float=_engagement_finite_float,
            )
        except (UnicodeDecodeError, _json.JSONDecodeError, RecursionError, TypeError, ValueError) as exc:
            raise _EngagementLedgerError("invalid_ledger") from exc
        if not isinstance(value, dict):
            raise _EngagementLedgerError("invalid_ledger")
        encoded = _json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        if len(encoded) > _ENGAGEMENT_LEDGER_MAX_LINE_BYTES:
            raise _EngagementLedgerError("invalid_ledger")
        rows.append(encoded)
    return rows, prefix_pruned, path_snapshot


def _engagement_destination_matches(
    *,
    state: Path,
    directory_fd: int | None,
    expected: tuple[int, ...] | None,
) -> bool:
    try:
        current = _engagement_child_stat(state, _ENGAGEMENT_LEDGER_NAME, directory_fd)
    except FileNotFoundError:
        return expected is None
    try:
        _engagement_validate_regular(current)
    except _EngagementLedgerError:
        return False
    return expected is not None and _engagement_snapshot(current) == expected


def _install_engagement_payload(
    payload: bytes,
    *,
    state: Path,
    directory_fd: int | None,
    expected: tuple[int, ...] | None,
) -> Path:
    descriptor = -1
    temporary_name: str | None = None
    for _attempt in range(128):
        candidate = f".{_ENGAGEMENT_LEDGER_NAME}.{_secrets.token_hex(8)}.tmp"
        try:
            descriptor, _opened = _engagement_open_new(
                state=state,
                name=candidate,
                directory_fd=directory_fd,
            )
        except FileExistsError:
            continue
        temporary_name = candidate
        break
    if descriptor < 0 or temporary_name is None:
        raise _EngagementLedgerError("io_failure")
    try:
        _engagement_write_all(descriptor, payload)
        _os.fsync(descriptor)
        temporary = _engagement_secure_descriptor(
            descriptor,
            state=state,
            name=temporary_name,
            directory_fd=directory_fd,
        )
        source_generation = capture_file_generation(
            descriptor,
            max_bytes=_ENGAGEMENT_LEDGER_MAX_BYTES,
        )
        if temporary.st_size != len(payload) or not _engagement_destination_matches(
            state=state,
            directory_fd=directory_fd,
            expected=expected,
        ):
            raise _EngagementLedgerError("unsafe_path")
        # Windows owner-only descriptors deliberately deny delete sharing, so
        # close the identity-pinning producer handle before publication.  The
        # public installer re-proves both metadata and SHA-256 immediately
        # before its native move; this also closes the same-size rewrite gap on
        # POSIX without weakening lock-file pinning.
        _os.close(descriptor)
        descriptor = -1

        def _assert_destination_unchanged() -> None:
            if not _engagement_destination_matches(
                state=state,
                directory_fd=directory_fd,
                expected=expected,
            ):
                raise _EngagementLedgerError("unsafe_path")

        conditional_install_file(
            state / temporary_name,
            state / _ENGAGEMENT_LEDGER_NAME,
            source_generation=source_generation,
            before_install=_assert_destination_unchanged,
            durable=True,
        )
        current = _engagement_child_stat(state, _ENGAGEMENT_LEDGER_NAME, directory_fd)
        path_private = _os.name != "nt" or path_is_owner_only(state / _ENGAGEMENT_LEDGER_NAME)
        current_final = _engagement_child_stat(state, _ENGAGEMENT_LEDGER_NAME, directory_fd)
        if (
            _engagement_identity(current) != _engagement_identity(temporary)
            or _engagement_identity(current_final) != _engagement_identity(temporary)
            or current.st_size != len(payload)
            or current_final.st_size != len(payload)
            or current.st_nlink != 1
            or current_final.st_nlink != 1
            or not path_private
        ):
            raise _EngagementLedgerError("io_failure")
        if directory_fd is not None:
            _os.fsync(directory_fd)
        return state / _ENGAGEMENT_LEDGER_NAME
    finally:
        if descriptor >= 0:
            _os.close(descriptor)
        # If installation did not happen, deliberately leave the random,
        # owner-only tempfile in place. A pathname check followed by unlink is
        # not an identity-bound delete on POSIX and could remove a raced
        # replacement. Failing safely is preferable to unsafe cleanup.


def _set_engagement_diagnostics(
    diagnostics: dict | None,
    *,
    state: str,
    retention_pruned: bool = False,
    records_retained: int = 0,
) -> None:
    if state not in _ENGAGEMENT_LEDGER_STATES:
        raise ValueError(f"unknown engagement ledger state: {state}")
    if diagnostics is not None:
        diagnostics.clear()
        diagnostics.update(
            {
                "state": state,
                "retention_pruned": bool(retention_pruned),
                "records_retained": int(records_retained),
            }
        )


def _persist_engagement_record(
    record: dict,
    *,
    diagnostics: dict | None = None,
) -> Path | None:
    """Persist one bounded engagement record under the shared ledger lock.

    Returns the ledger path on success, ``None`` on failure (never raises —
    telemetry must not break a buyer-facing run). When supplied, *diagnostics*
    receives only closed-vocabulary state and bounded numeric fields.
    """
    _set_engagement_diagnostics(diagnostics, state="not_requested")
    try:
        encoded_record = _json.dumps(record, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        if len(encoded_record) > _ENGAGEMENT_LEDGER_MAX_LINE_BYTES:
            raise _EngagementLedgerError("invalid_ledger")
        with _pinned_engagement_directory() as (state, directory_fd):
            with _exclusive_engagement_write(state, directory_fd):
                rows, prefix_pruned, expected = _read_engagement_rows(
                    state=state,
                    directory_fd=directory_fd,
                )
                rows.append(encoded_record)
                retention_pruned = prefix_pruned
                while len(rows) > _ENGAGEMENT_LEDGER_MAX_RECORDS:
                    del rows[0]
                    retention_pruned = True
                payload_size = sum(len(row) + 1 for row in rows)
                while rows and payload_size > _ENGAGEMENT_LEDGER_MAX_BYTES:
                    removed = rows.pop(0)
                    payload_size -= len(removed) + 1
                    retention_pruned = True
                if not rows or rows[-1] != encoded_record:
                    raise _EngagementLedgerError("invalid_ledger")
                payload = b"\n".join(rows) + b"\n"
                ledger = _install_engagement_payload(
                    payload,
                    state=state,
                    directory_fd=directory_fd,
                    expected=expected,
                )
                _set_engagement_diagnostics(
                    diagnostics,
                    state="logged_retention_pruned" if retention_pruned else "logged",
                    retention_pruned=retention_pruned,
                    records_retained=len(rows),
                )
                return ledger
    except _EngagementLedgerError as exc:
        _set_engagement_diagnostics(diagnostics, state=exc.state)
        return None
    except (OSError, RuntimeError, TypeError, UnicodeError, ValueError):
        _set_engagement_diagnostics(diagnostics, state="io_failure")
        return None


def _record_engagement(
    *,
    report_type: str,
    client: str | None,
    subject: str,
    headline: str,
    output_path: str,
    generated_at: str,
    diagnostics: dict | None = None,
) -> Path | None:
    """Persist one bounded service-report engagement record safely."""

    return _persist_engagement_record(
        {
            "ledger_schema": 1,
            "kind": "service-report",
            "report_type": report_type,
            "client": client,
            "subject": subject,
            "headline": headline,
            "output_path": output_path,
            "generated_at": generated_at,
        },
        diagnostics=diagnostics,
    )


# ---------------------------------------------------------------------------
# CLI entry point.
# ---------------------------------------------------------------------------


@roam_capability(
    category="review",
    summary="Generate a one-command service-engagement report (due-diligence, AI-readiness, reachability-triage, post-incident).",
    inputs=["report_type"],
    outputs=["narrative_report", "sections"],
    examples=[
        "roam service-report --type due-diligence",
        "roam service-report --type reachability-triage --client 'Acme Inc' --output triage.md",
        "roam service-report --type post-incident --range v1.0..main --output incident.md",
    ],
    tags=["audit", "review", "services", "demo"],
    ai_safe=True,
    requires_index=True,
    since="13.5",
    side_effect=True,
)
@click.command(name="service-report")
@click.option(
    "--type",
    "report_type",
    type=click.Choice(list(_REPORT_TYPES.keys()), case_sensitive=False),
    required=True,
    help=(
        "Report type. ``due-diligence`` (codebase health / M&A), "
        "``ai-readiness`` (AI adoption readiness), ``reachability-triage`` "
        "(security noise-reduction), or ``post-incident`` (detector + "
        "audit-trail replay of a commit range)."
    ),
)
@click.option(
    "--client",
    default=None,
    help="Client name to inject into the report header (paid framing).",
)
@click.option(
    "--range",
    "commit_range",
    default=None,
    help=(
        "Commit range for ``--type post-incident`` (e.g. ``v1.0..main``, "
        "``HEAD~30..HEAD``). Ignored by the other report types. Defaults to "
        "``HEAD~20..HEAD`` when unset."
    ),
)
@click.option(
    "--output",
    "output_path",
    default=None,
    type=click.Path(dir_okay=False, writable=True),
    help="Write the Markdown report to PATH instead of stdout.",
)
@click.option(
    "--pdf",
    "pdf_path",
    default=None,
    type=click.Path(dir_okay=False, writable=True),
    help=(
        "Also write a PDF render of the report to PATH (requires ``pandoc`` on "
        "PATH, or ``reportlab`` as a fallback). Implies --output if unset; the "
        "Markdown source is written next to the PDF as ``<pdf>.md``."
    ),
)
@click.option(
    "--track-engagement/--no-track-engagement",
    default=True,
    show_default=True,
    help=(
        "When --output is set, append a one-line JSONL record to "
        "``.roam/engagements.jsonl`` (report type, client, subject, headline, "
        "output path, timestamp) so the operator has a single-file ledger of "
        "every delivered report."
    ),
)
@click.pass_context
def service_report_cmd(
    ctx,
    report_type: str,
    client: str | None,
    commit_range: str | None,
    output_path: str | None,
    pdf_path: str | None,
    track_engagement: bool,
):
    """Generate a one-command service-engagement report.

    Runs the right existing Roam primitives for the chosen ``--type``,
    aggregates their JSON envelopes, and emits a buyer-facing narrative
    report — the productised form of the templates under
    ``templates/services-reports/``. Sibling of ``roam pr-replay``.

    \b
    Examples:
      roam service-report --type due-diligence
      roam service-report --type reachability-triage --client "Acme Inc" --output triage.md
      roam service-report --type post-incident --range v1.0..main --output incident.md

    \b
    Output: Markdown by default; ``roam --json service-report`` returns the
    full envelope (summary + sections + report_markdown).
    """
    json_mode = ctx.obj.get("json") if ctx.obj else False
    report_type = report_type.lower()
    type_meta = _REPORT_TYPES[report_type]
    ensure_index()

    # Post-incident is the only type that consumes a commit range. Validate
    # it the same way pr-replay validates --range (reject argv-injection
    # shapes) and default to a recent window.
    if report_type == "post-incident":
        if commit_range is None:
            commit_range = "HEAD~20..HEAD"
        elif not _is_safe_commit_range(commit_range):
            raise click.UsageError(
                f"--range value must not start with '-' (got {commit_range!r}); "
                "use a git revspec like 'HEAD~30..HEAD', 'v1.0..main', or a branch name."
            )
    else:
        commit_range = commit_range or ""

    generated_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    index_sha = _git_head_sha()
    subject = client or "target repository"

    # Gather best-effort, but never collapse a failed component into an empty
    # successful-looking report. Each expected failure is already represented
    # by a structured component envelope; this outer guard handles only an
    # unexpected orchestration defect.
    try:
        env = _GATHER[report_type](commit_range)
    except Exception as exc:  # noqa: BLE001 — the report must survive a bad section
        env = {
            report_type: _component_failure(
                report_type,
                "report_gather_failure",
                f"report gathering failed ({type(exc).__name__})",
            )
        }

    component_failures, component_degraded = _component_health(env)

    meta = {
        "type_meta": type_meta,
        "report_type": report_type,
        "client": client,
        "index_sha": index_sha,
        "generated_at": generated_at,
        "subject": subject,
        "component_failures": component_failures,
        "component_degraded": component_degraded,
    }
    report_md = _render(report_type, env=env, meta=meta, commit_range=commit_range)
    headline = _headline(report_type, env)
    if component_failures:
        headline = f"{headline} — partial report: {len(component_failures)} unavailable components"
    if component_degraded:
        headline = f"{headline} — degraded evidence: {len(component_degraded)} partial components"

    # --pdf without --output writes the markdown sibling next to the PDF.
    if pdf_path and not output_path:
        output_path = str(Path(pdf_path).with_suffix(".md"))

    if output_path:
        Path(output_path).write_text(report_md, encoding="utf-8")
        if not json_mode:
            click.echo(f"Wrote {len(report_md):,} bytes to {output_path}")

    requested_pdf_path = pdf_path
    delivered_pdf_path = None
    pdf_backend = None
    pdf_state = "not_requested"
    pdf_failure = None
    artifact_failures: list[str] = []
    if requested_pdf_path:
        try:
            ok, info = _render_pdf(report_md, Path(requested_pdf_path))
        except Exception:  # noqa: BLE001 — renderer failures are a bounded delivery state
            ok, info = False, None
        if ok:
            delivered_pdf_path = requested_pdf_path
            pdf_backend = info
            pdf_state = "delivered"
            if not json_mode:
                click.echo(f"Wrote PDF to {requested_pdf_path} (backend: {info})")
        else:
            pdf_state = "render_failed"
            pdf_failure = "pdf_render_failed"
            artifact_failures.append("pdf_render_failed")
            click.echo("WARNING: PDF render failed — requested artifact was not delivered", err=True)

    engagement_record = None
    engagement_diagnostics: dict = {
        "state": "not_requested",
        "retention_pruned": False,
        "records_retained": 0,
    }
    if track_engagement and output_path:
        engagement_record = _record_engagement(
            report_type=report_type,
            client=client,
            subject=subject,
            headline=headline,
            output_path=output_path,
            generated_at=generated_at,
            diagnostics=engagement_diagnostics,
        )
        if engagement_record and not json_mode:
            click.echo(f"Logged engagement to {engagement_record}")
        elif not engagement_record:
            click.echo("WARNING: Engagement ledger persistence failed", err=True)

    engagement_failed = engagement_diagnostics["state"] in _ENGAGEMENT_FAILURE_STATES
    summary_verdict = headline
    if artifact_failures:
        summary_verdict = f"{summary_verdict} — partial delivery: PDF artifact unavailable"
    if engagement_failed:
        summary_verdict = f"{summary_verdict} — engagement ledger unavailable"
    if component_failures:
        summary_state = "component_failure"
    elif artifact_failures:
        summary_state = "artifact_failure"
    elif engagement_failed:
        summary_state = "engagement_persistence_failure"
    elif component_degraded:
        summary_state = "component_degraded"
    else:
        summary_state = "complete"
    partial_success = bool(component_failures or component_degraded or artifact_failures or engagement_failed)

    if json_mode:
        envelope = json_envelope(
            "service-report",
            summary={
                "verdict": summary_verdict,
                "report_type": report_type,
                "client": client,
                "subject": subject,
                "commit_range": commit_range or None,
                "index_sha": index_sha,
                "generated_at": generated_at,
                "output_path": output_path,
                "pdf_requested_path": requested_pdf_path,
                "pdf_path": delivered_pdf_path,
                "pdf_backend": pdf_backend,
                "pdf_state": pdf_state,
                "pdf_failure": pdf_failure,
                "artifact_failures": artifact_failures,
                "engagement_logged_to": str(engagement_record) if engagement_record else None,
                "engagement_ledger_state": engagement_diagnostics["state"],
                "engagement_ledger_failure": (engagement_diagnostics["state"] if engagement_failed else None),
                "engagement_retention_pruned": engagement_diagnostics["retention_pruned"],
                "engagement_records_retained": engagement_diagnostics["records_retained"],
                "sections_present": sorted(k for k, v in env.items() if v),
                "sections_failed": list(component_failures),
                "sections_degraded": list(component_degraded),
                "state": summary_state,
                "partial_success": partial_success,
            },
            report_markdown=report_md,
            sections=env,
        )
        _target = (f"{report_type}:{commit_range}" if commit_range else report_type)[:80]
        try:
            auto_log(envelope, action="service-report", target=_target)
        except Exception as _exc:  # noqa: BLE001 — telemetry must not break the run
            # Telemetry failure must not break the report — surface lineage
            # so a dropped engagement-log record has a traceable cause.
            from roam.observability import log_swallowed

            log_swallowed("cmd_service_report:auto_log", _exc)
        click.echo(to_json(envelope))
        _ = EXIT_SUCCESS
        return

    if not output_path:
        click.echo(report_md)

    _ = EXIT_SUCCESS
    return
