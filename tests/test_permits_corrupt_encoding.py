"""Unreadable permit contents must be dropped without escaping exceptions."""

import pytest

from roam.permits.store import read_permit


@pytest.mark.parametrize("contents", [b"\xff", b'{"scope":'])
@pytest.mark.parametrize("collect_warnings", [True, False])
def test_read_permit_corrupt_contents(tmp_path, contents, collect_warnings):
    permit_id = "permit_20261104_abcdef"
    path = tmp_path / ".roam" / "permits" / f"{permit_id}.json"
    path.parent.mkdir(parents=True)
    path.write_bytes(contents)
    warnings = [] if collect_warnings else None

    assert read_permit(tmp_path, permit_id, warnings_out=warnings) is None

    if collect_warnings:
        assert len(warnings) == 1
        assert warnings[0].startswith("permit_corrupt:")
