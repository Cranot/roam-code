"""critique must count diffs that carry no hunks (mode-only, rename-only, binary).

A diff with ``diff --git`` and ``old mode``/``new mode`` lines but no ``+++``
line used to be reported as ``changed files: 0``. These tests drive the real CLI.
"""

from __future__ import annotations

import json
import os
import subprocess

import pytest
from click.testing import CliRunner

from roam.cli import cli
from tests import test_critique as _fixtures

critique_project = _fixtures.critique_project
_DIFF_REFRESH_ONLY = _fixtures._DIFF_REFRESH_ONLY

_MODE_ONLY = """\
diff --git a/scripts/run.sh b/scripts/run.sh
old mode 100644
new mode 100755
"""

_RENAME_ONLY = """\
diff --git a/src/old_name.py b/src/new_name.py
similarity index 100%
rename from src/old_name.py
rename to src/new_name.py
"""

_BINARY = """\
diff --git a/assets/logo.png b/assets/logo.png
index 1111111..2222222 100644
Binary files a/assets/logo.png and b/assets/logo.png differ
"""


def _run(diff, *flags):
    return CliRunner().invoke(cli, [*flags, "critique"], input=diff)


@pytest.mark.parametrize(
    "diff,kind",
    [(_MODE_ONLY, "mode"), (_RENAME_ONLY, "rename"), (_BINARY, "binary")],
    ids=["mode_only", "rename_only", "binary"],
)
def test_hunkless_diff_is_counted_in_json(critique_project, diff, kind):
    result = _run(diff, "--json")
    assert result.exit_code in (0, 5), result.output
    summary = json.loads(result.stdout)["summary"]
    assert summary["changed_files_total"] == 1
    assert summary["changed_files_without_hunks"] == 1
    assert [c["kind"] for c in json.loads(result.stdout)["non_content_changes"]] == [kind]


def test_mode_only_records_old_and_new_mode(critique_project):
    change = json.loads(_run(_MODE_ONLY, "--json").stdout)["non_content_changes"][0]
    assert (change["path"], change["old_mode"], change["new_mode"]) == ("scripts/run.sh", "100644", "100755")


def test_rename_records_both_paths(critique_project):
    change = json.loads(_run(_RENAME_ONLY, "--json").stdout)["non_content_changes"][0]
    assert (change["old_path"], change["path"]) == ("src/old_name.py", "src/new_name.py")


def test_hunkless_diff_text_does_not_say_zero_files(critique_project):
    result = _run(_MODE_ONLY)
    assert result.exit_code in (0, 5), result.output
    assert "changed files:   1 (1 without content hunks)" in result.output
    assert "changed files:   0" not in result.output


def test_mixed_diff_counts_both(critique_project):
    result = _run(_DIFF_REFRESH_ONLY + _MODE_ONLY, "--json")
    summary = json.loads(result.stdout)["summary"]
    assert summary["changed_files_total"] == 2
    assert summary["changed_files_without_hunks"] == 1
    assert summary["changed_files"] == 1  # symbol-bearing regions unchanged


# --- valid-case controls: behaviour identical to before -----------------------


def test_control_content_edit_text_and_legacy_fields_conserved(critique_project):
    """Parent-green: only fields and text that existed before this change."""
    result = _run(_DIFF_REFRESH_ONLY)
    assert "changed files:   1\n" in result.output
    assert "without content hunks" not in result.output
    assert json.loads(_run(_DIFF_REFRESH_ONLY, "--json").stdout)["summary"]["changed_files"] == 1


def test_content_edit_has_empty_non_content_schema(critique_project):
    """New schema: a plain content edit reports no hunk-less entries."""
    summary = json.loads(_run(_DIFF_REFRESH_ONLY, "--json").stdout)["summary"]
    assert summary["changed_files_total"] == 1
    assert summary["changed_files_without_hunks"] == 0
    assert json.loads(_run(_DIFF_REFRESH_ONLY, "--json").stdout)["non_content_changes"] == []


@pytest.mark.parametrize("payload", ["", "   \n"])
def test_control_empty_input_still_refused(critique_project, payload):
    assert _run(payload, "--json").exit_code == 2


def test_control_non_diff_text_still_invalid(critique_project):
    result = _run("hello world, not a diff\n", "--json")
    assert result.exit_code == 2
    assert json.loads(result.stdout)["error_code"] == "INVALID_DIFF"


# --- git C-quoted paths (core.quotePath default) ------------------------------


def _git(proj, *args):
    return subprocess.run(
        ["git", "-c", "user.name=Test", "-c", "user.email=test@example.test", *args],
        cwd=proj,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
    )


