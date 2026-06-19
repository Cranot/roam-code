"""Detect AI code anti-patterns and compute AI rot score.

The vibe-check command scans for 10 categories of AI-generated code smells:

1. Dead exports / orphaned symbols — public symbols with zero callers
2. Short-term churn — files revised heavily within 14 days
3. Empty error handlers — try/catch with empty or trivial bodies
4. Abandoned stubs — functions with pass/TODO/NotImplementedError bodies
5. Hallucinated imports — unresolvable references
6. Error handling inconsistency — mixed patterns in same module
7. Comment density anomalies — files with outlier comment ratios
8. Copy-paste functions — duplicate normalized function bodies
9. Modular Mirage — exported helpers with exactly 1 caller (W371)
10. Boilerplate Inflation — comment-restates-code + shallow wrappers (W371)

Patterns 1-8 contribute to the canonical 0-100 "AI rot score". Patterns
9 and 10 (W371) emit findings into the registry but do NOT alter the
canonical score — they are informational layers added on top of the
existing 8-pattern composite so downstream consumers reading the
``ai_rot_score`` see the same number before and after W371.

W371 research backing:

* Modular Mirage — Zhu, Tsantalis, Rigby, "AI-Generated Smells: An
  Analysis of Code and Architecture in LLM- and Agent-Driven
  Development" (arxiv:2605.02741). Defines the pattern as agents
  achieving "superficial structural modularity (file separation) but
  fail[ing] to create semantic cohesion" — operationalised here as
  exported symbols with a single inbound caller (the helper exists as
  if it were a reusable abstraction, but there's exactly one consumer).
* Boilerplate Inflation — variant of the redundant-implementation
  smell discussed in the same corpus + the LLM-code-smell catalog
  (arxiv:2512.18020). Operationalised here as comment-restates-code
  occurrences and shallow Python wrappers (docstring + single
  delegation line).

Counts are at occurrence level (not function level) and span all
indexed languages. For a Python-only, function-level, actionable list
of related exception handling issues, use ``roam math --task py-bare-except``
or ``roam math --task py-broad-except``. The two numbers will not match by
design — vibe-check is a coarse health signal.

Output formats: text (default), ``--json``. SARIF is deliberately NOT
emitted because vibe-check outputs are invocation-scoped aggregate AI-rot
scores (composite 0-100 + per-pattern rates) — not per-location findings.
Individual per-pattern findings DO persist to the findings registry per
W125 and are queryable via ``roam findings list --detector vibe-check``;
that path is orthogonal to vibe-check's CLI rollup output. See action.yml
_SUPPORTED_SARIF allowlist and W1170 audit memo.
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from collections import defaultdict
from pathlib import Path

import click

from roam.capability import roam_capability
from roam.commands.resolve import ensure_index
from roam.db.connection import find_project_root, open_db
from roam.output.formatter import format_table, json_envelope, to_json
from roam.quality.ai_rot import DEFINITION as AI_ROT_DEFINITION

# W125 — detector version stamp. Bump per the W81 / ROADMAP A6 rules when
# the pattern set, confidence-tier mapping, or evidence-shape changes
# meaningfully. Re-using the W81 module-level pattern (DEAD_DETECTOR_VERSION,
# CLONES_DETECTOR_VERSION) so consumers can ``import VIBE_CHECK_DETECTOR_VERSION``
# the same way.
#
# W1256: kept as the "composite" stamp (legacy). External consumers that
# import this constant continue to read a single string; new emits use the
# per-pattern stamps below so an FP-rate fix on one pattern can bump that
# pattern's version without ripple to the other nine.
VIBE_CHECK_DETECTOR_VERSION: str = "1.0.0"

# W1256: per-pattern version stamps. Bump the pattern's stamp (not the
# composite) when its predicate / threshold / claim shape / evidence shape
# changes. Mirrors cmd_smells.py's per-kind constants (REFUSED_BEQUEST_*,
# PRIMITIVE_OBSESSION_*, ...). Same call-site discipline as W81 — the
# stamps live alongside the call site, NOT in src/roam/catalog/versions.py
# (which is reserved for the task_id-keyed algorithm-catalog registry).
#
# All ten initial stamps are "1.0.0" so the persist rows stay byte-identical
# to pre-W1256 emits (the existing tests/test_findings_vibe_check.py
# regression assertion ``source_version == VIBE_CHECK_DETECTOR_VERSION``
# continues to hold until the first divergence).
DEAD_EXPORTS_DETECTOR_VERSION: str = "1.0.0"
SHORT_CHURN_DETECTOR_VERSION: str = "1.0.0"
EMPTY_HANDLERS_DETECTOR_VERSION: str = "1.0.0"
ABANDONED_STUBS_DETECTOR_VERSION: str = "1.0.0"
HALLUCINATED_IMPORTS_DETECTOR_VERSION: str = "1.0.0"
ERROR_INCONSISTENCY_DETECTOR_VERSION: str = "1.0.0"
COMMENT_ANOMALIES_DETECTOR_VERSION: str = "1.0.0"
COPY_PASTE_DETECTOR_VERSION: str = "1.0.0"
# W371 informational patterns (do not contribute to AI rot score).
MODULAR_MIRAGE_DETECTOR_VERSION: str = "1.0.0"
BOILERPLATE_INFLATION_DETECTOR_VERSION: str = "1.0.0"

# W1256: lookup table consumed by ``_emit_vibe_check_findings`` so each
# finding row stamps its pattern's own version. Unknown kinds fall back to
# the composite (defensive — same behaviour as ``_vibe_check_tier``).
_VIBE_KIND_TO_VERSION: dict[str, str] = {
    "dead_exports": DEAD_EXPORTS_DETECTOR_VERSION,
    "short_churn": SHORT_CHURN_DETECTOR_VERSION,
    "empty_handlers": EMPTY_HANDLERS_DETECTOR_VERSION,
    "abandoned_stubs": ABANDONED_STUBS_DETECTOR_VERSION,
    "hallucinated_imports": HALLUCINATED_IMPORTS_DETECTOR_VERSION,
    "error_inconsistency": ERROR_INCONSISTENCY_DETECTOR_VERSION,
    "comment_anomalies": COMMENT_ANOMALIES_DETECTOR_VERSION,
    "copy_paste": COPY_PASTE_DETECTOR_VERSION,
    "modular_mirage": MODULAR_MIRAGE_DETECTOR_VERSION,
    "boilerplate_inflation": BOILERPLATE_INFLATION_DETECTOR_VERSION,
}

# ---------------------------------------------------------------------------
# Severity labels
# ---------------------------------------------------------------------------


def _severity_label(score: int) -> str:
    if score <= 15:
        return "HEALTHY"
    elif score <= 35:
        return "LOW"
    elif score <= 55:
        return "MODERATE"
    elif score <= 75:
        return "HIGH"
    else:
        return "CRITICAL"


# ---------------------------------------------------------------------------
# Pattern 1: Dead exports / orphaned symbols
# ---------------------------------------------------------------------------

# W161: Framework hook allowlist — names that the call graph cannot see
# because the runtime invokes them via duck-typing, reflection, or
# protocol-method dispatch. Without this, the dogfood audit (W149) found
# 405 dead-exports on roam-code, most of them false positives in two
# clusters:
#
#  1. Click ``MultiCommand`` / ``Group`` overrides on ``cli.py:LazyGroup``
#     (``list_commands``, ``get_command``, ``resolve_command``,
#     ``parse_args``, ``format_help``, ``invoke``, ...) — Click's runtime
#     calls these by name on every ``roam <subcmd>`` invocation; the
#     static call graph has zero edges to them.
#
#  2. Quality-envelope ``as_envelope_dict`` methods in
#     ``quality/cycles.py``, ``quality/god_components.py``,
#     ``quality/ai_rot.py`` — consumers call ``obj.as_envelope_dict()`` via
#     attribute reflection on a result object; the indexer can't resolve the
#     attribute access.
#
# This is a NAME-BASED allowlist (cheap, no class-hierarchy walk). Per
# the W161 brief, a class-hierarchy variant (exempt anything inheriting
# from ``click.MultiCommand`` / ``click.Group`` / ``click.Command``) is
# explicitly deferred; the name set below already catches the high-FP
# cases the dogfood audit surfaced.
#
# Categorised so the rationale stays auditable when a future detector
# tunes adds or removes entries.
_FRAMEWORK_HOOK_NAMES: frozenset[str] = frozenset(
    {
        # --- Click ``MultiCommand`` / ``Group`` method overrides ---
        # cli.py:LazyGroup ships ~half of these; the rest cover sibling
        # ``Command`` subclasses we'd want to allowlist consistently.
        "list_commands",
        "get_command",
        "resolve_command",
        "parse_args",
        "format_help",
        "invoke",
        "format_options",
        "format_usage",
        "format_help_text",
        "format_epilog",
        "format_commands",
        "get_help",
        "get_short_help_str",
        "get_usage",
        # --- Click ``Group`` / ``Command`` registration / callbacks ---
        "add_command",
        "command",
        "group",
        "result_callback",
        # --- Reflective dataclass-style serialisation methods ---
        # ``quality/cycles.py``, ``quality/god_components.py``, and
        # ``quality/ai_rot.py`` expose ``as_envelope_dict`` called via
        # ``obj.as_envelope_dict()`` reflection from consumers.
        "as_envelope_dict",
        "as_dict",
        "to_dict",
        "to_json",
        "from_dict",
        "from_json",
        "from_yaml",
        "to_yaml",
        # --- Dunders that the runtime always reaches ---
        # NOTE: most of these are already excluded upstream by the
        # ``name NOT LIKE '_%'`` SQL filter on ``_detect_dead_exports``
        # (which strips any leading-underscore name). They're listed
        # here explicitly so the allowlist documents *intent* — and so
        # the future class-hierarchy variant (which won't have the leading
        # underscore filter) inherits the right exemptions without a
        # second pass.
        "__init__",
        "__call__",
        "__enter__",
        "__exit__",
        "__iter__",
        "__next__",
        "__getitem__",
        "__setitem__",
        "__contains__",
        "__len__",
        "__hash__",
        "__eq__",
        "__repr__",
        "__str__",
        "__bool__",
        "__init_subclass__",
        "__class_getitem__",
        "__post_init__",
        # --- pytest fixture / lifecycle hooks ---
        # pytest discovers these by attribute lookup on test classes /
        # modules; the call graph never resolves them. Included even
        # though the existing test-file exclusion already covers most
        # callers — kept consistent with the rest of the policy and so
        # an in-tree ``conftest.py`` extension lives outside ``tests/``
        # is still exempt.
        "setup_method",
        "teardown_method",
        "setup_class",
        "teardown_class",
        "setup_module",
        "teardown_module",
    }
)


def _detect_dead_exports(conn) -> tuple[int, int]:
    """Count public symbols with zero incoming edges.

    This is a deliberately COARSER metric than ``roam dead``. The two
    commands disagree by design on the same codebase — known divergence
    (W19) of ~3.4x on a PHP backend (2936 here vs 855 from ``roam
    dead``). Three documented reasons for the gap:

    1. ``vibe-check`` counts ALL incoming-edge=0 symbols as dead. ``roam
       dead`` only counts symbols with zero *production* consumers
       (test-only consumers move it to REVIEW, not SAFE).
    2. ``vibe-check`` does NOT apply the ``is_excluded_path`` filter
       (``dev/``, ``examples/``, ``vendor/``, ``benchmarks/``,
       ``generated/``, ``docs/``, ``fixtures/``, ``samples/``, etc.).
       ``roam dead`` does, so symbols in those trees are invisible.
    3. ``vibe-check`` does NOT apply the transitively-alive filter (a
       symbol consumed by a barrel re-exporter whose own consumer
       still uses it through a downstream import chain). ``roam dead``
       does, so barrel-routed exports don't show up.

    The vibe-check number is a HEALTH SIGNAL (rough rot proxy). The
    ``roam dead`` number is an ACTIONABLE deletion list. Both numbers
    are correct under their own definitions; they answer different
    questions, so labelling them with a ``_definition`` field is the
    fix per CLAUDE.md Pattern 3.

    Excludes test files, dunders, CLI command files, framework hook
    names (W161 — Click ``MultiCommand`` overrides, reflective
    ``as_envelope_dict`` callbacks, pytest lifecycle hooks), and
    entry-point names to reduce false positives common to
    AI-generated code.

    Returns (found, total_public_symbols).
    """
    # Exclude test files and cmd_ files from dead export analysis
    _EXCLUDE_SQL = (
        "AND f.path NOT LIKE '%test\\_%' ESCAPE '\\' "
        "AND f.path NOT LIKE '%\\_test.%' ESCAPE '\\' "
        "AND f.path NOT LIKE '%/tests/%' "
        "AND f.path NOT LIKE '%/test/%' "
        "AND f.path NOT LIKE '%conftest%' "
        "AND f.path NOT LIKE '%cmd\\_%' ESCAPE '\\' "
    )

    # W161: framework-hook name allowlist — applied AT THE SQL LEVEL so
    # both the ``total`` and ``dead`` counts agree, and so the per-symbol
    # ``_collect_dead_export_findings`` query (which mirrors this WHERE
    # clause for the findings registry) emits the same row set.
    hook_names = tuple(sorted(_FRAMEWORK_HOOK_NAMES))
    hook_placeholders = ",".join("?" * len(hook_names))
    _HOOK_SQL = f"AND s.name NOT IN ({hook_placeholders}) "

    total = conn.execute(
        "SELECT COUNT(*) FROM symbols s "
        "JOIN files f ON s.file_id = f.id "
        "WHERE s.kind IN ('function', 'class', 'method') "
        "AND s.name NOT LIKE '\\_%' ESCAPE '\\' "
        "AND s.is_exported = 1 " + _EXCLUDE_SQL + _HOOK_SQL,
        hook_names,
    ).fetchone()[0]

    dead = conn.execute(
        "SELECT COUNT(*) FROM symbols s "
        "JOIN files f ON s.file_id = f.id "
        "WHERE s.kind IN ('function', 'class', 'method') "
        "AND s.name NOT LIKE '\\_%' ESCAPE '\\' "
        "AND s.is_exported = 1 "
        "AND s.id NOT IN (SELECT target_id FROM edges) " + _EXCLUDE_SQL + _HOOK_SQL,
        hook_names,
    ).fetchone()[0]

    return dead, max(total, 1)


# ---------------------------------------------------------------------------
# Pattern 2: Short-term churn (revised heavily within 14 days)
# ---------------------------------------------------------------------------


def _detect_short_churn(conn) -> tuple[int, int, list[dict]]:
    """Find files with 4+ commits where most activity was within 14 days.

    Returns (found, total_files, details).
    """
    rows = conn.execute(
        "SELECT f.path, fs.commit_count, "
        "  MIN(gc.timestamp) as first_ts, MAX(gc.timestamp) as last_ts "
        "FROM file_stats fs "
        "JOIN files f ON fs.file_id = f.id "
        "JOIN git_file_changes gfc ON gfc.file_id = f.id "
        "JOIN git_commits gc ON gfc.commit_id = gc.id "
        "WHERE fs.commit_count >= 4 "
        "GROUP BY f.id "
        "HAVING (MAX(gc.timestamp) - MIN(gc.timestamp)) < 14 * 86400 "
        "AND (MAX(gc.timestamp) - MIN(gc.timestamp)) > 0"
    ).fetchall()

    total_files = conn.execute("SELECT COUNT(*) FROM files").fetchone()[0]
    details = []
    for r in rows:
        span_days = (r["last_ts"] - r["first_ts"]) / 86400 if r["last_ts"] and r["first_ts"] else 0
        details.append(
            {
                "file": r["path"],
                "commits": r["commit_count"],
                "span_days": round(span_days, 1),
            }
        )

    return len(rows), max(total_files, 1), details


# ---------------------------------------------------------------------------
# Pattern 3: Empty error handlers
#
# vibe-check counts empty-handler OCCURRENCES across all languages
# (Python, JS, TS, Java, C#, Go, Ruby). Multiple empties in one function
# all count. This is intentional — vibe-check is a coarse health signal,
# not an actionable refactor list.
#
# For a per-function actionable list, use the Python exception-handling
# detectors in ``roam math``. The two numbers won't match by design.
# ---------------------------------------------------------------------------

_EMPTY_HANDLER_PATTERNS = {
    "python": [
        # except ...: pass
        re.compile(r"^\s*except\b.*:\s*$\n\s*pass\s*$", re.MULTILINE),
        # except ...: ...
        re.compile(r"^\s*except\b.*:\s*$\n\s*\.\.\.\s*$", re.MULTILINE),
        # bare except: pass on one line (some styles)
        re.compile(r"^\s*except\b[^:]*:\s*pass\s*$", re.MULTILINE),
        # except ...: ... on one line
        re.compile(r"^\s*except\b[^:]*:\s*\.\.\.\s*$", re.MULTILINE),
    ],
    "javascript": [
        # catch (e) {} or catch (e) { }
        re.compile(r"(?<!\.)\bcatch\s*\([^)]*\)\s*\{\s*\}", re.MULTILINE),
    ],
    "typescript": [
        re.compile(r"(?<!\.)\bcatch\s*\([^)]*\)\s*\{\s*\}", re.MULTILINE),
    ],
    "java": [
        re.compile(r"(?<!\.)\bcatch\s*\([^)]*\)\s*\{\s*\}", re.MULTILINE),
    ],
    "c_sharp": [
        re.compile(r"\bcatch\s*\([^)]*\)\s*\{\s*\}", re.MULTILINE),
        re.compile(r"\bcatch\s*\{\s*\}", re.MULTILINE),
    ],
    "go": [
        # if err != nil { } (empty body — error swallowed)
        re.compile(r"\bif\s+err\s*!=\s*nil\s*\{\s*\}", re.MULTILINE),
    ],
    "ruby": [
        re.compile(r"\brescue\b.*\n\s*(?:nil|next|#.*)?\s*\n\s*end", re.MULTILINE),
    ],
}


def _detect_empty_handlers(conn, project_root: Path) -> tuple[int, int, list[dict]]:
    """Scan source files for empty error handlers using regex.

    Returns (found, total_handlers_approximation, details).
    """
    files = conn.execute("SELECT id, path, language FROM files WHERE language IS NOT NULL").fetchall()

    found = 0
    total_try_blocks = 0
    details: list[dict] = []

    for f in files:
        lang = f["language"]
        patterns = _EMPTY_HANDLER_PATTERNS.get(lang, [])
        if not patterns:
            continue

        file_path = project_root / f["path"]
        try:
            source = file_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue

        # Count total error handling blocks (rough)
        if lang == "python":
            total_try_blocks += len(re.findall(r"^\s*except\b", source, re.MULTILINE))
        elif lang in ("javascript", "typescript", "java", "c_sharp"):
            # Count catch statements, not Promise `.catch(...)` fallbacks such
            # as `response.json().catch(() => ({}))`.
            total_try_blocks += len(re.findall(r"(?<!\.)\bcatch\s*\(", source, re.MULTILINE))
        elif lang == "go":
            total_try_blocks += len(re.findall(r"\bif\s+err\s*!=\s*nil", source, re.MULTILINE))
        elif lang == "ruby":
            total_try_blocks += len(re.findall(r"\brescue\b", source, re.MULTILINE))

        file_count = 0
        for pat in patterns:
            matches = pat.findall(source)
            file_count += len(matches)

        if file_count > 0:
            found += file_count
            details.append(
                {
                    "file": f["path"],
                    "count": file_count,
                    "pattern": "empty_handler",
                }
            )

    return found, max(total_try_blocks, 1), details


# ---------------------------------------------------------------------------
# Pattern 4: Abandoned stubs
# ---------------------------------------------------------------------------

_STUB_PATTERNS = {
    "python": [
        # def foo(): pass
        re.compile(
            r'^\s*def\s+\w+\s*\([^)]*\)\s*(?:->[^:]*)?:\s*$\n(?:\s*(?:"""[^"]*"""|\'\'\'[^\']*\'\'\')?\s*$\n)*\s*pass\s*$',
            re.MULTILINE,
        ),
        # def foo(): ...
        re.compile(
            r'^\s*def\s+\w+\s*\([^)]*\)\s*(?:->[^:]*)?:\s*$\n(?:\s*(?:"""[^"]*"""|\'\'\'[^\']*\'\'\')?\s*$\n)*\s*\.\.\.\s*$',
            re.MULTILINE,
        ),
        # def foo(): raise NotImplementedError
        re.compile(
            r'^\s*def\s+\w+\s*\([^)]*\)\s*(?:->[^:]*)?:\s*$\n(?:\s*(?:"""[^"]*"""|\'\'\'[^\']*\'\'\')?\s*$\n)*\s*raise\s+NotImplementedError',
            re.MULTILINE,
        ),
    ],
    "javascript": [
        # function foo() {} or function foo() { }
        re.compile(r"\bfunction\s+\w+\s*\([^)]*\)\s*\{\s*\}", re.MULTILINE),
        # function foo() { /* TODO */ }
        re.compile(r"\bfunction\s+\w+\s*\([^)]*\)\s*\{\s*/\*.*?TODO.*?\*/\s*\}", re.MULTILINE | re.DOTALL),
    ],
    "typescript": [
        re.compile(r"\bfunction\s+\w+\s*\([^)]*\)\s*(?::\s*\w+)?\s*\{\s*\}", re.MULTILINE),
        re.compile(
            r"\bfunction\s+\w+\s*\([^)]*\)\s*(?::\s*\w+)?\s*\{\s*/\*.*?TODO.*?\*/\s*\}",
            re.MULTILINE | re.DOTALL,
        ),
    ],
    "go": [
        # func foo() {}
        re.compile(r"\bfunc\s+\w+\s*\([^)]*\)\s*(?:\([^)]*\)\s*)?\{\s*\}", re.MULTILINE),
    ],
}


