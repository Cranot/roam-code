from __future__ import annotations

from .base import LanguageExtractor

# Built-in Go types that produce no useful type_ref edge.
_GO_BUILTIN_TYPES: frozenset[str] = frozenset(
    {
        "bool",
        "byte",
        "complex64",
        "complex128",
        "error",
        "float32",
        "float64",
        "int",
        "int8",
        "int16",
        "int32",
        "int64",
        "rune",
        "string",
        "uint",
        "uint8",
        "uint16",
        "uint32",
        "uint64",
        "uintptr",
        "any",
        "comparable",
    }
)


class GoExtractor(LanguageExtractor):
    """Full Go symbol and reference extractor."""

    @property
    def language_name(self) -> str:
        return "go"

    @property
    def file_extensions(self) -> list[str]:
        return [".go"]

    def extract_symbols(self, tree, source: bytes, file_path: str) -> list[dict]:
        symbols = []
        self._pending_inherits = []
        self._walk_symbols(tree.root_node, source, file_path, symbols)
        return symbols

    def extract_references(self, tree, source: bytes, file_path: str) -> list[dict]:
        refs = []
        self._walk_refs(tree.root_node, source, refs, scope_name=None)
        # Collect inheritance refs accumulated during extract_symbols
        refs.extend(getattr(self, "_pending_inherits", []))
        self._pending_inherits = []
        return refs

    def get_docstring(self, node, source: bytes) -> str | None:
        """Go doc comments: look for consecutive comment lines before node."""
        prev = node.prev_sibling
        comments = []
        while prev and prev.type == "comment":
            text = self.node_text(prev, source).strip()
            if text.startswith("//"):
                text = text[2:].strip()
            comments.insert(0, text)
            prev = prev.prev_sibling
        return "\n".join(comments) if comments else None

    def _is_exported(self, name: str) -> bool:
        """In Go, exported identifiers start with an uppercase letter."""
        return bool(name) and name[0].isupper()

    # ---- Symbol extraction ----

    def _walk_symbols(self, node, source, file_path, symbols):
        for child in node.children:
            if child.type == "function_declaration":
                self._extract_function(child, source, symbols)
            elif child.type == "method_declaration":
                self._extract_method(child, source, symbols)
            elif child.type == "type_declaration":
                self._extract_type_decl(child, source, symbols)
            elif child.type == "package_clause":
                self._extract_package(child, source, symbols)
            elif child.type == "var_declaration":
                self._extract_var_decl(child, source, symbols)
            elif child.type == "const_declaration":
                self._extract_const_decl(child, source, symbols)

    def _extract_function(self, node, source, symbols):
        name_node = node.child_by_field_name("name")
        if name_node is None:
            return
        name = self.node_text(name_node, source)
        params = node.child_by_field_name("parameters")
        result = node.child_by_field_name("result")

        sig = f"func {name}({self._params_text(params, source)})"
        if result:
            sig += f" {self.node_text(result, source)}"

        # Type parameters (generics)
        type_params = node.child_by_field_name("type_parameters")
        if type_params:
            sig = f"func {name}{self.node_text(type_params, source)}({self._params_text(params, source)})"
            if result:
                sig += f" {self.node_text(result, source)}"

        symbols.append(
            self._make_symbol(
                name=name,
                kind="function",
                line_start=node.start_point[0] + 1,
                line_end=node.end_point[0] + 1,
                signature=sig,
                docstring=self.get_docstring(node, source),
                visibility="public" if self._is_exported(name) else "private",
                is_exported=self._is_exported(name),
            )
        )

    def _extract_method(self, node, source, symbols):
        name_node = node.child_by_field_name("name")
        if name_node is None:
            return
        name = self.node_text(name_node, source)

        # Get receiver
        receiver = node.child_by_field_name("receiver")
        recv_text = self.node_text(receiver, source) if receiver else ""
        recv_type = self._extract_receiver_type(receiver, source)

        params = node.child_by_field_name("parameters")
        result = node.child_by_field_name("result")

        sig = f"func {recv_text} {name}({self._params_text(params, source)})"
        if result:
            sig += f" {self.node_text(result, source)}"

        qualified = f"{recv_type}.{name}" if recv_type else name

        symbols.append(
            self._make_symbol(
                name=name,
                kind="method",
                line_start=node.start_point[0] + 1,
                line_end=node.end_point[0] + 1,
                qualified_name=qualified,
                signature=sig,
                docstring=self.get_docstring(node, source),
                visibility="public" if self._is_exported(name) else "private",
                is_exported=self._is_exported(name),
                parent_name=recv_type,
            )
        )

    def _extract_receiver_type(self, receiver, source) -> str:
        """Extract the type name from a method receiver like (s *Server)."""
        if receiver is None:
            return ""
        for child in receiver.children:
            if child.type == "parameter_declaration":
                type_node = child.child_by_field_name("type")
                if type_node:
                    text = self.node_text(type_node, source).lstrip("*")
                    return text
        return ""

    def _extract_type_decl(self, node, source, symbols):
        """Extract type declarations (struct, interface, type alias)."""
        for child in node.children:
            if child.type == "type_spec":
                self._extract_type_spec(child, source, symbols, node)

    def _extract_type_spec(self, node, source, symbols, parent_node):
        name_node = node.child_by_field_name("name")
        if name_node is None:
            return
        name = self.node_text(name_node, source)
        type_node = node.child_by_field_name("type")

        if type_node is None:
            return

        kind = "type_alias"
        sig = f"type {name}"
        if type_node.type == "struct_type":
            kind = "struct"
            sig = f"type {name} struct"
            # Extract struct fields
            self._extract_struct_fields(type_node, source, symbols, name)
        elif type_node.type == "interface_type":
            kind = "interface"
            sig = f"type {name} interface"
            self._extract_interface_methods(type_node, source, symbols, name)
        else:
            sig += f" {self.node_text(type_node, source)[:60]}"

        symbols.append(
            self._make_symbol(
                name=name,
                kind=kind,
                line_start=parent_node.start_point[0] + 1,
                line_end=parent_node.end_point[0] + 1,
                signature=sig,
                docstring=self.get_docstring(parent_node, source),
                visibility="public" if self._is_exported(name) else "private",
                is_exported=self._is_exported(name),
            )
        )

    def _extract_struct_fields(self, struct_node, source, symbols, struct_name):
        for child in struct_node.children:
            if child.type == "field_declaration_list":
                for field in child.children:
                    if field.type == "field_declaration":
                        name_node = field.child_by_field_name("name")
                        type_node = field.child_by_field_name("type")
                        if name_node:
                            field_name = self.node_text(name_node, source)
                            sig = field_name
                            if type_node:
                                sig += f" {self.node_text(type_node, source)}"
                            symbols.append(
                                self._make_symbol(
                                    name=field_name,
                                    kind="field",
                                    line_start=field.start_point[0] + 1,
                                    line_end=field.end_point[0] + 1,
                                    qualified_name=f"{struct_name}.{field_name}",
                                    signature=sig,
                                    visibility="public" if self._is_exported(field_name) else "private",
                                    is_exported=self._is_exported(field_name),
                                    parent_name=struct_name,
                                )
                            )
                        elif type_node:
                            # No name = embedded/anonymous field (struct embedding)
                            type_name = self.node_text(type_node, source).lstrip("*")
                            self._pending_inherits.append(
                                self._make_reference(
                                    target_name=type_name,
                                    kind="inherits",
                                    line=field.start_point[0] + 1,
                                    source_name=struct_name,
                                )
                            )

    def _extract_interface_methods(self, iface_node, source, symbols, iface_name):
        for child in iface_node.children:
            if child.type in ("method_spec", "method_elem"):
                name_node = child.child_by_field_name("name")
                if name_node:
                    method_name = self.node_text(name_node, source)
                    params = child.child_by_field_name("parameters")
                    result = child.child_by_field_name("result")
                    sig = f"{method_name}({self._params_text(params, source)})"
                    if result:
                        sig += f" {self.node_text(result, source)}"
                    symbols.append(
                        self._make_symbol(
                            name=method_name,
                            kind="method",
                            line_start=child.start_point[0] + 1,
                            line_end=child.end_point[0] + 1,
                            qualified_name=f"{iface_name}.{method_name}",
                            signature=sig,
                            visibility="public" if self._is_exported(method_name) else "private",
                            is_exported=self._is_exported(method_name),
                            parent_name=iface_name,
                        )
                    )

    def _extract_package(self, node, source, symbols):
        for child in node.children:
            if child.type == "package_identifier":
                name = self.node_text(child, source)
                symbols.append(
                    self._make_symbol(
                        name=name,
                        kind="module",
                        line_start=node.start_point[0] + 1,
                        line_end=node.end_point[0] + 1,
                        signature=f"package {name}",
                        is_exported=True,
                    )
                )

    def _extract_var_decl(self, node, source, symbols):
        for child in node.children:
            if child.type == "var_spec":
                name_node = child.child_by_field_name("name")
                if name_node:
                    name = self.node_text(name_node, source)
                    type_n = child.child_by_field_name("type")
                    sig = f"var {name}"
                    if type_n:
                        sig += f" {self.node_text(type_n, source)}"
                    symbols.append(
                        self._make_symbol(
                            name=name,
                            kind="variable",
                            line_start=child.start_point[0] + 1,
                            line_end=child.end_point[0] + 1,
                            signature=sig,
                            visibility="public" if self._is_exported(name) else "private",
                            is_exported=self._is_exported(name),
                        )
                    )

    def _extract_const_decl(self, node, source, symbols):
        for child in node.children:
            if child.type == "const_spec":
                name_node = child.child_by_field_name("name")
                if name_node:
                    name = self.node_text(name_node, source)
                    type_n = child.child_by_field_name("type")
                    sig = f"const {name}"
                    if type_n:
                        sig += f" {self.node_text(type_n, source)}"
                    symbols.append(
                        self._make_symbol(
                            name=name,
                            kind="constant",
                            line_start=child.start_point[0] + 1,
                            line_end=child.end_point[0] + 1,
                            signature=sig,
                            visibility="public" if self._is_exported(name) else "private",
                            is_exported=self._is_exported(name),
                        )
                    )

    # ---- Type-reference helpers ----

    def _walk_type_node(self, type_node, source, refs, scope_name):
        """Walk a Go type node and emit type_ref edges for named types."""
        if type_node is None:
            return
        t = type_node.type
        line = type_node.start_point[0] + 1

        if t == "type_identifier":
            name = self.node_text(type_node, source)
            if name not in _GO_BUILTIN_TYPES:
                refs.append(self._make_reference(target_name=name, kind="type_ref", line=line, source_name=scope_name))

        elif t == "qualified_type":
            # pkg.Type — emit the unqualified name only; that matches how symbols are stored.
            # Cross-package resolution happens through import edges, not qualified type_ref names.
            name_n = type_node.child_by_field_name("name")
            if name_n:
                name = self.node_text(name_n, source)
                if name not in _GO_BUILTIN_TYPES:
                    refs.append(
                        self._make_reference(target_name=name, kind="type_ref", line=line, source_name=scope_name)
                    )

        elif t == "pointer_type":
            # *T — walk the pointed-to type (no named field in tree-sitter-go)
            for child in type_node.children:
                if child.type != "*":
                    self._walk_type_node(child, source, refs, scope_name)

        elif t in ("slice_type", "array_type"):
            elem = type_node.child_by_field_name("element")
            self._walk_type_node(elem, source, refs, scope_name)

        elif t == "map_type":
            self._walk_type_node(type_node.child_by_field_name("key"), source, refs, scope_name)
            self._walk_type_node(type_node.child_by_field_name("value"), source, refs, scope_name)

        elif t == "channel_type":
            self._walk_type_node(type_node.child_by_field_name("value"), source, refs, scope_name)

        elif t == "generic_type":
            # Type[T1, T2] — base type and type arguments
            self._walk_type_node(type_node.child_by_field_name("type"), source, refs, scope_name)
            type_args = type_node.child_by_field_name("type_arguments")
            if type_args:
                for child in type_args.children:
                    if child.is_named:
                        self._walk_type_node(child, source, refs, scope_name)

        elif t == "function_type":
            # func(Arg) Ret — walk parameter_list children directly (no field names in tree-sitter-go)
            plists = [c for c in type_node.children if c.type == "parameter_list"]
            for plist in plists:
                for param in plist.children:
                    if param.type in ("parameter_declaration", "variadic_parameter_declaration"):
                        self._walk_type_node(param.child_by_field_name("type"), source, refs, scope_name)
            # Single return type sits directly as a named child after the parameter_list
            for child in type_node.children:
                if child.is_named and child.type not in ("parameter_list",) and child.type != "func":
                    self._walk_type_node(child, source, refs, scope_name)

        elif t == "struct_type":
            # Struct field type_refs are emitted by _walk_refs recursion; skip here to avoid
            # double-emission when a struct appears as an inline anonymous struct field type.
            return

        elif t == "interface_type":
            # Go 1.18+: type constraint elements inside an interface
            for child in type_node.children:
                if child.type == "type_elem":
                    for sub in child.children:
                        if sub.type == "negated_type":
                            # ~T constraint — T is a type_ref
                            for inner in sub.children:
                                if inner.type != "~":
                                    self._walk_type_node(inner, source, refs, scope_name)
                        elif sub.type not in ("|",):
                            self._walk_type_node(sub, source, refs, scope_name)

        else:
            # Fallback: walk named children (handles parenthesized_type and future node types)
            for child in type_node.children:
                if child.is_named and child.type not in ("comment", "block", "struct_type"):
                    self._walk_type_node(child, source, refs, scope_name)

    def _extract_func_type_refs(self, func_node, source, refs, scope_name):
        """Emit type_ref edges for a function/method's parameter and return types."""
        params = func_node.child_by_field_name("parameters")
        result = func_node.child_by_field_name("result")
        if params:
            for child in params.children:
                if child.type in ("parameter_declaration", "variadic_parameter_declaration"):
                    self._walk_type_node(child.child_by_field_name("type"), source, refs, scope_name)
        if result:
            if result.type == "parameter_list":
                for child in result.children:
                    if child.type in ("parameter_declaration", "variadic_parameter_declaration"):
                        self._walk_type_node(child.child_by_field_name("type"), source, refs, scope_name)
            else:
                self._walk_type_node(result, source, refs, scope_name)

    # ---- Reference extraction ----

    def _walk_refs(self, node, source, refs, scope_name):
        for child in node.children:
            if child.type == "import_declaration":
                self._extract_imports(child, source, refs, scope_name)
            elif child.type == "call_expression":
                self._extract_call(child, source, refs, scope_name)
            else:
                new_scope = scope_name
                if child.type == "function_declaration":
                    n = child.child_by_field_name("name")
                    if n:
                        new_scope = self.node_text(n, source)
                    self._extract_func_type_refs(child, source, refs, new_scope)
                elif child.type == "method_declaration":
                    n = child.child_by_field_name("name")
                    if n:
                        recv = self._extract_receiver_type(child.child_by_field_name("receiver"), source)
                        fname = self.node_text(n, source)
                        new_scope = f"{recv}.{fname}" if recv else fname
                    self._extract_func_type_refs(child, source, refs, new_scope)
                elif child.type == "field_declaration":
                    # Struct field type → type_ref
                    self._walk_type_node(child.child_by_field_name("type"), source, refs, scope_name)
                elif child.type in ("parameter_declaration", "variadic_parameter_declaration"):
                    # Already handled by _extract_func_type_refs; skip to avoid double-emission
                    pass
                elif child.type == "type_spec":
                    type_node = child.child_by_field_name("type")
                    # Emit type_ref for aliases only; struct/interface are definitions, not refs
                    if type_node and type_node.type not in ("struct_type", "interface_type"):
                        self._walk_type_node(type_node, source, refs, scope_name)
                elif child.type == "type_alias":
                    # type Foo = Bar — the RHS type is a ref, the LHS name is the definition.
                    # child_by_field_name("type") correctly returns the RHS even for generic aliases.
                    rhs = child.child_by_field_name("type")
                    if rhs:
                        self._walk_type_node(rhs, source, refs, scope_name)
                elif child.type in ("var_spec", "const_spec"):
                    self._walk_type_node(child.child_by_field_name("type"), source, refs, scope_name)
                elif child.type == "method_elem":
                    # Interface method signature — no named field for params/result; walk children.
                    # Structure: field_identifier, parameter_list (params), then either a
                    # parameter_list (multi-return) or a bare type node (single return).
                    n = child.child_by_field_name("name")
                    method_name = self.node_text(n, source) if n else None
                    m_scope = f"{scope_name}.{method_name}" if method_name else scope_name
                    for mc in child.children:
                        if mc.type == "parameter_list":
                            for param in mc.children:
                                if param.type in ("parameter_declaration", "variadic_parameter_declaration"):
                                    self._walk_type_node(param.child_by_field_name("type"), source, refs, m_scope)
                        elif mc.is_named and mc.type != "field_identifier":
                            # Single return type sits directly as a named non-field child
                            self._walk_type_node(mc, source, refs, m_scope)
                self._walk_refs(child, source, refs, new_scope)

    def _extract_imports(self, node, source, refs, scope_name):
        for child in node.children:
            if child.type == "import_spec":
                path_node = child.child_by_field_name("path")
                if path_node:
                    path = self.node_text(path_node, source).strip('"')
                    # Use last component as target name
                    target = path.rsplit("/", 1)[-1] if "/" in path else path
                    # Check for alias
                    name_node = child.child_by_field_name("name")
                    if name_node:
                        target = self.node_text(name_node, source)
                    refs.append(
                        self._make_reference(
                            target_name=target,
                            kind="import",
                            line=child.start_point[0] + 1,
                            source_name=scope_name,
                            import_path=path,
                        )
                    )
            elif child.type == "import_spec_list":
                self._extract_imports(child, source, refs, scope_name)
            elif child.type == "interpreted_string_literal":
                # Single import without parens
                path = self.node_text(child, source).strip('"')
                target = path.rsplit("/", 1)[-1] if "/" in path else path
                refs.append(
                    self._make_reference(
                        target_name=target,
                        kind="import",
                        line=child.start_point[0] + 1,
                        source_name=scope_name,
                        import_path=path,
                    )
                )

    def _extract_call(self, node, source, refs, scope_name):
        func_node = node.child_by_field_name("function")
        if func_node is None:
            return

        # Handle method calls: obj.Method() -> extract "Method"
        if func_node.type == "selector_expression":
            field = func_node.child_by_field_name("field")
            if field:
                name = self.node_text(field, source)
            else:
                name = self.node_text(func_node, source)
        else:
            name = self.node_text(func_node, source)

        refs.append(
            self._make_reference(
                target_name=name,
                kind="call",
                line=func_node.start_point[0] + 1,
                source_name=scope_name,
            )
        )
        # Recurse into arguments
        args = node.child_by_field_name("arguments")
        if args:
            self._walk_refs(args, source, refs, scope_name)
