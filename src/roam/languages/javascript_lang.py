from __future__ import annotations

import os
import re
from typing import NamedTuple

from .base import LanguageExtractor


class _Binding(NamedTuple):
    owner: str
    declaration: object
    indexed_owner: bool = False
    declared_type: bool = False
    mode: str | None = None


class JavaScriptExtractor(LanguageExtractor):
    """Full JavaScript symbol and reference extractor."""

    @property
    def language_name(self) -> str:
        return "javascript"

    @property
    def file_extensions(self) -> list[str]:
        return [".js", ".jsx", ".mjs", ".cjs"]

    def extract_symbols(self, tree, source: bytes, file_path: str) -> list[dict]:
        symbols = []
        if not hasattr(self, "_pending_inherits"):
            self._pending_inherits = []
        self._walk_symbols(tree.root_node, source, file_path, symbols, parent_name=None, is_exported=False)
        return symbols

    def extract_references(self, tree, source: bytes, file_path: str) -> list[dict]:
        refs = []
        self._binding_cache = {}
        self._member_imports = {}
        self._dynamic_parameter_imports = {}
        for statement in tree.root_node.named_children:
            if statement.type != "import_statement":
                continue
            require_clause = next((n for n in statement.named_children if n.type == "import_require_clause"), None)
            if require_clause is not None:
                name = next((n for n in require_clause.named_children if n.type == "identifier"), None)
                path_node = next((n for n in require_clause.named_children if n.type == "string"), None)
                if name is not None and path_node is not None:
                    self._member_imports[self.node_text(name, source)] = (
                        self.node_text(path_node, source).strip("'\""),
                        "export=",
                    )
                continue
            source_node = statement.child_by_field_name("source")
            if source_node is None:
                continue
            path = self.node_text(source_node, source).strip("'\"")
            for node in statement.named_children:
                if node.type != "import_clause":
                    continue
                for binding in node.named_children:
                    if binding.type == "namespace_import":
                        for name in binding.named_children:
                            if name.type == "identifier":
                                self._member_imports[self.node_text(name, source)] = (path, "*")
                    elif binding.type == "identifier":
                        self._member_imports[self.node_text(binding, source)] = (path, "default")
                    elif binding.type == "named_imports":
                        for spec in binding.named_children:
                            name = spec.child_by_field_name("name")
                            alias = spec.child_by_field_name("alias") or name
                            if name is not None:
                                self._member_imports[self.node_text(alias, source)] = (
                                    path,
                                    self.node_text(name, source),
                                )
        try:
            self._walk_refs(tree.root_node, source, refs, scope_name=None)
        finally:
            self._binding_cache = {}
            self._member_imports = {}
            self._dynamic_parameter_imports = {}
        refs.extend(self._extract_dynamic_import_refs(source))
        # Collect inheritance refs accumulated during extract_symbols
        refs.extend(getattr(self, "_pending_inherits", []))
        self._pending_inherits = []
        return refs

    _FUNCTION_NODES = frozenset(
        {
            "function_declaration",
            "generator_function_declaration",
            "function_expression",
            "generator_function",
            "arrow_function",
            "method_definition",
        }
    )
    _CLASS_NODES = frozenset({"class", "class_declaration", "abstract_class_declaration"})
    _BINDING_SCOPES = (
        _FUNCTION_NODES
        | _CLASS_NODES
        | frozenset({"program", "statement_block", "catch_clause", "for_statement", "for_in_statement", "switch_body"})
    )

    def _pattern_names(self, node, source):
        """Binding identifiers only; object keys and default expressions are not bindings."""
        if node is None:
            return []
        if node.type in {"identifier", "shorthand_property_identifier_pattern"}:
            return [self.node_text(node, source)]
        if node.type in {"pair_pattern", "required_parameter", "optional_parameter"}:
            field = "value" if node.type == "pair_pattern" else "pattern"
            return self._pattern_names(node.child_by_field_name(field), source)
        if node.type in {"assignment_pattern", "object_assignment_pattern"}:
            return self._pattern_names(node.child_by_field_name("left"), source)
        if node.type in {"formal_parameters", "object_pattern", "array_pattern", "rest_pattern"}:
            return [name for child in node.named_children for name in self._pattern_names(child, source)]
        return []

    def _annotated_owner(self, node, source):
        """A single named TS type, optionally generic or nullable."""
        annotation = node.child_by_field_name("type")
        if annotation is None or annotation.type != "type_annotation":
            return "?"
        children = annotation.named_children
        if len(children) != 1:
            return "?"
        name = children[0]

        def non_null_types(node):
            if node.type == "union_type":
                return [t for child in node.named_children for t in non_null_types(child)]
            if self.node_text(node, source) in {"null", "undefined"}:
                return []
            return [node]

        types = non_null_types(name)
        if len(types) != 1:
            return "?"
        name = types[0]
        if name.type == "generic_type":
            name = name.child_by_field_name("name")
        if name is not None and name.type in {"type_identifier", "nested_type_identifier"}:
            return self.node_text(name, source)
        return "?"

    def _scope_bindings(self, scope, source):
        """Cache lexical bindings per AST scope for this extraction only.

        Type and value bindings occupy separate namespaces. Unknown bindings
        shadow imports. Only direct immutable constructions/objects establish owners;
        direct TS annotations are retained, but aliases and mutations are not inferred.
        """
        cache = self._binding_cache
        if scope.id in cache:
            return cache[scope.id]
        bindings = {}
        types = {}
        # Named expressions bind their name only inside their own body. They
        # have no indexed owner and cannot borrow a same-named outer symbol.
        if scope.type in {"function_expression", "generator_function", "class"}:
            name = scope.child_by_field_name("name")
            if name is not None:
                name_text = self.node_text(name, source)
                bindings[name_text] = _Binding("?", scope)
                if scope.type == "class":
                    types[name_text] = bindings[name_text]
        if scope.type in self._FUNCTION_NODES:
            params = scope.child_by_field_name("parameters") or scope.child_by_field_name("parameter")
            if params is not None:
                parameters = params.named_children if params.type == "formal_parameters" else [params]
                for parameter in parameters:
                    for name in self._pattern_names(parameter, source):
                        bindings[name] = _Binding(
                            self._annotated_owner(parameter, source), parameter, declared_type=True, mode="instance"
                        )
        type_params = scope.child_by_field_name("type_parameters")
        for param in type_params.named_children if type_params is not None else []:
            name = param.child_by_field_name("name")
            if name is not None:
                name_text = self.node_text(name, source)
                types[name_text] = bindings[name_text] = _Binding("?", param)
        if scope.type == "catch_clause":
            for name in self._pattern_names(scope.child_by_field_name("parameter"), source):
                bindings[name] = _Binding("?", scope)
        if scope.type == "for_in_statement" and scope.child_by_field_name("kind") is not None:
            for name in self._pattern_names(scope.child_by_field_name("left"), source):
                bindings[name] = _Binding("?", scope)
        # `var` belongs to the function even when declared in a nested block.
        if scope.type in self._FUNCTION_NODES | {"program"}:
            stack = list(scope.named_children)
            while stack:
                node = stack.pop()
                if node.type in self._FUNCTION_NODES | self._CLASS_NODES:
                    continue
                if node.type == "variable_declaration":
                    for declarator in node.named_children:
                        for name in self._pattern_names(declarator.child_by_field_name("name"), source):
                            bindings[name] = _Binding("?", declarator)
                if node.type == "for_in_statement":
                    kind = node.child_by_field_name("kind")
                    if kind is not None and self.node_text(kind, source) == "var":
                        for name in self._pattern_names(node.child_by_field_name("left"), source):
                            bindings[name] = _Binding("?", node)
                stack.extend(node.named_children)
        stack = list(scope.named_children)
        while stack:
            node = stack.pop()
            type_declaration = node.type in {"interface_declaration", "type_alias_declaration"}
            is_class = node.type in {"class_declaration", "abstract_class_declaration"}
            if type_declaration or is_class or node.type in {"function_declaration", "generator_function_declaration"}:
                name = node.child_by_field_name("name")
                if name is not None:
                    name_text = self.node_text(name, source)
                    indexed = scope.type == "program" and node.type != "type_alias_declaration"
                    binding = _Binding(
                        name_text if indexed else "?", node, indexed, mode="static" if is_class else None
                    )
                    if is_class or type_declaration:
                        types[name_text] = binding
                    if not type_declaration:
                        bindings[name_text] = binding
                continue
            if node.type in self._BINDING_SCOPES | self._CLASS_NODES:
                continue
            if node.type == "variable_declarator":
                pattern = node.child_by_field_name("name")
                value = node.child_by_field_name("value")
                owner = self._annotated_owner(node, source)
                declared_type = owner != "?"
                mode = "instance" if declared_type else None
                if value is not None and pattern is not None and pattern.type == "identifier":
                    declaration = node.parent
                    immutable = declaration is not None and declaration.children[0].type == "const"
                    if immutable and value.type == "new_expression":
                        ctor = value.child_by_field_name("constructor")
                        if ctor is not None and ctor.type == "identifier":
                            owner = self.node_text(ctor, source)
                            declared_type = False
                            mode = "instance"
                    elif immutable and value.type == "object":
                        owner = self.node_text(pattern, source) if scope.type == "program" else "?"
                        declared_type = False
                for name in self._pattern_names(pattern, source):
                    bindings[name] = _Binding(owner, node, declared_type=declared_type, mode=mode)
                continue
            stack.extend(node.named_children)
        cache[scope.id] = bindings, types
        return bindings, types

    def _lexical_binding(self, node, name, source, *, local_only=False, type_only=False):
        current = node.parent
        while current is not None:
            if current.type in self._BINDING_SCOPES:
                if local_only and current.type == "program":
                    return None
                bindings, types = self._scope_bindings(current, source)
                found = (types if type_only else bindings).get(name)
                if found is not None:
                    return found
            current = current.parent
        return None

    def _is_local_value(self, node, name, source):
        binding = self._lexical_binding(node, name, source, local_only=True)
        return binding is not None

    def _this_context(self, node, source):
        """Nearest non-arrow function owns `this`; object methods stop the walk."""
        current = node.parent
        while current is not None:
            if current.type in {"method_definition", "field_definition", "public_field_definition"}:
                body = current.parent
                cls = body.parent if body is not None and body.type == "class_body" else None
                if cls is None or cls.type not in self._CLASS_NODES:
                    return None, None
                mode = "static" if "static" in self._collect_method_prefixes(current, source) else "instance"
                return cls, mode
            if current.type in self._CLASS_NODES or current.type in self._FUNCTION_NODES - {"arrow_function"}:
                return None, None
            current = current.parent
        return None, None

    def _unparenthesized(self, node):
        while node is not None and node.type == "parenthesized_expression":
            node = next((child for child in node.named_children if child.type != "comment"), None)
        return node

    def _dynamic_import_path(self, expression, source):
        call = self._unparenthesized(expression)
        if call is None or call.type != "call_expression":
            return None
        callee = call.child_by_field_name("function")
        args = call.child_by_field_name("arguments")
        if callee is None or callee.type != "import" or args is None:
            return None
        specifier = next((child for child in args.named_children if child.type != "comment"), None)
        if specifier is not None and specifier.type == "string":
            return self.node_text(specifier, source)[1:-1]
        return None

    def _awaited_import_path(self, expression, source):
        expression = self._unparenthesized(expression)
        if expression is None or expression.type != "await_expression":
            return None
        operand = next((child for child in expression.named_children if child.type != "comment"), None)
        return self._dynamic_import_path(operand, source)

    def _then_parameter_import_path(self, parameter, name, source):
        # Only the first argument to the callback receives the module namespace.
        callback = parameter.parent
        if callback.type == "formal_parameters":
            first = next((child for child in callback.named_children if child.type != "comment"), None)
            if first != parameter:
                return None
            callback = callback.parent
        pattern = parameter
        if parameter.type in {"required_parameter", "optional_parameter"}:
            pattern = parameter.child_by_field_name("pattern")
        if pattern is None or pattern.type != "identifier":
            return None
        if callback.type not in {"arrow_function", "function_expression"}:
            return None
        args = callback.parent
        if args.type != "arguments" or callback != next(
            (child for child in args.named_children if child.type != "comment"), None
        ):
            return None
        call = args.parent
        callee = call.child_by_field_name("function")
        if callee is None or callee.type != "member_expression":
            return None
        if self.node_text(callee.child_by_field_name("property"), source) != "then":
            return None
        path = self._dynamic_import_path(callee.child_by_field_name("object"), source)
        if path and not self._writes_to_parameter(callback, parameter, name, source):
            return path
        return None

    def _writes_to_parameter(self, callback, parameter, name, source):
        stack = list(callback.named_children)
        while stack:
            node = stack.pop()
            target = None
            if node.type in {"assignment_expression", "augmented_assignment_expression"}:
                target = node.child_by_field_name("left")
            elif node.type == "update_expression":
                target = node.child_by_field_name("argument")
            elif node.type == "for_in_statement" and node.child_by_field_name("kind") is None:
                target = node.child_by_field_name("left")
            if name in self._pattern_names(self._unparenthesized(target), source):
                binding = self._lexical_binding(target, name, source)
                if binding is not None and binding.declaration == parameter:
                    return True
            stack.extend(node.named_children)
        return False

    def _dynamic_receiver_import_path(self, receiver, binding, name, source):
        if binding is None:
            return self._awaited_import_path(receiver, source)
        declaration = binding.declaration
        if declaration.type == "variable_declarator":
            kind = declaration.parent.child_by_field_name("kind")
            pattern = declaration.child_by_field_name("name")
            if kind is not None and kind.type == "const" and pattern is not None and pattern.type == "identifier":
                return self._awaited_import_path(declaration.child_by_field_name("value"), source)
            return None
        paths = self._dynamic_parameter_imports
        if declaration.id not in paths:
            paths[declaration.id] = self._then_parameter_import_path(declaration, name, source)
        return paths[declaration.id]

    def _member_evidence(self, member, source):
        receiver_node = member.child_by_field_name("object")
        receiver = self.node_text(receiver_node, source) if receiver_node is not None else "?"
        binding = None
        if receiver_node is not None and receiver_node.type == "identifier":
            binding = self._lexical_binding(member, receiver, source)
        path = self._dynamic_receiver_import_path(receiver_node, binding, receiver, source)
        if path:
            return {"receiver": receiver, "receiver_owner": "*", "receiver_import_path": path}
        owner = ""
        mode = None
        declared_type = False
        declaration = member
        if receiver == "this" or (
            receiver_node is not None
            and receiver_node.type == "member_expression"
            and self.node_text(receiver_node.child_by_field_name("object"), source) == "this"
        ):
            cls, mode = self._this_context(member, source)
            owner = "?"
            if cls is not None:
                name = cls.child_by_field_name("name")
                if receiver == "this":
                    owner = self.node_text(name, source) if name is not None and cls.type != "class" else "?"
                    declaration = cls
                else:
                    field = self.node_text(receiver_node.child_by_field_name("property"), source)
                    body = cls.child_by_field_name("body")
                    declarations = []
                    for child in body.named_children if body is not None else []:
                        child_name = child.child_by_field_name("name") or child.child_by_field_name("property")
                        is_static = "static" in self._collect_method_prefixes(child, source)
                        if (
                            child_name is not None
                            and self.node_text(child_name, source) == field
                            and is_static == (mode == "static")
                        ):
                            declarations.append(child)
                        if (
                            mode == "instance"
                            and child_name is not None
                            and self.node_text(child_name, source) == "constructor"
                        ):
                            params = child.child_by_field_name("parameters")
                            for param in params.named_children if params is not None else []:
                                modifiers = {n.type for n in param.children}
                                if modifiers & {"accessibility_modifier", "readonly"} and field in self._pattern_names(
                                    param, source
                                ):
                                    declarations.append(param)
                    owner = "?"
                    declared_type = True
                    if len(declarations) == 1:
                        declaration = declarations[0]
                        owner = self._annotated_owner(declaration, source)
                        value = declaration.child_by_field_name("value")
                        if owner == "?" and value is not None and value.type == "new_expression":
                            ctor = value.child_by_field_name("constructor")
                            if ctor is not None and ctor.type == "identifier":
                                owner = self.node_text(ctor, source)
                                declared_type = False
                    mode = "instance"
        elif receiver_node is not None and receiver_node.type == "identifier":
            mode = "static"
            if binding is not None:
                owner = binding.owner
                declared_type = binding.declared_type
                mode = binding.mode
                declaration = binding.declaration
        evidence = {"receiver": receiver, "receiver_owner": owner}
        if mode is not None:
            evidence["receiver_mode"] = mode
        if declared_type:
            evidence["receiver_owner_kind"] = "type"
        owner_binding = self._lexical_binding(declaration, owner or receiver, source, type_only=declared_type)
        if (
            owner
            and (mode == "instance" or owner != receiver)
            and owner_binding is not None
            and not owner_binding.indexed_owner
        ):
            owner = "?"
            evidence["receiver_owner"] = owner
        imported = self._member_imports.get(owner or receiver) if owner_binding is None else None
        if imported is not None and owner != "?":
            evidence["receiver_import_path"], evidence["receiver_owner"] = imported
        if declared_type and "." in owner:
            root, suffix = owner.split(".", 1)
            # A qualified type still refers to a lexical root binding.
            if (
                self._lexical_binding(declaration, root, source) is not None
                or self._lexical_binding(declaration, root, source, type_only=True) is not None
            ):
                evidence["receiver_owner"] = "?"
            elif root in self._member_imports:
                path, exported = self._member_imports[root]
                evidence["receiver_import_path"] = path
                evidence["receiver_owner"] = suffix if exported == "*" else exported + "." + suffix
        return evidence

    def get_docstring(self, node, source: bytes) -> str | None:
        """JSDoc: look for comment node immediately before this node."""
        prev = node.prev_sibling
        if prev and prev.type == "comment":
            text = self.node_text(prev, source).strip()
            if text.startswith("/**"):
                # Strip /** and */
                text = text[3:]
                if text.endswith("*/"):
                    text = text[:-2]
                return text.strip()
        return None

    # ---- Symbol extraction ----

    def _walk_symbols(self, node, source, file_path, symbols, parent_name, is_exported):
        for child in node.children:
            exported = is_exported or self._is_export_node(child)

            if child.type == "function_declaration":
                self._extract_function(child, source, symbols, parent_name, exported)
            elif child.type == "generator_function_declaration":
                self._extract_function(child, source, symbols, parent_name, exported, generator=True)
            elif child.type in ("class_declaration", "class"):
                self._extract_class(child, source, file_path, symbols, parent_name, exported)
            elif child.type in ("lexical_declaration", "variable_declaration"):
                self._extract_variable_decl(child, source, file_path, symbols, parent_name, exported)
            elif child.type == "export_statement":
                # Walk the export's children with exported=True
                self._walk_symbols(child, source, file_path, symbols, parent_name, is_exported=True)
            elif child.type == "expression_statement":
                self._extract_module_exports(child, source, symbols, parent_name)
            else:
                self._walk_symbols(child, source, file_path, symbols, parent_name, is_exported)

    def _is_export_node(self, node) -> bool:
        return node.type == "export_statement"

    def _extract_function(self, node, source, symbols, parent_name, is_exported, generator=False):
        name_node = node.child_by_field_name("name")
        if name_node is None:
            return
        name = self.node_text(name_node, source)
        params = node.child_by_field_name("parameters")
        prefix = "function*" if generator else "function"
        sig = f"{prefix} {name}({self._params_text(params, source)})"

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

    def _extract_class(self, node, source, file_path, symbols, parent_name, is_exported):
        name_node = node.child_by_field_name("name")
        if name_node is None:
            # Anonymous class (e.g. export default class extends LightningElement {})
            # Derive name from file path using LWC convention: myComponent.js -> MyComponent
            basename = os.path.basename(file_path)
            name = basename.rsplit(".", 1)[0]
            name = name[0].upper() + name[1:] if name else "Anonymous"
        else:
            name = self.node_text(name_node, source)
        sig = f"class {name}"

        def handle_class_heritage(child, qualified):
            for sub in child.children:
                if sub.type == "extends_clause":
                    # TS: extends_clause > identifier or type_identifier
                    for extends_node in sub.children:
                        if extends_node.type in ("identifier", "type_identifier"):
                            self._pending_inherits.append(
                                self._make_reference(
                                    target_name=self.node_text(extends_node, source),
                                    kind="inherits",
                                    line=node.start_point[0] + 1,
                                    source_name=qualified,
                                )
                            )
                            break
                elif sub.type == "implements_clause":
                    # TS: implements_clause > type_identifier (can be multiple)
                    for implementation in sub.children:
                        if implementation.type in ("type_identifier", "identifier"):
                            self._pending_inherits.append(
                                self._make_reference(
                                    target_name=self.node_text(implementation, source),
                                    kind="implements",
                                    line=node.start_point[0] + 1,
                                    source_name=qualified,
                                )
                            )
                elif sub.type == "identifier":
                    # Plain JS: class_heritage > identifier (no extends_clause wrapper)
                    self._pending_inherits.append(
                        self._make_reference(
                            target_name=self.node_text(sub, source),
                            kind="inherits",
                            line=node.start_point[0] + 1,
                            source_name=qualified,
                        )
                    )
            return f" {self.node_text(child, source)}"

        self._extract_class_skeleton(
            node,
            source,
            symbols,
            name=name,
            kind="class",
            parent_name=parent_name,
            signature=sig,
            visibility="public",
            is_exported=is_exported,
            child_handlers={"class_heritage": handle_class_heritage},
            body_walker=lambda body, qualified: self._extract_class_members(body, source, symbols, qualified),
        )

    def _extract_class_members(self, body_node, source, symbols, class_name):
        for child in body_node.children:
            self._extract_single_class_member(child, source, symbols, class_name)

    def _extract_single_class_member(self, child, source, symbols, class_name):
        if child.type not in ("method_definition", "public_field_definition", "field_definition"):
            return
        name_node = child.child_by_field_name("name") or child.child_by_field_name("property")
        if name_node is None:
            return
        name = self.node_text(name_node, source)
        qualified = f"{class_name}.{name}"

        if child.type == "method_definition":
            self._extract_method_member(child, source, symbols, class_name, name, qualified)
        else:
            self._extract_property_member(child, source, symbols, class_name, name, qualified)

    def _extract_method_member(self, child, source, symbols, class_name, name, qualified):
        sig = self._build_method_signature(child, source, name)
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
                parent_name=class_name,
            )
        )

    def _extract_property_member(self, child, source, symbols, class_name, name, qualified):
        symbols.append(
            self._make_symbol(
                name=name,
                kind="property",
                signature=f"static {name}" if "static" in self._collect_method_prefixes(child, source) else None,
                line_start=child.start_point[0] + 1,
                line_end=child.end_point[0] + 1,
                qualified_name=qualified,
                parent_name=class_name,
            )
        )

    def _build_method_signature(self, child, source, name):
        params = child.child_by_field_name("parameters")
        sig = f"{name}({self._params_text(params, source)})"
        prefixes = self._collect_method_prefixes(child, source)
        if prefixes:
            sig = " ".join(prefixes) + " " + sig
        return sig

    def _collect_method_prefixes(self, child, source):
        prefixes = []
        name_node = child.child_by_field_name("name") or child.child_by_field_name("property")
        for sub in child.children:
            if sub == name_node:
                continue
            text = self.node_text(sub, source)
            if sub.type in ("static", "async", "get", "set") or text in ("static", "async", "get", "set"):
                prefixes.append(text)
        return prefixes

    def _extract_variable_decl(self, node, source, file_path, symbols, parent_name, is_exported):
        """Extract const/let/var declarations, detecting function values."""
        decl_kind_text = ""
        for child in node.children:
            if child.type in ("const", "let", "var") or self.node_text(child, source) in (
                "const",
                "let",
                "var",
            ):
                decl_kind_text = self.node_text(child, source)
                break

        for child in node.children:
            if child.type == "variable_declarator":
                name_node = child.child_by_field_name("name")
                value_node = child.child_by_field_name("value")
                if name_node is None:
                    continue

                # Handle destructured bindings: const { a, b } = ... or const [a, b] = ...
                if name_node.type in ("object_pattern", "array_pattern"):
                    self._extract_destructured(
                        name_node,
                        node,
                        source,
                        symbols,
                        parent_name,
                        is_exported,
                        decl_kind_text,
                        value_node,
                    )
                    continue

                name = self.node_text(name_node, source)
                qualified = f"{parent_name}.{name}" if parent_name else name

                # Check if value is a function
                if value_node and value_node.type in (
                    "arrow_function",
                    "function_expression",
                    "generator_function",
                ):
                    params = value_node.child_by_field_name("parameters")
                    p_text = self._params_text(params, source)
                    if value_node.type == "arrow_function":
                        sig = f"const {name} = ({p_text}) =>"
                    else:
                        sig = f"const {name} = function({p_text})"

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
                elif value_node and value_node.type == "class":
                    # const Foo = class { ... }
                    sig = f"const {name} = class"
                    symbols.append(
                        self._make_symbol(
                            name=name,
                            kind="class",
                            line_start=node.start_point[0] + 1,
                            line_end=node.end_point[0] + 1,
                            qualified_name=qualified,
                            signature=sig,
                            is_exported=is_exported,
                            parent_name=parent_name,
                        )
                    )
                else:
                    kind = "constant" if decl_kind_text == "const" else "variable"
                    val_text = self.node_text(value_node, source)[:80] if value_node else ""
                    sig = f"{decl_kind_text} {name}" + (f" = {val_text}" if val_text else "")

                    symbols.append(
                        self._make_symbol(
                            name=name,
                            kind=kind,
                            line_start=node.start_point[0] + 1,
                            line_end=node.end_point[0] + 1,
                            qualified_name=qualified,
                            signature=sig,
                            is_exported=is_exported,
                            parent_name=parent_name,
                        )
                    )

                if value_node is not None and value_node.type == "object":
                    self._extract_class_members(value_node, source, symbols, qualified)

    def _member_assignment_parts(self, left, source):
        obj_node = left.child_by_field_name("object")
        prop_node = left.child_by_field_name("property")
        if obj_node is None or prop_node is None:
            return None
        prototype = False
        if obj_node.type == "member_expression":
            inner_obj = obj_node.child_by_field_name("object")
            inner_prop = obj_node.child_by_field_name("property")
            if inner_prop and self.node_text(inner_prop, source) == "prototype" and inner_obj:
                obj_node = inner_obj
                prototype = True
        return self.node_text(obj_node, source), self.node_text(prop_node, source), obj_node.type, prototype

    def _extract_member_assignment(self, child, left, right, source, symbols, node):
        """Extract symbol from a member_expression assignment (exports.X = ..., obj.method = ...)."""
        parts = self._member_assignment_parts(left, source)
        if parts is None:
            return
        obj_text, prop_name, base_type, prototype = parts
        assignment_owner = f"{obj_text}.prototype" if prototype else obj_text

        is_exports = obj_text in ("exports", "module.exports")

        if right.type == "identifier" and is_exports:
            rname = self.node_text(right, source)
            for sym in symbols:
                if sym["name"] == rname:
                    sym["is_exported"] = True
            return

        if right.type in ("function_expression", "arrow_function", "generator_function"):
            params = right.child_by_field_name("parameters")
            p_text = self._params_text(params, source)
            signature = f"{assignment_owner}.{prop_name} = function({p_text})"
            if not is_exports and base_type == "identifier":
                signature += ";"
            symbols.append(
                self._make_symbol(
                    name=prop_name,
                    kind="function",
                    line_start=child.start_point[0] + 1,
                    line_end=child.end_point[0] + 1,
                    qualified_name=f"{obj_text}.{prop_name}",
                    signature=signature,
                    docstring=self.get_docstring(node, source),
                    is_exported=is_exports,
                    parent_name=obj_text,
                )
            )
        else:
            val_text = self.node_text(right, source)[:80]
            symbols.append(
                self._make_symbol(
                    name=prop_name,
                    kind="constant",
                    line_start=child.start_point[0] + 1,
                    line_end=child.end_point[0] + 1,
                    qualified_name=f"{obj_text}.{prop_name}",
                    signature=f"{obj_text}.{prop_name} = {val_text}",
                    is_exported=is_exports,
                    parent_name=obj_text,
                )
            )

    def _extract_module_exports(self, node, source, symbols, parent_name):
        """Detect module.exports/exports assignments and obj.method assignments."""
        for child in node.children:
            if child.type != "assignment_expression":
                continue
            left = child.child_by_field_name("left")
            right = child.child_by_field_name("right")
            if left is None or right is None:
                continue

            left_text = self.node_text(left, source)

            if left_text in ("module.exports", "exports"):
                if right.type == "identifier":
                    name = self.node_text(right, source)
                    for sym in symbols:
                        if sym["name"] == name:
                            sym["is_exported"] = True
                elif right.type == "object":
                    self._extract_object_export_members(right, source, symbols)
                continue

            if left.type == "member_expression":
                self._extract_member_assignment(child, left, right, source, symbols, node)

    def _extract_object_export_members(self, obj_node, source, symbols):
        """Extract members from module.exports = { ... } object literal."""
        for child in obj_node.children:
            if child.type == "method_definition":
                name_node = child.child_by_field_name("name")
                if name_node is None:
                    continue
                name = self.node_text(name_node, source)
                params = child.child_by_field_name("parameters")
                sig = f"exports.{name}({self._params_text(params, source)})"
                symbols.append(
                    self._make_symbol(
                        name=name,
                        kind="function",
                        line_start=child.start_point[0] + 1,
                        line_end=child.end_point[0] + 1,
                        qualified_name=f"exports.{name}",
                        signature=sig,
                        is_exported=True,
                        parent_name="exports",
                    )
                )
            elif child.type == "pair":
                key_node = child.child_by_field_name("key")
                value_node = child.child_by_field_name("value")
                if key_node is None or value_node is None:
                    continue
                name = self.node_text(key_node, source)
                if value_node.type in (
                    "function_expression",
                    "arrow_function",
                    "generator_function",
                ):
                    params = value_node.child_by_field_name("parameters")
                    sig = f"exports.{name} = function({self._params_text(params, source)})"
                    symbols.append(
                        self._make_symbol(
                            name=name,
                            kind="function",
                            line_start=child.start_point[0] + 1,
                            line_end=child.end_point[0] + 1,
                            qualified_name=f"exports.{name}",
                            signature=sig,
                            is_exported=True,
                            parent_name="exports",
                        )
                    )
                else:
                    val_text = self.node_text(value_node, source)[:80]
                    symbols.append(
                        self._make_symbol(
                            name=name,
                            kind="constant",
                            line_start=child.start_point[0] + 1,
                            line_end=child.end_point[0] + 1,
                            qualified_name=f"exports.{name}",
                            signature=f"exports.{name} = {val_text}",
                            is_exported=True,
                            parent_name="exports",
                        )
                    )
            elif child.type == "shorthand_property_identifier":
                # { existingVar } — mark matching symbol as exported
                name = self.node_text(child, source)
                for sym in symbols:
                    if sym["name"] == name:
                        sym["is_exported"] = True

    def _extract_destructured(
        self,
        pattern_node,
        decl_node,
        source,
        symbols,
        parent_name,
        is_exported,
        decl_kind,
        value_node,
    ):
        """Extract individual bindings from destructured patterns."""
        names = self._collect_pattern_names(pattern_node, source)
        kind = "constant" if decl_kind == "const" else "variable"
        for name in names:
            qualified = f"{parent_name}.{name}" if parent_name else name
            sig = f"{decl_kind} {name}"
            symbols.append(
                self._make_symbol(
                    name=name,
                    kind=kind,
                    line_start=decl_node.start_point[0] + 1,
                    line_end=decl_node.end_point[0] + 1,
                    qualified_name=qualified,
                    signature=sig,
                    is_exported=is_exported,
                    parent_name=parent_name,
                )
            )

    _DESTRUCTURED_LOCAL_NAME_TYPES = frozenset(
        (
            "shorthand_property_identifier_pattern",
            "shorthand_property_identifier",
            "identifier",
        )
    )
    _DESTRUCTURED_CONTAINER_TYPES = frozenset(("object_pattern", "array_pattern"))

    def _collect_pattern_names(self, pattern_node, source, memo=None):
        """Collect all identifier names from a destructuring pattern."""
        if memo is None:
            memo = {}
        key = (pattern_node.type, pattern_node.start_byte, pattern_node.end_byte)
        cached = memo.get(key)
        if cached is not None:
            return list(cached)

        names = []
        for child in pattern_node.children:
            names.extend(self._declared_names_from_destructuring_child(child, source, memo))
        memo[key] = tuple(names)
        return names

    def _declared_names_from_destructuring_child(self, child, source, memo):
        """Return only local bindings, not object keys, from one pattern child."""
        if child.type in self._DESTRUCTURED_LOCAL_NAME_TYPES:
            return [self.node_text(child, source)]

        if child.type == "pair_pattern":
            return self._declared_names_from_property_binding_value(child, source, memo)

        if child.type == "rest_pattern":
            return [self.node_text(sub, source) for sub in child.children if sub.type == "identifier"]

        if child.type == "assignment_pattern":
            return self._declared_names_from_default_binding_left(child, source)

        if child.type in self._DESTRUCTURED_CONTAINER_TYPES:
            return self._collect_pattern_names(child, source, memo)

        return []

    def _declared_names_from_property_binding_value(self, child, source, memo):
        value = child.child_by_field_name("value")
        if value is None:
            return []
        if value.type == "identifier":
            return [self.node_text(value, source)]
        if value.type in self._DESTRUCTURED_CONTAINER_TYPES:
            return self._collect_pattern_names(value, source, memo)
        return []

    def _declared_names_from_default_binding_left(self, child, source):
        left = child.child_by_field_name("left")
        if left is None:
            return []
        if left.type in self._DESTRUCTURED_LOCAL_NAME_TYPES:
            return [self.node_text(left, source)]
        return []

    # ---- Reference extraction ----

    # JS keywords to skip when extracting identifier references from arguments
    _JS_KEYWORDS = frozenset(
        {
            "true",
            "false",
            "null",
            "undefined",
            "this",
            "super",
            "arguments",
            "new",
            "void",
            "typeof",
            "instanceof",
            "in",
            "of",
            "async",
            "await",
            "yield",
            "return",
            "throw",
            "delete",
            "NaN",
            "Infinity",
        }
    )

    def _emit_argument_identifier_ref(self, child, source, refs, scope_name) -> None:
        """Bug 2 — identifiers passed as function arguments (callbacks by
        reference, e.g. ``addEventListener('keydown', handleKeyboardShortcut)``)."""
        name = self.node_text(child, source)
        if name and name not in self._JS_KEYWORDS and not self._is_local_value(child, name, source):
            refs.append(
                self._make_reference(
                    target_name=name,
                    kind="reference",
                    line=child.start_point[0] + 1,
                    source_name=scope_name,
                )
            )

    def _emit_shorthand_property_ref(self, child, source, refs, scope_name) -> None:
        """Bug 3 — shorthand properties are always variable references
        (e.g. ``defineExpose({ resetForm, loadUserData })``). Leaf node, so
        no recursion needed."""
        name = self.node_text(child, source)
        if name and not self._is_local_value(child, name, source):
            refs.append(
                self._make_reference(
                    target_name=name,
                    kind="reference",
                    line=child.start_point[0] + 1,
                    source_name=scope_name,
                )
            )

    def _scope_name_for_child(self, child, source, scope_name) -> str:
        """Decide the scope name to use when recursing into ``child``. For
        function/class/generator declarations and const/let/var declarations
        the scope expands; otherwise it inherits the parent scope."""
        if child.type in (
            "function_declaration",
            "class_declaration",
            "generator_function_declaration",
        ):
            n = child.child_by_field_name("name")
            if n:
                fname = self.node_text(n, source)
                return f"{scope_name}.{fname}" if scope_name else fname
            return scope_name
        if child.type in ("lexical_declaration", "variable_declaration"):
            for sub in child.children:
                if sub.type == "variable_declarator":
                    n = sub.child_by_field_name("name")
                    if n and n.type == "identifier":
                        vname = self.node_text(n, source)
                        return f"{scope_name}.{vname}" if scope_name else vname
                    break
        if child.type in {"method_definition", "field_definition", "public_field_definition"} and scope_name:
            name = child.child_by_field_name("name") or child.child_by_field_name("property")
            if name is not None:
                return f"{scope_name}.{self.node_text(name, source)}"
        return scope_name

    def _call_scope(self, node, source):
        """Read the lexical owner of this call, independent of declaration references."""
        ancestors = []
        ancestor = node.parent
        while ancestor is not None:
            ancestors.append(ancestor)
            ancestor = ancestor.parent
        scope = None
        for ancestor in reversed(ancestors):
            scope = self._call_scope_name_for_child(ancestor, source, scope)
        return scope

    def _call_scope_name_for_child(self, child, source, scope_name) -> str | None:
        """Extend the scope at a named declaration or member."""
        if child.type in (
            "function_declaration",
            "class_declaration",
            "abstract_class_declaration",
            "generator_function_declaration",
            "method_definition",
            "internal_module",
        ):
            name = child.child_by_field_name("name")
            if name and name.type not in ("object_pattern", "array_pattern"):
                text = self.node_text(name, source)
                return f"{scope_name}.{text}" if scope_name else text
        elif child.type in ("field_definition", "public_field_definition"):
            value = child.child_by_field_name("value")
            name = child.child_by_field_name("name") or child.child_by_field_name("property")
            if scope_name and name and value and value.type in self._FUNCTION_NODES:
                return f"{scope_name}.{self.node_text(name, source)}"
        elif child.type == "variable_declarator":
            name = child.child_by_field_name("name")
            value = child.child_by_field_name("value")
            if (
                name
                and name.type == "identifier"
                and value
                and (value.type in self._FUNCTION_NODES or value.type in ("class", "object"))
            ):
                text = self.node_text(name, source)
                return f"{scope_name}.{text}" if scope_name else text
        elif child.type == "assignment_expression":
            left = child.child_by_field_name("left")
            value = child.child_by_field_name("right")
            if left and value:
                text = self.node_text(left, source)
                if text in ("module.exports", "exports") and value.type == "object":
                    return "exports"
                if left.type == "member_expression" and value.type in (
                    "arrow_function",
                    "function_expression",
                    "generator_function",
                ):
                    parts = self._member_assignment_parts(left, source)
                    if parts:
                        owner, member, _, _ = parts
                        qualified = f"{owner}.{member}"
                        return f"{scope_name}.{qualified}" if scope_name else qualified
        elif child.type == "pair" and scope_name == "exports":
            key = child.child_by_field_name("key")
            if key is not None:
                return f"exports.{self.node_text(key, source)}"
        return scope_name

    def _walk_refs(self, node, source, refs, scope_name):
        for child in node.children:
            ctype = child.type
            if ctype == "import_statement":
                self._extract_esm_import(child, source, refs, scope_name)
            elif ctype == "export_statement":
                self._extract_export_refs(child, source, refs, scope_name)
            elif ctype == "call_expression":
                self._extract_call(child, source, refs, scope_name)
            elif ctype == "new_expression":
                self._extract_new(child, source, refs, scope_name)
            elif ctype in ("jsx_self_closing_element", "jsx_opening_element"):
                self._extract_jsx_element_refs(child, source, refs, scope_name)
            elif ctype == "identifier" and node.type in ("arguments", "jsx_expression"):
                self._emit_argument_identifier_ref(child, source, refs, scope_name)
            elif ctype == "shorthand_property_identifier":
                self._emit_shorthand_property_ref(child, source, refs, scope_name)
            else:
                self._walk_refs(child, source, refs, self._scope_name_for_child(child, source, scope_name))

    def _extract_jsx_element_refs(self, node, source, refs, scope_name):
        """Emit a call edge for user-defined JSX components and recurse into
        attribute expressions to capture prop callback references.

        User-defined components start with an uppercase letter (React convention).
        Native elements (div, span, input…) are lowercase and are skipped.
        ``<Foo.Bar />`` is emitted as a reference to the full dotted name.
        """
        for ch in node.children:
            if ch.type == "<":
                continue
            if ch.type == "identifier":
                name = self.node_text(ch, source)
                if name and name[0].isupper():
                    refs.append(
                        self._make_reference(
                            target_name=name,
                            kind="call",
                            line=ch.start_point[0] + 1,
                            source_name=self._call_scope(node, source),
                        )
                    )
                break
            if ch.type == "member_expression":
                name = self.node_text(ch, source)
                if name:
                    refs.append(
                        self._make_reference(
                            target_name=name,
                            kind="reference",
                            line=ch.start_point[0] + 1,
                            source_name=scope_name,
                        )
                    )
                break
            # Stop scanning tag name on anything that is not '<'
            break
        # Recurse to capture jsx_attribute → jsx_expression → identifier refs
        self._walk_refs(node, source, refs, scope_name)

    def _extract_export_refs(self, export_stmt, source, refs, scope_name):
        """Emit reference edges for named export and re-export statements.

        Handles:
          export { X }             -> reference edge to X (local re-export)
          export { X as Y }        -> reference edge to X (local name)
          export { X } from '...'  -> import edge for X from the module
          export * from '...'      -> import edge for wildcard re-export
        Delegates everything else (export function/class/let/const) to the
        normal _walk_refs recursion so call-graph edges are not lost.
        """
        # Find the from-string child, if present
        from_path = None
        for ch in export_stmt.children:
            if ch.type == "string":
                frags = [c for c in ch.children if c.type == "string_fragment"]
                if frags:
                    from_path = self.node_text(frags[0], source)
                break

        # Named export clause: export { X } or export { X } from '...'
        for ch in export_stmt.children:
            if ch.type == "export_clause":
                for spec in ch.children:
                    if spec.type == "export_specifier":
                        # First identifier is the local name being exported
                        local_id = next((s for s in spec.children if s.type == "identifier"), None)
                        if local_id:
                            local_name = self.node_text(local_id, source)
                            if from_path:
                                refs.append(
                                    self._make_reference(
                                        target_name=local_name,
                                        kind="import",
                                        line=spec.start_point[0] + 1,
                                        source_name=scope_name,
                                        import_path=from_path,
                                    )
                                )
                            else:
                                refs.append(
                                    self._make_reference(
                                        target_name=local_name,
                                        kind="reference",
                                        line=spec.start_point[0] + 1,
                                        source_name=scope_name,
                                    )
                                )
                return  # Handled — no further recursion needed

        # export * from '...' — wildcard re-export
        for ch in export_stmt.children:
            if ch.type == "*" or (hasattr(ch, "text") and ch.text == b"*"):
                if from_path:
                    refs.append(
                        self._make_reference(
                            target_name="*",
                            kind="import",
                            line=export_stmt.start_point[0] + 1,
                            source_name=scope_name,
                            import_path=f"{from_path}.*",
                        )
                    )
                return

        # export function/class/const/let/var — recurse normally
        self._walk_refs(export_stmt, source, refs, scope_name)

    def _resolve_salesforce_import(self, path: str) -> tuple[str, str] | None:
        """Resolve @salesforce/* import paths to (target_name, edge_kind).

        Handles:
          @salesforce/apex/ClassName.methodName  -> (ClassName.methodName, call)
          @salesforce/schema/Object.Field        -> (Object.Field, schema_ref)
          @salesforce/label/c.LabelName          -> (Label.LabelName, label)
          @salesforce/messageChannel/Channel__c  -> (Channel__c, import)
        """
        if not path.startswith("@salesforce/"):
            return None
        rest = path[len("@salesforce/") :]
        if rest.startswith("apex/"):
            target = rest[len("apex/") :]
            return (target, "call")
        if rest.startswith("schema/"):
            target = rest[len("schema/") :]
            return (target, "schema_ref")
        if rest.startswith("label/"):
            target = rest[len("label/") :]
            # Normalise c.LabelName -> Label.LabelName
            if target.startswith("c."):
                target = "Label." + target[2:]
            return (target, "label")
        if rest.startswith("messageChannel/"):
            target = rest[len("messageChannel/") :]
            return (target, "import")
        return None

    def _named_import_names(self, node, source):
        """Names bound by a named_imports node: ``import { a, b as c }``."""
        names = []
        for spec in node.children:
            if spec.type != "import_specifier":
                continue
            name_node = spec.child_by_field_name("name")
            if name_node:
                names.append(self.node_text(name_node, source))
        return names

    def _namespace_import_names(self, node, source):
        """Name bound by a namespace_import node: ``import * as ns``."""
        return [self.node_text(ns_child, source) for ns_child in node.children if ns_child.type == "identifier"]

    def _import_clause_names(self, clause, source):
        """Dispatch each import_clause child to its grammar-specific extractor."""
        names = []
        for sub in clause.children:
            if sub.type == "identifier":
                names.append(self.node_text(sub, source))
            elif sub.type == "named_imports":
                names.extend(self._named_import_names(sub, source))
            elif sub.type == "namespace_import":
                names.extend(self._namespace_import_names(sub, source))
        return names

    def _collect_import_names(self, node, source):
        """Collect imported symbol names from an import_clause."""
        names = []
        for child in node.children:
            if child.type == "import_clause":
                names.extend(self._import_clause_names(child, source))
        return names

    _IDENT_RE = r"[A-Za-z_$][A-Za-z0-9_$]*"
    _DYN_IMPORT_THEN_DESTRUCTURED_RE = re.compile(
        r"import\s*\(\s*(['\"])(?P<path>[^'\"]+)\1\s*\)"
        r"\s*\.\s*then\s*\(\s*\(\s*\{(?P<names>[^}]*)\}\s*\)\s*=>",
        re.DOTALL,
    )
    _DYN_IMPORT_AWAIT_DESTRUCTURED_RE = re.compile(
        r"(?:const|let|var)\s*\{(?P<names>[^}]*)\}\s*="
        r"\s*await\s+import\s*\(\s*(['\"])(?P<path>[^'\"]+)\2\s*\)",
        re.DOTALL,
    )

    def _dynamic_import_line(self, text: str, pos: int) -> int:
        return text.count("\n", 0, pos) + 1

    def _destructured_export_names(self, text: str) -> list[str]:
        names: list[str] = []
        for raw in text.split(","):
            part = raw.strip()
            if not part or part.startswith("..."):
                continue
            part = part.split("=", 1)[0].strip()
            if ":" in part:
                part = part.split(":", 1)[0].strip()
            if re.match(rf"^{self._IDENT_RE}$", part):
                names.append(part)
        return names

    def _make_dynamic_import_ref(self, name: str, path: str, line: int) -> dict:
        return self._make_reference(
            target_name=name,
            kind="import",
            line=line,
            source_name=None,
            import_path=path,
        )

    def _extract_dynamic_import_refs(self, source: bytes) -> list[dict]:
        """Extract named consumers from destructured dynamic imports."""
        text = source.decode("utf-8", errors="replace")
        refs: list[dict] = []
        seen: set[tuple[str, str, int]] = set()

        def add(name: str, path: str, line: int) -> None:
            key = (name, path, line)
            if key in seen:
                return
            seen.add(key)
            refs.append(self._make_dynamic_import_ref(name, path, line))

        for match in self._DYN_IMPORT_THEN_DESTRUCTURED_RE.finditer(text):
            line = self._dynamic_import_line(text, match.start())
            for name in self._destructured_export_names(match.group("names")):
                add(name, match.group("path"), line)

        for match in self._DYN_IMPORT_AWAIT_DESTRUCTURED_RE.finditer(text):
            line = self._dynamic_import_line(text, match.start())
            for name in self._destructured_export_names(match.group("names")):
                add(name, match.group("path"), line)

        return refs

    def _extract_esm_import(self, node, source, refs, scope_name):
        """Extract ESM import statements."""
        source_node = node.child_by_field_name("source")
        if source_node is None:
            return
        path = self.node_text(source_node, source).strip("'\"")

        # Resolve @salesforce/* imports to semantic targets
        sf_resolved = self._resolve_salesforce_import(path)
        if sf_resolved is not None:
            sf_target, sf_kind = sf_resolved
            refs.append(
                self._make_reference(
                    target_name=sf_target,
                    kind=sf_kind,
                    line=node.start_point[0] + 1,
                    source_name=scope_name,
                    import_path=path,
                )
            )
            return

        names = self._collect_import_names(node, source)
        if names:
            for name in names:
                refs.append(
                    self._make_reference(
                        target_name=name,
                        kind="import",
                        line=node.start_point[0] + 1,
                        source_name=scope_name,
                        import_path=path,
                    )
                )
        else:
            refs.append(
                self._make_reference(
                    target_name=path,
                    kind="import",
                    line=node.start_point[0] + 1,
                    source_name=scope_name,
                    import_path=path,
                )
            )

    def _extract_call(self, node, source, refs, scope_name):
        func_node = node.child_by_field_name("function")
        if func_node is None:
            return

        evidence = {}
        # Handle method calls: obj.method() -> extract "method"
        if func_node.type == "member_expression":
            evidence = self._member_evidence(func_node, source)
            prop = func_node.child_by_field_name("property")
            if prop:
                name = self.node_text(prop, source)
            else:
                name = self.node_text(func_node, source)
        else:
            name = self.node_text(func_node, source)

        # Special handling for require() - use module name as target
        if name == "require" and self._record_static_require_import_for_dependency_precision(
            node, source, refs, scope_name
        ):
            return

        if func_node.type == "identifier" and self._is_local_value(node, name, source):
            args = node.child_by_field_name("arguments")
            if args is not None:
                self._walk_refs(args, source, refs, scope_name)
            return

        ref = self._make_reference(
            target_name=name,
            kind="call",
            line=node.start_point[0] + 1,
            source_name=self._call_scope(node, source),
        )
        if not evidence and name in self._member_imports and self._lexical_binding(node, name, source) is None:
            path, exported = self._member_imports[name]
            if exported in {"default", "export="}:
                evidence = {"binding_import_path": path, "binding_export": exported}
        ref.update(evidence)
        refs.append(ref)

        # Recurse into arguments for nested calls
        args = node.child_by_field_name("arguments")
        if args:
            self._walk_refs(args, source, refs, scope_name)

    def _record_static_require_import_for_dependency_precision(self, node, source, refs, scope_name):
        """Return True when require("path") is emitted as a dependency edge."""
        args = node.child_by_field_name("arguments")
        if args is None:
            return False
        for arg_child in args.children:
            if arg_child.type != "string":
                continue
            path = self.node_text(arg_child, source).strip("'\"")
            # Use last path segment as target name
            target = path.rsplit("/", 1)[-1] if "/" in path else path
            # Strip .js/.json extension
            for ext in (".js", ".json", ".mjs", ".cjs"):
                if target.endswith(ext):
                    target = target[: -len(ext)]
                    break
            refs.append(
                self._make_reference(
                    target_name=target,
                    kind="import",
                    line=node.start_point[0] + 1,
                    source_name=scope_name,
                    import_path=path,
                )
            )
            return True
        return False

    def _extract_new(self, node, source, refs, scope_name):
        """Extract new expressions: new Foo(), new module.Foo()."""
        ctor = node.child_by_field_name("constructor")
        if ctor is None:
            return

        evidence = {}
        # Handle new module.Foo() -> extract "Foo"
        if ctor.type == "member_expression":
            evidence = self._member_evidence(ctor, source)
            prop = ctor.child_by_field_name("property")
            if prop:
                name = self.node_text(prop, source)
            else:
                name = self.node_text(ctor, source)
        else:
            name = self.node_text(ctor, source)

        if ctor.type == "identifier" and self._is_local_value(node, name, source):
            args = node.child_by_field_name("arguments")
            if args is not None:
                self._walk_refs(args, source, refs, scope_name)
            return

        ref = self._make_reference(
            target_name=name,
            kind="call",
            line=ctor.start_point[0] + 1,
            source_name=self._call_scope(node, source),
        )
        if not evidence and name in self._member_imports and self._lexical_binding(node, name, source) is None:
            path, exported = self._member_imports[name]
            if exported in {"default", "export="}:
                evidence = {"binding_import_path": path, "binding_export": exported}
        ref.update(evidence)
        refs.append(ref)

        # Recurse into arguments for nested calls/refs
        args = node.child_by_field_name("arguments")
        if args:
            self._walk_refs(args, source, refs, scope_name)
