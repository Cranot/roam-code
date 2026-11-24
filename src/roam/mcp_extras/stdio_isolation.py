"""Private standard input for the MCP stdio transport.

Every child a process starts without an explicit ``stdin=`` inherits the
process's standard input. While ``roam mcp`` serves stdio, that input is the
protocol pipe, and the transport's reader thread keeps a synchronous read
pending on it. On Windows a child that touches an inherited pipe handle with a
read pending blocks at startup: ``git status``, ``git rev-parse``, ``git diff``
and Python children were all measured hanging until their caller's timeout,
so tools such as ``roam_diff`` answered ``git_timeout`` with no changes.

``isolate_stdio_transport_stdin`` closes the class at the process boundary
instead of call by call. It duplicates the pipe onto a private,
non-inheritable descriptor for the transport and points descriptor 0 (and, on
Windows, the process's ``STD_INPUT_HANDLE``) at the null device. Every child
started afterwards, by any code path, reads end-of-file from its stdin.
"""

from __future__ import annotations

import os
import sys

_STD_INPUT_HANDLE = -10


def _point_windows_std_input_at_fd0() -> None:
    import ctypes
    import msvcrt

    handle = msvcrt.get_osfhandle(0)
    if not ctypes.windll.kernel32.SetStdHandle(_STD_INPUT_HANDLE, handle):
        raise ctypes.WinError()


def isolate_stdio_transport_stdin() -> bool:
    """Move the transport onto a private stdin; children inherit the null device.

    Returns ``True`` when the swap happened. Returns ``False`` and changes
    nothing when ``sys.stdin`` is not the process's descriptor 0 (an embedded
    or test runner already replaced it), so only a real stdio server is moved.
    """
    try:
        if sys.stdin is None or sys.stdin.fileno() != 0:
            return False
    except (AttributeError, OSError, ValueError):
        return False
    encoding = getattr(sys.stdin, "encoding", None) or "utf-8"
    errors = getattr(sys.stdin, "errors", None) or "strict"
    private_fd = os.dup(0)  # non-inheritable (PEP 446): no child can receive it
    null_fd = os.open(os.devnull, os.O_RDONLY)
    try:
        os.dup2(null_fd, 0)
    finally:
        os.close(null_fd)
    if os.name == "nt":
        _point_windows_std_input_at_fd0()
    sys.stdin = open(private_fd, encoding=encoding, errors=errors, closefd=True)
    return True
