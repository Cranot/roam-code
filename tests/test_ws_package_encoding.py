"""Role detection must tolerate package metadata that cannot be decoded."""

import pytest

from roam.commands.cmd_ws import _detect_role


@pytest.mark.parametrize("has_backend", [False, True])
def test_invalid_utf8_package_falls_through(tmp_path, has_backend):
    (tmp_path / "package.json").write_bytes(b'{"name": "\xff"}')
    if has_backend:
        (tmp_path / "go.mod").write_text("module example.test/service\n", encoding="utf-8")

    assert _detect_role(tmp_path) == ("backend" if has_backend else "")


def test_invalid_json_package_falls_through_to_backend(tmp_path):
    (tmp_path / "package.json").write_text("{", encoding="utf-8")
    (tmp_path / "go.mod").write_text("module example.test/service\n", encoding="utf-8")

    assert _detect_role(tmp_path) == "backend"


def test_valid_frontend_package_keeps_priority(tmp_path):
    (tmp_path / "package.json").write_text('{"dependencies": {"react": "*"}}', encoding="utf-8")
    (tmp_path / "go.mod").write_text("module example.test/service\n", encoding="utf-8")

    assert _detect_role(tmp_path) == "frontend"
