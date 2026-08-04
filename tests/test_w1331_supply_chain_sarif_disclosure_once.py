"""W1331 -- ``roam supply-chain --sarif`` discloses each marker EXACTLY ONCE.

The W1331 fix for ``cmd_supply_chain`` routes W607-AK/W607-CD degradation
markers onto SARIF ``run.invocations[0].toolExecutionNotifications[]`` so a
Code-Scanning gate cannot read a degraded dependency scan as a clean one.

There are two candidate places to attach them and only ONE may be used:

* ``supply_chain_to_sarif(..., disclosures=...)`` -- a snapshot passed INTO
  the projector. Taken before the call, so it can never carry a marker
  raised BY the projection itself, and it cannot rescue the ``default={}``
  floor that ``_run_check_ak`` substitutes when the projector raises.
* ``with_sarif_disclosures(document, disclosures)`` -- the command-boundary
  adapter applied AFTER the call. Strictly more capable: it sees markers the
  projection itself produced and substitutes a valid zero-result run for a
  floored ``{}``.

Doing both duplicates every marker in ``toolExecutionNotifications[]``. These
tests pin the exactly-once contract, the healthy-run negative control (valid
SARIF, zero notifications), and the projector-raise case that only the
command-boundary adapter can cover.
"""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from unittest import mock

import pytest
from click.testing import CliRunner

from roam.commands import cmd_supply_chain
from roam.commands.cmd_supply_chain import supply_chain

_PRODUCER_ADVISORY = "producer.advisory-warning"


@pytest.fixture
def cli_runner():
    return CliRunner()


@pytest.fixture
def supply_chain_project(tmp_path: Path) -> Path:
    (tmp_path / "requirements.txt").write_text(
        "requests==2.28.0\nclick==8.1.0\nflask\n",
        encoding="utf-8",
    )
    return tmp_path


def _invoke_sarif(runner: CliRunner, project_root: Path):
    with mock.patch("roam.commands.cmd_supply_chain.find_project_root", return_value=project_root):
        return runner.invoke(supply_chain, [], obj={"json": False, "sarif": True, "budget": 0})


def _parse_sarif(output: str) -> dict:
    """Decode the SARIF document, ignoring the trailing ``# warning:`` text.

    ``supply-chain --sarif`` echoes the SARIF document first and then the
    W1331 human-readable warning lines, so the document is the first JSON
    value on stdout rather than the whole of stdout.
    """
    doc, _consumed = json.JSONDecoder().raw_decode(output.lstrip())
    return doc


def _notifications(doc: dict) -> list[dict]:
    invocations = doc["runs"][0].get("invocations") or []
    if not invocations:
        return []
    return invocations[0].get("toolExecutionNotifications") or []


def _raiser(message: str):
    def _raise(*_args, **_kwargs):
        raise RuntimeError(message)

    return _raise


# ---------------------------------------------------------------------------
# (1) Negative control -- a healthy run is valid SARIF with NO notifications.
# ---------------------------------------------------------------------------


def test_healthy_run_emits_valid_sarif_with_zero_notifications(cli_runner, supply_chain_project):
    result = _invoke_sarif(cli_runner, supply_chain_project)

    assert result.exit_code == 0, result.output
    doc = _parse_sarif(result.output)
    assert doc["version"] == "2.1.0"
    assert len(doc["runs"]) == 1
    # Real findings still project: the fixture has one unpinned dependency.
    assert doc["runs"][0]["results"], "healthy run must still carry its findings"
    assert _notifications(doc) == []
    assert "# warning:" not in result.output


# ---------------------------------------------------------------------------
# (2) The exactly-once contract -- fails against the double-emitting form.
# ---------------------------------------------------------------------------


def test_single_degradation_marker_appears_exactly_once(cli_runner, supply_chain_project):
    with mock.patch.object(cmd_supply_chain, "compute_risk_score", _raiser("boom-score")):
        result = _invoke_sarif(cli_runner, supply_chain_project)

    assert result.exit_code == 0, result.output
    doc = _parse_sarif(result.output)
    texts = [n["message"]["text"] for n in _notifications(doc)]
    counts = Counter(texts)

    assert counts == {"supply_chain_compute_risk_score_failed:RuntimeError:boom-score": 1}, (
        f"each marker must be disclosed exactly once, got {counts}"
    )
    # W1331 NEGATIVE-CONTROL HARDENING. Counting notifications alone does not
    # pin that the FINDINGS survive a degraded run. An adversarial check found
    # a degenerate "fix" -- `with_sarif_disclosures({} if markers else doc, ...)`
    # -- which discards every result the moment any boundary degrades, and it
    # passed every assertion in this file. Disclosing a degradation while
    # silently emptying the report is a worse outcome than the bug, so the
    # surviving results are asserted explicitly.
    assert doc["runs"][0]["results"], (
        "a degraded run must still carry its findings; disclosing the "
        "degradation is not a licence to empty the document"
    )


def test_every_marker_appears_exactly_once_when_several_boundaries_degrade(cli_runner, supply_chain_project):
    with (
        mock.patch.object(cmd_supply_chain, "discover_and_parse", _raiser("boom-discover")),
        mock.patch.object(cmd_supply_chain, "compute_risk_score", _raiser("boom-score")),
    ):
        result = _invoke_sarif(cli_runner, supply_chain_project)

    assert result.exit_code == 0, result.output
    notifications = _notifications(_parse_sarif(result.output))
    counts = Counter(n["message"]["text"] for n in notifications)

    assert counts == {
        "supply_chain_discover_and_parse_failed:RuntimeError:boom-discover": 1,
        "supply_chain_compute_risk_score_failed:RuntimeError:boom-score": 1,
    }, f"each marker must be disclosed exactly once, got {counts}"
    assert {n["descriptor"]["id"] for n in notifications} == {_PRODUCER_ADVISORY}
    assert {n["level"] for n in notifications} == {"warning"}


# ---------------------------------------------------------------------------
# (3) Coverage the command-boundary adapter has and the projector kwarg does
#     not: a marker raised BY the projection, on a floored ``{}`` document.
# ---------------------------------------------------------------------------


def test_projector_failure_still_yields_a_valid_sarif_document_with_one_marker(cli_runner, supply_chain_project):
    with mock.patch.object(cmd_supply_chain, "supply_chain_to_sarif", _raiser("boom-project")):
        result = _invoke_sarif(cli_runner, supply_chain_project)

    assert result.exit_code == 0, result.output
    doc = _parse_sarif(result.output)
    # The floored ``{}`` is replaced by a valid zero-result run -- otherwise
    # the failure would be emitted as bare ``{}`` with nowhere to disclose.
    assert doc["version"] == "2.1.0"
    assert doc["runs"][0]["results"] == []
    counts = Counter(n["message"]["text"] for n in _notifications(doc))
    assert counts == {"supply_chain_supply_chain_to_sarif_failed:RuntimeError:boom-project": 1}, (
        f"the projector's own failure must be disclosed exactly once, got {counts}"
    )


# ---------------------------------------------------------------------------
# (4) The projector stays disclosure-free -- no second emission site.
# ---------------------------------------------------------------------------


def test_projector_signature_carries_no_disclosure_channel():
    import inspect

    params = inspect.signature(cmd_supply_chain.supply_chain_to_sarif).parameters
    assert "disclosures" not in params and "warnings_out" not in params, (
        "supply_chain_to_sarif must not take a disclosure channel: the command "
        "boundary attaches markers via with_sarif_disclosures, and a second "
        "emission site duplicates every marker"
    )
