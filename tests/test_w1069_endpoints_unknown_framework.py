"""W1069 — ``cmd_endpoints --framework`` unknown-value disclosure.

Sibling of W1063 (cmd_findings unknown-detector disclosure), W1064 (difflib
closest-match), and W1068 (cmd_search unknown-kind disclosure). The bug
being fixed: ``roam endpoints --framework garbage`` previously returned a
generic ``"no endpoints detected matching the given filters"`` envelope —
indistinguishable from "valid framework substring, 0 hits because of
``--method``". Pattern-1D silent-success on degraded filter resolution.

Four scenarios pinned here:

1. No ``--framework`` flag → byte-identical to pre-W1069 (no
   ``unknown_framework_filter`` state, no ``requested_framework`` field).
2. ``--framework`` substring matches some endpoints → normal result, no
   ``unknown_framework_filter`` state.
3. ``--framework`` substring is a substring of an observed framework label
   but 0 endpoints survive (e.g. paired with a ``--method`` filter that
   narrows to zero) → normal "no endpoints" path, NOT
   ``unknown_framework_filter``.
4. ``--framework`` substring is NOT a substring of any observed framework
   → ``state="unknown_framework_filter"``, ``partial_success=True``,
   ``requested_framework`` echoed, ``observed_frameworks`` enumerated,
   ``agent_contract.facts`` anchored on ``frameworks`` (LAW 4), and a
   difflib closest-match suggestion when within cutoff 0.6.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest
from click.testing import CliRunner

sys.path.insert(0, str(Path(__file__).parent))
from conftest import git_init, index_in_process

# Minimal Flask + Express corpus — enough to populate the
# observed_frameworks superset with at least two distinct framework labels
# so the "substring matches some" and "substring matches none" branches
# are both exercisable.
_FLASK_APP = """\
from flask import Flask

app = Flask(__name__)


@app.route('/api/users', methods=['GET'])
def get_users():
    return []


@app.route('/api/users', methods=['POST'])
def create_user():
    return {}, 201
"""

_EXPRESS_APP = """\
const express = require('express');
const app = express();

