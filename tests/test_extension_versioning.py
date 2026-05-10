"""Bridges + extractors carry a VERSION class attr.

Audit A6: when a bridge's inference logic changes (Django learning
``through=`` on M2M, REST-API learning OpenAPI 3.1, etc.) the index
built under the old version carries stale or shape-incompatible
edges. The ``VERSION`` class attribute lets manifest writers + drift
checkers detect "this index needs --rebuild after the bridge bump."

Same applies to language extractors: a Python extractor bump that
starts capturing decorator metadata changes the symbol-row shape;
older indexes without that data should be flagged.
"""

from __future__ import annotations

import re

_SEMVER = re.compile(r"^\d+\.\d+\.\d+$")


def test_language_bridge_base_carries_version():
    from roam.bridges.base import LanguageBridge

    assert hasattr(LanguageBridge, "VERSION"), "LanguageBridge must declare a VERSION class attr"
    assert isinstance(LanguageBridge.VERSION, str)
    assert _SEMVER.match(LanguageBridge.VERSION), f"VERSION must be semver, got {LanguageBridge.VERSION!r}"


def test_language_extractor_base_carries_version():
    from roam.languages.base import LanguageExtractor

    assert hasattr(LanguageExtractor, "VERSION"), "LanguageExtractor must declare a VERSION class attr"
    assert isinstance(LanguageExtractor.VERSION, str)
    assert _SEMVER.match(LanguageExtractor.VERSION), f"VERSION must be semver, got {LanguageExtractor.VERSION!r}"


def test_concrete_bridges_inherit_version():
    """Each concrete bridge inherits LanguageBridge.VERSION (or overrides
    it with its own semver). A subclass without a version would be
    impossible — Python class attribute inheritance ensures it.
    """
    from roam.bridges.base import LanguageBridge

    # Discover concrete bridges via auto-discovery so the test stays
    # current as new bridges land.
    try:
        from roam.bridges.registry import _auto_discover

        _auto_discover()
    except Exception:
        pass

    for cls in LanguageBridge.__subclasses__():
        v = getattr(cls, "VERSION", None)
        assert isinstance(v, str), f"{cls.__name__} missing VERSION"
        assert _SEMVER.match(v), f"{cls.__name__}.VERSION not semver: {v!r}"
