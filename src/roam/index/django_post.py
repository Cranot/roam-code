"""Post-indexing Django inheritance and custom field resolution.

Runs after all files are parsed and edges are resolved. Queries the DB
to build a cross-file inheritance graph, then batch-updates framework_type
and field metadata for Django models and custom fields.

Ported from upstream fork work.
"""

from __future__ import annotations

import json
import sys

# W543-followup-C: canonical inheritance-kind IN-clause helper. Both
# resolve_django_inheritance and resolve_django_custom_fields walk the
# same ``edges WHERE kind ∈ inheritance-kinds`` shape; the helper widens
# the legacy bare ``'inherits'`` literal to ``('inherits', 'implements',
# 'uses_trait')`` without losing the canonical kind that python_lang.py
# emits for Django models (the W156/W39.3 bridge contract still holds).
from roam.db.connection import batched_in
from roam.db.edge_kinds import inheritance_in_clause

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


def _load_class_graph(conn, columns: str) -> tuple[dict, dict]:
    """Load class symbols and their cross-file inheritance parent map.

    Returns ``(class_by_id, parent_ids)`` where ``parent_ids[child_id]``
    is the set of parent symbol IDs. Shared by the inheritance and
    custom-field resolvers, which walk the same graph shape.

    W543-followup-C: unions all three canonical inheritance kinds via the
    shared ``inheritance_in_clause`` helper (imported at module top).
    python_lang.py emits ``'inherits'`` for Django models (the W156/W39.3
    bridge contract is unchanged — the canonical writer is still
    ``'inherits'``), but reading via the helper means a future plugin
    extractor that emits ``'implements'`` / ``'uses_trait'`` for
    Django-shaped class hierarchies will still flow through the
    post-resolvers.
    """
    class_rows = conn.execute(f"SELECT {columns} FROM symbols WHERE kind = 'class'").fetchall()
    class_by_id = {r["id"]: dict(r) for r in class_rows}
    if not class_by_id:
        return {}, {}

    inherits_rows = conn.execute(
        f"SELECT source_id, target_id FROM edges WHERE {inheritance_in_clause('kind')}"
    ).fetchall()
    parent_ids = {}
    for r in inherits_rows:
        if r["source_id"] in class_by_id:
            parent_ids.setdefault(r["source_id"], set()).add(r["target_id"])
    return class_by_id, parent_ids


def resolve_django_inheritance(conn) -> int:
    """Resolve transitive Django model inheritance across all indexed files.

    Queries inherits edges and symbols table to build a full inheritance
    graph, walks transitively with cycle detection, and batch-updates
    framework_type='django_model' on all transitive Django model descendants.

    Returns the number of symbols updated.
    """
    class_by_id, parent_ids = _load_class_graph(conn, "id, name, qualified_name, framework_type")
    if not class_by_id:
        return 0

    # 1. Already-tagged: symbols with framework_type='django_model' (fast-path)
    already_tagged = {sid for sid, info in class_by_id.items() if info["framework_type"] == "django_model"}

    # 2. Transitive resolution with memoization
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

    # 3. Walk all class symbols
    to_update = []
    for sid in class_by_id:
        if sid in already_tagged:
            continue
        if _is_django_model(sid, set()):
            to_update.append(sid)

    # 4. Batch update
    if to_update:
        with conn:
            conn.executemany(
                "UPDATE symbols SET framework_type = 'django_model' WHERE id = ?",
                [(sid,) for sid in to_update],
            )

    return len(to_update)


def _build_custom_field_map(class_by_id: dict, parent_ids: dict) -> dict:
    """Map custom field class names to their nearest Django base field type.

    Walks the inheritance graph with cycle detection, honouring the
    python_lang.py fast-path tag (framework_type='django_field' +
    field_base_type). Classes that ARE canonical Django field types are
    excluded from the returned map.
    """
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

    custom_field_map = {}
    for sid, info in class_by_id.items():
        base = _resolve_field_base(sid, set())
        if base is not None and info["name"] not in _DJANGO_FIELD_TYPES:
            custom_field_map[info["name"]] = base
    return custom_field_map


def _target_model_name_for_edge(prop, base_type: str) -> str | None:
    """Extract the short target model name from a relationship property.

    Returns None unless the property is a relationship field and its
    field_metadata names a target_model.
    """
    if base_type not in _DJANGO_RELATIONSHIP_FIELDS or not prop["field_metadata"]:
        return None
    try:
        meta = json.loads(prop["field_metadata"])
    except (json.JSONDecodeError, TypeError):
        return None
    target_model = meta.get("target_model")
    if not target_model:
        return None
    return target_model.split(".")[-1] or None


def _relationship_edge(prop, base_type: str, target_name: str, class_ids_by_name: dict[str, int]) -> dict | None:
    """Build a django_fk/o2o/m2m edge dict for a relationship custom field.

    Returns None unless the property belongs to a parent class symbol and
    ``target_name`` resolves to a class ID in the pre-loaded map.
    """
    parent_id = prop.get("parent_id")
    if not parent_id:
        return None
    edge_kind = _DJANGO_REL_KIND.get(base_type)
    if not edge_kind:
        return None
    target_id = class_ids_by_name.get(target_name)
    if not target_id:
        return None
    return {
        "source_id": parent_id,
        "target_id": target_id,
        "kind": edge_kind,
        "line": prop["line_start"],
        "source_file_id": prop["file_id"],
    }


