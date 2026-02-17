
from __future__ import annotations
from .base import LanguageExtractor


class PhpExtractor(LanguageExtractor):
    """PHP symbol and reference extractor.

    Handles namespaces, use statements, classes, interfaces, traits, enums,
    constructor promotion (PHP 8.0+), and visibility modifiers.
    """

    @property
    def language_name(self) -> str:
        return "php"

    @property
    def file_extensions(self) -> list[str]:
        return [".php"]

    def extract_symbols(self, tree, source: bytes, file_path: str) -> list[dict]:
        symbols = []
        self._current_namespace = None
        self._pending_inherits = []
        self._walk_symbols(tree.root_node, source, symbols, parent_name=None)
        return symbols

    def extract_references(self, tree, source: bytes, file_path: str) -> list[dict]:
        refs = []
        self._walk_refs(tree.root_node, source, refs, scope_name=None)
        refs.extend(getattr(self, '_pending_inherits', []))
        self._pending_inherits = []
        return refs

    def get_docstring(self, node, source: bytes) -> str | None:
        """PHPDoc: /** ... */ comment before node."""
        prev = node.prev_sibling
        if prev and prev.type == "comment":
            text = self.node_text(prev, source).strip()
            if text.startswith("/**"):
                text = text[3:]
                if text.endswith("*/"):
                    text = text[:-2]
                return text.strip()
        return None

    def _qualify(self, name: str) -> str:
        """Prepend current namespace to a name."""
        ns = getattr(self, '_current_namespace', None)
        if ns:
            return f"{ns}\\{name}"
        return name

    def _get_visibility(self, node, source) -> str:
        """Extract visibility modifier from a declaration's children."""
        for child in node.children:
            if child.type == "visibility_modifier":
                text = self.node_text(child, source)
                if "private" in text:
                    return "private"
                if "protected" in text:
                    return "protected"
                if "public" in text:
                    return "public"
        return "public"

    def _has_modifier(self, node, source, modifier: str) -> bool:
        for child in node.children:
            if child.type in ("static_modifier", "readonly_modifier", "abstract_modifier", "final_modifier"):
                if modifier in self.node_text(child, source):
                    return True
            if child.type == "visibility_modifier" and modifier in self.node_text(child, source):
                return True
        return False

    # ---- Symbol extraction ----

    def _walk_symbols(self, node, source, symbols, parent_name):
        for child in node.children:
            ntype = child.type
            if ntype == "namespace_definition":
                self._extract_namespace(child, source, symbols)
            elif ntype == "class_declaration":
                self._extract_class(child, source, symbols, parent_name, kind="class")
            elif ntype == "interface_declaration":
                self._extract_class(child, source, symbols, parent_name, kind="interface")
            elif ntype == "trait_declaration":
                self._extract_class(child, source, symbols, parent_name, kind="interface")
            elif ntype == "enum_declaration":
                self._extract_enum(child, source, symbols, parent_name)
            elif ntype == "function_definition":
                self._extract_function(child, source, symbols, parent_name)
            elif ntype == "method_declaration":
                self._extract_method(child, source, symbols, parent_name)
            elif ntype == "property_declaration":
                self._extract_property(child, source, symbols, parent_name)
            elif ntype == "const_declaration":
                self._extract_const(child, source, symbols, parent_name)
            elif ntype in ("declaration_list", "program", "compound_statement"):
                # Recurse into bodies
                self._walk_symbols(child, source, symbols, parent_name)

    def _extract_namespace(self, node, source, symbols):
        """Extract namespace and set as current context."""
        name_node = node.child_by_field_name("name")
        if name_node is None:
            return
        name = self.node_text(name_node, source)
        self._current_namespace = name

        symbols.append(self._make_symbol(
            name=name,
            kind="module",
            line_start=node.start_point[0] + 1,
            line_end=node.end_point[0] + 1,
            signature=f"namespace {name}",
            is_exported=True,
        ))

        # Walk the namespace body
        body = node.child_by_field_name("body")
        if body:
            self._walk_symbols(body, source, symbols, parent_name=None)
        else:
            # Namespace without braces — rest of file belongs to it
            for child in node.children:
                if child.type not in ("namespace", "name", "namespace_name", ";"):
                    self._walk_symbols(child, source, symbols, parent_name=None)

    def _extract_class(self, node, source, symbols, parent_name, kind="class"):
        name_node = node.child_by_field_name("name")
        if name_node is None:
            return
        name = self.node_text(name_node, source)
        vis = self._get_visibility(node, source)
        qualified = f"{parent_name}\\{name}" if parent_name else self._qualify(name)

        sig = f"{kind} {name}"

        # Base class (extends)
        for child in node.children:
            if child.type == "base_clause":
                sig += f" {self.node_text(child, source)}"
                self._collect_type_refs(child, source, "inherits", node.start_point[0] + 1, qualified)
            elif child.type == "class_interface_clause":
                sig += f" {self.node_text(child, source)}"
                self._collect_type_refs(child, source, "implements", node.start_point[0] + 1, qualified)

        is_abstract = self._has_modifier(node, source, "abstract")
        is_final = self._has_modifier(node, source, "final")
        if is_abstract:
            sig = "abstract " + sig
        if is_final:
            sig = "final " + sig

        symbols.append(self._make_symbol(
            name=name,
            kind=kind,
            line_start=node.start_point[0] + 1,
            line_end=node.end_point[0] + 1,
            qualified_name=qualified,
            signature=sig,
            docstring=self.get_docstring(node, source),
            visibility=vis,
            is_exported=True,
            parent_name=parent_name,
        ))

        # Walk class body
        body = node.child_by_field_name("body")
        if body:
            self._walk_symbols(body, source, symbols, qualified)
            # Also extract constructor-promoted properties
            self._extract_promoted_properties(body, source, symbols, qualified)

    def _extract_enum(self, node, source, symbols, parent_name):
        name_node = node.child_by_field_name("name")
        if name_node is None:
            return
        name = self.node_text(name_node, source)
        qualified = f"{parent_name}\\{name}" if parent_name else self._qualify(name)
        sig = f"enum {name}"

        # Backed enum type (e.g., enum Status: string)
        for child in node.children:
            if child.type == ":":
                # Next sibling is the type
                idx = node.children.index(child)
                if idx + 1 < len(node.children):
                    type_node = node.children[idx + 1]
                    sig += f": {self.node_text(type_node, source)}"
                break
            elif child.type == "class_interface_clause":
                sig += f" {self.node_text(child, source)}"
                self._collect_type_refs(child, source, "implements", node.start_point[0] + 1, qualified)

        symbols.append(self._make_symbol(
            name=name,
            kind="enum",
            line_start=node.start_point[0] + 1,
            line_end=node.end_point[0] + 1,
            qualified_name=qualified,
            signature=sig,
            docstring=self.get_docstring(node, source),
            visibility="public",
            is_exported=True,
            parent_name=parent_name,
        ))

        # Walk enum body for cases and methods
        body = node.child_by_field_name("body")
        if body:
            for child in body.children:
                if child.type == "enum_case":
                    self._extract_enum_case(child, source, symbols, qualified)
                elif child.type == "method_declaration":
                    self._extract_method(child, source, symbols, qualified)
                elif child.type == "const_declaration":
                    self._extract_const(child, source, symbols, qualified)
            # Also check for use_declaration (trait use inside enum)
            for child in body.children:
                if child.type == "use_declaration":
                    pass  # Handled in reference extraction

    def _extract_enum_case(self, node, source, symbols, parent_name):
        """Extract enum case as a constant."""
        name_node = node.child_by_field_name("name")
        if name_node is None:
            # Fallback: look for first identifier child
            for child in node.children:
                if child.type == "name":
                    name_node = child
                    break
        if name_node is None:
            return
        name = self.node_text(name_node, source)
        symbols.append(self._make_symbol(
            name=name,
            kind="constant",
            line_start=node.start_point[0] + 1,
            line_end=node.end_point[0] + 1,
            qualified_name=f"{parent_name}\\{name}",
            parent_name=parent_name,
            visibility="public",
            is_exported=True,
        ))

    def _extract_function(self, node, source, symbols, parent_name):
        """Extract top-level function."""
        name_node = node.child_by_field_name("name")
        if name_node is None:
            return
        name = self.node_text(name_node, source)
        params = node.child_by_field_name("parameters")
        ret = node.child_by_field_name("return_type")

        sig = f"function {name}({self._params_text(params, source)})"
        if ret:
            sig += f": {self.node_text(ret, source)}"

        qualified = f"{parent_name}\\{name}" if parent_name else self._qualify(name)
        symbols.append(self._make_symbol(
            name=name,
            kind="function",
            line_start=node.start_point[0] + 1,
            line_end=node.end_point[0] + 1,
            qualified_name=qualified,
            signature=sig,
            docstring=self.get_docstring(node, source),
            visibility="public",
            is_exported=True,
            parent_name=parent_name,
        ))

    def _extract_method(self, node, source, symbols, parent_name):
        name_node = node.child_by_field_name("name")
        if name_node is None:
            return
        name = self.node_text(name_node, source)
        vis = self._get_visibility(node, source)
        params = node.child_by_field_name("parameters")
        ret = node.child_by_field_name("return_type")

        sig = f"function {name}({self._params_text(params, source)})"
        if ret:
            sig += f": {self.node_text(ret, source)}"

        if self._has_modifier(node, source, "static"):
            sig = "static " + sig
        if self._has_modifier(node, source, "abstract"):
            sig = "abstract " + sig
        sig = f"{vis} {sig}"

        qualified = f"{parent_name}\\{name}" if parent_name else self._qualify(name)
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

    def _extract_property(self, node, source, symbols, parent_name):
        """Extract property declarations, stripping $ prefix."""
        vis = self._get_visibility(node, source)
        is_static = self._has_modifier(node, source, "static")
        is_readonly = self._has_modifier(node, source, "readonly")

        for child in node.children:
            if child.type == "property_element":
                var_node = child.child_by_field_name("name") or _find_child_type(child, "variable_name")
                if var_node is None:
                    continue
                raw_name = self.node_text(var_node, source)
                name = raw_name.lstrip("$")
                sig = f"{vis} {raw_name}"
                if is_static:
                    sig = "static " + sig
                if is_readonly:
                    sig = "readonly " + sig

                qualified = f"{parent_name}\\{name}" if parent_name else name
                symbols.append(self._make_symbol(
                    name=name,
                    kind="property",
                    line_start=node.start_point[0] + 1,
                    line_end=node.end_point[0] + 1,
                    qualified_name=qualified,
                    signature=sig,
                    visibility=vis,
                    is_exported=vis == "public",
                    parent_name=parent_name,
                ))
            elif child.type == "variable_name":
                raw_name = self.node_text(child, source)
                name = raw_name.lstrip("$")
                sig = f"{vis} {raw_name}"
                if is_static:
                    sig = "static " + sig
                if is_readonly:
                    sig = "readonly " + sig

                qualified = f"{parent_name}\\{name}" if parent_name else name
                symbols.append(self._make_symbol(
                    name=name,
                    kind="property",
                    line_start=node.start_point[0] + 1,
                    line_end=node.end_point[0] + 1,
                    qualified_name=qualified,
                    signature=sig,
                    visibility=vis,
                    is_exported=vis == "public",
                    parent_name=parent_name,
                ))

    def _extract_const(self, node, source, symbols, parent_name):
        """Extract class or file-level const declarations."""
        vis = self._get_visibility(node, source)
        for child in node.children:
            if child.type == "const_element":
                name_node = child.child_by_field_name("name") or _find_child_type(child, "name")
                if name_node is None:
                    continue
                name = self.node_text(name_node, source)
                qualified = f"{parent_name}\\{name}" if parent_name else self._qualify(name)
                symbols.append(self._make_symbol(
                    name=name,
                    kind="constant",
                    line_start=node.start_point[0] + 1,
                    line_end=node.end_point[0] + 1,
                    qualified_name=qualified,
                    signature=f"const {name}",
                    visibility=vis,
                    is_exported=vis == "public",
                    parent_name=parent_name,
                ))

    def _extract_promoted_properties(self, body, source, symbols, parent_name):
        """Extract constructor-promoted parameters (PHP 8.0+).

        In `__construct(private string $name)`, $name is both a param
        and a property.
        """
        for child in body.children:
            if child.type != "method_declaration":
                continue
            name_node = child.child_by_field_name("name")
            if name_node is None or self.node_text(name_node, source) != "__construct":
                continue
            params = child.child_by_field_name("parameters")
            if params is None:
                continue
            for param in params.children:
                if param.type not in ("simple_parameter", "property_promotion_parameter"):
                    continue
                # Check if this parameter has a visibility modifier (promoted)
                has_vis = False
                vis = "public"
                for sub in param.children:
                    if sub.type == "visibility_modifier":
                        has_vis = True
                        vis = self.node_text(sub, source)
                        break
                if not has_vis:
                    continue
                # Find variable name
                var_node = None
                for sub in param.children:
                    if sub.type == "variable_name":
                        var_node = sub
                        break
                if var_node is None:
                    continue
                raw_name = self.node_text(var_node, source)
                name = raw_name.lstrip("$")
                qualified = f"{parent_name}\\{name}"
                symbols.append(self._make_symbol(
                    name=name,
                    kind="property",
                    line_start=param.start_point[0] + 1,
                    line_end=param.end_point[0] + 1,
                    qualified_name=qualified,
                    signature=f"promoted {raw_name}",
                    visibility=vis,
                    is_exported=vis == "public",
                    parent_name=parent_name,
                ))

    def _collect_type_refs(self, node, source, kind, line, source_name):
        """Recursively collect name/qualified_name nodes as references."""
        for child in node.children:
            if child.type in ("name", "qualified_name"):
                target = self.node_text(child, source)
                # Use last segment for matching
                short = target.rsplit("\\", 1)[-1] if "\\" in target else target
                self._pending_inherits.append(self._make_reference(
                    target_name=short,
                    kind=kind,
                    line=line,
                    source_name=source_name,
                ))
            else:
                self._collect_type_refs(child, source, kind, line, source_name)

    # ---- Reference extraction ----

    def _walk_refs(self, node, source, refs, scope_name):
        for child in node.children:
            ntype = child.type
            if ntype == "namespace_use_declaration":
                self._extract_use_import(child, source, refs, scope_name)
            elif ntype == "use_declaration":
                # Trait use inside class body: use HasFactory, SoftDeletes;
                self._extract_trait_use(child, source, refs, scope_name)
            elif ntype == "member_call_expression":
                self._extract_member_call(child, source, refs, scope_name)
            elif ntype == "nullsafe_member_call_expression":
                self._extract_member_call(child, source, refs, scope_name)
            elif ntype == "nullsafe_member_access_expression":
                self._extract_member_call(child, source, refs, scope_name)
            elif ntype == "scoped_call_expression":
                self._extract_scoped_call(child, source, refs, scope_name)
            elif ntype == "object_creation_expression":
                self._extract_new(child, source, refs, scope_name)
            elif ntype == "function_call_expression":
                self._extract_function_call(child, source, refs, scope_name)
            else:
                new_scope = scope_name
                if ntype == "namespace_definition":
                    n = child.child_by_field_name("name")
                    if n:
                        new_scope = self.node_text(n, source)
                elif ntype in ("class_declaration", "interface_declaration",
                               "trait_declaration", "enum_declaration"):
                    n = child.child_by_field_name("name")
                    if n:
                        cname = self.node_text(n, source)
                        new_scope = f"{scope_name}\\{cname}" if scope_name else cname
                elif ntype == "method_declaration":
                    n = child.child_by_field_name("name")
                    if n:
                        mname = self.node_text(n, source)
                        new_scope = f"{scope_name}\\{mname}" if scope_name else mname
                elif ntype == "function_definition":
                    n = child.child_by_field_name("name")
                    if n:
                        fname = self.node_text(n, source)
                        new_scope = f"{scope_name}\\{fname}" if scope_name else fname
                self._walk_refs(child, source, refs, new_scope)

    def _use_clause_target(self, clause_node, source):
        """Extract (target_name, import_path) from a namespace_use_clause."""
        name_node = _find_child_type(clause_node, "qualified_name") or _find_child_type(clause_node, "name")
        if name_node is None:
            return None, None
        path = self.node_text(name_node, source)
        target = path.rsplit("\\", 1)[-1] if "\\" in path else path
        alias_node = _find_child_type(clause_node, "namespace_aliasing_clause")
        if alias_node:
            for sub in alias_node.children:
                if sub.type == "name":
                    target = self.node_text(sub, source)
                    break
        return target, path

    def _extract_use_import(self, node, source, refs, scope_name):
        """Extract `use App\\Models\\User;` and `use App\\Models\\{User, Post};`."""
        line = node.start_point[0] + 1
        for child in node.children:
            if child.type == "namespace_use_clause":
                target, path = self._use_clause_target(child, source)
                if target:
                    refs.append(self._make_reference(
                        target_name=target, kind="import", line=line,
                        source_name=scope_name, import_path=path,
                    ))
            elif child.type == "namespace_use_group":
                prefix = ""
                for sub in node.children:
                    if sub.type in ("qualified_name", "name"):
                        prefix = self.node_text(sub, source)
                        break
                for sub in child.children:
                    if sub.type == "namespace_use_clause":
                        target, short_path = self._use_clause_target(sub, source)
                        if target:
                            full_path = f"{prefix}\\{short_path}" if prefix else (short_path or "")
                            refs.append(self._make_reference(
                                target_name=target, kind="import", line=line,
                                source_name=scope_name, import_path=full_path,
                            ))
            elif child.type in ("qualified_name", "name"):
                path = self.node_text(child, source)
                target = path.rsplit("\\", 1)[-1] if "\\" in path else path
                refs.append(self._make_reference(
                    target_name=target, kind="import", line=line,
                    source_name=scope_name, import_path=path,
                ))

    def _extract_trait_use(self, node, source, refs, scope_name):
        """Extract `use HasFactory, SoftDeletes;` inside class body."""
        for child in node.children:
            if child.type in ("name", "qualified_name"):
                target = self.node_text(child, source)
                short = target.rsplit("\\", 1)[-1] if "\\" in target else target
                refs.append(self._make_reference(
                    target_name=short,
                    kind="uses_trait",
                    line=node.start_point[0] + 1,
                    source_name=scope_name,
                ))

    def _extract_member_call(self, node, source, refs, scope_name):
        """Extract $obj->method() calls."""
        name_node = node.child_by_field_name("name")
        if name_node is None:
            return
        name = self.node_text(name_node, source)
        refs.append(self._make_reference(
            target_name=name,
            kind="call",
            line=node.start_point[0] + 1,
            source_name=scope_name,
        ))
        # Recurse into arguments
        args = node.child_by_field_name("arguments")
        if args:
            self._walk_refs(args, source, refs, scope_name)

    def _extract_scoped_call(self, node, source, refs, scope_name):
        """Extract ClassName::method() static calls."""
        scope_node = node.child_by_field_name("scope")
        name_node = node.child_by_field_name("name")
        if name_node is None:
            return
        name = self.node_text(name_node, source)
        if scope_node:
            scope_text = self.node_text(scope_node, source)
            if scope_text not in ("self", "static", "parent"):
                name = f"{scope_text}.{name}"
        refs.append(self._make_reference(
            target_name=name,
            kind="call",
            line=node.start_point[0] + 1,
            source_name=scope_name,
        ))
        args = node.child_by_field_name("arguments")
        if args:
            self._walk_refs(args, source, refs, scope_name)

    def _extract_new(self, node, source, refs, scope_name):
        """Extract `new ClassName()` constructor calls."""
        # tree-sitter-php uses child[1] for the class name typically
        for child in node.children:
            if child.type in ("name", "qualified_name"):
                target = self.node_text(child, source)
                short = target.rsplit("\\", 1)[-1] if "\\" in target else target
                refs.append(self._make_reference(
                    target_name=short,
                    kind="call",
                    line=node.start_point[0] + 1,
                    source_name=scope_name,
                ))
                break
        args = node.child_by_field_name("arguments")
        if args:
            self._walk_refs(args, source, refs, scope_name)

    def _extract_function_call(self, node, source, refs, scope_name):
        """Extract free function calls like helper(), view(), etc."""
        fn_node = node.child_by_field_name("function")
        if fn_node is None:
            return
        name = self.node_text(fn_node, source)
        # Skip variable function calls like $callback()
        if name.startswith("$"):
            return
        short = name.rsplit("\\", 1)[-1] if "\\" in name else name
        refs.append(self._make_reference(
            target_name=short,
            kind="call",
            line=node.start_point[0] + 1,
            source_name=scope_name,
        ))
        args = node.child_by_field_name("arguments")
        if args:
            self._walk_refs(args, source, refs, scope_name)


def _find_child_type(node, type_name: str):
    """Find first child of a given type."""
    for child in node.children:
        if child.type == type_name:
            return child
    return None
