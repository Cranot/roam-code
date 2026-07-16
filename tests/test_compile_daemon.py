"""S2-lite `roam compile-daemon` — warm loopback compile server.

The daemon exists to remove the ~346 ms cold-spawn cost from the UPS hook
chain; the hook tries it with a 10 ms connect budget and falls back cold on
any failure. These tests drive the server class directly (threads + real
sockets) with the in-process compile stubbed — hermetic, no index needed.
"""

from __future__ import annotations

import json
import socket
import threading

import pytest
from click.testing import CliRunner

from roam.commands.cmd_compile_daemon import (
    CompileDaemonServer,
    _daemon_file,
    _read_daemon_file,
    _request,
    compile_daemon,
)

_FAKE_ENVELOPE = {"summary": {"verdict": "ok", "procedure": "synthesis_query"}, "artifact": {"plan": {}}}


@pytest.fixture
def server(tmp_path, monkeypatch):
    """A live server for tmp_path with the real compile stubbed out."""
    import roam.mcp_server as mcp

    monkeypatch.setattr(mcp, "_run_roam_inprocess", lambda args: {**_FAKE_ENVELOPE, "_args": args})
    srv = CompileDaemonServer(str(tmp_path))
    srv.write_daemon_file()
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    yield srv
    srv._shutdown = True
    try:  # unblock the accept loop if still parked on it
        socket.create_connection(("127.0.0.1", srv.port), timeout=0.2).close()
    except OSError:
        pass
    srv.close()
    t.join(timeout=2)


def _cfg(tmp_path):
    return _read_daemon_file(tmp_path)


