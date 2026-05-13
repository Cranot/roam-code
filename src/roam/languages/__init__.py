"""Language detection, grammar loading, and symbol extraction."""

from .base import LanguageExtractor
from .registry import (
    JS_FAMILY_LANGUAGES,
    get_extractor,
    get_extractor_for_file,
    get_language_for_file,
    get_supported_extensions,
    get_supported_languages,
    get_ts_language,
)

__all__ = [
    "JS_FAMILY_LANGUAGES",
    "LanguageExtractor",
    "get_extractor",
    "get_extractor_for_file",
    "get_language_for_file",
    "get_supported_extensions",
    "get_supported_languages",
    "get_ts_language",
]
