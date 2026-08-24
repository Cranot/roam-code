"""Scan for hardcoded secrets, API keys, tokens, and passwords."""

from __future__ import annotations

import math
import os
import re
import sqlite3
from collections import Counter
from pathlib import Path

import click

from roam.capability import roam_capability
from roam.commands.resolve import ensure_index, index_status
from roam.db.connection import find_project_root, open_db
from roam.exit_codes import gate_should_fail
from roam.output._severity import severity_breakdown, severity_rank
from roam.output.confidence import (
    confidence_distribution,
    verdict_with_high_count,
    wrap_findings,
)
from roam.output.formatter import format_table, json_envelope, to_json

# ---------------------------------------------------------------------------
# Secret patterns — compiled once at module level for performance
# ---------------------------------------------------------------------------

_SECRET_PATTERN_DEFS: list[dict] = [
    # --- AI provider keys ---
    # roam's users ARE AI-agent developers, so these are the credentials most
    # likely to appear in their repos, .env files, notebooks and pasted logs --
    # and until 2026-07-27 the catalogue had none of them. A scanner that detects
    # AWS and GitHub but not the provider key sitting in the user's own toolchain
    # gives false assurance exactly where its audience is most exposed.
    #
    # Found by scanning a real transcript corpus with this scanner: it passed a
    # live `sk-ant-oat01-` OAuth token (1-year validity) as clean.
    {
        "name": "Anthropic OAuth Token",
        "pattern": r"sk-ant-oat[0-9]{2}-[A-Za-z0-9_\-]{40,}",
        "severity": "high",
    },
    {
        "name": "Anthropic API Key",
        "pattern": r"sk-ant-(?:api)?[0-9]{2}-[A-Za-z0-9_\-]{40,}",
        "severity": "high",
    },
    {
        "name": "OpenAI Project Key",
        "pattern": r"sk-proj-[A-Za-z0-9_\-]{20,}",
        "severity": "high",
    },
    {
        "name": "OpenAI API Key",
        # Anchored on the historical 48-char form; the generic `sk-` prefix alone
        # is too weak and would collide with Stripe's sk_live_ and DeepSeek hex.
        "pattern": r"sk-[A-Za-z0-9]{48}",
        "severity": "high",
    },
    {"name": "xAI API Key", "pattern": r"xai-[A-Za-z0-9]{20,}", "severity": "high"},
    {"name": "Groq API Key", "pattern": r"gsk_[A-Za-z0-9]{20,}", "severity": "high"},
    {
        "name": "HuggingFace Token",
        "pattern": r"hf_[A-Za-z0-9]{34,}",
        "severity": "high",
    },
    {
        "name": "Replicate API Token",
        "pattern": r"r8_[A-Za-z0-9]{37,}",
        "severity": "high",
    },
    # --- API Keys ---
    {"name": "AWS Access Key", "pattern": r"AKIA[0-9A-Z]{16}", "severity": "high"},
    {
        "name": "AWS Secret Key",
        "pattern": r"(?i)aws_secret_access_key\s*[=:]\s*['\"]?[A-Za-z0-9/+=]{40}",
        "severity": "high",
    },
    {"name": "GitHub Token", "pattern": r"gh[pousr]_[A-Za-z0-9_]{36,255}", "severity": "high"},
    {
        "name": "GitHub Personal Access Token (classic)",
        "pattern": r"ghp_[A-Za-z0-9]{36}",
        "severity": "high",
    },
    {"name": "GitLab Token", "pattern": r"glpat-[A-Za-z0-9\-]{20,}", "severity": "high"},
    {
        "name": "Slack Bot Token",
        "pattern": r"xoxb-[0-9]{10,13}-[0-9]{10,13}-[a-zA-Z0-9]{24}",
        "severity": "high",
    },
    {
        "name": "Slack Webhook",
        "pattern": r"https://hooks\.slack\.com/services/T[A-Z0-9]+/B[A-Z0-9]+/[a-zA-Z0-9]+",
        "severity": "medium",
    },
    {"name": "Stripe Secret Key", "pattern": r"sk_live_[0-9a-zA-Z]{24,}", "severity": "high"},
    {"name": "Stripe Publishable Key", "pattern": r"pk_live_[0-9a-zA-Z]{24,}", "severity": "low"},
    {"name": "Google API Key", "pattern": r"AIza[0-9A-Za-z\-_]{35}", "severity": "high"},
    {
        "name": "Google OAuth Secret",
        "pattern": r"(?i)client_secret.*['\"][A-Za-z0-9\-_]{24}['\"]",
        "severity": "high",
    },
    {
        "name": "Heroku API Key",
        "pattern": r"(?i)heroku.*['\"][0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}['\"]",
        "severity": "high",
    },
    {"name": "NPM Token", "pattern": r"npm_[A-Za-z0-9]{36}", "severity": "high"},
    {"name": "PyPI Token", "pattern": r"pypi-[A-Za-z0-9\-_]{100,}", "severity": "high"},
    {
        "name": "SendGrid API Key",
        "pattern": r"SG\.[A-Za-z0-9\-_]{22}\.[A-Za-z0-9\-_]{43}",
        "severity": "high",
    },
    {"name": "Twilio API Key", "pattern": r"SK[0-9a-fA-F]{32}", "severity": "medium"},
    {"name": "Mailgun API Key", "pattern": r"key-[0-9a-zA-Z]{32}", "severity": "high"},
    # --- Generic Secrets ---
    {
        "name": "Private Key",
        "pattern": r"-----BEGIN (?:RSA |EC |DSA |OPENSSH )?PRIVATE KEY-----",
        "severity": "high",
    },
    {
        "name": "Generic Password Assignment",
        "pattern": r"(?i)(?:password|passwd|pwd)\s*[=:]\s*['\"][^\s'\"]{8,}['\"]",
        "severity": "medium",
    },
    {
        "name": "Generic Secret Assignment",
        "pattern": r"(?i)(?:secret|token|api_key|apikey|access_key)\s*[=:]\s*['\"][^\s'\"]{8,}['\"]",
        "severity": "medium",
    },
    {
        # W1075: bump min token-body length from 1 to 20 chars to stop matching
        # the English phrase "Bearer Token" / "Bearer token." in docstrings,
        # help text, and remediation-message constants. Real bearers (opaque
        # tokens or JWTs) are always ≥20 chars; common forms are 32+. The
        # `Token`/`token` English word is also explicitly excluded via the
        # `(?!Token\b|token\b)` lookahead to catch any 20-char-aligned phrase
        # ("Bearer token contains valid…" etc.). Tested in tests/test_secrets_v2.py
        # via the cmd_secrets self-scan regression.
        "name": "Generic Bearer Token",
        "pattern": r"(?i)bearer\s+(?!Token\b|token\b)[a-zA-Z0-9\-_.~+/]{20,}=*",
        "severity": "medium",
    },
    {
        "name": "JWT Token",
        "pattern": r"eyJ[A-Za-z0-9\-_]+\.eyJ[A-Za-z0-9\-_]+\.[A-Za-z0-9\-_.+/=]+",
        "severity": "medium",
    },
    {
        "name": "Base64 Encoded Secret",
        "pattern": r"(?i)(?:secret|password|token).*base64.*[A-Za-z0-9+/]{40,}={0,2}",
        "severity": "low",
    },
    # --- Database ---
    {
        "name": "Database Connection String",
        "pattern": r"(?i)(?:mysql|postgres|postgresql|mongodb|redis)://[^\s'\"]{10,}",
        "severity": "high",
    },
    # --- High Entropy String (post-filtered by Shannon entropy) ---
    {
        "name": "High Entropy String",
        "pattern": r"(?i)(?:key|secret|token|password|api_key|apikey|access_key|auth)\s*[=:]\s*['\"]([A-Za-z0-9+/=\-_]{20,})['\"]",
        "severity": "low",
    },
]

