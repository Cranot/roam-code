"""``roam.evidence.github_reviews`` - W247a parser/normalizer.

Pure parser that turns GitHub PR review JSON into neutral evidence-
compatible records. This is the FIRST HALF of W247 (real approvals
producer). The second half (W247b) will wire the output of this module
into ``pr-replay`` and the W176 envelope collector; this module
deliberately has NO pr-replay / collector integration yet.

Design constraints (from the W247a directive):

* **Head-commit filtering** - an ``APPROVED`` review on an older commit
  (before subsequent pushes) is NOT a current approval. GitHub's UI
  even shows it as "outdated" once newer commits land. Only ``APPROVED``
  reviews whose ``commit_id`` matches the current head commit become
  ``ApprovalRecord`` entries; older approvals are filtered out with a
  warning.
* **CHANGES_REQUESTED routes to PolicyDecision** - per the directive,
  a changes-requested review is a blocker / policy signal, NOT an
  approval. It normalizes into a ``PolicyDecision`` row with
  ``decision="deny"``.
* **Never store review bodies** - the parser MUST NEVER include
  ``review["body"]``, ``review["body_text"]``, or ``review["body_html"]``
  in any returned data. Reviewer rationale text belongs in the GitHub
  comment archive, not in the evidence packet. The parser's drift-guard
  test asserts this is enforced by code, not just policy.
* **Closed-state vocabulary** - every review must carry a ``state``
  literal in :data:`roam.evidence._vocabulary.GITHUB_REVIEW_STATES`;
  the parser raises ``ValueError`` on any other value so unknown
  GitHub API additions surface immediately.
* **Filtered states (COMMENTED / DISMISSED / PENDING)** - neither
  approvals nor blockers; the parser drops them and emits a warning
  per filtered review on the third tuple slot.
* **Pure functions** - ``parse_github_reviews`` and
  ``load_reviews_from_fixture`` are pure (no globals, no env reads, no
  clock dependence). ``harvest_reviews_from_gh_cli`` is unavoidably
  impure (subprocess call) and is documented as a deliberate-opt-in
  helper that nothing in the default pipeline calls.

Public API:

* :func:`parse_github_reviews` - normalize raw GitHub review JSON list
  into ``(approvals, policy_decisions, warnings)``.
* :func:`load_reviews_from_fixture` - load saved API JSON from disk for
  test/offline use.
* :func:`harvest_reviews_from_gh_cli` - optional network harvester via
  the ``gh api`` CLI. NOT called by default.

NON-GOALS (W247a):

* No pr-replay integration (deferred to W247b).
* No collector wiring (deferred to W247b).
* No ``accepted_risks`` producer (separate channel per the directive).
* No new closed enum bump beyond ``GITHUB_REVIEW_STATES``.
"""

from __future__ import annotations

import json
import pathlib
import subprocess
from collections.abc import Mapping
from typing import Any

from roam.evidence._vocabulary import GITHUB_REVIEW_STATES
from roam.evidence.approval import ApprovalRecord
from roam.evidence.policy import PolicyDecision


# Neutral reason string stamped on ApprovalRecord.reason. Deliberately
# NOT the review body (which is the W247a "never store bodies" rule).
_NEUTRAL_APPROVAL_REASON: str = "github_pr_approval"


