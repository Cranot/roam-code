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
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
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


@dataclass
class _DiffParseState:
    """Mutable parse state kept across diff lines."""

    files: dict[str, dict] = field(default_factory=dict)
    current_file: str | None = None
    current_new_line: int = 0
    pending_new_file: bool = False


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
    state = _DiffParseState()
    for raw in diff_text.splitlines():
        kind, payload = _classify_diff_line(raw)
        if kind == "diff_git":
            _handle_diff_git(state, payload)
        elif kind == "new_file_mode":
            _handle_new_file_mode(state)
        elif kind == "plus_plus":
            _handle_plus_plus(state, payload)
        elif kind == "hunk":
            _handle_hunk(state, payload)
        elif kind == "added":
            _handle_added_line(state, payload)
        elif kind == "context":
            _handle_context_line(state)
        # deletions don't advance new-line counter
    return {"files": state.files}


def _classify_diff_line(raw: str) -> tuple[str, str]:
    """Return (kind, payload) for a single diff line.

    Payload is the file path for ``diff_git`` / ``plus_plus``, the hunk
    start line number as a string for ``hunk``, the line text without the
    leading ``+`` for ``added``, and empty otherwise.
    """
    if raw.startswith("diff --git "):
        return "diff_git", raw
    if raw.startswith("new file mode"):
        return "new_file_mode", ""
    if raw.startswith("+++ b/"):
        return "plus_plus", raw[6:]
    if raw.startswith("@@"):
        return "hunk", raw
    if raw.startswith("+") and not raw.startswith("+++"):
        return "added", raw[1:]
    if raw.startswith(" "):
        return "context", ""
    return "other", ""


def _handle_diff_git(state: _DiffParseState, raw: str) -> None:
    """Start tracking a new file from a ``diff --git`` header.

    Parses the ``b/`` path eagerly so renames without a later ``+++ b/``
    line still get recorded.
    """
    state.current_file = None
    state.current_new_line = 0
    state.pending_new_file = False
    m = re.match(r"diff --git a/(.+?) b/(.+)$", raw)
    if not m:
        return
    state.current_file = m.group(2).replace("\\", "/")
    state.files.setdefault(state.current_file, _new_file_entry())


def _handle_new_file_mode(state: _DiffParseState) -> None:
    """Remember that the current file is a newly-created file."""
    state.pending_new_file = True
    if state.current_file is None:
        return
    entry = _ensure_entry(state, state.current_file)
    entry["added_full_file"] = True


def _handle_plus_plus(state: _DiffParseState, path_raw: str) -> None:
    """Switch to the file named after ``+++ b/`` and apply pending new-file state."""
    state.current_file = path_raw.replace("\\", "/")
    entry = _ensure_entry(state, state.current_file)
    if state.pending_new_file:
        entry["added_full_file"] = True


def _handle_hunk(state: _DiffParseState, raw: str) -> None:
    """Update the new-file line counter from a hunk header."""
    m = re.search(r"\+(\d+)(?:,\d+)?", raw)
    state.current_new_line = int(m.group(1)) if m else 0


def _handle_added_line(state: _DiffParseState, text: str) -> None:
    """Record one added line and detect imports."""
    if state.current_file is None:
        return
    entry = state.files.setdefault(state.current_file, _new_file_entry())
    entry["added_lines"].append((state.current_new_line, text))
    if _is_import_line(text):
        entry["added_imports"].append(text.strip())
    state.current_new_line += 1


def _handle_context_line(state: _DiffParseState) -> None:
    """Advance the new-file line counter for unchanged context lines."""
    state.current_new_line += 1


def _ensure_entry(state: _DiffParseState, path: str) -> dict:
    """Return the file entry for *path*, creating it if necessary."""
    return state.files.setdefault(path, _new_file_entry())


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
    except ImportError:
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
    for source_path, import_line in _iter_imports_that_can_break_boundary_law(parsed, from_dir):
        violation = _violation_when_import_breaks_allowed_bucket(law, source_path, import_line, from_dir, to_dir)
        if violation:
            violations.append(violation)
    return violations


