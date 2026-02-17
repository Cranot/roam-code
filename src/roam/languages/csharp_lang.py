from __future__ import annotations

from .base import LanguageExtractor


class CSharpExtractor(LanguageExtractor):
    """C# symbol and reference extractor."""

    @property
    def language_name(self) -> str:
        return "c_sharp"

    @property
    def file_extensions(self) -> list[str]:
        return [".cs"]

    def extract_symbols(self, tree, source: bytes, file_path: str) -> list[dict]:
        symbols = []
        self._pending_inherits = []
        self._symbol_kinds = {}
        self._walk_symbols(tree.root_node, source, symbols, parent_name=None)
        return symbols

    def extract_references(self, tree, source: bytes, file_path: str) -> list[dict]:
        refs = []
        self._walk_refs(tree.root_node, source, refs, scope_name=None)
        # resolve pending inheritance refs with positional heuristic
        for entry in self._pending_inherits:
            target = entry["_target"]
            pos = entry["_position"]
            if pos == 0 and not target.startswith("I"):
                kind = "inherits"
            else:
                kind = "implements"
            refs.append(self._make_reference(
                target_name=target,
                kind=kind,
                line=entry["_line"],
                source_name=entry["_source"],
            ))
        self._pending_inherits = []
        return refs

    def get_docstring(self, node, source: bytes) -> str | None:
        """extract xml doc comments (/// chains) preceding a node."""
        prev = node.prev_sibling
        lines = []
        while prev and prev.type == "comment":
            text = self.node_text(prev, source).strip()
            if text.startswith("///"):
                lines.insert(0, text[3:].strip())
                prev = prev.prev_sibling
            else:
                break
        return "\n".join(lines) if lines else None

    def _get_visibility(self, node, source, parent_kind: str | None = None) -> str:
        """extract visibility, accounting for compound modifiers and context-dependent defaults."""
        mods = set()
        for child in node.children:
            if child.type == "modifier":
                mods.add(self.node_text(child, source))
        # compound modifiers (two separate nodes combined)
        if {"private", "protected"} <= mods:
            return "private protected"
        if {"protected", "internal"} <= mods:
            return "protected internal"
        # single modifiers
        if "private" in mods:
            return "private"
        if "protected" in mods:
            return "protected"
        if "internal" in mods:
            return "internal"
        if "public" in mods:
            return "public"
        # context-dependent defaults
        if parent_kind in ("interface", "enum"):
            return "public"
        if parent_kind in ("class", "struct", "record"):
            return "private"
        return "internal"

    def _get_class_modifiers(self, node, source) -> list[str]:
        """extract class/struct modifiers for signature."""
        relevant = {"static", "sealed", "abstract", "partial", "readonly", "unsafe", "file"}
        modifiers = []
        for child in node.children:
            if child.type == "modifier":
                text = self.node_text(child, source)
                if text in relevant:
                    modifiers.append(text)
        return modifiers

    def _has_modifier(self, node, source, modifier: str) -> bool:
        for child in node.children:
            if child.type == "modifier":
                if self.node_text(child, source) == modifier:
                    return True
        return False

    def _get_type_params(self, node, source) -> str:
        """extract generic type parameter list (<T, U>)."""
        for child in node.children:
            if child.type == "type_parameter_list":
                return self.node_text(child, source)
        return ""

    def _get_constraints(self, node, source) -> str:
        """extract type parameter constraints (where T : class, ...)."""
        constraints = []
        for child in node.children:
            if child.type == "type_parameter_constraints_clause":
                constraints.append(self.node_text(child, source))
        if not constraints:
            return ""
        text = " ".join(constraints)
        if len(text) > 200:
            text = text[:200] + "..."
        return " " + text

    def _get_namespace_name(self, node, source) -> str:
        """extract namespace name from a namespace declaration node."""
        name_node = node.child_by_field_name("name")
        if name_node:
            return self.node_text(name_node, source)
        for child in node.children:
            if child.type in ("qualified_name", "identifier"):
                return self.node_text(child, source)
        return ""

    # ---- Symbol extraction ----

    def _walk_symbols(self, node, source, symbols, parent_name):
        current_ns = parent_name
        for child in node.children:
            if child.type == "file_scoped_namespace_declaration":
                ns_name = self._get_namespace_name(child, source)
                qualified = f"{parent_name}.{ns_name}" if parent_name else ns_name
                symbols.append(self._make_symbol(
                    name=ns_name,
                    kind="module",
                    line_start=child.start_point[0] + 1,
                    line_end=child.end_point[0] + 1,
                    qualified_name=qualified,
                    signature=f"namespace {ns_name}",
                    visibility="public",
                    is_exported=True,
                ))
                current_ns = qualified
            elif child.type == "namespace_declaration":
                self._extract_namespace(child, source, symbols, current_ns)
            elif child.type == "class_declaration":
                self._extract_class(child, source, symbols, current_ns, kind="class")
            elif child.type == "interface_declaration":
                self._extract_class(child, source, symbols, current_ns, kind="interface")
            elif child.type == "struct_declaration":
                self._extract_class(child, source, symbols, current_ns, kind="struct")
            elif child.type == "enum_declaration":
                self._extract_enum(child, source, symbols, current_ns)
            elif child.type == "method_declaration":
                self._extract_method(child, source, symbols, current_ns)
            elif child.type == "constructor_declaration":
                self._extract_constructor(child, source, symbols, current_ns)
            elif child.type == "field_declaration":
                self._extract_field(child, source, symbols, current_ns)
            elif child.type == "property_declaration":
                self._extract_property(child, source, symbols, current_ns)
            elif child.type == "delegate_declaration":
                self._extract_delegate(child, source, symbols, current_ns)
            elif child.type == "record_declaration":
                is_struct = any(c.type == "struct" for c in child.children)
                kind = "struct" if is_struct else "class"
                keyword = "record struct" if is_struct else "record"
                self._extract_class(
                    child, source, symbols, current_ns,
                    kind=kind, sig_keyword=keyword,
                )
            elif child.type == "event_declaration":
                self._extract_event(child, source, symbols, current_ns)
            elif child.type == "event_field_declaration":
                self._extract_event_field(child, source, symbols, current_ns)
            elif child.type == "indexer_declaration":
                self._extract_indexer(child, source, symbols, current_ns)
            elif child.type == "operator_declaration":
                self._extract_operator(child, source, symbols, current_ns)
            elif child.type == "conversion_operator_declaration":
                self._extract_conversion_operator(child, source, symbols, current_ns)
            elif child.type == "destructor_declaration":
                self._extract_destructor(child, source, symbols, current_ns)
            elif child.type == "local_function_statement":
                self._extract_local_function(child, source, symbols, current_ns)

    def _extract_namespace(self, node, source, symbols, parent_name):
        name = self._get_namespace_name(node, source)
        if not name:
            return
        qualified = f"{parent_name}.{name}" if parent_name else name
        symbols.append(self._make_symbol(
            name=name,
            kind="module",
            line_start=node.start_point[0] + 1,
            line_end=node.end_point[0] + 1,
            qualified_name=qualified,
            signature=f"namespace {name}",
            visibility="public",
            is_exported=True,
        ))
        body = node.child_by_field_name("body")
        if body:
            self._walk_symbols(body, source, symbols, parent_name=qualified)

    def _extract_class(self, node, source, symbols, parent_name, kind="class",
                       sig_keyword=None):
        name_node = node.child_by_field_name("name")
        if name_node is None:
            return
        name = self.node_text(name_node, source)
        parent_kind = self._symbol_kinds.get(parent_name) if parent_name else None
        vis = self._get_visibility(node, source, parent_kind)
        class_mods = self._get_class_modifiers(node, source)
        qualified = f"{parent_name}.{name}" if parent_name else name
        self._symbol_kinds[qualified] = kind

        # build signature
        keyword = sig_keyword or kind
        mod_prefix = " ".join(class_mods)
        sig = f"{mod_prefix} {keyword} {name}" if mod_prefix else f"{keyword} {name}"

        # generics (type params before base_list, constraints after)
        type_params = self._get_type_params(node, source)
        if type_params:
            sig += type_params

        # base_list (superclass + interfaces combined)
        for child in node.children:
            if child.type == "base_list":
                sig += f" {self.node_text(child, source)}"
                self._collect_base_list_refs(child, source, node.start_point[0] + 1, qualified)
                break

        constraints = self._get_constraints(node, source)
        if constraints:
            sig += constraints

        is_file_scoped = "file" in class_mods
        symbols.append(self._make_symbol(
            name=name,
            kind=kind,
            line_start=node.start_point[0] + 1,
            line_end=node.end_point[0] + 1,
            qualified_name=qualified,
            signature=sig,
            docstring=self.get_docstring(node, source),
            visibility=vis,
            is_exported=not is_file_scoped and vis == "public",
            parent_name=parent_name,
        ))

        # primary constructor (C# 12): parameter_list directly on class/struct/record
        # not a named field in tree-sitter, so find by iterating children
        primary_params = None
        for child in node.children:
            if child.type == "parameter_list":
                primary_params = child
                break
        if primary_params:
            ctor_sig = f"{name}({self._params_text(primary_params, source)})"
            symbols.append(self._make_symbol(
                name=name,
                kind="constructor",
                line_start=node.start_point[0] + 1,
                line_end=node.start_point[0] + 1,
                qualified_name=f"{qualified}.{name}",
                signature=ctor_sig,
                visibility=vis,
                is_exported=vis == "public",
                parent_name=qualified,
            ))

        # walk body for nested types and members
        body = node.child_by_field_name("body")
        if body:
            self._walk_symbols(body, source, symbols, qualified)

    def _extract_enum(self, node, source, symbols, parent_name):
        name_node = node.child_by_field_name("name")
        if name_node is None:
            return
        name = self.node_text(name_node, source)
        parent_kind = self._symbol_kinds.get(parent_name) if parent_name else None
        vis = self._get_visibility(node, source, parent_kind)
        qualified = f"{parent_name}.{name}" if parent_name else name
        self._symbol_kinds[qualified] = "enum"
        sig = f"enum {name}"

        # base type (e.g. enum Foo : byte)
        for child in node.children:
            if child.type == "base_list":
                sig += f" {self.node_text(child, source)}"
                break

        symbols.append(self._make_symbol(
            name=name,
            kind="enum",
            line_start=node.start_point[0] + 1,
            line_end=node.end_point[0] + 1,
            qualified_name=qualified,
            signature=sig,
            docstring=self.get_docstring(node, source),
            visibility=vis,
            is_exported=vis == "public",
            parent_name=parent_name,
        ))

        # enum members
        body = node.child_by_field_name("body")
        if body:
            for child in body.children:
                if child.type == "enum_member_declaration":
                    cn = child.child_by_field_name("name")
                    if cn is None:
                        for sub in child.children:
                            if sub.type == "identifier":
                                cn = sub
                                break
                    if cn:
                        const_name = self.node_text(cn, source)
                        symbols.append(self._make_symbol(
                            name=const_name,
                            kind="constant",
                            line_start=child.start_point[0] + 1,
                            line_end=child.end_point[0] + 1,
                            qualified_name=f"{qualified}.{const_name}",
                            parent_name=qualified,
                            visibility="public",
                            is_exported=vis == "public",
                        ))

    def _extract_method(self, node, source, symbols, parent_name):
        name_node = node.child_by_field_name("name")
        if name_node is None:
            return
        name = self.node_text(name_node, source)
        parent_kind = self._symbol_kinds.get(parent_name) if parent_name else None
        vis = self._get_visibility(node, source, parent_kind)
        is_static = self._has_modifier(node, source, "static")
        is_async = self._has_modifier(node, source, "async")

        ret_type = node.child_by_field_name("returns")
        params = node.child_by_field_name("parameters")

        sig = ""
        if is_static:
            sig += "static "
        if is_async:
            sig += "async "
        if ret_type:
            sig += self.node_text(ret_type, source) + " "
        sig += name

        type_params = self._get_type_params(node, source)
        if type_params:
            sig += type_params

        sig += f"({self._params_text(params, source)})"

        constraints = self._get_constraints(node, source)
        if constraints:
            sig += constraints

        qualified = f"{parent_name}.{name}" if parent_name else name
        symbols.append(self._make_symbol(
            name=name,
            kind="method",
            line_start=node.start_point[0] + 1,
            line_end=node.end_point[0] + 1,
            qualified_name=qualified,
            signature=sig,
            docstring=self.get_docstring(node, source),
            visibility=vis,
            is_exported=vis == "public",
            parent_name=parent_name,
        ))

        # search body for local functions
        body = node.child_by_field_name("body")
        if body:
            self._find_local_functions(body, source, symbols, qualified)

    def _extract_constructor(self, node, source, symbols, parent_name):
        name_node = node.child_by_field_name("name")
        if name_node is None:
            return
        name = self.node_text(name_node, source)
        parent_kind = self._symbol_kinds.get(parent_name) if parent_name else None
        vis = self._get_visibility(node, source, parent_kind)
        params = node.child_by_field_name("parameters")
        sig = f"{name}({self._params_text(params, source)})"

        qualified = f"{parent_name}.{name}" if parent_name else name
        symbols.append(self._make_symbol(
            name=name,
            kind="constructor",
            line_start=node.start_point[0] + 1,
            line_end=node.end_point[0] + 1,
            qualified_name=qualified,
            signature=sig,
            docstring=self.get_docstring(node, source),
            visibility=vis,
            is_exported=vis == "public",
            parent_name=parent_name,
        ))

        # search body for local functions
        body = node.child_by_field_name("body")
        if body:
            self._find_local_functions(body, source, symbols, qualified)

    def _iter_variable_declarators(self, node, source):
        """yield (name, type_text) for each variable_declarator in a variable_declaration.

        Handles the c# nesting: field/event_field -> variable_declaration -> variable_declarator.
        """
        for child in node.children:
            if child.type != "variable_declaration":
                continue
            type_node = child.child_by_field_name("type")
            type_text = self.node_text(type_node, source) if type_node else ""
            for var_child in child.children:
                if var_child.type != "variable_declarator":
                    continue
                name_node = var_child.child_by_field_name("name")
                if name_node is None:
                    name_node = next(
                        (vc for vc in var_child.children if vc.type == "identifier"),
                        None,
                    )
                if name_node is not None:
                    yield self.node_text(name_node, source), type_text

    def _extract_field(self, node, source, symbols, parent_name):
        """extract field declarations with c# variable_declaration nesting."""
        parent_kind = self._symbol_kinds.get(parent_name) if parent_name else None
        vis = self._get_visibility(node, source, parent_kind)
        is_static = self._has_modifier(node, source, "static")
        is_readonly = self._has_modifier(node, source, "readonly")
        is_const = self._has_modifier(node, source, "const")

        for name, type_text in self._iter_variable_declarators(node, source):
            kind = "constant" if is_const or (is_static and is_readonly) else "field"
            sig_parts = []
            if is_static:
                sig_parts.append("static")
            if is_readonly:
                sig_parts.append("readonly")
            if is_const:
                sig_parts.append("const")
            sig_parts.append(f"{type_text} {name}")
            sig = " ".join(sig_parts)

            qualified = f"{parent_name}.{name}" if parent_name else name
            symbols.append(self._make_symbol(
                name=name,
                kind=kind,
                line_start=node.start_point[0] + 1,
                line_end=node.end_point[0] + 1,
                qualified_name=qualified,
                signature=sig,
                visibility=vis,
                is_exported=vis == "public",
                parent_name=parent_name,
            ))

    def _extract_property(self, node, source, symbols, parent_name):
        """extract property declaration with accessor info."""
        name_node = node.child_by_field_name("name")
        if name_node is None:
            return
        name = self.node_text(name_node, source)
        parent_kind = self._symbol_kinds.get(parent_name) if parent_name else None
        vis = self._get_visibility(node, source, parent_kind)
        is_static = self._has_modifier(node, source, "static")
        is_required = self._has_modifier(node, source, "required")

        type_node = node.child_by_field_name("type")
        type_text = self.node_text(type_node, source) if type_node else ""

        accessors = self._get_accessors(node, source)

        sig = ""
        if is_static:
            sig += "static "
        if is_required:
            sig += "required "
        sig += f"{type_text} {name}"
        if accessors:
            sig += f" {{ {accessors} }}"

        qualified = f"{parent_name}.{name}" if parent_name else name
        symbols.append(self._make_symbol(
            name=name,
            kind="property",
            line_start=node.start_point[0] + 1,
            line_end=node.end_point[0] + 1,
            qualified_name=qualified,
            signature=sig,
            docstring=self.get_docstring(node, source),
            visibility=vis,
            is_exported=vis == "public",
            parent_name=parent_name,
        ))

    def _extract_delegate(self, node, source, symbols, parent_name):
        """extract delegate declaration."""
        name_node = node.child_by_field_name("name")
        if name_node is None:
            return
        name = self.node_text(name_node, source)
        parent_kind = self._symbol_kinds.get(parent_name) if parent_name else None
        vis = self._get_visibility(node, source, parent_kind)

        # delegates use 'type' field for return type (not 'returns')
        ret_type = node.child_by_field_name("type")
        params = node.child_by_field_name("parameters")

        sig = "delegate "
        if ret_type:
            sig += self.node_text(ret_type, source) + " "
        sig += name

        type_params = self._get_type_params(node, source)
        if type_params:
            sig += type_params

        sig += f"({self._params_text(params, source)})"

        constraints = self._get_constraints(node, source)
        if constraints:
            sig += constraints

        qualified = f"{parent_name}.{name}" if parent_name else name
        symbols.append(self._make_symbol(
            name=name,
            kind="delegate",
            line_start=node.start_point[0] + 1,
            line_end=node.end_point[0] + 1,
            qualified_name=qualified,
            signature=sig,
            docstring=self.get_docstring(node, source),
            visibility=vis,
            is_exported=vis == "public",
            parent_name=parent_name,
        ))

    def _extract_event(self, node, source, symbols, parent_name):
        """extract event declaration with explicit add/remove accessors."""
        name_node = node.child_by_field_name("name")
        if name_node is None:
            return
        name = self.node_text(name_node, source)
        parent_kind = self._symbol_kinds.get(parent_name) if parent_name else None
        vis = self._get_visibility(node, source, parent_kind)
        is_static = self._has_modifier(node, source, "static")

        type_node = node.child_by_field_name("type")
        type_text = self.node_text(type_node, source) if type_node else ""

        sig = ""
        if is_static:
            sig += "static "
        sig += f"event {type_text} {name}"

        qualified = f"{parent_name}.{name}" if parent_name else name
        symbols.append(self._make_symbol(
            name=name,
            kind="event",
            line_start=node.start_point[0] + 1,
            line_end=node.end_point[0] + 1,
            qualified_name=qualified,
            signature=sig,
            docstring=self.get_docstring(node, source),
            visibility=vis,
            is_exported=vis == "public",
            parent_name=parent_name,
        ))

    def _extract_event_field(self, node, source, symbols, parent_name):
        """extract event field declaration (field-like events)."""
        parent_kind = self._symbol_kinds.get(parent_name) if parent_name else None
        vis = self._get_visibility(node, source, parent_kind)
        is_static = self._has_modifier(node, source, "static")

        for name, type_text in self._iter_variable_declarators(node, source):
            sig = "static " if is_static else ""
            sig += f"event {type_text} {name}"

            qualified = f"{parent_name}.{name}" if parent_name else name
            symbols.append(self._make_symbol(
                name=name,
                kind="event",
                line_start=node.start_point[0] + 1,
                line_end=node.end_point[0] + 1,
                qualified_name=qualified,
                signature=sig,
                docstring=self.get_docstring(node, source),
                visibility=vis,
                is_exported=vis == "public",
                parent_name=parent_name,
            ))

    def _extract_indexer(self, node, source, symbols, parent_name):
        """extract indexer declaration as property with name='this'."""
        parent_kind = self._symbol_kinds.get(parent_name) if parent_name else None
        vis = self._get_visibility(node, source, parent_kind)

        type_node = node.child_by_field_name("type")
        type_text = self.node_text(type_node, source) if type_node else ""
        params = node.child_by_field_name("parameters")
        params_text = self.node_text(params, source) if params else "[]"

        accessors = self._get_accessors(node, source)

        sig = f"{type_text} this{params_text}"
        if accessors:
            sig += f" {{ {accessors} }}"

        qualified = f"{parent_name}.this" if parent_name else "this"
        symbols.append(self._make_symbol(
            name="this",
            kind="property",
            line_start=node.start_point[0] + 1,
            line_end=node.end_point[0] + 1,
            qualified_name=qualified,
            signature=sig,
            docstring=self.get_docstring(node, source),
            visibility=vis,
            is_exported=vis == "public",
            parent_name=parent_name,
        ))

    def _extract_local_function(self, node, source, symbols, parent_name):
        """extract local function statement as method with is_exported=False."""
        name_node = node.child_by_field_name("name")
        if name_node is None:
            return
        name = self.node_text(name_node, source)

        # local functions use 'type' field for return type (not 'returns')
        ret_type = node.child_by_field_name("type")
        params = node.child_by_field_name("parameters")

        is_static = self._has_modifier(node, source, "static")
        is_async = self._has_modifier(node, source, "async")

        sig = ""
        if is_static:
            sig += "static "
        if is_async:
            sig += "async "
        if ret_type:
            sig += self.node_text(ret_type, source) + " "
        sig += name

        type_params = self._get_type_params(node, source)
        if type_params:
            sig += type_params

        sig += f"({self._params_text(params, source)})"

        constraints = self._get_constraints(node, source)
        if constraints:
            sig += constraints

        qualified = f"{parent_name}.{name}" if parent_name else name
        symbols.append(self._make_symbol(
            name=name,
            kind="method",
            line_start=node.start_point[0] + 1,
            line_end=node.end_point[0] + 1,
            qualified_name=qualified,
            signature=sig,
            docstring=self.get_docstring(node, source),
            visibility="private",
            is_exported=False,
            parent_name=parent_name,
        ))

        # recurse into body for nested local functions
        body = node.child_by_field_name("body")
        if body:
            self._find_local_functions(body, source, symbols, qualified)

    def _extract_operator(self, node, source, symbols, parent_name):
        """extract operator overload declaration."""
        op_node = node.child_by_field_name("operator")
        if op_node is None:
            return
        op_text = self.node_text(op_node, source)
        name = f"operator{op_text}"
        parent_kind = self._symbol_kinds.get(parent_name) if parent_name else None
        vis = self._get_visibility(node, source, parent_kind)

        type_node = node.child_by_field_name("type")
        type_text = self.node_text(type_node, source) if type_node else ""
        params = node.child_by_field_name("parameters")

        sig = f"static {type_text} operator {op_text}({self._params_text(params, source)})"

        qualified = f"{parent_name}.{name}" if parent_name else name
        symbols.append(self._make_symbol(
            name=name,
            kind="method",
            line_start=node.start_point[0] + 1,
            line_end=node.end_point[0] + 1,
            qualified_name=qualified,
            signature=sig,
            visibility=vis,
            is_exported=vis == "public",
            parent_name=parent_name,
        ))

    def _extract_conversion_operator(self, node, source, symbols, parent_name):
        """extract implicit/explicit conversion operator."""
        type_node = node.child_by_field_name("type")
        if type_node is None:
            return
        type_text = self.node_text(type_node, source)

        conversion_kind = "explicit"
        for child in node.children:
            if child.type == "implicit":
                conversion_kind = "implicit"
                break

        name = f"operator {type_text}"
        parent_kind = self._symbol_kinds.get(parent_name) if parent_name else None
        vis = self._get_visibility(node, source, parent_kind)
        params = node.child_by_field_name("parameters")

        sig = f"static {conversion_kind} operator {type_text}({self._params_text(params, source)})"

        qualified = f"{parent_name}.{name}" if parent_name else name
        symbols.append(self._make_symbol(
            name=name,
            kind="method",
            line_start=node.start_point[0] + 1,
            line_end=node.end_point[0] + 1,
            qualified_name=qualified,
            signature=sig,
            visibility=vis,
            is_exported=vis == "public",
            parent_name=parent_name,
        ))

    def _extract_destructor(self, node, source, symbols, parent_name):
        """extract destructor (~ClassName)."""
        name_node = node.child_by_field_name("name")
        if name_node is None:
            return
        name = self.node_text(name_node, source)

        sig = f"~{name}()"
        qualified = f"{parent_name}.~{name}" if parent_name else f"~{name}"
        symbols.append(self._make_symbol(
            name=f"~{name}",
            kind="method",
            line_start=node.start_point[0] + 1,
            line_end=node.end_point[0] + 1,
            qualified_name=qualified,
            signature=sig,
            visibility="private",
            is_exported=False,
            parent_name=parent_name,
        ))

    def _find_local_functions(self, node, source, symbols, parent_name):
        """recursively find local_function_statement nodes in method bodies."""
        for child in node.children:
            if child.type == "local_function_statement":
                self._extract_local_function(child, source, symbols, parent_name)
            elif child.is_named:
                self._find_local_functions(child, source, symbols, parent_name)

    _ACCESSOR_KEYWORDS = frozenset({"get", "set", "init", "add", "remove"})

    def _parse_accessor_decl(self, acc, source) -> str | None:
        """parse a single accessor_declaration into 'get', 'private set', etc."""
        mod = ""
        kw = ""
        for ac in acc.children:
            if ac.type == "modifier":
                mod = self.node_text(ac, source) + " "
            elif ac.type in self._ACCESSOR_KEYWORDS:
                kw = ac.type
        return f"{mod}{kw}".strip() if kw else None

    def _get_accessors(self, node, source) -> str:
        """extract accessor summary (get; set; init;) from property/indexer."""
        parts = []
        for child in node.children:
            if child.type == "accessor_list":
                for acc in child.children:
                    if acc.type == "accessor_declaration":
                        parsed = self._parse_accessor_decl(acc, source)
                        if parsed:
                            parts.append(parsed)
                break
            if child.type == "accessor_declaration":
                parsed = self._parse_accessor_decl(child, source)
                if parsed:
                    parts.append(parsed)
        if not parts:
            if any(c.type == "arrow_expression_clause" for c in node.children):
                parts.append("get")
        return "; ".join(parts) + ";" if parts else ""

    def _collect_base_list_refs(self, base_list, source, line, source_name):
        """accumulate base_list entries as pending inheritance refs."""
        position = 0
        for child in base_list.children:
            if not child.is_named:
                continue
            name = self._identifier_from_node(child, source)
            if name is None:
                continue
            self._pending_inherits.append({
                "_source": source_name,
                "_target": name,
                "_position": position,
                "_line": line,
            })
            position += 1

    # ---- Reference extraction ----

    def _walk_refs(self, node, source, refs, scope_name):
        """walk AST for references with scope tracking."""
        current_scope = scope_name
        for child in node.children:
            if child.type == "file_scoped_namespace_declaration":
                ns_name = self._get_namespace_name(child, source)
                current_scope = f"{scope_name}.{ns_name}" if scope_name else ns_name
                self._walk_refs(child, source, refs, current_scope)
                continue
            if child.type == "using_directive":
                self._extract_using(child, source, refs, current_scope)
            elif child.type == "invocation_expression":
                self._extract_call(child, source, refs, current_scope)
            elif child.type == "object_creation_expression":
                self._extract_new(child, source, refs, current_scope)
            elif child.type == "attribute_list":
                self._extract_attributes(child, source, refs, current_scope)
            elif child.type == "nullable_type":
                self._extract_nullable_type_ref(child, source, refs, current_scope)
            elif child.type == "catch_declaration":
                self._extract_catch_type_ref(child, source, refs, current_scope)
            elif child.type == "typeof_expression":
                self._extract_typeof_ref(child, source, refs, current_scope)
            elif child.type == "is_pattern_expression":
                self._extract_is_pattern_ref(child, source, refs, current_scope)
            elif child.type in ("as_expression", "cast_expression"):
                self._extract_cast_type_ref(child, source, refs, current_scope)
            elif child.type == "throw_expression":
                self._walk_refs(child, source, refs, current_scope)
            else:
                new_scope = current_scope
                if child.type == "namespace_declaration":
                    ns_name = self._get_namespace_name(child, source)
                    new_scope = f"{current_scope}.{ns_name}" if current_scope else ns_name
                elif child.type in ("class_declaration", "interface_declaration",
                                    "struct_declaration", "enum_declaration",
                                    "record_declaration"):
                    n = child.child_by_field_name("name")
                    if n:
                        cname = self.node_text(n, source)
                        new_scope = f"{current_scope}.{cname}" if current_scope else cname
                elif child.type in ("method_declaration", "constructor_declaration",
                                    "local_function_statement",
                                    "destructor_declaration"):
                    n = child.child_by_field_name("name")
                    if n:
                        mname = self.node_text(n, source)
                        new_scope = f"{current_scope}.{mname}" if current_scope else mname
                self._walk_refs(child, source, refs, new_scope)

    def _extract_using(self, node, source, refs, scope_name):
        """extract using directive as import reference."""
        is_static = False
        alias_name = None
        import_path = None
        has_equals = any(c.type == "=" for c in node.children)

        for child in node.children:
            if child.type == "static":
                is_static = True
            elif child.type == "name_equals":
                # some grammar versions wrap alias in name_equals
                for gc in child.children:
                    if gc.type == "identifier":
                        alias_name = self.node_text(gc, source)
                        break
            elif child.type == "identifier":
                if has_equals and import_path is None and alias_name is None:
                    # bare identifier before = is the alias name
                    alias_name = self.node_text(child, source)
                else:
                    import_path = self.node_text(child, source)
            elif child.type in ("qualified_name", "generic_name"):
                import_path = self.node_text(child, source)

        if not import_path:
            return

        if alias_name:
            target = alias_name
        else:
            target = import_path.rsplit(".", 1)[-1] if "." in import_path else import_path

        refs.append(self._make_reference(
            target_name=target,
            kind="import",
            line=node.start_point[0] + 1,
            source_name=scope_name,
            import_path=import_path,
        ))

    def _identifier_from_node(self, node, source) -> str | None:
        """extract the simple identifier name from a node (unwraps generic_name)."""
        if node.type == "identifier":
            return self.node_text(node, source)
        if node.type == "generic_name":
            for gc in node.children:
                if gc.type == "identifier":
                    return self.node_text(gc, source)
        if node.type == "qualified_name":
            return self.node_text(node, source)
        return None

    def _extract_call(self, node, source, refs, scope_name):
        """extract method/function call reference."""
        target = None

        for child in node.children:
            if target is not None or not child.is_named or child.type == "argument_list":
                continue
            if child.type == "member_access_expression":
                name_node = child.child_by_field_name("name")
                target = self._identifier_from_node(name_node, source) if name_node else self.node_text(child, source)
            else:
                target = self._identifier_from_node(child, source)

        if target:
            refs.append(self._make_reference(
                target_name=target,
                kind="call",
                line=node.start_point[0] + 1,
                source_name=scope_name,
            ))

        # recurse into all children to catch chained calls and nested expressions
        for child in node.children:
            self._walk_refs(child, source, refs, scope_name)

    def _extract_new(self, node, source, refs, scope_name):
        """extract constructor call (new) reference."""
        target = None
        arg_list = None

        for child in node.children:
            if child.type == "argument_list":
                arg_list = child
            elif target is None:
                target = self._identifier_from_node(child, source)

        if target:
            refs.append(self._make_reference(
                target_name=target,
                kind="call",
                line=node.start_point[0] + 1,
                source_name=scope_name,
            ))

        if arg_list:
            self._walk_refs(arg_list, source, refs, scope_name)

    def _extract_attributes(self, node, source, refs, scope_name):
        """extract attribute references from attribute_list ([Attr1] [Attr2(args)])."""
        for child in node.children:
            if child.type == "attribute":
                name = next(
                    (self._identifier_from_node(ac, source)
                     for ac in child.children
                     if ac.type in ("identifier", "qualified_name", "generic_name")),
                    None,
                )
                if name:
                    refs.append(self._make_reference(
                        target_name=name,
                        kind="type_ref",
                        line=child.start_point[0] + 1,
                        source_name=scope_name,
                    ))

    def _extract_nullable_type_ref(self, node, source, refs, scope_name):
        """unwrap nullable_type (string?, List<int>?) and extract type reference."""
        for child in node.children:
            if child.type == "predefined_type":
                return  # skip builtins (int?, string?, bool?, etc.)
            if child.type == "identifier":
                refs.append(self._make_reference(
                    target_name=self.node_text(child, source),
                    kind="type_ref",
                    line=node.start_point[0] + 1,
                    source_name=scope_name,
                ))
                return
            if child.type == "generic_name":
                for gc in child.children:
                    if gc.type == "identifier":
                        refs.append(self._make_reference(
                            target_name=self.node_text(gc, source),
                            kind="type_ref",
                            line=node.start_point[0] + 1,
                            source_name=scope_name,
                        ))
                        return
            if child.type == "qualified_name":
                refs.append(self._make_reference(
                    target_name=self.node_text(child, source),
                    kind="type_ref",
                    line=node.start_point[0] + 1,
                    source_name=scope_name,
                ))
                return

    # ---- Additional type reference extraction ----

    _BUILTIN_TYPES = frozenset({
        "int", "string", "bool", "byte", "sbyte", "short", "ushort",
        "uint", "long", "ulong", "float", "double", "decimal", "char",
        "object", "void", "dynamic", "var", "nint", "nuint",
    })

    def _type_name_from_node(self, node, source) -> str | None:
        """extract a non-builtin type name from a type node."""
        if node is None:
            return None
        if node.type == "predefined_type":
            return None
        if node.type == "identifier":
            name = self.node_text(node, source)
            return None if name in self._BUILTIN_TYPES else name
        if node.type == "generic_name":
            for gc in node.children:
                if gc.type == "identifier":
                    name = self.node_text(gc, source)
                    return None if name in self._BUILTIN_TYPES else name
        if node.type == "qualified_name":
            return self.node_text(node, source)
        return None

    def _extract_catch_type_ref(self, node, source, refs, scope_name):
        """extract type reference from catch(ExceptionType e)."""
        type_node = node.child_by_field_name("type")
        name = self._type_name_from_node(type_node, source)
        if name:
            refs.append(self._make_reference(
                target_name=name,
                kind="type_ref",
                line=node.start_point[0] + 1,
                source_name=scope_name,
            ))

    def _extract_typeof_ref(self, node, source, refs, scope_name):
        """extract type reference from typeof(SomeType)."""
        for child in node.children:
            name = self._type_name_from_node(child, source)
            if name:
                refs.append(self._make_reference(
                    target_name=name,
                    kind="type_ref",
                    line=node.start_point[0] + 1,
                    source_name=scope_name,
                ))
                return

    def _extract_is_pattern_ref(self, node, source, refs, scope_name):
        """extract type reference from 'obj is MyType' pattern expressions."""
        # is_pattern_expression -> constant_pattern/declaration_pattern -> identifier
        for child in node.children:
            if child.type in ("constant_pattern", "declaration_pattern"):
                for gc in child.children:
                    name = self._type_name_from_node(gc, source)
                    if name:
                        refs.append(self._make_reference(
                            target_name=name,
                            kind="type_ref",
                            line=node.start_point[0] + 1,
                            source_name=scope_name,
                        ))
                        return
        # recurse for nested expressions
        self._walk_refs(node, source, refs, scope_name)

    def _extract_cast_type_ref(self, node, source, refs, scope_name):
        """extract type reference from as/cast expressions."""
        # as_expression: obj as MyType — type is the last type-like child
        # cast_expression: (MyType)obj — type is before the expression
        type_node = node.child_by_field_name("type")
        if type_node is None:
            # for as_expression: type is the last identifier/generic_name child
            last_type = None
            for child in node.children:
                if child.type in ("identifier", "generic_name", "qualified_name",
                                  "nullable_type"):
                    last_type = child
            type_node = last_type
        name = self._type_name_from_node(type_node, source)
        if name:
            refs.append(self._make_reference(
                target_name=name,
                kind="type_ref",
                line=node.start_point[0] + 1,
                source_name=scope_name,
            ))
