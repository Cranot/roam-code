"""Watch mode: poll for file changes and auto-re-index."""

from __future__ import annotations

import json
import queue
import threading
import time
from datetime import datetime, timezone
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import click

from roam.db.connection import open_db, find_project_root, db_exists
from roam.commands.resolve import ensure_index


def load_tracked_files(project_root: Path) -> dict[str, float]:
    """Return {relative_path: mtime} for all files currently in the DB.

    Returns an empty dict when no index exists.
    """
    if not db_exists(project_root):
        return {}
    try:
        with open_db(readonly=True, project_root=project_root) as conn:
            rows = conn.execute("SELECT path, mtime FROM files").fetchall()
        return {row[0]: (row[1] or 0.0) for row in rows}
    except Exception:
        return {}


def scan_disk_mtimes(
    file_paths: list[str],
    project_root: Path,
) -> dict[str, float]:
    """Return {relative_path: mtime} for paths that exist on disk.

    Missing files are omitted from the result.
    """
    result: dict[str, float] = {}
    for rel in file_paths:
        try:
            result[rel] = (project_root / rel).stat().st_mtime
        except OSError:
            pass
    return result


def detect_changes(
    tracked: dict[str, float],
    current_disk: dict[str, float],
) -> tuple[list[str], list[str], list[str]]:
    """Compare tracked DB state with current disk state.

    Args:
        tracked:      {path: stored_mtime}  -- from the index DB.
        current_disk: {path: disk_mtime}    -- from the filesystem right now.

    Returns:
        (added, modified, removed) -- three lists of relative paths.
        added    = paths on disk but not in index.
        modified = paths in both but mtime differs by > 0.001 s.
        removed  = paths in index but no longer on disk.
    """
    tracked_set = set(tracked)
    disk_set = set(current_disk)

    added = sorted(disk_set - tracked_set)
    removed = sorted(tracked_set - disk_set)
    modified = sorted(
        p for p in tracked_set & disk_set
        if abs(current_disk[p] - tracked[p]) > 0.001
    )
    return added, modified, removed


def discover_current_files(project_root: Path) -> list[str]:
    """Discover source files on disk via the same logic as the indexer.

    Falls back to an empty list on any error so the watcher degrades
    gracefully when called before the project is fully initialised.
    """
    try:
        from roam.index.discovery import discover_files
        return discover_files(project_root)
    except Exception:
        return []


def run_incremental_index(project_root: Path, quiet: bool, force: bool = False) -> None:
    """Trigger an index refresh.

    Args:
        project_root: repository root.
        quiet: suppress index progress output.
        force: run a full rebuild when True.
    """
    from roam.index.indexer import Indexer
    indexer = Indexer(project_root=project_root)
    indexer.run(force=force, quiet=quiet)


def _guardian_drift_summary(
    conn,
    project_root: Path,
    threshold: float = 0.5,
    max_files: int = 250,
) -> dict:
    """Return compact ownership-drift summary for architecture guardian mode."""
    from roam.commands.cmd_codeowners import find_codeowners, parse_codeowners, resolve_owners
    from roam.commands.cmd_drift import compute_file_ownership, compute_drift_score

    co_path = find_codeowners(project_root)
    if co_path is None:
        return {
            "codeowners_found": False,
            "owned_files": 0,
            "drift_files": 0,
            "drift_pct": 0.0,
            "threshold": threshold,
        }

    rules = parse_codeowners(co_path)
    file_rows = conn.execute(
        """
        SELECT f.id, f.path
        FROM files f
        LEFT JOIN file_stats fs ON fs.file_id = f.id
        ORDER BY COALESCE(fs.total_churn, 0) DESC, f.path
        LIMIT ?
        """,
        (max_files,),
    ).fetchall()
    if not file_rows:
        return {
            "codeowners_found": True,
            "owned_files": 0,
            "drift_files": 0,
            "drift_pct": 0.0,
            "threshold": threshold,
        }

    now_ts = int(time.time())
    owned = 0
    drift = 0
    for row in file_rows:
        path = str(row["path"]).replace("\\", "/")
        owners = resolve_owners(rules, path)
        if not owners:
            continue
        owned += 1
        shares = compute_file_ownership(conn, int(row["id"]), now_ts=now_ts)
        if not shares:
            continue
        score = compute_drift_score(owners, shares)
        if score >= threshold:
            drift += 1

    pct = round((drift * 100.0 / owned), 1) if owned else 0.0
    return {
        "codeowners_found": True,
        "owned_files": owned,
        "drift_files": drift,
        "drift_pct": pct,
        "threshold": threshold,
    }


