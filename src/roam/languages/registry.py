"""Language detection, grammar loading, and extractor registry."""

from __future__ import annotations

import logging
import os
from functools import lru_cache
from typing import TYPE_CHECKING

log = logging.getLogger(__name__)

from roam.index.parser import EXTENSION_MAP, GRAMMAR_ALIASES, REGEX_ONLY_LANGUAGES

if TYPE_CHECKING:
    from collections.abc import Callable

    from .base import LanguageExtractor

# Single source of truth for extension → language is parser.EXTENSION_MAP.
# _EXTENSION_MAP is kept as a module-level name for backward compatibility with
# any internal code that references it directly, but it is now an alias.
_EXTENSION_MAP: dict[str, str] = EXTENSION_MAP

# JavaScript / TypeScript family — the set of ``files.language`` values whose
# import graph should be treated as a single TypeScript-like ecosystem.
# Vue / Svelte single-file components are stored with ``files.language='vue'``
# / ``'svelte'`` in the DB, but their ``<script>`` blocks are extracted via
# the TS/JS extractor and DO produce real symbol edges into ``.ts`` modules.
# Any SQL query or filter that wants "all files that participate in the TS
# import graph" must include vue / svelte or it will silently drop those
# edges. Historic bug (W6.3 → orphan-imports / verify-imports, W19.x →
# dead / vibe-check): 23 of 89 dead findings on a real Vue/TS codebase
# (~26%) were TS exports actually consumed by .vue files but invisible
# to the analyser.
# A single canonical tuple is the prophylactic — use it instead of
# hard-coding language lists in SQL.
JS_FAMILY_LANGUAGES: tuple[str, ...] = (
    "javascript",
    "typescript",
    "tsx",
    "jsx",
    "vue",
    "svelte",
)

# Languages with dedicated extractors
_DEDICATED_EXTRACTORS = frozenset(
    {
        "python",
        "javascript",
        "typescript",
        "tsx",
        "go",
        "rust",
        "java",
        "c",
        "cpp",
        "php",
        "c_sharp",
        "ruby",
        "kotlin",
        "swift",
        "scala",
        "sql",
        "dart",
    }
)

# 28 language identifiers that roam can extract symbols from. Closed enumeration,
# pinned to the installed tree-sitter-language-pack (>=0.6, see pyproject.toml).
# Composition: 19 native tree-sitter grammars (python..svelte), 4 aliased SF
# variants (apex/sfxml/aura/visualforce → java/html), 3 regex-only languages
# (foxpro/yaml/hcl mirrored in parser.REGEX_ONLY_LANGUAGES), 2 aliased
# JSON/Markdown variants (jsonc/mdx). Adding a language requires a *_lang.py
# extractor under src/roam/languages/ AND a grammar in the language-pack.
_SUPPORTED_LANGUAGES = frozenset(
    {
        "python",
        "javascript",
        "typescript",
        "tsx",
        "go",
        "rust",
        "java",
        "c",
        "cpp",
        "ruby",
        "php",
        "c_sharp",
        "kotlin",
        "swift",
        "scala",
        "sql",
        "dart",
        "vue",
        "svelte",
        # Aliased languages (parsed via grammar aliases)
        "apex",
        "sfxml",
        "aura",
        "visualforce",
        # Regex-only languages (no tree-sitter grammar)
        "foxpro",
        "yaml",
        "hcl",
        # Aliased variants
        "jsonc",
        "mdx",
    }
)


def _plugin_language_extractors():
    try:
        from roam.plugins import get_plugin_language_extractors

        return get_plugin_language_extractors()
    except ImportError:
        return {}
    except Exception:
        log.debug("plugin language extractor discovery failed", exc_info=True)
        return {}


def _plugin_language_extensions():
    try:
        from roam.plugins import get_plugin_language_extensions

        return get_plugin_language_extensions()
    except ImportError:
        return {}
    except Exception:
        log.debug("plugin language extension discovery failed", exc_info=True)
        return {}


