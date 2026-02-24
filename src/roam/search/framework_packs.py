"""Pre-indexed framework/library symbol packs for semantic search (#96)."""

from __future__ import annotations

import hashlib
import math
from collections import Counter

from roam.search.tfidf import tokenize, cosine_similarity

_PACK_SCORE_SCALE = 0.55


# Curated starter packs focused on common onboarding/search intents.
_PACK_DEFINITIONS: dict[str, list[dict[str, str]]] = {
    "python-stdlib": [
        {"name": "Path", "qualified_name": "pathlib.Path", "kind": "class",
         "signature": "Path(path: str) -> Path",
         "summary": "Filesystem path object with read_text and write_text helpers.",
         "keywords": "filesystem io path read write exists"},
        {"name": "defaultdict", "qualified_name": "collections.defaultdict", "kind": "class",
         "signature": "defaultdict(factory, ...) -> defaultdict",
         "summary": "Dictionary that creates missing keys from a factory.",
         "keywords": "dictionary grouping counter map aggregate"},
        {"name": "lru_cache", "qualified_name": "functools.lru_cache", "kind": "decorator",
         "signature": "lru_cache(maxsize=128)",
         "summary": "Memoization decorator for deterministic function calls.",
         "keywords": "cache memoization performance recursion"},
        {"name": "groupby", "qualified_name": "itertools.groupby", "kind": "function",
         "signature": "groupby(iterable, key=None)",
         "summary": "Group consecutive items by key in sorted iterables.",
         "keywords": "stream transform grouping aggregation"},
        {"name": "dataclass", "qualified_name": "dataclasses.dataclass", "kind": "decorator",
         "signature": "@dataclass",
         "summary": "Generate init/eq/repr methods for model-like classes.",
         "keywords": "model schema value object class"},
        {"name": "gather", "qualified_name": "asyncio.gather", "kind": "function",
         "signature": "gather(*aws) -> list",
         "summary": "Run awaitables concurrently and collect results.",
         "keywords": "async concurrent tasks await"},
        {"name": "contextmanager", "qualified_name": "contextlib.contextmanager", "kind": "decorator",
         "signature": "@contextmanager",
         "summary": "Build context managers from generator functions.",
         "keywords": "resource lifetime cleanup with statement"},
        {"name": "Protocol", "qualified_name": "typing.Protocol", "kind": "class",
         "signature": "class Protocol",
         "summary": "Structural typing interface for duck-typed contracts.",
         "keywords": "interface contract typing static-check"},
    ],
    "django": [
        {"name": "Model", "qualified_name": "django.db.models.Model", "kind": "class",
         "signature": "class Model",
         "summary": "Base ORM model class for Django entities.",
         "keywords": "orm database table migration"},
        {"name": "QuerySet.filter", "qualified_name": "django.db.models.QuerySet.filter", "kind": "method",
         "signature": "filter(**lookups) -> QuerySet",
         "summary": "Filter ORM records by lookup expressions.",
         "keywords": "query where condition lookup"},
        {"name": "QuerySet.select_related", "qualified_name": "django.db.models.QuerySet.select_related", "kind": "method",
         "signature": "select_related(*fields) -> QuerySet",
         "summary": "Eager load FK relationships in one SQL query.",
         "keywords": "orm eager loading n+1 joins"},
        {"name": "QuerySet.prefetch_related", "qualified_name": "django.db.models.QuerySet.prefetch_related", "kind": "method",
         "signature": "prefetch_related(*lookups) -> QuerySet",
         "summary": "Eager load reverse and M2M relationships.",
         "keywords": "orm eager loading related n+1"},
        {"name": "path", "qualified_name": "django.urls.path", "kind": "function",
         "signature": "path(route, view, name=None)",
         "summary": "Register URL routes in Django URLConf.",
         "keywords": "routing endpoint http url"},
        {"name": "View", "qualified_name": "django.views.View", "kind": "class",
         "signature": "class View",
         "summary": "Class-based view base with HTTP verb methods.",
         "keywords": "controller endpoint get post"},
        {"name": "ModelForm", "qualified_name": "django.forms.ModelForm", "kind": "class",
         "signature": "class ModelForm",
         "summary": "Form class mapped to ORM models.",
         "keywords": "validation schema input form"},
        {"name": "TestCase", "qualified_name": "django.test.TestCase", "kind": "class",
         "signature": "class TestCase",
         "summary": "Transactional test base with database rollback.",
         "keywords": "testing unit integration db"},
    ],
    "flask": [
        {"name": "Flask", "qualified_name": "flask.Flask", "kind": "class",
         "signature": "Flask(import_name)",
         "summary": "WSGI app object and route registration root.",
         "keywords": "app server routing"},
        {"name": "Blueprint", "qualified_name": "flask.Blueprint", "kind": "class",
         "signature": "Blueprint(name, import_name)",
         "summary": "Modular route grouping for Flask applications.",
         "keywords": "routing module endpoints"},
        {"name": "request", "qualified_name": "flask.request", "kind": "object",
         "signature": "request: LocalProxy",
         "summary": "Incoming HTTP request context object.",
         "keywords": "http headers args payload"},
        {"name": "jsonify", "qualified_name": "flask.jsonify", "kind": "function",
         "signature": "jsonify(*args, **kwargs)",
         "summary": "Return JSON response with Flask response object.",
         "keywords": "response serialization api"},
        {"name": "abort", "qualified_name": "flask.abort", "kind": "function",
         "signature": "abort(status_code)",
         "summary": "Raise HTTP error response with status code.",
         "keywords": "error handling http exception"},
        {"name": "SQLAlchemy", "qualified_name": "flask_sqlalchemy.SQLAlchemy", "kind": "class",
         "signature": "SQLAlchemy(app=None)",
         "summary": "Flask integration wrapper for SQLAlchemy ORM.",
         "keywords": "orm database models session"},
        {"name": "LoginManager", "qualified_name": "flask_login.LoginManager", "kind": "class",
         "signature": "LoginManager(app=None)",
         "summary": "Session authentication manager for Flask apps.",
         "keywords": "auth login session user"},
        {"name": "generate_password_hash", "qualified_name": "werkzeug.security.generate_password_hash", "kind": "function",
         "signature": "generate_password_hash(password)",
         "summary": "Hash plain passwords before persistence.",
         "keywords": "security password auth crypto"},
    ],
    "fastapi": [
        {"name": "FastAPI", "qualified_name": "fastapi.FastAPI", "kind": "class",
         "signature": "FastAPI()",
         "summary": "ASGI app with OpenAPI generation and dependency injection.",
         "keywords": "api asgi server openapi"},
        {"name": "APIRouter", "qualified_name": "fastapi.APIRouter", "kind": "class",
         "signature": "APIRouter(prefix='', tags=None)",
         "summary": "Composable route groups for feature modules.",
         "keywords": "routing endpoints modular"},
        {"name": "Depends", "qualified_name": "fastapi.Depends", "kind": "function",
         "signature": "Depends(dependency)",
         "summary": "Declare dependency injection providers for handlers.",
         "keywords": "dependency injection service container"},
        {"name": "HTTPException", "qualified_name": "fastapi.HTTPException", "kind": "class",
         "signature": "HTTPException(status_code, detail)",
         "summary": "Structured API error response exception.",
         "keywords": "error response status api"},
        {"name": "BaseModel", "qualified_name": "pydantic.BaseModel", "kind": "class",
         "signature": "class BaseModel",
         "summary": "Typed request/response schema with validation.",
         "keywords": "validation schema model parsing"},
        {"name": "BackgroundTasks", "qualified_name": "fastapi.BackgroundTasks", "kind": "class",
         "signature": "BackgroundTasks()",
         "summary": "Run non-blocking jobs after response is sent.",
         "keywords": "async task queue background"},
        {"name": "OAuth2PasswordBearer", "qualified_name": "fastapi.security.OAuth2PasswordBearer", "kind": "class",
         "signature": "OAuth2PasswordBearer(tokenUrl)",
         "summary": "Bearer-token dependency helper for auth flows.",
         "keywords": "auth security oauth token"},
        {"name": "Session", "qualified_name": "sqlalchemy.orm.Session", "kind": "class",
         "signature": "class Session",
         "summary": "Database session typically injected into handlers.",
         "keywords": "database transaction orm"},
    ],
    "react": [
        {"name": "useState", "qualified_name": "react.useState", "kind": "hook",
         "signature": "useState(initialState)",
         "summary": "Component-local state hook.",
         "keywords": "state ui component"},
        {"name": "useEffect", "qualified_name": "react.useEffect", "kind": "hook",
         "signature": "useEffect(effect, deps?)",
         "summary": "Run side effects after render/update.",
         "keywords": "lifecycle side effect dependency"},
        {"name": "useMemo", "qualified_name": "react.useMemo", "kind": "hook",
         "signature": "useMemo(factory, deps)",
         "summary": "Memoize expensive computed values.",
         "keywords": "performance cache compute"},
        {"name": "useCallback", "qualified_name": "react.useCallback", "kind": "hook",
         "signature": "useCallback(fn, deps)",
         "summary": "Memoize callback identity for props.",
         "keywords": "callback memo props rerender"},
        {"name": "createContext", "qualified_name": "react.createContext", "kind": "function",
         "signature": "createContext(defaultValue)",
         "summary": "Create shared context for nested components.",
         "keywords": "context provider consumer state"},
        {"name": "BrowserRouter", "qualified_name": "react-router-dom.BrowserRouter", "kind": "component",
         "signature": "<BrowserRouter>",
         "summary": "Client-side router root component.",
         "keywords": "routing navigation spa"},
        {"name": "useQuery", "qualified_name": "@tanstack/react-query.useQuery", "kind": "hook",
         "signature": "useQuery(options)",
         "summary": "Remote data fetching, caching, and status.",
         "keywords": "fetch api cache loading"},
        {"name": "createSlice", "qualified_name": "@reduxjs/toolkit.createSlice", "kind": "function",
         "signature": "createSlice({name, initialState, reducers})",
         "summary": "Generate reducer/actions for app state modules.",
         "keywords": "redux state reducers actions"},
    ],
    "express": [
        {"name": "Router", "qualified_name": "express.Router", "kind": "function",
         "signature": "Router()",
         "summary": "Compose route handlers by feature module.",
         "keywords": "routing endpoint middleware"},
        {"name": "Request", "qualified_name": "express.Request", "kind": "interface",
         "signature": "interface Request",
         "summary": "Incoming HTTP request object type.",
         "keywords": "http params query body"},
        {"name": "Response", "qualified_name": "express.Response", "kind": "interface",
         "signature": "interface Response",
         "summary": "HTTP response writer object type.",
         "keywords": "http status json send"},
        {"name": "NextFunction", "qualified_name": "express.NextFunction", "kind": "type",
         "signature": "type NextFunction = (err?) => void",
         "summary": "Pass control to downstream middleware.",
         "keywords": "middleware chain error handler"},
        {"name": "json", "qualified_name": "body-parser.json", "kind": "function",
         "signature": "json()",
         "summary": "Parse JSON request bodies.",
         "keywords": "middleware payload parser"},
        {"name": "authenticate", "qualified_name": "passport.authenticate", "kind": "function",
         "signature": "authenticate(strategy, options?)",
         "summary": "Attach auth strategy middleware to routes.",
         "keywords": "auth login jwt session"},
        {"name": "find", "qualified_name": "mongoose.Model.find", "kind": "method",
         "signature": "find(filter) -> Query",
         "summary": "MongoDB query builder for model collections.",
         "keywords": "mongodb orm query"},
        {"name": "object", "qualified_name": "joi.object", "kind": "function",
         "signature": "object(schema)",
         "summary": "Schema validation builder for request payloads.",
         "keywords": "validation schema input"},
    ],
    "sqlalchemy": [
        {"name": "Session", "qualified_name": "sqlalchemy.orm.Session", "kind": "class",
         "signature": "class Session",
         "summary": "ORM unit-of-work and transaction boundary.",
         "keywords": "transaction commit rollback orm"},
        {"name": "relationship", "qualified_name": "sqlalchemy.orm.relationship", "kind": "function",
         "signature": "relationship(target, ...)",
         "summary": "Declare ORM relationships between mapped classes.",
         "keywords": "orm relation foreign-key model"},
        {"name": "select", "qualified_name": "sqlalchemy.select", "kind": "function",
         "signature": "select(*columns)",
         "summary": "Composable SQL expression select constructor.",
         "keywords": "query sql statement"},
        {"name": "joinedload", "qualified_name": "sqlalchemy.orm.joinedload", "kind": "function",
         "signature": "joinedload(attr)",
         "summary": "Eager load relations in same query.",
         "keywords": "eager loading n+1 performance"},
        {"name": "func", "qualified_name": "sqlalchemy.func", "kind": "namespace",
         "signature": "func.<sql_fn>()",
         "summary": "SQL function namespace for aggregate expressions.",
         "keywords": "aggregate count sum group-by"},
        {"name": "AsyncSession", "qualified_name": "sqlalchemy.ext.asyncio.AsyncSession", "kind": "class",
         "signature": "class AsyncSession",
         "summary": "Async ORM session for asyncio applications.",
         "keywords": "async orm transaction"},
        {"name": "declarative_base", "qualified_name": "sqlalchemy.orm.declarative_base", "kind": "function",
         "signature": "declarative_base()",
         "summary": "Factory for declarative ORM model base classes.",
         "keywords": "model base mapping"},
        {"name": "add_column", "qualified_name": "alembic.op.add_column", "kind": "function",
         "signature": "add_column(table_name, column)",
         "summary": "Alembic migration op for schema evolution.",
         "keywords": "migration schema database"},
    ],
    "pytest": [
        {"name": "fixture", "qualified_name": "pytest.fixture", "kind": "decorator",
         "signature": "@pytest.fixture",
         "summary": "Declare reusable test setup/teardown function.",
         "keywords": "testing setup dependency injection"},
        {"name": "mark.parametrize", "qualified_name": "pytest.mark.parametrize", "kind": "decorator",
         "signature": "@pytest.mark.parametrize(args, values)",
         "summary": "Parameterize one test over multiple datasets.",
         "keywords": "testing matrix data-driven"},
        {"name": "raises", "qualified_name": "pytest.raises", "kind": "context-manager",
         "signature": "pytest.raises(ExpectedError)",
         "summary": "Assert exceptions and inspect failure details.",
         "keywords": "testing errors assertion"},
        {"name": "MonkeyPatch", "qualified_name": "pytest.MonkeyPatch", "kind": "class",
         "signature": "class MonkeyPatch",
         "summary": "Temporarily patch env vars/attrs during tests.",
         "keywords": "mock patch environment"},
        {"name": "approx", "qualified_name": "pytest.approx", "kind": "function",
         "signature": "approx(expected, rel=..., abs=...)",
         "summary": "Tolerance-based numeric assertions.",
         "keywords": "floating point assertion numeric"},
        {"name": "mark.asyncio", "qualified_name": "pytest_asyncio.mark.asyncio", "kind": "decorator",
         "signature": "@pytest.mark.asyncio",
         "summary": "Run async test coroutines in event loop.",
         "keywords": "async testing coroutine"},
        {"name": "db", "qualified_name": "pytest_django.mark.django_db", "kind": "decorator",
         "signature": "@pytest.mark.django_db",
         "summary": "Enable Django database access in tests.",
         "keywords": "django database test transaction"},
        {"name": "cov", "qualified_name": "pytest-cov", "kind": "plugin",
         "signature": "pytest --cov",
         "summary": "Coverage reporting plugin for pytest runs.",
         "keywords": "coverage quality testing"},
    ],
}