def collect_guardian_snapshot(
    project_root: Path,
    *,
    health_gate: float = 70.0,
    drift_threshold: float = 0.5,
) -> dict:
    """Collect a continuous architecture-guardian snapshot."""
    from roam.commands.metrics_history import append_snapshot, get_snapshots
    from roam.commands.cmd_trend import _analyze_trends, _trend_verdict

    with open_db(readonly=False, project_root=project_root) as conn:
        current = append_snapshot(conn, source="watch-guardian")
        snaps = get_snapshots(conn, limit=30)
        drift = _guardian_drift_summary(
            conn,
            project_root=project_root,
            threshold=drift_threshold,
        )

    chrono = []
    for snap in reversed(snaps):
        chrono.append({
            "files": snap["files"],
            "symbols": snap["symbols"],
            "edges": snap["edges"],
            "cycles": snap["cycles"],
            "god_components": snap["god_components"],
            "bottlenecks": snap["bottlenecks"],
            "dead_exports": snap["dead_exports"],
            "layer_violations": snap["layer_violations"],
            "health_score": snap["health_score"],
        })

    trend = {
        "verdict": "insufficient-data",
        "anomaly_count": 0,
        "significant_trends": 0,
    }
    if len(chrono) >= 4:
        analysis = _analyze_trends(chrono, sensitivity="medium")
        trend = {
            "verdict": _trend_verdict(analysis),
            "anomaly_count": len(analysis.get("anomalies", [])),
            "significant_trends": len(
                [
                    t for t in analysis.get("trends", [])
                    if t.get("direction") != "stable"
                ]
            ),
        }

    health_score = int(current.get("health_score", 0) or 0)
    gates = {
        "health_gate": health_gate,
        "health_gate_pass": health_score >= health_gate,
    }
    return {
        "timestamp": (
            datetime.now(timezone.utc)
            .replace(microsecond=0)
            .isoformat()
            .replace("+00:00", "Z")
        ),
        "source": "watch-guardian",
        "current": {
            "health_score": health_score,
            "cycles": int(current.get("cycles", 0) or 0),
            "layer_violations": int(current.get("layer_violations", 0) or 0),
            "dead_exports": int(current.get("dead_exports", 0) or 0),
            "tangle_ratio": float(current.get("tangle_ratio", 0.0) or 0.0),
            "avg_complexity": float(current.get("avg_complexity", 0.0) or 0.0),
            "brain_methods": int(current.get("brain_methods", 0) or 0),
        },
        "trend": trend,
        "drift": drift,
        "gates": gates,
    }


def append_guardian_report(report_path: Path, payload: dict) -> None:
    """Append guardian snapshots as JSONL for compliance/report pipelines."""
    report_path.parent.mkdir(parents=True, exist_ok=True)
    with report_path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, sort_keys=True, ensure_ascii=True) + "\n")


