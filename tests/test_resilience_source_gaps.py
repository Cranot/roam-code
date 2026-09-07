"""A readable subset does not establish complete resilience analysis."""

from __future__ import annotations

import json
import sqlite3
from contextlib import closing

import pytest

from roam.resilience import run_resilience


@pytest.mark.parametrize("missing", [True, False])
def test_actual_harvester_keeps_findings_and_discloses_missing_source(tmp_path, missing):
    (tmp_path / "app.py").write_text("import requests\nrequests.get(url)\n", encoding="utf-8")
    with closing(sqlite3.connect(":memory:")) as conn:
        conn.row_factory = sqlite3.Row
        conn.execute("CREATE TABLE files(path TEXT, language TEXT, file_role TEXT)")
        conn.execute("INSERT INTO files VALUES ('app.py', 'python', 'source')")
        if missing:
            conn.execute("INSERT INTO files VALUES ('missing.py', 'python', 'source')")
        findings, meta = run_resilience(conn, root=str(tmp_path))
    payload = json.loads(json.dumps({"findings": findings, "meta": meta}))
    assert [finding["subject"] for finding in payload["findings"]] == ["app.py:2"]
    assert payload["meta"]["partial_success"] is missing
    assert bool(payload["meta"]["sources"]["files_unreadable"]) is missing
