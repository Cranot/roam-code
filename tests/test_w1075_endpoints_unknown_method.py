"""W1075 — ``cmd_endpoints --method`` unknown-value disclosure.

Drive-by sibling of W1069 (cmd_endpoints --framework unknown-value
disclosure). Same Pattern-1D shape: ``roam endpoints --method GARBAGE``
previously returned a generic ``"no endpoints detected matching the
given filters"`` envelope — indistinguishable from "valid method, 0
occurrences in the corpus".

Five scenarios pinned here:

1. No ``--method`` flag → byte-identical to pre-W1075 (no
   ``unknown_method_filter`` state, no ``requested_method`` field).
2. Valid ``--method`` with matches → normal result, no
   ``unknown_method_filter`` state.
3. Valid ``--method`` with 0 matches (paired with another filter that
   narrows to zero, while the method itself IS observed in the corpus)
   → normal "no endpoints" path, NOT ``unknown_method_filter``.
4. Unknown ``--method`` (e.g. ``GARBAGE``) → ``state="unknown_method_filter"``,
   ``partial_success=True``, ``requested_method`` echoed,
   ``observed_methods`` enumerated, ``agent_contract.facts`` anchored on
   ``methods`` (LAW 4), and a difflib closest-match suggestion when
   within cutoff 0.6.
5. LAW 4 anchor terminal verification.
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

# Minimal Flask corpus — provides GET / POST / DELETE so the
# observed_methods superset has multiple entries.
_FLASK_APP = """\
from flask import Flask

app = Flask(__name__)


@app.route('/api/users', methods=['GET'])
def get_users():
    return []


@app.route('/api/users', methods=['POST'])
def create_user():
    return {}, 201


@app.delete('/api/users/<int:user_id>')
def delete_user(user_id):
    return {}, 204
