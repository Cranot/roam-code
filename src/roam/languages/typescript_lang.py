from __future__ import annotations

import os

from .javascript_lang import JavaScriptExtractor

# Built-in TypeScript / JavaScript type names that never create user-defined type edges.
# Includes primitives AND common globals/utility types so type_ref edges don't pollute
# the graph with unresolvable entries.
_TS_BUILTIN_TYPES = frozenset(
    {
        # Primitives
        "string",
        "number",
        "boolean",
        "void",
        "never",
        "any",
        "unknown",
        "object",
        "null",
        "undefined",
        "symbol",
        "bigint",
        # Built-in global types
        "Array",
        "Promise",
        "Map",
        "Set",
        "WeakMap",
        "WeakSet",
        "WeakRef",
        "Error",
        "Function",
        "Date",
        "RegExp",
        "URL",
        "Event",
        # TS utility types
        "Partial",
        "Required",
        "Readonly",
        "Record",
        "Pick",
        "Omit",
        "Exclude",
        "Extract",
        "NonNullable",
        "ReturnType",
        "InstanceType",
        "Parameters",
        "ConstructorParameters",
        "ThisType",
        "Awaited",
    }
)

# Tree-sitter node types that form a "type context" (never contain calls/values)
_TS_TYPE_CONTEXT_NODES = frozenset(
    {
        "type_annotation",
        "implements_clause",
        "type_parameters",
        "type_parameter",
        "constraint",
        "type_arguments",
        "union_type",
        "intersection_type",
        "parenthesized_type",
        "array_type",
        "predefined_type",
        "object_type",
        "tuple_type",
        "conditional_type",
        "mapped_type_clause",
        "literal_type",
        "index_type_query",
        "lookup_type",
        "template_literal_type",
        # Wave 8: type predicate return types — function (x: T): x is UserModel
        "type_predicate_annotation",
        "type_predicate",
        # Wave 9: tuple element wrappers and index signatures
        # rest_type covers ...OtherTypes in tuple position
        "rest_type",
        # optional_type covers MyType? in tuple position
        "optional_type",
        # index_signature is the container for both mapped-type clauses
        # ({ [K in keyof T]: V }) and regular index signatures ({ [k: string]: V })
        "index_signature",
        # Wave 10: function/constructor type expressions — the return type is a
        # direct type_identifier child of these nodes.  Without them in the set
        # the return-type identifier falls through to _walk_refs which never
        # emits type_ref edges for bare identifiers.
        # function_type covers: type F = (x: A) => ReturnType
        "function_type",
        # constructor_type covers: type C = new (x: A) => ReturnType
        "constructor_type",
        # Wave 11: template literal type substitutions — `prefix${MyType}suffix`
        # template_literal_type is already in the set (above); each ${...}
        # substitution inside it is a template_type node whose children are the
        # actual type identifiers.  Without template_type in the set those
        # identifiers are skipped when _walk_type_node iterates the children of
        # template_literal_type.
        "template_type",
        # Wave 11: asserts type predicate — function assert(): asserts x is T
        # type_predicate_annotation (already in set) wraps asserts_type_predicate;
        # without the inner node in the set its type_identifier child is skipped.
        "asserts_type_predicate",
    }
)


