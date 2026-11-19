"""Regression coverage for numeric npm wildcard versions."""

import json

import pytest

from roam.commands.cmd_supply_chain import _parse_package_json


@pytest.mark.parametrize("spec", ["1.x", "2.*", "1.2.x", "1.X", "1.2.*", "=1.2.x"])
def test_npm_numeric_wildcards_are_not_exact_pins(tmp_path, spec):
    manifest = tmp_path / "package.json"
    manifest.write_text(json.dumps({"dependencies": {"example": spec}}), encoding="utf-8")

    deps = _parse_package_json(manifest, "package.json")

    assert len(deps) == 1
    dep = deps[0]
    assert dep.pin_status in {"unpinned", "range"}
    assert dep.risk_level in {"high", "medium"}


@pytest.mark.parametrize(
    ("spec", "status", "risk"),
    [("1.2.3", "exact", "low"), ("^1.2.3", "range", "medium")],
)
def test_npm_pin_controls(tmp_path, spec, status, risk):
    """Controls: exact releases and caret ranges already classify correctly."""
    manifest = tmp_path / "package.json"
    manifest.write_text(json.dumps({"dependencies": {"example": spec}}), encoding="utf-8")

    deps = _parse_package_json(manifest, "package.json")

    assert len(deps) == 1
    assert deps[0].pin_status == status
    assert deps[0].risk_level == risk


@pytest.mark.parametrize("spec", ["1.x || 2.0.0", "1.* || 2.0.0", "1.2.x || 3.x", "1.2.3 || 2.0.0", "1.2.3 - 2.0.0"])
def test_compound_npm_ranges_are_not_exact_pins(tmp_path, spec):
    manifest = tmp_path / "package.json"
    manifest.write_text(json.dumps({"dependencies": {"example": spec}}))
    [dep] = _parse_package_json(manifest, "package.json")
    assert dep.pin_status in {"unpinned", "range"}
    assert dep.risk_level in {"high", "medium"}


@pytest.mark.parametrize("spec", ["1", "1.2", "=1", "=1.2"])
def test_partial_versions_are_ranges(spec):
    from roam.commands.cmd_supply_chain import _pin_status_npm

    assert _pin_status_npm(spec) == "range"


@pytest.mark.parametrize("spec", ["1.2.3-beta.x", "=1.2.3-beta.x+build.7", "1.2.3+build.x"])
def test_full_prerelease_and_build_are_exact(spec):
    from roam.commands.cmd_supply_chain import _pin_status_npm

    assert _pin_status_npm(spec) == "exact"
