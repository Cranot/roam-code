"""Scan for hardcoded secrets, API keys, tokens, and passwords."""

from __future__ import annotations

import math
import os
import re
from collections import Counter
from pathlib import Path

import click

from roam.db.connection import open_db, find_project_root
from roam.output.formatter import format_table, to_json, json_envelope
from roam.commands.resolve import ensure_index

# ---------------------------------------------------------------------------
# Secret patterns — compiled once at module level for performance
# ---------------------------------------------------------------------------

_SECRET_PATTERN_DEFS: list[dict] = [
    # --- API Keys ---
    {"name": "AWS Access Key", "pattern": r"AKIA[0-9A-Z]{16}", "severity": "high"},
    {"name": "AWS Secret Key", "pattern": r"(?i)aws_secret_access_key\s*[=:]\s*['\"]?[A-Za-z0-9/+=]{40}", "severity": "high"},
    {"name": "GitHub Token", "pattern": r"gh[pousr]_[A-Za-z0-9_]{36,255}", "severity": "high"},
    {"name": "GitHub Personal Access Token (classic)", "pattern": r"ghp_[A-Za-z0-9]{36}", "severity": "high"},
    {"name": "GitLab Token", "pattern": r"glpat-[A-Za-z0-9\-]{20,}", "severity": "high"},
    {"name": "Slack Bot Token", "pattern": r"xoxb-[0-9]{10,13}-[0-9]{10,13}-[a-zA-Z0-9]{24}", "severity": "high"},
    {"name": "Slack Webhook", "pattern": r"https://hooks\.slack\.com/services/T[A-Z0-9]+/B[A-Z0-9]+/[a-zA-Z0-9]+", "severity": "medium"},
    {"name": "Stripe Secret Key", "pattern": r"sk_live_[0-9a-zA-Z]{24,}", "severity": "high"},
    {"name": "Stripe Publishable Key", "pattern": r"pk_live_[0-9a-zA-Z]{24,}", "severity": "low"},
    {"name": "Google API Key", "pattern": r"AIza[0-9A-Za-z\-_]{35}", "severity": "high"},
    {"name": "Google OAuth Secret", "pattern": r"(?i)client_secret.*['\"][A-Za-z0-9\-_]{24}['\"]", "severity": "high"},
    {"name": "Heroku API Key", "pattern": r"(?i)heroku.*['\"][0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}['\"]", "severity": "high"},
    {"name": "NPM Token", "pattern": r"npm_[A-Za-z0-9]{36}", "severity": "high"},
    {"name": "PyPI Token", "pattern": r"pypi-[A-Za-z0-9\-_]{100,}", "severity": "high"},
    {"name": "SendGrid API Key", "pattern": r"SG\.[A-Za-z0-9\-_]{22}\.[A-Za-z0-9\-_]{43}", "severity": "high"},
    {"name": "Twilio API Key", "pattern": r"SK[0-9a-fA-F]{32}", "severity": "medium"},
    {"name": "Mailgun API Key", "pattern": r"key-[0-9a-zA-Z]{32}", "severity": "high"},

    # --- Generic Secrets ---
    {"name": "Private Key", "pattern": r"-----BEGIN (?:RSA |EC |DSA |OPENSSH )?PRIVATE KEY-----", "severity": "high"},
    {"name": "Generic Password Assignment", "pattern": r"(?i)(?:password|passwd|pwd)\s*[=:]\s*['\"][^\s'\"]{8,}['\"]", "severity": "medium"},
    {"name": "Generic Secret Assignment", "pattern": r"(?i)(?:secret|token|api_key|apikey|access_key)\s*[=:]\s*['\"][^\s'\"]{8,}['\"]", "severity": "medium"},
    {"name": "Generic Bearer Token", "pattern": r"(?i)bearer\s+[a-zA-Z0-9\-_.~+/]+=*", "severity": "medium"},
    {"name": "JWT Token", "pattern": r"eyJ[A-Za-z0-9\-_]+\.eyJ[A-Za-z0-9\-_]+\.[A-Za-z0-9\-_.+/=]+", "severity": "medium"},
    {"name": "Base64 Encoded Secret", "pattern": r"(?i)(?:secret|password|token).*base64.*[A-Za-z0-9+/]{40,}={0,2}", "severity": "low"},

    # --- Database ---
    {"name": "Database Connection String", "pattern": r"(?i)(?:mysql|postgres|postgresql|mongodb|redis)://[^\s'\"]{10,}", "severity": "high"},

    # --- High Entropy String (post-filtered by Shannon entropy) ---
    {"name": "High Entropy String", "pattern": r"(?i)(?:key|secret|token|password|api_key|apikey|access_key|auth)\s*[=:]\s*['\"]([A-Za-z0-9+/=\-_]{20,})['\"]", "severity": "low"},
]

