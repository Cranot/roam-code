"""Pattern-1 Variant D audit — pinning tests for the 3 HIGH-severity candidates
flagged in ``(internal memo)``.

Pattern-1 Variant D (CLAUDE.md lines 213-275): "Command resolves a target
partially [...] proceeds to act on the degraded resolution, and emits a
success verdict indistinguishable from a fully-resolved success."

Fix template (W324 / W1245 / W1309 sealed cases):
- Disclose ``resolution`` field (closed enum: ``symbol`` / ``file`` /
  ``file_substring`` / ``fuzzy`` / ``unresolved``).
- Flip ``partial_success: true`` when resolution != ``"symbol"``.
- Distinct verdict reflecting the degradation.

This file holds **xfail(strict=True)** pin tests for the top 3
HIGH-severity Variant D candidates. The xfail-strict guard fires when
the fix lands (test passes -> strict fails) so the pin MUST be lifted
as part of the fix commit -- preventing silent acceptance of the buggy
shape after the fix is in.

Audited candidates:
  1. ``safe-zones <substring-file>``  -- silent ``resolution: "symbol"``
     on file-substring fallback (``cmd_safe_zones.py:38-46``).
  2. ``metrics <substring-file>``     -- ``resolution: null`` on
     file-substring fallback (``cmd_metrics.py:484-489``).
  3. ``affected-tests <substring-file>`` -- silent ``resolution: "symbol"``
     on file-substring fallback (``cmd_affected_tests.py:228-230``).

Fix substrate already exists: ``_RESOLUTION_KINDS`` includes
``"file_substring"`` (W1309) and ``resolution_disclosure()`` accepts it.
The pending fix is wiring the substring-fallback branch in each
``_resolve_file_symbols`` helper to return the tier and threading it
through to the envelope.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from click.testing import CliRunner

sys.path.insert(0, str(Path(__file__).parent))
from conftest import invoke_cli  # noqa: E402

_VARIANT_D_REASON = (
    "Pattern-1 Variant D HIGH-severity: silent file-substring fallback "
    "emits success verdict without resolution disclosure. "
    "Fix wave pending per (internal memo). "
    "When the fix lands this xfail-strict guard fires (test passes -> "
    "strict fails) and the pin must be lifted in the same commit."
)


@pytest.fixture
def cli_runner() -> CliRunner:
    return CliRunner()


def _load(result) -> dict:
    """Parse JSON envelope from a CliRunner result."""
    return json.loads(getattr(result, "stdout", None) or result.output)


# ---------------------------------------------------------------------------
# HIGH #1 — safe-zones substring-file fallback
# ---------------------------------------------------------------------------


class TestSafeZonesVariantDFileSubstring:
    """``roam safe-zones service.py`` substring-matches ``src/service.py``
    via the LIKE %name fallback in ``_resolve_file_symbols``
    (``cmd_safe_zones.py:38-46``).

    Wave C (Pattern-1 Variant D audit) sealed this by routing
    ``cmd_safe_zones._resolve_file_symbols`` through the shared
    :func:`roam.commands.resolve.resolve_file_symbols` substrate, which
    returns a tier discriminator (``"file"`` vs ``"file_substring"``).
    The disclosure flips ``partial_success: true`` automatically per
    the ``resolution != "symbol"`` rule in
    :func:`roam.output.formatter.resolution_disclosure`. The xfail-strict
    pin is removed (the test now passes); the assertions remain as a
    regression guard.
    """

    def test_substring_file_match_discloses_file_substring(self, indexed_project, cli_runner, monkeypatch) -> None:
        monkeypatch.chdir(indexed_project)
        result = invoke_cli(cli_runner, ["safe-zones", "service.py"], json_mode=True)
        assert result.exit_code == 0, result.output
        data = _load(result)
        summary = data.get("summary", {})

        # Pinning assertions — these MUST pass post-fix:
        assert summary.get("resolution") == "file_substring", (
            f"expected resolution='file_substring' on LIKE %service.py fallback, got {summary.get('resolution')!r}"
        )
        assert summary.get("partial_success") is True, "partial_success must flip True on substring fallback"
        # The top-level envelope must mirror the disclosure per the
        # W1245 LAW-6 single-line consumer contract.
        assert data.get("resolution") == "file_substring"
        assert data.get("partial_success") is True


# ---------------------------------------------------------------------------
# HIGH #2 — metrics substring-file fallback
# ---------------------------------------------------------------------------


class TestMetricsVariantDFileSubstring:
    """``roam metrics service.py`` substring-matches ``src/service.py``
    via the LIKE %name% fallback in ``_resolve_target``
    (``cmd_metrics.py:484-489``).

    Wave C (Pattern-1 Variant D audit) sealed this by routing
    ``cmd_metrics._resolve_target`` through the shared
    :func:`roam.commands.resolve.resolve_file_symbols` substrate, which
    returns a tier discriminator (``"file"`` vs ``"file_substring"``).
    Pre-Wave-C this branch emitted ``resolution: null`` on a degraded
    success — the most severe of the three audit candidates because
    agents had no signal whatsoever. Post-fix the disclosure flips
    ``partial_success: true`` automatically per the
    ``resolution != "symbol"`` rule. The xfail-strict pin is removed
    (the test now passes); the assertions remain as a regression guard.
    """

    def test_substring_file_match_discloses_file_substring(self, indexed_project, cli_runner, monkeypatch) -> None:
        monkeypatch.chdir(indexed_project)
        result = invoke_cli(cli_runner, ["metrics", "service.py"], json_mode=True)
        assert result.exit_code == 0, result.output
        data = _load(result)
        summary = data.get("summary", {})

        # Pinning assertions — these MUST pass post-fix:
        assert summary.get("resolution") == "file_substring", (
            f"expected resolution='file_substring' on LIKE %service.py% fallback, got {summary.get('resolution')!r}"
        )
        assert summary.get("partial_success") is True, "partial_success must flip True on substring fallback"
        assert data.get("resolution") == "file_substring"
        assert data.get("partial_success") is True


# ---------------------------------------------------------------------------
# HIGH #3 — affected-tests substring-file fallback
# ---------------------------------------------------------------------------


class TestAffectedTestsVariantDFileSubstring:
    """``roam affected-tests service.py`` substring-matches ``src/service.py``
    via the LIKE %path fallback in ``_resolve_file_symbols``
    (``cmd_affected_tests.py:228-230``).

    Pre-Wave-B: the envelope reported ``resolution: "symbol"`` and
    ``partial_success: false`` on substring match -- agents triggered
    test runs against an unknown number of test files for what may be
    the wrong source file. Verdict ``"N tests affected (M files) for
    X.py"`` looked fully resolved.

    Wave B (Pattern-1 Variant D audit) sealed this by routing both
    ``cmd_affected_tests._resolve_file_symbols`` and the import-site
    consumers (``cmd_preflight``, ``cmd_plan``) through the
    :func:`roam.commands.resolve.resolve_file_symbols` substrate, which
    returns a tier discriminator (``"file"`` vs ``"file_substring"``).
    The disclosure flips ``partial_success: true`` automatically per
    the ``resolution != "symbol"`` rule in
    :func:`roam.output.formatter.resolution_disclosure`. The xfail-strict
    pin is removed (the test now passes); the assertions remain as a
    regression guard.
    """

    def test_substring_file_match_discloses_file_substring(self, indexed_project, cli_runner, monkeypatch) -> None:
        monkeypatch.chdir(indexed_project)
        result = invoke_cli(cli_runner, ["affected-tests", "service.py"], json_mode=True)
        assert result.exit_code == 0, result.output
        data = _load(result)
        summary = data.get("summary", {})

        # Pinning assertions — these MUST pass post-fix:
        assert summary.get("resolution") == "file_substring", (
            f"expected resolution='file_substring' on LIKE %service.py fallback, got {summary.get('resolution')!r}"
        )
        assert summary.get("partial_success") is True, "partial_success must flip True on substring fallback"
        assert data.get("resolution") == "file_substring"
        assert data.get("partial_success") is True


# ---------------------------------------------------------------------------
# Baseline guards — assert the EXACT-path branch already emits a clean
# disclosure today. If the fix accidentally regresses exact-match path,
# these will catch it.
# ---------------------------------------------------------------------------


class TestExactMatchBaselines:
    """Sanity checks: exact-path file targets should NOT trip the
    ``file_substring`` tier. These tests are NOT xfail — they pass
    today and must continue to pass after the fix.
    """

    def test_safe_zones_exact_file_path(self, indexed_project, cli_runner, monkeypatch) -> None:
        monkeypatch.chdir(indexed_project)
        result = invoke_cli(cli_runner, ["safe-zones", "src/service.py"], json_mode=True)
        assert result.exit_code == 0, result.output
        data = _load(result)
        summary = data.get("summary", {})
        # exact path -> resolution is NOT file_substring. Today the env
        # reports symbol; post-fix it should be ``file``. Either way, NOT
        # ``file_substring``.
        assert summary.get("resolution") != "file_substring", (
            "exact-path match must not be tagged as substring fallback"
        )

    def test_metrics_exact_file_path(self, indexed_project, cli_runner, monkeypatch) -> None:
        monkeypatch.chdir(indexed_project)
        result = invoke_cli(cli_runner, ["metrics", "src/service.py"], json_mode=True)
        assert result.exit_code == 0, result.output
        data = _load(result)
        summary = data.get("summary", {})
        assert summary.get("resolution") != "file_substring", (
            "exact-path match must not be tagged as substring fallback"
        )

    def test_affected_tests_exact_file_path(self, indexed_project, cli_runner, monkeypatch) -> None:
        monkeypatch.chdir(indexed_project)
        result = invoke_cli(cli_runner, ["affected-tests", "src/service.py"], json_mode=True)
        assert result.exit_code == 0, result.output
        data = _load(result)
        summary = data.get("summary", {})
        assert summary.get("resolution") != "file_substring", (
            "exact-path match must not be tagged as substring fallback"
        )
