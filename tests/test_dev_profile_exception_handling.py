"""Regression tests for dev-profile exception boundaries."""

from __future__ import annotations

import pytest

from roam.commands import cmd_dev_profile


def test_parse_iso8601_propagates_unexpected_datetime_errors(monkeypatch):
    class BrokenDateTime:
        @staticmethod
        def fromisoformat(_value):
            raise RuntimeError("parser unavailable")

    monkeypatch.setattr(cmd_dev_profile, "datetime", BrokenDateTime)

    with pytest.raises(RuntimeError, match="parser unavailable"):
        cmd_dev_profile._parse_iso8601("2024-01-01T00:00:00+00:00")
