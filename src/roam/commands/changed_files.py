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

# W1462 fail-loud sentinels for the SHARED changed-file helper.
#
# ``get_changed_files`` returns ``list[str]``, a type with no channel for
# "I could not ask git". Every failure mode of ``git diff --name-only``
# — binary missing, subprocess timeout, non-zero return (corrupt index,
# unreadable object store, not-a-repository) — collapsed into the same
# ``[]`` that means "the tree is clean", and all 19 caller modules read
# that empty list as the literal fact. Under a corrupt ``GIT_INDEX_FILE``
# a genuinely dirty tree produced ``adversarial --fail-on-critical`` exit
# 0 with "VERDICT: No changes detected".
#
# The correct shape already existed in this repo on a private helper —
# ``cmd_delete_check._git_diff`` returns ``(diff_text, error_kind)`` and
# the command refuses under ``--ci`` (CP45/CP46). These constants are the
# same vocabulary, hoisted to the shared helper so the gated callers can
# publish the error kind instead of a false clean. The sentinel values are
# byte-identical to cmd_delete_check's so an envelope consumer sees one
# vocabulary across commands.
GIT_NOT_AVAILABLE = "git_not_available"
GIT_TIMEOUT = "git_timeout"
GIT_ERROR = "git_error"
MIN_GIT_VERSION = (2, 24)
MIN_GIT_VERSION_LABEL = ".".join(map(str, MIN_GIT_VERSION))


