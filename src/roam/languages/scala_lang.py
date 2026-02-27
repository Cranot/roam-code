from __future__ import annotations

from .base import LanguageExtractor


class ScalaExtractor(LanguageExtractor):
    """Full Scala symbol and reference extractor (Tier 1)."""

    @property
    def language_name(self) -> str:
        return "scala"

    @property
    def file_extensions(self) -> list[str]:
        return [".scala", ".sc"]

    def extract_symbols(self, tree, source: bytes, file_path: str) -> list[dict]:
        symbols = []
        self._pending_inherits = []
        self._walk_symbols(tree.root_node, source, symbols, parent_name=None)
        return symbols

    def extract_references(self, tree, source: bytes, file_path: str) -> list[dict]:
        refs = []
        self._walk_refs(tree.root_node, source, refs, scope_name=None)
        refs.extend(getattr(self, "_pending_inherits", []))
        self._pending_inherits = []
        return refs

    def get_docstring(self, node, source: bytes) -> str | None:
        """Scaladoc: /** ... */ comment before node."""
        prev = node.prev_sibling
        if prev and prev.type in ("block_comment", "comment"):
            text = self.node_text(prev, source).strip()
            if text.startswith("/**"):
                text = text[3:]
                if text.endswith("*/"):
                    text = text[:-2]
                return text.strip() or None
        return None

    def _get_visibility(self, node, source) -> str:
        """Extract access modifier from modifiers node."""
        for child in node.children:
            if child.type == "modifiers":
                for sub in child.children:
                    if sub.type == "access_modifier":
                        text = self.node_text(sub, source).lower()
                        if "private" in text:
                            return "private"
                        if "protected" in text:
                            return "protected"
                return "public"
        return "public"

    def _has_modifier(self, node, source, modifier: str) -> bool:
        for child in node.children:
            if child.type == "modifiers":
                return modifier in self.node_text(child, source).lower()
        return False

    def _is_case(self, node) -> bool:
        """Check if a class/object has the 'case' keyword."""
        for child in node.children:
            if not child.is_named and child.type == "case":
                return True
        return False

    # ---- Symbol extraction ----

    _MAX_DEPTH = 50

    def _walk_symbols(self, node, source, symbols, parent_name, _depth=0):
        if _depth > self._MAX_DEPTH:
            return
        for child in node.children:
            if child.type == "class_definition":
                self._extract_class(child, source, symbols, parent_name, _depth)
            elif child.type == "trait_definition":
                self._extract_trait(child, source, symbols, parent_name, _depth)
            elif child.type == "object_definition":
                self._extract_object(child, source, symbols, parent_name, _depth)
            elif child.type in ("function_definition", "function_declaration"):
                self._extract_function(child, source, symbols, parent_name)
            elif child.type in ("val_definition", "val_declaration"):
                self._extract_val(child, source, symbols, parent_name, is_var=False)
            elif child.type in ("var_definition", "var_declaration"):
                self._extract_val(child, source, symbols, parent_name, is_var=True)
            elif child.type == "package_clause":
                self._extract_package(child, source, symbols)
            elif child.type == "type_definition":
                self._extract_type_alias(child, source, symbols, parent_name)

    def _extract_class(self, node, source, symbols, parent_name, _depth=0):
        name = self._get_identifier(node, source)
        if not name:
            return
        vis = self._get_visibility(node, source)
        is_case = self._is_case(node)
        is_abstract = self._has_modifier(node, source, "abstract")
        is_sealed = self._has_modifier(node, source, "sealed")

        prefix = ""
        if is_sealed:
            prefix = "sealed "
        if is_abstract:
            prefix += "abstract "
        if is_case:
            prefix += "case "

        sig = f"{prefix}class {name}"
        type_params = self._find_child(node, "type_parameters")
        if type_params:
            sig += self.node_text(type_params, source)
        params_node = self._find_child(node, "class_parameters")
        if params_node:
            sig += self.node_text(params_node, source)

        qualified = f"{parent_name}.{name}" if parent_name else name

        # Extract extends clause for signature
        extends = self._find_child(node, "extends_clause")
        if extends:
            sig += f" {self.node_text(extends, source)}"
            self._extract_extends_refs(extends, source, qualified)

        symbols.append(
            self._make_symbol(
                name=name,
                kind="class",
                line_start=node.start_point[0] + 1,
                line_end=node.end_point[0] + 1,
                qualified_name=qualified,
                signature=sig,
                docstring=self.get_docstring(node, source),
                visibility=vis,
                is_exported=vis == "public",
                parent_name=parent_name,
            )
        )

        # Extract class parameters as properties (val/var params)
        if params_node:
            self._extract_class_params(params_node, source, symbols, qualified, is_case)

        # Walk body
        body = self._find_child(node, "template_body")
        if body:
            self._walk_symbols(body, source, symbols, qualified, _depth + 1)

    def _extract_trait(self, node, source, symbols, parent_name, _depth=0):
        name = self._get_identifier(node, source)
        if not name:
            return
        vis = self._get_visibility(node, source)
        is_sealed = self._has_modifier(node, source, "sealed")

        sig = "sealed trait " if is_sealed else "trait "
        sig += name
        type_params = self._find_child(node, "type_parameters")
        if type_params:
            sig += self.node_text(type_params, source)

        qualified = f"{parent_name}.{name}" if parent_name else name

        extends = self._find_child(node, "extends_clause")
        if extends:
            sig += f" {self.node_text(extends, source)}"
            self._extract_extends_refs(extends, source, qualified)

        symbols.append(
            self._make_symbol(
                name=name,
                kind="interface",
                line_start=node.start_point[0] + 1,
                line_end=node.end_point[0] + 1,
                qualified_name=qualified,
                signature=sig,
                docstring=self.get_docstring(node, source),
                visibility=vis,
                is_exported=vis == "public",
                parent_name=parent_name,
            )
        )

        body = self._find_child(node, "template_body")
        if body:
            self._walk_symbols(body, source, symbols, qualified, _depth + 1)

    def _extract_object(self, node, source, symbols, parent_name, _depth=0):
        name = self._get_identifier(node, source)
        if not name:
            return
        vis = self._get_visibility(node, source)
        is_case = self._is_case(node)

        sig = "case object " if is_case else "object "
        sig += name

        qualified = f"{parent_name}.{name}" if parent_name else name

        extends = self._find_child(node, "extends_clause")
        if extends:
            sig += f" {self.node_text(extends, source)}"
            self._extract_extends_refs(extends, source, qualified)

        symbols.append(
            self._make_symbol(
                name=name,
                kind="class",
                line_start=node.start_point[0] + 1,
                line_end=node.end_point[0] + 1,
                qualified_name=qualified,
                signature=sig,
                docstring=self.get_docstring(node, source),
                visibility=vis,
                is_exported=vis == "public",
                parent_name=parent_name,
            )
        )

        body = self._find_child(node, "template_body")
        if body:
            self._walk_symbols(body, source, symbols, qualified, _depth + 1)

    def _extract_function(self, node, source, symbols, parent_name):
        name = self._get_identifier(node, source)
        if not name:
            return
        vis = self._get_visibility(node, source)
        is_override = self._has_modifier(node, source, "override")

        kind = "method" if parent_name else "function"

        # Build signature
        sig = ""
        if is_override:
            sig += "override "
        sig += f"def {name}"
        type_params = self._find_child(node, "type_parameters")
        if type_params:
            sig += self.node_text(type_params, source)

        params = self._find_child(node, "parameters")
        if params:
            sig += self.node_text(params, source)

        # Return type
        for i, child in enumerate(node.children):
            if child.type == ":" and i + 1 < len(node.children):
                ret_type = node.children[i + 1]
                if ret_type.type in (
                    "type_identifier",
                    "generic_type",
                    "compound_type",
                    "infix_type",
                    "tuple_type",
                    "function_type",
                ):
                    sig += f": {self.node_text(ret_type, source)}"
                break

        qualified = f"{parent_name}.{name}" if parent_name else name
        symbols.append(
            self._make_symbol(
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
            )
        )

    def _extract_val(self, node, source, symbols, parent_name, *, is_var: bool):
        name = self._get_identifier(node, source)
        if not name:
            return
        vis = self._get_visibility(node, source)
        is_override = self._has_modifier(node, source, "override")

        keyword = "var" if is_var else "val"
        sig = ""
        if is_override:
            sig += "override "
        sig += f"{keyword} {name}"

        # Type annotation
        for i, child in enumerate(node.children):
            if child.type == ":" and i + 1 < len(node.children):
                type_node = node.children[i + 1]
                if type_node.type in (
                    "type_identifier",
                    "generic_type",
                    "compound_type",
                    "infix_type",
                    "tuple_type",
                    "function_type",
                ):
                    sig += f": {self.node_text(type_node, source)}"
                break

        qualified = f"{parent_name}.{name}" if parent_name else name

        # Determine kind: constant if val at top level or in object, else property if in class
        if parent_name:
            kind = "property"
        else:
            kind = "constant" if not is_var else "variable"

        symbols.append(
            self._make_symbol(
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
            )
        )

    def _extract_type_alias(self, node, source, symbols, parent_name):
        name = self._get_identifier(node, source)
        if not name:
            return
        vis = self._get_visibility(node, source)
        sig = f"type {name}"

        # Get the RHS
        for i, child in enumerate(node.children):
            if child.type == "=" and i + 1 < len(node.children):
                rhs = node.children[i + 1]
                sig += f" = {self.node_text(rhs, source)[:60]}"
                break

        qualified = f"{parent_name}.{name}" if parent_name else name
        symbols.append(
            self._make_symbol(
                name=name,
                kind="type_alias",
                line_start=node.start_point[0] + 1,
                line_end=node.end_point[0] + 1,
                qualified_name=qualified,
                signature=sig,
                visibility=vis,
                is_exported=vis == "public",
                parent_name=parent_name,
            )
        )

    def _extract_package(self, node, source, symbols):
        pkg = self._find_child(node, "package_identifier")
        if pkg:
            name = self.node_text(pkg, source)
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

    def _extract_class_params(self, params_node, source, symbols, class_name, is_case):
        """Extract val/var class parameters as properties.

        For case classes, all params are implicitly val.
        """
        for child in params_node.children:
            if child.type != "class_parameter":
                continue
            has_val = False
            has_var = False
            name = None
            vis = "public"
            for sub in child.children:
                if sub.type == "val":
                    has_val = True
                elif sub.type == "var":
                    has_var = True
                elif sub.type == "identifier":
                    name = self.node_text(sub, source)
                elif sub.type == "modifiers":
                    for mod in sub.children:
                        if mod.type == "access_modifier":
                            text = self.node_text(mod, source).lower()
                            if "private" in text:
                                vis = "private"
                            elif "protected" in text:
                                vis = "protected"

            if not name:
                continue
            # Only emit property for val/var params, or for case class params
            if not has_val and not has_var and not is_case:
                continue

            keyword = "var" if has_var else "val"
            sig = f"{keyword} {name}"
            # Type annotation
            for i, sub in enumerate(child.children):
                if sub.type == ":" and i + 1 < len(child.children):
                    type_node = child.children[i + 1]
                    if type_node.type in (
                        "type_identifier",
                        "generic_type",
                        "compound_type",
                        "infix_type",
                        "tuple_type",
                        "function_type",
                    ):
                        sig += f": {self.node_text(type_node, source)}"
                    break

            qualified = f"{class_name}.{name}"
            symbols.append(
                self._make_symbol(
                    name=name,
                    kind="property",
                    line_start=child.start_point[0] + 1,
                    line_end=child.end_point[0] + 1,
                    qualified_name=qualified,
                    signature=sig,
                    parent_name=class_name,
                    visibility=vis,
                    is_exported=vis == "public",
                )
            )

    # ---- Inheritance reference extraction ----

    def _extract_extends_refs(self, extends_node, source, class_name):
        """Extract inherits/implements refs from an extends_clause.

        Handles both plain types (``extends Animal``) and generic types
        (``extends Comparable[Int]``) by extracting the first
        ``type_identifier`` from ``generic_type`` nodes.
        """
        # First type after 'extends' is the superclass.
        # Additional types after 'with' are trait mixins.
        after_with = False
        for child in extends_node.children:
            if child.type == "with":
                after_with = True
            elif child.type in ("type_identifier", "generic_type"):
                target = self._first_type_identifier(child, source)
                if target:
                    ref_kind = "implements" if after_with else "inherits"
                    self._pending_inherits.append(
                        self._make_reference(
                            target_name=target,
                            kind=ref_kind,
                            line=extends_node.start_point[0] + 1,
                            source_name=class_name,
                        )
                    )

    # ---- Reference extraction ----

    def _walk_refs(self, node, source, refs, scope_name):
        for child in node.children:
            if child.type == "import_declaration":
                self._extract_import(child, source, refs, scope_name)
            elif child.type == "call_expression":
                self._extract_call(child, source, refs, scope_name)
            elif child.type == "instance_expression":
                self._extract_new(child, source, refs, scope_name)
            else:
                new_scope = scope_name
                if child.type == "class_definition":
                    n = self._get_identifier(child, source)
                    if n:
                        new_scope = f"{scope_name}.{n}" if scope_name else n
                elif child.type == "trait_definition":
                    n = self._get_identifier(child, source)
                    if n:
                        new_scope = f"{scope_name}.{n}" if scope_name else n
                elif child.type == "object_definition":
                    n = self._get_identifier(child, source)
                    if n:
                        new_scope = f"{scope_name}.{n}" if scope_name else n
                elif child.type in ("function_definition", "function_declaration"):
                    n = self._get_identifier(child, source)
                    if n:
                        new_scope = f"{scope_name}.{n}" if scope_name else n
                self._walk_refs(child, source, refs, new_scope)

    def _extract_import(self, node, source, refs, scope_name):
        """Extract import references.

        Scala imports: import a.b.c, import a.b.{X, Y}, import a.b._
        """
        # Collect all identifier children to build the import path
        parts = []
        for child in node.children:
            if child.type == "identifier":
                parts.append(self.node_text(child, source))
            elif child.type == "namespace_selectors":
                # Multiple selectors: {X, Y}
                for sub in child.children:
                    if sub.type == "identifier":
                        target = self.node_text(sub, source)
                        path = ".".join(parts + [target])
                        refs.append(
                            self._make_reference(
                                target_name=target,
                                kind="import",
                                line=node.start_point[0] + 1,
                                source_name=scope_name,
                                import_path=path,
                            )
                        )
                return

        if parts:
            target = parts[-1]
            path = ".".join(parts)
            refs.append(
                self._make_reference(
                    target_name=target,
                    kind="import",
                    line=node.start_point[0] + 1,
                    source_name=scope_name,
                    import_path=path,
                )
            )

    def _extract_call(self, node, source, refs, scope_name):
        """Extract function/method call references."""
        # First child is the function expression
        func = None
        for child in node.children:
            if child.type == "arguments":
                break
            func = child

        if func is None:
            return

        if func.type == "field_expression":
            # obj.method() → extract method name
            field = func.child_by_field_name("field")
            if field:
                name = self.node_text(field, source)
            else:
                name = self.node_text(func, source)
        else:
            name = self.node_text(func, source)

        refs.append(
            self._make_reference(
                target_name=name,
                kind="call",
                line=func.start_point[0] + 1,
                source_name=scope_name,
            )
        )

        # Recurse into arguments
        for child in node.children:
            if child.type == "arguments":
                self._walk_refs(child, source, refs, scope_name)

    def _extract_new(self, node, source, refs, scope_name):
        """Extract 'new Type(args)' instantiation references."""
        for child in node.children:
            if child.type == "type_identifier":
                name = self.node_text(child, source)
                refs.append(
                    self._make_reference(
                        target_name=name,
                        kind="call",
                        line=node.start_point[0] + 1,
                        source_name=scope_name,
                    )
                )
                break
        # Recurse into arguments
        for child in node.children:
            if child.type == "arguments":
                self._walk_refs(child, source, refs, scope_name)

    # ---- Helpers ----

    def _first_type_identifier(self, node, source) -> str | None:
        """Return the text of the first ``type_identifier`` in *node*.

        If *node* itself is a ``type_identifier``, return its text directly.
        Otherwise search direct children (handles ``generic_type`` wrappers).
        """
        if node.type == "type_identifier":
            return self.node_text(node, source)
        for child in node.children:
            if child.type == "type_identifier":
                return self.node_text(child, source)
        return None

    def _get_identifier(self, node, source) -> str | None:
        """Get the identifier (name) from a node."""
        for child in node.children:
            if child.type in ("identifier", "type_identifier"):
                return self.node_text(child, source)
        return None

    def _find_child(self, node, child_type: str):
        """Find first child of a given type."""
        for child in node.children:
            if child.type == child_type:
                return child
        return None
