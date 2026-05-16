"""W1243 — Pattern-2 variant-D resolution-state disclosure on ``cmd_preflight``.

W1233's audit verdict NON-COMPLIANT flagged that the happy-path envelope was
silent on whether the target resolved as a symbol, a file, a fuzzy LIKE match,
or didn't resolve at all. The W1241 ``resolution_disclosure()`` helper now
backs preflight's happy-path verdict suffix + envelope-level ``resolution`` /
``partial_success`` fields.

This test file locks in one assertion per closed-enum kind so the four
branches in ``_resolve_targets`` can never silently regress:

* ``symbol``    — ``find_symbol`` returns an exact name / qualified-name match
* ``file``      — target string is a file path; ``_resolve_file_symbols`` hits
* ``fuzzy``     — ``find_symbol`` returns a LIKE-fallback (substring) match
* ``unresolved``— neither tier hit; existing error-envelope path

Scope: behaviour-only tests against the JSON envelope shape. No new fixtures.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from click.testing import CliRunner

sys.path.insert(0, str(Path(__file__).parent))
from conftest import invoke_cli, parse_json_output  # noqa: E402


@pytest.fixture
def cli_runner():
    """Provide a Click CliRunner (Click 8.3+ removed mix_stderr)."""
    return CliRunner()


class TestPreflightResolutionDisclosure:
    """W1243 — resolution disclosure on every closed-enum branch."""

    def test_symbol_resolution_emits_symbol_kind(self, indexed_project, cli_runner, monkeypatch):
        """Exact-name match emits ``resolution: "symbol"`` + no partial_success.

        ``User`` is defined exactly once in the fixture so it resolves
        through ``find_symbol``'s simple-name tier without going through
        the LIKE fallback. The W1241 polarity says ``partial_success``
        is False ONLY for the ``symbol`` tier.
        """
        monkeypatch.chdir(indexed_project)
        result = invoke_cli(cli_runner, ["preflight", "User"], json_mode=True)
        data = parse_json_output(result, "preflight")
        assert data["summary"]["resolution"] == "symbol"
        # symbol-tier is the only non-partial-success polarity
        assert data["summary"]["partial_success"] is False
        # top-level mirror so envelope-shape readers find it without
        # diving into summary
        assert data["resolution"] == "symbol"
        assert data["partial_success"] is False
        # verdict must NOT carry a degraded-tier suffix on exact matches
        assert "[fuzzy resolution]" not in data["summary"]["verdict"]
        assert "[file fallback]" not in data["summary"]["verdict"]

    def test_file_resolution_emits_file_kind(self, indexed_project, cli_runner, monkeypatch):
        """File-path target emits ``resolution: "file"`` + partial_success=True.

        ``_looks_like_file`` flips on the ``/`` in the target string and
        routes through ``_resolve_file_symbols`` instead of
        ``find_symbol``. Per W1241 polarity, file-fallback resolutions
        are partial: the agent asked for a path, not a single symbol,
        so the success verdict must disclose the degraded tier.
        """
        monkeypatch.chdir(indexed_project)
        result = invoke_cli(cli_runner, ["preflight", "src/models.py"], json_mode=True)
        data = parse_json_output(result, "preflight")
        assert data["summary"]["resolution"] == "file"
        assert data["summary"]["partial_success"] is True
        assert data["resolution"] == "file"
        assert data["partial_success"] is True
        # Degraded-tier suffix appears on the verdict so agents reading
        # the summary alone see the disclosure (LAW 6).
        assert "[file fallback]" in data["summary"]["verdict"]

    def test_fuzzy_resolution_emits_fuzzy_kind(self, indexed_project, cli_runner, monkeypatch):
        """LIKE-fallback match emits ``resolution: "fuzzy"`` + partial_success=True.

        ``find_symbol`` runs (qualified_name == ?) -> (name == ?) ->
        (name LIKE %?%). The first two tiers fail on a substring-only
        match. ``Use`` is a substring of ``User`` / no exact match for
        ``Use`` exists in the fixture, so resolution drops to the LIKE
        tier. Agents reading the envelope must see ``"fuzzy"`` plus the
        degraded verdict suffix so they know to re-issue with a more
        precise name.
        """
        monkeypatch.chdir(indexed_project)
        result = invoke_cli(cli_runner, ["preflight", "Use"], json_mode=True)
        data = parse_json_output(result, "preflight")
        # Happy-path: the LIKE tier MUST have matched something
        # (``Use`` is a substring of ``User`` and ``unused_helper``).
        # If the test infrastructure changes such that nothing matches,
        # the resolution would be ``unresolved`` and this test would
        # surface the regression.
        assert data["summary"]["resolution"] == "fuzzy", (
            f"expected fuzzy resolution, got {data['summary'].get('resolution')!r} "
            f"with verdict {data['summary'].get('verdict')!r}"
        )
        assert data["summary"]["partial_success"] is True
        assert data["resolution"] == "fuzzy"
        assert data["partial_success"] is True
        assert "[fuzzy resolution]" in data["summary"]["verdict"]

    def test_unresolved_resolution_emits_unresolved_kind(self, indexed_project, cli_runner, monkeypatch):
        """Symbol-not-found emits ``resolution: "unresolved"`` on error envelope.

        The error path already set ``partial_success: True`` and a
        ``"target not found"`` verdict; W1243 additionally stamps the
        canonical ``resolution: "unresolved"`` field so unresolved is
        machine-distinguishable from a successful-but-degraded match.
        """
        monkeypatch.chdir(indexed_project)
        result = invoke_cli(
            cli_runner,
            ["preflight", "ZZZ_NoSuchSymbolAnywhere_ZZZ"],
            json_mode=True,
        )
        data = parse_json_output(result, "preflight")
        assert data["summary"]["resolution"] == "unresolved"
        assert data["summary"]["partial_success"] is True
        # Top-level mirror present on the error envelope too.
        assert data["resolution"] == "unresolved"
        assert data["partial_success"] is True
        # Error path keeps the existing ``risk_level`` shape.
        assert data["summary"]["risk_level"] == "UNKNOWN"
