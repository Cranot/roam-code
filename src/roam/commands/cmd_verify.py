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

import os
import re
from collections import Counter, defaultdict
from difflib import SequenceMatcher
from pathlib import Path

import click

from roam.capability import roam_capability
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
from roam.commands.conventions_helper import CONVENTION_NEUTRAL_FILE_ROLES, is_excluded_path
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
}

# Severity levels for violations
SEVERITY_FAIL = "FAIL"
SEVERITY_WARN = "WARN"
SEVERITY_INFO = "INFO"

# FAILs first, then WARN, then INFO, then anything unknown. Ranks the flat
# findings list (Tier-1 blast-radius weighting) without touching verdict/score.
_SEVERITY_ORDER = {SEVERITY_FAIL: 0, SEVERITY_WARN: 1, SEVERITY_INFO: 2}


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


def _changed_line_ranges(files, root):
    """Map ``{relpath: set(changed new-line numbers)}`` from ``git diff HEAD
    -U0``. Files with no tracked diff (untracked / new / no hunks) are omitted,
    so callers keep all of those files' violations (no baseline to scope
    against). Used by ``--diff-only`` to report only what the edit touched."""
    import re as _re
    import subprocess

    ranges: dict[str, set] = {}
    flist = sorted({f for f in (files or []) if f})
    if not flist:
        return ranges
    try:
        out = subprocess.run(
            ["git", "-C", str(root), "diff", "HEAD", "-U0", "--", *flist],
            capture_output=True,
            text=True,
            timeout=8,
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return ranges
    cur = None
    hunk_re = _re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@")
    for line in out.splitlines():
        if line.startswith("+++ b/"):
            cur = line[6:].strip()
            ranges.setdefault(cur, set())
        elif cur is not None and line.startswith("@@"):
            m = hunk_re.match(line)
            if m:
                start = int(m.group(1))
                count = int(m.group(2)) if m.group(2) is not None else 1
                for ln in range(start, start + max(count, 1)):
                    ranges[cur].add(ln)
    return {f: s for f, s in ranges.items() if s}


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
        seg = seg.strip()
        if ":" not in seg:
            continue
        path, _, rng = seg.rpartition(":")
        path, rng = path.strip(), rng.strip()
        if not path or not rng:
            continue
        try:
            if "-" in rng:
                a, _, b = rng.partition("-")
                lo, hi = int(a), int(b)
            else:
                lo = hi = int(rng)
        except ValueError:
            continue
        if lo > hi:
            lo, hi = hi, lo
        ranges.setdefault(path, set()).update(range(lo, hi + 1))
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
_ALL_CHECKS: tuple[str, ...] = _DEFAULT_CHECKS + ("complexity", "cycles", "tests")
_VERIFY_CONFIG_REL = (".roam", "verify.yaml")


def _verify_config_path(root: Path) -> Path:
    return root.joinpath(*_VERIFY_CONFIG_REL)


def load_verify_config(root: Path) -> dict:
    """Load `.roam/verify.yaml`. Keys: enabled(bool), checks(list|None),
    threshold(int|None), auto(bool). A missing/bad file → permissive defaults
    (enabled, all checks) so verify never silently breaks on bad config."""
    cfg: dict = {"enabled": True, "checks": None, "threshold": None, "auto": False}
    path = _verify_config_path(root)
    if not path.exists():
        return cfg
    try:
        import yaml

        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except Exception:  # noqa: BLE001 — bad config must not break the gate
        return cfg
    if isinstance(data, dict):
        if isinstance(data.get("enabled"), bool):
            cfg["enabled"] = data["enabled"]
        if isinstance(data.get("auto"), bool):
            cfg["auto"] = data["auto"]
        if isinstance(data.get("threshold"), int):
            cfg["threshold"] = data["threshold"]
        raw = data.get("checks")
        if isinstance(raw, list):
            picked = [c for c in raw if c in _ALL_CHECKS]
            cfg["checks"] = picked or None
    return cfg


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


def _check_naming(conn, file_ids: list[int]) -> dict:
    """Check naming consistency of symbols in changed files.

    Compares new/changed symbol names against the codebase's dominant
    naming convention per kind-group (functions, classes, variables, etc.).
    """
    if not file_ids:
        return {"score": 100, "violations": []}

    # 1. Get the dominant style per kind-group from ALL symbols
    all_symbols = conn.execute("""
        SELECT s.name, s.kind, s.signature, f.language AS language, f.path AS path,
               COALESCE(f.file_role, 'source') AS file_role
        FROM symbols s
        JOIN files f ON s.file_id = f.id
        WHERE s.kind IN ('function', 'method', 'class', 'interface',
                         'struct', 'trait', 'enum', 'variable',
                         'constant', 'property', 'field', 'type_alias')
    """).fetchall()

    # Convention is computed PER (kind-group, LANGUAGE): naming norms are
    # language-specific, so a JS `clampComment` or a Kotlin fixture's camelCase
    # must be compared against THAT language's convention — not the repo's
    # Python-dominant snake_case. Computing one codebase-wide convention across
    # languages was the cross-language false-positive source (15 FPs on .js CI
    # scripts + .kt parser fixtures in a 99.9%-Python repo).
    group_cases: dict[tuple[str, str], Counter] = defaultdict(Counter)
    for sym in all_symbols:
        # Exclude parser test fixtures + codegen templates from MODELING the
        # convention — they're deliberately written in varied styles (e.g. a
        # Kotlin fixture's 78%-snake mix), not the project's own conventions.
        if is_excluded_path(sym["path"]):
            continue
        # Test files follow the test framework's idiom (`test_*` snake_case
        # in PHPUnit/pytest); on test-heavy repos they OUTVOTE production
        # code and invert the convention (dogfood: PSR-12 PHP
        # repo reported snake_case 62.8% → ~2000 naming FPs). Vendored and
        # generated files carry third-party style.
        if sym["file_role"] in CONVENTION_NEUTRAL_FILE_ROLES:
            continue
        group = _naming_group_or_skip(sym["name"], sym["kind"], sym["language"], sym["signature"])
        if group is None:
            continue
        style = classify_case(sym["name"])
        if style:
            group_cases[(group, (sym["language"] or "").lower())][style] += 1

    # Dominant style per (group, language). Require a minimum sample count so a
    # handful of symbols in a non-primary language can neither establish nor be
    # flagged against a "convention" (sparse JS/Kotlin/etc. are simply skipped).
    dominant: dict[tuple[str, str], tuple[str, float]] = {}
    for key, counter in group_cases.items():
        total = sum(counter.values())
        if total >= _NAMING_MIN_LANG_SAMPLES:
            best_style, best_count = counter.most_common(1)[0]
            dominant[key] = (best_style, round(100 * best_count / total, 1))

    # 2. Check symbols in changed files
    changed_symbols = batched_in(
        conn,
        """SELECT s.name, s.kind, s.line_start, s.signature,
                  f.path as file_path, f.language AS language,
                  COALESCE(f.file_role, 'source') AS file_role
           FROM symbols s
           JOIN files f ON s.file_id = f.id
           WHERE s.file_id IN ({ph})
             AND s.kind IN ('function', 'method', 'class', 'interface',
                            'struct', 'trait', 'enum', 'variable',
                            'constant', 'property', 'field', 'type_alias')""",
        file_ids,
    )

    violations = []
    checked = 0
    for sym in changed_symbols:
        # Don't flag names INSIDE fixtures/templates (parser test data / codegen).
        if is_excluded_path(sym["file_path"]):
            continue
        # Test-framework idiom isn't the project convention; never flag
        # test/vendored/generated files for naming (mirror of the model
        # loop's exclusion above — flagging them against the production
        # convention is the same FP in the other direction).
        if sym["file_role"] in CONVENTION_NEUTRAL_FILE_ROLES:
            continue
        name = sym["name"]
        if len(name) < _MIN_NAME_LEN or name in _SKIP_NAMES:
            continue
        if name.startswith("__") and name.endswith("__"):
            continue

        group = _naming_group_or_skip(name, sym["kind"], sym["language"], sym["signature"])
        if group is None:
            continue
        style = classify_case(name)
        if not style:
            continue

        checked += 1
        key = (group, (sym["language"] or "").lower())
        if key in dominant:
            expected_style, pct = dominant[key]
            if style != expected_style and pct >= 60:
                violations.append(
                    {
                        "category": "naming",
                        "severity": SEVERITY_WARN if pct < 90 else SEVERITY_FAIL,
                        "file": sym["file_path"],
                        "line": sym["line_start"],
                        "message": (
                            f"fn `{name}` uses {style} (codebase: {expected_style} {pct}%)"
                            if group == "functions"
                            else f"{group[:-1]} `{name}` uses {style} (codebase: {expected_style} {pct}%)"
                        ),
                        "symbol": name,
                        "actual_style": style,
                        "expected_style": expected_style,
                        "codebase_pct": pct,
                        "fix": f"Rename `{name}` to match {expected_style} convention",
                    }
                )

    # Score: fraction of checked symbols that are consistent
    if checked == 0:
        score = 100
    else:
        score = round(100 * (checked - len(violations)) / checked)
        score = max(0, min(100, score))

    return {"score": score, "violations": violations}


# ---------------------------------------------------------------------------
# Import pattern consistency check
# ---------------------------------------------------------------------------


def _check_imports(conn, file_ids: list[int]) -> dict:
    """Check import patterns in changed files against codebase norms.

    Detects whether changed files follow the project's dominant import style
    (absolute vs relative) based on file_edges data.
    """
    if not file_ids:
        return {"score": 100, "violations": []}

    # 1. Determine the codebase import style from ALL file_edges
    all_edges = conn.execute("""
        SELECT fe.source_file_id, sf.path as source_path, tf.path as target_path
        FROM file_edges fe
        JOIN files sf ON fe.source_file_id = sf.id
        JOIN files tf ON fe.target_file_id = tf.id
        WHERE fe.kind = 'imports'
    """).fetchall()

    if not all_edges:
        return {"score": 100, "violations": []}

    # Classify each import edge
    absolute_count = 0
    relative_count = 0
    for edge in all_edges:
        src_dir = (
            edge["source_path"].replace("\\", "/").rsplit("/", 1)[0]
            if "/" in edge["source_path"].replace("\\", "/")
            else ""
        )
        tgt_dir = (
            edge["target_path"].replace("\\", "/").rsplit("/", 1)[0]
            if "/" in edge["target_path"].replace("\\", "/")
            else ""
        )
        if (
            src_dir
            and tgt_dir
            and (src_dir == tgt_dir or src_dir.startswith(tgt_dir + "/") or tgt_dir.startswith(src_dir + "/"))
        ):
            relative_count += 1
        else:
            absolute_count += 1

    total_imports = absolute_count + relative_count
    if total_imports == 0:
        return {"score": 100, "violations": []}

    abs_pct = round(100 * absolute_count / total_imports, 1)
    dominant_style = "absolute" if abs_pct >= 60 else "relative" if abs_pct <= 40 else "mixed"
    dominant_pct = (
        abs_pct if dominant_style == "absolute" else round(100 - abs_pct, 1) if dominant_style == "relative" else 50.0
    )

    if dominant_style == "mixed":
        return {"score": 100, "violations": []}

    # 2. Check changed files' import edges
    changed_edges = batched_in(
        conn,
        """SELECT fe.source_file_id, sf.path as source_path, tf.path as target_path
           FROM file_edges fe
           JOIN files sf ON fe.source_file_id = sf.id
           JOIN files tf ON fe.target_file_id = tf.id
           WHERE fe.kind = 'imports' AND fe.source_file_id IN ({ph})""",
        file_ids,
    )

    violations = []
    checked = 0
    for edge in changed_edges:
        checked += 1
        src_dir = (
            edge["source_path"].replace("\\", "/").rsplit("/", 1)[0]
            if "/" in edge["source_path"].replace("\\", "/")
            else ""
        )
        tgt_dir = (
            edge["target_path"].replace("\\", "/").rsplit("/", 1)[0]
            if "/" in edge["target_path"].replace("\\", "/")
            else ""
        )

        is_same_dir = (
            src_dir
            and tgt_dir
            and (src_dir == tgt_dir or src_dir.startswith(tgt_dir + "/") or tgt_dir.startswith(src_dir + "/"))
        )

        # If dominant is absolute but this is same-directory (relative-style)
        if dominant_style == "absolute" and is_same_dir:
            pass  # same-dir imports are fine even in absolute codebases
        elif dominant_style == "relative" and not is_same_dir:
            violations.append(
                {
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
            )

    if checked == 0:
        score = 100
    else:
        score = round(100 * (checked - len(violations)) / checked)
        score = max(0, min(100, score))

    return {"score": score, "violations": violations}


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
    error_candidates = conn.execute("""
        SELECT s.name, s.kind
        FROM symbols s
        WHERE (s.name LIKE '%Error%'
            OR s.name LIKE '%Exception%'
            OR s.name LIKE '%Failure%')
          AND s.kind IN ('class', 'struct', 'interface')
    """).fetchall()

    custom_error_count = sum(1 for r in error_candidates if _ERROR_NAME_RE.search(r["name"]))
    has_custom_errors = custom_error_count > 0

    # 2. Read changed files and check for bad patterns
    changed_files = batched_in(
        conn,
        "SELECT id, path FROM files WHERE id IN ({ph})",
        file_ids,
    )

    violations = []
    files_checked = 0
    issues_found = 0

    for frow in changed_files:
        fpath = root / frow["path"]
        if not fpath.exists():
            continue
        # Only check Python files for error handling (other languages have
        # different patterns)
        if not frow["path"].endswith(".py"):
            continue

        try:
            content = fpath.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue

        files_checked += 1
        _src_lines = content.split("\n")
        # Run the except-clause regexes on a copy with string/comment spans
        # blanked, so `except Exception:` text inside a docstring/fixture string
        # is not flagged; noqa detection still reads the ORIGINAL line.
        scan = _mask_py_strings_comments(content)

        def _noqa_at(line_num: int, codes: tuple[str, ...]) -> bool:
            return _has_noqa(
                _src_lines[line_num - 1] if 1 <= line_num <= len(_src_lines) else "",
                codes,
            )

        # Bare except
        for m in _BARE_EXCEPT_RE.finditer(scan):
            line_num = scan[: m.start()].count("\n") + 1
            if _noqa_at(line_num, ("E722",)):
                continue  # author marked it intended (# noqa: E722 / bare # noqa)
            issues_found += 1
            violations.append(
                {
                    "category": "error_handling",
                    "severity": SEVERITY_FAIL,
                    "file": frow["path"],
                    "line": line_num,
                    "message": (
                        "bare `except:` "
                        + (
                            f"(codebase has {custom_error_count} custom exception classes)"
                            if has_custom_errors
                            else "(use specific exceptions)"
                        )
                    ),
                    "fix": "Replace bare `except:` with a specific exception type",
                }
            )

        # Broad Exception catch
        for m in _BROAD_EXCEPT_RE.finditer(scan):
            line_num = scan[: m.start()].count("\n") + 1
            if _noqa_at(line_num, ("BLE001",)):
                continue  # deliberate broad-except resilience (# noqa: BLE001)
            issues_found += 1
            violations.append(
                {
                    "category": "error_handling",
                    "severity": SEVERITY_WARN,
                    "file": frow["path"],
                    "line": line_num,
                    "message": (
                        "broad `except Exception:` "
                        + (
                            f"(codebase has {custom_error_count} specific exception classes)"
                            if has_custom_errors
                            else "(consider catching specific exceptions)"
                        )
                    ),
                    "fix": "Narrow the exception type to catch only expected errors",
                }
            )

        # Silent exception swallowing (broad/bare only -- see _SILENT_EXCEPT_RE).
        for m in _SILENT_EXCEPT_RE.finditer(scan):
            line_num = scan[: m.start()].count("\n") + 1
            if _noqa_at(line_num, ("BLE001", "E722")):
                continue  # deliberately-acknowledged broad swallow
            issues_found += 1
            violations.append(
                {
                    "category": "error_handling",
                    "severity": SEVERITY_WARN,
                    "file": frow["path"],
                    "line": line_num,
                    "message": "broad silent exception swallow (no logging/re-raise)",
                    "fix": "Add logging or re-raise the exception instead of silently swallowing",
                }
            )

    # Score: based on ratio of issues to files checked
    if files_checked == 0:
        score = 100
    elif issues_found == 0:
        score = 100
    else:
        # Each issue deducts points; more issues = lower score
        penalty = min(issues_found * 15, 100)
        score = max(0, 100 - penalty)

    return {"score": score, "violations": violations}


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


def _check_duplicates(conn, file_ids: list[int]) -> dict:
    """Detect potential duplicate functions by comparing new symbols to existing ones.

    Uses name similarity (SequenceMatcher) and signature comparison to find
    symbols in changed files that may duplicate existing functionality.
    """
    if not file_ids:
        return {"score": 100, "violations": []}

    # 1. Get symbols from changed files
    new_symbols = batched_in(
        conn,
        """SELECT s.id, s.name, s.kind, s.signature, s.line_start,
                  f.path as file_path, f.file_role AS file_role
           FROM symbols s
           JOIN files f ON s.file_id = f.id
           WHERE s.file_id IN ({ph})
             AND s.kind IN ('function', 'method')""",
        file_ids,
    )

    if not new_symbols:
        return {"score": 100, "violations": []}

    # 2. Get all other functions/methods NOT in changed files
    existing_symbols = conn.execute("""
        SELECT s.id, s.name, s.kind, s.signature, s.line_start,
               f.path as file_path, f.file_role AS file_role
        FROM symbols s
        JOIN files f ON s.file_id = f.id
        WHERE s.kind IN ('function', 'method')
    """).fetchall()

    # Build lookup by name for fast filtering + a per-(role,name) distinct-file
    # count so a name shared across many files (an interface/ABC contract) is
    # not mistaken for duplication. Keyed by role so the contract count only
    # aggregates comparable code (we only ever compare within a role below).
    existing_by_name: dict[str, list] = defaultdict(list)
    _name_files: dict[tuple[str, str], set] = defaultdict(set)
    for sym in existing_symbols:
        existing_by_name[sym["name"].lower()].append(sym)
        _name_files[(sym["file_role"] or "", sym["name"].lower())].add(sym["file_path"])

    violations = []
    checked = 0

    new_ids = {s["id"] for s in new_symbols}

    # The similar-name pass costs ~(public changed symbols × distinct repo
    # names) Python iterations. On a hook-typical diff (1-3 files, a handful
    # of new symbols) that's negligible; on a sweeping refactor diff it
    # dominated the whole gate. Past the cap, keep the exact-name pass
    # (cheap, dict lookup) and skip similarity — disclosed in the result.
    _SIMILARITY_PASS_CAP = 150
    eligible = [s for s in new_symbols if len(s["name"]) >= 4 and not s["name"].startswith("_")]
    similarity_enabled = len(eligible) <= _SIMILARITY_PASS_CAP

    for new_sym in new_symbols:
        name = new_sym["name"]
        if len(name) < 4:
            continue
        if name.startswith("_"):
            continue
        checked += 1

        # Check for exact name matches in different files
        lower_name = name.lower()
        _role = new_sym["file_role"] or ""
        # Shared interface/ABC contract (defined in many same-role files) ->
        # not a duplicate; skip both the exact-name and similar-name checks.
        if len(_name_files.get((_role, lower_name), ())) >= _INTERFACE_CONTRACT_MIN_FILES:
            continue
        for existing in existing_by_name.get(lower_name, []):
            if existing["id"] in new_ids:
                continue
            if existing["file_path"] == new_sym["file_path"]:
                continue
            # Cross-role matches (src fn vs its test/script/ci namesake) are
            # expected mirroring, not duplication -- compare within a role only.
            if (existing["file_role"] or "") != _role:
                continue
            violations.append(
                {
                    "category": "duplicates",
                    "severity": SEVERITY_WARN,
                    "file": new_sym["file_path"],
                    "line": new_sym["line_start"],
                    "message": (
                        f"fn `{name}` has same name as "
                        f"`{existing['name']}` at {loc(existing['file_path'], existing['line_start'])}"
                    ),
                    "fix": f"Consider reusing `{existing['name']}` from {existing['file_path']}",
                }
            )
            break  # one match per new symbol is enough

        # Check for similar names (ratio > 0.8) in existing symbols
        if similarity_enabled and not any(v["symbol"] == name if "symbol" in v else False for v in violations):
            name_lower = name.lower()
            # The full ratio() against EVERY distinct repo name was the
            # whole-gate hot spot: a diff touching one large module ran tens
            # of millions of O(n*m) ratio() calls (~200s measured). The
            # documented difflib fast path fixes it: seq2 is set ONCE per
            # changed symbol (its preprocessing is cached), and the two
            # cheap upper bounds (real_quick_ratio = length math,
            # quick_ratio = char-multiset match) gate the expensive ratio()
            # — both are guaranteed >= ratio(), so no candidate is lost.
            candidates = []
            _sm = SequenceMatcher()
            _sm.set_seq2(name_lower)
            for existing_name, existing_list in existing_by_name.items():
                if abs(len(existing_name) - len(name_lower)) > 5:
                    continue
                # A name that contains (or is contained by) the other is a
                # deliberate variant -- `run_agent` ⊂ `run_agent_opt`, `name` ⊂
                # `_names`, `source_extensions` vs `source_to_test...` -- not a
                # duplicate. Substring relation = intentional naming family.
                if name_lower in existing_name or existing_name in name_lower:
                    continue
                _sm.set_seq1(existing_name)
                if _sm.real_quick_ratio() < 0.8 or _sm.quick_ratio() < 0.8:
                    continue
                ratio = _sm.ratio()
                if ratio >= 0.8 and ratio < 1.0:
                    for ex in existing_list:
                        if (
                            ex["id"] not in new_ids
                            and ex["file_path"] != new_sym["file_path"]
                            and (ex["file_role"] or "") == _role
                        ):
                            candidates.append((ex, ratio))
                            break

            if candidates:
                best = max(candidates, key=lambda x: x[1])
                existing, ratio = best
                violations.append(
                    {
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
                )

    if checked == 0:
        score = 100
    else:
        # Each duplicate deducts points
        fail_count = sum(1 for v in violations if v["severity"] == SEVERITY_FAIL)
        warn_count = sum(1 for v in violations if v["severity"] == SEVERITY_WARN)
        info_count = sum(1 for v in violations if v["severity"] == SEVERITY_INFO)
        penalty = fail_count * 20 + warn_count * 10 + info_count * 5
        score = max(0, 100 - penalty)

    result: dict = {"score": score, "violations": violations}
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

    changed_files = batched_in(
        conn,
        "SELECT id, path, language FROM files WHERE id IN ({ph})",
        file_ids,
    )

    violations = []
    files_checked = 0
    files_with_errors = 0
    parse_failures = 0

    try:
        from roam.index.parser import parse_file
    except ImportError:
        # If tree-sitter is not available the syntax check could not run.
        # W-Pattern2: do NOT silently score 100 (a fabricated perfect
        # verdict); mark the category unavailable so the composite scorer
        # treats it as a non-credit dimension rather than a passed gate.
        return {
            "score": 100,
            "violations": [],
            "available": False,
            "unavailable_reason": "tree-sitter parser unavailable -- syntax check did not run",
        }

    for frow in changed_files:
        fpath = root / frow["path"]
        if not fpath.exists():
            continue

        lang = frow["language"]
        if not lang or lang in _SYNTAX_SKIP_LANGS:
            continue

        if lang == "python":
            try:
                import ast

                ast.parse(fpath.read_text(encoding="utf-8"))
            except SyntaxError as exc:
                files_checked += 1
                files_with_errors += 1
                line_num = exc.lineno or 1
                violations.append(
                    {
                        "category": "syntax",
                        "severity": SEVERITY_FAIL,
                        "file": frow["path"],
                        "line": line_num,
                        "message": f"python syntax error at line {line_num}: {exc.msg}",
                        "fix": "Fix the Python syntax error indicated by ast.parse",
                    }
                )
                continue
            except OSError:
                parse_failures += 1
                violations.append(
                    {
                        "category": "syntax",
                        "severity": SEVERITY_INFO,
                        "file": frow["path"],
                        "line": None,
                        "message": f"could not read `{frow['path']}` -- syntax not verified",
                        "fix": "Verify the file can be read; this file was NOT syntax-checked",
                    }
                )
                continue

        try:
            result = parse_file(fpath, lang)
        except Exception:  # noqa: BLE001 — any parse crash = unverified file (W-Pattern2)
            # W-Pattern2: a crashed parse is NOT a clean file. Count it as
            # a parse failure and surface it -- never credit it as checked.
            parse_failures += 1
            violations.append(
                {
                    "category": "syntax",
                    "severity": SEVERITY_INFO,
                    "file": frow["path"],
                    "line": None,
                    "message": f"could not parse `{frow['path']}` -- syntax not verified",
                    "fix": "Verify the file parses; this file was NOT syntax-checked",
                }
            )
            continue

        # parse_file returns (tree, source_bytes, language) or (None, None,
        # None). For a CODE language (data/markup already skipped above via
        # _SYNTAX_SKIP_LANGS), tree-sitter is error-TOLERANT -- a broken file
        # still yields a tree with ERROR nodes. So None here means the file was
        # genuinely not verified (W-Pattern2): disclose it, never credit it as
        # clean.
        if result is None or result[0] is None:
            parse_failures += 1
            violations.append(
                {
                    "category": "syntax",
                    "severity": SEVERITY_INFO,
                    "file": frow["path"],
                    "line": None,
                    "message": f"could not parse `{frow['path']}` -- syntax not verified",
                    "fix": "Verify the file parses; this file was NOT syntax-checked",
                }
            )
            continue

        files_checked += 1
        tree = result[0]

        error_nodes = _find_error_nodes(tree.root_node)
        if error_nodes:
            files_with_errors += 1
            for node in error_nodes[:5]:  # Cap per-file error reports
                line_num = node.start_point[0] + 1
                violations.append(
                    {
                        "category": "syntax",
                        "severity": SEVERITY_FAIL,
                        "file": frow["path"],
                        "line": line_num,
                        "message": f"syntax error at line {line_num}",
                        "fix": "Fix the syntax error indicated by the parser",
                    }
                )

    if files_checked == 0:
        score = 100
    elif files_with_errors == 0:
        score = 100
    else:
        score = round(100 * (files_checked - files_with_errors) / files_checked)
        score = max(0, min(100, score))

    result_dict: dict = {"score": score, "violations": violations}
    if parse_failures > 0:
        # W-Pattern2: disclose that some files were not actually verified.
        result_dict["parse_failures"] = parse_failures
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
            is_composable_container = (
                r["language"] or ""
            ).lower() in _COMPOSABLE_LANGS and _COMPOSABLE_CONTAINER_RE.match(r["name"] or "")
            if is_composable_container:
                violations.append(
                    {
                        "category": "complexity",
                        "severity": SEVERITY_INFO,
                        "file": r["file_path"],
                        "line": r["line_start"],
                        "message": (
                            f"composable `{r['name']}` container complexity {round(cc)} — sum over its "
                            f"inner closures, advisory only (container idiom, not per-function load)"
                        ),
                        "symbol": r["name"],
                        "cognitive_complexity": round(cc),
                        "fix": (
                            f"Review the inner closures of `{r['name']}` individually; extract only "
                            f"closures that don't share refs"
                        ),
                    }
                )
                continue
            violations.append(
                {
                    "category": "complexity",
                    "severity": SEVERITY_FAIL if cc >= _COMPLEXITY_FAIL else SEVERITY_WARN,
                    "file": r["file_path"],
                    "line": r["line_start"],
                    "message": (f"fn `{r['name']}` cognitive complexity {round(cc)} (threshold {threshold})"),
                    "symbol": r["name"],
                    "cognitive_complexity": round(cc),
                    "fix": (f"Decompose `{r['name']}` — extract helpers / flatten nesting to lower cognitive load"),
                }
            )
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


def _collect_cycle_violations(graph, changed_set: set[str]) -> list[dict]:
    """One WARN per changed file that sits in a SMALL import cycle (2..8 files)."""
    import networkx as nx

    violations: list[dict] = []
    seen: set[str] = set()
    for scc in nx.strongly_connected_components(graph):
        if not (2 <= len(scc) <= _MAX_ACTIONABLE_CYCLE):
            continue
        for f in sorted(changed_set & scc):
            if f in seen:
                continue
            seen.add(f)
            others = sorted(scc - {f})
            tail = "..." if len(others) > 3 else ""
            violations.append(
                {
                    "category": "cycles",
                    "severity": SEVERITY_WARN,
                    "file": f,
                    "line": None,
                    "message": (
                        f"`{f}` is in an import cycle of {len(scc)} files (with {', '.join(others[:3])}{tail})"
                    ),
                    "fix": ("Break the cycle — invert one dependency or extract the shared piece into a new module"),
                }
            )
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
    for rel in changed_paths:
        norm = rel.replace("\\", "/")
        if not norm.endswith(_SECRETS_SCAN_EXTENSIONS):
            continue
        fpath = root / rel
        if not fpath.is_file():
            continue
        try:
            text = fpath.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        checked += 1
        per_file = 0
        scan_repo_patterns = bool(repo_patterns) and (repo_should_scan is None or repo_should_scan(norm))
        for line_no, line in enumerate(text.splitlines(), start=1):
            if per_file >= _SECRETS_MAX_PER_FILE:
                break
            hit = None
            for pat in SECRET_PATTERNS:
                if pat.search(line):
                    hit = (
                        SEVERITY_FAIL,
                        f"credential-shaped string ({pattern_id(pat)}) in `{norm}`",
                        "Remove the credential and rotate it; load secrets from the environment instead",
                    )
                    break
            if hit is None and scan_repo_patterns:
                for name, pat in repo_patterns:
                    if pat.search(line):
                        hit = (
                            SEVERITY_WARN,
                            f"repo-forbidden pattern [{name}] in `{norm}`",
                            f"Reword the line — [{name}] is on this repo's never-publish list "
                            f"({_LEAK_PATTERNS_FILENAME})",
                        )
                        break
            if hit is not None:
                severity, message, fix = hit
                violations.append(
                    {
                        "category": "secrets",
                        "severity": severity,
                        "file": norm,
                        "line": line_no,
                        "message": message,
                        "fix": fix,
                    }
                )
                per_file += 1

    if checked == 0 or not violations:
        score = 100
    else:
        penalty = sum(25 if v["severity"] == SEVERITY_FAIL else 8 for v in violations)
        score = max(0, 100 - penalty)
    result: dict = {"score": score, "violations": violations}
    if repo_error:
        # Pattern 2 — the repo catalogue did NOT run; disclose, never
        # silently pass as if it had.
        result["repo_patterns_error"] = repo_error
    if repo_patterns:
        result["repo_pattern_count"] = len(repo_patterns)
    return result


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


def _gather_and_rank_tests(conn, sym_ids, src_paths, changed_paths, root):
    """Rank impacted test files by relevance and return ``(ordered, unavailable)``.

    ``ordered`` is the de-duped, existence-checked list of impacted ``.py`` test
    files, most-relevant first (a changed test > DIRECT caller > colocated).
    ``unavailable`` is None on success, else the ready-to-return result dict
    describing why test discovery could not run.
    """
    try:
        from roam.commands.cmd_affected_tests import _gather_affected_tests
    except Exception:  # noqa: BLE001
        return [], {
            "score": 100,
            "violations": [],
            "available": False,
            "unavailable_reason": "affected-tests helper unavailable",
        }

    ranked: list[tuple[int, int, str]] = []
    try:
        for e in _gather_affected_tests(conn, sym_ids, src_paths):
            f = e.get("file")
            if not (f and f.endswith(".py")):
                continue
            pri = {"DIRECT": 1, "COLOCATED": 2}.get(e.get("kind"), 3)
            ranked.append((pri, int(e.get("hops") or 9), f.replace("\\", "/")))
    except Exception as exc:  # noqa: BLE001 — never let test-discovery break the gate
        return [], {
            "score": 100,
            "violations": [],
            "available": False,
            "unavailable_reason": f"affected-tests discovery failed: {exc!r}",
        }

    for p in changed_paths:  # a changed test file is always most relevant
        if is_test_file(p) and p.endswith(".py"):
            ranked.append((0, 0, p.replace("\\", "/")))
    ranked.sort()

    seen: set[str] = set()
    ordered: list[str] = []
    for _pri, _hops, f in ranked:
        if f not in seen and (root / f).exists():
            seen.add(f)
            ordered.append(f)
    return ordered, None


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
        "py-except-pass",
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
    raw: list = []
    set_idiom_scope(file_ids)
    try:
        for task_id, _way, fn in applicable_idiom_detectors(scanned):
            if task_id not in _DEEP_IDIOM_DENY:
                raw.extend(_safe_run_idiom(fn, task_id, conn))
    finally:
        set_idiom_scope(None)
    # JS/TS edits fire the JS idiom pack the same content-driven way
    # (2026-06-11 — the pack landed after the Python wiring above; this keeps
    # the deep sweep language-honest instead of silently Python-only).
    try:
        from roam.catalog.js_idioms import applicable_js_idiom_detectors
        from roam.catalog.js_idioms import set_idiom_scope as set_js_idiom_scope
    except Exception as exc:  # noqa: BLE001 — deep mode is optional/advisory
        from roam.observability import log_swallowed

        log_swallowed("verify.deep.js_import", exc)
        return raw
    set_js_idiom_scope(file_ids)
    try:
        for task_id, _way, fn in applicable_js_idiom_detectors(scanned):
            if task_id not in _DEEP_IDIOM_DENY:
                raw.extend(_safe_run_idiom(fn, task_id, conn))
    finally:
        set_js_idiom_scope(None)
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
    help="Comma-list to run: naming,imports,error_handling,duplicates,syntax. Default: all (or .roam/verify.yaml).",
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
    json_mode = ctx.obj.get("json") if ctx.obj else False
    token_budget = ctx.obj.get("budget", 0) if ctx.obj else 0
    root = find_project_root()

    # --on/--off — persist the stop/start toggle and exit. No index needed.
    if set_on or set_off:
        enabled = bool(set_on) and not set_off
        cfg_path = write_verify_enabled(root, enabled)
        state = "ON (verify will run)" if enabled else "OFF (verify disabled)"
        if json_mode:
            click.echo(
                to_json(
                    json_envelope(
                        "verify", summary={"verdict": "CONFIG", "enabled": enabled, "config_path": str(cfg_path)}
                    )
                )
            )
        else:
            click.echo(f"VERDICT: verify {state} -- wrote {cfg_path}")
        return

    cfg = load_verify_config(root)
    # Resolution: the ROAM_COMPILE_VERIFY env (a host-platform Verify toggle /
    # per-invocation control) OVERRIDES .roam/verify.yaml. '1/true/on/yes' →
    # force run; '0/false/off/no' → force skip; unset → honor the file's
    # `enabled`. Lets the server auto-run verify on toggle without flipping the
    # repo's persistent config (mirrors cmd_compile._resolve_verify_enabled).
    import os as _os

    _vraw = (_os.environ.get("ROAM_COMPILE_VERIFY") or "").strip().lower()
    if _vraw in ("1", "true", "on", "yes"):
        _verify_enabled = True
    elif _vraw in ("0", "false", "off", "no"):
        _verify_enabled = False
    else:
        _verify_enabled = bool(cfg.get("enabled", True))
    # NOT forced: a user can disable verify entirely (`roam verify --off`).
    if not _verify_enabled:
        msg = "verify disabled in .roam/verify.yaml (enabled: false) -- run `roam verify --on` to resume"
        if json_mode:
            click.echo(
                to_json(json_envelope("verify", summary={"verdict": "SKIPPED", "enabled": False, "reason": msg}))
            )
        else:
            click.echo(f"VERDICT: SKIPPED -- {msg}")
        return

    ensure_index()

    # Threshold: CLI flag > config > default 70.
    if threshold is None:
        threshold = cfg.get("threshold") if cfg.get("threshold") is not None else 70

    # Resolve target files
    if files:
        target_paths = [f.replace("\\", "/") for f in files]
        target_paths = _expand_dir_targets(target_paths, root)
    else:
        # Default behavior: use git diff changed files
        target_paths = get_changed_files(root)

    # Select WHICH checks run (freedom): --checks > --auto/config.auto > config > all.
    selected = resolve_selected_checks(checks_opt, auto, cfg, target_paths)

    # --auto implies the DEEP advisory sweep: the algorithm/idiom detectors
    # scoped to the touched files (~1-2s, content-driven, never gates).
    # The catalog's Current/Better/Fix findings are exactly the post-edit
    # signal the hook loop exists to surface. ROAM_VERIFY_NO_DEEP=1 opts out
    # (e.g. minimal CI environments).
    if auto and not deep and os.environ.get("ROAM_VERIFY_NO_DEEP") not in ("1", "true", "yes"):
        deep = True

    if report:
        # REPORT mode: whole-repo scan (unless a path was given), all static
        # checks, NON-gating. Skip the executable `tests` check (too heavy for a
        # scan) and force diff scoping off so findings span whole files.
        if not files:
            target_paths = _all_source_paths(root)
        if not checks_opt:
            selected = [c for c in _ALL_CHECKS if c != "tests"]
        diff_only = False

    if not target_paths:
        # No changed files
        score = 100
        verdict = "PASS"
        _verify_empty_envelope = json_envelope(
            "verify",
            summary={
                "verdict": verdict,
                "score": score,
                "threshold": threshold,
                "files_checked": 0,
                "violation_count": 0,
            },
            categories={cat: {"score": 100, "violations": []} for cat in _CATEGORY_WEIGHTS},
            violations=[],
        )
        auto_log(_verify_empty_envelope, action="verify", target="")
        if json_mode:
            click.echo(to_json(_verify_empty_envelope))
            return
        click.echo(f"VERDICT: {verdict} (score {score}/100) -- no changed files")
        return

    # New OR just-edited targets may not be reflected in the index — `ensure_index()`
    # above is a no-op when the DB already exists, and verify reads symbols FROM the
    # DB, so it would check STALE symbols: a freshly-written file resolves to nothing
    # (files_checked=0 → false-green PASS), and a newly-added symbol inside an
    # already-indexed file is invisible (the most common agent edit — the file is in
    # the DB so an absence check passes, but its new symbols aren't). Re-index when
    # any target is added or content-modified vs the DB. `get_changed_files` is the
    # indexer's own detector (mtime fast-path + sha256 fallback) and only hashes the
    # targets whose mtime moved, so this is cheap; the incremental run then re-parses
    # only genuinely-changed files (~0.5s for a handful).
    try:
        # Aliased: `changed_files.get_changed_files` is already imported at module
        # top and called above (line ~1203). A bare `from ... import
        # get_changed_files` here would make the name function-local for the WHOLE
        # `verify` scope → the earlier call hits UnboundLocalError (bare
        # `roam verify` crashed before this alias). Different function, same name.
        from roam.index.incremental import get_changed_files as _incremental_changed_files

        with open_db(readonly=True) as _idx_conn:
            _on_disk = [p for p in target_paths if (root / p).exists()]
            _added, _modified, _ = _incremental_changed_files(_idx_conn, _on_disk, root)
        if _added or _modified:
            from roam.index.indexer import Indexer

            # light=True: refresh the changed files' symbols + edges only, skipping
            # the O(repo) metric phases (effects/taint ~113s, graph metrics, git,
            # health, search) that verify's checks never read. ~150s → ~2-3s on a
            # large working tree; the structural data verify needs is fully fresh.
            Indexer().run(quiet=True, progress_bar=False, light=True)
    except Exception as exc:  # noqa: BLE001 — best-effort; never break verify
        from roam.observability import log_swallowed

        log_swallowed("verify.index_stale_targets", exc)

    with open_db(readonly=True) as conn:
        # Map paths to file IDs
        file_map = resolve_changed_to_db(conn, target_paths)
        file_ids = list(file_map.values())

        # Run only the SELECTED checks; unselected ones are marked skipped
        # (score 100, excluded from the renormalized composite).
        def _run_check(name: str, fn):
            if name in selected:
                return fn()
            return {"score": 100, "violations": [], "skipped": True}

        naming_result = _run_check("naming", lambda: _check_naming(conn, file_ids))
        imports_result = _run_check("imports", lambda: _check_imports(conn, file_ids))
        error_result = _run_check("error_handling", lambda: _check_error_handling(conn, file_ids, root))
        duplicates_result = _run_check("duplicates", lambda: _check_duplicates(conn, file_ids))
        syntax_result = _run_check("syntax", lambda: _check_syntax(conn, file_ids, root))
        complexity_result = _run_check("complexity", lambda: _check_complexity(conn, file_ids))
        cycles_result = _run_check("cycles", lambda: _check_cycles(conn, file_ids, target_paths))
        tests_result = _run_check("tests", lambda: _check_tests(conn, file_ids, target_paths, root))
        import_side_effects_result = _run_check(
            "import_side_effects", lambda: _check_import_side_effects(conn, file_ids, root)
        )
        secrets_result = _run_check("secrets", lambda: _check_secrets(target_paths, root))

        categories = {
            "naming": naming_result,
            "imports": imports_result,
            "error_handling": error_result,
            "duplicates": duplicates_result,
            "syntax": syntax_result,
            "complexity": complexity_result,
            "cycles": cycles_result,
            "tests": tests_result,
            "import_side_effects": import_side_effects_result,
            "secrets": secrets_result,
        }

        # --deep: advisory algorithm/idiom anti-patterns scoped to the target
        # files. Added to `categories` (so it flows through suppression / baseline
        # / diff scoping and surfaces to the agent) but deliberately NOT added to
        # `selected`, so it never moves the PASS/FAIL composite verdict.
        if deep:
            categories["patterns"] = _run_deep_patterns(conn, file_ids)

        # Composite score over the selected checks only.
        score = _compute_composite(categories, selected)
        verdict = _compute_verdict(score)
        # Verdict floor: a FAIL-severity secrets finding (credential-shaped
        # string in a tracked file) can never be averaged into a PASS — the
        # quiet-on-pass hook loop would swallow it. WARN keeps the gate
        # advisory (fail-open philosophy) while guaranteeing it SURFACES.
        secrets_fails = sum(
            1 for v in (categories.get("secrets", {}).get("violations") or []) if v.get("severity") == SEVERITY_FAIL
        )
        if verdict == "PASS" and secrets_fails:
            verdict = "WARN"
            score = min(score, 79)

        # W-Pattern2: detect degraded sub-checks -- a crashed parse or an
        # unavailable syntax category means part of the gate did NOT run.
        # The composite verdict must disclose that rather than reporting a
        # clean PASS indistinguishable from a fully-verified one.
        syntax_parse_failures = categories.get("syntax", {}).get("parse_failures", 0)
        syntax_unavailable = categories.get("syntax", {}).get("available", True) is False
        degraded = syntax_parse_failures > 0 or syntax_unavailable
        if degraded:
            qualifiers = []
            if syntax_parse_failures > 0:
                qualifiers.append(
                    f"{syntax_parse_failures} file{'s' if syntax_parse_failures != 1 else ''} "
                    "not syntax-checked (parse failed)"
                )
            if syntax_unavailable:
                qualifiers.append("syntax check unavailable (tree-sitter parser missing)")
            verdict = f"{verdict} -- {'; '.join(qualifiers)}"

        # Flatten all violations
        all_violations = []
        for cat_result in categories.values():
            all_violations.extend(cat_result.get("violations", []))

        # Advisory categories (the `--deep` `patterns` sweep) SURFACE findings but
        # must never gate the verdict — mirrors _compute_composite, which omits
        # them from the weighted score. The diff-only / suppression recompute
        # paths below re-derive verdict+score from the surviving violation set, so
        # they must score from the GATING subset (advisory findings excluded)
        # while the full set still flows to the output. Without this, a single
        # INFO `patterns` finding on a changed line flipped PASS/100 → WARN/95.
        _advisory_cats = {name for name, res in categories.items() if res.get("advisory")}

        def _gating(viols):
            """Violations that may move the verdict (advisory categories excluded)."""
            return [v for v in viols if v.get("category") not in _advisory_cats]

        # Honor `.roam-suppressions.yml` (rule=category, file, optional line):
        # INTENDED findings (e.g. a deliberate broad-except resilience pattern)
        # can be ACKNOWLEDGED so they stop re-surfacing, keeping the signal sharp
        # on genuinely-NEW debt. Transparent (Pattern 2) — the count is reported,
        # never silently hidden. `roam suppress`/`.roam-suppressions.yml` is the
        # acknowledge sink the auto-correct dogfood loop needs.
        suppressed_count = 0
        try:
            from roam.commands.suppression import is_suppressed, load_suppressions

            _sups = load_suppressions(root)
            if _sups:

                def _is_sup(v):
                    return is_suppressed(
                        _sups,
                        v.get("category", ""),
                        v.get("file", ""),
                        v.get("line"),
                        symbol=v.get("symbol"),
                    )

                suppressed_count = sum(1 for v in all_violations if _is_sup(v))
                if suppressed_count:
                    all_violations = [v for v in all_violations if not _is_sup(v)]
                    for _cat in categories.values():
                        _vs = _cat.get("violations")
                        if _vs:
                            _cat["violations"] = [v for v in _vs if not _is_sup(v)]
        except Exception as exc:  # noqa: BLE001 — suppression must never break the gate
            from roam.observability import log_swallowed

            log_swallowed("verify.suppressions", exc)

        # --baseline-write: snapshot the current (post-suppression) findings as
        # accepted debt and exit. Captures everything currently flagged so a
        # later `--new-only` run shows only genuinely-new findings.
        if baseline_write:
            _written = _write_verify_baseline(all_violations, root)
            _bl_env = json_envelope(
                "verify",
                summary={
                    "verdict": "BASELINE_WRITTEN",
                    "baseline_written": _written,
                    "baseline_path": str(_verify_baseline_path(root)),
                },
                violations=[],
            )
            if json_mode:
                click.echo(to_json(_bl_env))
                return
            click.echo(
                f"VERDICT: BASELINE_WRITTEN -- {_written} finding"
                f"{'s' if _written != 1 else ''} accepted "
                f"({_verify_baseline_path(root)})"
            )
            return

        # --new-only: filter against the accepted-debt baseline by IDENTITY
        # (line-shift tolerant fingerprint), leaving only findings the baseline
        # did not record. Composes with --diff-only below (position scoping).
        baselined_count = 0
        baseline_state = None
        if new_only:
            _base = _load_verify_baseline(root)
            if _base is None:
                baseline_state = "absent"  # no baseline → everything is new
            else:
                baseline_state = "applied"
                _remaining = dict(_base)
                _line_cache: dict = {}
                _kept_ids = set()
                for v in all_violations:
                    fp = _finding_fingerprint(v, _line_cache, root)
                    if _remaining.get(fp, 0) > 0:
                        _remaining[fp] -= 1
                        baselined_count += 1
                    else:
                        _kept_ids.add(id(v))
                if baselined_count:
                    all_violations = [v for v in all_violations if id(v) in _kept_ids]
                    for _cat in categories.values():
                        _vs = _cat.get("violations")
                        if _vs:
                            _cat["violations"] = [v for v in _vs if id(v) in _kept_ids]

        # --diff-only: scope to lines changed vs HEAD so the verdict reflects
        # the EDIT, not the file's accumulated debt. Files with no tracked diff
        # keep all their violations (no baseline). Overrides verdict + score.
        diff_scoped = False
        _explicit_changed = _parse_changed_lines(changed_lines) if changed_lines else None
        if (diff_only or _explicit_changed) and all_violations:
            _vfiles = {v.get("file") for v in all_violations if v.get("file")}
            # Explicit ranges (caller knows exactly what it changed) override the
            # git-diff-vs-HEAD baseline, which is noisy on a big uncommitted tree.
            _changed = _explicit_changed if _explicit_changed is not None else _changed_line_ranges(_vfiles, root)
            if _changed:

                def _on_changed_line(v):
                    f = v.get("file")
                    if f not in _changed:
                        return True  # no baseline for this file → keep
                    ln = v.get("line")
                    return ln is not None and ln in _changed[f]

                all_violations = [v for v in all_violations if _on_changed_line(v)]
                for _cat in categories.values():
                    if _cat.get("violations"):
                        _cat["violations"] = [v for v in _cat["violations"] if _on_changed_line(v)]
                diff_scoped = True
                _gv = _gating(all_violations)
                if not _gv:
                    verdict, score = "PASS", 100
                else:
                    # FAIL only when the edit broke parsing (syntax); other
                    # categories are quality WARNs, not blockers.
                    _broke = any(v.get("category") == "syntax" for v in _gv)
                    verdict = "FAIL" if _broke else "WARN"
                    score = max(0, 100 - len(_gv) * 5)

        # If suppression or the baseline reduced the surfaced set but position
        # scoping (--diff-only, which recomputes inline above) did NOT run, the
        # verdict/score were computed from the RAW pre-filter categories and now
        # overstate the problem -- a fully-baselined or fully-suppressed file is
        # a PASS, not the raw file's FAIL. Recompute from what REMAINS, using the
        # same rule --diff-only uses (FAIL only when the surviving set broke
        # parsing; other categories are quality WARNs).
        if (suppressed_count or baselined_count) and not diff_scoped:
            _gv = _gating(all_violations)
            if not _gv:
                verdict, score = "PASS", 100
            else:
                _broke = any(v.get("category") == "syntax" for v in _gv)
                verdict = "FAIL" if _broke else "WARN"
                score = max(0, 100 - len(_gv) * 5)

        violation_count = len(all_violations)
        files_checked = len(file_map)

        # Tier-1 blast-radius weighting: annotate each finding with its file's
        # caller count and surface the widely-depended-on ones first. Pure
        # ranking + annotation — the verdict/score/exit code were finalized
        # above (_gating); this never changes them.
        _blast = _blast_radius_by_file(conn, {v.get("file") for v in all_violations if v.get("file")})
        max_blast_radius = 0
        for v in all_violations:
            _br = _blast.get(v.get("file"), 0)
            v["blast_radius"] = _br
            if _br > max_blast_radius:
                max_blast_radius = _br
        all_violations.sort(
            key=lambda v: (
                _SEVERITY_ORDER.get(v.get("severity"), 9),
                -int(v.get("blast_radius") or 0),
                v.get("file") or "",
                v.get("line") or 0,
            )
        )

        # --severity: DISPLAY filter only. Verdict/score/violation_count are
        # already computed above from the FULL set; this just narrows the
        # findings list shown/returned (cuts the report's noise floor).
        _severity_full_count = len(all_violations)
        if severity:
            _sev_rank = {"fail": 0, "warn": 1, "info": 2}[severity.lower()]
            all_violations = [v for v in all_violations if _SEVERITY_ORDER.get(v.get("severity"), 9) <= _sev_rank]

        # Build category summary (used by JSON output + auto-log).
        cat_summary = {}
        for cat_name, cat_result in categories.items():
            entry = {
                "score": cat_result["score"],
                "violation_count": len(cat_result.get("violations", [])),
                "violations": cat_result.get("violations", []),
            }
            # W-Pattern2: surface degraded-path disclosure on the affected
            # category only -- keeps the healthy-path envelope unchanged.
            if cat_result.get("parse_failures", 0) > 0:
                entry["parse_failures"] = cat_result["parse_failures"]
            if cat_result.get("available", True) is False:
                entry["available"] = False
                if cat_result.get("unavailable_reason"):
                    entry["unavailable_reason"] = cat_result["unavailable_reason"]
            cat_summary[cat_name] = entry

        verify_summary = {
            "verdict": verdict,
            "score": score,
            "threshold": threshold,
            "files_checked": files_checked,
            "violation_count": violation_count,
            "checks_run": selected,
        }
        if degraded:
            verify_summary["partial_success"] = True
        if severity:
            verify_summary["severity_filter"] = severity.lower()
            verify_summary["shown_count"] = len(all_violations)
            verify_summary["total_count"] = _severity_full_count
        if suppressed_count:
            verify_summary["suppressed"] = suppressed_count
        if diff_scoped:
            verify_summary["diff_scoped"] = True
        if baseline_state:
            verify_summary["baseline"] = baseline_state
            if baselined_count:
                verify_summary["baselined"] = baselined_count
        if max_blast_radius:
            verify_summary["max_blast_radius"] = max_blast_radius
            verify_summary["blast_radius_definition"] = (
                "MAX caller count (graph_metrics.in_degree) among symbols in a "
                "finding's file; findings sorted by severity then blast_radius"
            )

        verify_envelope = json_envelope(
            "verify",
            summary=verify_summary,
            categories=cat_summary,
            violations=all_violations,
            budget=token_budget,
        )
        # Auto-log into the active run; target is the first file path or
        # empty when the gate ran on the staged set.
        _verify_target = (target_paths[0] if target_paths else "") or ""
        auto_log(verify_envelope, action="verify", target=_verify_target)

        # REPORT mode is non-gating: render the ranked punch-list and exit 0,
        # skipping the PASS/FAIL gate below.
        if report:
            if persist:
                _persist_verify_report(verify_envelope, all_violations, out, root, json_mode)
            else:
                _render_verify_report(verify_envelope, all_violations, json_mode)
            return

        # JSON output
        if json_mode:
            click.echo(to_json(verify_envelope))

            if score < threshold:
                ctx.exit(EXIT_GATE_FAILURE)
            return

        # Text output
        click.echo(
            f"VERDICT: {verdict} (score {score}/100) "
            f"-- {violation_count} issue{'s' if violation_count != 1 else ''} "
            f"in {files_checked} changed file{'s' if files_checked != 1 else ''}"
        )
        if len(selected) != len(_ALL_CHECKS):
            click.echo(
                f"checks: {', '.join(selected)} (skipped: {', '.join(c for c in _ALL_CHECKS if c not in selected)})"
            )
        click.echo("")

        # Naming
        _print_category("NAMING", naming_result, fix_suggestions)

        # Imports
        _print_category("IMPORTS", imports_result, fix_suggestions)

        # Error handling
        _print_category("ERROR HANDLING", error_result, fix_suggestions)

        # Duplicates
        _print_category("DUPLICATES", duplicates_result, fix_suggestions)

        # Syntax
        _print_category("SYNTAX", syntax_result, fix_suggestions)

        # Complexity (KISS) + import cycles (architecture) + the executable signal
        _print_category("COMPLEXITY", complexity_result, fix_suggestions)
        _print_category("CYCLES", cycles_result, fix_suggestions)
        _print_category("TESTS", tests_result, fix_suggestions)

        # Summary line
        gate_result = "PASS" if score >= threshold else "FAIL"
        click.echo(f"\nOverall: {score}/100 (threshold: {threshold}) -- {gate_result}")

        if score < threshold:
            ctx.exit(EXIT_GATE_FAILURE)


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