def _load_existing_relationship_keys_to_preserve_idempotency(conn) -> set[tuple[int, int, str]]:
    rows = conn.execute(
        "SELECT source_id, target_id, kind FROM edges WHERE kind IN ('django_fk', 'django_o2o', 'django_m2m')"
    ).fetchall()
    return {(row["source_id"], row["target_id"], row["kind"]) for row in rows}


def _relationship_type_from_field_aliases(prop) -> str | None:
    ft = prop["field_type"] or ""
    if ft in _DJANGO_RELATIONSHIP_FIELDS:
        return ft
    fbt = prop["field_base_type"] or ""
    if fbt in _DJANGO_RELATIONSHIP_FIELDS:
        return fbt
    return None


def _normalized_relationship_target_name(prop) -> str | None:
    try:
        meta = json.loads(prop["field_metadata"])
    except (json.JSONDecodeError, TypeError):
        return None
    target_model = meta.get("target_model")
    if not target_model:
        return None

    target_name = target_model.split(".")[-1]
    if target_name == "self":
        target_name = prop["parent_name"]
    return target_name or None


def _load_class_ids_for_relationship_targets(conn, target_names) -> dict[str, int]:
    names = sorted({name for name in target_names if name})
    rows = batched_in(
        conn,
        "SELECT id, name FROM symbols WHERE kind = 'class' AND name IN ({ph}) ORDER BY id",
        names,
    )
    class_ids_by_name = {}
    for row in rows:
        class_ids_by_name.setdefault(row["name"], row["id"])
    return class_ids_by_name


def resolve_django_custom_fields(conn) -> int:
    """Resolve custom Django field types across all indexed files.

    Queries inherits edges to find classes inheriting from Django field
    types, builds a cross-file custom field map, then updates property
    symbols that use these custom fields.

    Returns the number of symbols updated.
    """
    # 1. Load class graph (framework_type + field_base_type feed the fast-path seeding)
    class_by_id, parent_ids = _load_class_graph(conn, "id, name, qualified_name, framework_type, field_base_type")
    if not class_by_id:
        return 0

    # 2. Resolve custom field types: classes whose ancestor is a Django field type
    custom_field_map = _build_custom_field_map(class_by_id, parent_ids)
    if not custom_field_map:
        return 0

    # 3. Find property symbols with call_function matching custom fields
    props = conn.execute(
        "SELECT id, parent_id, call_function, field_metadata, file_id, line_start "
        "FROM symbols WHERE call_function IS NOT NULL AND kind = 'property'"
    ).fetchall()

    updates = []
    edge_candidates = []
    target_names = set()
    for prop in props:
        call_name = prop["call_function"]
        if call_name not in custom_field_map:
            continue
        base_type = custom_field_map[call_name]
        updates.append(
            {
                "id": prop["id"],
                "field_type": call_name,
                "field_base_type": base_type,
            }
        )
        target_name = _target_model_name_for_edge(prop, base_type)
        if target_name:
            target_names.add(target_name)
            edge_candidates.append((prop, base_type, target_name))

    class_ids_by_name = _load_class_ids_for_relationship_targets(conn, target_names)

    new_edges = []
    for prop, base_type, target_name in edge_candidates:
        edge = _relationship_edge(prop, base_type, target_name, class_ids_by_name)
        if edge is not None:
            new_edges.append(edge)

    # 4. Batch update
    if updates:
        with conn:
            conn.executemany(
                "UPDATE symbols SET field_type = ?, field_base_type = ? WHERE id = ?",
                [(u["field_type"], u["field_base_type"], u["id"]) for u in updates],
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
    existing = _load_existing_relationship_keys_to_preserve_idempotency(conn)

    # 2. Find properties with field_metadata containing target_model
    props = conn.execute(
        "SELECT s.id, s.field_type, s.field_base_type, s.field_metadata, "
        "       s.file_id, s.line_start, s.parent_id, p.name AS parent_name "
        "FROM symbols s "
        "LEFT JOIN symbols p ON s.parent_id = p.id "
        "WHERE s.kind = 'property' AND s.field_metadata IS NOT NULL "
        "AND s.parent_id IS NOT NULL"
    ).fetchall()

    edge_candidates = []
    target_names = set()
    for prop in props:
        # Determine the base relationship type
        rel_type = _relationship_type_from_field_aliases(prop)
        if not rel_type:
            continue

        target_name = _normalized_relationship_target_name(prop)
        if not target_name:
            continue

        if rel_type not in _DJANGO_REL_KIND:
            continue
        edge_kind = _DJANGO_REL_KIND[rel_type]

        target_names.add(target_name)
        edge_candidates.append((prop, target_name, edge_kind))

    class_ids_by_name = _load_class_ids_for_relationship_targets(conn, target_names)

    new_edges = []
    for prop, target_name, edge_kind in edge_candidates:
        if target_name not in class_ids_by_name:
            continue
        target_id = class_ids_by_name[target_name]
        source_id = prop["parent_id"]
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
