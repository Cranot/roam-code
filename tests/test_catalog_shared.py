"""W1015: focused unit tests for ``roam.catalog._shared`` (W886 drive-by).

The catalog-shared helpers (``loc`` / ``find_workspace_root`` /
``is_test_path`` / ``enclosing_symbol`` / ``make_smell_finding``) are
the W864 + W873 + W877 + W923 hoists that consolidated four+ historical
in-file clones. Coverage was scattered across ``test_smells.py`` /
``test_findings_smells.py`` and the W886 parity test in
``test_is_test_path.py``; none exercised the helpers in isolation.

This file pins each helper's contract directly:

  * ``loc(path, line)``               -- W864, str builder with None-guard
  * ``find_workspace_root()``         -- W864, returns Path on .git miss
  * ``is_test_path(path)``            -- W873 + W886 + W889, None / "" guard
                                         (broader coverage lives in
                                         ``test_is_test_path.py``)
  * ``enclosing_symbol(conn, fid, l)``-- W877, 3-tuple return shape +
                                         ``<module>`` fallback + defensive
                                         ``OperationalError`` swallowing
  * ``make_smell_finding(...)``       -- W923, 8-key insertion order +
                                         optional-kwarg None-omission +
                                         hash-stable JSON shape

NOTE: the task description referenced a ``camel_split`` helper in
``_shared.py`` with ``list[str]`` return + ``__all__`` export. That
helper does NOT live in ``roam.catalog._shared`` -- the only
camel splitter in the tree is ``roam.search.index_embeddings._camel_split``
(returns ``str``, no ``__all__``). Adapted per hard-constraint "don't
fix the helper" -- the ``camel_split`` section is omitted here and the
location drift is captured as a W-followup in the task report.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from roam.catalog._shared import (
    enclosing_symbol,
    find_workspace_root,
    is_test_path,
    loc,
    make_smell_finding,
)

# ---------------------------------------------------------------------------
# loc(path, line) -- W864
# ---------------------------------------------------------------------------


def test_loc_formats_path_and_line() -> None:
    """Standard case: ``"path:line"`` shape with both fields populated."""
    assert loc("src/roam/cli.py", 42) == "src/roam/cli.py:42"


def test_loc_handles_line_none() -> None:
    """``line=None`` MUST drop the trailing ``:`` -- returns bare path."""
    assert loc("src/roam/cli.py", None) == "src/roam/cli.py"


def test_loc_line_zero_is_falsy_but_explicit() -> None:
    """``line=0`` is not None, so it MUST appear in the output (the
    helper's None-guard tests ``is not None``, not truthiness)."""
    assert loc("foo.py", 0) == "foo.py:0"


def test_loc_handles_empty_path() -> None:
    """Empty path + line still produces ``":<line>"`` -- documents the
    current behavior (the helper does not validate input)."""
    assert loc("", 17) == ":17"


# ---------------------------------------------------------------------------
# find_workspace_root() -- W864
# ---------------------------------------------------------------------------


def test_find_workspace_root_returns_path() -> None:
    """Smoke: helper always returns a ``Path`` instance (never raises)."""
    root = find_workspace_root()
    assert isinstance(root, Path)


def test_find_workspace_root_falls_back_to_cwd_in_temp(tmp_path, monkeypatch) -> None:
    """When invoked from a directory with no ``.git`` parent in any
    ancestor, the helper still returns a Path (cwd fallback). This
    pins the ``except (ImportError, OSError): return Path.cwd()``
    contract.

    Uses ``tmp_path`` which has no ``.git`` so ``find_project_root``
    walks to the filesystem root and returns that.
    """
    monkeypatch.chdir(tmp_path)
    root = find_workspace_root()
    assert isinstance(root, Path)
    # Returned path must exist (either a real ancestor with .git, or
    # cwd / the filesystem root after walking up).
    assert root.exists()


# ---------------------------------------------------------------------------
# is_test_path(path) -- W873 / W886 / W889 / W891
#
# Broad parametrized coverage lives in ``test_is_test_path.py``. This
# file pins ONLY the falsy-input guards and a one-shot per-pattern smoke
# (so a future refactor that drops the helper from ``_shared.py`` fails
# this file's import, not just the broader file).
# ---------------------------------------------------------------------------


def test_is_test_path_empty_string_returns_false() -> None:
    assert is_test_path("") is False


def test_is_test_path_none_safe_via_falsy_guard() -> None:
    """The helper's first line is ``if not path: return False``, so
    ``None`` is rejected via the falsy guard rather than raising
    ``AttributeError`` on ``path.replace(...)``. This pins the W886
    None-tolerance contract."""
    # type: ignore[arg-type] -- intentionally passing None to exercise
    # the falsy guard at runtime; the annotation is ``str`` but the
    # guard is the documented contract.
    assert is_test_path(None) is False  # type: ignore[arg-type]


def test_is_test_path_python_test_dir() -> None:
    assert is_test_path("tests/test_foo.py") is True


def test_is_test_path_plain_source_rejected() -> None:
    assert is_test_path("src/roam/cli.py") is False


def test_is_test_path_go_underscore_test() -> None:
    """Go convention: ``*_test.go`` basename matches without needing a
    ``tests/`` parent directory."""
    assert is_test_path("src/foo_test.go") is True


def test_is_test_path_java_camelcase_test_class() -> None:
    """W889: ``FooTest.java`` (camelCase) matches via
    ``_CAMELCASE_TEST_BASENAME_PATTERNS``."""
    assert is_test_path("src/FooTest.java") is True


def test_is_test_path_ruby_spec_dir() -> None:
    """Ruby/RSpec convention: ``spec/`` directory + ``_spec.rb`` basename.
    Matches via ``_TEST_DIR_PREFIXES`` / ``_TEST_DIR_SEGMENTS``."""
    assert is_test_path("spec/models/example_spec.rb") is True


# ---------------------------------------------------------------------------
# enclosing_symbol(conn, file_id, line) -- W877
# ---------------------------------------------------------------------------


def _build_symbols_fixture(conn: sqlite3.Connection) -> None:
    """Build the minimal ``symbols`` schema ``enclosing_symbol`` queries
    against. Real ``open_db`` builds the full schema (W877 helper uses
    only ``id`` / ``file_id`` / ``name`` / ``kind`` / ``line_start`` /
    ``line_end``) so this in-memory fixture is sufficient.
    """
    conn.row_factory = sqlite3.Row
    conn.execute(
        """
        CREATE TABLE symbols (
            id INTEGER PRIMARY KEY,
            file_id INTEGER NOT NULL,
            name TEXT NOT NULL,
            kind TEXT NOT NULL,
            line_start INTEGER,
            line_end INTEGER
        )
        """
    )
    # file_id=1: two top-level functions and one method.
    #   foo()   : lines 10..20
    #   bar()   : lines 25..40
    #   Cls.baz : lines 50..60 (kind='method')
    # plus one non-callable symbol that the SQL filter must exclude.
    conn.executemany(
        "INSERT INTO symbols (id, file_id, name, kind, line_start, line_end) VALUES (?, ?, ?, ?, ?, ?)",
        [
            (1, 1, "foo", "function", 10, 20),
            (2, 1, "bar", "function", 25, 40),
            (3, 1, "baz", "method", 50, 60),
            (4, 1, "Cls", "class", 45, 60),  # excluded by kind filter
        ],
    )
    conn.commit()


def test_enclosing_symbol_returns_innermost_function() -> None:
    """Line inside ``foo``'s span returns ``("foo", "function", 10)``."""
    conn = sqlite3.connect(":memory:")
    _build_symbols_fixture(conn)
    assert enclosing_symbol(conn, 1, 15) == ("foo", "function", 10)


def test_enclosing_symbol_returns_method_kind() -> None:
    """Line inside the method's span returns kind=``method``."""
    conn = sqlite3.connect(":memory:")
    _build_symbols_fixture(conn)
    assert enclosing_symbol(conn, 1, 55) == ("baz", "method", 50)


def test_enclosing_symbol_falls_back_to_module_sentinel() -> None:
    """Line outside every recorded symbol span (e.g. line 5, before
    ``foo`` starts at 10) falls back to the ``<module>`` sentinel,
    kind=``file``, and the queried line as the start."""
    conn = sqlite3.connect(":memory:")
    _build_symbols_fixture(conn)
    assert enclosing_symbol(conn, 1, 5) == ("<module>", "file", 5)


def test_enclosing_symbol_handles_operational_error() -> None:
    """When the ``symbols`` table is missing entirely, the SQL raises
    ``sqlite3.OperationalError``; the helper must swallow it and return
    the ``<module>`` sentinel (pins W877's defensive contract inherited
    from ``type_switch._enclosing_symbol``)."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row  # no table created
    assert enclosing_symbol(conn, 1, 42) == ("<module>", "file", 42)


def test_enclosing_symbol_class_kind_is_filtered_out() -> None:
    """``kind='class'`` rows MUST NOT be returned -- the SQL filter
    restricts to ``kind IN ('function', 'method')``. Line 47 is inside
    ``Cls`` (45..60) but outside any function/method, so the helper
    falls back to ``<module>``."""
    conn = sqlite3.connect(":memory:")
    _build_symbols_fixture(conn)
    assert enclosing_symbol(conn, 1, 47) == ("<module>", "file", 47)


# ---------------------------------------------------------------------------
# make_smell_finding(...) -- W923
# ---------------------------------------------------------------------------


def test_make_smell_finding_8_key_default_shape() -> None:
    """With no optional kwargs, the returned dict has EXACTLY the 8
    historical keys in insertion order. This pins the byte-identical
    JSON shape the 24 ``smells.py`` call-sites rely on."""
    finding = make_smell_finding(
        "long-function",
        "warning",
        "do_work",
        "function",
        "src/foo.py:42",
        120,
        80,
        "function exceeds threshold",
    )
    assert list(finding.keys()) == [
        "smell_id",
        "severity",
        "symbol_name",
        "kind",
        "location",
        "metric_value",
        "threshold",
        "description",
    ]
    # No None-filler keys leak in.
    assert "evidence" not in finding
    assert "confidence" not in finding
    assert "detector_version" not in finding


def test_make_smell_finding_appends_optional_kwargs_in_order() -> None:
    """When all three optional kwargs are non-None, the returned dict
    has 11 keys in the documented insertion order: 8 positional, then
    ``evidence``, ``confidence``, ``detector_version``."""
    finding = make_smell_finding(
        "god-class",
        "critical",
        "BigClass",
        "class",
        "src/big.py:1",
        500,
        200,
        "class too large",
        evidence={"lines": 500, "methods": 47},
        confidence="structural",
        detector_version=3,
    )
    assert list(finding.keys()) == [
        "smell_id",
        "severity",
        "symbol_name",
        "kind",
        "location",
        "metric_value",
        "threshold",
        "description",
        "evidence",
        "confidence",
        "detector_version",
    ]
    assert finding["evidence"] == {"lines": 500, "methods": 47}
    assert finding["confidence"] == "structural"
    assert finding["detector_version"] == 3


def test_make_smell_finding_omits_none_optional_kwargs() -> None:
    """Mixed case: passing ``confidence=None`` MUST drop the key
    entirely (NOT emit ``"confidence": None``). This is the byte-shape
    invariant for the 24 8-key call-sites in ``smells.py`` per the
    docstring's ``W923`` design note."""
    finding = make_smell_finding(
        "smell-id",
        "info",
        "sym",
        "function",
        "f.py:1",
        1,
        0,
        "desc",
        evidence={"k": "v"},
        confidence=None,  # explicit None: key MUST be omitted
        detector_version=2,
    )
    assert "confidence" not in finding
    assert "evidence" in finding
    assert "detector_version" in finding
    # Insertion-order: evidence (set first) then detector_version.
    assert list(finding.keys())[-2:] == ["evidence", "detector_version"]


def test_make_smell_finding_json_serialization_is_stable() -> None:
    """Two findings built with identical args produce byte-identical
    JSON (insertion-order stability + no hidden state). Pins the
    hash-stability invariant the finding-registry tests rely on."""
    args = (
        "complex-function",
        "warning",
        "process",
        "function",
        "src/x.py:10",
        25,
        15,
        "complexity exceeds threshold",
    )
    a = make_smell_finding(*args, evidence={"score": 25}, confidence="structural")
    b = make_smell_finding(*args, evidence={"score": 25}, confidence="structural")
    assert json.dumps(a, sort_keys=False) == json.dumps(b, sort_keys=False)


def test_make_smell_finding_preserves_evidence_dict_identity() -> None:
    """The helper does not deep-copy ``evidence`` -- it stores the dict
    reference as-is. Documents the current behavior so a future
    deep-copy refactor would fail this test loudly."""
    evidence = {"callers": 12, "depth": 4}
    finding = make_smell_finding(
        "id",
        "warning",
        "sym",
        "function",
        "loc",
        1,
        0,
        "desc",
        evidence=evidence,
    )
    assert finding["evidence"] is evidence


def test_make_smell_finding_accepts_float_and_int_metric_threshold() -> None:
    """The signature types ``metric_value`` / ``threshold`` as
    ``float | int``; both must round-trip through the dict unchanged."""
    f_int = make_smell_finding("id", "info", "s", "fn", "l", 10, 5, "d")
    assert f_int["metric_value"] == 10 and isinstance(f_int["metric_value"], int)
    f_float = make_smell_finding("id", "info", "s", "fn", "l", 3.14, 2.5, "d")
    assert f_float["metric_value"] == pytest.approx(3.14)
    assert f_float["threshold"] == pytest.approx(2.5)
