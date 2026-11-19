"""Regression coverage for malformed expected_files in eval task JSONL."""

import json

import pytest

from roam.eval.harness import load_tasks


def test_expected_files_string_is_rejected(tmp_path):
    path = tmp_path / "tasks.jsonl"
    path.write_text(
        json.dumps({"task": "find a", "expected_files": "src/a.py"}) + "\n",
        encoding="utf-8",
    )

    with pytest.raises((ValueError, TypeError)):
        load_tasks(path)


def test_expected_files_array_preserves_whole_paths(tmp_path):
    path = tmp_path / "tasks.jsonl"
    expected = ["src/a.py", "src/b.py"]
    path.write_text(
        json.dumps({"task": "find a and b", "expected_files": expected}) + "\n",
        encoding="utf-8",
    )

    tasks = load_tasks(path)

    assert len(tasks) == 1
    assert tuple(tasks[0].expected_files) == tuple(expected)


def test_empty_expected_files_array_is_rejected(tmp_path):
    path = tmp_path / "tasks.jsonl"
    path.write_text(
        json.dumps({"task": "find a", "expected_files": []}) + "\n",
        encoding="utf-8",
    )

    with pytest.raises((ValueError, TypeError)):
        load_tasks(path)
