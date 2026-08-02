"""W1448 — ``roam health`` must not present a SQL ``LIMIT`` as a population.

The god-component and bottleneck sections render a TOP-N LIST. The caps were
inline literals passed to ``LIMIT ?``, so once saturated the returned list
length was indistinguishable from a measured count. Measured on this
repository: ``summary.god_components: 50`` against a true population of 553,
``bottlenecks: 15`` against 3019, and ``issue_count: 65`` — literally 50 + 15,
the sum of two LIMIT constants — all emitted with
``summary.partial_success: false`` and an empty ``preserved_list_truncations``.

Nothing upstream could have caught it: ``output.formatter`` records only the
truncations IT performs, and this one happens in SQL, beneath that layer. The
envelope asserted completeness in good faith.

Both thresholds are structurally unreachable, so this is not a quirk of one
repo: the 50th-ranked symbol here has degree 142 against a threshold of 20, and
betweenness is unnormalised (raw shortest-path counts) so the 15th-ranked value
is ~1.5e7 against a threshold of 0.5. The cap, never the threshold, decides.

These tests seed a population deliberately larger than the cap rather than
relying on this repository staying large, so they keep discriminating on any
checkout.
"""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
from conftest import invoke_cli, parse_json_output  # noqa: E402

from roam.commands.cmd_health import (  # noqa: E402
    _BOTTLENECK_LIST_LIMIT,
    _GOD_COMPONENT_LIST_LIMIT,
    _GOD_COMPONENT_MIN_DEGREE,
)

# Comfortably above both caps so saturation is unambiguous.
_SEEDED_GOD_COMPONENTS = _GOD_COMPONENT_LIST_LIMIT + 23
_SEEDED_BOTTLENECKS = _BOTTLENECK_LIST_LIMIT + 41


def _seed_graph_metrics(project: Path) -> tuple[int, int]:
    """Insert symbols whose degree/betweenness exceed the health thresholds.

    Returns ``(god_population, bottleneck_population)`` counted the same way
    the command counts them, so the test compares against the database rather
    than against its own arithmetic.
    """
    db = project / ".roam" / "index.db"
    conn = sqlite3.connect(db)
    try:
        conn.execute("INSERT OR IGNORE INTO files (path, language) VALUES ('seeded/w1448.py', 'python')")
        file_id = conn.execute("SELECT id FROM files WHERE path = 'seeded/w1448.py'").fetchone()[0]

        for i in range(_SEEDED_GOD_COMPONENTS):
            cur = conn.execute(
                "INSERT INTO symbols (file_id, name, qualified_name, kind, line_start) VALUES (?,?,?,?,?)",
                (file_id, f"w1448_god_{i}", f"seeded.w1448_god_{i}", "function", i + 1),
            )
            # Degree far above the threshold so ordering is deterministic and
            # the cap — not the threshold — is what stops the cursor.
            conn.execute(
                "INSERT INTO graph_metrics (symbol_id, in_degree, out_degree, betweenness) VALUES (?,?,?,0)",
                (cur.lastrowid, 500 + i, 500 + i),
            )

        for i in range(_SEEDED_BOTTLENECKS):
            cur = conn.execute(
                "INSERT INTO symbols (file_id, name, qualified_name, kind, line_start) VALUES (?,?,?,?,?)",
                (file_id, f"w1448_bn_{i}", f"seeded.w1448_bn_{i}", "function", 10_000 + i),
            )
            conn.execute(
                "INSERT INTO graph_metrics (symbol_id, in_degree, out_degree, betweenness) VALUES (?,0,0,?)",
                (cur.lastrowid, 1_000_000.0 + i),
            )
        conn.commit()

        god_pop = conn.execute(
            "SELECT COUNT(*) FROM graph_metrics WHERE (COALESCE(in_degree,0) + COALESCE(out_degree,0)) > ?",
            (_GOD_COMPONENT_MIN_DEGREE,),
        ).fetchone()[0]
        bn_pop = conn.execute("SELECT COUNT(*) FROM graph_metrics WHERE betweenness > 0").fetchone()[0]
        return god_pop, bn_pop
    finally:
        conn.close()


