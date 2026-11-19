"""Java inheritance extraction, using in-memory tree-sitter node shapes.

No grammar downloads, parser caches, or user-home access are needed. The node
shapes below match Java's superclass/generic_type/scoped_type_identifier AST.
"""

from types import SimpleNamespace

import pytest

from roam.languages.java_lang import JavaExtractor


class Node:
    """The tree-sitter Node interface consumed by the extractor."""

    def __init__(self, source, kind, text, children=(), **fields):
        self.type = kind
        self.start_byte = source.index(text)
        self.end_byte = self.start_byte + len(text)
        self.start_point = (0, self.start_byte)
        self.end_point = (0, self.end_byte)
        self.children = list(children)
        self.named_children = self.children
        self.prev_sibling = None
        self.fields = fields

    def child_by_field_name(self, name):
        return self.fields.get(name)


@pytest.mark.parametrize(
    "base,clause,edge_kind",
    [
        ("Base<T>", "extends", "inherits"),
        ("pkg.Base", "extends", "inherits"),
        ("pkg.Base<T>", "extends", "inherits"),
        ("Base", "extends", "inherits"),
        ("Base", "implements", "implements"),
    ],
)
def test_java_base_type_reference(base, clause, edge_kind):
    source = f"class A<T> {clause} {base} {{}}".encode()

    def node(kind, text, children=(), **fields):
        return Node(source, kind, text.encode(), children, **fields)

    base_node = node("type_identifier", "Base")
    if base.startswith("pkg."):
        package = node("type_identifier", "pkg")
        base_node = node("scoped_type_identifier", "pkg.Base", [package, base_node])
    if base.endswith("<T>"):
        argument = node("type_identifier", "T")
        arguments = node("type_arguments", "<T>", [argument])
        base_node = node("generic_type", base, [base_node, arguments])

    field = "superclass" if clause == "extends" else "interfaces"
    wrapper_kind = "superclass" if clause == "extends" else "super_interfaces"
    if clause == "implements":
        base_node = node("type_list", base, [base_node])
    wrapper = node(wrapper_kind, f"{clause} {base}", [base_node])
    name = node("identifier", "A")
    body = node("class_body", "{}")
    declaration = node(
        "class_declaration",
        source.decode(),
        [name, wrapper, body],
        name=name,
        body=body,
        **{field: wrapper},
    )
    tree = SimpleNamespace(root_node=node("program", source.decode(), [declaration]))

    extractor = JavaExtractor()
    symbols = extractor.extract_symbols(tree, source, "A.java")
    refs = extractor.extract_references(tree, source, "A.java")
    assert any(symbol["name"] == "A" for symbol in symbols)
    assert any(
        ref["source_name"] == "A" and ref["kind"] == edge_kind and ref["target_name"].rsplit(".", 1)[-1] == "Base"
        for ref in refs
    ), refs
