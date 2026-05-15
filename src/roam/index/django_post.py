"""Post-indexing Django inheritance and custom field resolution.

Runs after all files are parsed and edges are resolved. Queries the DB
to build a cross-file inheritance graph, then batch-updates framework_type
and field metadata for Django models and custom fields.

Ported from `upstream fork/roam-code` — credit upstream fork author.
"""

from __future__ import annotations

import json
import sys

# Django model + field vocabulary. Lives here because Django post-processing
# is the only consumer in roam-code; `python_lang.py` is the generic Python
# extractor and stays framework-agnostic. W902 audit verified these are not
# duplicated anywhere else in the tree.
_DJANGO_MODEL_BASES = frozenset(
    {
        "Model",
        "models.Model",
        "TimeStampedModel",
        "AbstractUser",
        "AbstractBaseUser",
    }
)

_DJANGO_FIELD_TYPES = frozenset(
    {
        "CharField",
        "IntegerField",
        "FloatField",
        "DecimalField",
        "BooleanField",
        "TextField",
        "DateField",
        "DateTimeField",
        "TimeField",
        "EmailField",
        "URLField",
        "UUIDField",
        "SlugField",
        "FileField",
        "ImageField",
        "JSONField",
        "BinaryField",
        "AutoField",
        "BigAutoField",
        "SmallAutoField",
        "BigIntegerField",
        "SmallIntegerField",
        "PositiveIntegerField",
        "PositiveSmallIntegerField",
        "PositiveBigIntegerField",
        "DurationField",
        "GenericIPAddressField",
        "FilePathField",
        "ForeignKey",
        "OneToOneField",
        "ManyToManyField",
    }
)

_DJANGO_RELATIONSHIP_FIELDS = frozenset(
    {
        "ForeignKey",
        "OneToOneField",
        "ManyToManyField",
    }
)

_DJANGO_REL_KIND = {
    "ForeignKey": "django_fk",
    "OneToOneField": "django_o2o",
    "ManyToManyField": "django_m2m",
}


def resolve_django_inheritance(conn) -> int:
    """Resolve transitive Django model inheritance across all indexed files.

    Queries inherits edges and symbols table to build a full inheritance
    graph, walks transitively with cycle detection, and batch-updates
    framework_type='django_model' on all transitive Django model descendants.

    Returns the number of symbols updated.
    """
    # 1. Load class symbols: {id: name, qualified_name, framework_type}
    class_rows = conn.execute(
        "SELECT id, name, qualified_name, framework_type FROM symbols WHERE kind = 'class'"
    ).fetchall()
    if not class_rows:
        return 0

    # Build lookup maps
    class_by_id = {r["id"]: dict(r) for r in class_rows}
    # Map name -> set of symbol IDs (multiple classes can share a name)
    ids_by_name = {}
    for r in class_rows:
        ids_by_name.setdefault(r["name"], set()).add(r["id"])

    # 2. Load inherits edges: source_id inherits from target_id
    inherits_rows = conn.execute("SELECT source_id, target_id FROM edges WHERE kind = 'inherits'").fetchall()

    # parent_ids[child_id] = set of parent symbol IDs
    parent_ids = {}
    for r in inherits_rows:
        src, tgt = r["source_id"], r["target_id"]
        if src in class_by_id:
            parent_ids.setdefault(src, set()).add(tgt)

    # 3. Already-tagged: symbols with framework_type='django_model' (fast-path)
    already_tagged = {sid for sid, info in class_by_id.items() if info["framework_type"] == "django_model"}

    # 4. Transitive resolution with memoization
    resolved = {}  # symbol_id -> bool

    def _is_django_model(sid, visited):
        if sid in resolved:
            return resolved[sid]
        if sid in already_tagged:
            resolved[sid] = True
            return True
        if sid in visited:
            resolved[sid] = False
            return False
        visited = visited | {sid}

        # Check if any parent is a django model
        for pid in parent_ids.get(sid, set()):
            if pid in already_tagged:
                resolved[sid] = True
                return True
            if pid in class_by_id:
                if _is_django_model(pid, visited):
                    resolved[sid] = True
                    return True

        resolved[sid] = False
        return False

    # 5. Walk all class symbols
    to_update = []
    for sid in class_by_id:
        if sid in already_tagged:
            continue
        if _is_django_model(sid, set()):
            to_update.append(sid)

    # 6. Batch update
    if to_update:
        with conn:
            for sid in to_update:
                conn.execute(
                    "UPDATE symbols SET framework_type = 'django_model' WHERE id = ?",
                    (sid,),
                )

    return len(to_update)


