"""Fail-closed usage contracts for pr-bundle signing flags."""

from __future__ import annotations

from roam.cli import cli


def test_sign_requires_slsa_l3(cli_runner):
    result = cli_runner.invoke(cli, ["pr-bundle", "emit", "--sign", "--keyless"])
    assert result.exit_code == 2
    assert "--sign requires --slsa-l3" in result.output


def test_sign_requires_exactly_one_method(cli_runner):
    result = cli_runner.invoke(cli, ["pr-bundle", "emit", "--slsa-l3", "--sign"])
    assert result.exit_code == 2
    assert "--key PATH or --keyless" in result.output


def test_keyless_requires_sign(cli_runner):
    result = cli_runner.invoke(cli, ["pr-bundle", "emit", "--slsa-l3", "--keyless"])
    assert result.exit_code == 2
    assert "--key and --keyless require --sign" in result.output


def test_key_and_keyless_are_mutually_exclusive(cli_runner, tmp_path):
    key = tmp_path / "cosign.key"
    key.write_text("test fixture only", encoding="utf-8")
    result = cli_runner.invoke(
        cli,
        ["pr-bundle", "emit", "--slsa-l3", "--sign", "--key", str(key), "--keyless"],
    )
    assert result.exit_code == 2
    assert "exactly one signing method" in result.output