class TypeScriptExtractor(JavaScriptExtractor):
    """TypeScript extractor extending JavaScript with TS-specific constructs."""

    @property
    def language_name(self) -> str:
        return "typescript"

    @property
    def file_extensions(self) -> list[str]:
        return [".ts", ".tsx", ".mts", ".cts"]

    def extract_symbols(self, tree, source: bytes, file_path: str) -> list[dict]:
        symbols = super().extract_symbols(tree, source, file_path)
        # Round 4 #10 / R-extended: Vue/Svelte SFCs don't define their
        # component as a top-level symbol — the component name lives in
        # the filename. Synthesise a "component" symbol so agents can
        # query `roam why MyDataManagementModal` and have it resolve.
        normalised = file_path.replace("\\", "/").lower()
        if normalised.endswith(".vue") or normalised.endswith(".svelte"):
            base = os.path.basename(file_path)
            stem, _ = os.path.splitext(base)
            if stem:
                synthetic = self._make_symbol(
                    name=stem,
                    kind="component",
                    qualified_name=stem,
                    line_start=1,
                    line_end=1,
                    signature=f"<{stem}/> ({normalised.rsplit('.', 1)[-1]} SFC)",
                    is_exported=True,
                    parent_name=None,
                )
                if synthetic and not any(s.get("name") == stem and s.get("kind") == "component" for s in symbols):
                    symbols.insert(0, synthetic)
        return symbols

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
                ns_node = next((c for c in child.children if c.type == "internal_module"), None)
                if ns_node is not None:
                    self._extract_namespace(ns_node, source, file_path, symbols, parent_name, exported)
                else:
                    self._extract_module_exports(child, source, symbols, parent_name)
            elif child.type == "internal_module":
                self._extract_namespace(child, source, file_path, symbols, parent_name, exported)
            else:
                self._walk_symbols(child, source, file_path, symbols, parent_name, is_exported)

    def _extract_namespace(self, node, source, file_path, symbols, parent_name, is_exported):
        """Extract a TypeScript namespace/module declaration as a ``namespace``
        symbol and recurse into its body to collect nested members."""
        name_node = next((c for c in node.children if c.type == "identifier"), None)
        if name_node is None:
            return
        name = self.node_text(name_node, source)
        qualified = f"{parent_name}.{name}" if parent_name else name
        symbols.append(
            self._make_symbol(
                name=name,
                kind="namespace",
                line_start=node.start_point[0] + 1,
                line_end=node.end_point[0] + 1,
                qualified_name=qualified,
                signature=f"namespace {qualified}",
                is_exported=is_exported,
                parent_name=parent_name,
            )
        )
        body = next((c for c in node.children if c.type == "statement_block"), None)
        if body is not None:
            self._walk_symbols(body, source, file_path, symbols, qualified, is_exported=False)

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
        symbols.append(
            self._make_symbol(
                name=name,
                kind="interface",
                line_start=node.start_point[0] + 1,
                line_end=node.end_point[0] + 1,
                qualified_name=qualified,
                signature=sig,
                docstring=self.get_docstring(node, source),
                is_exported=is_exported,
                parent_name=parent_name,
            )
        )

        # Emit edges for interface extends (so roam impact tracks subtypes)
        for child in node.children:
            if child.type == "extends_type_clause":
                for extends_child in child.children:
                    base_name = None
                    if extends_child.type in ("type_identifier", "identifier"):
                        base_name = self.node_text(extends_child, source)
                    elif extends_child.type == "generic_type":
                        name_part = extends_child.child_by_field_name("name")
                        if name_part:
                            base_name = self.node_text(name_part, source)
                    if base_name:
                        if not hasattr(self, "_pending_inherits"):
                            self._pending_inherits = []
                        self._pending_inherits.append(
                            self._make_reference(
                                target_name=base_name,
                                kind="inherits",
                                line=node.start_point[0] + 1,
                                source_name=qualified,
                            )
                        )
                break

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
                        ret_text = self.node_text(ret, source).lstrip(": ").lstrip(":")
                        sig += f": {ret_text}"
                    symbols.append(
                        self._make_symbol(
                            name=name,
                            kind="method",
                            line_start=child.start_point[0] + 1,
                            line_end=child.end_point[0] + 1,
                            qualified_name=qualified,
                            signature=sig,
                            parent_name=interface_name,
                        )
                    )
                else:
                    type_ann = child.child_by_field_name("type")
                    sig = name
                    if type_ann:
                        ta_text = self.node_text(type_ann, source).lstrip(": ").lstrip(":")
                        sig += f": {ta_text}"
                    symbols.append(
                        self._make_symbol(
                            name=name,
                            kind="property",
                            line_start=child.start_point[0] + 1,
                            line_end=child.end_point[0] + 1,
                            qualified_name=qualified,
                            signature=sig,
                            parent_name=interface_name,
                        )
                    )

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
        symbols.append(
            self._make_symbol(
                name=name,
                kind="type_alias",
                line_start=node.start_point[0] + 1,
                line_end=node.end_point[0] + 1,
                qualified_name=qualified,
                signature=sig,
                docstring=self.get_docstring(node, source),
                is_exported=is_exported,
                parent_name=parent_name,
            )
        )

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
        symbols.append(
            self._make_symbol(
                name=name,
                kind="enum",
                line_start=node.start_point[0] + 1,
                line_end=node.end_point[0] + 1,
                qualified_name=qualified,
                signature=sig,
                docstring=self.get_docstring(node, source),
                is_exported=is_exported,
                parent_name=parent_name,
            )
        )

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
                        symbols.append(
                            self._make_symbol(
                                name=mem_name,
                                kind="field",
                                line_start=child.start_point[0] + 1,
                                line_end=child.end_point[0] + 1,
                                qualified_name=f"{qualified}.{mem_name}",
                                parent_name=qualified,
                            )
                        )

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
        symbols.append(
            self._make_symbol(
                name=name,
                kind="function",
                line_start=node.start_point[0] + 1,
                line_end=node.end_point[0] + 1,
                qualified_name=qualified,
                signature=sig,
                docstring=self.get_docstring(node, source),
                is_exported=is_exported,
                parent_name=parent_name,
            )
        )

    def _get_ts_decorators(self, node, source) -> list[str]:
        decorators = []
        for child in node.children:
            if child.type == "decorator":
                decorators.append(self.node_text(child, source))
        return decorators

    def _walk_type_node(self, node, source, refs, scope_name):
        """Collect type_ref edges from a TS type annotation / constraint / implements clause.

        Recurses into the nested type AST to emit one type_ref edge per
        user-defined type identifier, skipping primitives and known builtins.

        May be called either on a CONTAINER node (type_annotation, union_type, …)
        or directly on a type leaf (type_identifier, generic_type) — the
        self-node check at the top handles both cases.
        """
        # Handle being called directly on the type leaf itself
        ntype = node.type
        if ntype == "type_identifier":
            name = self.node_text(node, source)
            if name and name not in _TS_BUILTIN_TYPES:
                refs.append(
                    self._make_reference(
                        target_name=name,
                        kind="type_ref",
                        line=node.start_point[0] + 1,
                        source_name=scope_name,
                    )
                )
            return
        if ntype == "generic_type":
            name_part = node.child_by_field_name("name")
            if name_part:
                name = self.node_text(name_part, source)
                if name and name not in _TS_BUILTIN_TYPES:
                    refs.append(
                        self._make_reference(
                            target_name=name,
                            kind="type_ref",
                            line=node.start_point[0] + 1,
                            source_name=scope_name,
                        )
                    )
            for sub in node.children:
                if sub.type in ("type_arguments", "type_annotation"):
                    self._walk_type_node(sub, source, refs, scope_name)
            return
        for child in node.children:
            ctype = child.type
            if ctype == "type_identifier":
                name = self.node_text(child, source)
                if name not in _TS_BUILTIN_TYPES:
                    refs.append(
                        self._make_reference(
                            target_name=name,
                            kind="type_ref",
                            line=child.start_point[0] + 1,
                            source_name=scope_name,
                        )
                    )
            elif ctype == "generic_type":
                name_part = child.child_by_field_name("name")
                if name_part:
                    name = self.node_text(name_part, source)
                    if name not in _TS_BUILTIN_TYPES:
                        refs.append(
                            self._make_reference(
                                target_name=name,
                                kind="type_ref",
                                line=child.start_point[0] + 1,
                                source_name=scope_name,
                            )
                        )
                # Recurse into type arguments for nested generics like Foo<Bar<Baz>>
                for sub in child.children:
                    if sub.type in ("type_arguments", "type_annotation"):
                        self._walk_type_node(sub, source, refs, scope_name)
            elif ctype == "formal_parameters":
                # Parameter list inside function_type / constructor_type.
                # Each parameter may carry a type_annotation; walk those.
                for param in child.children:
                    if param.type in (
                        "required_parameter",
                        "optional_parameter",
                        "rest_parameter",
                    ):
                        type_ann = param.child_by_field_name("type")
                        if type_ann is not None:
                            self._walk_type_node(type_ann, source, refs, scope_name)
            elif ctype in _TS_TYPE_CONTEXT_NODES:
                self._walk_type_node(child, source, refs, scope_name)

    def _extract_as_expression(self, node, source, refs, scope_name):
        """Handle a TypeScript ``as_expression`` (type assertion).

        ``value as TypeName`` — walk the value part normally for call/import
        edges and walk the type part with ``_walk_type_node`` for type_ref
        edges.  Handles chained assertions like ``fn() as unknown as Foo``
        because the outer value is itself an ``as_expression``, which hits
        this handler again recursively.
        """
        as_idx = next((i for i, c in enumerate(node.children) if c.type == "as"), -1)
        if as_idx > 0:
            # Value part — dispatch as if each child were encountered in _walk_refs
            for i in range(as_idx):
                vn = node.children[i]
                vtype = vn.type
                if vtype == "call_expression":
                    self._extract_call(vn, source, refs, scope_name)
                elif vtype == "new_expression":
                    self._extract_new(vn, source, refs, scope_name)
                elif vtype == "as_expression":
                    self._extract_as_expression(vn, source, refs, scope_name)
                else:
                    self._walk_refs(vn, source, refs, self._scope_name_for_child(vn, source, scope_name))
        if as_idx >= 0 and as_idx + 1 < len(node.children):
            # Type part — type_ref edges only
            self._walk_type_node(node.children[as_idx + 1], source, refs, scope_name)

    def _extract_satisfies_expression(self, node, source, refs, scope_name):
        """Handle a TypeScript ``satisfies_expression`` (TS 4.9+).

        ``value satisfies TypeName`` — walk the value part normally for
        call/import edges and walk the type part with ``_walk_type_node`` for
        type_ref edges.  The ``satisfies`` keyword is the separator.
        """
        sat_idx = next((i for i, c in enumerate(node.children) if c.type == "satisfies"), -1)
        if sat_idx > 0:
            for i in range(sat_idx):
                vn = node.children[i]
                vtype = vn.type
                if vtype == "call_expression":
                    self._extract_call(vn, source, refs, scope_name)
                elif vtype == "new_expression":
                    self._extract_new(vn, source, refs, scope_name)
                elif vtype == "as_expression":
                    self._extract_as_expression(vn, source, refs, scope_name)
                elif vtype == "satisfies_expression":
                    self._extract_satisfies_expression(vn, source, refs, scope_name)
                else:
                    self._walk_refs(vn, source, refs, self._scope_name_for_child(vn, source, scope_name))
        if sat_idx >= 0 and sat_idx + 1 < len(node.children):
            self._walk_type_node(node.children[sat_idx + 1], source, refs, scope_name)

    def _walk_refs(self, node, source, refs, scope_name):
        """Walk the AST collecting call/import/type references.

        Overrides the JS base to also emit type_ref edges from TS type
        annotations, implements clauses, and type parameter constraints.
        Mirrors js._walk_refs exactly for value nodes so that call-graph
        and import edges are not affected.
        """
        for child in node.children:
            ctype = child.type
            if ctype in _TS_TYPE_CONTEXT_NODES:
                self._walk_type_node(child, source, refs, scope_name)
            elif ctype == "import_statement":
                self._extract_esm_import(child, source, refs, scope_name)
            elif ctype == "export_statement":
                self._extract_export_refs(child, source, refs, scope_name)
            elif ctype == "call_expression":
                self._extract_call(child, source, refs, scope_name)
            elif ctype == "new_expression":
                self._extract_new(child, source, refs, scope_name)
            elif ctype in ("jsx_self_closing_element", "jsx_opening_element"):
                self._extract_jsx_element_refs(child, source, refs, scope_name)
            elif ctype == "as_expression":
                self._extract_as_expression(child, source, refs, scope_name)
            elif ctype == "satisfies_expression":
                self._extract_satisfies_expression(child, source, refs, scope_name)
            elif ctype == "identifier" and node.type in ("arguments", "jsx_expression"):
                self._emit_argument_identifier_ref(child, source, refs, scope_name)
            elif ctype == "shorthand_property_identifier":
                self._emit_shorthand_property_ref(child, source, refs, scope_name)
            else:
                self._walk_refs(child, source, refs, self._scope_name_for_child(child, source, scope_name))

    def _extract_class_members(self, body_node, source, symbols, class_name):
        """Override to handle TS-specific class members."""
        for child in body_node.children:
            if child.type in (
                "method_definition",
                "abstract_method_signature",
                "public_field_definition",
                "field_definition",
                "method_signature",
                "property_signature",
            ):
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

                if child.type in (
                    "method_definition",
                    "method_signature",
                    "abstract_method_signature",
                ):
                    params = child.child_by_field_name("parameters")
                    sig = f"{name}({self._params_text(params, source)})"
                    if child.type == "abstract_method_signature":
                        sig = "abstract " + sig
                    ret = child.child_by_field_name("return_type")
                    if ret:
                        ret_text = self.node_text(ret, source).lstrip(": ").lstrip(":")
                        sig += f": {ret_text}"

                    # Decorators
                    decorators = self._get_ts_decorators(child, source)
                    if decorators:
                        sig = "\n".join(decorators) + "\n" + sig

                    kind = "constructor" if name == "constructor" else "method"
                    symbols.append(
                        self._make_symbol(
                            name=name,
                            kind=kind,
                            line_start=child.start_point[0] + 1,
                            line_end=child.end_point[0] + 1,
                            qualified_name=qualified,
                            signature=sig,
                            docstring=self.get_docstring(child, source),
                            visibility=visibility,
                            parent_name=class_name,
                        )
                    )
                    # TypeScript constructor parameter properties:
                    # constructor(private db: Db, public name: string)
                    # → emit a property symbol for each access-modified param
                    if name == "constructor":
                        self._extract_constructor_param_properties(child, source, symbols, class_name)
                else:
                    type_ann = child.child_by_field_name("type")
                    sig = name
                    if type_ann:
                        ta_text = self.node_text(type_ann, source).lstrip(": ").lstrip(":")
                        sig += f": {ta_text}"
                    symbols.append(
                        self._make_symbol(
                            name=name,
                            kind="property",
                            line_start=child.start_point[0] + 1,
                            line_end=child.end_point[0] + 1,
                            qualified_name=qualified,
                            signature=sig,
                            visibility=visibility,
                            parent_name=class_name,
                        )
                    )

    def _extract_constructor_param_properties(self, constructor_node, source, symbols, class_name):
        """Extract TypeScript constructor parameter properties.

        Handles patterns like:
            constructor(private db: Database, public readonly name: string)

        A `required_parameter` (or `optional_parameter`) with an
        `accessibility_modifier` child declares a class property, not just
        a local parameter.  Emit one property symbol per such parameter.
        """
        params = constructor_node.child_by_field_name("parameters")
        if params is None:
            return
        for param in params.children:
            if param.type not in ("required_parameter", "optional_parameter"):
                continue
            # Check for accessibility modifier (private / protected / public)
            has_modifier = any(c.type == "accessibility_modifier" for c in param.children)
            if not has_modifier:
                continue
            # The parameter name is the `identifier` or `pattern` field
            name_node = param.child_by_field_name("pattern")
            if name_node is None:
                name_node = next((c for c in param.children if c.type == "identifier"), None)
            if name_node is None:
                continue
            prop_name = self.node_text(name_node, source)
            type_ann = param.child_by_field_name("type")
            sig = prop_name
            if type_ann:
                sig += f": {self.node_text(type_ann, source)}"
            visibility = "public"
            for mod in param.children:
                if mod.type == "accessibility_modifier":
                    visibility = self.node_text(mod, source)
                    break
            qualified = f"{class_name}.{prop_name}"
            symbols.append(
                self._make_symbol(
                    name=prop_name,
                    kind="property",
                    line_start=param.start_point[0] + 1,
                    line_end=param.end_point[0] + 1,
                    qualified_name=qualified,
                    signature=sig,
                    visibility=visibility,
                    parent_name=class_name,
                )
            )
