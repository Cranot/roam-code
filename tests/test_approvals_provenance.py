"""W293 — tests for approvals provenance wiring.

The W293 directive wires the W282 :func:`provenance_label` helper onto
the approval-dict wire shape so each approval records WHICH data channel
produced it. Approvals stay raw dicts in
``ChangeEvidence.approvals`` (no typed dataclass round-trip in the
collector path), so the stamp lives at a TOP-LEVEL ``provenance`` key on
the dict (matching the wire form ``PolicyDecision.to_dict()`` emits for
its own provenance attribution).

Channel-to-provenance mapping:

* ``roam pr-bundle add-approval`` (CLI ingestion) -> ``cli_flag``
* W247b APPROVED-on-head from GitHub PR review   -> ``producer_envelope(github_review)``
* Legacy / no-source-signal dict at collector    -> ``unknown``

Constraints pinned here:

* ``approval["provenance"]`` is present on every approval row after
  collection (Pattern-2 always-emit).
* Producer-stamped ``provenance`` MUST NOT be overwritten by the
  collector fallback.
* Every emitted provenance value MUST live in the closed
  :data:`PROVENANCE_SOURCES` frozenset (drift guard at module end).
* Bodies / body_text / body_html MUST NOT travel through the
  provenance hop (W247a guardrail still holds).
"""

from __future__ import annotations

import json as _json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
from click.testing import CliRunner  # noqa: E402
from conftest import git_init, invoke_cli  # noqa: E402

from roam.evidence import PROVENANCE_SOURCES  # noqa: E402
from roam.evidence.collector import collect_change_evidence  # noqa: E402
from roam.evidence.github_reviews import parse_github_reviews  # noqa: E402

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _provenance_base(label: str) -> str:
    """Strip the optional ``"(detail)"`` suffix from a provenance label."""
    return label.split("(", 1)[0]


@pytest.fixture
def bundle_project(tmp_path, monkeypatch):
    """Minimal git-initialised project for pr-bundle CLI tests."""
    proj = tmp_path / "w293_proj"
    proj.mkdir()
    (proj / ".gitignore").write_text(".roam/\n")
    (proj / "app.py").write_text("def main():\n    return 0\n")
    git_init(proj)
    monkeypatch.delenv("ROAM_RUN_ID", raising=False)
    return proj


# ---------------------------------------------------------------------------
# W240 pr-bundle add-approval CLI -> cli_flag
# ---------------------------------------------------------------------------


def test_add_approval_cli_stamps_cli_flag(bundle_project):
    """``roam pr-bundle add-approval`` records ``provenance="cli_flag"``."""
    runner = CliRunner()
    init = invoke_cli(
        runner,
        ["pr-bundle", "init", "--intent", "w293 approval-provenance test"],
        cwd=bundle_project,
    )
    assert init.exit_code == 0, init.output

    result = invoke_cli(
        runner,
        [
            "pr-bundle",
            "add-approval",
            "--approver",
            "human:alice@example.com",
            "--scope",
            "pr-42",
            "--reason",
            "looks good",
            "--id",
            "appr_w293_001",
        ],
        cwd=bundle_project,
    )
    assert result.exit_code == 0, result.output

    bundle_path = bundle_project / ".roam" / "pr-bundles"
    # The bundle filename depends on the current branch; just read whatever
    # exists in the directory.
    candidates = list(bundle_path.glob("*.json"))
    assert candidates, "expected a bundle file on disk"
    bundle = _json.loads(candidates[0].read_text(encoding="utf-8"))
    approvals = bundle.get("approvals") or []
    assert approvals, "bundle has no approvals"
    target = next(
        (a for a in approvals if a.get("approval_id") == "appr_w293_001"),
        None,
    )
    assert target is not None, f"expected approval row absent: {approvals!r}"
    assert target.get("provenance") == "cli_flag", f"add-approval did not stamp cli_flag provenance: {target!r}"


# ---------------------------------------------------------------------------
# W247b GitHub review APPROVED-on-head -> producer_envelope(github_review)
# ---------------------------------------------------------------------------


