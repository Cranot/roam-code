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
    if suffix:
        return (path,)
    extensions = (".ts", ".tsx", ".d.ts", ".js", ".jsx", ".mts", ".cts", ".mjs", ".cjs", ".json", ".vue", ".svelte")
    return (path, *(path + ext for ext in extensions), *(path + "/index" + ext for ext in extensions))
