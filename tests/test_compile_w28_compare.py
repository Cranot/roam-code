"""W28 — compare_x_vs_y classifier + dispatch + probe tests.

Mirrors the W11/W12/W13 test pattern in
``test_compile_probe_families_w11_w12_w13.py``:

  - 5 POSITIVE prompts that MUST classify as ``compare_x_vs_y``
  - 5 NEGATIVE prompts that must NOT misroute
  - Dispatch-registered sanity test
  - Helper-consistency test (``_is_compare_x_vs_y`` agrees with ``_classify``)
  - Probe-returns-data test (the probe yields a non-empty
    ``compare_x_vs_y_result`` envelope on a known-good comparison)
"""

from __future__ import annotations

import pytest

from roam.plan.compiler import (
    _PROBE_DISPATCH,
    _classify,
    _extract_compare_x_vs_y,
    _is_compare_x_vs_y,
    _probe_compare_x_vs_y_for_task,
)

# ---- W28 — compare_x_vs_y ---------------------------------------------

_W28_POSITIVE = [
    "compare _classify vs _classify_procedure",
    "compare cli.py and mcp_server.py",
    "what's the difference between compile_plan and compile_for_artifact",
    "diff _probe_w11_dispatch vs _probe_w12_dispatch",
    "_classify compared to _classify_structural_subtype",
    # bare lowercase operands ARE valid in our domain — "vanilla" must stay
    # extractable (it's the A/B baseline we compare the compiler against).
    "compare compile vs vanilla",
]

_W28_NEGATIVE = [
    # caller-intent shape — must route to structural_callers, not compare
    "who calls _evaluate_mcp_mode_policy",
    # symbol-define shape (W11)
    "where is compile_plan defined",
    # ranking shape (W12)
    "top 5 most-imported files",
    # plain freeform exploration
    "explain this codebase",
    # bare CLI-verb perf shape (W13)
    "why is roam index slow",
    # PROSE false-positive: a long instruction where "...telemetry AND
    # compared TO vanilla..." made the non-greedy operand capture grab the
    # connector "and" → compare("and","vanilla"). Connector/glue stopwords
    # now reject it. (Regression for the live mis-fire on the user's prompt.)
    "I want us to find and analyze all telemetry and compared to vanilla where we stand",
    "we ran the suite and compared to the previous baseline",
]


@pytest.mark.parametrize("task", _W28_POSITIVE)
def test_w28_positive_classifies_compare_x_vs_y(task: str) -> None:
    proc, _rejected = _classify(task)
    assert proc == "compare_x_vs_y", f"expected compare_x_vs_y, got {proc!r} for {task!r}"


@pytest.mark.parametrize("task", _W28_NEGATIVE)
def test_w28_negative_does_not_misroute_to_compare_x_vs_y(task: str) -> None:
    proc, _rejected = _classify(task)
    assert proc != "compare_x_vs_y", f"unexpected compare_x_vs_y for {task!r}"


# ---- Dispatch wiring --------------------------------------------------


def test_w28_dispatch_registered() -> None:
    assert "compare_x_vs_y" in _PROBE_DISPATCH


# ---- Helper-level sanity ---------------------------------------------


def test_w28_extractor_helpers_consistent_with_classifier() -> None:
    """``_is_compare_x_vs_y`` must agree with ``_classify`` on every
    positive prompt — catches drift between extractor and classifier."""
    for task in _W28_POSITIVE:
        assert _is_compare_x_vs_y(task), f"W28 helper missed {task!r}"
        pair = _extract_compare_x_vs_y(task)
        assert pair is not None, f"W28 extractor returned None for {task!r}"
        x, y = pair
        assert x and y, f"W28 extractor produced empty side for {task!r}"
        assert x.lower() != y.lower(), f"W28 extractor collapsed both sides for {task!r}"


# ---- Probe returns data ----------------------------------------------


def test_w28_probe_returns_compare_x_vs_y_result() -> None:
    """Invoke ``_probe_compare_x_vs_y_for_task`` directly on a
    known-good comparison and verify the returned envelope carries a
    populated ``compare_x_vs_y_result`` key with X / Y / shape fields.

    cwd=None is intentional — exercises the probe's "no roam result"
    fallback path, which still returns a structured envelope (the W28
    contract: probe ALWAYS returns either ``compare_x_vs_y_result`` or
    ``compare_x_vs_y_unavailable``, never None when the pair extracts)."""
    task = "compare _classify vs _classify_procedure"
    out = _probe_compare_x_vs_y_for_task(task, cwd=None)
    assert out is not None, f"probe returned None for {task!r}"
    # Either the success key or the remediation key must be present.
    assert "compare_x_vs_y_result" in out or "compare_x_vs_y_unavailable" in out, (
        f"probe envelope missing W28 keys: keys={list(out)!r}"
    )
    # The result block must carry the extracted pair on X / Y, even when
    # the underlying roam command produced no data.
    result = out.get("compare_x_vs_y_result") or {}
    assert result.get("x") == "_classify", f"expected x='_classify', got {result.get('x')!r}"
    assert result.get("y") == "_classify_procedure", f"expected y='_classify_procedure', got {result.get('y')!r}"
    # Shape fields exist (possibly empty when no roam available).
    assert "diff_summary" in result
    assert "common_signature" in result
    assert "divergence_points" in result
    assert isinstance(result["divergence_points"], list)
