"""End-to-end retrieve regression on a synthetic non-self repo.

The 30-task self-bench in ``bench/retrieve/`` measures roam-code on
roam-code's own codebase — friendly by construction. To guard against
the "self-bench overfit" critique, this module spins up a small
synthetic project with a known structure, indexes it via the real
indexer, and runs the retrieve harness against a hand-crafted task
set. The pipeline either delivers reasonable recall on this foreign
shape, or the iter 1–4 changes are coupled to roam-code's specific
layout.

The synthetic project is intentionally generic (no roam-isms): a small
microservice with auth, payments, and notifications modules — a
canonical bench shape used by SWE-bench-style harnesses. If the
domain-noun supplement, file-edge expansion, or path-token boost ever
regress for non-roam codebases, this test fails immediately.

Empirically (2026-05-01, v12.3 commit 2471521): the synthetic repo
returns recall@5 = recall@10 = recall@20 = 1.000 across all 5 tasks.
The thresholds enforced below are tight (R@5 ≥ 0.9, R@20 ≥ 0.95) so
any drop is caught before merge.
"""

from __future__ import annotations

import sqlite3
import subprocess
from pathlib import Path

import pytest

from roam.eval.harness import EvalTask, run_eval

SYNTHETIC_FILES = {
    "src/auth/login.py": '''
"""Authentication module — login + token issuance."""

from __future__ import annotations


def authenticate_user(username: str, password: str) -> bool:
    """Verify a user's password against the stored hash."""
    return _check_password(username, password)


def _check_password(username: str, password: str) -> bool:
    return False


def issue_access_token(user_id: int) -> str:
    """Mint a JWT for the given user."""
    return f"token-for-{user_id}"
''',
    "src/auth/session.py": '''
"""Session lifecycle — create, validate, expire."""

from __future__ import annotations

from src.auth.login import issue_access_token


def create_session(user_id: int) -> dict:
    """Issue a token and persist a session row."""
    token = issue_access_token(user_id)
    return {"user_id": user_id, "token": token}


def expire_session(session_id: str) -> None:
    """Mark a session expired so subsequent token checks fail."""
    pass
''',
    "src/payments/charge.py": '''
"""Payment processing — capture and refund flows."""

from __future__ import annotations


def capture_payment(amount_cents: int, source_token: str) -> str:
    """Authorise + capture a charge in a single round-trip."""
    return f"charge-{amount_cents}-{source_token[:6]}"


def refund_payment(charge_id: str, amount_cents: int | None = None) -> bool:
    """Refund a previously-captured charge."""
    return True
''',
    "src/payments/webhook.py": '''
"""Webhook handlers for payment provider events."""

from __future__ import annotations

from src.payments.charge import capture_payment, refund_payment


def handle_payment_webhook(event: dict) -> str:
    """Route webhook events to capture or refund."""
    if event.get("type") == "charge.refunded":
        refund_payment(event["charge_id"])
        return "refund_handled"
    return "ignored"
''',
    "src/notifications/email.py": '''
"""Outbound email notifications."""

from __future__ import annotations


def send_welcome_email(user_id: int, email_address: str) -> bool:
    """Send the post-signup welcome email."""
    return True


def send_payment_receipt(user_id: int, charge_id: str) -> bool:
    """Send a receipt after a successful payment capture."""
    return True
''',
    "tests/test_login.py": '''
"""Tests for authentication module."""

from src.auth.login import authenticate_user, issue_access_token


def test_authenticate_user_rejects_invalid_password():
    assert authenticate_user("alice", "wrong") is False


def test_issue_access_token_includes_user_id():
    assert "42" in issue_access_token(42)
''',
    "tests/test_charge.py": '''
"""Tests for payment capture + refund."""

from src.payments.charge import capture_payment, refund_payment


def test_capture_payment_returns_charge_id():
    assert capture_payment(1000, "tok_visa_4242") != ""


def test_refund_payment_succeeds():
    assert refund_payment("charge_123") is True
''',
}