app.get('/api/products', getProducts);
app.post('/api/products', createProduct);
"""


@pytest.fixture
def cli_runner():
    return CliRunner()


@pytest.fixture
def project(tmp_path):
    proj = tmp_path / "w1069_project"
    proj.mkdir()
    (proj / ".gitignore").write_text(".roam/\n")
    (proj / "app.py").write_text(_FLASK_APP, encoding="utf-8")
    (proj / "server.js").write_text(_EXPRESS_APP, encoding="utf-8")
    git_init(str(proj))

    old_cwd = os.getcwd()
    try:
        os.chdir(str(proj))
        out, rc = index_in_process(str(proj))
        assert rc in (0, 1), f"index failed: {out}"
    finally:
        os.chdir(old_cwd)
    return proj


def _invoke(project, *args, json_mode=False):
    from roam.cli import cli

    runner = CliRunner()
    full_args: list[str] = []
    if json_mode:
        full_args.append("--json")
    full_args.append("endpoints")
    full_args.extend(args)
    old_cwd = os.getcwd()
    try:
        os.chdir(str(project))
        return runner.invoke(cli, full_args, catch_exceptions=False)
    finally:
        os.chdir(old_cwd)


# ---------------------------------------------------------------------------
# Scenario 1: no --framework flag → byte-identical (no unknown state leak).
# ---------------------------------------------------------------------------


def test_no_framework_flag_does_not_emit_unknown_state(project):
    """Without ``--framework``, the W1069 disclosure must be a no-op.
    No ``unknown_framework_filter`` state, no ``requested_framework`` /
    ``observed_frameworks`` keys."""
    result = _invoke(project, json_mode=True)
    assert result.exit_code == 0
    data = json.loads(result.output)
    summary = data["summary"]
    assert summary.get("state") != "unknown_framework_filter"
    assert "requested_framework" not in summary
    assert "observed_frameworks" not in summary
    # The normal verdict shape continues to surface a count + framework
    # tally — Pattern 2 always-emit discipline.
    assert summary.get("count", 0) > 0
    assert "found" in summary["verdict"].lower()


def test_no_framework_text_mode_unchanged(project):
    """Text mode without ``--framework`` is the standard endpoints output —
    no `unknown framework` chatter, no `Observed frameworks:` line."""
    result = _invoke(project)
    assert result.exit_code == 0
    out = result.output
    assert "unknown framework" not in out.lower()
    assert "Observed frameworks:" not in out


# ---------------------------------------------------------------------------
# Scenario 2: --framework substring matches some endpoints → normal result.
# ---------------------------------------------------------------------------


def test_framework_filter_matches_some(project):
    """``--framework flask`` matches the Flask file and emits a normal
    result envelope. No ``unknown_framework_filter`` state."""
    result = _invoke(project, "--framework", "flask", json_mode=True)
    assert result.exit_code == 0
    data = json.loads(result.output)
    summary = data["summary"]
    assert summary.get("state") != "unknown_framework_filter"
    # At least one matching endpoint survives.
    assert summary.get("count", 0) >= 1
    frameworks = summary.get("frameworks", [])
    assert any("flask" in fw.lower() or "fastapi" in fw.lower() or "python" in fw.lower() for fw in frameworks)


# ---------------------------------------------------------------------------
# Scenario 3: substring IS in observed set but 0 endpoints survive (because
# of --method narrowing) → normal "no endpoints" path, NOT unknown.
# ---------------------------------------------------------------------------


def test_known_framework_zero_results_is_not_unknown(project):
    """``--framework flask --method PATCH`` — Flask matches the corpus.
    This must NOT trip the unknown_framework_filter disclosure.

    Post-W1075 note: this scenario now legitimately trips the SIBLING
    ``unknown_method_filter`` disclosure (PATCH is not in observed
    methods {GET, POST}). The W1069 assertion is narrower than the
    original "no endpoints" path: it pins ONLY that the
    framework-unknown state is not falsely set. The method-side
    disclosure is covered by ``test_w1075_endpoints_unknown_method.py``."""
    result = _invoke(project, "--framework", "flask", "--method", "PATCH", json_mode=True)
    assert result.exit_code == 0
    data = json.loads(result.output)
    summary = data["summary"]
    assert summary.get("state") != "unknown_framework_filter", (
        f"valid framework + 0 method matches must not produce unknown_framework_filter state, got summary={summary!r}"
    )
    assert summary.get("count") == 0


# ---------------------------------------------------------------------------
# Scenario 4: unknown framework substring → state=unknown_framework_filter,
# partial_success, observed_frameworks enumerated, closest-match suggestion.
# ---------------------------------------------------------------------------


def test_unknown_framework_json_envelope_shape(project):
    """``--framework garblargle`` triggers the W1069 disclosure envelope."""
    result = _invoke(project, "--framework", "garblargle", json_mode=True)
    assert result.exit_code == 0
    data = json.loads(result.output)
    summary = data["summary"]
    assert summary["state"] == "unknown_framework_filter"
    assert summary["partial_success"] is True
    assert summary["requested_framework"] == "garblargle"
    assert summary["count"] == 0
    assert summary["frameworks"] == []
    assert summary["framework_count"] == 0
    assert isinstance(summary["observed_frameworks"], list)
    # Observed set is a non-empty sorted list for this seeded corpus.
    assert summary["observed_frameworks"]
    assert summary["observed_frameworks"] == sorted(summary["observed_frameworks"])
    # Verdict names the unknown value explicitly.
    assert "garblargle" in summary["verdict"]
    assert "unknown" in summary["verdict"].lower()
    # Top-level endpoints array stays empty (no synthetic rows).
    assert data.get("endpoints") == []


def test_unknown_framework_agent_contract_law4_anchored(project):
    """The agent_contract facts must terminal on the ``frameworks``
    concrete-noun anchor — see LAW 4 in CLAUDE.md."""
    result = _invoke(project, "--framework", "garblargle", json_mode=True)
    assert result.exit_code == 0
    data = json.loads(result.output)
    facts = data["agent_contract"]["facts"]
    assert isinstance(facts, list) and facts
    # First two facts terminal on ``frameworks``. (The optional third
    # closest-match fact may terminate on the suggestion name, but
    # closest-match is not guaranteed against ``garblargle`` here.)
    assert facts[0].strip().split()[-1].rstrip(",.;:!?)") == "frameworks"
    assert facts[1].strip().split()[-1].rstrip(",.;:!?)") == "frameworks"


def test_unknown_framework_text_mode_lists_observed(project):
    """Text mode (non-JSON) discloses the observed-frameworks set so a
    human reader sees the same information as an agent reading JSON."""
    result = _invoke(project, "--framework", "garblargle")
    assert result.exit_code == 0
    assert "unknown framework filter" in result.output.lower()
    assert "Observed frameworks:" in result.output


def test_unknown_framework_close_match_suggests_correction(project):
    """A typo close to a real framework (``flas`` → ``flask``) emits a
    difflib-derived correction in the verdict (cutoff 0.6, n=2). The
    ``flas`` substring is NOT a substring of any observed framework
    label so it trips the unknown-framework disclosure first."""
    # ``flas`` is 4 chars; cutoff 0.6 vs ``flask`` (5 chars) ≈ ratio
    # 0.888 → comfortably above cutoff.
    result = _invoke(project, "--framework", "flask_typo_zzz", json_mode=True)
    assert result.exit_code == 0
    data = json.loads(result.output)
    summary = data["summary"]
    assert summary["state"] == "unknown_framework_filter"
    # When close enough, "Did you mean:" suggestion appears in the
    # verdict + a ``did_you_mean`` field is present in summary.
    if "did_you_mean" in summary:
        assert isinstance(summary["did_you_mean"], list)
        assert summary["did_you_mean"]
        verdict = summary["verdict"]
        assert "did you mean" in verdict.lower()
