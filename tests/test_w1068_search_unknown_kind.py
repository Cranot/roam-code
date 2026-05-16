"""W1068 — ``cmd_search --kind`` unknown-value disclosure.

Sibling of W1063 (cmd_findings unknown-detector disclosure) and W1064
(difflib closest-match). The bug being fixed: ``roam search foo --kind
garbage`` previously returned a generic "no matches" envelope —
indistinguishable from "valid kind, 0 hits". This regresses
Pattern-1D silent-success on degraded filter resolution.

Four scenarios pinned here:

1. ``--kind <valid> <pattern-with-no-hits>`` → clean empty result, NO
   ``unknown_kind`` state.
2. ``--kind <garbage>`` → ``state="unknown_kind"``, ``partial_success=True``,
   ``requested_kind`` echoed, ``known_kinds`` enumerated, agent_contract
   facts anchored on ``kinds``.
3. ``--kind <typo-close-to-real>`` → verdict carries a "Did you mean: …?"
   suggestion via difflib (cutoff 0.6, n=2).
4. ``roam search <pattern>`` (no ``--kind``) → byte-identical to pre-W1068.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from click.testing import CliRunner

sys.path.insert(0, str(Path(__file__).parent))
from conftest import assert_json_envelope, git_init, index_in_process


def _make_project(tmp_path_factory):
    proj = tmp_path_factory.mktemp("w1068_project")
    (proj / ".gitignore").write_text(".roam/\n")
    (proj / "auth.py").write_text(
        "class AuthManager:\n"
        "    def authenticate_user(self, username, password):\n"
        "        return True\n"
        "\n"
        "    def create_user(self, username, email):\n"
        "        pass\n",
        encoding="utf-8",
    )
    (proj / "utils.py").write_text(
        "def validate_email(email):\n    return '@' in email\n",
        encoding="utf-8",
    )
    git_init(proj)
    index_in_process(proj)
    return proj


@pytest.fixture
def project(tmp_path_factory, monkeypatch):
    proj = _make_project(tmp_path_factory)
    monkeypatch.chdir(proj)
    return proj


def _run(project, *args):
    from roam.cli import cli

    runner = CliRunner()
    return runner.invoke(cli, list(args), catch_exceptions=False)


# ---------------------------------------------------------------------------
# Scenario 1: valid kind + 0 matches → clean empty result, no unknown state.
# ---------------------------------------------------------------------------


def test_valid_kind_zero_matches_is_not_unknown_kind(project):
    """``--kind fn`` is valid; pattern with no hits returns ``state`` absent
    from ``unknown_kind``. Distinguishes empty-result from typo-result."""
    result = _run(project, "--json", "search", "zzzNotInIndex999", "-k", "fn")
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert_json_envelope(data, command="search")
    summary = data["summary"]
    # Must NOT be the unknown_kind state.
    assert summary.get("state") != "unknown_kind", (
        f"valid kind + 0 matches must not produce unknown_kind state, got summary={summary!r}"
    )
    assert summary.get("total") == 0
    # The no-match envelope continues to surface a verdict; the W1068
    # contract only restricts the unknown-kind state from leaking in here.
    assert "no matches" in summary["verdict"].lower()


def test_valid_full_kind_name_also_accepted(project):
    """Full kind names like ``function`` are valid (alongside the
    abbreviation ``fn``)."""
    result = _run(project, "--json", "search", "zzzNotInIndex999", "-k", "function")
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert data["summary"].get("state") != "unknown_kind"


# ---------------------------------------------------------------------------
# Scenario 2: unknown kind → state=unknown_kind, partial_success, etc.
# ---------------------------------------------------------------------------


def test_unknown_kind_json_envelope_shape(project):
    """``--kind garblargle`` triggers the W1068 disclosure envelope."""
    result = _run(project, "--json", "search", "user", "-k", "garblargle")
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert_json_envelope(data, command="search")
    summary = data["summary"]
    assert summary["state"] == "unknown_kind"
    assert summary["partial_success"] is True
    assert summary["requested_kind"] == "garblargle"
    assert summary["total"] == 0
    assert isinstance(summary["known_kinds"], list)
    assert "fn" in summary["known_kinds"]
    assert "function" in summary["known_kinds"]
    # known_kinds is sorted (deterministic disclosure surface).
    assert summary["known_kinds"] == sorted(summary["known_kinds"])
    # Verdict names the unknown value explicitly.
    assert "garblargle" in summary["verdict"]
    assert "unknown" in summary["verdict"].lower()


def test_unknown_kind_agent_contract_law4_anchored(project):
    """The agent_contract facts must end on the ``kinds`` concrete-noun
    anchor — see LAW 4 in CLAUDE.md."""
    result = _run(project, "--json", "search", "user", "-k", "garblargle")
    assert result.exit_code == 0
    data = json.loads(result.output)
    facts = data["agent_contract"]["facts"]
    assert isinstance(facts, list) and facts
    # Both facts terminal on ``kinds`` (concrete-noun-anchored per LAW 4).
    for fact in facts:
        terminal = fact.strip().split()[-1].rstrip(",.;:!?)")
        assert terminal == "kinds", f"fact {fact!r} terminal {terminal!r} not anchored on 'kinds'"


def test_unknown_kind_text_mode_lists_known_kinds(project):
    """Text mode (non-JSON) still discloses the known-kinds set so a human
    reader sees the same information as an agent reading JSON."""
    result = _run(project, "search", "user", "-k", "garblargle")
    assert result.exit_code == 0
    assert "unknown kind" in result.output.lower()
    assert "Known kinds:" in result.output
    # At least the two canonical full+abbrev names should appear.
    assert "fn" in result.output
    assert "function" in result.output


# ---------------------------------------------------------------------------
# Scenario 3: unknown kind with close-match → "did you mean X?" suggestion.
# ---------------------------------------------------------------------------


def test_unknown_kind_close_match_suggests_correction(project):
    """A typo close to a real kind (``functoin`` → ``function``) emits a
    difflib-derived correction in the verdict (cutoff 0.6, n=2)."""
    result = _run(project, "--json", "search", "user", "-k", "functoin")
    assert result.exit_code == 0
    data = json.loads(result.output)
    verdict = data["summary"]["verdict"]
    assert "did you mean" in verdict.lower(), f"expected 'did you mean' suggestion in verdict, got: {verdict!r}"
    assert "function" in verdict


def test_unknown_kind_text_mode_close_match(project):
    """Text mode surfaces the suggestion via the verdict line as well."""
    result = _run(project, "search", "user", "-k", "functoin")
    assert result.exit_code == 0
    # Verdict line contains the suggestion.
    assert "did you mean" in result.output.lower()
    assert "function" in result.output


# ---------------------------------------------------------------------------
# Scenario 4: default path (no --kind) is byte-identical to pre-W1068.
# ---------------------------------------------------------------------------


def test_default_path_no_kind_filter_byte_identical(project):
    """Without ``--kind``, the W1068 validation must be a no-op. The
    output of a vanilla search is preserved byte-for-byte (modulo the
    ``_meta.timestamp`` field, which is non-deterministic by design)."""
    result = _run(project, "--json", "search", "user")
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert_json_envelope(data, command="search")
    summary = data["summary"]
    # The unknown_kind state MUST NOT appear when --kind is absent.
    assert "state" not in summary or summary.get("state") != "unknown_kind"
    assert "requested_kind" not in summary
    assert "known_kinds" not in summary
    # The verdict is the normal "N matches for 'pattern'" form.
    assert "matches for" in summary["verdict"]
    # Results array is populated for the seeded "user" pattern.
    assert data.get("total", 0) > 0


def test_default_path_text_mode_unchanged(project):
    """Text mode without ``--kind`` is the standard search output —
    no `unknown` chatter, no `Known kinds` line."""
    result = _run(project, "search", "user")
    assert result.exit_code == 0
    out = result.output
    assert "unknown kind" not in out.lower()
    assert "Known kinds:" not in out
    assert "=== Symbols matching" in out