# Compile all regexes once
_COMPILED_PATTERNS: list[dict] = []
for _pdef in _SECRET_PATTERN_DEFS:
    _COMPILED_PATTERNS.append(
        {
            "name": _pdef["name"],
            "regex": re.compile(_pdef["pattern"]),
            "severity": _pdef["severity"],
        }
    )

# W564: severity ordering now sourced from
# roam.output._severity.severity_rank — canonical, alias-aware, shared
# with every other roam command. Filter semantics preserved: ``high``
# (rank=4) > ``medium`` (rank=2) > ``low`` (rank=1).

# Words in a line that indicate example/placeholder values (case-insensitive)
_PLACEHOLDER_WORDS = frozenset(
    {
        "example",
        "placeholder",
        "changeme",
        "your_",
        "xxx",
        "dummy",
        "fake",
        "sample",
        "todo",
        "fixme",
        "replace_me",
        "insert_",
        "your-",
        "<your",
        "xxxxxx",
    }
)

# Directories to always skip during scanning
_SKIP_DIRS = frozenset(
    {
        ".git",
        "node_modules",
        "vendor",
        "__pycache__",
        ".tox",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        "venv",
        ".venv",
        "env",
        ".env",
        "dist",
        "build",
        ".eggs",
        ".next",
        ".nuxt",
        ".roam",
    }
)

# _BINARY_EXTENSIONS is an alias of roam.index.discovery.SKIP_EXTENSIONS
# (53 entries) after W39.4 collapse. History: W37.5 found _BINARY_EXTENSIONS
# (48 entries) was a strict subset; W38.3 backfilled citations for both;
# W39.4 collapsed to one source of truth on the rationale that the 5 extras
# (.lock / .map / .min.js / .min.css / .sct) are low-value secrets-scan
# targets: .lock files are also matched by SKIP_NAMES (package-lock.json,
# yarn.lock, Cargo.lock, ...) so the lockfile-via-extension branch was
# already redundant; .map and .min.* leaks would surface in the unminified
# source the scanner does process; .sct is a Visual FoxPro companion file.
# Kept as a named alias for backward compatibility with any external
# consumers (no in-tree callers as of 2026-05-13).
from roam.index.discovery import SKIP_EXTENSIONS as _BINARY_EXTENSIONS  # noqa: E402

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

_TEST_DIR_SEGMENTS = frozenset(
    {
        "tests",
        "test",
        "__tests__",
        "spec",
        "fixtures",
        "docs",
        "examples",
    }
)

# File-level test-file detection delegates to the canonical anchored
# classifier (roam.index.file_roles.is_test -> test_conventions.is_test_file)
# instead of a hand-rolled regex. The previous `_TEST_FILE_RE` (Python-only:
# `^test_.*` / `.*_test\.[^.]+$`) missed every JS/TS convention (*.test.ts,
# *.spec.js, ...), Go (*_test.go), Java/Kotlin/C# (*Test.java, ...), Ruby
# (*_spec.rb), etc. -- so in a JS/TS repo, test fixtures were never
# recognised as tests and every secret-scan finding in them surfaced as a
# real (unsuppressed) hit. Measured: 86/86 false positives on a JS/TS
# fixture corpus before this fix.
#
# Called on the BASENAME only (not the full relative path) -- this is
# load-bearing, not incidental. `is_test` also matches directory-based
# conventions (`(^|/)tests/`, `(^|/)test/`, `(^|/)testing/`, ...); feeding
# it the full path would let those directory heuristics reach first-party
# source that merely lives under a similarly-named path component. The
# directory case is already handled above by `_TEST_DIR_SEGMENTS`; this
# basename-only call adds *only* the filename-convention coverage.
# _DOC_EXTENSIONS re-exported from roam.index.file_roles (W37.5 consolidation —
# was 1 of 3 divergent local copies before consolidation; now uses the 5-entry
# canonical union from file_roles.DOC_EXTENSIONS).
from roam.index.file_roles import DOC_EXTENSIONS as _DOC_EXTENSIONS  # noqa: E402
from roam.index.file_roles import is_test as _roles_is_test  # noqa: E402

# ---------------------------------------------------------------------------
# Per-finding remediation suggestions
# ---------------------------------------------------------------------------

