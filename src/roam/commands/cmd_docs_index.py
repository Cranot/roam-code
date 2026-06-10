"""Planning-doc index hygiene: orphan memos and broken local links.

Output formats: text (default), ``--json``. This command is intentionally
index-free because it checks local Markdown files rather than indexed code
symbols.

Broken-link checking covers ANY local relative link target that resolves to a
path on disk -- not just ``*.md``. A memo that points at ``../../src/roam/foo.py``
or ``../docs/architecture.html`` is validated the same way as a sibling ``.md``
link, so stale impl-pointers and doc cross-links are caught (DOCS-ORG-AUDIT gap
(b)). Links inside inline-code spans (`` `like this` ``) and fenced code blocks
are ignored, because per CommonMark a ``[x](y)`` inside code is literal text,
not a live link -- this is what keeps illustrative example paths in memos from
flagging as broken.

SARIF is deliberately NOT emitted: the output is Markdown-doc hygiene (orphan
memos + broken local links), not file-located code findings, so there are no
code ``locations[]`` coordinates to populate.
"""

from __future__ import annotations

import re
import shlex
from pathlib import Path

import click

from roam.capability import roam_capability
from roam.output.formatter import json_envelope, to_json

_LINK_RE = re.compile(r"\[[^\]]+\]\(([^)]+)\)")


def _is_index_doc(path: Path) -> bool:
    return path.name == "README.md" or path.name.endswith("-INDEX.md")


