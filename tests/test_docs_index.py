"""Tests for ``roam docs-index`` planning-doc hygiene."""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from conftest import assert_json_envelope, invoke_cli


def _write(path, text: str) -> None:
    path.write_text(text, encoding="utf-8")


def test_docs_index_flags_exact_orphan_and_broken_link(cli_runner, tmp_path):
    docs = tmp_path / "planning"
    docs.mkdir()
    _write(
        docs / "README.md",
        "\n".join(
            [
                "- [Indexed](INDEXED.md)",
                "- [Broken source](BROKEN-SOURCE.md)",
            ]
        ),
    )
    _write(docs / "INDEXED.md", "Indexed memo.\n")
    _write(docs / "BROKEN-SOURCE.md", "[Missing](MISSING.md)\n")
    _write(docs / "ORPHAN.md", "No index entry points here.\n")
    _write(docs / "PLANNING-INDEX.md", "Index docs are not orphan candidates.\n")

    result = invoke_cli(cli_runner, ["docs-index", "--dir", str(docs)], json_mode=True)

    data = json.loads(result.output)
    assert_json_envelope(data, "docs-index")
    assert data["orphans"] == [{"file": "ORPHAN.md"}]
    assert data["broken_links"] == [
        {
            "source": "BROKEN-SOURCE.md",
            "line": 1,
            "target": "MISSING.md",
        }
    ]
    assert data["summary"]["orphan_count"] == 1
    assert data["summary"]["broken_link_count"] == 1


def test_docs_index_flags_broken_non_md_link(cli_runner, tmp_path):
    """DOCS-ORG-AUDIT gap (b): non-.md local pointers (.py/.html/...) are
    validated too. A live target passes; a stale one is flagged."""
    docs = tmp_path / "planning"
    docs.mkdir()
    (docs / "real_impl.py").write_text("x = 1\n", encoding="utf-8")
    _write(
        docs / "README.md",
        "\n".join(
            [
                "- [valid py](real_impl.py)",
                "- [stale py](../src/roam/gone.py)",
            ]
        ),
    )

    result = invoke_cli(cli_runner, ["docs-index", "--dir", str(docs)], json_mode=True)

    data = json.loads(result.output)
    assert_json_envelope(data, "docs-index")
    assert {b["target"] for b in data["broken_links"]} == {"../src/roam/gone.py"}


def test_docs_index_ignores_links_in_code_spans_and_fences(cli_runner, tmp_path):
    """Links inside `inline code` and fenced blocks are literal text, not live
    links — they must NOT flag (this is what stops illustrative example paths in
    memos from false-positiving)."""
    docs = tmp_path / "planning"
    docs.mkdir()
    _write(
        docs / "README.md",
        "\n".join(
            [
                "Inline example: `[x](../src/roam/NONEXISTENT.py)` is illustrative.",
                "",
                "```python",
                "result = MAP[key](task, cwd)   # python subscript, not a link",
                "```",
                "```",
                "[y](ALSO-MISSING.md)",
                "```",
            ]
        ),
    )

    result = invoke_cli(cli_runner, ["docs-index", "--dir", str(docs)], json_mode=True)

    data = json.loads(result.output)
    assert_json_envelope(data, "docs-index")
    assert data["broken_links"] == []


def test_docs_index_line_numbers_survive_code_stripping(cli_runner, tmp_path):
    """Code stripping is length-preserving: a broken link after a fenced block
    still reports its true source line."""
    docs = tmp_path / "planning"
    docs.mkdir()
    _write(
        docs / "README.md",
        "\n".join(
            [
                "```",
                "[x](IN-FENCE.md)",
                "```",
                "[real](MISSING.md)",
            ]
        ),
    )

    result = invoke_cli(cli_runner, ["docs-index", "--dir", str(docs)], json_mode=True)

    data = json.loads(result.output)
    assert data["broken_links"] == [{"source": "README.md", "line": 4, "target": "MISSING.md"}]


def test_docs_index_ci_exits_one_on_findings(cli_runner, tmp_path):
    docs = tmp_path / "planning"
    docs.mkdir()
    _write(docs / "README.md", "")
    _write(docs / "ORPHAN.md", "No incoming filename reference.\n")

    result = invoke_cli(cli_runner, ["docs-index", "--dir", str(docs), "--ci"])

    assert result.exit_code == 1
    assert "VERDICT: docs-index found 1 orphan memos and 0 broken links" in result.output
