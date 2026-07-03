from __future__ import annotations

from .base import LanguageExtractor


class CExtractor(LanguageExtractor):
    """C/C++ symbol and reference extractor."""

    @property
    def language_name(self) -> str:
        return "c"

    @property
    def file_extensions(self) -> list[str]:
        return [".c", ".h"]

    def extract_symbols(self, tree, source: bytes, file_path: str) -> list[dict]:
        symbols = []
        is_header = file_path.endswith(".h") or file_path.endswith(".hpp")
        self._walk_symbols(tree.root_node, source, symbols, parent_name=None, is_header=is_header)
        return symbols

    def extract_references(self, tree, source: bytes, file_path: str) -> list[dict]:
        refs = []
        self._walk_refs(tree.root_node, source, refs, scope_name=None)
        return refs

    def get_docstring(self, node, source: bytes) -> str | None:
        """C-style doc comment: /* ... */ or // before node."""
        prev = node.prev_sibling
        comments = []
        while prev and prev.type == "comment":
            text = self.node_text(prev, source).strip()
            if text.startswith("/*"):
                text = text[2:]
                if text.endswith("*/"):
                    text = text[:-2]
                comments.insert(0, text.strip())
            elif text.startswith("//"):
                comments.insert(0, text[2:].strip())
            else:
                break
            prev = prev.prev_sibling
        return "\n".join(comments) if comments else None

    # ---- Symbol extraction ----

    def _add_symbol(
        self,
        symbols,
        node,
        name,
        kind,
        parent_name,
        *,
        signature=None,
        docstring=None,
        is_exported=False,
    ) -> str:
        """Record `node` as one symbol in the enclosing scope.

        Owns the two invariants every extractor must agree on — `::`
        qualification against the parent scope and the tree-sitter
        0-based -> 1-based line convention — so they are stated once
        instead of per extractor. Returns the qualified name for
        extractors that scope children under it.
        """
        qualified = f"{parent_name}::{name}" if parent_name else name
        symbols.append(
            self._make_symbol(
                name=name,
                kind=kind,
                line_start=node.start_point[0] + 1,
                line_end=node.end_point[0] + 1,
                qualified_name=qualified,
                signature=signature,
                docstring=docstring,
                is_exported=is_exported,
                parent_name=parent_name,
            )
        )
        return qualified

    def _walk_symbols(self, node, source, symbols, parent_name, is_header):
        for child in node.children:
            if child.type == "function_definition":
                self._extract_function(child, source, symbols, parent_name, is_header)
            elif child.type == "declaration":
                self._extract_declaration(child, source, symbols, parent_name, is_header)
            elif child.type == "struct_specifier":
                self._extract_struct(child, source, symbols, parent_name, is_header, kind="struct")
            elif child.type == "union_specifier":
                self._extract_struct(child, source, symbols, parent_name, is_header, kind="struct")
            elif child.type == "enum_specifier":
                self._extract_enum(child, source, symbols, parent_name, is_header)
            elif child.type == "type_definition":
                self._extract_typedef(child, source, symbols, parent_name, is_header)
            elif child.type == "namespace_definition":
                # C++ namespace
                self._extract_namespace(child, source, symbols, is_header)
            elif child.type == "class_specifier":
                # C++ class
                self._extract_cpp_class(child, source, symbols, parent_name, is_header)
            elif child.type == "template_declaration":
                # Process template contents
                self._walk_symbols(child, source, symbols, parent_name, is_header)

    def _extract_function(self, node, source, symbols, parent_name, is_header):
        declarator = node.child_by_field_name("declarator")
        if declarator is None:
            return

        name, params_text = self._parse_function_declarator(declarator, source)
        if name is None:
            return

        ret_type = node.child_by_field_name("type")
        ret_text = self.node_text(ret_type, source) if ret_type else ""
        sig = f"{ret_text} {name}({params_text})"

        self._add_symbol(
            symbols,
            node,
            name,
            "function",
            parent_name,
            signature=sig.strip(),
            docstring=self.get_docstring(node, source),
            is_exported=is_header,
        )

    def _parse_function_declarator(self, declarator, source) -> tuple[str | None, str]:
        """Extract function name and parameters from a declarator."""
        if declarator.type == "function_declarator":
            name_node = declarator.child_by_field_name("declarator")
            params = declarator.child_by_field_name("parameters")
            name = None
            if name_node:
                if name_node.type == "identifier":
                    name = self.node_text(name_node, source)
                elif name_node.type == "qualified_identifier":
                    name = self.node_text(name_node, source)
                elif name_node.type == "field_identifier":
                    name = self.node_text(name_node, source)
                elif name_node.type == "parenthesized_declarator":
                    # (*funcptr)(...)
                    name = self.node_text(name_node, source)
                else:
                    name = self.node_text(name_node, source)
            params_text = self.node_text(params, source) if params else ""
            # Strip outer parens from params
            if params_text.startswith("(") and params_text.endswith(")"):
                params_text = params_text[1:-1]
            return name, params_text
        elif declarator.type == "pointer_declarator":
            for child in declarator.children:
                if child.type == "function_declarator":
                    return self._parse_function_declarator(child, source)
            return None, ""
        elif declarator.type == "reference_declarator":
            for child in declarator.children:
                if child.type == "function_declarator":
                    return self._parse_function_declarator(child, source)
            return None, ""
        return None, ""

    def _extract_declaration(self, node, source, symbols, parent_name, is_header):
        """Extract variable/function declarations (not definitions)."""
        type_node = node.child_by_field_name("type")
        if type_node is None:
            return

        type_text = self.node_text(type_node, source)

        for child in node.children:
            if child.type == "function_declarator":
                # Function prototype
                name, params_text = self._parse_function_declarator(child, source)
                if name:
                    sig = f"{type_text} {name}({params_text})"
                    self._add_symbol(
                        symbols,
                        node,
                        name,
                        "function",
                        parent_name,
                        signature=sig.strip(),
                        docstring=self.get_docstring(node, source),
                        is_exported=is_header,
                    )
            elif child.type == "init_declarator":
                decl = child.child_by_field_name("declarator")
                if decl and decl.type == "identifier":
                    name = self.node_text(decl, source)
                    self._add_symbol(
                        symbols,
                        node,
                        name,
                        "variable",
                        parent_name,
                        signature=f"{type_text} {name}",
                        is_exported=is_header,
                    )
            elif child.type == "identifier":
                name = self.node_text(child, source)
                self._add_symbol(
                    symbols,
                    node,
                    name,
                    "variable",
                    parent_name,
                    signature=f"{type_text} {name}",
                    is_exported=is_header,
                )

    def _extract_struct(self, node, source, symbols, parent_name, is_header, kind="struct"):
        name_node = node.child_by_field_name("name")
        if name_node is None:
            return
        name = self.node_text(name_node, source)
        qualified = self._add_symbol(
            symbols,
            node,
            name,
            "struct",
            parent_name,
            signature=f"{kind} {name}",
            docstring=self.get_docstring(node, source),
            is_exported=is_header,
        )

        # Extract fields
        body = node.child_by_field_name("body")
        if body:
            for child in body.children:
                if child.type == "field_declaration":
                    self._extract_struct_field(child, source, symbols, qualified)

    def _extract_struct_field(self, node, source, symbols, struct_name):
        type_node = node.child_by_field_name("type")
        type_text = self.node_text(type_node, source) if type_node else ""
        for child in node.children:
            if child.type == "field_identifier":
                field_name = self.node_text(child, source)
                self._add_symbol(
                    symbols,
                    node,
                    field_name,
                    "field",
                    struct_name,
                    signature=f"{type_text} {field_name}",
                )

    def _extract_enum(self, node, source, symbols, parent_name, is_header):
        name_node = node.child_by_field_name("name")
        name = self.node_text(name_node, source) if name_node else None
        if name is None:
            return
        qualified = self._add_symbol(
            symbols,
            node,
            name,
            "enum",
            parent_name,
            signature=f"enum {name}",
            docstring=self.get_docstring(node, source),
            is_exported=is_header,
        )

        # Extract enumerators
        body = node.child_by_field_name("body")
        if body:
            for child in body.children:
                if child.type == "enumerator":
                    en = child.child_by_field_name("name")
                    if en:
                        en_name = self.node_text(en, source)
                        self._add_symbol(
                            symbols,
                            child,
                            en_name,
                            "constant",
                            qualified,
                            is_exported=is_header,
                        )

    def _resolve_typedef_alias_without_losing_fallbacks(self, node, source) -> tuple[str, str, bool] | None:
        """Preserve precise typedef aliases while keeping grammar fallback coverage."""
        declarator = node.child_by_field_name("declarator")
        if declarator:
            name = self.node_text(declarator, source).strip("*[] ")
            if not name:
                return None
            type_node = node.child_by_field_name("type")
            type_text = self.node_text(type_node, source) if type_node else ""
            return name, f"typedef {type_text} {name}".strip(), True

        type_id = None
        for child in node.children:
            if child.type == "type_identifier":
                type_id = child
        if type_id is None:
            return None

        name = self.node_text(type_id, source)
        return name, f"typedef ... {name}", False

    def _add_typedef_alias_with_docstring_scope(self, symbols, node, source, parent_name, is_header, resolved) -> None:
        name, signature, include_docstring = resolved
        self._add_symbol(
            symbols,
            node,
            name,
            "type_alias",
            parent_name,
            signature=signature,
            docstring=self.get_docstring(node, source) if include_docstring else None,
            is_exported=is_header,
        )

    def _extract_typedef(self, node, source, symbols, parent_name, is_header):
        if resolved := self._resolve_typedef_alias_without_losing_fallbacks(node, source):
            self._add_typedef_alias_with_docstring_scope(symbols, node, source, parent_name, is_header, resolved)

    def _extract_namespace(self, node, source, symbols, is_header):
        name_node = node.child_by_field_name("name")
        if name_node is None:
            return
        name = self.node_text(name_node, source)
        self._add_symbol(
            symbols,
            node,
            name,
            "module",
            None,
            signature=f"namespace {name}",
            is_exported=True,
        )

        body = node.child_by_field_name("body")
        if body:
            self._walk_symbols(body, source, symbols, parent_name=name, is_header=is_header)

    def _extract_cpp_class(self, node, source, symbols, parent_name, is_header):
        name_node = node.child_by_field_name("name")
        if name_node is None:
            return
        name = self.node_text(name_node, source)
        qualified = self._add_symbol(
            symbols,
            node,
            name,
            "class",
            parent_name,
            signature=f"class {name}",
            docstring=self.get_docstring(node, source),
            is_exported=is_header,
        )

        body = node.child_by_field_name("body")
        if body:
            self._walk_symbols(body, source, symbols, parent_name=qualified, is_header=is_header)

    # ---- Reference extraction ----

    def _walk_refs(self, node, source, refs, scope_name):
        for child in node.children:
            if child.type == "preproc_include":
                self._extract_include(child, source, refs, scope_name)
            elif child.type == "call_expression":
                self._extract_call(child, source, refs, scope_name)
            else:
                new_scope = scope_name
                if child.type == "function_definition":
                    decl = child.child_by_field_name("declarator")
                    if decl:
                        name, _ = self._parse_function_declarator(decl, source)
                        if name:
                            new_scope = name
                self._walk_refs(child, source, refs, new_scope)

    def _extract_include(self, node, source, refs, scope_name):
        path_node = node.child_by_field_name("path")
        if path_node:
            path = self.node_text(path_node, source).strip('<>"')
            refs.append(
                self._make_reference(
                    target_name=path,
                    kind="import",
                    line=node.start_point[0] + 1,
                    source_name=scope_name,
                    import_path=path,
                )
            )
        else:
            # Fallback: look for string_literal or system_lib_string
            for child in node.children:
                if child.type in ("string_literal", "system_lib_string"):
                    path = self.node_text(child, source).strip('<>"')
                    refs.append(
                        self._make_reference(
                            target_name=path,
                            kind="import",
                            line=node.start_point[0] + 1,
                            source_name=scope_name,
                            import_path=path,
                        )
                    )
                    break

    def _extract_call(self, node, source, refs, scope_name):
        func_node = node.child_by_field_name("function")
        if func_node is None:
            return
        name = self.node_text(func_node, source)
        refs.append(
            self._make_reference(
                target_name=name,
                kind="call",
                line=func_node.start_point[0] + 1,
                source_name=scope_name,
            )
        )
        args = node.child_by_field_name("arguments")
        if args:
            self._walk_refs(args, source, refs, scope_name)


class CppExtractor(CExtractor):
    """C++ extractor extending C with C++ specifics."""

    @property
    def language_name(self) -> str:
        return "cpp"

    @property
    def file_extensions(self) -> list[str]:
        return [".cpp", ".cxx", ".cc", ".hpp", ".hxx", ".hh", ".h"]
