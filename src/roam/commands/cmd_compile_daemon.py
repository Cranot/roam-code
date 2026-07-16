"""`roam compile-daemon start|status|stop` — S2-lite warm compile server.

The interactive cost of the compile channel is process STARTUP, not compute:
on the measured Windows box the UPS hook chain (python3 hook -> cold
`roam --json compile`) costs ~346 ms median while the envelope compute on a
cache HIT is ~2 ms — startup is ~49% of total compile wall across the whole
workload. The daemon removes the cold spawn: it keeps roam imported (and the
index/cache connections warm) inside one long-lived process and serves
compile requests over a loopback socket. The UPS hook tries the socket with
a ~10 ms connect budget and falls back to the cold spawn on ANY failure —
the daemon can never break the hook (fail-open, same contract as everything
else on this path).

Scope (deliberately S2-LITE):
  - per-repo: one daemon serves exactly the repo it was started in; a
    request from another cwd is refused and the hook falls back cold.
  - manual lifecycle: `start` runs in the foreground (background it with
    your shell / a service manager); `stop` asks it to exit; `status` pings.
  - loopback + token: binds 127.0.0.1 only; every request must carry the
    random token from `.roam/compile-daemon.json` (written 0600 best-effort).

SARIF is deliberately NOT emitted: output is daemon lifecycle status, not
file-located findings.
"""

from __future__ import annotations

import json
import os
import secrets
import socket
import time
from pathlib import Path

import click

from roam.capability import roam_capability
from roam.output.formatter import json_envelope, to_json

_DAEMON_FILENAME = "compile-daemon.json"
_RECV_LIMIT = 1_048_576  # 1 MiB request cap: a prompt, not a payload channel
_CLIENT_TIMEOUT_S = 10.0  # per-connection read/write budget inside the server


def _daemon_file(root: str | Path) -> Path:
    return Path(root) / ".roam" / _DAEMON_FILENAME


