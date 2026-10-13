"""A contract with zero required checks must not read as a completed one.

When no rule matches any changed file the contract is empty because the pack
has no opinion about those paths, not because nothing needed checking. The
command must say so (partial_success true, unmatched files counted) and
``--require-coverage`` must turn that state into exit 5. Exercised through the
real CLI entry point.
"""

from __future__ import annotations

import json

from click.testing import CliRunner

from roam.cli import cli


def _run(*args: str):
    return CliRunner().invoke(cli, ["--json", "verification-contract", *args], catch_exceptions=False)


def _text(*args: str):
    return CliRunner().invoke(cli, ["verification-contract", *args], catch_exceptions=False)


def test_vacuous_contract_is_partial_and_names_unmatched_files():
    r = _run("-f", "a.dat", "-f", "b.sh")
    assert r.exit_code == 0, r.output
    d = json.loads(r.output)
    assert d["summary"]["required_count"] == 0
    assert d["summary"]["partial_success"] is True
    assert d["summary"]["unmatched_count"] == 2
    assert d["contract"]["_meta"]["unmatched_changed_files"] == ["a.dat", "b.sh"]
    assert "matched no rule" in d["summary"]["verdict"]


def test_vacuous_contract_text_verdict_says_no_rule_matched():
    r = _text("-f", "a.dat", "-f", "b.sh")
    assert r.exit_code == 0, r.output
    assert "VERDICT: 0 required; 2 changed file(s) matched no rule" in r.output


def test_require_coverage_exits_5_on_vacuous_contract():
    for runner in (_run, _text):
        r = runner("-f", "a.dat", "-f", "b.sh", "--require-coverage")
        assert r.exit_code == 5, r.output


def test_matched_file_control_is_complete_and_exits_0():
    r = _run("-f", "src/app/api.py")
    assert r.exit_code == 0, r.output
    d = json.loads(r.output)
    assert d["summary"]["required_count"] > 0
    assert d["summary"]["partial_success"] is False
    assert "matched no rule" not in d["summary"]["verdict"]
    t = _text("-f", "src/app/api.py")
    assert t.output.startswith("VERDICT: ") and "matched no rule" not in t.output


def test_some_unmatched_with_required_nonempty_stays_complete():
    """Only an EMPTY contract is vacuous; a mixed change keeps today's shape."""
    r = _run("-f", "src/app/api.py", "-f", "a.dat")
    assert r.exit_code == 0, r.output
    assert json.loads(r.output)["summary"]["partial_success"] is False


def test_require_coverage_does_not_fail_a_non_vacuous_contract():
    for args in (("-f", "src/app/api.py"), ("-f", "src/app/api.py", "-f", "a.dat")):
        assert _run(*args, "--require-coverage").exit_code == 0
        assert _text(*args, "--require-coverage").exit_code == 0