def test_github_review_approved_stamps_producer_envelope_github_review() -> None:
    """APPROVED-on-head review flattens with
    ``provenance="producer_envelope(github_review)"``.
    """
    # Lazy import: keeps the module-level imports tiny.
    from roam.commands.cmd_pr_replay import _approval_record_to_envelope_dict

    head = "deadbeef" * 5  # 40 chars
    reviews: list[dict] = [
        {
            "id": 9876,
            "state": "APPROVED",
            "user": {"login": "alice"},
            "submitted_at": "2026-05-14T10:00:00Z",
            "commit_id": head,
            "html_url": "https://example.com/pr/42#review-9876",
            "body": "sensitive private info",
        },
    ]
    approvals, _, _ = parse_github_reviews(
        reviews=reviews,
        head_commit_sha=head,
        pr_number=42,
    )
    assert approvals, "expected APPROVED-on-head row"
    wire = _approval_record_to_envelope_dict(approvals[0])
    assert wire.get("provenance") == "producer_envelope(github_review)", f"github approval missing provenance: {wire!r}"
    # Body MUST NOT leak through the provenance hop (W247a guardrail).
    assert "sensitive private info" not in _json.dumps(wire), f"review body leaked through provenance hop: {wire!r}"


# ---------------------------------------------------------------------------
# Collector fallback + preserve-existing discipline
# ---------------------------------------------------------------------------


def test_legacy_approval_dict_gets_unknown_provenance_at_collector() -> None:
    """A legacy approval dict without ``provenance`` lands with
    ``"unknown"`` at the collector's Pattern-2 always-emit fallback.
    """
    bundle_env = {
        "approvals": [
            {
                "approval_id": "appr_legacy_001",
                "approver": "human:bob@example.com",
                "scope": "pr-99",
                "reason": "manual import, no producer stamp",
                "recorded_at": "2026-05-14T00:00:00Z",
                # No 'provenance' key
            },
        ],
    }
    packet, _ = collect_change_evidence(
        pr_bundle_envelope=bundle_env,
        repo_id="github.com/example/repo",
        commit_sha="0" * 40,
    )
    matching = [a for a in packet.approvals if a.get("approval_id") == "appr_legacy_001"]
    assert matching, "legacy approval missing from packet"
    row = matching[0]
    assert row.get("provenance") == "unknown", f"legacy approval should land at unknown; got {row!r}"


def test_existing_provenance_preserved_at_collector() -> None:
    """A producer-stamped approval ``provenance`` is NOT overwritten
    by the collector's fallback stamping.
    """
    bundle_env = {
        "approvals": [
            {
                "approval_id": "appr_future_001",
                "approver": "human:carol@example.com",
                "scope": "pr-200",
                "recorded_at": "2026-05-14T00:00:00Z",
                "provenance": "ci_env_var",  # custom producer stamp
            },
        ],
    }
    packet, _ = collect_change_evidence(
        pr_bundle_envelope=bundle_env,
        repo_id="github.com/example/repo",
        commit_sha="1" * 40,
    )
    matching = [a for a in packet.approvals if a.get("approval_id") == "appr_future_001"]
    assert matching, "custom approval missing from packet"
    row = matching[0]
    assert row.get("provenance") == "ci_env_var", f"existing provenance was overwritten; got {row!r}"


# ---------------------------------------------------------------------------
# Drift guard — every emitted approval provenance lives in PROVENANCE_SOURCES
# ---------------------------------------------------------------------------


def test_approval_provenance_uses_only_PROVENANCE_SOURCES_values() -> None:
    """Every stamped approval provenance has a base in
    :data:`PROVENANCE_SOURCES` (closed-enum drift guard).
    """
    from roam.commands.cmd_pr_replay import _approval_record_to_envelope_dict

    # Fan-out across all approval-bearing channels.
    head = "abc" * 14  # 42 chars; same suffix in commit_id
    head40 = head[:40]
    reviews: list[dict] = [
        {
            "id": 1,
            "state": "APPROVED",
            "user": {"login": "rev"},
            "submitted_at": "2026-05-14T00:00:00Z",
            "commit_id": head40,
        },
    ]
    gh_approvals, _, _ = parse_github_reviews(
        reviews=reviews,
        head_commit_sha=head40,
        pr_number=1,
    )
    rows: list[dict] = [_approval_record_to_envelope_dict(r) for r in gh_approvals]
    # Legacy + fallback path: feed an unstamped row through the collector.
    bundle_env = {
        "approvals": [
            {
                "approval_id": "appr_legacy_drift",
                "approver": "human:d@example.com",
                "scope": "pr-1",
                "recorded_at": "2026-05-14T00:00:00Z",
            },
        ],
    }
    packet, _ = collect_change_evidence(
        pr_bundle_envelope=bundle_env,
        repo_id="github.com/example/repo",
        commit_sha="2" * 40,
    )
    rows.extend(packet.approvals)

    assert rows, "fanout produced no approval rows - test bug"

    for row in rows:
        prov = row.get("provenance")
        assert isinstance(prov, str) and prov, f"approval missing provenance: {row!r}"
        base = _provenance_base(prov)
        assert base in PROVENANCE_SOURCES, (
            f"approval provenance base {base!r} (from {prov!r}) not in PROVENANCE_SOURCES"
        )
