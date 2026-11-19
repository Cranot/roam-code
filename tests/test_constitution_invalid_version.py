"""Invalid constitution versions must degrade without escaping the loader."""

import pytest

from roam.constitution.loader import Constitution, load_constitution


@pytest.mark.parametrize("version", ["v2", "latest"])
def test_nonnumeric_version_returns_diagnostic_constitution(tmp_path, monkeypatch, version):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    config_dir = tmp_path / ".roam"
    config_dir.mkdir()
    (config_dir / "constitution.yml").write_text(f"version: {version}\n", encoding="utf-8")

    constitution = load_constitution(tmp_path)

    assert isinstance(constitution, Constitution)
    # No metadata was supplied: the degraded result must disclose the problem.
    assert constitution.metadata


@pytest.mark.parametrize("version_line, expected", [("version: 2\n", 2), ("", 1)])
def test_valid_or_omitted_version_still_loads(tmp_path, monkeypatch, version_line, expected):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    config_dir = tmp_path / ".roam"
    config_dir.mkdir()
    (config_dir / "constitution.yml").write_text(version_line + "policy:\n  max_cycles: 3\n", encoding="utf-8")

    constitution = load_constitution(tmp_path)

    assert isinstance(constitution, Constitution)
    assert constitution.version == expected
    assert constitution.policy == {"max_cycles": 3}


def test_invalid_version_discloses_mode_policy_fallback(tmp_path, monkeypatch, capsys):
    from roam.modes.policy import _materialise_from_constitution

    monkeypatch.setenv("HOME", str(tmp_path))
    config = tmp_path / ".roam"
    config.mkdir()
    (config / "constitution.yml").write_text("version: v2\nmodes:\n  read_only: [search]\n")
    assert _materialise_from_constitution(tmp_path) is None
    assert "[modes.policy] constitution policy unavailable:" in capsys.readouterr().err
