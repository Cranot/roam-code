"""Shared utilities for resolving changed files from git."""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

from roam.index.file_roles import classify_file
from roam.index.file_roles import is_test as _roles_is_test

# ---------------------------------------------------------------------------
# Test / low-risk file detection
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# W1512 — segment-and-qualify test-directory detection.
#
# History. The pre-W1511 form was ``any(d in p_lower for d in ["tests/",
# "test/", "__tests__/", "spec/", "testing/"])``. Every pattern carries a
# trailing ``/``, so the substring could only land at a component boundary
# and the effective rule was "any component ENDING WITH a test token" —
# ``latest/``, ``contest/``, ``attest/``, ``backtesting/`` all matched.
# W1511 replaced that with an exact/delimiter/camelCase component match,
# which fixed the ``latest/`` class but was refuted in BOTH directions:
#
#   * still True and must be False — ``Illuminate/Testing/`` (a shipped
#     Laravel package, 74 production .php files), ``SupportTesting/``,
#     ``image-spec/``, ``openapi-spec/``, ``tokio-test/`` (a shipped helper
#     crate), and ``backTesting`` (which contradicted its own pins);
#   * still False and must be True — ``unittests/`` (LLVM), ``unittest/``,
#     ``jstests/`` (MongoDB's whole JS suite), ``apitests/``,
#     ``smoketests/``, ``e2etests/``, ``regressiontests/``,
#     ``acceptancetests/``, ``systemtest/``, ``foo__tests__/``.
#
# The rule below segments a component on delimiters and camel-case
# boundaries and then asks a QUALIFIER question about the segment
# preceding the test token. That is what separates ``unittests`` (unit +
# tests) from ``backtests`` (back + tests): the token is identical, the
# qualifier is not.
#
# Deliberately absent from _QUALIFIERS and load-bearing: ``py`` (keeps
# ``pytest/`` False), ``doc`` (keeps the vendored ``doctest/`` C++
# framework False), ``vi`` (keeps ``vitest/`` False), and every English
# stem (``la``, ``at``, ``con``, ``pro``, ``grea``, ``back``, ``pre``,
# ``re``, ``my``) so ``latest``/``attest``/``contest``/``protest``/
# ``greatest``/``backtests``/``pretest``/``respec``/``myspec`` stay
# production.
#
# The predicate is a pure function of the component string. Control
# characters are rejected up front, which removes the ``$``-vs-``\Z``
# anchor defect BY CONSTRUCTION rather than by patching each pattern:
# ``"MyTest\n"`` is not a directory name, and no regex in here can be
# fooled by a trailing newline because no regex in here sees one.
# ---------------------------------------------------------------------------

_CTRL = re.compile(r"[\x00-\x1f\x7f]")
_DELIM = re.compile(r"[-_. ]+")
# camelCase boundary: lower/digit -> upper  (androidTest -> android|Test)
_CAMEL1 = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")
# acronym boundary: upper -> upper+lower    (UITests -> UI|Tests)
_CAMEL2 = re.compile(r"(?<=[A-Z])(?=[A-Z][a-z])")

_EXACT = frozenset({"test", "tests", "spec", "__tests__"})

# Segments that legitimately qualify a test token. An allowlist, and its
# misses are all False (a test directory quietly promoted to production
# source), so it is enumerated under ROAM_DEBUG_TEST_DIRS — see
# ``debug_unqualified_test_components``.
_QUALIFIERS = frozenset(
    {
        # scope / level
        "unit",
        "units",
        "integration",
        "integ",
        "functional",
        "acceptance",
        "regression",
        "smoke",
        "sanity",
        "e2e",
        "endtoend",
        "system",
        "sys",
        "component",
        "contract",
        "conformance",
        "compat",
        "compatibility",
        "interop",
        "migration",
        # non-functional
        "perf",
        "performance",
        "load",
        "stress",
        "soak",
        "security",
        "fuzz",
        "property",
        "snapshot",
        "golden",
        "approval",
        "mutation",
        "characterization",
        # surface under test
        "api",
        "ui",
        "gui",
        "web",
        "browser",
        "cli",
        "db",
        "http",
        "rest",
        "grpc",
        # platform / source set
        "android",
        "ios",
        "shared",
        "instrumentation",
        "device",
        "emulator",
        "native",
        # language tags used as directory prefixes (js/jstests, go/gotests)
        "js",
        "ts",
        "go",
        "java",
        "lib",
        "net",
    }
)


