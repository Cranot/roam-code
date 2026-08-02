"""W1452 — sampled betweenness must be reproducible for an unchanged graph.

``nx.betweenness_centrality(G, k=...)`` is an APPROXIMATION over k randomly
drawn source nodes. Called with ``seed=None`` it draws from the process-global
``random`` state, so two runs over a byte-identical graph return different
numbers. Two production sites did exactly that:

* ``roam.graph.pagerank.compute_centrality`` — writes ``graph_metrics.betweenness``
  on every ``roam index``. That column feeds ``roam health``'s bottleneck
  population, its p70/p90 severity bands, and ``roam health --baseline`` deltas.
* ``roam.graph.simulate.compute_graph_metrics`` — the ``bottlenecks`` count and
  the health score derived from it, which every architecture "what-if" delta is
  measured against.

Measured on roam-code's own 44,688-node symbol graph (k=1,056 pivots), two
back-to-back unseeded runs over the SAME graph disagreed on 3,904 nodes, moved
individual values by -6% to +54%, moved the positive-betweenness population
from 3,446 to 3,566 symbols, and moved the p70/p90 severity thresholds by
+11.5% / +9.4%. A score input that moves without a code change is not a metric,
and it manufactures phantom deltas in the one feature meant to detect real
change.

The tests below pin BOTH directions:

* the positive tests assert the production paths are now reproducible;
* ``test_negative_control_*`` asserts that, on the very same graph and the very
  same k, an explicitly UNSEEDED call still varies — proving the equality
  assertions above can actually fail, rather than passing because the graph is
  too small to sample or because the comparison is vacuous;
* ``test_no_sampled_betweenness_call_site_omits_seed`` is the structural guard:
  it fails at source level if any present or FUTURE call anywhere in
  ``src/roam`` passes ``k`` without ``seed``.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

import networkx as nx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _helpers.repo_root import repo_root  # noqa: E402

from roam.graph.pagerank import BETWEENNESS_SEED, compute_centrality
from roam.graph.simulate import compute_graph_metrics

# n > 1000 so compute_centrality takes its SAMPLED branch (k=200 of 1050
# nodes, ~19%). At n <= 1000 it passes k=n, every node is a pivot, and the
# result is already reproducible — a smaller graph would make these tests
# pass for the wrong reason. ``test_exact_branch_needs_no_sampling`` pins
# that boundary so the size choice here cannot silently rot.
_N = 1050
_M = 3200


def _graph() -> nx.DiGraph:
    """A fixed random graph — same every call, so any variation is the sampler's."""
    return nx.gnm_random_graph(_N, _M, seed=13, directed=True)


def _sampled_k(n: int) -> int:
    """The k ``compute_centrality`` picks for *n* nodes (mirrors the production rule)."""
    return n if n <= 1000 else min(n, max(200, int(n**0.5 * 5)))


def test_compute_centrality_betweenness_is_reproducible():
    """Same graph in, byte-identical betweenness out — across two calls."""
    G = _graph()
    first = {nid: c["betweenness"] for nid, c in compute_centrality(G).items()}
    second = {nid: c["betweenness"] for nid, c in compute_centrality(_graph()).items()}

    assert first.keys() == second.keys()
    differing = [nid for nid in first if first[nid] != second[nid]]
    assert not differing, (
        f"{len(differing)} of {len(first)} nodes changed betweenness between two "
        f"runs over an identical graph — the pivot sample is unseeded again. "
        f"Worst: {max((abs(first[n] - second[n]), n) for n in differing)}"
    )
    # Guard against a vacuous pass: the metric must actually carry signal.
    assert sum(1 for v in first.values() if v > 0) > 10


def test_debt_score_is_reproducible():
    """betweenness is 25% of debt_score — the whole vector must settle too."""
    first = compute_centrality(_graph())
    second = compute_centrality(_graph())
    assert [first[n]["debt_score"] for n in sorted(first)] == [second[n]["debt_score"] for n in sorted(second)]