_REMEDIATION: dict[str, str] = {
    # AI provider keys. Rotation advice is explicit because these leak through a
    # different channel than most credentials -- pasted logs, notebooks and agent
    # transcripts -- where the holder often does not realise a copy persists.
    "Anthropic OAuth Token": (
        "Revoke at console.anthropic.com and re-issue; use os.environ['ANTHROPIC_API_KEY']. "
        "Long-lived OAuth tokens also persist in agent transcripts -- rotate, do not just delete."
    ),
    "Anthropic API Key": "Use os.environ['ANTHROPIC_API_KEY']; rotate the exposed key at console.anthropic.com",
    "OpenAI Project Key": "Use os.environ['OPENAI_API_KEY']; revoke the exposed key in the OpenAI dashboard",
    "OpenAI API Key": "Use os.environ['OPENAI_API_KEY']; revoke the exposed key in the OpenAI dashboard",
    "xAI API Key": "Use os.environ['XAI_API_KEY']; revoke the exposed key in the xAI console",
    "Groq API Key": "Use os.environ['GROQ_API_KEY']; revoke the exposed key in the Groq console",
    "HuggingFace Token": "Use os.environ['HF_TOKEN']; revoke at huggingface.co/settings/tokens",
    "Replicate API Token": "Use os.environ['REPLICATE_API_TOKEN']; revoke at replicate.com/account",
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
# R22 — confidence classifier for secret findings.
#
# Confidence reflects how trustworthy the secret-detection signal is, NOT
# the severity of the leak:
#   high   — the matched value is a high-entropy random-looking string,
#            which the regex was anchored to (e.g. "High Entropy String"
#            pattern that passed the Shannon threshold). Very low false-
#            positive rate.
#   medium — the pattern is a known-prefix match (AKIA*, ghp_*, sk_live_*,
#            etc.) — vendor-issued opaque tokens. Still very accurate but
#            could conceivably be a placeholder of the same shape.
#   low    — generic / structural patterns: password= assignments,
#            base64-ish blobs, JWT shapes. These are easy to false-
#            positive on test data, example configs, and constant strings.
#
# A patterns categorised as "high entropy" by name OR a regex with a
# unique vendor prefix is high/medium; everything else lands in low.
# ---------------------------------------------------------------------------

_HIGH_ENTROPY_PATTERN_NAMES = frozenset({"High Entropy String"})

# Vendor-prefixed tokens — distinctive enough to almost-always be real,
# but technically a string of that shape could exist as a placeholder.
_PREFIX_MATCH_PATTERN_NAMES = frozenset(
    {
        "AWS Access Key",
        "AWS Secret Key",
        "GitHub Token",
        "GitHub Personal Access Token (classic)",
        "GitLab Token",
        "Slack Bot Token",
        "Slack Webhook",
        "Stripe Secret Key",
        "Stripe Publishable Key",
        "Google API Key",
        "Heroku API Key",
        "NPM Token",
        "PyPI Token",
        "SendGrid API Key",
        "Twilio API Key",
        "Mailgun API Key",
        "Private Key",
        "Database Connection String",
    }
)


def _secrets_classify(finding: dict) -> tuple[str, str]:
    """Map a secret finding to a (confidence, reason) tuple.

    High-entropy passing matches get "high"; named vendor-prefix matches
    get "medium"; everything else (generic regex, password assignments,
    JWT shapes) lands in "low".
    """
    name = finding.get("pattern") or finding.get("pattern_name") or ""
    matched = finding.get("matched_text") or ""
    # The matched_text we ship is masked (e.g. "AKIA...XYZ4"). We still
    # use it for length-based reasoning but cannot recompute entropy from
    # a mask — the entropy gate has already been applied to keep this
    # finding in the list, so trust the name as the dominant signal.
    if name in _HIGH_ENTROPY_PATTERN_NAMES:
        return "high", f"{name} pattern passed Shannon-entropy threshold {_ENTROPY_THRESHOLD}"
    if name in _PREFIX_MATCH_PATTERN_NAMES:
        return "medium", f"known-prefix match ({name}); length={len(matched)}"
    return "low", f"generic / structural match ({name or 'unknown'}); manual review recommended"


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


# A candidate that contains a repeated regex atom describes a value shape; it
# is not the value itself. This is deliberately content-based rather than a
# filename allowlist, so redaction catalogues and validation schemas receive
# the same treatment wherever they live. Requiring both an atom (``[...]`` or
# ``\w``/``\d``-style class) and a quantifier keeps ordinary hardcoded values
# on the normal detection path.
_REPEATED_REGEX_ATOM_RE = re.compile(r"(?:\[(?:\\.|[^\]\\\n]){1,80}\]|\\[AbBdDsSwWZ])(?:\{\d+(?:,\d*)?\}|[+*?])")


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

    # Check filename patterns (canonical, language-aware classifier;
    # basename-only -- see comment on the import above)
    if _roles_is_test(basename):
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


def _high_entropy_passes(pat: dict, match) -> bool:
    """Apply the Shannon-entropy threshold for the High Entropy String
    pattern. Other patterns always pass (returns True)."""
    if pat["name"] != "High Entropy String":
        return True
    value = match.group(1) if match.lastindex else match.group()
    return _shannon_entropy(value) >= _ENTROPY_THRESHOLD


def _is_pattern_definition_candidate(matched_text: str) -> bool:
    """Return True when a matched value is regex syntax, not a credential."""
    return _REPEATED_REGEX_ATOM_RE.search(matched_text) is not None


def _match_pattern_to_finding(line: str, pat: dict, file_path: str, line_num: int, min_rank: int) -> dict | None:
    """Try one pattern against one line. Returns a finding dict or None."""
    if severity_rank(pat["severity"]) < min_rank:
        return None
    for match in pat["regex"].finditer(line):
        if _is_pattern_definition_candidate(match.group()):
            continue
        if not _high_entropy_passes(pat, match):
            continue
        return {
            "file": file_path,
            "line": line_num,
            "severity": pat["severity"],
            "pattern_name": pat["name"],
            "matched_text": mask_secret(match.group()),
            "remediation": _REMEDIATION.get(pat["name"], "Move to environment variable or secrets manager"),
        }
    return None


def scan_file(
    file_path: str,
    patterns: list[dict] | None = None,
    min_severity: str = "all",
    read_errors: list[dict] | None = None,
) -> list[dict]:
    """Scan a single file for secret patterns.

    Returns a list of finding dicts with keys:
        file, line, severity, pattern_name, matched_text (masked), remediation

    ``[]`` is TWO-VALUED here and always has been: "this file holds no
    secrets" and "this file could not be read" produce the identical empty
    list, because the read failure is swallowed below. Pass ``read_errors``
    to tell them apart — a list this function appends
    ``{"file", "error"}`` to when the read fails. Callers that count files
    MUST pass it: a denominator incremented around a call that can return
    ``[]`` without reading anything is a count of files certified, not of
    files examined.
    """
    if patterns is None:
        patterns = _COMPILED_PATTERNS

    # W1005-followup-C: ``all`` is a no-floor sentinel (case-insensitive
    # for parity with the widened click.Choice ``case_sensitive=False``);
    # every other token routes through canonical ``severity_rank``.
    min_rank = -1 if min_severity.lower() == "all" else severity_rank(min_severity)

    findings: list[dict] = []
    try:
        # Read BYTES, not text. `errors="replace"` reads a UTF-16 file WRONG
        # rather than failing -- NUL padding is itself valid UTF-8, so nothing
        # is dropped and a token arrives as `g\x00h\x00p\x00_\x00...`, matching
        # no pattern. This is a blocking CI gate (`--fail-on-found`), and
        # PowerShell's `>` emits UTF-16LE by default, so `gh auth token >
        # token.txt` plus an accidental `git add` was invisible here.
        from roam.security.text_views import decode_views

        seen: set[tuple] = set()
        for view in decode_views(Path(file_path).read_bytes()):
            for line_num, line in enumerate(view.splitlines(), start=1):
                if _is_placeholder_line(line) or _is_env_var_line(line):
                    continue
                for pat in patterns:
                    finding = _match_pattern_to_finding(line, pat, file_path, line_num, min_rank)
                    if finding is not None:
                        key = (
                            tuple(sorted(finding.items(), key=lambda kv: kv[0]))
                            if isinstance(finding, dict)
                            else finding
                        )
                        if key in seen:
                            continue
                        seen.add(key)
                        findings.append(finding)
    except (OSError, UnicodeDecodeError) as _exc:
        from roam.observability import log_swallowed

        log_swallowed("cmd_secrets:scan_file", _exc)
        if read_errors is not None:
            # Any findings collected before the failure are still returned —
            # truncation loses matches, it does not invent them — but the
            # file is reported as unread, because the part that was never
            # decoded is exactly the part nothing can be claimed about.
            read_errors.append({"file": file_path, "error": f"{type(_exc).__name__}: {_exc}"})

    return findings


def emit_vocab_max_label(patterns: list[dict] | None = None) -> str:
    """Return the highest severity LABEL any secret pattern can emit."""
    pats = _COMPILED_PATTERNS if patterns is None else patterns
    best = max(pats, key=lambda p: severity_rank(p["severity"]), default=None)
    return str(best["severity"]) if best else "none"


def emit_vocab_max_rank(patterns: list[dict] | None = None) -> int:
    """Return the highest severity rank any secret pattern can actually emit.

    The ``--severity`` choice list is the canonical W547 7-tier vocabulary,
    but the pattern table only ever tags findings ``high``/``medium``/
    ``low``. A floor above this rank therefore cannot match anything —
    the scan is vacuous by construction, not clean. Callers use this to
    tell those two states apart instead of printing "No secrets found"
    for both.
    """
    pats = _COMPILED_PATTERNS if patterns is None else patterns
    return max((severity_rank(p["severity"]) for p in pats), default=-1)


def severity_floor_is_vacuous(min_severity: str, patterns: list[dict] | None = None) -> bool:
    """True when ``min_severity`` filters out every pattern the scanner has."""
    if min_severity.lower() == "all":
        return False
    return severity_rank(min_severity) > emit_vocab_max_rank(patterns)


def scan_project(
    project_root: Path,
    min_severity: str = "all",
    use_index: bool = True,
    include_tests: bool = False,
    stats: dict | None = None,
) -> list[dict]:
    """Scan all indexed files in a project for secrets.

    If use_index is True, reads file paths from the roam index DB.
    Otherwise falls back to walking the filesystem.

    When include_tests is False (the default), files in test, fixture,
    docs, and example directories are suppressed.

    Returns a list of finding dicts sorted by severity (high first),
    then file path, then line number.

    THE DENOMINATOR COUNTS FILES READ, NOT FILES REACHED (W1459)
    ------------------------------------------------------------
    ``files_scanned`` used to be incremented BEFORE ``scan_file`` was
    called, and ``scan_file`` swallows a read failure and returns ``[]``.
    So every file the scanner could not open still landed in the number the
    verdict quotes: "No secrets found (N files scanned)" over a set that
    included files nothing ever decoded. That is the same defect as
    "roam secrets certified files it never opened", surviving in the
    COUNTER after it was removed from the verdict.

    Now the loop publishes the whole partition of the candidate list, and
    every bucket is separately nameable:
    ``files_listed = files_scanned + files_unreadable + files_unresolved
    + files_filtered``.
    """
    root = Path(project_root).resolve()

    index_backed = False
    walk_dropped: list[dict] = []
    if use_index:
        try:
            with open_db(readonly=True) as conn:
                rows = conn.execute("SELECT path FROM files").fetchall()
                file_paths = [row["path"] for row in rows]
            index_backed = True
        except (click.ClickException, sqlite3.DatabaseError):
            file_paths = _walk_for_files(root, dropped=walk_dropped)
    else:
        file_paths = _walk_for_files(root, dropped=walk_dropped)

    # W1466: recover the files discovery's 1 MB cap dropped.
    #
    # That cap is an INDEXER bound -- parsing a multi-megabyte file into a
    # symbol graph is expensive. A line-oriented regex sweep over the same
    # bytes is not, and honouring the indexer's bound here made the scan
    # blind to a whole class of tracked file: the oversized path is in
    # neither the discovered set nor the indexed set, so ``files_unindexed``
    # (discovered - indexed) subtracts it to zero and every counter reads
    # clean. Measured: a tracked 1.4 MB file whose last line held
    # ``AKIA...`` produced "No secrets found (1 files scanned)" and exit 0
    # under --fail-on-found, while the identical key in a 39-byte file was
    # reported.
    #
    # Recovering the file MEASURES what the verdict claims, which is better
    # than refusing to answer. A second, much larger bound still applies so
    # the pass stays bounded -- and unlike the first one, exceeding it is
    # LOUD (see _oversized_beyond_recovery / oversized_blind).
    oversized_recovered, oversized_unrecoverable, unenumerable_dirs = _discovery_gaps(root)
    if oversized_recovered:
        _already = {p.replace("\\", "/") for p in file_paths}
        file_paths = list(file_paths) + [p for p in oversized_recovered if p not in _already]

    all_findings: list[dict] = []
    read_errors: list[dict] = []
    files_scanned = 0
    files_unresolved = 0
    files_filtered = 0
    for rel_path in file_paths:
        if _is_binary(rel_path):
            files_filtered += 1
            continue
        if _in_skip_dir(rel_path):
            files_filtered += 1
            continue
        # Suppress test/fixture/docs files unless --include-tests
        if not include_tests and _is_test_or_doc_path(rel_path):
            files_filtered += 1
            continue

        full_path = root / rel_path
        if not full_path.is_file():
            # Indexed but not present on disk under this root (index built
            # from a different root, file deleted since). Counted, not
            # silently dropped — see stats["files_unresolved"].
            files_unresolved += 1
            continue

        before = len(read_errors)
        file_findings = scan_file(str(full_path), min_severity=min_severity, read_errors=read_errors)
        if len(read_errors) == before:
            # Increment only on a read that actually happened. This line was
            # above the call; that one position is the whole defect.
            files_scanned += 1
        # Store relative path in findings for cleaner output
        for f in file_findings:
            f["file"] = rel_path
        all_findings.extend(file_findings)

    if stats is not None:
        # Out-param so "0 findings because we read 0 files" is separable
        # from "0 findings because the code is clean". Without this the
        # envelope had no field that could tell the two apart.
        stats["files_scanned"] = files_scanned
        stats["files_unreadable"] = len(read_errors)
        stats["read_errors"] = read_errors
        stats["files_unresolved"] = files_unresolved
        stats["files_filtered"] = files_filtered
        stats["files_listed"] = len(file_paths)
        # A path the filesystem fallback could not even name is a candidate
        # that never entered ``files_listed``, so it cannot be recovered from
        # the partition above — it needs its own number.
        stats["files_undiscoverable"] = len(walk_dropped)
        # The index was asked for and could not be used. The fallback walk
        # covers MORE of the disk than the index would have, so this is not
        # itself an incomplete scan — but a consumer reading
        # ``files_unindexed == 0`` deserves to know the zero came from a
        # different measurement than the one it names.
        stats["index_fallback"] = bool(use_index and not index_backed)
        # ...and separable again from "0 findings because the newest files
        # were never in the list we scanned". See _count_unindexed.
        stats["files_unindexed"] = _count_unindexed(root, file_paths) if index_backed else 0
        # W1466: a file discovery declined to open because it exceeds the
        # 1 MB cap is in NEITHER the discovered set nor the indexed set, so
        # ``files_unindexed`` (discovered - indexed) can never see it. It
        # needs its own number for the same reason ``files_undiscoverable``
        # does: it is a candidate that never entered any partition above.
        stats["files_oversized_recovered"] = len(oversized_recovered)
        stats["oversized_recovered_paths"] = list(oversized_recovered)
        # ``None`` when the discovery probe itself could not run -- "I could
        # not determine whether anything was skipped" is not "nothing was".
        stats["files_oversized_unscanned"] = None if oversized_unrecoverable is None else len(oversized_unrecoverable)
        stats["oversized_unscanned_paths"] = list(oversized_unrecoverable or [])
        # W1533: directories the enumerator could not open. Deliberately NOT a
        # ``files_*`` key -- an unknown number of files sit behind each, and a
        # count of directories presented as a file count would be the same
        # false precision this whole family of counters exists to remove.
        # ``None`` means the probe could not run, which is not "none found".
        stats["unenumerable_dirs"] = None if unenumerable_dirs is None else len(unenumerable_dirs)
        stats["unenumerable_paths"] = list(unenumerable_dirs or [])

    # Sort: high severity first, then by file path, then line number
    all_findings.sort(key=lambda f: (-severity_rank(f["severity"]), f["file"], f["line"]))
    return all_findings


def _count_unindexed(root: Path, indexed_paths: list[str]) -> int | None:
    """Count files roam's own discovery would index that the index has never seen.

    An index-backed scan takes its file list from the DB, so a file created
    since the last ``roam index`` is not scanned *at all*. Reporting "no
    secrets found" over a set that omits precisely the newest files is the
    false-clean this counter exists to name: a freshly-pasted credential is,
    by definition, in a file the index has not caught up with.

    Measured against ``discover_files`` rather than a raw filesystem walk.
    That distinction is what keeps the count at 0 on a fresh index — files
    discovery deliberately excludes (generated artefacts, distribution
    templates) are not blind spots, and counting them would make the signal
    fire constantly and be tuned out.

    Returns ``None`` when the probe itself could not run. That is deliberately
    NOT 0: "I could not determine whether anything was missed" and "nothing
    was missed" are different answers, and collapsing the first into the
    second is the very defect this function guards against.
    """
    try:
        from roam.index.discovery import discover_files

        discovered = {str(p).replace("\\", "/") for p in discover_files(root)}
    except (ImportError, OSError, ValueError, sqlite3.DatabaseError):
        return None
    known = {p.replace("\\", "/") for p in indexed_paths}
    return len(discovered - known)


#: Secondary bound for the W1466 recovery pass. Discovery's 1 MB cap is an
#: indexer bound; this is the scanner's own, and it is ~25x larger because a
#: line-oriented regex sweep costs far less than building a symbol graph.
#: Unlike the first cap, exceeding THIS one is loud: the file is named,
#: counted, and makes ``--fail-on-found`` refuse rather than certify.
SECRETS_MAX_RECOVERABLE_SIZE = 25_000_000


def _discovery_gaps(root: Path) -> tuple[list[str], list[str] | None, list[str] | None]:
    """``(recoverable, unrecoverable, unenumerable)`` from one discovery pass.

    ``_count_unindexed`` measures against ``discover_files``, which is the
    right yardstick for scope DECISIONS (generated artefacts, distribution
    templates) but blind to both gaps below, because a path dropped before
    discovery returns appears on NEITHER side of ``discovered - indexed`` and
    subtracts to zero.

    *recoverable* / *unrecoverable* split the files the 1 MB size cap dropped
    (W1466) by whether this command will still read them.

    *unenumerable* is W1533: DIRECTORIES the enumerator could not open. Unlike
    the other two these name no files at all -- an unknown number sit behind
    each -- so a caller may use the list's presence to withhold a completeness
    claim but must never read its length as a file count.

    The two ``None``s mean the probe itself could not run. That is deliberately
    not ``[]`` -- the same discipline ``_count_unindexed`` follows, for the
    same reason.
    """
    try:
        from roam.index.discovery import SKIP_OVERSIZED, SKIP_UNENUMERABLE, discover_files_with_skips

        _discovered, skips = discover_files_with_skips(root)
    except (ImportError, OSError, ValueError, sqlite3.DatabaseError):
        return [], None, None
    recoverable: list[str] = []
    unrecoverable: list[str] = []
    for rel_path in sorted(skips.get(SKIP_OVERSIZED, [])):
        try:
            size = (root / rel_path).stat().st_size
        except OSError:
            unrecoverable.append(rel_path)
            continue
        if size > SECRETS_MAX_RECOVERABLE_SIZE:
            unrecoverable.append(rel_path)
        else:
            recoverable.append(rel_path)
    return recoverable, unrecoverable, sorted(skips.get(SKIP_UNENUMERABLE, []))


def _walk_for_files(root: Path, dropped: list[dict] | None = None) -> list[str]:
    """Walk the filesystem to find scannable files (fallback).

    A path whose relative form cannot be computed (a different drive on
    Windows, a broken junction) is dropped from the candidate list. Pass
    ``dropped`` to record those: without it the file leaves no trace in any
    count, so a scan that never considered it is indistinguishable from one
    that cleared it — the same silent narrowing this module's counters exist
    to close, one layer further out.
    """
    result: list[str] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS]
        for fname in filenames:
            full = os.path.join(dirpath, fname)
            try:
                rel = os.path.relpath(full, root).replace("\\", "/")
            except (ValueError, OSError) as exc:
                if dropped is not None:
                    dropped.append({"path": full, "error": f"{type(exc).__name__}: {exc}"})
                continue
            result.append(rel)
    return result


