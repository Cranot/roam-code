"""Language-specific code generation for refactoring transforms."""

from __future__ import annotations

import os
import posixpath


def detect_language(file_path: str) -> str:
    """Detect language from file extension.

    Uses the roam language registry when available, falls back to
    extension-based detection for common languages.
    """
    try:
        from roam.languages.registry import get_language_for_file
        lang = get_language_for_file(file_path)
        if lang:
            return lang
    except Exception:
        pass

    ext = os.path.splitext(file_path)[1].lower()
    ext_map = {
        ".py": "python",
        ".js": "javascript",
        ".jsx": "javascript",
        ".mjs": "javascript",
        ".ts": "typescript",
        ".tsx": "typescript",
        ".go": "go",
        ".rs": "rust",
        ".java": "java",
        ".rb": "ruby",
        ".php": "php",
        ".c": "c",
        ".h": "c",
        ".cpp": "cpp",
    }
    return ext_map.get(ext, "unknown")


def compute_relative_path(from_file: str, to_file: str) -> str:
    """Compute the relative import path between two files.

    Both paths are normalized to POSIX-style forward slashes.
    Extensions are stripped for import purposes.
    """
    from_file = from_file.replace("\\", "/")
    to_file = to_file.replace("\\", "/")

    from_dir = posixpath.dirname(from_file)
    rel = posixpath.relpath(to_file, from_dir)

    # Strip extension
    root, _ = posixpath.splitext(rel)
    # Ensure ./ prefix for same-directory or below
    if not root.startswith("."):
        root = "./" + root
    return root


def _python_module_path(file_path: str) -> str:
    """Convert a file path to a Python dotted module path.

    Strips .py extension and replaces / with dots.
    """
    path = file_path.replace("\\", "/")
    # Strip .py extension
    if path.endswith(".py"):
        path = path[:-3]
    # Convert slashes to dots
    return path.replace("/", ".")


def generate_import(language: str, from_file: str, symbol_name: str,
                    target_file: str) -> str:
    """Generate an import statement based on language.

    Parameters
    ----------
    language:
        Target language (python, javascript, typescript, go, etc.).
    from_file:
        The file where the symbol is defined.
    symbol_name:
        The name of the symbol to import.
    target_file:
        The file that needs the import (the consumer).
    """
    if language == "python":
        module_path = _python_module_path(from_file)
        return f"from {module_path} import {symbol_name}"

    if language in ("javascript", "typescript", "tsx"):
        rel = compute_relative_path(target_file, from_file)
        return f"import {{ {symbol_name} }} from '{rel}'"

    if language == "go":
        pkg_path = posixpath.dirname(from_file.replace("\\", "/"))
        if not pkg_path:
            pkg_path = "."
        return f'import "{pkg_path}"'

    # Default fallback
    return f"# import {symbol_name} from {from_file}"
