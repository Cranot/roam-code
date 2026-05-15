"""Tests for the W857 parallel-hierarchy detector.

Detector lives in ``src/roam/catalog/parallel_hierarchy.py``. New module
(NOT added to ``smells.py``) to avoid in-flight session-state conflicts.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from roam.catalog.parallel_hierarchy import (
    _jaccard,
    _strip_super_token_overlap,
    _tokenize,
    detect_parallel_hierarchy,
)


# ---------------------------------------------------------------------------
# Tiny in-memory schema — mirrors the subset used by ``tests/test_smells.py``
# ---------------------------------------------------------------------------


def _make_db(tmp_path: Path) -> sqlite3.Connection:
    db_path = tmp_path / ".roam" / "index.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS files (
            id INTEGER PRIMARY KEY, path TEXT NOT NULL UNIQUE,
            language TEXT
        );
        CREATE TABLE IF NOT EXISTS symbols (
            id INTEGER PRIMARY KEY, file_id INTEGER NOT NULL,
            name TEXT NOT NULL, qualified_name TEXT, kind TEXT NOT NULL,
            line_start INTEGER, line_end INTEGER, parent_id INTEGER,
            FOREIGN KEY(file_id) REFERENCES files(id)
        );
        CREATE TABLE IF NOT EXISTS edges (
            id INTEGER PRIMARY KEY, source_id INTEGER NOT NULL,
            target_id INTEGER NOT NULL, kind TEXT NOT NULL,
            FOREIGN KEY(source_id) REFERENCES symbols(id),
            FOREIGN KEY(target_id) REFERENCES symbols(id)
        );
    """)
    conn.commit()
    return conn


_NEXT_ID = {"v": 1}


def _next_id() -> int:
    i = _NEXT_ID["v"]
    _NEXT_ID["v"] = i + 1
    return i


def _add_file(conn: sqlite3.Connection, path: str) -> int:
    fid = _next_id()
    conn.execute("INSERT INTO files (id, path, language) VALUES (?, ?, 'python')", (fid, path))
    return fid


def _add_class(
    conn: sqlite3.Connection,
    *,
    file_id: int,
    name: str,
    line: int = 1,
) -> int:
    sid = _next_id()
    conn.execute(
        "INSERT INTO symbols (id, file_id, name, kind, line_start, line_end) "
        "VALUES (?, ?, ?, 'class', ?, ?)",
        (sid, file_id, name, line, line + 10),
    )
    return sid


def _inherits(conn: sqlite3.Connection, child_id: int, parent_id: int) -> None:
    conn.execute(
        "INSERT INTO edges (source_id, target_id, kind) VALUES (?, ?, 'inherits')",
        (child_id, parent_id),
    )


# ---------------------------------------------------------------------------
# Unit tests: tokenizer + jaccard primitives
# ---------------------------------------------------------------------------


class TestTokenizer:
    def test_camel_case(self) -> None:
        assert _tokenize("EmployeeUSPayroll") == {"employee", "us", "payroll"}

    def test_snake_case(self) -> None:
        assert _tokenize("savings_account") == {"savings", "account"}

    def test_empty(self) -> None:
        assert _tokenize("") == set()

    def test_mixed(self) -> None:
        toks = _tokenize("HTTPClientV2")
        # HTTP, Client, V, 2 - lowercased
        assert "http" in toks
        assert "client" in toks


class TestJaccard:
    def test_identical(self) -> None:
        assert _jaccard({"a", "b"}, {"a", "b"}) == 1.0

    def test_disjoint(self) -> None:
        assert _jaccard({"a", "b"}, {"c", "d"}) == 0.0

    def test_partial(self) -> None:
        # {a,b} ∩ {a,c} = {a}, union = {a,b,c}, => 1/3
        assert _jaccard({"a", "b"}, {"a", "c"}) == pytest.approx(1 / 3)

    def test_both_empty(self) -> None:
        assert _jaccard(set(), set()) == 0.0