def _plugin_language_grammar_aliases():
    try:
        from roam.plugins import get_plugin_language_grammar_aliases

        return get_plugin_language_grammar_aliases()
    except ImportError:
        return {}


def _is_supported_language(language: str) -> bool:
    return language in _SUPPORTED_LANGUAGES or language in _plugin_language_extractors()


def get_language_for_file(path: str) -> str | None:
    """Determine the language for a file based on its extension.

    Returns the language name string, or None if unsupported.
    """
    # Salesforce metadata files: *.cls-meta.xml, *.object-meta.xml, etc.
    if path.endswith("-meta.xml"):
        return "sfxml"
    _, ext = os.path.splitext(path)
    ext = ext.lower()
    language = _EXTENSION_MAP.get(ext)
    if language:
        return language
    return _plugin_language_extensions().get(ext)


def get_ts_language(language: str):
    """Get a tree-sitter Language object from tree_sitter_language_pack.

    Args:
        language: Language name (e.g. 'python', 'javascript', 'c_sharp')

    Returns:
        A tree-sitter Language object ready for use with Parser.

    Raises:
        ValueError: If the language is not supported.
        ImportError: If tree_sitter_language_pack is not installed.
    """
    if not _is_supported_language(language):
        raise ValueError(f"Unsupported language: {language}")

    # Regex-only languages have no tree-sitter grammar
    if language in REGEX_ONLY_LANGUAGES:
        raise ValueError(f"Language {language} is regex-only (no tree-sitter grammar)")

    # Resolve grammar alias (e.g. apex → java)
    plugin_alias = _plugin_language_grammar_aliases().get(language)
    grammar = plugin_alias or GRAMMAR_ALIASES.get(language, language)

    from tree_sitter_language_pack import get_language

    return get_language(grammar)


# W646 -- dispatch table replacing a 23-arm if/elif chain on `language`.
# Each entry is a zero-arg thunk so per-language extractor modules stay lazily
# imported (the same property the old chain preserved by importing inside each
# arm). The TS family (typescript/tsx/vue/svelte) shares one class, hence the
# repeated thunk. Adding a language = add a thunk here AND add the name to
# _SUPPORTED_LANGUAGES above; the drift-guard test in tests/test_languages.py
# fails on a one-sided edit.


def _make_python() -> "LanguageExtractor":
    from .python_lang import PythonExtractor

    return PythonExtractor()


def _make_javascript() -> "LanguageExtractor":
    from .javascript_lang import JavaScriptExtractor

    return JavaScriptExtractor()


def _make_typescript() -> "LanguageExtractor":
    from .typescript_lang import TypeScriptExtractor

    return TypeScriptExtractor()


def _make_go() -> "LanguageExtractor":
    from .go_lang import GoExtractor

    return GoExtractor()


def _make_rust() -> "LanguageExtractor":
    from .rust_lang import RustExtractor

    return RustExtractor()


def _make_java() -> "LanguageExtractor":
    from .java_lang import JavaExtractor

    return JavaExtractor()


def _make_c() -> "LanguageExtractor":
    from .c_lang import CExtractor

    return CExtractor()


def _make_cpp() -> "LanguageExtractor":
    from .c_lang import CppExtractor

    return CppExtractor()


def _make_php() -> "LanguageExtractor":
    from .php_lang import PhpExtractor

    return PhpExtractor()


def _make_csharp() -> "LanguageExtractor":
    from .csharp_lang import CSharpExtractor

    return CSharpExtractor()


def _make_ruby() -> "LanguageExtractor":
    from .ruby_lang import RubyExtractor

    return RubyExtractor()


def _make_kotlin() -> "LanguageExtractor":
    from .kotlin_lang import KotlinExtractor

    return KotlinExtractor()


def _make_swift() -> "LanguageExtractor":
    from .swift_lang import SwiftExtractor

    return SwiftExtractor()


def _make_scala() -> "LanguageExtractor":
    from .scala_lang import ScalaExtractor

    return ScalaExtractor()


