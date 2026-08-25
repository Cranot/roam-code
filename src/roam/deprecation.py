"""Leaf storage for the active CLI deprecation notice."""

from __future__ import annotations

_ACTIVE_DEPRECATION_NOTICE: str | None = None


def set_active_deprecation_notice(text: str | None) -> None:
    global _ACTIVE_DEPRECATION_NOTICE
    _ACTIVE_DEPRECATION_NOTICE = text


def get_active_deprecation_notice() -> str | None:
    return _ACTIVE_DEPRECATION_NOTICE
