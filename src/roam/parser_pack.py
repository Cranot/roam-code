"""Bounded access to the tree-sitter language-pack backend.

The language pack performs one checksum-verified platform-bundle retrieval on
the first parser load.  A transient transport timeout must not make a fresh
Roam install unusable, while integrity, availability, and runtime failures must
remain loud.  Keep the retry policy centralized so every parser consumer has
the same boundary semantics.
"""

from __future__ import annotations

import os
import time
from collections.abc import Callable
from threading import RLock
from typing import TypeVar

import tree_sitter_language_pack as _language_pack
from tree_sitter import Language, Parser

DownloadError = _language_pack.DownloadError
LanguageNotFoundError = _language_pack.LanguageNotFoundError

SEALED_PRODUCTION_GRAMMARS = (
    "bash",
    "c",
    "cpp",
    "csharp",
    "css",
    "dart",
    "elisp",
    "elixir",
    "erlang",
    "go",
    "haskell",
    "html",
    "java",
    "javascript",
    "json",
    "julia",
    "kotlin",
    "lua",
    "markdown",
    "ocaml",
    "php",
    "python",
    "r",
    "ruby",
    "rust",
    "scala",
    "scss",
    "sql",
    "svelte",
    "swift",
    "toml",
    "tsx",
    "typescript",
    "vue",
    "zig",
)

_DOWNLOAD_RETRY_DELAYS_SECONDS = (0.25, 1.0)
_T = TypeVar("_T")
_configured_cache_path: str | None = None
_sealed_inventory: tuple[str, frozenset[str]] | None = None
_sealed_cache_path: str | None = None
_state_lock = RLock()


def _configure_reviewed_cache() -> None:
    """Select an operator-pinned parser cache without changing XDG caches."""
    global _configured_cache_path

    value = os.environ.get("ROAM_TREE_SITTER_CACHE_DIR")
    if value is None:
        return
    if (
        not value
        or len(value) > 4096
        or "\x00" in value
        or not os.path.isabs(value)
        or os.path.normpath(value) != value
    ):
        raise RuntimeError("ROAM_TREE_SITTER_CACHE_DIR must be a canonical absolute path")
    with _state_lock:
        if _configured_cache_path != value:
            _language_pack.configure(_language_pack.PackConfig(cache_dir=value))
            _configured_cache_path = value
        if _language_pack.cache_dir() != value:
            raise RuntimeError("tree-sitter language pack did not select the sealed cache path")


def _sealed_cache_enabled() -> bool:
    global _sealed_cache_path

    value = os.environ.get("ROAM_TREE_SITTER_CACHE_SEALED")
    cache_path = os.environ.get("ROAM_TREE_SITTER_CACHE_DIR")
    with _state_lock:
        if _sealed_cache_path is not None:
            if value != "1" or cache_path != _sealed_cache_path:
                raise RuntimeError("sealed parser mode cannot be disabled or redirected in-process")
            return True
        if value is None:
            return False
    if value != "1":
        raise RuntimeError("ROAM_TREE_SITTER_CACHE_SEALED must be exactly 1 when set")
    if cache_path is None:
        raise RuntimeError("sealed parser mode requires ROAM_TREE_SITTER_CACHE_DIR")
    with _state_lock:
        _sealed_cache_path = cache_path
    return True


def _prepare_grammar(grammar: str) -> bool:
    """Configure the backend and prove sealed grammar availability."""
    global _sealed_inventory

    sealed = _sealed_cache_enabled()
    _configure_reviewed_cache()
    if not sealed:
        return False
    cache_path = os.environ["ROAM_TREE_SITTER_CACHE_DIR"]
    with _state_lock:
        try:
            installed = frozenset(_language_pack.downloaded_languages())
        except Exception as error:
            raise RuntimeError("sealed parser cache inventory is unavailable") from error
        _sealed_inventory = (cache_path, installed)
    if grammar not in installed:
        raise RuntimeError(f"sealed parser cache is missing required grammar: {grammar}")
    return True


def has_language(grammar: str) -> bool:
    """Return package availability without triggering parser acquisition."""

    _prepare_grammar(grammar)
    return _language_pack.has_language(grammar)


def _acquire_with_retry(factory: Callable[[str], _T], grammar: str) -> _T:
    """Retry only transient download failures, then preserve the final error."""

    for delay in (*_DOWNLOAD_RETRY_DELAYS_SECONDS, None):
        try:
            return factory(grammar)
        except DownloadError:
            if delay is None:
                raise
            time.sleep(delay)
    raise AssertionError("parser acquisition retry loop exhausted without returning or raising")


def get_parser(grammar: str) -> Parser:
    """Acquire a parser with bounded retry for transient bundle downloads."""

    if _prepare_grammar(grammar):
        try:
            return _language_pack.get_parser(grammar)
        except DownloadError as error:
            raise RuntimeError(f"sealed parser cache could not load grammar: {grammar}") from error
    return _acquire_with_retry(_language_pack.get_parser, grammar)


def get_language(grammar: str) -> Language:
    """Acquire a language with the same bounded first-use download policy."""

    if _prepare_grammar(grammar):
        try:
            return _language_pack.get_language(grammar)
        except DownloadError as error:
            raise RuntimeError(f"sealed parser cache could not load grammar: {grammar}") from error
    return _acquire_with_retry(_language_pack.get_language, grammar)