# Compile all regexes once
_COMPILED_PATTERNS: list[dict] = []
for _pdef in _SECRET_PATTERN_DEFS:
    _COMPILED_PATTERNS.append({
        "name": _pdef["name"],
        "regex": re.compile(_pdef["pattern"]),
        "severity": _pdef["severity"],
    })

# Severity ordering for filtering
_SEVERITY_RANK = {"high": 3, "medium": 2, "low": 1}

# Words in a line that indicate example/placeholder values (case-insensitive)
_PLACEHOLDER_WORDS = frozenset({
    "example", "placeholder", "changeme", "your_", "xxx",
    "dummy", "fake", "sample", "todo", "fixme", "replace_me",
    "insert_", "your-", "<your", "xxxxxx",
})

# Directories to always skip during scanning
_SKIP_DIRS = frozenset({
    ".git", "node_modules", "vendor", "__pycache__",
    ".tox", ".mypy_cache", ".pytest_cache", ".ruff_cache",
    "venv", ".venv", "env", ".env",
    "dist", "build", ".eggs",
    ".next", ".nuxt",
    ".roam",
})

# Binary file extensions to skip
_BINARY_EXTENSIONS = frozenset({
    ".png", ".jpg", ".jpeg", ".gif", ".bmp", ".ico", ".svg", ".webp",
    ".woff", ".woff2", ".ttf", ".eot", ".otf",
    ".zip", ".tar", ".gz", ".bz2", ".xz", ".7z", ".rar",
    ".exe", ".dll", ".so", ".dylib", ".o", ".a", ".lib",
    ".pyc", ".pyo", ".class", ".jar",
    ".db", ".sqlite", ".sqlite3",
    ".pdf", ".doc", ".docx", ".xls", ".xlsx",
    ".mp3", ".mp4", ".wav", ".avi", ".mov",
    ".bin", ".dat", ".pak", ".wasm",
})

# ---------------------------------------------------------------------------
# Environment-variable patterns — lines matching these are NOT hardcoded
# ---------------------------------------------------------------------------

_ENV_VAR_INDICATORS = (
    "os.environ",
    "process.env",
    "config.get(",
    "getenv(",
    "ENV[",
    "env.",
    "Config.",
    "settings.",
)

# ---------------------------------------------------------------------------
# Test/fixture/docs path patterns — suppressed by default
# ---------------------------------------------------------------------------

_TEST_DIR_SEGMENTS = frozenset({
    "tests", "test", "__tests__", "spec", "fixtures", "docs", "examples",
})

# File-level patterns (basename matching)
_TEST_FILE_RE = re.compile(
    r"^test_.*|.*_test\.[^.]+$",
    re.IGNORECASE,
)

_DOC_EXTENSIONS = frozenset({".md", ".rst", ".txt"})

# ---------------------------------------------------------------------------
# Per-finding remediation suggestions
# ---------------------------------------------------------------------------

