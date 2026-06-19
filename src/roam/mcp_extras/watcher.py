"""File watcher that invalidates MCP resources on disk changes.

When the user edits source files outside the agent (or the agent
itself uses ``Edit``/``Write``), roam's resources go stale until the
next ``roam_reindex`` call. With this watcher running, the server:

1. Observes the project root with ``watchdog``.
2. Debounces a flurry of writes into a single re-index pass.
3. Runs ``roam index`` (incremental) in a background thread.
4. Emits ``notifications/resources/updated`` for the affected
   ``roam://...`` resources.

Disabled by default; enable via :func:`start_watcher`. Falls through
silently if ``watchdog`` is not installed or the FastMCP build does
not expose the resources/updated notification API.
"""

from __future__ import annotations

import asyncio
import os
import threading
import time
from pathlib import Path
from typing import Any

from roam.observability import log_swallowed

# Resources that depend on indexed code state. When any source file
# changes we re-emit updates for these so subscribed clients refresh.
_DEPENDENT_RESOURCES = (
    "roam://health",
    "roam://summary",
    "roam://hotspots",
    "roam://recent-changes",
    "roam://complexity",
    "roam://dead-code",
    "roam://dependencies",
    "roam://architecture",
    "roam://tech-stack",
    "roam://test-coverage",
)

# Suffixes considered "code" -- broad enough for the 27 supported
# languages, narrow enough to skip lockfiles, build outputs, media.
_CODE_SUFFIXES = {
    ".py",
    ".js",
    ".jsx",
    ".ts",
    ".tsx",
    ".mjs",
    ".cjs",
    ".java",
    ".kt",
    ".kts",
    ".scala",
    ".go",
    ".rs",
    ".c",
    ".h",
    ".cpp",
    ".cc",
    ".hpp",
    ".cs",
    ".rb",
    ".php",
    ".swift",
    ".sql",
    ".cls",
    ".trigger",
    ".cmp",
    ".page",
    ".html",
    ".vue",
    ".tf",
    ".hcl",
    ".yaml",
    ".yml",
    ".prg",
    ".sf",
}

_IGNORE_DIR_NAMES = {".roam", ".git", "node_modules", "__pycache__", "dist", "build", ".venv", "venv"}


def _is_code_file(path: str) -> bool:
    if not path:
        return False
    suffix = Path(path).suffix.lower()
    return suffix in _CODE_SUFFIXES


def _within_ignored(path: str) -> bool:
    parts = Path(path).parts
    return any(p in _IGNORE_DIR_NAMES for p in parts)


class _DebouncedReindexer:
    """Coalesces file-change events into one reindex per debounce window."""

    def __init__(
        self,
        *,
        root: Path,
        notify: Any,
        debounce_seconds: float = 1.5,
    ) -> None:
        self._root = root
        self._notify = notify  # async callable: notify(uris: Iterable[str]) -> None
        self._debounce = debounce_seconds
        self._pending: set[str] = set()
        self._lock = threading.Lock()
        self._timer: threading.Timer | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._stopped = False

    def bind_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop

    def push(self, src_path: str) -> None:
        if self._stopped:
            return
        if not _is_code_file(src_path) or _within_ignored(src_path):
            return
        with self._lock:
            self._pending.add(src_path)
            if self._timer is not None:
                self._timer.cancel()
            self._timer = threading.Timer(self._debounce, self._fire)
            self._timer.daemon = True
            self._timer.start()

    def _fire(self) -> None:
        with self._lock:
            files = list(self._pending)
            self._pending.clear()
            self._timer = None
        if not files:
            return
        try:
            self._do_reindex(files)
        except Exception as exc:  # noqa: BLE001 — background thread, never crash the observer
            log_swallowed("watcher:_fire.reindex", exc)
            return
        self._dispatch_notify()

    def _do_reindex(self, files: list[str]) -> None:
        """Run incremental index. Best-effort -- no exceptions escape."""
        try:
            from click.testing import CliRunner

            from roam.cli import cli as _cli

            CliRunner().invoke(_cli, ["index"], catch_exceptions=True)
        except Exception as exc:  # noqa: BLE001 — best-effort reindex; stale resources are tolerable
            log_swallowed("watcher:_do_reindex", exc)

    def _dispatch_notify(self) -> None:
        if self._notify is None or self._loop is None:
            return
        try:
            for uri in _DEPENDENT_RESOURCES:
                asyncio.run_coroutine_threadsafe(self._notify(uri), self._loop)
        except Exception as exc:  # noqa: BLE001 — notification is best-effort; client refresh degrades gracefully
            log_swallowed("watcher:_dispatch_notify", exc)

    def stop(self) -> None:
        self._stopped = True
        with self._lock:
            if self._timer is not None:
                self._timer.cancel()
                self._timer = None