class WebhookBridge:
    """Tiny local HTTP bridge that queues index-refresh triggers.

    Exposes:
      POST {path}  -> enqueue trigger event
      GET  /health -> daemon/webhook status
    """

    def __init__(
        self,
        host: str,
        port: int,
        path: str = "/roam/reindex",
        secret: str = "",
    ) -> None:
        self.host = host
        self.port = port
        self.path = path if path.startswith("/") else f"/{path}"
        self.secret = secret.strip()
        self._events: queue.Queue[dict] = queue.Queue()
        self._lock = threading.Lock()
        self._accepted = 0
        self._started_at = time.time()
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

    def _auth_ok(self, headers: dict[str, str]) -> bool:
        """Validate webhook secret (if configured)."""
        if not self.secret:
            return True
        token = headers.get("x-roam-secret", "").strip()
        if token == self.secret:
            return True
        bearer = headers.get("authorization", "").strip()
        if bearer.lower().startswith("bearer "):
            return bearer[7:].strip() == self.secret
        return False

    def _enqueue(self, event: str, force: bool = False) -> None:
        with self._lock:
            self._accepted += 1
        self._events.put({
            "event": event or "webhook",
            "force": bool(force),
            "received_at": time.time(),
        })

    def drain_events(self) -> list[dict]:
        """Drain queued trigger events."""
        out: list[dict] = []
        while True:
            try:
                out.append(self._events.get_nowait())
            except queue.Empty:
                break
        return out

    def stats(self) -> dict:
        with self._lock:
            accepted = self._accepted
        return {
            "host": self.host,
            "port": self.port,
            "path": self.path,
            "queued_events": self._events.qsize(),
            "accepted_events": accepted,
            "uptime_s": round(max(0.0, time.time() - self._started_at), 3),
            "secret_required": bool(self.secret),
        }

    def _build_handler(self):
        bridge = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, fmt, *args):  # noqa: D401, ANN001
                # Keep daemon mode quiet unless the user asked for verbose logs.
                return

            def _json(self, status: int, payload: dict) -> None:
                body = json.dumps(payload, sort_keys=True).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):  # noqa: N802
                parsed = urllib.parse.urlparse(self.path)
                if parsed.path == "/health":
                    payload = {"ok": True, **bridge.stats()}
                    self._json(200, payload)
                    return
                self._json(404, {"ok": False, "error": "not found"})

            def do_POST(self):  # noqa: N802
                parsed = urllib.parse.urlparse(self.path)
                if parsed.path != bridge.path:
                    self._json(404, {"ok": False, "error": "not found"})
                    return

                headers = {k.lower(): v for k, v in self.headers.items()}
                if not bridge._auth_ok(headers):
                    self._json(401, {"ok": False, "error": "unauthorized"})
                    return

                length = 0
                try:
                    length = int(self.headers.get("Content-Length", "0"))
                except Exception:
                    length = 0
                raw = self.rfile.read(max(0, min(length, 1_000_000))) if length > 0 else b""

                payload: dict = {}
                if raw:
                    try:
                        decoded = json.loads(raw.decode("utf-8"))
                        if isinstance(decoded, dict):
                            payload = decoded
                    except Exception:
                        payload = {}

                event = (
                    headers.get("x-roam-event")
                    or headers.get("x-github-event")
                    or str(payload.get("event", "")).strip()
                    or "webhook"
                )
                force = bool(payload.get("force", False))
                bridge._enqueue(event=event, force=force)
                self._json(
                    202,
                    {
                        "ok": True,
                        "accepted": True,
                        "event": event,
                        "force": force,
                        "queued_events": bridge._events.qsize(),
                    },
                )

        return Handler

    def start(self) -> None:
        """Start the local webhook HTTP server in a background thread."""
        if self._server is not None:
            return
        handler = self._build_handler()
        self._server = ThreadingHTTPServer((self.host, self.port), handler)
        self.port = self._server.server_address[1]
        self._thread = threading.Thread(
            target=self._server.serve_forever,
            name="roam-webhook-bridge",
            daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        """Stop the background webhook server."""
        if self._server is None:
            return
        try:
            self._server.shutdown()
            self._server.server_close()
        finally:
            self._server = None
        if self._thread is not None:
            self._thread.join(timeout=2.0)
            self._thread = None


class DebounceAccumulator:
    """Accumulate file-change events and fire after a quiet period.

    Call add() with changed paths each poll cycle, then check
    should_fire() to know when the debounce window has elapsed.
    """

    def __init__(self, window: float = 1.0) -> None:
        self.window = window
        self._pending: set[str] = set()
        self._last_change: float | None = None

    def add(self, paths: list[str]) -> None:
        """Record newly changed paths and reset the quiet-period timer."""
        if paths:
            self._pending.update(paths)
            self._last_change = time.monotonic()

    def has_pending(self) -> bool:
        """Return True if there are accumulated changes waiting to fire."""
        return bool(self._pending)

    def should_fire(self, now: float) -> bool:
        """Return True when the quiet window has elapsed since the last change."""
        if not self._pending or self._last_change is None:
            return False
        return (now - self._last_change) >= self.window

    def flush(self) -> list[str]:
        """Return accumulated paths and reset internal state."""
        result = sorted(self._pending)
        self._pending.clear()
        self._last_change = None
        return result


def poll_loop(
    project_root: Path,
    interval: float,
    debounce: float,
    quiet: bool,
    webhook_only: bool = False,
    webhook_force: bool = False,
    guardian: bool = False,
    guardian_report: str = "",
    guardian_health_gate: float = 70.0,
    guardian_drift_threshold: float = 0.5,
    *,
    _sleep=time.sleep,
    _discover=None,
    _reindex=None,
    _external_events=None,
    _guardian_collect=None,
    _guardian_write=None,
) -> None:
    """Run the watch loop until interrupted.

    Args:
        project_root: Root directory to watch.
        interval:     Poll interval in seconds.
        debounce:     Quiet-period window in seconds before triggering re-index.
        quiet:        Suppress per-file change messages when True.
        webhook_only: When True, skip file polling and only process webhook triggers.
        webhook_force: Force full rebuild for webhook-triggered refreshes.
        guardian:     Enable continuous architecture guardian snapshots.
        guardian_report:
                      Optional JSONL path for compliance-ready guardian artifacts.
        guardian_health_gate:
                      Health-score gate threshold tracked in guardian payload.
        guardian_drift_threshold:
                      Drift threshold used in guardian ownership summary.
        _sleep:       Injectable sleep function (for testing).
        _discover:    Injectable file-discovery callable (for testing).
        _reindex:     Injectable re-index callable (for testing).
        _external_events:
                      Injectable callable returning queued webhook events.
        _guardian_collect:
                      Injectable guardian collector callable (for testing).
        _guardian_write:
                      Injectable guardian writer callable (for testing).
    """
    if _discover is None:
        _discover = lambda: discover_current_files(project_root)
    if _reindex is None:
        _reindex = lambda force=False: run_incremental_index(project_root, quiet=True, force=force)
    if _external_events is None:
        _external_events = lambda: []
    if _guardian_collect is None:
        _guardian_collect = lambda: collect_guardian_snapshot(
            project_root=project_root,
            health_gate=guardian_health_gate,
            drift_threshold=guardian_drift_threshold,
        )
    if _guardian_write is None:
        _guardian_write = (
            lambda payload: append_guardian_report(Path(guardian_report), payload)
            if guardian_report
            else None
        )

    acc = DebounceAccumulator(window=debounce)

    # Seed initial state from what is already tracked in the DB
    tracked = load_tracked_files(project_root)

    file_count = len(tracked)
    mode = "webhook-only" if webhook_only else "poll+webhook"
    click.echo(f"Watching {file_count} files... (interval={interval}s, debounce={debounce}s, mode={mode})")
    click.echo("Press Ctrl+C to stop.")
    pending_force = False

    while True:
        _sleep(interval)

        changed: list[str] = []
        webhook_events = _external_events() or []
        force_needed = any(bool(evt.get("force")) for evt in webhook_events if isinstance(evt, dict))
        force_needed = force_needed or (webhook_force and bool(webhook_events))
        pending_force = pending_force or force_needed

        if not webhook_only:
            # Discover current files on disk
            current_paths = _discover()
            current_disk = scan_disk_mtimes(current_paths, project_root)

            added, modified, removed = detect_changes(tracked, current_disk)
            changed.extend(added + modified + removed)

            if changed and not quiet:
                for path in added:
                    click.echo(f"  + {path}")
                for path in modified:
                    click.echo(f"  ~ {path}")
                for path in removed:
                    click.echo(f"  - {path}")

        if webhook_events:
            webhook_labels = [
                f"<webhook:{str(evt.get('event', 'webhook')).strip() or 'webhook'}>"
                for evt in webhook_events
                if isinstance(evt, dict)
            ]
            changed.extend(webhook_labels)
            if not quiet:
                for label in webhook_labels:
                    click.echo(f"  * {label}")

        if changed:
            acc.add(changed)

        now = time.monotonic()
        if acc.should_fire(now):
            batch = acc.flush()
            if not quiet:
                mode_label = "force re-indexing" if pending_force else "re-indexing"
                click.echo(f"Changed: {len(batch)} event(s) -- {mode_label}...")
            _reindex(force=pending_force)
            pending_force = False
            # Refresh tracked state from DB after re-index
            tracked = load_tracked_files(project_root)
            new_count = len(tracked)
            if not quiet:
                click.echo(f"Re-index complete. Watching {new_count} files.")
            elif new_count != file_count:
                click.echo(f"Re-indexed. Watching {new_count} files.")
            file_count = new_count

            if guardian or guardian_report:
                try:
                    guard_payload = _guardian_collect()
                    if guardian_report:
                        _guardian_write(guard_payload)
                    if not quiet:
                        gate_state = (
                            "PASS"
                            if guard_payload.get("gates", {}).get("health_gate_pass")
                            else "FAIL"
                        )
                        click.echo(
                            "Guardian: health={} trend={} drift={} ({})".format(
                                guard_payload.get("current", {}).get("health_score", "n/a"),
                                guard_payload.get("trend", {}).get("verdict", "n/a"),
                                guard_payload.get("drift", {}).get("drift_files", "n/a"),
                                gate_state,
                            )
                        )
                except Exception as exc:
                    if not quiet:
                        click.echo(f"Guardian update failed: {exc}")


@click.command("watch")
@click.option(
    "--interval", "-i",
    default=2.0,
    show_default=True,
    type=float,
    help="Poll interval in seconds.",
)
@click.option(
    "--debounce", "-d",
    default=1.0,
    show_default=True,
    type=float,
    help="Quiet-period window before triggering re-index (seconds).",
)
@click.option(
    "--quiet", "-q",
    is_flag=True,
    help="Suppress per-file change messages.",
)
@click.option(
    "--webhook-port",
    default=0,
    show_default=True,
    type=int,
    help="Enable webhook bridge HTTP listener on this port (0=disabled).",
)
@click.option(
    "--webhook-host",
    default="127.0.0.1",
    show_default=True,
    help="Webhook bridge bind host.",
)
@click.option(
    "--webhook-path",
    default="/roam/reindex",
    show_default=True,
    help="Webhook POST path used to trigger re-index.",
)
@click.option(
    "--webhook-secret",
    default="",
    help="Optional shared secret (X-Roam-Secret or Bearer token).",
)
@click.option(
    "--webhook-only",
    is_flag=True,
    help="Disable file polling and only react to webhook triggers.",
)
@click.option(
    "--webhook-force",
    is_flag=True,
    help="Force full reindex for webhook-triggered events.",
)
@click.option(
    "--guardian",
    is_flag=True,
    help="Enable continuous architecture guardian snapshots after each re-index.",
)
@click.option(
    "--guardian-report",
    default="",
    help="Optional JSONL file path to append guardian compliance snapshots.",
)
@click.option(
    "--guardian-health-gate",
    default=70.0,
    show_default=True,
    type=float,
    help="Guardian health score gate threshold (tracked in snapshots).",
)
@click.option(
    "--guardian-drift-threshold",
    default=0.5,
    show_default=True,
    type=float,
    help="Ownership drift threshold used in guardian summaries.",
)
@click.pass_context
def watch(
    ctx,
    interval,
    debounce,
    quiet,
    webhook_port,
    webhook_host,
    webhook_path,
    webhook_secret,
    webhook_only,
    webhook_force,
    guardian,
    guardian_report,
    guardian_health_gate,
    guardian_drift_threshold,
):
    """Watch for file changes and auto-re-index incrementally.

    Uses polling (no external dependencies). Debounces rapid bursts of
    changes and only re-indexes what actually changed.
    Optional webhook bridge mode allows CI/webhook systems to trigger
    refreshes without shelling into the process.

    Press Ctrl+C to stop.
    """
    project_root = find_project_root()
    bridge: WebhookBridge | None = None

    # Ensure an index exists before we start watching.
    # In webhook-only mode we still ensure an index exists so triggers
    # can refresh an already valid baseline.
    ensure_index(quiet=quiet)

    if webhook_port:
        bridge = WebhookBridge(
            host=webhook_host,
            port=webhook_port,
            path=webhook_path,
            secret=webhook_secret,
        )
        bridge.start()
        if not quiet:
            click.echo(
                f"Webhook bridge listening on http://{webhook_host}:{bridge.port}{bridge.path}"
            )
            if webhook_secret:
                click.echo("Webhook auth: secret required via X-Roam-Secret or Bearer token.")
    if (guardian or guardian_report) and not quiet:
        click.echo(
            "Architecture guardian enabled "
            f"(health_gate={guardian_health_gate}, drift_threshold={guardian_drift_threshold})"
        )
        if guardian_report:
            click.echo(f"Guardian report: {guardian_report}")

    try:
        poll_loop(
            project_root=project_root,
            interval=interval,
            debounce=debounce,
            quiet=quiet,
            webhook_only=webhook_only,
            webhook_force=webhook_force,
            guardian=guardian,
            guardian_report=guardian_report,
            guardian_health_gate=guardian_health_gate,
            guardian_drift_threshold=guardian_drift_threshold,
            _external_events=(bridge.drain_events if bridge else None),
        )
    except KeyboardInterrupt:
        click.echo("\nWatch stopped.")
    finally:
        if bridge is not None:
            bridge.stop()
