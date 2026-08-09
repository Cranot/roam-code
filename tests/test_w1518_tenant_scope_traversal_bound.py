"""W1518 — a traversal bound made an endpoint DISAPPEAR and published PASS.

``_reachable_symbols`` is bounded by ``_MAX_HOPS`` (6) and
``_MAX_REACHABLE_SYMBOLS`` (500).  Hitting either bound used to ``continue``
past the endpoint entirely, so the endpoint contributed no finding and no
disclosure — and ``roam verify --checks tenant_scope`` published ``PASS``,
``score 100``, ``verification_complete: true``, ``partial_success: false``.

The bound is on the TRAVERSAL, not on the evidence.  Measured on a scratch
repo whose handler holds the unguarded ``db.session.execute`` in its OWN body
— hop 0 — deepening an entirely UNRELATED call chain from 3 to 8 links made
the violation vanish: 1 violation at 3 links, 0 at 8, with
``verification_complete: true`` both times.  The data access was element 0 of
the visited list; the walk knew about it before it ever reached the limit.

The must-not-fire half carries the weight here.  A guard found inside the
bound is positive evidence that no amount of further walking can retract, so a
truncated-but-guarded endpoint must stay silent and must NOT be reported as
incomplete; and a traversal that finished must behave exactly as before.
"""

from __future__ import annotations

import ast
import sqlite3
from pathlib import Path

from roam.commands.cmd_verify import _check_tenant_scope
from roam.db.schema import SCHEMA_SQL
from roam.security.tenant_scope import (
    _MAX_HOPS,
    find_tenant_scope_findings,
    find_tenant_scope_findings_status,
)

_GUARDS = ("require_tenant", "current_tenant")


def _source(chain_len: int, *, guard_the_handler: bool = False) -> str:
    """A repo with a guard convention, one handler, and an UNRELATED chain.

    The chain hanging off ``list_orders`` contains no data access and no
    guard; its only job is to make the walk deep. The thing the detector
    should find sits in the handler's own body.
    """
    guard_line = "@require_tenant\n" if guard_the_handler else ""
    parts = [
        "from flask import Flask\n",
        "\n",
        "app = Flask(__name__)\n",
        "\n",
        '@app.get("/guarded")\n',
        "@require_tenant\n",
        "def guarded_accounts():\n",
        "    return Account.query.all()\n",
        "\n",
        '@app.get("/orders")\n',
        guard_line,
        "def list_orders():\n",
        "    step1()\n",
        "    return Account.objects.filter(active=True)\n",
        "\n",
    ]
    for index in range(1, chain_len):
        parts.append(f"def step{index}():\n    return step{index + 1}()\n\n")
    parts.append(f"def step{chain_len}():\n    return 0\n")
    return "".join(parts)


def _line_span(source: str, function_name: str) -> tuple[int, int]:
    tree = ast.parse(source)
    matches = [
        node
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == function_name
    ]
    assert len(matches) == 1, function_name
    node = matches[0]
    start = min([node.lineno, *(decorator.lineno for decorator in node.decorator_list)])
    return start, node.end_lineno or node.lineno


def _fixture(tmp_path: Path, chain_len: int, *, guard_the_handler: bool = False) -> sqlite3.Connection:
    source = _source(chain_len, guard_the_handler=guard_the_handler)
    (tmp_path / "app.py").write_text(source, encoding="utf-8")
    conn = sqlite3.connect(":memory:")
    conn.executescript(SCHEMA_SQL)
    file_id = conn.execute(
        "INSERT INTO files(path, language, file_role) VALUES ('app.py', 'python', 'source') RETURNING id"
    ).fetchone()[0]
    names = ["guarded_accounts", "list_orders", *[f"step{i}" for i in range(1, chain_len + 1)]]
    symbol_ids: dict[str, int] = {}
    for name in names:
        line_start, line_end = _line_span(source, name)
        symbol_ids[name] = conn.execute(
            "INSERT INTO symbols(file_id, name, qualified_name, kind, line_start, line_end) "
            "VALUES (?, ?, ?, 'function', ?, ?) RETURNING id",
            (file_id, name, f"app.{name}", line_start, line_end),
        ).fetchone()[0]
    edges = [("list_orders", "step1")]
    edges += [(f"step{i}", f"step{i + 1}") for i in range(1, chain_len)]
    for src, dst in edges:
        conn.execute(
            "INSERT INTO edges(source_id, target_id, kind) VALUES (?, ?, 'call')",
            (symbol_ids[src], symbol_ids[dst]),
        )
    conn.commit()
    return conn


# A chain long enough that the walk still has a frontier after _MAX_HOPS + 1
# rounds. Derived from the bound rather than hard-coded, so raising the bound
# does not silently turn these into vacuous passes.
_DEEP = _MAX_HOPS + 3
_SHALLOW = 3


# ---------------------------------------------------------------------------
# MUST FIRE
# ---------------------------------------------------------------------------