def _camel_parts(chunk: str) -> list[str]:
    """Split one delimiter-free chunk on camelCase / acronym boundaries."""
    parts: list[str] = []
    for piece in _CAMEL1.split(chunk):
        parts.extend(p for p in _CAMEL2.split(piece) if p)
    return parts


def _segments(component: str) -> list[str]:
    """Lowercased delimiter + camelCase segments of one path component."""
    segs: list[str] = []
    for chunk in _DELIM.split(component):
        if not chunk:
            continue
        segs.extend(p.lower() for p in _camel_parts(chunk))
    return segs


def _camel_before_last(component: str) -> bool:
    """True when the component's LAST delimiter chunk is itself camel-split.

    This is the orthographic signal that distinguishes ``androidTest`` and
    ``UserSpec`` (a capital ``T``/``S`` inside a word) from ``latest`` and
    ``myspec``, and it is checked on the last chunk specifically so that
    ``tokio-test`` / ``image-spec`` — delimiter-separated, no internal
    capital — do not qualify.
    """
    chunks = [c for c in _DELIM.split(component) if c]
    if not chunks:
        return False
    return len(_camel_parts(chunks[-1])) >= 2


def _is_test_dir_component(component: str) -> bool:
    """Return True when one path component names a test directory.

    The component is segmented on delimiters (``-_. ``) and camelCase
    boundaries; the decision then keys off the LAST segment and, when the
    last segment is a test token, off the segment before it.

    True:  ``tests`` ``test`` ``spec`` ``__tests__`` ``testing`` ``TESTING``
           ``integration-tests`` ``api_tests`` ``MyProject.Tests``
           ``androidTest`` ``MyAppUITests`` ``UserSpec``
           ``unittests`` ``unittest`` ``jstests`` ``apitests`` ``smoketests``
           ``e2etests`` ``regressiontests`` ``acceptancetests`` ``systemtest``
    False: ``latest`` ``greatest`` ``contest`` ``protest`` ``attest``
           ``pytest`` ``backtesting`` ``backTesting`` ``myspec`` ``respec``
           ``backtests`` ``contests`` ``protests`` ``attests`` ``latests``
           ``Testing`` (mixed-case, i.e. a PHP namespace) ``SupportTesting``
           ``tokio-test`` ``image-spec`` ``openapi-spec`` ``specs-go``
    """
    if not component or _CTRL.search(component):
        return False
    segs = _segments(component)
    if not segs:
        return False

    if len(segs) == 1:
        # Glued: no orthographic boundary anywhere in the component, so the
        # only evidence available is the token itself and its prefix.
        s = segs[0]
        if s in _EXACT:
            return True
        if s == "testing":
            # ``testing`` / ``TESTING`` are directories; ``Testing`` is a
            # PHP/C# namespace segment (Illuminate\Testing, a SHIPPED
            # package). The case is the only signal and it is load-bearing.
            return component == component.lower() or component == component.upper()
        for tok in ("tests", "test"):
            if s.endswith(tok) and len(s) > len(tok):
                return s[: -len(tok)] in _QUALIFIERS
        return False

    last, prev = segs[-1], segs[-2]
    if last == "tests":
        # Delimited or camel-split plural is unambiguous: ``foo__tests__``,
        # ``integration-tests``, ``MyAppUITests``. The glued ``backtests``
        # never reaches here — it has no boundary — which is why the plural
        # collisions stay production.
        return True
    if last == "test":
        return _camel_before_last(component) or prev in _QUALIFIERS
    if last == "spec":
        # ``spec`` is a heavily overloaded English word (``image-spec``,
        # ``openapi-spec``, ``api-spec``). Only the camelCase Ruby/RSpec
        # target shape (``UserSpec``) counts; never via delimiter or glue.
        return _camel_before_last(component)
    if last == "testing":
        return prev in _QUALIFIERS
    for tok in ("tests", "test"):
        if last.endswith(tok) and len(last) > len(tok):
            return last[: -len(tok)] in _QUALIFIERS
    return False


# ---------------------------------------------------------------------------
# Allowlist-decay defence.
#
# Every _QUALIFIERS miss is a False — a real test directory silently
# promoted to production source, with no signal at any of the ~123 call
# sites. Under ROAM_DEBUG_TEST_DIRS the components that ENDED IN a test
# token but failed the qualifier check are collected and can be dumped
# once per run, which makes the allowlist's decay reviewable instead of
# invisible. If this set grows past ~80 distinct qualifiers the
# closed-taxonomy premise has failed and the answer is a corroborating
# signal (does the file import a test framework?), not a longer list.
# ---------------------------------------------------------------------------

