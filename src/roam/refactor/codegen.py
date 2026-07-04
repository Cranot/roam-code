"""Language-specific code generation for refactoring transforms."""

from __future__ import annotations

import posixpath
from dataclasses import dataclass

# Re-export: detect_language moved to roam.index.parser (dedup); external
# callers still import it from here. The `as` form marks it as an explicit
# re-export so ruff's unused-import autofix does not strip it again.
from roam.index.parser import detect_language as detect_language


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


@dataclass(frozen=True)
class ImportRequest:
    """Value object bundling the four inputs an import statement is built from.

    Replaces the primitive-obsessed four-``str`` parameter list of
    :func:`generate_import` with a single named concept so the
    (language, source file, symbol, target file) tuple travels together.
    The per-language path math hangs off the object it describes instead of
    being computed from loose positional ``str`` arguments.
    """

    language: str
    source_file: str
    symbol_name: str
    target_file: str

    def python_module(self) -> str:
        """Dotted module path of the producer file (Python imports)."""
        return _python_module_path(self.source_file)

    def relative_path(self) -> str:
        """Consumer-relative import path to the producer file (JS/TS family)."""
        return compute_relative_path(self.target_file, self.source_file)

    def go_package(self) -> str:
        """Directory of the producer file as a Go package path."""
        pkg_path = posixpath.dirname(self.source_file.replace("\\", "/"))
        return pkg_path or "."


def _generate_import(request: ImportRequest) -> str:
    """Build the import statement for a bundled :class:`ImportRequest`."""
    language = request.language
    symbol_name = request.symbol_name

    if language == "python":
        return f"from {request.python_module()} import {symbol_name}"

    if language in ("javascript", "typescript", "tsx"):
        return f"import {{ {symbol_name} }} from '{request.relative_path()}'"

    if language == "go":
        return f'import "{request.go_package()}"'

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
        return f"// TODO: import {symbol_name} from {request.source_file}"
    return f"# TODO: import {symbol_name} from {request.source_file}"


def generate_import(language: str, from_file: str, symbol_name: str, target_file: str) -> str:
    """Generate an import statement based on language.

    Thin backwards-compatible adapter: bundles the four primitives into an
    :class:`ImportRequest` (the value object the W370c primitive-obsession
    finding asks for) and delegates to :func:`_generate_import`. New callers
    may construct an ``ImportRequest`` and call ``_generate_import`` directly;
    this positional signature is kept for the existing call site in
    ``roam.refactor.transforms`` and the ``test_mutate`` tests.

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
    return _generate_import(
        ImportRequest(
            language=language,
            source_file=from_file,
            symbol_name=symbol_name,
            target_file=target_file,
        )
    )
