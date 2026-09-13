"""Bound reader allocations even when the file changes after its size check."""

from __future__ import annotations

import io

import pytest

from roam.commands import cmd_stale_refs as mod


def test_growth_after_stat_stays_unanalyzable(tmp_path, monkeypatch):
    path = tmp_path / "growing.md"
    path.write_bytes(b"small")
    real_open = open

    def grow_then_open(target, *args, **kwargs):
        # Intentional fixture-local I/O deterministically simulates the race;
        # no scheduler, sleep, or concurrent writer is needed.
        path.write_bytes(b"x" * 2048)
        return real_open(target, *args, **kwargs)

    monkeypatch.setattr(mod, "open", grow_then_open, raising=False)
    content, reason = mod._read_text_with_reason(path, max_bytes=1024)
    assert content is None
    assert "oversize" in reason


def test_read_requests_only_cap_plus_sentinel(tmp_path, monkeypatch):
    path = tmp_path / "small.md"
    path.write_bytes(b"ok")
    requested = []

    class ObservedBytes(io.BytesIO):
        def read(self, size=-1):
            requested.append(size)
            return super().read(size)

    class ObservedText(io.StringIO):
        def read(self, size=-1):
            requested.append(size)
            return super().read(size)

    def observed_open(target, mode="r", **kwargs):
        return ObservedBytes(b"ok") if "b" in mode else ObservedText("ok")

    monkeypatch.setattr(mod, "open", observed_open, raising=False)
    assert mod._read_text_with_reason(path, max_bytes=10) == ("ok", None)
    assert requested == [11]


@pytest.mark.parametrize(
    ("raw", "limit", "expected"),
    [
        (b"", 0, ""),
        (b"abc", 3, "abc"),
        ("é".encode(), 2, "é"),
        (b"a\r\nb\rc\n", 7, "a\nb\nc\n"),
        (b"\xff", 1, "\ufffd"),
    ],
)
def test_bounded_reader_preserves_text_semantics(tmp_path, raw, limit, expected):
    # Real fixture bytes exercise byte limits, UTF-8 replacement and universal
    # newlines; no external file or environment state participates.
    path = tmp_path / "text.md"
    path.write_bytes(raw)
    assert mod._read_text_with_reason(path, max_bytes=limit) == (expected, None)
