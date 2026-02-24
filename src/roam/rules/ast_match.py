"""AST pattern matching with `$METAVAR` placeholders.

Pattern syntax:
- Use a single language snippet (expression or statement).
- Replace variable parts with `$NAME` metavariables.
- Repeated metavariables must match the same captured subtree.

Example:
    pattern: "same($X, $X)"
    matches: same(a, a)
    rejects: same(a, b)
"""

from __future__ import annotations

from dataclasses import dataclass
import re

from tree_sitter_language_pack import get_parser

from roam.index.parser import GRAMMAR_ALIASES


_METAVAR_RE = re.compile(r"\$([A-Za-z_][A-Za-z0-9_]*)")
_WHITESPACE_RE = re.compile(r"\s+")
_CONTAINER_NODE_TYPES = {
    "module",
    "program",
    "source_file",
    "translation_unit",
    "document",
    "script",
}
_STATEMENT_WRAPPERS = {
    "expression_statement",
    "statement",
}
_LANG_ALIASES = {
    "py": "python",
    "python": "python",
    "js": "javascript",
    "javascript": "javascript",
    "ts": "typescript",
    "typescript": "typescript",
    "tsx": "tsx",
    "c#": "c_sharp",
    "cs": "c_sharp",
    "csharp": "c_sharp",
    "c_sharp": "c_sharp",
}


@dataclass(frozen=True)
class CompiledAstPattern:
    """A compiled AST pattern for a single language."""

    language: str
    pattern: str
    pattern_source: bytes
    pattern_root: object
    placeholder_map: dict[str, str]


def normalize_language_name(language: str | None) -> str | None:
    """Normalize user-facing language aliases to parser language names."""
    if language is None:
        return None
    key = language.strip().lower()
    if not key:
        return None
    return _LANG_ALIASES.get(key, key)


def _normalize_text(text: str) -> str:
    return _WHITESPACE_RE.sub(" ", text).strip()


def _node_text(node, source: bytes) -> str:
    return source[node.start_byte:node.end_byte].decode("utf-8", errors="replace")


def _rewrite_metavars(pattern: str) -> tuple[str, dict[str, str]]:
    """Rewrite `$NAME` metavars into parser-safe sentinel identifiers."""

    placeholder_map: dict[str, str] = {}

    def _repl(match: re.Match[str]) -> str:
        name = match.group(1)
        token = f"ROAM_META_{name}_TOKEN"
        placeholder_map[token] = name
        return token

    rewritten = _METAVAR_RE.sub(_repl, pattern)
    return rewritten, placeholder_map


def _meaningful_named_children(node) -> list:
    return [child for child in node.named_children if child.type != "comment"]


def _extract_pattern_root(root_node):
    """Extract a single meaningful pattern root node from a parse tree."""
    node = root_node

    while True:
        children = _meaningful_named_children(node)
        if not children:
            return node if node.is_named else None

        if node.type in _CONTAINER_NODE_TYPES:
            if len(children) != 1:
                return None
            node = children[0]
            continue

        if node.type in _STATEMENT_WRAPPERS and len(children) == 1:
            node = children[0]
            continue

        return node


def compile_ast_pattern(pattern: str, language: str) -> CompiledAstPattern:
    """Compile an AST pattern for the target language."""
    if not pattern or not pattern.strip():
        raise ValueError("AST pattern is empty")

    normalized_lang = normalize_language_name(language)
    if not normalized_lang:
        raise ValueError("AST pattern language is required")

    grammar = GRAMMAR_ALIASES.get(normalized_lang, normalized_lang)
    parser = get_parser(grammar)

    rewritten, placeholder_map = _rewrite_metavars(pattern)
    source = rewritten.encode("utf-8")
    tree = parser.parse(source)
    pattern_root = _extract_pattern_root(tree.root_node)
    if pattern_root is None:
        raise ValueError("Pattern must contain exactly one AST construct")
    if pattern_root.type == "ERROR":
        raise ValueError("Pattern could not be parsed for this language")

    return CompiledAstPattern(
        language=normalized_lang,
        pattern=pattern,
        pattern_source=source,
        pattern_root=pattern_root,
        placeholder_map=placeholder_map,
    )


def _pattern_metavar_name(pattern_node, compiled: CompiledAstPattern) -> str | None:
    token = _node_text(pattern_node, compiled.pattern_source)
    return compiled.placeholder_map.get(token)


def _match_nodes(pattern_node, code_node, compiled: CompiledAstPattern, code_source: bytes, captures: dict) -> bool:
    """Recursively match a code AST node against a pattern AST node."""
    metavar = _pattern_metavar_name(pattern_node, compiled)
    if metavar is not None:
        captured_text = _node_text(code_node, code_source)
        normalized = _normalize_text(captured_text)
        existing = captures.get(metavar)
        if existing is not None and existing["_normalized"] != normalized:
            return False
        captures[metavar] = {
            "text": captured_text,
            "line": code_node.start_point[0] + 1,
            "_normalized": normalized,
        }
        return True

    if pattern_node.type != code_node.type:
        return False

    pattern_children = _meaningful_named_children(pattern_node)
    code_children = _meaningful_named_children(code_node)

    if len(pattern_children) != len(code_children):
        return False

    if not pattern_children:
        pattern_text = _normalize_text(_node_text(pattern_node, compiled.pattern_source))
        code_text = _normalize_text(_node_text(code_node, code_source))
        return pattern_text == code_text

    for p_child, c_child in zip(pattern_children, code_children):
        if not _match_nodes(p_child, c_child, compiled, code_source, captures):
            return False
    return True


def _walk_named_nodes(root_node):
    stack = [root_node]
    while stack:
        node = stack.pop()
        if node.is_named:
            yield node
            children = _meaningful_named_children(node)
            for child in reversed(children):
                stack.append(child)


def find_ast_matches(
    tree,
    code_source: bytes,
    compiled: CompiledAstPattern,
    *,
    max_matches: int = 0,
) -> list[dict]:
    """Find all AST matches for a compiled pattern in a parsed tree."""
    if tree is None or not code_source:
        return []

    results: list[dict] = []
    root_is_metavar = _pattern_metavar_name(compiled.pattern_root, compiled) is not None

    for node in _walk_named_nodes(tree.root_node):
        if not root_is_metavar and node.type != compiled.pattern_root.type:
            continue

        captures: dict = {}
        if not _match_nodes(compiled.pattern_root, node, compiled, code_source, captures):
            continue

        visible_captures = {
            name: data["text"] for name, data in captures.items()
        }
        results.append({
            "line": node.start_point[0] + 1,
            "snippet": _node_text(node, code_source),
            "captures": visible_captures,
        })

        if max_matches > 0 and len(results) >= max_matches:
            break

    return results
