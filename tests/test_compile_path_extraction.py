"""W32 regression test — `_extract_file_paths` boundary + filename bug.

The trailing-boundary character class was missing `?` `!` `;` `]` `}` `>`
so paths followed by natural-language punctuation (especially `?`) failed
to extract. The filename character class also lacked `-` so kebab-case
files (`claude-sdk.js`, `my-component.vue`) failed.

Result: every compile envelope for natural-language tasks that named
a kebab-case file or ended the path with `?` showed search-semantic
noise in `named_paths` instead of the obvious target. Every prior compile
A/B was polluted by this — see project_compiler_eval_multiphase_2026-05-30.
"""

from __future__ import annotations

import pytest

from roam.plan.compiler import _extract_file_paths, _repo_contained_path


@pytest.mark.parametrize(
    "task,expected",
    [
        # The regression case from the multi-phase A/B.
        (
            "Which files have the strongest temporal coupling to src/roam/cli.py? Answer in <=120 words.",
            ["src/roam/cli.py"],
        ),
        # Kebab-case filename (the second bug).
        ("Which files have the strongest coupling to server/claude-sdk.js?", ["server/claude-sdk.js"]),
        # Path followed by `!`.
        ("Edit src/roam/cli.py! it's broken.", ["src/roam/cli.py"]),
        # Path followed by `;`.
        ("Run src/roam/cli.py; then commit.", ["src/roam/cli.py"]),
        # Path in brackets / braces.
        ("Affected: [src/roam/cli.py].", ["src/roam/cli.py"]),
        # Multiple files.
        ("Compare src/roam/cli.py and src/roam/mcp_server.py?", ["src/roam/cli.py", "src/roam/mcp_server.py"]),
        # Original cases that should still work (regression guard).
        ("Edit src/roam/cli.py.", ["src/roam/cli.py"]),
        ("Edit src/roam/cli.py, please.", ["src/roam/cli.py"]),
        ("tests/test_foo.py:123 has the bug", ["tests/test_foo.py"]),
        ("'src/quoted.py'", ["src/quoted.py"]),
    ],
)
def test_extract_file_paths_finds_path_across_boundaries(task, expected):
    assert _extract_file_paths(task) == expected


def test_extract_file_paths_empty_when_no_path():
    assert _extract_file_paths("What does this code do") == []
    assert _extract_file_paths("Refactor the auth module") == []


@pytest.mark.parametrize(
    "task",
    [
        # Absolute path escapes the cwd join (os.path.join(cwd, "/etc/x") == "/etc/x").
        "summarize /etc/secret.py please",
        # `..` traversal escapes the repo.
        "read ../../../etc/passwd.py now",
        "look at sub/../../escape.py",
        # Forbidden folders (internal/**, .git/**, node_modules/**, .venv/**, .roam/**).
        "what is in internal/planning/secret.md?",
        "open .git/config.yml",
        "look at node_modules/foo/bar.js",
        ".roam/index.db notes in .roam/cache.json",
        # Forbidden bare-name patterns nested under a directory.
        "a path like a/b/package.json here",
        "see config/pnpm-lock.yaml",
        # Forbidden directory anchor (trailing slash).
        "check internal/ for notes",
    ],
)
def test_extract_file_paths_drops_unsafe_paths(task):
    """Task text is attacker-influenced: absolute, `..`, and forbidden paths
    must never reach named_paths / likely_files or the downstream read/diff
    probes that open() them. The single repo-contained resolver drops them."""
    assert _extract_file_paths(task) == []


@pytest.mark.parametrize(
    "task,expected",
    [
        # `./` and `//` collapse; the path is otherwise repo-contained.
        ("collapse ./src/roam/cli.py path", ["src/roam/cli.py"]),
        ("double src//roam//cli.py slash", ["src/roam/cli.py"]),
        # Directory anchors keep their trailing slash (scope-lock relies on it).
        ("look in src/roam/commands/ dir", ["src/roam/commands/"]),
    ],
)
def test_extract_file_paths_normalizes_safe_paths(task, expected):
    assert _extract_file_paths(task) == expected


