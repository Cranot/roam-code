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
# the ``pytest.`` prefix). Examples:
#   @pytest.fixture
#   @pytest.fixture(scope="session")
#   @fixture
#   @fixture(autouse=True)
_FIXTURE_DECORATOR_RE = re.compile(r"@(?:pytest\.)?fixture\b")

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


def resolve_pytest_fixtures(conn) -> int:
    """Insert ``pytest_fixture_dep`` edges from fixtures and tests to the
    fixtures they parameterise on.

    Returns the number of edges inserted. Idempotent: existing
    ``pytest_fixture_dep`` edges are removed and re-derived each run.
    """
    # Load every Python function/method symbol with its decorators +
    # signature + file path. One pass is cheaper than per-fixture queries.
    rows = conn.execute(
        """
        SELECT s.id, s.name, s.signature, s.decorators, s.kind, f.path AS file_path
        FROM symbols s
        JOIN files f ON s.file_id = f.id
        WHERE f.language = 'python'
          AND s.kind IN ('function', 'method')
        """
    ).fetchall()
    if not rows:
        return 0

    # Fixtures keyed by name. Multiple fixtures can share a name across
    # conftest scopes — disambiguated by file path at resolution time.
    fixtures_by_name: dict[str, list[dict]] = {}
    consumers: list[dict] = []

    for r in rows:
        name = r["name"]
        decorators = r["decorators"] or ""
        signature = r["signature"] or ""
        file_path = (r["file_path"] or "").replace("\\", "/")

        if _is_fixture(decorators):
            entry = {"id": r["id"], "name": name, "file_path": file_path}
            fixtures_by_name.setdefault(name, []).append(entry)
            consumers.append({"id": r["id"], "name": name, "signature": signature, "file_path": file_path})
        elif _is_test_function(name):
            consumers.append({"id": r["id"], "name": name, "signature": signature, "file_path": file_path})

    if not fixtures_by_name:
        return 0

    def _resolve(consumer_file: str, fixture_name: str) -> int | None:
        """Pick the best fixture symbol id for ``fixture_name`` from the
        perspective of ``consumer_file``."""
        candidates = fixtures_by_name.get(fixture_name)
        if not candidates:
            return None
        consumer_file = consumer_file.replace("\\", "/")
        # Same file
        for c in candidates:
            if c["file_path"] == consumer_file:
                return c["id"]
        # conftest chain (closest first)
        chain = _conftest_chain(consumer_file)
        chain_index = {p: i for i, p in enumerate(chain)}
        chain_hits = [(chain_index[c["file_path"]], c["id"]) for c in candidates if c["file_path"] in chain_index]
        if chain_hits:
            chain_hits.sort()
            return chain_hits[0][1]
        # Fallback — any one (deterministic by id for stable output)
        if len(candidates) == 1:
            return candidates[0]["id"]
        return sorted(candidates, key=lambda c: c["id"])[0]["id"]

    # Compute edges
    edges: list[tuple[int, int]] = []
    seen: set[tuple[int, int]] = set()
    for c in consumers:
        param_names = _parse_param_names(c["signature"])
        if not param_names:
            continue
        for p in param_names:
            if p in _BUILTIN_FIXTURES or p in _NON_FIXTURE_PARAMS:
                continue
            target_id = _resolve(c["file_path"], p)
            if target_id is None:
                continue
            if target_id == c["id"]:
                # A fixture cannot depend on itself — skip the
                # degenerate self-loop the parser would otherwise produce
                # for a fixture whose param shadows its own name.
                continue
            key = (c["id"], target_id)
            if key in seen:
                continue
            seen.add(key)
            edges.append(key)

    # Drop any prior pytest_fixture_dep edges so reindex is consistent.
    with conn:
        conn.execute("DELETE FROM edges WHERE kind = 'pytest_fixture_dep'")
        if edges:
            conn.executemany(
                "INSERT INTO edges (source_id, target_id, kind) VALUES (?, ?, 'pytest_fixture_dep')",
                edges,
            )
    return len(edges)
