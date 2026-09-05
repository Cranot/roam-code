"""Exercise the installed MCP transport, not a mocked dispatcher (#106/#107)."""

from __future__ import annotations

import json
import os
import queue
import subprocess
import sys
import threading
import time
from collections import deque

import pytest

from tests._helpers.repo_root import repo_root

pytest.importorskip("fastmcp")
pytest.importorskip("mcp")


@pytest.fixture
def wire(tmp_path):
    env = os.environ.copy()
    env["PYTHONPATH"] = str(repo_root() / "src")
    env["ROAM_MCP_PRESET"] = "core"
    env["ROAM_MCP_WATCH"] = "0"
    process = subprocess.Popen(
        [sys.executable, "-m", "roam", "mcp", "--no-auto-index"],
        cwd=tmp_path,
        env=env,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
    )
    messages = queue.Queue()
    errors = deque(maxlen=40)

    def read_stdout():
        for line in process.stdout:
            messages.put(line)

    def read_stderr():
        for line in process.stderr:
            errors.append(line)

    readers = [threading.Thread(target=read_stdout, daemon=True), threading.Thread(target=read_stderr, daemon=True)]
    for reader in readers:
        reader.start()

    def request(method, params=None, *, request_id=1):
        message = {"jsonrpc": "2.0", "method": method}
        if request_id is not None:
            message["id"] = request_id
        if params is not None:
            message["params"] = params
        process.stdin.write(json.dumps(message) + "\n")
        process.stdin.flush()
        if request_id is None:
            return None
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            try:
                line = messages.get(timeout=max(0.01, deadline - time.monotonic()))
            except queue.Empty:
                pytest.fail(f"No {method} response; stderr: {''.join(errors)}")
            response = json.loads(line)  # Any stdout banner/install noise is a failure.
            if response.get("id") == request_id:
                return response
        pytest.fail(f"No matching {method} response; stderr: {''.join(errors)}")

    try:
        yield request
    finally:
        process.stdin.close()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
        for reader in readers:
            reader.join(timeout=2)
        process.stdout.close()
        process.stderr.close()


def _params(version="2025-11-25"):
    return {"protocolVersion": version, "capabilities": {}, "clientInfo": {"name": "roam-wire-test", "version": "1"}}


def test_stdio_initializes_and_lists_core_tools(wire):
    response = wire("initialize", _params())
    assert response["result"]["protocolVersion"] == "2025-11-25"
    wire("notifications/initialized", request_id=None)
    response = wire("tools/list", {}, request_id=2)
    assert response["result"]["tools"]
    assert any(tool["name"] == "roam_ask" for tool in response["result"]["tools"])


def test_missing_initialize_version_returns_error_and_server_survives(wire):
    params = _params()
    del params["protocolVersion"]
    response = wire("initialize", params)
    assert response["error"]["code"] == -32602
    assert response["error"]["message"]
    assert "result" in wire("initialize", _params(), request_id=2)


def test_unsupported_initialize_version_negotiates_a_supported_version(wire):
    from mcp.shared.version import SUPPORTED_PROTOCOL_VERSIONS

    response = wire("initialize", _params("1900-01-01"))
    assert response["result"]["protocolVersion"] in SUPPORTED_PROTOCOL_VERSIONS
    wire("notifications/initialized", request_id=None)
    assert wire("tools/list", {}, request_id=2)["result"]["tools"]
