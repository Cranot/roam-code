"""W1310 — Pattern-1 Variant D MEDIUM-severity tier-discard regression test
for ``cmd_preflight`` and ``cmd_plan``.

Pattern-1 Variant D (CLAUDE.md lines 213-275): "Command resolves a target
partially [...] proceeds to act on the degraded resolution, and emits a
success verdict indistinguishable from a fully-resolved success."

Wave-B (W1245 family) widened ``cmd_affected_tests._resolve_file_symbols``
into a 3-tuple ``(sym_ids, fpaths, tier)`` where ``tier`` is one of
``"file"`` / ``"file_substring"`` / ``None``. The import-site consumers
``cmd_preflight._resolve_targets`` and ``cmd_plan._resolve_plan_targets``
were widened to consume the tier and thread it into
``resolution_disclosure()`` so a LIKE-fallback substring match no longer
collapses into a bare ``"file"`` resolution.

Wave-C (W1309) sealed the same shape for ``cmd_safe_zones`` and
``cmd_metrics``. This file is the regression pin for the Wave-B
``cmd_preflight`` + ``cmd_plan`` path so a future refactor cannot silently
re-discard the tier and re-collapse the disclosure.

Coverage matrix:
  * preflight + substring fragment    -> resolution="file_substring",
                                          partial_success=True,
                                          verdict suffix "[file substring match]"
  * preflight + exact file path       -> resolution="file" (NOT null,
                                          NOT "file_substring")
  * plan + substring fragment         -> resolution="file_substring",
                                          partial_success=True,
                                          verdict suffix "[file substring match]"
  * plan + exact file path            -> resolution="file"
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from click.testing import CliRunner

sys.path.insert(0, str(Path(__file__).parent))
from conftest import invoke_cli  # noqa: E402


@pytest.fixture
def cli_runner() -> CliRunner:
    return CliRunner()


def _load(result) -> dict:
    """Parse JSON envelope from a CliRunner result."""
    return json.loads(getattr(result, "stdout", None) or result.output)


# ---------------------------------------------------------------------------
# preflight — substring fragment fallback
# ---------------------------------------------------------------------------


class TestPreflightVariantDFileSubstring:
    """``roam preflight service.py`` substring-matches ``src/service.py``
    via the LIKE %name fallback in
    :func:`roam.commands.resolve.resolve_file_symbols`.

    Pre-Wave-B the envelope reported ``resolution: "file"`` and
    ``partial_success: false`` on the substring match — agents read a
    fully-resolved success and proceeded to gate a change against the
    WRONG file (the substring may have hit a sibling). Wave-B widened
    ``_resolve_targets`` to thread the ``file_substring`` tier through
    to :func:`roam.output.formatter.resolution_disclosure`, which auto-
    flips ``partial_success: true`` per the ``resolution != "symbol"``
    rule. This test is the regression pin.
    """

    def test_substring_file_match_discloses_file_substring(self, indexed_project, cli_runner, monkeypatch) -> None:
        monkeypatch.chdir(indexed_project)
        result = invoke_cli(cli_runner, ["preflight", "service.py"], json_mode=True)
        assert result.exit_code == 0, result.output
        data = _load(result)
        summary = data.get("summary", {})

        # Resolution disclosure must surface the degraded tier on summary
        # AND the top-level envelope (W1245 LAW-6 single-line consumer
        # contract).
        assert summary.get("resolution") == "file_substring", (
            f"expected resolution='file_substring' on LIKE %service.py fallback, got {summary.get('resolution')!r}"
        )
        assert data.get("resolution") == "file_substring"
        assert summary.get("partial_success") is True, "partial_success must flip True on substring fallback"
        assert data.get("partial_success") is True

        # Verdict suffix — single-line LAW-6 consumers (agents reading
        # only `summary.verdict`) must see the degraded-tier disclosure.
        verdict = summary.get("verdict", "")
        assert "[file substring match]" in verdict, (
            f"expected '[file substring match]' suffix in verdict, got: {verdict!r}"
        )


# ---------------------------------------------------------------------------
# plan — substring fragment fallback
# ---------------------------------------------------------------------------


class TestPlanVariantDFileSubstring:
    """``roam plan service.py`` substring-matches ``src/service.py`` via
    the same LIKE-fallback substrate. Wave-B widened
    ``_resolve_plan_targets`` to thread the ``file_substring`` tier
    through ``resolution_disclosure()``. This test is the regression
    pin.
    """

    def test_substring_file_match_discloses_file_substring(self, indexed_project, cli_runner, monkeypatch) -> None:
        monkeypatch.chdir(indexed_project)
        result = invoke_cli(cli_runner, ["plan", "service.py"], json_mode=True)
        assert result.exit_code == 0, result.output
        data = _load(result)
        summary = data.get("summary", {})

        assert summary.get("resolution") == "file_substring", (
            f"expected resolution='file_substring' on LIKE %service.py fallback, got {summary.get('resolution')!r}"
        )
        assert data.get("resolution") == "file_substring"
        assert summary.get("partial_success") is True, "partial_success must flip True on substring fallback"
        assert data.get("partial_success") is True

        verdict = summary.get("verdict", "")
        assert "[file substring match]" in verdict, (
            f"expected '[file substring match]' suffix in verdict, got: {verdict!r}"
        )


# ---------------------------------------------------------------------------
# Baseline guards — exact-path branch must NOT trip the substring tier.
# These are regression pins for the preservation check that Wave-C didn't
# accidentally collapse the exact-match path into ``null`` or
# ``"file_substring"``.
# ---------------------------------------------------------------------------


class TestExactMatchBaselines:
    """Exact-path file targets resolve to ``resolution: "file"`` (NOT
    ``null``, NOT ``"file_substring"``). The exact-path branch is the
    canonical success shape — fully-resolved without degradation.
    """

    def test_preflight_exact_file_path(self, indexed_project, cli_runner, monkeypatch) -> None:
        monkeypatch.chdir(indexed_project)
        result = invoke_cli(cli_runner, ["preflight", "src/service.py"], json_mode=True)
        assert result.exit_code == 0, result.output
        data = _load(result)
        summary = data.get("summary", {})
        # exact path must map to ``"file"``, not the degraded substring tier.
        assert summary.get("resolution") == "file", (
            f"exact-path preflight must resolve to 'file', got {summary.get('resolution')!r}"
        )
        # Exact match must NOT carry the substring-match suffix.
        verdict = summary.get("verdict", "")
        assert "[file substring match]" not in verdict, f"exact-path must not carry substring suffix, got: {verdict!r}"

    def test_plan_exact_file_path(self, indexed_project, cli_runner, monkeypatch) -> None:
        monkeypatch.chdir(indexed_project)
        result = invoke_cli(cli_runner, ["plan", "src/service.py"], json_mode=True)
        assert result.exit_code == 0, result.output
        data = _load(result)
        summary = data.get("summary", {})
        assert summary.get("resolution") == "file", (
            f"exact-path plan must resolve to 'file', got {summary.get('resolution')!r}"
        )
        verdict = summary.get("verdict", "")
        assert "[file substring match]" not in verdict, f"exact-path must not carry substring suffix, got: {verdict!r}"
