"""``roam pr-analyze`` — agent-aware PR risk verdict.

Aggregates :command:`roam pr-prep` (diff + critique + pr-risk) with
AI-generated-change heuristics, ``.roam/rules.yml`` enforcement, and a
verdict mapping (INTENTIONAL / SAFE / REVIEW / BLOCK) suitable for
posting as a single GitHub PR comment.

This is the CLI engine behind Roam Agent Review — the v2 subscription
product. The GitHub App calls ``roam pr-analyze --json`` on every PR
open / push and renders the envelope as a sticky PR comment. The same
command runs locally so engineers can dogfood the bot's reasoning
before it posts.

Pipeline
--------
1. **Diff acquisition** — ``--input`` file > stdin (when piped) >
   ``--staged`` > ``COMMIT_RANGE`` argument > unstaged ``git diff``.
2. **Foundation** — invoke ``pr-prep --json`` in-process. Reuses the
   diff + critique + pr-risk aggregation already battle-tested in
   :mod:`roam.commands.cmd_pr_prep`.
3. **AI-likelihood scoring** — six weighted heuristic signals
   (see :func:`_compute_ai_likelihood` for details + weights).
4. **Rules enforcement** — load ``.roam/rules.yml`` (or ``--rules``
   path), match the ``import_from`` pattern against the diff. v1
   handles import bans only; future patterns can extend
   :func:`_check_rules`.
5. **Verdict mapping** — combine the above into INTENTIONAL / SAFE /
   REVIEW / BLOCK with explicit reasons.
6. **Output** — text for humans, JSON for the GitHub App worker.
   ``--gate`` exits 5 (gate failure) when the verdict is BLOCK.
"""

from __future__ import annotations

import fnmatch
import hashlib
import json as _json
import re
import subprocess
import sys
from pathlib import Path

import click
from click.testing import CliRunner

from roam.commands.audit_trail_helpers import (
    AUDIT_TRAIL_SCHEMA,
    DEFAULT_AUDIT_TRAIL_PATH,
    next_sequence_number,
)
from roam.commands.git_helpers import (
    detect_roam_version,
    git_actor,
    git_head_sha,
    git_origin_url,
    utc_timestamp,
)
from roam.commands.resolve import ensure_index
from roam.output.formatter import json_envelope, to_json

EXIT_GATE_BLOCK = 5  # mirrors EXIT_GATE_FAILURE used by cmd_rules / cmd_critique
DEFAULT_BASELINE_PATH = Path(".roam") / "last-pr-analysis.json"
DEFAULT_CACHE_DIR = Path(".roam") / "pr-analyze-cache"
CACHE_VERSION = 1  # bump when the envelope shape changes


def _cache_key(diff_text: str, rules_path: Path, block_threshold: int, language_override: str | None) -> str:
    """Derive a stable cache key from inputs that affect the analysis.

    Inputs hashed: diff text, rules file content (mtime-independent), block
    threshold, language override, cache version. Repeating the same call
    with the same inputs returns the cached envelope; any change invalidates.
    """
    h = hashlib.sha256()
    h.update(f"v={CACHE_VERSION}\n".encode())
    h.update(b"diff=")
    h.update((diff_text or "").encode("utf-8"))
    h.update(b"\nrules=")
    if rules_path.exists():
        try:
            h.update(rules_path.read_bytes())
        except OSError:
            h.update(b"<unreadable>")
    h.update(f"\nthreshold={block_threshold}\n".encode())
    h.update(f"lang={language_override or ''}".encode())
    return h.hexdigest()


def _cache_path(cache_dir: Path, key: str) -> Path:
    return cache_dir / f"{key}.json"


def _load_cache(cache_dir: Path, key: str) -> dict | None:
    """Return cached envelope or None on miss / read error."""
    p = _cache_path(cache_dir, key)
    if not p.exists():
        return None
    try:
        return _json.loads(p.read_text(encoding="utf-8"))
    except (OSError, _json.JSONDecodeError):
        return None


def _save_cache(cache_dir: Path, key: str, bundle: dict) -> None:
    """Persist envelope to the cache. Best-effort; failures are silent."""
    try:
        cache_dir.mkdir(parents=True, exist_ok=True)
        _cache_path(cache_dir, key).write_text(_json.dumps(bundle, indent=2), encoding="utf-8")
    except OSError:
        pass


# Backward-compatible aliases — kept so any out-of-tree consumer of the
# private helpers (tests, ad-hoc scripts) continues to import successfully.
_git_actor = git_actor
_git_origin_short = git_origin_url
_git_head_sha = git_head_sha
_detect_roam_version = detect_roam_version


def _last_record_hash(path: Path) -> str:
    """Return SHA-256 of the last line in the audit-trail JSONL, or '' if none."""
    if not path.exists():
        return ""
    try:
        # Read tail efficiently — last 8 KB is plenty for a single JSON line
        with path.open("rb") as f:
            f.seek(0, 2)
            size = f.tell()
            f.seek(max(0, size - 8192))
            tail = f.read().decode("utf-8", errors="replace")
        last_line = ""
        for line in tail.strip().split("\n"):
            if line.strip():
                last_line = line.strip()
        if not last_line:
            return ""
        return hashlib.sha256(last_line.encode("utf-8")).hexdigest()
    except OSError:
        return ""


def _emit_audit_trail_record(
    *,
    audit_trail_path: Path,
    diff_text: str,
    bundle: dict,
    intent: str | None,
    reviewers_payload: dict | None,
) -> dict:
    """Append a tamper-evident Article 12-shaped record to the audit trail.

    The record includes: invoking actor (from git config), repo + git SHA,
    diff hash (SHA-256), the verdict + structural metrics, the rationale
    summary, the previous record's hash for chain integrity, and the
    full reviewer payload when supplied.

    Designed for local emission first (no signing, no network) — pair
    with cosign / sigstore later via the existing ``roam.attest.cga``
    module when a cryptographic guarantee is required.
    """
    audit_trail_path.parent.mkdir(parents=True, exist_ok=True)
    summary = bundle.get("summary") or {}
    rationale = bundle.get("rationale") or {}

    record = {
        "schema": AUDIT_TRAIL_SCHEMA,
        "sequence_number": next_sequence_number(audit_trail_path),
        "timestamp": utc_timestamp(),
        "tool": "roam-code",
        "tool_version": detect_roam_version(),
        "actor": git_actor(),
        "repo": git_origin_url(),
        "git_sha": git_head_sha(),
        "diff_sha256": hashlib.sha256((diff_text or "").encode("utf-8")).hexdigest(),
        "verdict": summary.get("verdict"),
        "blast_radius": summary.get("blast_radius"),
        "ai_likelihood": summary.get("ai_likelihood"),
        "rule_violations_count": summary.get("rule_violations", 0),
        "high_severity_critique": summary.get("high_severity_critique", 0),
        "intent_marker": intent or None,
        "rationale_summary": rationale.get("summary_text"),
        "suggested_reviewers": [r.get("name") for r in (rationale.get("suggested_reviewers") or [])],
        "previous_record_hash": _last_record_hash(audit_trail_path),
    }
    # Stable JSON encoding so the chain hash is reproducible
    line = _json.dumps(record, separators=(",", ":"), sort_keys=True)
    with audit_trail_path.open("a", encoding="utf-8") as f:
        f.write(line + "\n")
    return record


# --- Baseline / drift detection ---------------------------------------------


def _load_baseline(path: Path) -> dict | None:
    """Load a previously-saved pr-analyze envelope; return None on any failure."""
    if not path.exists():
        return None
    try:
        return _json.loads(path.read_text(encoding="utf-8"))
    except (OSError, _json.JSONDecodeError):
        return None


