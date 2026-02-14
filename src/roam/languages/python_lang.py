
from __future__ import annotations
from .base import LanguageExtractor

# Builtin type names that don't create real reference edges
_BUILTIN_TYPES = frozenset({
    "int", "str", "float", "bool", "bytes", "None",
    "list", "dict", "set", "tuple", "type", "object",
})


class PythonExtractor(LanguageExtractor):
    """Full Python symbol and reference extractor."""

    @property
    def language_name(self) -> str:
        return "python"

    @property
    def file_extensions(self) -> list[str]:
        return [".py", ".pyi"]

    def extract_symbols(self, tree, source: bytes, file_path: str) -> list[dict]:
        symbols = []
        self._pending_inherits = []
        dunder_all = self._find_dunder_all(tree.root_node, source)
        self._walk_node(tree.root_node, source, file_path, symbols, parent_name=None, dunder_all=dunder_all)
        return symbols

    def extract_references(self, tree, source: bytes, file_path: str) -> list[dict]:
        refs = []
        self._walk_refs(tree.root_node, source, file_path, refs, scope_name=None)
        # Add inheritance references collected during symbol extraction
        for info in getattr(self, "_pending_inherits", []):
            refs.append(self._make_reference(
                target_name=info["base_name"],
                kind="inherits",
                line=info["line"],
                source_name=info["class_name"],
            ))
        return refs

    def get_docstring(self, node, source: bytes) -> str | None:
        """Python docstring: first string child in body block."""
        body = node.child_by_field_name("body")
        if body is None:
            return None
        for child in body.children:
            if child.type == "expression_statement":
                for sub in child.children:
                    if sub.type == "string":
                        return self._extract_string_content(sub, source)
                break
            elif child.type == "string":
                return self._extract_string_content(child, source)
            elif child.type == "comment":
                continue
            else:
                break
        return None

    def _extract_string_content(self, string_node, source: bytes) -> str | None:
        """Extract content from a string node, handling both old and new grammar formats."""
        # New grammar: string has string_start, string_content, string_end children
        for child in string_node.children:
            if child.type == "string_content":
                return self.node_text(child, source).strip()
        # Fallback: use full text and strip quotes
        text = self.node_text(string_node, source)
        for q in ('"""', "'''", '"', "'"):
            if text.startswith(q) and text.endswith(q):
                return text[len(q):-len(q)].strip()
        return text

    def _find_dunder_all(self, root, source: bytes) -> set[str] | None:
        """Parse __all__ = [...] to determine explicit exports."""
        for child in root.children:
            if child.type == "assignment":
                left = child.child_by_field_name("left")
                right = child.child_by_field_name("right")
                if left and self.node_text(left, source) == "__all__" and right:
                    return self._parse_all_list(right, source)
            elif child.type == "expression_statement":
                for sub in child.children:
                    if sub.type == "assignment":
                        left = sub.child_by_field_name("left")
                        right = sub.child_by_field_name("right")
                        if left and self.node_text(left, source) == "__all__" and right:
                            return self._parse_all_list(right, source)
        return None

    def _parse_all_list(self, node, source: bytes) -> set[str]:
        names = set()
        if node.type == "list":
            for child in node.children:
                if child.type == "string":
                    content = self._extract_string_content(child, source)
                    if content:
                        names.add(content)
        return names

    def _get_decorators(self, node, source: bytes) -> list[str]:
        decorators = []
        for child in node.children:
            if child.type == "decorator":
                decorators.append(self.node_text(child, source))
        return decorators

    def _visibility(self, name: str) -> str:
        if name.startswith("__") and not name.endswith("__"):
            return "private"
        if name.startswith("_"):
            return "private"
        return "public"

    def _walk_node(self, node, source, file_path, symbols, parent_name, dunder_all):
        for child in node.children:
            if child.type == "function_definition":
                self._extract_function(child, source, symbols, parent_name, dunder_all)
            elif child.type == "class_definition":
                self._extract_class(child, source, file_path, symbols, parent_name, dunder_all)
            elif child.type == "decorated_definition":
                # The actual definition is a child of the decorated_definition
                for sub in child.children:
                    if sub.type == "function_definition":
                        self._extract_function(sub, source, symbols, parent_name, dunder_all, decorator_node=child)
                    elif sub.type == "class_definition":
                        self._extract_class(sub, source, file_path, symbols, parent_name, dunder_all, decorator_node=child)
            elif child.type == "assignment":
                if parent_name is None:
                    # Module-level assignments
                    self._extract_assignment(child, source, symbols, dunder_all)
                else:
                    # Class-level assignments (properties)
                    self._extract_class_property(child, source, symbols, parent_name)
            elif child.type == "expression_statement":
                for sub in child.children:
                    if sub.type == "assignment":
                        if parent_name is None:
                            self._extract_assignment(sub, source, symbols, dunder_all)
                        else:
                            self._extract_class_property(sub, source, symbols, parent_name)

    def _extract_function(self, node, source, symbols, parent_name, dunder_all, decorator_node=None):
        name_node = node.child_by_field_name("name")
        if name_node is None:
            return
        name = self.node_text(name_node, source)
        params = node.child_by_field_name("parameters")
        sig = f"def {name}({self._params_text(params, source)})"
        ret = node.child_by_field_name("return_type")
        if ret:
            sig += f" -> {self.node_text(ret, source)}"

        decorators = self._get_decorators(decorator_node or node, source)
        if decorators:
            sig = "\n".join(decorators) + "\n" + sig

        kind = "method" if parent_name else "function"
        qualified = f"{parent_name}.{name}" if parent_name else name
        vis = self._visibility(name)
        is_exported = self._is_exported(name, dunder_all)

        # Use decorator_node's range if present (includes decorators)
        outer = decorator_node or node
        symbols.append(self._make_symbol(
            name=name,
            kind=kind,
            line_start=outer.start_point[0] + 1,
            line_end=node.end_point[0] + 1,
            qualified_name=qualified,
            signature=sig,
            docstring=self.get_docstring(node, source),
            visibility=vis,
            is_exported=is_exported,
            parent_name=parent_name,
        ))

        # Extract instance attributes from __init__ body (self.x = ...)
        if name == "__init__" and parent_name:
            body = node.child_by_field_name("body")
            if body:
                self_name = self._detect_self_name(node)
                self._extract_init_attributes(
                    body, source, symbols, parent_name, dunder_all, self_name,
                )

    def _extract_class(self, node, source, file_path, symbols, parent_name, dunder_all, decorator_node=None):
        name_node = node.child_by_field_name("name")
        if name_node is None:
            return
        name = self.node_text(name_node, source)

        # Build signature with bases
        bases = node.child_by_field_name("superclasses")
        sig = f"class {name}"
        if bases:
            bases_text = self.node_text(bases, source)
            # argument_list already includes parens
            if bases_text.startswith("("):
                sig += bases_text
            else:
                sig += f"({bases_text})"

        decorators = self._get_decorators(decorator_node or node, source)
        if decorators:
            sig = "\n".join(decorators) + "\n" + sig

        qualified = f"{parent_name}.{name}" if parent_name else name
        vis = self._visibility(name)
        is_exported = self._is_exported(name, dunder_all)

        outer = decorator_node or node
        symbols.append(self._make_symbol(
            name=name,
            kind="class",
            line_start=outer.start_point[0] + 1,
            line_end=node.end_point[0] + 1,
            qualified_name=qualified,
            signature=sig,
            docstring=self.get_docstring(node, source),
            visibility=vis,
            is_exported=is_exported,
            parent_name=parent_name,
        ))

        # Extract base class names for inheritance tracking
        bases_node = node.child_by_field_name("superclasses")
        if bases_node:
            for child in bases_node.children:
                if child.type == "identifier":
                    base_name = self.node_text(child, source)
                    if base_name:
                        self._pending_inherits.append({
                            "class_name": qualified,
                            "base_name": base_name,
                            "line": node.start_point[0] + 1,
                        })
                elif child.type == "attribute":
                    base_name = self.node_text(child, source)
                    if base_name:
                        # Use just the last part for matching (e.g. "enum.Enum" -> "Enum")
                        short_name = base_name.split(".")[-1]
                        self._pending_inherits.append({
                            "class_name": qualified,
                            "base_name": short_name,
                            "line": node.start_point[0] + 1,
                        })

        # Walk class body for methods and nested classes
        body = node.child_by_field_name("body")
        if body:
            self._walk_node(body, source, file_path, symbols, parent_name=qualified, dunder_all=dunder_all)

    def _extract_assignment(self, node, source, symbols, dunder_all):
        left = node.child_by_field_name("left")
        if left is None:
            return
        name = self.node_text(left, source)
        # Skip dunder assignments and complex targets
        if "." in name or "[" in name:
            return
        if name == "__all__":
            return

        right = node.child_by_field_name("right")
        sig = f"{name} = {self.node_text(right, source)[:80]}" if right else name

        # Check if it looks like a constant (ALL_CAPS)
        kind = "constant" if name.isupper() or (name.upper() == name and "_" in name) else "variable"

        symbols.append(self._make_symbol(
            name=name,
            kind=kind,
            line_start=node.start_point[0] + 1,
            line_end=node.end_point[0] + 1,
            signature=sig,
            visibility=self._visibility(name),
            is_exported=self._is_exported(name, dunder_all),
        ))

    def _extract_class_property(self, node, source, symbols, parent_name):
        """Extract a class-level assignment as a property symbol."""
        left = node.child_by_field_name("left")
        if left is None:
            return
        name = self.node_text(left, source)
        # Skip complex targets (self.x, a.b, a[0])
        if "." in name or "[" in name:
            return
        if name == "__all__":
            return

        right = node.child_by_field_name("right")
        default_value = None
        if right:
            # Extract simple literal values
            default_value = self._extract_literal_value(right, source)

        qualified = f"{parent_name}.{name}" if parent_name else name
        vis = self._visibility(name)
        symbols.append(self._make_symbol(
            name=name,
            kind="property",
            line_start=node.start_point[0] + 1,
            line_end=node.end_point[0] + 1,
            qualified_name=qualified,
            visibility=vis,
            parent_name=parent_name,
            default_value=default_value,
        ))

    def _extract_literal_value(self, node, source) -> str | None:
        """Extract a simple literal value from a node."""
        literal_types = {"string", "integer", "float", "true", "false",
                         "none", "None", "concatenated_string"}
        if node.type in literal_types:
            text = self.node_text(node, source)
            return text[:200] if len(text) <= 200 else None
        # String with string_content child
        if node.type == "string":
            for child in node.children:
                if child.type == "string_content":
                    return self.node_text(child, source)
            return self.node_text(node, source)[:200]
        # Unary minus for negative numbers
        if node.type == "unary_operator":
            op = self.node_text(node, source)
            if op.lstrip("-").strip().replace(".", "").isdigit():
                return op
        # List/tuple/dict literals — just return first ~80 chars
        if node.type in ("list", "tuple", "dictionary"):
            text = self.node_text(node, source)
            if len(text) <= 80:
                return text
        return None

    def _detect_self_name(self, func_node) -> bytes:
        """Detect the self/cls parameter name from a method's first argument (Pyan-inspired)."""
        params = func_node.child_by_field_name("parameters")
        if params:
            for child in params.children:
                if child.type == "identifier":
                    return child.text
                # typed_parameter: (self: Self) or typed_default_parameter
                if child.type in ("typed_parameter", "typed_default_parameter"):
                    name_node = child.child_by_field_name("name")
                    if name_node and name_node.type == "identifier":
                        return name_node.text
        return b"self"

    def _extract_init_attributes(self, body_node, source, symbols, parent_name,
                                 dunder_all, self_name):
        """Extract instance attributes from __init__ body (self.x = value)."""
        seen = set()
        # Collect existing class-level property names to avoid duplicates
        for sym in symbols:
            if sym.get("parent_name") == parent_name and sym.get("kind") == "property":
                seen.add(sym["name"])

        self._collect_self_assignments(body_node, source, symbols, parent_name,
                                       dunder_all, self_name, seen)

    def _collect_self_assignments(self, node, source, symbols, parent_name,
                                  dunder_all, self_name, seen):
        """Walk __init__ body collecting self.x = ... assignments."""
        for child in node.children:
            if child.type == "expression_statement":
                for sub in child.children:
                    if sub.type == "assignment":
                        self._try_extract_self_attr(
                            sub, source, symbols, parent_name, dunder_all,
                            self_name, seen,
                        )
            elif child.type == "assignment":
                self._try_extract_self_attr(
                    child, source, symbols, parent_name, dunder_all,
                    self_name, seen,
                )
            # Recurse into conditional/try/with blocks (common in __init__)
            elif child.type in ("if_statement", "try_statement", "with_statement",
                                "for_statement", "block"):
                self._collect_self_assignments(
                    child, source, symbols, parent_name, dunder_all,
                    self_name, seen,
                )

    def _try_extract_self_attr(self, assign_node, source, symbols, parent_name,
                                dunder_all, self_name, seen):
        """If assign_node is `self.x = value`, extract x as a property symbol."""
        left = assign_node.child_by_field_name("left")
        if left is None or left.type != "attribute":
            return
        obj = left.child_by_field_name("object")
        if obj is None or obj.type != "identifier" or obj.text != self_name:
            return
        attr_node = left.child_by_field_name("attribute")
        if attr_node is None:
            return
        attr_name = self.node_text(attr_node, source)
        if attr_name in seen:
            return
        seen.add(attr_name)

        right = assign_node.child_by_field_name("right")
        default_value = self._extract_literal_value(right, source) if right else None

        qualified = f"{parent_name}.{attr_name}"
        symbols.append(self._make_symbol(
            name=attr_name,
            kind="property",
            line_start=assign_node.start_point[0] + 1,
            line_end=assign_node.end_point[0] + 1,
            qualified_name=qualified,
            visibility=self._visibility(attr_name),
            parent_name=parent_name,
            default_value=default_value,
        ))

    def _is_exported(self, name: str, dunder_all: set[str] | None) -> bool:
        if dunder_all is not None:
            return name in dunder_all
        return not name.startswith("_")

    def _walk_refs(self, node, source, file_path, refs, scope_name):
        for child in node.children:
            if child.type == "import_statement":
                self._extract_import(child, source, refs, scope_name)
            elif child.type == "import_from_statement":
                self._extract_from_import(child, source, refs, scope_name)
            elif child.type == "call":
                self._extract_call(child, source, refs, scope_name)
            elif child.type == "decorated_definition":
                self._extract_decorator_refs(child, source, refs, scope_name)
                self._walk_refs(child, source, file_path, refs, scope_name)
            elif child.type in ("assignment", "expression_statement"):
                # Walk type annotations on assignments: x: Path = ..., self.x: int = ...
                # Note: type aliases (PathList = List[Path]) are NOT handled here —
                # the RHS is a subscript expression, not a type annotation field.
                self._extract_assignment_type_refs(child, source, refs, scope_name)
                self._walk_refs(child, source, file_path, refs, scope_name)
            else:
                # Recurse, updating scope for classes/functions
                new_scope = scope_name
                if child.type in ("function_definition", "class_definition"):
                    n = child.child_by_field_name("name")
                    if n:
                        fname = self.node_text(n, source)
                        new_scope = f"{scope_name}.{fname}" if scope_name else fname
                    # Extract type annotation refs from function parameters and return
                    if child.type == "function_definition":
                        self._extract_type_refs(child, source, refs, new_scope)
                self._walk_refs(child, source, file_path, refs, new_scope)

    def _extract_decorator_refs(self, decorated_node, source, refs, scope_name):
        """Extract references from decorators (e.g., @cache, @app.route)."""
        for child in decorated_node.children:
            if child.type == "decorator":
                # The decorator content is after the '@'
                for sub in child.children:
                    if sub.type == "identifier":
                        name = self.node_text(sub, source)
                        refs.append(self._make_reference(
                            target_name=name,
                            kind="call",
                            line=sub.start_point[0] + 1,
                            source_name=scope_name,
                        ))
                    elif sub.type == "attribute":
                        name = self.node_text(sub, source)
                        refs.append(self._make_reference(
                            target_name=name,
                            kind="call",
                            line=sub.start_point[0] + 1,
                            source_name=scope_name,
                        ))
                    elif sub.type == "call":
                        self._extract_call(sub, source, refs, scope_name)

    def _extract_type_refs(self, func_node, source, refs, scope_name):
        """Extract references from type annotations in function signatures."""
        # Parameter type annotations
        params = func_node.child_by_field_name("parameters")
        if params:
            for param in params.children:
                type_node = param.child_by_field_name("type")
                if type_node:
                    self._walk_type_node(type_node, source, refs, scope_name)

        # Return type annotation
        ret = func_node.child_by_field_name("return_type")
        if ret:
            self._walk_type_node(ret, source, refs, scope_name)

    def _extract_assignment_type_refs(self, node, source, refs, scope_name):
        """Extract type_ref edges from annotated assignments (class fields, module vars)."""
        targets = [node] if node.type == "assignment" else []
        if node.type == "expression_statement":
            for sub in node.children:
                if sub.type == "assignment":
                    targets.append(sub)
        for assign in targets:
            type_node = assign.child_by_field_name("type")
            if type_node:
                self._walk_type_node(type_node, source, refs, scope_name)

    def _walk_type_node(self, node, source, refs, scope_name):
        """Walk a type annotation node and extract type references."""
        if node.type == "identifier":
            name = self.node_text(node, source)
            if name not in _BUILTIN_TYPES:
                refs.append(self._make_reference(
                    target_name=name,
                    kind="type_ref",
                    line=node.start_point[0] + 1,
                    source_name=scope_name,
                ))
        elif node.type == "attribute":
            name = self.node_text(node, source)
            refs.append(self._make_reference(
                target_name=name,
                kind="type_ref",
                line=node.start_point[0] + 1,
                source_name=scope_name,
            ))
        elif node.type == "string":
            # Forward reference: Optional["Config"] -> extract "Config"
            for child in node.children:
                if child.type == "string_content":
                    name = self.node_text(child, source).strip()
                    # Simple identifier forward ref: "Config"
                    if name.isidentifier() and name not in _BUILTIN_TYPES:
                        refs.append(self._make_reference(
                            target_name=name,
                            kind="type_ref",
                            line=child.start_point[0] + 1,
                            source_name=scope_name,
                        ))
                    # Dotted forward ref: "module.ClassName"
                    elif "." in name and all(
                        p.isidentifier() for p in name.split(".")
                    ):
                        refs.append(self._make_reference(
                            target_name=name,
                            kind="type_ref",
                            line=child.start_point[0] + 1,
                            source_name=scope_name,
                        ))
        else:
            # Recurse into generic types like List[Item], Optional[str], etc.
            for child in node.children:
                self._walk_type_node(child, source, refs, scope_name)

    def _extract_import(self, node, source, refs, scope_name):
        # import x, import x.y, import x as y
        for child in node.children:
            if child.type == "dotted_name":
                mod = self.node_text(child, source)
                refs.append(self._make_reference(
                    target_name=mod,
                    kind="import",
                    line=child.start_point[0] + 1,
                    source_name=scope_name,
                    import_path=mod,
                ))
            elif child.type == "aliased_import":
                name_node = child.child_by_field_name("name")
                if name_node:
                    mod = self.node_text(name_node, source)
                    refs.append(self._make_reference(
                        target_name=mod,
                        kind="import",
                        line=child.start_point[0] + 1,
                        source_name=scope_name,
                        import_path=mod,
                    ))

    def _extract_from_import(self, node, source, refs, scope_name):
        # from x import y, z
        module_node = node.child_by_field_name("module_name")
        mod_path = self.node_text(module_node, source) if module_node else ""

        for child in node.children:
            if child.type == "dotted_name" and child != module_node:
                name = self.node_text(child, source)
                refs.append(self._make_reference(
                    target_name=name,
                    kind="import",
                    line=child.start_point[0] + 1,
                    source_name=scope_name,
                    import_path=f"{mod_path}.{name}" if mod_path else name,
                ))
            elif child.type == "aliased_import":
                name_node = child.child_by_field_name("name")
                if name_node:
                    name = self.node_text(name_node, source)
                    refs.append(self._make_reference(
                        target_name=name,
                        kind="import",
                        line=child.start_point[0] + 1,
                        source_name=scope_name,
                        import_path=f"{mod_path}.{name}" if mod_path else name,
                    ))
            elif child.type == "wildcard_import":
                refs.append(self._make_reference(
                    target_name="*",
                    kind="import",
                    line=child.start_point[0] + 1,
                    source_name=scope_name,
                    import_path=f"{mod_path}.*" if mod_path else "*",
                ))

    def _extract_call(self, node, source, refs, scope_name):
        func_node = node.child_by_field_name("function")
        if func_node is None:
            return

        if func_node.type == "identifier":
            name = self.node_text(func_node, source)
        elif func_node.type == "attribute":
            name = self.node_text(func_node, source)
        else:
            name = self.node_text(func_node, source)

        refs.append(self._make_reference(
            target_name=name,
            kind="call",
            line=func_node.start_point[0] + 1,
            source_name=scope_name,
        ))

        # Recurse into call arguments for nested calls
        args = node.child_by_field_name("arguments")
        if args:
            self._walk_refs(args, source, "", refs, scope_name)
