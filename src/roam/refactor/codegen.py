"""Language-specific code generation for refactoring transforms."""

from __future__ import annotations

import posixpath

from roam.index.parser import detect_language


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


def generate_import(language: str, from_file: str, symbol_name: str, target_file: str) -> str:
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

    # Default fallback: emit a comment in a syntax appropriate for the
    # target language so the generated text is at least syntactically
    # valid where roam can guess the comment prefix. Pattern-2 "make
    # fallback chains loud" — the TODO marker signals a human-review
    # touchpoint rather than silently shipping a Python-style ``#`` into
    # a Rust/Java/C/PHP file (where ``#`` is the start of a preprocessor
    # directive or a syntax error, not a comment).
    #
    # ``go``, ``javascript``, ``typescript``, ``tsx`` are NOT in this set
    # because they have dedicated branches above; listing them here would
    # be dead code that drifts the moment a branch is removed.
    slash_comment_langs = {
        "rust",
        "java",
        "c",
        "cpp",
        "csharp",
        "swift",
        "kotlin",
        "scala",
        "dart",
    }
    if language in slash_comment_langs:
        return f"// TODO: import {symbol_name} from {from_file}"
    return f"# TODO: import {symbol_name} from {from_file}"