def test_truncated_walk_still_reports_the_handlers_own_data_access(tmp_path: Path):
    """The evidence sits at hop 0; the bound cannot un-know it."""
    conn = _fixture(tmp_path, _DEEP)

    findings, coverage = find_tenant_scope_findings_status(conn, tmp_path, guard_signals=_GUARDS)

    orders = [f for f in findings if f["endpoint"] == "/orders"]
    assert orders, f"the endpoint vanished; coverage={coverage}"
    assert orders[0]["data_signal"] == "Account.objects.filter"
    assert orders[0]["traversal_truncated"] is True
    assert "would not have been seen" in orders[0]["guard_metric_definition"]


def test_status_names_the_endpoint_whose_guard_search_did_not_finish(tmp_path: Path):
    conn = _fixture(tmp_path, _DEEP)

    _findings, coverage = find_tenant_scope_findings_status(conn, tmp_path, guard_signals=_GUARDS)

    assert coverage["endpoints_truncated"] == 1
    assert coverage["endpoints_analyzed"] == coverage["endpoints_total"] - 1
    assert coverage["hop_limit"] == _MAX_HOPS
    entry = coverage["truncated_endpoints"][0]
    assert entry["endpoint"] == "/orders"
    assert entry["conclusion"] == "unguarded_data_access"
    assert "app.py" in entry["files"]


def test_verify_refuses_to_call_a_bounded_run_complete(tmp_path: Path):
    """`capped` is what makes verification_complete false downstream."""
    conn = _fixture(tmp_path, _DEEP)

    result = _check_tenant_scope(conn, [], ["app.py"], tmp_path)

    assert result.get("capped") is True, result
    assert result.get("scan_cap") == 1, result
    assert "hop" in result.get("unavailable_reason", ""), result
    assert "/orders" in result.get("unavailable_reason", ""), result
    # The proven violation must survive the disclosure, not be replaced by it.
    assert [v["category"] for v in result["violations"]] == ["tenant_scope"]


# ---------------------------------------------------------------------------
# MUST NOT FIRE
# ---------------------------------------------------------------------------


def test_completed_walk_is_untouched(tmp_path: Path):
    """Everything above fires only when the walk actually hit the bound."""
    conn = _fixture(tmp_path, _SHALLOW)

    findings, coverage = find_tenant_scope_findings_status(conn, tmp_path, guard_signals=_GUARDS)

    orders = [f for f in findings if f["endpoint"] == "/orders"]
    assert orders and orders[0]["traversal_truncated"] is False
    assert "would not have been seen" not in orders[0]["guard_metric_definition"]
    assert coverage["endpoints_truncated"] == 0
    assert coverage["truncated_endpoints"] == []

    result = _check_tenant_scope(conn, [], ["app.py"], tmp_path)
    assert "capped" not in result
    assert "unavailable_reason" not in result
    assert result["score"] == 60


def test_a_guard_inside_the_bound_still_suppresses_and_is_not_capped(tmp_path: Path):
    """A guard is positive evidence; a longer walk cannot retract it.

    This is the over-eager-fix guard: treating every truncated walk as
    undetermined would report an endpoint that is demonstrably guarded as
    unverified, and would fail builds that are in fact clean.
    """
    conn = _fixture(tmp_path, _DEEP, guard_the_handler=True)

    findings, coverage = find_tenant_scope_findings_status(conn, tmp_path, guard_signals=_GUARDS)

    assert [f["endpoint"] for f in findings if f["endpoint"] == "/orders"] == []
    assert coverage["endpoints_truncated"] == 0, coverage["truncated_endpoints"]

    result = _check_tenant_scope(conn, [], ["app.py"], tmp_path)
    assert "capped" not in result
    assert result["violations"] == []
    assert result["score"] == 100


def test_a_truncated_endpoint_outside_the_changed_scope_does_not_cap(tmp_path: Path):
    """verify is diff-scoped; an untouched file must not degrade the run."""
    conn = _fixture(tmp_path, _DEEP)

    result = _check_tenant_scope(conn, [], ["unrelated.py"], tmp_path)

    assert "capped" not in result
    assert result["violations"] == []
    assert result["score"] == 100


def test_no_guard_convention_stays_empty_with_empty_coverage(tmp_path: Path):
    """The detector is opt-in; a project without the convention is untouched."""
    conn = _fixture(tmp_path, _DEEP)

    findings, coverage = find_tenant_scope_findings_status(conn, tmp_path, guard_signals=("custom_tenant_guard",))

    assert findings == []
    assert coverage["endpoints_truncated"] == 0
    assert coverage["endpoints_total"] == 0
    assert coverage["truncated_endpoints"] == []


def test_list_wrapper_still_returns_exactly_the_findings(tmp_path: Path):
    """The existing callers keep the list-returning signature."""
    conn = _fixture(tmp_path, _DEEP)

    assert (
        find_tenant_scope_findings(conn, tmp_path, guard_signals=_GUARDS)
        == find_tenant_scope_findings_status(conn, tmp_path, guard_signals=_GUARDS)[0]
    )
