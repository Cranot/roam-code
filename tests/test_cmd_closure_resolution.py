"""W1245 — Pattern-2 variant-D resolution disclosure on ``roam closure``.

W1233 audit identified ``cmd_closure`` among 34 resolver-using commands
that emit a single-symbol envelope but never disclose which rung of the
3-tier ``find_symbol`` chain (qualified-name -> simple-name -> fuzzy LIKE)
actually matched. A fuzzy-LIKE fallback produces a real change set, but
for a symbol that may not be the one the caller meant — the agent
silently consumes a degraded plan as if it were exact.

W1241 hoisted ``resolution_disclosure()`` into ``roam.output.formatter``.
W1249 stamps ``_resolution_tier`` on every ``find_symbol`` return.
W1245 batch-1 applies the disclosure to five high-traffic resolver
commands; this file pins the ``closure`` wiring on the three tier
outcomes:

* exact symbol match  -> ``resolution=symbol``,    partial_success=False
* fuzzy LIKE match    -> ``resolution=fuzzy``,     partial_success=True,
                         verdict carries ``[fuzzy resolution ...]`` suffix
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


class TestClosureResolution:
    """W1245 — ``roam closure`` resolution disclosure tests."""

    def test_exact_match_emits_symbol_resolution(self, indexed_project, cli_runner, monkeypatch) -> None:
        """Exact qualified/simple-name match -> ``resolution=symbol``.

        ``create_user`` is defined in the fixture's ``src/service.py``; the
        resolver lands on the exact-name rung (tier 1/2). The envelope MUST
        disclose ``resolution=symbol`` + ``partial_success=False`` in BOTH
        the summary block and the top-level envelope so consumers reading
        either surface get the same signal. The verdict must NOT carry the
        fuzzy-resolution suffix.
        """
        monkeypatch.chdir(indexed_project)
        result = invoke_cli(cli_runner, ["closure", "create_user"], json_mode=True)
        assert result.exit_code == 0, result.output
        data = json.loads(getattr(result, "stdout", None) or result.output)
        summary = data["summary"]

        assert summary["resolution"] == "symbol"
        assert summary["partial_success"] is False
        assert data["resolution"] == "symbol"
        assert data["partial_success"] is False
        assert "[fuzzy resolution" not in summary["verdict"]

    def test_fuzzy_match_emits_fuzzy_resolution(self, indexed_project, cli_runner, monkeypatch) -> None:
        """LIKE-fallback substring -> ``resolution=fuzzy`` + verdict suffix.

        ``create_us`` is not an exact name; ``find_symbol`` falls through
        the qualified- and simple-name rungs and lands on the LIKE
        ``%create_us%`` fallback (tier 3), resolving to ``create_user``.
        The envelope must disclose the degradation: ``resolution=fuzzy``,
        ``partial_success=True``, and the verdict must carry the
        disambiguating ``[fuzzy resolution ...]`` suffix so LAW-6
        single-line consumers see the signal on the verdict alone.
        """
        monkeypatch.chdir(indexed_project)
        result = invoke_cli(cli_runner, ["closure", "create_us"], json_mode=True)
        assert result.exit_code == 0, result.output
        data = json.loads(getattr(result, "stdout", None) or result.output)
        summary = data["summary"]

        assert summary["resolution"] == "fuzzy"
        assert summary["partial_success"] is True
        assert data["resolution"] == "fuzzy"
        assert data["partial_success"] is True
        assert "[fuzzy resolution" in summary["verdict"]
        # ``target`` echoes the resolved symbol so the agent can confirm
        # which symbol roam actually ranked.
        assert summary.get("target") == "create_user" or data.get("target") == "create_user"

    def test_unresolved_input_exits_nonzero(self, indexed_project, cli_runner, monkeypatch) -> None:
        """Total-miss input still exits non-zero via the pre-existing
        ``symbol_not_found`` path (a separate error envelope shape).

        Variant-D guards the SUCCESS verdict on degraded resolution; the
        not-found case is already explicit by exit code + error message,
        so this test just confirms the legacy contract still holds — a
        completely-unmatched name fails fast.
        """
        monkeypatch.chdir(indexed_project)
        result = invoke_cli(cli_runner, ["closure", "definitely_no_such_symbol_zzz"])
        assert result.exit_code != 0 or "not found" in result.output.lower()