def _build_doc_text(pack: str, entry: dict[str, str]) -> str:
    return " ".join([
        pack,
        entry.get("name", ""),
        entry.get("qualified_name", ""),
        entry.get("kind", ""),
        entry.get("signature", ""),
        entry.get("summary", ""),
        entry.get("keywords", ""),
    ])


def _stable_pack_symbol_id(pack: str, name: str, qualified_name: str) -> int:
    digest = hashlib.sha1(f"{pack}:{qualified_name or name}".encode("utf-8")).hexdigest()
    return -int(digest[:12], 16)


def _compile_entries() -> list[dict]:
    docs: list[dict] = []
    df: dict[str, int] = {}

    for pack_name, entries in sorted(_PACK_DEFINITIONS.items()):
        for entry in entries:
            tokens = tokenize(_build_doc_text(pack_name, entry))
            if not tokens:
                continue
            tf_raw = Counter(tokens)
            max_freq = max(tf_raw.values()) if tf_raw else 1
            tf = {t: c / max_freq for t, c in tf_raw.items()}
            docs.append({
                "pack": pack_name,
                "name": entry["name"],
                "qualified_name": entry.get("qualified_name", ""),
                "kind": entry.get("kind", "symbol"),
                "signature": entry.get("signature", ""),
                "summary": entry.get("summary", ""),
                "tf": tf,
            })
            for term in tf:
                df[term] = df.get(term, 0) + 1

    n_docs = len(docs)
    if n_docs == 0:
        return []
    idf = {t: math.log((n_docs + 1) / (count + 1)) + 1 for t, count in df.items()}

    compiled: list[dict] = []
    for doc in docs:
        identity = doc["qualified_name"] or doc["name"]
        path_identity = identity.replace(".", "/")
        vec = {t: tf_val * idf.get(t, 1.0) for t, tf_val in doc["tf"].items()}
        compiled.append({
            "pack": doc["pack"],
            "name": doc["name"],
            "qualified_name": doc["qualified_name"],
            "kind": doc["kind"],
            "signature": doc["signature"],
            "summary": doc["summary"],
            "vector": vec,
            "symbol_id": _stable_pack_symbol_id(doc["pack"], doc["name"], doc["qualified_name"]),
            "file_path": f"@pack/{doc['pack']}/{path_identity}",
        })

    return compiled