def _detect_stubs(conn, project_root: Path) -> tuple[int, int, list[dict]]:
    """Find functions with stub bodies (pass, ..., TODO, NotImplementedError, empty).

    Returns (found, total_functions, details).
    """
    total_functions = conn.execute("SELECT COUNT(*) FROM symbols WHERE kind IN ('function', 'method')").fetchone()[0]

    files = conn.execute("SELECT id, path, language FROM files WHERE language IS NOT NULL").fetchall()

    found = 0
    details: list[dict] = []

    for f in files:
        lang = f["language"]
        patterns = _STUB_PATTERNS.get(lang, [])
        if not patterns:
            continue

        file_path = project_root / f["path"]
        try:
            source = file_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue

        file_count = 0
        for pat in patterns:
            matches = pat.findall(source)
            file_count += len(matches)

        if file_count > 0:
            found += file_count
            details.append(
                {
                    "file": f["path"],
                    "count": file_count,
                    "pattern": "stub",
                }
            )

    return found, max(total_functions, 1), details


# ---------------------------------------------------------------------------
# Pattern 5: Hallucinated imports (unresolvable references)
# ---------------------------------------------------------------------------


def _detect_hallucinated_imports(conn) -> tuple[int, int, list[dict]]:
    """Count edges of kind 'imports' or 'calls' where the target could
    not be resolved (target symbol has no definition in the index).

    Simpler approach: count references/edges that point to symbols that
    have no file (orphan targets), or look for import-type edges with
    unresolved targets.

    Returns (found, total_import_edges, details).
    """
    # Count total import-type edges
    total_imports = conn.execute("SELECT COUNT(*) FROM edges WHERE kind IN ('imports', 'import')").fetchone()[0]

    # If no import edges, fall back to counting symbols of kind 'import'
    # that have no outgoing resolved edges
    if total_imports == 0:
        # Alternative: count symbols referencing unknown names
        # Use unresolved references — symbols mentioned but not in the index
        # Look for source symbols that have outgoing edges to targets not in any file
        total_imports = conn.execute("SELECT COUNT(DISTINCT source_id) FROM edges").fetchone()[0]

    # Hallucinated: edges whose target_id points to a symbol that doesn't
    # exist in the symbols table (should be 0 due to FK, but check references
    # that couldn't be resolved during indexing)
    # Better approach: count files that import other files which don't exist
    # in the index (file_edges pointing to missing targets)
    hallucinated = 0
    details: list[dict] = []

    # Look at file-level imports that don't resolve
    # file_edges where target file has zero symbols => potentially hallucinated
    rows = conn.execute(
        "SELECT f_src.path as src_path, f_tgt.path as tgt_path "
        "FROM file_edges fe "
        "JOIN files f_src ON fe.source_file_id = f_src.id "
        "JOIN files f_tgt ON fe.target_file_id = f_tgt.id "
        "WHERE NOT EXISTS ("
        "  SELECT 1 FROM symbols s WHERE s.file_id = fe.target_file_id"
        ")"
    ).fetchall()

    # Also count symbols that are referenced but don't appear in the index
    # This uses edges where target_id maps to symbols with no callers themselves
    # Approximate: symbols referenced (in edges) but not defined (no line_start)
    orphan_refs = conn.execute(
        "SELECT COUNT(*) FROM edges e "
        "JOIN symbols s ON e.target_id = s.id "
        "WHERE s.line_start IS NULL AND s.line_end IS NULL"
    ).fetchone()[0]

    hallucinated = len(rows) + orphan_refs
    total = max(total_imports + orphan_refs, 1)

    by_file: dict[str, int] = defaultdict(int)
    for r in rows:
        by_file[r["src_path"]] += 1

    for path, count in by_file.items():
        details.append({"file": path, "count": count, "pattern": "hallucinated_import"})

    return hallucinated, total, details


