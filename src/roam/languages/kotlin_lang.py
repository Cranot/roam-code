from __future__ import annotations

import re

from .generic_lang import GenericExtractor

# Belt-and-suspenders fallbacks for top-level Kotlin declarations the
# tree-sitter grammar may emit differently across versions. Linux CI's
# tree-sitter-language-pack 1.6.x wheels produce node shapes that bypass
# both ``object_declaration`` matching and ``enum_class_body`` child
# detection. Each regex is line-anchored so we don't trip on
# ``val x = object : T`` (object expression) or similar non-decl uses.
_KOTLIN_MOD = (
    r"(?:public\s+|private\s+|internal\s+|protected\s+)?"
    r"(?:abstract\s+|open\s+|sealed\s+|final\s+|data\s+|inner\s+|companion\s+)*"
)
_OBJECT_DECL_RE = re.compile(
    r"^[\t ]*" + _KOTLIN_MOD + r"object\s+([A-Za-z_][A-Za-z0-9_]*)\s*[:({\n]",
    re.MULTILINE,
)
_ENUM_DECL_RE = re.compile(
    r"^[\t ]*" + _KOTLIN_MOD + r"enum\s+class\s+([A-Za-z_][A-Za-z0-9_]*)\s*[<({:\s]",
    re.MULTILINE,
)
_INTERFACE_DECL_RE = re.compile(
    r"^[\t ]*" + _KOTLIN_MOD + r"(?:fun\s+)?interface\s+([A-Za-z_][A-Za-z0-9_]*)\s*[<({:\s]",
    re.MULTILINE,
)
_CLASS_DECL_RE = re.compile(
    r"^[\t ]*" + _KOTLIN_MOD + r"class\s+([A-Za-z_][A-Za-z0-9_]*)\s*[<({:\s\n]",
    re.MULTILINE,
)


