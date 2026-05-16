"""W1242 — Pattern-2 variant-D: ``roam impact`` resolution-state disclosure.

W1233 audit flagged ``impact`` as the flagship for the silent-success-on-
degraded-resolution anti-pattern: a fuzzy-LIKE-match success was
indistinguishable from an exact-symbol-match success even though the
blast-radius numbers reported are for the fuzzy target, not the symbol the
agent meant.

This file locks in the three tier outcomes:

* exact symbol match  -> ``resolution: "symbol"``,    partial_success: False
* fuzzy LIKE match    -> ``resolution: "fuzzy"``,     partial_success: True,
                         verdict carries the ``[fuzzy resolution ...]`` suffix
* unresolved (missing)-> ``resolution: "unresolved"``, partial_success: True

The W1241 substrate (``roam.output.formatter.resolution_disclosure``) drives
the shape; this test pins the cmd_impact wiring.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from click.testing import CliRunner

sys.path.insert(0, str(Path(__file__).parent))
from conftest import (  # noqa: E402 — relative-to-tests-dir import after sys.path mutation
    git_init,
    index_in_process,
    invoke_cli,
    parse_json_output,
)

# ---------------------------------------------------------------------------
# Fixture — tiny project with one distinctive symbol to exercise all 3 tiers.
# ---------------------------------------------------------------------------


@pytest.fixture
def cli_runner():
    return CliRunner()


@pytest.fixture
def resolution_project(tmp_path):
    """Project with a single distinctively-named symbol + one caller.

    The exact symbol name (``handle_payment_event``) supports the exact-match
    tier; a substring (``payment_event``) drives the fuzzy LIKE fallback;
    a name that doesn't appear anywhere drives the unresolved branch.
    """
    proj = tmp_path / "resolution"
    proj.mkdir()
    (proj / ".gitignore").write_text(".roam/\n")
    src = proj / "src"
    src.mkdir()

    (src / "core.py").write_text(
        "def handle_payment_event(event):\n"
        "    return event.id\n"
        "\n"
        "def caller():\n"
        "    return handle_payment_event(None)\n"
    )

    git_init(proj)
    out, rc = index_in_process(proj)
    assert rc == 0, f"index failed:\n{out}"
    return proj


# ---------------------------------------------------------------------------
# Tier 1 — exact symbol match.
# ---------------------------------------------------------------------------


def test_exact_match_sets_resolution_symbol(cli_runner, resolution_project, monkeypatch):
    """An exact name match must yield ``resolution=symbol`` + ``partial_success=False``.

    Crucially the partial_success flag must remain False even though there is
    only one dependent: degradation comes from the resolver tier, NOT from
    blast-radius truncation.
    """
    monkeypatch.chdir(resolution_project)
    result = invoke_cli(
        cli_runner,
        ["impact", "handle_payment_event"],
        cwd=resolution_project,
        json_mode=True,
    )
    data = parse_json_output(result, "impact")
    summary = data["summary"]

    assert summary["resolution"] == "symbol"
    assert summary["partial_success"] is False
    # Top-level mirror also disclosed.
    assert data.get("resolution") == "symbol"
    assert data.get("partial_success") is False
    # Exact match -> verdict carries NO fuzzy suffix.
    assert "fuzzy resolution" not in summary["verdict"]
    # Sanity: the fixture really did find a dependent (the caller).
    assert summary["affected_symbols"] >= 1


# ---------------------------------------------------------------------------
# Tier 3 — fuzzy LIKE fallback.
# ---------------------------------------------------------------------------


def test_fuzzy_match_sets_resolution_fuzzy_and_partial(cli_runner, resolution_project, monkeypatch):
    """A bare substring must trigger the LIKE-tier fallback.

    The fixture has no symbol literally named ``payment_event``; ``find_symbol``
    only lands on ``handle_payment_event`` via the LIKE ``%payment_event%``
    branch. The envelope must reflect that degradation: ``resolution=fuzzy``,
    ``partial_success=True``, and the verdict must carry the disambiguating
    suffix so text-only consumers see it.
    """
    monkeypatch.chdir(resolution_project)
    result = invoke_cli(
        cli_runner,
        ["impact", "payment_event"],
        cwd=resolution_project,
        json_mode=True,
    )
    data = parse_json_output(result, "impact")
    summary = data["summary"]

    assert summary["resolution"] == "fuzzy"
    assert summary["partial_success"] is True
    assert data.get("resolution") == "fuzzy"
    assert data.get("partial_success") is True
    # Verdict must name the degradation + the actual target found.
    assert "fuzzy resolution" in summary["verdict"]
    assert "handle_payment_event" in summary["verdict"]
    # Resolver landed on a real symbol -> target reflects the resolved name.
    assert summary.get("target") in (
        "handle_payment_event",
        "core.handle_payment_event",
    ) or summary.get("target", "").endswith("handle_payment_event")


# ---------------------------------------------------------------------------
# Tier 4 — unresolved.
# ---------------------------------------------------------------------------


def test_unresolved_sets_resolution_unresolved(cli_runner, resolution_project, monkeypatch):
    """A truly missing name exits 0 (Pattern-2c Convention (c), W1272)
    AND the envelope surfaces ``resolution=unresolved`` +
    ``partial_success=True``.

    Pre-W1272 the not-found branch echoed ``symbol_not_found(...)`` to
    stdout AND raised ``SystemExit(1)``. The W1268 audit collapsed the
    5-way exit-convention divergence onto Convention (c): structured
    envelope + exit 0 so CI gating can distinguish a name-typo
    (recoverable, agent retries with a hint) from a tool/IO failure
    (non-recoverable). The envelope MUST still mark the outcome as
    partial_success so downstream consumers don't conflate it with a
    fully-resolved success.
    """
    import json as _json

    monkeypatch.chdir(resolution_project)
    result = invoke_cli(
        cli_runner,
        ["impact", "definitely_not_a_real_symbol_xyz"],
        cwd=resolution_project,
        json_mode=True,
    )
    # W1272 — Convention (c): unresolved is a successful "nothing-to-
    # analyze" envelope, not a tool failure.
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)
    # The envelope discloses the degraded resolution state explicitly.
    assert data["resolution"] == "unresolved"
    assert data["partial_success"] is True
    assert data["summary"]["resolution"] == "unresolved"
    assert "not found" in data["summary"]["verdict"].lower()
