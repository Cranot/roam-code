"""Conservative tenant-scope analysis for Python API handlers.

The detector proves three things before emitting a finding:

1. the project uses one of the configured tenant-guard signals;
2. a Flask, FastAPI, or Django route resolves to one unambiguous handler; and
3. that handler reaches a concrete ORM / database operation without reaching
   a configured guard.

It intentionally fails closed on parse errors, ambiguous handlers, and bounded
call-graph traversals.  The check displaces the manual ``Grep`` + call-chain
audit developers otherwise perform when reviewing multi-tenant endpoints.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from roam.db.connection import batched_in
from roam.db.edge_kinds import CALL_EDGE_KINDS
from roam.index.test_conventions import is_test_file

DEFAULT_TENANT_GUARDS: tuple[str, ...] = (
    "require_tenant",
    "current_tenant",
    "get_current_tenant",
    "tenant_required",
    "tenant_scoped",
    "scope_to_tenant",
    "with_tenant",
    "tenant_middleware",
)

_HTTP_DECORATOR_METHODS = frozenset({"route", "get", "post", "put", "patch", "delete", "head", "options"})
_DJANGO_ROUTE_CALLS = frozenset({"path", "re_path", "url"})
_ORM_TERMINALS = frozenset(
    {
        "all",
        "count",
        "delete",
        "exclude",
        "exists",
        "filter",
        "filter_by",
        "first",
        "get",
        "get_or_create",
        "last",
        "one",
        "one_or_none",
        "order_by",
        "prefetch_related",
        "select_related",
        "update",
        "update_or_create",
    }
)
_DB_EXECUTION_TERMINALS = frozenset(
    {"execute", "executemany", "fetchall", "fetchmany", "fetchone", "scalar", "scalars"}
)
_DB_RECEIVERS = frozenset({"conn", "connection", "cursor", "db", "database", "session", "sql_session"})
_MAX_HOPS = 6
_MAX_REACHABLE_SYMBOLS = 500


@dataclass(frozen=True)
class _Endpoint:
    method: str
    path: str
    handler: str
    route_file: str
    route_line: int


@dataclass(frozen=True)
class _Symbol:
    id: int
    name: str
    qualified_name: str
    file: str
    line_start: int
    line_end: int


@dataclass
class _Module:
    path: str
    source: str
    tree: ast.Module
    sqlalchemy_calls: frozenset[str]


class _DirectNodeVisitor(ast.NodeVisitor):
    """Visit one function body without attributing nested defs to it."""

    def __init__(self, root: ast.AST) -> None:
        self.root = root
        self.nodes: list[ast.AST] = []

    def generic_visit(self, node: ast.AST) -> None:
        self.nodes.append(node)
        super().generic_visit(node)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:  # noqa: N802
        if node is self.root:
            self.generic_visit(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:  # noqa: N802
        if node is self.root:
            self.generic_visit(node)

    def visit_ClassDef(self, node: ast.ClassDef) -> None:  # noqa: N802
        if node is self.root:
            self.generic_visit(node)


def _normalise_signal(value: str) -> str:
    return "".join(ch for ch in value.casefold() if ch.isalnum())


def _configured_guard_map(signals: Iterable[str]) -> dict[str, str]:
    out: dict[str, str] = {}
    for raw in signals:
        value = str(raw).strip()
        if value:
            out.setdefault(_normalise_signal(value), value)
    return out


def load_tenant_guard_signals(root: Path) -> tuple[str, ...]:
    """Load tenant guards from ``.roam/verify.yaml`` or return defaults.

    Both ``tenant_guards: [...]`` and ``tenant_scope: {guards: [...]}`` are
    accepted.  An explicit empty list disables the detector.
    """
    path = root / ".roam" / "verify.yaml"
    if not path.exists():
        return DEFAULT_TENANT_GUARDS
    try:
        import yaml

        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except Exception:  # noqa: BLE001 - malformed config must fail closed
        return ()
    if not isinstance(data, dict):
        return ()
    raw = data.get("tenant_guards")
    nested = data.get("tenant_scope")
    if raw is None and isinstance(nested, dict):
        raw = nested.get("guards")
    if raw is None:
        return DEFAULT_TENANT_GUARDS
    if not isinstance(raw, list):
        return ()
    return tuple(dict.fromkeys(str(item).strip() for item in raw if str(item).strip()))


def _dotted_name(node: ast.AST | None) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        prefix = _dotted_name(node.value)
        return f"{prefix}.{node.attr}" if prefix else node.attr
    return ""


def _string_value(node: ast.AST | None) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def _decorator_method(decorator: ast.AST) -> str | None:
    call = decorator if isinstance(decorator, ast.Call) else None
    dotted = _dotted_name(call.func if call else decorator)
    parts = dotted.split(".")
    if len(parts) < 2:
        return None
    terminal = parts[-1].casefold()
    receiver = _normalise_signal(parts[-2])
    route_receiver = receiver in {"api", "app", "application", "blueprint", "bp", "router", "server"} or any(
        receiver.endswith(suffix) for suffix in ("api", "app", "blueprint", "router")
    )
    return terminal if terminal in _HTTP_DECORATOR_METHODS and route_receiver else None


def _route_path(call: ast.Call) -> str | None:
    if call.args:
        path = _string_value(call.args[0])
        if path is not None:
            return path
    for keyword in call.keywords:
        if keyword.arg in {"path", "rule"}:
            return _string_value(keyword.value)
    return None


def _decorated_endpoints(tree: ast.Module, rel_path: str) -> list[_Endpoint]:
    endpoints: list[_Endpoint] = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for decorator in node.decorator_list:
            method = _decorator_method(decorator)
            if method is None or not isinstance(decorator, ast.Call):
                continue
            path = _route_path(decorator)
            if path is None:
                continue
            methods = [method.upper()]
            if method == "route":
                methods = ["GET"]
                for keyword in decorator.keywords:
                    if keyword.arg != "methods" or not isinstance(keyword.value, (ast.List, ast.Tuple)):
                        continue
                    configured = [_string_value(item) for item in keyword.value.elts]
                    methods = [item.upper() for item in configured if item] or methods
            line = getattr(decorator, "lineno", node.lineno)
            endpoints.extend(_Endpoint(http_method, path, node.name, rel_path, line) for http_method in methods)
    return endpoints


def _django_handler_name(node: ast.AST) -> str:
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "as_view":
        return _dotted_name(node.func.value).rsplit(".", 1)[-1]
    return _dotted_name(node).rsplit(".", 1)[-1]


def _django_endpoints(tree: ast.Module, rel_path: str) -> list[_Endpoint]:
    has_urlpatterns = any(
        isinstance(node, (ast.Assign, ast.AnnAssign))
        and any(
            isinstance(target, ast.Name) and target.id == "urlpatterns"
            for target in (node.targets if isinstance(node, ast.Assign) else [node.target])
        )
        for node in tree.body
    )
    has_django_urls_import = any(
        isinstance(node, ast.ImportFrom) and (node.module or "") == "django.urls" for node in tree.body
    )
    if not (has_urlpatterns or has_django_urls_import):
        return []
    endpoints: list[_Endpoint] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        call_name = _dotted_name(node.func).rsplit(".", 1)[-1]
        if call_name not in _DJANGO_ROUTE_CALLS or len(node.args) < 2:
            continue
        path = _string_value(node.args[0])
        handler = _django_handler_name(node.args[1])
        if path is None or not handler:
            continue
        rendered_path = path if path.startswith("/") else f"/{path.lstrip('^').rstrip('$')}"
        endpoints.append(_Endpoint("ANY", rendered_path, handler, rel_path, node.lineno))
    return endpoints


def _sqlalchemy_calls(tree: ast.Module) -> frozenset[str]:
    names: set[str] = set()
    for node in tree.body:
        if not isinstance(node, ast.ImportFrom) or not (node.module or "").startswith("sqlalchemy"):
            continue
        for alias in node.names:
            if alias.name in {"select", "update", "delete"}:
                names.add(alias.asname or alias.name)
    return frozenset(names)


def _load_modules(conn, root: Path) -> dict[str, _Module]:
    rows = conn.execute("SELECT path FROM files WHERE language = 'python' OR path LIKE '%.py'").fetchall()
    modules: dict[str, _Module] = {}
    for row in rows:
        rel_path = str(row[0]).replace("\\", "/")
        if is_test_file(rel_path):
            continue
        try:
            source = (root / rel_path).read_text(encoding="utf-8", errors="replace")
            tree = ast.parse(source, filename=rel_path)
        except (OSError, SyntaxError, ValueError):
            continue
        modules[rel_path] = _Module(rel_path, source, tree, _sqlalchemy_calls(tree))
    return modules


def _load_symbols(conn, modules: dict[str, _Module]) -> dict[int, _Symbol]:
    if not modules:
        return {}
    rows = conn.execute(
        "SELECT s.id, s.name, COALESCE(s.qualified_name, ''), f.path, "
        "       COALESCE(s.line_start, 0), COALESCE(s.line_end, s.line_start, 0) "
        "FROM symbols s JOIN files f ON f.id = s.file_id "
        "WHERE s.kind IN ('function', 'method')"
    ).fetchall()
    symbols: dict[int, _Symbol] = {}
    for row in rows:
        rel_path = str(row[3]).replace("\\", "/")
        if rel_path not in modules:
            continue
        symbols[int(row[0])] = _Symbol(
            id=int(row[0]),
            name=str(row[1]),
            qualified_name=str(row[2]),
            file=rel_path,
            line_start=int(row[4]),
            line_end=int(row[5]),
        )
    return symbols


def _definition_nodes(module: _Module) -> list[ast.FunctionDef | ast.AsyncFunctionDef]:
    return [node for node in ast.walk(module.tree) if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))]


def _symbol_node(symbol: _Symbol, module: _Module) -> ast.FunctionDef | ast.AsyncFunctionDef | None:
    candidates = [node for node in _definition_nodes(module) if node.name == symbol.name]
    if not candidates:
        return None
    covering = [
        node
        for node in candidates
        if min([node.lineno, *(d.lineno for d in node.decorator_list)])
        <= symbol.line_start
        <= (node.end_lineno or node.lineno)
    ]
    picked = covering or candidates
    return picked[0] if len(picked) == 1 else None


def _direct_nodes(node: ast.AST) -> list[ast.AST]:
    visitor = _DirectNodeVisitor(node)
    visitor.visit(node)
    return visitor.nodes


def _guard_match(value: str, guards: dict[str, str]) -> str | None:
    if not value:
        return None
    dotted = _normalise_signal(value)
    terminal = _normalise_signal(value.rsplit(".", 1)[-1])
    return guards.get(dotted) or guards.get(terminal)


def _matched_guard_in_nodes(nodes: Iterable[ast.AST], guards: dict[str, str]) -> str | None:
    for node in nodes:
        if isinstance(node, ast.Name):
            matched = _guard_match(node.id, guards)
        elif isinstance(node, ast.Attribute):
            matched = _guard_match(_dotted_name(node), guards)
        else:
            continue
        if matched:
            return matched
    return None


def _project_guard_matches(
    modules: dict[str, _Module], symbols: dict[int, _Symbol], guards: dict[str, str]
) -> set[str]:
    matched: set[str] = set()
    for symbol in symbols.values():
        value = _guard_match(symbol.name, guards) or _guard_match(symbol.qualified_name, guards)
        if value:
            matched.add(value)
    for module in modules.values():
        for node in ast.walk(module.tree):
            value = ""
            if isinstance(node, ast.Name):
                value = node.id
            elif isinstance(node, ast.Attribute):
                value = _dotted_name(node)
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                value = node.name
            matched_value = _guard_match(value, guards)
            if matched_value:
                matched.add(matched_value)
    return matched


def _has_global_guard(modules: dict[str, _Module], guards: dict[str, str]) -> bool:
    """Recognise only explicit framework-wide middleware registrations."""
    for module in modules.values():
        for node in ast.walk(module.tree):
            if isinstance(node, ast.Call):
                terminal = _dotted_name(node.func).rsplit(".", 1)[-1]
                if terminal in {"add_middleware", "include_router"}:
                    if _matched_guard_in_nodes(ast.walk(node), guards):
                        return True
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                decorators = {
                    _dotted_name(d.func if isinstance(d, ast.Call) else d).rsplit(".", 1)[-1]
                    for d in node.decorator_list
                }
                if decorators & {"before_request", "middleware"} and (
                    _guard_match(node.name, guards) or _matched_guard_in_nodes(_direct_nodes(node), guards)
                ):
                    return True
            if isinstance(node, (ast.Assign, ast.AnnAssign)):
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                if not any(isinstance(target, ast.Name) and target.id == "MIDDLEWARE" for target in targets):
                    continue
                value = node.value
                for item in ast.walk(value):
                    text = _string_value(item)
                    if text and _guard_match(text.rsplit(".", 1)[-1], guards):
                        return True
    return False


def _data_access(nodes: Iterable[ast.AST], module: _Module) -> tuple[str, int] | None:
    for node in nodes:
        if not isinstance(node, ast.Call):
            continue
        dotted = _dotted_name(node.func)
        parts = dotted.split(".") if dotted else []
        terminal = parts[-1].casefold() if parts else ""
        lowered_parts = [part.casefold() for part in parts]
        if terminal in _ORM_TERMINALS and ("objects" in lowered_parts or "query" in lowered_parts[:-1]):
            return dotted, node.lineno
        receiver = lowered_parts[-2] if len(lowered_parts) >= 2 else ""
        if terminal == "query" and receiver in _DB_RECEIVERS:
            return dotted, node.lineno
        if terminal in _DB_EXECUTION_TERMINALS and receiver in _DB_RECEIVERS:
            return dotted, node.lineno
        if isinstance(node.func, ast.Name) and node.func.id in module.sqlalchemy_calls:
            return node.func.id, node.lineno
    return None


def _resolve_handler(endpoint: _Endpoint, symbols: dict[int, _Symbol]) -> _Symbol | None:
    same_file = [
        symbol for symbol in symbols.values() if symbol.file == endpoint.route_file and symbol.name == endpoint.handler
    ]
    if len(same_file) == 1:
        return same_file[0]
    project = [symbol for symbol in symbols.values() if symbol.name == endpoint.handler]
    return project[0] if len(project) == 1 else None


def _reachable_symbols(conn, start: _Symbol, symbols: dict[int, _Symbol]) -> tuple[list[_Symbol], dict[int, int], bool]:
    visited = {start.id}
    parents: dict[int, int] = {}
    ordered = [start]
    frontier = [start.id]
    truncated = False
    for _depth in range(_MAX_HOPS + 1):
        if not frontier:
            break
        rows = batched_in(
            conn,
            "SELECT source_id, target_id FROM edges "
            "WHERE source_id IN ({ph}) AND kind IN (?, ?) ORDER BY source_id, target_id",
            frontier,
            post=CALL_EDGE_KINDS,
        )
        next_frontier: list[int] = []
        for row in rows:
            source_id, target_id = int(row[0]), int(row[1])
            if target_id in visited or target_id not in symbols:
                continue
            if len(visited) >= _MAX_REACHABLE_SYMBOLS:
                truncated = True
                continue
            visited.add(target_id)
            parents[target_id] = source_id
            next_frontier.append(target_id)
            ordered.append(symbols[target_id])
        frontier = next_frontier
    if frontier:
        truncated = True
    return ordered, parents, truncated


def _path_to(symbol_id: int, parents: dict[int, int], symbols: dict[int, _Symbol]) -> list[dict]:
    ids = [symbol_id]
    while ids[-1] in parents:
        ids.append(parents[ids[-1]])
    ids.reverse()
    return [{"symbol": symbols[item].qualified_name or symbols[item].name, "file": symbols[item].file} for item in ids]


def find_tenant_scope_findings(
    conn,
    root: Path,
    *,
    guard_signals: Iterable[str] = DEFAULT_TENANT_GUARDS,
) -> list[dict]:
    """Return deterministic unguarded tenant-data endpoint findings."""
    guards = _configured_guard_map(guard_signals)
    if not guards:
        return []
    modules = _load_modules(conn, root)
    symbols = _load_symbols(conn, modules)
    matched_project_guards = _project_guard_matches(modules, symbols, guards)
    if not matched_project_guards or _has_global_guard(modules, guards):
        return []

    endpoints: list[_Endpoint] = []
    for module in modules.values():
        endpoints.extend(_decorated_endpoints(module.tree, module.path))
        endpoints.extend(_django_endpoints(module.tree, module.path))

    findings: list[dict] = []
    seen: set[tuple[str, str, str]] = set()
    for endpoint in sorted(endpoints, key=lambda item: (item.route_file, item.route_line, item.method, item.path)):
        key = (endpoint.method, endpoint.path, endpoint.handler)
        if key in seen:
            continue
        seen.add(key)
        handler = _resolve_handler(endpoint, symbols)
        if handler is None:
            continue
        reachable, parents, truncated = _reachable_symbols(conn, handler, symbols)
        if truncated:
            continue
        guard_on_path: str | None = None
        data_evidence: tuple[_Symbol, str, int] | None = None
        for symbol in reachable:
            matched = _guard_match(symbol.name, guards) or _guard_match(symbol.qualified_name, guards)
            if matched:
                guard_on_path = matched
                break
            module = modules[symbol.file]
            node = _symbol_node(symbol, module)
            if node is None:
                continue
            direct_nodes = _direct_nodes(node)
            matched = _matched_guard_in_nodes(direct_nodes, guards)
            if matched:
                guard_on_path = matched
                break
            access = _data_access(direct_nodes, module)
            if access is not None and data_evidence is None:
                data_evidence = (symbol, access[0], access[1])
        if guard_on_path or data_evidence is None:
            continue
        data_symbol, data_signal, data_line = data_evidence
        path = _path_to(data_symbol.id, parents, symbols)
        findings.append(
            {
                "method": endpoint.method,
                "endpoint": endpoint.path,
                "handler": handler.qualified_name or handler.name,
                "file": handler.file,
                "line": endpoint.route_line,
                "route_file": endpoint.route_file,
                "data_file": data_symbol.file,
                "data_line": data_line,
                "data_signal": data_signal,
                "reachable_path": path,
                "reachable_files": sorted({step["file"] for step in path} | {endpoint.route_file}),
                "matched_project_guards": sorted(matched_project_guards),
                "guard_metric_definition": (
                    "exact configured identifier match on the handler's bounded call path; "
                    "global middleware registrations suppress findings"
                ),
                "data_access_definition": (
                    "explicit Django ORM manager, SQLAlchemy query/select, or named DB session/connection operation"
                ),
            }
        )
    return findings


__all__ = ["DEFAULT_TENANT_GUARDS", "find_tenant_scope_findings", "load_tenant_guard_signals"]