def parse_github_reviews(
    *,
    reviews: list[Mapping[str, Any]],
    head_commit_sha: str,
    pr_number: int,
) -> tuple[
    tuple[ApprovalRecord, ...],
    tuple[PolicyDecision, ...],
    tuple[str, ...],
]:
    """Parse GitHub PR review JSON into neutral evidence records.

    Args:
        reviews: list of GitHub review dicts as returned by
            ``GET /repos/{owner}/{repo}/pulls/{pull_number}/reviews``.
            Each entry must carry at minimum ``id``, ``state``,
            ``user.login``, ``submitted_at``, ``commit_id``.
        head_commit_sha: SHA of the PR head commit. Only ``APPROVED``
            reviews whose ``commit_id`` matches this SHA become
            ``ApprovalRecord`` entries; older approvals are filtered out
            and surfaced as warnings.
        pr_number: PR number used to construct the ``scope`` /
            ``subject`` strings (``"pr:<number>"``).

    Returns:
        A 3-tuple ``(approvals, policy_decisions, warnings)``:

        * ``approvals`` - tuple of :class:`ApprovalRecord` (only
          ``APPROVED`` reviews on ``head_commit_sha``).
        * ``policy_decisions`` - tuple of :class:`PolicyDecision`
          (every ``CHANGES_REQUESTED`` review, on any commit, becomes
          a ``decision="deny"`` row).
        * ``warnings`` - tuple of human-readable strings describing
          each filtered or skipped review (stale approval, ignored
          state, etc.). Ordered by input order.

    Raises:
        ValueError: if any review carries a ``state`` not in
            :data:`roam.evidence._vocabulary.GITHUB_REVIEW_STATES`, or
            if a required field is missing on an otherwise-actionable
            row.
    """
    if not isinstance(reviews, list):
        raise ValueError(
            "parse_github_reviews: 'reviews' must be a list of review "
            "dicts (received {type})".format(type=type(reviews).__name__)
        )
    if not isinstance(head_commit_sha, str) or not head_commit_sha:
        raise ValueError(
            "parse_github_reviews: 'head_commit_sha' must be a non-empty "
            "string"
        )
    if not isinstance(pr_number, int):
        raise ValueError(
            "parse_github_reviews: 'pr_number' must be an int"
        )

    approvals: list[ApprovalRecord] = []
    policy_decisions: list[PolicyDecision] = []
    warnings: list[str] = []
    scope = f"pr:{pr_number}"

    for review in reviews:
        if not isinstance(review, Mapping):
            raise ValueError(
                "parse_github_reviews: every review entry must be a "
                "mapping (got {type})".format(type=type(review).__name__)
            )
        state = review.get("state")
        if state not in GITHUB_REVIEW_STATES:
            raise ValueError(
                f"parse_github_reviews: review state {state!r} is not in "
                f"GITHUB_REVIEW_STATES; refusing to parse unknown literal"
            )

        if state == "APPROVED":
            record = _maybe_build_approval_record(
                review=review,
                head_commit_sha=head_commit_sha,
                scope=scope,
                warnings=warnings,
            )
            if record is not None:
                approvals.append(record)
            continue

        if state == "CHANGES_REQUESTED":
            decision = _build_policy_decision(
                review=review,
                pr_number=pr_number,
            )
            policy_decisions.append(decision)
            continue

        # COMMENTED / DISMISSED / PENDING - neither approval nor blocker.
        # Drop with a warning so the consumer can audit why a review
        # didn't surface either way.
        review_id = review.get("id")
        reviewer = _safe_login(review)
        warnings.append(
            f"filtered review id={review_id} state={state} reviewer={reviewer}"
            " - not an approval and not a blocker"
        )

    return tuple(approvals), tuple(policy_decisions), tuple(warnings)


def load_reviews_from_fixture(
    fixture_path: pathlib.Path,
) -> list[Mapping[str, Any]]:
    """Load saved GitHub review JSON from disk (fixture-first).

    Pure helper for test / offline use. Reads the file, decodes JSON,
    and returns the result. NOT network-bound.

    Args:
        fixture_path: path to a ``.json`` file produced by
            ``gh api repos/.../pulls/<n>/reviews`` (or a hand-crafted
            synthetic fixture).

    Returns:
        The decoded JSON list. The function does NOT validate the
        contents against ``GITHUB_REVIEW_STATES`` - that happens at
        :func:`parse_github_reviews` time.

    Raises:
        FileNotFoundError: if ``fixture_path`` does not exist.
        ValueError: if the file's contents are not a JSON list.
    """
    if not isinstance(fixture_path, pathlib.Path):
        fixture_path = pathlib.Path(fixture_path)
    text = fixture_path.read_text(encoding="utf-8")
    data = json.loads(text)
    if not isinstance(data, list):
        raise ValueError(
            f"load_reviews_from_fixture: expected a JSON list at "
            f"{fixture_path}, got {type(data).__name__}"
        )
    return data


def harvest_reviews_from_gh_cli(
    *,
    owner: str,
    repo: str,
    pr_number: int,
    gh_executable: str = "gh",
    timeout: int = 30,
) -> list[Mapping[str, Any]]:
    """Optional network harvester via ``gh api`` CLI subprocess.

    Deliberately opt-in: nothing in the default Roam pipeline calls
    this. Callers must invoke it explicitly. Returns the raw JSON list
    from the GitHub PR-reviews endpoint. Auth is delegated to the
    ``gh`` CLI (no token handling here).

    Args:
        owner: GitHub repo owner / org.
        repo: GitHub repo name.
        pr_number: PR number.
        gh_executable: path to the ``gh`` binary (default ``"gh"``,
            resolved via PATH). Overridable for tests.
        timeout: subprocess timeout in seconds.

    Returns:
        The decoded JSON list of review dicts.

    Raises:
        RuntimeError: if ``gh`` is not installed, exits non-zero, or
            the response is not a JSON list.
    """
    endpoint = f"repos/{owner}/{repo}/pulls/{pr_number}/reviews"
    try:
        proc = subprocess.run(
            [gh_executable, "api", endpoint],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
            encoding="utf-8",
            errors="replace",
        )
    except FileNotFoundError as exc:
        raise RuntimeError(
            f"harvest_reviews_from_gh_cli: gh executable not found at "
            f"{gh_executable!r}; install GitHub CLI or pass an explicit "
            f"gh_executable path ({exc})"
        ) from exc
    except (OSError, subprocess.SubprocessError) as exc:
        raise RuntimeError(
            f"harvest_reviews_from_gh_cli: gh invocation failed for "
            f"{owner}/{repo}#{pr_number} ({exc})"
        ) from exc

    if proc.returncode != 0:
        raise RuntimeError(
            f"harvest_reviews_from_gh_cli: gh api returned exit "
            f"{proc.returncode} for {endpoint}: "
            f"{(proc.stderr or '').strip()[:500]}"
        )

    try:
        data = json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError(
            f"harvest_reviews_from_gh_cli: gh api response was not JSON "
            f"for {endpoint} ({exc})"
        ) from exc

    if not isinstance(data, list):
        raise RuntimeError(
            f"harvest_reviews_from_gh_cli: gh api response was not a "
            f"JSON list for {endpoint} (got {type(data).__name__})"
        )
    return data


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _safe_login(review: Mapping[str, Any]) -> str:
    """Return the reviewer login, or ``"<unknown>"`` if missing."""
    user = review.get("user")
    if isinstance(user, Mapping):
        login = user.get("login")
        if isinstance(login, str) and login:
            return login
    return "<unknown>"