def test_compute_graph_metrics_bottleneck_count_is_reproducible():
    """simulate.py's bottleneck count (and the health score built on it) settle.

    Weak on its own, and deliberately labelled as such: ``bn_count`` counts the
    nodes above the 90th percentile OF ITS OWN distribution, so it lands on
    ~10% of the graph whatever the pivot draw was — measured 104/104 on eight
    unseeded runs of this graph. The count is not what the missing seed broke;
    ``test_simulate_bottleneck_membership_is_reproducible`` below tests what
    was actually moving.
    """
    first = compute_graph_metrics(_graph())
    second = compute_graph_metrics(_graph())
    assert first["bottlenecks"] == second["bottlenecks"], (
        f"bottleneck count moved {first['bottlenecks']} -> {second['bottlenecks']} "
        "for an identical graph — simulate.py's sampled betweenness is unseeded again"
    )
    assert first["health_score"] == second["health_score"]
    assert first["bottlenecks"] > 0  # not a vacuous zero-vs-zero comparison


def _simulate_bottleneck_members(G: nx.DiGraph, *, seed) -> frozenset:
    """Mirror ``compute_graph_metrics``'s bottleneck selection, seed configurable."""
    k = min(len(G), max(50, int(len(G) ** 0.5 * 3)))
    bc = nx.betweenness_centrality(G, k=k, seed=seed)
    values = sorted(bc.values())
    p90 = values[int(len(values) * 0.9)]
    return frozenset(nid for nid, v in bc.items() if v > p90)


def test_simulate_bottleneck_membership_is_reproducible():
    """WHICH nodes simulate.py calls bottlenecks must not be a fresh draw.

    Unseeded, the count held at 104 while the membership overlapped by a
    Jaccard of only 0.29 between two runs on the same graph — ~71% of the
    "bottlenecks" were different symbols for identical input. The count hid it.
    """
    G = _graph()
    first = _simulate_bottleneck_members(G, seed=BETWEENNESS_SEED)
    second = _simulate_bottleneck_members(G, seed=BETWEENNESS_SEED)
    assert first == second
    assert len(first) > 10

    # FALSIFIER: unseeded, the same selection churns — so the equality above
    # is a real constraint and not an artefact of a draw-insensitive graph.
    unseeded = {_simulate_bottleneck_members(G, seed=None) for _ in range(6)}
    assert len(unseeded) > 1, (
        "unseeded bottleneck membership was stable across 6 runs — this test "
        "no longer proves anything; re-tune _N/_M rather than deleting it"
    )


def test_explicit_seed_is_what_makes_it_reproducible():
    """Different seeds give different answers; the same seed always agrees.

    Distinguishes "reproducible because it is seeded" from "reproducible
    because this graph happens to be insensitive to the pivot draw".
    """
    G = _graph()
    k = _sampled_k(len(G))
    assert k < len(G), "graph must be in the sampled regime for this test to mean anything"

    fixed_a = nx.betweenness_centrality(G, k=k, normalized=False, seed=BETWEENNESS_SEED)
    fixed_b = nx.betweenness_centrality(G, k=k, normalized=False, seed=BETWEENNESS_SEED)
    other = nx.betweenness_centrality(G, k=k, normalized=False, seed=BETWEENNESS_SEED + 1)

    assert fixed_a == fixed_b
    assert fixed_a != other, "pivot draw is insensitive to the seed — test proves nothing"


def test_negative_control_unseeded_sampling_does_vary():
    """FALSIFIER: without a seed, the same graph at the same k gives new numbers.

    If this ever fails, the positive assertions above are worthless — they
    would be passing on a graph where the pivot draw does not matter, and a
    regression that deletes ``seed=`` would ship silently.

    Reliability: on this graph shape (1050 nodes, k=200 -> 19% sampling) an
    unseeded call returned a distinct result on 12 of 12 measured runs, so a
    run of 8 collisions is not a realistic flake mode.
    """
    G = _graph()
    k = _sampled_k(len(G))
    results = {
        tuple(v for _, v in sorted(nx.betweenness_centrality(G, k=k, normalized=False).items())) for _ in range(8)
    }
    assert len(results) > 1, (
        "8 unseeded sampled-betweenness runs over the same graph agreed exactly. "
        "The pivot draw is no longer a source of variation here, so the "
        "determinism tests in this module have lost their teeth — re-tune _N/_M "
        "(smaller k relative to n) rather than deleting them."
    )