@pytest.fixture
def seeded_health(cli_runner, indexed_project):
    """Health envelope for a project with populations exceeding both caps."""
    god_pop, bn_pop = _seed_graph_metrics(indexed_project)
    result = invoke_cli(cli_runner, ["--json", "health"], cwd=indexed_project, json_mode=True)
    return parse_json_output(result, "health"), god_pop, bn_pop


def test_seeding_actually_saturates_both_caps(seeded_health) -> None:
    """Guard the guard: if this fails, every other test here is vacuous."""
    _, god_pop, bn_pop = seeded_health
    assert god_pop > _GOD_COMPONENT_LIST_LIMIT, "seed did not exceed the god-component cap"
    assert bn_pop > _BOTTLENECK_LIST_LIMIT, "seed did not exceed the bottleneck cap"


def test_god_population_is_measured_not_the_cap(seeded_health) -> None:
    """The published population must be the COUNT, never the LIMIT."""
    data, god_pop, _ = seeded_health
    thresholds = data.get("god_component_thresholds")
    assert thresholds is not None, "god_component_thresholds absent — population is unpublished again"
    assert thresholds["population"] == god_pop
    assert thresholds["population"] != _GOD_COMPONENT_LIST_LIMIT, (
        "population equals the LIMIT — the cap is being reported as a count"
    )
    assert thresholds["list_limit"] == _GOD_COMPONENT_LIST_LIMIT


def test_bottleneck_population_is_measured_not_the_cap(seeded_health) -> None:
    data, _, bn_pop = seeded_health
    thresholds = data["bottleneck_thresholds"]
    assert thresholds["population"] == bn_pop
    assert thresholds["population"] != _BOTTLENECK_LIST_LIMIT
    assert thresholds["list_limit"] == _BOTTLENECK_LIST_LIMIT


def test_saturated_lists_are_disclosed_not_silently_capped(seeded_health) -> None:
    """The defect proper: completeness must not be asserted over a capped list."""
    data, god_pop, bn_pop = seeded_health
    summary = data["summary"]
    assert summary.get("partial_success") is True, "health capped both lists and still reported partial_success=false"
    warnings = " ".join(summary.get("warnings_out") or [])
    assert "health_god_components_list_capped" in warnings
    assert "health_bottlenecks_list_capped" in warnings
    # The marker must carry the true population, or a reader learns only that
    # something was capped and not by how much.
    assert str(god_pop) in warnings
    assert str(bn_pop) in warnings


def test_rendered_lists_are_still_capped(seeded_health) -> None:
    """NEGATIVE CONTROL — disclosure must not have become "return everything".

    Uncapping would make the envelope honest and enormous. The point is a
    capped list that SAYS it is capped.
    """
    data, _, _ = seeded_health
    counts = data.get("list_counts") or {}
    god_shown = counts.get("god_components", len(data.get("god_components") or []))
    bn_shown = counts.get("bottlenecks", len(data.get("bottlenecks") or []))
    assert god_shown <= _GOD_COMPONENT_LIST_LIMIT
    assert bn_shown <= _BOTTLENECK_LIST_LIMIT


def test_unsaturated_repo_makes_no_truncation_claim(cli_runner, indexed_project) -> None:
    """NEGATIVE CONTROL — no false disclosure on a small project.

    A gate that always says "capped" is as useless as one that never does.
    The bare fixture has far fewer than 50 god components.
    """
    result = invoke_cli(cli_runner, ["--json", "health"], cwd=indexed_project, json_mode=True)
    data = parse_json_output(result, "health")
    warnings = " ".join((data.get("summary") or {}).get("warnings_out") or [])
    assert "health_god_components_list_capped" not in warnings
    assert "health_bottlenecks_list_capped" not in warnings