# --- likely_files resolver parity ----------------------------------------
# The explicit-path branch funnels through `_repo_contained_path`, but the
# search-semantic and cache-hit branches of `_likely_files_from_search`
# produce index-derived paths that must ALSO be repo-contained: an indexed
# forbidden file (.env, a lockfile, internal/**) or a stale cache row must
# never reach likely_files or the downstream read/diff probes that open() it.
import roam.plan.compiler as _c


def test_likely_files_drops_forbidden_search_results(monkeypatch):
    # Force the search-semantic branch (no explicit path, no cache hit).
    monkeypatch.setattr(_c, "_symbol_resolution_cache_lookup", lambda *a, **k: None)
    monkeypatch.setattr(_c, "_symbol_resolution_cache_store", lambda *a, **k: None)
    monkeypatch.setattr(_c, "_path_token_recall", lambda *a, **k: [])
    # Rerank is index/db-driven; keep the input ordering deterministic here.
    monkeypatch.setattr(_c, "_rerank_likely_files", lambda task, scored, cwd: [p for p, _ in scored])
    monkeypatch.setattr(
        _c,
        "_run_roam",
        lambda *a, **k: {
            "results": [
                {"file_path": "internal/planning/secret.md", "score": 9.0},
                {"file_path": ".env", "score": 8.0},
                {"file_path": "src/roam/cli.py", "score": 1.0},
            ]
        },
    )
    files, invoked = _c._likely_files_from_search("anything about caching", cwd="/tmp/x")
    assert invoked is True
    assert files == ["src/roam/cli.py"]


def test_likely_files_drops_forbidden_cache_hit(monkeypatch):
    monkeypatch.setattr(
        _c,
        "_symbol_resolution_cache_lookup",
        lambda *a, **k: (
            [
                "internal/planning/secret.md",
                ":(glob)**/*.py",
                "src/roam/cli.py",
                "../escape.py",
            ],
            True,
        ),
    )
    files, invoked = _c._likely_files_from_search("cached task", cwd="/tmp/x")
    assert invoked is False  # cache hit → subprocess not run
    assert files == ["src/roam/cli.py"]


# --- module-name resolver parity ------------------------------------------
# `_probe_module_name_for_task` globs the filesystem for "the auth module"
# shorthand and stitches the hits straight into named_paths (L1 probe + facts
# envelope), where they chain into the downstream read/diff probes. Those
# glob hits must funnel through `_repo_contained_path` too — the broad
# `src/**/*{name}*.py` pattern is task-text-driven, so a forbidden-but-tracked
# or repo-escaping hit must never reach named_paths.


def test_probe_module_name_drops_forbidden_glob_hits(monkeypatch):
    # base = cwd = /tmp/x; relpath of these hits yields one forbidden
    # (internal/**) path and one safe src path.
    monkeypatch.setattr(
        "glob.glob",
        lambda pat, recursive=False: [
            "/tmp/x/internal/planning/auth.py",
            "/tmp/x/src/roam/auth.py",
        ],
    )
    result = _c._probe_module_name_for_task("Refactor the auth module", [], cwd="/tmp/x")
    assert result is not None
    assert result["resolved_named_paths_from_module_name"] == ["src/roam/auth.py"]


def test_probe_module_name_returns_none_when_all_hits_forbidden(monkeypatch):
    # When every glob hit is forbidden, the probe yields no usable target —
    # it must return None, not an empty-path payload.
    monkeypatch.setattr(
        "glob.glob",
        lambda pat, recursive=False: ["/tmp/x/internal/planning/auth.py"],
    )
    result = _c._probe_module_name_for_task("Refactor the auth module", [], cwd="/tmp/x")
    assert result is None


# --- cwd-aware symlink containment ----------------------------------------
# Lexical normalization (absolute / `..` / forbidden) is necessary but NOT
# sufficient: a repo-tracked SYMLINK whose name passes every lexical rule
# (`src/link.py -> /etc/passwd`) survives normalization, and the downstream
# read/diff probes that open()/read_text() the os.path.join(cwd, name) then
# follow the link OUTSIDE the repo. When cwd is supplied, the resolver must
# realpath the candidate and reject anything that escapes the repo root.
import os


def _make_repo_with_escaping_symlink(tmp_path):
    repo = tmp_path / "repo"
    (repo / "src").mkdir(parents=True)
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "passwd").write_text("SECRET")
    # repo-tracked symlink that escapes the repo
    os.symlink(outside / "passwd", repo / "src" / "link.py")
    # in-repo real file + an in-repo symlink pointing at it (must be allowed)
    (repo / "src" / "real.py").write_text("ok")
    os.symlink(repo / "src" / "real.py", repo / "src" / "innerlink.py")
    return repo


