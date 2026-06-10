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

from roam.plan.compiler import _extract_file_paths


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