def test_quoted_mode_diff_cli(critique_project):
    """A real git mode change on a non-ASCII filename must be counted."""
    name = "caf\u00e9.sh"
    (critique_project / name).write_text("#!/bin/sh\necho hi\n", encoding="utf-8")
    _git(critique_project, "add", name)
    _git(critique_project, "commit", "-m", "add script")
    _git(critique_project, "update-index", "--chmod=+x", name)
    diff = _git(critique_project, "-c", "core.quotePath=true", "diff", "--cached").stdout
    assert 'diff --git "a/caf\\303\\251.sh"' in diff, diff  # real git really quotes it
    data = json.loads(_run(diff, "--json").stdout)
    summary = data["summary"]
    assert summary["changed_files_total"] == 1
    assert summary["changed_files_without_hunks"] == 1
    change = data["non_content_changes"][0]
    assert (change["kind"], change["path"], change["new_mode"]) == ("mode", name, "100755")


@pytest.mark.parametrize(
    "header,old,new",
    [
        ('diff --git "a/caf\\303\\251.sh" "b/caf\\303\\251.sh"', "caf\u00e9.sh", "caf\u00e9.sh"),
        ('diff --git "a/q\\"x\\\\y.sh" "b/q\\"x\\\\y.sh"', 'q"x\\y.sh', 'q"x\\y.sh'),
        ('diff --git "a/tab\\there.sh" "b/tab\\there.sh"', "tab\there.sh", "tab\there.sh"),
        ("diff --git a/with space.sh b/with space.sh", "with space.sh", "with space.sh"),
        ('diff --git a/plain.sh "b/caf\\303\\251.sh"', "plain.sh", "caf\u00e9.sh"),
    ],
    ids=["utf8_octal", "quote_and_backslash", "tab", "space_unquoted", "mixed_sides"],
)
def test_header_parser_decodes_quoted_paths(header, old, new):
    from roam.commands.changed_files import parse_diff_file_headers

    [entry] = parse_diff_file_headers(header + "\nold mode 100644\nnew mode 100755\n")
    assert (entry["old_path"], entry["path"], entry["kind"]) == (old, new, "mode")


def test_quoted_rename_paths_decoded():
    from roam.commands.changed_files import parse_diff_file_headers

    text = (
        'diff --git "a/caf\\303\\251.py" "b/n\\303\\261.py"\n'
        "similarity index 100%\n"
        'rename from "caf\\303\\251.py"\n'
        'rename to "n\\303\\261.py"\n'
    )
    [entry] = parse_diff_file_headers(text)
    assert (entry["kind"], entry["old_path"], entry["path"]) == ("rename", "caf\u00e9.py", "n\u00f1.py")


# --- a decoded quoted path keeps every byte git preserved ---------------------


@pytest.mark.skipif(os.name != "posix", reason="Windows cannot create names with trailing whitespace")
@pytest.mark.parametrize("name", ["caf\u00e9.sh\t", "caf\u00e9.sh ", "plain.sh "], ids=["tab", "space", "ascii_space"])
def test_quoted_mode_path_keeps_trailing_whitespace_cli(critique_project, name):
    (critique_project / name).write_text("#!/bin/sh\necho hi\n", encoding="utf-8")
    _git(critique_project, "add", name)
    _git(critique_project, "commit", "-m", "add script")
    _git(critique_project, "update-index", "--chmod=+x", name)
    diff = _git(critique_project, "-c", "core.quotePath=true", "diff", "--cached").stdout
    data = json.loads(_run(diff, "--json").stdout)
    summary = data["summary"]
    assert summary["changed_files_total"] == 1
    [entry] = data["non_content_changes"]
    assert entry["path"] == name, repr(entry)
    assert entry["old_path"] == name, repr(entry)


@pytest.mark.parametrize(
    "header,expected",
    [
        ('diff --git "a/caf\\303\\251.sh " "b/caf\\303\\251.sh "', "caf\u00e9.sh "),
        ('diff --git "a/caf\\303\\251.sh\\t" "b/caf\\303\\251.sh\\t"', "caf\u00e9.sh\t"),
        ('diff --git "a/ lead.sh" "b/ lead.sh"', " lead.sh"),
        ("diff --git a/plain.sh  b/plain.sh ", "plain.sh "),
    ],
    ids=["trailing_space", "trailing_tab", "leading_space", "ascii_trailing_space"],
)
def test_header_parser_keeps_whitespace_inside_names(header, expected):
    from roam.commands.changed_files import parse_diff_file_headers

    [entry] = parse_diff_file_headers(header + "\nold mode 100644\nnew mode 100755\n")
    assert (entry["old_path"], entry["path"]) == (expected, expected)