_REMEDIATION: dict[str, str] = {
    "AWS Access Key": "Use os.environ['AWS_ACCESS_KEY_ID'] or AWS Secrets Manager",
    "AWS Secret Key": "Use os.environ['AWS_SECRET_ACCESS_KEY'] or AWS Secrets Manager",
    "GitHub Token": "Use os.environ['GITHUB_TOKEN'] or GitHub Actions secrets",
    "GitHub Personal Access Token (classic)": "Use os.environ['GITHUB_TOKEN'] or GitHub Actions secrets",
    "GitLab Token": "Use os.environ['GITLAB_TOKEN'] or CI/CD variables",
    "Slack Bot Token": "Use os.environ['SLACK_BOT_TOKEN'] or Slack app config",
    "Slack Webhook": "Use os.environ['SLACK_WEBHOOK_URL'] or secrets manager",
    "Stripe Secret Key": "Use os.environ['STRIPE_SECRET_KEY'] or secrets manager",
    "Stripe Publishable Key": "Publishable keys are public; verify this is intentional",
    "Google API Key": "Use os.environ['GOOGLE_API_KEY'] or secrets manager",
    "Google OAuth Secret": "Use os.environ['GOOGLE_CLIENT_SECRET'] or secrets manager",
    "Heroku API Key": "Use os.environ['HEROKU_API_KEY'] or Heroku config vars",
    "NPM Token": "Use .npmrc with env var: //registry.npmjs.org/:_authToken=${NPM_TOKEN}",
    "PyPI Token": "Use keyring or TWINE_PASSWORD env var",
    "SendGrid API Key": "Use os.environ['SENDGRID_API_KEY'] or secrets manager",
    "Twilio API Key": "Use os.environ['TWILIO_API_KEY'] or secrets manager",
    "Mailgun API Key": "Use os.environ['MAILGUN_API_KEY'] or secrets manager",
    "Private Key": "Store in a secure key vault, not in source code",
    "Generic Password Assignment": "Move to environment variable or secrets manager",
    "Generic Secret Assignment": "Move to environment variable or secrets manager",
    "Generic Bearer Token": "Generate tokens at runtime, don't hardcode",
    "JWT Token": "Generate tokens at runtime, don't hardcode",
    "Base64 Encoded Secret": "Move to environment variable or secrets manager",
    "Database Connection String": "Use connection pool with env-var DSN",
    "High Entropy String": "If this is a real secret, move to environment variable",
}


# ---------------------------------------------------------------------------
# Shannon entropy helper
# ---------------------------------------------------------------------------

def _shannon_entropy(s: str) -> float:
    """Calculate Shannon entropy in bits per character."""
    if not s:
        return 0.0
    freq = Counter(s)
    length = len(s)
    return -sum((c / length) * math.log2(c / length) for c in freq.values())


# Minimum entropy threshold for "High Entropy String" findings
_ENTROPY_THRESHOLD = 4.5


def _is_binary(file_path: str) -> bool:
    """Check if a file is likely binary by extension."""
    _, ext = os.path.splitext(file_path)
    return ext.lower() in _BINARY_EXTENSIONS


def _in_skip_dir(rel_path: str) -> bool:
    """Check if a relative path is under a directory that should be skipped."""
    parts = rel_path.replace("\\", "/").split("/")
    return any(p in _SKIP_DIRS for p in parts)


def _is_placeholder_line(line: str) -> bool:
    """Check if a line contains placeholder/example indicators."""
    lower = line.lower()
    return any(word in lower for word in _PLACEHOLDER_WORDS)


def _is_env_var_line(line: str) -> bool:
    """Check if a line reads a value from an environment variable or config.

    These are legitimate patterns, not hardcoded secrets.
    """
    return any(indicator in line for indicator in _ENV_VAR_INDICATORS)


def _is_test_or_doc_path(rel_path: str) -> bool:
    """Check if a relative path belongs to test, fixture, or docs directories.

    Returns True if the file should be suppressed (test/fixture/docs).
    """
    normed = rel_path.replace("\\", "/")
    parts = normed.split("/")
    basename = parts[-1] if parts else ""

    # Check directory segments
    for part in parts[:-1]:  # exclude the filename itself
        if part in _TEST_DIR_SEGMENTS:
            return True

    # Check filename patterns
    if _TEST_FILE_RE.match(basename):
        return True

    # Check doc extensions
    _, ext = os.path.splitext(basename)
    if ext.lower() in _DOC_EXTENSIONS:
        return True

    return False


