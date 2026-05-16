"""W1248 — Pattern-2 variant-D: ``roam trace`` resolution-state disclosure.

W1246 audit flagged ``trace`` as NON-COMPLIANT: it resolves TWO targets
(source + target) through ``find_symbol_id``'s 3-rung chain (exact name ->
exact qualified_name -> fuzzy LIKE LIMIT 50), iterates up to 50 x 50 = 2500
combinations, then emits a success envelope indistinguishable from the
fully-resolved case. W1248 wires per-target tier detection so the envelope
discloses:

* both exact            -> ``resolution: "symbol"``,     partial_success: False
* source fuzzy + tgt OK -> ``resolution: "fuzzy"``,      partial_success: True,
                            verdict carries ``[fuzzy: src]``,
                            ``src_resolution: "fuzzy"``, ``tgt_resolution: "symbol"``
* source OK + tgt fuzzy -> ``resolution: "fuzzy"``,      partial_success: True,
                            verdict carries ``[fuzzy: tgt]``,
                            ``src_resolution: "symbol"``, ``tgt_resolution: "fuzzy"``
* both fuzzy            -> ``resolution: "fuzzy"``,      partial_success: True,
                            verdict carries ``[fuzzy: src+tgt]``
* source unresolved     -> ``resolution: "unresolved"``, partial_success: True

The most-degraded outcome wins for the top-level ``resolution`` field;
per-target tiers are surfaced via ``src_resolution`` / ``tgt_resolution``
extension fields so consumers can distinguish "both fuzzy" from
"source fuzzy, target exact". This file pins all five cases.
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
# Fixture — two distinctively-named symbols connected by a call edge so
# trace finds a path; substrings drive the fuzzy LIKE tier independently
# on either side.
# ---------------------------------------------------------------------------


@pytest.fixture
def cli_runner():
    return CliRunner()


@pytest.fixture
def two_target_project(tmp_path):
    """Two named symbols with a direct call edge between them.

    ``handle_payment_inbound`` and ``record_payment_outbound`` share the
    ``payment`` substring -- enough for LIKE matching -- but no shorter
    name collides, so we can drive each tier independently:

    * exact-match: pass the full name as both source and target.
    * fuzzy: pass a substring (``payment_inbound`` -> LIKE -> only
      ``handle_payment_inbound``; ``payment_outbound`` -> LIKE -> only
      ``record_payment_outbound``).
    * unresolved: pass a name nobody has.
    """
    proj = tmp_path / "trace_resolution"
    proj.mkdir()
    (proj / ".gitignore").write_text(".roam/\n")
    src = proj / "src"
    src.mkdir()

    (src / "core.py").write_text(
        "def record_payment_outbound(amount):\n"
        "    return amount\n"
        "\n"
        "def handle_payment_inbound(event):\n"
        "    return record_payment_outbound(event)\n"
    )

    git_init(proj)
    out, rc = index_in_process(proj)
    assert rc == 0, f"index failed:\n{out}"
    return proj


# ---------------------------------------------------------------------------
# Case 1 — both exact match.
# ---------------------------------------------------------------------------


def test_both_exact_match_resolution_symbol(cli_runner, two_target_project, monkeypatch):
    """Exact source + exact target -> ``resolution=symbol``, partial_success=False.

    Both names exist verbatim and there's a direct call edge, so the
    envelope must look fully-resolved with no fuzzy markers.
    """
    monkeypatch.chdir(two_target_project)
    result = invoke_cli(
        cli_runner,
        ["trace", "handle_payment_inbound", "record_payment_outbound"],
        cwd=two_target_project,
        json_mode=True,
    )
    data = parse_json_output(result, "trace")
    summary = data["summary"]

    assert summary["resolution"] == "symbol"
    assert summary["partial_success"] is False
    assert summary["src_resolution"] == "symbol"
    assert summary["tgt_resolution"] == "symbol"
    # Top-level mirror also disclosed.
    assert data.get("resolution") == "symbol"
    assert data.get("partial_success") is False
    # Exact match on both -> verdict carries NO fuzzy suffix.
    assert "[fuzzy:" not in summary["verdict"]
    # Sanity: path actually traced.
    assert summary["state"] == "ok"
    assert summary["paths"] >= 1


# ---------------------------------------------------------------------------
# Case 2 — source fuzzy, target exact.
# ---------------------------------------------------------------------------


def test_source_fuzzy_target_exact(cli_runner, two_target_project, monkeypatch):
    """``payment_inbound`` -> LIKE-only match (``handle_payment_inbound``);
    target is exact. Envelope must say ``resolution=fuzzy``,
    ``src_resolution=fuzzy``, ``tgt_resolution=symbol``, and the verdict
    must carry the ``[fuzzy: src]`` suffix.
    """
    monkeypatch.chdir(two_target_project)
    result = invoke_cli(
        cli_runner,
        ["trace", "payment_inbound", "record_payment_outbound"],
        cwd=two_target_project,
        json_mode=True,
    )
    data = parse_json_output(result, "trace")
    summary = data["summary"]

    assert summary["resolution"] == "fuzzy"
    assert summary["partial_success"] is True
    assert summary["src_resolution"] == "fuzzy"
    assert summary["tgt_resolution"] == "symbol"
    assert data.get("resolution") == "fuzzy"
    assert data.get("partial_success") is True
    assert "[fuzzy: src]" in summary["verdict"]


# ---------------------------------------------------------------------------
# Case 3 — source exact, target fuzzy.
# ---------------------------------------------------------------------------


def test_source_exact_target_fuzzy(cli_runner, two_target_project, monkeypatch):
    """Exact source + fuzzy target (``payment_outbound`` -> LIKE) ->
    ``resolution=fuzzy``, ``src_resolution=symbol``, ``tgt_resolution=fuzzy``,
    verdict carries ``[fuzzy: tgt]``.
    """
    monkeypatch.chdir(two_target_project)
    result = invoke_cli(
        cli_runner,
        ["trace", "handle_payment_inbound", "payment_outbound"],
        cwd=two_target_project,
        json_mode=True,
    )
    data = parse_json_output(result, "trace")
    summary = data["summary"]

    assert summary["resolution"] == "fuzzy"
    assert summary["partial_success"] is True
    assert summary["src_resolution"] == "symbol"
    assert summary["tgt_resolution"] == "fuzzy"
    assert data.get("resolution") == "fuzzy"
    assert data.get("partial_success") is True
    assert "[fuzzy: tgt]" in summary["verdict"]


# ---------------------------------------------------------------------------
# Case 4 — both fuzzy.
# ---------------------------------------------------------------------------


def test_both_fuzzy(cli_runner, two_target_project, monkeypatch):
    """Both source + target driven through the LIKE tier ->
    ``resolution=fuzzy``, ``src_resolution=fuzzy``, ``tgt_resolution=fuzzy``,
    verdict carries the combined ``[fuzzy: src+tgt]`` suffix.
    """
    monkeypatch.chdir(two_target_project)
    result = invoke_cli(
        cli_runner,
        ["trace", "payment_inbound", "payment_outbound"],
        cwd=two_target_project,
        json_mode=True,
    )
    data = parse_json_output(result, "trace")
    summary = data["summary"]

    assert summary["resolution"] == "fuzzy"
    assert summary["partial_success"] is True
    assert summary["src_resolution"] == "fuzzy"
    assert summary["tgt_resolution"] == "fuzzy"
    assert data.get("resolution") == "fuzzy"
    assert data.get("partial_success") is True
    assert "[fuzzy: src+tgt]" in summary["verdict"]


# ---------------------------------------------------------------------------
# Case 5 — source unresolved.
# ---------------------------------------------------------------------------


def test_source_unresolved(cli_runner, two_target_project, monkeypatch):
    """A missing source name must exit non-zero AND the JSON envelope
    surfaces ``resolution=unresolved`` + ``partial_success=True``.

    cmd_trace's not-found branches echo the symbol-not-found envelope to
    stdout and raise SystemExit(1); the disclosure must still be present
    so MCP consumers see the Pattern-2 variant-D shape on failure too.
    """
    monkeypatch.chdir(two_target_project)
    result = invoke_cli(
        cli_runner,
        [
            "trace",
            "definitely_not_a_real_symbol_xyz",
            "record_payment_outbound",
        ],
        cwd=two_target_project,
        json_mode=True,
    )
    # Resolver miss exits non-zero so CI gating works.
    assert result.exit_code != 0
    # Body must NOT silently claim success and MUST carry the disclosure.
    import json

    payload = json.loads(result.output)
    summary = payload["summary"]
    assert summary["resolution"] == "unresolved"
    assert summary["partial_success"] is True
    assert summary["src_resolution"] == "unresolved"
    # Top-level mirror also disclosed.
    assert payload.get("resolution") == "unresolved"
    assert payload.get("partial_success") is True
    # Verdict must name the missing name; no silent success.
    assert "not found" in summary["verdict"].lower()
