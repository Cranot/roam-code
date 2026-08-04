"""Deterministic public-package surface detection for ``roam dead``.

Fail-closed contract (W-PSU). Every reason this module returns is *positive*
evidence that a symbol is externally facing. The absence of a reason is
therefore consumed by ``roam dead`` as proof of the negative -- the symbol is
bucketed SAFE under the published ``action_definition`` "SAFE = no production
consumers (graph proof)".

That inference is only sound while the evidence was actually READ. An
``__init__.py`` that does not parse, a malformed ``pyproject.toml``, an
unreadable ``setup.cfg`` or ``package.json`` produce exactly the same "no
reason" as a file that genuinely re-exports nothing. Absent evidence would
otherwise resolve to EQUAL ("not public") instead of to UNKNOWN.

So unreadable evidence is tracked explicitly and every candidate inside its
blast radius receives an ``unverifiable: ...`` reason. That reason is truthy,
so it flows through the existing ``external_facing`` path and downgrades the
candidate SAFE -> REVIEW, and it is disclosed to the caller so the envelope can
set ``partial_success``.
"""

from __future__ import annotations

import ast
import configparser
import json
from pathlib import Path

# 8 extensions: JS/TS source file types recognised when resolving a package's
# public entry points (.js/.jsx/.mjs/.cjs/.ts/.tsx/.mts/.cts).
_JS_EXTENSIONS = frozenset({".js", ".jsx", ".mjs", ".cjs", ".ts", ".tsx", ".mts", ".cts"})

# 2 extensions: the Python source file types whose public surface this
# module resolves -- .py (implementation) and .pyi (stub). A stub counts
# because a name exported only through a .pyi is still externally facing,
# and omitting it would let `dead` report such a symbol as unreferenced.
_PY_EXTENSIONS = frozenset({".py", ".pyi"})

#: Prefix marking a reason as "public-surface status could not be determined"
#: rather than "symbol is externally facing". Consumers must treat it as
#: UNKNOWN -- never as a proof of publicness and never as a proof of deadness.
UNVERIFIABLE_PREFIX = "unverifiable"


def _display(root: Path, path: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return path.as_posix()


def _unverifiable(root: Path, path: Path) -> str:
    return f"{UNVERIFIABLE_PREFIX}: {_display(root, path)} could not be parsed, re-export evidence unread"


def _source_path(root: Path, value: str) -> Path | None:
    path = Path(value.replace("\\", "/"))
    candidate = path if path.is_absolute() else root / path
    try:
        candidate = candidate.resolve()
        candidate.relative_to(root)
    except (OSError, ValueError):
        return None
    return candidate


def _read_python(path: Path) -> ast.Module | None:
    try:
        return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except (OSError, SyntaxError, UnicodeError):
        return None


def _string_collection(node: ast.AST) -> set[str] | None:
    if isinstance(node, (ast.List, ast.Tuple, ast.Set)):
        names = set()
        for item in node.elts:
            if not isinstance(item, ast.Constant) or not isinstance(item.value, str):
                return None
            names.add(item.value)
        return names
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        left = _string_collection(node.left)
        right = _string_collection(node.right)
        if left is not None and right is not None:
            return left | right
    return None


def _dunder_all(tree: ast.Module | None) -> set[str] | None:
    if tree is None:
        return None
    exports: set[str] | None = None
    for node in tree.body:
        value = None
        if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "__all__" for t in node.targets):
            value = node.value
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) and node.target.id == "__all__":
            value = node.value
        if value is not None:
            exports = _string_collection(value)
        elif (
            isinstance(node, ast.AugAssign)
            and isinstance(node.target, ast.Name)
            and node.target.id == "__all__"
            and isinstance(node.op, ast.Add)
        ):
            added = _string_collection(node.value)
            if exports is not None and added is not None:
                exports |= added
    return exports


def _candidate_module_matches(path: Path, module: str) -> bool:
    parts = tuple(module.split("."))
    file_parts = path.with_suffix("").parts
    if path.name == "__init__.py":
        file_parts = path.parent.parts
    return len(file_parts) >= len(parts) and file_parts[-len(parts) :] == parts


def _relative_import_paths(init_path: Path, node: ast.ImportFrom) -> tuple[Path, ...]:
    base = init_path.parent
    for _ in range(max(node.level - 1, 0)):
        base = base.parent
    if node.module:
        base = base.joinpath(*node.module.split("."))
    return (base.with_suffix(".py"), base / "__init__.py")


