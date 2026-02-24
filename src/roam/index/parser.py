"""Tree-sitter parsing coordinator."""

from __future__ import annotations

import logging
import os
import re
import struct
from pathlib import Path

log = logging.getLogger(__name__)

from tree_sitter_language_pack import get_parser

# Map file extensions to tree-sitter language names.
# This is the single canonical source of truth for extension → language mapping.
# registry.py's _EXTENSION_MAP is derived from this dict (see registry.py).
EXTENSION_MAP = {
    ".vue": "vue",
    ".svelte": "svelte",
    # Python
    ".py": "python",
    ".pyi": "python",
    # JavaScript / ESM variants
    ".js": "javascript",
    ".jsx": "javascript",
    ".mjs": "javascript",
    ".cjs": "javascript",
    # TypeScript variants
    ".ts": "typescript",
    ".tsx": "tsx",
    ".mts": "typescript",
    ".cts": "typescript",
    ".rs": "rust",
    ".go": "go",
    ".java": "java",
    ".c": "c",
    ".h": "c",
    ".cpp": "cpp",
    ".cc": "cpp",
    ".cxx": "cpp",
    ".hpp": "cpp",
    ".hxx": "cpp",
    ".hh": "cpp",
    ".cs": "c_sharp",
    ".rb": "ruby",
    ".php": "php",
    ".swift": "swift",
    ".kt": "kotlin",
    ".kts": "kotlin",
    # Scala
    ".scala": "scala",
    ".sc": "scala",
    # Tier-2 / tree-sitter-only languages (no dedicated roam extractor)
    ".lua": "lua",
    ".zig": "zig",
    ".el": "elisp",
    ".ex": "elixir",
    ".exs": "elixir",
    ".erl": "erlang",
    ".hs": "haskell",
    ".ml": "ocaml",
    ".mli": "ocaml",
    ".r": "r",
    ".R": "r",
    ".jl": "julia",
    ".dart": "dart",
    ".sh": "bash",
    ".bash": "bash",
    ".zsh": "bash",
    ".css": "css",
    ".scss": "scss",
    ".html": "html",
    ".htm": "html",
    ".yaml": "yaml",
    ".yml": "yaml",
    ".toml": "toml",
    ".json": "json",
    ".jsonc": "jsonc",
    ".md": "markdown",
    ".mdx": "mdx",
    ".sql": "sql",
    # HCL / Terraform (regex-only)
    ".tf": "hcl",
    ".tfvars": "hcl",
    ".hcl": "hcl",
    # Salesforce
    ".cls": "apex",
    ".trigger": "apex",
    ".page": "visualforce",
    ".component": "aura",
    ".cmp": "aura",
    ".app": "aura",
    ".evt": "aura",
    ".intf": "aura",
    ".design": "aura",
    # Visual FoxPro
    ".prg": "foxpro",
    ".scx": "foxpro",
}

# Grammar aliasing: languages that reuse existing tree-sitter grammars.
# This allows languages with similar syntax to piggyback on available grammars
# without requiring a dedicated tree-sitter parser.
GRAMMAR_ALIASES = {
    # C# (tree-sitter-language-pack uses "csharp", not "c_sharp")
    "c_sharp": "csharp",
    # Salesforce
    "apex": "java",
    "sfxml": "html",          # SF metadata XML → HTML parser (close enough for structure)
    "aura": "html",
    "visualforce": "html",
    # JSONC (JSON with comments) → json grammar
    "jsonc": "json",
    # MDX (Markdown + JSX) → markdown grammar
    "mdx": "markdown",
}

# Languages that use regex-only extraction (no tree-sitter grammar)
REGEX_ONLY_LANGUAGES = frozenset({"foxpro", "yaml", "hcl"})

# Track parse error stats
parse_errors = {"no_grammar": 0, "parse_error": 0, "unreadable": 0}


def _plugin_language_extensions() -> dict[str, str]:
    try:
        from roam.plugins import get_plugin_language_extensions

        return get_plugin_language_extensions()
    except Exception:
        return {}


def _plugin_grammar_aliases() -> dict[str, str]:
    try:
        from roam.plugins import get_plugin_language_grammar_aliases

        return get_plugin_language_grammar_aliases()
    except Exception:
        return {}


def _find_sct_path(scx_path: Path) -> Path | None:
    """Find the companion .sct memo file for an .scx form file (case-insensitive)."""
    base = scx_path.with_suffix("")
    # Try common case variations first
    for ext in (".sct", ".SCT", ".Sct"):
        candidate = base.with_suffix(ext)
        if candidate.exists():
            return candidate
    # Case-insensitive directory scan as fallback
    parent = scx_path.parent
    stem_lower = base.name.lower()
    try:
        for f in parent.iterdir():
            if f.stem.lower() == stem_lower and f.suffix.lower() == ".sct":
                return f
    except OSError:
        pass
    return None


