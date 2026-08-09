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

W1448-followup — the disclosure existed on only two of the three channels.
Every test in the original file went through ``invoke_cli(["--json", …])``, so
nothing exercised ``--sarif``, and ``roam --sarif health`` shipped 50 of 60
god components to Code Scanning with no cap notice anywhere: no
``invocations``, no ``toolExecutionNotifications``, nothing on stderr. That
output is uploaded by ``github/codeql-action/upload-sarif`` in the composite
action and redirected to a file in five bundled CI templates, so a consumer
saw a complete-looking census that was missing ten findings. The channel tests
below close that: the SARIF document now carries the same ``warnings_out``
bucket the JSON and text channels publish, projected through the canonical
``with_sarif_disclosures`` adapter.
"""

from __future__ import annotations

import json
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


# ---------------------------------------------------------------------------
# W1448-followup — the OTHER two channels
#
# The disclosure is only worth anything on the channel a consumer reads. These
# fixtures deliberately re-run the command per output mode rather than reusing
# ``seeded_health``, because the defect was precisely that one mode took a
# different code path out of the same prologue.
# ---------------------------------------------------------------------------


def _sarif_notifications(document: dict) -> list[str]:
    """Every producer-advisory notification text in a SARIF document."""
    texts: list[str] = []
    for run in document.get("runs") or []:
        for invocation in run.get("invocations") or []:
            for note in invocation.get("toolExecutionNotifications") or []:
                texts.append(str((note.get("message") or {}).get("text", "")))
    return texts


@pytest.fixture
def seeded_sarif(cli_runner, indexed_project):
    """SARIF document for a project whose populations exceed both caps."""
    god_pop, bn_pop = _seed_graph_metrics(indexed_project)
    result = invoke_cli(cli_runner, ["--sarif", "health"], cwd=indexed_project)
    assert result.exit_code == 0, result.output
    return json.loads(result.stdout), god_pop, bn_pop, result


def test_sarif_channel_discloses_the_cap(seeded_sarif) -> None:
    """THE DEFECT: 50 alerts drawn from 60 findings, published as a census.

    A Code Scanning consumer has no other surface to learn this from — SARIF
    results carry no population, and the run exits 0 with an empty stderr.
    """
    document, god_pop, _bn_pop, _result = seeded_sarif
    texts = _sarif_notifications(document)
    assert any("health_god_components_list_capped" in t for t in texts), (
        f"SARIF published no cap disclosure; notifications were {texts!r}"
    )
    # The marker must name the population as well as the cap, or the reader
    # learns only that something was dropped and not how much.
    assert any(f"showing_top_{_GOD_COMPONENT_LIST_LIMIT}_of_{god_pop}" in t for t in texts), texts


def test_sarif_channel_publishes_every_marker_the_json_channel_does(cli_runner, indexed_project) -> None:
    """Channel parity, asserted against the JSON envelope rather than a literal.

    Pinning SARIF to a hard-coded marker list would let the two drift apart
    the next time a truncation site is added. Compare them.
    """
    _god_pop, _bn_pop = _seed_graph_metrics(indexed_project)
    json_result = invoke_cli(cli_runner, ["--json", "health"], cwd=indexed_project, json_mode=True)
    data = parse_json_output(json_result, "health")
    expected = [w for w in ((data.get("summary") or {}).get("warnings_out") or []) if "_list_capped" in w]
    assert expected, "fixture did not saturate a cap — the parity assertion would be vacuous"

    sarif_result = invoke_cli(cli_runner, ["--sarif", "health"], cwd=indexed_project)
    texts = _sarif_notifications(json.loads(sarif_result.stdout))
    for marker in expected:
        assert marker in texts, f"{marker!r} is on the JSON channel but not the SARIF one; got {texts!r}"


def test_sarif_channel_also_writes_the_cap_to_stderr(seeded_sarif) -> None:
    """Parity with the text channel, without touching stdout.

    ``echo_text_warnings`` writes to stderr by design, so a pipeline doing
    ``roam --sarif health > file`` still gets a human-visible notice and the
    redirected document stays byte-identical to what the assertions above read.
    """
    _document, _god_pop, _bn_pop, result = seeded_sarif
    assert "health_god_components_list_capped" in result.stderr, result.stderr
    assert "health_god_components_list_capped" not in result.stdout.split('"results"')[0]


def test_sarif_results_are_still_capped(seeded_sarif) -> None:
    """NEGATIVE CONTROL — disclosure must not have become "upload everything".

    Uncapping would change the reported issue count on every repo at once,
    making every stored ``--baseline`` snapshot read as a phantom regression,
    and would flood Code Scanning with thousands of INFO alerts on any large
    codebase. The cap is legitimate; only its silence was the defect.
    """
    document, god_pop, _bn_pop, _result = seeded_sarif
    god_results = [r for r in document["runs"][0]["results"] if r.get("ruleId") == "health/god-component"]
    assert len(god_results) <= _GOD_COMPONENT_LIST_LIMIT
    assert len(god_results) < god_pop, "the cap stopped being a cap"


def test_sarif_run_still_exits_zero_when_the_cap_fires(seeded_sarif) -> None:
    """NEGATIVE CONTROL — this finding is disclosure, not refusal.

    All five bundled CI templates run ``roam --sarif health > file`` inside
    pipelines that are or may be ``set -e``. Exiting non-zero on a capped list
    would break the pipeline — and the SARIF upload — on every repository with
    more than 50 god components, i.e. most real ones. The alerts that ARE
    emitted are all correct.
    """
    _document, _god_pop, _bn_pop, result = seeded_sarif
    assert result.exit_code == 0


def test_unsaturated_sarif_carries_no_invocations_block(cli_runner, indexed_project) -> None:
    """NEGATIVE CONTROL — hash stability for every clean repo.

    ``health_to_sarif`` documents a byte-identical-without-kwargs invariant.
    ``with_sarif_disclosures`` returns the document untouched on an empty
    bucket; a hand-rolled version that always appended the block would move
    the bytes for every uncapped repository.
    """
    result = invoke_cli(cli_runner, ["--sarif", "health"], cwd=indexed_project)
    assert result.exit_code == 0, result.output
    document = json.loads(result.stdout)
    run = document["runs"][0]
    assert "invocations" not in run, run.get("invocations")
    assert _sarif_notifications(document) == []
    assert "list_capped" not in result.stderr


def test_text_channel_names_the_cap_and_the_population(cli_runner, indexed_project) -> None:
    """The third channel, asserted rather than assumed.

    The text renderer already disclosed this; the guard is here so the three
    channels are covered by one file and none can regress unobserved.
    """
    god_pop, _bn_pop = _seed_graph_metrics(indexed_project)
    result = invoke_cli(cli_runner, ["health"], cwd=indexed_project)
    blob = result.output + result.stderr
    assert f"of {god_pop} god components" in blob or f"of_{god_pop}" in blob, blob[:800]