_UNQUALIFIED_TEST_COMPONENTS: set[str] = set()
_DEBUG_TEST_DIRS = os.environ.get("ROAM_DEBUG_TEST_DIRS") or os.environ.get("ROAM_DEBUG")


def _note_unqualified(component: str) -> None:
    """Record a component that looked test-ish but failed _QUALIFIERS."""
    if not _DEBUG_TEST_DIRS:
        return
    segs = _segments(component)
    if segs and any(segs[-1].endswith(t) for t in ("test", "tests", "testing", "spec")):
        _UNQUALIFIED_TEST_COMPONENTS.add(component)


def debug_unqualified_test_components() -> list[str]:
    """Sorted components rejected by the qualifier check this process.

    Empty unless ``ROAM_DEBUG_TEST_DIRS`` (or ``ROAM_DEBUG``) is set.
    """
    return sorted(_UNQUALIFIED_TEST_COMPONENTS)


# Filename conventions the canonical detector may miss, ANCHORED. The
# pre-W1512 form was ``any(pat in bn for pat in ["test_", "_test.",
# ".test.", ".spec."])`` — the same substring-where-anchor defect one level
# down, which is what kept roam-code's own ``cmd_test_gaps.py``,
# ``cmd_test_impact.py``, ``cmd_test_pyramid.py``, ``cmd_test_scaffold.py``,
# ``cmd_test_hermeticity.py``, ``cmd_pytest_fixtures.py`` and
# ``index/pytest_fixtures.py`` invisible to dead-code, taint, auth-gaps and
# safe-delete analysis. ``.test-d.ts`` / ``.spec-d.ts`` are Vitest
# type-level tests, which the canonical detector returns False on.
_TEST_BASENAME_RE = re.compile(r"^test_|_test\.[^.]+$|\.(?:test|spec)\.[^.]+$|\.(?:test|spec)-d\.[jt]sx?$")


def _is_test_basename(basename: str) -> bool:
    """Return True when a FILE NAME follows a test-file convention.

    The ``$`` in ``_TEST_BASENAME_RE`` is the same anchor shape that made
    ``_TEST_DIR_CAMEL`` accept ``"MyTest\\n"``, and ``[^.]`` matches a
    newline, so ``\\Z`` alone would not close it: ``"foo_test.go\\n"``
    matches either way. It is closed the same way the component predicate
    closes it — BY CONSTRUCTION. A string carrying a control character is
    not a file name, so it is rejected before any regex sees it, and the
    module's claim that no regex in here can be fooled by a trailing
    newline is then true of BOTH halves rather than only the directory
    half.

    Strictly narrowing: every name this rejects was accepted by the
    pre-W1512 substring rule too, and no name a real filesystem, a
    ``splitlines()`` ingest or the index DB can produce contains one.
    """
    if not basename or _CTRL.search(basename):
        return False
    return bool(_TEST_BASENAME_RE.search(basename))


def is_test_file(path: str | None) -> bool:
    """Check if a file path looks like a test file.

    Consults the canonical ``test_conventions`` detector first (via
    ``file_roles.is_test``, ~22 language-specific naming patterns), then a
    fallback covering the conventions it does not: case-insensitive
    ``TESTS/`` (W898), .NET/Xcode/Gradle test project directories, the
    ``unittests/`` / ``jstests/`` / ``apitests/`` family, and Vitest
    ``.test-d.ts`` type tests.

    Both halves of the fallback are ANCHORED (W1511, W1512) — the
    directory half to whole path components segmented on delimiter and
    camelCase boundaries, the filename half to a real regex. A directory
    that merely ends with a test token (``latest/``, ``contest/``,
    ``attest/``, ``backtests/``, ``Illuminate/Testing/``, ``tokio-test/``)
    is production, and so is a filename that merely contains one
    (``cmd_test_gaps.py``, ``latest_snapshot.py``).

    Returns ``False`` for empty / falsy inputs.
    """
    if not path:
        return False
    if _roles_is_test(path):
        return True
    # Fallback for conventions the canonical detector does not cover.
    p_cs = path.replace("\\", "/")
    if _is_test_basename(os.path.basename(p_cs)):
        return True
    # Directory components only — the basename is handled above.
    for c in p_cs.split("/")[:-1]:
        if _is_test_dir_component(c):
            return True
        if _DEBUG_TEST_DIRS:
            _note_unqualified(c)
    return False


