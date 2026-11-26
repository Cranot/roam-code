"""Shared JS/TS source-path candidates (not a full compiler resolver)."""

from __future__ import annotations

from pathlib import PurePosixPath


def source_path_candidates(path: str) -> tuple[str, ...]:
    """Expand a normalized import path using TypeScript extension substitution.

    An explicit runtime extension is replaced, not appended: ``a.js`` may
    name ``a.ts`` but never ``a.js.ts``. Extensionless paths additionally
    support directory index files and the SFC extensions used by bundlers.
    See https://www.typescriptlang.org/docs/handbook/modules/reference.html#file-extension-substitution.
    Package exports, compiler aliases and runtime existence are separate checks.
    """
    path = path.replace("\\", "/")
    suffix = PurePosixPath(path).suffix
    substitutions = {
        ".js": (".ts", ".tsx", ".d.ts", ".js", ".jsx"),
        ".jsx": (".tsx", ".d.ts", ".jsx"),
        ".mjs": (".mts", ".d.mts", ".mjs"),
        ".cjs": (".cts", ".d.cts", ".cjs"),
    }
    if suffix in substitutions:
        stem = path[: -len(suffix)]
        return tuple(stem + ext for ext in substitutions[suffix])
    extensions = (".ts", ".tsx", ".d.ts", ".js", ".jsx", ".mts", ".cts", ".mjs", ".cjs", ".json", ".vue", ".svelte")
    if suffix in extensions:
        return (path,)
    # A dot inside a file name (`./app.service`, `./user.dto`) is not an extension.
    return (path, *(path + ext for ext in extensions), *(path + "/index" + ext for ext in extensions))


class ModuleExports:
    """Read explicit local module exports for one resolution pass.

    Local paths use ordered source-path candidates. Duplicate definitions and
    conflicting star exports stay unresolved. Cached AST summaries include
    symbol-free barrels. Cycles stop at the visited module.
    """

    def __init__(self, root, symbols_by_name):
        from pathlib import Path

        self.root = Path(root or ".")
        self.by_file = {}
        for symbols in symbols_by_name.values():
            for symbol in symbols:
                self.by_file.setdefault(symbol.get("file_path"), []).append(symbol)
        self.cache = {}
        self.instances = set()

    def module_path(self, source_file, module):
        import posixpath

        if module.startswith("."):
            base = posixpath.normpath(posixpath.join(posixpath.dirname(source_file), module))
        elif module.startswith("@/"):
            base = "src/" + module[2:]
        else:
            return None
        return next((p for p in source_path_candidates(base) if (self.root / p).is_file()), None)

    def _summary(self, path):
        if path in self.cache:
            return self.cache[path]
        exports, stars = {}, []
        self.cache[path] = exports, stars
        from roam.index.parser import detect_language
        from roam.parser_pack import get_parser

        language = detect_language(path)
        if language not in {"javascript", "typescript", "tsx"}:
            return exports, stars
        try:
            source = (self.root / path).read_bytes()
            tree = get_parser(language).parse(source)
        except (OSError, ValueError):
            return exports, stars

        def text(node):
            return source[node.start_byte : node.end_byte].decode("utf-8", errors="replace") if node is not None else ""

        def add(name, local, module=None):
            exports.setdefault(name, []).append((local, module))

        for stmt in tree.root_node.named_children:
            if stmt.type == "expression_statement":
                # CommonJS: module.exports = { a, b: c }
                assignment = stmt.named_children[0] if stmt.named_children else None
                if assignment is not None and assignment.type == "assignment_expression":
                    left, right = assignment.child_by_field_name("left"), assignment.child_by_field_name("right")
                    if text(left) == "module.exports" and right is not None and right.type == "object":
                        for prop in right.named_children:
                            if prop.type == "shorthand_property_identifier":
                                add(text(prop), text(prop))
                            elif prop.type == "pair":
                                value = prop.child_by_field_name("value")
                                if value is not None and value.type == "identifier":
                                    add(text(prop.child_by_field_name("key")), text(value))
                continue
            if stmt.type != "export_statement":
                continue
            module_node = stmt.child_by_field_name("source")
            module = text(module_node).strip("'\"") if module_node is not None else None
            tokens = {n.type for n in stmt.children}
            if "default" in tokens or "=" in tokens:
                name = "default" if "default" in tokens else "export="
                declaration = stmt.child_by_field_name("declaration") or stmt.child_by_field_name("value")
                if declaration is None:
                    declaration = next((n for n in stmt.named_children if n.type == "identifier"), None)
                local = declaration.child_by_field_name("name") if declaration is not None else None
                if local is None and declaration is not None and declaration.type == "identifier":
                    local = declaration
                if local is None and declaration is not None and declaration.type == "new_expression":
                    ctor = declaration.child_by_field_name("constructor")
                    if ctor is not None and ctor.type == "identifier":
                        local = ctor
                        self.instances.add((path, name))
                if local is not None:
                    add(name, text(local))
                continue
            clause = next((n for n in stmt.named_children if n.type == "export_clause"), None)
            if clause is not None:
                for spec in clause.named_children:
                    local = spec.child_by_field_name("name")
                    alias = spec.child_by_field_name("alias") or local
                    if local is not None:
                        add(text(alias), text(local), module)
            elif "*" in tokens and module:
                stars.append(module)
            else:
                declaration = stmt.child_by_field_name("declaration")
                if declaration is not None:
                    local = declaration.child_by_field_name("name")
                    if local is not None:
                        add(text(local), text(local))
                    for child in declaration.named_children:
                        if child.type == "variable_declarator":
                            local = child.child_by_field_name("name")
                            if local is not None and local.type == "identifier":
                                add(text(local), text(local))
        return exports, stars

    def is_instance_export(self, path, name):
        """True when ``name`` is exported as ``new Class()`` (not the class itself)."""
        self._summary(path)
        return (path, name) in self.instances

    def resolve(self, path, name, visited=frozenset(), *, type_only=False):
        # None distinguishes unresolved/ambiguous module evidence from a
        # known module which simply does not export this name. Barrels must
        # propagate the former rather than silently select another branch.
        if path is None:
            return None
        if path in visited:
            return []
        if "." in name:
            root, suffix = name.split(".", 1)
            owners = self.resolve(path, root, visited) or []
            if len(owners) != 1 or owners[0].get("kind") != "namespace":
                return None
            owner = owners[0]
            qualified = owner["qualified_name"] + "." + suffix
            return [s for s in self.by_file.get(owner["file_path"], []) if s.get("qualified_name") == qualified]
        visited = visited | {path}
        exports, stars = self._summary(path)
        found = []
        if name in exports:
            # Repeated declarations may describe one value (TS overloads or
            # a type/value pair). Distinct re-export identities stay ambiguous;
            # actual duplicate owners are retained below for owner selection.
            identities = set(exports[name])
            if len(identities) != 1:
                return None
            local, module = next(iter(identities))
            if module:
                return self.resolve(self.module_path(path, module), local, visited, type_only=type_only)
            symbols = [s for s in self.by_file.get(path, []) if s.get("qualified_name") == local]
            if type_only:
                return [s for s in symbols if s.get("kind") in {"class", "interface"}]
            values = [s for s in symbols if s.get("kind") not in {"type_alias", "interface"}]
            return values or symbols
        if name in {"default", "export="}:
            return []  # export * never forwards a default/export assignment
        for module in stars:
            branch = self.resolve(self.module_path(path, module), name, visited, type_only=type_only)
            if branch is None:
                return None
            found.extend(branch)
        return list({s["id"]: s for s in found}.values())
