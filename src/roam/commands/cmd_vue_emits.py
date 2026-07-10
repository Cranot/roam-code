"""Find Vue child emits that have no handler at a resolved parent usage."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import click

from roam.capability import roam_capability
from roam.db.connection import find_project_root
from roam.index.parser import _preprocess_vue, extract_vue_template, read_source
from roam.output.formatter import json_envelope, to_json

_VUE_DIRS_TO_SKIP = frozenset({".git", ".roam", "node_modules"})
_DEFAULT_IMPORT_RE = re.compile(r"\bimport\s+([A-Za-z_$][\w$]*)\s+from\s*(['\"])(\.\.?/[^'\"]+)\2")
_TAG_RE = re.compile(r'<(?P<name>[A-Za-z][\w.-]*)(?P<attrs>(?:"[^"]*"|\'[^\']*\'|[^<>])*)>')
_HANDLER_RE = re.compile(r"(?:@|v-on:)(?P<event>[A-Za-z_$][\w:.-]*)")
_EVENT_NAME = r"[A-Za-z_$][\w:.-]*"


@dataclass(frozen=True)
class _ComponentEmits:
    events: dict[str, int]


def _mask_js_comments(text: str) -> str:
    """Blank JavaScript comments while preserving strings and line numbers."""
    out = list(text)
    i = 0
    quote: str | None = None
    while i < len(text):
        ch = text[i]
        if quote:
            if ch == "\\":
                i += 2
                continue
            if ch == quote:
                quote = None
            i += 1
            continue
        if ch in "'\"`":
            quote = ch
            i += 1
            continue
        if text.startswith("//", i):
            end = text.find("\n", i)
            end = len(text) if end == -1 else end
            for j in range(i, end):
                out[j] = " "
            i = end
            continue
        if text.startswith("/*", i):
            end = text.find("*/", i + 2)
            end = len(text) if end == -1 else end + 2
            for j in range(i, end):
                if out[j] != "\n":
                    out[j] = " "
            i = end
            continue
        i += 1
    return "".join(out)


def _balanced(text: str, start: int, opening: str, closing: str) -> tuple[str, int] | None:
    """Return the contents and end offset of one balanced region."""
    depth = 0
    quote: str | None = None
    i = start
    while i < len(text):
        ch = text[i]
        if quote:
            if ch == "\\":
                i += 2
                continue
            if ch == quote:
                quote = None
            i += 1
            continue
        if ch in "'\"`":
            quote = ch
        elif ch == opening:
            depth += 1
        elif ch == closing:
            depth -= 1
            if depth == 0:
                return text[start + 1 : i], i + 1
        i += 1
    return None


def _line_number(text: str, offset: int) -> int:
    return text.count("\n", 0, offset) + 1


def _type_events(type_body: str) -> set[str]:
    events = {match.group(2) for match in re.finditer(r"\(\s*[A-Za-z_$][\w$]*\s*:\s*(['\"])([^'\"]+)\1", type_body)}
    for match in re.finditer(
        rf"(?:^|[;{{,\n])\s*(?:['\"]({_EVENT_NAME})['\"]|({_EVENT_NAME}))\s*\??\s*:",
        type_body,
    ):
        events.add(match.group(1) or match.group(2))
    return events


def _array_events(arg: str) -> set[str] | None:
    if not arg.lstrip().startswith("[") or not arg.rstrip().endswith("]"):
        return None
    body = arg.strip()[1:-1]
    if not re.fullmatch(r"\s*(?:['\"][^'\"]+['\"]\s*(?:,\s*)?)*", body):
        return None
    return {match.group(2) for match in re.finditer(r"(['\"])([^'\"]+)\1", body)}


def _extract_component_emits(script: str) -> _ComponentEmits | None:
    """Return static emitted events, or None when the declaration is dynamic."""
    masked = _mask_js_comments(script)
    events: dict[str, int] = {}
    unknown_declaration = False
    saw_declaration = False

    for match in re.finditer(r"\bdefineEmits\b", masked):
        saw_declaration = True
        cursor = match.end()
        while cursor < len(masked) and masked[cursor].isspace():
            cursor += 1
        type_body = None
        if cursor < len(masked) and masked[cursor] == "<":
            parsed_type = _balanced(masked, cursor, "<", ">")
            if parsed_type is None:
                unknown_declaration = True
                continue
            type_body, cursor = parsed_type
            type_is_static = type_body.lstrip().startswith("{") and type_body.rstrip().endswith("}")
            if not type_is_static:
                unknown_declaration = True
            else:
                for event in _type_events(type_body):
                    events.setdefault(event, _line_number(script, match.start()))
        while cursor < len(masked) and masked[cursor].isspace():
            cursor += 1
        if cursor >= len(masked) or masked[cursor] != "(":
            unknown_declaration = True
            continue
        parsed_arg = _balanced(masked, cursor, "(", ")")
        if parsed_arg is None:
            unknown_declaration = True
            continue
        arg, _ = parsed_arg
        if type_body is None:
            array_events = _array_events(arg)
            if array_events is None:
                unknown_declaration = True
            else:
                for event in array_events:
                    events.setdefault(event, _line_number(script, match.start()))

    for match in re.finditer(rf"(?:\$emit|\bemit)\s*\(\s*(['\"])({_EVENT_NAME})\1", masked):
        events.setdefault(match.group(2), _line_number(script, match.start()))

    if unknown_declaration:
        return None
    if not saw_declaration and not events:
        return None
    return _ComponentEmits(events)


def _kebab_name(name: str) -> str:
    return re.sub(r"(?<!^)([A-Z])", r"-\1", name).lower()


def _resolve_vue_import(parent: Path, specifier: str, root: Path) -> Path | None:
    """Resolve one relative import only when exactly one `.vue` target exists."""
    raw = (parent.parent / specifier).resolve()
    root = root.resolve()
    try:
        raw.relative_to(root)
    except ValueError:
        return None
    candidates = [raw] if raw.suffix.lower() == ".vue" else [raw.with_suffix(".vue"), raw / "index.vue"]
    existing = [candidate for candidate in candidates if candidate.is_file()]
    return existing[0] if len(existing) == 1 else None


def _event_handlers(attrs: str) -> set[str]:
    return {match.group("event").split(".", 1)[0] for match in _HANDLER_RE.finditer(attrs)}


def _scan(root: Path) -> dict:
    vue_files = sorted(
        path for path in root.rglob("*.vue") if not any(part in _VUE_DIRS_TO_SKIP for part in path.parts)
    )
    components: dict[Path, _ComponentEmits] = {}
    scripts: dict[Path, str] = {}
    templates: dict[Path, tuple[str, int] | None] = {}
    for path in vue_files:
        source = read_source(path)
        if source is None:
            continue
        processed, _ = _preprocess_vue(source)
        script = processed.decode("utf-8", errors="replace")
        emits = _extract_component_emits(script)
        if emits is not None:
            components[path.resolve()] = emits
        scripts[path.resolve()] = script
        templates[path.resolve()] = extract_vue_template(source)

    findings: list[dict] = []
    usages_checked = 0
    for parent in vue_files:
        parent_key = parent.resolve()
        template_result = templates.get(parent_key)
        if template_result is None:
            continue
        template, start_line = template_result
        for import_match in _DEFAULT_IMPORT_RE.finditer(scripts.get(parent_key, "")):
            child = _resolve_vue_import(parent, import_match.group(3), root)
            child_emits = components.get(child) if child else None
            if child_emits is None:
                continue
            local_name = import_match.group(1)
            tag_names = {local_name.lower(), _kebab_name(local_name)}
            for tag_match in _TAG_RE.finditer(template):
                if tag_match.group("name").lower() not in tag_names:
                    continue
                usages_checked += 1
                line = start_line + template.count("\n", 0, tag_match.start())
                handlers = _event_handlers(tag_match.group("attrs"))
                for event, child_line in child_emits.events.items():
                    if event in handlers:
                        continue
                    findings.append(
                        {
                            "parent": parent.relative_to(root).as_posix(),
                            "child": child.relative_to(root).as_posix(),
                            "event": event,
                            "line": line,
                            "child_line": child_line,
                            "message": f"{child.name} emits `{event}` but this usage has no `@{event}` handler",
                        }
                    )

    findings.sort(key=lambda item: (item["parent"], item["line"], item["child"], item["event"]))
    return {
        "findings": findings,
        "components_scanned": len(components),
        "usages_checked": usages_checked,
    }


@roam_capability(
    name="vue-emits",
    category="health",
    summary="Find statically emitted Vue child events without parent handlers",
    inputs=["Vue single-file components"],
    outputs=["findings", "verdict"],
    examples=["roam vue-emits", "roam --json vue-emits"],
    tags=["vue", "framework", "events"],
    ai_safe=True,
    requires_index=False,
    maturity="stable",
    mcp_expose=False,
    side_effect=False,
    task_required=False,
    destructive=False,
    stale_sensitive=False,
)
@click.command("vue-emits")
@click.pass_context
def vue_emits(ctx):
    """Find Vue child emits without matching handlers in resolved parent usages."""
    json_mode = bool(ctx.obj and ctx.obj.get("json"))
    root = find_project_root()
    result = _scan(root)
    findings = result["findings"]
    if findings:
        verdict = f"{len(findings)} Vue emitted events lack parent handlers"
    else:
        verdict = f"No unresolved Vue emitted events across {result['usages_checked']} component usages"
    summary = {
        "verdict": verdict,
        "finding_count": len(findings),
        "components_scanned": result["components_scanned"],
        "usages_checked": result["usages_checked"],
    }
    if json_mode:
        click.echo(to_json(json_envelope("vue-emits", summary=summary, findings=findings)))
        return
    click.echo(f"VERDICT: {verdict}")
    for finding in findings:
        click.echo(f"  {finding['parent']}:{finding['line']} {finding['message']} (child: {finding['child']})")
