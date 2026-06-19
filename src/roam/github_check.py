"""github_check — minimal GitHub Check Run API binding for Roam Guard.

Posts the AgentChangeProofBundle v1 verdict + markdown summary as a
GitHub Check Run, surfacing the Roam Guard verdict on PRs.

API: POST https://api.github.com/repos/{owner}/{repo}/check-runs
Docs: https://docs.github.com/en/rest/checks/runs

The verdict → GitHub `conclusion` mapping:
  pass               → success
  pass_with_warnings → neutral
  needs_review       → action_required
  blocked            → failure

This module:
  * Has ZERO third-party dependencies (urllib only).
  * Splits payload-build from network-post so tests can hit the build
    side without mocking HTTP.
  * Reads GITHUB_TOKEN from env. Caller controls whether to actually POST.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from datetime import datetime, timezone
from typing import Any

# Re-exported from guard_enums (single source of truth).
from roam.guard_enums import VERDICT_TITLES as VERDICT_TO_TITLE  # noqa: E402
from roam.guard_enums import VERDICT_TO_GH_CONCLUSION as VERDICT_TO_CONCLUSION  # noqa: E402

# GitHub Check Run output.summary has a 65535-byte cap.
SUMMARY_BYTE_CAP = 65000


def _normalize_github_token(token: str | None) -> tuple[str | None, str | None]:
    """Return a header-safe token value plus an optional error code."""
    if token is None:
        return None, "no_github_token"
    normalized = token.strip()
    if not normalized:
        return None, "no_github_token"
    if any(ord(ch) < 32 or ord(ch) == 127 for ch in normalized):
        return None, "invalid_github_token"
    return normalized, None


def build_check_run_payload(
    v1: dict[str, Any],
    *,
    head_sha: str,
    name: str = "Roam Guard",
    markdown: str | None = None,
    details_url: str | None = None,
) -> dict[str, Any]:
    """Build the JSON body for a GitHub Check Run POST.

    Args:
      v1: AgentChangeProofBundle v1 dict.
      head_sha: full 40-char commit SHA of the PR head.
      name: display name of the check.
      markdown: optional pre-rendered markdown for output.summary.
                If None, a minimal summary is generated from the verdict.
      details_url: optional external link (e.g. dashboard).

    Returns:
      Dict suitable for `json.dumps` and POST to GitHub.
    """
    verdict = v1.get("verdict") or {}
    verdict_value = verdict.get("value", "pass")
    conclusion = VERDICT_TO_CONCLUSION.get(verdict_value, "neutral")
    title = VERDICT_TO_TITLE.get(verdict_value, f"Roam Guard — {verdict_value}")

    summary = markdown or _default_summary(v1)
    if len(summary.encode("utf-8")) > SUMMARY_BYTE_CAP:
        # Truncate at character boundary, append marker.
        cap_chars = SUMMARY_BYTE_CAP // 2  # safe upper bound on bytes per char
        summary = summary[:cap_chars].rstrip() + "\n\n_(summary truncated to fit GitHub Check size cap)_"

    payload: dict[str, Any] = {
        "name": name,
        "head_sha": head_sha,
        "status": "completed",
        "conclusion": conclusion,
        "completed_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "output": {
            "title": title,
            "summary": summary,
        },
    }
    if details_url:
        payload["details_url"] = details_url
    return payload


def _default_summary(v1: dict[str, Any]) -> str:
    """Fallback summary when caller doesn't pass markdown."""
    verdict = v1.get("verdict") or {}
    verdict_value = verdict.get("value", "pass")
    contract = v1.get("verification_contract") or {}
    required = contract.get("required") or []
    executed = v1.get("executed_checks") or []
    missing = v1.get("missing_checks") or []
    lines = [
        f"**Verdict:** `{verdict_value}`",
        "",
        f"- {len(executed)} of {len(required)} required checks executed",
        f"- {len(missing)} checks missing",
        f"- {len(v1.get('changed_files') or [])} files changed",
    ]
    return "\n".join(lines)


def post_check_run(
    *,
    owner: str,
    repo: str,
    payload: dict[str, Any],
    token: str | None = None,
    timeout: float = 15.0,
) -> dict[str, Any]:
    """POST a check run to GitHub.

    Args:
      owner: repository owner (e.g. "Cranot").
      repo: repository name (e.g. "roam-code").
      payload: JSON body from `build_check_run_payload`.
      token: GitHub token. Defaults to env var GITHUB_TOKEN.
      timeout: HTTP timeout in seconds.

    Returns:
      Dict with `{"ok": bool, "status": int, "body": ..., "error"?: str}`.
      Network or auth errors never raise — they surface in the dict.
    """
    token, token_error = _normalize_github_token(token or os.environ.get("GITHUB_TOKEN"))
    if token_error:
        return {"ok": False, "status": 0, "error": token_error}

    url = f"https://api.github.com/repos/{owner}/{repo}/check-runs"
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=body,
        method="POST",
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "Content-Type": "application/json",
            "User-Agent": "roam-code/roam-guard",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            text = resp.read().decode("utf-8", errors="replace")
            try:
                parsed = json.loads(text)
            except json.JSONDecodeError:
                parsed = {"raw": text}
            return {"ok": 200 <= resp.status < 300, "status": resp.status, "body": parsed}
    except urllib.error.HTTPError as e:
        try:
            err_text = e.read().decode("utf-8", errors="replace")
        except OSError:
            err_text = str(e)
        return {"ok": False, "status": e.code, "body": err_text, "error": f"http_{e.code}"}
    except (urllib.error.URLError, OSError) as e:
        return {"ok": False, "status": 0, "error": f"network: {e}"}