def _save_baseline(path: Path, bundle: dict) -> None:
    """Write the current envelope to disk for later drift comparison."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_json.dumps(bundle, indent=2), encoding="utf-8")


def _compute_drift(current: dict, baseline: dict | None) -> dict | None:
    """Compute deltas between current envelope and baseline envelope.

    Returns ``None`` if no baseline. Otherwise emits:
      * ``blast_radius_delta`` (current - baseline)
      * ``ai_likelihood_delta``
      * ``new_violations`` — rule violations present now but not before
      * ``resolved_violations`` — present in baseline but not now
      * ``regression`` — any axis got worse (positive deltas)
      * ``improvement`` — every axis got better
    """
    if not baseline:
        return None

    cur_summary = current.get("summary") or {}
    base_summary = baseline.get("summary") or {}

    def _pair(key: str) -> tuple[int, int]:
        return int(cur_summary.get(key) or 0), int(base_summary.get(key) or 0)

    blast_now, blast_before = _pair("blast_radius")
    ai_now, ai_before = _pair("ai_likelihood")

    cur_violations = current.get("rule_violations") or []
    base_violations = baseline.get("rule_violations") or []

    def _vkey(v: dict) -> tuple[str, str, str]:
        return (v.get("rule_id", ""), v.get("file", ""), v.get("matched_target", v.get("matched_import", "")))

    cur_keys = {_vkey(v) for v in cur_violations}
    base_keys = {_vkey(v) for v in base_violations}
    new_keys = cur_keys - base_keys
    resolved_keys = base_keys - cur_keys

    new_violations = [v for v in cur_violations if _vkey(v) in new_keys]
    resolved_violations = [v for v in base_violations if _vkey(v) in resolved_keys]

    blast_delta = blast_now - blast_before
    ai_delta = ai_now - ai_before
    new_count = len(new_violations)
    resolved_count = len(resolved_violations)

    regression = blast_delta > 0 or ai_delta > 0 or new_count > 0
    improvement = (blast_delta < 0 and ai_delta <= 0 and new_count == 0 and resolved_count > 0) or (
        blast_delta <= 0 and ai_delta < 0 and new_count == 0
    )

    return {
        "baseline_timestamp": (baseline.get("_meta") or {}).get("timestamp"),
        "blast_radius_delta": blast_delta,
        "ai_likelihood_delta": ai_delta,
        "new_violations": new_violations,
        "resolved_violations": resolved_violations,
        "new_violation_count": new_count,
        "resolved_violation_count": resolved_count,
        "regression": regression,
        "improvement": improvement,
        "verdict_changed": cur_summary.get("verdict") != base_summary.get("verdict"),
        "previous_verdict": base_summary.get("verdict"),
    }


def _capture_suggest_reviewers(file_paths: list[str], top: int) -> dict:
    """Invoke ``suggest-reviewers`` on the diff's touched files.

    Returns ``{"error": ..., "exit_code": N}`` on failure so the
    pr-analyze envelope still emits even if reviewer scoring is broken
    (e.g. shallow git history, no CODEOWNERS, or no commit author data).
    """
    if not file_paths:
        return {"summary": {"verdict": "no files in diff"}}
    from roam.cli import cli

    runner = CliRunner()
    args = ["--json", "suggest-reviewers", "--top", str(top), *file_paths]
    result = runner.invoke(cli, args)
    try:
        return _json.loads(result.output)
    except Exception as exc:  # noqa: BLE001 — defensive
        return {
            "error": f"suggest-reviewers failed: {exc}",
            "exit_code": result.exit_code,
            "summary": {"verdict": "reviewer scoring failed"},
        }


def _capture_pr_prep(commit_range: str | None, high_callers: int) -> dict:
    """Run ``pr-prep`` in-process and return its parsed JSON envelope.

    Mirrors :func:`roam.commands.cmd_pr_prep._capture_json_subcommand` —
    the same CliRunner-based pattern used by ``cmd_audit`` and ``cmd_pr_prep``
    themselves. Failures are inlined into the returned dict so callers
    can degrade gracefully.
    """
    from roam.cli import cli

    runner = CliRunner()
    args = ["--json", "pr-prep"]
    if commit_range:
        args.append(commit_range)
    args.extend(["--high-callers", str(high_callers)])

    result = runner.invoke(cli, args)
    try:
        return _json.loads(result.output)
    except Exception as exc:  # noqa: BLE001 — defensive: pr-prep failure shouldn't crash pr-analyze
        return {
            "error": f"pr-prep failed to produce JSON: {exc}",
            "exit_code": result.exit_code,
            "summary": {"verdict": "pr-prep error"},
        }


def _acquire_diff(input_file: str | None, commit_range: str | None, staged: bool) -> str:
    """Return the diff text from the highest-priority available source.

    Order: ``--input`` file > stdin (when piped, not a tty) > ``--staged``
    git diff > ``COMMIT_RANGE`` git diff > unstaged ``git diff``.
    Returns empty string on any acquisition failure; downstream signals
    handle that as the trivial-diff case.
    """
    if input_file:
        try:
            return Path(input_file).read_text(encoding="utf-8")
        except OSError:
            return ""

    # stdin if we're being piped to (not a tty)
    if not sys.stdin.isatty():
        try:
            data = sys.stdin.read()
            if data:
                return data
        except Exception:  # noqa: BLE001 — defensive
            pass

    git_args = ["git", "diff"]
    if staged:
        git_args.append("--cached")
    elif commit_range:
        git_args.append(commit_range)

    try:
        proc = subprocess.run(
            git_args,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=10,
            check=False,
        )
        if proc.returncode == 0:
            return proc.stdout
    except (OSError, subprocess.SubprocessError):
        pass
    return ""


# ------------------------------------------------------- AI-likelihood scoring ---

# Patterns suggestive of AI-generated function naming.
_GENERIC_FN_NAME_RE = re.compile(
    r"\b(?:def|function|func)\s+(handle|process|manage|do|run|execute|"
    r"perform|create|update|delete|build|make|fetch)_\w+\s*\(",
    re.IGNORECASE,
)

_COMMENT_LINE_RE = re.compile(r"^(\s*)(#|//|\*\s|/\*|--\s)")
_PYTHON_IMPORT_RE = re.compile(r"^\s*(?:from\s+(\S+)\s+import|\s*import\s+(\S+))")
_JS_IMPORT_RE = re.compile(r"""import\s+.*?from\s+['"]([^'"]+)['"]""")
_FN_DEF_RE = re.compile(
    r"^\s*(?:def\s+\w+|function\s+\w+|func\s+\w+|"
    r"public\s+\w+\s+\w+\s*\(|private\s+\w+\s+\w+\s*\()"
)
_TEST_PATH_HINTS = ("/test", "/__tests__/", ".test.", ".spec.", "_test.py", "test_")

# v2 signal patterns (added 2026-05-06):
#
# Placeholder/stub markers — LLMs love generating "TODO: implement" stubs.
# Slightly opinionated: also includes "raise NotImplementedError" + "pass  #" as
# common stub patterns.
_PLACEHOLDER_RE = re.compile(
    r"\b(TODO|FIXME|XXX|HACK|PLACEHOLDER|TBD)\b|"
    r"\braise\s+NotImplementedError|"
    r"\bpass\s*(#|$)|"
    r"throw\s+new\s+Error\(['\"](?:not\s+implemented|todo)['\"]",
    re.IGNORECASE,
)

# LLM-comment fingerprints — phrasings AI assistants over-use that humans rarely write.
# Built from the patterns identified in CodeSlick's 105-pattern hallucination
# catalog (https://codeslick.dev/learn/ai-code-detection) and DEV.to's 164-signal
# guide (2026). False positives are real (humans do say these things), but the
# DENSITY is the signal — a single occurrence shouldn't trip the score.
_LLM_PHRASE_RE = re.compile(
    r"#\s*(this|note|here we|in this|the following|we use|we can use|"
    r"as you can see|importantly|keep in mind|please note)\b|"
    r"//\s*(this|note|here we|in this|the following|we use|we can use|"
    r"as you can see|importantly|keep in mind|please note)\b|"
    r"#\s*helper\s+function|"
    r"#\s*main\s+entry\s*point|"
    r"//\s*helper\s+function|"
    r"//\s*main\s+entry\s*point",
    re.IGNORECASE,
)

# Suspicious imports — heuristics for LLM hallucination patterns:
# - Numbered modules (foo1, foo_v2 — unusual in human-written code)
# - Suspiciously generic helper modules (utils.helper, helpers.common)
# - typing.* over-imports (LLMs over-import everything from typing)
# Conservative — these are common in some real codebases too.
_SUSPICIOUS_IMPORT_RE = re.compile(
    r"""\b(?:from|import)\s+['"]?(\w+_v\d+|\w+\d+)\b|"""
    r"""\b(?:from|import)\s+['"]?(?:helpers?\.helpers?|utils?\.utils?|common\.common)\b|"""
    r"""\bfrom\s+typing\s+import\s+(?:[A-Z]\w*,\s*){4,}""",  # 5+ typing imports on one line
    re.IGNORECASE,
)

# Default weights — applied when language can't be inferred. Tuned against
# a mixed-language synthetic corpus.
#
# v2 (2026-05-06): three new signals added — placeholder_density,
# llm_phrase_density, suspicious_imports. Existing weights rebalanced
# proportionally so all new diffs still produce a 0-100 composite.
_DEFAULT_WEIGHTS = {
    "add_remove_ratio": 0.08,
    "comment_density": 0.15,
    "test_coverage": 0.12,
    "function_size": 0.10,
    "generic_naming": 0.15,
    "orphan_imports": 0.15,
    # NEW signals (v2 — see CodeSlick / DEV.to AI-detection research, 2026):
    "placeholder_density": 0.10,  # TODO/FIXME/PLACEHOLDER added by stub-style generation
    "llm_phrase_density": 0.10,  # "We can use this approach because..." style comments
    "suspicious_imports": 0.05,  # imports that look like LLM hallucinations (numbered modules, etc.)
}

