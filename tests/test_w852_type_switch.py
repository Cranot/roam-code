"""W852 — type-switch / polymorphism-rejection smell detector.

Catches functions that dispatch on the runtime type of an input via
``isinstance`` / ``type(x) is T`` / ``match x: case ClassName(...):``
arms against >=N concrete classes. Canonical OCP violation; the
remediation is Strategy / Visitor / ``functools.singledispatch``.

Detector module: ``src/roam/catalog/type_switch.py``.
Wired into ``src/roam/catalog/smells.py::ALL_DETECTORS``.
"""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path

from roam.catalog.smells import ALL_DETECTORS
from roam.catalog.type_switch import (
    TYPE_SWITCH_DETECTOR,
    TYPE_SWITCH_DETECTOR_VERSION,
    _classes_from_isinstance,
    _classname_from_node,
    _file_is_test,
    _is_concrete_class_name,
    detect_type_switch,
)


# ---------------------------------------------------------------------------
# DB fixture — mirrors test_smells.py's ``_make_db`` (subset)
# ---------------------------------------------------------------------------


def _make_db(tmp_path: Path) -> sqlite3.Connection:
    db_path = tmp_path / ".roam" / "index.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS files (
            id INTEGER PRIMARY KEY, path TEXT NOT NULL UNIQUE,
            language TEXT, file_role TEXT DEFAULT 'source'
        );
        CREATE TABLE IF NOT EXISTS symbols (
            id INTEGER PRIMARY KEY, file_id INTEGER NOT NULL,
            name TEXT NOT NULL, qualified_name TEXT, kind TEXT NOT NULL,
            signature TEXT, line_start INTEGER, line_end INTEGER,
            docstring TEXT, visibility TEXT DEFAULT 'public',
            is_exported INTEGER DEFAULT 1, parent_id INTEGER,
            FOREIGN KEY(file_id) REFERENCES files(id)
        );
        CREATE TABLE IF NOT EXISTS edges (
            id INTEGER PRIMARY KEY, source_id INTEGER NOT NULL,
            target_id INTEGER NOT NULL, kind TEXT NOT NULL DEFAULT 'call'
        );
        """
    )
    return conn


def _wire_file(
    tmp_path: Path,
    conn: sqlite3.Connection,
    rel_path: str,
    source: str,
    *,
    enclosing: tuple[str, str, int, int] | None = None,
) -> int:
    """Write ``rel_path`` under ``tmp_path``, insert into ``files`` table.

    ``enclosing`` is ``(name, kind, line_start, line_end)`` for the
    function the detector should attribute findings to. When ``None``,
    no enclosing symbol row is inserted (the detector then falls back
    to the AST function name + a heuristic kind).
    """
    full = tmp_path / rel_path
    full.parent.mkdir(parents=True, exist_ok=True)
    full.write_text(source, encoding="utf-8")
    cur = conn.execute(
        "INSERT INTO files (path, language, file_role) VALUES (?, 'python', 'source')",
        (rel_path,),
    )
    file_id = cur.lastrowid
    if enclosing is not None:
        name, kind, ls, le = enclosing
        conn.execute(
            "INSERT INTO symbols (file_id, name, kind, line_start, line_end) "
            "VALUES (?, ?, ?, ?, ?)",
            (file_id, name, kind, ls, le),
        )
    conn.commit()
    # Mark tmp_path as a git root so find_project_root() returns it.
    (tmp_path / ".git").mkdir(exist_ok=True)
    return file_id


def _run(tmp_path: Path, conn: sqlite3.Connection, **kw) -> list[dict]:
    """Chdir into ``tmp_path`` so ``find_project_root()`` resolves there."""
    old_cwd = os.getcwd()
    try:
        os.chdir(str(tmp_path))
        return detect_type_switch(conn, **kw)
    finally:
        os.chdir(old_cwd)


# ---------------------------------------------------------------------------
# Unit tests: small helpers
# ---------------------------------------------------------------------------


class TestPrimitiveAllowlist:
    def test_int_is_primitive(self):
        assert not _is_concrete_class_name("int")

    def test_str_is_primitive(self):
        assert not _is_concrete_class_name("str")

    def test_dict_is_primitive(self):
        assert not _is_concrete_class_name("dict")

    def test_capitalised_classname_is_concrete(self):
        assert _is_concrete_class_name("Cat")

    def test_dotted_terminal_classname_is_concrete(self):
        assert _is_concrete_class_name("models.Cat")

    def test_dotted_primitive_terminal_is_not(self):
        # `typing.Optional` -> terminal `Optional` -> primitive (typing helper).
        assert not _is_concrete_class_name("typing.Optional")

    def test_lowercase_terminal_is_not_concrete(self):
        assert not _is_concrete_class_name("my_helper")

    def test_empty_string_is_not_concrete(self):
        assert not _is_concrete_class_name("")

    def test_none_is_not_concrete(self):
        assert not _is_concrete_class_name(None)


class TestFileIsTest:
    def test_tests_dir(self):
        assert _file_is_test("tests/test_foo.py")

    def test_nested_tests_dir(self):
        assert _file_is_test("src/pkg/tests/test_bar.py")

    def test_test_dir(self):
        assert _file_is_test("test/test_foo.py")

    def test_conftest(self):
        assert _file_is_test("conftest.py")

    def test_nested_conftest(self):
        assert _file_is_test("src/pkg/conftest.py")

    def test_normal_source(self):
        assert not _file_is_test("src/widget.py")

    def test_windows_separator(self):
        assert _file_is_test("tests\\test_foo.py")


# ---------------------------------------------------------------------------
# Integration tests: detector against synthetic Python files
# ---------------------------------------------------------------------------


class TestTypeSwitchDetector:
    def test_empty_corpus_no_findings(self, tmp_path):
        """Empty DB -> empty list, no exception (Pattern 1 always-emit)."""
        conn = _make_db(tmp_path)
        assert _run(tmp_path, conn) == []
        conn.close()

    def test_three_isinstance_arms_flagged(self, tmp_path):
        """Canonical positive: 3 isinstance arms on same discriminator -> finding."""
        conn = _make_db(tmp_path)
        src = (
            "class Cat: pass\n"
            "class Dog: pass\n"
            "class Bird: pass\n"
            "\n"
            "def speak(animal):\n"
            "    if isinstance(animal, Cat):\n"
            "        return 'meow'\n"
            "    elif isinstance(animal, Dog):\n"
            "        return 'woof'\n"
            "    elif isinstance(animal, Bird):\n"
            "        return 'tweet'\n"
            "    return '?'\n"
        )
        _wire_file(
            tmp_path, conn, "src/zoo.py", src,
            enclosing=("speak", "function", 5, 12),
        )
        findings = _run(tmp_path, conn)
        assert len(findings) == 1
        f = findings[0]
        assert f["smell_id"] == TYPE_SWITCH_DETECTOR
        assert f["severity"] == "warning"
        assert f["symbol_name"] == "speak"
        assert f["metric_value"] == 3
        assert f["threshold"] == 3
        assert f["confidence"] == "structural"
        assert f["detector_version"] == TYPE_SWITCH_DETECTOR_VERSION
        ev = f["evidence"]
        assert ev["discriminator"] == "animal"
        assert ev["class_arms"] == ["Bird", "Cat", "Dog"]
        assert ev["check_kind"] == "isinstance"
        # LAW 4 anchor — terminal noun is concrete.
        assert "arms" in f["description"]
        conn.close()

    def test_two_arms_not_flagged(self, tmp_path):
        """Only 2 distinct classes -> below threshold -> no finding."""
        conn = _make_db(tmp_path)
        src = (
            "class Cat: pass\n"
            "class Dog: pass\n"
            "\n"
            "def speak(animal):\n"
            "    if isinstance(animal, Cat):\n"
            "        return 'meow'\n"
            "    elif isinstance(animal, Dog):\n"
            "        return 'woof'\n"
            "    return '?'\n"
        )
        _wire_file(
            tmp_path, conn, "src/zoo.py", src,
            enclosing=("speak", "function", 4, 9),
        )
        assert _run(tmp_path, conn) == []
        conn.close()

    def test_primitive_isinstance_not_flagged(self, tmp_path):
        """isinstance on int/str/float -> primitive allowlist -> no finding."""
        conn = _make_db(tmp_path)
        src = (
            "def coerce(x):\n"
            "    if isinstance(x, int):\n"
            "        return float(x)\n"
            "    elif isinstance(x, str):\n"
            "        return x\n"
            "    elif isinstance(x, float):\n"
            "        return x\n"
            "    return None\n"
        )
        _wire_file(
            tmp_path, conn, "src/coerce.py", src,
            enclosing=("coerce", "function", 1, 8),
        )
        assert _run(tmp_path, conn) == []
        conn.close()

    def test_singledispatch_register_not_flagged(self, tmp_path):
        """@singledispatch register callsites are the polymorphic FIX, not the smell."""
        conn = _make_db(tmp_path)
        src = (
            "from functools import singledispatch\n"
            "\n"
            "class Cat: pass\n"
            "class Dog: pass\n"
            "class Bird: pass\n"
            "\n"
            "@singledispatch\n"
            "def speak(animal):\n"
            "    return '?'\n"
            "\n"
            "def configure():\n"
            "    # The function still uses isinstance for an audit log,\n"
            "    # but the polymorphic alternative IS in use here.\n"
            "    speak.register(Cat)\n"
            "    speak.register(Dog)\n"
            "    speak.register(Bird)\n"
            "    if isinstance(speak, Cat):\n"
            "        pass\n"
            "    elif isinstance(speak, Dog):\n"
            "        pass\n"
            "    elif isinstance(speak, Bird):\n"
            "        pass\n"
        )
        _wire_file(
            tmp_path, conn, "src/dispatch.py", src,
            enclosing=("configure", "function", 11, 22),
        )
        assert _run(tmp_path, conn) == []
        conn.close()

    def test_match_case_flagged(self, tmp_path):
        """``match x: case Cat(): case Dog(): case Bird():`` -> finding."""
        conn = _make_db(tmp_path)
        src = (
            "class Cat:\n"
            "    __match_args__ = ()\n"
            "class Dog:\n"
            "    __match_args__ = ()\n"
            "class Bird:\n"
            "    __match_args__ = ()\n"
            "\n"
            "def speak(animal):\n"
            "    match animal:\n"
            "        case Cat():\n"
            "            return 'meow'\n"
            "        case Dog():\n"
            "            return 'woof'\n"
            "        case Bird():\n"
            "            return 'tweet'\n"
            "    return '?'\n"
        )
        _wire_file(
            tmp_path, conn, "src/zoo_match.py", src,
            enclosing=("speak", "function", 8, 16),
        )
        findings = _run(tmp_path, conn)
        assert len(findings) == 1
        f = findings[0]
        assert f["smell_id"] == TYPE_SWITCH_DETECTOR
        ev = f["evidence"]
        assert ev["check_kind"] == "match_case"
        assert ev["discriminator"] == "animal"
        assert ev["class_arms"] == ["Bird", "Cat", "Dog"]
        conn.close()

    def test_type_eq_flagged(self, tmp_path):
        """``if type(x) is Cat: elif type(x) is Dog: elif type(x) is Bird:`` -> finding."""
        conn = _make_db(tmp_path)
        src = (
            "class Cat: pass\n"
            "class Dog: pass\n"
            "class Bird: pass\n"
            "\n"
            "def speak(animal):\n"
            "    if type(animal) is Cat:\n"
            "        return 'meow'\n"
            "    elif type(animal) == Dog:\n"
            "        return 'woof'\n"
            "    elif type(animal) is Bird:\n"
            "        return 'tweet'\n"
            "    return '?'\n"
        )
        _wire_file(
            tmp_path, conn, "src/zoo_typeeq.py", src,
            enclosing=("speak", "function", 5, 12),
        )
        findings = _run(tmp_path, conn)
        # Detector emits one finding per (discriminator, check_kind) bucket.
        assert len(findings) == 1
        f = findings[0]
        assert f["evidence"]["check_kind"] == "type_eq"
        assert f["evidence"]["discriminator"] == "animal"
        assert sorted(f["evidence"]["class_arms"]) == ["Bird", "Cat", "Dog"]
        conn.close()

    def test_test_file_skipped(self, tmp_path):
        """Type-switch in ``tests/test_foo.py`` -> no finding (test-skip rule)."""
        conn = _make_db(tmp_path)
        src = (
            "class Cat: pass\n"
            "class Dog: pass\n"
            "class Bird: pass\n"
            "\n"
            "def speak(animal):\n"
            "    if isinstance(animal, Cat):\n"
            "        return 'meow'\n"
            "    elif isinstance(animal, Dog):\n"
            "        return 'woof'\n"
            "    elif isinstance(animal, Bird):\n"
            "        return 'tweet'\n"
            "    return '?'\n"
        )
        _wire_file(
            tmp_path, conn, "tests/test_zoo.py", src,
            enclosing=("speak", "function", 5, 12),
        )
        assert _run(tmp_path, conn) == []
        conn.close()

    def test_tuple_isinstance_counted_as_arms(self, tmp_path):
        """``isinstance(x, (Cat, Dog, Bird))`` -> 3 arms in one tuple -> finding."""
        conn = _make_db(tmp_path)
        src = (
            "class Cat: pass\n"
            "class Dog: pass\n"
            "class Bird: pass\n"
            "\n"
            "def speak(animal):\n"
            "    if isinstance(animal, (Cat, Dog, Bird)):\n"
            "        return 'noise'\n"
            "    return '?'\n"
        )
        _wire_file(
            tmp_path, conn, "src/zoo_tuple.py", src,
            enclosing=("speak", "function", 5, 8),
        )
        findings = _run(tmp_path, conn)
        assert len(findings) == 1
        assert findings[0]["evidence"]["class_arms"] == ["Bird", "Cat", "Dog"]
        conn.close()

    def test_distinct_discriminators_not_conflated(self, tmp_path):
        """isinstance(x, Cat) / isinstance(y, Dog) / isinstance(z, Bird) -> no finding.

        Three different discriminators, each with one class arm, do NOT
        constitute a type-switch.
        """
        conn = _make_db(tmp_path)
        src = (
            "class Cat: pass\n"
            "class Dog: pass\n"
            "class Bird: pass\n"
            "\n"
            "def speak(x, y, z):\n"
            "    if isinstance(x, Cat):\n"
            "        return 'meow'\n"
            "    if isinstance(y, Dog):\n"
            "        return 'woof'\n"
            "    if isinstance(z, Bird):\n"
            "        return 'tweet'\n"
            "    return '?'\n"
        )
        _wire_file(
            tmp_path, conn, "src/zoo_multi.py", src,
            enclosing=("speak", "function", 5, 12),
        )
        assert _run(tmp_path, conn) == []
        conn.close()

    def test_threshold_kwarg_tunable(self, tmp_path):
        """``min_class_arms=4`` rejects a 3-arm function; default flags it."""
        conn = _make_db(tmp_path)
        src = (
            "class Cat: pass\n"
            "class Dog: pass\n"
            "class Bird: pass\n"
            "\n"
            "def speak(animal):\n"
            "    if isinstance(animal, Cat):\n"
            "        return 'meow'\n"
            "    elif isinstance(animal, Dog):\n"
            "        return 'woof'\n"
            "    elif isinstance(animal, Bird):\n"
            "        return 'tweet'\n"
            "    return '?'\n"
        )
        _wire_file(
            tmp_path, conn, "src/zoo.py", src,
            enclosing=("speak", "function", 5, 12),
        )
        assert _run(tmp_path, conn, min_class_arms=4) == []
        assert len(_run(tmp_path, conn, min_class_arms=3)) == 1
        conn.close()

    def test_syntax_error_file_skipped(self, tmp_path):
        """Files that fail ``ast.parse`` are silently skipped, not crashed."""
        conn = _make_db(tmp_path)
        _wire_file(tmp_path, conn, "src/broken.py", "def x(:\n", enclosing=None)
        # No exception, just empty findings on this file.
        assert _run(tmp_path, conn) == []
        conn.close()

    def test_missing_file_skipped(self, tmp_path):
        """File row exists in DB but disk file missing -> silently skipped."""
        conn = _make_db(tmp_path)
        # Insert a files row without writing the actual file.
        conn.execute(
            "INSERT INTO files (path, language, file_role) "
            "VALUES ('src/ghost.py', 'python', 'source')"
        )
        conn.commit()
        (tmp_path / ".git").mkdir(exist_ok=True)
        old_cwd = os.getcwd()
        try:
            os.chdir(str(tmp_path))
            assert detect_type_switch(conn) == []
        finally:
            os.chdir(old_cwd)
        conn.close()


# ---------------------------------------------------------------------------
# Registry wiring
# ---------------------------------------------------------------------------


class TestRegistryWiring:
    def test_registered_in_all_detectors(self):
        smell_ids = [smell_id for smell_id, _fn in ALL_DETECTORS]
        assert "type-switch" in smell_ids

    def test_detector_callable_matches_module(self):
        for smell_id, fn in ALL_DETECTORS:
            if smell_id == "type-switch":
                assert fn is detect_type_switch
                return
        assert False, "type-switch missing from ALL_DETECTORS"


# ---------------------------------------------------------------------------
# Smoke test on the AST helpers (defensive — these are the load-bearing parts)
# ---------------------------------------------------------------------------


class TestClassesFromIsinstance:
    def _parse_call(self, src: str):
        import ast
        tree = ast.parse(src, mode="eval")
        return tree.body

    def test_single_class_arg(self):
        call = self._parse_call("isinstance(x, Cat)")
        assert _classes_from_isinstance(call) == ["Cat"]

    def test_tuple_class_arg(self):
        call = self._parse_call("isinstance(x, (Cat, Dog, Bird))")
        assert _classes_from_isinstance(call) == ["Cat", "Dog", "Bird"]

    def test_attribute_class_arg(self):
        call = self._parse_call("isinstance(x, models.Cat)")
        assert _classes_from_isinstance(call) == ["models.Cat"]

    def test_non_isinstance_call(self):
        call = self._parse_call("issubclass(x, Cat)")
        assert _classes_from_isinstance(call) == []

    def test_classname_from_node_call_type_none(self):
        import ast
        tree = ast.parse("type(None)", mode="eval")
        assert _classname_from_node(tree.body) == "NoneType"
