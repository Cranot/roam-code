"""W1245 — Pattern-2 variant-D resolution disclosure on ``roam safe-delete``.

W1233 audit identified ``cmd_safe_delete`` among 34 resolver-using
commands that emit a single-symbol verdict (SAFE / REVIEW / UNSAFE) but
never disclose which rung of the 3-tier ``find_symbol`` chain
(qualified-name -> simple-name -> fuzzy LIKE) actually matched. A
fuzzy-LIKE fallback produces a real deletion decision, but for a symbol
that may not be the one the caller meant — the agent silently consumes
a degraded SAFE verdict as if it were a green light on the intended
target.

W1241 hoisted ``resolution_disclosure()`` into ``roam.output.formatter``.
W1249 stamps ``_resolution_tier`` on every ``find_symbol`` return.
W1245 batch-1 applies the disclosure to five high-traffic resolver
commands; this file pins the ``safe-delete`` wiring on the three tier
outcomes:

* exact symbol match  -> ``resolution=symbol``,    partial_success=False;
                         ``summary.verdict`` stays bare SAFE/REVIEW/UNSAFE.
* fuzzy LIKE match    -> ``resolution=fuzzy``,     partial_success=True;
                         ``summary.verdict`` carries the
                         ``[fuzzy resolution ...]`` suffix while the
                         top-level ``verdict`` stays the bare categorical
                         (callers reading the structured field keep their
                         enum-based dispatch).
* unresolved (missing)-> existing ``symbol_not_found`` non-zero exit path
                         (unchanged — variant-D guards the success path).
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


class TestSafeDeleteResolution:
    """W1245 — ``roam safe-delete`` resolution disclosure tests."""

    def test_exact_match_emits_symbol_resolution(self, indexed_project, cli_runner, monkeypatch) -> None:
        """Exact-name match -> ``resolution=symbol`` + ``partial_success=False``.

        ``unused_helper`` is defined in the fixture's ``src/service.py`` and
        has no callers; the resolver lands on the exact-name rung (tier 1/2)
        and the verdict will be SAFE. Disclosure must reflect the exact
        match in BOTH the summary block and the top-level envelope; the
        verdict must NOT carry the fuzzy-resolution suffix.
        """
        monkeypatch.chdir(indexed_project)
        result = invoke_cli(cli_runner, ["safe-delete", "unused_helper"], json_mode=True)
        assert result.exit_code == 0, result.output
        data = json.loads(getattr(result, "stdout", None) or result.output)
        summary = data["summary"]

        assert summary["resolution"] == "symbol"
        assert summary["partial_success"] is False
        assert data["resolution"] == "symbol"
        assert data["partial_success"] is False
        assert "[fuzzy resolution" not in summary["verdict"]
        # The categorical top-level verdict stays SAFE/REVIEW/UNSAFE for
        # enum-based dispatch consumers.
        assert data["verdict"] in {"SAFE", "REVIEW", "UNSAFE"}

    def test_fuzzy_match_emits_fuzzy_resolution(self, indexed_project, cli_runner, monkeypatch) -> None:
        """LIKE-fallback substring -> ``resolution=fuzzy`` + verdict suffix.

        ``unused_help`` is not an exact name; ``find_symbol`` falls
        through the exact-name rungs and lands on the LIKE
        ``%unused_help%`` fallback (tier 3), resolving to
        ``unused_helper``. The envelope must disclose the degradation in
        the summary, and the summary verdict must carry the
        disambiguating ``[fuzzy resolution ...]`` suffix so LAW-6
        single-line consumers see the signal on the verdict alone. The
        top-level ``verdict`` stays the bare categorical so enum-based
        dispatch consumers keep working.
        """
        monkeypatch.chdir(indexed_project)
        result = invoke_cli(cli_runner, ["safe-delete", "unused_help"], json_mode=True)
        assert result.exit_code == 0, result.output
        data = json.loads(getattr(result, "stdout", None) or result.output)
        summary = data["summary"]

        assert summary["resolution"] == "fuzzy"
        assert summary["partial_success"] is True
        assert data["resolution"] == "fuzzy"
        assert data["partial_success"] is True
        assert "[fuzzy resolution" in summary["verdict"]
        # Top-level categorical stays bare for enum-based dispatch.
        assert data["verdict"] in {"SAFE", "REVIEW", "UNSAFE"}
        # ``target`` echoes the resolved symbol.
        assert summary.get("target") == "unused_helper" or data.get("target") == "unused_helper"

    def test_unresolved_input_exits_nonzero(self, indexed_project, cli_runner, monkeypatch) -> None:
        """Total-miss input still exits non-zero via the pre-existing
        ``symbol_not_found`` path (a separate error envelope shape).

        Variant-D guards the SUCCESS verdict on degraded resolution; the
        not-found case is already explicit by exit code + error message,
        so this test just confirms the legacy contract still holds.
        """
        monkeypatch.chdir(indexed_project)
        result = invoke_cli(cli_runner, ["safe-delete", "definitely_no_such_symbol_zzz"])
        assert result.exit_code != 0 or "not found" in result.output.lower()