class TestProtocol:
    def test_daemon_file_written_with_token(self, tmp_path, server):
        cfg = _cfg(tmp_path)
        assert cfg["port"] == server.port
        assert len(cfg["token"]) == 32

    def test_ping_roundtrip(self, tmp_path, server):
        pong = _request(_cfg(tmp_path), {"op": "ping"}, timeout=2.0)
        assert pong["ok"] is True and pong["root"] == server.root

    def test_compile_roundtrip_and_serial_counter(self, tmp_path, server):
        cfg = _cfg(tmp_path)
        resp = _request(cfg, {"op": "compile", "args": ["fix the login bug"], "cwd": str(tmp_path)}, timeout=2.0)
        assert resp["summary"]["verdict"] == "ok"
        assert resp["_args"] == ["compile", "fix the login bug"]
        _request(cfg, {"op": "compile", "args": ["again"], "cwd": str(tmp_path)}, timeout=2.0)
        pong = _request(cfg, {"op": "ping"}, timeout=2.0)
        assert pong["served"] == 2

    def test_bad_token_refused(self, tmp_path, server):
        resp = _request({**_cfg(tmp_path), "token": "wrong"}, {"op": "compile", "args": ["x"]}, timeout=2.0)
        assert resp == {"error": "bad_token"}

    def test_wrong_repo_refused(self, tmp_path, server):
        """A request from another repo must fall back cold, never get this
        repo's envelope."""
        other = tmp_path.parent / "other-repo"
        other.mkdir(exist_ok=True)
        resp = _request(_cfg(tmp_path), {"op": "compile", "args": ["x"], "cwd": str(other)}, timeout=2.0)
        assert resp["error"] == "wrong_repo"

    def test_subdir_of_repo_accepted(self, tmp_path, server):
        sub = tmp_path / "pkg"
        sub.mkdir()
        resp = _request(_cfg(tmp_path), {"op": "compile", "args": ["x"], "cwd": str(sub)}, timeout=2.0)
        assert resp["summary"]["verdict"] == "ok"

    def test_bad_args_refused(self, tmp_path, server):
        cfg = _cfg(tmp_path)
        assert _request(cfg, {"op": "compile", "args": "not-a-list", "cwd": str(tmp_path)}, timeout=2.0) == {
            "error": "bad_args"
        }
        assert _request(cfg, {"op": "nonsense"}, timeout=2.0) == {"error": "unknown_op"}

    def test_garbage_request_never_kills_daemon(self, tmp_path, server):
        with socket.create_connection(("127.0.0.1", server.port), timeout=2.0) as s:
            s.sendall(b"NOT JSON AT ALL\n")
            s.shutdown(socket.SHUT_WR)
            raw = s.recv(65536)
        assert json.loads(raw.decode()) == {"error": "bad_request"}
        assert _request(_cfg(tmp_path), {"op": "ping"}, timeout=2.0)["ok"] is True  # still alive

    def test_shutdown_op_stops_serving(self, tmp_path, server):
        resp = _request(_cfg(tmp_path), {"op": "shutdown"}, timeout=2.0)
        assert resp["stopping"] is True

    def test_episode_identity_stamped_for_telemetry(self, tmp_path, monkeypatch, server):
        """The daemon preserves the cold path's complete episode identity."""
        import os

        import roam.mcp_server as mcp

        seen = {}

        def spy(args):
            seen["session"] = os.environ.get("ROAM_SESSION_ID")
            seen["episode"] = os.environ.get("ROAM_EPISODE_ID")
            seen["turn_seq"] = os.environ.get("ROAM_TURN_SEQ")
            seen["mode"] = os.environ.get("ROAM_AGENT_MODE")
            return _FAKE_ENVELOPE

        monkeypatch.setattr(mcp, "_run_roam_inprocess", spy)
        monkeypatch.delenv("ROAM_SESSION_ID", raising=False)
        monkeypatch.delenv("ROAM_EPISODE_ID", raising=False)
        monkeypatch.delenv("ROAM_TURN_SEQ", raising=False)
        _request(
            _cfg(tmp_path),
            {
                "op": "compile",
                "args": ["x"],
                "cwd": str(tmp_path),
                "session_id": "sess-42",
                "episode_id": "ep-42",
                "turn_seq": "7",
            },
            timeout=2.0,
        )
        assert seen == {"session": "sess-42", "episode": "ep-42", "turn_seq": "7", "mode": "hook"}
        assert os.environ.get("ROAM_SESSION_ID") is None  # restored after
        assert os.environ.get("ROAM_EPISODE_ID") is None
        assert os.environ.get("ROAM_TURN_SEQ") is None


class TestCliLifecycle:
    def _invoke(self, *args, cwd=None):
        return CliRunner().invoke(compile_daemon, list(args), obj={"json": False})

    def test_status_absent(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        res = self._invoke("status")
        assert res.exit_code == 1
        assert "no compile daemon" in res.output

    def test_status_alive_and_stop(self, tmp_path, monkeypatch, server):
        monkeypatch.chdir(tmp_path)
        res = self._invoke("status")
        assert res.exit_code == 0, res.output
        assert "alive" in res.output
        res = self._invoke("stop")
        assert "stopped daemon" in res.output

    def test_stop_cleans_stale_file(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        stale = _daemon_file(tmp_path)
        stale.parent.mkdir(parents=True, exist_ok=True)
        stale.write_text(json.dumps({"port": 1, "token": "x", "pid": 0}), encoding="utf-8")
        res = self._invoke("stop")
        assert "removed stale daemon file" in res.output
        assert not stale.exists()

    def test_status_stale_exit_1(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        stale = _daemon_file(tmp_path)
        stale.parent.mkdir(parents=True, exist_ok=True)
        stale.write_text(json.dumps({"port": 1, "token": "x", "pid": 12345}), encoding="utf-8")
        res = self._invoke("status")
        assert res.exit_code == 1
        assert "stale" in res.output

    def test_start_refuses_double_start(self, tmp_path, monkeypatch, server):
        monkeypatch.chdir(tmp_path)
        res = self._invoke("start")
        assert res.exit_code != 0
        assert "already running" in res.output