@roam_capability(
    name="secrets",
    category="reports",
    summary="Scan for hardcoded secrets, API keys, tokens, and passwords",
    maturity="stable",
    mcp_expose=True,
    mcp_preset=("core",),
    side_effect=False,
    task_required=False,
    destructive=False,
    stale_sensitive=True,
    ai_safe=True,
    requires_index=True,
)
@click.command("secrets")
@click.option(
    "--severity",
    type=click.Choice(
        # W1005-followup-C: widened from 3-tier {high, medium, low} to the
        # W547 canonical 7-tier so agents can pass any canonical token.
        # ``all`` is preserved as a 4th option — it's a "no floor" sentinel,
        # NOT a severity, handled distinctly in ``scan_file``/``scan_project``
        # (``min_rank = -1`` short-circuits the rank comparison so every
        # finding passes). Emit-vocab is narrower: the secret patterns at
        # module-load time emit only {high, medium, low}, so a user-passed
        # ``--severity warning`` (rank 3) keeps high(4) and drops medium(2)
        # / low(1); ``--severity critical`` (rank 5) keeps nothing because
        # no pattern emits critical. Aliases (``note``/``unknown``)
        # intentionally omitted — they collapse onto info / sort-below-info
        # in severity_rank, so a user-facing filter on them would be
        # confusing.
        ["all", "critical", "error", "high", "warning", "medium", "low", "info"],
        case_sensitive=False,
    ),
    default="all",
    help=(
        "Show only findings at or above this severity level. Uses the "
        "canonical W547 7-tier ordering (critical > error == high > warning > "
        "medium > low > info); ``all`` is a no-floor sentinel. Patterns emit "
        "high/medium/low today; canonical aliases route through severity_rank()."
    ),
)
@click.option("--fail-on-found", is_flag=True, help="Exit with code 5 if any secrets are found (for CI)")
@click.option(
    "--include-tests",
    is_flag=True,
    default=False,
    help="Include test files, fixtures, docs, and examples in scan",
)
@click.pass_context
def secrets(ctx, severity, fail_on_found, include_tests):
    """Scan for hardcoded secrets, API keys, tokens, and passwords.

    Unlike ``supply-chain`` (which assesses third-party dependency risk),
    this command scans source code for hardcoded credentials and API keys.
    """
    json_mode = ctx.obj.get("json") if ctx.obj else False
    sarif_mode = ctx.obj.get("sarif") if ctx.obj else False
    token_budget = ctx.obj.get("budget", 0) if ctx.obj else 0
    ensure_index()

    project_root = find_project_root()
    scan_stats: dict = {}
    findings = scan_project(project_root, min_severity=severity, include_tests=include_tests, stats=scan_stats)
    # A zero here can mean four very different things. Name which one.
    vacuous_filter = severity_floor_is_vacuous(severity)
    files_scanned = scan_stats.get("files_scanned", 0)
    # A third way a zero can lie: the index predates the files that matter.
    # None means the freshness probe could not run, which is itself not a
    # clean answer, so it counts as incomplete rather than as "0 missed".
    files_unindexed = scan_stats.get("files_unindexed", 0)
    unindexed_blind = files_unindexed is None or files_unindexed > 0
    # A fourth: files that were reached and could not be READ. They used to
    # be counted in files_scanned, so the run reported them as examined.
    files_unreadable = scan_stats.get("files_unreadable", 0)
    files_undiscoverable = scan_stats.get("files_undiscoverable", 0)
    unread_blind = files_unreadable > 0 or files_undiscoverable > 0
    # W1466 — a fifth: files discovery never opened because they exceed the
    # 1 MB cap. They are in neither the discovered nor the indexed set, so
    # ``files_unindexed`` subtracts them to zero. ``None`` means the probe
    # could not run, which is not a clean answer either.
    files_oversized_recovered = scan_stats.get("files_oversized_recovered", 0)
    files_oversized_unscanned = scan_stats.get("files_oversized_unscanned", 0)
    oversized_blind = files_oversized_unscanned is None or files_oversized_unscanned > 0
    # W1533 — a sixth: a directory the ENUMERATOR could not open. Every count
    # above is computed from a list that never contained what is behind it, so
    # each one subtracts to zero and reads clean. Measured: a repo with a
    # credential here produced output byte-identical to a genuinely clean one.
    # Presence alone decides; the number of directories is not a number of
    # files, and nothing here pretends otherwise.
    unenumerable_dirs = scan_stats.get("unenumerable_dirs", 0)
    unenumerable_blind = unenumerable_dirs is None or unenumerable_dirs > 0
    scan_incomplete = (
        vacuous_filter or files_scanned == 0 or unindexed_blind or unread_blind or oversized_blind or unenumerable_blind
    )

    # Compute summary stats. W566 — bucketing delegates to the canonical
    # ``severity_breakdown`` helper. Secrets patterns at module-load
    # time only emit ``high`` / ``medium`` / ``low`` severities, so the
    # ``unknown_bucket=None`` (drop-silently) path is unreachable in
    # practice; keeping the 3-tier vocab + ``drop_zero=False`` makes the
    # zero-padded output byte-identical to the pre-W566 contract that
    # the downstream verdict-build expects.
    total = len(findings)
    # ONE SITE DECIDES. This command already had the invariant right, but it
    # held it by writing `fail_on_found and (total > 0 or scan_incomplete)`
    # three times by hand -- once each in the sarif, json and text branches.
    # Three copies is three chances to drift, and the sibling gate
    # `roam ignore-drift` proved the risk is real: it wrote two copies and one
    # of them lost the `or scan_incomplete` term. Compute the answer once and
    # let all three channels read it.
    gate_failed = gate_should_fail(fail_on_found, findings=total, scan_incomplete=scan_incomplete)
    files_affected = len({f["file"] for f in findings})
    by_severity = severity_breakdown(
        findings,
        key="severity",
        vocab=("high", "medium", "low"),
        unknown_bucket=None,
        drop_zero=False,
    )

    if total == 0 and vacuous_filter:
        # The floor is above every severity the pattern table emits, so no
        # finding could have survived it regardless of what is in the code.
        # Reporting this as "No secrets found" made a vacuous scan
        # indistinguishable from a clean one.
        verdict = (
            f"NOT SCANNED: --severity {severity.lower()} is above the highest severity "
            f"the secret patterns emit ({emit_vocab_max_label()}), so no finding can match. "
            f"Re-run with --severity {emit_vocab_max_label()} or --severity all."
        )
    elif total == 0 and files_scanned == 0:
        # Nothing was read: empty index, index built from another root, or
        # every candidate filtered out before the scan loop.
        verdict = (
            f"NOT SCANNED: 0 files were read "
            f"({scan_stats.get('files_listed', 0)} listed, "
            f"{scan_stats.get('files_unresolved', 0)} unresolved under {project_root}). "
            "Run `roam index` and retry."
        )
    elif total == 0 and unread_blind:
        # The files were listed and reached; the bytes never arrived. Before
        # W1459 these were inside ``files_scanned``, so this run printed
        # "No secrets found (N files scanned)" with the unreadable files
        # inside N — a clean bill of health for files nothing ever opened.
        verdict = (
            f"NOT PROVEN CLEAN: {files_scanned} files read, but {files_unreadable} file(s) "
            f"could not be read and {files_undiscoverable} could not be named — they were "
            "NOT scanned and are NOT in the scanned count."
        )
    elif total == 0 and unindexed_blind:
        # The scan ran, but not over everything on disk. Saying "no secrets
        # found" here would certify files that were never opened.
        if files_unindexed is None:
            missed = "an undetermined number of files are"
        else:
            missed = f"{files_unindexed} file(s) present on disk are"
        verdict = (
            f"NOT PROVEN CLEAN: {files_scanned} indexed files scanned, but {missed} "
            "not in the index and were NOT scanned. Run `roam index` and retry."
        )
    elif total == 0 and oversized_blind:
        # W1466: the file was never opened, so it is in neither the scanned
        # count nor the unindexed count. Saying "no secrets found" here
        # certifies bytes nothing read.
        if files_oversized_unscanned is None:
            skipped = "an undetermined number of files were"
        else:
            skipped = f"{files_oversized_unscanned} file(s) larger than {SECRETS_MAX_RECOVERABLE_SIZE} bytes were"
        verdict = (
            f"NOT PROVEN CLEAN: {files_scanned} files scanned, but {skipped} "
            "past the scanner size bound and were NOT read."
        )
    elif total == 0 and unenumerable_blind:
        # W1533: the enumerator never handed these paths over, so they are in
        # no count above — not scanned, not unindexed, not oversized, not
        # unreadable. Every counter subtracts them to zero and reads clean.
        if unenumerable_dirs is None:
            blocked = "an undetermined number of directories"
        else:
            _p = "directory" if unenumerable_dirs == 1 else "directories"
            blocked = f"{unenumerable_dirs} {_p}"
        named = ", ".join(scan_stats.get("unenumerable_paths", [])[:3])
        verdict = (
            f"NOT PROVEN CLEAN: {files_scanned} files scanned, but {blocked} "
            "could not be opened, so an unknown number of files were never "
            f"listed at all{f' ({named})' if named else ''}."
        )
    elif total == 0:
        verdict = f"No secrets found ({files_scanned} files scanned)"
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
        if unindexed_blind:
            # Findings are real, but the list is a floor rather than a total.
            extra = "an undetermined number of" if files_unindexed is None else str(files_unindexed)
            verdict += f" — and this is a LOWER BOUND: {extra} unindexed file(s) were not scanned"
        if unread_blind:
            verdict += (
                f" — and a LOWER BOUND again: {files_unreadable} file(s) could not be read, "
                f"{files_undiscoverable} could not be named"
            )
        if oversized_blind:
            _n = "an undetermined number of" if files_oversized_unscanned is None else str(files_oversized_unscanned)
            verdict += f" — and a LOWER BOUND again: {_n} file(s) exceeded the scanner size bound"
        if unenumerable_blind:
            _d = "an undetermined number of" if unenumerable_dirs is None else str(unenumerable_dirs)
            verdict += (
                f" — and a LOWER BOUND again: {_d} directory/ies could not be opened, "
                "so an unknown number of files were never listed"
            )

    # --- SARIF output ---
    if sarif_mode:
        from roam.output.sarif import secrets_to_sarif, write_sarif

        sarif = secrets_to_sarif(findings)
        click.echo(write_sarif(sarif))
        if gate_failed:
            from roam.exit_codes import GateFailureError

            raise GateFailureError(verdict)
        return

    # --- JSON output ---
    if json_mode:
        # R22: wrap each finding in {value, confidence, reason}. Consumers
        # that previously read findings[i]["file"] must now read
        # findings[i]["value"]["file"] plus findings[i]["confidence"] /
        # findings[i]["reason"].
        finding_values = [
            {
                "file": f["file"],
                "line": f["line"],
                "severity": f["severity"],
                "pattern": f["pattern_name"],
                "matched_text": f["matched_text"],
                "remediation": f.get("remediation", ""),
            }
            for f in findings
        ]
        finding_triples = wrap_findings(finding_values, classifier=_secrets_classify)
        distribution = confidence_distribution(finding_triples)
        wrapped_verdict = verdict_with_high_count(verdict, distribution)
        envelope = json_envelope(
            "secrets",
            summary={
                "verdict": wrapped_verdict,
                "total_findings": total,
                "files_affected": files_affected,
                # files_scanned counts files whose bytes were READ. The
                # remaining buckets are published beside it rather than
                # folded into it, so no consumer has to guess which
                # question the one number answers.
                "files_scanned": files_scanned,
                "files_unreadable": files_unreadable,
                "files_unresolved": scan_stats.get("files_unresolved", 0),
                "files_filtered": scan_stats.get("files_filtered", 0),
                "files_listed": scan_stats.get("files_listed", 0),
                "files_undiscoverable": files_undiscoverable,
                "index_fallback": scan_stats.get("index_fallback", False),
                "files_unindexed": files_unindexed,
                # W1466: files discovery never opened because of the 1 MB
                # cap. They are in NEITHER files_scanned NOR files_unindexed,
                # so without this bucket they vanish from every partition.
                "files_oversized_recovered": files_oversized_recovered,
                "oversized_recovered_paths": scan_stats.get("oversized_recovered_paths", []),
                "files_oversized_unscanned": files_oversized_unscanned,
                "oversized_unscanned_paths": scan_stats.get("oversized_unscanned_paths", []),
                # W1533: directories the enumerator could not open. Named
                # ``unenumerable_dirs`` and not ``files_*`` on purpose -- an
                # unknown number of files sit behind each, and every sibling
                # count above is a file count. ``None`` means the probe could
                # not run, which is not "none found".
                "unenumerable_dirs": unenumerable_dirs,
                "unenumerable_paths": scan_stats.get("unenumerable_paths", []),
                "severity_floor": severity.lower(),
                "severity_floor_vacuous": vacuous_filter,
                "scan_incomplete": scan_incomplete,
                "partial_success": scan_incomplete,
                "by_severity": by_severity,
                "findings_confidence_distribution": distribution,
            },
            budget=token_budget,
            findings=finding_triples,
        )
        click.echo(to_json(envelope))
        if gate_failed:
            from roam.exit_codes import GateFailureError

            raise GateFailureError(verdict)
        return

    # --- Text output ---
    # index_status()'s own docstring specifies that callers print this
    # "before the VERDICT in text mode" so a reader going top-down cannot
    # miss it. The JSON envelope has carried it in _meta all along; the text
    # branch never did, so a CI log — the surface a human actually reads —
    # showed a bare clean verdict over a possibly-stale file set.
    _index = index_status()
    if isinstance(_index, dict) and _index.get("fresh") is False:
        _hint = _index.get("hint") or "run `roam index` and retry"
        click.echo(f"WARNING: index is stale — {_hint}")
    click.echo(f"VERDICT: {verdict}")
    click.echo()

    if findings:
        rows = []
        for f in findings:
            loc_str = f"{f['file']}:{f['line']}"
            sev = f["severity"].upper()
            rows.append([loc_str, sev, f["pattern_name"], f["matched_text"]])

        click.echo(
            format_table(
                ["Location", "Severity", "Pattern", "Match"],
                rows,
            )
        )
        click.echo()
        click.echo(f"  {total} secrets, {files_affected} files, {by_severity['high']} high severity")
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
    elif unread_blind:
        # The text branch must name the same gap the envelope does; a
        # disclosure that reaches only --json is the W1331 defect.
        click.echo(
            f"  Read {files_scanned} files and found nothing, but {files_unreadable} file(s) "
            f"could not be read and {files_undiscoverable} could not be named — those were "
            "never examined, so this is NOT a clean result."
        )
    elif unindexed_blind:
        # Distinct from the "nothing ran" case below: the scan DID run, it
        # just did not cover everything on disk. Saying otherwise here would
        # reintroduce the false-clean one layer down from the verdict.
        _n = "an undetermined number of" if files_unindexed is None else str(files_unindexed)
        click.echo(
            f"  Scanned {files_scanned} indexed files and found nothing, but {_n} file(s) "
            "on disk were never scanned — this is NOT a clean result."
        )
    elif oversized_blind:
        # W1466: same discipline as the two branches above — the text
        # channel must name the gap the envelope names.
        _n = "an undetermined number of" if files_oversized_unscanned is None else str(files_oversized_unscanned)
        click.echo(
            f"  Scanned {files_scanned} files and found nothing, but {_n} file(s) exceeded the "
            f"{SECRETS_MAX_RECOVERABLE_SIZE}-byte scanner size bound and were never opened — "
            "this is NOT a clean result."
        )
        for skipped in scan_stats.get("oversized_unscanned_paths", [])[:5]:
            click.echo(f"    over size bound: {skipped}")
    elif unenumerable_blind:
        # W1533: same discipline again. This one cannot report how many files
        # were missed, because nothing ever listed them -- so it names the
        # directories instead of inventing a number.
        _d = "an undetermined number of" if unenumerable_dirs is None else str(unenumerable_dirs)
        click.echo(
            f"  Scanned {files_scanned} files and found nothing, but {_d} director(y/ies) could "
            "not be opened, so an unknown number of files were never listed — "
            "this is NOT a clean result."
        )
        for blocked in scan_stats.get("unenumerable_paths", [])[:5]:
            click.echo(f"    could not enumerate: {blocked}")
    elif scan_incomplete:
        click.echo("  Scan did not run over any pattern/file — this is NOT a clean result.")
    else:
        click.echo(f"  No hardcoded secrets detected ({files_scanned} files scanned).")

    # Fail closed: --fail-on-found must not report success when the scan
    # could not have found anything (vacuous --severity floor, or zero
    # files actually read). Exit 0 there meant "clean" for a scan that
    # never ran.
    if gate_failed:
        from roam.exit_codes import GateFailureError

        raise GateFailureError(verdict)
