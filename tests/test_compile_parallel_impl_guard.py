"""Parallel-implementation guard for the freeform compile envelope.

Targets the SWE-django-11138 over-generalization failure mode: a freeform
envelope surfaced db/backends/{mysql,oracle,sqlite3}/operations.py side-by-side
and the agent copied one backend's guard onto another. The compiler now emits a
bounded `parallel_implementations` fact telling the agent to treat each sibling
as independent.
"""

from __future__ import annotations

from roam.plan.compiler import _detect_parallel_implementations as detect


def test_fires_on_three_plus_source_siblings():
    groups = detect(
        [
            "django/db/backends/mysql/operations.py",
            "django/db/backends/oracle/operations.py",
            "django/db/backends/sqlite3/operations.py",
        ]
    )
    assert groups == ["backends/{mysql,oracle,sqlite3}/operations.py"]


def test_no_fire_below_min_siblings():
    assert (
        detect(
            [
                "django/db/backends/mysql/operations.py",
                "django/db/backends/oracle/operations.py",
            ]
        )
        == []
    )


def test_test_and_fixture_parents_denylisted():
    # tests/<name>/tests.py is NOT a parallel SOURCE surface — a fix to one test
    # dir is not blindly copyable to the next; surfacing it added confused
    # exploration on the 11532 win, so it must stay suppressed.
    assert (
        detect(
            [
                "tests/cache/tests.py",
                "tests/queries/tests.py",
                "tests/admin_views/tests.py",
            ]
        )
        == []
    )


def test_mixed_keeps_only_source_siblings():
    groups = detect(
        [
            "django/db/backends/mysql/base.py",
            "django/db/backends/oracle/base.py",
            "django/db/backends/sqlite3/base.py",
            "tests/x/models.py",
            "tests/y/models.py",
        ]
    )
    assert groups == ["backends/{mysql,oracle,sqlite3}/base.py"]


def test_distinct_base_files_are_separate_families():
    groups = detect(
        [
            "p/a/ops.py",
            "p/b/ops.py",
            "p/c/ops.py",
            "p/a/base.py",
            "p/b/base.py",
            "p/c/base.py",
        ]
    )
    assert sorted(groups) == [
        "p/{a,b,c}/base.py",
        "p/{a,b,c}/ops.py",
    ]


def test_ignores_non_string_and_non_matching():
    assert detect([None, 123, "flat.py", "a/b.py"]) == []
