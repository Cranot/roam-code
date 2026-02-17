
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

    def _get_generic_signature(self, node, source) -> str:
        """build generic type parameters + constraints string."""
        parts = ""
        for child in node.children:
            if child.type == "type_parameter_list":
                parts += self.node_text(child, source)
        constraints = []
        for child in node.children:
            if child.type == "type_parameter_constraints_clause":
                constraints.append(self.node_text(child, source))
        if constraints:
            constraint_text = " ".join(constraints)
            if len(constraint_text) > 200:
                constraint_text = constraint_text[:200] + "..."
            parts += " " + constraint_text
        return parts

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

    def _extract_class(self, node, source, symbols, parent_name, kind="class"):
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
        mod_prefix = " ".join(class_mods)
        sig = f"{mod_prefix} {kind} {name}" if mod_prefix else f"{kind} {name}"

        # generics
        generic_sig = self._get_generic_signature(node, source)
        if generic_sig:
            sig += generic_sig

        # base_list (superclass + interfaces combined)
        for child in node.children:
            if child.type == "base_list":
                sig += f" {self.node_text(child, source)}"
                self._collect_base_list_refs(child, source, node.start_point[0] + 1, qualified)
                break

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

        generic_sig = self._get_generic_signature(node, source)
        if generic_sig:
            sig += generic_sig

        sig += f"({self._params_text(params, source)})"

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

    def _extract_field(self, node, source, symbols, parent_name):
        """extract field declarations with c# variable_declaration nesting."""
        parent_kind = self._symbol_kinds.get(parent_name) if parent_name else None
        vis = self._get_visibility(node, source, parent_kind)
        is_static = self._has_modifier(node, source, "static")
        is_readonly = self._has_modifier(node, source, "readonly")
        is_const = self._has_modifier(node, source, "const")

        # c# fields: field_declaration -> variable_declaration -> variable_declarator
        for child in node.children:
            if child.type == "variable_declaration":
                type_node = child.child_by_field_name("type")
                type_text = self.node_text(type_node, source) if type_node else ""
                for var_child in child.children:
                    if var_child.type == "variable_declarator":
                        name_node = var_child.child_by_field_name("name")
                        if name_node is None:
                            for vc in var_child.children:
                                if vc.type == "identifier":
                                    name_node = vc
                                    break
                        if name_node is None:
                            continue
                        name = self.node_text(name_node, source)
                        kind = "constant" if is_const or (is_static and is_readonly) else "field"
                        sig = ""
                        if is_static:
                            sig += "static "
                        if is_readonly:
                            sig += "readonly "
                        if is_const:
                            sig += "const "
                        sig += f"{type_text} {name}"

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

    def _collect_base_list_refs(self, base_list, source, line, source_name):
        """accumulate base_list entries as pending inheritance refs."""
        position = 0
        for child in base_list.children:
            if not child.is_named:
                continue
            if child.type == "identifier":
                name = self.node_text(child, source)
            elif child.type == "generic_name":
                id_node = None
                for gc in child.children:
                    if gc.type == "identifier":
                        id_node = gc
                        break
                name = self.node_text(id_node, source) if id_node else self.node_text(child, source)
            elif child.type == "qualified_name":
                name = self.node_text(child, source)
            else:
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
                continue
            new_scope = current_scope
            if child.type == "namespace_declaration":
                ns_name = self._get_namespace_name(child, source)
                new_scope = f"{current_scope}.{ns_name}" if current_scope else ns_name
            elif child.type in ("class_declaration", "interface_declaration",
                                "struct_declaration", "enum_declaration"):
                n = child.child_by_field_name("name")
                if n:
                    cname = self.node_text(n, source)
                    new_scope = f"{current_scope}.{cname}" if current_scope else cname
            elif child.type in ("method_declaration", "constructor_declaration"):
                n = child.child_by_field_name("name")
                if n:
                    mname = self.node_text(n, source)
                    new_scope = f"{current_scope}.{mname}" if current_scope else mname
            self._walk_refs(child, source, refs, new_scope)
