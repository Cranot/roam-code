
from __future__ import annotations
from .javascript_lang import JavaScriptExtractor


class TypeScriptExtractor(JavaScriptExtractor):
    """TypeScript extractor extending JavaScript with TS-specific constructs."""

    @property
    def language_name(self) -> str:
        return "typescript"

    @property
    def file_extensions(self) -> list[str]:
        return [".ts", ".tsx", ".mts", ".cts"]

    def _walk_symbols(self, node, source, file_path, symbols, parent_name, is_exported):
        for child in node.children:
            exported = is_exported or self._is_export_node(child)

            if child.type == "function_declaration":
                self._extract_function(child, source, symbols, parent_name, exported)
            elif child.type == "generator_function_declaration":
                self._extract_function(child, source, symbols, parent_name, exported, generator=True)
            elif child.type == "class_declaration":
                self._extract_class(child, source, file_path, symbols, parent_name, exported)
            elif child.type in ("lexical_declaration", "variable_declaration"):
                self._extract_variable_decl(child, source, file_path, symbols, parent_name, exported)
            elif child.type == "export_statement":
                self._walk_symbols(child, source, file_path, symbols, parent_name, is_exported=True)
            elif child.type == "interface_declaration":
                self._extract_interface(child, source, symbols, parent_name, exported)
            elif child.type == "type_alias_declaration":
                self._extract_type_alias(child, source, symbols, parent_name, exported)
            elif child.type == "enum_declaration":
                self._extract_enum(child, source, symbols, parent_name, exported)
            elif child.type == "abstract_class_declaration":
                self._extract_class(child, source, file_path, symbols, parent_name, exported)
            elif child.type == "expression_statement":
                self._extract_module_exports(child, source, symbols, parent_name)
            else:
                self._walk_symbols(child, source, file_path, symbols, parent_name, is_exported)

    def _extract_interface(self, node, source, symbols, parent_name, is_exported):
        name_node = node.child_by_field_name("name")
        if name_node is None:
            return
        name = self.node_text(name_node, source)
        sig = f"interface {name}"

        # Check for type parameters
        type_params = node.child_by_field_name("type_parameters")
        if type_params:
            sig += self.node_text(type_params, source)

        # Check for extends
        for child in node.children:
            if child.type == "extends_type_clause":
                sig += f" {self.node_text(child, source)}"
                break

        qualified = f"{parent_name}.{name}" if parent_name else name
        symbols.append(self._make_symbol(
            name=name,
            kind="interface",
            line_start=node.start_point[0] + 1,
            line_end=node.end_point[0] + 1,
            qualified_name=qualified,
            signature=sig,
            docstring=self.get_docstring(node, source),
            is_exported=is_exported,
            parent_name=parent_name,
        ))

        # Extract interface members
        body = node.child_by_field_name("body")
        if body:
            self._extract_interface_members(body, source, symbols, qualified)

    def _extract_interface_members(self, body_node, source, symbols, interface_name):
        for child in body_node.children:
            if child.type in ("property_signature", "method_signature"):
                name_node = child.child_by_field_name("name")
                if name_node is None:
                    continue
                name = self.node_text(name_node, source)
                qualified = f"{interface_name}.{name}"

                if child.type == "method_signature":
                    params = child.child_by_field_name("parameters")
                    sig = f"{name}({self._params_text(params, source)})"
                    ret = child.child_by_field_name("return_type")
                    if ret:
                        sig += f": {self.node_text(ret, source)}"
                    symbols.append(self._make_symbol(
                        name=name,
                        kind="method",
                        line_start=child.start_point[0] + 1,
                        line_end=child.end_point[0] + 1,
                        qualified_name=qualified,
                        signature=sig,
                        parent_name=interface_name,
                    ))
                else:
                    type_ann = child.child_by_field_name("type")
                    sig = name
                    if type_ann:
                        sig += f": {self.node_text(type_ann, source)}"
                    symbols.append(self._make_symbol(
                        name=name,
                        kind="property",
                        line_start=child.start_point[0] + 1,
                        line_end=child.end_point[0] + 1,
                        qualified_name=qualified,
                        signature=sig,
                        parent_name=interface_name,
                    ))

    def _extract_type_alias(self, node, source, symbols, parent_name, is_exported):
        name_node = node.child_by_field_name("name")
        if name_node is None:
            return
        name = self.node_text(name_node, source)
        sig = f"type {name}"

        type_params = node.child_by_field_name("type_parameters")
        if type_params:
            sig += self.node_text(type_params, source)

        value = node.child_by_field_name("value")
        if value:
            val_text = self.node_text(value, source)
            if len(val_text) <= 80:
                sig += f" = {val_text}"

        qualified = f"{parent_name}.{name}" if parent_name else name
        symbols.append(self._make_symbol(
            name=name,
            kind="type_alias",
            line_start=node.start_point[0] + 1,
            line_end=node.end_point[0] + 1,
            qualified_name=qualified,
            signature=sig,
            docstring=self.get_docstring(node, source),
            is_exported=is_exported,
            parent_name=parent_name,
        ))

    def _extract_enum(self, node, source, symbols, parent_name, is_exported):
        name_node = node.child_by_field_name("name")
        if name_node is None:
            return
        name = self.node_text(name_node, source)

        # Check for const enum
        is_const = any(
            child.type == "const" or self.node_text(child, source) == "const"
            for child in node.children
            if child != node.child_by_field_name("name") and child != node.child_by_field_name("body")
        )
        sig = f"{'const ' if is_const else ''}enum {name}"

        qualified = f"{parent_name}.{name}" if parent_name else name
        symbols.append(self._make_symbol(
            name=name,
            kind="enum",
            line_start=node.start_point[0] + 1,
            line_end=node.end_point[0] + 1,
            qualified_name=qualified,
            signature=sig,
            docstring=self.get_docstring(node, source),
            is_exported=is_exported,
            parent_name=parent_name,
        ))

        # Extract enum members
        body = node.child_by_field_name("body")
        if body:
            for child in body.children:
                if child.type == "enum_assignment" or child.type == "property_identifier":
                    mem_name = None
                    if child.type == "property_identifier":
                        mem_name = self.node_text(child, source)
                    elif child.type == "enum_assignment":
                        n = child.child_by_field_name("name")
                        if n:
                            mem_name = self.node_text(n, source)
                    if mem_name:
                        symbols.append(self._make_symbol(
                            name=mem_name,
                            kind="field",
                            line_start=child.start_point[0] + 1,
                            line_end=child.end_point[0] + 1,
                            qualified_name=f"{qualified}.{mem_name}",
                            parent_name=qualified,
                        ))

    def _extract_function(self, node, source, symbols, parent_name, is_exported, generator=False):
        """Override to include type annotations."""
        name_node = node.child_by_field_name("name")
        if name_node is None:
            return
        name = self.node_text(name_node, source)
        params = node.child_by_field_name("parameters")
        prefix = "function*" if generator else "function"
        sig = f"{prefix} {name}({self._params_text(params, source)})"

        # Add type parameters
        type_params = node.child_by_field_name("type_parameters")
        if type_params:
            sig = f"{prefix} {name}{self.node_text(type_params, source)}({self._params_text(params, source)})"

        # Add return type
        ret = node.child_by_field_name("return_type")
        if ret:
            sig += f": {self.node_text(ret, source)}"

        # Check for decorators
        decorators = self._get_ts_decorators(node, source)
        if decorators:
            sig = "\n".join(decorators) + "\n" + sig

        qualified = f"{parent_name}.{name}" if parent_name else name
        symbols.append(self._make_symbol(
            name=name,
            kind="function",
            line_start=node.start_point[0] + 1,
            line_end=node.end_point[0] + 1,
            qualified_name=qualified,
            signature=sig,
            docstring=self.get_docstring(node, source),
            is_exported=is_exported,
            parent_name=parent_name,
        ))

    def _get_ts_decorators(self, node, source) -> list[str]:
        decorators = []
        for child in node.children:
            if child.type == "decorator":
                decorators.append(self.node_text(child, source))
        return decorators

    def _extract_class_members(self, body_node, source, symbols, class_name):
        """Override to handle TS-specific class members."""
        for child in body_node.children:
            if child.type in ("method_definition", "public_field_definition", "field_definition",
                              "method_signature", "property_signature"):
                name_node = child.child_by_field_name("name")
                if name_node is None:
                    continue
                name = self.node_text(name_node, source)
                qualified = f"{class_name}.{name}"

                # Determine visibility from access modifiers
                visibility = "public"
                for sub in child.children:
                    text = self.node_text(sub, source)
                    if text in ("private", "protected", "public"):
                        visibility = text
                        break

                if child.type in ("method_definition", "method_signature"):
                    params = child.child_by_field_name("parameters")
                    sig = f"{name}({self._params_text(params, source)})"
                    ret = child.child_by_field_name("return_type")
                    if ret:
                        sig += f": {self.node_text(ret, source)}"

                    # Decorators
                    decorators = self._get_ts_decorators(child, source)
                    if decorators:
                        sig = "\n".join(decorators) + "\n" + sig

                    kind = "constructor" if name == "constructor" else "method"
                    symbols.append(self._make_symbol(
                        name=name,
                        kind=kind,
                        line_start=child.start_point[0] + 1,
                        line_end=child.end_point[0] + 1,
                        qualified_name=qualified,
                        signature=sig,
                        docstring=self.get_docstring(child, source),
                        visibility=visibility,
                        parent_name=class_name,
                    ))
                else:
                    type_ann = child.child_by_field_name("type")
                    sig = name
                    if type_ann:
                        sig += f": {self.node_text(type_ann, source)}"
                    symbols.append(self._make_symbol(
                        name=name,
                        kind="property",
                        line_start=child.start_point[0] + 1,
                        line_end=child.end_point[0] + 1,
                        qualified_name=qualified,
                        signature=sig,
                        visibility=visibility,
                        parent_name=class_name,
                    ))