def mask_secret(matched_text: str) -> str:
    """Mask a matched secret value for safe display.

    Shows first 4 chars + "..." + last 4 chars for values >= 12 chars.
    Shows first 4 chars + "..." for shorter values.
    Never shows the full secret.
    """
    if len(matched_text) <= 8:
        return matched_text[:4] + "..."
    if len(matched_text) >= 12:
        return matched_text[:4] + "..." + matched_text[-4:]
    return matched_text[:4] + "..."


def scan_file(file_path: str, patterns: list[dict] | None = None,
              min_severity: str = "all") -> list[dict]:
    """Scan a single file for secret patterns.

    Returns a list of finding dicts with keys:
        file, line, severity, pattern_name, matched_text (masked), remediation
    """
    if patterns is None:
        patterns = _COMPILED_PATTERNS

    min_rank = 0 if min_severity == "all" else _SEVERITY_RANK.get(min_severity, 0)

    findings: list[dict] = []
    try:
        with open(file_path, "r", encoding="utf-8", errors="replace") as f:
            for line_num, line in enumerate(f, start=1):
                # Skip lines that look like examples or placeholders
                if _is_placeholder_line(line):
                    continue
                # Skip lines that read from environment variables / config
                if _is_env_var_line(line):
                    continue
                for pat in patterns:
                    if _SEVERITY_RANK.get(pat["severity"], 0) < min_rank:
                        continue
                    match = pat["regex"].search(line)
                    if match:
                        # For High Entropy String, apply entropy threshold
                        if pat["name"] == "High Entropy String":
                            # Use group(1) if available (the captured value),
                            # otherwise use the full match
                            value = match.group(1) if match.lastindex else match.group()
                            if _shannon_entropy(value) < _ENTROPY_THRESHOLD:
                                continue
                        findings.append({
                            "file": file_path,
                            "line": line_num,
                            "severity": pat["severity"],
                            "pattern_name": pat["name"],
                            "matched_text": mask_secret(match.group()),
                            "remediation": _REMEDIATION.get(pat["name"], "Move to environment variable or secrets manager"),
                        })
    except (OSError, UnicodeDecodeError):
        pass

    return findings


def scan_project(project_root: Path, min_severity: str = "all",
                 use_index: bool = True,
                 include_tests: bool = False) -> list[dict]:
    """Scan all indexed files in a project for secrets.

    If use_index is True, reads file paths from the roam index DB.
    Otherwise falls back to walking the filesystem.

    When include_tests is False (the default), files in test, fixture,
    docs, and example directories are suppressed.

    Returns a list of finding dicts sorted by severity (high first),
    then file path, then line number.
    """
    root = Path(project_root).resolve()

    if use_index:
        try:
            with open_db(readonly=True) as conn:
                rows = conn.execute("SELECT path FROM files").fetchall()
                file_paths = [row["path"] for row in rows]
        except Exception:
            file_paths = _walk_for_files(root)
    else:
        file_paths = _walk_for_files(root)

    all_findings: list[dict] = []
    for rel_path in file_paths:
        if _is_binary(rel_path):
            continue
        if _in_skip_dir(rel_path):
            continue
        # Suppress test/fixture/docs files unless --include-tests
        if not include_tests and _is_test_or_doc_path(rel_path):
            continue

        full_path = root / rel_path
        if not full_path.is_file():
            continue

        file_findings = scan_file(str(full_path), min_severity=min_severity)
        # Store relative path in findings for cleaner output
        for f in file_findings:
            f["file"] = rel_path
        all_findings.extend(file_findings)

    # Sort: high severity first, then by file path, then line number
    all_findings.sort(
        key=lambda f: (-_SEVERITY_RANK.get(f["severity"], 0), f["file"], f["line"])
    )
    return all_findings


def _walk_for_files(root: Path) -> list[str]:
    """Walk the filesystem to find scannable files (fallback)."""
    result: list[str] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS]
        for fname in filenames:
            full = os.path.join(dirpath, fname)
            try:
                rel = os.path.relpath(full, root).replace("\\", "/")
            except (ValueError, OSError):
                continue
            result.append(rel)
    return result


@click.command("secrets")
@click.option("--severity", type=click.Choice(["all", "high", "medium", "low"]),
              default="all", help="Show only findings at or above this severity level")
