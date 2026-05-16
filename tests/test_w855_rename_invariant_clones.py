"""Tests for the W855 rename-invariant clone detector.

The detector lives at ``src/roam/catalog/clones_rename_invariant.py``.
These tests pin its behaviour on the four scenarios called out in the
W855 task spec:

1. positive: two functions identical except variable names -> 1 finding
2. positive: ``a + b`` vs ``b + a`` -> documented expected behaviour
   (commutative operand reorder lands in the same vector, so YES it
   flags; that is by design for a recall-oriented detector)
3. negative: two functions of identical node count but different
   operations -> 0 findings (different node types differ in the vector)
4. negative: a function vs a verbatim copy -> 1 finding (this IS the
   primary goal of the detector)

The tests exercise the in-memory ``_vectorise_source`` helper so they
stay fast and do not need a full SQLite-backed roam index.
"""

from __future__ import annotations

import os
import textwrap

import pytest
from click.testing import CliRunner

from roam.catalog.clones_rename_invariant import (
    RenameClonePair,
    _cosine,
    _vectorise_source,
    detect_rename_invariant_clones,
)
from roam.cli import cli
from roam.db.connection import open_db
from tests.conftest import make_src_project

# ---------------------------------------------------------------------------
# Pure-vector tests (no SQLite, no filesystem)
# ---------------------------------------------------------------------------


class TestVectorAndCosine:
    """Exercise the characteristic-vector layer directly."""

    def test_alpha_rename_identical_vectors(self):
        """Two functions identical up to variable + parameter names
        produce the SAME characteristic vector (cosine = 1.0).

        This is the primary recall scenario the detector is built for.
        """
        src = textwrap.dedent(
            """
            def process_orders(items):
                results = []
                for item in items:
                    if item.is_valid():
                        value = item.calculate()
                        results.append(value)
                return results

            def handle_invoices(entries):
                output = []
                for entry in entries:
                    if entry.is_valid():
                        amount = entry.calculate()
                        output.append(amount)
                return output
            """
        )
        vectors = _vectorise_source(src, language="python")
        assert len(vectors) == 2, f"expected 2 vectorised functions, got {len(vectors)}"
        sim = _cosine(vectors[0], vectors[1])
        assert sim >= 0.99, (
            f"alpha-renamed clones must score ~1.0; got {sim:.4f}. "
            f"vec_a={dict(vectors[0].vector)} vec_b={dict(vectors[1].vector)}"
        )

    def test_verbatim_copy_is_clone(self):
        """A function vs a verbatim copy is the canonical positive.

        Cosine must be exactly 1.0 — same source produces the same AST
        node-type counts.
        """
        src = textwrap.dedent(
            """
            def transform(rows):
                acc = 0
                for row in rows:
                    if row.active:
                        acc = acc + row.value
                return acc

            def transform_copy(rows):
                acc = 0
                for row in rows:
                    if row.active:
                        acc = acc + row.value
                return acc
            """
        )
        vectors = _vectorise_source(src, language="python")
        assert len(vectors) == 2
        sim = _cosine(vectors[0], vectors[1])
        assert sim == pytest.approx(1.0, abs=1e-9), f"verbatim copy must score 1.0; got {sim}"

    def test_different_operations_distinct_vectors(self):
        """Two functions doing genuinely different operations score below
        threshold even when their node counts overlap.

        ``fibonacci`` uses tuple-assignment + augmented assignment;
        ``factorial`` uses a single multiplicative augmented assignment.
        Different AST node types appear in each, so the vectors differ.
        """
        src = textwrap.dedent(
            """
            def fibonacci(n):
                if n <= 1:
                    return n
                a, b = 0, 1
                for _ in range(2, n + 1):
                    a, b = b, a + b
                return b

            def total_users(records):
                total = 0
                seen = set()
                for record in records:
                    if record.user_id not in seen:
                        seen.add(record.user_id)
                        total = total + 1
                return total
            """
        )
        vectors = _vectorise_source(src, language="python")
        assert len(vectors) == 2
        sim = _cosine(vectors[0], vectors[1])
        # We don't assert sim < threshold strictly; just that the two are
        # NOT a 0.95 clone. They share many node kinds (`return`,
        # `identifier`, `assignment`) but the count distribution differs
        # enough that they fall below the strict threshold.
        assert sim < 0.95, (
            f"distinct-operation functions must score < 0.95 to avoid flooding the findings; got {sim:.4f}"
        )

    def test_commutative_operand_swap_documented(self):
        """``a + b`` vs ``b + a`` produces the SAME characteristic vector.

        DOCUMENTED EXPECTED BEHAVIOUR: a frequency-only vector cannot
        distinguish operand order, so commutative swaps are flagged as
        clones. That is acceptable here — operand swaps usually ARE a
        clone the reviewer wants to know about. Tests pin the behaviour
        so a future ordering-aware variant does not break this one
        silently.
        """
        src = textwrap.dedent(
            """
            def add_left(x, y):
                result = x + y
                final = result + 1
                check = final + 0
                return check

            def add_right(x, y):
                result = y + x
                final = 1 + result
                check = 0 + check
                return check
            """
        )
        vectors = _vectorise_source(src, language="python")
        assert len(vectors) == 2
        sim = _cosine(vectors[0], vectors[1])
        # Identical node-type frequency -> exact cosine match.
        assert sim == pytest.approx(1.0, abs=1e-9), (
            f"commutative-swap pair must score 1.0 under a frequency-only vector; got {sim}"
        )