class KotlinExtractor(GenericExtractor):
    """Kotlin dedicated extractor (Tier 1)."""

    def __init__(self):
        super().__init__(language="kotlin")

    @property
    def language_name(self) -> str:
        return "kotlin"

    @property
    def file_extensions(self) -> list[str]:
        return [".kt", ".kts"]

    def extract_symbols(self, tree, source: bytes, file_path: str) -> list[dict]:
        """Run the AST extraction, then add any top-level decls the AST
        shape didn't surface (grammar drift across tree-sitter wheel
        versions). We promote any *existing* fallback symbol's kind too,
        so an ``enum class Color`` mis-classified as a plain ``class``
        gets corrected to ``enum``.
        """
        symbols = super().extract_symbols(tree, source, file_path)
        try:
            text = source.decode("utf-8", errors="replace")
        except Exception:
            return symbols

        by_name = {s.get("name"): s for s in symbols if s.get("name")}

        def _promote_or_add(name: str, kind: str, signature: str, line: int):
            existing = by_name.get(name)
            if existing is not None:
                # Promote kind for grammar-misclassified declarations.
                if existing.get("kind") == "class" and kind in ("enum", "interface"):
                    existing["kind"] = kind
                    if signature and not existing.get("signature"):
                        existing["signature"] = signature
                return
            sym = self._make_symbol(
                name=name,
                kind=kind,
                line_start=line,
                line_end=line,
                qualified_name=name,
                signature=signature,
            )
            symbols.append(sym)
            by_name[name] = sym

        # Order matters: enum and interface are *promotions* over plain
        # ``class`` matches, so process them first.
        for match in _ENUM_DECL_RE.finditer(text):
            line = text.count("\n", 0, match.start()) + 1
            _promote_or_add(match.group(1), "enum", f"enum class {match.group(1)}", line)
        for match in _INTERFACE_DECL_RE.finditer(text):
            line = text.count("\n", 0, match.start()) + 1
            _promote_or_add(match.group(1), "interface", f"interface {match.group(1)}", line)
        for match in _OBJECT_DECL_RE.finditer(text):
            line = text.count("\n", 0, match.start()) + 1
            _promote_or_add(match.group(1), "class", f"object {match.group(1)}", line)
        for match in _CLASS_DECL_RE.finditer(text):
            line = text.count("\n", 0, match.start()) + 1
            _promote_or_add(match.group(1), "class", f"class {match.group(1)}", line)
        return symbols

    def _classify_node(self, node) -> str | None:
        # The Kotlin tree-sitter grammar shape varies across versions of
        # ``tree-sitter-language-pack``. Older versions emit
        # ``object_declaration`` for ``object Foo``; newer versions
        # collapse it under ``class_declaration`` with the first non-named
        # child being the literal ``object`` token. We accept both so the
        # extractor doesn't silently drop ``object`` symbols on either CI
        # toolchain version.
        if node.type in ("object_declaration", "object_literal"):
            return "class"
        if node.type == "class_declaration":
            token_types = {c.type for c in node.children if not c.is_named}
            if "interface" in token_types:
                return "interface"
            if "enum" in token_types or any(c.type == "enum_class_body" for c in node.children if c.is_named):
                return "enum"
            # Newer grammar variant: `object Foo` → class_declaration with
            # a leading ``object`` token.
            if "object" in token_types:
                return "class"
            return "class"
        if node.type == "function_declaration":
            return "method" if self._in_type_context(node) else "function"
        return super()._classify_node(node)

    def _get_name(self, node, source) -> str | None:
        name = super()._get_name(node, source)
        if name:
            return name
        for child in node.children:
            if child.type in ("simple_identifier", "identifier", "type_identifier"):
                text = self.node_text(child, source).strip()
                if text:
                    return text
        return None

    def _in_type_context(self, node) -> bool:
        cur = node.parent
        while cur is not None:
            if cur.type in ("class_body", "enum_class_body"):
                return True
            cur = cur.parent
        return False

    def _extract_properties(self, body_node, source, symbols, class_name):
        """Extract body properties and constructor-bound properties (val/var)."""
        super()._extract_properties(body_node, source, symbols, class_name)

        class_node = body_node.parent if body_node is not None else None
        if class_node is None or class_node.type != "class_declaration":
            return

        for child in class_node.children:
            if child.type != "primary_constructor":
                continue
            for param in child.children:
                if param.type != "class_parameter":
                    continue
                binding = None
                name = None
                visibility = "public"
                for sub in param.children:
                    if not sub.is_named:
                        continue
                    if sub.type == "modifiers":
                        mods = self.node_text(sub, source).lower()
                        if "private" in mods:
                            visibility = "private"
                        elif "protected" in mods:
                            visibility = "protected"
                        elif "internal" in mods:
                            visibility = "internal"
                        elif "public" in mods:
                            visibility = "public"
                    if sub.type == "binding_pattern_kind":
                        binding = self.node_text(sub, source).strip()
                    elif sub.type in ("simple_identifier", "identifier"):
                        name = self.node_text(sub, source).strip()
                if binding not in ("val", "var") or not name:
                    continue
                qualified = f"{class_name}.{name}" if class_name else name
                symbols.append(
                    self._make_symbol(
                        name=name,
                        kind="property",
                        line_start=param.start_point[0] + 1,
                        line_end=param.end_point[0] + 1,
                        qualified_name=qualified,
                        signature=f"{binding} {name}",
                        parent_name=class_name,
                        visibility=visibility,
                        is_exported=visibility == "public",
                    )
                )

    def _extract_inheritance_refs(self, class_node, source, refs, class_name):
        """Kotlin delegation_specifier: first is superclass, rest are interfaces."""
        if class_node.type not in ("class_declaration", "object_declaration"):
            return super()._extract_inheritance_refs(class_node, source, refs, class_name)

        kind = self._classify_node(class_node) or "class"
        specs = [c for c in class_node.children if c.type == "delegation_specifier"]
        for i, spec in enumerate(specs):
            target = self._kotlin_delegation_target(spec, source)
            if not target:
                continue

            if kind == "class":
                has_ctor = any(c.type == "constructor_invocation" for c in self._iter_all_descendants(spec))
                ref_kind = "inherits" if has_ctor else "implements"
            elif kind == "interface":
                ref_kind = "inherits"
            else:
                ref_kind = "inherits"

            refs.append(
                self._make_reference(
                    target_name=target,
                    kind=ref_kind,
                    line=class_node.start_point[0] + 1,
                    source_name=class_name,
                )
            )

    def _kotlin_delegation_target(self, spec_node, source: bytes) -> str | None:
        for child in self._iter_all_descendants(spec_node):
            if child.type in ("type_identifier", "simple_identifier"):
                text = self.node_text(child, source).strip()
                if text:
                    return text

        text = self.node_text(spec_node, source).strip()
        if not text:
            return None
        if " by " in text:
            text = text.split(" by ", 1)[0].strip()
        if "(" in text:
            text = text.split("(", 1)[0].strip()
        return text or None