def test_repo_contained_path_rejects_escaping_symlink(tmp_path):
    repo = _make_repo_with_escaping_symlink(tmp_path)
    # The lexical-only call (no cwd) cannot see the symlink — it passes,
    # which is precisely the hole the cwd realpath check closes.
    assert _repo_contained_path("src/link.py") == "src/link.py"
    # With cwd, the realpath escapes the repo root → rejected.
    assert _repo_contained_path("src/link.py", str(repo)) is None


def test_repo_contained_path_allows_in_repo_paths_with_cwd(tmp_path):
    repo = _make_repo_with_escaping_symlink(tmp_path)
    # Real in-repo file, in-repo symlink, and a not-yet-existent in-repo path
    # all stay contained — the check must not require existence (paths are
    # extracted from task text before any probe reads them).
    assert _repo_contained_path("src/real.py", str(repo)) == "src/real.py"
    assert _repo_contained_path("src/innerlink.py", str(repo)) == "src/innerlink.py"
    assert _repo_contained_path("src/not_created_yet.py", str(repo)) == "src/not_created_yet.py"


@pytest.mark.parametrize(
    "path",
    [
        ":(top)src/roam/cli.py",
        ":(glob)**/*.py",
        ":/src/roam/cli.py",
        ":./src/roam/cli.py",
        "./:(glob)**/*.py",
    ],
)
def test_repo_contained_path_rejects_leading_git_pathspec_magic(path):
    assert _repo_contained_path(path) is None


def test_extract_file_paths_drops_escaping_symlink_with_cwd(tmp_path):
    repo = _make_repo_with_escaping_symlink(tmp_path)
    task = "summarize src/link.py and src/real.py"
    # Text-only (no cwd) cannot resolve the symlink, so both pass.
    assert _extract_file_paths(task) == ["src/link.py", "src/real.py"]
    # cwd-aware: the escaping symlink is dropped, the real file survives.
    assert _extract_file_paths(task, str(repo)) == ["src/real.py"]


# --- cwd-aware symlink that resolves into a FORBIDDEN tree ----------------
# A symlink whose name passes every lexical rule AND whose realpath stays
# inside the repo, but lands in `internal/**` (`src/public.py ->
# ../internal/private.py`). Containment alone is not enough — the resolver
# must re-test the RESOLVED repo-relative path against the forbidden globs.
def _make_repo_with_symlink_into_forbidden(tmp_path):
    repo = tmp_path / "repo"
    (repo / "src").mkdir(parents=True)
    (repo / "internal").mkdir()
    (repo / "internal" / "private.py").write_text("PRIVATE")
    # allowed name, in-repo target, but resolves inside the forbidden tree
    os.symlink("../internal/private.py", repo / "src" / "public.py")
    # an in-repo symlink at an allowed target — must still pass
    (repo / "src" / "real.py").write_text("ok")
    os.symlink("../real.py", repo / "src" / "innerlink.py")
    return repo


def test_repo_contained_path_rejects_symlink_into_forbidden(tmp_path):
    repo = _make_repo_with_symlink_into_forbidden(tmp_path)
    # Lexical-only (no cwd) sees the clean name `src/public.py` — it passes,
    # which is precisely the hole the resolved-forbidden check closes.
    assert _repo_contained_path("src/public.py") == "src/public.py"
    # With cwd, the symlink resolves into internal/** → rejected, even though
    # it stays inside the repo root (containment alone is not enough).
    assert _repo_contained_path("src/public.py", str(repo)) is None
    # An in-repo symlink at an ALLOWED target must still resolve fine, so the
    # new check doesn't regress the benign case.
    assert _repo_contained_path("src/innerlink.py", str(repo)) == "src/innerlink.py"


def test_extract_file_paths_drops_symlink_into_forbidden_with_cwd(tmp_path):
    repo = _make_repo_with_symlink_into_forbidden(tmp_path)
    task = "show me src/public.py and src/real.py"
    # cwd-aware: the symlink into forbidden content is dropped, real file survives.
    assert _extract_file_paths(task, str(repo)) == ["src/real.py"]