def _maybe_build_approval_record(
    *,
    review: Mapping[str, Any],
    head_commit_sha: str,
    scope: str,
    warnings: list[str],
) -> ApprovalRecord | None:
    """Build an ``ApprovalRecord`` if the review is on the head commit.

    Returns ``None`` (and appends a warning) when the review is
    ``APPROVED`` but on a stale commit - that's the "outdated" case the
    W247a directive calls out explicitly.
    """
    commit_id = review.get("commit_id")
    reviewer = _safe_login(review)
    review_id = review.get("id")
    if commit_id != head_commit_sha:
        warnings.append(
            f"stale approval review id={review_id} reviewer={reviewer} "
            f"on commit {commit_id!r} (head is {head_commit_sha!r})"
            " - filtered out per W247a head-commit rule"
        )
        return None

    submitted_at = review.get("submitted_at")
    if not isinstance(submitted_at, str) or not submitted_at:
        raise ValueError(
            f"parse_github_reviews: APPROVED review id={review_id} is "
            f"missing 'submitted_at'"
        )

    extra: dict[str, Any] = {
        "commit_id": commit_id,
        "review_id": review_id,
    }
    html_url = review.get("html_url")
    if isinstance(html_url, str) and html_url:
        extra["html_url"] = html_url

    return ApprovalRecord(
        approver=reviewer,
        scope=scope,
        timestamp=submitted_at,
        reason=_NEUTRAL_APPROVAL_REASON,
        extra=extra,
    )


def _build_policy_decision(
    *,
    review: Mapping[str, Any],
    pr_number: int,
) -> PolicyDecision:
    """Build a ``PolicyDecision(decision="deny")`` for CHANGES_REQUESTED.

    W293 — stamp ``extra["provenance"] = "producer_envelope(github_review)"``
    at the parser ingestion site so consumers can attribute the decision
    to the GitHub PR-review data channel. The stamp lives on the
    dataclass's ``extra`` field; ``to_dict()`` flattens it to a top-level
    ``provenance`` key on the wire dict that flows into
    ``extra_policy_decisions`` and ultimately ``ChangeEvidence.policy_decisions``.
    """
    review_id = review.get("id")
    reviewer = _safe_login(review)
    submitted_at = review.get("submitted_at")
    commit_id = review.get("commit_id")
    html_url = review.get("html_url")

    extra: dict[str, Any] = {
        "reviewer": reviewer,
        "submitted_at": submitted_at,
        "commit_id": commit_id,
    }
    if isinstance(html_url, str) and html_url:
        extra["html_url"] = html_url
    # W293 — provenance stamp at the parser ingestion site. Bare ``import``
    # at module top would be cleaner but parser is a leaf module and we
    # avoid the indirect circular-import risk via local-scope import.
    try:
        from roam.evidence.provenance import provenance_label
        extra["provenance"] = provenance_label(
            "producer_envelope", detail="github_review",
        )
    except (ImportError, AttributeError):
        # W746: narrowed from bare Exception. The helper is a leaf
        # module that builds a dict from string args; the realistic
        # failures are an import failure (module renamed/removed) or
        # attribute-lookup on a partial import. Programmer-class
        # errors inside provenance_label now propagate per W531.
        pass

    return PolicyDecision(
        rule_id=f"github_review:{review_id}",
        decision="deny",
        subject=f"pr:{pr_number}",
        subject_kind="commit",
        extra=extra,
    )


__all__ = [
    "parse_github_reviews",
    "load_reviews_from_fixture",
    "harvest_reviews_from_gh_cli",
]