_LOW_RISK_ROLES = frozenset({"docs", "config", "data", "ci", "generated", "vendored"})

_LOW_RISK_EXTS = {
    ".md",
    ".txt",
    ".rst",
    ".json",
    ".yaml",
    ".yml",
    ".toml",
    ".ini",
    ".cfg",
    ".lock",
    ".xml",
    ".svg",
    ".png",
    ".jpg",
    ".gif",
    ".ico",
    ".csv",
    ".env",
}


def is_low_risk_file(path: str | None) -> bool:
    """Check if a file is docs/config/asset with dampened risk contribution.

    Uses file_roles classifier first, falls back to extension check.

    Returns ``False`` for empty / falsy inputs.
    """
    if not path:
        return False
    role = classify_file(path)
    if role in _LOW_RISK_ROLES:
        return True
    p = path.replace("\\", "/").lower()
    _, ext = os.path.splitext(p)
    return ext in _LOW_RISK_EXTS


# ---------------------------------------------------------------------------
# Changed file resolution
# ---------------------------------------------------------------------------


def get_changed_files(
    root: Path,
    staged: bool = False,
    commit_range: str | None = None,
    pr: bool = False,
    base_ref: str = "main",
    untracked: bool = False,
) -> list[str]:
    """Get list of changed files from git diff.

    Supports four mutually exclusive sources:
    - *commit_range*: arbitrary range (e.g. ``HEAD~3..HEAD``)
    - *staged*: files in the staging area
    - *pr*: files changed in ``base_ref..HEAD``
    - (default): unstaged working-tree changes

    When *untracked* is True, also includes new files that are not yet
    tracked by git (``git ls-files --others --exclude-standard``).

    Returns normalised forward-slash paths relative to the repo root.
    """
    cmd = ["git", "diff", "--name-only"]

    if commit_range:
        cmd.append(commit_range)
    elif pr:
        cmd.append(f"{base_ref}...HEAD")
    elif staged:
        cmd.append("--cached")

    from roam.git_utils import worktree_git_env

    git_env = worktree_git_env(root)
    try:
        result = subprocess.run(
            cmd,
            cwd=str(root),
            capture_output=True,
            text=True,
            timeout=10,
            encoding="utf-8",
            errors="replace",
            env=git_env,
        )
        if result.returncode != 0:
            return []
        paths = [p.replace("\\", "/") for p in result.stdout.strip().splitlines() if p.strip()]
    except (FileNotFoundError, subprocess.TimeoutExpired) as _exc:
        # Git missing / diff timed out -> empty changeset. A TimeoutExpired
        # here is a real failure masquerading as "no changes" — surface it.
        from roam.observability import log_swallowed

        log_swallowed("changed_files:get_changed_files:git_diff", _exc)
        return []

    if untracked:
        try:
            ut = subprocess.run(
                ["git", "ls-files", "--others", "--exclude-standard"],
                cwd=str(root),
                capture_output=True,
                text=True,
                timeout=10,
                encoding="utf-8",
                errors="replace",
                env=git_env,
            )
            if ut.returncode == 0 and ut.stdout.strip():
                for line in ut.stdout.strip().splitlines():
                    line = line.strip()
                    if line:
                        paths.append(line.replace("\\", "/"))
        except (FileNotFoundError, subprocess.TimeoutExpired) as _exc:
            # Untracked-file enrichment is best-effort: the tracked-diff
            # paths are still returned. Surface the cause so a missing
            # git binary / slow repo isn't silently dropped.
            from roam.observability import log_swallowed

            log_swallowed("changed_files:get_changed_files:untracked_ls_files", _exc)

    return paths


def resolve_changed_to_db(conn, changed_paths: list[str]) -> dict[str, int]:
    """Map a list of changed paths to ``{path: file_id}`` using the index DB.

    Falls back to suffix matching when exact path fails (handles sub-directory
    prefixes and normalisation differences).  Uses a basename index for O(1)
    suffix lookups instead of scanning all files.
    """
    file_map: dict[str, int] = {}
    all_files = conn.execute("SELECT id, path FROM files").fetchall()
    exact: dict[str, tuple[int, str]] = {}
    # Suffix index: basename -> list of (id, full_path) for fast fallback
    suffix_idx: dict[str, list[tuple[int, str]]] = {}
    for f in all_files:
        f_id, f_path = f["id"], f["path"]
        exact[f_path] = (f_id, f_path)
        basename = f_path.rsplit("/", 1)[-1]
        suffix_idx.setdefault(basename, []).append((f_id, f_path))
    for path in changed_paths:
        hit = exact.get(path)
        if not hit:
            # Use suffix index: look up by basename, then verify endswith
            basename = path.rsplit("/", 1)[-1]
            for f_id, f_path in suffix_idx.get(basename, ()):
                if f_path.endswith(path):
                    hit = (f_id, f_path)
                    break
        if hit:
            file_map[hit[1]] = hit[0]
    return file_map


