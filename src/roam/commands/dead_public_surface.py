"""Deterministic public-package surface detection for ``roam dead``."""

from __future__ import annotations

import ast
import configparser
import json
from pathlib import Path

# 8 extensions: JS/TS source file types recognised when resolving a package's
# public entry points (.js/.jsx/.mjs/.cjs/.ts/.tsx/.mts/.cts).
_JS_EXTENSIONS = frozenset({".js", ".jsx", ".mjs", ".cjs", ".ts", ".tsx", ".mts", ".cts"})


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


def _toml_data(path: Path) -> dict:
    try:
        import tomllib
    except ImportError:  # pragma: no cover - Python 3.10
        import tomli as tomllib  # type: ignore[no-redef]
    try:
        with path.open("rb") as stream:
            return tomllib.load(stream)
    except (OSError, tomllib.TOMLDecodeError):
        return {}


def _entry_point_targets(value) -> set[str]:
    if isinstance(value, str):
        return {value}
    if isinstance(value, dict):
        return {target for nested in value.values() for target in _entry_point_targets(nested)}
    return set()


def _setup_cfg_targets(path: Path) -> set[str]:
    parser = configparser.ConfigParser(interpolation=None)
    try:
        parser.read(path, encoding="utf-8")
    except (configparser.Error, OSError, UnicodeError):
        return set()
    targets = set()
    for section in parser.sections():
        if not section.lower().startswith("options.entry_points"):
            continue
        for _key, value in parser.items(section):
            for line in value.splitlines():
                target = line.split("=", 1)[-1].strip()
                if ":" in target:
                    targets.add(target)
    return targets


def _python_entry_points(rows_by_path: dict[Path, list], root: Path) -> dict[int, str]:
    targets = set()
    pyproject = root / "pyproject.toml"
    if pyproject.is_file():
        project = _toml_data(pyproject).get("project") or {}
        targets |= _entry_point_targets(project.get("scripts") or {})
        targets |= _entry_point_targets(project.get("entry-points") or {})
    setup_cfg = root / "setup.cfg"
    if setup_cfg.is_file():
        targets |= _setup_cfg_targets(setup_cfg)

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


def _js_package_entries(rows_by_path: dict[Path, list], root: Path) -> dict[int, str]:
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


def public_surface_reasons(rows, project_root: Path) -> dict[int, str]:
    """Return dead-candidate ids that are explicitly externally facing."""
    root = project_root.resolve()
    rows_by_path: dict[Path, list] = {}
    for row in rows:
        path = _source_path(root, row["file_path"])
        if path is not None:
            rows_by_path.setdefault(path, []).append(row)

    python_trees = {path: _read_python(path) for path in rows_by_path if path.suffix in {".py", ".pyi"}}
    reasons = {
        row["id"]: "named in module __all__"
        for path, rows_at_path in rows_by_path.items()
        if path in python_trees
        for row in rows_at_path
        if row["parent_id"] is None and row["name"] in (_dunder_all(python_trees[path]) or set())
    }
    reasons.update(_python_reexports(rows_by_path, python_trees, root))
    reasons.update(_python_entry_points(rows_by_path, root))
    reasons.update(_js_package_entries(rows_by_path, root))
    return reasons
