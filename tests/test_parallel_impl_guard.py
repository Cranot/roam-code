"""W-SWE: the parallel-implementation over-generalization guard in the compiler.

Proven on SWE-bench django-11138 (Docker-graded): the broad envelope surfaced
3 db backends side-by-side and the agent copied mysql's `!= tzname` conditional
onto oracle, breaking it. The annotation flips that to a pass.
"""

from __future__ import annotations

from roam.plan.compiler import (
    _annotate_parallel_implementations,
    _detect_parallel_impl_groups,
)


def test_detects_three_parallel_backends() -> None:
    facts = {
        "structural": (
            "see django/db/backends/mysql/operations.py and "
            "django/db/backends/oracle/operations.py and "
            "django/db/backends/sqlite3/operations.py"
        )
    }
    groups = _detect_parallel_impl_groups(facts)
    assert any("operations.py" in g for g in groups), groups
    assert any("mysql" in g and "oracle" in g for g in groups), groups


def test_two_siblings_below_threshold() -> None:
    # min_siblings=3 by default: only mysql+oracle => no group.
    facts = {"x": "backends/mysql/base.py backends/oracle/base.py"}
    assert _detect_parallel_impl_groups(facts) == []


def test_ignores_test_dirs() -> None:
    facts = {"x": "tests/cache/tests.py tests/i18n/tests.py tests/servers/tests.py"}
    assert _detect_parallel_impl_groups(facts) == []


def test_annotation_additive_and_idempotent() -> None:
    facts = {"x": ("backends/mysql/base.py backends/oracle/base.py backends/sqlite3/base.py")}
    _annotate_parallel_implementations(facts)
    assert "parallel_implementations" in facts
    assert facts["parallel_implementations_definition"].rstrip(".").endswith("sibling implementations")
    snapshot = dict(facts)
    _annotate_parallel_implementations(facts)  # second call is a no-op
    assert facts == snapshot


def test_no_annotation_when_single_file() -> None:
    facts = {"x": "the bug is in django/core/mail/message.py only"}
    _annotate_parallel_implementations(facts)
    assert "parallel_implementations" not in facts
