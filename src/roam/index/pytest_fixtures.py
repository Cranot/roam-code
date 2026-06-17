"""Post-indexing pytest fixture dependency resolution.

A pytest fixture's parameters are *other fixtures*. The relationship is
implicit (parameter name == fixture name) and not visible from any
import or call edge, so the regular call-graph extractor misses it
entirely. This module reconstructs the chain.

Runs after symbol extraction. For each function decorated with
``@pytest.fixture`` (or bare ``@fixture``), it parses the parameter list
and looks up another fixture with the matching name, preferring:

1. Same file
2. ``conftest.py`` in the same directory
3. ``conftest.py`` in any parent directory (closest wins)
4. Any fixture project-wide (last-resort, ambiguous)

Each match becomes an edge with ``kind='pytest_fixture_dep'``. The same
resolution is applied to test functions (``def test_*``) so an agent can
ask "which fixtures does this test depend on, transitively".
"""

from __future__ import annotations

import os
import re

# Match a fixture decorator (with or without arguments, with or without
# the ``pytest.`` prefix). The leading ``@`` is required so help-text
# mentions of ``pytest.fixture`` (e.g. inside Click ``--help`` strings
# captured by the extractor) don't masquerade as a real fixture.
# Examples that match:
#   @pytest.fixture
#   @pytest.fixture(scope="session")
#   @fixture
#   @fixture(autouse=True)
_FIXTURE_DECORATOR_RE = re.compile(r"@(?:pytest\.)?fixture\b")

# Pull ``scope="..."``  (or single-quoted) from the decorator call.
_SCOPE_RE = re.compile(r"""scope\s*=\s*['"]([a-z]+)['"]""")
# Pull ``autouse=True`` (or False) — case-sensitive Python literal.
_AUTOUSE_RE = re.compile(r"\bautouse\s*=\s*(True|False)\b")
_VALID_SCOPES = frozenset({"function", "class", "module", "package", "session"})

# Match the parameter list of a ``def name(...)`` line. Captures the
# entire parens-block so we can split on commas.
_DEF_PARAMS_RE = re.compile(r"\bdef\s+\w+\s*\(([^)]*)\)")

# pytest's built-in fixtures — not user-defined, never resolvable to a
# project symbol. Skip them silently.
_BUILTIN_FIXTURES = frozenset(
    {
        "request",
        "tmp_path",
        "tmp_path_factory",
        "tmpdir",
        "tmpdir_factory",
        "capsys",
        "capsysbinary",
        "capfd",
        "capfdbinary",
        "caplog",
        "monkeypatch",
        "pytestconfig",
        "record_property",
        "record_xml_attribute",
        "record_testsuite_property",
        "recwarn",
        "doctest_namespace",
        "cache",
        "testdir",
        "pytester",
    }
)

# Common parameter names that look like fixtures but are usually
# non-fixture arguments (``self``, ``cls``, parametrize values).
_NON_FIXTURE_PARAMS = frozenset({"self", "cls", "args", "kwargs"})


def _is_fixture(decorators: str | None) -> bool:
    """True when the comma-joined decorator string marks a pytest fixture."""
    if not decorators:
        return False
    return bool(_FIXTURE_DECORATOR_RE.search(decorators))


def _fixture_scope(decorators: str | None) -> str:
    """Parse the fixture's scope from its decorator. Defaults to
    ``function`` when no explicit scope is given (pytest's default).

    Returns one of ``function``, ``class``, ``module``, ``package``,
    ``session`` — or ``function`` when the decorator string is malformed
    or doesn't match the recognised set.
    """
    if not decorators:
        return "function"
    m = _SCOPE_RE.search(decorators)
    if not m:
        return "function"
    scope = m.group(1)
    return scope if scope in _VALID_SCOPES else "function"


def _fixture_autouse(decorators: str | None) -> bool:
    """True when the fixture is decorated ``@pytest.fixture(autouse=True)``."""
    if not decorators:
        return False
    m = _AUTOUSE_RE.search(decorators)
    return bool(m and m.group(1) == "True")


def _is_test_function(name: str) -> bool:
    """pytest's discovery rule: function-level test names start with ``test_``."""
    return name.startswith("test_")


def _parse_param_names(signature: str | None) -> list[str]:
    """Pull parameter names out of a Python ``def`` signature line.

    Handles type annotations and default values by splitting on ``,`` then
    stripping ``: type`` and ``= default`` from each param. Returns names
    only — empty list if no params or signature unparseable.
    """
    if not signature:
        return []
    m = _DEF_PARAMS_RE.search(signature)
    if not m:
        return []
    params_blob = m.group(1).strip()
    if not params_blob:
        return []

    names: list[str] = []
    # Naive comma split is fine here: type annotations with commas
    # (Generic[X, Y]) are rare in fixture signatures, and pytest itself
    # doesn't allow them syntactically as param-of-param.
    for raw in params_blob.split(","):
        part = raw.strip()
        if not part:
            continue
        # Drop default value
        if "=" in part:
            part = part.split("=", 1)[0].strip()
        # Drop type annotation
        if ":" in part:
            part = part.split(":", 1)[0].strip()
        # Drop ``*`` / ``**`` markers
        part = part.lstrip("*").strip()
        if not part:
            continue
        names.append(part)
    return names


