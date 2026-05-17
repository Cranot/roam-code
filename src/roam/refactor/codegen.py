"""Language-specific code generation for refactoring transforms."""

from __future__ import annotations

import logging
import os
import posixpath

_log = logging.getLogger(__name__)


def detect_language(file_path: str) -> str:
    """Detect language from file extension.

    Uses the roam language registry when available, falls back to
    extension-based detection for common languages.

    Fallback discipline (CLAUDE.md "Make fallback chains loud" /
    CP45-46-52-53): an unexpected registry failure is logged at DEBUG so
    repeat occurrences are not silently lost. A clean ``return None``
    from the registry is NOT a failure and stays quiet.
    """
    try:
        from roam.languages.registry import get_language_for_file

        lang = get_language_for_file(file_path)
        if lang:
            return lang
    except Exception as exc:  # pragma: no cover -- registry is best-effort
        _log.debug("language-registry lookup failed for %s: %s", file_path, exc)

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
        "rust", "java", "c", "cpp", "csharp",
        "swift", "kotlin", "scala", "dart",
    }
    if language in slash_comment_langs:
        return f"// TODO: import {symbol_name} from {from_file}"
    return f"# TODO: import {symbol_name} from {from_file}"
