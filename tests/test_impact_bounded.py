"""Tests for bounded ``roam impact`` execution.

The dogfood corpus flagged ``impact`` as a tier-3 skip because high-fan-in
symbols (e.g. ``useThemeClasses`` with 528 callers) hit a 60s timeout. This
suite locks in three guarantees:

1. ``--max-callers N`` caps total fan-out at exactly N
2. ``--depth N`` caps BFS depth at exactly N
3. When a cap fires, the envelope reports ``truncated: true`` and
   ``partial_success: true``

See ``cmd_impact.py`` for the bounded BFS implementation.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from click.testing import CliRunner

sys.path.insert(0, str(Path(__file__).parent))
from conftest import (  # noqa: E402  — relative-to-tests-dir import after sys.path mutation
    git_init,
    index_in_process,
    invoke_cli,
    parse_json_output,
)

# ---------------------------------------------------------------------------
# Click 8.2+ removed CliRunner(mix_stderr=...) — mirror the override the
# main exploration tests use so stdout-only assertions still parse cleanly.
# ---------------------------------------------------------------------------


@pytest.fixture
def cli_runner():
    runner = CliRunner()
    return runner


# ---------------------------------------------------------------------------
# Fixtures — high-fan-in and deep-chain projects
# ---------------------------------------------------------------------------


@pytest.fixture
def high_fan_in_project(tmp_path):
    """Project with one symbol called by 100 distinct callers.

    Mirrors the ``useThemeClasses`` pattern (1 hub, many consumers) that
    caused the 60s timeout in the dogfood corpus.
    """
    proj = tmp_path / "high_fan_in"
    proj.mkdir()
    (proj / ".gitignore").write_text(".roam/\n")
    src = proj / "src"
    src.mkdir()

    # The hub symbol.
    (src / "hub.py").write_text("def hub():\n    return 42\n")

    # 100 caller files, each with a function that calls hub().
    callers_dir = src / "callers"
    callers_dir.mkdir()
    (callers_dir / "__init__.py").write_text("")
    for i in range(100):
        (callers_dir / f"caller_{i:03d}.py").write_text(
            f"from hub import hub\n\ndef caller_{i:03d}():\n    return hub()\n"
        )

    git_init(proj)
    out, rc = index_in_process(proj)
    assert rc == 0, f"index failed:\n{out}"
    return proj


@pytest.fixture
def deep_chain_project(tmp_path):
    """Project with a 5-level call chain: f0 <- f1 <- f2 <- f3 <- f4 <- f5.

    Each f{n} calls f{n-1}, so the reverse-graph descendants of f0 form a
    chain of length 5.
    """
    proj = tmp_path / "deep_chain"
    proj.mkdir()
    (proj / ".gitignore").write_text(".roam/\n")
    src = proj / "src"
    src.mkdir()

    # f0 — the leaf at the bottom; everything else eventually calls it.
    (src / "chain.py").write_text(
        "def f0():\n"
        "    return 0\n"
        "\n"
        "def f1():\n"
        "    return f0()\n"
        "\n"
        "def f2():\n"
        "    return f1()\n"
        "\n"
        "def f3():\n"
        "    return f2()\n"
        "\n"
        "def f4():\n"
        "    return f3()\n"
        "\n"
        "def f5():\n"
        "    return f4()\n"
    )

    git_init(proj)
    out, rc = index_in_process(proj)
    assert rc == 0, f"index failed:\n{out}"
    return proj


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_impact_respects_max_callers(cli_runner, high_fan_in_project, monkeypatch):
    """With 100 callers, ``--max-callers 10`` returns at most 10 dependents
    and flags truncation.
    """
    monkeypatch.chdir(high_fan_in_project)
    result = invoke_cli(
        cli_runner,
        ["impact", "hub", "--max-callers", "10", "--depth", "10"],
        cwd=high_fan_in_project,
        json_mode=True,
    )
    data = parse_json_output(result, "impact")
    summary = data["summary"]

    # The cap is on dependents (BFS frontier nodes). 10 must hold.
    assert summary["affected_symbols"] <= 10, (
        f"Expected <=10 affected symbols under --max-callers 10, got {summary['affected_symbols']}"
    )
    # Truncation flags must fire — there are 100 real callers, far more
    # than the cap.
    assert summary["truncated"] is True
    assert summary["partial_success"] is True
    # State must explicitly name which cap fired.
    assert summary["state"] == "caller_cap"


def test_impact_depth_limit(cli_runner, deep_chain_project, monkeypatch):
    """With a 5-deep chain anchored on ``f0``, ``--depth 2`` returns only
    the callers reachable within 2 reverse hops.
    """
    monkeypatch.chdir(deep_chain_project)
    # Unbounded baseline first — at most 5 dependents (f1..f5).
    full_result = invoke_cli(
        cli_runner,
        ["impact", "f0", "--depth", "10", "--max-callers", "0"],
        cwd=deep_chain_project,
        json_mode=True,
    )
    full_data = parse_json_output(full_result, "impact")
    full_count = full_data["summary"]["affected_symbols"]

    # Now bounded at depth 2 — should be strictly less than full reach
    # (assuming the chain extracts > 2 levels of callers, which is
    # the whole point of the deep-chain fixture).
    bounded_result = invoke_cli(
        cli_runner,
        ["impact", "f0", "--depth", "2", "--max-callers", "0"],
        cwd=deep_chain_project,
        json_mode=True,
    )
    bounded_data = parse_json_output(bounded_result, "impact")
    bounded_count = bounded_data["summary"]["affected_symbols"]

    # Sanity: full reach should be > 2 for the fixture to exercise depth.
    assert full_count >= 3, (
        f"Fixture should produce >=3 reverse-graph dependents at f0; "
        f"got {full_count}. Adjust the fixture if the parser changed."
    )
    # Bounded reach must respect depth=2 — at most 2 hops of dependents.
    assert bounded_count <= 2, f"Expected <=2 dependents at --depth 2, got {bounded_count}"
    assert bounded_count < full_count


def test_weighted_impact_not_truncated_to_zero_on_real_data(cli_runner, high_fan_in_project, monkeypatch):
    """W336 — ``weighted_impact`` must be > 0 whenever there are real
    affected symbols.

    Two bugs combined to silently zero this metric:

    1. The unconditional ``round(weighted_impact, 4)`` truncated legitimate
       small per-node PageRank sums (1e-5 to 1e-3 range on multi-thousand
       node graphs) down to 0.0.
    2. The bare ``except Exception`` around the ``nx.pagerank`` call
       silently swallowed ``ImportError`` when scipy/numpy weren't
       installed, leaving ``ppr = {}`` so the sum was always 0.

    With 100 callers in the fixture and the bug fixed, the personalized
    PageRank sum over the affected set MUST be > 0 — either via the
    scipy/numpy path (full PageRank) or via the
    ``personalized_pagerank`` degree-based fallback.
    """
    monkeypatch.chdir(high_fan_in_project)
    result = invoke_cli(
        cli_runner,
        # Pull the full blast radius (no caller cap) so we always have
        # dependents under either backend.
        ["impact", "hub", "--depth", "10", "--max-callers", "0"],
        cwd=high_fan_in_project,
        json_mode=True,
    )
    data = parse_json_output(result, "impact")
    summary = data["summary"]

    # Sanity: there must actually be dependents — the fixture has 100
    # callers, so this fails fast if the index is broken.
    assert summary["affected_symbols"] > 0, f"Expected affected_symbols > 0 in high-fan-in fixture, got {summary}"

    # The bug — weighted_impact stayed 0 even with 100 affected symbols.
    assert summary["weighted_impact"] > 0, (
        "weighted_impact must be > 0 when there are real dependents. "
        f"Got {summary['weighted_impact']!r} with "
        f"{summary['affected_symbols']} affected_symbols. "
        "If this fails, check both the rounding precision (4 -> 6 decimals "
        "in cmd_impact.py) and the ImportError fallback for "
        "personalized_pagerank."
    )

    # The mirror at top-level must agree.
    assert data["weighted_impact"] > 0, (
        f"Top-level weighted_impact mirror must also be > 0; got {data['weighted_impact']!r}"
    )

    # Definition sidecar must still be stamped (regression guard for the
    # W331 sidecar wiring, which the round-precision fix is paired with).
    assert summary.get("weighted_impact_definition"), "weighted_impact_definition sidecar missing from summary"


def test_impact_truncation_flag(cli_runner, high_fan_in_project, monkeypatch):
    """When truncated, envelope contains ``truncated: true`` AND
    ``partial_success: true`` AND a ``state`` field naming the cap.

    The verdict string must also surface the partial-result note so
    text-only consumers see the truncation.
    """
    monkeypatch.chdir(high_fan_in_project)
    result = invoke_cli(
        cli_runner,
        ["impact", "hub", "--max-callers", "5", "--depth", "10"],
        cwd=high_fan_in_project,
        json_mode=True,
    )
    data = parse_json_output(result, "impact")
    summary = data["summary"]

    # All three flags must be present and truthy.
    assert summary.get("truncated") is True
    assert summary.get("partial_success") is True
    assert summary.get("state") in ("caller_cap", "depth_cap", "timeout"), (
        f"Expected state in (caller_cap, depth_cap, timeout), got {summary.get('state')}"
    )
    # Top-level mirrors (envelope-level consumers also see truncation).
    assert data.get("truncated") is True
    assert data.get("partial_success") is True
    # Verdict must name a concrete limit for the agent to act on.
    assert "partial" in summary["verdict"].lower()
    # ``limits`` block must echo what was applied.
    assert "limits" in summary
    assert summary["limits"]["max_callers"] == 5
