"""F4 regression — the intent check ABSTAINS without an explicit ``--intent``.

D1: two zod findings ("PR title says 'add' but diff has no additions") were pure
harness artifacts — the code silently defaulted the intent to HEAD's subject
("chore: add chrome-devtools ...") while the piped diff was "docs: remove ...".
On any ``git show <sha> | roam critique`` sweep HEAD describes a DIFFERENT
commit. F4: no ``--intent`` ⇒ the intent check is skipped, not guessed.
"""

from __future__ import annotations

import sqlite3

from roam.commands.cmd_critique import _run_checks_with_status
from roam.critique.checks import ChangedRegion, ChangedSymbol


def _empty_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("CREATE TABLE clone_pairs (id INTEGER)")
    conn.execute("CREATE TABLE edges (source_id INTEGER, target_id INTEGER, kind TEXT)")
    conn.commit()
    return conn


def test_intent_skipped_without_intent_text() -> None:
    conn = _empty_conn()
    changed = [ChangedSymbol(1, "Bronze", "Bronze", "function", "bronze.tsx", 1, 5)]
    regions = [ChangedRegion("bronze.tsx", ((1, 5),), additions=0, deletions=6)]

    findings, status = _run_checks_with_status(
        conn, changed, regions, high_callers=10, effective_intent=None
    )
    assert status["intent"] == "skipped:no_intent_text"
    assert not any(f.check == "intent" for f in findings)


def test_intent_runs_when_supplied() -> None:
    # With an explicit deletion-only diff + "add" intent, the mismatch DOES fire
    # — the check still works when the caller opts in.
    conn = _empty_conn()
    changed = [ChangedSymbol(1, "Bronze", "Bronze", "function", "bronze.tsx", 1, 5)]
    regions = [ChangedRegion("bronze.tsx", ((1, 5),), additions=0, deletions=6)]

    findings, status = _run_checks_with_status(
        conn, changed, regions, high_callers=10, effective_intent="feat: add a sponsor"
    )
    assert status["intent"] == "ran"
    assert any(f.check == "intent" for f in findings)
