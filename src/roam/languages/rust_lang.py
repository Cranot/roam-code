
from __future__ import annotations
from .base import LanguageExtractor


class RustExtractor(LanguageExtractor):
    """Rust symbol and reference extractor."""

    @property
    def language_name(self) -> str:
        return "rust"

    @property
    def file_extensions(self) -> list[str]:
        return [".rs"]

    def extract_symbols(self, tree, source: bytes, file_path: str) -> list[dict]:
        symbols = []
        self._walk_symbols(tree.root_node, source, symbols, parent_name=None)
        return symbols

    def extract_references(self, tree, source: bytes, file_path: str) -> list[dict]:
        refs = []
        self._walk_refs(tree.root_node, source, refs, scope_name=None)
        return refs

    def get_docstring(self, node, source: bytes) -> str | None:
        """Rust doc comments: /// or //! lines before node."""
        prev = node.prev_sibling
        comments = []
        while prev and prev.type in ("line_comment", "block_comment"):
            text = self.node_text(prev, source).strip()
            if text.startswith("///") or text.startswith("//!"):
                text = text[3:].strip()
                comments.insert(0, text)
            elif text.startswith("/**"):
                text = text[3:]
                if text.endswith("*/"):
                    text = text[:-2]
                comments.insert(0, text.strip())
            else:
                break
            prev = prev.prev_sibling
        return "\n".join(comments) if comments else None

    def _visibility(self, node, source) -> str:
        """Check for pub visibility modifier."""
        for child in node.children:
            if child.type == "visibility_modifier":
                text = self.node_text(child, source)
                if "crate" in text:
                    return "public"
                if "super" in text:
                    return "private"
                return "public"
        return "private"

    def _is_pub(self, node, source) -> bool:
        return self._visibility(node, source) == "public"

    # ---- Symbol extraction ----

    def _walk_symbols(self, node, source, symbols, parent_name):
        for child in node.children:
            if child.type == "function_item":
                self._extract_function(child, source, symbols, parent_name)
            elif child.type == "struct_item":
                self._extract_struct(child, source, symbols, parent_name)
            elif child.type == "enum_item":
                self._extract_enum(child, source, symbols, parent_name)
            elif child.type == "trait_item":
                self._extract_trait(child, source, symbols, parent_name)
            elif child.type == "impl_item":
                self._extract_impl(child, source, symbols, parent_name)
            elif child.type == "mod_item":
                self._extract_mod(child, source, symbols, parent_name)
            elif child.type == "type_item":
                self._extract_type_alias(child, source, symbols, parent_name)
            elif child.type == "const_item":
                self._extract_const(child, source, symbols, parent_name)
            elif child.type == "static_item":
                self._extract_static(child, source, symbols, parent_name)
            elif child.type == "macro_definition":
                self._extract_macro(child, source, symbols, parent_name)

    def _extract_function(self, node, source, symbols, parent_name, kind="function"):
        name_node = node.child_by_field_name("name")
        if name_node is None:
            return
        name = self.node_text(name_node, source)
        params = node.child_by_field_name("parameters")
        sig = f"fn {name}({self.node_text(params, source) if params else ''})"

        # Type parameters
        type_params = node.child_by_field_name("type_parameters")
        if type_params:
            sig = f"fn {name}{self.node_text(type_params, source)}({self.node_text(params, source) if params else ''})"

        # Return type
        ret = node.child_by_field_name("return_type")
        if ret:
            sig += f" -> {self.node_text(ret, source)}"

        vis = self._visibility(node, source)
        if vis == "public":
            sig = f"pub {sig}"

        qualified = f"{parent_name}::{name}" if parent_name else name
        symbols.append(self._make_symbol(
            name=name,
            kind=kind,
            line_start=node.start_point[0] + 1,
            line_end=node.end_point[0] + 1,
            qualified_name=qualified,
            signature=sig,
            docstring=self.get_docstring(node, source),
            visibility=vis,
            is_exported=self._is_pub(node, source),
            parent_name=parent_name,
        ))

    def _extract_struct(self, node, source, symbols, parent_name):
        name_node = node.child_by_field_name("name")
        if name_node is None:
            return
        name = self.node_text(name_node, source)
        vis = self._visibility(node, source)
        sig = f"{'pub ' if vis == 'public' else ''}struct {name}"

        type_params = node.child_by_field_name("type_parameters")
        if type_params:
            sig += self.node_text(type_params, source)

        qualified = f"{parent_name}::{name}" if parent_name else name
        symbols.append(self._make_symbol(
            name=name,
            kind="struct",
            line_start=node.start_point[0] + 1,
            line_end=node.end_point[0] + 1,
            qualified_name=qualified,
            signature=sig,
            docstring=self.get_docstring(node, source),
            visibility=vis,
            is_exported=vis == "public",
            parent_name=parent_name,
        ))

        # Extract struct fields
        body = node.child_by_field_name("body")
        if body:
            for child in body.children:
                if child.type == "field_declaration":
                    fn = child.child_by_field_name("name")
                    if fn:
                        field_name = self.node_text(fn, source)
                        ftype = child.child_by_field_name("type")
                        fsig = field_name
                        if ftype:
                            fsig += f": {self.node_text(ftype, source)}"
                        fvis = self._visibility(child, source)
                        symbols.append(self._make_symbol(
                            name=field_name,
                            kind="field",
                            line_start=child.start_point[0] + 1,
                            line_end=child.end_point[0] + 1,
                            qualified_name=f"{qualified}::{field_name}",
                            signature=fsig,
                            visibility=fvis,
                            is_exported=fvis == "public",
                            parent_name=qualified,
                        ))

    def _extract_enum(self, node, source, symbols, parent_name):
        name_node = node.child_by_field_name("name")
        if name_node is None:
            return
        name = self.node_text(name_node, source)
        vis = self._visibility(node, source)
        sig = f"{'pub ' if vis == 'public' else ''}enum {name}"

        type_params = node.child_by_field_name("type_parameters")
        if type_params:
            sig += self.node_text(type_params, source)

        qualified = f"{parent_name}::{name}" if parent_name else name
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

        # Extract enum variants
        body = node.child_by_field_name("body")
        if body:
            for child in body.children:
                if child.type == "enum_variant":
                    vn = child.child_by_field_name("name")
                    if vn:
                        variant_name = self.node_text(vn, source)
                        symbols.append(self._make_symbol(
                            name=variant_name,
                            kind="field",
                            line_start=child.start_point[0] + 1,
                            line_end=child.end_point[0] + 1,
                            qualified_name=f"{qualified}::{variant_name}",
                            parent_name=qualified,
                            visibility=vis,
                            is_exported=vis == "public",
                        ))

    def _extract_trait(self, node, source, symbols, parent_name):
        name_node = node.child_by_field_name("name")
        if name_node is None:
            return
        name = self.node_text(name_node, source)
        vis = self._visibility(node, source)
        sig = f"{'pub ' if vis == 'public' else ''}trait {name}"

        type_params = node.child_by_field_name("type_parameters")
        if type_params:
            sig += self.node_text(type_params, source)

        qualified = f"{parent_name}::{name}" if parent_name else name
        symbols.append(self._make_symbol(
            name=name,
            kind="trait",
            line_start=node.start_point[0] + 1,
            line_end=node.end_point[0] + 1,
            qualified_name=qualified,
            signature=sig,
            docstring=self.get_docstring(node, source),
            visibility=vis,
            is_exported=vis == "public",
            parent_name=parent_name,
        ))

        # Extract trait methods
        body = node.child_by_field_name("body")
        if body:
            for child in body.children:
                if child.type == "function_item":
                    self._extract_function(child, source, symbols, qualified, kind="method")
                elif child.type == "function_signature_item":
                    self._extract_fn_signature(child, source, symbols, qualified)

    def _extract_fn_signature(self, node, source, symbols, parent_name):
        """Extract function signature items (trait method declarations without body)."""
        name_node = node.child_by_field_name("name")
        if name_node is None:
            return
        name = self.node_text(name_node, source)
        params = node.child_by_field_name("parameters")
        sig = f"fn {name}({self.node_text(params, source) if params else ''})"
        ret = node.child_by_field_name("return_type")
        if ret:
            sig += f" -> {self.node_text(ret, source)}"

        symbols.append(self._make_symbol(
            name=name,
            kind="method",
            line_start=node.start_point[0] + 1,
            line_end=node.end_point[0] + 1,
            qualified_name=f"{parent_name}::{name}",
            signature=sig,
            docstring=self.get_docstring(node, source),
            parent_name=parent_name,
        ))

    def _extract_impl(self, node, source, symbols, parent_name):
        """Extract impl block: associate methods with the implementing type."""
        # Determine the type being implemented
        type_node = node.child_by_field_name("type")
        trait_node = node.child_by_field_name("trait")

        if type_node is None:
            return

        type_name = self.node_text(type_node, source)
        impl_name = type_name

        if trait_node:
            trait_name = self.node_text(trait_node, source)
            impl_name = f"{type_name}::{trait_name}"

        body = node.child_by_field_name("body")
        if body:
            for child in body.children:
                if child.type == "function_item":
                    self._extract_function(child, source, symbols, impl_name, kind="method")
                elif child.type == "type_item":
                    self._extract_type_alias(child, source, symbols, impl_name)
                elif child.type == "const_item":
                    self._extract_const(child, source, symbols, impl_name)

    def _extract_mod(self, node, source, symbols, parent_name):
        name_node = node.child_by_field_name("name")
        if name_node is None:
            return
        name = self.node_text(name_node, source)
        vis = self._visibility(node, source)
        sig = f"{'pub ' if vis == 'public' else ''}mod {name}"

        qualified = f"{parent_name}::{name}" if parent_name else name
        symbols.append(self._make_symbol(
            name=name,
            kind="module",
            line_start=node.start_point[0] + 1,
            line_end=node.end_point[0] + 1,
            qualified_name=qualified,
            signature=sig,
            visibility=vis,
            is_exported=vis == "public",
            parent_name=parent_name,
        ))

        # Walk mod body if inline
        body = node.child_by_field_name("body")
        if body:
            self._walk_symbols(body, source, symbols, qualified)

    def _extract_type_alias(self, node, source, symbols, parent_name):
        name_node = node.child_by_field_name("name")
        if name_node is None:
            return
        name = self.node_text(name_node, source)
        vis = self._visibility(node, source)
        sig = f"{'pub ' if vis == 'public' else ''}type {name}"

        type_params = node.child_by_field_name("type_parameters")
        if type_params:
            sig += self.node_text(type_params, source)

        value = node.child_by_field_name("type")
        if value:
            val_text = self.node_text(value, source)
            if len(val_text) <= 60:
                sig += f" = {val_text}"

        qualified = f"{parent_name}::{name}" if parent_name else name
        symbols.append(self._make_symbol(
            name=name,
            kind="type_alias",
            line_start=node.start_point[0] + 1,
            line_end=node.end_point[0] + 1,
            qualified_name=qualified,
            signature=sig,
            visibility=vis,
            is_exported=vis == "public",
            parent_name=parent_name,
        ))

    def _extract_const(self, node, source, symbols, parent_name):
        name_node = node.child_by_field_name("name")
        if name_node is None:
            return
        name = self.node_text(name_node, source)
        vis = self._visibility(node, source)
        type_n = node.child_by_field_name("type")
        sig = f"{'pub ' if vis == 'public' else ''}const {name}"
        if type_n:
            sig += f": {self.node_text(type_n, source)}"

        qualified = f"{parent_name}::{name}" if parent_name else name
        symbols.append(self._make_symbol(
            name=name,
            kind="constant",
            line_start=node.start_point[0] + 1,
            line_end=node.end_point[0] + 1,
            qualified_name=qualified,
            signature=sig,
            visibility=vis,
            is_exported=vis == "public",
            parent_name=parent_name,
        ))

    def _extract_static(self, node, source, symbols, parent_name):
        name_node = node.child_by_field_name("name")
        if name_node is None:
            return
        name = self.node_text(name_node, source)
        vis = self._visibility(node, source)
        type_n = node.child_by_field_name("type")
        sig = f"{'pub ' if vis == 'public' else ''}static {name}"
        if type_n:
            sig += f": {self.node_text(type_n, source)}"

        qualified = f"{parent_name}::{name}" if parent_name else name
        symbols.append(self._make_symbol(
            name=name,
            kind="variable",
            line_start=node.start_point[0] + 1,
            line_end=node.end_point[0] + 1,
            qualified_name=qualified,
            signature=sig,
            visibility=vis,
            is_exported=vis == "public",
            parent_name=parent_name,
        ))

    def _extract_macro(self, node, source, symbols, parent_name):
        name_node = node.child_by_field_name("name")
        if name_node is None:
            return
        name = self.node_text(name_node, source)
        sig = f"macro_rules! {name}"
        qualified = f"{parent_name}::{name}" if parent_name else name

        symbols.append(self._make_symbol(
            name=name,
            kind="function",
            line_start=node.start_point[0] + 1,
            line_end=node.end_point[0] + 1,
            qualified_name=qualified,
            signature=sig,
            docstring=self.get_docstring(node, source),
            is_exported=True,
            parent_name=parent_name,
        ))

    # ---- Reference extraction ----

    def _walk_refs(self, node, source, refs, scope_name):
        for child in node.children:
            if child.type == "use_declaration":
                self._extract_use(child, source, refs, scope_name)
            elif child.type == "call_expression":
                self._extract_call(child, source, refs, scope_name)
            elif child.type == "macro_invocation":
                self._extract_macro_call(child, source, refs, scope_name)
            else:
                new_scope = scope_name
                if child.type == "function_item":
                    n = child.child_by_field_name("name")
                    if n:
                        fname = self.node_text(n, source)
                        new_scope = f"{scope_name}::{fname}" if scope_name else fname
                elif child.type == "impl_item":
                    t = child.child_by_field_name("type")
                    if t:
                        new_scope = self.node_text(t, source)
                self._walk_refs(child, source, refs, new_scope)

    def _extract_use(self, node, source, refs, scope_name):
        """Extract use declarations."""
        # Get the full use path text
        for child in node.children:
            if child.type in ("use_as_clause", "use_list", "scoped_use_list",
                              "scoped_identifier", "identifier", "use_wildcard"):
                path = self.node_text(child, source)
                # Clean up and extract the target name
                target = path.rsplit("::", 1)[-1] if "::" in path else path
                target = target.strip("{}*, ")
                if target:
                    refs.append(self._make_reference(
                        target_name=target,
                        kind="import",
                        line=node.start_point[0] + 1,
                        source_name=scope_name,
                        import_path=path,
                    ))

    def _extract_call(self, node, source, refs, scope_name):
        func_node = node.child_by_field_name("function")
        if func_node is None:
            return

        # Handle method calls: obj.method() -> extract "method"
        if func_node.type == "field_expression":
            field = func_node.child_by_field_name("field")
            if field:
                name = self.node_text(field, source)
            else:
                name = self.node_text(func_node, source)
        else:
            name = self.node_text(func_node, source)

        refs.append(self._make_reference(
            target_name=name,
            kind="call",
            line=func_node.start_point[0] + 1,
            source_name=scope_name,
        ))
        args = node.child_by_field_name("arguments")
        if args:
            self._walk_refs(args, source, refs, scope_name)

    def _extract_macro_call(self, node, source, refs, scope_name):
        macro_node = node.child_by_field_name("macro")
        if macro_node is None:
            # Try first child as identifier
            for child in node.children:
                if child.type == "identifier" or child.type == "scoped_identifier":
                    macro_node = child
                    break
        if macro_node:
            name = self.node_text(macro_node, source)
            refs.append(self._make_reference(
                target_name=f"{name}!",
                kind="call",
                line=node.start_point[0] + 1,
                source_name=scope_name,
            ))