@pytest.fixture(scope="module")
def synthetic_repo(tmp_path_factory) -> Path:
    """Build the synthetic repo and index it with ``roam init``."""
    root = tmp_path_factory.mktemp("synth-repo")
    for relpath, content in SYNTHETIC_FILES.items():
        path = root / relpath
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content.lstrip(), encoding="utf-8")

    # Initialise as a git repo so discovery picks up files.
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(["git", "add", "-A"], cwd=root, check=True)
    subprocess.run(
        ["git", "-c", "user.email=t@e.com", "-c", "user.name=t", "commit", "-qm", "init"],
        cwd=root,
        check=True,
    )

    # Index it. Use the real CLI so we exercise the same code path users hit.
    result = subprocess.run(
        ["roam", "init"],
        cwd=root,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, f"roam init failed: {result.stderr}"

    yield root

    # Cleanup is automatic — pytest tmp_path_factory handles it.


CROSS_TASKS = [
    EvalTask(
        task_id="login-flow",
        task="how does user authentication work",
        expected_files=("src/auth/login.py", "tests/test_login.py"),
    ),
    EvalTask(
        task_id="payment-capture",
        task="where is payment capture implemented",
        expected_files=("src/payments/charge.py", "tests/test_charge.py"),
    ),
    EvalTask(
        task_id="webhook-handler",
        task="payment webhook event handler",
        expected_files=("src/payments/webhook.py", "src/payments/charge.py"),
    ),
    EvalTask(
        task_id="session-management",
        task="session creation and expiration",
        expected_files=("src/auth/session.py", "src/auth/login.py"),
    ),
    EvalTask(
        task_id="email-notifications",
        task="welcome email and payment receipt notifications",
        expected_files=("src/notifications/email.py",),
    ),
]


class TestRetrieveOnSyntheticRepo:
    """Smoke + regression: retrieve must work on a foreign codebase.

    Thresholds are tight (R@5 ≥ 0.9, R@10 ≥ 0.95, R@20 ≥ 0.95) — the
    synthetic repo currently returns 1.0/1.0/1.0 so any drop here
    points to a real regression in the iter 1-4 retrieve changes.
    """

    def test_recall_floors_on_synthetic_microservice(self, synthetic_repo: Path):
        db = synthetic_repo / ".roam" / "index.db"
        assert db.is_file(), "index DB missing — roam init failed silently?"
        conn = sqlite3.connect(str(db))
        conn.row_factory = sqlite3.Row
        try:
            per_task, agg = run_eval(conn, CROSS_TASKS)
        finally:
            conn.close()

        assert agg["task_count"] == len(CROSS_TASKS)
        # Tight floors — empirically all three are 1.0 on this repo. If
        # they slip below this, a recent change has degraded retrieval
        # on non-self codebases.
        assert agg["recall_at_5"] >= 0.9, f"recall@5 floor: {agg}"
        assert agg["recall_at_10"] >= 0.95, f"recall@10 floor: {agg}"
        assert agg["recall_at_20"] >= 0.95, f"recall@20 floor: {agg}"

    def test_synthetic_eval_returns_per_task_results(self, synthetic_repo: Path):
        """Per-task structure: each task gets a TaskResult with full
        recall_at coverage. Catches eval-harness shape regressions."""
        db = synthetic_repo / ".roam" / "index.db"
        conn = sqlite3.connect(str(db))
        conn.row_factory = sqlite3.Row
        try:
            per_task, _ = run_eval(conn, CROSS_TASKS)
        finally:
            conn.close()

        assert len(per_task) == len(CROSS_TASKS)
        for r in per_task:
            assert set(r.recall_at.keys()) == {5, 10, 20}
            assert all(0.0 <= v <= 1.0 for v in r.recall_at.values())
            assert r.task_id in {t.task_id for t in CROSS_TASKS}
