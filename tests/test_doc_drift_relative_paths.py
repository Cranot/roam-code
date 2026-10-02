"""Explicit dot-relative documentation paths keep their document authority.

The CLI control uses an isolated temporary Git repo/index through the existing
fixture. No network, user checkout mutation, or real-time expectation is involved.
"""

from __future__ import annotations

import pytest

from roam.commands import cmd_doc_drift as mod
from tests.test_doc_drift import _finding, _invoke_json, _make_project


class IgnoreAuthority:
    def __init__(self, result=False):
        self.result = result
        self.seen = []

    def matches(self, path):
        self.seen.append(path)
        return self.result


@pytest.mark.parametrize(
    "text",
    [
        "Read [the source](../src/app.py).",
        "Read [`src/app.py`](../src/app.py).",
        "Read [the source](../src/app.py#function).",
    ],
)
def test_simple_dot_relative_markdown_destination_is_one_claim(text):
    claims = mod._extract_path_claims(text, doc="docs/guide.md", line_number=1)
    assert len(claims) == 1
    assert claims[0]["_path"] == "../src/app.py"


@pytest.mark.parametrize(
    "text",
    [
        "Example syntax: `[source](../src/missing.py)`.",
        "Example syntax: ``[source](../src/missing.py)``.",
        "Example syntax: ```[`source`](../src/missing.py)```.",
        "Read [source](https://example.org/src/app.py).",
    ],
)
def test_literal_link_syntax_and_external_urls_are_not_local_path_claims(text):
    assert mod._extract_path_claims(text, doc="docs/guide.md", line_number=1) == []


@pytest.mark.parametrize(
    "doc,path,expected,status",
    [
        ("docs/guide.md", "../src/app.py", "src/app.py", "verified"),
        ("docs/nested/guide.md", "../../src/app.py", "src/app.py", "verified"),
        ("docs/guide.md", "./src/app.py", "docs/src/app.py", "verified"),
        ("docs/guide.md", "../src/missing.py", "src/missing.py", "drifted"),
        ("docs/guide.md", "./missing/path.py", "docs/missing/path.py", "drifted"),
        # Unqualified source paths retain existing repository-root precedence.
        ("docs/guide.md", "src/app.py", "src/app.py", "verified"),
    ],
)
def test_explicit_relative_paths_do_not_borrow_a_different_existing_target(tmp_path, doc, path, expected, status):
    for name in ("src/app.py", "docs/src/app.py", "docs/src/missing.py", "missing/path.py"):
        target = tmp_path / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("pass\n", encoding="utf-8")
    # A discovered Markdown document really exists. POSIX must traverse its
    # directory even before `..`; Windows normalizes a missing directory away.
    document = tmp_path / doc
    document.parent.mkdir(parents=True, exist_ok=True)
    document.write_text(f"Use `{path}`.\n", encoding="utf-8")
    claim = mod._extract_path_claims(document.read_text(encoding="utf-8"), doc=doc, line_number=1)[0]
    ignore = IgnoreAuthority()
    result = mod._evaluate_path_claim(claim, tmp_path, ignore)
    assert result["status"] == status
    assert ignore.seen == [expected]
    if status == "verified":
        assert result["resolved_path"] == expected


@pytest.mark.parametrize("doc,path", [("README.md", "../outside/file.py"), ("docs/guide.md", "../../outside/file.py")])
def test_genuine_escape_is_unknown_before_any_ignore_lookup(tmp_path, doc, path):
    root = tmp_path / "project"
    root.mkdir()
    outside = tmp_path / "outside/file.py"
    outside.parent.mkdir()
    outside.write_text("pass\n", encoding="utf-8")
    claim = mod._extract_path_claims(f"Use `{path}`.", doc=doc, line_number=1)[0]
    ignore = IgnoreAuthority()
    result = mod._evaluate_path_claim(claim, root, ignore)
    assert result["status"] == "unverifiable"
    assert result["actual"] == "path escapes project root"
    assert ignore.seen == []


@pytest.mark.parametrize("authority", [True, None])
def test_normalized_relative_path_preserves_ignore_and_unknown_authorities(tmp_path, authority):
    source = tmp_path / "src/app.py"
    source.parent.mkdir()
    source.write_text("pass\n", encoding="utf-8")
    claim = mod._extract_path_claims("Use `../src/app.py`.", doc="docs/guide.md", line_number=1)[0]
    ignore = IgnoreAuthority(authority)
    result = mod._evaluate_path_claim(claim, tmp_path, ignore)
    assert ignore.seen == ["src/app.py"]
    assert result["status"] == "unverifiable"
    assert result["actual"] == ("gitignored path" if authority else None)


@pytest.mark.parametrize("doc,parent", [("docs/guide.md", "../"), ("docs/nested/guide.md", "../../")])
def test_real_cli_accepts_nested_markdown_parent_link_but_retains_missing_target(tmp_path, cli_runner, doc, parent):
    project = _make_project(
        tmp_path,
        docs={doc: f"Read [the source]({parent}src/app.py) and [missing source]({parent}src/missing.py).\n"},
        extra_files={"docs/src/missing.py": "pass\n"},
    )
    result, payload = _invoke_json(cli_runner, project, "--ci")
    assert result.exit_code == 5
    assert payload["summary"]["docs_scanned"] == 1
    assert payload["summary"]["claims_total"] == 2
    assert payload["summary"]["verified"] == 1
    assert payload["summary"]["drifted"] == 1
    assert _finding(payload, f"{parent}src/app.py")["resolved_path"] == "src/app.py"
    assert _finding(payload, f"{parent}src/missing.py")["status"] == "drifted"