def _pack_scx_sct(scx_path: Path, scx_bytes: bytes) -> bytes:
    """Pack .scx and .sct bytes into a single length-prefixed blob.

    Format: 4-byte big-endian scx length + scx_bytes + sct_bytes.
    The extractor unpacks this to get both files' data.
    If the .sct is missing, returns just the header + scx_bytes (sct portion empty).
    """
    sct_path = _find_sct_path(scx_path)
    sct_bytes = b""
    if sct_path is not None:
        try:
            with open(sct_path, "rb") as f:
                sct_bytes = f.read()
        except OSError:
            pass
    return struct.pack(">I", len(scx_bytes)) + scx_bytes + sct_bytes


def detect_language(file_path: str) -> str | None:
    """Detect the tree-sitter language name from a file path."""
    # Salesforce metadata files: *.cls-meta.xml, *.object-meta.xml, etc.
    if file_path.endswith("-meta.xml"):
        return "sfxml"
    _, ext = os.path.splitext(file_path)
    ext = ext.lower()
    language = EXTENSION_MAP.get(ext)
    if language:
        return language
    return _plugin_language_extensions().get(ext)


def read_source(path: Path) -> bytes | None:
    """Read file bytes, trying utf-8 then latin-1."""
    try:
        with open(path, "rb") as f:
            raw = f.read()
        # Validate it's decodable text
        try:
            raw.decode("utf-8")
        except UnicodeDecodeError:
            try:
                raw.decode("latin-1")
            except UnicodeDecodeError:
                return None
        return raw
    except OSError:
        return None


def _preprocess_vue(source: bytes) -> tuple[bytes, str]:
    """Extract <script> blocks from a Vue SFC and return (processed_source, effective_language).

    Handles both <script> and <script setup> blocks.
    Preserves line numbers by replacing non-script regions with blank lines.
    """
    text = source.decode("utf-8", errors="replace")
    lines = text.split("\n")
    effective_lang = "javascript"

    # Find all <script...>...</script> regions
    script_pattern = re.compile(
        r'<script(\s[^>]*)?>.*?</script>',
        re.DOTALL,
    )

    # Track which lines belong to script blocks
    script_line_flags = [False] * len(lines)

    for match in script_pattern.finditer(text):
        attrs = match.group(1) or ""
        if 'lang="ts"' in attrs or "lang='ts'" in attrs or 'lang="tsx"' in attrs:
            effective_lang = "typescript"

        # Find the line range for the script content (excluding the tags)
        block_start = text[:match.start()].count("\n")

        # Find the opening tag end line and closing tag start line
        inner_text = match.group(0)
        opening_tag_end = inner_text.index(">") + 1
        opening_lines = inner_text[:opening_tag_end].count("\n")
        closing_tag_start = inner_text.rfind("</script>")
        closing_lines = inner_text[:closing_tag_start].count("\n")

        # +1 to skip the opening tag line, no +1 on end to exclude </script>
        content_start = block_start + opening_lines + 1
        content_end = block_start + closing_lines

        for i in range(content_start, min(content_end, len(lines))):
            script_line_flags[i] = True

    # Build output: keep script lines, blank out everything else
    output_lines = []
    for i, line in enumerate(lines):
        if script_line_flags[i]:
            output_lines.append(line)
        else:
            output_lines.append("")

    processed = "\n".join(output_lines)
    return processed.encode("utf-8"), effective_lang


def parse_file(path: Path, language: str | None = None):
    """Parse a file with tree-sitter and return (tree, source_bytes, language).

    Returns (None, None, None) if parsing fails.
    Failure categories:
    - no_grammar: language detected but no tree-sitter grammar available (expected skip)
    - parse_error: grammar exists but parsing failed (warning)
    - unreadable: file could not be read (error)
    """
    if language is None:
        language = detect_language(str(path))
    if language is None:
        return None, None, None  # Not a supported extension, expected skip

    source = read_source(path)
    if source is None:
        parse_errors["unreadable"] += 1
        log.warning("Unreadable file: %s", path)
        return None, None, None

    # Regex-only languages: return source without tree-sitter parsing
    if language in REGEX_ONLY_LANGUAGES:
        if str(path).lower().endswith(".scx"):
            source = _pack_scx_sct(path, source)
        return None, source, language

    # Vue/Svelte SFC: extract <script> blocks and route to TS/JS
    if language in ("vue", "svelte"):
        source, language = _preprocess_vue(source)

    # Resolve grammar alias (e.g. apex → java, sfxml → html)
    plugin_alias = _plugin_grammar_aliases().get(language)
    grammar = plugin_alias or GRAMMAR_ALIASES.get(language, language)

    try:
        parser = get_parser(grammar)
    except Exception:
        parse_errors["no_grammar"] += 1
        return None, None, None  # Grammar not available, expected skip

    try:
        tree = parser.parse(source)
    except Exception as e:
        parse_errors["parse_error"] += 1
        log.warning("Parse error in %s: %s", path, e)
        return None, None, None

    return tree, source, language