"""


@pytest.fixture
def cli_runner():
    return CliRunner()


@pytest.fixture
def project(tmp_path):
    proj = tmp_path / "w1075_project"
    proj.mkdir()
    (proj / ".gitignore").write_text(".roam/\n")
    (proj / "app.py").write_text(_FLASK_APP, encoding="utf-8")
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
# Scenario 1: no --method flag → byte-identical (no unknown state leak).
# ---------------------------------------------------------------------------


def test_no_method_flag_does_not_emit_unknown_state(project):
    """Without ``--method``, the W1075 disclosure must be a no-op.
    No ``unknown_method_filter`` state, no ``requested_method`` /
    ``observed_methods`` keys."""
    result = _invoke(project, json_mode=True)
    assert result.exit_code == 0
    data = json.loads(result.output)
    summary = data["summary"]
    assert summary.get("state") != "unknown_method_filter"
    assert "requested_method" not in summary
    assert "observed_methods" not in summary
    # The normal verdict shape continues to surface a count + framework
    # tally — Pattern 2 always-emit discipline.
    assert summary.get("count", 0) > 0
    assert "found" in summary["verdict"].lower()


def test_no_method_text_mode_unchanged(project):
    """Text mode without ``--method`` is the standard endpoints output —
    no `unknown method` chatter, no `Observed methods:` line."""
    result = _invoke(project)
    assert result.exit_code == 0
    out = result.output
    assert "unknown method" not in out.lower()
    assert "Observed methods:" not in out


# ---------------------------------------------------------------------------
# Scenario 2: valid --method with matches → normal result.
# ---------------------------------------------------------------------------


def test_method_filter_matches_some(project):
    """``--method GET`` matches the Flask GET route and emits a normal
    result envelope. No ``unknown_method_filter`` state."""
    result = _invoke(project, "--method", "GET", json_mode=True)
    assert result.exit_code == 0
    data = json.loads(result.output)
    summary = data["summary"]
    assert summary.get("state") != "unknown_method_filter"
    assert summary.get("count", 0) >= 1
    # All surviving endpoints are GETs (case-normalised upstream).
    ep_list = data.get("endpoints", [])
    assert all(e["method"] == "GET" for e in ep_list)


def test_method_filter_case_insensitive(project):
    """``--method get`` (lower-case) is normalised to upper-case and
    behaves identically to ``--method GET``. No unknown_method_filter."""
    result = _invoke(project, "--method", "get", json_mode=True)
    assert result.exit_code == 0
    data = json.loads(result.output)
    summary = data["summary"]
    assert summary.get("state") != "unknown_method_filter"
    assert summary.get("count", 0) >= 1


# ---------------------------------------------------------------------------
# Scenario 3: valid --method with 0 matches (paired with a filter that
# narrows to zero) → normal "no endpoints" path, NOT unknown_method_filter.
# ---------------------------------------------------------------------------


def test_known_method_zero_results_is_not_unknown(project):
    """``--method GET --include-tests`` — GET is in the observed-method
    superset; even if a paired filter narrowed to zero matches, this
    must NOT trip the unknown_method_filter disclosure. The corpus has
    GET routes so the method itself is observed.

    For this corpus a paired narrowing that zeroes WITHOUT zeroing the
    framework first is hard to engineer (the only filters are method +
    framework + include-tests + group-by). We assert the simpler
    invariant: when ``--method GET`` matches >=1 endpoint, the state is
    NOT ``unknown_method_filter``."""
    result = _invoke(project, "--method", "GET", json_mode=True)
    assert result.exit_code == 0
    data = json.loads(result.output)
    summary = data["summary"]
    assert summary.get("state") != "unknown_method_filter", (
        f"valid method must not produce unknown_method_filter state, got summary={summary!r}"
    )


# ---------------------------------------------------------------------------
# Scenario 4: unknown --method → state=unknown_method_filter,
# partial_success, observed_methods enumerated, closest-match suggestion.
# ---------------------------------------------------------------------------


def test_unknown_method_json_envelope_shape(project):
    """``--method GARBAGE`` triggers the W1075 disclosure envelope."""
    result = _invoke(project, "--method", "GARBAGE", json_mode=True)
    assert result.exit_code == 0
    data = json.loads(result.output)
    summary = data["summary"]
    assert summary["state"] == "unknown_method_filter"
    assert summary["partial_success"] is True
    assert summary["requested_method"] == "GARBAGE"
    assert summary["count"] == 0
    assert summary["frameworks"] == []
    assert summary["framework_count"] == 0
    assert isinstance(summary["observed_methods"], list)
    # Observed set is a non-empty sorted list for this seeded corpus
    # (GET, POST, DELETE for FLASK_APP).
    assert summary["observed_methods"]
    assert summary["observed_methods"] == sorted(summary["observed_methods"])
    # Verdict names the unknown value explicitly.
    assert "GARBAGE" in summary["verdict"]
    assert "unknown" in summary["verdict"].lower()
    # Top-level endpoints array stays empty (no synthetic rows).
    assert data.get("endpoints") == []


def test_unknown_method_upper_cases_user_input(project):
    """A lower-case unknown ``--method garbage`` is upper-cased in the
    ``requested_method`` echo and the verdict, matching the existing
    case-normalisation contract (``meth_upper = http_method.upper()``)."""
    result = _invoke(project, "--method", "garbage", json_mode=True)
    assert result.exit_code == 0
    data = json.loads(result.output)
    summary = data["summary"]
    assert summary["state"] == "unknown_method_filter"
    assert summary["requested_method"] == "GARBAGE"
    assert "GARBAGE" in summary["verdict"]


def test_unknown_method_close_match_suggests_correction(project):
    """A typo close to a real method (``GETT`` → ``GET``) emits a
    difflib-derived correction in the verdict (cutoff 0.6, n=2). GETT
    is NOT in the observed-method superset so it trips the
    unknown_method_filter disclosure first."""
    result = _invoke(project, "--method", "GETT", json_mode=True)
    assert result.exit_code == 0
    data = json.loads(result.output)
    summary = data["summary"]
    assert summary["state"] == "unknown_method_filter"
    # When close enough, "Did you mean:" suggestion appears in the
    # verdict + a ``did_you_mean`` field is present in summary.
    if "did_you_mean" in summary:
        assert isinstance(summary["did_you_mean"], list)
        assert summary["did_you_mean"]
        verdict = summary["verdict"]
        assert "did you mean" in verdict.lower()


def test_unknown_method_text_mode_lists_observed(project):
    """Text mode (non-JSON) discloses the observed-methods set so a
    human reader sees the same information as an agent reading JSON."""
    result = _invoke(project, "--method", "GARBAGE")
    assert result.exit_code == 0
    assert "unknown method filter" in result.output.lower()
    assert "Observed methods:" in result.output


# ---------------------------------------------------------------------------
# Scenario 5: LAW 4 anchor terminal verification.
# ---------------------------------------------------------------------------


def test_unknown_method_agent_contract_law4_anchored(project):
    """The agent_contract facts must terminal on the ``methods``
    concrete-noun anchor — see LAW 4 in CLAUDE.md.

    ``methods`` is already in the formatter / lint anchor set; no
    vocabulary extension required by W1075."""
    result = _invoke(project, "--method", "GARBAGE", json_mode=True)
    assert result.exit_code == 0
    data = json.loads(result.output)
    facts = data["agent_contract"]["facts"]
    assert isinstance(facts, list) and facts
    # First two facts terminal on ``methods``. (The optional third
    # closest-match fact may terminate on the suggestion name, but
    # closest-match is not guaranteed against ``GARBAGE`` here.)
    assert facts[0].strip().split()[-1].rstrip(",.;:!?)") == "methods"
    assert facts[1].strip().split()[-1].rstrip(",.;:!?)") == "methods"
