
from __future__ import annotations
from .base import LanguageExtractor


class RubyExtractor(LanguageExtractor):
    """Ruby symbol and reference extractor.

    Handles modules, classes (with inheritance), methods, singleton methods,
    constants, require/require_relative, include/extend, and method calls.
    """

    @property
    def language_name(self) -> str:
        return "ruby"

    @property
    def file_extensions(self) -> list[str]:
        return [".rb"]

    def extract_symbols(self, tree, source: bytes, file_path: str) -> list[dict]:
        symbols: list[dict] = []
        self._pending_inherits: list[dict] = []
        self._walk_symbols(tree.root_node, source, symbols, parent_name=None)
        return symbols

    def extract_references(self, tree, source: bytes, file_path: str) -> list[dict]:
        refs: list[dict] = []
        self._walk_refs(tree.root_node, source, refs, scope_name=None)
        refs.extend(getattr(self, '_pending_inherits', []))
        self._pending_inherits = []
        return refs

    def get_docstring(self, node, source: bytes) -> str | None:
        """Ruby doc comments: consecutive # comment lines before node."""
        prev = node.prev_sibling
        comments: list[str] = []
        while prev and prev.type == "comment":
            text = self.node_text(prev, source).strip()
            if text.startswith("#"):
                text = text[1:].strip()
            comments.insert(0, text)
            prev = prev.prev_sibling
        return "\n".join(comments) if comments else None

    # ---- Symbol extraction ----

    def _walk_symbols(self, node, source, symbols, parent_name):
        for child in node.children:
            ntype = child.type
            if ntype == "module":
                self._extract_module(child, source, symbols, parent_name)
            elif ntype == "class":
                self._extract_class(child, source, symbols, parent_name)
            elif ntype == "method":
                self._extract_method(child, source, symbols, parent_name)
            elif ntype == "singleton_method":
                self._extract_singleton_method(child, source, symbols, parent_name)
            elif ntype == "assignment":
                self._extract_assignment(child, source, symbols, parent_name)
            elif ntype in ("body_statement", "program", "then", "else", "begin"):
                self._walk_symbols(child, source, symbols, parent_name)

    def _extract_module(self, node, source, symbols, parent_name):
        name_node = node.child_by_field_name("name")
        if name_node is None:
            return
        name = self.node_text(name_node, source)
        qualified = f"{parent_name}::{name}" if parent_name else name

        symbols.append(self._make_symbol(
            name=name,
            kind="module",
            line_start=node.start_point[0] + 1,
            line_end=node.end_point[0] + 1,
            qualified_name=qualified,
            signature=f"module {name}",
            docstring=self.get_docstring(node, source),
            is_exported=True,
        ))

        # Walk module body
        body = node.child_by_field_name("body")
        if body:
            self._walk_symbols(body, source, symbols, qualified)

    def _extract_class(self, node, source, symbols, parent_name):
        name_node = node.child_by_field_name("name")
        if name_node is None:
            return
        name = self.node_text(name_node, source)
        qualified = f"{parent_name}::{name}" if parent_name else name

        sig = f"class {name}"

        # Superclass
        superclass_node = node.child_by_field_name("superclass")
        if superclass_node:
            # The superclass node contains "< BaseClass"
            # Extract just the class name (skip the "<")
            for child in superclass_node.children:
                if child.type == "constant" or child.type == "scope_resolution":
                    parent_class = self.node_text(child, source)
                    sig += f" < {parent_class}"
                    # Record inheritance reference
                    short_name = parent_class.rsplit("::", 1)[-1] if "::" in parent_class else parent_class
                    self._pending_inherits.append(self._make_reference(
                        target_name=short_name,
                        kind="inherits",
                        line=superclass_node.start_point[0] + 1,
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
            is_exported=True,
            parent_name=parent_name,
        ))

        # Walk class body
        body = node.child_by_field_name("body")
        if body:
            self._walk_symbols(body, source, symbols, qualified)

    def _extract_method(self, node, source, symbols, parent_name):
        name_node = node.child_by_field_name("name")
        if name_node is None:
            return
        name = self.node_text(name_node, source)
        qualified = f"{parent_name}#{name}" if parent_name else name

        params = node.child_by_field_name("parameters")
        params_text = self._params_text(params, source) if params else ""
        sig = f"def {name}({params_text})"

        symbols.append(self._make_symbol(
            name=name,
            kind="method" if parent_name else "function",
            line_start=node.start_point[0] + 1,
            line_end=node.end_point[0] + 1,
            qualified_name=qualified,
            signature=sig,
            docstring=self.get_docstring(node, source),
            visibility="public",
            is_exported=True,
            parent_name=parent_name,
        ))

    def _extract_singleton_method(self, node, source, symbols, parent_name):
        name_node = node.child_by_field_name("name")
        if name_node is None:
            return
        name = self.node_text(name_node, source)

        # The object field tells us what the receiver is (typically "self")
        obj_node = node.child_by_field_name("object")
        obj_text = self.node_text(obj_node, source) if obj_node else "self"

        qualified = f"{parent_name}.{name}" if parent_name else name

        params = node.child_by_field_name("parameters")
        params_text = self._params_text(params, source) if params else ""
        sig = f"def {obj_text}.{name}({params_text})"

        symbols.append(self._make_symbol(
            name=name,
            kind="method",
            line_start=node.start_point[0] + 1,
            line_end=node.end_point[0] + 1,
            qualified_name=qualified,
            signature=sig,
            docstring=self.get_docstring(node, source),
            visibility="public",
            is_exported=True,
            parent_name=parent_name,
        ))

    def _extract_assignment(self, node, source, symbols, parent_name):
        """Extract UPPER_CASE constant assignments."""
        left = node.child_by_field_name("left")
        if left is None:
            return
        if left.type != "constant":
            return
        name = self.node_text(left, source)
        # Only treat as constant if name is UPPER_CASE or starts with uppercase
        if not name or not name[0].isupper():
            return
        qualified = f"{parent_name}::{name}" if parent_name else name

        right = node.child_by_field_name("right")
        value_text = self.node_text(right, source)[:60] if right else None

        symbols.append(self._make_symbol(
            name=name,
            kind="constant",
            line_start=node.start_point[0] + 1,
            line_end=node.end_point[0] + 1,
            qualified_name=qualified,
            signature=f"{name} = {value_text}" if value_text else name,
            visibility="public",
            is_exported=True,
            parent_name=parent_name,
            default_value=value_text,
        ))

    # ---- Reference extraction ----

    def _walk_refs(self, node, source, refs, scope_name):
        for child in node.children:
            ntype = child.type
            if ntype == "call":
                self._extract_call(child, source, refs, scope_name)
            elif ntype == "constant":
                # Standalone constant reference (e.g. using a class name)
                name = self.node_text(child, source)
                refs.append(self._make_reference(
                    target_name=name,
                    kind="reference",
                    line=child.start_point[0] + 1,
                    source_name=scope_name,
                ))
            elif ntype == "scope_resolution":
                # SomeModule::SomeClass
                name_node = child.child_by_field_name("name")
                if name_node:
                    name = self.node_text(name_node, source)
                    refs.append(self._make_reference(
                        target_name=name,
                        kind="reference",
                        line=child.start_point[0] + 1,
                        source_name=scope_name,
                    ))
            else:
                new_scope = scope_name
                if ntype == "module":
                    n = child.child_by_field_name("name")
                    if n:
                        mname = self.node_text(n, source)
                        new_scope = f"{scope_name}::{mname}" if scope_name else mname
                elif ntype == "class":
                    n = child.child_by_field_name("name")
                    if n:
                        cname = self.node_text(n, source)
                        new_scope = f"{scope_name}::{cname}" if scope_name else cname
                elif ntype == "method":
                    n = child.child_by_field_name("name")
                    if n:
                        fname = self.node_text(n, source)
                        new_scope = f"{scope_name}#{fname}" if scope_name else fname
                elif ntype == "singleton_method":
                    n = child.child_by_field_name("name")
                    if n:
                        fname = self.node_text(n, source)
                        new_scope = f"{scope_name}.{fname}" if scope_name else fname
                self._walk_refs(child, source, refs, new_scope)

    def _extract_call(self, node, source, refs, scope_name):
        """Extract method calls, require/require_relative, include/extend."""
        method_node = node.child_by_field_name("method")
        receiver_node = node.child_by_field_name("receiver")
        args_node = node.child_by_field_name("arguments")

        if method_node is None:
            return

        method_name = self.node_text(method_node, source)

        # Handle require and require_relative
        if method_name in ("require", "require_relative") and receiver_node is None:
            self._extract_require(node, method_name, args_node, source, refs, scope_name)
            return

        # Handle include and extend
        if method_name in ("include", "extend") and receiver_node is None:
            self._extract_include_extend(node, method_name, args_node, source, refs, scope_name)
            return

        # Regular method call
        if receiver_node:
            receiver_text = self.node_text(receiver_node, source)
            # ClassName.new -> treat as call to ClassName
            if method_name == "new" and receiver_node.type in ("constant", "scope_resolution"):
                class_name = receiver_text.rsplit("::", 1)[-1] if "::" in receiver_text else receiver_text
                refs.append(self._make_reference(
                    target_name=class_name,
                    kind="call",
                    line=node.start_point[0] + 1,
                    source_name=scope_name,
                ))
            else:
                refs.append(self._make_reference(
                    target_name=method_name,
                    kind="call",
                    line=node.start_point[0] + 1,
                    source_name=scope_name,
                ))
        else:
            # Free function / method call without receiver
            refs.append(self._make_reference(
                target_name=method_name,
                kind="call",
                line=node.start_point[0] + 1,
                source_name=scope_name,
            ))

        # Recurse into arguments and block
        if args_node:
            self._walk_refs(args_node, source, refs, scope_name)
        block_node = node.child_by_field_name("block")
        if block_node:
            self._walk_refs(block_node, source, refs, scope_name)

    def _extract_require(self, node, method_name, args_node, source, refs, scope_name):
        """Extract require 'lib' and require_relative 'path'."""
        if args_node is None:
            return
        # Find the string argument
        for child in args_node.children:
            if child.type == "string":
                # Get string content (skip quotes)
                for sub in child.children:
                    if sub.type == "string_content":
                        path = self.node_text(sub, source)
                        target = path.rsplit("/", 1)[-1] if "/" in path else path
                        refs.append(self._make_reference(
                            target_name=target,
                            kind="import",
                            line=node.start_point[0] + 1,
                            source_name=scope_name,
                            import_path=path,
                        ))
                        return

    def _extract_include_extend(self, node, method_name, args_node, source, refs, scope_name):
        """Extract include ModuleName and extend ModuleName."""
        if args_node is None:
            return
        for child in args_node.children:
            if child.type == "constant":
                name = self.node_text(child, source)
                refs.append(self._make_reference(
                    target_name=name,
                    kind="import",
                    line=node.start_point[0] + 1,
                    source_name=scope_name,
                ))
            elif child.type == "scope_resolution":
                name_node = child.child_by_field_name("name")
                if name_node:
                    name = self.node_text(name_node, source)
                    refs.append(self._make_reference(
                        target_name=name,
                        kind="import",
                        line=node.start_point[0] + 1,
                        source_name=scope_name,
                    ))
