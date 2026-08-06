"""``--budget 0`` is documented as unlimited; it used to be the value that CAPPED.

Measured on this repo before the fix (v13.5, `roam --budget 0 taint --json`)::

    summary.truncated          = True
    summary.truncation_reason  = budget
    summary.budget_tokens      = 20000
    summary.findings           = 894      <- what the summary claimed
    len(envelope["findings"])  = 10       <- what the envelope actually carried

while ``roam taint --sarif`` on the same tree emitted all 894. JSON and SARIF
disagreed 88x on one run, and the only escape hatch was the undocumented
``ROAM_DEFAULT_JSON_BUDGET`` environment variable.

ROOT CAUSE was a sentinel collision, not the cap itself. ``--budget`` used
click ``default=0``, so "the user typed 0" and "the user typed nothing" arrived
at :func:`roam.output.formatter._apply_envelope_budget` as the same integer, and
the Pattern-6 default-bounding branch (which genuinely wants to bound
``uses``/``clones``/``path-coverage`` blowouts) swallowed the explicit override.

The fix cannot be "make 0 mean uncapped" at the formatter: 127 call sites read
``ctx.obj.get("budget", 0) if ctx.obj else 0`` and ``json_envelope`` itself
defaults to ``budget=0``, all of them meaning "nothing was asked for, apply the
default cap". Flipping the sentinel there would uncap every one of them and
reintroduce the 56K-224KB envelopes the default cap exists to bound. So
explicitness is carried alongside the value (``ctx.obj["budget_explicit"]``) and
only an explicitly-typed global ``--budget 0`` uncaps.

The tests below pin BOTH directions: the override must work, and the default
cap must be untouched for every caller that did not ask for it.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest
from click.testing import CliRunner

sys.path.insert(0, str(Path(__file__).parent))
from conftest import git_init, index_in_process  # noqa: E402

# Small enough that ANY envelope this repo emits blows through it, so the
# fixture project does not need to be large to exercise the default cap.
TINY_CAP = "50"


@pytest.fixture
def budget_project(tmp_path, monkeypatch):
    """Minimal indexed project whose `health` envelope exceeds TINY_CAP tokens."""
    proj = tmp_path / "repo"
    proj.mkdir()
    (proj / ".gitignore").write_text(".roam/\n")
    src = proj / "src"
    src.mkdir()
    (src / "app.py").write_text("def main():\n    print('hello')\n\ndef helper():\n    return main()\n")
    (src / "utils.py").write_text("def format_name(name):\n    return name.title()\n")
    git_init(proj)
    monkeypatch.chdir(proj)
    out, rc = index_in_process(proj)
    assert rc == 0, f"index failed: {out}"
    return proj


def _run(args, cwd):
    from roam.cli import cli

    old_cwd = os.getcwd()
    try:
        os.chdir(str(cwd))
        return CliRunner().invoke(cli, args, catch_exceptions=False)
    finally:
        os.chdir(old_cwd)


def test_explicit_budget_zero_uncaps(budget_project, monkeypatch):
    """RED before the fix: `--budget 0` was truncated by the default cap."""
    monkeypatch.setenv("ROAM_DEFAULT_JSON_BUDGET", TINY_CAP)
    result = _run(["--json", "--budget", "0", "--detail", "health"], budget_project)
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    summary = data["summary"]
    assert "truncated" not in summary, (
        f"--budget 0 must mean no cap, but the envelope was truncated: budget_tokens={summary.get('budget_tokens')}"
    )
    assert "budget_tokens" not in summary


def test_omitted_budget_still_default_capped(budget_project, monkeypatch):
    """GREEN before AND after: omitting --budget must keep the Pattern-6 cap.

    This is the control that would catch the fix if it had been implemented by
    flipping the meaning of 0 at the formatter.
    """
    monkeypatch.setenv("ROAM_DEFAULT_JSON_BUDGET", TINY_CAP)
    result = _run(["--json", "--detail", "health"], budget_project)
    assert result.exit_code == 0, result.output
    summary = json.loads(result.output)["summary"]
    assert summary["truncated"] is True
    assert summary["truncation_reason"] == "budget"
    assert summary["budget_tokens"] == int(TINY_CAP)


def test_positive_budget_still_truncates(budget_project, monkeypatch):
    """`--budget N` for N > 0 is unchanged by the sentinel split."""
    result = _run(["--json", "--budget", "50", "--detail", "health"], budget_project)
    assert result.exit_code == 0, result.output
    summary = json.loads(result.output)["summary"]
    assert summary["truncated"] is True
    assert summary["budget_tokens"] == 50


def test_agent_mode_budget_zero_keeps_agent_cap(budget_project, monkeypatch):
    """`--agent --budget 0` keeps agent mode's 500-token cap, as it always did.

    Agent mode rewrites a non-positive budget to 500 BEFORE the value reaches
    the context, so the explicit-0 uncap must not fire behind its back.
    """
    result = _run(["--agent", "--budget", "0", "health"], budget_project)
    assert result.exit_code == 0, result.output
    summary = json.loads(result.output)["summary"]
    assert summary.get("budget_tokens") == 500 or "truncated" not in summary
    assert summary.get("budget_tokens") != 0


def test_internal_budget_zero_is_still_default_capped(monkeypatch):
    """A literal ``budget=0`` from a non-CLI caller still gets the default cap.

    ``json_envelope``'s own signature default is ``budget=0`` and 127 command
    call sites fall back to ``0`` when ``ctx.obj`` is absent (MCP server, direct
    function calls). All of them mean "nothing was asked for". With no click
    context active, 0 must NOT uncap.
    """
    from roam.output.formatter import json_envelope

    monkeypatch.setenv("ROAM_DEFAULT_JSON_BUDGET", TINY_CAP)
    out = json_envelope(
        "test",
        summary={"verdict": "ok"},
        budget=0,
        items=[{"name": f"item_{i}", "data": "x" * 200} for i in range(200)],
    )
    assert out["summary"]["truncated"] is True
    assert out["summary"]["budget_tokens"] == int(TINY_CAP)
    # At a 50-token cap the payload key is dropped outright, not merely capped.
    assert len(out.get("items", [])) < 200


def test_truncated_envelope_discloses_what_it_emitted():
    """A truncated envelope must not report a count it did not emit.

    Pre-fix, `roam --budget 0 taint --json` shipped `summary.findings: 894`
    beside a 10-item `findings` list with nothing tying the two together. An
    agent reading the summary count off a truncated payload is the expensive
    silent wrongness here.
    """
    from roam.output.formatter import budget_truncate_json

    data = {
        "command": "taint",
        "summary": {"verdict": "894 findings", "findings": 894},
        "findings": [{"rule": f"r{i}", "msg": "x" * 200} for i in range(894)],
        "rule_ids": [f"rule-{i}" for i in range(22)],
    }
    out = budget_truncate_json(data, 200)

    assert out["summary"]["truncated"] is True
    emitted = out["summary"]["emitted_counts"]
    assert emitted["findings"] == len(out.get("findings", []))
    assert emitted["findings"] < 894


def test_emitted_counts_reports_zero_for_dropped_keys():
    """A payload key dropped ENTIRELY must be disclosed as 0, not go missing.

    ``_drop_fields_to_budget`` deletes the key, so a consumer that only checks
    for its presence sees "no findings" where the truth is "findings withheld".
    """
    from roam.output.formatter import budget_truncate_json

    data = {
        "command": "taint",
        "summary": {"verdict": "ok", "findings": 50},
        "findings": [{"blob": "x" * 4000} for _ in range(50)],
    }
    out = budget_truncate_json(data, 20)

    assert "findings" not in out
    assert out["summary"]["emitted_counts"]["findings"] == 0


def test_no_emitted_counts_when_nothing_was_lost():
    """Envelopes that fit are byte-identical — no new key appears."""
    from roam.output.formatter import budget_truncate_json

    data = {"command": "test", "summary": {"verdict": "ok"}, "items": [1, 2, 3]}
    assert budget_truncate_json(data, 10000) == data