def test_quoted_rename_keeps_trailing_space():
    from roam.commands.changed_files import parse_diff_file_headers

    text = (
        'diff --git "a/o\\303\\251 " "b/n\\303\\261 "\n'
        "similarity index 100%\n"
        'rename from "o\\303\\251 "\n'
        'rename to "n\\303\\261 "\n'
    )
    [entry] = parse_diff_file_headers(text)
    assert (entry["old_path"], entry["path"]) == ("o\u00e9 ", "n\u00f1 ")


@pytest.mark.parametrize("deleted", [False, True])
def test_content_file_count_includes_deletions(critique_project, deleted):
    diff = (
        "diff --git a/gone.py b/gone.py\n"
        + ("deleted file mode 100644\n" if deleted else "")
        + "index 1111111..2222222\n--- a/gone.py\n"
        + ("+++ /dev/null\n@@ -1 +0,0 @@\n-x = 1\n" if deleted else "+++ b/gone.py\n@@ -1 +1 @@\n-x = 1\n+x = 2\n")
    )
    result = _run(diff, "--json")
    assert result.exit_code in (0, 5), result.output
    summary = json.loads(result.output)["summary"]
    assert summary["changed_files_total"] == 1
    assert summary["changed_files_without_hunks"] == int(deleted)


@pytest.mark.skipif(os.name != "posix", reason="a working-tree executable-bit change needs a POSIX filesystem")
@pytest.mark.parametrize("content_changed", [False, True])
def test_worktree_mode_only_requires_unchanged_content(critique_project, content_changed):
    from roam.commands.changed_files import git_mode_only_files

    path = critique_project / "run.sh"
    path.write_text("#!/bin/sh\necho hi\n")
    _git(critique_project, "config", "core.fileMode", "true")
    _git(critique_project, "add", path.name)
    _git(critique_project, "commit", "-m", "script")
    path.chmod(0o755)
    if content_changed:
        path.write_text("#!/bin/sh\necho changed\n")
    assert (path.name in git_mode_only_files(critique_project, "HEAD")) is not content_changed


@pytest.mark.parametrize("quote_path", ["false", "true"])
def test_quoted_mode_fixture_overrides_git_configuration(critique_project, quote_path):
    _git(critique_project, "config", "core.quotePath", quote_path)
    test_quoted_mode_diff_cli(critique_project)


@pytest.mark.parametrize(
    "header,path",
    [
        ("diff --git i/scripts/run.sh w/scripts/run.sh", "scripts/run.sh"),
        ("diff --git scripts/run.sh scripts/run.sh", "scripts/run.sh"),
        ("diff --git a/x b/run.sh b/x b/run.sh", "x b/run.sh"),
    ],
)
def test_header_variants_counted(critique_project, header, path):
    data = json.loads(_run(header + "\nold mode 100644\nnew mode 100755\n", "--json").stdout)
    assert data["summary"]["changed_files_total"] == 1
    assert data["summary"]["changed_files_total_definition"]
    assert data["non_content_changes"][0]["path"] == path


def test_unsplit_header_is_disclosed(critique_project):
    header = "diff --git cannot-split"
    data = json.loads(_run(header + "\nold mode 100644\nnew mode 100755\n", "--json").stdout)
    assert data["summary"]["changed_files_total"] == 1
    assert data["non_content_changes"][0]["kind"] == "other"
    assert data["non_content_changes"][0]["raw_header"] == header


def test_non_content_list_has_budgeted_location(critique_project):
    data = json.loads(_run(_MODE_ONLY, "--json").stdout)
    assert "non_content_changes" not in data["summary"]
    assert len(data["non_content_changes"]) == 1


def test_every_git_header_is_counted_even_with_repeated_path(critique_project):
    data = json.loads(_run(_DIFF_REFRESH_ONLY * 2, "--json").stdout)
    assert data["summary"]["changed_files_total"] == 2


def test_empty_git_header_is_still_counted():
    from roam.commands.changed_files import parse_diff_file_headers

    [entry] = parse_diff_file_headers("diff --git \n")
    assert entry["raw_header"] == "diff --git "
    assert entry["kind"] == "other"


def test_non_content_payload_obeys_envelope_budget(critique_project):
    diff = "".join(_MODE_ONLY.replace("run.sh", f"tool_{i:05}.sh") for i in range(5000))
    result = _run(diff, "--json", "--budget", "500")
    data = json.loads(result.stdout)
    assert data["summary"]["changed_files_total"] == 5000
    assert "non_content_changes" not in data["summary"]
    assert data["summary"]["truncated"] is True
    assert len(result.stdout) < 20000
