"""W1451 — the health score has ONE implementation, and it cannot drift again.

The defect
----------
``roam health`` and ``metrics_history.collect_metrics`` each computed the
0-100 composite health score. ``collect_metrics`` is what gets written into
the ``snapshots`` table; ``roam health --baseline <ref>`` compares *today's*
``cmd_health`` number against *yesterday's* stored ``collect_metrics`` number.

Measured on this repository's own index, at the same instant, pre-fix::

    roam health                     score 71    tangle_ratio 0.0    cycles 0
    metrics_history.collect_metrics score 64    tangle_ratio 3.6    cycles 39

So every ``roam health --baseline`` run reported a phantom **+7 improvement**
and **39 "fixed" cycles** on a repository where nothing had changed. Not a
crash — a confident false report, which is the worse failure.

Root cause: ``cmd_health`` filtered the tangle-ratio numerator to *actionable*
SCCs (>= 2 files, no test file). Nothing else in the estate applies that
filter — not ``TANGLE_RATIO_DEFINITION``, not ``roam fingerprint``, not the
snapshot writer. ``collect_metrics`` was right; ``cmd_health`` was wrong.

What these tests lock down
--------------------------
1. Both call sites produce the SAME score and the SAME tangle ratio on one DB.
2. Tangle ratio counts ALL non-trivial SCCs (the discriminating fixture is a
   same-file cycle: non-actionable, so pre-fix ``cmd_health`` reported 0.0).
3. ``roam health --baseline`` reports no phantom deltas against a snapshot
   written from the same unchanged index.
4. The geometric-mean composition exists in exactly one source file — the
   guard against the *next* copy, which is what makes the fix permanent.
5. NEGATIVE CONTROL: the unified function still returns a sane, non-constant,
   discriminating score. "Make both sides return 0" must not pass.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
from conftest import git_init, index_in_process, invoke_cli, parse_json_output  # noqa: E402

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _write_project(root: Path, *, tangled: bool) -> Path:
    """Build a small indexable Python project.

    When *tangled*, ``knot.py`` contains a mutual-recursion pair inside ONE
    file. That is a non-trivial SCC (size 2) but is NOT "actionable" — it
    spans a single file — which is precisely the case the pre-fix
    ``cmd_health`` numerator dropped while the snapshot writer kept it. It is
    the discriminating input: with it, the two implementations disagreed.
    """
    root.mkdir(parents=True, exist_ok=True)
    (root / ".gitignore").write_text(".roam/\n")
    src = root / "src"
    src.mkdir()

    (src / "models.py").write_text(
        "class User:\n"
        "    def __init__(self, name):\n"
        "        self.name = name\n"
        "\n"
        "    def greet(self):\n"
        "        return f'hi {self.name}'\n"
    )
    (src / "service.py").write_text(
        "from models import User\n"
        "\n"
        "\n"
        "def make_user(name):\n"
        "    return User(name)\n"
        "\n"
        "\n"
        "def greet_user(name):\n"
        "    return make_user(name).greet()\n"
    )
    (src / "app.py").write_text("from service import greet_user\n\n\ndef main():\n    return greet_user('world')\n")

    if tangled:
        (src / "knot.py").write_text(
            "def ping(n):\n"
            "    if n <= 0:\n"
            "        return 0\n"
            "    return pong(n - 1)\n"
            "\n"
            "\n"
            "def pong(n):\n"
            "    if n <= 0:\n"
            "        return 1\n"
            "    return ping(n - 1)\n"
        )

    git_init(root)
    _out, code = index_in_process(root)
    assert code == 0, f"index failed: {_out}"
    return root


@pytest.fixture
def tangled_project(tmp_path):
    """Project containing a same-file (non-actionable) dependency cycle."""
    return _write_project(tmp_path / "tangled", tangled=True)


@pytest.fixture
def healthy_project(tmp_path):
    """Structurally clean project — no cycles at all. Negative-control base."""
    return _write_project(tmp_path / "healthy", tangled=False)


def _reset_graph_cache() -> None:
    """Drop the process-wide symbol-graph cache.

    ``roam.graph.builder`` memoises the built graph on ``id(conn)``. Several
    tests here measure TWO different projects inside one test function, and
    CPython readily reuses the id of a closed ``sqlite3.Connection`` — so the
    second project can be served the first project's graph and silently score
    as if it had no cycles. conftest's ``_clear_graph_cache_between_tests``
    only covers test boundaries, not within-test project switches.
    """
    from roam.graph.builder import clear_graph_cache

    clear_graph_cache()


def _live_health(cli_runner, project) -> dict:
    """``roam health --json`` summary for *project*."""
    _reset_graph_cache()
    result = invoke_cli(cli_runner, ["health"], cwd=project, json_mode=True)
    return parse_json_output(result, "health")["summary"]


def _stored_metrics(project) -> dict:
    """What ``collect_metrics`` would write into ``snapshots`` for *project*."""
    import os

    from roam.commands.metrics_history import collect_metrics
    from roam.db.connection import open_db

    _reset_graph_cache()
    old = os.getcwd()
    try:
        os.chdir(str(project))
        with open_db() as conn:
            return collect_metrics(conn)
    finally:
        os.chdir(old)


# ---------------------------------------------------------------------------
# 1. The core invariant: one DB, one score.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("fixture_name", ["tangled_project", "healthy_project"])
def test_live_and_stored_health_score_agree(cli_runner, request, fixture_name):
    """``roam health``'s score == the score written into ``snapshots``.

    This is the whole defect in one assertion. ``roam health --baseline``
    subtracts one of these from the other; if they disagree, the difference is
    reported to the user as change they caused.

    Pre-fix this failed on ``tangled_project``: live 98 vs stored 50 — the
    same shape as 71 vs 64 on the roam-code index itself, amplified because
    the fixture is small enough for one cycle to dominate.
    """
    project = request.getfixturevalue(fixture_name)
    live = _live_health(cli_runner, project)
    stored = _stored_metrics(project)

    assert live["health_score"] == stored["health_score"], (
        f"health_score divergence on {fixture_name}: "
        f"`roam health` says {live['health_score']}, the stored snapshot says "
        f"{stored['health_score']}. Every `roam health --baseline` run would "
        f"report a phantom {live['health_score'] - stored['health_score']:+d} "
        f"delta on an unchanged repository."
    )


@pytest.mark.parametrize("fixture_name", ["tangled_project", "healthy_project"])
def test_live_and_stored_tangle_ratio_agree(cli_runner, request, fixture_name):
    """Both call sites measure the same tangle ratio.

    Pre-fix ``cmd_health`` filtered the numerator to actionable SCCs and the
    snapshot writer did not, so on ``tangled_project`` this was 0.0 vs 22.2.
    """
    project = request.getfixturevalue(fixture_name)
    live = _live_health(cli_runner, project)
    stored = _stored_metrics(project)

    assert live["tangle_ratio"] == stored["tangle_ratio"], (
        f"tangle_ratio divergence on {fixture_name}: `roam health` says "
        f"{live['tangle_ratio']}, the stored snapshot says {stored['tangle_ratio']}."
    )


def test_tangle_ratio_counts_non_actionable_cycles(cli_runner, tangled_project):
    """A same-file cycle IS cyclic coupling and MUST move the tangle ratio.

    ``TANGLE_RATIO_DEFINITION`` says "symbols inside non-trivial SCCs" — there
    is no actionability qualifier, and ``roam fingerprint`` applies none. This
    is the assertion that fails hardest against pre-fix ``cmd_health``, which
    reported ``tangle_ratio 0.0`` while simultaneously reporting
    ``cycles_total: 1`` in the same envelope.
    """
    live = _live_health(cli_runner, tangled_project)

    assert live["cycles_total"] >= 1, "fixture must actually contain a cycle"
    assert live["cycles_actionable"] == 0, (
        "fixture must contain a NON-actionable cycle — that is the case that discriminates the two implementations"
    )
    assert live["tangle_ratio"] > 0.0, (
        f"cycles_total={live['cycles_total']} but tangle_ratio="
        f"{live['tangle_ratio']}. A command cannot report cycles and zero "
        f"tangle in the same envelope."
    )


def test_health_and_fingerprint_report_the_same_tangle(cli_runner, tangled_project):
    """`roam health`'s percent == `roam fingerprint`'s fraction * 100.

    Both commands stamp a definition that says "symbols inside non-trivial
    SCCs", and both are read by the same agent in the same session. The two
    surfaces use different UNITS by design — the percent feeds
    ``.roam-gates.yml``'s ``tangle_max`` and the score's sigmoid scale, the
    fraction feeds ``fingerprint --compare``'s ``max_range: 1.0`` — but they
    must never differ in what they MEASURE.

    Pre-fix they did: ``health`` said 0.0 and ``fingerprint`` said 0.0359 for
    the roam-code index at the same instant.
    """
    health = _live_health(cli_runner, tangled_project)

    _reset_graph_cache()
    fp_result = invoke_cli(cli_runner, ["fingerprint"], cwd=tangled_project, json_mode=True)
    fp_fraction = parse_json_output(fp_result, "fingerprint")["summary"]["tangle_ratio"]

    assert health["tangle_ratio"] == pytest.approx(fp_fraction * 100, abs=0.1), (
        f"`roam health` reports tangle_ratio {health['tangle_ratio']}% but "
        f"`roam fingerprint` reports {fp_fraction} ({fp_fraction * 100}%) for the "
        f"same index. One of them has redefined the measurement behind a shared name."
    )


# ---------------------------------------------------------------------------
# 2. End-to-end: the false-clean the user actually saw.
# ---------------------------------------------------------------------------


def test_baseline_against_own_snapshot_reports_no_phantom_change(cli_runner, tangled_project):
    """Snapshot the index, change nothing, and ``--baseline`` must report nothing.

    This is the user-visible bug end to end. ``roam trends --save`` writes a
    snapshot via ``collect_metrics``; ``roam health --baseline last`` then
    diffs the live numbers against it. With no edit in between, EVERY delta
    must be zero. Pre-fix this fixture reported a +48 score delta and a
    "fixed" cycle, because the two sides counted different populations —
    the same shape as the +7 and the 39 phantom "fixed" cycles the roam-code
    index itself reported.
    """
    save = invoke_cli(cli_runner, ["trends", "--save"], cwd=tangled_project)
    assert save.exit_code == 0, f"trends --save failed: {save.output}"

    result = invoke_cli(cli_runner, ["health", "--baseline", "last"], cwd=tangled_project, json_mode=True)
    data = parse_json_output(result, "health")
    summary = data["summary"]
    delta = data["delta"]

    assert summary["verdict"] != "DEGRADED", f"no snapshot was found: {summary}"
    assert summary["score_delta"]["health_score"] == 0, (
        f"phantom score delta {summary['score_delta']['health_score']:+d} against a "
        f"snapshot of the SAME unchanged index"
    )
    assert delta["fixed_findings"] == [], (
        f"reported fixes nobody made: {delta['fixed_findings']}. The classic "
        f"shape is a 'cycles' entry going N -> 0 because the live side counted "
        f"actionable cycles and the stored column counted all of them."
    )
    assert delta["new_findings"] == [], f"phantom regressions: {delta['new_findings']}"
    assert delta["regressed"] == [], f"phantom regressions: {delta['regressed']}"


# ---------------------------------------------------------------------------
# 3. NEGATIVE CONTROL — "make both sides return 0" must not pass.
# ---------------------------------------------------------------------------


def test_unified_score_is_sane_on_a_healthy_project(cli_runner, healthy_project):
    """A clean project scores HIGH. Rules out collapsing both sides to a constant.

    Every agreement assertion above is satisfiable by returning 0 (or any
    constant) from both call sites. This test — plus the discrimination test
    below — is what makes those assertions mean something.
    """
    live = _live_health(cli_runner, healthy_project)
    stored = _stored_metrics(healthy_project)

    assert live["cycles_total"] == 0, "healthy fixture must be acyclic"
    assert live["tangle_ratio"] == 0.0
    for label, score in (("live", live["health_score"]), ("stored", stored["health_score"])):
        assert score is not None, f"{label} score is None"
        assert 60 <= score <= 100, (
            f"{label} health_score {score} is not a sane reading for a clean, "
            f"acyclic 3-file project — the unified function has been broken or "
            f"stubbed, not merely unified"
        )


def test_unified_score_discriminates_tangled_from_healthy(cli_runner, tangled_project, healthy_project):
    """The score must still MOVE when the codebase gets worse.

    A constant-returning implementation satisfies every equality assertion in
    this file. This one it cannot: adding a dependency cycle must lower the
    score on BOTH call sites, and both must move together.
    """
    healthy_live = _live_health(cli_runner, healthy_project)["health_score"]
    tangled_live = _live_health(cli_runner, tangled_project)["health_score"]
    healthy_stored = _stored_metrics(healthy_project)["health_score"]
    tangled_stored = _stored_metrics(tangled_project)["health_score"]

    assert tangled_live < healthy_live, (
        f"adding a cycle did not lower the live score ({healthy_live} -> {tangled_live}); "
        f"the score is constant or the tangle factor is disconnected"
    )
    assert tangled_stored < healthy_stored, (
        f"adding a cycle did not lower the stored score ({healthy_stored} -> {tangled_stored})"
    )
    # And the two call sites must move by the SAME amount.
    assert (healthy_live - tangled_live) == (healthy_stored - tangled_stored)


def test_file_health_average_of_zero_is_not_rewritten_as_perfect():
    """A measured ``AVG(file_stats.health_score) == 0`` scores 0, not 1.0.

    ``collect_metrics`` used ``(avg or 10) / 10.0``, so a genuine zero average
    fell through the falsy-check and became a PERFECT file-health factor while
    ``cmd_health`` scored it 0.0 — a second, quieter copy of the same
    false-clean shape. The unified helper distinguishes "no rows" (None ->
    neutral 1.0) from "measured zero" (0.0 -> 0.0).
    """
    from roam.quality.health_score import file_health_factor

    assert file_health_factor(None) == 1.0, "no data must be neutral, not damning"
    assert file_health_factor(0.0) == 0.0, "a measured zero must not be rewritten as perfect"
    assert file_health_factor(10.0) == 1.0
    assert file_health_factor(5.0) == pytest.approx(0.5)


# ---------------------------------------------------------------------------
# 4. The guard against the NEXT copy.
# ---------------------------------------------------------------------------


def test_geometric_mean_composition_exists_in_exactly_one_module():
    """Only ONE file in ``src/roam`` may compose the weighted geometric mean.

    De-duplicating today does not stop someone re-inlining the formula
    tomorrow, and the re-inlined copy is invisible precisely because nothing
    compares the two. This test is that comparison. If it fails, do not add
    the new file to an allowlist — route it through
    ``roam.quality.health_score.compute_health_score``.
    """
    from tests._helpers.repo_root import repo_root

    src = repo_root() / "src" / "roam"
    if not src.is_dir():
        pytest.skip("source tree not available")

    needle = "math.log(max(h, 1e-9))"
    offenders = sorted(
        str(p.relative_to(src)).replace("\\", "/")
        for p in src.rglob("*.py")
        if needle in p.read_text(encoding="utf-8", errors="replace")
    )

    assert offenders == ["quality/health_score.py"], (
        f"the health-score geometric mean is implemented in {len(offenders)} "
        f"place(s): {offenders}. It must exist only in "
        f"quality/health_score.py — two copies of a metric always drift, and "
        f"the drift is invisible because nothing compares them (W1451)."
    )


def test_both_call_sites_import_the_canonical_scorer():
    """``cmd_health`` and ``metrics_history`` both route through the one module.

    Complements the source-grep above: that one proves no second *formula*
    exists, this one proves the two call sites the ``--baseline`` comparison
    spans are actually wired to the shared one.
    """
    import inspect

    from roam.commands import cmd_health, metrics_history
    from roam.quality import health_score as canonical

    for module in (cmd_health, metrics_history):
        source = inspect.getsource(module)
        assert "roam.quality.health_score" in source, (
            f"{module.__name__} does not import the canonical scorer; a private re-implementation has crept back in"
        )

    # Both must reach the identical function object.
    assert cmd_health._compute_canonical_health_score is canonical.compute_health_score
    assert metrics_history.compute_health_score is canonical.compute_health_score
    assert cmd_health._compute_canonical_tangle_ratio is canonical.compute_tangle_ratio
    assert metrics_history.compute_tangle_ratio is canonical.compute_tangle_ratio