def extract_vue_template(source: bytes) -> tuple[str, int] | None:
    """Extract the <template> block content from a Vue SFC.

    Returns (template_content, start_line_number) or None if no template found.
    The start_line_number is 1-based.

    Uses depth-counting to handle nested <template #slot> tags correctly.
    """
    text = source.decode("utf-8", errors="replace")

    # Find the outer <template...> open tag (the SFC root template)
    outer_open = re.search(r'<template(\s[^>]*)?>',  text)
    if not outer_open:
        return None

    content_start = outer_open.end()
    depth = 1

    # Scan for all <template...> and </template> tags after the outer open
    # Use non-greedy [^>]*? so that self-closing / isn't consumed by attributes
    tag_re = re.compile(r'<(/?)template\b([^>]*?)(/?)>')
    for m in tag_re.finditer(text, pos=content_start):
        is_closing = m.group(1) == '/'
        is_self_closing = m.group(3) == '/'

        if is_self_closing:
            continue  # <template ... /> doesn't affect depth
        elif is_closing:
            depth -= 1
            if depth == 0:
                # Found the matching close for the outer template
                content = text[content_start:m.start()]
                start_line = text[:content_start].count("\n") + 1
                return content, start_line
        else:
            depth += 1

    # No matching close tag found (malformed)
    return None


def scan_template_references(
    template_content: str,
    start_line: int,
    known_symbols: set[str],
    file_path: str,
) -> list[dict]:
    """Scan a Vue template block for identifiers matching known script symbols.

    Returns a list of reference dicts compatible with the reference pipeline.
    """
    if not template_content or not known_symbols:
        return []

    # Patterns to extract expression strings from template
    # Mustache interpolations: {{ expression }}
    # Attribute bindings: :attr="expression" or v-bind:attr="expression"
    # Directives: v-if="expression", v-for="expr", v-show="expr", etc.
    # Event handlers: @event="handler" or v-on:event="handler"
    expr_patterns = [
        re.compile(r'\{\{(.*?)\}\}', re.DOTALL),           # {{ expr }}
        re.compile(r'(?::|v-bind:)[\w.-]+="([^"]*)"'),     # :attr="expr"
        re.compile(r'v-[\w-]+="([^"]*)"'),                  # v-directive="expr"
        re.compile(r'(?:@|v-on:)[\w.-]+="([^"]*)"'),       # @event="handler"
    ]
    # Identifier pattern
    ident_re = re.compile(r'\b([a-zA-Z_$][a-zA-Z0-9_$]*)\b')

    # Also detect PascalCase component names: <MyComponent> → MyComponent
    component_re = re.compile(r'<([A-Z][a-zA-Z0-9]+)')

    refs = []
    seen = set()

    # Pass 1: Extract identifiers from template expressions on FULL content.
    # This handles multi-line attribute values like :class="cn(\n  isRowFocused(row)\n)"
    # where the opening " and closing " are on different lines.
    for pattern in expr_patterns:
        for match in pattern.finditer(template_content):
            expr = match.group(1)
            # Compute line number from match position
            line_num = start_line + template_content[:match.start()].count("\n")
            for ident_match in ident_re.finditer(expr):
                name = ident_match.group(1)
                if name in known_symbols and name not in seen:
                    seen.add(name)
                    refs.append({
                        "source_name": None,
                        "target_name": name,
                        "kind": "template",
                        "line": line_num,
                        "source_file": file_path,
                    })

    # Pass 2: Detect PascalCase component usage (per-line — tags don't span lines)
    lines = template_content.split("\n")
    for line_offset, line in enumerate(lines):
        line_num = start_line + line_offset
        for match in component_re.finditer(line):
            name = match.group(1)
            if name in known_symbols and name not in seen:
                seen.add(name)
                refs.append({
                    "source_name": None,
                    "target_name": name,
                    "kind": "template",
                    "line": line_num,
                    "source_file": file_path,
                })

    return refs


def get_parse_error_summary() -> str:
    """Return a summary of parse errors for logging."""
    parts = []
    if parse_errors["unreadable"]:
        parts.append(f"{parse_errors['unreadable']} unreadable")
    if parse_errors["parse_error"]:
        parts.append(f"{parse_errors['parse_error']} parse errors")
    if parse_errors["no_grammar"]:
        parts.append(f"{parse_errors['no_grammar']} no grammar")
    return ", ".join(parts) if parts else ""