# Per-language weight overrides. Values must sum to 1.0. Each language
# emphasises the signals that historically have the highest information
# content for AI-shaped diffs in that language.
#
# v2 (2026-05-06): all language weight maps include the 3 new signals.
# Per-language tuning leans on placeholder_density for languages where
# LLM stubs are most visible (Python, JS), llm_phrase_density for
# languages where AI comments stand out (Python, Java).
_LANG_WEIGHT_OVERRIDES = {
    # Python: AI explains itself heavily; comment density + generic naming dominate.
    "python": {
        "add_remove_ratio": 0.08,
        "comment_density": 0.20,
        "test_coverage": 0.15,
        "function_size": 0.05,
        "generic_naming": 0.15,
        "orphan_imports": 0.10,
        "placeholder_density": 0.10,
        "llm_phrase_density": 0.12,
        "suspicious_imports": 0.05,
    },
    # TypeScript / JavaScript: AI auto-imports a lot; orphan imports are the strongest tell.
    "typescript": {
        "add_remove_ratio": 0.08,
        "comment_density": 0.07,
        "test_coverage": 0.15,
        "function_size": 0.10,
        "generic_naming": 0.10,
        "orphan_imports": 0.25,
        "placeholder_density": 0.12,
        "llm_phrase_density": 0.08,
        "suspicious_imports": 0.05,
    },
    "javascript": {
        "add_remove_ratio": 0.08,
        "comment_density": 0.07,
        "test_coverage": 0.15,
        "function_size": 0.10,
        "generic_naming": 0.10,
        "orphan_imports": 0.25,
        "placeholder_density": 0.12,
        "llm_phrase_density": 0.08,
        "suspicious_imports": 0.05,
    },
    # Go: idiomatic Go = small focused funcs, godoc is terse.
    "go": {
        "add_remove_ratio": 0.12,
        "comment_density": 0.08,
        "test_coverage": 0.15,
        "function_size": 0.20,
        "generic_naming": 0.15,
        "orphan_imports": 0.10,
        "placeholder_density": 0.08,
        "llm_phrase_density": 0.07,
        "suspicious_imports": 0.05,
    },
    # Rust: rustdoc encourages doc comments — comment_density is unreliable.
    "rust": {
        "add_remove_ratio": 0.15,
        "comment_density": 0.03,
        "test_coverage": 0.10,
        "function_size": 0.15,
        "generic_naming": 0.15,
        "orphan_imports": 0.15,
        "placeholder_density": 0.10,
        "llm_phrase_density": 0.12,
        "suspicious_imports": 0.05,
    },
    # Java / Kotlin: similar to Python, but generic naming is more pervasive.
    "java": {
        "add_remove_ratio": 0.08,
        "comment_density": 0.15,
        "test_coverage": 0.15,
        "function_size": 0.05,
        "generic_naming": 0.20,
        "orphan_imports": 0.10,
        "placeholder_density": 0.10,
        "llm_phrase_density": 0.12,
        "suspicious_imports": 0.05,
    },
    "kotlin": {
        "add_remove_ratio": 0.08,
        "comment_density": 0.15,
        "test_coverage": 0.15,
        "function_size": 0.05,
        "generic_naming": 0.20,
        "orphan_imports": 0.10,
        "placeholder_density": 0.10,
        "llm_phrase_density": 0.12,
        "suspicious_imports": 0.05,
    },
}

_LANG_BY_EXT = {
    ".py": "python",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".js": "javascript",
    ".jsx": "javascript",
    ".mjs": "javascript",
    ".cjs": "javascript",
    ".vue": "javascript",
    ".go": "go",
    ".rs": "rust",
    ".java": "java",
    ".kt": "kotlin",
    ".kts": "kotlin",
    ".scala": "java",  # close enough for weight purposes
    ".rb": "python",  # comment-heavy convention; closer to Python weights
    ".php": "javascript",  # closer to JS in import density patterns
}


def _detect_primary_language(file_paths: list[str]) -> str | None:
    """Return the most-touched recognised language across the diff's files."""
    if not file_paths:
        return None
    counts: dict[str, int] = {}
    for p in file_paths:
        low = p.lower()
        for ext, lang in _LANG_BY_EXT.items():
            if low.endswith(ext):
                counts[lang] = counts.get(lang, 0) + 1
                break
    if not counts:
        return None
    return max(counts.items(), key=lambda kv: kv[1])[0]


def _parse_diff_into_buckets(diff_text: str) -> tuple[list[str], list[str], list[str]]:
    """Walk a unified diff. Return ``(added_lines, removed_lines, file_paths)``.

    Plain helper extracted so each of the 9 signal computations can take the
    pre-bucketed lists without re-parsing.
    """
    added_lines: list[str] = []
    removed_lines: list[str] = []
    file_paths: list[str] = []
    cur_file: str | None = None
    in_hunk = False

    for line in diff_text.splitlines():
        if line.startswith("+++ "):
            path = line[4:].strip()
            if path.startswith("b/"):
                path = path[2:]
            if path != "/dev/null":
                cur_file = path
                file_paths.append(path)
            else:
                cur_file = None
            in_hunk = False
            continue
        if line.startswith("@@"):
            in_hunk = True
            continue
        if not in_hunk or cur_file is None:
            continue
        if line.startswith("+") and not line.startswith("+++"):
            added_lines.append(line[1:])
        elif line.startswith("-") and not line.startswith("---"):
            removed_lines.append(line[1:])
    return added_lines, removed_lines, file_paths


def _bucket_score(value: float, thresholds: list[tuple[float, int]], default: int) -> int:
    """Map ``value`` to a 0-100 score using ``[(threshold, score), ...]`` in descending order.

    First threshold the value exceeds wins. Eliminates the if/elif chains
    that drove _compute_ai_likelihood's cognitive complexity to 110.
    """
    for threshold, score in thresholds:
        if value > threshold:
            return score
    return default


def _signal_add_remove_ratio(added: int, removed: int) -> tuple[int, float]:
    """Returns ``(score, ratio)`` for the add/remove-balance signal."""
    ratio = added / max(removed, 1)
    score = _bucket_score(ratio, [(10, 90), (5, 60), (3, 40)], default=20)
    return score, ratio


def _signal_comment_density(added_lines: list[str]) -> tuple[int, float, int, int]:
    """Returns ``(score, ratio, comment_count, non_blank_count)``."""
    comment_count = sum(1 for line in added_lines if _COMMENT_LINE_RE.match(line))
    non_blank = max(sum(1 for line in added_lines if line.strip()), 1)
    ratio = comment_count / non_blank
    score = _bucket_score(ratio, [(0.4, 85), (0.25, 60), (0.15, 35)], default=15)
    return score, ratio, comment_count, non_blank


def _signal_test_coverage(file_paths: list[str]) -> tuple[int, float, int]:
    """Returns ``(score, ratio, test_file_count)``."""
    test_files = sum(1 for p in file_paths if any(t in p.lower() for t in _TEST_PATH_HINTS))
    non_test = max(len(file_paths) - test_files, 1)
    ratio = test_files / non_test
    # Inverted thresholds: low coverage = high AI-likelihood.
    if ratio < 0.1:
        score = 75
    elif ratio < 0.3:
        score = 45
    elif ratio < 0.5:
        score = 25
    else:
        score = 10
    return score, ratio, test_files


def _signal_function_size(added_lines: list[str]) -> tuple[int, list[int]]:
    """Returns ``(score, fn_start_indices)``."""
    fn_starts = [i for i, line in enumerate(added_lines) if _FN_DEF_RE.match(line)]
    if len(fn_starts) < 2:
        return 0, fn_starts
    sizes = [fn_starts[i + 1] - fn_starts[i] for i in range(len(fn_starts) - 1)]
    avg = sum(sizes) / len(sizes) if sizes else 0
    if avg < 4 or avg > 80:
        score = 60
    elif avg < 8 or avg > 50:
        score = 35
    else:
        score = 15
    return score, fn_starts


def _signal_generic_naming(added_lines: list[str], fn_count: int) -> tuple[int, int]:
    """Returns ``(score, generic_count)``."""
    generic_count = sum(1 for line in added_lines if _GENERIC_FN_NAME_RE.search(line))
    ratio = generic_count / max(fn_count, 1)
    score = _bucket_score(ratio, [(0.5, 80), (0.25, 50), (0.1, 25)], default=10)
    return score, generic_count


def _split_imports_from_body(added_lines: list[str]) -> tuple[list[str], list[str]]:
    """Partition added lines into (imports, other)."""
    imports: list[str] = []
    other: list[str] = []
    for line in added_lines:
        if _PYTHON_IMPORT_RE.match(line) or _JS_IMPORT_RE.search(line):
            imports.append(line)
        else:
            other.append(line)
    return imports, other


def _import_target(line: str) -> str:
    """Extract the imported module/symbol name from one import line."""
    py_match = _PYTHON_IMPORT_RE.match(line)
    js_match = _JS_IMPORT_RE.search(line)
    if py_match:
        return (py_match.group(1) or py_match.group(2) or "").strip()
    if js_match:
        return js_match.group(1).strip()
    return ""


def _signal_orphan_imports(import_lines: list[str], other_added: list[str]) -> tuple[int, int]:
    """Returns ``(score, orphan_count)``."""
    if not (import_lines and other_added):
        return 0, 0
    body = "\n".join(other_added)
    orphan_count = 0
    for imp in import_lines:
        target = _import_target(imp)
        if not target:
            continue
        name = target.split(".")[-1].split("/")[-1].strip("\"'")
        if name and name not in body:
            orphan_count += 1
    ratio = orphan_count / len(import_lines)
    score = _bucket_score(ratio, [(0.4, 75), (0.2, 45)], default=15)
    return score, orphan_count


def _signal_placeholder_density(added_lines: list[str], non_blank: int) -> tuple[int, int, float]:
    """Returns ``(score, count, ratio)``."""
    count = sum(1 for line in added_lines if _PLACEHOLDER_RE.search(line))
    ratio = count / non_blank if non_blank > 0 else 0.0
    if ratio > 0.10:
        score = 85
    elif ratio > 0.05:
        score = 60
    elif ratio > 0.02:
        score = 35
    elif count > 0:
        score = 15
    else:
        score = 0
    return score, count, ratio


def _signal_llm_phrase_density(added_lines: list[str], comment_count: int) -> tuple[int, int, float]:
    """Returns ``(score, count, ratio)``."""
    count = sum(1 for line in added_lines if _LLM_PHRASE_RE.search(line))
    ratio = count / comment_count if comment_count > 0 else 0.0
    if ratio > 0.5:
        score = 85
    elif ratio > 0.3:
        score = 60
    elif ratio > 0.15:
        score = 35
    elif count >= 2:
        score = 20
    else:
        score = 0
    return score, count, ratio