class TestStripSuperToken:
    def test_strips_parent_tokens(self) -> None:
        # subclass "EmployeeUS" with parent "Employee" -> {us}
        sub = _tokenize("EmployeeUS")
        sup = _tokenize("Employee")
        assert _strip_super_token_overlap(sub, sup) == {"us"}


# ---------------------------------------------------------------------------
# Integration tests: detector on synthetic DB
# ---------------------------------------------------------------------------


class TestParallelHierarchyDetector:
    def setup_method(self) -> None:
        _NEXT_ID["v"] = 1

    def test_empty_db_no_findings(self, tmp_path: Path) -> None:
        """Negative: empty DB — no findings."""
        conn = _make_db(tmp_path)
        assert detect_parallel_hierarchy(conn) == []
        conn.close()

    def test_single_subclass_per_super_no_finding(self, tmp_path: Path) -> None:
        """Negative: each superclass has only one subclass — not a hierarchy."""
        conn = _make_db(tmp_path)
        f = _add_file(conn, "src/m.py")
        emp = _add_class(conn, file_id=f, name="Employee")
        emp_us = _add_class(conn, file_id=f, name="EmployeeUS")
        pay = _add_class(conn, file_id=f, name="Payroll")
        pay_us = _add_class(conn, file_id=f, name="PayrollUS")
        _inherits(conn, emp_us, emp)
        _inherits(conn, pay_us, pay)
        conn.commit()
        assert detect_parallel_hierarchy(conn) == []
        conn.close()

    def test_no_token_overlap_no_finding(self, tmp_path: Path) -> None:
        """Negative: two unrelated 3-class hierarchies with no marker overlap."""
        conn = _make_db(tmp_path)
        f = _add_file(conn, "src/m.py")

        animal = _add_class(conn, file_id=f, name="Animal")
        dog = _add_class(conn, file_id=f, name="AnimalDog")
        cat = _add_class(conn, file_id=f, name="AnimalCat")
        bird = _add_class(conn, file_id=f, name="AnimalBird")
        _inherits(conn, dog, animal)
        _inherits(conn, cat, animal)
        _inherits(conn, bird, animal)

        vehicle = _add_class(conn, file_id=f, name="Vehicle")
        car = _add_class(conn, file_id=f, name="VehicleSedan")
        truck = _add_class(conn, file_id=f, name="VehicleTruck")
        bike = _add_class(conn, file_id=f, name="VehicleMotorcycle")
        _inherits(conn, car, vehicle)
        _inherits(conn, truck, vehicle)
        _inherits(conn, bike, vehicle)
        conn.commit()

        assert detect_parallel_hierarchy(conn) == []
        conn.close()

    def test_employee_payroll_parallel(self, tmp_path: Path) -> None:
        """Positive: Employee{US,UK} mirrored by Payroll{US,UK}."""
        conn = _make_db(tmp_path)
        f = _add_file(conn, "src/hr.py")

        emp = _add_class(conn, file_id=f, name="Employee")
        emp_us = _add_class(conn, file_id=f, name="EmployeeUS")
        emp_uk = _add_class(conn, file_id=f, name="EmployeeUK")
        _inherits(conn, emp_us, emp)
        _inherits(conn, emp_uk, emp)

        pay = _add_class(conn, file_id=f, name="Payroll")
        pay_us = _add_class(conn, file_id=f, name="PayrollUS")
        pay_uk = _add_class(conn, file_id=f, name="PayrollUK")
        _inherits(conn, pay_us, pay)
        _inherits(conn, pay_uk, pay)

        conn.commit()
        findings = detect_parallel_hierarchy(conn)
        assert len(findings) == 1
        f0 = findings[0]
        assert f0["smell_id"] == "parallel-hierarchy"
        assert f0["confidence"] == "structural"
        ev = f0["evidence"]
        # us+uk are the shared markers (employee/payroll stripped as parent toks)
        assert set(ev["shared_markers"]) == {"us", "uk"}
        assert ev["jaccard"] == pytest.approx(1.0)
        assert ev["cochange_confirmed"] is None
        conn.close()

    def test_account_bank_three_way_parallel(self, tmp_path: Path) -> None:
        """Positive: Account{Savings,Checking} mirrored by Bank{Savings,Checking}."""
        conn = _make_db(tmp_path)
        f = _add_file(conn, "src/finance.py")

        acct = _add_class(conn, file_id=f, name="Account")
        sav_a = _add_class(conn, file_id=f, name="SavingsAccount")
        chk_a = _add_class(conn, file_id=f, name="CheckingAccount")
        _inherits(conn, sav_a, acct)
        _inherits(conn, chk_a, acct)

        bank = _add_class(conn, file_id=f, name="Bank")
        sav_b = _add_class(conn, file_id=f, name="SavingsBank")
        chk_b = _add_class(conn, file_id=f, name="CheckingBank")
        _inherits(conn, sav_b, bank)
        _inherits(conn, chk_b, bank)
        conn.commit()

        findings = detect_parallel_hierarchy(conn)
        assert len(findings) == 1
        ev = findings[0]["evidence"]
        assert set(ev["shared_markers"]) == {"savings", "checking"}
        assert ev["jaccard"] >= 0.7
        conn.close()

    def test_threshold_respected(self, tmp_path: Path) -> None:
        """Tuning: a very high threshold rejects a partial match."""
        conn = _make_db(tmp_path)
        f = _add_file(conn, "src/m.py")
        emp = _add_class(conn, file_id=f, name="Emp")
        emp_us = _add_class(conn, file_id=f, name="EmpUS")
        emp_uk = _add_class(conn, file_id=f, name="EmpUK")
        _inherits(conn, emp_us, emp)
        _inherits(conn, emp_uk, emp)

        pay = _add_class(conn, file_id=f, name="Pay")
        pay_us = _add_class(conn, file_id=f, name="PayUS")
        # Second pair: PayDE — only shares US with EmpUS, not UK with EmpUK.
        pay_de = _add_class(conn, file_id=f, name="PayDE")
        _inherits(conn, pay_us, pay)
        _inherits(conn, pay_de, pay)

        conn.commit()
        # Threshold 0.9 should reject (only 1 marker shared out of 3: us)
        findings = detect_parallel_hierarchy(conn, jaccard_threshold=0.9)
        assert findings == []
        conn.close()

    def test_extends_edge_kind_also_recognized(self, tmp_path: Path) -> None:
        """Detector accepts ``kind='extends'`` as well as ``'inherits'``."""
        conn = _make_db(tmp_path)
        f = _add_file(conn, "src/m.py")
        emp = _add_class(conn, file_id=f, name="Employee")
        emp_us = _add_class(conn, file_id=f, name="EmployeeUS")
        emp_uk = _add_class(conn, file_id=f, name="EmployeeUK")
        conn.execute("INSERT INTO edges (source_id, target_id, kind) VALUES (?, ?, 'extends')", (emp_us, emp))
        conn.execute("INSERT INTO edges (source_id, target_id, kind) VALUES (?, ?, 'extends')", (emp_uk, emp))

        pay = _add_class(conn, file_id=f, name="Payroll")
        pay_us = _add_class(conn, file_id=f, name="PayrollUS")
        pay_uk = _add_class(conn, file_id=f, name="PayrollUK")
        conn.execute("INSERT INTO edges (source_id, target_id, kind) VALUES (?, ?, 'extends')", (pay_us, pay))
        conn.execute("INSERT INTO edges (source_id, target_id, kind) VALUES (?, ?, 'extends')", (pay_uk, pay))

        conn.commit()
        findings = detect_parallel_hierarchy(conn)
        assert len(findings) == 1
        conn.close()