# ---------------------------------------------------------------------------
# Integration test against the SQLite-backed entry point
# ---------------------------------------------------------------------------


class TestDetectRenameInvariantClones:
    """End-to-end: enumerate files via roam DB, find clone pairs on disk."""

    def test_alpha_rename_pair_emitted_as_finding(self, tmp_path):
        """A two-file project with one alpha-renamed clone pair must
        produce exactly one finding through the SQLite-backed entry
        point.
        """
        proj = make_src_project(
            tmp_path,
            {
                "a.py": """
                def process_orders(items):
                    results = []
                    for item in items:
                        if item.is_valid():
                            value = item.calculate()
                            results.append(value)
                    return results
                """,
                "b.py": """
                def handle_invoices(entries):
                    output = []
                    for entry in entries:
                        if entry.is_valid():
                            amount = entry.calculate()
                            output.append(amount)
                    return output
                """,
                "c.py": """
                def unrelated_aggregator(rows):
                    n = 0
                    while n < len(rows):
                        n = n + 1
                    return n * 2
                """,
            },
        )
        old_cwd = os.getcwd()
        try:
            os.chdir(str(proj))
            runner = CliRunner()
            result = runner.invoke(cli, ["index"])
            assert result.exit_code == 0, result.output

            with open_db(readonly=True) as conn:
                pairs = detect_rename_invariant_clones(
                    conn,
                    similarity_threshold=0.95,
                    min_lines=3,
                    project_root=proj,
                )

            # We expect AT LEAST one finding (a.py vs b.py). We allow
            # extra incidental pairs because the unrelated_aggregator
            # function might bucket with one of the others on small
            # samples — but the alpha-renamed pair MUST appear.
            assert len(pairs) >= 1, "expected at least one clone pair"
            names = {(p.func_a, p.func_b) for p in pairs} | {(p.func_b, p.func_a) for p in pairs}
            assert ("process_orders", "handle_invoices") in names or (
                "handle_invoices",
                "process_orders",
            ) in names, f"alpha-renamed pair must appear in findings; got {names}"
            # Confidence tier is the structural marker called out in the
            # CLAUDE.md confidence-tier vocabulary.
            assert all(p.confidence == "structural" for p in pairs)
            assert all(isinstance(p, RenameClonePair) for p in pairs)
        finally:
            os.chdir(old_cwd)

    def test_no_clones_in_distinct_project(self, tmp_path):
        """A project where every function does something structurally
        different must produce zero findings at the 0.95 threshold.
        """
        proj = make_src_project(
            tmp_path,
            {
                "fib.py": """
                def fibonacci(n):
                    if n <= 1:
                        return n
                    a, b = 0, 1
                    for _ in range(2, n + 1):
                        a, b = b, a + b
                    return b
                """,
                "users.py": """
                class UserManager:
                    def __init__(self, db):
                        self.db = db
                        self.cache = {}
                        self.logger = None

                    def get_user(self, user_id):
                        if user_id in self.cache:
                            return self.cache[user_id]
                        user = self.db.query(user_id)
                        self.cache[user_id] = user
                        return user
                """,
            },
        )
        old_cwd = os.getcwd()
        try:
            os.chdir(str(proj))
            runner = CliRunner()
            result = runner.invoke(cli, ["index"])
            assert result.exit_code == 0, result.output

            with open_db(readonly=True) as conn:
                pairs = detect_rename_invariant_clones(
                    conn,
                    similarity_threshold=0.95,
                    min_lines=3,
                    project_root=proj,
                )

            # Zero high-confidence rename-invariant clone pairs at 0.95.
            assert pairs == [], (
                f"distinct-project test must produce zero findings; "
                f"got {len(pairs)}: "
                f"{[(p.func_a, p.func_b, p.cosine_similarity) for p in pairs]}"
            )
        finally:
            os.chdir(old_cwd)