_PACK_ENTRIES = _compile_entries()


def available_packs() -> list[str]:
    """List all built-in pack names."""
    return sorted(_PACK_DEFINITIONS.keys())


def search_pack_symbols(
    query: str,
    top_k: int = 10,
    packs: list[str] | None = None,
) -> list[dict]:
    """Search pre-indexed framework/library packs for semantic matches."""
    query_tokens = tokenize(query)
    if not query_tokens:
        return []

    query_vec: dict[str, float] = {}
    for token in query_tokens:
        query_vec[token] = query_vec.get(token, 0.0) + 1.0

    selected_packs = None
    if packs:
        selected_packs = {p.strip().lower() for p in packs if p and p.strip()}

    ranked: list[tuple[float, dict]] = []
    for entry in _PACK_ENTRIES:
        if selected_packs and entry["pack"] not in selected_packs:
            continue
        sim = cosine_similarity(query_vec, entry["vector"])
        if sim <= 0:
            continue
        score = min(1.0, sim * _PACK_SCORE_SCALE)
        ranked.append((score, entry))

    ranked.sort(key=lambda x: (-x[0], x[1]["pack"], x[1]["name"], x[1]["symbol_id"]))
    ranked = ranked[:top_k]

    results = []
    for score, entry in ranked:
        results.append({
            "score": round(score, 4),
            "symbol_id": entry["symbol_id"],
            "name": entry["name"],
            "file_path": entry["file_path"],
            "kind": entry["kind"],
            "line_start": 1,
            "line_end": 1,
            "source": "pack",
            "pack": entry["pack"],
        })
    return results

