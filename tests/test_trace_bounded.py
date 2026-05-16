"""Tests for bounded ``roam trace`` execution.

The dogfood corpus flagged ``trace`` as a tier-3 skip because it hard-failed
on 18K-symbol production graphs. This suite locks in three guarantees:

1. Default ``--max-hops 6`` finds short paths and returns a clean
   ``no_path_within_hops`` envelope otherwise (no exception)
2. ``--max-hops 3`` is respected — paths > 3 edges are not returned
3. When no path exists, the envelope is structured (NOT an exception)
   with ``partial_success: true`` and ``state: "no_path_within_hops"``

See ``cmd_trace.py`` for the bounded BFS implementation.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from click.testing import CliRunner

sys.path.insert(0, str(Path(__file__).parent))
from conftest import (  # noqa: E402
    git_init,
    index_in_process,
    invoke_cli,
    parse_json_output,
)


@pytest.fixture
def cli_runner():
    return CliRunner()


# ---------------------------------------------------------------------------
# Fixtures — long-chain and disconnected projects
# ---------------------------------------------------------------------------


@pytest.fixture
def long_chain_project(tmp_path):
    """10-deep chain: f0 -> f1 -> f2 -> ... -> f9 (each calls the next).

    Path from f0 to f9 = 9 edges. Anchors --max-hops tests so we can
    assert "within hops" vs "exhausted budget" verdicts deterministically.
    """
    proj = tmp_path / "long_chain"
    proj.mkdir()
    (proj / ".gitignore").write_text(".roam/\n")
    src = proj / "src"
    src.mkdir()

    body = ""
    for i in range(10):
        body += f"def f{i}():\n"
        if i < 9:
            body += f"    return f{i + 1}()\n"
        else:
            body += "    return 0\n"
        body += "\n"
    (src / "chain.py").write_text(body)

    git_init(proj)
    out, rc = index_in_process(proj)
    assert rc == 0, f"index failed:\n{out}"
    return proj


@pytest.fixture
def disconnected_project(tmp_path):
    """Two unrelated symbols in unrelated files (no call edges).

    Used to confirm "no path exists" returns a clean envelope rather
    than an exception or hard error.
    """
    proj = tmp_path / "disconnected"
    proj.mkdir()
    (proj / ".gitignore").write_text(".roam/\n")
    src = proj / "src"
    src.mkdir()

    (src / "alpha.py").write_text("def alpha():\n    return 1\n")
    (src / "beta.py").write_text("def beta():\n    return 2\n")

    git_init(proj)
    out, rc = index_in_process(proj)
    assert rc == 0, f"index failed:\n{out}"
    return proj


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_trace_max_hops_default(cli_runner, long_chain_project, monkeypatch):
    """Default ``--max-hops 6`` finds short paths and emits a clean
    ``no_path_within_hops`` envelope when the target sits past the cap.

    The 10-deep chain has a 9-edge path from f0 to f9 — beyond the
    default 6-hop budget, so the envelope must report partial_success
    (never raise).
    """
    monkeypatch.chdir(long_chain_project)

    # Within hops: f0 -> f1 is 1 edge — must succeed with state=ok.
    short = invoke_cli(
        cli_runner,
        ["trace", "f0", "f1"],
        cwd=long_chain_project,
        json_mode=True,
    )
    short_data = parse_json_output(short, "trace")
    assert short_data["summary"].get("state") == "ok"
    assert short_data["summary"]["paths"] >= 1

    # Beyond hops: f0 -> f9 is 9 edges — clean envelope, no exception.
    far = invoke_cli(
        cli_runner,
        ["trace", "f0", "f9"],
        cwd=long_chain_project,
        json_mode=True,
    )
    assert far.exit_code == 0, f"trace beyond max-hops should NOT raise; got exit={far.exit_code}\n{far.output}"
    far_data = parse_json_output(far, "trace")
    assert far_data["summary"].get("state") == "no_path_within_hops"
    assert far_data["summary"].get("partial_success") is True
    assert far_data["summary"]["paths"] == 0


def test_trace_max_hops_explicit(cli_runner, long_chain_project, monkeypatch):
    """``--max-hops 3`` is respected — f0 to f5 (5 edges) returns
    ``no_path_within_hops``; f0 to f2 (2 edges) succeeds.
    """
    monkeypatch.chdir(long_chain_project)

    # f0 -> f2 = 2 edges. Within max-hops=3.
    ok = invoke_cli(
        cli_runner,
        ["trace", "f0", "f2", "--max-hops", "3"],
        cwd=long_chain_project,
        json_mode=True,
    )
    ok_data = parse_json_output(ok, "trace")
    assert ok_data["summary"].get("state") == "ok"
    # Path length (hops in node terms) is 3 nodes = 2 edges; envelope
    # reports node count.
    assert ok_data["summary"]["paths"] >= 1

    # f0 -> f5 = 5 edges. Past max-hops=3.
    out_of_budget = invoke_cli(
        cli_runner,
        ["trace", "f0", "f5", "--max-hops", "3"],
        cwd=long_chain_project,
        json_mode=True,
    )
    assert out_of_budget.exit_code == 0
    oob_data = parse_json_output(out_of_budget, "trace")
    assert oob_data["summary"].get("state") == "no_path_within_hops"
    assert oob_data["summary"]["paths"] == 0
    # max_hops echoed back in the envelope.
    assert oob_data["summary"].get("max_hops") == 3


def test_trace_no_path_clean_envelope(cli_runner, disconnected_project, monkeypatch):
    """When no path can possibly exist, the envelope is well-formed —
    NOT an exception, NOT empty stdout.
    """
    monkeypatch.chdir(disconnected_project)
    result = invoke_cli(
        cli_runner,
        ["trace", "alpha", "beta"],
        cwd=disconnected_project,
        json_mode=True,
    )

    # Pattern 1 (no JSON-parse-on-empty-stdout): exit 0, parseable JSON.
    assert result.exit_code == 0, f"trace with no path must exit 0; got {result.exit_code}\n{result.output}"
    data = parse_json_output(result, "trace")
    summary = data["summary"]

    # Structured failure, not silent fallback (Pattern 2).
    assert summary.get("partial_success") is True
    assert summary.get("state") == "no_path_within_hops"
    assert summary["paths"] == 0
    # Verdict is single-line and self-contained (LAW 6).
    assert "verdict" in summary
    assert isinstance(summary["verdict"], str)
    assert "alpha" in summary["verdict"] and "beta" in summary["verdict"]
    # paths key is present even on failure (empty list, not missing).
    assert data.get("paths") == []