def _signal_suspicious_imports(import_lines: list[str]) -> tuple[int, int, float]:
    """Returns ``(score, count, ratio)``."""
    count = sum(1 for line in import_lines if _SUSPICIOUS_IMPORT_RE.search(line))
    ratio = count / len(import_lines) if import_lines else 0.0
    if ratio > 0.4:
        score = 80
    elif ratio > 0.2:
        score = 50
    elif count >= 1:
        score = 25
    else:
        score = 0
    return score, count, ratio


def _compute_ai_likelihood(diff_text: str, language_override: str | None = None) -> dict:
    """Heuristic 0-100 score that a diff was AI-generated.

    Nine signals, each scored 0-100 then weighted into a composite. Weights
    are **language-aware** — Python emphasises comment density, TypeScript
    emphasises orphan imports, Go emphasises function-size variance, etc.
    The primary language is detected from file extensions or supplied via
    ``language_override``.

    Signals (per-signal computation lives in dedicated helpers below):

    1. **add/remove ratio** — refactors balance; AI rewrites add-heavy.
    2. **comment-to-code ratio** — AI explains itself; humans rarely do.
    3. **test-coverage ratio** — AI often skips tests for new behavior.
    4. **function-size variance** — extremes (tiny stubs or god-functions) are agent fingerprints.
    5. **generic-naming density** — ``handle_*``/``process_*``/``manage_*``.
    6. **orphan-import density** — imports added with no matching usage in the diff body.
    7. **placeholder density** — TODO/FIXME/NotImplementedError stubs.
    8. **LLM-phrase density** — "we use this approach because…" comment-style fingerprints.
    9. **suspicious imports** — numbered modules / mass typing imports / helper.helper.

    Returns a dict with the composite score, per-signal breakdown, weights,
    and raw metrics so the GitHub App comment can show *why* the score
    landed where it did. Empty / trivial diffs return ``score=0``.
    """
    if not diff_text or not diff_text.strip():
        return {"score": 0, "signals": {}, "weights": {}, "raw_metrics": {}, "reason": "empty diff"}

    added_lines, removed_lines, file_paths = _parse_diff_into_buckets(diff_text)
    if not added_lines and not removed_lines:
        return {"score": 0, "signals": {}, "weights": {}, "raw_metrics": {}, "reason": "no hunks"}

    sig_ratio, add_remove_ratio = _signal_add_remove_ratio(len(added_lines), len(removed_lines))
    sig_comment, comment_ratio, added_comments, added_non_blank = _signal_comment_density(added_lines)
    sig_tests, test_coverage_ratio, test_files = _signal_test_coverage(file_paths)
    sig_size, fn_starts = _signal_function_size(added_lines)
    sig_naming, generic_count = _signal_generic_naming(added_lines, len(fn_starts))
    import_lines, other_added = _split_imports_from_body(added_lines)
    sig_imports, orphan_imports = _signal_orphan_imports(import_lines, other_added)
    sig_placeholder, placeholder_count, placeholder_ratio = _signal_placeholder_density(added_lines, added_non_blank)
    sig_llm_phrase, llm_phrase_count, llm_phrase_ratio = _signal_llm_phrase_density(added_lines, added_comments)
    sig_suspicious, suspicious_import_count, suspicious_ratio = _signal_suspicious_imports(import_lines)

    primary_language = (language_override or _detect_primary_language(file_paths) or "").lower() or None
    weights = _LANG_WEIGHT_OVERRIDES.get(primary_language, _DEFAULT_WEIGHTS)

    signals = {
        "add_remove_ratio": sig_ratio,
        "comment_density": sig_comment,
        "test_coverage": sig_tests,
        "function_size": sig_size,
        "generic_naming": sig_naming,
        "orphan_imports": sig_imports,
        "placeholder_density": sig_placeholder,
        "llm_phrase_density": sig_llm_phrase,
        "suspicious_imports": sig_suspicious,
    }
    # Defensive: any new language weight map missing a signal falls back to 0 weight.
    score = sum(signals[k] * weights.get(k, 0) for k in signals)

    return {
        "score": round(score),
        "signals": signals,
        "weights": weights,
        "primary_language": primary_language or "unknown",
        "raw_metrics": {
            "added_lines": len(added_lines),
            "removed_lines": len(removed_lines),
            "files_touched": len(file_paths),
            "test_files": test_files,
            "comment_ratio": round(comment_ratio, 3),
            "add_remove_ratio": round(add_remove_ratio, 2),
            "new_functions": len(fn_starts),
            "generic_function_names": generic_count,
            "orphan_imports": orphan_imports,
            "test_coverage_ratio": round(test_coverage_ratio, 3),
            "placeholder_count": placeholder_count,
            "placeholder_ratio": round(placeholder_ratio, 3),
            "llm_phrase_count": llm_phrase_count,
            "llm_phrase_ratio": round(llm_phrase_ratio, 3),
            "suspicious_import_count": suspicious_import_count,
            "suspicious_import_ratio": round(suspicious_ratio, 3),
        },
    }


# ----------------------------------------------------------- rules enforcement ---


def _warn_or_raise(msg: str, *, strict: bool, warnings: list[str], cause: Exception | None = None) -> None:
    """In strict mode raise ValueError(msg); in tolerant mode append to warnings.

    Centralises the strict-vs-tolerant branching that drove
    ``_load_rules_yaml``'s cognitive complexity to 71. Callers either
    return immediately after on the early-out branches or continue
    accumulating warnings.
    """
    if strict:
        if cause is not None:
            raise ValueError(msg) from cause
        raise ValueError(msg)
    warnings.append(msg)


