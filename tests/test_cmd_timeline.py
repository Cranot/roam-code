"""Tests for ``roam timeline`` helpers."""

from __future__ import annotations

import pytest

from roam.commands import cmd_timeline


def test_fmt_ts_handles_expected_timestamp_failures() -> None:
    assert cmd_timeline._fmt_ts(None) == "?"
    assert cmd_timeline._fmt_ts(object()) == "?"
    assert cmd_timeline._fmt_ts("not-a-timestamp") == "?"
    assert cmd_timeline._fmt_ts(10**100) == "?"


def test_fmt_ts_propagates_unexpected_runtime_failures(monkeypatch: pytest.MonkeyPatch) -> None:
    class BrokenDatetime:
        @staticmethod
        def fromtimestamp(_ts: int) -> object:
            raise RuntimeError("unexpected formatter failure")

    monkeypatch.setattr(cmd_timeline, "datetime", BrokenDatetime)

    with pytest.raises(RuntimeError, match="unexpected formatter failure"):
        cmd_timeline._fmt_ts(123)