# ---------------------------------------------------------------------------
# Ref-anchored git helpers (W-vibe-check DRY consolidation)
#
# These helpers were previously duplicated across cmd_api_changes,
# cmd_breaking, cmd_semantic_diff, and graph/diff.py. The detect-paste-
# functions roster picked them up as a clone group of 4 (`_git_show`) and
# two clone groups of 3 (`_git_changed_files`, `_parse_source_bytes`).
# Hoisted here so every diff-against-ref command shares the same impl.
# ---------------------------------------------------------------------------


def git_changed_files_against_ref(root: Path, ref: str) -> list[str]:
    """Return files changed between *ref* and the working tree (indexed state).

    Distinct from :func:`get_changed_files` which handles staged / commit-range
    / pr / working-tree sources via a single entry point. This helper is the
    narrow "diff against an explicit ref" form used by the api-changes,
    breaking, and semantic-diff commands.
    """
    cmd = ["git", "diff", "--name-only", ref]
    try:
        result = subprocess.run(
            cmd,
            cwd=str(root),
            capture_output=True,
            text=True,
            timeout=10,
            encoding="utf-8",
            errors="replace",
        )
        if result.returncode != 0:
            return []
        return [p.replace("\\", "/") for p in result.stdout.strip().splitlines() if p.strip()]
    except (FileNotFoundError, subprocess.TimeoutExpired) as _exc:
        # Git missing / diff-against-ref timed out -> empty changeset.
        # A TimeoutExpired masquerades as "no changes" — surface it.
        from roam.observability import log_swallowed

        log_swallowed("changed_files:git_changed_files_against_ref:git_diff", _exc)
        return []


def git_show_at_ref(root: Path, ref: str, filepath: str) -> bytes | None:
    """Return the content of *filepath* at *ref*, or None if it didn't exist."""
    cmd = ["git", "show", f"{ref}:{filepath}"]
    try:
        result = subprocess.run(
            cmd,
            cwd=str(root),
            capture_output=True,
            timeout=10,
        )
        # returncode != 0 is the expected "file absent at ref" path.
        if result.returncode != 0:
            return None
        return result.stdout
    except (FileNotFoundError, subprocess.TimeoutExpired) as _exc:
        # Git missing / git-show timed out -> None. Distinct from the
        # expected "absent at ref" miss above — surface the real failure.
        from roam.observability import log_swallowed

        log_swallowed("changed_files:git_show_at_ref:git_show", _exc)
        return None


def parse_source_with_grammar(source: bytes, language: str):
    """Parse *source* bytes with tree-sitter for the given language.

    Returns ``(tree, source_bytes, effective_language)`` or
    ``(None, None, None)`` on parser unavailability or parse failure.
    Resolves grammar aliases (e.g. ``apex`` -> ``java``) via the indexer's
    ``GRAMMAR_ALIASES`` map.
    """
    from roam.index.parser import GRAMMAR_ALIASES

    grammar = GRAMMAR_ALIASES.get(language, language)

    try:
        from roam.parser_pack import get_parser

        parser = get_parser(grammar)
    except Exception as _exc:  # noqa: BLE001 — defensive
        # Grammar unavailable -> documented (None, None, None) sentinel;
        # surface the cause so a missing grammar isn't silently dropped.
        from roam.observability import log_swallowed

        log_swallowed(f"changed_files:parse_source_with_grammar:get_parser:{grammar}", _exc)
        return None, None, None

    try:
        tree = parser.parse(source)
    except Exception as _exc:  # noqa: BLE001 — defensive
        # Parse failure -> documented (None, None, None) sentinel; surface
        # the cause so a malformed source isn't mistaken for "no symbols".
        from roam.observability import log_swallowed

        log_swallowed(f"changed_files:parse_source_with_grammar:parse:{language}", _exc)
        return None, None, None

    return tree, source, language
