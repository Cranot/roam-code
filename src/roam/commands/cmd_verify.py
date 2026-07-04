"""Pre-commit consistency verification against established codebase patterns.

Output formats: text (default), ``--json``. SARIF is deliberately NOT
emitted because cmd_verify is a multi-check pre-commit composer
(naming + imports + error-handling + duplicates + syntax) with weighted
scoring + threshold gates. The composed sub-checks emit their own
verdicts; cmd_verify rolls them into a single pre-commit PASS/FAIL
summary. SARIF would conflate composite-gate output with per-violation
findings. Each sub-check exposes its own --sarif via dedicated
commands when applicable. See action.yml _SUPPORTED_SARIF allowlist +
W1198-audit memo.
"""

from __future__ import annotations

import importlib
import os
import re
from collections import Counter, defaultdict
from difflib import SequenceMatcher
from pathlib import Path

import click

from roam.capability import roam_capability
from roam.command_advice import validate_command_advice
from roam.commands.changed_files import (
    get_changed_files,
    is_test_file,
    resolve_changed_to_db,
)
from roam.commands.cmd_conventions import (
    _MIN_NAME_LEN,
    _SKIP_NAMES,
    NON_CODE_CONVENTION_LANGUAGES,
    _group_for_kind,
    classify_case,
    is_python_type_alias_signature,
    is_upper_snake_constant_name,
)
from roam.commands.conventions_helper import CONVENTION_NEUTRAL_FILE_ROLES, has_excluded_prefix
from roam.commands.resolve import ensure_index
from roam.db.connection import batched_in, find_project_root, open_db
from roam.output.formatter import json_envelope, loc, to_json
from roam.runs.helpers import auto_log

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

EXIT_GATE_FAILURE = 5

_CATEGORY_WEIGHTS = {
    # The conventions-grade DEFAULT five (sum to 1.0 so default scoring is
    # unchanged — _compute_composite renormalizes over whatever is selected).
    "naming": 0.25,
    "imports": 0.20,
    "error_handling": 0.20,
    "duplicates": 0.20,
    "syntax": 0.15,
    # Import-time side effects — a default semantic check (0 FP audited on
    # roam-code; _compute_composite renormalizes so the relative weight is what
    # matters). Catches "importing this module mutates the world".
    "import_side_effects": 0.15,
    # Richer STRUCTURAL checks (opt-in via --checks/--auto/--all/config): higher
    # signal than style — KISS (complexity) + architecture (import cycles).
    "complexity": 0.20,
    "cycles": 0.20,
    # The EXECUTABLE signal — highest weight; only runs when explicitly selected.
    "tests": 0.30,
    # The leak gate. The weight barely matters for the composite — a
    # FAIL-severity secrets finding (credential shape) FORCES the verdict
    # below PASS regardless of the weighted average (see the verdict-floor
    # logic at the _compute_verdict call site): averaging away a leaked
    # credential is exactly the silent-fallback pattern this repo bans.
    "secrets": 0.15,
    # Advisory only: _compute_composite ignores categories not in this dict's
    # selected weighted set, but the category still surfaces in the envelope.
}

# Severity levels for violations
SEVERITY_FAIL = "FAIL"
SEVERITY_WARN = "WARN"
SEVERITY_INFO = "INFO"

# FAILs first, then WARN, then INFO, then anything unknown. Ranks the flat
# findings list (Tier-1 blast-radius weighting) without touching verdict/score.
_SEVERITY_ORDER = {SEVERITY_FAIL: 0, SEVERITY_WARN: 1, SEVERITY_INFO: 2}
_DIFF_HUNK_RE = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@")

# Public module entrypoints that legitimately repeat across independent
# domains. Exact-name matching alone is not useful signal for these API shapes:
# ``load_rules`` exists in architecture-rule and taint-rule engines with
# different schemas/return types. CLI command entrypoints mirrored by MCP
# wrappers are handled by a path-aware exemption below so real same-name
# helpers are still checked.
_DUPLICATE_ENTRYPOINT_SKIP_NAMES = frozenset({"load_rules"})

# ---------------------------------------------------------------------------
# Guardrail wiring (post-edit gate strengthening). Every behavior-CHANGING
# wire below is env-flagged with a sensible default and a kill switch, so each
# is fully reversible without a code change. Defaults make the auto/diff-only
# gate strictly stronger than before WITHOUT touching any existing check:
#   ROAM_VERIFY_TESTS=1                 run impacted tests in --auto (executable signal)
#   ROAM_VERIFY_BREAKING=1             block a sig-change with un-edited external callers
#   ROAM_VERIFY_BREAKING_MIN_CALLERS=1 external-caller blast threshold (private-helper noise)
#   ROAM_VERIFY_UNRESOLVED=1           flag calls that resolve to nothing (NameError shape)
#   ROAM_VERIFY_TAINT=0                surface source->sink taint touching the edit (opt-in)
#   ROAM_VERIFY_DELETE_CHECK=0        block a diff whose deleted symbol still has survivors (opt-in; grep-FP)
#   ROAM_VERIFY_MIGRATION_SAFETY=1    block a non-idempotent / destructive changed .php migration
#   ROAM_VERIFY_SMELLS=0              warn on god-class / brain-method / deep-nesting in changed code
#   ROAM_VERIFY_CLONES=0             warn on AST near-duplicate of changed code (heavier)
#   ROAM_VERIFY_MAGIC_NUMBERS=0      warn on repeated magic numbers in changed code
#   ROAM_VERIFY_DEAD=0               warn on a newly-orphaned exported symbol in changed code
#   ROAM_VERIFY_N1=0                 warn on an N+1 lazy-load introduced by a changed model
#   ROAM_VERIFY_OVER_FETCH=0         warn on serializer over-fetch in changed code
#   ROAM_VERIFY_LLM_SMELLS=0         warn on LLM-API anti-patterns in changed code
#   ROAM_VERIFY_TEST_HERMETICITY=0   warn on non-hermetic patterns in a changed test file
# ---------------------------------------------------------------------------
_VERIFY_BREAKING_CATEGORY = "breaking"
_VERIFY_TAINT_CATEGORY = "taint"
# Additional reusable detector wires (each env-flagged, diff-scoped, fail-open).
# Guardrails emit hard_block FAIL when they fire; the rest emit advisory WARN
# that surface in the violations list WITHOUT entering the weighted composite
# (same contract as the breaking / taint wires).
_VERIFY_DELETE_CATEGORY = "delete_check"
_VERIFY_MIGRATION_CATEGORY = "migration_safety"
_VERIFY_SMELLS_CATEGORY = "smells"
_VERIFY_CLONES_CATEGORY = "clones"
_VERIFY_MAGIC_CATEGORY = "magic_numbers"
_VERIFY_DEAD_CATEGORY = "dead"
_VERIFY_N1_CATEGORY = "n1"
_VERIFY_OVER_FETCH_CATEGORY = "over_fetch"
_VERIFY_LLM_SMELLS_CATEGORY = "llm_smells"
_VERIFY_HERMETICITY_CATEGORY = "test_hermeticity"
# Forced score when a hard-block guardrail fires: below the default threshold
# (70) so the CLI gate exits EXIT_GATE_FAILURE; the verdict is also pinned to
# FAIL so the verdict-keyed post-edit hook blocks regardless of threshold.
_HARD_BLOCK_SCORE = 40
# Cap the breaking-change git/parse work so a huge uncommitted tree can't slow
# the gate; above it the check fails open (no finding).
_MAX_BREAKING_FILES = 50


def _verify_env_flag(name: str, default: bool) -> bool:
    """Read a ``ROAM_VERIFY_*`` on/off switch. Unset/garbage falls back to
    *default* so every wiring is fully reversible from the environment."""
    raw = (os.environ.get(name) or "").strip().lower()
    if raw in ("1", "true", "on", "yes"):
        return True
    if raw in ("0", "false", "off", "no"):
        return False
    return default


def _verify_env_int(name: str, default: int) -> int:
    """Read an integer ``ROAM_VERIFY_*`` knob, falling back to *default*."""
    try:
        return int((os.environ.get(name) or "").strip())
    except (TypeError, ValueError):
        return default


def _blast_radius_by_file(conn, files):
    """File-level blast radius = the MAX caller count
    (``graph_metrics.in_degree`` = incoming edges) among symbols defined in the
    file. Reuses the cached column — no graph rebuild, one indexed GROUP BY.

    A finding in a file that exports a heavily-called symbol outranks one in a
    leaf module. Returns ``{path: caller_count}`` for the requested files only;
    stale/empty metrics resolve to an empty map so ranking degrades cleanly to
    severity-only. Best-effort: never raises into the gate.
    """
    wanted = {f for f in (files or ()) if f}
    if not wanted:
        return {}
    try:
        rows = conn.execute(
            "SELECT f.path, MAX(gm.in_degree) "
            "FROM files f "
            "JOIN symbols s ON s.file_id = f.id "
            "JOIN graph_metrics gm ON gm.symbol_id = s.id "
            "GROUP BY f.path"
        ).fetchall()
    except Exception as exc:  # noqa: BLE001 — ranking is advisory, never break verify
        from roam.observability import log_swallowed

        log_swallowed("verify.blast_radius", exc)
        return {}
    out: dict[str, int] = {}
    for path, mx in rows:
        if path in wanted:
            out[path] = int(mx or 0)
    return out


def _normalize_diff_file_list(files) -> list[str]:
    return sorted({f for f in (files or []) if f})