def _python_reexports(
    rows_by_path: dict[Path, list],
    python_trees: dict[Path, ast.Module | None],
    root: Path,
    unreadable: set[Path] | None = None,
) -> dict[int, str]:
    reasons: dict[int, str] = {}
    init_paths = {
        parent / "__init__.py"
        for path in rows_by_path
        if path.suffix in {".py", ".pyi"}
        for parent in path.parents
        if parent == root or root in parent.parents
        if (parent / "__init__.py").is_file()
    }
    for init_path in init_paths:
        tree = python_trees.setdefault(init_path, _read_python(init_path))
        init_all = _dunder_all(tree)
        if tree is None:
            # The barrel exists but is unreadable. Its re-export set is
            # UNKNOWN, not empty -- record it so the caller can fail closed.
            if unreadable is not None:
                unreadable.add(init_path)
            continue
        for node in tree.body:
            if not isinstance(node, ast.ImportFrom):
                continue
            if node.level:
                target_paths = set(_relative_import_paths(init_path, node))
                target_rows = [row for path in target_paths for row in rows_by_path.get(path, ())]
            elif node.module:
                target_rows = [
                    row
                    for path, rows in rows_by_path.items()
                    if _candidate_module_matches(path, node.module)
                    for row in rows
                ]
            else:
                target_rows = []
            for alias in node.names:
                if alias.name == "*":
                    for row in target_rows:
                        name = row["name"]
                        if row["parent_id"] is None and (
                            (init_all is None and not name.startswith("_")) or name in (init_all or set())
                        ):
                            reasons[row["id"]] = f"re-exported from {init_path.name}"
                    continue
                exposed_name = alias.asname or alias.name
                if exposed_name.startswith("_") or (init_all is not None and exposed_name not in init_all):
                    continue
                for row in target_rows:
                    if row["parent_id"] is None and row["name"] == alias.name:
                        reasons[row["id"]] = f"re-exported from {init_path.name}"
    return reasons


def _toml_data(path: Path) -> tuple[dict, bool]:
    """Return ``(data, readable)``. ``readable`` is False on a parse failure."""
    try:
        import tomllib
    except ImportError:  # pragma: no cover - Python 3.10
        import tomli as tomllib  # type: ignore[no-redef]
    try:
        with path.open("rb") as stream:
            return tomllib.load(stream), True
    except (OSError, tomllib.TOMLDecodeError):
        return {}, False


def _entry_point_targets(value) -> set[str]:
    if isinstance(value, str):
        return {value}
    if isinstance(value, dict):
        return {target for nested in value.values() for target in _entry_point_targets(nested)}
    return set()


def _setup_cfg_targets(path: Path) -> tuple[set[str], bool]:
    """Return ``(targets, readable)``. ``readable`` is False on a parse failure."""
    parser = configparser.ConfigParser(interpolation=None)
    try:
        parser.read(path, encoding="utf-8")
    except (configparser.Error, OSError, UnicodeError):
        return set(), False
    targets = set()
    for section in parser.sections():
        if not section.lower().startswith("options.entry_points"):
            continue
        for _key, value in parser.items(section):
            for line in value.splitlines():
                target = line.split("=", 1)[-1].strip()
                if ":" in target:
                    targets.add(target)
    return targets, True


def _python_entry_points(
    rows_by_path: dict[Path, list],
    root: Path,
    unreadable: set[Path] | None = None,
) -> dict[int, str]:
    targets = set()
    pyproject = root / "pyproject.toml"
    if pyproject.is_file():
        data, readable = _toml_data(pyproject)
        if not readable and unreadable is not None:
            unreadable.add(pyproject)
        project = data.get("project") or {}
        targets |= _entry_point_targets(project.get("scripts") or {})
        targets |= _entry_point_targets(project.get("entry-points") or {})
    setup_cfg = root / "setup.cfg"
    if setup_cfg.is_file():
        cfg_targets, readable = _setup_cfg_targets(setup_cfg)
        if not readable and unreadable is not None:
            unreadable.add(setup_cfg)
        targets |= cfg_targets

    reasons = {}
    for target in targets:
        module, separator, attribute = target.partition(":")
        if not separator:
            continue
        attribute = attribute.split("[", 1)[0].strip()
        for path, rows in rows_by_path.items():
            if path.suffix not in {".py", ".pyi"} or not _candidate_module_matches(path, module.strip()):
                continue
            for row in rows:
                if row["qualified_name"] == attribute:
                    reasons[row["id"]] = "declared package entry point"
    return reasons


def _package_entry_values(value) -> set[str]:
    if isinstance(value, str):
        return {value}
    if isinstance(value, list):
        return {entry for nested in value for entry in _package_entry_values(nested)}
    if isinstance(value, dict):
        return {entry for nested in value.values() for entry in _package_entry_values(nested)}
    return set()


