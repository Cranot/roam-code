"""Unit tests for the deterministic stranger-repository precision harness."""

from __future__ import annotations

import json
from pathlib import Path

from scripts.stranger_precision_harness import (
    SELF_CHECK_FIXTURE,
    _write_outputs,
    generate_prompt,
    normalize_findings,
    run_harness,
)


def test_normalize_detector_payloads_and_malformed_payload(tmp_path):
    source = tmp_path / "app.py"
    source.write_text("\n".join(f"line {number}" for number in range(1, 12)) + "\n", encoding="utf-8")
    payload = {
        "findings": [
            {"location": "app.py:7", "reason": "second issue"},
            {"file": "app.py", "line": 3, "message": "first issue"},
        ]
    }

    rows = normalize_findings(payload, repo="example/repo.git", detector="detector", repo_path=tmp_path)

    assert [(row["file"], row["line"], row["message"]) for row in rows] == [
        ("app.py", 3, "first issue"),
        ("app.py", 7, "second issue"),
    ]
    assert "1: line 1" in rows[0]["context"]
    assert "6: line 6" in rows[1]["context"]
    malformed = normalize_findings({}, repo="repo", detector="detector", repo_path=tmp_path)
    assert malformed == [
        {"repo": "repo", "detector": "detector", "error": "malformed detector JSON: missing findings list"}
    ]


def test_prompt_is_blind_and_contains_strict_verdict_format():
    findings = [
        {"id": f"sample-{number}", "file": "app.py", "line": number, "message": "candidate", "context": "x()"}
        for number in range(1, 4)
    ]

    prompt = generate_prompt(findings)

    assert all(f"sample-{number}" in prompt for number in range(1, 4))
    assert "ID: true_positive" in prompt
    assert "ID: false_positive" in prompt
    assert "ID: unjudgeable" in prompt
    assert "roam" not in prompt.lower()


def test_truncation_is_deterministic(tmp_path):
    findings = [
        {"location": f"pkg/{29 - number:02d}.py:{30 - number}", "reason": f"finding {number}"} for number in range(30)
    ]
    payload = {"findings": findings}

    first = normalize_findings(payload, repo="repo", detector="detector", repo_path=tmp_path, max_findings=25)
    second = normalize_findings(payload, repo="repo", detector="detector", repo_path=tmp_path, max_findings=25)

    assert len(first) == 25
    assert first == second
    assert [(row["file"], row["line"]) for row in first] == sorted((row["file"], row["line"]) for row in first)


def test_full_pipeline_detects_known_positive_flask_fixture(tmp_path):
    repo = str(SELF_CHECK_FIXTURE)
    rows, successful = run_harness([repo], ["detect_flask_debug_true"], tmp_path / "work", 25)
    out = tmp_path / "findings.jsonl"
    prompt = tmp_path / "prompt.txt"

    _write_outputs(rows, out, prompt)

    output_rows = [json.loads(line) for line in out.read_text(encoding="utf-8").splitlines()]
    findings = [row for row in output_rows if row.get("detector") == "detect_flask_debug_true" and "id" in row]
    assert repo in successful
    assert len(findings) >= 1
    assert any(Path(row["file"]).name == "tp_flask_app.py" for row in findings)