# ---------------------------------------------------------------------------
# Pattern 6: Error handling inconsistency
# ---------------------------------------------------------------------------

_ERROR_PATTERNS_BY_LANG: dict[str, list[tuple[str, re.Pattern]]] = {
    "python": [
        ("try/except", re.compile(r"\btry\s*:", re.MULTILINE)),
        ("raise", re.compile(r"\braise\s+\w+", re.MULTILINE)),
        ("return_error", re.compile(r"\breturn\s+(?:None|False|-1)\b", re.MULTILINE)),
        ("assert", re.compile(r"\bassert\s+", re.MULTILINE)),
    ],
    "javascript": [
        ("try/catch", re.compile(r"\btry\s*\{", re.MULTILINE)),
        ("throw", re.compile(r"\bthrow\s+", re.MULTILINE)),
        ("callback_error", re.compile(r"\bcallback\s*\(\s*(?:err|error)", re.MULTILINE)),
        ("promise_reject", re.compile(r"\.catch\s*\(", re.MULTILINE)),
    ],
    "typescript": [
        ("try/catch", re.compile(r"\btry\s*\{", re.MULTILINE)),
        ("throw", re.compile(r"\bthrow\s+", re.MULTILINE)),
        ("promise_reject", re.compile(r"\.catch\s*\(", re.MULTILINE)),
    ],
    "go": [
        ("error_return", re.compile(r"\breturn\s+.*,\s*err\b", re.MULTILINE)),
        ("error_check", re.compile(r"\bif\s+err\s*!=\s*nil", re.MULTILINE)),
        ("panic", re.compile(r"\bpanic\s*\(", re.MULTILINE)),
    ],
}


def _detect_error_inconsistency(conn, project_root: Path) -> tuple[int, int, list[dict]]:
    """Detect files/modules with mixed error handling patterns.

    A file using 3+ distinct error patterns is flagged as inconsistent.

    Returns (found, total_modules, details).
    """
    files = conn.execute("SELECT id, path, language FROM files WHERE language IS NOT NULL").fetchall()

    inconsistent = 0
    total_modules = 0
    details: list[dict] = []

    for f in files:
        lang = f["language"]
        error_patterns = _ERROR_PATTERNS_BY_LANG.get(lang, [])
        if not error_patterns:
            continue

        file_path = project_root / f["path"]
        try:
            source = file_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue

        total_modules += 1
        used_patterns = set()
        for pname, pat in error_patterns:
            if pat.search(source):
                used_patterns.add(pname)

        if len(used_patterns) >= 3:
            inconsistent += 1
            details.append(
                {
                    "file": f["path"],
                    "patterns": sorted(used_patterns),
                    "count": len(used_patterns),
                }
            )

    return inconsistent, max(total_modules, 1), details


# ---------------------------------------------------------------------------
# Pattern 7: Comment density anomalies
# ---------------------------------------------------------------------------


def _detect_comment_anomalies(conn, project_root: Path) -> tuple[int, int, list[dict]]:
    """Find files with outlier comment-to-code ratios.

    Files with ratio >2 standard deviations from the mean are flagged.
    AI-generated code tends to have either zero comments or excessive comments.

    Returns (found, total_files, details).
    """
    files = conn.execute(
        "SELECT id, path, language, line_count FROM files WHERE language IS NOT NULL AND line_count > 10"
    ).fetchall()

    if not files:
        return 0, 1, []

    _COMMENT_MARKERS = {
        "python": (re.compile(r"^\s*#"), None),
        "javascript": (re.compile(r"^\s*//"), re.compile(r"/\*.*?\*/", re.DOTALL)),
        "typescript": (re.compile(r"^\s*//"), re.compile(r"/\*.*?\*/", re.DOTALL)),
        "java": (re.compile(r"^\s*//"), re.compile(r"/\*.*?\*/", re.DOTALL)),
        "c": (re.compile(r"^\s*//"), re.compile(r"/\*.*?\*/", re.DOTALL)),
        "cpp": (re.compile(r"^\s*//"), re.compile(r"/\*.*?\*/", re.DOTALL)),
        "c_sharp": (re.compile(r"^\s*//"), re.compile(r"/\*.*?\*/", re.DOTALL)),
        "go": (re.compile(r"^\s*//"), re.compile(r"/\*.*?\*/", re.DOTALL)),
        "ruby": (re.compile(r"^\s*#"), None),
        "rust": (re.compile(r"^\s*//"), re.compile(r"/\*.*?\*/", re.DOTALL)),
        "php": (re.compile(r"^\s*(?://|#)"), re.compile(r"/\*.*?\*/", re.DOTALL)),
    }

    ratios: list[tuple[dict, float]] = []

    for f in files:
        lang = f["language"]
        markers = _COMMENT_MARKERS.get(lang)
        if not markers:
            continue

        line_pattern, block_pattern = markers
        file_path = project_root / f["path"]
        try:
            source = file_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue

        lines = source.split("\n")
        total_lines = len(lines)
        if total_lines < 5:
            continue

        # Count single-line comments
        comment_lines = 0
        if line_pattern:
            for line in lines:
                if line_pattern.match(line):
                    comment_lines += 1

        # Count block comment lines
        if block_pattern:
            for match in block_pattern.finditer(source):
                comment_lines += match.group(0).count("\n") + 1

        code_lines = max(total_lines - comment_lines, 1)
        ratio = comment_lines / code_lines
        ratios.append((f, ratio))

    if len(ratios) < 3:
        return 0, max(len(ratios), 1), []

    # Compute mean and std dev
    values = [r for _, r in ratios]
    mean = sum(values) / len(values)
    variance = sum((v - mean) ** 2 for v in values) / len(values)
    std_dev = variance**0.5

    if std_dev < 0.01:
        return 0, len(ratios), []

    anomalies: list[dict] = []
    for f, ratio in ratios:
        z_score = (ratio - mean) / std_dev
        if abs(z_score) > 2.0:
            anomalies.append(
                {
                    "file": f["path"],
                    "comment_ratio": round(ratio, 2),
                    "z_score": round(z_score, 2),
                    "direction": "excessive" if z_score > 0 else "absent",
                }
            )

    return len(anomalies), len(ratios), anomalies


# ---------------------------------------------------------------------------
# Pattern 8: Copy-paste functions (duplicate normalized bodies)
# ---------------------------------------------------------------------------


def _normalize_body(source: str) -> str:
    """Normalize a function body for duplication detection.

    Strips whitespace, comments, string literals, and identifier names
    to detect structural clones.
    """
    # Remove single-line comments
    s = re.sub(r"//.*$", "", source, flags=re.MULTILINE)
    s = re.sub(r"#.*$", "", s, flags=re.MULTILINE)
    # Remove block comments
    s = re.sub(r"/\*.*?\*/", "", s, flags=re.DOTALL)
    # Remove string literals
    s = re.sub(r'"[^"]*"', '""', s)
    s = re.sub(r"'[^']*'", "''", s)
    # Collapse whitespace
    s = re.sub(r"\s+", " ", s).strip()
    return s


def _detect_copy_paste(conn, project_root: Path) -> tuple[int, int, list[dict]]:
    """Find groups of 3+ functions with identical normalized bodies.

    Returns (found_in_clone_groups, total_functions, details).
    """
    # Get function symbols with their line ranges
    functions = conn.execute(
        "SELECT s.id, s.name, s.kind, s.line_start, s.line_end, "
        "  f.path as file_path "
        "FROM symbols s JOIN files f ON s.file_id = f.id "
        "WHERE s.kind IN ('function', 'method') "
        "AND s.line_start IS NOT NULL AND s.line_end IS NOT NULL "
        "AND (s.line_end - s.line_start) >= 3"
    ).fetchall()

    total_functions = len(functions)
    if total_functions < 3:
        return 0, max(total_functions, 1), []

    # Group by file for efficient reading
    by_file: dict[str, list] = defaultdict(list)
    for fn in functions:
        by_file[fn["file_path"]].append(fn)

    # Hash normalized bodies
    body_hashes: dict[str, list[dict]] = defaultdict(list)

    for file_path, fns in by_file.items():
        full_path = project_root / file_path
        try:
            source_lines = full_path.read_text(encoding="utf-8", errors="replace").split("\n")
        except OSError:
            continue

        for fn in fns:
            start = (fn["line_start"] or 1) - 1
            end = fn["line_end"] or start + 1
            body = "\n".join(source_lines[start:end])
            normalized = _normalize_body(body)

            if len(normalized) < 30:
                continue  # Too short to be meaningful

            h = hashlib.md5(normalized.encode("utf-8")).hexdigest()
            body_hashes[h].append(
                {
                    "name": fn["name"],
                    "file": file_path,
                    "line": fn["line_start"],
                }
            )

    # Find groups of 3+ duplicates
    found = 0
    details: list[dict] = []
    for h, group in body_hashes.items():
        if len(group) >= 3:
            found += len(group)
            details.append(
                {
                    "clone_group_size": len(group),
                    "functions": group[:5],  # limit detail size
                }
            )

    return found, max(total_functions, 1), details


# ---------------------------------------------------------------------------
# Pattern 9 (W371): Modular Mirage — single-caller exported helpers
#
# Operationalises the "Modular Mirage" smell from arxiv:2605.02741
# (Zhu / Tsantalis / Rigby, "AI-Generated Smells", May 2026). The paper
# defines the pattern as agents achieving "superficial structural
# modularity (file separation) but fail[ing] to create semantic
# cohesion". The cheapest reliable proxy on the roam index: count
# EXPORTED symbols (function / method) that have exactly ONE incoming
# edge — the helper exists as if it were a reusable abstraction, but
# there's only one consumer.
#
# Distinct from ``copy_paste`` (which fires on duplicated bodies) and
# from ``dead_exports`` (zero callers). Single-caller helpers sit at the
# boundary: not dead, not reused, just an unjustified split. The paper
# co-occurs this with "Scattered Functionality + Unstable Dependencies"
# — both are richer signals that require AST-level coupling analysis
# (deferred; see "drive-by findings" in the W371 report).
#
# Reuses the same exclusion clauses as ``_detect_dead_exports``:
# excludes test/cmd files, dunders, leading-underscore privates, and
# the framework-hook allowlist (Click ``MultiCommand`` overrides,
# reflective ``as_envelope_dict`` callbacks, pytest lifecycle hooks).
# That keeps the two detectors mutually consistent: a symbol is either
# dead (0 callers), modular-mirage (1 caller), or reused (>=2 callers).
# ---------------------------------------------------------------------------


