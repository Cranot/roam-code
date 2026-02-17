
from __future__ import annotations
import os
from .base import LanguageExtractor


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
        if not hasattr(self, '_pending_inherits'):
            self._pending_inherits = []
        self._walk_symbols(tree.root_node, source, file_path, symbols, parent_name=None, is_exported=False)
        return symbols

    def extract_references(self, tree, source: bytes, file_path: str) -> list[dict]:
        refs = []
        self._walk_refs(tree.root_node, source, refs, scope_name=None)
        # Collect inheritance refs accumulated during extract_symbols
        refs.extend(getattr(self, '_pending_inherits', []))
        self._pending_inherits = []
        return refs

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
        qualified = f"{parent_name}.{name}" if parent_name else name

        # Check for extends/implements
        for child in node.children:
            if child.type == "class_heritage":
                sig += f" {self.node_text(child, source)}"
                for sub in child.children:
                    if sub.type == "extends_clause":
                        # TS: extends_clause > identifier or type_identifier
                        for exn in sub.children:
                            if exn.type in ("identifier", "type_identifier"):
                                self._pending_inherits.append(self._make_reference(
                                    target_name=self.node_text(exn, source),
                                    kind="inherits",
                                    line=node.start_point[0] + 1,
                                    source_name=qualified,
                                ))
                                break
                    elif sub.type == "implements_clause":
                        # TS: implements_clause > type_identifier (can be multiple)
                        for imp in sub.children:
                            if imp.type in ("type_identifier", "identifier"):
                                self._pending_inherits.append(self._make_reference(
                                    target_name=self.node_text(imp, source),
                                    kind="implements",
                                    line=node.start_point[0] + 1,
                                    source_name=qualified,
                                ))
                    elif sub.type == "identifier":
                        # Plain JS: class_heritage > identifier (no extends_clause wrapper)
                        self._pending_inherits.append(self._make_reference(
                            target_name=self.node_text(sub, source),
                            kind="inherits",
                            line=node.start_point[0] + 1,
                            source_name=qualified,
                        ))
                break
        symbols.append(self._make_symbol(
            name=name,
            kind="class",
            line_start=node.start_point[0] + 1,
            line_end=node.end_point[0] + 1,
            qualified_name=qualified,
            signature=sig,
            docstring=self.get_docstring(node, source),
            is_exported=is_exported,
            parent_name=parent_name,
        ))

        # Walk class body for methods
        body = node.child_by_field_name("body")
        if body:
            self._extract_class_members(body, source, symbols, qualified)

    def _extract_class_members(self, body_node, source, symbols, class_name):
        for child in body_node.children:
            if child.type in ("method_definition", "public_field_definition", "field_definition"):
                name_node = child.child_by_field_name("name")
                if name_node is None:
                    continue
                name = self.node_text(name_node, source)
                qualified = f"{class_name}.{name}"

                if child.type == "method_definition":
                    params = child.child_by_field_name("parameters")
                    sig = f"{name}({self._params_text(params, source)})"

                    # Check for static/async/get/set
                    prefixes = []
                    for sub in child.children:
                        if sub.type in ("static", "async", "get", "set") or self.node_text(sub, source) in ("static", "async", "get", "set"):
                            if sub == name_node:
                                continue
                            t = self.node_text(sub, source)
                            if t in ("static", "async", "get", "set"):
                                prefixes.append(t)
                    if prefixes:
                        sig = " ".join(prefixes) + " " + sig

                    kind = "constructor" if name == "constructor" else "method"
                    symbols.append(self._make_symbol(
                        name=name,
                        kind=kind,
                        line_start=child.start_point[0] + 1,
                        line_end=child.end_point[0] + 1,
                        qualified_name=qualified,
                        signature=sig,
                        docstring=self.get_docstring(child, source),
                        parent_name=class_name,
                    ))
                else:
                    # Field/property
                    symbols.append(self._make_symbol(
                        name=name,
                        kind="property",
                        line_start=child.start_point[0] + 1,
                        line_end=child.end_point[0] + 1,
                        qualified_name=qualified,
                        parent_name=class_name,
                    ))

    def _extract_variable_decl(self, node, source, file_path, symbols, parent_name, is_exported):
        """Extract const/let/var declarations, detecting function values."""
        decl_kind_text = ""
        for child in node.children:
            if child.type in ("const", "let", "var") or self.node_text(child, source) in ("const", "let", "var"):
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
                        name_node, node, source, symbols,
                        parent_name, is_exported, decl_kind_text, value_node,
                    )
                    continue

                name = self.node_text(name_node, source)
                qualified = f"{parent_name}.{name}" if parent_name else name

                # Check if value is a function
                if value_node and value_node.type in ("arrow_function", "function_expression", "generator_function"):
                    params = value_node.child_by_field_name("parameters")
                    p_text = self._params_text(params, source)
                    if value_node.type == "arrow_function":
                        sig = f"const {name} = ({p_text}) =>"
                    else:
                        sig = f"const {name} = function({p_text})"

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
                elif value_node and value_node.type == "class":
                    # const Foo = class { ... }
                    sig = f"const {name} = class"
                    symbols.append(self._make_symbol(
                        name=name,
                        kind="class",
                        line_start=node.start_point[0] + 1,
                        line_end=node.end_point[0] + 1,
                        qualified_name=qualified,
                        signature=sig,
                        is_exported=is_exported,
                        parent_name=parent_name,
                    ))
                else:
                    kind = "constant" if decl_kind_text == "const" else "variable"
                    val_text = self.node_text(value_node, source)[:80] if value_node else ""
                    sig = f"{decl_kind_text} {name}" + (f" = {val_text}" if val_text else "")

                    symbols.append(self._make_symbol(
                        name=name,
                        kind=kind,
                        line_start=node.start_point[0] + 1,
                        line_end=node.end_point[0] + 1,
                        qualified_name=qualified,
                        signature=sig,
                        is_exported=is_exported,
                        parent_name=parent_name,
                    ))

    def _extract_member_assignment(self, child, left, right, source, symbols, node):
        """Extract symbol from a member_expression assignment (exports.X = ..., obj.method = ...)."""
        obj_node = left.child_by_field_name("object")
        prop_node = left.child_by_field_name("property")
        if obj_node is None or prop_node is None:
            return
        obj_text = self.node_text(obj_node, source)
        prop_name = self.node_text(prop_node, source)

        if obj_node.type == "member_expression":
            inner_obj = obj_node.child_by_field_name("object")
            inner_prop = obj_node.child_by_field_name("property")
            if inner_prop and self.node_text(inner_prop, source) == "prototype" and inner_obj:
                obj_text = self.node_text(inner_obj, source)

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
            symbols.append(self._make_symbol(
                name=prop_name, kind="function",
                line_start=child.start_point[0] + 1,
                line_end=child.end_point[0] + 1,
                qualified_name=f"{obj_text}.{prop_name}",
                signature=f"{obj_text}.{prop_name} = function({p_text})",
                docstring=self.get_docstring(node, source),
                is_exported=is_exports, parent_name=obj_text,
            ))
        else:
            val_text = self.node_text(right, source)[:80]
            symbols.append(self._make_symbol(
                name=prop_name, kind="constant",
                line_start=child.start_point[0] + 1,
                line_end=child.end_point[0] + 1,
                qualified_name=f"{obj_text}.{prop_name}",
                signature=f"{obj_text}.{prop_name} = {val_text}",
                is_exported=is_exports, parent_name=obj_text,
            ))

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
                symbols.append(self._make_symbol(
                    name=name,
                    kind="function",
                    line_start=child.start_point[0] + 1,
                    line_end=child.end_point[0] + 1,
                    qualified_name=f"exports.{name}",
                    signature=sig,
                    is_exported=True,
                    parent_name="exports",
                ))
            elif child.type == "pair":
                key_node = child.child_by_field_name("key")
                value_node = child.child_by_field_name("value")
                if key_node is None or value_node is None:
                    continue
                name = self.node_text(key_node, source)
                if value_node.type in ("function_expression", "arrow_function", "generator_function"):
                    params = value_node.child_by_field_name("parameters")
                    sig = f"exports.{name} = function({self._params_text(params, source)})"
                    symbols.append(self._make_symbol(
                        name=name,
                        kind="function",
                        line_start=child.start_point[0] + 1,
                        line_end=child.end_point[0] + 1,
                        qualified_name=f"exports.{name}",
                        signature=sig,
                        is_exported=True,
                        parent_name="exports",
                    ))
                else:
                    val_text = self.node_text(value_node, source)[:80]
                    symbols.append(self._make_symbol(
                        name=name,
                        kind="constant",
                        line_start=child.start_point[0] + 1,
                        line_end=child.end_point[0] + 1,
                        qualified_name=f"exports.{name}",
                        signature=f"exports.{name} = {val_text}",
                        is_exported=True,
                        parent_name="exports",
                    ))
            elif child.type == "shorthand_property_identifier":
                # { existingVar } — mark matching symbol as exported
                name = self.node_text(child, source)
                for sym in symbols:
                    if sym["name"] == name:
                        sym["is_exported"] = True

    def _extract_destructured(self, pattern_node, decl_node, source, symbols,
                              parent_name, is_exported, decl_kind, value_node):
        """Extract individual bindings from destructured patterns."""
        names = self._collect_pattern_names(pattern_node, source)
        kind = "constant" if decl_kind == "const" else "variable"
        for name in names:
            qualified = f"{parent_name}.{name}" if parent_name else name
            sig = f"{decl_kind} {name}"
            symbols.append(self._make_symbol(
                name=name,
                kind=kind,
                line_start=decl_node.start_point[0] + 1,
                line_end=decl_node.end_point[0] + 1,
                qualified_name=qualified,
                signature=sig,
                is_exported=is_exported,
                parent_name=parent_name,
            ))

    def _collect_pattern_names(self, pattern_node, source):
        """Collect all identifier names from a destructuring pattern."""
        names = []
        for child in pattern_node.children:
            if child.type in ("shorthand_property_identifier_pattern",
                              "shorthand_property_identifier",
                              "identifier"):
                names.append(self.node_text(child, source))
            elif child.type == "pair_pattern":
                # { key: localVar } — extract the local binding
                value = child.child_by_field_name("value")
                if value:
                    if value.type == "identifier":
                        names.append(self.node_text(value, source))
                    elif value.type in ("object_pattern", "array_pattern"):
                        names.extend(self._collect_pattern_names(value, source))
            elif child.type == "rest_pattern":
                for sub in child.children:
                    if sub.type == "identifier":
                        names.append(self.node_text(sub, source))
            elif child.type == "assignment_pattern":
                # { x = defaultValue } — extract x
                left = child.child_by_field_name("left")
                if left:
                    if left.type == "identifier":
                        names.append(self.node_text(left, source))
                    elif left.type in ("shorthand_property_identifier_pattern",
                                       "shorthand_property_identifier"):
                        names.append(self.node_text(left, source))
            elif child.type in ("object_pattern", "array_pattern"):
                names.extend(self._collect_pattern_names(child, source))
        return names

    # ---- Reference extraction ----

    # JS keywords to skip when extracting identifier references from arguments
    _JS_KEYWORDS = frozenset({
        "true", "false", "null", "undefined", "this", "super", "arguments",
        "new", "void", "typeof", "instanceof", "in", "of", "async", "await",
        "yield", "return", "throw", "delete", "NaN", "Infinity",
    })

    def _walk_refs(self, node, source, refs, scope_name):
        for child in node.children:
            if child.type == "import_statement":
                self._extract_esm_import(child, source, refs, scope_name)
            elif child.type == "export_statement":
                self._walk_refs(child, source, refs, scope_name)
            elif child.type == "call_expression":
                self._extract_call(child, source, refs, scope_name)
            elif child.type == "new_expression":
                self._extract_new(child, source, refs, scope_name)
            elif child.type == "identifier" and node.type == "arguments":
                # Bug 2: Identifiers passed as function arguments (callbacks by reference)
                # e.g. addEventListener('keydown', handleKeyboardShortcut)
                name = self.node_text(child, source)
                if name and name not in self._JS_KEYWORDS:
                    refs.append(self._make_reference(
                        target_name=name,
                        kind="reference",
                        line=child.start_point[0] + 1,
                        source_name=scope_name,
                    ))
            elif child.type == "shorthand_property_identifier":
                # Bug 3: Shorthand properties are always variable references
                # e.g. defineExpose({ resetForm, populateFromKinisi })
                name = self.node_text(child, source)
                if name:
                    refs.append(self._make_reference(
                        target_name=name,
                        kind="reference",
                        line=child.start_point[0] + 1,
                        source_name=scope_name,
                    ))
                # No recursion needed — shorthand_property_identifier is a leaf node
            else:
                new_scope = scope_name
                if child.type in ("function_declaration", "class_declaration", "generator_function_declaration"):
                    n = child.child_by_field_name("name")
                    if n:
                        fname = self.node_text(n, source)
                        new_scope = f"{scope_name}.{fname}" if scope_name else fname
                elif child.type in ("lexical_declaration", "variable_declaration"):
                    # Track const/let/var declarations as scope for their initializers
                    for sub in child.children:
                        if sub.type == "variable_declarator":
                            n = sub.child_by_field_name("name")
                            if n and n.type == "identifier":
                                vname = self.node_text(n, source)
                                new_scope = f"{scope_name}.{vname}" if scope_name else vname
                                break
                self._walk_refs(child, source, refs, new_scope)

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
        rest = path[len("@salesforce/"):]
        if rest.startswith("apex/"):
            target = rest[len("apex/"):]
            return (target, "call")
        if rest.startswith("schema/"):
            target = rest[len("schema/"):]
            return (target, "schema_ref")
        if rest.startswith("label/"):
            target = rest[len("label/"):]
            # Normalise c.LabelName -> Label.LabelName
            if target.startswith("c."):
                target = "Label." + target[2:]
            return (target, "label")
        if rest.startswith("messageChannel/"):
            target = rest[len("messageChannel/"):]
            return (target, "import")
        return None

    def _collect_import_names(self, node, source):
        """Collect imported symbol names from an import_clause."""
        names = []
        for child in node.children:
            if child.type == "import_clause":
                for sub in child.children:
                    if sub.type == "identifier":
                        names.append(self.node_text(sub, source))
                    elif sub.type == "named_imports":
                        for spec in sub.children:
                            if spec.type == "import_specifier":
                                name_node = spec.child_by_field_name("name")
                                if name_node:
                                    names.append(self.node_text(name_node, source))
                    elif sub.type == "namespace_import":
                        for ns_child in sub.children:
                            if ns_child.type == "identifier":
                                names.append(self.node_text(ns_child, source))
        return names

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
            refs.append(self._make_reference(
                target_name=sf_target, kind=sf_kind,
                line=node.start_point[0] + 1, source_name=scope_name,
                import_path=path,
            ))
            return

        names = self._collect_import_names(node, source)
        if names:
            for name in names:
                refs.append(self._make_reference(
                    target_name=name, kind="import",
                    line=node.start_point[0] + 1, source_name=scope_name,
                    import_path=path,
                ))
        else:
            refs.append(self._make_reference(
                target_name=path, kind="import",
                line=node.start_point[0] + 1, source_name=scope_name,
                import_path=path,
            ))

    def _extract_call(self, node, source, refs, scope_name):
        func_node = node.child_by_field_name("function")
        if func_node is None:
            return

        # Handle method calls: obj.method() -> extract "method"
        if func_node.type == "member_expression":
            prop = func_node.child_by_field_name("property")
            if prop:
                name = self.node_text(prop, source)
            else:
                name = self.node_text(func_node, source)
        else:
            name = self.node_text(func_node, source)

        # Special handling for require() - use module name as target
        if name == "require":
            args = node.child_by_field_name("arguments")
            if args:
                for arg_child in args.children:
                    if arg_child.type == "string":
                        path = self.node_text(arg_child, source).strip("'\"")
                        # Use last path segment as target name
                        target = path.rsplit("/", 1)[-1] if "/" in path else path
                        # Strip .js/.json extension
                        for ext in (".js", ".json", ".mjs", ".cjs"):
                            if target.endswith(ext):
                                target = target[:-len(ext)]
                                break
                        refs.append(self._make_reference(
                            target_name=target,
                            kind="import",
                            line=node.start_point[0] + 1,
                            source_name=scope_name,
                            import_path=path,
                        ))
                        return

        refs.append(self._make_reference(
            target_name=name,
            kind="call",
            line=node.start_point[0] + 1,
            source_name=scope_name,
        ))

        # Recurse into arguments for nested calls
        args = node.child_by_field_name("arguments")
        if args:
            self._walk_refs(args, source, refs, scope_name)

    def _extract_new(self, node, source, refs, scope_name):
        """Extract new expressions: new Foo(), new module.Foo()."""
        ctor = node.child_by_field_name("constructor")
        if ctor is None:
            return

        # Handle new module.Foo() -> extract "Foo"
        if ctor.type == "member_expression":
            prop = ctor.child_by_field_name("property")
            if prop:
                name = self.node_text(prop, source)
            else:
                name = self.node_text(ctor, source)
        else:
            name = self.node_text(ctor, source)

        refs.append(self._make_reference(
            target_name=name,
            kind="call",
            line=ctor.start_point[0] + 1,
            source_name=scope_name,
        ))

        # Recurse into arguments for nested calls/refs
        args = node.child_by_field_name("arguments")
        if args:
            self._walk_refs(args, source, refs, scope_name)