def test_exact_branch_needs_no_sampling():
    """Pins the n <= 1000 boundary: there k == n, every node is a pivot.

    Documents WHY the tests above use a >1000-node graph. If this rule ever
    changes so that small graphs sample too, the seed becomes load-bearing at
    every size and _N here may be lowered.
    """
    assert _sampled_k(1000) == 1000
    assert _sampled_k(1001) < 1001
    small = nx.gnm_random_graph(300, 900, seed=7, directed=True)
    exact = {
        tuple(v for _, v in sorted(nx.betweenness_centrality(small, k=len(small), normalized=False).items()))
        for _ in range(4)
    }
    assert len(exact) == 1


@pytest.mark.parametrize(
    ("module", "call"),
    [
        ("roam.graph.pagerank", lambda G: compute_centrality(G)),
        ("roam.graph.simulate", lambda G: compute_graph_metrics(G)),
    ],
)
def test_production_call_sites_pass_a_seed(monkeypatch, module, call):
    """Runtime guard on the two sites, complementing the source-level scan.

    ``compute_graph_metrics`` reports only a derived count, so a seed removal
    there is invisible in its output (see the membership test above). Spying on
    the actual kwargs makes it visible.
    """
    import importlib

    mod = importlib.import_module(module)
    seen: list[dict] = []
    real = nx.betweenness_centrality

    def spy(G, **kwargs):
        seen.append(kwargs)
        return real(G, **kwargs)

    monkeypatch.setattr(mod.nx, "betweenness_centrality", spy)
    call(_graph())

    assert seen, f"{module} did not call betweenness_centrality"
    for kwargs in seen:
        if kwargs.get("k") is not None:
            assert kwargs.get("seed") is not None, (
                f"{module} sampled betweenness with k={kwargs['k']} and no seed — "
                "values will differ on every run for unchanged input"
            )
            assert kwargs["seed"] == BETWEENNESS_SEED


_SAMPLED_FNS = {"betweenness_centrality", "edge_betweenness_centrality"}
# W572/W588 — canonical toplevel via git, not a parents[] walk: under
# nested-worktree dispatch parents[1] lands on the worktree root and this
# AST drift guard would scan a `src/roam` that is not the one under test.
_SRC = repo_root() / "src" / "roam"


def _sampled_calls_without_seed(tree: ast.AST) -> list[int]:
    """Line numbers of ``*betweenness_centrality`` calls passing k but not seed."""
    bad = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        fn = node.func
        name = fn.attr if isinstance(fn, ast.Attribute) else getattr(fn, "id", None)
        if name not in _SAMPLED_FNS:
            continue
        kwargs = {kw.arg for kw in node.keywords if kw.arg}
        # k is the 2nd positional parameter of both functions.
        k_given = "k" in kwargs or len(node.args) >= 2
        if k_given and "seed" not in kwargs:
            bad.append(node.lineno)
    return bad


def test_no_sampled_betweenness_call_site_omits_seed():
    """Structural drift guard over the whole package, not just today's two sites.

    Approximate betweenness without a seed is nondeterministic by construction.
    Any new call site that samples must say which draw it wants.
    """
    offenders = []
    for path in sorted(_SRC.rglob("*.py")):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (OSError, SyntaxError):  # pragma: no cover - unreadable/generated file
            continue
        offenders += [f"{path.relative_to(_SRC.parents[1])}:{ln}" for ln in _sampled_calls_without_seed(tree)]

    assert not offenders, "sampled betweenness without seed= (nondeterministic across runs) at: " + ", ".join(offenders)


def test_structural_guard_detects_a_missing_seed():
    """NEGATIVE CONTROL for the guard above — it must reject the pre-fix source."""
    prefix = "import networkx as nx\n"
    assert _sampled_calls_without_seed(ast.parse(prefix + "nx.betweenness_centrality(G, k=k)")) == [2]
    assert _sampled_calls_without_seed(ast.parse(prefix + "nx.betweenness_centrality(G, 200)")) == [2]
    assert _sampled_calls_without_seed(ast.parse(prefix + "nx.edge_betweenness_centrality(G, k=k)")) == [2]
    # ...and accept the compliant shapes.
    assert _sampled_calls_without_seed(ast.parse(prefix + "nx.betweenness_centrality(G, k=k, seed=1)")) == []
    assert _sampled_calls_without_seed(ast.parse(prefix + "nx.betweenness_centrality(G)")) == []


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-v"]))