def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def _display_path(path: Path, root: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return path.as_posix()


def _blank_inline_code(line: str) -> str:
    """Replace inline-code spans (`` `...` ``) with equal-length blanks.

    Length is preserved 1:1 so byte offsets -- and therefore the reported line
    numbers -- stay correct. Backtick runs act as matched delimiters (CommonMark
    code spans), so ``` ``a`b`` ``` is one span. An unterminated run is left as a
    literal backtick.
    """
    result: list[str] = []
    i, n = 0, len(line)
    while i < n:
        if line[i] != "`":
            result.append(line[i])
            i += 1
            continue
        run_end = i
        while run_end < n and line[run_end] == "`":
            run_end += 1
        delim = line[i:run_end]
        close = line.find(delim, run_end)
        if close == -1:
            result.append(delim)  # unterminated -> literal backticks
            i = run_end
            continue
        span_end = close + len(delim)
        result.append(" " * (span_end - i))
        i = span_end
    return "".join(result)


def _strip_code(text: str) -> str:
    """Blank out fenced code blocks and inline-code spans, length-preserving.

    Newlines and total length are kept intact so a link's offset still maps to
    the right source line. Used only for the broken-link scan; orphan detection
    keeps matching the raw text.
    """
    out: list[str] = []
    in_fence = False
    fence_marker = ""
    for line in text.split("\n"):
        stripped = line.lstrip()
        if not in_fence and (stripped.startswith("```") or stripped.startswith("~~~")):
            in_fence = True
            fence_marker = stripped[:3]
            out.append(" " * len(line))
            continue
        if in_fence:
            if stripped.startswith(fence_marker):
                in_fence = False
            out.append(" " * len(line))
            continue
        out.append(_blank_inline_code(line))
    return "\n".join(out)


def _local_link_target(raw_target: str) -> str | None:
    """Return the on-disk target of a local relative link, or ``None``.

    ``None`` for anything that isn't a checkable local file reference: empty
    targets, pure ``#anchor`` links, ``scheme:`` URLs (``http``/``mailto``/...),
    and glob patterns (``*.py``) that are illustrative rather than concrete.
    Any other extension is kept -- ``.py`` / ``.html`` / ``.txt`` pointers are
    validated the same as ``.md``.
    """
    target = raw_target.strip()
    if target.startswith("<") and target.endswith(">"):
        target = target[1:-1].strip()
    if not target or target.startswith("#"):
        return None
    if re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*:", target):
        return None

    target = target.split()[0]
    target_no_fragment = target.split("#", 1)[0]
    if not target_no_fragment:
        return None
    if any(ch in target_no_fragment for ch in "*?[]"):
        return None
    return target_no_fragment


def _line_number(text: str, offset: int) -> int:
    return text.count("\n", 0, offset) + 1


def _markdown_files(root: Path) -> list[Path]:
    if not root.is_dir():
        return []
    return sorted(path for path in root.iterdir() if path.is_file() and path.suffix.lower() == ".md")


def scan_docs_index(root: Path) -> dict:
    root = root.resolve()
    all_docs = _markdown_files(root)
    memo_docs = [path for path in all_docs if not _is_index_doc(path)]
    texts = {path: _read_text(path) for path in all_docs}

    orphans: list[dict] = []
    for memo in memo_docs:
        referenced_by = [
            _display_path(source, root) for source, text in texts.items() if source != memo and memo.name in text
        ]
        if not referenced_by:
            orphans.append({"file": _display_path(memo, root)})

    broken_links: list[dict] = []
    for source in all_docs:
        # Scan the code-stripped text so links inside `inline code` / fenced
        # blocks (illustrative example paths) don't flag. _strip_code is
        # length-preserving, so offsets still map to the right source line.
        scan_text = _strip_code(texts[source])
        for match in _LINK_RE.finditer(scan_text):
            target = _local_link_target(match.group(1))
            if target is None:
                continue
            resolved = (source.parent / target).resolve()
            if not resolved.exists():
                broken_links.append(
                    {
                        "source": _display_path(source, root),
                        "line": _line_number(scan_text, match.start()),
                        "target": target,
                    }
                )

    return {
        "root": root.as_posix(),
        "markdown_files": len(all_docs),
        "checked_files": len(memo_docs),
        "orphans": orphans,
        "broken_links": broken_links,
    }


def _verdict(result: dict) -> str:
    orphan_count = len(result["orphans"])
    broken_count = len(result["broken_links"])
    checked = result["checked_files"]
    if orphan_count == 0 and broken_count == 0:
        return f"docs-index clean: {checked} planning memos checked"
    return f"docs-index found {orphan_count} orphan memos and {broken_count} broken links"


def _missing_dir_payload(root: Path) -> dict:
    return {
        "root": root.as_posix(),
        "markdown_files": 0,
        "checked_files": 0,
        "orphans": [],
        "broken_links": [],
        "missing_dir": True,
    }


def _agent_contract(result: dict) -> dict:
    facts = [
        f"{len(result['orphans'])} orphan files",
        f"{len(result['broken_links'])} broken links",
        f"{result['checked_files']} checked files",
    ]
    next_commands = []
    if result["orphans"] or result["broken_links"]:
        next_commands.append(f"roam docs-index --dir {shlex.quote(result['root'])} --ci")
    return {"facts": facts, "next_commands": next_commands}


@roam_capability(
    name="docs-index",
    category="refactoring",
    summary="Find orphaned planning memos and broken local Markdown links",
    maturity="stable",
    mcp_expose=False,
    mcp_preset=(),
    side_effect=False,
    task_required=False,
    destructive=False,
    stale_sensitive=False,
    ai_safe=True,
    requires_index=False,
)
@click.command("docs-index")
@click.option(
    "--dir",
    "docs_dir",
    default="internal/planning",
    show_default=True,
    type=click.Path(file_okay=False, dir_okay=True, path_type=Path),
    help="Directory of Markdown planning memos to scan.",
)
@click.option("--ci", is_flag=True, help="Exit 1 when orphan memos or broken links are found.")
@click.pass_context
def docs_index(ctx, docs_dir: Path, ci: bool) -> None:
    """Find orphaned planning memos and broken local Markdown links."""
    json_mode = ctx.obj.get("json") if ctx.obj else False
    root = docs_dir.resolve()

    if not root.is_dir():
        result = _missing_dir_payload(root)
        result["summary"] = {
            "verdict": f"docs-index directory not found: {docs_dir.as_posix()}",
            "checked_files": 0,
            "orphan_count": 0,
            "broken_link_count": 0,
            "partial_success": True,
        }
    else:
        result = scan_docs_index(root)
        result["summary"] = {
            "verdict": _verdict(result),
            "checked_files": result["checked_files"],
            "orphan_count": len(result["orphans"]),
            "broken_link_count": len(result["broken_links"]),
            "partial_success": False,
        }

    has_findings = bool(result["orphans"] or result["broken_links"] or result.get("missing_dir"))

    if json_mode:
        click.echo(
            to_json(
                json_envelope(
                    "docs-index",
                    summary=result["summary"],
                    root=result["root"],
                    markdown_files=result["markdown_files"],
                    checked_files=result["checked_files"],
                    orphans=result["orphans"],
                    broken_links=result["broken_links"],
                    agent_contract=_agent_contract(result),
                )
            )
        )
        if ci and has_findings:
            ctx.exit(1)
        return

    click.echo(f"VERDICT: {result['summary']['verdict']}")
    click.echo(f"Directory: {result['root']}")
    click.echo(f"Checked memos: {result['checked_files']} ({result['markdown_files']} markdown files)")

    if result["orphans"]:
        click.echo("\nOrphan memos:")
        for item in result["orphans"]:
            click.echo(f"  {item['file']}")

    if result["broken_links"]:
        click.echo("\nBroken links:")
        for item in result["broken_links"]:
            click.echo(f"  {item['source']}:{item['line']} -> {item['target']}")

    if ci and has_findings:
        ctx.exit(1)
