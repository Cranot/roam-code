"""Run a list of mined laws against a git diff and report violations.

The checker is intentionally diff-driven: it operates on the *new*
content added by the diff, not on the whole codebase. That way the
gate behaves predictably in CI (one PR = bounded violation count) and
agents that touched only a few files don't get drowned in pre-existing
violations.

Each :class:`~roam.laws.miner.Law` kind has a corresponding ``_check_*``
function below. The dispatcher (:func:`check_laws`) loops over the
laws and routes them by ``rule.kind``.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path
from typing import Optional

from roam.laws.miner import Law, Violation

# Small stdlib + common-3rd-party allowlist for the import-law checker.
# When a diff adds ``import sqlite3`` inside ``tests/`` we shouldn't flag
# it just because the mined law says ``tests -> src/roam``. The list is
# intentionally short — anything not here gets treated as internal,
# which is the conservative default (false-positive > false-negative
# when the gate is advisory).
_STDLIB_MODULES = frozenset(
    {
        # Python stdlib (most-imported)
        "os",
        "sys",
        "re",
        "json",
        "io",
        "math",
        "time",
        "datetime",
        "pathlib",
        "subprocess",
        "collections",
        "itertools",
        "functools",
        "typing",
        "dataclasses",
        "abc",
        "enum",
        "contextlib",
        "logging",
        "sqlite3",
        "shutil",
        "tempfile",
        "textwrap",
        "argparse",
        "ast",
        "asyncio",
        "copy",
        "csv",
        "hashlib",
        "hmac",
        "html",
        "http",
        "inspect",
        "operator",
        "pickle",
        "platform",
        "random",
        "secrets",
        "socket",
        "ssl",
        "string",
        "struct",
        "threading",
        "traceback",
        "unittest",
        "urllib",
        "uuid",
        "warnings",
        "weakref",
        "xml",
        "zipfile",
        "zlib",
        "__future__",
        "importlib",
        # Frequent third-party
        "click",
        "pytest",
        "numpy",
        "pandas",
        "networkx",
        "requests",
        "yaml",
        "toml",
        "tomllib",
        "tomli",
        "tree_sitter",
        "tree_sitter_language_pack",
        "fastmcp",
        "anthropic",
        "watchdog",
        "rich",
        "tabulate",
        "ruff",
    }
)


# ---------------------------------------------------------------------------
# Diff sourcing
# ---------------------------------------------------------------------------


def get_diff_text(
    *,
    repo_root: Path,
    diff_source: str = "working",
    diff_file: Optional[str] = None,
    base_ref: str = "main",
) -> str:
    """Return the unified-diff text for the requested source.

    Parameters
    ----------
    repo_root
        Path to the git repo root.
    diff_source
        One of ``working`` / ``staged`` / ``head`` / ``pr`` / ``file``.
        When ``file``, *diff_file* must be set and that path is read
        instead of running git.
    diff_file
        Path to a saved diff file (used when ``diff_source == "file"``).
    base_ref
        Base ref for ``pr`` mode (default ``main``).
    """
    if diff_source == "file":
        if not diff_file:
            return ""
        try:
            return Path(diff_file).read_text(encoding="utf-8", errors="replace")
        except (OSError, ValueError):
            return ""

    cmd = ["git", "diff", "--unified=3"]
    if diff_source == "staged":
        cmd.append("--cached")
    elif diff_source == "pr":
        cmd.append(f"{base_ref}...HEAD")
    elif diff_source == "head":
        cmd.append("HEAD")
    # else: working-tree default — no extra arg

    try:
        result = subprocess.run(
            cmd,
            cwd=str(repo_root),
            capture_output=True,
            text=True,
            timeout=30,
            encoding="utf-8",
            errors="replace",
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return ""
    if result.returncode != 0:
        return ""
    return result.stdout or ""


# ---------------------------------------------------------------------------
# Tiny diff parser (added lines + added files)
# ---------------------------------------------------------------------------


def parse_added(diff_text: str) -> dict:
    """Parse a unified-diff into a structure the checkers can consume.

    Returns::

        {
          "files": {
              "src/foo.py": {
                  "added_lines": [(lineno, text), ...],
                  "added_full_file": bool,  # True iff "new file mode"
                  "added_imports": [str, ...],  # raw `import X` / `from X` lines
              },
              ...
          }
        }
    """
    files: dict[str, dict] = {}
    current_file: str | None = None
    current_new_line = 0
    pending_new_file = False

    for raw in diff_text.splitlines():
        if raw.startswith("diff --git "):
            current_file = None
            current_new_line = 0
            pending_new_file = False
            # Try to parse the b/ path right away — handles renames where
            # there's no "+++ b/" line later.
            m = re.match(r"diff --git a/(.+?) b/(.+)$", raw)
            if m:
                current_file = m.group(2).replace("\\", "/")
                files.setdefault(current_file, _new_file_entry())
            continue
        if raw.startswith("new file mode"):
            pending_new_file = True
            if current_file:
                files.setdefault(current_file, _new_file_entry())
                files[current_file]["added_full_file"] = True
            continue
        if raw.startswith("+++ b/"):
            current_file = raw[6:].replace("\\", "/")
            files.setdefault(current_file, _new_file_entry())
            if pending_new_file:
                files[current_file]["added_full_file"] = True
            continue
        if raw.startswith("@@"):
            m = re.search(r"\+(\d+)(?:,\d+)?", raw)
            current_new_line = int(m.group(1)) if m else 0
            continue
        if raw.startswith("+") and not raw.startswith("+++"):
            if current_file is None:
                continue
            entry = files.setdefault(current_file, _new_file_entry())
            text = raw[1:]
            entry["added_lines"].append((current_new_line, text))
            if _is_import_line(text):
                entry["added_imports"].append(text.strip())
            current_new_line += 1
        elif raw.startswith(" "):
            current_new_line += 1
        # deletions don't advance new-line counter

    return {"files": files}


def _new_file_entry() -> dict:
    return {"added_lines": [], "added_full_file": False, "added_imports": []}


_IMPORT_PATTERNS = (
    re.compile(r"^\s*(?:from\s+([\w\.]+)\s+import\s+|import\s+([\w\.]+))"),  # Python
    re.compile(r"^\s*import\s+.*from\s+['\"]([^'\"]+)['\"]"),  # JS/TS ES
    re.compile(r"^\s*const\s+\w+\s*=\s*require\(['\"]([^'\"]+)['\"]\)"),  # CommonJS
)


def _is_import_line(text: str) -> bool:
    stripped = text.lstrip()
    if not stripped:
        return False
    if stripped.startswith(("import ", "from ", "require(")):
        return True
    # Detect ES-module `import foo from 'bar'` or `const x = require(...)`
    return any(p.match(stripped) for p in _IMPORT_PATTERNS)


# ---------------------------------------------------------------------------
# Added-symbol detection (cheap regex — good enough for diff scope)
# ---------------------------------------------------------------------------


_PY_DEF_RE = re.compile(r"^\s*(?:async\s+)?def\s+(\w+)\s*\(")
_PY_CLASS_RE = re.compile(r"^\s*class\s+(\w+)\s*[:\(]")
_JS_FN_RE = re.compile(r"^\s*(?:export\s+)?(?:async\s+)?function\s+(\w+)\s*\(")
_JS_CLASS_RE = re.compile(r"^\s*(?:export\s+)?class\s+(\w+)\b")
_GO_FN_RE = re.compile(r"^\s*func\s+(?:\([^)]*\)\s*)?(\w+)\s*\(")
_GO_TYPE_RE = re.compile(r"^\s*type\s+(\w+)\s+(?:struct|interface)\b")


def added_symbols(parsed: dict) -> list[dict]:
    """Return ``[{name, kind, file, line}, ...]`` for newly added symbols.

    Heuristic — uses the same regexes ``cmd_delete_check`` uses on the
    delete-side. Good enough to power naming / testing law checks.
    """
    out: list[dict] = []
    for path, entry in parsed.get("files", {}).items():
        for lineno, text in entry["added_lines"]:
            for rx, kind in (
                (_PY_DEF_RE, "function"),
                (_PY_CLASS_RE, "class"),
                (_JS_FN_RE, "function"),
                (_JS_CLASS_RE, "class"),
                (_GO_FN_RE, "function"),
                (_GO_TYPE_RE, "class"),
            ):
                m = rx.match(text)
                if m:
                    out.append(
                        {
                            "name": m.group(1),
                            "kind": kind,
                            "file": path,
                            "line": lineno,
                        }
                    )
                    break
    return out


# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------


def check_laws(
    laws: list[Law],
    diff: str | None = None,
    *,
    parsed: dict | None = None,
    conn=None,
    repo_root: Optional[Path] = None,
) -> list[Violation]:
    """Run every law against the (parsed) diff and collect violations.

    Parameters
    ----------
    laws
        List of :class:`~roam.laws.miner.Law` to enforce.
    diff
        Unified-diff text. If ``None`` and *parsed* is also ``None``,
        no violations are returned. Callers that already have a parsed
        diff can pass it via *parsed* to avoid re-parsing.
    parsed
        Pre-parsed result from :func:`parse_added`.
    conn
        Optional DB connection. Used by co-change checks (stub for v1).
    repo_root
        Optional repo root. Used by testing law when scanning for sibling
        test files inside the diff (so the gate doesn't false-positive
        when the PR adds the test alongside the new symbol).
    """
    if parsed is None:
        if diff is None:
            return []
        parsed = parse_added(diff)

    syms_added = added_symbols(parsed)

    violations: list[Violation] = []
    for law in laws:
        rkind = (law.rule or {}).get("kind") or law.kind
        if rkind == "naming":
            violations.extend(_check_naming_law(law, syms_added))
        elif rkind == "import":
            violations.extend(_check_import_law(law, parsed))
        elif rkind == "testing":
            violations.extend(_check_testing_law(law, parsed, syms_added))
        elif rkind == "errors":
            # Stub kind — checker no-op for v1.
            pass
        elif rkind == "co_change":
            violations.extend(_check_cochange_law(law, parsed))
    return violations


# ---------------------------------------------------------------------------
# Per-kind checkers
# ---------------------------------------------------------------------------


def _check_naming_law(law: Law, syms_added: list[dict]) -> list[Violation]:
    """Flag any newly-added symbol whose name doesn't match the law's
    case style.

    Skips symbols of the wrong ``kind`` and any name that the canonical
    classifier rejects (dunders, single-letter, etc.).
    """
    try:
        from roam.commands.cmd_conventions import classify_case
    except Exception:
        return []

    rule = law.rule or {}
    target_kind = rule.get("symbol_kind") or ""
    expected_style = rule.get("style") or law.evidence.get("style") or ""
    if not target_kind or not expected_style:
        return []

    violations: list[Violation] = []
    for sym in syms_added:
        if sym["kind"] != target_kind:
            continue
        actual = classify_case(sym["name"])
        if actual is None:
            continue
        if actual != expected_style:
            violations.append(
                Violation(
                    law_id=law.id,
                    kind="naming",
                    severity=law.severity,
                    confidence=law.confidence,
                    message=(f"{sym['kind']} '{sym['name']}' is {actual}, expected {expected_style}"),
                    file=sym["file"],
                    line=sym["line"],
                    evidence={
                        "actual_style": actual,
                        "expected_style": expected_style,
                        "symbol_kind": sym["kind"],
                    },
                )
            )
    return violations


def _check_import_law(law: Law, parsed: dict) -> list[Violation]:
    """Flag new imports that violate the (from_dir, to_dir) law.

    Specifically: when a file inside ``from_dir`` adds an import whose
    resolved-target path lives **outside** the allowed ``to_dir`` (and
    is itself another repo-internal directory), we flag it.

    The check is intentionally narrow: we only flag *new* imports
    added in the diff; we don't try to validate the entire transitive
    closure. Cheap, deterministic, agent-friendly.
    """
    rule = law.rule or {}
    from_dir = rule.get("from_dir") or ""
    to_dir = rule.get("to_dir") or ""
    if not from_dir:
        return []

    violations: list[Violation] = []
    for path, entry in parsed.get("files", {}).items():
        norm = path.replace("\\", "/")
        if not norm.startswith(from_dir + "/"):
            continue
        for imp in entry["added_imports"]:
            target = _resolve_import_target(imp)
            if not target:
                continue
            # Skip stdlib / 3rd-party imports — the law only applies to
            # internal cross-directory traffic. We use a small built-in
            # stdlib list because we don't want to depend on
            # ``sys.stdlib_module_names`` (only available on 3.10+).
            top_module = target.replace("\\", "/").split("/", 1)[0]
            if top_module in _STDLIB_MODULES:
                continue
            # Cross-bucket internal import — only flag if it goes to a
            # different top-bucket than the law's allowed ``to_dir``.
            target_bucket = _path_bucket(target)
            if not target_bucket:
                continue
            if target_bucket == from_dir:
                # same-bucket internal — allowed
                continue
            if to_dir and target_bucket == to_dir:
                # canonical target — allowed
                continue
            # Anything else is a cross-bucket import that violates the law.
            violations.append(
                Violation(
                    law_id=law.id,
                    kind="import",
                    severity=law.severity,
                    confidence=law.confidence,
                    message=(f"{norm} imports from {target_bucket}/ — law requires imports from {to_dir}/"),
                    file=norm,
                    line=0,
                    evidence={
                        "import_line": imp,
                        "from_dir": from_dir,
                        "to_dir": to_dir,
                        "actual_target_dir": target_bucket,
                    },
                )
            )
    return violations


def _resolve_import_target(import_line: str) -> str:
    """Pull out the import path from an import statement.

    Handles Python (``from X import Y``, ``import X``) and JS
    (``import ... from 'x'``, ``require('x')``). Returns a normalised
    path-like string. Cross-language is fine — we only use this for
    coarse-bucket comparisons.
    """
    stripped = import_line.strip()
    # Python: from X import Y
    m = re.match(r"^from\s+([\w\.]+)\s+import", stripped)
    if m:
        return m.group(1).replace(".", "/")
    # Python: import X
    m = re.match(r"^import\s+([\w\.]+)", stripped)
    if m:
        return m.group(1).replace(".", "/")
    # JS ES module
    m = re.match(r"^import\s+.*from\s+['\"]([^'\"]+)['\"]", stripped)
    if m:
        return m.group(1).lstrip("./")
    # CommonJS
    m = re.match(r"^.*require\(['\"]([^'\"]+)['\"]\)", stripped)
    if m:
        return m.group(1).lstrip("./")
    return ""


def _path_bucket(path: str) -> str:
    """Top-two-directory-segment bucket (mirrors :func:`miner._import_bucket`).

    Drops the basename so paths inside the same directory collapse to
    one bucket. Import targets resolved from source — e.g. the dotted
    module ``src.db.users`` returned by :func:`_resolve_import_target`
    as ``src/db/users`` — also get their last segment trimmed so the
    target bucket matches the miner's law.
    """
    if not path:
        return ""
    norm = path.replace("\\", "/").lstrip("./")
    parts = norm.split("/")
    dirs = parts[:-1]
    if not dirs:
        # No directory part. Treat the single segment as its own bucket
        # so a top-level import (``from foo import bar``) still resolves
        # to ``foo``. We're matching against the miner here — the miner
        # only emits laws when a real directory dominates, so this
        # branch is mostly for synthetic test diffs.
        return parts[0]
    return "/".join(dirs[:2])


def _check_testing_law(law: Law, parsed: dict, syms_added: list[dict]) -> list[Violation]:
    """Flag newly-added public symbols of the matching kind when no
    test file with their name is also added in the same diff.

    Conservative: only flags symbols whose name doesn't start with ``_``
    and whose source file isn't itself a test file.
    """
    rule = law.rule or {}
    target_kind = rule.get("symbol_kind") or ""
    if not target_kind:
        return []

    # W898-followup-B: delegate to the canonical changed_files.is_test_file
    # (which factors through file_roles + the 22-language test_conventions
    # adapter framework). Lazy-imported intra-function because the
    # _check_testing_law function is called per-law during diff-driven
    # checking, not at module-import time — keeping the import lazy
    # matches the sibling _check_naming_law pattern in this same file
    # and avoids paying the file_roles import cost on every `roam laws`
    # cold start. No import cycle exists between roam.laws and
    # roam.commands.changed_files (verified W898-followup-B); the
    # try/except guards against future packaging or partial-install
    # breakage and degrades gracefully by skipping the law rather than
    # silently re-introducing a narrower test-path heuristic.
    try:
        from roam.commands.changed_files import is_test_file
    except Exception:
        return []

    # Build the set of test-file basenames touched by the diff.
    diff_test_basenames: set[str] = set()
    for path in parsed.get("files", {}):
        if is_test_file(path):
            diff_test_basenames.add(path.rsplit("/", 1)[-1].lower())

    violations: list[Violation] = []
    for sym in syms_added:
        if sym["kind"] != target_kind:
            continue
        name = sym["name"]
        if name.startswith("_"):
            continue
        if is_test_file(sym["file"]):
            continue
        if _has_matching_test_in_diff(name, diff_test_basenames):
            continue
        violations.append(
            Violation(
                law_id=law.id,
                kind="testing",
                severity=law.severity,
                confidence=law.confidence,
                message=(f"public {sym['kind']} '{name}' added without a matching test file"),
                file=sym["file"],
                line=sym["line"],
                evidence={
                    "symbol_kind": sym["kind"],
                    "expected_test_pattern": "test_<name>.py / <name>.test.* / <name>_test.go",
                },
            )
        )
    return violations


def _has_matching_test_in_diff(name: str, basenames: set[str]) -> bool:
    if not name or not basenames:
        return False
    low = name.lower()
    candidates = (
        f"test_{low}.py",
        f"{low}_test.py",
        f"{low}.test.js",
        f"{low}.test.ts",
        f"{low}.spec.js",
        f"{low}.spec.ts",
        f"{low}_test.go",
        f"{low}_spec.rb",
    )
    if any(c in basenames for c in candidates):
        return True
    for bn in basenames:
        if low in bn:
            return True
    return False


def _check_cochange_law(law: Law, parsed: dict) -> list[Violation]:
    """v1 stub for co-change enforcement.

    The mining side currently returns no laws of this kind, so this
    checker is a deliberate no-op. Documented as a seam for follow-up
    work: when ``trigger`` file is in the diff but any expected
    partner isn't, emit a violation.
    """
    return []
