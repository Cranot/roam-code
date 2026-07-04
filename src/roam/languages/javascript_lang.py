from __future__ import annotations

import os
import re

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
        if not hasattr(self, "_pending_inherits"):
            self._pending_inherits = []
        self._walk_symbols(tree.root_node, source, file_path, symbols, parent_name=None, is_exported=False)
        return symbols

    def extract_references(self, tree, source: bytes, file_path: str) -> list[dict]:
        refs = []
        self._walk_refs(tree.root_node, source, refs, scope_name=None)
        refs.extend(self._extract_dynamic_import_refs(source))
        # Collect inheritance refs accumulated during extract_symbols
        refs.extend(getattr(self, "_pending_inherits", []))
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
                                self._pending_inherits.append(
                                    self._make_reference(
                                        target_name=self.node_text(exn, source),
                                        kind="inherits",
                                        line=node.start_point[0] + 1,
                                        source_name=qualified,
                                    )
                                )
                                break
                    elif sub.type == "implements_clause":
                        # TS: implements_clause > type_identifier (can be multiple)
                        for imp in sub.children:
                            if imp.type in ("type_identifier", "identifier"):
                                self._pending_inherits.append(
                                    self._make_reference(
                                        target_name=self.node_text(imp, source),
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
                break
        symbols.append(
            self._make_symbol(
                name=name,
                kind="class",
                line_start=node.start_point[0] + 1,
                line_end=node.end_point[0] + 1,
                qualified_name=qualified,
                signature=sig,
                docstring=self.get_docstring(node, source),
                is_exported=is_exported,
                parent_name=parent_name,
            )
        )

        # Walk class body for methods
        body = node.child_by_field_name("body")
        if body:
            self._extract_class_members(body, source, symbols, qualified)

    def _extract_class_members(self, body_node, source, symbols, class_name):
        for child in body_node.children:
            self._extract_single_class_member(child, source, symbols, class_name)

    def _extract_single_class_member(self, child, source, symbols, class_name):
        if child.type not in ("method_definition", "public_field_definition", "field_definition"):
            return
        name_node = child.child_by_field_name("name")
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
        name_node = child.child_by_field_name("name")
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
            symbols.append(
                self._make_symbol(
                    name=prop_name,
                    kind="function",
                    line_start=child.start_point[0] + 1,
                    line_end=child.end_point[0] + 1,
                    qualified_name=f"{obj_text}.{prop_name}",
                    signature=f"{obj_text}.{prop_name} = function({p_text})",
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
            return [
                self.node_text(sub, source)
                for sub in child.children
                if sub.type == "identifier"
            ]

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
        if name and name not in self._JS_KEYWORDS:
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
        if name:
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
        return scope_name

    def _walk_refs(self, node, source, refs, scope_name):
        for child in node.children:
            ctype = child.type
            if ctype == "import_statement":
                self._extract_esm_import(child, source, refs, scope_name)
            elif ctype == "export_statement":
                self._walk_refs(child, source, refs, scope_name)
            elif ctype == "call_expression":
                self._extract_call(child, source, refs, scope_name)
            elif ctype == "new_expression":
                self._extract_new(child, source, refs, scope_name)
            elif ctype == "identifier" and node.type == "arguments":
                self._emit_argument_identifier_ref(child, source, refs, scope_name)
            elif ctype == "shorthand_property_identifier":
                self._emit_shorthand_property_ref(child, source, refs, scope_name)
            else:
                self._walk_refs(child, source, refs, self._scope_name_for_child(child, source, scope_name))

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
    _DYN_IMPORT_THEN_MEMBER_RE = re.compile(
        r"import\s*\(\s*(['\"])(?P<path>[^'\"]+)\1\s*\)"
        r"\s*\.\s*then\s*\(\s*(?P<param>[A-Za-z_$][A-Za-z0-9_$]*)\s*=>"
        r"\s*(?P=param)\.(?P<name>[A-Za-z_$][A-Za-z0-9_$]*)",
        re.DOTALL,
    )
    _DYN_IMPORT_THEN_DESTRUCTURED_RE = re.compile(
        r"import\s*\(\s*(['\"])(?P<path>[^'\"]+)\1\s*\)"
        r"\s*\.\s*then\s*\(\s*\(\s*\{(?P<names>[^}]*)\}\s*\)\s*=>",
        re.DOTALL,
    )
    _DYN_IMPORT_AWAIT_ASSIGN_RE = re.compile(
        r"(?:const|let|var)\s+(?P<var>[A-Za-z_$][A-Za-z0-9_$]*)\s*="
        r"\s*await\s+import\s*\(\s*(['\"])(?P<path>[^'\"]+)\2\s*\)"
        r"(?P<body>[\s\S]{0,1200})",
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
        """Extract named consumers from dynamic ``import(...)`` forms.

        Static ESM imports already carry an import_path that disambiguates
        same-named exports. Dynamic imports need the same treatment or dead
        code analysis can miss production-only button/action consumers.
        """
        text = source.decode("utf-8", errors="replace")
        refs: list[dict] = []
        seen: set[tuple[str, str, int]] = set()

        def add(name: str, path: str, line: int) -> None:
            key = (name, path, line)
            if key in seen:
                return
            seen.add(key)
            refs.append(self._make_dynamic_import_ref(name, path, line))

        for match in self._DYN_IMPORT_THEN_MEMBER_RE.finditer(text):
            add(match.group("name"), match.group("path"), self._dynamic_import_line(text, match.start()))

        for match in self._DYN_IMPORT_THEN_DESTRUCTURED_RE.finditer(text):
            line = self._dynamic_import_line(text, match.start())
            for name in self._destructured_export_names(match.group("names")):
                add(name, match.group("path"), line)

        member_access_re = re.compile(rf"\b(?P<var>{self._IDENT_RE})\.(?P<name>{self._IDENT_RE})")
        for match in self._DYN_IMPORT_AWAIT_ASSIGN_RE.finditer(text):
            var_name = match.group("var")
            path = match.group("path")
            line = self._dynamic_import_line(text, match.start())
            for access in member_access_re.finditer(match.group("body")):
                if access.group("var") == var_name:
                    add(access.group("name"), path, line)

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
        if name == "require" and self._record_static_require_import_for_dependency_precision(
            node, source, refs, scope_name
        ):
            return

        refs.append(
            self._make_reference(
                target_name=name,
                kind="call",
                line=node.start_point[0] + 1,
                source_name=scope_name,
            )
        )

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

        # Handle new module.Foo() -> extract "Foo"
        if ctor.type == "member_expression":
            prop = ctor.child_by_field_name("property")
            if prop:
                name = self.node_text(prop, source)
            else:
                name = self.node_text(ctor, source)
        else:
            name = self.node_text(ctor, source)

        refs.append(
            self._make_reference(
                target_name=name,
                kind="call",
                line=ctor.start_point[0] + 1,
                source_name=scope_name,
            )
        )

        # Recurse into arguments for nested calls/refs
        args = node.child_by_field_name("arguments")
        if args:
            self._walk_refs(args, source, refs, scope_name)