class _Watcher:
    """Public handle returned by :func:`start_watcher`."""

    def __init__(self) -> None:
        self.observer: Any = None
        self.reindexer: _DebouncedReindexer | None = None
        self.started_at: float = 0.0

    def stop(self) -> None:
        if self.observer is not None:
            try:
                self.observer.stop()
                self.observer.join(timeout=2.0)
            except Exception as exc:  # noqa: BLE001 — observer teardown is best-effort
                log_swallowed("watcher:stop", exc)
        if self.reindexer is not None:
            self.reindexer.stop()


def _try_import_watchdog() -> tuple[Any, Any] | None:
    try:
        from watchdog.events import FileSystemEventHandler
        from watchdog.observers import Observer

        return Observer, FileSystemEventHandler
    except ImportError:
        # Genuine optional-dependency guard: watchdog is not a hard
        # dependency. Returning None disables the watcher entirely (the
        # caller's documented fall-through). No lineage needed — absence
        # of watchdog is an expected, user-visible config state.
        return None


def _make_resources_updated_notifier(fastmcp_server: Any):
    """Return an async ``notify(uri)`` callable, or None if unsupported.

    FastMCP / mcp Python SDK have shifted notification APIs across
    versions. We try the most likely paths in order.
    """
    if fastmcp_server is None:
        return None

    low_level = getattr(fastmcp_server, "_mcp_server", None)
    if low_level is None:
        return None

    # Path 1: low_level.session.send_resource_updated(uri)
    async def _notify(uri: str) -> None:
        try:
            session = getattr(low_level, "request_context", None)
            if session is not None:
                session_obj = getattr(session, "session", None)
                if session_obj is not None and hasattr(session_obj, "send_resource_updated"):
                    await session_obj.send_resource_updated(uri)
                    return
        except Exception as exc:  # noqa: BLE001 — Path-1 probe; falls through to Path-2 below
            log_swallowed("watcher:_notify.path1", exc)
        # Path 2: server-level notification helper
        try:
            from mcp.types import ResourceUpdatedNotification, ResourceUpdatedNotificationParams

            payload = ResourceUpdatedNotification(
                method="notifications/resources/updated",
                params=ResourceUpdatedNotificationParams(uri=uri),
            )
            sender = getattr(low_level, "send_notification", None)
            if callable(sender):
                await sender(payload)
        except Exception as exc:  # noqa: BLE001 — Path-2 probe; no notification path on this build
            log_swallowed("watcher:_notify.path2", exc)

    return _notify


def start_watcher(
    fastmcp_server: Any,
    *,
    root: str | os.PathLike[str] | None = None,
    debounce_seconds: float = 1.5,
) -> _Watcher | None:
    """Start a watcher, returning a handle the caller can ``.stop()``.

    Returns ``None`` when watchdog is missing or the server doesn't
    expose a resource-updated notification path.
    """
    imports = _try_import_watchdog()
    if imports is None:
        return None
    Observer, FileSystemEventHandler = imports

    notify = _make_resources_updated_notifier(fastmcp_server)
    project_root = Path(root or os.getcwd()).resolve()
    if not project_root.exists():
        return None
    if not (project_root / ".roam").exists() and not (project_root / ".git").exists():
        return None

    reindexer = _DebouncedReindexer(root=project_root, notify=notify, debounce_seconds=debounce_seconds)

    try:
        loop = asyncio.get_running_loop()
        reindexer.bind_loop(loop)
    except RuntimeError:
        # No running loop yet; notification dispatch stays disabled.
        pass

    class _Handler(FileSystemEventHandler):
        def on_modified(self, event):  # type: ignore[override]
            if event.is_directory:
                return
            reindexer.push(event.src_path)

        def on_created(self, event):  # type: ignore[override]
            if event.is_directory:
                return
            reindexer.push(event.src_path)

        def on_deleted(self, event):  # type: ignore[override]
            if event.is_directory:
                return
            reindexer.push(event.src_path)

        def on_moved(self, event):  # type: ignore[override]
            if event.is_directory:
                return
            reindexer.push(getattr(event, "dest_path", "") or event.src_path)

    observer = Observer()
    observer.schedule(_Handler(), str(project_root), recursive=True)
    try:
        observer.start()
    except Exception:
        return None

    handle = _Watcher()
    handle.observer = observer
    handle.reindexer = reindexer
    handle.started_at = time.time()
    return handle