def _detect_modular_mirage(conn) -> tuple[int, int, list[dict]]:
    """Find exported function / method symbols with exactly 1 caller.

    Returns (found, total_eligible_exports, details). ``details`` is one
    dict per single-caller export, carrying the symbol id + file + line
    + the single caller's file so the W125-style finding emit can attach
    structured evidence.

    The eligible-population denominator matches ``_detect_dead_exports``
    (same WHERE clauses, same hook allowlist) so the per-detector rate
    is comparable across the three caller-count tiers.
    """
    _EXCLUDE_SQL = (
        "AND f.path NOT LIKE '%test\\_%' ESCAPE '\\' "
        "AND f.path NOT LIKE '%\\_test.%' ESCAPE '\\' "
        "AND f.path NOT LIKE '%/tests/%' "
        "AND f.path NOT LIKE '%/test/%' "
        "AND f.path NOT LIKE '%conftest%' "
        "AND f.path NOT LIKE '%cmd\\_%' ESCAPE '\\' "
    )
    hook_names = tuple(sorted(_FRAMEWORK_HOOK_NAMES))
    hook_placeholders = ",".join("?" * len(hook_names))
    _HOOK_SQL = f"AND s.name NOT IN ({hook_placeholders}) "

    total_eligible = conn.execute(
        "SELECT COUNT(*) FROM symbols s "
        "JOIN files f ON s.file_id = f.id "
        "WHERE s.kind IN ('function', 'method') "
        "AND s.name NOT LIKE '\\_%' ESCAPE '\\' "
        "AND s.is_exported = 1 " + _EXCLUDE_SQL + _HOOK_SQL,
        hook_names,
    ).fetchone()[0]

    # Symbols with exactly 1 DISTINCT caller. The indexer emits both an
    # ``import`` and a ``call`` edge for the same source/target pair in
    # Python, so a raw ``COUNT(*) = 1`` would miss every Python case.
    # ``COUNT(DISTINCT source_id) = 1`` correctly collapses the
    # multi-edge-per-caller indexer artifact and counts unique callers.
    # Class symbols are excluded because a single-instantiation class is
    # a different (and weaker) signal than a single-caller function —
    # we want the function/method form which directly maps to the
    # paper's "scattered helper" example.
    rows = conn.execute(
        "SELECT s.id, s.name, s.kind, s.line_start, f.path AS file_path, "
        "       (SELECT f2.path FROM edges e2 "
        "          JOIN symbols s2 ON e2.source_id = s2.id "
        "          JOIN files f2 ON s2.file_id = f2.id "
        "          WHERE e2.target_id = s.id LIMIT 1) AS caller_file "
        "FROM symbols s "
        "JOIN files f ON s.file_id = f.id "
        "WHERE s.kind IN ('function', 'method') "
        "AND s.name NOT LIKE '\\_%' ESCAPE '\\' "
        "AND s.is_exported = 1 "
        "AND s.id IN ("
        "  SELECT target_id FROM edges "
        "  GROUP BY target_id HAVING COUNT(DISTINCT source_id) = 1"
        ") " + _EXCLUDE_SQL + _HOOK_SQL,
        hook_names,
    ).fetchall()

    details: list[dict] = []
    for r in rows:
        # Filter out same-file "callers" — if the only caller lives in
        # the SAME file as the callee, this is a within-file
        # refactor-split, not a cross-file modular mirage. The paper's
        # signal is specifically about cross-file abstraction.
        caller_file = r["caller_file"] or ""
        if caller_file and caller_file == r["file_path"]:
            continue
        details.append(
            {
                "symbol_id": int(r["id"]),
                "name": r["name"],
                "kind": r["kind"],
                "file": r["file_path"],
                "file_path": r["file_path"],
                "line_start": r["line_start"],
                "caller_file": caller_file,
                "count": 1,
            }
        )

    return len(details), max(total_eligible, 1), details


# ---------------------------------------------------------------------------
# Pattern 10 (W371): Boilerplate Inflation
#
# Operationalises the "boilerplate inflation" variant of the
# redundant-implementation pattern discussed in arxiv:2605.02741 (the
# paper calls it "Redundant Implementation / PAU — copy-paste invocation
# style that inflates code volume") and intersects with the LLM
# code-smell catalog in arxiv:2512.18020. Two sub-heuristics, both
# implementable with the existing file-content scan path:
#
#   A) ``comment_restates_code`` — a single-line comment whose stripped
#      text is a prefix-substring of the next non-empty code line.
#      Example: ``# call save()`` immediately followed by ``save()``.
#      AI-rot signature: comments that document the next statement
#      verbatim rather than explaining intent.
#
#   B) ``shallow_wrapper`` (Python only) — an exported function whose
#      entire body is a docstring + a single statement (``return X(...)``
#      or ``X(...)``). The wrapper has more documentation than code; a
#      common pattern in AI-generated "professional-looking" scaffolds.
#
# Both fire at OCCURRENCE level (one finding per match). The detector
# returns aggregated per-file counts in ``details`` so the worst-files
# aggregation works the same way as for other patterns.
#
# Heuristic confidence: ``heuristic`` (regex on source text). Defensive
# None-check detection on statically-non-None values would be a richer
# signal but requires dataflow — deferred as a drive-by.
# ---------------------------------------------------------------------------

# Regex matching a single-line Python/JS/TS/Go/Java/C# comment followed
# by the next non-blank line. We match on the COMMENT TEXT (after the
# marker) and check below whether it is a case-insensitive substring of
# the following code line.
_COMMENT_CODE_PATTERNS = {
    "python": re.compile(r"^[ \t]*#[ \t]*([^\n]+)\n[ \t]*([^\n#]+)$", re.MULTILINE),
    "ruby": re.compile(r"^[ \t]*#[ \t]*([^\n]+)\n[ \t]*([^\n#]+)$", re.MULTILINE),
    "javascript": re.compile(r"^[ \t]*//[ \t]*([^\n]+)\n[ \t]*([^\n/]+)$", re.MULTILINE),
    "typescript": re.compile(r"^[ \t]*//[ \t]*([^\n]+)\n[ \t]*([^\n/]+)$", re.MULTILINE),
    "java": re.compile(r"^[ \t]*//[ \t]*([^\n]+)\n[ \t]*([^\n/]+)$", re.MULTILINE),
    "c_sharp": re.compile(r"^[ \t]*//[ \t]*([^\n]+)\n[ \t]*([^\n/]+)$", re.MULTILINE),
    "go": re.compile(r"^[ \t]*//[ \t]*([^\n]+)\n[ \t]*([^\n/]+)$", re.MULTILINE),
}

# Tokens we tolerate (small words AI uses interchangeably with the code).
# A comment is "restating" if 60%+ of its >=3-char tokens appear in the
# code line below, case-insensitively. Pure boolean substring matching
# is too lossy ("Save the file" vs ``save_file()`` — both should match).
_RESTATE_TOLERATED: frozenset[str] = frozenset(
    {"the", "a", "an", "and", "or", "of", "to", "in", "on", "for", "with", "this", "that", "if", "is", "are"}
)


def _comment_restates_code(comment_text: str, code_line: str) -> bool:
    """True when *comment_text* essentially restates *code_line*.

    Two acceptance criteria, both AI-rot signatures observed in the
    arxiv:2605.02741 corpus:

    1. **Identifier-echo** — the comment uses the same identifier as
       the next line (case-insensitive). Example: ``# update counter``
       above ``counter = counter + 1``. A SINGLE identifier overlap is
       enough when the comment is short.

    2. **High-overlap paraphrase** — >=60% of the comment's content
       tokens appear (as substrings, case-insensitive) in the code
       line, ignoring small stop words. Catches direct restatements
       where the AI literally translates the code into English.

    Both criteria are needed: pure substring matching catches "set X to
    Y" → "X = Y" only when "X" appears (criterion 1), and high-overlap
    catches "return the user_id value" → "return user_id" (criterion 2).
    """
    code_lower = code_line.lower()
    # First, the comment has to be SHORT — long-form comments that
    # happen to mention a code identifier are usually explaining
    # context ("Edge case discovered in prod 2026-03-12 — empty
    # payloads."). Restating comments are terse and run-on-style
    # ("# set counter to value plus one"). Cap at 10 content words.
    all_words = re.findall(r"[A-Za-z_][A-Za-z0-9_]*", comment_text)
    if len(all_words) > 10:
        return False
    content_tokens = [t.lower() for t in all_words if len(t) >= 3 and t.lower() not in _RESTATE_TOLERATED]
    if not content_tokens:
        return False
    matched_count = sum(1 for t in content_tokens if t in code_lower)
    if matched_count == 0:
        return False
    # Density gate: at least 40% of meaningful content words must
    # appear in the code line. This filters "Reuses the legacy
    # seven-day rollup window agreed with finance." (one match in 8
    # content words = 12%) from "set counter to value plus one" (one
    # match in 4 content words = 25% — still rejected) and
    # "update counter" (one match in 2 content words = 50% — flagged).
    # The threshold + the 10-word cap together separate inline
    # restatements from contextual commentary.
    density = matched_count / len(content_tokens)
    if density >= 0.4:
        return True
    return False


# Python "shallow wrapper" pattern: ``def NAME(...):`` followed by
# ``"""docstring"""`` followed by EITHER ``return CALL(...)`` OR
# ``CALL(...)`` as the ONLY remaining body statement.
_SHALLOW_WRAPPER_PY = re.compile(
    r"^([ \t]*)def\s+(\w+)\s*\([^)]*\)\s*(?:->[^:]*)?:\s*\n"  # def header
    r"\1[ \t]+(?:r|b|rb|br)?(?P<q>\"{3}|\'{3})(?P<doc>.*?)(?P=q)\s*\n"  # docstring
    r"\1[ \t]+(?:return\s+)?\w[\w.]*\s*\([^)]*\)\s*\n"  # single call / return
    r"(?=\1[^\s]|\Z)",  # next def at same/lower indent OR EOF
    re.MULTILINE | re.DOTALL,
)


def _is_scaffolding_path(path: str) -> bool:
    """True for test / conftest / cmd_* paths skipped by the inflation detector.

    Matches the same exclusion the rest of the AI-rot detector applies
    (mirrors ``_detect_dead_exports``'s scaffolding skip-list).
    """
    if not path:
        return False
    basename = path.rsplit("/", 1)[-1]
    return (
        "/tests/" in path
        or "/test/" in path
        or "conftest" in path
        or "cmd_" in basename
        or path.endswith("_test.py")
        or "test_" in basename
    )


def _compute_line_starts(source: str) -> list[int]:
    """Return 0-based byte offsets of every line start in *source*."""
    line_starts: list[int] = [0]
    for idx, ch in enumerate(source):
        if ch == "\n":
            line_starts.append(idx + 1)
    return line_starts


def _offset_to_line(start: int, line_starts: list[int]) -> int:
    """Map a byte *start* offset into its 1-based line number.

    Falls back to ``len(line_starts)`` when *start* lies past the last
    recorded newline (matches the for/else fallthrough in the original).
    """
    for i, ls in enumerate(line_starts):
        if ls > start:
            return i
    return len(line_starts)


def _scan_comment_restates(
    source: str,
    pat: "re.Pattern[str]",
    line_starts: list[int],
) -> list[dict]:
    """Yield ``comment_restates_code`` occurrences for one file."""
    occurrences: list[dict] = []
    for m in pat.finditer(source):
        comment_text = m.group(1).strip()
        code_line = m.group(2).strip()
        # Ignore special markers and section dividers.
        if not comment_text or comment_text.startswith(("---", "===", "***", "TODO", "FIXME", "NOTE", "XXX")):
            continue
        if not code_line or code_line.startswith(("#", "//")):
            continue
        if not _comment_restates_code(comment_text, code_line):
            continue
        line_no = _offset_to_line(m.start(), line_starts)
        occurrences.append(
            {
                "line": line_no,
                "subkind": "comment_restates_code",
                "snippet": f"# {comment_text[:60]} / {code_line[:60]}",
            }
        )
    return occurrences


