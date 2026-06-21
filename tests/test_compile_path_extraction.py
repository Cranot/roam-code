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

from roam.plan.compiler import _extract_file_paths, _likely_files_from_search


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
        lambda *a, **k: (["internal/planning/secret.md", "src/roam/cli.py", "../escape.py"], True),
    )
    files, invoked = _c._likely_files_from_search("cached task", cwd="/tmp/x")
    assert invoked is False  # cache hit → subprocess not run
    assert files == ["src/roam/cli.py"]
