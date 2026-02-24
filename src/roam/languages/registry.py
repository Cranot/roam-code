"""Language detection, grammar loading, and extractor registry."""

from __future__ import annotations

import os
from functools import lru_cache
from typing import TYPE_CHECKING

from roam.index.parser import EXTENSION_MAP, GRAMMAR_ALIASES, REGEX_ONLY_LANGUAGES

if TYPE_CHECKING:
    from .base import LanguageExtractor

# Single source of truth for extension → language is parser.EXTENSION_MAP.
# _EXTENSION_MAP is kept as a module-level name for backward compatibility with
# any internal code that references it directly, but it is now an alias.
_EXTENSION_MAP: dict[str, str] = EXTENSION_MAP

# Languages with dedicated extractors
_DEDICATED_EXTRACTORS = frozenset({
    "python", "javascript", "typescript", "tsx",
    "go", "rust", "java", "c", "cpp", "php",
    "c_sharp", "ruby", "kotlin", "swift",
})

# All supported tree-sitter language names (includes aliased languages)
_SUPPORTED_LANGUAGES = frozenset({
    "python", "javascript", "typescript", "tsx",
    "go", "rust", "java", "c", "cpp",
    "ruby", "php", "c_sharp", "kotlin", "swift", "scala",
    "vue", "svelte",
    # Aliased languages (parsed via grammar aliases)
    "apex", "sfxml", "aura", "visualforce",
    # Regex-only languages (no tree-sitter grammar)
    "foxpro",
    "yaml",
    "hcl",
    # Aliased variants
    "jsonc",
    "mdx",
})


def _plugin_language_extractors():
    try:
        from roam.plugins import get_plugin_language_extractors

        return get_plugin_language_extractors()
    except Exception:
        return {}


def _plugin_language_extensions():
    try:
        from roam.plugins import get_plugin_language_extensions

        return get_plugin_language_extensions()
    except Exception:
        return {}


def _plugin_language_grammar_aliases():
    try:
        from roam.plugins import get_plugin_language_grammar_aliases

        return get_plugin_language_grammar_aliases()
    except Exception:
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


@lru_cache(maxsize=None)
def _create_extractor(language: str) -> "LanguageExtractor":
    """Create and cache an extractor instance for a language."""
    if language == "python":
        from .python_lang import PythonExtractor
        return PythonExtractor()
    elif language == "javascript":
        from .javascript_lang import JavaScriptExtractor
        return JavaScriptExtractor()
    elif language in ("typescript", "tsx", "vue", "svelte"):
        from .typescript_lang import TypeScriptExtractor
        return TypeScriptExtractor()
    elif language == "go":
        from .go_lang import GoExtractor
        return GoExtractor()
    elif language == "rust":
        from .rust_lang import RustExtractor
        return RustExtractor()
    elif language == "java":
        from .java_lang import JavaExtractor
        return JavaExtractor()
    elif language == "c":
        from .c_lang import CExtractor
        return CExtractor()
    elif language == "cpp":
        from .c_lang import CppExtractor
        return CppExtractor()
    elif language == "php":
        from .php_lang import PhpExtractor
        return PhpExtractor()
    elif language == "c_sharp":
        from .csharp_lang import CSharpExtractor
        return CSharpExtractor()
    elif language == "ruby":
        from .ruby_lang import RubyExtractor
        return RubyExtractor()
    elif language == "kotlin":
        from .kotlin_lang import KotlinExtractor
        return KotlinExtractor()
    elif language == "swift":
        from .swift_lang import SwiftExtractor
        return SwiftExtractor()
    # Salesforce extractors
    elif language == "apex":
        from .apex_lang import ApexExtractor
        return ApexExtractor()
    elif language == "sfxml":
        from .sfxml_lang import SfxmlExtractor
        return SfxmlExtractor()
    elif language == "aura":
        from .aura_lang import AuraExtractor
        return AuraExtractor()
    elif language == "visualforce":
        from .visualforce_lang import VisualforceExtractor
        return VisualforceExtractor()
    elif language == "foxpro":
        from .foxpro_lang import FoxProExtractor
        return FoxProExtractor()
    elif language == "yaml":
        from .yaml_lang import YamlExtractor
        return YamlExtractor()
    elif language == "hcl":
        from .hcl_lang import HclExtractor
        return HclExtractor()
    else:
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
    for tier-2 languages (e.g. Scala).

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
