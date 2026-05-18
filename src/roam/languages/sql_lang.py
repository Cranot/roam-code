from __future__ import annotations

from .base import LanguageExtractor


class SqlExtractor(LanguageExtractor):
    """Full SQL DDL symbol and reference extractor (Tier 1).

    Extracts tables, columns, views, functions, triggers, schemas,
    types (enums), sequences, and ALTER TABLE ADD COLUMN statements.
    Foreign keys (inline and constraint-level) produce graph edges.

    W539 — Foreign-key edges emit ``kind="reference"`` (singular) to
    match the canonical writer vocabulary in
    :mod:`roam.db.edge_kinds`. Earlier revisions emitted the plural
    ``"references"`` (matching the SQL keyword), which forced every
    reader to union both forms via ``REFERENCE_EDGE_KINDS``. The
    singular form keeps SQL aligned with every other language
    extractor while remaining accepted by readers that still pull
    the canonical set.
    """

    @property
    def language_name(self) -> str:
        return "sql"

    @property
    def file_extensions(self) -> list[str]:
        return [".sql"]

    def extract_symbols(self, tree, source: bytes, file_path: str) -> list[dict]:
        symbols: list[dict] = []
        self._pending_refs: list[dict] = []
        self._walk_statements(tree.root_node, source, symbols)
        return symbols

    def extract_references(self, tree, source: bytes, file_path: str) -> list[dict]:
        refs = list(getattr(self, "_pending_refs", []))
        self._pending_refs = []
        return refs

    def get_docstring(self, node, source: bytes) -> str | None:
        """SQL comments: -- line comment or /* block comment */ before node."""
        prev = node.prev_sibling
        if prev is None:
            return None
        if prev.type == "comment":
            text = self.node_text(prev, source).strip()
            if text.startswith("--"):
                text = text[2:].strip()
            return text or None
        if prev.type == "marginalia":
            text = self.node_text(prev, source).strip()
            if text.startswith("/*"):
                text = text[2:]
            if text.endswith("*/"):
                text = text[:-2]
            return text.strip() or None
        return None

    # ---- Statement dispatcher ----

    def _walk_statements(self, node, source: bytes, symbols: list[dict]) -> None:
        for child in node.children:
            if child.type == "statement":
                self._dispatch_statement(child, source, symbols)
            elif child.type in (
                "create_table",
                "create_view",
                "create_function",
                "create_trigger",
                "create_schema",
                "create_type",
                "create_sequence",
                "create_index",
                "alter_table",
            ):
                self._dispatch_node(child, source, symbols, node)

    def _dispatch_statement(self, stmt_node, source: bytes, symbols: list[dict]) -> None:
        for child in stmt_node.children:
            self._dispatch_node(child, source, symbols, stmt_node)

    def _dispatch_node(self, child, source: bytes, symbols: list[dict], parent) -> None:
        if child.type == "create_table":
            self._extract_create_table(child, source, symbols, parent)
        elif child.type == "create_view":
            self._extract_create_view(child, source, symbols, parent)
        elif child.type == "create_function":
            self._extract_create_function(child, source, symbols, parent)
        elif child.type == "create_trigger":
            self._extract_create_trigger(child, source, symbols, parent)
        elif child.type == "create_schema":
            self._extract_create_schema(child, source, symbols, parent)
        elif child.type == "create_type":
            self._extract_create_type(child, source, symbols, parent)
        elif child.type == "create_sequence":
            self._extract_create_sequence(child, source, symbols, parent)
        elif child.type == "create_index":
            self._extract_create_index(child, source, symbols)
        elif child.type == "alter_table":
            self._extract_alter_table(child, source, symbols)

    # ---- Table extraction ----

    def _extract_create_table(self, node, source, symbols, parent) -> None:
        name = self._get_object_name(node, source)
        if not name:
            return

        sig = f"CREATE TABLE {name}"

        docstring = self._get_statement_docstring(parent, node, source)

        symbols.append(
            self._make_symbol(
                name=name.split(".")[-1] if "." in name else name,
                kind="class",
                line_start=node.start_point[0] + 1,
                line_end=node.end_point[0] + 1,
                qualified_name=name,
                signature=sig,
                docstring=docstring,
                is_exported=True,
            )
        )

        # Extract columns
        col_defs = self._find_child(node, "column_definitions")
        if col_defs:
            self._extract_columns(col_defs, source, symbols, name)
            self._extract_constraint_refs(col_defs, source, name)

    def _extract_columns(self, col_defs_node, source, symbols, table_name) -> None:
        for child in col_defs_node.children:
            if child.type == "column_definition":
                self._extract_column(child, source, symbols, table_name)

    def _extract_column(self, node, source, symbols, table_name) -> None:
        col_name = None
        for child in node.children:
            if child.type == "identifier":
                col_name = self.node_text(child, source)
                break
        if not col_name:
            return

        col_type = self._get_column_type(node, source)
        constraints = self._get_column_constraints(node, source)

        sig_parts = [col_name]
        if col_type:
            sig_parts.append(col_type)
        if constraints:
            sig_parts.append(constraints)
        sig = " ".join(sig_parts)

        qualified = f"{table_name}.{col_name}"

        symbols.append(
            self._make_symbol(
                name=col_name,
                kind="field",
                line_start=node.start_point[0] + 1,
                line_end=node.end_point[0] + 1,
                qualified_name=qualified,
                signature=sig,
                is_exported=True,
                parent_name=table_name,
            )
        )

        # Check for inline FK reference
        self._extract_inline_fk_refs(node, source, table_name)

    def _get_column_type(self, col_node, source) -> str:
        """Extract column type text from a column_definition node."""
        # Types can be: int, varchar, decimal, keyword_serial, keyword_text,
        # keyword_boolean, timestamp, uuid, bigint, etc.
        type_node_types = {
            "int",
            "bigint",
            "smallint",
            "tinyint",
            "varchar",
            "char",
            "decimal",
            "numeric",
            "float",
            "double",
            "real",
            "timestamp",
            "date",
            "time",
            "datetime",
            "uuid",
            "keyword_serial",
            "keyword_text",
            "keyword_boolean",
            "keyword_int",
            "keyword_float",
            "keyword_char",
        }
        found_name = False
        for child in col_node.children:
            if child.type == "identifier" and not found_name:
                found_name = True
                continue
            if found_name and (child.type in type_node_types or child.type.startswith("keyword_")):
                # Check if it's actually a constraint keyword
                if child.type in (
                    "keyword_primary",
                    "keyword_key",
                    "keyword_not",
                    "keyword_null",
                    "keyword_unique",
                    "keyword_default",
                    "keyword_references",
                    "keyword_check",
                    "keyword_constraint",
                    "keyword_auto_increment",
                ):
                    break
                return self.node_text(child, source).upper()
        return ""

    def _get_column_constraints(self, col_node, source) -> str:
        """Extract constraint keywords from a column_definition."""
        constraint_keywords = {
            "keyword_primary": "PRIMARY",
            "keyword_key": "KEY",
            "keyword_not": "NOT",
            "keyword_null": "NULL",
            "keyword_unique": "UNIQUE",
            "keyword_default": "DEFAULT",
            "keyword_auto_increment": "AUTO_INCREMENT",
        }
        parts = []
        found_type = False
        for child in col_node.children:
            if child.type == "identifier" and not found_type:
                continue
            # Skip the type node itself
            if not found_type and child.type not in (
                "keyword_primary",
                "keyword_key",
                "keyword_not",
                "keyword_null",
                "keyword_unique",
                "keyword_default",
                "keyword_auto_increment",
                "keyword_references",
                ",",
                "(",
                ")",
            ):
                if child.type != "identifier":
                    found_type = True
                continue
            found_type = True
            if child.type in constraint_keywords:
                parts.append(constraint_keywords[child.type])
        return " ".join(parts)

    # ---- FK reference extraction ----

    def _extract_inline_fk_refs(self, col_node, source, table_name) -> None:
        """Extract inline REFERENCES from a column_definition."""
        has_ref = False
        for child in col_node.children:
            if child.type == "keyword_references":
                has_ref = True
            elif has_ref and child.type == "object_reference":
                ref_table = self._get_object_name_from_ref(child, source)
                if ref_table:
                    self._pending_refs.append(
                        self._make_reference(
                            target_name=ref_table,
                            kind="reference",  # W539
                            line=child.start_point[0] + 1,
                            source_name=table_name,
                        )
                    )
                return

    def _extract_constraint_refs(self, col_defs_node, source, table_name) -> None:
        """Extract table-level CONSTRAINT FOREIGN KEY ... REFERENCES refs."""
        for child in col_defs_node.children:
            if child.type == "constraints":
                for sub in child.children:
                    if sub.type == "constraint":
                        self._extract_single_constraint_ref(sub, source, table_name)

    def _extract_single_constraint_ref(self, constraint_node, source, table_name) -> None:
        """Extract FK reference from a single constraint node."""
        has_ref = False
        for child in constraint_node.children:
            if child.type == "keyword_references":
                has_ref = True
            elif has_ref and child.type == "object_reference":
                ref_table = self._get_object_name_from_ref(child, source)
                if ref_table:
                    self._pending_refs.append(
                        self._make_reference(
                            target_name=ref_table,
                            kind="reference",  # W539
                            line=child.start_point[0] + 1,
                            source_name=table_name,
                        )
                    )
                return

    # ---- View extraction ----

    def _extract_create_view(self, node, source, symbols, parent) -> None:
        name = self._get_object_name(node, source)
        if not name:
            return

        # Detect OR REPLACE
        has_or_replace = any(child.type == "keyword_replace" for child in node.children)
        if has_or_replace:
            sig = f"CREATE OR REPLACE VIEW {name}"
        else:
            sig = f"CREATE VIEW {name}"

        docstring = self._get_statement_docstring(parent, node, source)

        symbols.append(
            self._make_symbol(
                name=name.split(".")[-1] if "." in name else name,
                kind="class",
                line_start=node.start_point[0] + 1,
                line_end=node.end_point[0] + 1,
                qualified_name=name,
                signature=sig,
                docstring=docstring,
                is_exported=True,
            )
        )

        # Extract table references from view body
        self._extract_view_table_refs(node, source, name)

    def _extract_view_table_refs(self, view_node, source, view_name) -> None:
        """Extract table references from the view's SELECT query."""
        query = self._find_child(view_node, "create_query")
        if not query:
            return
        self._collect_relation_refs(query, source, view_name)

    def _collect_relation_refs(self, node, source, view_name) -> None:
        """Recursively collect table names from relation nodes."""
        for child in node.children:
            if child.type == "relation":
                obj_ref = self._find_child(child, "object_reference")
                if obj_ref:
                    table_name = self._get_object_name_from_ref(obj_ref, source)
                    if table_name:
                        self._pending_refs.append(
                            self._make_reference(
                                target_name=table_name,
                                kind="call",
                                line=obj_ref.start_point[0] + 1,
                                source_name=view_name,
                            )
                        )
            # Recurse into from, join, subquery etc.
            if child.type in ("from", "join", "select", "create_query", "subquery"):
                self._collect_relation_refs(child, source, view_name)

    # ---- Function extraction ----

    def _extract_create_function(self, node, source, symbols, parent) -> None:
        name = self._get_object_name(node, source)
        if not name:
            return

        # Build signature
        sig = f"CREATE FUNCTION {name}"
        args_node = self._find_child(node, "function_arguments")
        if args_node:
            sig += self.node_text(args_node, source)

        # Return type
        returns_type = self._get_function_return_type(node, source)
        if returns_type:
            sig += f" RETURNS {returns_type}"

        docstring = self._get_statement_docstring(parent, node, source)

        symbols.append(
            self._make_symbol(
                name=name.split(".")[-1] if "." in name else name,
                kind="function",
                line_start=node.start_point[0] + 1,
                line_end=node.end_point[0] + 1,
                qualified_name=name,
                signature=sig,
                docstring=docstring,
                is_exported=True,
            )
        )

    def _get_function_return_type(self, func_node, source) -> str:
        """Extract the RETURNS type from a create_function node."""
        found_returns = False
        for child in func_node.children:
            if child.type == "keyword_returns":
                found_returns = True
            elif found_returns and child.type not in ("keyword_returns",):
                if child.type == "function_body":
                    break
                return self.node_text(child, source).upper()
        return ""

    # ---- Trigger extraction ----

    def _extract_create_trigger(self, node, source, symbols, parent) -> None:
        name = self._get_object_name(node, source)
        if not name:
            return

        # Build signature: CREATE TRIGGER name BEFORE/AFTER event ON table
        timing = ""
        event = ""
        on_table = ""
        found_on = False
        for child in node.children:
            if child.type in ("keyword_before", "keyword_after", "keyword_instead"):
                timing = self.node_text(child, source).upper()
            elif child.type in (
                "keyword_insert",
                "keyword_update",
                "keyword_delete",
            ):
                event = self.node_text(child, source).upper()
            elif child.type == "keyword_on":
                found_on = True
            elif found_on and child.type == "object_reference" and not on_table:
                on_table = self._get_object_name_from_ref(child, source)
                found_on = False

        sig = f"CREATE TRIGGER {name}"
        if timing:
            sig += f" {timing}"
        if event:
            sig += f" {event}"
        if on_table:
            sig += f" ON {on_table}"

        docstring = self._get_statement_docstring(parent, node, source)

        symbols.append(
            self._make_symbol(
                name=name.split(".")[-1] if "." in name else name,
                kind="function",
                line_start=node.start_point[0] + 1,
                line_end=node.end_point[0] + 1,
                qualified_name=name,
                signature=sig,
                docstring=docstring,
                is_exported=True,
            )
        )

        # Extract trigger references
        self._extract_trigger_refs(node, source, name)

    def _extract_trigger_refs(self, trigger_node, source, trigger_name) -> None:
        """Extract table and function references from a trigger."""
        found_on = False
        found_execute = False
        for child in trigger_node.children:
            if child.type == "keyword_on":
                found_on = True
            elif found_on and child.type == "object_reference":
                table_name = self._get_object_name_from_ref(child, source)
                if table_name:
                    self._pending_refs.append(
                        self._make_reference(
                            target_name=table_name,
                            kind="call",
                            line=child.start_point[0] + 1,
                            source_name=trigger_name,
                        )
                    )
                found_on = False
            elif child.type in ("keyword_execute", "keyword_function"):
                found_execute = True
            elif found_execute and child.type == "object_reference":
                func_name = self._get_object_name_from_ref(child, source)
                if func_name:
                    self._pending_refs.append(
                        self._make_reference(
                            target_name=func_name,
                            kind="call",
                            line=child.start_point[0] + 1,
                            source_name=trigger_name,
                        )
                    )
                found_execute = False

    # ---- Schema extraction ----

    def _extract_create_schema(self, node, source, symbols, parent) -> None:
        name = None
        # create_schema uses bare identifier, not object_reference
        for child in node.children:
            if child.type == "identifier":
                name = self.node_text(child, source)
                break
        if not name:
            name = self._get_object_name(node, source)
        if not name:
            return

        sig = f"CREATE SCHEMA {name}"
        docstring = self._get_statement_docstring(parent, node, source)

        symbols.append(
            self._make_symbol(
                name=name,
                kind="module",
                line_start=node.start_point[0] + 1,
                line_end=node.end_point[0] + 1,
                qualified_name=name,
                signature=sig,
                docstring=docstring,
                is_exported=True,
            )
        )

    # ---- Type extraction ----

    def _extract_create_type(self, node, source, symbols, parent) -> None:
        name = self._get_object_name(node, source)
        if not name:
            return

        sig = f"CREATE TYPE {name}"
        # Check for ENUM
        has_enum = any(child.type == "keyword_enum" for child in node.children)
        if has_enum:
            enum_node = self._find_child(node, "enum_elements")
            if enum_node:
                sig += f" AS ENUM {self.node_text(enum_node, source)}"

        docstring = self._get_statement_docstring(parent, node, source)

        symbols.append(
            self._make_symbol(
                name=name.split(".")[-1] if "." in name else name,
                kind="type_alias",
                line_start=node.start_point[0] + 1,
                line_end=node.end_point[0] + 1,
                qualified_name=name,
                signature=sig,
                docstring=docstring,
                is_exported=True,
            )
        )

    # ---- Sequence extraction ----

    def _extract_create_sequence(self, node, source, symbols, parent) -> None:
        name = self._get_object_name(node, source)
        if not name:
            return

        sig = f"CREATE SEQUENCE {name}"
        docstring = self._get_statement_docstring(parent, node, source)

        symbols.append(
            self._make_symbol(
                name=name.split(".")[-1] if "." in name else name,
                kind="variable",
                line_start=node.start_point[0] + 1,
                line_end=node.end_point[0] + 1,
                qualified_name=name,
                signature=sig,
                docstring=docstring,
                is_exported=True,
            )
        )

    # ---- Index extraction (reference only, no symbol) ----

    def _extract_create_index(self, node, source, symbols) -> None:
        """Indexes don't produce symbols, but create a reference to the table."""
        found_on = False
        for child in node.children:
            if child.type == "keyword_on":
                found_on = True
            elif found_on and child.type == "object_reference":
                table_name = self._get_object_name_from_ref(child, source)
                if table_name:
                    # Find index name for source_name
                    idx_name = None
                    for c in node.children:
                        if c.type == "identifier":
                            idx_name = self.node_text(c, source)
                            break
                    self._pending_refs.append(
                        self._make_reference(
                            target_name=table_name,
                            kind="reference",  # W539
                            line=child.start_point[0] + 1,
                            source_name=idx_name,
                        )
                    )
                return

    # ---- ALTER TABLE ----

    def _extract_alter_table(self, node, source, symbols) -> None:
        """Extract ADD COLUMN from ALTER TABLE statements."""
        table_name = self._get_object_name(node, source)
        if not table_name:
            return

        for child in node.children:
            if child.type == "add_column":
                col_def = self._find_child(child, "column_definition")
                if col_def:
                    self._extract_column(col_def, source, symbols, table_name)

    # ---- Helpers ----

    def _get_object_name(self, node, source) -> str:
        """Get the name from the first object_reference child."""
        obj_ref = self._find_child(node, "object_reference")
        if obj_ref:
            return self._get_object_name_from_ref(obj_ref, source)
        return ""

    def _get_object_name_from_ref(self, obj_ref, source) -> str:
        """Extract name from an object_reference node (handles schema.name)."""
        parts = []
        for child in obj_ref.children:
            if child.type == "identifier":
                parts.append(self.node_text(child, source))
        return ".".join(parts) if parts else ""

    def _find_child(self, node, child_type: str):
        """Find first child of a given type."""
        for child in node.children:
            if child.type == child_type:
                return child
        return None

    def _get_statement_docstring(self, parent, node, source) -> str | None:
        """Get docstring from comment/marginalia preceding a statement."""
        # Check the statement-level parent for preceding comment
        if parent and parent.type == "statement":
            return self.get_docstring(parent, source)
        return self.get_docstring(node, source)