@click.option("--fail-on-found", is_flag=True,
              help="Exit with code 5 if any secrets are found (for CI)")
@click.option("--include-tests", is_flag=True, default=False,
              help="Include test files, fixtures, docs, and examples in scan")
@click.pass_context
def secrets(ctx, severity, fail_on_found, include_tests):
    """Scan for hardcoded secrets, API keys, tokens, and passwords."""
    json_mode = ctx.obj.get("json") if ctx.obj else False
    sarif_mode = ctx.obj.get("sarif") if ctx.obj else False
    token_budget = ctx.obj.get("budget", 0) if ctx.obj else 0
    ensure_index()

    project_root = find_project_root()
    findings = scan_project(project_root, min_severity=severity,
                            include_tests=include_tests)

    # Compute summary stats
    total = len(findings)
    files_affected = len({f["file"] for f in findings})
    by_severity = {"high": 0, "medium": 0, "low": 0}
    for f in findings:
        by_severity[f["severity"]] = by_severity.get(f["severity"], 0) + 1

    if total == 0:
        verdict = "No secrets found"
    else:
        parts = []
        if by_severity["high"]:
            parts.append(f"{by_severity['high']} high")
        if by_severity["medium"]:
            parts.append(f"{by_severity['medium']} medium")
        if by_severity["low"]:
            parts.append(f"{by_severity['low']} low")
        sev_str = ", ".join(parts)
        verdict = f"{total} secrets found in {files_affected} files ({sev_str})"

    # --- SARIF output ---
    if sarif_mode:
        from roam.output.sarif import secrets_to_sarif, write_sarif
        sarif = secrets_to_sarif(findings)
        click.echo(write_sarif(sarif))
        if fail_on_found and total > 0:
            from roam.exit_codes import GateFailureError
            raise GateFailureError(verdict)
        return

    # --- JSON output ---
    if json_mode:
        envelope = json_envelope(
            "secrets",
            summary={
                "verdict": verdict,
                "total_findings": total,
                "files_affected": files_affected,
                "by_severity": by_severity,
            },
            budget=token_budget,
            findings=[
                {
                    "file": f["file"],
                    "line": f["line"],
                    "severity": f["severity"],
                    "pattern": f["pattern_name"],
                    "matched_text": f["matched_text"],
                    "remediation": f.get("remediation", ""),
                }
                for f in findings
            ],
        )
        click.echo(to_json(envelope))
        if fail_on_found and total > 0:
            from roam.exit_codes import GateFailureError
            raise GateFailureError(verdict)
        return

    # --- Text output ---
    click.echo(f"VERDICT: {verdict}")
    click.echo()

    if findings:
        rows = []
        for f in findings:
            loc_str = f"{f['file']}:{f['line']}"
            sev = f["severity"].upper()
            rows.append([loc_str, sev, f["pattern_name"], f["matched_text"]])

        click.echo(format_table(
            ["Location", "Severity", "Pattern", "Match"],
            rows,
        ))
        click.echo()
        click.echo(f"  {total} secrets, {files_affected} files, "
                    f"{by_severity['high']} high severity")
        click.echo()

        # Per-finding remediation
        seen_patterns: set[str] = set()
        remediation_lines: list[str] = []
        for f in findings:
            pname = f["pattern_name"]
            if pname not in seen_patterns:
                seen_patterns.add(pname)
                hint = f.get("remediation", _REMEDIATION.get(pname, ""))
                if hint:
                    remediation_lines.append(f"  - {pname}: {hint}")

        click.echo("  Recommendations:")
        click.echo("  - Move secrets to environment variables or a secrets manager")
        click.echo("  - Add .env to .gitignore")
        click.echo("  - Consider using git-secrets or pre-commit hooks to prevent future leaks")
        if remediation_lines:
            click.echo()
            click.echo("  Per-pattern guidance:")
            for line in remediation_lines:
                click.echo(line)
    else:
        click.echo("  No hardcoded secrets detected.")

    if fail_on_found and total > 0:
        from roam.exit_codes import GateFailureError
        raise GateFailureError(verdict)
