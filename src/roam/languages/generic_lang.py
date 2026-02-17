
from __future__ import annotations
from .base import LanguageExtractor


# Common node types across many tree-sitter grammars
_FUNCTION_TYPES = frozenset({
    "function_definition", "function_declaration", "method_definition",
    "method_declaration", "function_item", "fn_item",
    "function", "singleton_method", "method",
})

_CLASS_TYPES = frozenset({
    "class_definition", "class_declaration", "class_specifier",
    "class", "module", "struct_item", "struct_specifier",
})

_INTERFACE_TYPES = frozenset({
    "interface_declaration", "trait_item", "protocol_declaration",
    "trait_declaration",  # PHP traits
})

_ENUM_TYPES = frozenset({
    "enum_declaration", "enum_specifier", "enum_item",
})

_MODULE_TYPES = frozenset({
    "module_definition", "module_declaration", "mod_item",
    "namespace_definition", "package_declaration",
})

# Node types whose children form a class body (used to detect context)
_CLASS_BODY_TYPES = frozenset({
    "declaration_list", "class_body", "block", "body",
    "field_declaration_list", "enum_body",
})

# ---- Inheritance config: extends / superclass ----
# Maps language -> how to find the parent class(es) from a class declaration node.
# parent_type: the child node type that contains the superclass name
# name_child: the child type within parent_type that holds the name (single)
# name_children: list of child types to collect (multiple inheritance)
_EXTENDS_CONFIG = {
    "php": {"parent_type": "base_clause", "name_child": "name"},
    "python": {"parent_type": "argument_list", "name_children": ["identifier"]},
    "java": {"parent_type": "superclass", "name_child": "type_identifier"},
    "typescript": {"parent_type": "extends_clause", "name_child": "identifier"},
    "tsx": {"parent_type": "extends_clause", "name_child": "identifier"},
    "javascript": {"parent_type": "class_heritage", "name_child": "identifier"},
    "go": None,       # handled specially via embedded structs
    "rust": None,     # handled via impl blocks
    "kotlin": {"parent_type": "delegation_specifier", "name_child": "user_type"},
    "ruby": {"parent_type": "superclass", "name_child": "constant"},
}

# ---- Trait / mixin / interface implementation config ----
# node_type: standalone node inside class body (e.g. PHP use_declaration)
# parent_type: child of the class declaration node (e.g. Java super_interfaces)
# name_child / name_children: how to extract names
# context: where the node appears ("class_body" means inside the body)
# trait_field: for Rust impl blocks, the field that holds the trait
_TRAIT_CONFIG = {
    "php": {"node_type": "use_declaration", "name_child": "name", "context": "class_body"},
    "java": {"parent_type": "super_interfaces", "name_children": ["type_identifier"]},
    "typescript": {"parent_type": "implements_clause", "name_children": ["type_identifier"]},
    "tsx": {"parent_type": "implements_clause", "name_children": ["type_identifier"]},
    "rust": {"node_type": "impl_item", "trait_field": "trait"},
}

# ---- Class property / field config ----
# node_type: the AST node type that represents a property/field
# name_field: a tree-sitter field name to get the name node (via child_by_field_name)
# name_child: a child node type to search for (via iteration)
# context: "class_block" means only extract when directly inside a class body
_PROPERTY_CONFIG = {
    "php": {"node_type": "property_declaration", "name_field": "variable_name", "value_field": "property_element"},
    "python": {"node_type": "assignment", "context": "class_block"},
    "java": {"node_type": "field_declaration", "name_child": "variable_declarator"},
    "typescript": {"node_type": "public_field_definition", "name_child": "property_identifier"},
    "tsx": {"node_type": "public_field_definition", "name_child": "property_identifier"},
    "javascript": {"node_type": "field_definition", "name_child": "property_identifier"},
    "go": {"node_type": "field_declaration", "name_child": "field_identifier"},
    "kotlin": {"node_type": "property_declaration", "name_field": "variable_declaration"},
}