def _conftest_chain(file_path: str) -> list[str]:
    """All ``conftest.py`` paths visible from ``file_path``, closest first.

    pytest looks up a fixture by walking up from the test file's directory
    to the rootdir, picking up every ``conftest.py`` along the way. We
    don't have the rootdir easily here, so we just walk up until the
    parent stops changing (filesystem root) — safe because the actual
    set of indexed conftest.py files is what bounds the lookup later.
    """
    paths: list[str] = []
    d = os.path.dirname(file_path).replace("\\", "/")
    while d:
        paths.append(f"{d}/conftest.py" if d else "conftest.py")
        parent = os.path.dirname(d).replace("\\", "/")
        if parent == d:
            break
        d = parent
    # Top-level conftest.py (no leading dir)
    paths.append("conftest.py")
    return paths


def _load_pytest_symbols(conn) -> list:
    """Load Python fixture/test candidates from test and conftest files."""
    return conn.execute(
        """
        SELECT s.id, s.name, s.signature, s.decorators, s.kind, f.path AS file_path
        FROM symbols s
        JOIN files f ON s.file_id = f.id
        WHERE f.language = 'python'
          AND s.kind IN ('function', 'method')
          AND (
              f.file_role = 'test'
              OR f.path LIKE '%/conftest.py'
              OR f.path = 'conftest.py'
          )
        """
    ).fetchall()


def _fixture_entry(row) -> dict:
    return {
        "id": row["id"],
        "name": row["name"],
        "file_path": (row["file_path"] or "").replace("\\", "/"),
    }


def _consumer_entry(row) -> dict:
    return {
        "id": row["id"],
        "name": row["name"],
        "signature": row["signature"] or "",
        "file_path": (row["file_path"] or "").replace("\\", "/"),
    }


def _collect_fixture_indexes(rows: list) -> tuple[dict[str, list[dict]], list[dict]]:
    fixtures_by_name: dict[str, list[dict]] = {}
    consumers: list[dict] = []
    for row in rows:
        decorators = row["decorators"] or ""
        if _is_fixture(decorators):
            entry = _fixture_entry(row)
            fixtures_by_name.setdefault(entry["name"], []).append(entry)
            consumers.append(_consumer_entry(row))
        elif _is_test_function(row["name"]):
            consumers.append(_consumer_entry(row))
    return fixtures_by_name, consumers


def _resolve_fixture_id(fixtures_by_name: dict[str, list[dict]], consumer_file: str, fixture_name: str) -> int | None:
    """Pick the best fixture symbol id for ``fixture_name`` from ``consumer_file``."""
    candidates = fixtures_by_name.get(fixture_name)
    if not candidates:
        return None
    consumer_file = consumer_file.replace("\\", "/")
    for candidate in candidates:
        if candidate["file_path"] == consumer_file:
            return candidate["id"]
    chain_index = {path: idx for idx, path in enumerate(_conftest_chain(consumer_file))}
    chain_hits = [
        (chain_index[candidate["file_path"]], candidate["id"])
        for candidate in candidates
        if candidate["file_path"] in chain_index
    ]
    if chain_hits:
        return min(chain_hits)[1]
    if len(candidates) == 1:
        return candidates[0]["id"]
    return min(candidates, key=lambda candidate: candidate["id"])["id"]


def _is_resolvable_fixture_param(param_name: str) -> bool:
    return param_name not in _BUILTIN_FIXTURES and param_name not in _NON_FIXTURE_PARAMS


def _fixture_edge_for_param(
    consumer: dict,
    param_name: str,
    fixtures_by_name: dict[str, list[dict]],
) -> tuple[int, int] | None:
    if not _is_resolvable_fixture_param(param_name):
        return None
    target_id = _resolve_fixture_id(fixtures_by_name, consumer["file_path"], param_name)
    if target_id is None or target_id == consumer["id"]:
        return None
    return consumer["id"], target_id


def _build_fixture_edges(consumers: list[dict], fixtures_by_name: dict[str, list[dict]]) -> list[tuple[int, int]]:
    edges: list[tuple[int, int]] = []
    seen: set[tuple[int, int]] = set()
    for consumer in consumers:
        for param_name in _parse_param_names(consumer["signature"]):
            key = _fixture_edge_for_param(consumer, param_name, fixtures_by_name)
            if key is None:
                continue
            if key not in seen:
                seen.add(key)
                edges.append(key)
    return edges


def _replace_fixture_edges(conn, edges: list[tuple[int, int]]) -> None:
    with conn:
        conn.execute("DELETE FROM edges WHERE kind = 'pytest_fixture_dep'")
        if edges:
            conn.executemany(
                "INSERT INTO edges (source_id, target_id, kind) VALUES (?, ?, 'pytest_fixture_dep')",
                edges,
            )


def resolve_pytest_fixtures(conn) -> int:
    """Insert ``pytest_fixture_dep`` edges from fixtures and tests to the
    fixtures they parameterise on.

    Returns the number of edges inserted. Idempotent: existing
    ``pytest_fixture_dep`` edges are removed and re-derived each run.
    """
    # Load Python function/method symbols from test/conftest files only.
    # Fixtures and test functions exclusively live in those, so we don't
    # need to scan production code. On a large project this avoids a
    # full-table scan of symbols.
    rows = _load_pytest_symbols(conn)
    if not rows:
        return 0

    # Fixtures keyed by name. Multiple fixtures can share a name across
    # conftest scopes — disambiguated by file path at resolution time.
    fixtures_by_name, consumers = _collect_fixture_indexes(rows)
    if not fixtures_by_name:
        return 0

    edges = _build_fixture_edges(consumers, fixtures_by_name)

    # Drop any prior pytest_fixture_dep edges so reindex is consistent.
    _replace_fixture_edges(conn, edges)
    return len(edges)