def _scan_shallow_wrappers(source: str, line_starts: list[int]) -> list[dict]:
    """Yield ``shallow_wrapper`` occurrences for one Python file."""
    occurrences: list[dict] = []
    for m in _SHALLOW_WRAPPER_PY.finditer(source):
        name = m.group(2)
        # Private wrappers are below-the-fold by convention.
        if name.startswith("_"):
            continue
        doc = (m.group("doc") or "").strip()
        # Empty docstrings are caught by abandoned_stubs; we need a real
        # docstring to call this "inflation".
        if len(doc) < 10:
            continue
        line_no = _offset_to_line(m.start(), line_starts)
        occurrences.append(
            {
                "line": line_no,
                "subkind": "shallow_wrapper",
                "name": name,
                "snippet": f'def {name}(...): """{doc[:60]}...""" + 1 stmt',
            }
        )
    return occurrences


def _scan_file_for_inflation(path: str, lang: str, source: str) -> list[dict]:
    """Run both inflation sub-heuristics over one file's source.

    Returns the merged per-occurrence list (sub-heuristic A first, then B
    for Python), matching the original ordering.
    """
    line_starts = _compute_line_starts(source)
    occurrences: list[dict] = []

    pat = _COMMENT_CODE_PATTERNS.get(lang)
    if pat is not None:
        occurrences.extend(_scan_comment_restates(source, pat, line_starts))

    if lang == "python":
        occurrences.extend(_scan_shallow_wrappers(source, line_starts))

    return occurrences


def _detect_boilerplate_inflation(conn, project_root: Path) -> tuple[int, int, list[dict]]:
    """Scan source files for boilerplate-inflation occurrences.

    Returns (found, total_files_scanned, details). ``found`` is the number
    of FILES carrying at least one occurrence (== ``len(details)``) — the
    SAME per-file unit as ``total_files_scanned``, so the downstream rate
    ``found / total`` stays a proper fraction in ``[0, 1]``. Counting
    ``found`` per-OCCURRENCE here (multiple per file across sub-heuristics
    A+B) against a per-FILE denominator produced an impossible >100% rate
    (W371 unit-mismatch bug). ``details`` is one record per affected file;
    each record carries a ``count`` (total occurrences in this file) and
    ``occurrences`` (per-occurrence line/kind/snippet payload), so the
    finding emit can still produce one registry row per location.
    """
    files = conn.execute("SELECT id, path, language FROM files WHERE language IS NOT NULL").fetchall()

    found = 0
    total_files_scanned = 0
    details: list[dict] = []

    for f in files:
        lang = f["language"]
        if lang not in _COMMENT_CODE_PATTERNS and lang != "python":
            continue

        path = f["path"] or ""
        if _is_scaffolding_path(path):
            continue

        file_path = project_root / path
        try:
            source = file_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue

        total_files_scanned += 1
        occurrences = _scan_file_for_inflation(path, lang, source)

        if occurrences:
            # Count this FILE once toward ``found`` (per-file unit, matching
            # the per-file denominator). The per-occurrence total is kept in
            # the ``count`` field so the persist path still emits one row per
            # location and the verdict can report total occurrences.
            found += 1
            details.append(
                {
                    "file": path,
                    "count": len(occurrences),
                    "occurrences": occurrences,
                    "pattern": "boilerplate_inflation",
                }
            )

    return found, max(total_files_scanned, 1), details


# ---------------------------------------------------------------------------
# Composite score
# ---------------------------------------------------------------------------

# W371: ``_WEIGHTS`` intentionally LEAVES OUT ``modular_mirage`` and
# ``boilerplate_inflation`` so the canonical 0-100 AI rot score stays
# byte-identical pre- and post-W371. Adding them to the weight set would
# change every AI-rot number every downstream consumer reads. The new
# patterns are surfaced as ``weight: 0`` rows in the envelope and in the
# findings registry — informational, not score-bearing.
_WEIGHTS = {
    "dead_exports": 15,
    "short_churn": 10,
    "empty_handlers": 20,
    "abandoned_stubs": 10,
    "hallucinated_imports": 15,
    "error_inconsistency": 10,
    "comment_anomalies": 10,
    "copy_paste": 10,
}

# Patterns that the detector RUNS but that do NOT contribute to the
# canonical AI rot score. W371's two additions sit here. New
# informational patterns should be added to this set and to
# ``_PATTERN_NAMES`` but NOT to ``_WEIGHTS``.
_INFORMATIONAL_PATTERNS: frozenset[str] = frozenset(
    {
        "modular_mirage",
        "boilerplate_inflation",
    }
)

_PATTERN_NAMES = {
    "dead_exports": "Dead exports",
    "short_churn": "Short-term churn (<14d)",
    "empty_handlers": "Empty error handlers",
    "abandoned_stubs": "Abandoned stubs",
    "hallucinated_imports": "Hallucinated imports",
    "error_inconsistency": "Error handling inconsistency",
    "comment_anomalies": "Comment density anomalies",
    "copy_paste": "Copy-paste functions",
    "modular_mirage": "Modular Mirage (single-caller exports)",
    "boilerplate_inflation": "Boilerplate Inflation",
}

# Pattern 3 / W19: Where two commands compute the "same" metric
# differently, label every field with its precise definition so
# downstream consumers don't conflate them. Numbers are intentionally
# coarser than ``roam dead`` — see ``_detect_dead_exports`` docstring.
_DEAD_EXPORTS_DEFINITION = (
    "Public exported function/class/method symbols with ZERO incoming "
    "edges of any kind. Coarser than ``roam dead`` which requires zero "
    "*production* consumers AND survives the tooling-path exclusion "
    "(dev/, examples/, vendor/, etc.) AND the transitively-alive filter "
    "(barrel re-export survival). On a PHP-only codebase, vibe-check's "
    "count is typically 2-4x ``roam dead`` because of those three "
    "filters; both numbers are correct under their own definition. "
    "Use ``roam dead`` for an actionable deletion list."
)


def _compute_score(patterns: dict[str, dict]) -> int:
    """Compute weighted composite AI rot score (0-100)."""
    weighted_sum = 0.0
    total_weight = sum(_WEIGHTS.values())

    for key, weight in _WEIGHTS.items():
        pdata = patterns.get(key, {})
        rate = pdata.get("rate", 0.0)
        # Cap each rate at 100%
        capped = min(rate, 100.0)
        weighted_sum += capped * weight

    score = weighted_sum / total_weight
    return max(0, min(100, int(round(score))))


# ---------------------------------------------------------------------------
# Per-file aggregation for "worst files"
# ---------------------------------------------------------------------------


def _aggregate_worst_files(all_details: dict[str, list[dict]], limit: int = 5) -> list[dict]:
    """Aggregate per-file issue counts across all patterns.

    Returns top N files sorted by total issue count.
    """
    file_issues: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))

    for pattern_key, details in all_details.items():
        for d in details:
            fp = d.get("file", "")
            if not fp:
                continue
            count = d.get("count", 1)
            if "clone_group_size" in d:
                # copy-paste pattern uses different structure
                for fn in d.get("functions", []):
                    file_issues[fn.get("file", "")][pattern_key] += 1
                continue
            file_issues[fp][pattern_key] += count

    results = []
    for fp, patterns in file_issues.items():
        total = sum(patterns.values())
        # Build breakdown string
        parts = []
        for pkey, cnt in sorted(patterns.items(), key=lambda x: -x[1]):
            short_name = _PATTERN_NAMES.get(pkey, pkey).split()[0].lower()
            parts.append(f"{cnt} {short_name}")
        results.append(
            {
                "file": fp,
                "total_issues": total,
                "breakdown": ", ".join(parts),
                "pattern_counts": dict(patterns),
            }
        )

    results.sort(key=lambda x: -x["total_issues"])
    return results[:limit]


# ---------------------------------------------------------------------------
# W125: persist findings into the central registry
# ---------------------------------------------------------------------------
#
# Tier mapping per kind, derived from reading each detector's actual
# implementation (not the docstring claims). Reasoning:
#
# - dead_exports: pure graph query (incoming-edge count). Structural.
# - hallucinated_imports: graph query (file_edges with empty-symbol targets
#   + orphan target_id symbols). Structural.
# - copy_paste: deterministic block-hash on regex-normalised source —
#   3+ functions with the same hash form a clone group. Structural
#   evidence (the hash equality is an exact match), even though body
#   normalisation is regex-based.
# - empty_handlers, abandoned_stubs: regex pattern matching on raw source
#   (NOT AST traversal). Heuristic.
# - short_churn: combo of file commit count + 14-day time window.
#   Threshold heuristic.
# - error_inconsistency: regex pattern presence count per file with a
#   >=3 distinct patterns threshold. Heuristic.
# - comment_anomalies: statistical z-score on regex-counted comment lines.
#   Heuristic (NLP-flavoured).
#
# Diverges from the W125 brief on three kinds:
#  * empty_handlers + stubs: brief suggested static_analysis; the actual
#    detector is regex-on-source not AST. Downgraded to heuristic.
#  * hallucinated_imports: brief suggested static_analysis; the actual
#    detector is graph-table queries. Promoted to structural.
#  * copy_paste: brief suggested static_analysis; the actual detector is
#    a block-hash on normalised source. Kept at structural (the equality
#    test IS deterministic; the normalisation isn't AST-based).


def _vibe_check_tier(kind: str) -> str:
    """Map a vibe-check pattern kind to a registry confidence tier."""
    from roam.db.findings import (
        CONFIDENCE_HEURISTIC,
        CONFIDENCE_STRUCTURAL,
    )

    structural_kinds = {
        "dead_exports",
        "hallucinated_imports",
        "copy_paste",
        # W371: ``modular_mirage`` is a pure graph-edge query
        # (``GROUP BY target_id HAVING COUNT(*) = 1``). The signal IS
        # the structural fact "exactly 1 inbound edge" — deterministic,
        # same tier as ``dead_exports`` (which uses zero-inbound edges).
        "modular_mirage",
    }
    if kind in structural_kinds:
        return CONFIDENCE_STRUCTURAL
    # short_churn, empty_handlers, abandoned_stubs,
    # error_inconsistency, comment_anomalies,
    # boilerplate_inflation (W371: regex-on-source, same shape as
    # empty_handlers / abandoned_stubs).
    return CONFIDENCE_HEURISTIC


def _vibe_finding_id(kind: str, subject: str, line_start: int | None) -> str:
    """Stable, deterministic finding id for one vibe-check finding.

    The (kind, subject, line_start) triple is enough to re-identify the
    same finding across runs. ``subject`` is either a symbol qname or a
    file path; ``line_start`` is None for whole-file findings (short_churn,
    error_inconsistency, comment_anomalies). Re-running
    ``roam vibe-check --persist`` on the same input upserts the existing
    row rather than duplicating.
    """
    line_part = str(line_start) if line_start is not None else "0"
    raw = f"{kind}:{subject}:{line_part}"
    digest = hashlib.sha1(raw.encode("utf-8")).hexdigest()[:12]
    return f"vibe-check:{kind}:{digest}"