def resolve_django_custom_fields(conn) -> int:
    """Resolve custom Django field types across all indexed files.

    Queries inherits edges to find classes inheriting from Django field
    types, builds a cross-file custom field map, then updates property
    symbols that use these custom fields.

    Returns the number of symbols updated.
    """
    # 1. Load class symbols (include framework_type and field_base_type for fast-path seeding)
    class_rows = conn.execute(
        "SELECT id, name, qualified_name, framework_type, field_base_type FROM symbols WHERE kind = 'class'"
    ).fetchall()
    if not class_rows:
        return 0

    class_by_id = {r["id"]: dict(r) for r in class_rows}
    ids_by_name = {}
    for r in class_rows:
        ids_by_name.setdefault(r["name"], set()).add(r["id"])

    # 2. Load inherits edges
    inherits_rows = conn.execute("SELECT source_id, target_id FROM edges WHERE kind = 'inherits'").fetchall()
    parent_ids = {}
    for r in inherits_rows:
        src, tgt = r["source_id"], r["target_id"]
        if src in class_by_id:
            parent_ids.setdefault(src, set()).add(tgt)

    # 3. Resolve custom field types: find classes whose ancestor is a Django field type
    resolved = {}  # symbol_id -> base_field_type or None

    def _resolve_field_base(sid, visited):
        if sid in resolved:
            return resolved[sid]
        info = class_by_id.get(sid)
        if info is None:
            return None
        name = info["name"]
        if name in _DJANGO_FIELD_TYPES:
            resolved[sid] = name
            return name
        # Fast-path: class tagged by python_lang.py as directly extending a Django field
        if info.get("framework_type") == "django_field" and info.get("field_base_type"):
            resolved[sid] = info["field_base_type"]
            return info["field_base_type"]
        if sid in visited:
            resolved[sid] = None
            return None
        visited = visited | {sid}
        for pid in parent_ids.get(sid, set()):
            if pid in class_by_id and class_by_id[pid]["name"] in _DJANGO_FIELD_TYPES:
                resolved[sid] = class_by_id[pid]["name"]
                return class_by_id[pid]["name"]
            base = _resolve_field_base(pid, visited)
            if base is not None:
                resolved[sid] = base
                return base
        resolved[sid] = None
        return None

    # Build name -> base_field_type map
    custom_field_map = {}
    for sid, info in class_by_id.items():
        base = _resolve_field_base(sid, set())
        if base is not None and info["name"] not in _DJANGO_FIELD_TYPES:
            custom_field_map[info["name"]] = base

    if not custom_field_map:
        return 0

    # 4. Find property symbols with call_function matching custom fields
    props = conn.execute(
        "SELECT id, call_function, field_metadata, file_id, line_start "
        "FROM symbols WHERE call_function IS NOT NULL AND kind = 'property'"
    ).fetchall()

    updates = []
    new_edges = []
    for prop in props:
        call_name = prop["call_function"]
        if call_name not in custom_field_map:
            continue

        base_type = custom_field_map[call_name]
        update = {
            "id": prop["id"],
            "field_type": call_name,
            "field_base_type": base_type,
        }
        updates.append(update)

        # For relationship custom fields, create edges
        if base_type in _DJANGO_RELATIONSHIP_FIELDS and prop["field_metadata"]:
            try:
                meta = json.loads(prop["field_metadata"])
            except (json.JSONDecodeError, TypeError):
                meta = {}
            target_model = meta.get("target_model")
            if target_model:
                # Find the parent class symbol (the model this property belongs to)
                parent_row = conn.execute("SELECT parent_id FROM symbols WHERE id = ?", (prop["id"],)).fetchone()
                if parent_row and parent_row["parent_id"]:
                    edge_kind = _DJANGO_REL_KIND.get(base_type)
                    if edge_kind:
                        # Find target symbol by name
                        target_sym = conn.execute(
                            "SELECT id FROM symbols WHERE name = ? AND kind = 'class' LIMIT 1",
                            (target_model.split(".")[-1],),
                        ).fetchone()
                        if target_sym:
                            new_edges.append(
                                {
                                    "source_id": parent_row["parent_id"],
                                    "target_id": target_sym["id"],
                                    "kind": edge_kind,
                                    "line": prop["line_start"],
                                    "source_file_id": prop["file_id"],
                                }
                            )

    # 5. Batch update
    if updates:
        with conn:
            for u in updates:
                conn.execute(
                    "UPDATE symbols SET field_type = ?, field_base_type = ? WHERE id = ?",
                    (u["field_type"], u["field_base_type"], u["id"]),
                )

    if new_edges:
        with conn:
            conn.executemany(
                "INSERT INTO edges (source_id, target_id, kind, line, source_file_id) VALUES (?, ?, ?, ?, ?)",
                [(e["source_id"], e["target_id"], e["kind"], e["line"], e["source_file_id"]) for e in new_edges],
            )

    return len(updates)


