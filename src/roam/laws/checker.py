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

import posixpath
import re
import subprocess
import sys
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Optional

from roam.laws.miner import Law, Violation
from roam.laws.namespace import detect_source_roots, namespace_contains, normalize_path, strip_source_root

# Symbol kinds this checker can actually see in a diff. ``added_symbols``
# below labels every Python/JS/Go declaration it recognises as exactly
# one of these two, so a law targeting any other kind matches nothing on
# every diff and can never raise a violation. The miner imports this set
# and declines to mine laws it cannot enforce (W1439) — widen it here and
# those laws start being mined again, in lockstep, automatically.
CHECKABLE_SYMBOL_KINDS = frozenset({"function", "class"})

# Modules that are never repo-internal, whatever the repo contains.
# ``sys.stdlib_module_names`` is the authoritative list for the running
# interpreter (3.10+); the hand-rolled predecessor was missing
# ``statistics``, which alone produced a false positive in this repo's
# trailing history — and would have kept producing one per newly-used
# stdlib module forever.
_STDLIB_MODULES = frozenset(sys.stdlib_module_names) | frozenset(
    {
        # Frequent third-party. Only consulted when the repo layout is
        # unavailable (see _module_is_internal); a readable repo answers
        # "is this ours?" from its own directory tree instead.
        "click",
        "pytest",
        "numpy",
        "pandas",
        "networkx",
        "requests",
        "yaml",
        "toml",
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


def get_diff_text_status(
    *,
    repo_root: Path,
    diff_source: str = "working",
    diff_file: Optional[str] = None,
    base_ref: str = "main",
) -> tuple[str, Optional[str]]:
    """Return ``(diff_text, error)`` for the requested source.

    ``error`` is ``None`` when the diff was produced successfully — note
    that a SUCCESSFUL diff can still be the empty string (a branch with no
    changes). When the diff could not be produced at all, ``error`` is a
    short human-readable reason.

    This split exists because :func:`get_diff_text` collapses four distinct
    failures (git missing, 30s timeout, non-zero git exit such as an
    unknown ``base_ref`` on a shallow CI clone, unreadable ``--diff-file``)
    into ``""`` — the same value a genuinely clean diff produces. Callers
    that gate CI on the result MUST be able to tell those apart, otherwise
    "we could not look" is reported as "we looked and it was fine".

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
            return "", "--diff-source file given without --diff-file"
        try:
            return Path(diff_file).read_text(encoding="utf-8", errors="replace"), None
        except (OSError, ValueError) as exc:
            return "", f"could not read --diff-file {diff_file!r}: {type(exc).__name__}"

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
    except FileNotFoundError:
        return "", "git executable not found"
    except subprocess.TimeoutExpired:
        return "", "git diff timed out after 30s"
    except OSError as exc:
        return "", f"git diff failed: {type(exc).__name__}"
    if result.returncode != 0:
        detail = (result.stderr or "").strip().splitlines()
        reason = detail[0] if detail else f"exit {result.returncode}"
        return "", f"`{' '.join(cmd)}` failed: {reason}"
    return result.stdout or "", None


def get_diff_text(
    *,
    repo_root: Path,
    diff_source: str = "working",
    diff_file: Optional[str] = None,
    base_ref: str = "main",
) -> str:
    """Return the unified-diff text, or ``""`` on any failure.

    Back-compat wrapper over :func:`get_diff_text_status`. Prefer the
    status form in gating code — this one cannot distinguish "no changes"
    from "could not compute the diff".
    """
    text, _error = get_diff_text_status(
        repo_root=repo_root,
        diff_source=diff_source,
        diff_file=diff_file,
        base_ref=base_ref,
    )
    return text


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
    source_roots: Optional[frozenset[str]] = None,
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
        Repo being checked. Used by the import law to read the repo's
        source layout (which prefixes are importable, which directories
        are the repo's own) and by the testing law when scanning for
        sibling test files inside the diff. Defaults to the current
        working directory — ``roam laws check`` runs inside the repo it
        checks, and an import law read against the wrong layout is worse
        than one read against none.
    source_roots
        Pre-computed source roots, bypassing detection. Mainly for
        callers that already know the layout (and for tests that want a
        layout independent of the filesystem).
    """
    if parsed is None:
        if diff is None:
            return []
        parsed = parse_added(diff)

    syms_added = added_symbols(parsed)
    root = repo_root if repo_root is not None else Path.cwd()
    roots = source_roots if source_roots is not None else detect_source_roots(root)

    violations: list[Violation] = []
    for law in laws:
        rkind = (law.rule or {}).get("kind") or law.kind
        if rkind == "naming":
            violations.extend(_check_naming_law(law, syms_added))
        elif rkind == "import":
            violations.extend(_check_import_law(law, parsed, roots, root))
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


def _check_import_law(
    law: Law,
    parsed: dict,
    source_roots: frozenset[str] = frozenset(),
    repo_root: Optional[Path] = None,
) -> list[Violation]:
    """Flag new imports that violate the (from_dir, to_dir) law.

    Specifically: when a file inside ``from_dir`` adds an import whose
    target lives **outside** the allowed ``to_dir`` (and is itself
    another repo-internal namespace), we flag it.

    Both sides are compared in the import namespace (W1439). The law's
    buckets arrive as directories and are normalised through
    :func:`~roam.laws.namespace.strip_source_root`, which also rescues
    laws mined by an older roam that recorded raw file paths
    (``src/roam``): they now name the same thing an import statement
    names (``roam``). Without that, under a ``src/`` layout the two
    namespaces never intersect, and the checker flags every conventional
    internal import while clearing ``from src.roam...`` — the one
    spelling that cannot resolve at runtime.

    The check is intentionally narrow: we only flag *new* imports
    added in the diff; we don't try to validate the entire transitive
    closure. Cheap, deterministic, agent-friendly.
    """
    rule = law.rule or {}
    from_ns = strip_source_root(rule.get("from_dir") or "", source_roots)
    to_ns = strip_source_root(rule.get("to_dir") or "", source_roots)
    if not from_ns:
        return []

    violations: list[Violation] = []
    for source_path, import_line in _iter_imports_that_can_break_boundary_law(parsed, from_ns, source_roots):
        violation = _violation_when_import_breaks_allowed_bucket(
            law, source_path, import_line, from_ns, to_ns, source_roots, repo_root
        )
        if violation:
            violations.append(violation)
    return violations


def _iter_imports_that_can_break_boundary_law(
    parsed: dict,
    from_ns: str,
    source_roots: frozenset[str] = frozenset(),
) -> Iterator[tuple[str, str]]:
    """Yield added imports from files governed by the import-boundary law.

    Governance is decided in the import namespace, so a law that says
    ``roam`` still governs the file the index stores as
    ``src/roam/commands/foo.py``. The *raw* path is what gets yielded —
    a violation has to point at a path the reader can open.
    """
    for path, entry in parsed.get("files", {}).items():
        norm = path.replace("\\", "/")
        if not namespace_contains(from_ns, strip_source_root(norm, source_roots)):
            continue
        for import_line in entry["added_imports"]:
            yield norm, import_line


def _violation_when_import_breaks_allowed_bucket(
    law: Law,
    source_path: str,
    import_line: str,
    from_ns: str,
    to_ns: str,
    source_roots: frozenset[str] = frozenset(),
    repo_root: Optional[Path] = None,
) -> Violation | None:
    """Return a violation only for new internal imports outside the law's namespace."""
    target = _import_target_namespace(source_path, import_line, source_roots)
    if not target:
        return None

    # Only internal cross-namespace traffic is governed. Stdlib and
    # third-party imports are nobody's layering violation.
    top_module = target.split("/", 1)[0]
    if not _module_is_internal(top_module, repo_root, source_roots):
        return None

    # Containment, not equality: the law names a directory (``roam``)
    # while the import names a module inside it (``roam/db/connection``).
    if namespace_contains(from_ns, target):
        return None  # same namespace — never a cross-boundary import
    if to_ns and namespace_contains(to_ns, target):
        return None

    target_bucket = _path_bucket(target)
    if not target_bucket:
        return None

    return Violation(
        law_id=law.id,
        kind="import",
        severity=law.severity,
        confidence=law.confidence,
        message=(f"{source_path} imports from {target_bucket}/ — law requires imports from {to_ns}/"),
        file=source_path,
        line=0,
        evidence={
            "import_line": import_line,
            "from_dir": from_ns,
            "to_dir": to_ns,
            "actual_target_dir": target_bucket,
        },
    )


def _import_target_namespace(source_path: str, import_line: str, source_roots: frozenset[str]) -> str:
    """Return what *import_line* names, in the import namespace.

    An absolute import already names the import namespace
    (``roam.db.connection`` -> ``roam/db/connection``). A *relative*
    import names a location instead, so it is resolved against the
    importing file's own directory and then re-expressed — otherwise
    ``from .helpers import x`` reads as a top-level package ``helpers``
    and every relative import in the repo becomes a layering violation.
    """
    relative = _relative_import_target(source_path, import_line)
    if relative is not None:
        return strip_source_root(relative, source_roots)
    return _resolve_import_target(import_line).replace("\\", "/").strip("/")


def _relative_import_target(source_path: str, import_line: str) -> Optional[str]:
    """Resolve a relative import to a repo path, or None if it isn't one."""
    stripped = import_line.strip()
    base = source_path.rpartition("/")[0]

    m = _PY_RELATIVE_IMPORT_RE.match(stripped)
    if m:
        dots, tail = m.group(1), m.group(2)
        # One dot is the current package; each extra dot climbs one level.
        for _ in range(len(dots) - 1):
            base = base.rpartition("/")[0]
        return "/".join(p for p in (base, tail.replace(".", "/")) if p)

    m = _JS_RELATIVE_IMPORT_RE.match(stripped)
    if m:
        return posixpath.normpath(posixpath.join(base, m.group(1))).replace("\\", "/")
    return None


def _module_is_internal(top_module: str, repo_root: Optional[Path], source_roots: frozenset[str]) -> bool:
    """Return True when *top_module* names something this repo owns.

    Asks the repo, not a hand-maintained list: a top-level import is
    internal when the checkout actually contains a directory or module
    of that name, at the repo root or under one of its source roots.
    That is the same question the miner answered from the index, and it
    is why a newly-used stdlib or third-party module can no longer
    become a false positive just by not having been listed yet.

    Falls back to the name allowlist when the repo is unreadable, which
    preserves the pre-W1439 conservative default (unknown -> internal).
    """
    if not top_module:
        return False
    if top_module in _STDLIB_MODULES:
        return False
    if repo_root is None:
        return True
    return _repo_owns_namespace(str(repo_root), top_module, source_roots)


@lru_cache(maxsize=2048)
def _repo_owns_namespace(repo_root: str, top_module: str, source_roots: frozenset[str]) -> bool:
    """Does *repo_root* contain a package/module named *top_module*?

    Cached: a diff repeats the same handful of top-level module names on
    every import line, and the answer cannot change mid-run.
    """
    root = Path(repo_root)
    for base in ("", *sorted(source_roots)):
        parent = root / base if base else root
        try:
            if (parent / top_module).is_dir():
                return True
            if any((parent / f"{top_module}{ext}").is_file() for ext in _MODULE_FILE_EXTENSIONS):
                return True
        except OSError:
            return True  # unreadable checkout — stay conservative
    return False


# Extensions consulted when asking "is this top-level name something this checkout
# owns?", used to distinguish a first-party module from a third-party package.
#
# 7 entries, covering the languages whose imports the two relative-import regexes
# below can actually parse: Python (.py/.pyi) and the JS/TS family (.js/.ts/.jsx/
# .tsx), plus .go. Adding an extension here without a matching import parser is
# inert — the checker would recognise the file as ours but never see an import
# statement naming it.
#
# W1440: this ownership probe replaced a hand-maintained third-party allowlist and
# closed 1 of the 4 false-positive classes measured in the src-layout defect —
# 47 violations raised / 0 true positives before the fix, 1 raised / 1 true after,
# over the same 29 non-merge commits.
_MODULE_FILE_EXTENSIONS = (".py", ".pyi", ".js", ".ts", ".jsx", ".tsx", ".go")

_PY_RELATIVE_IMPORT_RE = re.compile(r"^from\s+(\.+)([\w\.]*)\s+import\b")
_JS_RELATIVE_IMPORT_RE = re.compile(
    r"""^(?:import\s+.*?from\s+|import\s+|(?:const|let|var)\s+.*?=\s*require\()['"](\.{1,2}/[^'"]+)['"]"""
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
    """Name the namespace an offending import landed in, for the message.

    Reporting only — the conform / violate decision is made by
    :func:`~roam.laws.namespace.namespace_contains` against the whole
    target, never against this truncation. Trimming the module's own
    last segment (``roam/db/connection`` -> ``roam/db``) keeps the
    message at directory granularity, which is the granularity the law
    is stated in.
    """
    if not path:
        return ""
    norm = normalize_path(path)
    parts = norm.split("/")
    dirs = parts[:-1]
    if not dirs:
        # A bare top-level import (``import roam``): the module is its
        # own namespace.
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