def _make_sql() -> "LanguageExtractor":
    from .sql_lang import SqlExtractor

    return SqlExtractor()


def _make_dart() -> "LanguageExtractor":
    from .dart_lang import DartExtractor

    return DartExtractor()


def _make_apex() -> "LanguageExtractor":
    from .apex_lang import ApexExtractor

    return ApexExtractor()


def _make_sfxml() -> "LanguageExtractor":
    from .sfxml_lang import SfxmlExtractor

    return SfxmlExtractor()


def _make_aura() -> "LanguageExtractor":
    from .aura_lang import AuraExtractor

    return AuraExtractor()


def _make_visualforce() -> "LanguageExtractor":
    from .visualforce_lang import VisualforceExtractor

    return VisualforceExtractor()


def _make_foxpro() -> "LanguageExtractor":
    from .foxpro_lang import FoxProExtractor

    return FoxProExtractor()


def _make_yaml() -> "LanguageExtractor":
    from .yaml_lang import YamlExtractor

    return YamlExtractor()


def _make_hcl() -> "LanguageExtractor":
    from .hcl_lang import HclExtractor

    return HclExtractor()


_LANGUAGE_EXTRACTORS: "dict[str, Callable[[], LanguageExtractor]]" = {
    "python": _make_python,
    "javascript": _make_javascript,
    "typescript": _make_typescript,
    "tsx": _make_typescript,
    "vue": _make_typescript,
    "svelte": _make_typescript,
    "go": _make_go,
    "rust": _make_rust,
    "java": _make_java,
    "c": _make_c,
    "cpp": _make_cpp,
    "php": _make_php,
    "c_sharp": _make_csharp,
    "ruby": _make_ruby,
    "kotlin": _make_kotlin,
    "swift": _make_swift,
    "scala": _make_scala,
    "sql": _make_sql,
    "dart": _make_dart,
    # Salesforce extractors
    "apex": _make_apex,
    "sfxml": _make_sfxml,
    "aura": _make_aura,
    "visualforce": _make_visualforce,
    # Regex-only extractors
    "foxpro": _make_foxpro,
    "yaml": _make_yaml,
    "hcl": _make_hcl,
}


@lru_cache(maxsize=None)
def _create_extractor(language: str) -> "LanguageExtractor":
    """Create and cache an extractor instance for a language."""
    factory = _LANGUAGE_EXTRACTORS.get(language)
    if factory is not None:
        return factory()
    plugin_factory = _plugin_language_extractors().get(language)
    if plugin_factory is not None:
        return plugin_factory()
    # For aliased languages, delegate to the alias target's extractor
    alias_target = GRAMMAR_ALIASES.get(language)
    if alias_target and alias_target in _DEDICATED_EXTRACTORS:
        return _create_extractor(alias_target)
    # Use generic extractor for tier-2 languages
    from .generic_lang import GenericExtractor

    return GenericExtractor(language=language)


def get_extractor(language: str) -> "LanguageExtractor":
    """Get an extractor instance for a language.

    Returns a dedicated extractor for tier-1 languages, or a GenericExtractor
    for tier-2 languages.

    Args:
        language: Language name string.

    Returns:
        A LanguageExtractor instance.

    Raises:
        ValueError: If the language is not supported.
    """
    if not _is_supported_language(language):
        raise ValueError(f"Unsupported language: {language}")
    return _create_extractor(language)


def get_extractor_for_file(path: str) -> "LanguageExtractor | None":
    """Get an extractor instance for a file based on its extension.

    Returns None if the file type is not supported.
    """
    language = get_language_for_file(path)
    if language is None:
        return None
    return _create_extractor(language)


def get_supported_extensions() -> list[str]:
    """Return all supported file extensions."""
    return sorted(set(_EXTENSION_MAP.keys()) | set(_plugin_language_extensions().keys()))


def get_supported_languages() -> list[str]:
    """Return all supported language names."""
    return sorted(set(_SUPPORTED_LANGUAGES) | set(_plugin_language_extractors().keys()))