# Simple literal node types whose text we extract as default_value
_LITERAL_TYPES = frozenset({
    "string", "encapsed_string", "string_content", "string_literal",
    "interpreted_string_literal", "raw_string_literal",
    "number", "integer", "float", "integer_literal", "float_literal",
    "decimal_integer_literal", "decimal_floating_point_literal",
    "true", "false", "boolean", "null", "nil", "none", "None",
    "number_literal",
})


class GenericExtractor(LanguageExtractor):
    """Fallback extractor that works for any tree-sitter grammar.

    Looks for common node types and extracts symbols by finding the
    first identifier child as the name. Does not do import resolution.
    Used for Ruby, PHP, C#, Kotlin, Swift, Scala, etc.
    """

    def __init__(self, language: str = "unknown"):
        self._language = language

    @property
    def language_name(self) -> str:
        return self._language

    @property
    def file_extensions(self) -> list[str]:
        return []

    def extract_symbols(self, tree, source: bytes, file_path: str) -> list[dict]:
        symbols = []
        self._walk_symbols(tree.root_node, source, symbols, parent_name=None)
        return symbols

    def extract_references(self, tree, source: bytes, file_path: str) -> list[dict]:
        refs = []
        self._walk_refs(tree.root_node, source, refs, scope_name=None)
        return refs

    def get_docstring(self, node, source: bytes) -> str | None:
        prev = node.prev_sibling
        if prev and prev.type in ("comment", "block_comment", "line_comment"):
            text = self.node_text(prev, source).strip()
            # Strip common comment prefixes
            for prefix in ("/**", "/*", "///", "//!", "//", "#"):
                if text.startswith(prefix):
                    text = text[len(prefix):]
                    break
            if text.endswith("*/"):
                text = text[:-2]
            return text.strip() or None
        return None

    def _get_name(self, node, source) -> str | None:
        """Try to extract a name from a node using common field names and patterns."""
        # Try field name first
        name_node = node.child_by_field_name("name")
        if name_node:
            return self.node_text(name_node, source)

        # Try first identifier child
        for child in node.children:
            if child.type in ("identifier", "type_identifier", "constant",
                              "property_identifier", "field_identifier"):
                return self.node_text(child, source)

        return None

    # ---- Symbol extraction ----

    def _find_class_body(self, node):
        """Find the body node of a class-like declaration."""
        body = node.child_by_field_name("body")
        if body:
            return body
        for sub in node.children:
            if sub.type in _CLASS_BODY_TYPES:
                return sub
        return None

    def _walk_symbols(self, node, source, symbols, parent_name, _depth=0):
        if _depth > 50:
            return
        for child in node.children:
            kind = self._classify_node(child)
            if kind:
                name = self._get_name(child, source)
                if name:
                    qualified = f"{parent_name}.{name}" if parent_name else name
                    sig = self.get_signature(child, source)
                    symbols.append(self._make_symbol(
                        name=name, kind=kind,
                        line_start=child.start_point[0] + 1,
                        line_end=child.end_point[0] + 1,
                        qualified_name=qualified, signature=sig,
                        docstring=self.get_docstring(child, source),
                        parent_name=parent_name,
                    ))
                    if kind in ("class", "interface", "module", "struct", "enum"):
                        body = self._find_class_body(child) or child
                        self._extract_properties(body, source, symbols, qualified)
                        self._walk_symbols(body, source, symbols, qualified, _depth + 1)
                    continue
            self._walk_symbols(child, source, symbols, parent_name, _depth + 1)

    def _classify_node(self, node) -> str | None:
        """Map a node type to a symbol kind."""
        ntype = node.type
        if ntype in _FUNCTION_TYPES:
            return "function"
        if ntype in _CLASS_TYPES:
            return "class"
        if ntype in _INTERFACE_TYPES:
            return "interface"
        if ntype in _ENUM_TYPES:
            return "enum"
        if ntype in _MODULE_TYPES:
            return "module"
        return None

    # ---- Property / field extraction ----

    def _extract_properties(self, body_node, source, symbols, class_name):
        """Extract class properties/fields from a class body node.

        Only extracts direct children of the body — does not recurse into
        methods or nested classes.
        """
        config = _PROPERTY_CONFIG.get(self._language)
        if config is None:
            return

        node_type = config.get("node_type")
        if node_type is None:
            return

        for child in body_node.children:
            if child.type != node_type:
                continue

            # Python: only extract class-level assignments (context check)
            if config.get("context") == "class_block":
                # The parent of the assignment should be the class body
                # (not a function body). We already know we're iterating
                # direct children of body_node, so this is satisfied.
                name = self._get_property_name_python(child, source)
                if name is None:
                    continue
                value = self._get_property_value_python(child, source)
                visibility = "public"
                if name.startswith("__") and not name.endswith("__"):
                    visibility = "private"
                elif name.startswith("_"):
                    visibility = "protected"
            else:
                name = self._get_property_name(child, source, config)
                if name is None:
                    continue
                value = self._get_property_value(child, source, config)
                visibility = self._get_property_visibility(child, source)

            qualified = f"{class_name}.{name}" if class_name else name
            symbols.append(self._make_symbol(
                name=name,
                kind="property",
                line_start=child.start_point[0] + 1,
                line_end=child.end_point[0] + 1,
                qualified_name=qualified,
                signature=None,
                docstring=self.get_docstring(child, source),
                parent_name=class_name,
                visibility=visibility,
                default_value=value,
            ))

    def _find_name_by_field(self, node, source, name_field) -> str | None:
        """Search for property name via field name (2-level deep search)."""
        name_node = node.child_by_field_name(name_field)
        if name_node is None:
            for child in node.children:
                if child.type == name_field:
                    name_node = child
                    break
                for sub in child.children:
                    if sub.type == name_field:
                        name_node = sub
                        break
                if name_node is not None:
                    break
        if name_node:
            text = self.node_text(name_node, source)
            if text.startswith("$"):
                text = text[1:]
            return text if text else None
        return None

    def _find_name_by_child_type(self, node, source, name_child_type) -> str | None:
        """Search for property name via child node type."""
        for child in node.children:
            if child.type == name_child_type:
                inner_name = child.child_by_field_name("name")
                if inner_name:
                    return self.node_text(inner_name, source)
                for sub in child.children:
                    if sub.type in ("identifier", "field_identifier",
                                    "property_identifier", "variable_name"):
                        text = self.node_text(sub, source)
                        if text.startswith("$"):
                            text = text[1:]
                        return text if text else None
                text = self.node_text(child, source)
                return text if text else None
        return None

    def _get_property_name(self, node, source, config) -> str | None:
        """Extract property name using config-driven strategy."""
        name_field = config.get("name_field")
        if name_field:
            result = self._find_name_by_field(node, source, name_field)
            if result is not None:
                return result

        name_child_type = config.get("name_child")
        if name_child_type:
            return self._find_name_by_child_type(node, source, name_child_type)

        return None

    def _get_property_name_python(self, node, source) -> str | None:
        """Extract the left-hand side name from a Python assignment node."""
        # Python assignment: left = identifier (the "left" field)
        left = node.child_by_field_name("left")
        if left:
            if left.type == "identifier":
                return self.node_text(left, source)
            # Could be attribute like self.x — skip those (instance attrs, not class props)
            return None

        # Fallback: first identifier child
        for child in node.children:
            if child.type == "identifier":
                return self.node_text(child, source)
        return None

    def _get_property_value(self, node, source, config) -> str | None:
        """Extract default value from a property/field node."""
        # Walk all descendants looking for a literal value
        return self._find_literal_value(node, source, max_depth=4)

    def _get_property_value_python(self, node, source) -> str | None:
        """Extract value from a Python assignment's right-hand side."""
        right = node.child_by_field_name("right")
        if right:
            return self._extract_literal(right, source)
        return None

    def _find_literal_value(self, node, source, max_depth=4) -> str | None:
        """Recursively look for a simple literal value in a node tree."""
        if max_depth <= 0:
            return None

        result = self._extract_literal(node, source)
        if result is not None:
            return result

        for child in node.children:
            result = self._find_literal_value(child, source, max_depth - 1)
            if result is not None:
                return result
        return None

    def _extract_literal(self, node, source) -> str | None:
        """Extract value if the node is a simple literal type."""
        if node.type in _LITERAL_TYPES:
            text = self.node_text(node, source).strip()
            # Limit length for sanity
            if len(text) <= 200:
                return text
        # Handle quoted strings that tree-sitter wraps
        if node.type in ("string", "encapsed_string", "string_literal",
                         "interpreted_string_literal", "raw_string_literal"):
            # Get the content child if it exists
            for child in node.children:
                if child.type == "string_content":
                    text = self.node_text(child, source).strip()
                    if len(text) <= 200:
                        return text
            # Fall back to full string text
            text = self.node_text(node, source).strip()
            if len(text) <= 200:
                return text
        return None

    def _get_property_visibility(self, node, source) -> str:
        """Detect visibility from modifier children."""
        for child in node.children:
            if child.type in ("visibility_modifier", "accessibility_modifier", "modifiers"):
                text = self.node_text(child, source).lower()
                if "private" in text:
                    return "private"
                if "protected" in text:
                    return "protected"
                if "public" in text:
                    return "public"
            # Java/C# style: direct modifier nodes
            if child.type in ("private", "protected", "public"):
                return child.type
        return "public"

    # ---- Reference extraction (calls + inheritance) ----

    def _walk_refs(self, node, source, refs, scope_name):
        for child in node.children:
            if child.type in ("call_expression", "call", "method_invocation"):
                func = child.child_by_field_name("function") or child.child_by_field_name("method")
                if func is None:
                    # Try first child
                    for sub in child.children:
                        if sub.type in ("identifier", "member_expression", "attribute",
                                        "scoped_identifier", "field_expression"):
                            func = sub
                            break
                if func:
                    name = self.node_text(func, source)
                    refs.append(self._make_reference(
                        target_name=name,
                        kind="call",
                        line=child.start_point[0] + 1,
                        source_name=scope_name,
                    ))
            else:
                new_scope = scope_name
                kind = self._classify_node(child)
                if kind:
                    n = self._get_name(child, source)
                    if n:
                        new_scope = f"{scope_name}.{n}" if scope_name else n

                        # Extract inheritance references for class-like nodes
                        if kind in ("class", "interface", "struct"):
                            self._extract_inheritance_refs(child, source, refs, new_scope)

                self._walk_refs(child, source, refs, new_scope)

        # Handle Rust impl blocks at top level
        if self._language == "rust":
            self._extract_rust_impl_refs(node, source, refs)

    def _extract_inheritance_refs(self, class_node, source, refs, class_name):
        """Extract extends/implements/trait references from a class declaration."""
        lang = self._language

        # ---- Extends ----
        extends_cfg = _EXTENDS_CONFIG.get(lang)
        if extends_cfg is not None:
            self._extract_extends_refs(class_node, source, refs, class_name, extends_cfg)

        # ---- Traits / implements ----
        trait_cfg = _TRAIT_CONFIG.get(lang)
        if trait_cfg is not None:
            self._extract_trait_refs(class_node, source, refs, class_name, trait_cfg)

        # ---- Go embedded structs ----
        if lang == "go":
            self._extract_go_embedded_refs(class_node, source, refs, class_name)

    def _extract_extends_refs(self, class_node, source, refs, class_name, cfg):
        """Extract 'inherits' references from extends/superclass clauses."""
        parent_type = cfg.get("parent_type")
        if not parent_type:
            return

        # Search class_node children for the parent_type node
        for child in class_node.children:
            if child.type == parent_type:
                # Multiple inheritance (e.g. Python argument_list)
                name_children_types = cfg.get("name_children")
                if name_children_types:
                    for sub in child.children:
                        if sub.type in name_children_types:
                            target = self.node_text(sub, source)
                            if target:
                                refs.append(self._make_reference(
                                    target_name=target,
                                    kind="inherits",
                                    line=class_node.start_point[0] + 1,
                                    source_name=class_name,
                                ))
                else:
                    # Single inheritance
                    name_child_type = cfg.get("name_child")
                    target = self._find_child_text(child, source, name_child_type)
                    if target:
                        refs.append(self._make_reference(
                            target_name=target,
                            kind="inherits",
                            line=class_node.start_point[0] + 1,
                            source_name=class_name,
                        ))

    def _trait_refs_from_body(self, class_node, source, refs, class_name, cfg):
        """Pattern A: Extract uses_trait refs from node_type inside class body."""
        node_type = cfg["node_type"]
        body = class_node.child_by_field_name("body")
        body_nodes = [body] if body else []
        if not body_nodes:
            for child in class_node.children:
                if child.type in _CLASS_BODY_TYPES:
                    body_nodes.append(child)
        name_child_type = cfg.get("name_child")
        for body_node in body_nodes:
            for child in body_node.children:
                if child.type == node_type:
                    target = self._find_child_text(child, source, name_child_type)
                    if target:
                        refs.append(self._make_reference(
                            target_name=target, kind="uses_trait",
                            line=child.start_point[0] + 1, source_name=class_name,
                        ))

    def _trait_refs_from_parent(self, class_node, source, refs, class_name, cfg):
        """Pattern B: Extract implements refs from parent_type child of class."""
        parent_type = cfg["parent_type"]
        for child in class_node.children:
            if child.type == parent_type:
                name_children_types = cfg.get("name_children")
                if name_children_types:
                    for sub in self._iter_all_descendants(child):
                        if sub.type in name_children_types:
                            target = self.node_text(sub, source)
                            if target:
                                refs.append(self._make_reference(
                                    target_name=target, kind="implements",
                                    line=class_node.start_point[0] + 1,
                                    source_name=class_name,
                                ))
                else:
                    name_child_type = cfg.get("name_child")
                    target = self._find_child_text(child, source, name_child_type)
                    if target:
                        refs.append(self._make_reference(
                            target_name=target, kind="implements",
                            line=class_node.start_point[0] + 1,
                            source_name=class_name,
                        ))

    def _extract_trait_refs(self, class_node, source, refs, class_name, cfg):
        """Extract 'implements' or 'uses_trait' references."""
        node_type = cfg.get("node_type")
        if node_type and cfg.get("context") == "class_body":
            self._trait_refs_from_body(class_node, source, refs, class_name, cfg)
            return
        if cfg.get("parent_type"):
            self._trait_refs_from_parent(class_node, source, refs, class_name, cfg)

    def _extract_go_embedded_refs(self, class_node, source, refs, class_name):
        """Extract Go embedded struct references (anonymous fields)."""
        # Go struct: field_declaration with type_identifier but no field_identifier = embedded
        for child in class_node.children:
            if child.type in _CLASS_BODY_TYPES:
                for field in child.children:
                    if field.type == "field_declaration":
                        has_name = False
                        type_name = None
                        for sub in field.children:
                            if sub.type == "field_identifier":
                                has_name = True
                            if sub.type == "type_identifier":
                                type_name = self.node_text(sub, source)
                        # No field_identifier means it's an embedded type
                        if not has_name and type_name:
                            refs.append(self._make_reference(
                                target_name=type_name,
                                kind="inherits",
                                line=field.start_point[0] + 1,
                                source_name=class_name,
                            ))

    def _extract_rust_impl_refs(self, node, source, refs):
        """Extract Rust impl Trait for Type references."""
        for child in node.children:
            if child.type == "impl_item":
                # Get the type being implemented for
                type_node = child.child_by_field_name("type")
                type_name = self.node_text(type_node, source) if type_node else None

                # Get the trait being implemented (if any)
                trait_node = child.child_by_field_name("trait")
                if trait_node and type_name:
                    trait_name = self.node_text(trait_node, source)
                    if trait_name:
                        refs.append(self._make_reference(
                            target_name=trait_name,
                            kind="implements",
                            line=child.start_point[0] + 1,
                            source_name=type_name,
                        ))

    def _find_child_text(self, node, source, child_type) -> str | None:
        """Find first child of given type and return its text."""
        if child_type is None:
            return None
        for child in node.children:
            if child.type == child_type:
                return self.node_text(child, source)
        # Search one level deeper for nested structures
        for child in node.children:
            for sub in child.children:
                if sub.type == child_type:
                    return self.node_text(sub, source)
        return None

    def _iter_all_descendants(self, node):
        """Yield all descendant nodes (depth-first)."""
        for child in node.children:
            yield child
            yield from self._iter_all_descendants(child)
