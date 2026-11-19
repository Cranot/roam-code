"""Regression coverage for inclusive activity-trail date bounds."""

import pytest


@pytest.fixture(autouse=True)
def isolated_home(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))


@pytest.mark.parametrize("since", [None, "2026-01-31"])
def test_date_only_until_includes_entire_day(since):
    from roam.commands.cmd_audit_trail_export import _filter_records

    timestamps = [
        "2026-01-30T23:59:59Z",
        "2026-01-31T00:00:00Z",
        "2026-01-31T10:00:00Z",
        "2026-01-31T23:59:59.999999Z",
        "2026-02-01T00:00:00Z",
    ]
    records = [{"timestamp": timestamp} for timestamp in timestamps]
    result = _filter_records(records, since=since, until="2026-01-31", verdict_filter=None)
    expected = records[:4] if since is None else records[1:4]
    assert result == expected


def test_timestamp_until_keeps_its_exact_inclusive_cutoff():
    from roam.commands.cmd_audit_trail_export import _filter_records

    records = [
        {"timestamp": "2026-01-30T23:59:59Z"},
        {"timestamp": "2026-01-31T09:00:00Z"},
        {"timestamp": "2026-01-31T10:00:00Z"},
        {"timestamp": "2026-01-31T11:00:00Z"},
    ]
    assert (
        _filter_records(
            records,
            since="2026-01-31",
            until="2026-01-31T10:00:00Z",
            verdict_filter=None,
        )
        == records[1:3]
    )
