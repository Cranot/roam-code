"""Response-storage failure must not masquerade as successful delivery."""

from __future__ import annotations

import errno
import json

import pytest

pytest.importorskip("fastmcp")

from roam import mcp_server as server


@pytest.fixture(autouse=True)
def isolated_error_storm(monkeypatch):
    monkeypatch.setattr(server, "_ERROR_STORM_STATE", {"_last_code": 0, "_count": 0})
    monkeypatch.setattr(server, "_first_error_message", {})


@pytest.mark.parametrize("failure", ["directory", "write", "root"])
@pytest.mark.parametrize("partial", [False, True])
def test_storage_failure_returns_bounded_honest_result(tmp_path, monkeypatch, failure, partial):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("ROAM_MCP_HANDLE_KB", "1")
    payload = {"summary": {"verdict": "observed", "partial_success": partial}, "items": ["x" * 1000] * 100}
    if failure == "directory":
        (tmp_path / ".roam").mkdir()
        (tmp_path / ".roam" / "responses").write_text("existing user file", encoding="utf-8")
    else:

        def fail(*args, **kwargs):
            raise OSError(errno.ENOSPC, "controlled storage failure")

        if failure == "write":
            monkeypatch.setattr("roam.response_store.store_response_text", fail)
        else:
            monkeypatch.setattr(server, "_handle_storage_dir", fail)

    result = server._maybe_handle_off(payload, tool_name="roam_audit")
    assert result["isError"] is True
    assert result["error_code"] == "COMMAND_FAILED"
    assert result["status"] == "partial_failure"
    assert result["summary"]["partial_success"] is True
    assert result["summary"]["state"] == "response_storage_unavailable"
    assert result["retryable"] is False
    assert "handle" not in result and "fetch_with" not in result
    assert len(json.dumps(result).encode()) < 8192
    assert result["preview"]["summary"]["partial_success"] is partial
    assert len(payload["items"]) == 100
    if failure == "directory":
        assert (tmp_path / ".roam" / "responses").read_text(encoding="utf-8") == "existing user file"


def test_repeated_delivery_failures_retain_recovery_semantics(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("ROAM_MCP_HANDLE_KB", "1")
    monkeypatch.setattr(server, "_persist_handle_blob", lambda *args: None)
    payload = {"summary": {"verdict": "operation observed", "partial_success": False}, "data": "x" * 10000}
    for _ in range(8):
        result = server._maybe_handle_off(payload, tool_name="roam_audit")
        assert result["summary"]["state"] == "response_storage_unavailable"
        assert result["summary"]["detail_available"] is False
        assert result["retryable"] is False
        assert "operation completed" in result["suggested_action"]
        assert result["preview"]["summary"]["partial_success"] is False
    assert result["trimmed"] is True


@pytest.mark.parametrize("failure", ["probe", "gc"])
def test_gc_failure_keeps_handle_but_discloses_unknown_maintenance(tmp_path, monkeypatch, failure):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("ROAM_MCP_HANDLE_KB", "1")
    monkeypatch.setattr(server, "_HANDLE_GC_WRITE_COUNTER", {"n": 24 if failure == "gc" else 0})

    def unavailable(*args, **kwargs):
        raise PermissionError("controlled GC access failure")

    monkeypatch.setattr(
        server if failure == "gc" else server.os, "_gc_handle_dir" if failure == "gc" else "listdir", unavailable
    )
    payload = {"summary": {"verdict": "analysis complete", "partial_success": False}, "data": "x" * 10000}
    result = server._maybe_handle_off(payload, tool_name="roam_audit")
    assert result["is_handle"] is True
    assert result["summary"]["partial_success"] is True
    assert result["maintenance"]["state"] == "unknown"
    assert result["maintenance"]["reason"] == "handle_gc_unavailable"
    assert result["preview"]["summary"]["partial_success"] is False


def test_gc_directory_probe_does_not_collapse_access_error():
    # Model pathlib variants that turn a failed stat into is_dir() == False.
    # The probe must query stat directly to preserve the error on every runtime.
    class UnavailablePath:
        def stat(self):
            raise PermissionError("controlled stat failure")

        def is_dir(self):
            return False

    with pytest.raises(PermissionError):
        server._gc_dir_is_usable(UnavailablePath())