def _iter_imports_that_can_break_boundary_law(parsed: dict, from_dir: str) -> Iterator[tuple[str, str]]:
    """Yield added imports from files governed by the import-boundary law."""
    for path, entry in parsed.get("files", {}).items():
        norm = path.replace("\\", "/")
        if not norm.startswith(from_dir + "/"):
            continue
        for import_line in entry["added_imports"]:
            yield norm, import_line


def _violation_when_import_breaks_allowed_bucket(
    law: Law,
    source_path: str,
    import_line: str,
    from_dir: str,
    to_dir: str,
) -> Violation | None:
    """Return a violation only for new internal imports outside the law bucket."""
    target = _resolve_import_target(import_line)
    if not target:
        return None

    # Skip stdlib / 3rd-party imports: the law only applies to internal
    # cross-directory traffic.
    top_module = target.replace("\\", "/").split("/", 1)[0]
    if top_module in _STDLIB_MODULES:
        return None

    target_bucket = _path_bucket(target)
    if not target_bucket:
        return None
    if target_bucket == from_dir:
        return None
    if to_dir and target_bucket == to_dir:
        return None

    return Violation(
        law_id=law.id,
        kind="import",
        severity=law.severity,
        confidence=law.confidence,
        message=(f"{source_path} imports from {target_bucket}/ — law requires imports from {to_dir}/"),
        file=source_path,
        line=0,
        evidence={
            "import_line": import_line,
            "from_dir": from_dir,
            "to_dir": to_dir,
            "actual_target_dir": target_bucket,
        },
    )


def _resolve_import_target(import_line: str) -> str:
    """Pull out the import path from an import statement.

    Handles Python (``from X import Y``, ``import X``) and JS
    (``import ... from 'x'``, ``require('x')``). Returns a normalised
    path-like string. Cross-language is fine — we only use this for
    coarse-bucket comparisons.
    """
    stripped = import_line.strip()
    import_patterns = (
        (r"^from\s+([\w\.]+)\s+import", lambda target: target.replace(".", "/")),
        (r"^import\s+([\w\.]+)", lambda target: target.replace(".", "/")),
        (r"^import\s+.*from\s+['\"]([^'\"]+)['\"]", lambda target: target.lstrip("./")),
        (r"^.*require\(['\"]([^'\"]+)['\"]\)", lambda target: target.lstrip("./")),
    )
    for pattern, normalize in import_patterns:
        m = re.match(pattern, stripped)
        if m:
            return normalize(m.group(1))
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


def _is_public_production_symbol(sym: dict, target_kind: str, is_test_file: Callable[[str], bool]) -> bool:
    """Return True when *sym* is a public, non-test symbol of the target kind.

    This is the eligibility gate that balances test-coverage breadth
    against false-positive avoidance: we only demand a matching test
    file for symbols that are (a) of the requested kind, (b) public
    (no leading underscore), and (c) defined outside an existing test
    file.
    """
    if sym["kind"] != target_kind:
        return False
    name = sym["name"]
    if name.startswith("_"):
        return False
    if is_test_file(sym["file"]):
        return False
    return True


def _collect_test_basenames(parsed: dict, is_test_file: Callable[[str], bool]) -> set[str]:
    """Build a lowercase-basename index of every test file touched by the diff.

    This normalizes the many framework-specific test-path conventions
    (``tests/test_*.py``, ``*_test.go``, ``*.test.ts``, …) into a single
    searchable set so the testing-law checker can answer "was a matching
    test added?" in O(1) rather than scanning the diff repeatedly.
    """
    basenames: set[str] = set()
    for path in parsed.get("files", {}):
        if is_test_file(path):
            basenames.add(path.rsplit("/", 1)[-1].lower())
    return basenames


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
    except ImportError:
        return []

    diff_test_basenames = _collect_test_basenames(parsed, is_test_file)

    violations: list[Violation] = []
    for sym in syms_added:
        if not _is_public_production_symbol(sym, target_kind, is_test_file):
            continue
        name = sym["name"]
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