def run_git_comparison(
    root: Path,
    cmd: list[str],
    error_details: list[str] | None,
    *,
    text=False,
    fallback_detail: str | None = None,
    defer_nonzero: bool = False,
):
    """Run Git and preserve its failure kind and diagnostics.

    With `defer_nonzero`, return the failed process without logging so the
    caller can distinguish an expected lookup miss from a genuine failure.
    """
    from roam.git_utils import worktree_git_env
    from roam.observability import log_swallowed

    git_env = worktree_git_env(root)
    try:
        result = subprocess.run(
            cmd,
            cwd=str(root),
            capture_output=True,
            timeout=10,
            env=git_env,
            **({"text": True, "encoding": "utf-8", "errors": "replace"} if text else {}),
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        log_swallowed("changed_files:git_comparison", exc)
        if error_details is not None:
            error_details.append(str(exc))
        if isinstance(exc, subprocess.TimeoutExpired):
            return None, GIT_TIMEOUT
        if isinstance(exc, FileNotFoundError) and root.is_dir():
            return None, GIT_NOT_AVAILABLE
        return None, GIT_ERROR

    if result.returncode != 0:
        stderr = result.stderr.decode("utf-8", errors="replace") if isinstance(result.stderr, bytes) else result.stderr
        detail = stderr.strip() or fallback_detail or f"git {cmd[1]} exited {result.returncode}"
        first_line = stderr.partition("\n")[0]
        options_end = cmd.index("--") if "--" in cmd else len(cmd)
        # git diff can print only usage and exit 129 for an unknown option.
        option_rejected = "--end-of-options" in cmd[:options_end] and (
            re.search(r"(?<![\w:./-])--end-of-options(?![\w./-])", first_line)
            or "`end-of-options'" in first_line
            or (result.returncode == 129 and "git diff " in first_line)
        )
        if option_rejected:
            detail += f"\nGit {MIN_GIT_VERSION_LABEL} or later is required for --end-of-options."
        defer_nonzero = defer_nonzero and not option_rejected
        if not defer_nonzero:
            log_swallowed("changed_files:git_comparison_returncode", RuntimeError(detail[:200]))
        if error_details is not None:
            error_details.append(detail)
        return (result if defer_nonzero else None), GIT_ERROR
    return result, None


def decode_git_path(path: bytes) -> str:
    """Decode Git path bytes without losing bytes rejected by the filesystem codec."""
    try:
        return os.fsdecode(path)
    except UnicodeDecodeError:
        return path.decode("utf-8", errors="surrogateescape")


def get_changed_files_status(
    root: Path,
    staged: bool = False,
    commit_range: str | None = None,
    pr: bool = False,
    base_ref: str = "main",
    untracked: bool = False,
    error_details: list[str] | None = None,
) -> tuple[list[str], str | None]:
    """``(paths, error_kind)`` form of :func:`get_changed_files`.

    *error_kind* is ``None`` when git answered, otherwise one of
    :data:`GIT_NOT_AVAILABLE`, :data:`GIT_TIMEOUT`, :data:`GIT_ERROR`.
    When it is not ``None`` the returned path list is empty *because the
    question could not be asked*, which is not the same fact as "no files
    changed" and must not be gated on as though it were.

    When supplied, *error_details* receives Git stderr (or the exception text)
    on failure, preserving diagnostics alongside the stable error-kind enum.

    Callers behind a CI gate flag MUST branch on *error_kind* and refuse;
    callers that only report may keep using :func:`get_changed_files`.
    """
    cmd = ["git", "diff", "--name-only", "-z", "--no-renames"]

    if commit_range is not None:
        cmd.extend(["--end-of-options", commit_range, "--"])
    elif pr:
        cmd.extend(["--end-of-options", f"{base_ref}...HEAD", "--"])
    elif staged:
        cmd.append("--cached")

    result, git_error = run_git_comparison(root, cmd, error_details)
    if git_error is not None:
        return [], git_error
    records = result.stdout.split(b"\0")
    if records[-1] or any(not path for path in records[:-1]):
        if error_details is not None:
            error_details.append("git diff returned an invalid NUL-delimited path list")
        return [], GIT_ERROR
    paths = [decode_git_path(path) for path in records[:-1]]

    if untracked:
        # Enrichment is best-effort, with failures disclosed by the shared runner.
        ut, _error = run_git_comparison(
            root, ["git", "ls-files", "-z", "--others", "--exclude-standard"], error_details
        )
        if ut is not None:
            paths.extend(decode_git_path(path) for path in ut.stdout.split(b"\0") if path)

    # The index stores Unicode paths in SQLite, which cannot bind surrogates.
    # Refuse a partial comparison and identify the path without printing raw surrogates.
    for path in paths:
        if re.search(r"[\ud800-\udfff]", path):
            detail = f"Git path cannot be matched in the index: {path!r}"
            if error_details is not None:
                error_details.append(detail)
            from roam.observability import log_swallowed

            log_swallowed("changed_files:git_path_encoding", UnicodeError(detail))
            return [], GIT_ERROR
    return paths, None


def get_changed_files(
    root: Path,
    staged: bool = False,
    commit_range: str | None = None,
    pr: bool = False,
    base_ref: str = "main",
    untracked: bool = False,
    error_details: list[str] | None = None,
) -> list[str]:
    """Get list of changed files from git diff.

    Supports four mutually exclusive sources:
    - *commit_range*: arbitrary range (e.g. ``HEAD~3..HEAD``)
    - *staged*: files in the staging area
    - *pr*: files changed in ``base_ref..HEAD``
    - (default): unstaged working-tree changes

    When *untracked* is True, also includes new files that are not yet
    tracked by git (``git ls-files --others --exclude-standard``).

    Returns literal paths relative to the repo root, with Git’s forward-slash separators.
    Supply *error_details* to collect failed-read diagnostics via the shared
    status helper while retaining this list-returning interface.

    This is the lossy projection of :func:`get_changed_files_status`: an
    empty list here means EITHER "no files changed" OR "git could not be
    asked". Do not gate on it. Any caller with a ``--fail-on-*`` / ``--ci``
    / ``--gate`` flag must call :func:`get_changed_files_status` and refuse
    when *error_kind* is not ``None``.
    """
    paths, _error_kind = get_changed_files_status(
        root,
        staged=staged,
        commit_range=commit_range,
        pr=pr,
        base_ref=base_ref,
        untracked=untracked,
        **({"error_details": error_details} if error_details is not None else {}),
    )
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


class GitComparisonError(RuntimeError):
    """A ref comparison failed, with the shared closed-enum Git failure kind."""

    def __init__(self, kind: str, detail: str):
        self.kind = kind
        self.detail = detail
        super().__init__(detail)


def git_changed_files_against_ref(root: Path, ref: str) -> list[str]:
    """Return changed paths; raise GitComparisonError when comparison is unavailable.

    Share status classification and worktree environment with all diff commands.
    An empty list now means a successful empty comparison, never a Git failure.
    """
    details: list[str] = []
    paths, error = get_changed_files_status(root, commit_range=ref, error_details=details)
    if error:
        raise GitComparisonError(error, "\n".join(details))
    return paths


def git_comparison_base(root: Path, ref: str) -> str:
    """Resolve the old side of git diff, including a three-dot merge base."""
    if "..." not in ref:
        return ref.split("..", 1)[0] or "HEAD"
    left, right = ref.split("...", 1)
    details: list[str] = []
    result, error = run_git_comparison(
        root,
        ["git", "merge-base", "--", left or "HEAD", right or "HEAD"],
        details,
        text=True,
        fallback_detail="git merge-base failed",
    )
    if error:
        raise GitComparisonError(error, "\n".join(details))
    base = result.stdout.strip()
    if not re.fullmatch(r"[0-9a-f]{40}(?:[0-9a-f]{24})?", base):
        raise GitComparisonError(GIT_ERROR, "git merge-base returned an invalid object ID")
    return base


class GitlinkPath(Exception):
    """The tree entry is a submodule pointer, without source to compare."""


def git_show_at_ref_status(
    root: Path, ref: str, path: str, error_details: list[str]
) -> tuple[bytes | None, str | None]:
    """Read a blob, distinguishing tree absence and gitlinks from failed reads."""
    from roam.observability import log_swallowed

    # Require a blob: git show can render trees and commits as text and
    # can fall back to a pathspec when object resolution fails.
    result, git_error = run_git_comparison(
        root, ["git", "cat-file", "blob", "--end-of-options", f"{ref}:{path}"], error_details, defer_nonzero=True
    )
    if result is not None and git_error == GIT_ERROR:
        # Git diagnostics are localized; only a successful tree query proves absence.
        show_detail = error_details[-1]
        listing, listing_error = run_git_comparison(
            root, ["git", "--literal-pathspecs", "ls-tree", "-z", "--end-of-options", ref, "--", path], error_details
        )
        if listing_error:
            return None, listing_error
        metadata, _, entry_path = listing.stdout.partition(b"\t")
        entry_matches = entry_path == os.fsencode(path) + b"\0"
        if not listing.stdout or (
            entry_matches and re.fullmatch(rb"040000 tree [0-9a-f]{40}(?:[0-9a-f]{24})?", metadata)
        ):
            error_details.pop()
            return None, None
        if entry_matches and re.fullmatch(rb"160000 commit [0-9a-f]{40}(?:[0-9a-f]{24})?", metadata):
            error_details.pop()
            raise GitlinkPath(path)
        log_swallowed("changed_files:git_comparison_returncode", RuntimeError(show_detail[:200]))
    return (result.stdout if git_error is None else None), git_error


def git_show_at_ref(root: Path, ref: str, filepath: str, *, strict: bool = False) -> bytes | None:
    """Read content; strict callers distinguish absent paths from Git failures."""
    details: list[str] = []
    try:
        source, error = git_show_at_ref_status(root, ref, filepath, details)
    except GitlinkPath:
        if strict:
            raise
        return None
    if error and strict:
        raise GitComparisonError(error, "\n".join(details))
    return source


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


# ---------------------------------------------------------------------------
# Per-file headers of a unified diff (mode-only, rename-only, binary)
#
# ``critique.checks.parse_diff`` opens a region only at a ``+++`` line, so a
# diff entry with no hunks (mode flip, pure rename, binary change) never
# appears in its output. These helpers read the ``diff --git`` headers so a
# caller can count every changed file and say how many had no content hunks.
# ---------------------------------------------------------------------------

_DIFF_GIT_HEADER_RE = re.compile(r"^diff --git (.*)$")
_C_ESCAPES = {"a": 7, "b": 8, "f": 12, "n": 10, "r": 13, "t": 9, "v": 11, "\\": 92, '"': 34}


def _read_c_quoted(text: str) -> tuple[str, str]:
    """Decode one git C-quoted token at the start of *text*; return (value, rest).

    git quotes a path (core.quotePath default) when it holds a byte >= 0x80, a
    control character, a double quote or a backslash: ``"caf\303\251.sh"``.
    """
    out = bytearray()
    i = 1  # skip the opening quote
    n = len(text)
    while i < n and text[i] != '"':
        ch = text[i]
        if ch == "\\" and i + 1 < n:
            nxt = text[i + 1]
            if nxt in "01234567":
                j = i + 1
                while j < n and j < i + 4 and text[j] in "01234567":
                    j += 1
                out.append(int(text[i + 1 : j], 8) & 0xFF)
                i = j
                continue
            out.append(_C_ESCAPES.get(nxt, ord(nxt) & 0xFF))
            i += 2
            continue
        out.extend(ch.encode("utf-8"))
        i += 1
    return out.decode("utf-8", errors="replace"), text[i + 1 :]


def _unquote_path(text: str) -> str:
    """Return *text* with git C-quoting removed (unquoted text is returned as is)."""
    # Only the whitespace around a quoted token is dropped; a decoded name (or an
    # unquoted rename/copy tail) keeps every byte git preserved, trailing spaces included.
    if text.lstrip().startswith('"'):
        return _read_c_quoted(text.lstrip())[0]
    return text


def _strip_side(path: str, prefix: str) -> str:
    return path[len(prefix) :] if path.startswith(prefix) else path


def _split_git_header(rest: str) -> tuple[str, str] | None:
    """Split the tail of a ``diff --git`` line into (old path, new path)."""
    rest = rest.lstrip()
    prefixes = (("a/", "b/"), ("i/", "w/"), ("c/", "w/"), ("c/", "i/"), ("", ""))
    if rest.startswith('"'):
        old, remaining = _read_c_quoted(rest)
        new = _unquote_path(remaining.lstrip())
    else:
        # Git leaves spaces in ordinary path names unquoted. An identical
        # old/new name is unambiguous even when it contains " b/" itself.
        for left, right in prefixes:
            if not rest.startswith(left):
                continue
            size = (len(rest) - len(left) - len(right) - 1) // 2
            if size > 0:
                name = rest[len(left) : len(left) + size]
                if rest == left + name + " " + right + name:
                    return name, name
        candidates = []
        for left, right in prefixes[:-1]:
            if rest.startswith(left):
                for match in re.finditer(r" " + re.escape(right), rest):
                    candidates.append((rest[: match.start()], rest[match.start() + 1 :]))
                quoted = ' "' + right
                if quoted in rest:
                    old, new = rest.split(quoted, 1)
                    return _strip_side(old, left), _strip_side(_unquote_path('"' + right + new), right)
        if len(candidates) == 1:
            old, new = candidates[0]
        elif not candidates and len(rest.split(" ")) == 2:
            old, new = rest.split(" ")
        else:
            return None
    for left, right in prefixes[:-1]:
        if old.startswith(left) and new.startswith(right):
            return old[len(left) :], new[len(right) :]
    return old, new


def parse_diff_file_headers(text: str) -> list[dict]:
    """Return one entry per ``diff --git`` header in *text*.

    Each entry: ``path`` (new side), ``old_path``, ``kind`` and ``has_hunks``.
    ``kind`` is one of ``content`` (a ``+++``/``@@`` section exists), ``binary``,
    ``rename`` (rename or copy), ``mode`` (only a mode change) or ``other``.
    ``old_mode``/``new_mode`` are present when the header carries them.
    Git C-quoted paths (non-ASCII, quotes, control characters) are decoded.
    Plain ``diff -u`` input has no ``diff --git`` lines and yields ``[]``.
    """
    entries: list[dict] = []
    cur: dict | None = None
    for line in (text or "").splitlines():
        m = _DIFF_GIT_HEADER_RE.match(line)
        if m:
            sides = _split_git_header(m.group(1))
            if sides is None:
                entries.append(
                    {"path": None, "old_path": None, "kind": "other", "has_hunks": False, "raw_header": line}
                )
                cur = None
                continue
            cur = {"path": sides[1], "old_path": sides[0], "kind": "other", "has_hunks": False}
            entries.append(cur)
            continue
        if cur is None:
            continue
        if line.startswith("old mode "):
            cur["old_mode"] = line[9:].strip()
        elif line.startswith("new mode "):
            cur["new_mode"] = line[9:].strip()
        elif line.startswith("rename to ") or line.startswith("copy to "):
            cur["path"] = _unquote_path(line.split(" to ", 1)[1])
            cur["kind"] = "rename"
        elif line.startswith("rename from ") or line.startswith("copy from "):
            cur["old_path"] = _unquote_path(line.split(" from ", 1)[1])
            cur["kind"] = "rename"
        elif line.startswith("Binary files ") or line.startswith("GIT binary patch"):
            cur["kind"] = "binary"
        elif line.startswith("+++ ") or line.startswith("@@ "):
            cur["has_hunks"] = True
    for e in entries:
        if e["has_hunks"]:
            e["kind"] = "content"
        elif e["kind"] == "other" and "new_mode" in e:
            e["kind"] = "mode"
    return entries


def changed_files_total(text: str, content_paths) -> int:
    """Count every Git header plus content regions from plain unified diffs."""
    headers = parse_diff_file_headers(text)
    represented = {entry["path"] for entry in headers}
    return len(headers) + sum(path not in represented for path in content_paths)


def non_content_changes(text: str, content_paths) -> list[dict]:
    """Header entries not represented by the new-side content parser.

    Deleted content has no new-side regions, so retain its header as well.
    """
    seen = set(content_paths)
    return [
        {k: v for k, v in e.items() if k != "has_hunks"} for e in parse_diff_file_headers(text) if e["path"] not in seen
    ]


def git_mode_only_files(root: Path, ref: str) -> set[str]:
    """Files whose only change against *ref* is the file mode (same blob)."""
    try:
        result = subprocess.run(
            ["git", "diff", "--raw", "-z", "--no-abbrev", "--no-renames", ref],
            cwd=str(root),
            capture_output=True,
            text=True,
            timeout=10,
            encoding="utf-8",
            errors="replace",
        )
    except (FileNotFoundError, subprocess.TimeoutExpired) as _exc:
        from roam.observability import log_swallowed

        log_swallowed("changed_files:git_mode_only_files:git_diff", _exc)
        return set()
    if result.returncode != 0:
        return set()
    try:
        content = subprocess.run(
            ["git", "diff", "--numstat", "-z", "--no-renames", ref],
            cwd=str(root),
            capture_output=True,
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired):
        return set()
    if content.returncode != 0:
        return set()
    unchanged_content = set()
    for record in content.stdout.split(b"\0"):
        added, sep, rest = record.partition(b"\t")
        removed, sep2, path = rest.partition(b"\t")
        if sep and sep2 and added == removed == b"0":
            unchanged_content.add(path.decode("utf-8", errors="replace"))
    out: set[str] = set()
    records = result.stdout.split("\0")
    for meta, path in zip(records[::2], records[1::2]):
        fields = meta.lstrip(":").split()
        # <old mode> <new mode> <old sha> <new sha> <status>
        if (
            len(fields) == 5
            and fields[4] == "M"
            and fields[0] != fields[1]
            and (fields[2] == fields[3] or path in unchanged_content)
        ):
            out.add(path)
    return out


def unexamined_files_note(not_examined: int, total: int) -> str:
    """Verdict suffix for changed files a command could not examine ('' when none)."""
    if not not_examined:
        return ""
    return f"; {not_examined} of {total} changed file{'s' if total != 1 else ''} not analyzable"