def _git_diff_zero_context(files: list[str], root: Path) -> str:
    import subprocess

    try:
        return subprocess.run(
            ["git", "-C", str(root), "diff", "HEAD", "-U0", "--", *files],
            capture_output=True,
            text=True,
            timeout=8,
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return ""


def _diff_new_file_path(line: str) -> str | None:
    if line.startswith("+++ b/"):
        return line[6:].strip()
    return None


def _line_numbers_from_hunk(line: str) -> set[int]:
    match = _DIFF_HUNK_RE.match(line)
    if not match:
        return set()
    start = int(match.group(1))
    count = int(match.group(2)) if match.group(2) is not None else 1
    return set(range(start, start + max(count, 1)))


def _collect_changed_line_ranges(diff_text: str) -> dict[str, set[int]]:
    ranges: dict[str, set] = {}
    cur = None
    for line in diff_text.splitlines():
        new_file = _diff_new_file_path(line)
        if new_file:
            cur = new_file
            ranges.setdefault(cur, set())
            continue
        if cur is not None and line.startswith("@@"):
            ranges[cur].update(_line_numbers_from_hunk(line))
    return {f: s for f, s in ranges.items() if s}


def _changed_line_ranges(files, root):
    """Map ``{relpath: set(changed new-line numbers)}`` from ``git diff HEAD
    -U0``. Files with no tracked diff (untracked / new / no hunks) are omitted,
    so callers keep all of those files' violations (no baseline to scope
    against). Used by ``--diff-only`` to report only what the edit touched."""
    flist = _normalize_diff_file_list(files)
    if not flist:
        return {}
    return _collect_changed_line_ranges(_git_diff_zero_context(flist, root))


def _parse_changed_line_range(raw: str) -> set[int]:
    if not raw:
        return set()
    try:
        if "-" in raw:
            start, _, end = raw.partition("-")
            lo, hi = int(start), int(end)
        else:
            lo = hi = int(raw)
    except ValueError:
        return set()
    if lo > hi:
        lo, hi = hi, lo
    return set(range(lo, hi + 1))


def _parse_changed_line_segment(segment: str) -> tuple[str, set[int]] | None:
    segment = segment.strip()
    if ":" not in segment:
        return None
    path, _, raw_range = segment.rpartition(":")
    path = path.strip()
    lines = _parse_changed_line_range(raw_range.strip())
    if not path or not lines:
        return None
    return path, lines


def _parse_changed_lines(spec):
    """Parse a ``--changed-lines`` spec into ``{relpath: set(line numbers)}``.

    Format: comma-separated ``path:START-END`` or ``path:LINE`` segments, e.g.
    ``src/a.py:1-5,src/a.py:10-12,src/b.py:7``. Lets a caller scope verify to the
    lines IT changed this turn rather than the whole git-diff-vs-HEAD (noisy on a
    big uncommitted tree). Malformed segments are skipped (best-effort)."""
    ranges: dict[str, set] = {}
    if not spec:
        return ranges
    for seg in str(spec).split(","):
        parsed = _parse_changed_line_segment(seg)
        if parsed is None:
            continue
        path, lines = parsed
        ranges.setdefault(path, set()).update(lines)
    return {f: s for f, s in ranges.items() if s}


def _expand_dir_targets(target_paths: list[str], root: Path) -> list[str]:
    """Expand any directory argument into the indexed files beneath it.

    `roam verify src/roam/` used to resolve the bare directory string against the
    DB (exact + basename suffix only) → 0 file matches → false-green PASS (the
    Pattern-2 silent-fallback anti-pattern). Expand each on-disk directory target
    to the DB files whose path is under it, so a directory arg verifies its tree
    instead of silently passing. Non-directory targets pass through untouched; if
    the DB can't be opened the input is returned verbatim (best-effort)."""
    dirs = [p for p in target_paths if (root / p).is_dir()]
    if not dirs:
        return target_paths
    prefixes = [p.rstrip("/") + "/" for p in dirs]
    expanded = [p for p in target_paths if p not in dirs]
    seen = set(expanded)
    try:
        with open_db(readonly=True) as _conn:
            rows = _conn.execute("SELECT path FROM files").fetchall()
        for r in rows:
            fp = r["path"].replace("\\", "/")
            if any(fp.startswith(pre) for pre in prefixes) and fp not in seen:
                expanded.append(fp)
                seen.add(fp)
    except Exception as exc:  # noqa: BLE001 — best-effort; never break verify
        from roam.observability import log_swallowed

        log_swallowed("verify.expand_dir_targets", exc)
        return target_paths
    return expanded


# ---------------------------------------------------------------------------
# Findings baseline — accept the current debt once, then surface only NEW
# findings. The single highest-leverage scoping mechanism for an established
# repo: `--diff-only`/`--changed-lines` scope by POSITION (this turn's lines),
# but a baseline scopes by IDENTITY (this finding existed before), so the 387
# pre-existing broad-excepts vanish from the auto-correct loop while any NEW
# one the agent introduces still surfaces. Fingerprints are line-shift
# tolerant: keyed on (category, file, symbol, message-kind, stripped code line)
# — NOT the line number — so editing elsewhere in a file doesn't unmute its
# baselined findings.
# ---------------------------------------------------------------------------
_VERIFY_BASELINE_REL = (".roam", "verify-baseline.json")


def _verify_baseline_path(root: Path) -> Path:
    return root.joinpath(*_VERIFY_BASELINE_REL)


def _source_line(f: str, ln, line_cache: dict, root: Path) -> str:
    """Stripped text of line *ln* (1-based) in *f*, or "" — the durable code
    anchor for a finding fingerprint. *line_cache* memoises each file's lines."""
    if not f or not isinstance(ln, int):
        return ""
    lines = line_cache.get(f)
    if lines is None:
        try:
            lines = (root / f).read_text(encoding="utf-8", errors="replace").splitlines()
        except Exception:  # noqa: BLE001 — missing file → empty anchor
            lines = []
        line_cache[f] = lines
    return lines[ln - 1].strip() if 1 <= ln <= len(lines) else ""


def _finding_fingerprint(v: dict, line_cache: dict, root: Path) -> str:
    """Stable, line-shift-tolerant identity for a single finding."""
    import hashlib

    cat = v.get("category", "")
    f = (v.get("file") or "").replace("\\", "/")
    sym = v.get("symbol") or ""
    # message kind with volatile numbers/percentages collapsed (e.g. "(82%
    # match)" / "codebase: snake_case 91%") so a count drift doesn't re-key it.
    msgkind = re.sub(r"\d+", "#", (v.get("message") or "")[:64])
    code = _source_line(f, v.get("line"), line_cache, root)
    raw = f"{cat}|{f}|{sym}|{msgkind}|{code}"
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


def _write_verify_baseline(violations: list, root: Path) -> int:
    """Snapshot fingerprint→count of *violations* to the baseline file."""
    from datetime import datetime, timezone

    from roam.atomic_io import atomic_write_json

    line_cache: dict = {}
    counts = Counter(_finding_fingerprint(v, line_cache, root) for v in violations)
    atomic_write_json(
        _verify_baseline_path(root),
        {
            "schema": 1,
            "created": datetime.now(timezone.utc).isoformat(),
            "count": sum(counts.values()),
            "fingerprints": dict(counts),
        },
    )
    return sum(counts.values())


def _load_verify_baseline(root: Path) -> dict | None:
    """Return the baseline fingerprint→count map, or None if absent/unreadable."""
    import json as _json

    p = _verify_baseline_path(root)
    if not p.exists():
        return None
    try:
        data = _json.loads(p.read_text(encoding="utf-8"))
        fps = data.get("fingerprints")
        return fps if isinstance(fps, dict) else {}
    except Exception:  # noqa: BLE001 — a corrupt baseline must not break verify
        from roam.observability import log_swallowed

        log_swallowed("verify.load_baseline", Exception("corrupt verify-baseline.json"))
        return None


# ---------------------------------------------------------------------------
# Verify-mode config / selection (the OUTPUT-side verify mode — opt-in, never
# forced; the user picks WHICH checks run via flags, `.roam/verify.yaml`, or an
# auto mode keyed on what was touched).
# ---------------------------------------------------------------------------

# Default = the conventions-grade five (backward compatible). The structural
# checks (complexity, cycles) are AVAILABLE but opt-in so `roam verify` with no
# args behaves exactly as before; `--all`, `--auto`, `--checks`, or config
# unlock them.
_DEFAULT_CHECKS: tuple[str, ...] = (
    "naming",
    "imports",
    "error_handling",
    "duplicates",
    "syntax",
    "import_side_effects",
    # The leak gate rides the default loop: built-in credential shapes plus
    # the optional repo-local `.roam-leak-patterns.py` catalogue. Cheap
    # (regex over changed files only) and the cost of missing one is a
    # public credential / internal-language leak.
    "secrets",
)
_ALL_CHECKS: tuple[str, ...] = _DEFAULT_CHECKS + (
    "complexity",
    "cycles",
    "tests",
    "command_examples",
    "claims",
    # Guardrail gates (env-flagged; see _verify_env_flag). Selectable via
    # --checks/--all/config; auto-selected by default on Python edits.
    _VERIFY_BREAKING_CATEGORY,
    _VERIFY_TAINT_CATEGORY,
    _VERIFY_DELETE_CATEGORY,
    _VERIFY_MIGRATION_CATEGORY,
    _VERIFY_SMELLS_CATEGORY,
    _VERIFY_CLONES_CATEGORY,
    _VERIFY_MAGIC_CATEGORY,
    _VERIFY_DEAD_CATEGORY,
    _VERIFY_N1_CATEGORY,
    _VERIFY_OVER_FETCH_CATEGORY,
    _VERIFY_LLM_SMELLS_CATEGORY,
    _VERIFY_HERMETICITY_CATEGORY,
)
_VERIFY_CONFIG_REL = (".roam", "verify.yaml")
_COMMAND_EXAMPLE_EXTS = frozenset({".md", ".mdx", ".rst", ".txt", ".html", ".htm", ".yaml", ".yml"})
_COMMAND_EXAMPLE_PATH_HINTS = (
    "README",
    "AGENTS.md",
    "agent-contract",
    "command-reference",
    "docs/",
    "templates/",
)
_INLINE_ROAM_COMMAND_RE = re.compile(r"`\s*(roam\s+[^`\n]*)`")
_SHELL_ROAM_COMMAND_RE = re.compile(r"^(?:(roam\s+.+?)|\s*(?:\$\s+|>\s*)(roam\s+.+?))\s*$")
_CLAIM_SURFACE_EXTS = frozenset({".md", ".mdx", ".rst", ".html", ".htm"})
_CLAIM_HINTED_SURFACE_EXTS = frozenset({".txt"})
_CLAIM_PATH_HINTS = _COMMAND_EXAMPLE_PATH_HINTS + (
    "audit",
    "compare",
    "email/",
    "legal/",
    "pricing",
    "security",
    "trust",
)
_CLAIM_TRIGGER_RE = re.compile(
    r"("
    r"\b\d+(?:\.\d+)?\s*(?:%|x|ms|seconds?|minutes?|hours?|days?|weeks?|months?|years?)\b"
    r"|\b\d+(?:\.\d+)?\s*(?:commands?|tools?|languages?|repos?|repositories?|files?|issues?|findings?|tests?|"
    r"users?|customers?|teams?|cells?|loc)\b"
    r"|\$\d[\d,]*(?:\.\d+)?"
    r"|\b100%\s+local\b"
    r"|\bzero[- ](?:api|network|config|configuration|model|outbound)\b"
    r"|\b(?:fastest|guaranteed|certified|enterprise-grade|production-ready|market-leading)\b"
    r"|\bonly\s+(?:tool|outbound|scope|surface|sub-?processors?)\b"
    r"|\bnever\s+(?:used|sent|stored|persisted|train|fine-tune)\b"
    r")",
    re.IGNORECASE,
)
_CLAIM_EVIDENCE_RE = re.compile(
    r"(https?://|\[[^\]]+\]\([^)]+\)|\bas of\b|\bbench(?:mark|marks|marked)?\b|"
    r"\bmeasured\b|\bsource\b|\bevidence\b|\bvalidated by\b|\btested\b|\bdated\b|"
    r"\bobserved\b|\bsurveyed\b|\bper\b|\bsee\b|\b20\d{2}[-/]\d{2}[-/]\d{2}\b|"
    r"\b(?:jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\s+20\d{2}\b|"
    r"\bn\s*=\s*\d+\b|\brun\s+#?\d+\b)",
    re.IGNORECASE,
)
_CLAIM_OUTLINE_HEADING_RE = re.compile(r"^\s{0,3}#{1,6}\s+\d+(?:\.\d+)*\b")
_CLAIM_TEMPLATE_PLACEHOLDER_RE = re.compile(r"\[([A-Za-z0-9_ ./+:-]{2,})\]")
_IMPORT_RESOLUTION_SOURCE_EXTS = frozenset(
    {
        ".apex",
        ".c",
        ".cc",
        ".cls",
        ".cpp",
        ".cs",
        ".dart",
        ".go",
        ".h",
        ".hpp",
        ".java",
        ".js",
        ".jsx",
        ".kt",
        ".mjs",
        ".php",
        ".py",
        ".pyi",
        ".rb",
        ".rs",
        ".scala",
        ".swift",
        ".ts",
        ".tsx",
    }
)


def _verify_config_path(root: Path) -> Path:
    return root.joinpath(*_VERIFY_CONFIG_REL)


def _read_verify_config_data(path: Path) -> dict:
    try:
        import yaml

        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except Exception:  # noqa: BLE001 — bad config must not break the gate
        return {}
    return data if isinstance(data, dict) else {}


def _known_verify_checks(raw) -> list[str] | None:
    if not isinstance(raw, list):
        return None
    picked = [check for check in raw if check in _ALL_CHECKS]
    return picked or None


def _apply_verify_config_data(cfg: dict, data: dict) -> dict:
    for key in ("enabled", "auto"):
        if isinstance(data.get(key), bool):
            cfg[key] = data[key]
    if isinstance(data.get("threshold"), int):
        cfg["threshold"] = data["threshold"]
    checks = _known_verify_checks(data.get("checks"))
    if checks is not None:
        cfg["checks"] = checks
    return cfg


def load_verify_config(root: Path) -> dict:
    """Load `.roam/verify.yaml`. Keys: enabled(bool), checks(list|None),
    threshold(int|None), auto(bool). A missing/bad file → permissive defaults
    (enabled, all checks) so verify never silently breaks on bad config."""
    cfg: dict = {"enabled": True, "checks": None, "threshold": None, "auto": False}
    path = _verify_config_path(root)
    if not path.exists():
        return cfg
    return _apply_verify_config_data(cfg, _read_verify_config_data(path))


def write_verify_enabled(root: Path, enabled: bool) -> Path:
    """Toggle verify on/off (the stop/start switch) by persisting the
    `enabled` flag into `.roam/verify.yaml`, preserving other keys."""
    path = _verify_config_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    cfg: dict = {}
    if path.exists():
        try:
            import yaml

            cfg = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except Exception:  # noqa: BLE001
            cfg = {}
    if not isinstance(cfg, dict):
        cfg = {}
    cfg["enabled"] = enabled
    try:
        import yaml

        path.write_text(yaml.safe_dump(cfg, sort_keys=False), encoding="utf-8")
    except Exception:  # noqa: BLE001
        path.write_text(f"enabled: {str(enabled).lower()}\n", encoding="utf-8")
    return path


def _is_command_example_surface(path: str) -> bool:
    norm = path.replace("\\", "/")
    suffix = Path(norm).suffix.lower()
    if suffix in _COMMAND_EXAMPLE_EXTS:
        return True
    return any(hint in norm for hint in _COMMAND_EXAMPLE_PATH_HINTS)


def _is_historical_command_example_surface(path: str) -> bool:
    name = Path(path.replace("\\", "/")).name.lower()
    return name.startswith("changelog.") or name in {"changelog", "history.md"}


def _is_plugin_example_command_surface(path: str) -> bool:
    norm = path.replace("\\", "/").lower()
    return norm.startswith("dev/example-plugin/")


def _is_import_resolution_source_path(path: str) -> bool:
    return Path(path.replace("\\", "/")).suffix.lower() in _IMPORT_RESOLUTION_SOURCE_EXTS


def _is_claim_surface(path: str) -> bool:
    norm = path.replace("\\", "/")
    suffix = Path(norm).suffix.lower()
    if suffix in _CLAIM_SURFACE_EXTS:
        return True
    if suffix in _CLAIM_HINTED_SURFACE_EXTS:
        return any(hint in norm for hint in _CLAIM_PATH_HINTS)
    return not suffix and any(hint in norm for hint in _CLAIM_PATH_HINTS)


def _is_non_code_verify_surface(path: str) -> bool:
    norm = path.replace("\\", "/")
    suffix = Path(norm).suffix.lower().lstrip(".")
    return suffix in NON_CODE_CONVENTION_LANGUAGES or _is_command_example_surface(norm) or _is_claim_surface(norm)


def auto_select_checks(target_paths: list[str]) -> list[str]:
    """AUTO mode — pick the checks that are RELEVANT to what was touched.
    Python edits unlock the Python-specific checks; any non-test source edit
    unlocks naming + duplicate detection. Empty selection falls back to all."""
    selected: set[str] = set()
    has_py = any(p.endswith(".py") for p in target_paths)
    has_nontest_source = any(not is_test_file(p) and "." in p.rsplit("/", 1)[-1] for p in target_paths)
    if target_paths:
        # The leak gate runs on EVERY touched file, test or not — credentials
        # and never-publish language are wrong anywhere in the tree.
        selected.add("secrets")
    if has_nontest_source:
        # import_side_effects is language-agnostic (py/ts/js/go/rb/java) — any
        # source edit can introduce an import-time side effect, so it belongs
        # in the auto set alongside naming/duplicates.
        selected |= {"naming", "duplicates", "import_side_effects"}
    if has_py:
        # Python edits unlock the Python checks AND the structural ones — a code
        # change is exactly when complexity/cycle regressions sneak in.
        selected |= {"imports", "error_handling", "syntax", "complexity", "cycles"}
    if any(_is_command_example_surface(p) for p in target_paths):
        selected.add("command_examples")
    if any(_is_claim_surface(p) for p in target_paths):
        selected.add("claims")
    if has_py:
        # Behavioral + guardrail gates (env-flagged, reversible). The impacted-
        # test run is the executable signal that trips a behavioral regression
        # even when every static check is green — the #1 reason an edit passes
        # the gate but breaks at runtime. The breaking-change gate blocks a
        # signature change whose callers were not co-edited. Both default ON;
        # taint is opt-in (FP-prone). Each is independently reversible.
        if _verify_env_flag("ROAM_VERIFY_TESTS", True):
            selected.add("tests")
        if _verify_env_flag("ROAM_VERIFY_BREAKING", True):
            selected.add(_VERIFY_BREAKING_CATEGORY)
        if _verify_env_flag("ROAM_VERIFY_TAINT", False):
            selected.add(_VERIFY_TAINT_CATEGORY)
    # Additional reusable detectors (env-flagged). Default-OFF ones stay OUT of
    # the auto set unless their switch is on, so the no-arg / Stop-hook gate is
    # byte-identical to before. Each detector fn ALSO re-checks its flag and
    # fails open, so `--checks all` never runs a disabled detector either.
    if has_nontest_source:
        if _verify_env_flag("ROAM_VERIFY_DELETE_CHECK", False):
            selected.add(_VERIFY_DELETE_CATEGORY)
        if _verify_env_flag("ROAM_VERIFY_CLONES", False):
            selected.add(_VERIFY_CLONES_CATEGORY)
        if _verify_env_flag("ROAM_VERIFY_OVER_FETCH", False):
            selected.add(_VERIFY_OVER_FETCH_CATEGORY)
        if _verify_env_flag("ROAM_VERIFY_LLM_SMELLS", False):
            selected.add(_VERIFY_LLM_SMELLS_CATEGORY)
    if has_py:
        if _verify_env_flag("ROAM_VERIFY_SMELLS", False):
            selected.add(_VERIFY_SMELLS_CATEGORY)
        if _verify_env_flag("ROAM_VERIFY_MAGIC_NUMBERS", False):
            selected.add(_VERIFY_MAGIC_CATEGORY)
        if _verify_env_flag("ROAM_VERIFY_DEAD", False):
            selected.add(_VERIFY_DEAD_CATEGORY)
        if _verify_env_flag("ROAM_VERIFY_N1", False):
            selected.add(_VERIFY_N1_CATEGORY)
    if any(("migration" in p.lower() and p.lower().endswith(".php")) for p in target_paths):
        if _verify_env_flag("ROAM_VERIFY_MIGRATION_SAFETY", True):
            selected.add(_VERIFY_MIGRATION_CATEGORY)
    if any((is_test_file(p) and p.endswith(".py")) for p in target_paths):
        if _verify_env_flag("ROAM_VERIFY_TEST_HERMETICITY", False):
            selected.add(_VERIFY_HERMETICITY_CATEGORY)
    if not selected:
        selected = set(_DEFAULT_CHECKS)
    return [c for c in _ALL_CHECKS if c in selected]


def resolve_selected_checks(checks_opt: str | None, auto: bool, cfg: dict, target_paths: list[str]) -> list[str]:
    """Precedence: explicit --checks > --auto/config.auto > config.checks >
    default-five. `--checks all` selects every available check."""
    if checks_opt:
        if checks_opt.strip().lower() == "all":
            return list(_ALL_CHECKS)
        picked = [c.strip() for c in checks_opt.split(",") if c.strip()]
        sel = [c for c in _ALL_CHECKS if c in picked]
        return sel or list(_DEFAULT_CHECKS)
    if auto or cfg.get("auto"):
        return auto_select_checks(target_paths)
    if cfg.get("checks"):
        return list(cfg["checks"])
    return list(_DEFAULT_CHECKS)


# ---------------------------------------------------------------------------
# Naming consistency check
# ---------------------------------------------------------------------------


def _naming_group_or_skip(name: str, kind: str, language, signature) -> str | None:
    """Resolve a symbol to its effective naming kind-group, or None to skip.

    Mirrors the canonical conventions detector's W162 carve-outs so verify's
    naming check stops re-flagging the same three false-positive classes (was 40
    findings on the roam-code self-index, all FPs):
      * non-code languages (yaml/json/CI templates) — their keys aren't code
        identifiers; skip entirely.
      * UPPER_SNAKE names (``VERSION``, ``MAX_RETRIES``) are PEP-8 constants
        regardless of the extractor's reported kind — re-route to ``constants``
        (where UPPER_SNAKE is the expectation) instead of flagging as a
        mis-cased variable.
      * Python PascalCase type aliases stored as ``kind=variable``
        (``PathLike = Union[...]``, ``LockMode = Literal[...]``) — PEP 484 says
        PascalCase IS the convention; skip.
    """
    if (language or "").lower() in NON_CODE_CONVENTION_LANGUAGES:
        return None
    if is_upper_snake_constant_name(name):
        return "constants"
    group = _group_for_kind(kind)
    if group == "variables" and classify_case(name) == "PascalCase" and is_python_type_alias_signature(signature, name):
        return None
    return group


# Minimum symbols a (kind-group, language) must have before its dominant style
# counts as a convention — below this, the language is too sparse to judge (and
# must not be flagged against another language's convention). Set so a handful
# of incidental non-primary-language symbols (e.g. 5 JS CI-script functions in a
# Python repo) are skipped rather than judged against a thin sample.
_NAMING_MIN_LANG_SAMPLES = 10
_NAMING_KINDS_SQL = """('function', 'method', 'class', 'interface',
                         'struct', 'trait', 'enum', 'variable',
                         'constant', 'property', 'field', 'type_alias')"""


def _all_naming_symbols(conn):
    return conn.execute(f"""
        SELECT s.name, s.kind, s.signature, f.language AS language, f.path AS path,
               COALESCE(f.file_role, 'source') AS file_role
        FROM symbols s
        JOIN files f ON s.file_id = f.id
        WHERE s.kind IN {_NAMING_KINDS_SQL}
    """).fetchall()


def _changed_naming_symbols(conn, file_ids: list[int]):
    return batched_in(
        conn,
        f"""SELECT s.name, s.kind, s.line_start, s.signature,
                  f.path as file_path, f.language AS language,
                  COALESCE(f.file_role, 'source') AS file_role
           FROM symbols s
           JOIN files f ON s.file_id = f.id
           WHERE s.file_id IN ({{ph}})
             AND s.kind IN {_NAMING_KINDS_SQL}""",
        file_ids,
    )


def _naming_model_candidate(sym) -> tuple[str, str, str] | None:
    if has_excluded_prefix(sym["path"]) or sym["file_role"] in CONVENTION_NEUTRAL_FILE_ROLES:
        return None
    group = _naming_group_or_skip(sym["name"], sym["kind"], sym["language"], sym["signature"])
    style = classify_case(sym["name"]) if group is not None else None
    if not style:
        return None
    return group, (sym["language"] or "").lower(), style


def _dominant_naming_styles(all_symbols) -> dict[tuple[str, str], tuple[str, float]]:
    group_cases: dict[tuple[str, str], Counter] = defaultdict(Counter)
    for sym in all_symbols:
        # Exclude parser test fixtures + codegen templates from MODELING the
        # convention — they're deliberately written in varied styles (e.g. a
        # Kotlin fixture's 78%-snake mix), not the project's own conventions.
        if has_excluded_prefix(sym["path"]):
            continue
        # Test files follow the test framework's idiom (`test_*` snake_case
        # in PHPUnit/pytest); on test-heavy repos they OUTVOTE production
        # code and invert the convention (dogfood: PSR-12 PHP
        # repo reported snake_case 62.8% → ~2000 naming FPs). Vendored and
        # generated files carry third-party style.
        candidate = _naming_model_candidate(sym)
        if candidate is not None:
            group, language, style = candidate
            group_cases[(group, language)][style] += 1

    # Dominant style per (group, language). Require a minimum sample count so a
    # handful of symbols in a non-primary language can neither establish nor be
    # flagged against a "convention" (sparse JS/Kotlin/etc. are simply skipped).
    dominant: dict[tuple[str, str], tuple[str, float]] = {}
    for key, counter in group_cases.items():
        total = sum(counter.values())
        if total >= _NAMING_MIN_LANG_SAMPLES:
            best_style, best_count = counter.most_common(1)[0]
            dominant[key] = (best_style, round(100 * best_count / total, 1))
    return dominant


def _changed_naming_candidate(sym) -> tuple[str, str, str] | None:
    # Don't flag names INSIDE fixtures/templates (parser test data / codegen).
    if has_excluded_prefix(sym["file_path"]):
        return None
    # Test-framework idiom isn't the project convention; never flag
    # test/vendored/generated files for naming (mirror of the model
    # loop's exclusion above — flagging them against the production
    # convention is the same FP in the other direction).
    if sym["file_role"] in CONVENTION_NEUTRAL_FILE_ROLES:
        return None
    name = sym["name"]
    if len(name) < _MIN_NAME_LEN or name in _SKIP_NAMES:
        return None
    if name.startswith("__") and name.endswith("__"):
        return None

    group = _naming_group_or_skip(name, sym["kind"], sym["language"], sym["signature"])
    style = classify_case(name) if group is not None else None
    if not style:
        return None
    return group, (sym["language"] or "").lower(), style


def _naming_violation(sym, group: str, style: str, expected_style: str, pct: float) -> dict:
    name = sym["name"]
    message = (
        f"fn `{name}` uses {style} (codebase: {expected_style} {pct}%)"
        if group == "functions"
        else f"{group[:-1]} `{name}` uses {style} (codebase: {expected_style} {pct}%)"
    )
    return {
        "category": "naming",
        "severity": SEVERITY_WARN if pct < 90 else SEVERITY_FAIL,
        "file": sym["file_path"],
        "line": sym["line_start"],
        "message": message,
        "symbol": name,
        "actual_style": style,
        "expected_style": expected_style,
        "codebase_pct": pct,
        "fix": f"Rename `{name}` to match {expected_style} convention",
    }


def _naming_score(checked: int, violations: list[dict]) -> int:
    if checked == 0:
        return 100
    score = round(100 * (checked - len(violations)) / checked)
    return max(0, min(100, score))


def _check_naming(conn, file_ids: list[int]) -> dict:
    """Check naming consistency of symbols in changed files.

    Compares new/changed symbol names against the codebase's dominant
    naming convention per kind-group (functions, classes, variables, etc.).
    """
    if not file_ids:
        return {"score": 100, "violations": []}

    # 1. Get the dominant style per kind-group from ALL symbols. Convention is
    # computed PER (kind-group, LANGUAGE) to avoid cross-language false positives.
    dominant = _dominant_naming_styles(_all_naming_symbols(conn))

    violations = []
    checked = 0
    for sym in _changed_naming_symbols(conn, file_ids):
        candidate = _changed_naming_candidate(sym)
        if candidate is None:
            continue
        group, language, style = candidate
        checked += 1
        expected = dominant.get((group, language))
        if expected is None:
            continue
        expected_style, pct = expected
        if style != expected_style and pct >= 60:
            violations.append(_naming_violation(sym, group, style, expected_style, pct))

    # Score: fraction of checked symbols that are consistent
    return {"score": _naming_score(checked, violations), "violations": violations}


# ---------------------------------------------------------------------------
# Import pattern consistency check
# ---------------------------------------------------------------------------


def _import_resolution_score(resolution: list[dict]) -> int:
    fails = sum(1 for violation in resolution if violation.get("severity") == SEVERITY_FAIL)
    warns = len(resolution) - fails
    return max(0, 100 - 25 * fails - 10 * warns)


def _merge_import_results(style_score: int, style_violations: list[dict], resolution: list[dict]) -> dict:
    return {
        "score": min(style_score, _import_resolution_score(resolution)),
        "violations": style_violations + resolution,
    }


def _all_import_edges(conn):
    return conn.execute("""
        SELECT fe.source_file_id, sf.path as source_path, tf.path as target_path
        FROM file_edges fe
        JOIN files sf ON fe.source_file_id = sf.id
        JOIN files tf ON fe.target_file_id = tf.id
        WHERE fe.kind = 'imports'
    """).fetchall()


def _changed_import_edges(conn, file_ids: list[int]):
    return batched_in(
        conn,
        """SELECT fe.source_file_id, sf.path as source_path, tf.path as target_path
           FROM file_edges fe
           JOIN files sf ON fe.source_file_id = sf.id
           JOIN files tf ON fe.target_file_id = tf.id
           WHERE fe.kind = 'imports' AND fe.source_file_id IN ({ph})""",
        file_ids,
    )


def _path_dir(path: str) -> str:
    normalized = path.replace("\\", "/")
    return normalized.rsplit("/", 1)[0] if "/" in normalized else ""


def _edge_is_same_dir_style(edge) -> bool:
    src_dir = _path_dir(edge["source_path"])
    tgt_dir = _path_dir(edge["target_path"])
    return bool(
        src_dir
        and tgt_dir
        and (src_dir == tgt_dir or src_dir.startswith(tgt_dir + "/") or tgt_dir.startswith(src_dir + "/"))
    )


def _import_style_counts(edges) -> tuple[int, int]:
    relative_count = sum(1 for edge in edges if _edge_is_same_dir_style(edge))
    return len(edges) - relative_count, relative_count


def _dominant_import_style(absolute_count: int, relative_count: int) -> tuple[str, float]:
    total_imports = absolute_count + relative_count
    abs_pct = round(100 * absolute_count / total_imports, 1)
    if abs_pct >= 60:
        return "absolute", abs_pct
    if abs_pct <= 40:
        return "relative", round(100 - abs_pct, 1)
    return "mixed", 50.0


def _import_style_violation(edge, dominant_style: str, dominant_pct: float) -> dict | None:
    is_same_dir = _edge_is_same_dir_style(edge)
    if dominant_style == "relative" and not is_same_dir:
        return {
            "category": "imports",
            "severity": SEVERITY_WARN,
            "file": edge["source_path"],
            "line": None,
            "message": (
                f"cross-directory import from `{edge['source_path']}` "
                f"to `{edge['target_path']}` "
                f"(codebase prefers same-directory imports {dominant_pct}%)"
            ),
            "fix": "Consider restructuring to keep imports within the same package",
        }
    return None


def _import_style_score(checked: int, violations: list[dict]) -> int:
    if checked == 0:
        return 100
    score = round(100 * (checked - len(violations)) / checked)
    return max(0, min(100, score))


def _check_imports(conn, file_ids: list[int]) -> dict:
    """Check import patterns in changed files against codebase norms.

    Detects whether changed files follow the project's dominant import style
    (absolute vs relative) based on file_edges data.
    """
    if not file_ids:
        return {"score": 100, "violations": []}

    # Resolution pass first — the hallucination firewall, INSIDE the loop.
    # Until 2026-06-12 this check was style-only (absolute vs relative) while
    # the README promised "every import must resolve"; a fully hallucinated
    # import passed clean (found by the planted-recall eval). Computed before
    # the style analysis because every style early-return below (no edges /
    # no imports / mixed style) must still surface resolution failures.
    resolution = _unresolved_import_violations(conn, file_ids)

    # Call-resolution pass — the sibling firewall to the import one above.
    # An import can resolve while a *call* in the same file targets a name that
    # is bound nowhere (hallucinated helper / wrong method name); that is a
    # guaranteed NameError/AttributeError the static checks otherwise miss.
    # Env-gated + reversible; rides the same `imports` category + score.
    if _verify_env_flag("ROAM_VERIFY_UNRESOLVED", True):
        resolution = resolution + _unresolved_call_violations(conn, file_ids)

    # 1. Determine the codebase import style from ALL file_edges
    all_edges = _all_import_edges(conn)

    if not all_edges:
        return _merge_import_results(100, [], resolution)

    # Classify each import edge
    absolute_count, relative_count = _import_style_counts(all_edges)

    total_imports = absolute_count + relative_count
    if total_imports == 0:
        return _merge_import_results(100, [], resolution)

    dominant_style, dominant_pct = _dominant_import_style(absolute_count, relative_count)

    if dominant_style == "mixed":
        return _merge_import_results(100, [], resolution)

    # 2. Check changed files' import edges
    changed_edges = _changed_import_edges(conn, file_ids)

    violations = []
    checked = 0
    for edge in changed_edges:
        checked += 1
        violation = _import_style_violation(edge, dominant_style, dominant_pct)
        if violation:
            violations.append(violation)

    return _merge_import_results(_import_style_score(checked, violations), violations, resolution)


def _unresolved_import_violations(conn, file_ids: list[int]) -> list[dict]:
    """Run import RESOLUTION over the changed files; map unresolved imports
    to violations. No-suggestion misses are the canonical hallucination
    signal (FAIL); near-miss names with fuzzy candidates are WARN.

    Test-role files are excluded (same default as the algo detectors): test
    fixtures intentionally embed unresolvable imports — planted-bug repos,
    import statements inside triple-quoted fixture strings — and the raw-line
    scanner cannot tell those from live code. The cost is that hallucinated
    imports inside tests go unflagged here; the test run itself catches those."""
    from roam.commands.cmd_verify_imports import (
        _build_file_path_index,
        _declared_dependency_modules,
        _scan_file_imports,
    )
    from roam.db.connection import batched_in, find_project_root

    rows = batched_in(conn, "SELECT id, path, file_role FROM files WHERE id IN ({ph})", file_ids)
    paths = [r["path"] for r in rows if r["file_role"] != "test" and _is_import_resolution_source_path(r["path"])]
    if not paths:
        return []
    project_root = str(find_project_root())
    symbol_names: set[str] = set()
    symbol_qnames: set[str] = set()
    for r in conn.execute("SELECT name, qualified_name FROM symbols"):
        if r["name"]:
            symbol_names.add(r["name"])
        if r["qualified_name"]:
            symbol_qnames.add(r["qualified_name"])
    file_index = _build_file_path_index(conn)
    declared = _declared_dependency_modules(project_root)
    out: list[dict] = []
    for path in paths:
        for imp in _scan_file_imports(
            conn,
            path,
            project_root,
            symbol_names=symbol_names,
            symbol_qnames=symbol_qnames,
            file_index=file_index,
            declared_deps=declared,
        ):
            if imp.get("status") == "unresolved":
                out.append(_unresolved_violation(imp))
    return out


def _unresolved_violation(imp: dict) -> dict:
    """Map one unresolved-import scanner row to a verify violation."""
    suggestions = imp.get("suggestions") or []
    if suggestions:
        msg = (
            f"import `{imp['name']}` does not resolve to any indexed "
            f"symbol or file — did you mean: {', '.join(suggestions[:3])}?"
        )
        severity = SEVERITY_WARN
        fix = f"Use one of: {', '.join(suggestions[:3])}"
    else:
        msg = (
            f"import `{imp['name']}` resolves to NOTHING in this "
            "codebase (no symbol, no file, not stdlib, not a declared "
            "dependency) — likely hallucinated"
        )
        severity = SEVERITY_FAIL
        fix = "Remove or correct the import; add the package to dependencies if it is real"
    return {
        "category": "imports",
        "severity": severity,
        "file": imp["file"],
        "line": imp.get("line"),
        "message": msg,
        "fix": fix,
    }


# ---------------------------------------------------------------------------
# Unresolved-CALL detection (the call-graph sibling of the import firewall).
# Conservative by construction: we only flag a call whose callee resolves to
# NOTHING with HIGH confidence — a bare name bound nowhere in the module (and
# not a builtin), or a ``self.method()`` on a base-less, decorator-free,
# non-dynamic class that defines no such attribute. Anything dynamic
# (``obj.method()`` on an arbitrary object, ``__getattr__``/``setattr``,
# ``from x import *``, inheritance, class/metaclass decorators) is skipped so
# the gate never cries wolf on legitimate dynamic-attribute access.
# ---------------------------------------------------------------------------


def _module_bound_names(tree) -> set[str]:
    """Every name BOUND anywhere in the module (defs, classes, imports,
    assignments, for/with/except targets, params, comprehension/walrus targets,
    global/nonlocal). Over-approximate on purpose: if a name is bound anywhere
    in the file we never flag a bare call to it — precision over recall, because
    a gate that false-positives gets turned off. The sentinel ``"*"`` means a
    star-import is present and bare-name resolution must abstain entirely."""
    import ast

    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
        elif isinstance(node, ast.Name) and isinstance(node.ctx, (ast.Store, ast.Del)):
            names.add(node.id)
        elif isinstance(node, ast.arg):
            names.add(node.arg)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                names.add((alias.asname or alias.name).split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            for alias in node.names:
                names.add("*" if alias.name == "*" else (alias.asname or alias.name))
        elif isinstance(node, (ast.Global, ast.Nonlocal)):
            names.update(node.names)
        elif isinstance(node, ast.ExceptHandler) and node.name:
            names.add(node.name)
    return names


def _bare_name_unresolved_calls(tree, bound: set[str]) -> list[tuple[str, int]]:
    """Bare ``name(...)`` calls whose name is bound nowhere in the module and is
    not a builtin — a guaranteed NameError. Abstain if a star-import is present
    (we can't know what it bound)."""
    import ast
    import builtins

    if "*" in bound:
        return []
    builtin_names = set(dir(builtins))
    out: list[tuple[str, int]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            name = node.func.id
            if name in bound or name in builtin_names:
                continue
            out.append((name, node.func.lineno))
    return out


def _class_is_dynamic(cls) -> bool:
    """True if *cls* could acquire methods/attrs we cannot see statically:
    any non-``object`` / complex base (inheritance), any class decorator,
    a metaclass/keyword, or a ``__getattr__``/``__getattribute__`` /
    ``setattr(self, ...)`` escape hatch. Such classes are skipped wholesale."""
    import ast

    if cls.decorator_list or cls.keywords:
        return True
    for base in cls.bases:
        if not (isinstance(base, ast.Name) and base.id == "object"):
            return True
    for node in ast.walk(cls):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in (
            "__getattr__",
            "__getattribute__",
            "__setattr__",
        ):
            return True
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "setattr":
            return True
    return False


def _class_defined_attrs(cls) -> set[str]:
    """Names defined on *cls*: methods, nested defs, class-level and
    ``self.x = ...`` assignments (the attributes a ``self.X()`` could resolve to)."""
    import ast

    defined: set[str] = set()
    for node in ast.walk(cls):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            defined.add(node.name)
        elif isinstance(node, ast.Assign):
            for tgt in node.targets:
                if isinstance(tgt, ast.Name):
                    defined.add(tgt.id)
                elif isinstance(tgt, ast.Attribute) and isinstance(tgt.value, ast.Name) and tgt.value.id == "self":
                    defined.add(tgt.attr)
        elif isinstance(node, ast.AnnAssign):
            tgt = node.target
            if isinstance(tgt, ast.Name):
                defined.add(tgt.id)
            elif isinstance(tgt, ast.Attribute) and isinstance(tgt.value, ast.Name) and tgt.value.id == "self":
                defined.add(tgt.attr)
    return defined


def _self_method_unresolved_calls(tree) -> list[tuple[str, int]]:
    """``self.method(...)`` calls on a static (base-less, non-dynamic) class that
    defines no such method/attr — a guaranteed AttributeError. Dunder names are
    skipped (Python provides many implicitly)."""
    import ast

    out: list[tuple[str, int]] = []
    for cls in ast.walk(tree):
        if not isinstance(cls, ast.ClassDef) or _class_is_dynamic(cls):
            continue
        defined = _class_defined_attrs(cls)
        for node in ast.walk(cls):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id == "self"
            ):
                attr = node.func.attr
                if attr.startswith("__") or attr in defined:
                    continue
                out.append((attr, node.func.value.lineno))
    return out


def _unresolved_call_violation(path: str, name: str, line: int, kind: str) -> dict:
    if kind == "self":
        msg = (
            f"call `self.{name}(...)` resolves to NOTHING — this class defines "
            f"no `{name}` method/attribute (likely a hallucinated/renamed method)"
        )
        fix = f"Define `{name}` on the class, or call the correct method name"
    else:
        msg = (
            f"call `{name}(...)` resolves to NOTHING — `{name}` is bound nowhere "
            "in this module and is not a builtin (likely hallucinated/undefined)"
        )
        fix = f"Import or define `{name}`, or call the correct name"
    return {
        "category": "imports",
        "severity": SEVERITY_FAIL,
        "file": path,
        "line": line,
        "message": msg,
        "fix": fix,
    }


def _unresolved_calls_for_source(path: str, source: str) -> list[dict]:
    """High-confidence unresolved calls in one Python source string."""
    import ast

    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError):
        # Mid-edit syntax errors are the syntax check's job, not ours.
        return []
    bound = _module_bound_names(tree)
    violations: list[dict] = []
    for name, line in _bare_name_unresolved_calls(tree, bound):
        violations.append(_unresolved_call_violation(path, name, line, "bare"))
    for name, line in _self_method_unresolved_calls(tree):
        violations.append(_unresolved_call_violation(path, name, line, "self"))
    return violations


def _unresolved_call_violations(conn, file_ids: list[int]) -> list[dict]:
    """Flag calls that resolve to NOTHING in the changed Python files. Reads the
    WORKING-TREE source (so a just-added hallucinated call is caught on the line
    it was added). Test-role files are excluded — same default as the import
    resolver and the algo detectors: fixtures intentionally embed dangling
    references. Best-effort: never raises into the gate."""
    if not file_ids:
        return []
    try:
        rows = batched_in(conn, "SELECT id, path, file_role FROM files WHERE id IN ({ph})", file_ids)
        root = Path(find_project_root())
    except Exception as exc:  # noqa: BLE001 — call resolution is advisory to the gate
        from roam.observability import log_swallowed

        log_swallowed("verify.unresolved_calls.setup", exc)
        return []
    out: list[dict] = []
    for row in rows:
        path = row["path"]
        if row["file_role"] == "test" or not str(path).endswith(".py"):
            continue
        try:
            source = (root / path).read_text(encoding="utf-8")
        except OSError:
            continue
        try:
            out.extend(_unresolved_calls_for_source(path, source))
        except Exception as exc:  # noqa: BLE001 — one bad file must not break the gate
            from roam.observability import log_swallowed

            log_swallowed("verify.unresolved_calls.scan", exc)
    return out


# ---------------------------------------------------------------------------
# Error handling consistency check
# ---------------------------------------------------------------------------

_BARE_EXCEPT_RE = re.compile(r"^\s*except\s*:", re.MULTILINE)
_BROAD_EXCEPT_RE = re.compile(r"^\s*except\s+Exception\s*:", re.MULTILINE)
# A silent swallow is only DANGEROUS when the caught exception is BROAD
# (bare `except:`, `except Exception:`, or `except BaseException:`) and the body
# does nothing. A NARROW, specific type caught with `pass` -- `except OSError:`
# around best-effort `os.unlink`/`os.close` cleanup, `except ValueError:` in a
# parse-fallback chain (`try float(v) except ValueError: pass` → try next),
# `except KeyError:` on optional dict access -- is deliberate control flow the
# author opted into by naming the exact exception. Flagging those is noise (was
# 40 of 57 silent-swallow findings on the roam-code self-index, all FPs). Match
# broad/bare-only; the optional `(Exception|BaseException) [as e]` is the sole
# accepted type list, so any named type after `except` fails the match.
_SILENT_EXCEPT_RE = re.compile(
    r"^\s*except\s*(?:\(?\s*(?:Exception|BaseException)\s*\)?\s*(?:as\s+\w+)?)?\s*:"
    r"\s*\n\s*(?:pass|\.\.\.)\s*$",
    re.MULTILINE,
)
_SPECIFIC_EXCEPT_RE = re.compile(r"^\s*except\s+(?!Exception\b)\w+", re.MULTILINE)

_ERROR_NAME_RE = re.compile(r"(Error|Exception|Err|Fault|Failure|Panic)$", re.IGNORECASE)


def _has_noqa(line_text: str, codes: tuple[str, ...]) -> bool:
    """True if the source line carries a ruff/flake8 ``# noqa`` covering one of
    ``codes`` (or a BARE ``# noqa`` which covers everything). Lets verify respect
    the codebase's EXISTING in-line acknowledgements — e.g. a deliberate broad
    ``except Exception:  # noqa: BLE001`` resilience pattern — instead of
    re-flagging code the author already marked intended."""
    m = re.search(r"#\s*noqa(?::\s*([A-Z0-9, ]+))?", line_text or "")
    if not m:
        return False
    if m.group(1) is None:  # bare `# noqa` suppresses all codes
        return True
    listed = {c.strip().upper() for c in m.group(1).split(",") if c.strip()}
    return any(c.upper() in listed for c in codes)


def _blank_token_spans(content: str, toks, mask_types: set) -> str:
    """Replace the char spans of every token in *mask_types* with spaces
    (newlines kept), so length + line structure are preserved."""
    line_starts = [0]
    for ln in content.splitlines(keepends=True):
        line_starts.append(line_starts[-1] + len(ln))
    n_lines = len(line_starts)
    spans = [
        (line_starts[t.start[0] - 1] + t.start[1], line_starts[t.end[0] - 1] + t.end[1])
        for t in toks
        if t.type in mask_types and 1 <= t.start[0] < n_lines and 1 <= t.end[0] < n_lines
    ]
    if not spans:
        return content
    chars = list(content)
    for start, end in spans:
        seg = "".join(chars[start:end])
        chars[start:end] = re.sub(r"[^\n]", " ", seg)
    return "".join(chars)


def _mask_py_strings_comments(content: str) -> str:
    """Blank Python string + comment token spans (newlines preserved) so the
    except-clause regexes don't match `except Exception:` text embedded in a
    docstring, string literal, or test fixture (the W-class FP the prior dogfood
    loop flagged: `test_verify_noqa.py`'s `_SRC` fixture string was re-flagged).
    Length-preserving, so `finditer` offsets still map to correct line numbers.
    Best-effort: source that won't tokenize is returned unchanged."""
    import io
    import tokenize

    try:
        toks = list(tokenize.generate_tokens(io.StringIO(content).readline))
    except Exception:  # noqa: BLE001 — unparseable source → scan raw (no masking)
        return content
    mask_types = {tokenize.STRING, tokenize.COMMENT}
    mask_types |= {
        getattr(tokenize, n) for n in ("FSTRING_START", "FSTRING_MIDDLE", "FSTRING_END") if hasattr(tokenize, n)
    }
    return _blank_token_spans(content, toks, mask_types)


def _custom_error_count(conn) -> int:
    rows = conn.execute("""
        SELECT s.name, s.kind
        FROM symbols s
        WHERE (s.name LIKE '%Error%'
            OR s.name LIKE '%Exception%'
            OR s.name LIKE '%Failure%')
          AND s.kind IN ('class', 'struct', 'interface')
    """).fetchall()
    return sum(1 for row in rows if _ERROR_NAME_RE.search(row["name"]))


def _python_source_files(conn, file_ids: list[int], root: Path) -> list[tuple[str, str]]:
    rows = batched_in(conn, "SELECT id, path FROM files WHERE id IN ({ph})", file_ids)
    files: list[tuple[str, str]] = []
    for row in rows:
        rel_path = row["path"]
        if not rel_path.endswith(".py"):
            continue
        path = root / rel_path
        if not path.exists():
            continue
        try:
            files.append((rel_path, path.read_text(encoding="utf-8", errors="replace")))
        except OSError:
            continue
    return files


def _line_number_for_match(scan: str, match) -> int:
    return scan[: match.start()].count("\n") + 1


def _noqa_on_line(source_lines: list[str], line_num: int, codes: tuple[str, ...]) -> bool:
    line_text = source_lines[line_num - 1] if 1 <= line_num <= len(source_lines) else ""
    return _has_noqa(line_text, codes)


def _bare_except_violation(path: str, line_num: int, custom_error_count: int) -> dict:
    if custom_error_count:
        suffix = f"(codebase has {custom_error_count} custom exception classes)"
    else:
        suffix = "(use specific exceptions)"
    return {
        "category": "error_handling",
        "severity": SEVERITY_FAIL,
        "file": path,
        "line": line_num,
        "message": f"bare `except:` {suffix}",
        "fix": "Replace bare `except:` with a specific exception type",
    }


def _broad_except_violation(path: str, line_num: int, custom_error_count: int) -> dict:
    if custom_error_count:
        suffix = f"(codebase has {custom_error_count} specific exception classes)"
    else:
        suffix = "(consider catching specific exceptions)"
    return {
        "category": "error_handling",
        "severity": SEVERITY_WARN,
        "file": path,
        "line": line_num,
        "message": f"broad `except Exception:` {suffix}",
        "fix": "Narrow the exception type to catch only expected errors",
    }


def _silent_except_violation(path: str, line_num: int, _custom_error_count: int) -> dict:
    return {
        "category": "error_handling",
        "severity": SEVERITY_WARN,
        "file": path,
        "line": line_num,
        "message": "broad silent exception swallow (no logging/re-raise)",
        "fix": "Add logging or re-raise the exception instead of silently swallowing",
    }


def _error_regex_violations(
    scan: str,
    source_lines: list[str],
    path: str,
    custom_error_count: int,
    regex,
    noqa_codes: tuple[str, ...],
    build_violation,
) -> list[dict]:
    violations: list[dict] = []
    for match in regex.finditer(scan):
        line_num = _line_number_for_match(scan, match)
        if _noqa_on_line(source_lines, line_num, noqa_codes):
            continue
        violations.append(build_violation(path, line_num, custom_error_count))
    return violations


def _error_handling_violations_for_file(path: str, content: str, custom_error_count: int) -> list[dict]:
    source_lines = content.split("\n")
    scan = _mask_py_strings_comments(content)
    return (
        _error_regex_violations(
            scan, source_lines, path, custom_error_count, _BARE_EXCEPT_RE, ("E722",), _bare_except_violation
        )
        + _error_regex_violations(
            scan, source_lines, path, custom_error_count, _BROAD_EXCEPT_RE, ("BLE001",), _broad_except_violation
        )
        + _error_regex_violations(
            scan,
            source_lines,
            path,
            custom_error_count,
            _SILENT_EXCEPT_RE,
            ("BLE001", "E722"),
            _silent_except_violation,
        )
    )


def _error_handling_score(files_checked: int, issues_found: int) -> int:
    if files_checked == 0 or issues_found == 0:
        return 100
    return max(0, 100 - min(issues_found * 15, 100))


def _check_error_handling(conn, file_ids: list[int], root: Path) -> dict:
    """Check error handling patterns in changed files.

    Looks for:
    - Bare except: clauses
    - Broad Exception catches
    - Silent exception swallowing (except: pass)

    Compares against the codebase's use of custom error classes.
    """
    if not file_ids:
        return {"score": 100, "violations": []}

    # 1. Detect codebase error patterns: how many specific exception classes exist?
    custom_error_count = _custom_error_count(conn)

    # 2. Read changed files and check for bad patterns
    violations = []
    python_files = _python_source_files(conn, file_ids, root)
    for path, content in python_files:
        violations.extend(_error_handling_violations_for_file(path, content, custom_error_count))

    return {"score": _error_handling_score(len(python_files), len(violations)), "violations": violations}


# ---------------------------------------------------------------------------
# Duplicate logic detection
# ---------------------------------------------------------------------------

# A function name defined in this many DISTINCT files is a shared interface /
# ABC contract (every `*_lang.py` overrides `language_name` / `extract_symbols`;
# every bridge implements `name` / `resolve`), NOT copy-paste duplication.
# Polymorphic overrides are the point, so flagging each pair is noise (was the
# bulk of 456 duplicate findings on the roam-code self-index, e.g. 26x
# `language_name`). A genuine copied helper lives in 2 files; an interface
# contract lives in many -- so a >=3-file threshold keeps real-duplication
# detection while dropping the override explosion.
_INTERFACE_CONTRACT_MIN_FILES = 3
_SIMILARITY_PASS_CAP = 150


def _new_duplicate_symbols(conn, file_ids: list[int]):
    return batched_in(
        conn,
        """SELECT s.id, s.name, s.kind, s.signature, s.line_start,
                  f.path as file_path, f.file_role AS file_role
           FROM symbols s
           JOIN files f ON s.file_id = f.id
           WHERE s.file_id IN ({ph})
             AND s.kind IN ('function', 'method')""",
        file_ids,
    )


def _existing_duplicate_symbols(conn):
    return conn.execute("""
        SELECT s.id, s.name, s.kind, s.signature, s.line_start,
               f.path as file_path, f.file_role AS file_role
        FROM symbols s
        JOIN files f ON s.file_id = f.id
        WHERE s.kind IN ('function', 'method')
    """).fetchall()


def _duplicate_name_eligible(name: str) -> bool:
    return len(name) >= 4 and not name.startswith("_") and name not in _DUPLICATE_ENTRYPOINT_SKIP_NAMES


def _duplicate_symbol_eligible(symbol) -> bool:
    return _duplicate_name_eligible(symbol["name"])


def _duplicate_indexes(existing_symbols) -> tuple[dict[str, list], dict[tuple[str, str], set]]:
    # Build lookup by name for fast filtering + a per-(role,name) distinct-file
    # count so a name shared across many files (an interface/ABC contract) is
    # not mistaken for duplication. Keyed by role so the contract count only
    # aggregates comparable code (we only ever compare within a role below).
    existing_by_name: dict[str, list] = defaultdict(list)
    name_files: dict[tuple[str, str], set] = defaultdict(set)
    for sym in existing_symbols:
        if not _duplicate_symbol_eligible(sym):
            continue
        lower_name = sym["name"].lower()
        existing_by_name[lower_name].append(sym)
        name_files[(sym["file_role"] or "", lower_name)].add(sym["file_path"])
    return existing_by_name, name_files


def _is_interface_contract(name_files: dict[tuple[str, str], set], role: str, lower_name: str) -> bool:
    return len(name_files.get((role, lower_name), ())) >= _INTERFACE_CONTRACT_MIN_FILES


def _same_role_external_symbol(existing, new_sym, new_ids: set[int], role: str) -> bool:
    return (
        existing["id"] not in new_ids
        and existing["file_path"] != new_sym["file_path"]
        and (existing["file_role"] or "") == role
    )


def _command_entrypoint_name_for_path(path: str) -> str | None:
    normalised = path.replace("\\", "/")
    prefix = "src/roam/commands/cmd_"
    if not normalised.startswith(prefix) or not normalised.endswith(".py"):
        return None
    return normalised[len(prefix) : -len(".py")]


def _is_cli_entrypoint_mirror_pair(new_sym, existing) -> bool:
    """Integration shims intentionally mirror Click command entrypoint names."""
    new_path = (new_sym["file_path"] or "").replace("\\", "/")
    existing_path = (existing["file_path"] or "").replace("\\", "/")
    mirror_paths = {"src/roam/api.py", "src/roam/mcp_server.py"}
    if new_path in mirror_paths:
        command_name = _command_entrypoint_name_for_path(existing_path)
        mirror_name = new_sym["name"]
    elif existing_path in mirror_paths:
        command_name = _command_entrypoint_name_for_path(new_path)
        mirror_name = existing["name"]
    else:
        return False
    return command_name == mirror_name


def _is_shared_substrate_lifecycle_pair(new_sym, existing) -> bool:
    """Permit and lease records intentionally expose the same expiry API."""
    if new_sym["name"] != "is_expired_at":
        return False
    paths = {
        (new_sym["file_path"] or "").replace("\\", "/"),
        (existing["file_path"] or "").replace("\\", "/"),
    }
    return paths == {"src/roam/leases/store.py", "src/roam/permits/store.py"}


def _exact_duplicate_violation(new_sym, existing) -> dict:
    name = new_sym["name"]
    return {
        "category": "duplicates",
        "severity": SEVERITY_WARN,
        "file": new_sym["file_path"],
        "line": new_sym["line_start"],
        "message": f"fn `{name}` has same name as `{existing['name']}` at {loc(existing['file_path'], existing['line_start'])}",
        "fix": f"Consider reusing `{existing['name']}` from {existing['file_path']}",
    }


def _exact_duplicate_for_symbol(
    new_sym, existing_by_name: dict[str, list], new_ids: set[int], role: str
) -> dict | None:
    for existing in existing_by_name.get(new_sym["name"].lower(), []):
        # Cross-role matches (src fn vs its test/script/ci namesake) are
        # expected mirroring, not duplication -- compare within a role only.
        if (
            _same_role_external_symbol(existing, new_sym, new_ids, role)
            and not _is_cli_entrypoint_mirror_pair(new_sym, existing)
            and not _is_shared_substrate_lifecycle_pair(new_sym, existing)
        ):
            return _exact_duplicate_violation(new_sym, existing)
    return None


def _similar_name_candidate(existing_name: str, name_lower: str, matcher: SequenceMatcher) -> float | None:
    if abs(len(existing_name) - len(name_lower)) > 5:
        return None
    # A name that contains (or is contained by) the other is a deliberate
    # variant -- `run_agent` ⊂ `run_agent_opt`, `name` ⊂ `_names`,
    # `source_extensions` vs `source_to_test...` -- not a duplicate.
    if name_lower in existing_name or existing_name in name_lower:
        return None
    matcher.set_seq1(existing_name)
    if matcher.real_quick_ratio() < 0.8 or matcher.quick_ratio() < 0.8:
        return None
    ratio = matcher.ratio()
    return ratio if 0.8 <= ratio < 1.0 else None


def _similar_duplicate_candidates(new_sym, existing_by_name: dict[str, list], new_ids: set[int], role: str) -> list:
    candidates = []
    name_lower = new_sym["name"].lower()
    matcher = SequenceMatcher()
    matcher.set_seq2(name_lower)
    for existing_name, existing_list in existing_by_name.items():
        ratio = _similar_name_candidate(existing_name, name_lower, matcher)
        if ratio is None:
            continue
        for existing in existing_list:
            if _same_role_external_symbol(existing, new_sym, new_ids, role):
                candidates.append((existing, ratio))
                break
    return candidates


def _similar_duplicate_violation(new_sym, existing, ratio: float) -> dict:
    name = new_sym["name"]
    return {
        "category": "duplicates",
        "severity": SEVERITY_INFO,
        "file": new_sym["file_path"],
        "line": new_sym["line_start"],
        "message": (
            f"fn `{name}` is similar to "
            f"`{existing['name']}` at "
            f"{loc(existing['file_path'], existing['line_start'])} "
            f"({round(ratio * 100)}% match)"
        ),
        "fix": f"Check if `{existing['name']}` in {existing['file_path']} provides the same functionality",
    }


def _similar_duplicate_for_symbol(
    new_sym, existing_by_name: dict[str, list], new_ids: set[int], role: str
) -> dict | None:
    candidates = _similar_duplicate_candidates(new_sym, existing_by_name, new_ids, role)
    if not candidates:
        return None
    existing, ratio = max(candidates, key=lambda item: item[1])
    return _similar_duplicate_violation(new_sym, existing, ratio)


def _duplicate_score(checked: int, violations: list[dict]) -> int:
    if checked == 0:
        return 100
    fail_count = sum(1 for violation in violations if violation["severity"] == SEVERITY_FAIL)
    warn_count = sum(1 for violation in violations if violation["severity"] == SEVERITY_WARN)
    info_count = sum(1 for violation in violations if violation["severity"] == SEVERITY_INFO)
    penalty = fail_count * 20 + warn_count * 10 + info_count * 5
    return max(0, 100 - penalty)


def _duplicate_violations_for_symbols(
    eligible,
    existing_by_name: dict[str, list],
    name_files: dict[tuple[str, str], set],
    new_ids: set[int],
    similarity_enabled: bool,
) -> list[dict]:
    violations = []
    for new_sym in eligible:
        lower_name = new_sym["name"].lower()
        role = new_sym["file_role"] or ""
        # Shared interface/ABC contract (defined in many same-role files) ->
        # not a duplicate; skip both the exact-name and similar-name checks.
        if _is_interface_contract(name_files, role, lower_name):
            continue
        exact = _exact_duplicate_for_symbol(new_sym, existing_by_name, new_ids, role)
        if exact is not None:
            violations.append(exact)
        # Check for similar names (ratio > 0.8) in existing symbols. The
        # difflib fast path sets seq2 once per changed symbol and uses the two
        # upper bounds before the expensive ratio() call.
        if similarity_enabled:
            similar = _similar_duplicate_for_symbol(new_sym, existing_by_name, new_ids, role)
            if similar is not None:
                violations.append(similar)
    return violations


def _check_duplicates(conn, file_ids: list[int]) -> dict:
    """Detect potential duplicate functions by comparing new symbols to existing ones.

    Uses name similarity (SequenceMatcher) and signature comparison to find
    symbols in changed files that may duplicate existing functionality.
    """
    if not file_ids:
        return {"score": 100, "violations": []}

    # 1. Get symbols from changed files
    new_symbols = _new_duplicate_symbols(conn, file_ids)
    if not new_symbols:
        return {"score": 100, "violations": []}

    # 2. Get all other functions/methods NOT in changed files
    existing_by_name, name_files = _duplicate_indexes(_existing_duplicate_symbols(conn))
    new_ids = {symbol["id"] for symbol in new_symbols}
    eligible = [symbol for symbol in new_symbols if _duplicate_symbol_eligible(symbol)]
    similarity_enabled = len(eligible) <= _SIMILARITY_PASS_CAP
    violations = _duplicate_violations_for_symbols(eligible, existing_by_name, name_files, new_ids, similarity_enabled)

    result: dict = {"score": _duplicate_score(len(eligible), violations), "violations": violations}
    if not similarity_enabled:
        # W-Pattern2: part of the check did not run — disclose, never imply
        # a full-fidelity pass.
        result["similarity_pass_skipped"] = len(eligible)
    return result


# ---------------------------------------------------------------------------
# Syntax integrity check
# ---------------------------------------------------------------------------

# Data / config / prose-markup languages the syntax check does NOT apply to.
# verify's syntax gate exists to catch authored CODE that won't parse; a
# config or markup file is out of scope. Two failure modes this skip removes:
# (1) yaml has a roam-index grammar but isn't wired into `parse_file` → it
# returns None → was mis-counted as a parse failure (30 false positives when
# verifying a whole tree of taint-rule + CI-template yaml). (2) markdown/html/
# css DO parse but their error nodes are routine prose artifacts, not
# actionable code-syntax feedback. Real code (python/js/go/...) is error-
# tolerant in tree-sitter (a broken file yields a tree with ERROR nodes, never
# None), so the W-Pattern2 "None on a code file = unverified, don't credit"
# rule still holds for everything NOT in this set.
_SYNTAX_SKIP_LANGS: frozenset[str] = frozenset(
    {
        "yaml",
        "json",
        "toml",
        "ini",
        "markdown",
        "text",
        "csv",
        "tsv",
        "xml",
        "html",
        "css",
        "scss",
        "less",
        # Regex-only languages (no tree-sitter grammar): parse_file returns
        # None for every file, which was disclosed as "could not parse"
        # noise on each verify pass over legacy FoxPro .prg/.scx artifacts.
        # The extractor never syntax-checks them, so
        # reporting them as unverified-code is misleading — they're out of
        # scope for this rule, like markup.
        "foxpro",
    }
)


def _syntax_unavailable() -> dict:
    return {
        "score": 100,
        "violations": [],
        "available": False,
        "unavailable_reason": "tree-sitter parser unavailable -- syntax check did not run",
    }


def _syntax_file_rows(conn, file_ids: list[int]):
    return batched_in(conn, "SELECT id, path, language FROM files WHERE id IN ({ph})", file_ids)


def _syntax_parse_failure(path: str) -> dict:
    return {
        "category": "syntax",
        "severity": SEVERITY_INFO,
        "file": path,
        "line": None,
        "message": f"could not parse `{path}` -- syntax not verified",
        "fix": "Verify the file parses; this file was NOT syntax-checked",
    }


def _syntax_read_failure(path: str) -> dict:
    return {
        "category": "syntax",
        "severity": SEVERITY_INFO,
        "file": path,
        "line": None,
        "message": f"could not read `{path}` -- syntax not verified",
        "fix": "Verify the file can be read; this file was NOT syntax-checked",
    }


def _python_syntax_error(path: str, exc: SyntaxError) -> dict:
    line_num = exc.lineno or 1
    return {
        "category": "syntax",
        "severity": SEVERITY_FAIL,
        "file": path,
        "line": line_num,
        "message": f"python syntax error at line {line_num}: {exc.msg}",
        "fix": "Fix the Python syntax error indicated by ast.parse",
    }


def _syntax_result(
    files_checked: int = 0,
    files_with_errors: int = 0,
    parse_failures: int = 0,
    violations: list[dict] | None = None,
) -> dict:
    return {
        "files_checked": files_checked,
        "files_with_errors": files_with_errors,
        "parse_failures": parse_failures,
        "violations": violations or [],
    }


def _python_ast_syntax_gate(path: str, fpath: Path) -> dict | None:
    try:
        import ast

        ast.parse(fpath.read_text(encoding="utf-8"))
    except SyntaxError as exc:
        return _syntax_result(files_checked=1, files_with_errors=1, violations=[_python_syntax_error(path, exc)])
    except OSError:
        return _syntax_result(parse_failures=1, violations=[_syntax_read_failure(path)])
    return None


def _tree_sitter_error_violations(path: str, tree) -> list[dict]:
    violations = []
    for node in _find_error_nodes(tree.root_node)[:5]:
        line_num = node.start_point[0] + 1
        violations.append(
            {
                "category": "syntax",
                "severity": SEVERITY_FAIL,
                "file": path,
                "line": line_num,
                "message": f"syntax error at line {line_num}",
                "fix": "Fix the syntax error indicated by the parser",
            }
        )
    return violations


def _tree_sitter_syntax_gate(path: str, fpath: Path, lang: str, parse_file) -> dict:
    try:
        result = parse_file(fpath, lang)
    except Exception:  # noqa: BLE001 — any parse crash = unverified file (W-Pattern2)
        return _syntax_result(parse_failures=1, violations=[_syntax_parse_failure(path)])

    if result is None or result[0] is None:
        return _syntax_result(parse_failures=1, violations=[_syntax_parse_failure(path)])

    violations = _tree_sitter_error_violations(path, result[0])
    return _syntax_result(files_checked=1, files_with_errors=1 if violations else 0, violations=violations)


def _syntax_result_for_file(row, root: Path, parse_file) -> dict | None:
    path = row["path"]
    fpath = root / path
    if not fpath.exists():
        return None

    lang = row["language"]
    if not lang or lang in _SYNTAX_SKIP_LANGS:
        return None

    if lang == "python":
        py_result = _python_ast_syntax_gate(path, fpath)
        if py_result is not None:
            return py_result
    return _tree_sitter_syntax_gate(path, fpath, lang, parse_file)


def _merge_syntax_totals(totals: dict, result: dict) -> None:
    totals["files_checked"] += result["files_checked"]
    totals["files_with_errors"] += result["files_with_errors"]
    totals["parse_failures"] += result["parse_failures"]
    totals["violations"].extend(result["violations"])


def _syntax_score(files_checked: int, files_with_errors: int) -> int:
    if files_checked == 0 or files_with_errors == 0:
        return 100
    score = round(100 * (files_checked - files_with_errors) / files_checked)
    return max(0, min(100, score))


def _check_syntax(conn, file_ids: list[int], root: Path) -> dict:
    """Check for syntax errors via tree-sitter ERROR nodes.

    Uses tree-sitter to parse changed files and reports any ERROR nodes found.

    W-Pattern2: a file whose parse CRASHED was not actually verified -- it
    must NOT be credited as a clean score-100 file. Such files are tracked
    in ``parse_failures`` and surfaced as INFO-level violations; the syntax
    category is marked ``available: false`` when the underlying check could
    not run at all (tree-sitter import failure), so the composite scorer can
    distinguish "skipped/crashed" from a genuine perfect score.
    """
    if not file_ids:
        return {"score": 100, "violations": []}

    try:
        from roam.index.parser import parse_file
    except ImportError:
        # If tree-sitter is not available the syntax check could not run.
        # W-Pattern2: do NOT silently score 100 (a fabricated perfect
        # verdict); mark the category unavailable so the composite scorer
        # treats it as a non-credit dimension rather than a passed gate.
        return _syntax_unavailable()

    totals = _syntax_result()
    for row in _syntax_file_rows(conn, file_ids):
        result = _syntax_result_for_file(row, root, parse_file)
        if result is not None:
            _merge_syntax_totals(totals, result)

    result_dict: dict = {
        "score": _syntax_score(totals["files_checked"], totals["files_with_errors"]),
        "violations": totals["violations"],
    }
    if totals["parse_failures"] > 0:
        # W-Pattern2: disclose that some files were not actually verified.
        result_dict["parse_failures"] = totals["parse_failures"]
    return result_dict


# String-literal node types across the tree-sitter grammars we verify.
# ERROR nodes INSIDE a string are the string's content (unicode-control fuzz
# corpora, embedded snippets), not code syntax errors — treat strings as
# opaque (dogfood: an intentional fuzz corpus in a test file
# produced syntax FAILs).
_STRING_NODE_TYPES = frozenset(
    {
        "string",
        "string_literal",
        "template_string",
        "raw_string_literal",
        "interpreted_string_literal",
        "heredoc_body",
        "string_content",
        "char_literal",
    }
)


def _find_error_nodes(node) -> list:
    """Recursively find ERROR nodes in a tree-sitter AST.

    Does not descend into string literals — their content is opaque data,
    not code, so an ERROR inside one is never an actionable syntax finding.
    """
    errors = []
    if node.type == "ERROR":
        errors.append(node)
        return errors
    if node.type in _STRING_NODE_TYPES:
        return errors
    for child in node.children:
        errors.extend(_find_error_nodes(child))
    return errors


# ---------------------------------------------------------------------------
# Complexity check (KISS) — reuses indexed symbol_metrics.cognitive_complexity
# ---------------------------------------------------------------------------

_COMPLEXITY_WARN = 15  # SonarSource-grade threshold cmd_complexity uses
_COMPLEXITY_FAIL = 25


# Vue composables / React hooks keep their closures INSIDE one `use*()`
# container over shared refs — that's the framework idiom, not bloat. The
# extractor scores the whole container (inner closures aren't separate
# symbols), so the number is the SUM over all closures and unactionable at
# function thresholds: extracting closures to module scope threads every
# shared ref through every signature, hurting the code to please the metric
# (dogfood: `useMyDataSyncDriver` scored 204 vs threshold 15).
# Until closures are scored individually, container findings are ADVISORY.
_COMPOSABLE_CONTAINER_RE = re.compile(r"^use[A-Z]\w*$")
_COMPOSABLE_LANGS = frozenset({"javascript", "typescript", "tsx", "jsx", "vue"})


def _is_composable_container(row) -> bool:
    language = (row["language"] or "").lower()
    return language in _COMPOSABLE_LANGS and bool(_COMPOSABLE_CONTAINER_RE.match(row["name"] or ""))


def _composable_complexity_violation(row, cc: float) -> dict:
    rounded = round(cc)
    name = row["name"]
    return {
        "category": "complexity",
        "severity": SEVERITY_INFO,
        "file": row["file_path"],
        "line": row["line_start"],
        "message": (
            f"composable `{name}` container complexity {rounded} — sum over its "
            f"inner closures, advisory only (container idiom, not per-function load)"
        ),
        "symbol": name,
        "cognitive_complexity": rounded,
        "fix": f"Review the inner closures of `{name}` individually; extract only closures that don't share refs",
    }


def _standard_complexity_violation(row, cc: float, threshold: int) -> dict:
    rounded = round(cc)
    name = row["name"]
    return {
        "category": "complexity",
        "severity": SEVERITY_FAIL if cc >= _COMPLEXITY_FAIL else SEVERITY_WARN,
        "file": row["file_path"],
        "line": row["line_start"],
        "message": f"fn `{name}` cognitive complexity {rounded} (threshold {threshold})",
        "symbol": name,
        "cognitive_complexity": rounded,
        "fix": f"Decompose `{name}` — extract helpers / flatten nesting to lower cognitive load",
    }


def _complexity_violation_for_row(row, cc: float, threshold: int) -> dict:
    if _is_composable_container(row):
        return _composable_complexity_violation(row, cc)
    return _standard_complexity_violation(row, cc, threshold)


def _check_complexity(conn, file_ids: list[int], threshold: int = _COMPLEXITY_WARN) -> dict:
    """Flag changed functions/methods whose cognitive complexity is too high."""
    if not file_ids:
        return {"score": 100, "violations": []}
    rows = batched_in(
        conn,
        """SELECT s.name, s.line_start, f.path AS file_path,
                  COALESCE(f.language, '') AS language,
                  sm.cognitive_complexity AS cc
           FROM symbols s
           JOIN symbol_metrics sm ON sm.symbol_id = s.id
           JOIN files f ON s.file_id = f.id
           WHERE s.file_id IN ({ph})
             AND s.kind IN ('function', 'method')""",
        file_ids,
    )
    violations = []
    checked = 0
    for r in rows:
        checked += 1
        cc = float(r["cc"] or 0)
        if cc >= threshold:
            violations.append(_complexity_violation_for_row(r, cc, threshold))
    if checked == 0:
        score = 100
    else:
        penalty = sum(15 if v["severity"] == SEVERITY_FAIL else 8 for v in violations if v["severity"] != SEVERITY_INFO)
        score = max(0, 100 - penalty)
    return {"score": score, "violations": violations}


_MODULE_INIT_SKIP_LANGS = frozenset(
    {"", "markdown", "json", "yaml", "yml", "toml", "ini", "text", "txt", "html", "css", "csv", "xml", "sql"}
)


def _import_side_effect_violations(path: str, src: str) -> list[dict]:
    """Build import_side_effects findings (io_write/process at module scope)
    for one file's source."""
    from roam.world_model.side_effects import scan_module_init_effects

    out: list[dict] = []
    for line_no, kind, label in scan_module_init_effects(src):
        if kind not in ("io_write", "process"):
            continue
        out.append(
            {
                "category": "import_side_effects",
                "severity": SEVERITY_WARN,
                "file": path,
                "line": line_no,
                "message": f"module-load side effect: {kind} ({label}) runs at import time",
                "fix": (
                    "Move import-time I/O into an explicit init function the "
                    "caller invokes (e.g. `setup()` / `get_db()`), so importing "
                    "the module has no side effects"
                ),
            }
        )
    return out


def _check_import_side_effects(conn, file_ids: list[int], root: Path) -> dict:
    """Flag I/O executed at MODULE-LOAD (import) time — executing DDL, writing,
    spawning a server/timer/subprocess at top level. Merely importing such a
    module mutates the world, which breaks tests, multiple entry points, and
    tooling. Only the unambiguous ``io_write``/``process`` kinds are flagged
    (a top-level resource *open* / config read is common and often lazy, so it
    is not). Diff scoping downstream keeps only the lines this edit touched."""
    if not file_ids:
        return {"score": 100, "violations": []}
    rows = batched_in(
        conn,
        "SELECT id, path, language FROM files WHERE id IN ({ph})",
        file_ids,
    )
    violations: list[dict] = []
    checked = 0
    for r in rows:
        if (r["language"] or "").lower() in _MODULE_INIT_SKIP_LANGS:
            continue
        try:
            src = (root / r["path"]).read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        checked += 1
        violations.extend(_import_side_effect_violations(r["path"], src))
    score = 100 if checked == 0 else max(0, 100 - 8 * len(violations))
    return {"score": score, "violations": violations}


# ---------------------------------------------------------------------------
# Import-cycle check (architecture) — file-level SCC over file_edges
# ---------------------------------------------------------------------------


# A per-edit verify only flags SMALL, ACTIONABLE cycles. A huge SCC (a systemic
# god-tangle) is not something one edit created or can break — that belongs to
# `roam health` (which reports cycles). Flagging it here is pure noise (the research's
# #1 reason these tools get ignored), so cap the cycle size we report.
_MAX_ACTIONABLE_CYCLE = 8


def _is_actionable_cycle(scc: set[str]) -> bool:
    return 2 <= len(scc) <= _MAX_ACTIONABLE_CYCLE


def _unseen_changed_cycle_files(scc: set[str], changed_set: set[str], seen: set[str]) -> list[str]:
    return [path for path in sorted(changed_set & scc) if path not in seen]


def _cycle_violation(path: str, scc: set[str]) -> dict:
    others = sorted(scc - {path})
    tail = "..." if len(others) > 3 else ""
    return {
        "category": "cycles",
        "severity": SEVERITY_WARN,
        "file": path,
        "line": None,
        "message": f"`{path}` is in an import cycle of {len(scc)} files (with {', '.join(others[:3])}{tail})",
        "fix": "Break the cycle — invert one dependency or extract the shared piece into a new module",
    }


def _collect_cycle_violations(graph, changed_set: set[str]) -> list[dict]:
    """One WARN per changed file that sits in a SMALL import cycle (2..8 files)."""
    import networkx as nx

    violations: list[dict] = []
    seen: set[str] = set()
    for scc in nx.strongly_connected_components(graph):
        if not _is_actionable_cycle(scc):
            continue
        for path in _unseen_changed_cycle_files(scc, changed_set, seen):
            seen.add(path)
            violations.append(_cycle_violation(path, scc))
    return violations


# ---------------------------------------------------------------------------
# Secrets / leak check — credential patterns + optional repo-local catalogue
# ---------------------------------------------------------------------------

# Extensions worth scanning for leaked credentials / forbidden language.
# Mirrors the anti-leak scanner's surface (10 extensions there): text-bearing
# source/config/docs. Calibration on the self-index: the first cut produced
# 40 findings, 37 of them fixture false positives — 0 after the bearer-regex
# payload floor + 12 file-level suppressions.
_SECRETS_SCAN_EXTENSIONS = (
    ".py",
    ".md",
    ".html",
    ".yml",
    ".yaml",
    ".json",
    ".txt",
    ".tmpl",
    ".css",
    ".js",
    ".ts",
    ".tsx",
    ".jsx",
    ".vue",
    ".php",
    ".rb",
    ".go",
    ".java",
    ".sh",
    ".env",
    ".toml",
    ".ini",
    ".cfg",
)
_SECRETS_MAX_PER_FILE = 5
_LEAK_PATTERNS_FILENAME = ".roam-leak-patterns.py"


def _load_repo_leak_patterns(root: Path) -> tuple[list, object | None, str | None]:
    """Load the optional repo-local leak catalogue ``.roam-leak-patterns.py``.

    Contract: the module exposes ``FORBIDDEN_PATTERNS`` (a list of
    ``(name, compiled_regex)`` tuples) and optionally ``should_scan(rel_path)
    -> bool`` to exempt files that intentionally contain the patterns (the
    catalogue itself, exemplar test fixtures). Returns
    ``(patterns, should_scan_fn, error)`` — fail-open: a broken catalogue
    yields no patterns plus a disclosed error string, never a crash.
    """
    cat_path = root / _LEAK_PATTERNS_FILENAME
    if not cat_path.is_file():
        return [], None, None
    try:
        import importlib.util

        spec = importlib.util.spec_from_file_location("_roam_leak_patterns", cat_path)
        if spec is None or spec.loader is None:
            return [], None, f"{_LEAK_PATTERNS_FILENAME}: not importable"
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        patterns = list(getattr(mod, "FORBIDDEN_PATTERNS", []) or [])
        should_scan = getattr(mod, "should_scan", None)
        return patterns, should_scan, None
    except Exception as exc:  # noqa: BLE001 — gate must fail open, disclosed
        return [], None, f"{_LEAK_PATTERNS_FILENAME}: {exc}"


def _secret_scan_targets(changed_paths: list[str], root: Path) -> list[tuple[str, Path]]:
    targets = []
    for rel in changed_paths:
        norm = rel.replace("\\", "/")
        if norm.endswith(_SECRETS_SCAN_EXTENSIONS) and (root / rel).is_file():
            targets.append((norm, root / rel))
    return targets


def _read_secret_scan_text(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None


def _repo_patterns_enabled(norm: str, repo_patterns: list, repo_should_scan) -> bool:
    return bool(repo_patterns) and (repo_should_scan is None or repo_should_scan(norm))


def _builtin_secret_hit(line: str, norm: str, secret_patterns, pattern_id_fn) -> tuple[str, str, str] | None:
    for pattern in secret_patterns:
        if pattern.search(line):
            return (
                SEVERITY_FAIL,
                f"credential-shaped string ({pattern_id_fn(pattern)}) in `{norm}`",
                "Remove the credential and rotate it; load secrets from the environment instead",
            )
    return None


def _repo_secret_hit(line: str, norm: str, repo_patterns: list) -> tuple[str, str, str] | None:
    for name, pattern in repo_patterns:
        if pattern.search(line):
            return (
                SEVERITY_WARN,
                f"repo-forbidden pattern [{name}] in `{norm}`",
                f"Reword the line — [{name}] is on this repo's never-publish list ({_LEAK_PATTERNS_FILENAME})",
            )
    return None


def _secret_hit_for_line(
    line: str,
    norm: str,
    secret_patterns,
    pattern_id_fn,
    repo_patterns: list,
    scan_repo_patterns: bool,
) -> tuple[str, str, str] | None:
    hit = _builtin_secret_hit(line, norm, secret_patterns, pattern_id_fn)
    if hit is not None:
        return hit
    return _repo_secret_hit(line, norm, repo_patterns) if scan_repo_patterns else None


def _secret_violation(norm: str, line_no: int, hit: tuple[str, str, str]) -> dict:
    severity, message, fix = hit
    return {
        "category": "secrets",
        "severity": severity,
        "file": norm,
        "line": line_no,
        "message": message,
        "fix": fix,
    }


def _secret_violations_for_file(
    norm: str,
    text: str,
    secret_patterns,
    pattern_id_fn,
    repo_patterns: list,
    scan_repo_patterns: bool,
) -> list[dict]:
    violations: list[dict] = []
    for line_no, line in enumerate(text.splitlines(), start=1):
        if len(violations) >= _SECRETS_MAX_PER_FILE:
            break
        hit = _secret_hit_for_line(line, norm, secret_patterns, pattern_id_fn, repo_patterns, scan_repo_patterns)
        if hit is not None:
            violations.append(_secret_violation(norm, line_no, hit))
    return violations


def _secrets_score(checked: int, violations: list[dict]) -> int:
    if checked == 0 or not violations:
        return 100
    penalty = sum(25 if violation["severity"] == SEVERITY_FAIL else 8 for violation in violations)
    return max(0, 100 - penalty)


def _secrets_result(score: int, violations: list[dict], repo_error: str | None, repo_patterns: list) -> dict:
    result: dict = {"score": score, "violations": violations}
    if repo_error:
        # Pattern 2 — the repo catalogue did NOT run; disclose, never
        # silently pass as if it had.
        result["repo_patterns_error"] = repo_error
    if repo_patterns:
        result["repo_pattern_count"] = len(repo_patterns)
    return result


def _check_secrets(changed_paths: list[str], root: Path) -> dict:
    """Flag leaked credentials and repo-forbidden language in changed files.

    Two pattern layers, both zero-network and line-scoped:

    * Built-in credential shapes (``roam.security.redact.SECRET_PATTERNS`` —
      GitHub PATs, sk- keys, AWS key IDs, Bearer tokens, PEM markers, JWTs)
      — severity FAIL: a credential in a tracked file is never intended.
    * Optional repo-local catalogue ``.roam-leak-patterns.py`` (internal
      codenames, private doc references — whatever the project must never
      publish) — severity WARN.

    Operates on raw changed paths (not the index) so brand-new files are
    covered before they're ever indexed. This is the leak gate riding the
    compile/verify loop: every `roam verify --auto` (and therefore the
    Claude Code Stop hook installed by `roam hooks claude` /
    `compile wire claude`) runs it by default.
    """
    from roam.security.redact import SECRET_PATTERNS, pattern_id

    repo_patterns, repo_should_scan, repo_error = _load_repo_leak_patterns(root)

    violations: list[dict] = []
    checked = 0
    for norm, path in _secret_scan_targets(changed_paths, root):
        text = _read_secret_scan_text(path)
        if text is None:
            continue
        checked += 1
        violations.extend(
            _secret_violations_for_file(
                norm,
                text,
                SECRET_PATTERNS,
                pattern_id,
                repo_patterns,
                _repo_patterns_enabled(norm, repo_patterns, repo_should_scan),
            )
        )

    return _secrets_result(_secrets_score(checked, violations), violations, repo_error, repo_patterns)


def _clean_command_example(command: str) -> str:
    cleaned = command.strip()
    if " #" in cleaned:
        cleaned = cleaned.partition(" #")[0].rstrip()
    if cleaned.endswith("\\"):
        cleaned = cleaned[:-1].rstrip()
    return cleaned


def _append_command_example(examples: list[dict], seen_on_line: set[str], line_no: int, command: str) -> None:
    cleaned = _clean_command_example(command)
    if cleaned and cleaned not in seen_on_line:
        examples.append({"line": line_no, "command": cleaned})
        seen_on_line.add(cleaned)


def _is_bare_inline_command_reference(command: str) -> bool:
    try:
        import shlex

        tokens = shlex.split(command)
    except ValueError:
        return False
    return len(tokens) == 2 and tokens[0] == "roam" and not tokens[1].startswith("-")


def _is_fence_boundary(line: str) -> bool:
    return line.strip().startswith(("```", "~~~"))


def _append_inline_command_examples(examples: list[dict], seen_on_line: set[str], line_no: int, line: str) -> None:
    for match in _INLINE_ROAM_COMMAND_RE.finditer(line):
        command = _clean_command_example(match.group(1))
        if not _is_bare_inline_command_reference(command):
            _append_command_example(examples, seen_on_line, line_no, command)


def _append_shell_command_example(
    examples: list[dict], seen_on_line: set[str], line_no: int, line: str, in_fence: bool
) -> None:
    shell_match = _SHELL_ROAM_COMMAND_RE.match(line)
    if shell_match and (in_fence or line.lstrip().startswith(("$", ">"))):
        _append_command_example(examples, seen_on_line, line_no, shell_match.group(1) or shell_match.group(2))


def _extract_roam_command_examples(text: str) -> list[dict]:
    examples: list[dict] = []
    in_fence = False
    for line_no, line in enumerate(text.splitlines(), start=1):
        if _is_fence_boundary(line):
            in_fence = not in_fence
            continue
        seen_on_line: set[str] = set()
        _append_inline_command_examples(examples, seen_on_line, line_no, line)
        _append_shell_command_example(examples, seen_on_line, line_no, line, in_fence)
    return examples


def _command_example_violation(path: str, line_no: int, check: dict) -> dict | None:
    status = check.get("executable_status")
    if status == "checked":
        return None
    if check.get("target_status") == "placeholder":
        severity = SEVERITY_INFO
        message = f"command example `{check.get('command_text')}` needs placeholder substitution"
        fix = "Replace placeholders before treating this as a copy-paste command"
    elif status == "failed":
        severity = SEVERITY_FAIL
        message = f"command example `{check.get('command_text')}` is not executable: {check.get('reason')}"
        fix = "Use a registered roam subcommand with valid flags"
    else:
        severity = SEVERITY_WARN
        message = f"command example `{check.get('command_text')}` was not checked: {check.get('reason')}"
        fix = "Rewrite as a literal `roam <subcommand>` example or mark it as prose"
    return {
        "category": "command_examples",
        "severity": severity,
        "file": path,
        "line": line_no,
        "message": message,
        "symbol": check.get("subcommand") or "",
        "command_check": check,
        "fix": fix,
    }


def _read_command_example_text(root: Path, rel: str) -> str | None:
    if not _is_command_example_surface(rel):
        return None
    if _is_historical_command_example_surface(rel) or _is_plugin_example_command_surface(rel):
        return None
    path = root / rel
    if not path.exists() or not path.is_file():
        return None
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None


def _command_example_violations_for_file(root: Path, rel: str) -> tuple[int, list[dict]]:
    text = _read_command_example_text(root, rel)
    if text is None:
        return 0, []
    violations: list[dict] = []
    examples = _extract_roam_command_examples(text)
    for example in examples:
        check = validate_command_advice(f"{rel}:{example['line']}", example["command"])
        violation = _command_example_violation(rel, example["line"], check)
        if violation:
            violations.append(violation)
    return len(examples), violations


def _check_command_examples(changed_paths: list[str], root: Path) -> dict:
    violations: list[dict] = []
    examples_checked = 0
    for rel in changed_paths:
        count, file_violations = _command_example_violations_for_file(root, rel)
        examples_checked += count
        violations.extend(file_violations)
    hard_count = sum(1 for v in violations if v.get("severity") != SEVERITY_INFO)
    score = 100 if hard_count == 0 else max(0, 100 - hard_count * 10)
    return {"score": score, "violations": violations, "advisory": True, "examples_checked": examples_checked}


def _read_claim_text(root: Path, rel: str) -> str | None:
    if not _is_claim_surface(rel):
        return None
    if _is_historical_command_example_surface(rel):
        return None
    path = root / rel
    if not path.exists() or not path.is_file():
        return None
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None


def _line_has_claim_evidence(line: str) -> bool:
    return bool(_CLAIM_EVIDENCE_RE.search(line))


def _is_claim_template_placeholder_line(line: str) -> bool:
    if "](" in line:
        return False
    for match in _CLAIM_TEMPLATE_PLACEHOLDER_RE.finditer(line):
        content = match.group(1)
        if any(ch.isalpha() for ch in content) and any(ch.isdigit() for ch in content):
            return True
    return False


def _is_non_assertive_claim_line(line: str) -> bool:
    stripped = line.strip()
    if not stripped:
        return True
    if _CLAIM_OUTLINE_HEADING_RE.match(stripped):
        return True
    if _is_claim_template_placeholder_line(stripped):
        return True
    return 'href="mailto:' in stripped or "href='mailto:" in stripped


def _claim_evidence_window(line: str) -> int:
    return 8 if line.lstrip().startswith("|") else 1


def _claim_has_nearby_evidence(lines: list[str], idx: int) -> bool:
    if _line_has_claim_evidence(lines[idx]):
        return True
    window = _claim_evidence_window(lines[idx])
    for neighbor in range(max(0, idx - window), min(len(lines), idx + window + 1)):
        if neighbor != idx and _line_has_claim_evidence(lines[neighbor]):
            return True
    return False


def _claim_excerpt(line: str, limit: int = 140) -> str:
    excerpt = " ".join(line.strip().split())
    if len(excerpt) <= limit:
        return excerpt
    return excerpt[: limit - 1].rstrip() + "..."


def _claim_violation(path: str, line_no: int, line: str) -> dict:
    excerpt = _claim_excerpt(line)
    return {
        "category": "claims",
        "severity": SEVERITY_WARN,
        "file": path,
        "line": line_no,
        "message": f"high-specificity claim needs evidence/date: `{excerpt}`",
        "symbol": "",
        "fix": "Add a source, measurement date, benchmark note, or narrow the claim",
    }


def _claim_violations_for_text(path: str, text: str) -> tuple[int, list[dict]]:
    claims_checked = 0
    violations: list[dict] = []
    in_fence = False
    lines = text.splitlines()
    for idx, line in enumerate(lines):
        line_no = idx + 1
        if _is_fence_boundary(line):
            in_fence = not in_fence
            continue
        if in_fence or _is_non_assertive_claim_line(line) or not _CLAIM_TRIGGER_RE.search(line):
            continue
        claims_checked += 1
        if not _claim_has_nearby_evidence(lines, idx):
            violations.append(_claim_violation(path, line_no, line))
    return claims_checked, violations


def _claim_violations_for_file(root: Path, rel: str) -> tuple[int, list[dict]]:
    text = _read_claim_text(root, rel)
    if text is None:
        return 0, []
    return _claim_violations_for_text(rel, text)


def _check_claims(changed_paths: list[str], root: Path) -> dict:
    violations: list[dict] = []
    claims_checked = 0
    for rel in changed_paths:
        count, file_violations = _claim_violations_for_file(root, rel)
        claims_checked += count
        violations.extend(file_violations)
    score = 100 if not violations else max(0, 100 - len(violations) * 5)
    return {"score": score, "violations": violations, "advisory": True, "claims_checked": claims_checked}


def _check_cycles(conn, file_ids: list[int], changed_paths: list[str]) -> dict:
    """Flag changed files in a SMALL import cycle (2..8 files) — actionable per
    edit. Large systemic tangles are out of scope (use `roam cycles`)."""
    if not changed_paths:
        return {"score": 100, "violations": []}
    try:
        import networkx as nx
    except ImportError:
        return {
            "score": 100,
            "violations": [],
            "available": False,
            "unavailable_reason": "networkx unavailable -- cycle check did not run",
        }
    edges = conn.execute(
        """SELECT sf.path AS src, tf.path AS tgt
           FROM file_edges fe
           JOIN files sf ON fe.source_file_id = sf.id
           JOIN files tf ON fe.target_file_id = tf.id
           WHERE fe.kind = 'imports'"""
    ).fetchall()
    if not edges:
        return {"score": 100, "violations": []}
    graph = nx.DiGraph()
    graph.add_edges_from((e["src"], e["tgt"]) for e in edges)
    changed_set = {p.replace("\\", "/") for p in changed_paths}
    violations = _collect_cycle_violations(graph, changed_set)
    score = 100 if not violations else max(0, 100 - len(violations) * 20)
    return {"score": score, "violations": violations}


# ---------------------------------------------------------------------------
# Tests check (the EXECUTABLE signal) — run the tests that cover the change.
# Research (arXiv 2310.01798 / AlphaCodium): the failing test that exercises the
# edit is the #1 verify signal — external + executable, NOT model self-review.
# Opt-in only (`--checks tests` / `--all`) because running tests is expensive;
# scoped to the IMPACTED tests + a hard timeout so it never hangs the gate.
# ---------------------------------------------------------------------------

_TESTS_TIMEOUT_S = 120
_MAX_TEST_FILES = 25
_PYTEST_FAIL_RE = re.compile(r"^(?:FAILED|ERROR)\s+(\S+)", re.MULTILINE)


def _tests_unavailable(reason: str) -> dict:
    return {
        "score": 100,
        "violations": [],
        "available": False,
        "unavailable_reason": reason,
    }


def _load_affected_tests_helper():
    try:
        from roam.commands.cmd_affected_tests import _gather_affected_tests
    except Exception:  # noqa: BLE001
        return None
    return _gather_affected_tests


def _rank_affected_test_entries(entries) -> list[tuple[int, int, str]]:
    ranked: list[tuple[int, int, str]] = []
    for entry in entries:
        path = entry.get("file")
        if not (path and path.endswith(".py")):
            continue
        priority = {"DIRECT": 1, "COLOCATED": 2}.get(entry.get("kind"), 3)
        ranked.append((priority, int(entry.get("hops") or 9), path.replace("\\", "/")))
    return ranked


def _rank_changed_test_paths(changed_paths: list[str]) -> list[tuple[int, int, str]]:
    return [(0, 0, path.replace("\\", "/")) for path in changed_paths if is_test_file(path) and path.endswith(".py")]


def _existing_ranked_paths(ranked: list[tuple[int, int, str]], root: Path) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for _priority, _hops, path in sorted(ranked):
        if path not in seen and (root / path).exists():
            seen.add(path)
            ordered.append(path)
    return ordered


def _gather_and_rank_tests(conn, sym_ids, src_paths, changed_paths, root):
    """Rank impacted test files by relevance and return ``(ordered, unavailable)``.

    ``ordered`` is the de-duped, existence-checked list of impacted ``.py`` test
    files, most-relevant first (a changed test > DIRECT caller > colocated).
    ``unavailable`` is None on success, else the ready-to-return result dict
    describing why test discovery could not run.
    """
    gather_affected_tests = _load_affected_tests_helper()
    if gather_affected_tests is None:
        return [], _tests_unavailable("affected-tests helper unavailable")

    try:
        ranked = _rank_affected_test_entries(gather_affected_tests(conn, sym_ids, src_paths))
    except Exception as exc:  # noqa: BLE001 — never let test-discovery break the gate
        return [], _tests_unavailable(f"affected-tests discovery failed: {exc!r}")

    ranked.extend(_rank_changed_test_paths(changed_paths))
    return _existing_ranked_paths(ranked, root), None


def _run_impacted_pytest(ordered: list[str], root: Path, timeout: int) -> dict:
    """Run pytest over the impacted (capped) test files and report failures."""
    import subprocess
    import sys

    capped = len(ordered) > _MAX_TEST_FILES
    targets = ordered[:_MAX_TEST_FILES]
    cmd = [sys.executable, "-B", "-m", "pytest", *targets, "--tb=line", "-q", "-p", "no:cacheprovider"]
    try:
        proc = subprocess.run(cmd, cwd=str(root), capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return {
            "score": 100,
            "timed_out": True,
            "violations": [
                {
                    "category": "tests",
                    "severity": SEVERITY_WARN,
                    "file": targets[0],
                    "line": None,
                    "message": (
                        f"impacted tests did not finish in {timeout}s ({len(targets)} test file(s)) — not verified"
                    ),
                    "fix": "Run the impacted tests manually, or narrow the change",
                }
            ],
            "tests_targeted": len(targets),
        }
    out = (proc.stdout or "") + "\n" + (proc.stderr or "")
    failed = sorted(set(_PYTEST_FAIL_RE.findall(out)))
    violations = [
        {
            "category": "tests",
            "severity": SEVERITY_FAIL,
            "file": nodeid.split("::")[0],
            "line": None,
            "message": f"impacted test FAILED: {nodeid}",
            "fix": "This test covers your change — fix the edit so it passes",
        }
        for nodeid in failed
    ]
    score = 100 if not failed else 0
    return {
        "score": score,
        "violations": violations,
        "tests_targeted": len(targets),
        "tests_failed": len(failed),
        "tests_total_impacted": len(ordered),
        "capped": capped,
    }


def _check_tests(
    conn, file_ids: list[int], changed_paths: list[str], root: Path, timeout: int = _TESTS_TIMEOUT_S
) -> dict:
    """Run the tests that COVER the changed symbols/files and report failures.

    Thin orchestrator: rank the impacted test files (`_gather_and_rank_tests`),
    then run the capped set (`_run_impacted_pytest`). On a large diff the impacted
    set explodes (roam's own tree → 828 files); ranking + a 25-file cap keeps the
    most-relevant tests (changed test > DIRECT caller > colocated) under a timeout.
    """
    sym_rows = batched_in(conn, "SELECT id FROM symbols WHERE file_id IN ({ph})", file_ids) if file_ids else []
    sym_ids = {r["id"] for r in sym_rows}
    src_paths = [p for p in changed_paths if not is_test_file(p)]

    ordered, unavailable = _gather_and_rank_tests(conn, sym_ids, src_paths, changed_paths, root)
    if unavailable is not None:
        return unavailable
    if not ordered:
        return {"score": 100, "violations": [], "no_impacted_tests": True}
    return _run_impacted_pytest(ordered, root, timeout)


# ---------------------------------------------------------------------------
# Breaking-change guardrail — a changed symbol whose SIGNATURE changed while at
# least one caller lives in a file that was NOT co-edited. The caller will break
# and the agent never saw it. BLOCK. Reuses the breaking-changes detector (git
# HEAD vs the indexed working tree) for the signature diff and the canonical
# call/reference edge graph for the callers.
# ---------------------------------------------------------------------------


def _symbol_ids_in_file(conn, path: str, name: str) -> list[int]:
    """DB symbol ids named *name* defined in *path* (for the caller lookup)."""
    rows = conn.execute(
        "SELECT s.id FROM symbols s JOIN files f ON s.file_id = f.id WHERE f.path = ? AND s.name = ?",
        (path, name),
    ).fetchall()
    return [r["id"] for r in rows]


def _breaking_caller_files(conn, symbol_ids: list[int]) -> set[str]:
    """Files that CALL or REFERENCE any of *symbol_ids* (reverse edges, 1-hop).
    Uses the canonical call+reference edge vocabulary so the reach matches
    blast-radius / affected semantics. Best-effort: empty on any failure (a
    missed caller is a false-negative gate, never a false block)."""
    if not symbol_ids:
        return set()
    try:
        from roam.db.edge_kinds import call_or_ref_in_clause

        rows = batched_in(
            conn,
            "SELECT DISTINCT f.path AS path "
            "FROM edges e JOIN symbols s ON e.source_id = s.id "
            "JOIN files f ON s.file_id = f.id "
            f"WHERE e.target_id IN ({{ph}}) AND {call_or_ref_in_clause('e.kind')}",
            symbol_ids,
        )
    except Exception as exc:  # noqa: BLE001 — caller lookup is advisory to the gate
        from roam.observability import log_swallowed

        log_swallowed("verify.breaking.callers", exc)
        return set()
    return {r["path"] for r in rows if r["path"]}


def _is_public_breaking_contract_name(name: str) -> bool:
    """Names that represent externally visible call contracts for this gate."""
    return bool(name) and not name.startswith("_")


def _changed_public_signatures_for_caller_gate(
    conn,
    path: str,
    root: Path,
    *,
    git_show,
    extract_old_symbols,
    get_current_symbols,
    compare_file,
) -> list[dict]:
    """Signature changes that can matter to callers outside this edit."""
    old_source = git_show(root, "HEAD", path)
    if old_source is None:
        return []  # new file at HEAD — nothing to break
    try:
        old_symbols = extract_old_symbols(old_source, path)
    except Exception as exc:  # noqa: BLE001 — one unparseable file must not break the gate
        from roam.observability import log_swallowed

        log_swallowed("verify.breaking.extract", exc)
        return []
    if not old_symbols:
        return []
    new_symbols = get_current_symbols(conn, path)
    _removed, sig_changed, _renamed = compare_file(path, old_symbols, new_symbols)
    return [
        sym
        for sym in sig_changed
        if _is_public_breaking_contract_name(str(sym.get("name") or ""))
    ]


def _external_caller_files_for_contract(conn, path: str, name: str, changed_paths: set[str]) -> list[str]:
    """Unedited caller files that would still consume the old call contract."""
    symbol_ids = _symbol_ids_in_file(conn, path, name)
    return sorted(f for f in _breaking_caller_files(conn, symbol_ids) if f not in changed_paths)


def _breaking_contract_violation(path: str, sym: dict, external: list[str]) -> dict:
    name = str(sym.get("name") or "")
    sample = ", ".join(external[:3]) + (" ..." if len(external) > 3 else "")
    return {
        "category": _VERIFY_BREAKING_CATEGORY,
        "severity": SEVERITY_FAIL,
        "hard_block": True,
        "file": path,
        "line": sym.get("line"),
        "message": (
            f"breaking change: signature of `{name}` changed but "
            f"{len(external)} un-edited caller file(s) still call it "
            f"({sample}) — they will break"
        ),
        "fix": (
            "Update the callers in this same change, keep the old "
            "signature back-compatible, or stage a deprecation"
        ),
    }


def _breaking_contract_violations_for_file(
    conn,
    path: str,
    changed_paths: set[str],
    min_callers: int,
    sig_changed: list[dict],
) -> list[dict]:
    """Caller-gate violations for public signatures with unedited callers."""
    violations: list[dict] = []
    for sym in sig_changed:
        name = str(sym.get("name") or "")
        external = _external_caller_files_for_contract(conn, path, name, changed_paths)
        if len(external) >= min_callers:
            violations.append(_breaking_contract_violation(path, sym, external))
    return violations


def _check_breaking(conn, file_ids: list[int], target_paths: list[str], root: Path) -> dict:
    """Guardrail: signature change + an un-edited external caller => FAIL (BLOCK).

    Scoped to EXPORTED, non-dunder, non-underscore symbols (the breaking-changes
    detector already filters to exported; the name gate is belt-and-suspenders
    against private-helper churn) and an external-caller blast threshold
    (``ROAM_VERIFY_BREAKING_MIN_CALLERS``). Findings are marked ``hard_block`` so
    they survive --diff-only scoping and pin the verdict to FAIL."""
    if not _verify_env_flag("ROAM_VERIFY_BREAKING", True):
        return {"score": 100, "violations": []}
    changed_paths = {p.replace("\\", "/") for p in (target_paths or [])}
    source_paths = [p for p in changed_paths if p.endswith(".py") and not is_test_file(p)]
    if not source_paths or len(source_paths) > _MAX_BREAKING_FILES:
        return {"score": 100, "violations": []}
    try:
        from roam.commands.cmd_breaking import (
            _compare_file,
            _extract_old_symbols,
            _get_current_symbols,
            _git_show,
        )
    except Exception as exc:  # noqa: BLE001 — never break the gate on an import error
        from roam.observability import log_swallowed

        log_swallowed("verify.breaking.import", exc)
        return {"score": 100, "violations": []}

    min_callers = max(1, _verify_env_int("ROAM_VERIFY_BREAKING_MIN_CALLERS", 1))
    violations: list[dict] = []
    for path in source_paths:
        sig_changed = _changed_public_signatures_for_caller_gate(
            conn,
            path,
            root,
            git_show=_git_show,
            extract_old_symbols=_extract_old_symbols,
            get_current_symbols=_get_current_symbols,
            compare_file=_compare_file,
        )
        violations.extend(
            _breaking_contract_violations_for_file(conn, path, changed_paths, min_callers, sig_changed)
        )
    return {"score": 0 if violations else 100, "violations": violations}


# ---------------------------------------------------------------------------
# Taint / auth gate (opt-in, ROAM_VERIFY_TAINT=1) — surface a source->sink taint
# path that TOUCHES a changed file, reusing the shipped taint engine + rule
# packs. Default OFF + WARN-severity (FP-prone): it surfaces, it does not block.
# ---------------------------------------------------------------------------


def _taint_finding_files(finding) -> set[str]:
    syms = [getattr(finding, "source_symbol", None), getattr(finding, "sink_symbol", None)]
    syms.extend(getattr(finding, "path_symbols", None) or [])
    files: set[str] = set()
    for sym in syms:
        if isinstance(sym, dict) and sym.get("file"):
            files.add(str(sym["file"]).replace("\\", "/"))
    return files


def _check_taint(conn, file_ids: list[int], target_paths: list[str], root: Path) -> dict:
    """Opt-in: surface taint source->sink paths whose source/sink/route touches a
    changed file. Best-effort and advisory — never raises, never hard-blocks."""
    if not _verify_env_flag("ROAM_VERIFY_TAINT", False):
        return {"score": 100, "violations": []}
    changed = {p.replace("\\", "/") for p in (target_paths or [])}
    if not changed:
        return {"score": 100, "violations": []}
    try:
        from roam.commands.cmd_taint import _default_rules_dir
        from roam.security.taint_engine import load_rules, run_taint

        rules = load_rules(_default_rules_dir())
        findings = run_taint(conn, rules) if rules else []
    except Exception as exc:  # noqa: BLE001 — opt-in security surface must never break the gate
        from roam.observability import log_swallowed

        log_swallowed("verify.taint", exc)
        return {"score": 100, "violations": []}

    violations: list[dict] = []
    seen: set = set()
    for finding in findings:
        touched = _taint_finding_files(finding)
        if not (touched & changed):
            continue
        src = getattr(finding, "source_symbol", None) or {}
        sink = getattr(finding, "sink_symbol", None) or {}
        rule_id = getattr(finding, "rule_id", "taint")
        key = (rule_id, src.get("file"), src.get("line"), sink.get("file"), sink.get("line"))
        if key in seen:
            continue
        seen.add(key)
        loc_file = (sink.get("file") or src.get("file") or sorted(touched & changed)[0] or "").replace("\\", "/")
        sanitized = " (a sanitizer is on the path)" if getattr(finding, "sanitizer_in_path", False) else ""
        violations.append(
            {
                "category": _VERIFY_TAINT_CATEGORY,
                "severity": SEVERITY_WARN,
                "file": loc_file,
                "line": sink.get("line") or src.get("line"),
                "message": (
                    f"taint [{rule_id}]: source `{src.get('name')}` reaches sink "
                    f"`{sink.get('name')}` through a file you changed{sanitized}"
                ),
                "fix": "Confirm the tainted value is validated/sanitized before the sink",
            }
        )
    return {"score": 100 if not violations else 60, "violations": violations}


# ---------------------------------------------------------------------------
# Additional reusable detector wires. Each reuses the shipped roam command's
# engine, is scoped to the changed files (the diff), is independently
# env-flagged (ROAM_VERIFY_<NAME>) and FULLY fail-open: any import or runtime
# error yields NO finding and never raises the gate.
# ---------------------------------------------------------------------------


def _swallow_verify(tag, exc):
    try:
        from roam.observability import log_swallowed

        log_swallowed(tag, exc)
    except (ImportError, AttributeError, OSError):  # noqa: BLE001 — logging must never break the gate
        pass


def _verify_changed_set(target_paths):
    return {p.replace("\\", "/") for p in (target_paths or [])}


def _verify_loc_path(loc_str):
    s = (loc_str or "").replace("\\", "/")
    m = re.match(r"^(.*?):\d+(?::\d+)?$", s)
    return m.group(1) if m else s


def _verify_loc_line(loc_str):
    m = re.search(r":(\d+)(?::\d+)?$", (loc_str or "").replace("\\", "/"))
    return int(m.group(1)) if m else None


def _verify_rowval(row, key, default=None):
    try:
        return row[key]
    except (IndexError, KeyError, TypeError):
        return default


def _compile_deleted_reference_lookup(candidate_names):
    ordered = sorted(candidate_names, key=lambda nm: (-len(nm), nm))
    return re.compile(r"\b(?:" + "|".join(re.escape(nm) for nm in ordered) + r")\b")


def _deleted_reference_hits_without_list_scan(content, reference_rx, candidate_lookup):
    hits = set()
    for match in reference_rx.finditer(content or ""):
        name = match.group(0)
        if name in candidate_lookup:
            hits.add(name)
    return sorted(hits)


def _build_deleted_symbol_candidates(conn, deleted_lines):
    """Return deleted symbol names that are not still defined elsewhere.

    Filters parsed deletion entries to real identifiers, deduplicates, and
    removes symbols that still exist in the index (moves/renames, not dangling
    deletes). Swallows DB failures and treats every name as a candidate.
    """
    names = []
    seen = set()
    for entry in deleted_lines:
        try:
            _p, _l, sym, kind = entry
        except Exception:  # noqa: BLE001
            continue
        if kind != "symbol":
            continue
        nm = (sym or "").strip()
        if len(nm) < 5 or nm.startswith("_") or not nm.isidentifier() or nm in seen:
            continue
        seen.add(nm)
        names.append(nm)
    if not names:
        return []
    still_defined = set()
    try:
        rows = batched_in(conn, "SELECT DISTINCT name FROM symbols WHERE name IN ({ph})", names)
        still_defined = {_verify_rowval(r, "name") for r in rows}
    except Exception as exc:  # noqa: BLE001
        _swallow_verify("verify.delete_check.defined", exc)
    return [n for n in names if n not in still_defined]


def _collect_deleted_reference_hits(matches, changed, reference_rx, candidate_lookup):
    """Group surviving references to deleted symbols by symbol name.

    Filters out matches inside the changed set, test files, or non-source
    surfaces, then uses a compiled regex + set lookup to avoid a per-candidate
    list scan.
    """
    by_name = {}
    for m in matches or []:
        mp = (m.get("path") or "").replace("\\", "/")
        if not mp or mp in changed or is_test_file(mp) or not _is_import_resolution_source_path(mp):
            continue
        content = m.get("content") or ""
        for nm in _deleted_reference_hits_without_list_scan(content, reference_rx, candidate_lookup):
            by_name.setdefault(nm, []).append((mp, m.get("line")))
    return by_name


def _build_delete_check_violations(by_name):
    """Translate grouped survivor references into verify violations."""
    violations = []
    for nm, locs in by_name.items():
        files = sorted({f for f, _ in locs})
        sample = ", ".join(files[:3]) + (" ..." if len(files) > 3 else "")
        violations.append(
            {
                "category": _VERIFY_DELETE_CATEGORY,
                "severity": SEVERITY_FAIL,
                "hard_block": True,
                "file": files[0],
                "line": next((ln for _, ln in locs), None),
                "message": (
                    f"deleted symbol `{nm}` is still referenced by {len(files)} "
                    f"un-edited file(s) ({sample}) — they will break"
                ),
                "fix": "Restore the symbol, update the surviving references in this change, or stage a deprecation",
            }
        )
    return violations


def _check_delete_safety(conn, target_paths, root):
    """Guardrail (opt-in, ROAM_VERIFY_DELETE_CHECK=1): a symbol the diff DELETES
    that is still referenced from an un-edited code file => FAIL (BLOCK). Reuses
    delete-check's git-diff parser + the grep survivor engine. Default OFF: the
    grep survivor scan is FP-prone for a live gate. Skips names still DEFINED
    elsewhere (a move/rename, not a dangling delete)."""
    if not _verify_env_flag("ROAM_VERIFY_DELETE_CHECK", False):
        return {"score": 100, "violations": []}
    changed = _verify_changed_set(target_paths)
    if not changed:
        return {"score": 100, "violations": []}
    try:
        from roam.commands.cmd_delete_check import _git_diff, _parse_deletions
        from roam.commands.grep_helpers import indexed_file_scan
    except Exception as exc:  # noqa: BLE001
        _swallow_verify("verify.delete_check.import", exc)
        return {"score": 100, "violations": []}
    try:
        diff_text, err = _git_diff(root, "working", "main", None)
    except Exception as exc:  # noqa: BLE001
        _swallow_verify("verify.delete_check.diff", exc)
        return {"score": 100, "violations": []}
    if err or not diff_text:
        return {"score": 100, "violations": []}
    try:
        _deleted_files, deleted_lines = _parse_deletions(diff_text)
    except Exception as exc:  # noqa: BLE001
        _swallow_verify("verify.delete_check.parse", exc)
        return {"score": 100, "violations": []}
    candidates = _build_deleted_symbol_candidates(conn, deleted_lines)
    if not candidates:
        return {"score": 100, "violations": []}
    candidate_lookup = set(candidates)
    try:
        # indexed_file_scan (delete-check's own fallback engine) reads indexed
        # files from disk + regex — tty-independent, unlike run_search/rg which
        # reads STDIN under a non-interactive Stop hook and silently finds none.
        reference_rx = _compile_deleted_reference_lookup(candidate_lookup)
        matches = indexed_file_scan([reference_rx], conn, root)
    except Exception as exc:  # noqa: BLE001
        _swallow_verify("verify.delete_check.search", exc)
        return {"score": 100, "violations": []}
    by_name = _collect_deleted_reference_hits(matches, changed, reference_rx, candidate_lookup)
    violations = _build_delete_check_violations(by_name)
    return {"score": 0 if violations else 100, "violations": violations}


def _run_verify_detector_wire(
    conn,
    env_name: str,
    default: bool,
    target_paths,
    analyzer,
    build_violations,
    log_tag: str,
    score_with_violations: int = 80,
    score_with_hard_block: int | None = None,
    scope_filter=None,
):
    """Fail-open scaffolding reused by verify's diff-scoped detector wires.

    Centralizes the conservation between uniform safety behavior (env guard,
    swallowed errors, empty-scope short-circuit) and per-detector specialization
    (the analysis call and violation translation). Callers only supply their
    domain-specific parts.
    """
    if not _verify_env_flag(env_name, default):
        return {"score": 100, "violations": []}
    scope = _verify_changed_set(target_paths)
    if scope_filter is not None:
        scope = scope_filter(scope)
    if not scope:
        return {"score": 100, "violations": []}
    try:
        findings = analyzer(conn)
    except Exception as exc:  # noqa: BLE001
        _swallow_verify(log_tag, exc)
        return {"score": 100, "violations": []}
    violations = build_violations(findings, scope)
    if score_with_hard_block is not None and any(v.get("hard_block") for v in violations):
        return {"score": score_with_hard_block, "violations": violations}
    return {"score": 100 if not violations else score_with_violations, "violations": violations}


def _check_migration_safety(conn, target_paths, root):
    """Guardrail (ROAM_VERIFY_MIGRATION_SAFETY=1, default ON): a changed .php
    migration with a non-idempotent / destructive op. High-confidence => BLOCK
    (catastrophic on rerun); the rest WARN. Reuses analyze_migration_safety. A
    no-op on every non-migration edit (it only runs when a changed file is a
    .php migration)."""

    def _migration_scope(paths):
        return {p for p in paths if "migration" in p.lower() and p.lower().endswith(".php")}

    def _analyze(conn):
        from roam.commands.cmd_migration_safety import analyze_migration_safety

        return analyze_migration_safety(conn, include_archive=False)

    def _build(findings, scope):
        violations = []
        for f in findings or []:
            fp = (f.get("file") or "").replace("\\", "/")
            if fp not in scope:
                continue
            block = (f.get("confidence") or "").lower() == "high"
            v = {
                "category": _VERIFY_MIGRATION_CATEGORY,
                "severity": SEVERITY_FAIL if block else SEVERITY_WARN,
                "file": fp,
                "line": f.get("line"),
                "message": f"migration safety: {f.get('issue') or 'non-idempotent / destructive operation'}",
                "fix": f.get("fix") or "Guard create/drop (hasTable/hasColumn), make it reversible, provide a down()",
            }
            if block:
                v["hard_block"] = True
            violations.append(v)
        return violations

    return _run_verify_detector_wire(
        conn,
        "ROAM_VERIFY_MIGRATION_SAFETY",
        True,
        target_paths,
        analyzer=_analyze,
        build_violations=_build,
        log_tag="verify.migration_safety",
        score_with_violations=60,
        score_with_hard_block=0,
        scope_filter=_migration_scope,
    )


def _check_smells(conn, target_paths):
    """Advisory WARN (ROAM_VERIFY_SMELLS=1): god-class / brain-method /
    deep-nesting and the other 24 structural smell detectors, scoped to changed
    files. Reuses roam.catalog.smells.run_all_detectors."""

    def _analyze(conn):
        from roam.catalog.smells import run_all_detectors

        return run_all_detectors(conn)

    def _build(findings, scope):
        violations = []
        for f in findings or []:
            loc_str = f.get("location") or f.get("file") or ""
            fp = _verify_loc_path(loc_str)
            if fp not in scope:
                continue
            kind = f.get("kind") or f.get("smell_id") or f.get("smell") or "smell"
            msg = f.get("message") or f.get("detail") or f.get("description") or str(kind)
            violations.append(
                {
                    "category": _VERIFY_SMELLS_CATEGORY,
                    "severity": SEVERITY_WARN,
                    "file": fp,
                    "line": f.get("line") or _verify_loc_line(loc_str),
                    "message": f"smell [{kind}]: {msg}",
                    "fix": f.get("suggestion") or "Refactor (extract / split) to reduce the structural smell",
                }
            )
        return violations

    return _run_verify_detector_wire(
        conn,
        "ROAM_VERIFY_SMELLS",
        False,
        target_paths,
        analyzer=_analyze,
        build_violations=_build,
        log_tag="verify.smells",
        score_with_violations=80,
    )


def _check_clones(conn, target_paths):
    """Advisory WARN (ROAM_VERIFY_CLONES=1): AST structural near-duplicate of a
    changed file (reimplemented-existing-code beyond exact dupes). Reuses
    roam.graph.clone_detect.detect_clones. Heavier (re-parses source)."""
    if not _verify_env_flag("ROAM_VERIFY_CLONES", False):
        return {"score": 100, "violations": []}
    changed = _verify_changed_set(target_paths)
    if not changed:
        return {"score": 100, "violations": []}
    try:
        from roam.graph.clone_detect import detect_clones

        pairs, _clusters = detect_clones(conn)
    except Exception as exc:  # noqa: BLE001
        _swallow_verify("verify.clones", exc)
        return {"score": 100, "violations": []}
    violations = []
    seen = set()
    for p in pairs or []:
        fa = (getattr(p, "file_a", "") or "").replace("\\", "/")
        fb = (getattr(p, "file_b", "") or "").replace("\\", "/")
        if fa in changed:
            here, other, line = fa, fb, getattr(p, "line_a", None)
        elif fb in changed:
            here, other, line = fb, fa, getattr(p, "line_b", None)
        else:
            continue
        sim = float(getattr(p, "similarity", 0.0) or 0.0)
        key = (min(fa, fb), max(fa, fb), round(sim, 2))
        if key in seen:
            continue
        seen.add(key)
        violations.append(
            {
                "category": _VERIFY_CLONES_CATEGORY,
                "severity": SEVERITY_WARN,
                "file": here,
                "line": line,
                "message": (
                    f"near-duplicate code ({sim:.0%} similar) between `{here}` and "
                    f"`{other}` — likely reimplements existing code"
                ),
                "fix": "Extract the shared logic into one place instead of duplicating it",
            }
        )
    return {"score": 100 if not violations else 80, "violations": violations}


def _changed_python_paths_for_magic_number_scan(target_paths) -> set[str]:
    """Changed production Python files where repeated literals can matter."""
    return {p for p in _verify_changed_set(target_paths) if p.endswith(".py") and not is_test_file(p)}


def _magic_number_occurrences_by_literal(changed_paths, root, scan_python_file):
    """Group scanned literal occurrences by value while preserving first location."""
    occ = defaultdict(list)
    for rel in changed_paths:
        try:
            raw = scan_python_file(root / rel, include_trivial=False)
        except Exception as exc:  # noqa: BLE001
            _swallow_verify("verify.magic_numbers.scan", exc)
            continue
        for item in raw or []:
            try:
                val, line, _snip = item
            except Exception:  # noqa: BLE001
                continue
            occ[val].append((rel, line))
    return occ


def _magic_number_repetition_violations(occurrences, threshold):
    """Build one violation per repeated literal that crosses the configured threshold."""
    violations = []
    for val, hits in occurrences.items():
        if len(hits) < threshold:
            continue
        files = sorted({path for path, _line in hits})
        f0, l0 = hits[0]
        violations.append(
            {
                "category": _VERIFY_MAGIC_CATEGORY,
                "severity": SEVERITY_WARN,
                "file": f0,
                "line": l0,
                "message": (
                    f"magic number {val!r} repeated {len(hits)}x across changed code "
                    f"({len(files)} file(s)) — name it a constant"
                ),
                "fix": "Extract the literal to a named module-level constant",
            }
        )
    return violations


def _check_magic_numbers(target_paths, root):
    """Advisory WARN (ROAM_VERIFY_MAGIC_NUMBERS=1): a numeric literal repeated
    >= ROAM_VERIFY_MAGIC_MIN (default 3) times across the changed Python.
    Reuses cmd_magic_numbers._scan_python_file."""
    if not _verify_env_flag("ROAM_VERIFY_MAGIC_NUMBERS", False):
        return {"score": 100, "violations": []}
    changed_paths = _changed_python_paths_for_magic_number_scan(target_paths)
    if not changed_paths:
        return {"score": 100, "violations": []}
    try:
        from roam.commands.cmd_magic_numbers import _scan_python_file
    except Exception as exc:  # noqa: BLE001
        _swallow_verify("verify.magic_numbers.import", exc)
        return {"score": 100, "violations": []}
    threshold = max(2, _verify_env_int("ROAM_VERIFY_MAGIC_MIN", 3))
    occurrences = _magic_number_occurrences_by_literal(changed_paths, root, _scan_python_file)
    violations = _magic_number_repetition_violations(occurrences, threshold)
    return {"score": 100 if not violations else 85, "violations": violations}


def _check_dead(conn, target_paths):
    """Advisory WARN (ROAM_VERIFY_DEAD=1): an exported symbol in a changed file
    that now has no production consumers. Reuses cmd_dead._analyze_dead."""
    if not _verify_env_flag("ROAM_VERIFY_DEAD", False):
        return {"score": 100, "violations": []}
    py_changed = {p for p in _verify_changed_set(target_paths) if p.endswith(".py")}
    if not py_changed:
        return {"score": 100, "violations": []}
    try:
        from roam.commands.cmd_dead import _analyze_dead

        result = _analyze_dead(conn)
    except Exception as exc:  # noqa: BLE001
        _swallow_verify("verify.dead", exc)
        return {"score": 100, "violations": []}
    high = result[0] if result else []
    try:
        prows = batched_in(conn, "SELECT id, path FROM files WHERE path IN ({ph})", sorted(py_changed))
        id_to_path = {_verify_rowval(r, "id"): _verify_rowval(r, "path") for r in prows}
    except Exception as exc:  # noqa: BLE001
        _swallow_verify("verify.dead.files", exc)
        return {"score": 100, "violations": []}
    changed_ids = {i for i in id_to_path if i is not None}
    violations = []
    for r in high or []:
        fid = _verify_rowval(r, "file_id")
        if fid not in changed_ids:
            continue
        name = _verify_rowval(r, "name", "?")
        line = _verify_rowval(r, "line_start") or _verify_rowval(r, "line")
        violations.append(
            {
                "category": _VERIFY_DEAD_CATEGORY,
                "severity": SEVERITY_WARN,
                "file": id_to_path.get(fid),
                "line": line,
                "message": f"exported symbol `{name}` has no production consumers (dead) after this change",
                "fix": "Remove the now-orphaned symbol, or wire the intended consumer",
            }
        )
    return {"score": 100 if not violations else 85, "violations": violations}


def _check_n1(conn, target_paths):
    """Advisory WARN (ROAM_VERIFY_N1=1): an N+1 lazy-load whose model/accessor
    touches a changed file. Reuses cmd_n1.analyze_n1; low-confidence skipped."""

    def _analyze(conn):
        from roam.commands.cmd_n1 import analyze_n1

        out = analyze_n1(conn)
        return out[0] if isinstance(out, tuple) else out

    def _build(findings, scope):
        violations = []
        for f in findings or []:
            if (f.get("confidence") or "").lower() == "low":
                continue
            ml = _verify_loc_path(f.get("model_location") or "")
            al = _verify_loc_path(f.get("accessor_location") or "")
            if ml not in scope and al not in scope:
                continue
            loc_file = al if al in scope else ml
            violations.append(
                {
                    "category": _VERIFY_N1_CATEGORY,
                    "severity": SEVERITY_WARN,
                    "file": loc_file,
                    "line": _verify_loc_line(f.get("accessor_location") or f.get("model_location") or ""),
                    "message": (
                        f"possible N+1: `{f.get('accessor_name')}` lazily loads "
                        f"`{f.get('relationship')}` ({f.get('confidence')} confidence)"
                    ),
                    "fix": f.get("suggestion") or "Eager-load the relationship to avoid a per-item query",
                }
            )
        return violations

    return _run_verify_detector_wire(
        conn,
        "ROAM_VERIFY_N1",
        False,
        target_paths,
        analyzer=_analyze,
        build_violations=_build,
        log_tag="verify.n1",
        score_with_violations=85,
    )


def _check_over_fetch(conn, target_paths):
    """Advisory WARN (ROAM_VERIFY_OVER_FETCH=1): a serializer / endpoint in a
    changed file returning more fields than used. Reuses analyze_over_fetch."""
    if not _verify_env_flag("ROAM_VERIFY_OVER_FETCH", False):
        return {"score": 100, "violations": []}
    changed = _verify_changed_set(target_paths)
    if not changed:
        return {"score": 100, "violations": []}
    try:
        from roam.commands.cmd_over_fetch import analyze_over_fetch

        findings = analyze_over_fetch(conn, 3, 100)
    except Exception as exc:  # noqa: BLE001
        _swallow_verify("verify.over_fetch", exc)
        return {"score": 100, "violations": []}
    violations = []
    for f in findings or []:
        fp = (f.get("file") or "").replace("\\", "/")
        if fp not in changed:
            continue
        violations.append(
            {
                "category": _VERIFY_OVER_FETCH_CATEGORY,
                "severity": SEVERITY_WARN,
                "file": fp,
                "line": f.get("line"),
                "message": f"over-fetch: {f.get('endpoint') or 'endpoint'} returns more fields than the response uses",
                "fix": "Select only the fields the response actually needs",
            }
        )
    return {"score": 100 if not violations else 88, "violations": violations}


def _load_llm_smell_detectors_without_hiding_import_bugs():
    try:
        module = importlib.import_module("roam.commands.cmd_llm_smells")
    except ModuleNotFoundError as exc:
        if exc.name == "roam.commands.cmd_llm_smells":
            _swallow_verify("verify.llm_smells.import", exc)
            return ()
        raise
    return module._DETECTORS


def _check_llm_smells(target_paths, root):
    """Advisory WARN (ROAM_VERIFY_LLM_SMELLS=1): LLM-API anti-patterns (no model
    pin, no max_tokens / timeout, prompt-injection concat, ...) in a changed
    file that calls an LLM SDK. Reuses cmd_llm_smells._DETECTORS per file."""
    if not _verify_env_flag("ROAM_VERIFY_LLM_SMELLS", False):
        return {"score": 100, "violations": []}
    changed = [
        p for p in _verify_changed_set(target_paths) if _is_import_resolution_source_path(p) and not is_test_file(p)
    ]
    if not changed:
        return {"score": 100, "violations": []}
    detectors = _load_llm_smell_detectors_without_hiding_import_bugs()
    if not detectors:
        return {"score": 100, "violations": []}
    hints = (
        "openai",
        "anthropic",
        "litellm",
        "completion",
        "chat.completion",
        "messages.create",
        "generativemodel",
        "gpt-",
        "claude-",
    )
    violations = []
    for rel in changed:
        try:
            text = (root / rel).read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            _swallow_verify("verify.llm_smells.read", exc)
            continue
        low = text.lower()
        if not any(h in low for h in hints):
            continue
        for kind, fn in detectors:
            try:
                hits = fn(rel, text) or []
            except (OSError, ImportError, ModuleNotFoundError) as exc:
                _swallow_verify("verify.llm_smells.detector", exc)
                continue
            for h in hits:
                violations.append(
                    {
                        "category": _VERIFY_LLM_SMELLS_CATEGORY,
                        "severity": SEVERITY_WARN,
                        "file": (h.get("file") or rel).replace("\\", "/"),
                        "line": h.get("line"),
                        "message": f"llm-smell [{kind}]: {h.get('message') or h.get('detail') or h.get('issue') or kind}",
                        "fix": h.get("fix")
                        or "Harden the LLM call (pin model, set max_tokens / timeout, validate output)",
                    }
                )
    return {"score": 100 if not violations else 85, "violations": violations}


def _check_test_hermeticity(target_paths, root):
    """Advisory WARN (ROAM_VERIFY_TEST_HERMETICITY=1): non-hermetic patterns
    (wall-clock, network, randomness, env access) in a changed test file.
    Reuses cmd_test_hermeticity._scan_test_file."""
    if not _verify_env_flag("ROAM_VERIFY_TEST_HERMETICITY", False):
        return {"score": 100, "violations": []}
    changed = [p for p in _verify_changed_set(target_paths) if is_test_file(p) and p.endswith(".py")]
    if not changed:
        return {"score": 100, "violations": []}
    try:
        from roam.commands.cmd_test_hermeticity import _scan_test_file
    except Exception as exc:  # noqa: BLE001
        _swallow_verify("verify.test_hermeticity.import", exc)
        return {"score": 100, "violations": []}
    violations = []
    for rel in changed:
        try:
            hits = _scan_test_file(rel, root) or []
        except Exception as exc:  # noqa: BLE001
            _swallow_verify("verify.test_hermeticity.scan", exc)
            continue
        for h in hits:
            violations.append(
                {
                    "category": _VERIFY_HERMETICITY_CATEGORY,
                    "severity": SEVERITY_WARN,
                    "file": (h.get("file") or rel).replace("\\", "/"),
                    "line": h.get("line"),
                    "message": f"non-hermetic test: {h.get('kind')} ({h.get('evidence')})",
                    "fix": "Mock / freeze the non-hermetic dependency (time, network, randomness, env)",
                }
            )
    return {"score": 100 if not violations else 85, "violations": violations}


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------


def _compute_verdict(score: int) -> str:
    """Compute verdict string from composite score."""
    if score >= 80:
        return "PASS"
    if score >= 60:
        return "WARN"
    return "FAIL"


# Idiom detectors that DUPLICATE verify's own error_handling category — excluded
# from the deep `patterns` surface so the agent doesn't see the same broad/bare/
# silent-except finding twice.
_DEEP_IDIOM_DENY: frozenset[str] = frozenset(
    {
        "py-broad-except",
        "py-bare-except",
    }
)

# The detector-IMPLEMENTATION files contain every anti-pattern as a regex literal
# or docstring example by nature, so the idiom detectors self-match when the
# changed file IS one of them (only happens when editing roam's own catalog).
# Drop those self-scan findings — they're noise, not real anti-patterns.
_DEEP_SELF_SCAN_SUFFIXES: tuple[str, ...] = (
    "catalog/python_idioms.py",
    "catalog/detectors.py",
)


def _safe_run_idiom(fn, task_id: str, conn) -> list:
    """Run one idiom detector, swallowing any raise (one detector must not sink
    the rest of the advisory deep sweep)."""
    try:
        return fn(conn) or []
    except Exception as exc:  # noqa: BLE001 — best-effort/advisory
        from roam.observability import log_swallowed

        log_swallowed(f"verify.deep.{task_id}", exc)
        return []


def _collect_scoped_idiom_findings(conn, file_ids: list[int]) -> list:
    """Run the idiom anti-pattern detectors SCOPED to ``file_ids`` and return
    their raw findings. CONTENT-DRIVEN: only the detectors whose trigger token
    appears in the changed files run (a pandas/django/flask detector can't fire
    on a change that imports none of them — so we don't run it). The deny-set
    drops detectors that duplicate verify's own error_handling."""
    try:
        from roam.catalog.python_idioms import (
            _file_text,
            applicable_idiom_detectors,
            set_idiom_scope,
        )
    except Exception as exc:  # noqa: BLE001 — deep mode is optional/advisory
        from roam.observability import log_swallowed

        log_swallowed("verify.deep.import", exc)
        return []
    # One cheap pass over the scoped files' text drives detector selection.
    scanned = "\n".join(_file_text(conn, fid) or "" for fid in file_ids)
    raw = _run_idiom_pack(conn, file_ids, scanned, applicable_idiom_detectors, set_idiom_scope)
    # JS/TS edits fire the JS idiom pack the same content-driven way
    # (2026-06-11 — the pack landed after the Python wiring above; this keeps
    # the deep sweep language-honest instead of silently Python-only).
    try:
        from roam.catalog.js_idioms import applicable_js_idiom_detectors, set_js_idiom_scope
    except Exception as exc:  # noqa: BLE001 — deep mode is optional/advisory
        from roam.observability import log_swallowed

        log_swallowed("verify.deep.js_import", exc)
        return raw
    raw.extend(_run_idiom_pack(conn, file_ids, scanned, applicable_js_idiom_detectors, set_js_idiom_scope))
    return raw


def _run_idiom_pack(conn, file_ids: list[int], scanned: str, applicable_fn, scope_fn) -> list:
    """Run one idiom pack (Python or JS) scoped + content-driven."""
    raw: list = []
    scope_fn(file_ids)
    try:
        for task_id, _way, fn in applicable_fn(scanned):
            if task_id not in _DEEP_IDIOM_DENY:
                raw.extend(_safe_run_idiom(fn, task_id, conn))
    finally:
        scope_fn(None)
    return raw


def _idiom_finding_to_violation(f: dict) -> dict:
    """Map one idiom-detector finding (``location``/``confidence``/``reason``...)
    to verify's violation shape under the ``patterns`` category."""
    loc = f.get("location") or ""
    path, _sep, line_s = loc.rpartition(":")
    try:
        line: int | None = int(line_s)
    except ValueError:
        path, line = (loc or f.get("file")), None
    high = (f.get("confidence") or "").lower() == "high"
    return {
        "category": "patterns",
        "severity": SEVERITY_WARN if high else SEVERITY_INFO,
        "file": path or f.get("file"),
        "line": line,
        "message": f"{f.get('task_id')}: {f.get('reason', '')}",
        "fix": f.get("fix"),
        "symbol": f.get("symbol_name"),
    }


def _run_deep_patterns(conn, file_ids: list[int]) -> dict:
    """Build the `--deep` advisory ``patterns`` category: the algorithm/idiom
    anti-pattern detectors scoped to ``file_ids`` (sub-second vs the ~17s whole-
    project sweep), mapped to verify violations. Advisory — never gates."""
    if not file_ids:
        return {"score": 100, "violations": [], "advisory": True}

    def _keep(f: dict) -> bool:
        loc = (f.get("location") or "").rsplit(":", 1)[0]
        # Skip detector-source self-scans and TEST files (tests legitimately
        # hold anti-pattern examples as fixtures — flagging them is noise).
        if any(s in loc for s in _DEEP_SELF_SCAN_SUFFIXES):
            return False
        return not is_test_file(loc)

    raw = [f for f in _collect_scoped_idiom_findings(conn, file_ids) if _keep(f)]
    violations = [_idiom_finding_to_violation(f) for f in raw]
    score = 100 if not violations else max(0, 100 - len(violations) * 5)
    return {"score": score, "violations": violations, "advisory": True}


def _compute_composite(categories: dict[str, dict], selected: list[str] | tuple[str, ...] | None = None) -> int:
    """Weighted composite over the SELECTED checks, renormalized so a subset
    still scores on 0-100. Default (all checks) is unchanged: weights sum to 1."""
    sel = set(selected) if selected else set(_CATEGORY_WEIGHTS)
    total = 0.0
    weight_sum = 0.0
    for cat_name, weight in _CATEGORY_WEIGHTS.items():
        if cat_name not in sel:
            continue
        weight_sum += weight
        total += weight * categories.get(cat_name, {}).get("score", 100)
    if weight_sum == 0:
        return 100
    return round(total / weight_sum)


def _handle_verify_toggle(set_on: bool, set_off: bool, root: Path, json_mode: bool) -> bool:
    if not (set_on or set_off):
        return False
    enabled = bool(set_on) and not set_off
    cfg_path = write_verify_enabled(root, enabled)
    state = "ON (verify will run)" if enabled else "OFF (verify disabled)"
    if json_mode:
        click.echo(
            to_json(
                json_envelope("verify", summary={"verdict": "CONFIG", "enabled": enabled, "config_path": str(cfg_path)})
            )
        )
    else:
        click.echo(f"VERDICT: verify {state} -- wrote {cfg_path}")
    return True


def _verify_enabled_from_env(cfg: dict) -> bool:
    raw = (os.environ.get("ROAM_COMPILE_VERIFY") or "").strip().lower()
    if raw in ("1", "true", "on", "yes"):
        return True
    if raw in ("0", "false", "off", "no"):
        return False
    return bool(cfg.get("enabled", True))


def _emit_verify_disabled(json_mode: bool) -> None:
    msg = "verify disabled in .roam/verify.yaml (enabled: false) -- run `roam verify --on` to resume"
    if json_mode:
        click.echo(to_json(json_envelope("verify", summary={"verdict": "SKIPPED", "enabled": False, "reason": msg})))
    else:
        click.echo(f"VERDICT: SKIPPED -- {msg}")


def _resolve_verify_threshold(threshold: int | None, cfg: dict) -> int:
    if threshold is not None:
        return threshold
    return cfg.get("threshold") if cfg.get("threshold") is not None else 70


def _resolve_verify_targets(files, root: Path) -> list[str]:
    if files:
        return _expand_dir_targets([path.replace("\\", "/") for path in files], root)
    return get_changed_files(root)


def _auto_deep_enabled(auto: bool, deep: bool) -> bool:
    if not auto or deep:
        return deep
    return os.environ.get("ROAM_VERIFY_NO_DEEP") not in ("1", "true", "yes")


def _apply_report_mode(
    report: bool,
    files,
    checks_opt: str | None,
    selected: list[str],
    target_paths: list[str],
    root: Path,
) -> tuple[list[str], list[str], bool]:
    if not report:
        return selected, target_paths, False
    # REPORT is a whole-repo, non-gating punch-list. Skip the diff/gate-only
    # checks: the executable `tests` run and the breaking/taint guardrails are
    # change-relative, not repo-wide lint, so they have no meaning here.
    _report_excluded = (
        "tests",
        _VERIFY_BREAKING_CATEGORY,
        _VERIFY_TAINT_CATEGORY,
        _VERIFY_DELETE_CATEGORY,
        _VERIFY_MIGRATION_CATEGORY,
    )
    report_selected = selected if checks_opt else [check for check in _ALL_CHECKS if check not in _report_excluded]
    report_targets = target_paths if files else _all_report_paths(root, report_selected)
    return report_selected, report_targets, True


def _empty_verify_envelope(threshold: int) -> dict:
    return json_envelope(
        "verify",
        summary={
            "verdict": "PASS",
            "score": 100,
            "threshold": threshold,
            "files_checked": 0,
            "violation_count": 0,
            # W805-EEEEE (Pattern-1-Variant-D): disclose the resolution path so
            # an agent can tell this PASS apart from a real verification run.
            # The empty-paths branch fires when the changed-files helper returns
            # an empty list — i.e. no changed files to verify. Naming the state
            # keeps the clean-tree PASS distinguishable from a populated run.
            "state": "no_changes",
        },
        categories={cat: {"score": 100, "violations": []} for cat in _CATEGORY_WEIGHTS},
        violations=[],
    )


def _emit_empty_verify(json_mode: bool, threshold: int) -> None:
    envelope = _empty_verify_envelope(threshold)
    auto_log(envelope, action="verify", target="")
    if json_mode:
        click.echo(to_json(envelope))
        return
    click.echo("VERDICT: PASS (score 100/100) -- no changed files")


def _refresh_stale_verify_targets(root: Path, target_paths: list[str]) -> None:
    try:
        # The index-state comparator is distinct from the git-diff helper
        # imported at module top for target discovery.
        from roam.index.incremental import get_index_changed_files

        with open_db(readonly=True) as idx_conn:
            on_disk = [path for path in target_paths if (root / path).exists()]
            added, modified, _ = get_index_changed_files(idx_conn, on_disk, root)
        if added or modified:
            from roam.index.indexer import Indexer

            Indexer().run(quiet=True, progress_bar=False, light=True)
    except Exception as exc:  # noqa: BLE001 — best-effort; never break verify
        from roam.observability import log_swallowed

        log_swallowed("verify.index_stale_targets", exc)


def _maybe_run_verify_check(selected: list[str], name: str, fn):
    if name in selected:
        return fn()
    return {"score": 100, "violations": [], "skipped": True}


def _run_verify_categories(conn, selected: list[str], file_ids: list[int], target_paths: list[str], root: Path) -> dict:
    return {
        "naming": _maybe_run_verify_check(selected, "naming", lambda: _check_naming(conn, file_ids)),
        "imports": _maybe_run_verify_check(selected, "imports", lambda: _check_imports(conn, file_ids)),
        "error_handling": _maybe_run_verify_check(
            selected, "error_handling", lambda: _check_error_handling(conn, file_ids, root)
        ),
        "duplicates": _maybe_run_verify_check(selected, "duplicates", lambda: _check_duplicates(conn, file_ids)),
        "syntax": _maybe_run_verify_check(selected, "syntax", lambda: _check_syntax(conn, file_ids, root)),
        "complexity": _maybe_run_verify_check(selected, "complexity", lambda: _check_complexity(conn, file_ids)),
        "cycles": _maybe_run_verify_check(selected, "cycles", lambda: _check_cycles(conn, file_ids, target_paths)),
        "tests": _maybe_run_verify_check(selected, "tests", lambda: _check_tests(conn, file_ids, target_paths, root)),
        "import_side_effects": _maybe_run_verify_check(
            selected, "import_side_effects", lambda: _check_import_side_effects(conn, file_ids, root)
        ),
        "secrets": _maybe_run_verify_check(selected, "secrets", lambda: _check_secrets(target_paths, root)),
        "command_examples": _maybe_run_verify_check(
            selected, "command_examples", lambda: _check_command_examples(target_paths, root)
        ),
        "claims": _maybe_run_verify_check(selected, "claims", lambda: _check_claims(target_paths, root)),
        _VERIFY_BREAKING_CATEGORY: _maybe_run_verify_check(
            selected, _VERIFY_BREAKING_CATEGORY, lambda: _check_breaking(conn, file_ids, target_paths, root)
        ),
        _VERIFY_TAINT_CATEGORY: _maybe_run_verify_check(
            selected, _VERIFY_TAINT_CATEGORY, lambda: _check_taint(conn, file_ids, target_paths, root)
        ),
        _VERIFY_DELETE_CATEGORY: _maybe_run_verify_check(
            selected, _VERIFY_DELETE_CATEGORY, lambda: _check_delete_safety(conn, target_paths, root)
        ),
        _VERIFY_MIGRATION_CATEGORY: _maybe_run_verify_check(
            selected, _VERIFY_MIGRATION_CATEGORY, lambda: _check_migration_safety(conn, target_paths, root)
        ),
        _VERIFY_SMELLS_CATEGORY: _maybe_run_verify_check(
            selected, _VERIFY_SMELLS_CATEGORY, lambda: _check_smells(conn, target_paths)
        ),
        _VERIFY_CLONES_CATEGORY: _maybe_run_verify_check(
            selected, _VERIFY_CLONES_CATEGORY, lambda: _check_clones(conn, target_paths)
        ),
        _VERIFY_MAGIC_CATEGORY: _maybe_run_verify_check(
            selected, _VERIFY_MAGIC_CATEGORY, lambda: _check_magic_numbers(target_paths, root)
        ),
        _VERIFY_DEAD_CATEGORY: _maybe_run_verify_check(
            selected, _VERIFY_DEAD_CATEGORY, lambda: _check_dead(conn, target_paths)
        ),
        _VERIFY_N1_CATEGORY: _maybe_run_verify_check(
            selected, _VERIFY_N1_CATEGORY, lambda: _check_n1(conn, target_paths)
        ),
        _VERIFY_OVER_FETCH_CATEGORY: _maybe_run_verify_check(
            selected, _VERIFY_OVER_FETCH_CATEGORY, lambda: _check_over_fetch(conn, target_paths)
        ),
        _VERIFY_LLM_SMELLS_CATEGORY: _maybe_run_verify_check(
            selected, _VERIFY_LLM_SMELLS_CATEGORY, lambda: _check_llm_smells(target_paths, root)
        ),
        _VERIFY_HERMETICITY_CATEGORY: _maybe_run_verify_check(
            selected, _VERIFY_HERMETICITY_CATEGORY, lambda: _check_test_hermeticity(target_paths, root)
        ),
    }


def _apply_verify_deep(categories: dict, deep: bool, conn, file_ids: list[int]) -> None:
    if deep:
        categories["patterns"] = _run_deep_patterns(conn, file_ids)


def _apply_secrets_verdict_floor(score: int, verdict: str, categories: dict) -> tuple[int, str]:
    secrets_fails = sum(
        1
        for violation in (categories.get("secrets", {}).get("violations") or [])
        if violation.get("severity") == SEVERITY_FAIL
    )
    if verdict == "PASS" and secrets_fails:
        return min(score, 79), "WARN"
    return score, verdict


def _apply_hard_block_floor(score: int, verdict: str, violations: list[dict]) -> tuple[int, str]:
    """Behavioral verdict floors, applied AFTER diff scoping so neither the
    lenient weighted composite nor a no-op diff-scope can launder a real
    regression back to PASS (the post-edit gate keys on a non-PASS verdict).

      * hard-block guardrail (breaking change) -> FAIL, score floored hard.
      * impacted-test FAILURE -> at least WARN (secrets-gate pattern): the
        executable signal must surface even when the failing test file is not
        itself on a changed line, which is the common case (you edit source,
        the covering test lives elsewhere)."""
    if any(v.get("hard_block") and v.get("severity") == SEVERITY_FAIL for v in violations):
        return min(score, _HARD_BLOCK_SCORE), "FAIL"
    test_failed = any(v.get("category") == "tests" and v.get("severity") == SEVERITY_FAIL for v in violations)
    if test_failed and str(verdict).upper().startswith("PASS"):
        return min(score, 79), "WARN"
    return score, verdict


def _syntax_degraded_qualifiers(categories: dict) -> list[str]:
    qualifiers: list[str] = []
    parse_failures = categories.get("syntax", {}).get("parse_failures", 0)
    syntax_unavailable = categories.get("syntax", {}).get("available", True) is False
    if parse_failures > 0:
        qualifiers.append(
            f"{parse_failures} file{'s' if parse_failures != 1 else ''} not syntax-checked (parse failed)"
        )
    if syntax_unavailable:
        qualifiers.append("syntax check unavailable (tree-sitter parser missing)")
    return qualifiers


def _apply_syntax_degraded_verdict(verdict: str, categories: dict) -> tuple[str, bool]:
    qualifiers = _syntax_degraded_qualifiers(categories)
    if not qualifiers:
        return verdict, False
    return f"{verdict} -- {'; '.join(qualifiers)}", True


def _flatten_category_violations(categories: dict) -> list[dict]:
    violations: list[dict] = []
    for cat_result in categories.values():
        violations.extend(cat_result.get("violations", []))
    return violations


def _advisory_categories(categories: dict) -> set[str]:
    return {name for name, result in categories.items() if result.get("advisory")}


def _gating_violations(violations: list[dict], advisory_categories: set[str]) -> list[dict]:
    return [violation for violation in violations if violation.get("category") not in advisory_categories]


def _filter_category_violations(categories: dict, keep_fn) -> None:
    for cat_result in categories.values():
        violations = cat_result.get("violations")
        if violations:
            cat_result["violations"] = [violation for violation in violations if keep_fn(violation)]


def _apply_verify_suppressions(root: Path, categories: dict, violations: list[dict]) -> tuple[list[dict], int]:
    try:
        from roam.commands.suppression import is_suppressed, load_suppressions

        suppressions = load_suppressions(root)
        if not suppressions:
            return violations, 0

        def is_suppressed_violation(violation):
            return is_suppressed(
                suppressions,
                violation.get("category", ""),
                violation.get("file", ""),
                violation.get("line"),
                symbol=violation.get("symbol"),
            )

        suppressed_count = sum(1 for violation in violations if is_suppressed_violation(violation))
        if suppressed_count:
            violations = [violation for violation in violations if not is_suppressed_violation(violation)]
            _filter_category_violations(categories, lambda violation: not is_suppressed_violation(violation))
        return violations, suppressed_count
    except Exception as exc:  # noqa: BLE001 — suppression must never break the gate
        from roam.observability import log_swallowed

        log_swallowed("verify.suppressions", exc)
        return violations, 0


def _emit_verify_baseline_written(violations: list[dict], root: Path, json_mode: bool) -> None:
    written = _write_verify_baseline(violations, root)
    envelope = json_envelope(
        "verify",
        summary={
            "verdict": "BASELINE_WRITTEN",
            "baseline_written": written,
            "baseline_path": str(_verify_baseline_path(root)),
        },
        violations=[],
    )
    if json_mode:
        click.echo(to_json(envelope))
        return
    click.echo(
        f"VERDICT: BASELINE_WRITTEN -- {written} finding"
        f"{'s' if written != 1 else ''} accepted "
        f"({_verify_baseline_path(root)})"
    )


def _filter_baselined_violations(root: Path, categories: dict, violations: list[dict]) -> tuple[list[dict], int, str]:
    baseline = _load_verify_baseline(root)
    if baseline is None:
        return violations, 0, "absent"

    remaining = dict(baseline)
    line_cache: dict = {}
    kept_ids = set()
    baselined_count = 0
    for violation in violations:
        fingerprint = _finding_fingerprint(violation, line_cache, root)
        if remaining.get(fingerprint, 0) > 0:
            remaining[fingerprint] -= 1
            baselined_count += 1
        else:
            kept_ids.add(id(violation))
    if baselined_count:
        violations = [violation for violation in violations if id(violation) in kept_ids]
        _filter_category_violations(categories, lambda violation: id(violation) in kept_ids)
    return violations, baselined_count, "applied"


def _changed_line_predicate(changed: dict[str, set[int]]):
    def on_changed_line(violation):
        # Hard-block guardrails (breaking change) are file-level, not line-
        # scoped — a signature change breaks callers regardless of which exact
        # line moved, so they survive --diff-only.
        if violation.get("hard_block"):
            return True
        path = violation.get("file")
        if path not in changed:
            return True
        line = violation.get("line")
        return line is not None and line in changed[path]

    return on_changed_line


def _filtered_verdict_score(gating_violations: list[dict]) -> tuple[int, str]:
    if not gating_violations:
        return 100, "PASS"
    broke_syntax = any(violation.get("category") == "syntax" for violation in gating_violations)
    return max(0, 100 - len(gating_violations) * 5), "FAIL" if broke_syntax else "WARN"


def _apply_diff_scope(
    root: Path,
    categories: dict,
    violations: list[dict],
    changed_lines: str | None,
    diff_only: bool,
    advisory_categories: set[str],
) -> tuple[list[dict], bool, int | None, str | None]:
    explicit_changed = _parse_changed_lines(changed_lines) if changed_lines else None
    if not ((diff_only or explicit_changed) and violations):
        return violations, False, None, None
    files = {violation.get("file") for violation in violations if violation.get("file")}
    changed = explicit_changed if explicit_changed is not None else _changed_line_ranges(files, root)
    if not changed:
        return violations, False, None, None
    keep_fn = _changed_line_predicate(changed)
    violations = [violation for violation in violations if keep_fn(violation)]
    _filter_category_violations(categories, keep_fn)
    score, verdict = _filtered_verdict_score(_gating_violations(violations, advisory_categories))
    return violations, True, score, verdict


def _recompute_filtered_verdict_if_needed(
    score: int,
    verdict: str,
    violations: list[dict],
    suppressed_count: int,
    baselined_count: int,
    diff_scoped: bool,
    advisory_categories: set[str],
) -> tuple[int, str]:
    if not ((suppressed_count or baselined_count) and not diff_scoped):
        return score, verdict
    return _filtered_verdict_score(_gating_violations(violations, advisory_categories))


def _annotate_sort_filter_violations(conn, violations: list[dict], severity: str | None) -> tuple[list[dict], int, int]:
    blast = _blast_radius_by_file(conn, {violation.get("file") for violation in violations if violation.get("file")})
    max_blast_radius = 0
    for violation in violations:
        blast_radius = blast.get(violation.get("file"), 0)
        violation["blast_radius"] = blast_radius
        max_blast_radius = max(max_blast_radius, blast_radius)
    violations.sort(
        key=lambda violation: (
            _SEVERITY_ORDER.get(violation.get("severity"), 9),
            -int(violation.get("blast_radius") or 0),
            violation.get("file") or "",
            violation.get("line") or 0,
        )
    )
    full_count = len(violations)
    if severity:
        severity_rank = {"fail": 0, "warn": 1, "info": 2}[severity.lower()]
        violations = [
            violation for violation in violations if _SEVERITY_ORDER.get(violation.get("severity"), 9) <= severity_rank
        ]
    return violations, max_blast_radius, full_count


def _verify_scope_summary(target_paths: list[str], file_map: dict) -> dict | None:
    non_code_count = sum(1 for path in target_paths if _is_non_code_verify_surface(path))
    unresolved_count = max(0, len(target_paths) - len(file_map))
    if not (non_code_count or unresolved_count):
        return None
    summary = {
        "target_file_count": len(target_paths),
        "indexed_file_count": len(file_map),
        "non_code_file_count": non_code_count,
    }
    if unresolved_count:
        summary["unresolved_file_count"] = unresolved_count
    if non_code_count:
        summary["non_code_scope_definition"] = (
            "Docs/product-copy surfaces are included for advisory checks such as "
            "command_examples and claims; code-gating checks use indexed source files."
        )
    return summary


def _category_summary(categories: dict) -> dict:
    summary = {}
    for cat_name, cat_result in categories.items():
        entry = {
            "score": cat_result["score"],
            "violation_count": len(cat_result.get("violations", [])),
            "violations": cat_result.get("violations", []),
        }
        if cat_result.get("parse_failures", 0) > 0:
            entry["parse_failures"] = cat_result["parse_failures"]
        if cat_result.get("available", True) is False:
            entry["available"] = False
            if cat_result.get("unavailable_reason"):
                entry["unavailable_reason"] = cat_result["unavailable_reason"]
        summary[cat_name] = entry
    return summary


def _build_verify_summary(
    verdict: str,
    score: int,
    threshold: int,
    files_checked: int,
    violation_count: int,
    selected: list[str],
    degraded: bool,
    severity: str | None,
    shown_count: int,
    total_count: int,
    suppressed_count: int,
    diff_scoped: bool,
    baseline_state: str | None,
    baselined_count: int,
    max_blast_radius: int,
    scope_summary: dict | None,
) -> dict:
    summary = {
        "verdict": verdict,
        "score": score,
        "threshold": threshold,
        "files_checked": files_checked,
        "violation_count": violation_count,
        "checks_run": selected,
    }
    if degraded:
        summary["partial_success"] = True
    if severity:
        summary["severity_filter"] = severity.lower()
        summary["shown_count"] = shown_count
        summary["total_count"] = total_count
    if suppressed_count:
        summary["suppressed"] = suppressed_count
    if diff_scoped:
        summary["diff_scoped"] = True
    if baseline_state:
        summary["baseline"] = baseline_state
        if baselined_count:
            summary["baselined"] = baselined_count
    if max_blast_radius:
        summary["max_blast_radius"] = max_blast_radius
        summary["blast_radius_definition"] = (
            "MAX caller count (graph_metrics.in_degree) among symbols in a "
            "finding's file; findings sorted by severity then blast_radius"
        )
    if scope_summary:
        summary["scope"] = scope_summary
    return summary


def _emit_verify_result(
    ctx,
    verify_envelope: dict,
    all_violations: list[dict],
    report: bool,
    persist: bool,
    out: str | None,
    root: Path,
    json_mode: bool,
    score: int,
    threshold: int,
    fix_suggestions: bool,
    categories: dict,
    selected: list[str],
) -> None:
    if report:
        if persist:
            _persist_verify_report(verify_envelope, all_violations, out, root, json_mode)
        else:
            _render_verify_report(verify_envelope, all_violations, json_mode)
        return
    if json_mode:
        click.echo(to_json(verify_envelope))
        if score < threshold:
            ctx.exit(EXIT_GATE_FAILURE)
        return
    summary = verify_envelope["summary"]
    _emit_verify_text(
        score,
        threshold,
        summary["verdict"],
        summary["violation_count"],
        summary["files_checked"],
        selected,
        categories,
        fix_suggestions,
    )
    if score < threshold:
        ctx.exit(EXIT_GATE_FAILURE)


def _emit_verify_text(
    score: int,
    threshold: int,
    verdict: str,
    violation_count: int,
    files_checked: int,
    selected: list[str],
    categories: dict,
    fix_suggestions: bool,
) -> None:
    click.echo(
        f"VERDICT: {verdict} (score {score}/100) "
        f"-- {violation_count} issue{'s' if violation_count != 1 else ''} "
        f"in {files_checked} changed file{'s' if files_checked != 1 else ''}"
    )
    if len(selected) != len(_ALL_CHECKS):
        skipped = ", ".join(check for check in _ALL_CHECKS if check not in selected)
        click.echo(f"checks: {', '.join(selected)} (skipped: {skipped})")
    click.echo("")
    for label, key in (
        ("NAMING", "naming"),
        ("IMPORTS", "imports"),
        ("ERROR HANDLING", "error_handling"),
        ("DUPLICATES", "duplicates"),
        ("SYNTAX", "syntax"),
        ("COMPLEXITY", "complexity"),
        ("CYCLES", "cycles"),
        ("TESTS", "tests"),
        ("COMMAND EXAMPLES", "command_examples"),
        ("CLAIMS", "claims"),
        ("BREAKING", _VERIFY_BREAKING_CATEGORY),
        ("TAINT", _VERIFY_TAINT_CATEGORY),
    ):
        _print_category(label, categories[key], fix_suggestions)
    gate_result = "PASS" if score >= threshold else "FAIL"
    click.echo(f"\nOverall: {score}/100 (threshold: {threshold}) -- {gate_result}")


def _build_verify_run(
    root: Path,
    target_paths: list[str],
    selected: list[str],
    deep: bool,
    baseline_write: bool,
    new_only: bool,
    changed_lines: str | None,
    diff_only: bool,
    severity: str | None,
    threshold: int,
    token_budget: int,
    json_mode: bool,
) -> dict | None:
    with open_db(readonly=True) as conn:
        file_map = resolve_changed_to_db(conn, target_paths)
        file_ids = list(file_map.values())
        categories = _run_verify_categories(conn, selected, file_ids, target_paths, root)
        _apply_verify_deep(categories, deep, conn, file_ids)

        score = _compute_composite(categories, selected)
        verdict = _compute_verdict(score)
        score, verdict = _apply_secrets_verdict_floor(score, verdict, categories)
        verdict, degraded = _apply_syntax_degraded_verdict(verdict, categories)

        all_violations = _flatten_category_violations(categories)
        advisory_cats = _advisory_categories(categories)
        all_violations, suppressed_count = _apply_verify_suppressions(root, categories, all_violations)

        if baseline_write:
            _emit_verify_baseline_written(all_violations, root, json_mode)
            return None

        baselined_count = 0
        baseline_state = None
        if new_only:
            all_violations, baselined_count, baseline_state = _filter_baselined_violations(
                root, categories, all_violations
            )

        all_violations, diff_scoped, scoped_score, scoped_verdict = _apply_diff_scope(
            root, categories, all_violations, changed_lines, diff_only, advisory_cats
        )
        if scoped_score is not None and scoped_verdict is not None:
            score, verdict = scoped_score, scoped_verdict

        score, verdict = _recompute_filtered_verdict_if_needed(
            score, verdict, all_violations, suppressed_count, baselined_count, diff_scoped, advisory_cats
        )
        # Hard-block guardrails win last, so neither diff-scoping nor the
        # recompute can launder a breaking change back to PASS.
        score, verdict = _apply_hard_block_floor(score, verdict, all_violations)

        violation_count = len(all_violations)
        files_checked = len(file_map)
        scope_summary = _verify_scope_summary(target_paths, file_map)
        all_violations, max_blast_radius, severity_full_count = _annotate_sort_filter_violations(
            conn, all_violations, severity
        )
        verify_summary = _build_verify_summary(
            verdict,
            score,
            threshold,
            files_checked,
            violation_count,
            selected,
            degraded,
            severity,
            len(all_violations),
            severity_full_count,
            suppressed_count,
            diff_scoped,
            baseline_state,
            baselined_count,
            max_blast_radius,
            scope_summary,
        )
        verify_envelope = json_envelope(
            "verify",
            summary=verify_summary,
            categories=_category_summary(categories),
            violations=all_violations,
            budget=token_budget,
        )
        auto_log(verify_envelope, action="verify", target=((target_paths[0] if target_paths else "") or ""))
        return {
            "envelope": verify_envelope,
            "violations": all_violations,
            "score": score,
            "categories": categories,
        }


def _verify_runtime_context(ctx) -> tuple[bool, int, Path]:
    obj = ctx.obj or {}
    return bool(obj.get("json")), int(obj.get("budget", 0) or 0), find_project_root()


def _active_verify_config_or_emit(set_on: bool, set_off: bool, root: Path, json_mode: bool) -> dict | None:
    if _handle_verify_toggle(set_on, set_off, root, json_mode):
        return None
    cfg = load_verify_config(root)
    if not _verify_enabled_from_env(cfg):
        _emit_verify_disabled(json_mode)
        return None
    return cfg


def _resolve_verify_request(
    cfg: dict,
    root: Path,
    files,
    threshold: int | None,
    checks_opt: str | None,
    auto: bool,
    deep: bool,
    report: bool,
    diff_only: bool,
) -> dict:
    resolved_threshold = _resolve_verify_threshold(threshold, cfg)
    target_paths = _resolve_verify_targets(files, root)
    selected = resolve_selected_checks(checks_opt, auto, cfg, target_paths)
    selected, target_paths, report_forced_full_files = _resolve_report_scope(
        report, files, checks_opt, selected, target_paths, root
    )
    return {
        "threshold": resolved_threshold,
        "target_paths": target_paths,
        "selected": selected,
        "deep": _auto_deep_enabled(auto, deep),
        "diff_only": diff_only and not report_forced_full_files,
    }


def _resolve_report_scope(
    report: bool,
    files,
    checks_opt: str | None,
    selected: list[str],
    target_paths: list[str],
    root: Path,
) -> tuple[list[str], list[str], bool]:
    if not report:
        return selected, target_paths, False
    return _apply_report_mode(report, files, checks_opt, selected, target_paths, root)


def _emit_empty_verify_if_needed(target_paths: list[str], json_mode: bool, threshold: int) -> bool:
    if target_paths:
        return False
    _emit_empty_verify(json_mode, threshold)
    return True


def _emit_verify_run_result(
    ctx,
    request: dict,
    root: Path,
    baseline_write: bool,
    new_only: bool,
    changed_lines: str | None,
    severity: str | None,
    token_budget: int,
    json_mode: bool,
    report: bool,
    persist: bool,
    out: str | None,
    fix_suggestions: bool,
) -> None:
    run = _build_verify_run(
        root,
        request["target_paths"],
        request["selected"],
        request["deep"],
        baseline_write,
        new_only,
        changed_lines,
        request["diff_only"],
        severity,
        request["threshold"],
        token_budget,
        json_mode,
    )
    if run is None:
        return

    _emit_verify_result(
        ctx,
        run["envelope"],
        run["violations"],
        report,
        persist,
        out,
        root,
        json_mode,
        run["score"],
        request["threshold"],
        fix_suggestions,
        run["categories"],
        request["selected"],
    )


def _dispatch_verify_command(
    ctx,
    root: Path,
    json_mode: bool,
    token_budget: int,
    set_on: bool,
    set_off: bool,
    files,
    threshold: int | None,
    checks_opt: str | None,
    auto: bool,
    deep: bool,
    report: bool,
    diff_only: bool,
    baseline_write: bool,
    new_only: bool,
    changed_lines: str | None,
    severity: str | None,
    persist: bool,
    out: str | None,
    fix_suggestions: bool,
) -> None:
    cfg = _active_verify_config_or_emit(set_on, set_off, root, json_mode)
    if cfg is None:
        return

    ensure_index()
    request = _resolve_verify_request(cfg, root, files, threshold, checks_opt, auto, deep, report, diff_only)
    if _emit_empty_verify_if_needed(request["target_paths"], json_mode, request["threshold"]):
        return

    # Verify reads symbols from the DB; refresh newly edited targets before
    # resolving file IDs so fresh symbols are not invisible to the gate.
    _refresh_stale_verify_targets(root, request["target_paths"])
    _emit_verify_run_result(
        ctx,
        request,
        root,
        baseline_write,
        new_only,
        changed_lines,
        severity,
        token_budget,
        json_mode,
        report,
        persist,
        out,
        fix_suggestions,
    )


# ---------------------------------------------------------------------------
# Main command
# ---------------------------------------------------------------------------


@roam_capability(
    name="verify",
    category="workflow",
    summary="Verify changed files follow codebase conventions",
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
@click.command()
@click.option(
    "--changed",
    is_flag=True,
    default=False,
    help="Use git diff to get changed files (default if no files given)",
)
@click.option("--threshold", type=int, default=None, help="Fail below this score (default 70, or .roam/verify.yaml).")
@click.option(
    "--checks",
    "checks_opt",
    default=None,
    help=(
        "Comma-list to run: naming,imports,error_handling,duplicates,syntax,"
        "command_examples,claims. Default: all (or .roam/verify.yaml)."
    ),
)
@click.option(
    "--auto",
    is_flag=True,
    default=False,
    help="Auto-select checks from what changed (Python edits unlock "
    "Python checks; source edits unlock naming/duplicates).",
)
@click.option(
    "--on", "set_on", is_flag=True, default=False, help="Resume verify: write .roam/verify.yaml enabled:true and exit."
)
@click.option(
    "--off", "set_off", is_flag=True, default=False, help="Stop verify: write .roam/verify.yaml enabled:false and exit."
)
@click.option(
    "--fix-suggestions",
    is_flag=True,
    default=False,
    help="Show concrete fix suggestions for each violation",
)
@click.option(
    "--diff-only",
    "diff_only",
    is_flag=True,
    default=False,
    help="Report only violations on lines changed vs HEAD (git diff). Scopes "
    "the verdict to the edit, not the whole file. Untracked/new files keep "
    "all violations (no baseline to diff against).",
)
@click.option(
    "--changed-lines",
    "changed_lines",
    default=None,
    help="Scope to EXPLICIT line ranges instead of git-diff-vs-HEAD: "
    "'path:START-END,path:LINE,...'. For editor/agent harnesses that know "
    "exactly which lines they changed this turn — avoids surfacing a big "
    "uncommitted tree's pre-existing debt. Implies --diff-only behaviour.",
)
@click.option(
    "--baseline-write",
    "baseline_write",
    is_flag=True,
    default=False,
    help="Snapshot ALL current findings to .roam/verify-baseline.json as "
    "accepted debt, then exit. Subsequent `--new-only` runs surface only "
    "findings absent from this baseline. Line-shift tolerant.",
)
@click.option(
    "--new-only",
    "new_only",
    is_flag=True,
    default=False,
    help="Surface only findings NOT in .roam/verify-baseline.json (the "
    "accepted-debt baseline). No baseline present → every finding is new. "
    "Composes with --changed-lines (identity AND position scoping).",
)
@click.option(
    "--deep",
    "deep",
    is_flag=True,
    default=False,
    help="DEEP review — in addition to the standard checks, run the algorithm/"
    "idiom anti-pattern detectors (perf traps, N+1 ORM, dangerous-eval, "
    "mutable-default-arg, regex-alternation, lambda-in-loop, ...) SCOPED to "
    "the target files, surfaced as an advisory `patterns` category. Fast "
    "(only the changed files). Advisory: does NOT change the PASS/FAIL "
    "verdict. Off by default — the standard checks are unchanged.",
)
@click.option(
    "--report",
    "report",
    is_flag=True,
    default=False,
    help="REPORT mode — scan the WHOLE repo (or the given path) with all static "
    "checks, NON-gating (always exit 0), and emit a ranked punch-list the "
    "agent can work through (severity, then blast radius, then file:line + "
    "fix). Skips the executable `tests` check. Pair with --json for the flat "
    "findings list, or pass a path to scope the scan.",
)
@click.option(
    "--severity",
    "severity",
    default=None,
    type=click.Choice(["fail", "warn", "info"], case_sensitive=False),
    help="Show only findings at this severity AND above (fail < warn < info). "
    "A display filter — the PASS/FAIL verdict + score are still computed "
    "from the full finding set. Pairs with --report to cut the noise floor "
    "(e.g. `--report --severity fail` = the must-fix punch-list).",
)
@click.option(
    "--persist",
    "persist",
    is_flag=True,
    default=False,
    help="With --report: WRITE the full report JSON to a file (default "
    "`.roam/verify-report.json`) and emit only a COMPACT summary (verdict, "
    "per-severity + per-category counts, persisted path, top findings) to "
    "stdout. For agents/tools: store a large whole-repo report on disk and "
    "inspect it on demand instead of consuming it inline.",
)
@click.option(
    "--out",
    "out",
    default=None,
    type=click.Path(),
    help="Output path for --persist (default `.roam/verify-report.json`).",
)
@click.argument("files", nargs=-1, type=click.Path())
@click.pass_context
def verify(
    ctx,
    changed,
    threshold,
    checks_opt,
    auto,
    set_on,
    set_off,
    fix_suggestions,
    diff_only,
    changed_lines,
    baseline_write,
    new_only,
    deep,
    report,
    severity,
    persist,
    out,
    files,
):
    """Verify changed files follow codebase conventions.

    Checks naming, import patterns, error handling, duplicate logic,
    and syntax integrity against established codebase patterns.

    If no files are specified, defaults to git-changed files.

    Unlike ``conventions`` (which reports codebase-wide style patterns) and
    ``smells`` (which detects structural anti-patterns from DB queries), this
    command runs pre-commit checks on changed files: naming, imports, error
    handling, duplicates, and syntax.
    """
    json_mode, token_budget, root = _verify_runtime_context(ctx)
    _dispatch_verify_command(
        ctx,
        root,
        json_mode,
        token_budget,
        set_on,
        set_off,
        files,
        threshold,
        checks_opt,
        auto,
        deep,
        report,
        diff_only,
        baseline_write,
        new_only,
        changed_lines,
        severity,
        persist,
        out,
        fix_suggestions,
    )


def _print_category(label: str, result: dict, fix_suggestions: bool):
    """Print a single category's results in text format."""
    if result.get("skipped"):
        click.echo(f"{label}: skipped (not selected)")
        click.echo("")
        return
    score = result["score"]
    violations = result.get("violations", [])

    click.echo(f"{label} ({score}/100):")
    if not violations:
        click.echo("  OK -- all checks passed")
    else:
        for v in violations:
            sev = v.get("severity", "WARN")
            file_loc = loc(v["file"], v.get("line"))
            click.echo(f"  {sev}: {file_loc} -- {v['message']}")
            if fix_suggestions and v.get("fix"):
                click.echo(f"    FIX: {v['fix']}")
    click.echo("")


def _all_source_paths(root: Path) -> list[str]:
    """All indexed CODE files (excludes data/markup languages). The report-mode
    whole-repo target when no path is given."""
    try:
        with open_db(readonly=True) as conn:
            rows = conn.execute("SELECT path, language FROM files").fetchall()
    except Exception:  # noqa: BLE001 — best-effort; never break verify
        return []
    out: list[str] = []
    for r in rows:
        lang = (r["language"] or "").lower()
        if lang and lang not in _MODULE_INIT_SKIP_LANGS:
            out.append(r["path"].replace("\\", "/"))
    return out


def _all_advisory_surface_paths(root: Path, selected: list[str]) -> list[str]:
    try:
        with open_db(readonly=True) as conn:
            rows = conn.execute("SELECT path FROM files").fetchall()
    except Exception:  # noqa: BLE001 - report mode stays best-effort
        return []
    include_commands = "command_examples" in selected
    include_claims = "claims" in selected
    return [
        row["path"].replace("\\", "/")
        for row in rows
        if (include_commands and _is_command_example_surface(row["path"]))
        or (include_claims and _is_claim_surface(row["path"]))
    ]


def _all_report_paths(root: Path, selected: list[str]) -> list[str]:
    return sorted(set(_all_source_paths(root)) | set(_all_advisory_surface_paths(root, selected)))


def _render_verify_report(envelope: dict, violations: list, json_mode: bool, cap: int = 200) -> None:
    """REPORT mode — NON-gating ranked punch-list the agent can work through.

    JSON: emit the envelope (already carries the flat findings). Text: a count
    header (total + per-category) then findings ranked by severity, then blast
    radius, then file:line — each with its one-line fix.
    """
    if json_mode:
        click.echo(to_json(envelope))
        return
    total = len(violations)
    fails = sum(1 for v in violations if v.get("severity") == SEVERITY_FAIL)
    warns = sum(1 for v in violations if v.get("severity") == SEVERITY_WARN)
    files_n = envelope.get("summary", {}).get("files_checked", "?")
    click.echo(
        f"VERDICT: REPORT -- {total} finding{'s' if total != 1 else ''} "
        f"({fails} FAIL, {warns} WARN) across {files_n} files (non-gating)"
    )
    counts = Counter(v.get("category", "?") for v in violations)
    if counts:
        click.echo("by category: " + ", ".join(f"{k}={n}" for k, n in sorted(counts.items(), key=lambda x: -x[1])))
    click.echo("")
    ranked = sorted(
        violations,
        key=lambda v: (
            _SEVERITY_ORDER.get(v.get("severity"), 9),
            -(v.get("blast_radius") or 0),
            v.get("file") or "",
            v.get("line") or 0,
        ),
    )
    for v in ranked[:cap]:
        click.echo(
            f"[{v.get('severity', '?')}] {v.get('category')}  "
            f"{loc(v.get('file'), v.get('line'))}  {v.get('message', '')}"
        )
        if v.get("fix"):
            click.echo(f"      fix: {v['fix']}")
    if total > cap:
        click.echo(f"\n... +{total - cap} more (use --json for the full ranked list)")


def _persist_verify_report(envelope: dict, violations: list, out, root: Path, json_mode: bool) -> None:
    """With --report --persist: write the FULL report to a file; emit a COMPACT summary.

    Lets an agent/tool store a large whole-repo report on disk (default
    ``.roam/verify-report.json``) and inspect it on demand, instead of consuming a
    multi-thousand-finding report inline. Stdout carries only verdict + per-severity
    + per-category counts + the persisted path + a few top findings.
    """
    import datetime as _dt

    from roam.atomic_io import atomic_write_text

    out_path = Path(out) if out else (root / ".roam" / "verify-report.json")
    out_path.parent.mkdir(parents=True, exist_ok=True)

    # dict-wrapped: serialized into the persisted report + the compact envelope.
    sev_counts: dict[str, int] = dict(Counter(v.get("severity", "?") for v in violations))
    cat_counts = {
        c: d.get("violation_count", 0)
        for c, d in (envelope.get("categories") or {}).items()
        if d.get("violation_count", 0) > 0
    }

    # Write the full report with a self-describing summary (counts stamped in) so a
    # consumer (e.g. an agent host platform) can read `.summary` without iterating the violations.
    full = dict(envelope)
    full["summary"] = {
        **envelope.get("summary", {}),
        "severity_counts": sev_counts,
        "category_counts": cat_counts,
    }
    full["generated_at"] = _dt.datetime.now(_dt.timezone.utc).isoformat()
    atomic_write_text(out_path, to_json(full))
    ranked = sorted(
        violations,
        key=lambda v: (_SEVERITY_ORDER.get(v.get("severity"), 9), -(v.get("blast_radius") or 0)),
    )
    top_findings = [
        {
            "severity": v.get("severity"),
            "category": v.get("category"),
            "file": v.get("file"),
            "line": v.get("line"),
            "message": (v.get("message") or "")[:140],
        }
        for v in ranked[:8]
    ]
    s_in = envelope.get("summary", {})
    compact = json_envelope(
        "verify-report",
        summary={
            "verdict": s_in.get("verdict"),
            "score": s_in.get("score"),
            "violation_count": s_in.get("violation_count"),
            "files_checked": s_in.get("files_checked"),
            "persisted_path": str(out_path),
            "severity_counts": sev_counts,
            "category_counts": cat_counts,
            "inspect_hint": f"read {out_path} (the `violations` array) for the full ranked list",
        },
        top_findings=top_findings,
    )
    if json_mode:
        click.echo(to_json(compact))
        return
    click.echo(
        f"VERDICT: {s_in.get('verdict')} -- {s_in.get('violation_count')} issues "
        f"(FAIL {sev_counts.get(SEVERITY_FAIL, 0)}, "
        f"WARN {sev_counts.get(SEVERITY_WARN, 0)}, "
        f"INFO {sev_counts.get(SEVERITY_INFO, 0)})"
    )
    if cat_counts:
        click.echo("by category: " + ", ".join(f"{k}={n}" for k, n in sorted(cat_counts.items(), key=lambda x: -x[1])))
    click.echo(f"persisted: {out_path}  (read it / `jq '.violations'` to inspect all findings)")
