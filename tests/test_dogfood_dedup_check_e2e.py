"""End-to-end smoke test for the dogfood-dedup helper.

Runs check_commands() against the LIVE eval corpus on disk and asserts
each dogfood-v2 command lands in the expected verdict bucket. Catches
regressions in either:
  - The helper's classification logic (verdict computation)
  - The on-disk eval corpus (someone deletes a status marker, etc.)

References W39.1's backfill — this test runs AFTER W39.1 lands.

W39.1 (in flight at write-time) is creating eval docs for:
  - auth-gaps  (W36.* fix marker)
  - algo       (W36.* fix marker)
  - stale-refs (W36.1 slugger fix — additional eval on top of W18.1)
  - dead       (Laravel-dead — additional eval on top of W18.3)

Until W39.1 ships, expected verdicts for auth-gaps and algo are 'open'
in the corpus on disk. This test pins the post-W39.1 state, so a failing
test_dogfood_v2_corpus_state with "auth-gaps: expected 'fixed', got 'open'"
is INFORMATIONAL: it tells the next person either to (a) finish the
W39.1-style backfill, or (b) consciously revise this test's expectations.
Do NOT lower the bar to mask a missing eval doc.
"""

from __future__ import annotations

import sys
from pathlib import Path

# Make dev/ importable for these tests (mirrors tests/test_dogfood_dedup_check.py)
_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT / "dev"))


# Dogfood-v2 commands grouped by current expected state.
# W39.1 will populate the W36.* fix_refs for auth-gaps/algo/dead/stale-refs.
DOGFOOD_V2_EXPECTED = {
    # Already-fixed via W18.*
    "sbom": "fixed",
    "stale-refs": "fixed",  # ALSO has W36.1 slugger fix after W39.1
    "dead": "fixed",  # Vue SFC + Laravel-dead after W39.1
    "missing-index": "fixed",
    "over-fetch": "fixed",
    "ws": "fixed",
    "supply-chain": "fixed",
    # W36.x real fixes — should be fixed after W39.1 backfill
    "auth-gaps": "fixed",
    "algo": "fixed",
}


def test_dogfood_v2_corpus_state():
    """Every dogfood-v2 command has an eval marking its post-fix state.

    Failing this test means EITHER:
    1. A new dogfood batch arrived without a corresponding fix → genuine open finding
    2. A fix shipped without an eval-doc → run W39.1-style backfill
    3. An eval-doc was deleted/edited away from a fixed state
    """
    from dogfood_dedup_check import check_commands

    rows = check_commands(list(DOGFOOD_V2_EXPECTED.keys()))
    by_command = {r["command"]: r for r in rows}

    failures = []
    for cmd, expected_verdict in DOGFOOD_V2_EXPECTED.items():
        row = by_command.get(cmd)
        if row is None:
            failures.append(f"{cmd}: dedup helper returned no row")
            continue
        actual = row["verdict"]
        if actual != expected_verdict:
            failures.append(
                f"{cmd}: expected {expected_verdict!r}, got {actual!r} "
                f"(latest_eval={row.get('latest')}, status={row.get('status')})"
            )

    assert not failures, "Dogfood-v2 corpus state drift:\n" + "\n".join(f"  - {f}" for f in failures)


def test_fixed_commands_count_at_least_six():
    """The dogfood-v2 corpus has at least 6 commands with fix_ref evals.

    Regression: someone deleting an eval doc would drop this below 6.
    """
    from dogfood_dedup_check import check_commands

    rows = check_commands(list(DOGFOOD_V2_EXPECTED.keys()))
    fixed = [r for r in rows if r["verdict"] == "fixed"]
    assert len(fixed) >= 6, (
        f"Expected >=6 'fixed' verdicts in dogfood-v2; got {len(fixed)}. "
        f"Missing eval markers for: "
        f"{[r['command'] for r in rows if r['verdict'] != 'fixed']}"
    )


def test_no_findings_in_unknown_state():
    """Every dogfood-v2 command should have at least ONE eval doc on disk.

    A verdict of 'no_evals' (no eval dir found) or 'unknown' (status: null
    AND fix_ref: null) means the eval is missing entirely.
    """
    from dogfood_dedup_check import check_commands

    rows = check_commands(list(DOGFOOD_V2_EXPECTED.keys()))
    missing = [r for r in rows if r["verdict"] in ("unknown", "no_evals")]
    assert not missing, f"Commands with no eval docs at all: {[r['command'] for r in missing]}"
