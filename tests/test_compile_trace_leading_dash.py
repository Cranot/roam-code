"""Regression: trace probe must pass `--` before the raw task text.

A leading-dash trace prompt (e.g. "-v trace the login flow") was parsed by
Click as a retrieve option, yielding non-JSON help/error output and dropping
the trace evidence. The probe now inserts `--` to halt option parsing so the
prompt is always treated as the positional task.
"""

from __future__ import annotations

from roam.plan import compiler


def test_probe_trace_passes_dashdash_before_task(monkeypatch):
    captured: dict = {}

    def _fake_run_roam(args, cwd, timeout=8.0, detail=False):
        captured["args"] = list(args)
        return {
            "candidates": [
                {
                    "file_path": "src/app.py",
                    "line_start": 10,
                    "line_end": 20,
                    "kind": "function",
                    "qualified_name": "app.login",
                    "score": 1.5,
                }
            ]
        }

    monkeypatch.setattr(compiler, "_run_roam", _fake_run_roam)

    task = "-v then trace the login flow"
    out = compiler._probe_trace_for_task(task, cwd=None)

    assert out is not None
    assert "trace_spans" in out
    # `--` must come before the raw task so Click stops option parsing.
    assert captured["args"][0] == "retrieve"
    assert captured["args"][1] == "--"
    assert captured["args"][2] == task
