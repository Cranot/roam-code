"""Operational smoke logs belong to the private internal directory."""

from __future__ import annotations

import importlib.util
import json
import sys

from tests._helpers.repo_root import repo_root


def test_smoke_harness_keeps_raw_log_and_report_private(tmp_path, monkeypatch):
    script = repo_root() / "dev" / "roam_smoke.py"
    spec = importlib.util.spec_from_file_location("roam_dev_smoke_private_output", script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    root = tmp_path / "project"
    public = root / "dev"
    public.mkdir(parents=True)
    previous = public / "roam_smoke_results.jsonl"
    previous.write_text("preserve existing evidence\n", encoding="utf-8")
    monkeypatch.setattr(module, "__file__", str(public / "roam_smoke.py"))
    monkeypatch.setattr(sys, "argv", ["roam_smoke.py", "--workers", "1"])
    monkeypatch.setattr(module, "_roam_commands", lambda: ["health"])
    row = {"cmd": "health", "kind": "OK", "rc": 0, "dur_s": 0.1, "note": ""}
    monkeypatch.setattr(module, "_run_one", lambda *args: row)

    assert module.main() == 0

    private = root / "internal" / "smoke"
    assert json.loads((private / "roam_smoke_results.jsonl").read_text(encoding="utf-8")) == row
    reports = list(private.glob("roam-smoke-*.md"))
    assert len(reports) == 1
    assert "1 commands run" in reports[0].read_text(encoding="utf-8")
    assert previous.read_text(encoding="utf-8") == "preserve existing evidence\n"
    assert list(public.iterdir()) == [previous]
