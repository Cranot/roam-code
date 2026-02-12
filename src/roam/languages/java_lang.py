
from __future__ import annotations
from .base import LanguageExtractor


class JavaExtractor(LanguageExtractor):
    """Java symbol and reference extractor."""

    @property
    def language_name(self) -> str:
        return "java"

    @property
    def file_extensions(self) -> list[str]:
        return [".java"]

    def extract_symbols(self, tree, source: bytes, file_path: str) -> list[dict]:
        symbols = []
        self._pending_inherits = []
        self._walk_symbols(tree.root_node, source, symbols, parent_name=None)
        return symbols

    def extract_references(self, tree, source: bytes, file_path: str) -> list[dict]:
        refs = []
        self._walk_refs(tree.root_node, source, refs, scope_name=None)
        # Collect inheritance refs accumulated during extract_symbols
        refs.extend(getattr(self, '_pending_inherits', []))
        self._pending_inherits = []
        return refs

    def get_docstring(self, node, source: bytes) -> str | None:
        """Javadoc: /** ... */ comment before node."""
        prev = node.prev_sibling
        if prev and prev.type in ("block_comment", "comment"):
            text = self.node_text(prev, source).strip()
            if text.startswith("/**"):
                text = text[3:]
                if text.endswith("*/"):
                    text = text[:-2]
                return text.strip()
        return None

    def _get_visibility(self, node, source) -> str:
        """Extract access modifier from modifiers node."""
        for child in node.children:
            if child.type == "modifiers":
                text = self.node_text(child, source)
                if "private" in text:
                    return "private"
                if "protected" in text:
                    return "protected"
                if "public" in text:
                    return "public"
        return "package"

    def _get_annotations(self, node, source) -> list[str]:
        """Extract annotations from modifiers."""
        annotations = []
        for child in node.children:
            if child.type == "modifiers":
                for sub in child.children:
                    if sub.type in ("annotation", "marker_annotation"):
                        annotations.append(self.node_text(sub, source))
        return annotations

    def _has_modifier(self, node, source, modifier: str) -> bool:
        for child in node.children:
            if child.type == "modifiers":
                return modifier in self.node_text(child, source)
        return False

    # ---- Symbol extraction ----

    def _walk_symbols(self, node, source, symbols, parent_name):
        for child in node.children:
            if child.type == "class_declaration":
                self._extract_class(child, source, symbols, parent_name, kind="class")
            elif child.type == "interface_declaration":
                self._extract_class(child, source, symbols, parent_name, kind="interface")
            elif child.type == "enum_declaration":
                self._extract_enum(child, source, symbols, parent_name)
            elif child.type == "record_declaration":
                self._extract_class(child, source, symbols, parent_name, kind="class")
            elif child.type == "annotation_type_declaration":
                self._extract_class(child, source, symbols, parent_name, kind="interface")
            elif child.type == "method_declaration":
                self._extract_method(child, source, symbols, parent_name)
            elif child.type == "constructor_declaration":
                self._extract_constructor(child, source, symbols, parent_name)
            elif child.type == "field_declaration":
                self._extract_field(child, source, symbols, parent_name)
            elif child.type == "package_declaration":
                self._extract_package(child, source, symbols)

    def _extract_class(self, node, source, symbols, parent_name, kind="class"):
        name_node = node.child_by_field_name("name")
        if name_node is None:
            return
        name = self.node_text(name_node, source)
        vis = self._get_visibility(node, source)
        annotations = self._get_annotations(node, source)

        qualified = f"{parent_name}.{name}" if parent_name else name
        sig = f"{kind} {name}"
        type_params = node.child_by_field_name("type_parameters")
        if type_params:
            sig += self.node_text(type_params, source)

        # Check superclass
        superclass = node.child_by_field_name("superclass")
        if superclass:
            sig += f" {self.node_text(superclass, source)}"
            # Emit inherits reference
            for child in superclass.children:
                if child.type == "type_identifier":
                    self._pending_inherits.append(self._make_reference(
                        target_name=self.node_text(child, source),
                        kind="inherits",
                        line=node.start_point[0] + 1,
                        source_name=qualified,
                    ))
                    break

        # Check interfaces
        interfaces = node.child_by_field_name("interfaces")
        if interfaces:
            sig += f" {self.node_text(interfaces, source)}"
            # Emit implements references (type_identifiers may be nested in type_list)
            self._collect_type_refs(interfaces, source, "implements", node.start_point[0] + 1, qualified)

        if annotations:
            sig = "\n".join(annotations) + "\n" + sig

        symbols.append(self._make_symbol(
            name=name,
            kind=kind,
            line_start=node.start_point[0] + 1,
            line_end=node.end_point[0] + 1,
            qualified_name=qualified,
            signature=sig,
            docstring=self.get_docstring(node, source),
            visibility=vis,
            is_exported=vis == "public",
            parent_name=parent_name,
        ))

        # Walk class body
        body = node.child_by_field_name("body")
        if body:
            self._walk_symbols(body, source, symbols, qualified)

    def _extract_enum(self, node, source, symbols, parent_name):
        name_node = node.child_by_field_name("name")
        if name_node is None:
            return
        name = self.node_text(name_node, source)
        vis = self._get_visibility(node, source)
        sig = f"enum {name}"

        qualified = f"{parent_name}.{name}" if parent_name else name
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

        # Walk enum body for constants and methods
        body = node.child_by_field_name("body")
        if body:
            for child in body.children:
                if child.type == "enum_constant":
                    cn = child.child_by_field_name("name")
                    if cn:
                        const_name = self.node_text(cn, source)
                        symbols.append(self._make_symbol(
                            name=const_name,
                            kind="constant",
                            line_start=child.start_point[0] + 1,
                            line_end=child.end_point[0] + 1,
                            qualified_name=f"{qualified}.{const_name}",
                            parent_name=qualified,
                            visibility=vis,
                            is_exported=vis == "public",
                        ))
                elif child.type == "enum_body_declarations":
                    self._walk_symbols(child, source, symbols, qualified)

    def _extract_method(self, node, source, symbols, parent_name):
        name_node = node.child_by_field_name("name")
        if name_node is None:
            return
        name = self.node_text(name_node, source)
        vis = self._get_visibility(node, source)
        annotations = self._get_annotations(node, source)

        # Build signature
        ret_type = node.child_by_field_name("type")
        params = node.child_by_field_name("parameters")
        type_params = node.child_by_field_name("type_parameters")

        sig = ""
        if type_params:
            sig += self.node_text(type_params, source) + " "
        if ret_type:
            sig += self.node_text(ret_type, source) + " "
        sig += f"{name}({self._params_text(params, source)})"

        # Throws
        for child in node.children:
            if child.type == "throws":
                sig += f" {self.node_text(child, source)}"

        if self._has_modifier(node, source, "static"):
            sig = "static " + sig

        if annotations:
            sig = "\n".join(annotations) + "\n" + sig

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
        vis = self._get_visibility(node, source)
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
        """Extract field declarations."""
        vis = self._get_visibility(node, source)
        type_node = node.child_by_field_name("type")
        type_text = self.node_text(type_node, source) if type_node else ""
        is_static = self._has_modifier(node, source, "static")
        is_final = self._has_modifier(node, source, "final")

        for child in node.children:
            if child.type == "variable_declarator":
                name_node = child.child_by_field_name("name")
                if name_node:
                    name = self.node_text(name_node, source)
                    kind = "constant" if is_static and is_final else "field"
                    sig = f"{type_text} {name}"
                    if is_static:
                        sig = "static " + sig
                    if is_final:
                        sig = "final " + sig

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

    def _collect_type_refs(self, node, source, kind, line, source_name):
        """Recursively collect type_identifier nodes as references."""
        for child in node.children:
            if child.type == "type_identifier":
                self._pending_inherits.append(self._make_reference(
                    target_name=self.node_text(child, source),
                    kind=kind,
                    line=line,
                    source_name=source_name,
                ))
            else:
                self._collect_type_refs(child, source, kind, line, source_name)

    def _extract_package(self, node, source, symbols):
        for child in node.children:
            if child.type == "scoped_identifier" or child.type == "identifier":
                name = self.node_text(child, source)
                symbols.append(self._make_symbol(
                    name=name,
                    kind="module",
                    line_start=node.start_point[0] + 1,
                    line_end=node.end_point[0] + 1,
                    signature=f"package {name}",
                    is_exported=True,
                ))
                break

    # ---- Reference extraction ----

    def _walk_refs(self, node, source, refs, scope_name):
        for child in node.children:
            if child.type == "import_declaration":
                self._extract_import(child, source, refs, scope_name)
            elif child.type == "method_invocation":
                self._extract_method_call(child, source, refs, scope_name)
            elif child.type == "object_creation_expression":
                self._extract_new(child, source, refs, scope_name)
            else:
                new_scope = scope_name
                if child.type in ("class_declaration", "interface_declaration", "enum_declaration"):
                    n = child.child_by_field_name("name")
                    if n:
                        cname = self.node_text(n, source)
                        new_scope = f"{scope_name}.{cname}" if scope_name else cname
                elif child.type in ("method_declaration", "constructor_declaration"):
                    n = child.child_by_field_name("name")
                    if n:
                        mname = self.node_text(n, source)
                        new_scope = f"{scope_name}.{mname}" if scope_name else mname
                self._walk_refs(child, source, refs, new_scope)

    def _extract_import(self, node, source, refs, scope_name):
        for child in node.children:
            if child.type == "scoped_identifier" or child.type == "identifier":
                path = self.node_text(child, source)
                target = path.rsplit(".", 1)[-1] if "." in path else path
                refs.append(self._make_reference(
                    target_name=target,
                    kind="import",
                    line=node.start_point[0] + 1,
                    source_name=scope_name,
                    import_path=path,
                ))
                break

    def _extract_method_call(self, node, source, refs, scope_name):
        name_node = node.child_by_field_name("name")
        obj_node = node.child_by_field_name("object")
        if name_node is None:
            return
        name = self.node_text(name_node, source)
        if obj_node:
            name = f"{self.node_text(obj_node, source)}.{name}"

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

    def _extract_new(self, node, source, refs, scope_name):
        type_node = node.child_by_field_name("type")
        if type_node:
            name = self.node_text(type_node, source)
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
