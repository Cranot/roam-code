"""Regression coverage for the conservative tenant-scope verify detector."""

from __future__ import annotations

import ast
import sqlite3
from pathlib import Path

from roam.commands.cmd_verify import _ALL_CHECKS, _check_tenant_scope
from roam.db.schema import SCHEMA_SQL
from roam.security.tenant_scope import (
    _decorated_endpoints,
    _django_endpoints,
    find_tenant_scope_findings,
    load_tenant_guard_signals,
)

_SOURCE = """\
from flask import Flask

app = Flask(__name__)

@app.get("/guarded")
@require_tenant
def guarded_accounts():
    return Account.query.all()

@app.get("/dependency-guarded", dependencies=[Depends(current_tenant)])
def dependency_guarded_accounts():
    return Account.query.all()

@app.get("/unguarded")
def unguarded_accounts():
    return load_accounts()

def load_accounts():
    return Account.objects.filter(active=True)

@app.get("/health")
def health():
    return {"ok": True}
"""


def _line_span(source: str, function_name: str) -> tuple[int, int]:
    tree = ast.parse(source)
    matches = [
        node
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == function_name
    ]
    assert len(matches) == 1
    node = matches[0]
    start = min([node.lineno, *(decorator.lineno for decorator in node.decorator_list)])
    return start, node.end_lineno or node.lineno


def _indexed_fixture(tmp_path: Path) -> sqlite3.Connection:
    (tmp_path / "app.py").write_text(_SOURCE, encoding="utf-8")
    conn = sqlite3.connect(":memory:")
    conn.executescript(SCHEMA_SQL)
    file_id = conn.execute(
        "INSERT INTO files(path, language, file_role) VALUES ('app.py', 'python', 'source') RETURNING id"
    ).fetchone()[0]
    symbol_ids: dict[str, int] = {}
    for name in (
        "guarded_accounts",
        "dependency_guarded_accounts",
        "unguarded_accounts",
        "load_accounts",
        "health",
    ):
        line_start, line_end = _line_span(_SOURCE, name)
        symbol_ids[name] = conn.execute(
            "INSERT INTO symbols(file_id, name, qualified_name, kind, line_start, line_end) "
            "VALUES (?, ?, ?, 'function', ?, ?) RETURNING id",
            (file_id, name, f"app.{name}", line_start, line_end),
        ).fetchone()[0]
    conn.execute(
        "INSERT INTO edges(source_id, target_id, kind) VALUES (?, ?, 'call')",
        (symbol_ids["unguarded_accounts"], symbol_ids["load_accounts"]),
    )
    conn.commit()
    return conn


def test_tenant_scope_flags_only_unguarded_data_handler(tmp_path: Path):
    conn = _indexed_fixture(tmp_path)

    findings = find_tenant_scope_findings(
        conn,
        tmp_path,
        guard_signals=("require_tenant", "current_tenant"),
    )

    assert [finding["endpoint"] for finding in findings] == ["/unguarded"]
    assert findings[0]["data_signal"] == "Account.objects.filter"
    assert [step["symbol"] for step in findings[0]["reachable_path"]] == [
        "app.unguarded_accounts",
        "app.load_accounts",
    ]


def test_tenant_scope_is_conservative_without_project_guard_or_data(tmp_path: Path):
    conn = _indexed_fixture(tmp_path)

    no_guard_convention = find_tenant_scope_findings(conn, tmp_path, guard_signals=("custom_tenant_guard",))
    with_guard_convention = find_tenant_scope_findings(
        conn,
        tmp_path,
        guard_signals=("require_tenant", "current_tenant"),
    )

    assert no_guard_convention == []
    assert all(finding["endpoint"] != "/health" for finding in with_guard_convention)
    assert all(finding["endpoint"] != "/guarded" for finding in with_guard_convention)
    assert all(finding["endpoint"] != "/dependency-guarded" for finding in with_guard_convention)


def test_tenant_scope_discovers_fastapi_and_django_routes():
    source = """\
@router.post("/fast", dependencies=[Depends(require_tenant)])
async def fast_handler():
    return Item.query.all()

urlpatterns = [path("django/items", item_view)]
"""
    tree = ast.parse(source)

    decorated = _decorated_endpoints(tree, "api.py")
    django = _django_endpoints(tree, "api.py")

    assert [(endpoint.method, endpoint.path, endpoint.handler) for endpoint in decorated] == [
        ("POST", "/fast", "fast_handler")
    ]
    assert [(endpoint.method, endpoint.path, endpoint.handler) for endpoint in django] == [
        ("ANY", "/django/items", "item_view")
    ]


def test_tenant_scope_verify_wiring_and_configurable_guards(tmp_path: Path):
    conn = _indexed_fixture(tmp_path)
    config_dir = tmp_path / ".roam"
    config_dir.mkdir()
    (config_dir / "verify.yaml").write_text(
        "checks: [tenant_scope]\ntenant_guards: [require_tenant, current_tenant]\n",
        encoding="utf-8",
    )

    result = _check_tenant_scope(conn, [], ["app.py"], tmp_path)

    assert "tenant_scope" in _ALL_CHECKS
    assert load_tenant_guard_signals(tmp_path) == ("require_tenant", "current_tenant")
    assert len(result["violations"]) == 1
    assert result["violations"][0]["category"] == "tenant_scope"
    assert "/unguarded" in result["violations"][0]["message"]