def _collect_dead_export_findings(conn) -> list[dict]:
    """Re-query dead-exports at symbol granularity for finding emit.

    ``_detect_dead_exports`` returns only counts. To emit one finding
    per dead export we re-run its SELECT with full symbol detail. Uses
    the SAME exclusion clauses and predicates as the count query — the
    row count returned here MUST match ``_detect_dead_exports``'s
    ``dead`` value or the migration would drift.
    """
    _EXCLUDE_SQL = (
        "AND f.path NOT LIKE '%test\\_%' ESCAPE '\\' "
        "AND f.path NOT LIKE '%\\_test.%' ESCAPE '\\' "
        "AND f.path NOT LIKE '%/tests/%' "
        "AND f.path NOT LIKE '%/test/%' "
        "AND f.path NOT LIKE '%conftest%' "
        "AND f.path NOT LIKE '%cmd\\_%' ESCAPE '\\' "
    )
    # W161: framework-hook allowlist — MUST mirror the WHERE clause used
    # by ``_detect_dead_exports`` so the row count returned here matches
    # that detector's ``dead`` value (the count and the per-symbol detail
    # are two reads of the same predicate; drift here means the findings
    # registry shows more rows than the verdict claims).
    hook_names = tuple(sorted(_FRAMEWORK_HOOK_NAMES))
    hook_placeholders = ",".join("?" * len(hook_names))
    _HOOK_SQL = f"AND s.name NOT IN ({hook_placeholders}) "
    rows = conn.execute(
        "SELECT s.id, s.name, s.kind, s.line_start, f.path AS file_path "
        "FROM symbols s "
        "JOIN files f ON s.file_id = f.id "
        "WHERE s.kind IN ('function', 'class', 'method') "
        "AND s.name NOT LIKE '\\_%' ESCAPE '\\' "
        "AND s.is_exported = 1 "
        "AND s.id NOT IN (SELECT target_id FROM edges) " + _EXCLUDE_SQL + _HOOK_SQL,
        hook_names,
    ).fetchall()
    return [
        {
            "symbol_id": int(r["id"]),
            "name": r["name"],
            "kind": r["kind"],
            "line_start": r["line_start"],
            "file_path": r["file_path"],
        }
        for r in rows
    ]


def _collect_copy_paste_findings(conn, project_root: Path) -> list[dict]:
    """Re-derive copy-paste groups at symbol granularity for finding emit.

    Mirrors ``_detect_copy_paste`` exactly — same body normalisation,
    same minimum-length filter, same >=3-group threshold. Returns one
    record per CLONE-GROUP MEMBER (so a group of 4 produces 4 records).
    The detector's ``found`` value is the sum across all groups, which
    this function reproduces by yielding one record per member.
    """
    functions = conn.execute(
        "SELECT s.id, s.name, s.kind, s.line_start, s.line_end, "
        "  f.path as file_path "
        "FROM symbols s JOIN files f ON s.file_id = f.id "
        "WHERE s.kind IN ('function', 'method') "
        "AND s.line_start IS NOT NULL AND s.line_end IS NOT NULL "
        "AND (s.line_end - s.line_start) >= 3"
    ).fetchall()
    if len(functions) < 3:
        return []

    by_file: dict[str, list] = defaultdict(list)
    for fn in functions:
        by_file[fn["file_path"]].append(fn)

    body_hashes: dict[str, list[dict]] = defaultdict(list)
    for file_path, fns in by_file.items():
        full_path = project_root / file_path
        try:
            source_lines = full_path.read_text(encoding="utf-8", errors="replace").split("\n")
        except OSError:
            continue
        for fn in fns:
            start = (fn["line_start"] or 1) - 1
            end = fn["line_end"] or start + 1
            body = "\n".join(source_lines[start:end])
            normalized = _normalize_body(body)
            if len(normalized) < 30:
                continue
            h = hashlib.md5(normalized.encode("utf-8")).hexdigest()
            body_hashes[h].append(
                {
                    "symbol_id": int(fn["id"]),
                    "name": fn["name"],
                    "file_path": file_path,
                    "line_start": fn["line_start"],
                    "line_end": fn["line_end"],
                    "group_hash": h,
                }
            )

    records: list[dict] = []
    for h, group in body_hashes.items():
        if len(group) >= 3:
            group_members = [{"name": m["name"], "file": m["file_path"], "line": m["line_start"]} for m in group]
            for m in group:
                rec = dict(m)
                rec["group_size"] = len(group)
                rec["group_members"] = group_members[:5]
                records.append(rec)
    return records


def _emit_vibe_check_findings(
    conn,
    findings_by_kind: dict[str, list[dict]],
    source_version: str,
) -> int:
    """Emit one ``FindingRecord`` per vibe-check finding into the registry.

    ``findings_by_kind`` maps each pattern key (``dead_exports``,
    ``short_churn``, ...) to a list of record dicts. The record shape is
    detector-specific; the helper inspects each kind to build the right
    claim string and evidence JSON.

    Returns the number of rows emitted. Wrapped by the caller in a
    defensive try/except so a pre-W89 DB (no ``findings`` table) silently
    no-ops rather than crashing the standard vibe-check command.

    W1256: each finding row stamps the per-pattern detector version
    (``_VIBE_KIND_TO_VERSION[kind]``) rather than the composite. The
    ``source_version`` parameter is retained as the fallback for unknown
    kinds so a future pattern lands here cleanly without a parallel edit
    to the lookup table.
    """
    from roam.db.findings import FindingRecord, emit_finding

    emitted = 0
    for kind, records in findings_by_kind.items():
        tier = _vibe_check_tier(kind)
        # W1256: per-pattern version stamp; falls back to composite for
        # any future kind that isn't yet in the lookup table.
        kind_version = _VIBE_KIND_TO_VERSION.get(kind, source_version)
        for rec in records:
            # Per-kind subject + claim shape.
            if kind == "dead_exports":
                subject_kind = "symbol"
                subject_id = rec.get("symbol_id")
                file_path = rec.get("file_path") or ""
                line_start = rec.get("line_start")
                finding_id = _vibe_finding_id(kind, f"{file_path}:{rec.get('name')}", line_start)
                claim = (
                    f"AI-rot dead-export: {rec.get('name')} ({rec.get('kind')}) at "
                    f"{file_path}:{line_start} — zero incoming edges"
                )
                evidence = {
                    "name": rec.get("name"),
                    "kind": rec.get("kind"),
                    "file_path": file_path,
                    "line_start": line_start,
                    "pattern": "dead_exports",
                    "note": (
                        "Coarser than `roam dead` — uses raw edges only, "
                        "no test-consumer / barrel / scaffolding filters."
                    ),
                }
            elif kind == "short_churn":
                subject_kind = "file"
                subject_id = None
                file_path = rec.get("file") or ""
                finding_id = _vibe_finding_id(kind, file_path, None)
                claim = f"AI-rot short-churn: {file_path} — {rec.get('commits')} commits over {rec.get('span_days')}d"
                evidence = {
                    "file_path": file_path,
                    "commits": rec.get("commits"),
                    "span_days": rec.get("span_days"),
                    "pattern": "short_churn",
                }
            elif kind == "empty_handlers":
                subject_kind = "file"
                subject_id = None
                file_path = rec.get("file") or ""
                finding_id = _vibe_finding_id(kind, file_path, None)
                count = rec.get("count", 0)
                claim = f"AI-rot empty-handlers: {file_path} — {count} empty error handler(s)"
                evidence = {
                    "file_path": file_path,
                    "count": count,
                    "pattern": "empty_handlers",
                }
            elif kind == "abandoned_stubs":
                subject_kind = "file"
                subject_id = None
                file_path = rec.get("file") or ""
                finding_id = _vibe_finding_id(kind, file_path, None)
                count = rec.get("count", 0)
                claim = f"AI-rot abandoned-stubs: {file_path} — {count} stub function(s)"
                evidence = {
                    "file_path": file_path,
                    "count": count,
                    "pattern": "abandoned_stubs",
                }
            elif kind == "hallucinated_imports":
                subject_kind = "file"
                subject_id = None
                file_path = rec.get("file") or ""
                finding_id = _vibe_finding_id(kind, file_path, None)
                count = rec.get("count", 0)
                claim = f"AI-rot hallucinated-imports: {file_path} — {count} unresolvable import(s)"
                evidence = {
                    "file_path": file_path,
                    "count": count,
                    "pattern": "hallucinated_imports",
                }
            elif kind == "error_inconsistency":
                subject_kind = "file"
                subject_id = None
                file_path = rec.get("file") or ""
                finding_id = _vibe_finding_id(kind, file_path, None)
                patterns = rec.get("patterns", [])
                claim = f"AI-rot error-inconsistency: {file_path} — {len(patterns)} distinct error patterns mixed"
                evidence = {
                    "file_path": file_path,
                    "patterns": list(patterns),
                    "count": rec.get("count", len(patterns)),
                    "pattern": "error_inconsistency",
                }
            elif kind == "comment_anomalies":
                subject_kind = "file"
                subject_id = None
                file_path = rec.get("file") or ""
                finding_id = _vibe_finding_id(kind, file_path, None)
                direction = rec.get("direction", "")
                ratio = rec.get("comment_ratio")
                z = rec.get("z_score")
                claim = f"AI-rot comment-anomaly: {file_path} — {direction} comments (ratio={ratio}, z={z})"
                evidence = {
                    "file_path": file_path,
                    "comment_ratio": ratio,
                    "z_score": z,
                    "direction": direction,
                    "pattern": "comment_anomalies",
                }
            elif kind == "copy_paste":
                subject_kind = "symbol"
                subject_id = rec.get("symbol_id")
                file_path = rec.get("file_path") or ""
                line_start = rec.get("line_start")
                finding_id = _vibe_finding_id(
                    kind,
                    f"{file_path}:{rec.get('name')}:{rec.get('group_hash')}",
                    line_start,
                )
                claim = (
                    f"AI-rot copy-paste: {rec.get('name')} at "
                    f"{file_path}:{line_start} — member of {rec.get('group_size')}-way clone group"
                )
                evidence = {
                    "name": rec.get("name"),
                    "file_path": file_path,
                    "line_start": line_start,
                    "line_end": rec.get("line_end"),
                    "group_size": rec.get("group_size"),
                    "group_members": rec.get("group_members", []),
                    "group_hash": rec.get("group_hash"),
                    "pattern": "copy_paste",
                }
            elif kind == "modular_mirage":
                # W371: one finding per single-caller exported helper.
                # subject is the symbol (not the file) so consumers can
                # join on ``symbols.id``.
                subject_kind = "symbol"
                subject_id = rec.get("symbol_id")
                file_path = rec.get("file_path") or ""
                line_start = rec.get("line_start")
                finding_id = _vibe_finding_id(kind, f"{file_path}:{rec.get('name')}", line_start)
                caller_file = rec.get("caller_file") or ""
                claim = (
                    f"AI-rot modular-mirage: {rec.get('name')} ({rec.get('kind')}) "
                    f"at {file_path}:{line_start} — exactly 1 cross-file caller "
                    f"({caller_file or 'unknown'})"
                )
                evidence = {
                    "name": rec.get("name"),
                    "kind": rec.get("kind"),
                    "file_path": file_path,
                    "line_start": line_start,
                    "caller_file": caller_file,
                    "caller_count": 1,
                    "pattern": "modular_mirage",
                    "research": "arxiv:2605.02741",
                }
            elif kind == "boilerplate_inflation":
                # W371: one finding per OCCURRENCE inside a file (not
                # one per file) — so a file with 4 comment-restates +
                # 1 shallow-wrapper produces 5 finding rows. The
                # caller of ``_emit_vibe_check_findings`` flattens the
                # per-file ``occurrences`` list before passing here.
                subject_kind = "file"
                subject_id = None
                file_path = rec.get("file_path") or rec.get("file") or ""
                line_start = rec.get("line")
                subkind = rec.get("subkind", "boilerplate_inflation")
                finding_id = _vibe_finding_id(kind, f"{file_path}:{subkind}", line_start)
                snippet = rec.get("snippet", "")
                claim = f"AI-rot boilerplate-inflation ({subkind}): {file_path}:{line_start} — {snippet[:80]}"
                evidence = {
                    "file_path": file_path,
                    "line": line_start,
                    "subkind": subkind,
                    "snippet": snippet,
                    "pattern": "boilerplate_inflation",
                    "research": "arxiv:2605.02741+2512.18020",
                }
            else:
                # Unknown kind — defensive skip rather than crash on a
                # future detector that forgets to register here.
                continue

            emit_finding(
                conn,
                FindingRecord(
                    finding_id_str=finding_id,
                    subject_kind=subject_kind,
                    subject_id=subject_id,
                    claim=claim,
                    evidence_json=json.dumps(evidence, sort_keys=True),
                    confidence=tier,
                    source_detector="vibe-check",
                    # W1256: per-pattern stamp (kind_version), not the
                    # composite (source_version). Falls back to the
                    # composite for any kind not yet in
                    # _VIBE_KIND_TO_VERSION (defensive forward-compat).
                    source_version=kind_version,
                ),
            )
            emitted += 1
    return emitted