def _read_daemon_file(root: str | Path) -> dict | None:
    try:
        return json.loads(_daemon_file(root).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _request(cfg: dict, payload: dict, timeout: float = 5.0) -> dict | None:
    """One request/response round-trip against a running daemon. None on any failure."""
    try:
        with socket.create_connection(("127.0.0.1", int(cfg["port"])), timeout=timeout) as s:
            s.settimeout(timeout)
            s.sendall((json.dumps({"token": cfg.get("token"), **payload}) + "\n").encode("utf-8"))
            s.shutdown(socket.SHUT_WR)
            chunks: list[bytes] = []
            while True:
                b = s.recv(65536)
                if not b:
                    break
                chunks.append(b)
        return json.loads(b"".join(chunks).decode("utf-8"))
    except (OSError, ValueError):
        return None


class CompileDaemonServer:
    """Loopback JSON-line server holding roam warm for one repo.

    Factored as a class so tests can drive `serve_once()` deterministically
    in a thread instead of a blocking CLI process.
    """

    def __init__(self, root: str, port: int = 0):
        self.root = os.path.realpath(root)
        self.token = secrets.token_hex(16)
        self.started = time.time()
        self.served = 0
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind(("127.0.0.1", port))
        self.sock.listen(8)
        self.port = self.sock.getsockname()[1]
        self._shutdown = False

    # -- lifecycle ---------------------------------------------------------
    def write_daemon_file(self) -> Path:
        p = _daemon_file(self.root)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(
            json.dumps({"port": self.port, "token": self.token, "pid": os.getpid(), "root": self.root}) + "\n",
            encoding="utf-8",
        )
        try:
            os.chmod(p, 0o600)  # token file: owner-only where the OS honors it
        except OSError:
            pass
        return p

    def close(self) -> None:
        try:
            self.sock.close()
        finally:
            try:
                _daemon_file(self.root).unlink()
            except OSError:
                pass

    def prewarm(self) -> None:
        """Front-load the ~hundreds-of-ms import cost at start, not first request."""
        from roam.cli import cli  # noqa: F401

    # -- serving -----------------------------------------------------------
    def serve_forever(self) -> None:
        while not self._shutdown:
            self.serve_once()

    def serve_once(self) -> None:
        try:
            conn, _addr = self.sock.accept()
        except OSError:
            self._shutdown = True
            return
        with conn:
            try:
                conn.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
                conn.settimeout(_CLIENT_TIMEOUT_S)
                req = self._read_request(conn)
                resp = self.handle(req) if req is not None else {"error": "bad_request"}
                conn.sendall((json.dumps(resp, ensure_ascii=False) + "\n").encode("utf-8"))
            except (OSError, ValueError):
                return  # a broken client never kills the daemon

    def _read_request(self, conn: socket.socket) -> dict | None:
        chunks: list[bytes] = []
        total = 0
        while total < _RECV_LIMIT:
            b = conn.recv(65536)
            if not b:
                break
            chunks.append(b)
            total += len(b)
            if b"\n" in b:
                break
        try:
            return json.loads(b"".join(chunks).decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            return None

    def handle(self, req: dict) -> dict:
        if not isinstance(req, dict) or req.get("token") != self.token:
            return {"error": "bad_token"}
        op = req.get("op")
        if op == "ping":
            return {
                "ok": True,
                "pid": os.getpid(),
                "root": self.root,
                "uptime_s": round(time.time() - self.started, 1),
                "served": self.served,
            }
        if op == "shutdown":
            self._shutdown = True
            return {"ok": True, "stopping": True}
        if op == "compile":
            cwd = os.path.realpath(str(req.get("cwd") or ""))
            if cwd != self.root and not cwd.startswith(self.root + os.sep):
                # per-repo daemon: a foreign-repo request must fall back cold,
                # never get another repo's envelope.
                return {"error": "wrong_repo", "root": self.root}
            args = req.get("args")
            if not isinstance(args, list) or not all(isinstance(a, str) for a in args) or not args:
                return {"error": "bad_args"}
            return self._compile(args, str(req.get("session_id") or ""))
        return {"error": "unknown_op"}

    def _compile(self, args: list[str], session_id: str) -> dict:
        """In-process compile with the caller's telemetry identity stamped.

        The cold path stamps ROAM_SESSION_ID / ROAM_AGENT_MODE on the child
        env; here the 'child' is us, so stamp os.environ around the invoke
        and restore. Requests are handled serially — no concurrent mutation.
        """
        from roam.mcp_server import _run_roam_inprocess

        saved = {k: os.environ.get(k) for k in ("ROAM_SESSION_ID", "ROAM_AGENT_MODE")}
        try:
            if session_id:
                os.environ["ROAM_SESSION_ID"] = session_id
            os.environ.setdefault("ROAM_AGENT_MODE", "hook")
            envelope = _run_roam_inprocess(["compile", *args])
        finally:
            for k, v in saved.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v
        self.served += 1
        return envelope


@click.group(name="compile-daemon")
@roam_capability(
    name="compile-daemon",
    category="planning",
    summary="S2-lite warm compile server: serve `roam compile` over a loopback socket, skipping process startup.",
    inputs=("subcommand",),
    outputs=("summary_envelope",),
    examples=(
        "roam compile-daemon start",
        "roam compile-daemon status",
        "roam compile-daemon stop",
    ),
    tags=("planning", "compiler", "daemon", "performance"),
)
def compile_daemon() -> None:
    """S2-lite warm compile server (per-repo, manual lifecycle, fail-open)."""


@compile_daemon.command("start")
@click.option("--port", type=int, default=0, help="Loopback port (default 0 = ephemeral).")
@click.pass_context
def start(ctx, port):
    """Run the warm compile server for THIS repo in the foreground.

    Writes `.roam/compile-daemon.json` (port + token + pid); removes it on
    clean exit. The UPS hook auto-discovers it there and falls back to the
    cold spawn whenever the daemon is absent, busy, or wrong — so starting
    and stopping this is always safe.
    """
    json_mode = ctx.obj.get("json") if ctx.obj else False
    existing = _read_daemon_file(os.getcwd())
    if existing and _request(existing, {"op": "ping"}, timeout=0.25):
        raise click.UsageError(f"a compile daemon is already running for this repo (pid {existing.get('pid')})")
    try:
        server = CompileDaemonServer(os.getcwd(), port=port)
    except OSError as exc:
        raise click.UsageError(f"cannot bind 127.0.0.1:{port}: {exc}") from exc
    server.write_daemon_file()
    verdict = f"compile daemon serving {server.root} on 127.0.0.1:{server.port} (pid {os.getpid()})"
    if json_mode:
        click.echo(to_json(json_envelope("compile-daemon", summary={"verdict": verdict, "port": server.port})))
    else:
        click.echo(f"VERDICT: {verdict}")
        click.echo("  stop with: roam compile-daemon stop  (or Ctrl+C)")
    server.prewarm()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.close()


@compile_daemon.command("status")
@click.pass_context
def status(ctx):
    """Ping the repo's daemon: alive, stale token file, or not running."""
    json_mode = ctx.obj.get("json") if ctx.obj else False
    cfg = _read_daemon_file(os.getcwd())
    if not cfg:
        verdict, state = "no compile daemon for this repo", "absent"
    else:
        pong = _request(cfg, {"op": "ping"}, timeout=1.0)
        if pong and pong.get("ok"):
            verdict = (
                f"alive: pid {pong.get('pid')} port {cfg.get('port')} "
                f"uptime {pong.get('uptime_s')}s served {pong.get('served')}"
            )
            state = "alive"
        else:
            verdict, state = (
                f"stale daemon file (pid {cfg.get('pid')} not answering) — restart or delete .roam/{_DAEMON_FILENAME}",
                "stale",
            )
    if json_mode:
        click.echo(to_json(json_envelope("compile-daemon", summary={"verdict": verdict, "state": state})))
        ctx.exit(0 if state == "alive" else 1)
    click.echo(f"VERDICT: {verdict}")
    ctx.exit(0 if state == "alive" else 1)


@compile_daemon.command("stop")
@click.pass_context
def stop(ctx):
    """Ask the repo's daemon to exit; clean up a stale daemon file."""
    json_mode = ctx.obj.get("json") if ctx.obj else False
    cfg = _read_daemon_file(os.getcwd())
    if not cfg:
        verdict, stopped = "no compile daemon for this repo", False
    else:
        resp = _request(cfg, {"op": "shutdown"}, timeout=2.0)
        if resp and resp.get("ok"):
            verdict, stopped = f"stopped daemon pid {cfg.get('pid')}", True
        else:
            try:
                _daemon_file(os.getcwd()).unlink()
            except OSError:
                pass
            verdict, stopped = "daemon not answering; removed stale daemon file", False
    if json_mode:
        click.echo(to_json(json_envelope("compile-daemon", summary={"verdict": verdict, "stopped": stopped})))
        return
    click.echo(f"VERDICT: {verdict}")
