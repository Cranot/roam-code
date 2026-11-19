"""Regression coverage for status braces before harvested JSON envelopes."""

import json

import click
import pytest

import roam.cli
from roam import agent_opt


@pytest.fixture
def command_output(monkeypatch, tmp_path):
    """Run an in-process command without accessing the repository or user home."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))

    def install(output, exit_code=0):
        @click.command()
        @click.option("--json", "as_json", is_flag=True)
        def command(as_json):
            click.echo(output)
            if exit_code:
                raise click.exceptions.Exit(exit_code)

        monkeypatch.setattr(roam.cli, "cli", command)

    return install


@pytest.mark.parametrize("prefix", ["", "Indexing repository\n", "Indexing {repo}\nScanning {files}\n"])
def test_harvest_recovers_envelope_after_status_output(command_output, prefix):
    envelope = {"summary": {"verdict": "ok"}, "items": []}
    command_output(prefix + json.dumps(envelope))

    envelopes, failures = agent_opt.harvest_command_envelopes([("sample", [])])

    assert envelopes == [("sample", envelope)]
    assert failures == []


@pytest.mark.parametrize("output,exit_code", [("Indexing {repo}\n{broken", 0), ('{"summary": {}}', 2)])
def test_harvest_still_reports_invalid_or_failed_commands(command_output, output, exit_code):
    command_output(output, exit_code)

    envelopes, failures = agent_opt.harvest_command_envelopes([("sample", [])])

    assert envelopes == []
    assert len(failures) == 1
    assert failures[0][0] == "sample"
    assert failures[0][1]


def test_invalid_json_recovery_is_in_place_and_bounded(command_output, monkeypatch):
    payload = "{status} " * 40000 + "{broken"
    command_output(payload)
    original = json.JSONDecoder.raw_decode
    attempts = []

    def decode(self, text, idx=0):
        attempts.append((len(text), idx))
        return original(self, text, idx)

    monkeypatch.setattr(json.JSONDecoder, "raw_decode", decode)
    data, error = agent_opt._invoke_json([])
    assert data is None and error
    assert 0 < len(attempts) <= 64
    assert all(length >= len(payload) for length, _ in attempts)