def _js_package_entries(
    rows_by_path: dict[Path, list],
    root: Path,
    unreadable: set[Path] | None = None,
) -> dict[int, str]:
    manifests = {
        parent / "package.json"
        for path in rows_by_path
        if path.suffix.lower() in _JS_EXTENSIONS
        for parent in (path.parent, *path.parents)
        if root == parent or root in parent.parents
        if (parent / "package.json").is_file()
    }
    reasons = {}
    for manifest in manifests:
        try:
            package = json.loads(manifest.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            if unreadable is not None:
                unreadable.add(manifest)
            continue
        entries = set()
        for field in ("main", "module", "exports", "bin"):
            entries |= _package_entry_values(package.get(field))
        for entry in entries:
            if not entry.startswith((".", "/")) and ":" in entry:
                continue
            entry_path = (manifest.parent / entry.split("?", 1)[0]).resolve()
            for path, rows in rows_by_path.items():
                same_path = path == entry_path
                same_stem = not entry_path.suffix and path.parent == entry_path.parent and path.stem == entry_path.name
                if path.suffix.lower() in _JS_EXTENSIONS and (same_path or same_stem):
                    for row in rows:
                        reasons[row["id"]] = "exported from declared package.json entry"
    return reasons


def _blast_radius(source: Path, root: Path, path: Path) -> bool:
    """Is *path*'s public-surface status unknowable given *source* is unreadable?

    * an unreadable ``__init__.py`` can only re-export from its own package
      subtree, so the radius is that subtree's Python files;
    * an unreadable ``pyproject.toml`` / ``setup.cfg`` declares entry points by
      dotted module path and can therefore name any Python file in the repo;
    * an unreadable ``package.json`` declares entries by relative path, so the
      radius is the JS/TS files beneath the manifest;
    * an unreadable source file loses only its own ``__all__``.
    """
    if source.name == "__init__.py":
        return path.suffix in _PY_EXTENSIONS and (path == source or source.parent in path.parents)
    if source.name in {"pyproject.toml", "setup.cfg"} and source.parent == root:
        return path.suffix in _PY_EXTENSIONS
    if source.name == "package.json":
        return path.suffix.lower() in _JS_EXTENSIONS and source.parent in path.parents
    return path == source


def public_surface_evidence(rows, project_root: Path) -> tuple[dict[int, str], list[str]]:
    """Return ``(reasons, unreadable_sources)`` for the dead candidates.

    ``reasons`` mixes two kinds of entry and the caller must keep them apart
    only for wording -- both are truthy and both must block a SAFE verdict:

    * positive evidence (``"named in module __all__"``, ``"re-exported from
      __init__.py"``, ...) -- the symbol IS externally facing;
    * ``unverifiable: ...`` -- the evidence that would have decided it was
      unreadable, so the answer is UNKNOWN.

    ``unreadable_sources`` lists the repo-relative evidence files that failed
    to parse, for envelope disclosure.
    """
    root = project_root.resolve()
    rows_by_path: dict[Path, list] = {}
    for row in rows:
        path = _source_path(root, row["file_path"])
        if path is not None:
            rows_by_path.setdefault(path, []).append(row)

    python_trees = {path: _read_python(path) for path in rows_by_path if path.suffix in _PY_EXTENSIONS}
    reasons = {
        row["id"]: "named in module __all__"
        for path, rows_at_path in rows_by_path.items()
        if path in python_trees
        for row in rows_at_path
        if row["parent_id"] is None and row["name"] in (_dunder_all(python_trees[path]) or set())
    }
    unreadable: set[Path] = set()
    reasons.update(_python_reexports(rows_by_path, python_trees, root, unreadable))
    reasons.update(_python_entry_points(rows_by_path, root, unreadable))
    reasons.update(_js_package_entries(rows_by_path, root, unreadable))
    # A candidate whose own source will not parse lost its own ``__all__``.
    unreadable |= {path for path, tree in python_trees.items() if tree is None}

    # Fail closed: anything the unread evidence could have covered becomes an
    # explicit UNKNOWN instead of an implicit "not public".
    for source in sorted(unreadable):
        marker = _unverifiable(root, source)
        for path, rows_at_path in rows_by_path.items():
            if not _blast_radius(source, root, path):
                continue
            for row in rows_at_path:
                reasons.setdefault(row["id"], marker)
    return reasons, sorted(_display(root, source) for source in unreadable)


def public_surface_reasons(rows, project_root: Path) -> dict[int, str]:
    """Return dead-candidate ids that are externally facing or unverifiable."""
    return public_surface_evidence(rows, project_root)[0]
