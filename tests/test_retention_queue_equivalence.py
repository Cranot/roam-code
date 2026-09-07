"""Retention must keep the same ordered bytes without quadratic front shifts."""

from __future__ import annotations

import ast
import inspect
import json
import time

import pytest

from roam.commands import cmd_service_report as service
from roam.plan import compiler


@pytest.mark.parametrize("function", [compiler._write_compile_telemetry_line, service._persist_engagement_record])
def test_retention_has_no_repeated_list_front_shift(function):
    tree = ast.parse(inspect.getsource(function))
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "pop":
            assert not (node.args and isinstance(node.args[0], ast.Constant) and node.args[0].value == 0)
        if isinstance(node, ast.Delete):
            assert not any(
                isinstance(target, ast.Subscript) and isinstance(target.slice, ast.Constant) and target.slice.value == 0
                for target in node.targets
            )


@pytest.mark.parametrize("count_cap, byte_cap", [(3, 10000), (20, 45), (3, 45), (1, 10000)])
def test_engagement_retention_exact_payload(tmp_path, monkeypatch, count_cap, byte_cap):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(service, "_ENGAGEMENT_LEDGER_MAX_RECORDS", count_cap)
    monkeypatch.setattr(service, "_ENGAGEMENT_LEDGER_MAX_BYTES", byte_cap)
    expected = []
    for index in range(8):
        record = {"id": index, "label": "é"}
        expected.append(json.dumps(record, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
        pruned = len(expected) > count_cap
        expected = expected[-count_cap:]
        while sum(len(row) + 1 for row in expected) > byte_cap:
            expected = expected[1:]
            pruned = True
        diagnostics = {}
        path = service._persist_engagement_record(record, diagnostics=diagnostics)
        assert path is not None
        assert path.read_bytes() == b"\n".join(expected) + b"\n"
        assert diagnostics["records_retained"] == len(expected)
        assert diagnostics["retention_pruned"] is pruned


@pytest.mark.parametrize("count_cap, byte_cap", [(3, 10000), (20, 600), (3, 600)])
def test_compile_retention_exact_payload(tmp_path, monkeypatch, count_cap, byte_cap):
    monkeypatch.setattr(compiler, "_COMPILE_TELEMETRY_MAX_RECORDS", count_cap)
    monkeypatch.setattr(compiler, "_COMPILE_TELEMETRY_MAX_BYTES", byte_cap)
    path = tmp_path / ".roam" / "compile-runs.jsonl"
    path.parent.mkdir()
    expected = []
    for index in range(8):
        record = {
            "ts": time.strftime("%Y-%m-%dT%H:00:00Z", time.gmtime()),
            "procedure": "freeform_explore",
            "total_ms": index,
        }
        row = compiler._sanitize_compile_telemetry_row(record)
        expected.append((compiler._fast_json_dumps(row) + "\n").encode("utf-8"))
        expected = expected[-count_cap:]
        while sum(map(len, expected)) > byte_cap:
            expected = expected[1:]
        compiler._write_compile_telemetry_line(str(path), json.dumps(record))
        assert path.read_bytes() == b"".join(expected)