def _parse_rules_data(rules_path: Path, *, strict: bool, warnings: list[str]) -> dict | None:
    """Parse the YAML/fallback content of ``rules_path``.

    Returns the parsed top-level dict, or ``None`` when parsing failed
    (warning already accumulated). Pulled out so _load_rules_yaml stays
    flat — this absorbs the 3 separate try/except branches.
    """
    try:
        import yaml  # PyYAML — optional, not a dep

        with rules_path.open("r", encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    except ImportError:
        try:
            from roam.rules.engine import _parse_simple_yaml

            return _parse_simple_yaml(rules_path) or {}
        except Exception as exc:  # noqa: BLE001 — fallback parser failure
            _warn_or_raise(
                f"fallback YAML parser failed for {rules_path}: {exc}",
                strict=strict,
                warnings=warnings,
                cause=exc,
            )
            return None
    except Exception as exc:  # noqa: BLE001 — yaml.YAMLError + OSError + ...
        _warn_or_raise(
            f"YAML parse error in {rules_path}: {exc}",
            strict=strict,
            warnings=warnings,
            cause=exc,
        )
        return None


def _coerce_rule(rule: dict, index: int, rules_path: Path, *, strict: bool, warnings: list[str]) -> dict | None:
    """Validate + type-coerce one rule. Returns None to skip a broken rule."""
    if not isinstance(rule, dict):
        _warn_or_raise(
            f"rule #{index} in {rules_path} is not a mapping (got {type(rule).__name__}); skipping",
            strict=strict,
            warnings=warnings,
        )
        return None

    rid = rule.get("id", f"<unnamed-{index}>")
    out = rule

    # Type-coerce: severity must be a string. A YAML integer like
    # ``severity: 42`` would break later when compared to "BLOCK".
    sev = rule.get("severity")
    if sev is not None and not isinstance(sev, str):
        _warn_or_raise(
            f"rule `{rid}` has non-string severity {sev!r}; coerced to string",
            strict=strict,
            warnings=warnings,
        )
        out = dict(out)
        out["severity"] = str(sev)

    # Type-coerce: forbidden_target_glob must be a string. A non-string
    # glob would never match — drop the rule rather than ship a broken matcher.
    fg = rule.get("forbidden_target_glob")
    if fg is not None and not isinstance(fg, str):
        _warn_or_raise(
            f"rule `{rid}` has non-string forbidden_target_glob {fg!r}; rule will not match anything",
            strict=strict,
            warnings=warnings,
        )
        return None

    return out


def _load_rules_yaml(rules_path: Path, *, strict: bool = False) -> tuple[list[dict], list[str]]:
    """Load ``.roam/rules.yml`` and return ``(rules, warnings)``.

    Uses PyYAML when available, else the in-tree
    :func:`roam.rules.engine._parse_simple_yaml` fallback.

    ``warnings`` is a structured list explaining any silent skips (missing
    file, malformed YAML, type-coerced fields). The pr-analyze envelope
    surfaces them under ``rules_warnings`` so users can see why a rule
    pack didn't behave as expected — instead of silently scoring 0
    violations.

    In ``strict=True`` mode, malformed inputs raise ``ValueError`` so the
    caller (with ``--rules-strict``) can fail the run early. Default
    tolerant mode preserves backward compatibility.

    Refactor (P13): the strict-vs-tolerant branching is collapsed into
    :func:`_warn_or_raise`; YAML parsing into :func:`_parse_rules_data`;
    per-rule type-coercion into :func:`_coerce_rule`. This function is
    now a flat 5-step pipeline.
    """
    warnings: list[str] = []

    if not rules_path.exists():
        _warn_or_raise(f"rules file not found at {rules_path}", strict=strict, warnings=warnings)
        return [], warnings

    data = _parse_rules_data(rules_path, strict=strict, warnings=warnings)
    if data is None:
        return [], warnings

    if not isinstance(data, dict):
        _warn_or_raise(f"top-level YAML in {rules_path} must be a mapping", strict=strict, warnings=warnings)
        return [], warnings

    raw_rules = data.get("rules")
    if not isinstance(raw_rules, list):
        if raw_rules is not None:
            _warn_or_raise(
                f"`rules:` in {rules_path} must be a list, got {type(raw_rules).__name__}",
                strict=strict,
                warnings=warnings,
            )
        return [], warnings

    cleaned: list[dict] = []
    for i, raw in enumerate(raw_rules):
        coerced = _coerce_rule(raw, i, rules_path, strict=strict, warnings=warnings)
        if coerced is not None:
            cleaned.append(coerced)
    return cleaned, warnings


def _added_lines_by_file(diff_text: str) -> dict[str, list[str]]:
    """Parse a unified diff and return per-file added-line lists."""
    out: dict[str, list[str]] = {}
    cur_file: str | None = None
    in_hunk = False
    for line in diff_text.splitlines():
        if line.startswith("+++ "):
            path = line[4:].strip()
            if path.startswith("b/"):
                path = path[2:]
            cur_file = path if path != "/dev/null" else None
            in_hunk = False
            continue
        if line.startswith("@@"):
            in_hunk = True
            continue
        if cur_file is None or not in_hunk:
            continue
        if line.startswith("+") and not line.startswith("+++"):
            out.setdefault(cur_file, []).append(line[1:])
    return out


# --- Per-pattern matchers ----------------------------------------------------

_CLASS_INHERIT_RE = re.compile(r"^\s*class\s+\w+\s*\(([^)]+)\)")
_DECORATOR_RE = re.compile(r"^\s*@([\w.]+)")
# Function-call detection — names plus optional dotted attribute path. Skips
# definition lines (def/class) by checking the line doesn't start with them.
_FUNCTION_CALL_RE = re.compile(r"(?<!def\s)(?<!class\s)\b([\w.]+)\s*\(")


def _match_import_from(line: str, forbidden_glob: str) -> str | None:
    py = _PYTHON_IMPORT_RE.match(line)
    js = _JS_IMPORT_RE.search(line)
    target = ""
    if py:
        target = (py.group(1) or py.group(2) or "").strip()
    elif js:
        target = js.group(1).strip()
    if target and fnmatch.fnmatch(target, forbidden_glob):
        return target
    return None


def _match_function_call(line: str, forbidden_glob: str) -> str | None:
    stripped = line.lstrip()
    # Definitions aren't calls — skip ``def foo(`` and ``class Foo(``.
    if stripped.startswith(("def ", "class ", "function ", "func ", "async def ")):
        return None
    for m in _FUNCTION_CALL_RE.finditer(line):
        target = m.group(1)
        if fnmatch.fnmatch(target, forbidden_glob):
            return target
    return None


def _match_class_inherit(line: str, forbidden_glob: str) -> str | None:
    m = _CLASS_INHERIT_RE.match(line)
    if not m:
        return None
    for raw_base in m.group(1).split(","):
        base = raw_base.strip().split("=", 1)[0].strip()  # strip kwargs like metaclass=X
        if not base:
            continue
        if fnmatch.fnmatch(base, forbidden_glob):
            return base
    return None


def _match_decorator_use(line: str, forbidden_glob: str) -> str | None:
    m = _DECORATOR_RE.match(line)
    if not m:
        return None
    name = m.group(1)
    if fnmatch.fnmatch(name, forbidden_glob):
        return name
    return None


_PATTERN_MATCHERS = {
    "import_from": _match_import_from,
    "function_call": _match_function_call,
    "class_inherit": _match_class_inherit,
    "decorator_use": _match_decorator_use,
}


def _check_rules(diff_text: str, rules: list[dict]) -> list[dict]:
    """Match each rule against the diff.

    v1.1 supports four pattern types via ``_PATTERN_MATCHERS``:

    * ``import_from`` — Python ``from X import`` / ``import X`` and
      JS/TS ``import ... from "X"`` whose target matches the forbidden glob.
    * ``function_call`` — any call ``name(`` or ``ns.name(`` whose
      qualified name matches (e.g. ``os.system``, ``eval``, ``pickle.loads``).
    * ``class_inherit`` — a class declaration whose base list contains a
      forbidden base (e.g. ``class Foo(DangerousMixin)``).
    * ``decorator_use`` — a decorator line ``@name`` or ``@ns.name``
      matching the forbidden glob (e.g. ``@deprecated``, ``@unsafe.*``).

    Unknown pattern names are skipped silently so future rule files
    don't crash older Roam clients.
    """
    if not diff_text or not rules:
        return []

    added_by_file = _added_lines_by_file(diff_text)
    violations: list[dict] = []

    for rule in rules:
        rule_id = rule.get("id", "<unnamed>")
        pattern = rule.get("pattern", "")
        matcher = _PATTERN_MATCHERS.get(pattern)
        if matcher is None:
            continue
        source_glob = rule.get("source_glob", "*")
        forbidden_glob = rule.get("forbidden_target_glob", "")
        severity = (rule.get("severity") or "WARN").upper()
        description = rule.get("description", "")
        if not forbidden_glob:
            continue

        for path, added_lines in added_by_file.items():
            if not fnmatch.fnmatch(path, source_glob):
                continue
            for line in added_lines:
                target = matcher(line, forbidden_glob)
                if target is None:
                    continue
                violations.append(
                    {
                        "rule_id": rule_id,
                        "severity": severity,
                        "description": description,
                        "pattern": pattern,
                        "file": path,
                        "matched_import": target,  # legacy name kept for stability
                        "matched_target": target,
                        "line_excerpt": line.strip()[:120],
                    }
                )
    return violations


# --------------------------------------------------------------- verdict logic ---

_INTENTIONAL_RE = re.compile(r"\[intentional\]|^intentional\s*[:!]\s*", re.IGNORECASE)


def _concern_high_blast(blast_radius: int) -> dict | None:
    if blast_radius < 60:
        return None
    return {
        "concern": "high blast radius",
        "score": blast_radius,
        "evidence": (
            f"pr-risk composite scored {blast_radius}/100 — the change touches "
            f"high fan-in or high-churn files. Changes at this radius tend to "
            f"trigger cross-team coordination."
        ),
    }


def _concern_ai_likelihood(ai: dict) -> dict | None:
    ai_score = ai.get("score", 0)
    if ai_score < 60:
        return None
    signals = ai.get("signals", {})
    top = sorted(signals.items(), key=lambda kv: -kv[1])[:3]
    bullets = [f"{name}: {val}/100" for name, val in top if val > 30]
    if bullets:
        evidence = f"Composite {ai_score}/100 driven by: " + ", ".join(bullets)
    else:
        evidence = f"Composite {ai_score}/100 across the heuristic signals."
    return {"concern": "AI-likelihood elevated", "score": ai_score, "evidence": evidence}


def _concerns_rule_violations(rule_violations: list[dict]) -> list[dict]:
    out: list[dict] = []
    block_rules = [v for v in rule_violations if v.get("severity") == "BLOCK"]
    warn_rules = [v for v in rule_violations if v.get("severity") in ("WARN", "WARNING")]
    if block_rules:
        rule_list = ", ".join(f"`{v['rule_id']}`" for v in block_rules[:5])
        out.append(
            {
                "concern": f"{len(block_rules)} BLOCK-severity rule violation(s)",
                "evidence": f"Triggered: {rule_list}. See .roam/rules.yml for definitions.",
            }
        )
    if warn_rules:
        rule_list = ", ".join(f"`{v['rule_id']}`" for v in warn_rules[:5])
        out.append(
            {
                "concern": f"{len(warn_rules)} WARN-severity rule violation(s)",
                "evidence": f"Triggered: {rule_list}.",
            }
        )
    return out


def _concern_high_severity_critique(count: int) -> dict | None:
    if count <= 0:
        return None
    return {
        "concern": f"{count} high-severity critique finding(s)",
        "evidence": "See the `pr_prep.critique` section for clones-not-edited, blast-radius, intent-mismatch findings.",
    }


_NEXT_STEP_BY_VERDICT = {
    "BLOCK": "Resolve every BLOCK-severity finding before merge, or mark with `[intentional]` if the change is conscious.",
    "REVIEW": None,  # REVIEW gets two steps; handled inline.
    "INTENTIONAL": (
        "Verdict bypassed by explicit `[intentional]` marker. Reviewer still recommended for high-blast PRs."
    ),
    "SAFE": "No structural concerns at the configured thresholds. Standard review still recommended.",
}


def _compose_next_steps(verdict: str, ai_score: int) -> list[str]:
    """Build the verdict-keyed next-steps list."""
    if verdict == "REVIEW":
        steps = [
            "Request reviewers familiar with the affected directories.",
            "If concerns are intentional, add `[intentional]` to the commit/PR title.",
        ]
    else:
        step = _NEXT_STEP_BY_VERDICT.get(verdict)
        steps = [step] if step else []
    if ai_score >= 70 and verdict not in ("INTENTIONAL", "BLOCK"):
        steps.append(
            "Consider adding tests for the new behaviour — coverage on AI-shaped diffs is the highest-leverage signal flip."
        )
    return steps


def _extract_suggested_reviewers(reviewers_payload: dict | None) -> list[dict]:
    if not reviewers_payload or "error" in reviewers_payload:
        return []
    candidates = reviewers_payload.get("reviewers") or reviewers_payload.get("suggestions") or []
    out: list[dict] = []
    for r in candidates[:5]:
        if isinstance(r, dict):
            name = r.get("name") or r.get("author") or r.get("login") or "?"
            out.append(
                {
                    "name": name,
                    "score": r.get("score") or r.get("expertise_score"),
                    "source": r.get("source") or r.get("signal") or "",
                }
            )
    return out


def _build_rationale(
    *,
    verdict: str,
    blast_radius: int,
    ai: dict,
    rule_violations: list[dict],
    high_severity_findings: int,
    reasons: list[str],
    intent: str,
    reviewers_payload: dict | None = None,
) -> dict:
    """Compose a human-readable rationale block for the verdict.

    Used by ``--explain`` mode and by the GitHub App's PR comment
    renderer. Each concern is structured (title + evidence + signal
    score) so downstream surfaces can render in their own style.

    Refactor (P13): per-concern collectors + next-steps composer + reviewer
    extractor live as small helpers above. This function is now a flat
    coordinator.
    """
    concerns: list[dict] = []
    for builder in (
        _concern_high_blast(blast_radius),
        _concern_ai_likelihood(ai),
    ):
        if builder is not None:
            concerns.append(builder)
    concerns.extend(_concerns_rule_violations(rule_violations))
    crit_concern = _concern_high_severity_critique(high_severity_findings)
    if crit_concern is not None:
        concerns.append(crit_concern)

    ai_score = ai.get("score", 0)
    next_steps = _compose_next_steps(verdict, ai_score)
    suggested_reviewers = _extract_suggested_reviewers(reviewers_payload)

    if suggested_reviewers and verdict in ("REVIEW", "BLOCK"):
        top_names = ", ".join(f"@{r['name']}" for r in suggested_reviewers[:3] if r["name"] != "?")
        if top_names:
            next_steps.insert(0, f"Suggested reviewers: {top_names}.")

    summary_text_parts = [f"Verdict: **{verdict}**."]
    if concerns:
        summary_text_parts.append(
            f"Surfaced {len(concerns)} structural concern(s): " + ", ".join(c["concern"] for c in concerns) + "."
        )
    elif verdict == "INTENTIONAL":
        summary_text_parts.append(f"Intent marker: {intent[:80]}.")
    else:
        summary_text_parts.append("All structural signals clean at the configured thresholds.")

    return {
        "summary_text": " ".join(summary_text_parts),
        "concerns": concerns,
        "next_steps": next_steps,
        "reasons_terse": reasons,
        "suggested_reviewers": suggested_reviewers,
    }


def _determine_verdict(
    blast_radius: int,
    ai_likelihood: int,
    rule_violations: list[dict],
    high_severity_findings: int,
    intent: str,
    block_threshold: int,
    pr_prep_error: bool,
) -> tuple[str, list[str]]:
    """Map signals to one of INTENTIONAL / SAFE / REVIEW / BLOCK with reasons.

    Order: explicit ``[intentional]`` marker wins. Then BLOCK conditions.
    Then REVIEW conditions. Default SAFE.
    """
    if intent and _INTENTIONAL_RE.search(intent):
        return "INTENTIONAL", ["explicit [intentional] marker on PR or commit"]

    block_reasons: list[str] = []
    block_rules = [v for v in rule_violations if v.get("severity") == "BLOCK"]
    if block_rules:
        block_reasons.append(f"{len(block_rules)} BLOCK-severity rule violation(s)")
    if blast_radius >= block_threshold:
        block_reasons.append(f"blast radius {blast_radius} ≥ threshold {block_threshold}")
    if ai_likelihood >= 90 and blast_radius >= 60:
        block_reasons.append(f"high AI-likelihood ({ai_likelihood}) combined with high blast radius ({blast_radius})")
    if block_reasons:
        return "BLOCK", block_reasons

    review_reasons: list[str] = []
    if pr_prep_error:
        review_reasons.append("pr-prep aggregator returned an error — manual review")
    warn_rules = [v for v in rule_violations if v.get("severity") in ("WARN", "WARNING")]
    if high_severity_findings > 0:
        review_reasons.append(f"{high_severity_findings} high-severity critique finding(s)")
    if warn_rules:
        review_reasons.append(f"{len(warn_rules)} WARN-severity rule violation(s)")
    if blast_radius >= 60:
        review_reasons.append(f"blast radius {blast_radius} ≥ 60")
    if ai_likelihood >= 70:
        review_reasons.append(f"AI-likelihood {ai_likelihood} ≥ 70")
    if review_reasons:
        return "REVIEW", review_reasons

    return "SAFE", ["all signals below review thresholds"]


# ----------------------------------------------------------- batch processing ---


def _process_single_diff(
    diff_path: str,
    rules_file: str | None,
    block_threshold: int,
    high_callers: int,
    language_override: str | None,
    cache: bool = False,
    cache_dir: str | None = None,
) -> dict:
    """Process one diff file and return a row dict. Top-level so it can be pickled
    for multiprocessing.Pool when --parallel is used.

    P10/P11 fix: ``cache`` + ``cache_dir`` now propagate to the inner CLI
    invocation. Without this, batch mode silently ignored ``--cache`` —
    repeated batches were as slow as the first run.
    """
    from roam.cli import cli

    args = ["--json", "pr-analyze", "--input", diff_path]
    if rules_file:
        args.extend(["--rules", rules_file])
    if block_threshold != 85:
        args.extend(["--block-threshold", str(block_threshold)])
    if high_callers != 10:
        args.extend(["--high-callers", str(high_callers)])
    if language_override:
        args.extend(["--language", language_override])
    if cache:
        args.append("--cache")
        if cache_dir:
            args.extend(["--cache-dir", cache_dir])

    runner = CliRunner()
    result = runner.invoke(cli, args)
    row: dict = {"file": Path(diff_path).name}
    try:
        env = _json.loads(result.output)
        s = env.get("summary") or {}
        row["verdict"] = s.get("verdict")
        row["blast_radius"] = s.get("blast_radius")
        row["ai_likelihood"] = s.get("ai_likelihood")
        row["rule_violations"] = s.get("rule_violations")
        # Surface cache hit in the row so batch summary can compute hit-rate.
        row["cache_hit"] = bool(env.get("cache_hit"))
    except Exception as exc:  # noqa: BLE001 — defensive
        row["error"] = f"parse failed: {exc}"
    return row


def _run_batch_serial(
    paths,
    rules_file,
    block_threshold,
    high_callers,
    language_override,
    cache,
    cache_dir,
    accept,
):
    """Process the batch one file at a time. Deterministic order."""
    for idx, p in enumerate(paths, 1):
        row = _process_single_diff(
            str(p), rules_file, block_threshold, high_callers, language_override, cache, cache_dir
        )
        accept(row, idx)


def _run_batch_parallel(
    paths,
    rules_file,
    block_threshold,
    high_callers,
    language_override,
    cache,
    cache_dir,
    parallel,
    accept,
):
    """Process the batch via ProcessPoolExecutor. Order is completion-order, not input-order."""
    from concurrent.futures import ProcessPoolExecutor, as_completed

    with ProcessPoolExecutor(max_workers=parallel) as pool:
        future_to_path = {
            pool.submit(
                _process_single_diff,
                str(p),
                rules_file,
                block_threshold,
                high_callers,
                language_override,
                cache,
                cache_dir,
            ): p
            for p in paths
        }
        for idx, fut in enumerate(as_completed(future_to_path), 1):
            try:
                row = fut.result()
            except Exception as exc:  # noqa: BLE001 — surface worker crashes
                row = {"file": future_to_path[fut].name, "error": f"worker crashed: {exc}"}
            accept(row, idx)


def _emit_batch(
    ctx,
    *,
    batch_dir: str,
    rules_file: str | None,
    block_threshold: int,
    high_callers: int,
    language_override: str | None,
    json_mode: bool,
    gate: bool,
    parallel: int = 0,
    show_progress: bool = False,
    cache: bool = False,
    cache_dir: str | None = None,
) -> None:
    """Run pr-analyze across every *.diff / *.patch in ``batch_dir``.

    Emits a summary envelope per file plus aggregate counts. Uses the
    existing CLI in-process via CliRunner so each file gets the full
    analysis pipeline (foundation + AI + rules + verdict).

    Performance options:

    * ``parallel`` — process N files concurrently using a process pool.
      Defaults to sequential (parallel=0) for deterministic order. Each
      worker runs an independent CliRunner invocation; pr-prep + pr-analyze
      are CPU-bound enough that 4-8x speedup is typical.
    * ``show_progress`` — emit a "Analysing N/M (file)..." stderr line per
      file so long batches don't feel hung.
    """
    base = Path(batch_dir)
    paths = sorted([*base.glob("*.diff"), *base.glob("*.patch")])

    per_file: list[dict] = []
    verdict_counts: dict[str, int] = {"INTENTIONAL": 0, "SAFE": 0, "REVIEW": 0, "BLOCK": 0}

    total = len(paths)

    def _accept(row: dict, idx: int) -> None:
        if show_progress and total > 0:
            click.echo(f"  [{idx}/{total}] {row.get('file', '?')} -> {row.get('verdict', 'ERROR')}", err=True)
        per_file.append(row)
        v = row.get("verdict")
        if v:
            verdict_counts[v] = verdict_counts.get(v, 0) + 1

    if parallel > 1 and total > 1:
        _run_batch_parallel(
            paths, rules_file, block_threshold, high_callers, language_override, cache, cache_dir, parallel, _accept
        )
    else:
        _run_batch_serial(
            paths, rules_file, block_threshold, high_callers, language_override, cache, cache_dir, _accept
        )

    worst_verdict = "SAFE"
    for v in ("BLOCK", "REVIEW", "SAFE", "INTENTIONAL"):
        if verdict_counts.get(v, 0) > 0:
            worst_verdict = v
            break

    cache_hits = sum(1 for r in per_file if r.get("cache_hit"))
    summary = {
        "verdict": f"batch worst: {worst_verdict}",
        "files_processed": len(per_file),
        "verdict_counts": verdict_counts,
        "worst_verdict": worst_verdict,
        "batch_dir": str(base),
        "parallel_workers": parallel if parallel > 1 else 1,
        "cache_hits": cache_hits,
        "cache_hit_rate": round(cache_hits / len(per_file), 3) if per_file else 0,
    }
    bundle = {"summary": summary, "files": per_file}

    if json_mode:
        click.echo(to_json(json_envelope("pr-analyze", **bundle)))
    else:
        click.echo(f"VERDICT: {summary['verdict']}")
        click.echo(f"  files processed: {len(per_file)}")
        click.echo(f"  counts: {verdict_counts}")
        if cache and per_file:
            click.echo(f"  cache hits:      {cache_hits}/{len(per_file)} ({summary['cache_hit_rate']:.0%})")
        click.echo()
        click.echo(f"{'File':<40}  {'Verdict':<12}  {'Blast':>5}  {'AI':>5}  {'Rules':>5}")
        click.echo("-" * 80)
        for row in per_file:
            v = row.get("verdict", "?")
            b = row.get("blast_radius", "?")
            a = row.get("ai_likelihood", "?")
            r = row.get("rule_violations", "?")
            err = row.get("error", "")
            if err:
                click.echo(f"{row['file']:<40}  ERROR: {err[:30]}")
            else:
                click.echo(f"{row['file']:<40}  {str(v):<12}  {str(b):>5}  {str(a):>5}  {str(r):>5}")

    if gate and worst_verdict == "BLOCK":
        sys.exit(EXIT_GATE_BLOCK)


# ---------------------------------------------------------------- main command ---


@click.command(name="pr-analyze")
@click.argument("commit_range", required=False, default=None)
@click.option(
    "--input",
    "input_file",
    type=click.Path(exists=True, dir_okay=False),
    default=None,
    help="Read diff from file instead of stdin / git diff.",
)
@click.option("--staged", is_flag=True, help="Analyse staged changes.")
@click.option(
    "--rules",
    "rules_file",
    type=click.Path(),
    default=None,
    help="Path to rules.yml (default: auto-detect .roam/rules.yml).",
)
@click.option(
    "--rules-strict",
    is_flag=True,
    help="Fail (exit 5) if the rules file is missing or malformed; default tolerant.",
)
@click.option(
    "--intent",
    default=None,
    help="PR title or commit message — checked for the [intentional] marker.",
)
@click.option(
    "--block-threshold",
    type=int,
    default=85,
    show_default=True,
    help="Blast-radius score (0-100) at or above which the verdict becomes BLOCK.",
)
@click.option("--gate", is_flag=True, help="Exit 5 (gate failure) when the verdict is BLOCK.")
@click.option(
    "--high-callers",
    type=int,
    default=10,
    show_default=True,
    help="Direct-caller threshold passed through to critique.",
)
@click.option(
    "--explain",
    is_flag=True,
    help="Verbose human-readable rationale (concerns, evidence, next steps). Pair with --json for programmatic access.",
)
@click.option(
    "--quiet",
    is_flag=True,
    help="CI-friendly: VERDICT line only. Mutually exclusive with --explain. Use --json for programmatic data.",
)
@click.option(
    "--language",
    "language_override",
    type=click.Choice(
        ["python", "typescript", "javascript", "go", "rust", "java", "kotlin"],
        case_sensitive=False,
    ),
    default=None,
    help="Override auto-detected primary language (changes AI-likelihood signal weights).",
)
@click.option(
    "--with-reviewers/--no-reviewers",
    default=False,
    show_default=True,
    help="Suggest reviewers for the touched files (calls suggest-reviewers internally).",
)
@click.option(
    "--reviewers-top",
    type=int,
    default=3,
    show_default=True,
    help="Number of reviewers to suggest when --with-reviewers is set.",
)
@click.option(
    "--audit-trail",
    is_flag=True,
    help="Append an EU AI Act Article 12-shaped record to the audit trail (.roam/audit-trail.jsonl).",
)
@click.option(
    "--audit-trail-path",
    type=click.Path(),
    default=None,
    help=f"Override audit-trail JSONL path (default: {DEFAULT_AUDIT_TRAIL_PATH}).",
)
@click.option(
    "--batch",
    "batch_dir",
    type=click.Path(exists=True, file_okay=False),
    default=None,
    help="Process every *.diff / *.patch in this directory; emits a summary envelope per file.",
)
@click.option(
    "--cache/--no-cache",
    default=False,
    show_default=True,
    help=f"Cache envelopes by sha256(diff+rules+threshold) at {DEFAULT_CACHE_DIR}/. Repeats are instant.",
)
@click.option(
    "--cache-dir",
    type=click.Path(),
    default=None,
    help=f"Override cache directory (default: {DEFAULT_CACHE_DIR}).",
)
@click.option(
    "--parallel",
    type=int,
    default=0,
    show_default=True,
    help="Process batch files concurrently with N workers (0 = sequential, deterministic order).",
)
@click.option(
    "--progress",
    "show_progress",
    is_flag=True,
    help="Emit per-file progress lines to stderr while processing a batch.",
)
@click.option(
    "--save-baseline",
    is_flag=True,
    help=f"Save the current envelope to {DEFAULT_BASELINE_PATH} for future drift comparison.",
)
@click.option(
    "--baseline",
    "baseline_path",
    type=click.Path(),
    default=None,
    help=f"Compare against the envelope at this path (default: {DEFAULT_BASELINE_PATH} if it exists).",
)
@click.pass_context
def pr_analyze(
    ctx,
    commit_range: str | None,
    input_file: str | None,
    staged: bool,
    rules_file: str | None,
    rules_strict: bool,
    intent: str | None,
    block_threshold: int,
    gate: bool,
    high_callers: int,
    explain: bool,
    quiet: bool,
    language_override: str | None,
    with_reviewers: bool,
    reviewers_top: int,
    audit_trail: bool,
    audit_trail_path: str | None,
    save_baseline: bool,
    baseline_path: str | None,
    batch_dir: str | None,
    cache: bool,
    cache_dir: str | None,
    parallel: int,
    show_progress: bool,
) -> None:
    """Analyse a PR diff for structural risk and AI-likelihood.

    Aggregates ``pr-prep`` (diff + critique + pr-risk) with AI-generated-
    change heuristics, ``.roam/rules.yml`` enforcement, and a verdict
    mapping (INTENTIONAL / SAFE / REVIEW / BLOCK) suitable for posting
    as a single GitHub PR comment.

    \b
    Examples:
      git diff | roam pr-analyze
      roam pr-analyze main..HEAD
      roam pr-analyze --staged --gate            # CI gate
      roam pr-analyze --input pr.diff --json     # automation

    The CLI engine behind Roam Agent Review.
    """
    json_mode = ctx.obj.get("json") if ctx.obj else False
    ensure_index()

    # P.6 — soft warning when --quiet + --json (--json wins, but be explicit).
    if quiet and json_mode:
        click.echo("Warning: --quiet ignored when --json is set (json envelope contains all data).", err=True)

    # P.9 — oversubscription warning when --parallel exceeds CPU count.
    if parallel > 1:
        import os as _os

        cpu = _os.cpu_count() or 1
        if parallel > cpu:
            click.echo(
                f"Warning: --parallel {parallel} > cpu_count {cpu}; oversubscription typically slows batches.",
                err=True,
            )

    # ---- batch mode short-circuits the normal pipeline ----
    if batch_dir:
        _emit_batch(
            ctx,
            batch_dir=batch_dir,
            rules_file=rules_file,
            block_threshold=block_threshold,
            high_callers=high_callers,
            language_override=language_override,
            json_mode=json_mode,
            gate=gate,
            parallel=parallel,
            show_progress=show_progress,
            cache=cache,
            cache_dir=cache_dir,
        )
        return

    diff_text = _acquire_diff(input_file, commit_range, staged)

    # Cache lookup — bypasses pr-prep (the slow part) on hit.
    cache_dir_path = Path(cache_dir) if cache_dir else DEFAULT_CACHE_DIR
    rules_path = Path(rules_file) if rules_file else (Path(".roam") / "rules.yml")
    cached_bundle: dict | None = None
    if cache:
        key = _cache_key(diff_text, rules_path, block_threshold, language_override)
        cached_bundle = _load_cache(cache_dir_path, key)
        if cached_bundle is not None:
            # Surface as top-level keys so json_envelope's _meta rebuild doesn't strip them.
            cached_bundle["cache_hit"] = True
            cached_bundle["cache_key"] = key
            if json_mode:
                click.echo(to_json(json_envelope("pr-analyze", **cached_bundle)))
            elif quiet:
                s = cached_bundle.get("summary") or {}
                click.echo(
                    f"VERDICT: {s.get('verdict', '?')} (cached, blast {s.get('blast_radius', '?')}, "
                    f"ai {s.get('ai_likelihood', '?')}, rules {s.get('rule_violations', 0)})"
                )
            else:
                s = cached_bundle.get("summary") or {}
                click.echo(f"VERDICT: {s.get('verdict', '?')} [cache hit]")
            if gate and (cached_bundle.get("summary") or {}).get("verdict") == "BLOCK":
                sys.exit(EXIT_GATE_BLOCK)
            return

    prep_payload = _capture_pr_prep(commit_range, high_callers)
    ai = _compute_ai_likelihood(diff_text, language_override=language_override)

    explicit_rules_path = rules_file is not None  # don't warn about default missing
    try:
        rules, rules_warnings = _load_rules_yaml(rules_path, strict=rules_strict)
    except ValueError as exc:
        # --rules-strict: surface the failure as a CI-friendly gate exit
        click.echo(f"VERDICT: rules-validate FAILED ({exc})", err=True)
        sys.exit(EXIT_GATE_BLOCK)

    # Suppress "missing" warning for the default path — only warn when the
    # user explicitly pointed at a path that doesn't exist.
    if not explicit_rules_path:
        rules_warnings = [w for w in rules_warnings if "not found" not in w]

    rule_violations = _check_rules(diff_text, rules)

    summary = prep_payload.get("summary") or {}
    blast_radius = int(summary.get("pr_risk_score") or 0)
    high_severity = int(summary.get("high_severity_findings") or 0)
    pr_prep_error = bool(prep_payload.get("error"))

    verdict, reasons = _determine_verdict(
        blast_radius=blast_radius,
        ai_likelihood=ai["score"],
        rule_violations=rule_violations,
        high_severity_findings=high_severity,
        intent=intent or "",
        block_threshold=block_threshold,
        pr_prep_error=pr_prep_error,
    )

    reviewers_payload: dict | None = None
    if with_reviewers:
        touched_files = sorted(_added_lines_by_file(diff_text).keys())
        if touched_files:
            reviewers_payload = _capture_suggest_reviewers(touched_files, reviewers_top)

    rationale = _build_rationale(
        verdict=verdict,
        blast_radius=blast_radius,
        ai=ai,
        rule_violations=rule_violations,
        high_severity_findings=high_severity,
        reasons=reasons,
        intent=intent or "",
        reviewers_payload=reviewers_payload,
    )

    bundle = {
        "summary": {
            "verdict": verdict,
            "blast_radius": blast_radius,
            "ai_likelihood": ai["score"],
            "rule_violations": len(rule_violations),
            "high_severity_critique": high_severity,
            "reasons": reasons,
        },
        "rationale": rationale,
        "pr_prep": prep_payload,
        "ai_likelihood": ai,
        "rule_violations": rule_violations,
        "rules_loaded": len(rules),
        "rules_path": str(rules_path) if rules_path.exists() else None,
        "rules_warnings": rules_warnings,
        "intent": intent,
        "reviewers": reviewers_payload,
    }

    # --- Baseline drift detection -----------------------------------------
    base_path = Path(baseline_path) if baseline_path else DEFAULT_BASELINE_PATH
    baseline_envelope = _load_baseline(base_path)
    drift = _compute_drift(bundle, baseline_envelope)
    if drift:
        bundle["drift"] = drift
        # Adjust verdict ONE tier upward on regression (SAFE → REVIEW; REVIEW → BLOCK)
        if drift["regression"] and verdict == "SAFE":
            verdict = "REVIEW"
            reasons.append(
                f"Drift regression vs baseline: blast {drift['blast_radius_delta']:+d}, "
                f"ai {drift['ai_likelihood_delta']:+d}, +{drift['new_violation_count']} violations"
            )
            bundle["summary"]["verdict"] = verdict
            bundle["summary"]["reasons"] = reasons
        elif (
            drift["regression"]
            and verdict == "REVIEW"
            and (drift["blast_radius_delta"] >= 20 or drift["new_violation_count"] >= 3)
        ):
            verdict = "BLOCK"
            reasons.append("Drift regression severe enough to escalate REVIEW → BLOCK")
            bundle["summary"]["verdict"] = verdict
            bundle["summary"]["reasons"] = reasons

    if save_baseline:
        # Save AFTER drift logic so saved envelope reflects the post-drift verdict
        try:
            _save_baseline(base_path, bundle)
            bundle["baseline_saved"] = str(base_path)
        except OSError as exc:
            bundle["baseline_save_error"] = str(exc)

    if cache:
        # Persist the analysis envelope so future runs with the same diff +
        # rules + threshold replay instantly. Save AFTER all post-processing
        # (drift + baseline) so the cached envelope is the final canonical one.
        cache_save_key = _cache_key(diff_text, rules_path, block_threshold, language_override)
        _save_cache(cache_dir_path, cache_save_key, bundle)
        bundle.setdefault("_meta", {})["cache_saved_to"] = str(_cache_path(cache_dir_path, cache_save_key))

    audit_record: dict | None = None
    audit_chain_status: dict | None = None
    if audit_trail:
        trail_path = Path(audit_trail_path) if audit_trail_path else DEFAULT_AUDIT_TRAIL_PATH

        # Pre-emission integrity check — if the existing chain is broken,
        # surface the issue BEFORE appending so we don't compound corruption.
        # Lazy import to avoid pulling cmd_audit_trail_verify on every pr-analyze.
        from roam.commands.cmd_audit_trail_verify import _verify_chain

        _, chain_issues = _verify_chain(trail_path)
        # Filter out the "audit trail not found" issue — that's the genesis case.
        real_issues = [i for i in chain_issues if "not found" not in i.get("issue", "")]
        chain_was_valid = not real_issues
        audit_chain_status = {
            "pre_emission_chain_valid": chain_was_valid,
            "pre_emission_issues": real_issues,
        }

        audit_record = _emit_audit_trail_record(
            audit_trail_path=trail_path,
            diff_text=diff_text,
            bundle=bundle,
            intent=intent,
            reviewers_payload=reviewers_payload,
        )
        bundle["audit_trail"] = {
            "path": str(trail_path),
            "record": audit_record,
            "chain_status": audit_chain_status,
        }

        # Auto-escalate verdict if the chain was tampered with — a broken
        # audit trail in CI is a compliance incident, gate must fire.
        if not chain_was_valid:
            reasons.append(
                f"Audit-trail chain broken before append ({len(real_issues)} pre-existing issue(s)) — "
                "tampering or partial-write corruption detected"
            )
            if verdict != "BLOCK":
                bundle["summary"]["verdict_pre_chain_break"] = verdict
                verdict = "BLOCK"
                bundle["summary"]["verdict"] = verdict
                bundle["summary"]["reasons"] = reasons

    if json_mode:
        click.echo(to_json(json_envelope("pr-analyze", **bundle)))
    elif quiet:
        # CI-friendly mode: 1-line summary + reason if any. No tables, no breakdowns.
        click.echo(f"VERDICT: {verdict} (blast {blast_radius}, ai {ai['score']}, rules {len(rule_violations)})")
    else:
        click.echo(f"VERDICT: {verdict}")
        click.echo()
        click.echo(f"  blast radius:    {blast_radius}/100")
        click.echo(f"  ai-likelihood:   {ai['score']}/100")
        click.echo(f"  rule violations: {len(rule_violations)}")
        click.echo(f"  critique high:   {high_severity}")
        if rules:
            click.echo(f"  rules loaded:    {len(rules)} from {rules_path}")
        if rules_warnings:
            click.echo()
            click.echo(f"Rules warnings ({len(rules_warnings)}):")
            for w in rules_warnings[:5]:
                click.echo(f"  - {w}")
            if len(rules_warnings) > 5:
                click.echo(f"  ... and {len(rules_warnings) - 5} more")
            click.echo("  Tip: run `roam rules-validate` to lint the rule file before next push.")
        if reasons:
            click.echo()
            click.echo("Reasons:")
            for r in reasons:
                click.echo(f"  - {r}")
        # Polish: when BLOCK without intent, hint at the conscious-bypass syntax.
        if verdict == "BLOCK" and not (intent and _INTENTIONAL_RE.search(intent)):
            click.echo()
            click.echo(
                "Tip: if this BLOCK is conscious, re-run with "
                '`--intent "[intentional] <reason>"` to bypass the gate (audit trail still records it).'
            )
        if rule_violations:
            click.echo()
            click.echo("Rule violations:")
            for v in rule_violations[:5]:
                click.echo(f"  [{v['severity']}] {v['rule_id']}: {v['file']} -> {v['matched_import']}")
            if len(rule_violations) > 5:
                click.echo(f"  ... and {len(rule_violations) - 5} more (use --json for full list)")
        if ai["score"] >= 50:
            signals = ai.get("signals", {}) or {}
            weights = ai.get("weights", {}) or {}
            top_signals = sorted(signals.items(), key=lambda kv: -kv[1])[:3]
            if top_signals:
                click.echo()
                click.echo("Top AI-likelihood signals (signal -> weighted contribution):")
                for name, val in top_signals:
                    w = weights.get(name, 0)
                    contribution = val * w
                    click.echo(f"  {name}: {val}/100  ->  {contribution:.1f} pts (x{w:.2f} weight)")

        if explain:
            click.echo()
            click.echo("RATIONALE")
            click.echo("---------")
            click.echo(rationale["summary_text"])
            if rationale["concerns"]:
                click.echo()
                click.echo("Concerns:")
                for i, c in enumerate(rationale["concerns"], 1):
                    label = c["concern"]
                    if c.get("score") is not None:
                        label += f" ({c['score']}/100)"
                    click.echo(f"  {i}. {label}")
                    click.echo(f"     {c['evidence']}")
            if rationale["next_steps"]:
                click.echo()
                click.echo("Next steps:")
                for step in rationale["next_steps"]:
                    click.echo(f"  - {step}")

    if gate and verdict == "BLOCK":
        sys.exit(EXIT_GATE_BLOCK)