# ---------------------------------------------------------------------------
# CLI command
# ---------------------------------------------------------------------------


@roam_capability(
    name="vibe-check",
    category="health",
    summary="Detect AI code anti-patterns and compute AI rot score",
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
@click.command("vibe-check")
@click.option("--threshold", type=int, default=0, help="Fail if AI rot score exceeds threshold (0=no gate)")
@click.option(
    "--persist",
    "persist",
    is_flag=True,
    default=False,
    help=(
        "Mirror each vibe-check finding into the central findings registry "
        "(``roam findings list --detector vibe-check``). Detector-specific "
        "output is unchanged; the registry rows are the denormalised "
        "cross-detector surface."
    ),
)
@click.pass_context
def vibe_check(ctx, threshold, persist):
    """Detect AI code anti-patterns and compute AI rot score.

    Unlike ``smells`` (which detects structural complexity anti-patterns from
    DB metrics) and ``health`` (which scores overall codebase fitness), this
    command specifically targets AI-generated code anti-patterns: dead exports,
    short-term churn, empty error handlers, abandoned stubs, hallucinated
    imports, and copy-paste functions.
    """
    json_mode = ctx.obj.get("json") if ctx.obj else False
    budget = ctx.obj.get("budget", 0) if ctx.obj else 0
    ensure_index()

    project_root = find_project_root()

    # W607-BS -- substrate-CALL marker plumbing on the 10-detector vibe-check
    # AI-rot pipeline. cmd_vibe_check is the LLM-rot sibling of cmd_smells
    # (W607-BN), the 831-findings-row detector listed in CLAUDE.md, with
    # ``static_analysis``/``structural`` confidence tiers and 8 score-bearing
    # ai-rot patterns plus 2 W371 informational patterns.
    #
    # Substrate boundaries wrapped:
    #
    #   * load_corpus                       -- file/symbol-count corpus probes
    #   * detect_dead_exports               -- pattern 1
    #   * detect_short_churn                -- pattern 2
    #   * detect_empty_handlers             -- pattern 3
    #   * detect_stubs                      -- pattern 4 (abandoned_stubs)
    #   * detect_hallucinated_imports       -- pattern 5
    #   * detect_error_inconsistency        -- pattern 6
    #   * detect_comment_anomalies          -- pattern 7
    #   * detect_copy_paste                 -- pattern 8
    #   * detect_modular_mirage             -- W371 informational pattern 9
    #   * detect_boilerplate_inflation      -- W371 informational pattern 10
    #   * aggregate_by_kind                 -- worst-files / patterns rollup
    #   * classify_severity                 -- _compute_score + _severity_label
    #   * emit_findings                     -- W125 findings-registry mirror
    #
    # Marker family ``vibe_check_<phase>_failed:<exc_class>:<detail>``.
    # Empty bucket -> byte-identical envelope on the happy path. Per-detector
    # isolation: a failure in one of the 10 detectors degrades that detector's
    # counts to (0, 0, []) and surfaces a single marker -- the remaining 9
    # detectors continue to report their findings (W607 canonical discipline).
    _w607bs_warnings_out: list[str] = []

    def _run_check_bs(phase, fn, *args, default=None, **kwargs):
        """Run one substrate helper with W607-BS marker emission.

        On a clean call the result is returned as-is. On an uncaught
        exception, surface a ``vibe_check_<phase>_failed:<exc_class>:<detail>``
        marker via ``_w607bs_warnings_out`` and return *default* -- the
        envelope still emits cleanly with the remaining substrates.
        """
        try:
            return fn(*args, **kwargs)
        except Exception as exc:  # noqa: BLE001 -- top-level disclosure
            _w607bs_warnings_out.append(f"vibe_check_{phase}_failed:{type(exc).__name__}:{exc}")
            return default

    with open_db(readonly=not persist) as conn:
        # W607-BS: per-detector isolation. Each of the 10 ai-rot patterns
        # runs through ``_run_check_bs`` so a raise in one detector
        # degrades to a zero-tuple for that pattern (the other 9 still
        # report). Empty-floor defaults match each detector's return
        # shape: 2-tuple for dead-exports, 3-tuple for the rest.
        _dead_result = _run_check_bs("detect_dead_exports", _detect_dead_exports, conn, default=(0, 0))
        p1_found, p1_total = _dead_result if _dead_result is not None else (0, 0)

        _short_result = _run_check_bs("detect_short_churn", _detect_short_churn, conn, default=(0, 0, []))
        p2_found, p2_total, p2_details = _short_result if _short_result is not None else (0, 0, [])

        _empty_result = _run_check_bs(
            "detect_empty_handlers",
            _detect_empty_handlers,
            conn,
            project_root,
            default=(0, 0, []),
        )
        p3_found, p3_total, p3_details = _empty_result if _empty_result is not None else (0, 0, [])

        _stubs_result = _run_check_bs("detect_stubs", _detect_stubs, conn, project_root, default=(0, 0, []))
        p4_found, p4_total, p4_details = _stubs_result if _stubs_result is not None else (0, 0, [])

        _halluc_result = _run_check_bs(
            "detect_hallucinated_imports",
            _detect_hallucinated_imports,
            conn,
            default=(0, 0, []),
        )
        p5_found, p5_total, p5_details = _halluc_result if _halluc_result is not None else (0, 0, [])

        _err_result = _run_check_bs(
            "detect_error_inconsistency",
            _detect_error_inconsistency,
            conn,
            project_root,
            default=(0, 0, []),
        )
        p6_found, p6_total, p6_details = _err_result if _err_result is not None else (0, 0, [])

        _comment_result = _run_check_bs(
            "detect_comment_anomalies",
            _detect_comment_anomalies,
            conn,
            project_root,
            default=(0, 0, []),
        )
        p7_found, p7_total, p7_details = _comment_result if _comment_result is not None else (0, 0, [])

        _cp_result = _run_check_bs(
            "detect_copy_paste",
            _detect_copy_paste,
            conn,
            project_root,
            default=(0, 0, []),
        )
        p8_found, p8_total, p8_details = _cp_result if _cp_result is not None else (0, 0, [])

        # W371: two additional informational detectors. NOT in
        # ``_WEIGHTS`` — the canonical AI rot score stays computed off
        # the 8 detectors above, so downstream consumers see the same
        # number pre- and post-W371.
        _mirage_result = _run_check_bs("detect_modular_mirage", _detect_modular_mirage, conn, default=(0, 0, []))
        p9_found, p9_total, p9_details = _mirage_result if _mirage_result is not None else (0, 0, [])

        _boiler_result = _run_check_bs(
            "detect_boilerplate_inflation",
            _detect_boilerplate_inflation,
            conn,
            project_root,
            default=(0, 0, []),
        )
        p10_found, p10_total, p10_details = _boiler_result if _boiler_result is not None else (0, 0, [])

        # --- W125: mirror into the central findings registry ---
        # Detector-specific output below is untouched; the registry rows
        # are the denormalised cross-detector surface (``roam findings``).
        # Wrapped so a pre-W89 DB (no ``findings`` table) silently no-ops
        # rather than crashing the standard vibe-check command path.
        if persist:
            # dead_exports needs per-symbol detail re-queried (the count
            # detector throws the rows away). copy_paste likewise needs
            # symbol-level membership rebuilt for finding emit.
            dead_export_records = _collect_dead_export_findings(conn)
            copy_paste_records = _collect_copy_paste_findings(conn, project_root)
            # W371: boilerplate_inflation reports per-FILE aggregates;
            # we flatten to per-OCCURRENCE records before emit so each
            # match becomes a distinct registry row.
            boilerplate_records: list[dict] = []
            for fd in p10_details:
                file_path = fd.get("file", "")
                for occ in fd.get("occurrences", []):
                    rec = dict(occ)
                    rec["file_path"] = file_path
                    boilerplate_records.append(rec)
            findings_by_kind: dict[str, list[dict]] = {
                "dead_exports": dead_export_records,
                "short_churn": p2_details,
                "empty_handlers": p3_details,
                "abandoned_stubs": p4_details,
                "hallucinated_imports": p5_details,
                "error_inconsistency": p6_details,
                "comment_anomalies": p7_details,
                "copy_paste": copy_paste_records,
                # W371 additions — informational, but persisted so the
                # findings-registry consumers (``roam findings list``,
                # SARIF emit, ``roam dashboard``) can read them.
                "modular_mirage": p9_details,
                "boilerplate_inflation": boilerplate_records,
            }
            # W607-BS: emit_findings substrate boundary. The pre-W89
            # schema path (sqlite3.OperationalError on missing
            # ``findings`` table) is the EXPECTED degraded path -- the
            # try/except below maintains the W125 silent no-op contract
            # for that case. Generic exceptions surface via the
            # ``vibe_check_emit_findings_failed:<exc>:<detail>`` marker.
            try:
                _emit_vibe_check_findings(
                    conn,
                    findings_by_kind,
                    source_version=VIBE_CHECK_DETECTOR_VERSION,
                )
                conn.commit()
            except sqlite3.OperationalError as _exc:
                # Expected: findings table missing (pre-W89 schema) —
                # degrade gracefully. Surface lineage so a non-expected
                # variant (locked / corrupt DB) is still discoverable.
                from roam.observability import log_swallowed

                log_swallowed("cmd_vibe_check:emit_findings", _exc)
            except Exception as _emit_exc:  # noqa: BLE001 -- W607-BS disclosure
                _w607bs_warnings_out.append(f"vibe_check_emit_findings_failed:{type(_emit_exc).__name__}:{_emit_exc}")

        # Build patterns dict
        def _rate(found, total):
            return round(found / max(total, 1) * 100, 1)

        patterns = {
            "dead_exports": {
                "found": p1_found,
                "total": p1_total,
                "rate": _rate(p1_found, p1_total),
            },
            "short_churn": {
                "found": p2_found,
                "total": p2_total,
                "rate": _rate(p2_found, p2_total),
            },
            "empty_handlers": {
                "found": p3_found,
                "total": p3_total,
                "rate": _rate(p3_found, p3_total),
            },
            "abandoned_stubs": {
                "found": p4_found,
                "total": p4_total,
                "rate": _rate(p4_found, p4_total),
            },
            "hallucinated_imports": {
                "found": p5_found,
                "total": p5_total,
                "rate": _rate(p5_found, p5_total),
            },
            "error_inconsistency": {
                "found": p6_found,
                "total": p6_total,
                "rate": _rate(p6_found, p6_total),
            },
            "comment_anomalies": {
                "found": p7_found,
                "total": p7_total,
                "rate": _rate(p7_found, p7_total),
            },
            "copy_paste": {"found": p8_found, "total": p8_total, "rate": _rate(p8_found, p8_total)},
            # W371 informational patterns — surfaced in the envelope and
            # findings registry but NOT used by ``_compute_score``.
            "modular_mirage": {
                "found": p9_found,
                "total": p9_total,
                "rate": _rate(p9_found, p9_total),
            },
            "boilerplate_inflation": {
                "found": p10_found,
                "total": p10_total,
                "rate": _rate(p10_found, p10_total),
            },
        }

        # W607-BS: classify_severity substrate boundary -- score
        # computation + severity-label classification. A raise here
        # (e.g. divide-by-zero on a malformed ``_WEIGHTS`` override)
        # degrades to a 0/HEALTHY tuple so the envelope still composes
        # with verdict + warnings_out.
        def _classify_score_and_severity(
            patterns_dict: dict[str, dict],
        ) -> tuple[int, str]:
            """W607-BS extracted helper: composite score + severity label."""
            return _compute_score(patterns_dict), _severity_label(_compute_score(patterns_dict))

        _classify_result = _run_check_bs(
            "classify_severity",
            _classify_score_and_severity,
            patterns,
            default=None,
        )
        if _classify_result is None:
            score = 0
            severity = "HEALTHY"
        else:
            score, severity = _classify_result
        # ``total_issues`` includes informational patterns so the
        # surface count tells an honest "raw issues seen" story even
        # though the SCORE is unaffected.
        total_issues = sum(p["found"] for p in patterns.values())

        # W607-BS: load_corpus substrate boundary -- the two SQL
        # COUNT(*) probes feed the W805-followup-A empty-corpus
        # disclosure. A raise here (e.g. transient cursor failure,
        # locked DB) degrades to ``(0, 0)`` so the empty-corpus path
        # fires cleanly with the substrate marker surfaced.
        def _probe_corpus(c) -> tuple[int, int]:
            """W607-BS extracted helper: files/symbols COUNT(*) probe."""
            f = c.execute("SELECT COUNT(*) FROM files").fetchone()[0]
            s = c.execute("SELECT COUNT(*) FROM symbols").fetchone()[0]
            return f, s

        _corpus_result = _run_check_bs("load_corpus", _probe_corpus, conn, default=None)
        if _corpus_result is None:
            files_scanned = 0
            symbols_count = 0
        else:
            files_scanned, symbols_count = _corpus_result

        # Severity per pattern
        for key, pdata in patterns.items():
            r = pdata["rate"]
            if r >= 30:
                pdata["severity"] = "high"
            elif r >= 10:
                pdata["severity"] = "medium"
            elif r > 0:
                pdata["severity"] = "low"
            else:
                pdata["severity"] = "none"

        # Worst files
        all_details = {
            "dead_exports": [],  # no per-file details for pattern 1
            "short_churn": p2_details,
            "empty_handlers": p3_details,
            "abandoned_stubs": p4_details,
            "hallucinated_imports": p5_details,
            "error_inconsistency": p6_details,
            "comment_anomalies": p7_details,
            "copy_paste": p8_details,
            # W371: include the informational patterns in worst-files
            # aggregation so an agent reading the worst-file list sees
            # boilerplate / mirage hot spots alongside score-bearing ones.
            "modular_mirage": p9_details,
            "boilerplate_inflation": p10_details,
        }
        # W607-BS: aggregate_by_kind substrate boundary -- worst-files
        # rollup over the 10-detector details dict. A raise here
        # degrades to an empty list so the envelope still emits the
        # patterns table + score (the worst-files block is purely
        # additive disclosure).
        _wf_result = _run_check_bs(
            "aggregate_by_kind",
            _aggregate_worst_files,
            all_details,
            default=None,
        )
        worst_files = [] if _wf_result is None else _wf_result

        # Recommendations
        recommendations = []
        if patterns["empty_handlers"]["found"] > 0:
            recommendations.append(
                f"Fix {patterns['empty_handlers']['found']} empty error handlers — silent failures hide bugs"
            )
        if patterns["dead_exports"]["found"] > 5:
            recommendations.append(
                f"Remove {patterns['dead_exports']['found']} dead exports — run `roam dead` for safe-delete candidates"
            )
        if patterns["abandoned_stubs"]["found"] > 0:
            recommendations.append(f"Complete or remove {patterns['abandoned_stubs']['found']} stub functions")
        if patterns["hallucinated_imports"]["found"] > 0:
            recommendations.append(f"Fix {patterns['hallucinated_imports']['found']} unresolvable imports")
        if patterns["copy_paste"]["found"] > 0:
            recommendations.append(
                f"Extract {patterns['copy_paste']['found']} copy-pasted functions into shared utilities"
            )
        # W371: informational-pattern recommendations name the same
        # follow-up commands an agent would otherwise have to guess.
        if patterns["modular_mirage"]["found"] > 5:
            recommendations.append(
                f"Review {patterns['modular_mirage']['found']} single-caller exports — "
                "inline or co-locate to remove modular-mirage abstractions"
            )
        if patterns["boilerplate_inflation"]["found"] > 0:
            _boiler_occurrences = sum(d.get("count", 0) for d in p10_details)
            recommendations.append(
                f"Trim {_boiler_occurrences} boilerplate-inflation occurrences across "
                f"{patterns['boilerplate_inflation']['found']} files "
                "(redundant comments + shallow wrappers)"
            )

        # W805-followup-A: empty-corpus disclosure (Pattern 2 silent-fallback fix).
        # When zero symbols are indexed all 8 AI-rot detectors collapse
        # to 0/100 ("HEALTHY") regardless of whether the corpus is
        # pristine or simply absent. Distinguish "pristine codebase"
        # (real success) from "nothing to analyze" (degraded/missing
        # input) via partial_success + a closed-enum state field.
        # ``files_scanned`` is the broader fallback (no files indexed
        # at all); ``symbols_count == 0`` covers the file-present-but-
        # symbol-empty case (e.g. zero-byte Python files). Per-detector
        # ``total`` values are individually clamped to ``max(_, 1)`` so
        # they are NOT a reliable empty-input signal. Mirrors W834 / W836 + W805.
        empty_corpus = files_scanned == 0 or symbols_count == 0
        if empty_corpus:
            verdict = "no files scanned (corpus empty — run `roam index --force` to populate)"
        else:
            verdict = f"AI rot score {score}/100 -- {severity}"

        # --- JSON output ---
        if json_mode:
            _summary = {
                "verdict": verdict,
                "score": score,
                "severity": severity,
                # W17.2 / Pattern 3c: name the axis the severity label
                # measures. vibe-check's severity is rot-axis (higher
                # score = more rot = worse); dashboard's `health.label`
                # is health-axis (higher score = healthier = better).
                # The label words coincidentally overlap ("HEALTHY")
                # but mean opposite things — agents that confuse them
                # ship bad decisions, so name the axis explicitly.
                "label_axis": "ai_rot_score",
                "label_axis_definition": (
                    "Severity label on the AI-rot axis (0-100, lower "
                    "= healthier). Bands: HEALTHY <=15, LOW <=35, "
                    "MODERATE <=55, HIGH <=75, CRITICAL >75. NOT the "
                    "same axis as dashboard's `health.label` "
                    "(project-health, higher = healthier)."
                ),
                "total_issues": total_issues,
                "files_scanned": files_scanned,
                "patterns_detected": sum(1 for p in patterns.values() if p["found"] > 0),
                # Pattern 3 label fix — vibe-check is the CANONICAL
                # source for AI rot. Downstream consumers (dashboard,
                # audit aggregates) read this label to confirm they
                # agree on what the number means. See
                # `the dogfood synthesis notes` Pattern 3.
                "ai_rot_score": score,
                "ai_rot_definition": AI_ROT_DEFINITION,
                # W19 / Pattern 3: the ``dead_exports`` count here
                # is COARSER than ``roam dead``'s by design (3.4x
                # observed on a PHP backend dogfood). Surface the
                # definition at envelope level so an agent reading
                # just the summary doesn't conflate the metrics.
                "dead_exports_metric_definition": _DEAD_EXPORTS_DEFINITION,
                "dead_exports_canonical_command": "roam dead",
            }
            # W805-followup-A: empty-corpus disclosure (Pattern 2). When
            # zero files are indexed, the 0/100 score is NOT a clean-run
            # success — it's a degraded state with no analyzable input.
            # Surface that via partial_success + closed-enum state field.
            if empty_corpus:
                _summary["partial_success"] = True
                _summary["state"] = "no_files_scanned"
            # W607-BS: surface the substrate-CALL marker bucket on BOTH
            # ``summary.warnings_out`` (so a consumer reading only the
            # summary block sees the degraded substrates) AND a top-level
            # ``warnings_out`` field (mirrors the W607-BN smells layout).
            # Empty bucket -> byte-identical envelope on the happy path
            # (the conditional avoids adding an empty list field).
            if _w607bs_warnings_out:
                _summary["partial_success"] = True
                _summary["warnings_out"] = list(_w607bs_warnings_out)
            envelope_kwargs: dict = {
                "budget": budget,
                "summary": _summary,
                "patterns": [
                    {
                        "name": key,
                        "label": _PATTERN_NAMES[key],
                        "found": pdata["found"],
                        "total": pdata["total"],
                        "rate": pdata["rate"],
                        "severity": pdata["severity"],
                        # W371: informational patterns sit at weight 0;
                        # consumers reading ``weight`` know which rows
                        # contribute to the score and which do not.
                        "weight": _WEIGHTS.get(key, 0),
                        "informational": key in _INFORMATIONAL_PATTERNS,
                        # Per-pattern definition: only ``dead_exports``
                        # has a documented W19 divergence right now;
                        # others may follow as we surface them.
                        **({"metric_definition": _DEAD_EXPORTS_DEFINITION} if key == "dead_exports" else {}),
                    }
                    for key, pdata in patterns.items()
                ],
                "worst_files": worst_files,
                "recommendations": recommendations,
                # LAW 11: surface the aggregate command so an agent
                # holding only the vibe-check envelope discovers
                # ``roam dashboard`` for the project-level summary.
                "next_steps": [
                    "roam dashboard for project-level summary",
                ],
            }
            if _w607bs_warnings_out:
                envelope_kwargs["warnings_out"] = list(_w607bs_warnings_out)
            envelope = json_envelope("vibe-check", **envelope_kwargs)
            click.echo(to_json(envelope))

            # Gate check
            if threshold > 0 and score > threshold:
                from roam.exit_codes import EXIT_GATE_FAILURE

                ctx.exit(EXIT_GATE_FAILURE)
            return

        # --- Text output ---
        click.echo(f"VERDICT: {verdict}")
        click.echo()

        # Pattern table — score-bearing patterns first, then the W371
        # informational patterns marked ``[info]`` so readers can tell
        # which rows feed the canonical AI-rot score.
        headers = ["Pattern", "Found", "Total", "Rate"]
        rows = []
        for key in _WEIGHTS:
            pdata = patterns[key]
            rate_str = f"{pdata['rate']:.1f}%"
            if pdata["rate"] >= 25:
                rate_str += "  !!"
            rows.append(
                [
                    _PATTERN_NAMES[key],
                    str(pdata["found"]),
                    str(pdata["total"]),
                    rate_str,
                ]
            )
        for key in sorted(_INFORMATIONAL_PATTERNS):
            pdata = patterns.get(key)
            if pdata is None:
                continue
            rate_str = f"{pdata['rate']:.1f}%"
            if pdata["rate"] >= 25:
                rate_str += "  !!"
            rows.append(
                [
                    f"{_PATTERN_NAMES[key]} [info]",
                    str(pdata["found"]),
                    str(pdata["total"]),
                    rate_str,
                ]
            )

        click.echo(format_table(headers, rows))
        click.echo()
        click.echo(f"  {score}/100 AI rot score (0=pristine, 100=severe)")
        click.echo(
            f"  {total_issues} issues across "
            f"{sum(1 for p in patterns.values() if p['found'] > 0)} categories "
            f"in {files_scanned} files"
        )

        # Worst files
        if worst_files:
            click.echo()
            click.echo("  Top worst files:")
            for wf in worst_files:
                click.echo(f"    {wf['file']:<50s} -- {wf['total_issues']} issues ({wf['breakdown']})")

        # Recommendations
        if recommendations:
            click.echo()
            click.echo("  Recommendations:")
            for rec in recommendations:
                click.echo(f"    - {rec}")

        # Gate check
        if threshold > 0 and score > threshold:
            click.echo()
            click.echo(f"  GATE FAILED: score {score} exceeds threshold {threshold}")
            from roam.exit_codes import EXIT_GATE_FAILURE

            ctx.exit(EXIT_GATE_FAILURE)
