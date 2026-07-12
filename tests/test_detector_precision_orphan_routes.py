"""Regression tripwire for orphan-routes, not a precision proof.

The labelled pair locks an unconsumed endpoint and its nearest frontend
consumer suppression against refactors; it does not claim a precision number.
"""

import sqlite3
import subprocess
from pathlib import Path

from roam.commands.cmd_orphan_routes import _analyse_orphan_routes

FIXTURES = Path(__file__).parent / "fixtures" / "detector_eval" / "orphan-routes"


def _run(fixture: str, tmp_path: Path):
    source = FIXTURES / fixture
    (tmp_path / "routes").mkdir(parents=True)
    (tmp_path / "routes" / "api.php").write_text(source.read_text(), encoding="utf-8")
    if fixture == "tn_frontend_consumer.php":
        consumer = FIXTURES / "frontend" / "InvoicesPage.vue"
        (tmp_path / "frontend").mkdir()
        (tmp_path / "frontend" / consumer.name).write_text(consumer.read_text(), encoding="utf-8")
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True)
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("CREATE TABLE files (path TEXT)")
    conn.executemany("INSERT INTO files(path) VALUES (?)", [("routes/api.php",), ("frontend/InvoicesPage.vue",)])
    return _analyse_orphan_routes(tmp_path, conn, limit=50)


def test_orphan_route_tp_fires_and_frontend_consumer_tn_is_clean(tmp_path):
    tp = _run("tp_unconsumed_route.php", tmp_path / "tp")
    tn = _run("tn_frontend_consumer.php", tmp_path / "tn")
    assert any(item["path"] == "/api/invoices" for item in tp["orphans"])
    assert not any(item["path"] == "/api/invoices" for item in tn["orphans"])
