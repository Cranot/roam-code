"""AIBOM extension for the CycloneDX 1.7 SBOM emitter.

Binds AI-authored commits (mined from ``git log`` via committer email +
Co-Authored-By trailers + AI-keyword scan) to the symbols they touched.
Required for **EU AI Act Art. 50** disclosure (effective 2026-08-02) and
the **GPAI Code of Practice** per-AI-change provenance mandate.

The "structural binding" piece — connecting an AI committer to specific
call-graph nodes — is what nobody else provides. SLSA = "how built",
SBOM = "what's in it", VEX = "is it exploitable". AIBOM-with-binding =
"who AI-authored this part of the call graph". That's roam's lane.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

from roam.git_utils import worktree_git_env

AIBOM_EXTENSION_VERSION = "0.1"


# Conservative AI-committer signals — false positives are worse than
# false negatives for an attestation auditors will scrutinise.
_AI_COMMITTER_EMAIL_PATTERNS = re.compile(
    r"(@anthropic\.com|@openai\.com|noreply@github\.com|copilot|cursor|claude|"
    r"gpt-[0-9]|deepseek|gemini|@windsurf\.app)",
    re.IGNORECASE,
)
_AI_TRAILER_PATTERN = re.compile(
    r"(?:^|\n)(?:Co-Authored-By|Generated-By|Assisted-By)\s*:\s*"
    r"([^\n<]+)<([^>]+)>",
    re.IGNORECASE,
)
_AI_TOOL_KEYWORDS = re.compile(
    r"(claude\b|gpt-?\d|copilot|cursor|aider|cline|continue\.dev|"
    r"chatgpt|deepseek|gemini|windsurf|codex)",
    re.IGNORECASE,
)


def _looks_like_ai_committer(email: str, message: str) -> bool:
    """Two-signal heuristic: AI email patterns OR AI keywords in the message."""
    if email and _AI_COMMITTER_EMAIL_PATTERNS.search(email):
        return True
    if message and _AI_TOOL_KEYWORDS.search(message):
        return True
    return False


def _extract_ai_trailers(message: str) -> list[dict[str, str]]:
    """Pull Co-Authored-By trailers and filter to AI-shaped ones."""
    if not message:
        return []
    out: list[dict[str, str]] = []
    for match in _AI_TRAILER_PATTERN.finditer(message):
        name = match.group(1).strip()
        email = match.group(2).strip()
        if _AI_COMMITTER_EMAIL_PATTERNS.search(email) or _AI_TOOL_KEYWORDS.search(name):
            out.append({"name": name, "email": email})
    return out


def _vendor_for_email(email: str) -> str:
    """Best-effort manufacturer inference from a committer email."""
    e = (email or "").lower()
    if "anthropic" in e or "claude" in e:
        return "Anthropic"
    if "openai" in e or "gpt" in e or "chatgpt" in e:
        return "OpenAI"
    if "github" in e or "copilot" in e:
        return "GitHub"
    if "cursor" in e:
        return "Cursor"
    if "windsurf" in e:
        return "Windsurf"
    if "google" in e or "gemini" in e:
        return "Google"
    if "deepseek" in e:
        return "DeepSeek"
    return "Unknown"


def mine_ai_commits(repo_root: Path, *, since: str | None = None, limit: int = 1000) -> list[dict]:
    """Return git commits whose committer / trailers / message indicate AI authorship.

    Each entry: ``{sha, author_email, committer_email, subject, body, ai_authors[]}``.
    Returns an empty list when git is unavailable or the repo isn't initialised.
    """
    cmd = ["git", "log", f"-n{limit}", "--format=%H%x1f%ae%x1f%ce%x1f%s%x1f%b%x1e", "--no-merges"]
    if since:
        cmd.append(f"--since={since}")
    try:
        result = subprocess.run(
            cmd,
            cwd=str(repo_root),
            capture_output=True,
            text=True,
            timeout=60,
            encoding="utf-8",
            errors="replace",
            env=worktree_git_env(repo_root),
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return []
    if result.returncode != 0:
        return []
    out: list[dict] = []
    for entry in result.stdout.split("\x1e"):
        entry = entry.strip()
        if not entry:
            continue
        parts = entry.split("\x1f")
        if len(parts) < 4:
            continue
        sha = parts[0].strip()
        author_email = parts[1].strip()
        committer_email = parts[2].strip()
        subject = parts[3].strip()
        body = parts[4].strip() if len(parts) > 4 else ""
        message = f"{subject}\n{body}"
        ai_authors = _extract_ai_trailers(message)
        ai_signal = (
            _looks_like_ai_committer(author_email, message)
            or _looks_like_ai_committer(committer_email, message)
            or bool(ai_authors)
        )
        if ai_signal:
            out.append(
                {
                    "sha": sha,
                    "author_email": author_email,
                    "committer_email": committer_email,
                    "subject": subject,
                    "body": body,
                    "ai_authors": ai_authors or [{"email": author_email}],
                }
            )
    return out


def _files_for_commit(repo_root: Path, sha: str) -> list[str]:
    """Return paths touched by a single commit."""
    try:
        r = subprocess.run(
            ["git", "show", "--name-only", "--format=", sha],
            cwd=str(repo_root),
            capture_output=True,
            text=True,
            timeout=10,
            encoding="utf-8",
            errors="replace",
            env=worktree_git_env(repo_root),
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return []
    if r.returncode != 0:
        return []
    return [p.strip() for p in r.stdout.strip().splitlines() if p.strip()]


def build_aibom_block(repo_root: Path, conn) -> dict:
    """Build the AIBOM extension block for embedding in a CycloneDX 1.7 SBOM.

    Shape::

        {
          "version": "0.1",
          "summary": {"ai_commits_total": N, "ai_components_total": M},
          "ai-components": [
            {"type": "ai-component", "name": "...", "email": "...",
             "manufacturer": "Anthropic|OpenAI|...",
             "binding": {"commits": [...], "commit_count": N,
                          "files": [...], "symbol_count": K}}
          ]
        }
    """
    ai_commits = mine_ai_commits(repo_root)
    if not ai_commits:
        return {
            "version": AIBOM_EXTENSION_VERSION,
            "ai-components": [],
            "summary": {"ai_commits_total": 0, "ai_components_total": 0},
        }

    by_committer: dict[str, dict] = {}
    for c in ai_commits:
        for author in c["ai_authors"]:
            email = (author.get("email") or "").strip().lower()
            if not email:
                continue
            entry = by_committer.setdefault(
                email,
                {
                    "type": "ai-component",
                    "name": author.get("name") or email,
                    "email": email,
                    "manufacturer": _vendor_for_email(email),
                    "binding": {"commits": [], "files": set()},
                },
            )
            entry["binding"]["commits"].append(c["sha"])

    # Hydrate file scope (bounded to first 200 commits per component to
    # keep the AIBOM size sane on large histories).
    for entry in by_committer.values():
        for sha in entry["binding"]["commits"][:200]:
            for path in _files_for_commit(repo_root, sha):
                entry["binding"]["files"].add(path)

    out_components: list[dict] = []
    for email, entry in by_committer.items():
        files = sorted(entry["binding"]["files"])[:50]
        symbol_count = 0
        if files and conn is not None:
            placeholders = ",".join("?" * len(files))
            try:
                row = conn.execute(
                    f"SELECT COUNT(*) FROM symbols s JOIN files f ON f.id = s.file_id WHERE f.path IN ({placeholders})",
                    files,
                ).fetchone()
                symbol_count = int(row[0]) if row else 0
            except Exception:
                symbol_count = 0
        out_components.append(
            {
                "type": "ai-component",
                "name": entry["name"],
                "email": email,
                "manufacturer": entry["manufacturer"],
                "binding": {
                    "commits": entry["binding"]["commits"][:50],
                    "commit_count": len(entry["binding"]["commits"]),
                    "files": files,
                    "symbol_count": symbol_count,
                },
            }
        )

    return {
        "version": AIBOM_EXTENSION_VERSION,
        "ai-components": out_components,
        "summary": {
            "ai_commits_total": len(ai_commits),
            "ai_components_total": len(out_components),
        },
    }