def resolve_django_relationships(conn) -> int:
    """Resolve Django FK/O2O/M2M edges from field_metadata.

    Handles dotted target models (e.g., 'core.Currency') and 'self' references
    by stripping the app prefix and looking up by class name.  Only creates
    edges that don't already exist (avoids duplicates with reference-resolution).

    Returns the number of new edges created.
    """
    # 1. Load existing relationship edges to avoid duplicates
    existing = set()
    for row in conn.execute(
        "SELECT source_id, target_id, kind FROM edges WHERE kind IN ('django_fk', 'django_o2o', 'django_m2m')"
    ).fetchall():
        existing.add((row["source_id"], row["target_id"], row["kind"]))

    # 2. Find properties with field_metadata containing target_model
    props = conn.execute(
        "SELECT s.id, s.field_type, s.field_base_type, s.field_metadata, "
        "       s.file_id, s.line_start, s.parent_id, p.name AS parent_name "
        "FROM symbols s "
        "LEFT JOIN symbols p ON s.parent_id = p.id "
        "WHERE s.kind = 'property' AND s.field_metadata IS NOT NULL "
        "AND s.parent_id IS NOT NULL"
    ).fetchall()

    new_edges = []
    for prop in props:
        # Determine the base relationship type
        ft = prop["field_type"] or ""
        fbt = prop["field_base_type"] or ""
        rel_type = None
        if ft in _DJANGO_RELATIONSHIP_FIELDS:
            rel_type = ft
        elif fbt in _DJANGO_RELATIONSHIP_FIELDS:
            rel_type = fbt
        if not rel_type:
            continue

        try:
            meta = json.loads(prop["field_metadata"])
        except (json.JSONDecodeError, TypeError):
            continue
        target_model = meta.get("target_model")
        if not target_model:
            continue

        # Strip app prefix: "core.Currency" -> "Currency"
        target_name = target_model.split(".")[-1]

        # Handle "self" references
        if target_name == "self":
            target_name = prop["parent_name"]
        if not target_name:
            continue

        edge_kind = _DJANGO_REL_KIND.get(rel_type)
        if not edge_kind:
            continue

        # Find target class symbol
        target_sym = conn.execute(
            "SELECT id FROM symbols WHERE name = ? AND kind = 'class' LIMIT 1",
            (target_name,),
        ).fetchone()
        if not target_sym:
            continue

        source_id = prop["parent_id"]
        target_id = target_sym["id"]
        edge_key = (source_id, target_id, edge_kind)
        if edge_key in existing:
            continue
        existing.add(edge_key)

        new_edges.append(
            {
                "source_id": source_id,
                "target_id": target_id,
                "kind": edge_kind,
                "line": prop["line_start"],
                "source_file_id": prop["file_id"],
            }
        )

    if new_edges:
        with conn:
            conn.executemany(
                "INSERT INTO edges (source_id, target_id, kind, line, source_file_id) VALUES (?, ?, ?, ?, ?)",
                [(e["source_id"], e["target_id"], e["kind"], e["line"], e["source_file_id"]) for e in new_edges],
            )

    return len(new_edges)


def _log(msg: str):
    """Log to stderr."""
    sys.stderr.write(f"{msg}\n")
    sys.stderr.flush()


def resolve_all_django(conn, quiet: bool = False) -> dict:
    """Run all Django post-indexing resolution steps.

    Returns a dict with counts of updated symbols.
    """
    model_count = resolve_django_inheritance(conn)
    if not quiet and model_count:
        _log(f"  Django model inheritance: {model_count} symbols updated")

    field_count = resolve_django_custom_fields(conn)
    if not quiet and field_count:
        _log(f"  Django custom fields: {field_count} symbols updated")

    rel_count = resolve_django_relationships(conn)
    if not quiet and rel_count:
        _log(f"  Django relationships: {rel_count} edges created")

    return {
        "models_updated": model_count,
        "fields_updated": field_count,
        "relationships_created": rel_count,
    }
