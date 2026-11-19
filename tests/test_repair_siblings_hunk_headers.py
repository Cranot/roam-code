"""Regression coverage for file-header-like lines inside unified diff hunks."""

from contextlib import nullcontext

import pytest

from roam.commands.cmd_repair_siblings import parse_unified_diff


@pytest.mark.parametrize(
    "deleted,added",
    [
        ("-- old SQL comment", "-- new SQL comment"),
        ("++ old expression", "++ new expression"),
        ("-- old comment", "++ new expression"),
    ],
)
def test_header_like_hunk_lines_preserve_changes_and_positions(deleted, added):
    patch = parse_unified_diff(
        "--- a/query.sql\n"
        "+++ b/query.sql\n"
        "@@ -10,4 +20,4 @@\n"
        " unchanged before\n"
        "-old_value\n"
        "+new_value\n"
        f"-{deleted}\n"
        f"+{added}\n"
        " unchanged after\n"
    )

    assert (patch.old_path, patch.new_path) == ("query.sql", "query.sql")
    assert patch.deleted_lines == ["old_value", deleted]
    assert patch.added_lines == ["new_value", added]
    assert patch.changed_old_lines == [11, 12]
    # Deletions anchor at the next new-file line, as well as additions.
    assert patch.changed_new_lines == [21, 21, 22, 22]
    assert patch.first_new_line() == 21


def test_multiple_hunks_and_plain_unified_file_boundaries():
    patch = parse_unified_diff(
        "--- a/first.sql\n"
        "+++ b/first.sql\n"
        "@@ -1 +1 @@\n"
        "-old\n"
        "+new\n"
        "@@ -8 +9 @@\n"
        "-later_old\n"
        "+later_new\n"
        "--- a/second.sql\n"
        "+++ b/second.sql\n"
        "@@ -1 +1 @@\n"
        "-second_old\n"
        "+second_new\n"
    )

    assert (patch.old_path, patch.new_path) == ("first.sql", "first.sql")
    assert patch.deleted_lines == ["old", "later_old"]
    assert patch.added_lines == ["new", "later_new"]
    assert patch.changed_old_lines == [1, 8]
    assert patch.changed_new_lines == [1, 1, 9, 9]


@pytest.mark.parametrize("prefix", ["", "diff --git a/first.py b/first.py\n"])
@pytest.mark.parametrize("count,context", [(4, " keep\n\n"), (5, " keep\n")])
def test_file_boundary_after_incomplete_hunk(prefix, count, context):
    disclosure = pytest.warns(UserWarning, match="hunk counts incomplete") if count == 5 else nullcontext()
    with disclosure:
        patch = parse_unified_diff(
            prefix
            + "--- a/first.py\n+++ b/first.py\n"
            + f"@@ -1,{count} +1,{count} @@\n"
            + context
            + "-old\n+new\n tail\n"
            + ("diff --git a/second.py b/second.py\n" if prefix else "")
            + "--- a/second.py\n+++ b/second.py\n@@ -1 +1 @@\n-second_old\n+second_new\n"
        )
    assert patch.deleted_lines == ["old"]
    assert patch.added_lines == ["new"]
