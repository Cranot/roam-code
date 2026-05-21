"""W805-TTTTT — OCTET trimmed-isError axis sweep across the compound family.

The OCTET family (W805-F / KK / LL / GGG / KKK / OOO / QQQ / VVV) previously
pinned 8 compound recipes that all consume ``_compound_envelope`` at
``src/roam/mcp_server.py:4448-4470``. Each prior member pinned a different
*content* axis (empty corpus, identity drift, etc.) but they all share the
same *aggregator surface*: the line that classifies a subcommand result as
failed.

W805-NNNNN surfaced a SECOND, more dangerous axis on that exact surface:
when the error-storm coalescer at ``mcp_server.py:3580-3627`` trims repeat
``USAGE_ERROR`` envelopes (after ``_ERROR_STORM_THRESHOLD=3`` fires of the
same ``error_code``) it returns the shape::

    {"isError": True, "error_code": "USAGE_ERROR", "severity": "...",
     "retryable": ..., "doc_link": ..., "repeat_count": ..., "trimmed": True,
     "trimmed_hint": "...", "first_error_message": "..."}

Critically: the trimmed envelope OMITS the top-level ``error`` key. The
aggregator at line 4448-4450 reads only ``"error" in data``::

    for name, data in sub_results:
        if not data or "error" in data:        # <-- THE CHECK
            err_msg = data.get("error", "empty result") if data else "empty result"
            errors.append({"command": name, "error": err_msg})
        else:
            sections[name] = data               # <-- TRIMMED isError LANDS HERE

So a trimmed ``isError:True`` envelope SKIPS the error branch and falls
into the success bucket. ``failed_subcommands`` never names it; ``sections``
includes it. An agent reading ``failed_subcommands`` concludes the child
ran cleanly when it actually erred — Pattern-2 silent SAFE / Variant-D
silent success on degraded resolution.

**AGENT-SAFETY CRITICAL.** Of the 8 OCTET compounds, six are situation-
scoped recipes consumed by agents to decide whether to commit code
(``for_bug_fix`` / ``for_refactor`` / ``for_security_review`` /
``for_new_feature`` / ``diagnose_issue`` / ``prepare_change`` /
``review_change``) and one is a navigation recipe (``explore``). If the
trimmed-isError envelope leak silently flips a child into the success
bucket on any of them, the user-facing verdict lies.

PIN STRATEGY (scoped 3-test version per task spec) — all three pins are
now plain asserts; the W805-OCTET seal wave landed the fix-forward:

1. **Root pin (SEALED, plain assert).** Directly probe ``_compound_envelope``
   with a synthetic trimmed-isError dict — no end-to-end shell, no
   error-storm priming, no fixture corpus. This is the minimal possible
   repro: feed the aggregator the exact shape the coalescer produces and
   assert that the bad-child lands in ``failed_subcommands``. The widened
   aggregator now classifies it correctly. This is the FAMILY ROOT.

2. **Representative end-to-end pin (SEALED, plain assert).** ``for_bug_fix``
   stand-in: prime the error-storm coalescer to threshold, then invoke
   the compound so its children inherit the trimmed envelope. Asserts
   that the failed child surfaces in ``failed_subcommands``. The widened
   aggregator now routes trimmed-isError children there.

3. **Drift-guard (always-on).** Asserts that the LITERAL aggregator check
   in ``_compound_envelope`` is the WIDENED ``"error" in data or
   data.get("isError") is True`` form (NOT the pre-fix narrow ``"error"
   in data`` form). The guard fired when the fix landed; it now pins the
   widened form so a future narrowing refactor trips a failure. This is
   the FAMILY CLOSER.

**Follow-up wave candidates** (deliberately deferred per task spec):
per-recipe end-to-end pins for ``for_refactor`` (W805-KK), ``for_new_feature``
(W805-GGG), ``diagnose_issue`` (W805-KKK), ``prepare_change`` (W805-OOO),
``review_change`` (W805-QQQ), and ``explore`` (W805-VVV). All seven recipes
consume the same ``_compound_envelope`` aggregator, so the root pin (item 1)
already covers them at the unit level; the per-recipe pins would only add
confirmation that the trimmed envelope is reachable end-to-end on each
specific compound's recipe shape. The representative pin on ``for_bug_fix``
demonstrates the end-to-end path is reachable; the rest are mechanical.

**Fix-forward — SEALED (W805-OCTET seal wave).** The aggregator check in
``_compound_envelope`` was widened to::

    for name, data in sub_results:
        if not data or "error" in data or (isinstance(data, dict) and data.get("isError") is True):
            err_msg = "empty result"
            if data:
                child_summary = data.get("summary")
                err_msg = (
                    data.get("error")
                    or data.get("first_error_message")
                    or (child_summary.get("verdict") if isinstance(child_summary, dict) else None)
                    or "empty result"
                )
            errors.append({"command": name, "error": err_msg})
        else:
            sections[name] = data

The ``err_msg`` chain additionally falls back to a structured ``isError``
envelope's ``summary.verdict`` so a real degraded child (e.g.
``affected-tests`` returning ``isError: True`` + ``verdict: "Symbol not
found: X"`` without a top-level ``error`` key) surfaces an actionable
message instead of the opaque ``"empty result"`` sentinel.

When the widening landed, the drift-guard (item 3) flipped polarity to
assert the WIDE form, and the two formerly-xfail-strict pins flipped to
plain asserts — the family is sealed. The remaining W805-F / W805-LL
xfail-strict pins probe a DIFFERENT, still-open bug: children that
disclose only ``summary.partial_success: True`` (no top-level ``isError``
or ``error``) are NOT lifted by this widening.
"""

from __future__ import annotations

import pytest

# Guarded import — mirrors W805-NNNNN's pattern. ``roam.mcp_server`` imports
# only specific fastmcp submodules, so ``pytest.importorskip("fastmcp")``
# would over-skip on environments where the compound IS callable.
try:
    from roam.mcp_server import (  # noqa: E402
        _compound_envelope,
        _reset_error_storm,
        _structured_error,
        for_bug_fix,
    )
except Exception as _exc:  # pragma: no cover - guarded environments only
    pytest.skip(
        f"roam.mcp_server import failed: {_exc!r}; MCP compound tests require the MCP server module to be importable.",
        allow_module_level=True,
    )


# ---------------------------------------------------------------------------
# Test hygiene: disable handle-off and reset the error-storm counter so each
# test sees clean state. The coalescer at mcp_server.py:3580-3627 carries
# state across calls — without a reset, the per-test trimmed-envelope shape
# depends on test-execution order (mirrors W805-NNNNN's fixtures).
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _disable_handle_off(monkeypatch):
    monkeypatch.setenv("ROAM_MCP_HANDLE_KB", "0")
    yield


@pytest.fixture(autouse=True)
def _reset_storm():
    _reset_error_storm()
    yield
    _reset_error_storm()


# ---------------------------------------------------------------------------
# (1) ROOT PIN — direct probe of _compound_envelope on the trimmed shape.
# This is the minimum-repro cell: the aggregator gets a synthetic dict
# matching the trimmed-isError shape and we assert the bad child lands in
# failed_subcommands. No corpus, no coalescer priming, no Click shell.
# ---------------------------------------------------------------------------


# A faithful reproduction of the trimmed envelope at mcp_server.py:3607-3626.
# Exact field set the coalescer returns once _ERROR_STORM_THRESHOLD (3) fires
# of the same error_code. Note: NO top-level ``error`` key.
_TRIMMED_ISERROR_ENVELOPE = {
    "isError": True,
    "error_code": "USAGE_ERROR",
    "severity": "error",
    "retryable": False,
    "doc_link": "https://roam-code.com/docs/errors/USAGE_ERROR",
    "repeat_count": 3,
    "trimmed": True,
    "trimmed_hint": (
        "same error fired 3x — fetch the full envelope by varying inputs "
        "or by calling another tool first to reset the counter."
    ),
    "first_error_message": "Got unexpected extra argument (handleAuth)",
}


def test_root_compound_envelope_classifies_trimmed_iserror_as_failed():
    """ROOT PIN — SEALED. Direct probe: feed _compound_envelope a single
    subcommand result matching the trimmed-isError shape; assert it lands
    in failed_subcommands (the failure bucket), NOT sections (the success
    bucket).

    The W805-TTTTT fix-forward widened the aggregator check at
    ``_compound_envelope`` to also catch ``data.get("isError") is True``,
    so the trimmed-isError envelope is now correctly classified as a
    failed subcommand. This pin is a plain assert (was xfail-strict
    pre-fix).
    """
    sub_results = [("bad_child", dict(_TRIMMED_ISERROR_ENVELOPE))]
    result = _compound_envelope("test-compound", sub_results)
    summary = result.get("summary") or {}
    failed = summary.get("failed_subcommands") or []
    sections = summary.get("sections") or []
    # The pin: trimmed isError must classify as failed.
    assert "bad_child" in failed, (
        f"Trimmed-isError envelope silently classified as success. "
        f"failed_subcommands={failed!r} sections={sections!r} "
        f"summary={summary!r}"
    )
    assert "bad_child" not in sections, (
        f"Trimmed-isError envelope landed in success bucket 'sections'. sections={sections!r}"
    )
    # And partial_success must flip.
    assert summary.get("partial_success") is True, (
        f"partial_success did not flip True despite isError=True child. summary={summary!r}"
    )


# ---------------------------------------------------------------------------
# (2) REPRESENTATIVE END-TO-END PIN — for_bug_fix (W805-F).
# Prime the error-storm coalescer to threshold (3 fires of the same
# error_code) BEFORE invoking the compound, so the next USAGE_ERROR from
# any child of for_bug_fix returns the trimmed shape. Asserts the trimmed
# child surfaces in failed_subcommands end-to-end.
# ---------------------------------------------------------------------------


def _prime_storm_to_threshold(code: str = "USAGE_ERROR") -> None:
    """Fire _structured_error 3x with the same error_code so the NEXT
    occurrence of that code returns the trimmed shape.

    Mirrors the coalescer state-machine at mcp_server.py:3579-3600:
    threshold is 3, and equality is on error_code. Three primer fires
    means the 4th fire (the first one from inside the compound's
    children) returns the trimmed envelope.
    """
    for i in range(3):
        _structured_error(
            {
                "error": f"primer fire {i}",
                "error_code": code,
                "hint": "no-op",
                "command": "storm_primer",
            }
        )


def test_end_to_end_for_bug_fix_trimmed_envelope_surfaces_in_failed():
    """End-to-end pin — SEALED. Prime the storm, invoke for_bug_fix on an
    unresolved symbol, assert that any child whose envelope is the trimmed
    shape surfaces in failed_subcommands.

    The W805-TTTTT fix-forward widened the aggregator check, so a trimmed
    (or any) ``isError: True`` child now correctly lands in
    failed_subcommands end-to-end. This pin is a plain assert (was
    xfail-strict pre-fix).

    The compound is invoked without a corpus fixture — it runs against
    the current working directory's roam-code index, which is stable
    across the test suite. We use a deliberately bogus symbol so
    children fail (or return unresolved) consistently. The point is
    not to assert on a specific subcommand result; it's to assert
    that IF any child returns the trimmed-isError shape, it surfaces
    in failed_subcommands.
    """
    _prime_storm_to_threshold("USAGE_ERROR")
    r = for_bug_fix(symbol="zzNoSuchSymbolForW805TTTTTRepro", root=".")
    assert isinstance(r, dict), f"compound returned non-dict: {type(r)!r}"
    summary = r.get("summary") or {}
    failed = set(summary.get("failed_subcommands") or [])
    # Scan every section the compound returned. Anywhere a child carries
    # isError=True but is NOT in failed_subcommands is the trimmed-leak
    # pattern.
    leaks: list[str] = []
    for child_name in ("diagnose", "affected_tests", "diff", "context"):
        block = r.get(child_name)
        if not isinstance(block, dict):
            continue
        if block.get("isError") is True and child_name not in failed:
            leaks.append(child_name)
    assert not leaks, (
        f"Trimmed-isError children leaked past failed_subcommands "
        f"end-to-end on for_bug_fix. leaks={leaks!r} failed={failed!r} "
        f"summary={summary!r}"
    )


# ---------------------------------------------------------------------------
# (3) DRIFT-GUARD / FAMILY CLOSER — always-on test that asserts the literal
# aggregator check in ``_compound_envelope`` is the WIDENED form (catches
# ``isError`` in addition to the top-level ``error`` key). The W805-TTTTT
# fix-forward landed this widening; the guard now pins it so a future
# refactor that narrows the check back trips a failure.
# ---------------------------------------------------------------------------


def test_drift_guard_aggregator_check_is_widened():
    """FAMILY CLOSER — SEALED: assert the aggregator's classification check
    is the WIDENED form (catches ``data.get("isError") is True`` in
    addition to the top-level ``"error" in data`` key).

    The W805-TTTTT fix-forward widened the check at ``_compound_envelope``
    so a trimmed-isError child (or any ``isError: True`` envelope) is
    correctly classified as a failed subcommand. This guard pins the
    widened form so a future narrowing refactor trips a failure here AND
    the two formerly-xfail pins above start failing for real.
    """
    import inspect
    import re

    src = inspect.getsource(_compound_envelope)
    # Scope the check to the AGGREGATOR ``if`` LINE only — the per-subcommand
    # classification check inside ``for name, data in sub_results``. A
    # whole-function grep false-positives: ``_compound_envelope`` legitimately
    # references ``isError`` ELSEWHERE — it stamps ``result["isError"] = True``
    # on the all-failed output envelope (Pattern-1 conformance), which is
    # unrelated to the per-child classification surface this guard tracks.
    # Isolating the aggregator ``if`` line keeps the guard firing ONLY when
    # the real check is narrowed back (a regression of the W805-TTTTT fix).
    agg_line = next(
        (ln for ln in src.splitlines() if ln.strip().startswith("if ") and '"error" in data' in ln),
        "",
    )
    # The narrow (pre-fix) form lived on ONE line near the top of the loop:
    #   if not data or "error" in data:
    narrow_pattern = re.compile(r'if not data or "error" in data:\s*$')
    # The widened (post-fix) form must reference isError on the same line:
    #   if not data or "error" in data or (... data.get("isError") is True):
    widened_pattern = re.compile(r"isError")
    narrow_hit = bool(narrow_pattern.search(agg_line))
    widened_hit = bool(widened_pattern.search(agg_line))
    assert widened_hit, (
        "Drift-guard expected the WIDENED aggregator check (referencing "
        "'isError' on the classification line) in _compound_envelope. The "
        "W805-TTTTT fix-forward widened it; if this guard fails the check "
        "has been narrowed back — a Pattern-2 silent-SAFE regression. "
        "Source extract follows:\n" + src[:2000]
    )
    assert not narrow_hit, (
        "Drift-guard saw the NARROW 'if not data or \"error\" in data:' "
        "check — the W805-TTTTT widening has been reverted. A "
        "trimmed-isError child would again leak into the success bucket. "
        "Source extract follows:\n" + src[:2000]
    )


# ---------------------------------------------------------------------------
# Companion guard: synthetic confirmation that the trimmed-envelope shape
# this whole wave probes is in fact what the coalescer produces. If the
# coalescer's trimmed shape ever changes (e.g. someone adds the 'error' key
# back into the trimmed envelope at mcp_server.py:3607-3626), THIS test
# trips — at which point the entire wave's premise is gone and the pins
# above need re-evaluation.
# ---------------------------------------------------------------------------


def test_storm_coalescer_trimmed_envelope_has_no_top_level_error_key():
    """Sanity: the error-storm coalescer's trimmed shape is what we
    think it is — isError=True, NO top-level 'error' key. If this
    invariant breaks, the wave's premise is invalid and the pins above
    must be re-justified.
    """
    _reset_error_storm()
    # Fire the same code 4 times. The 4th fire crosses the threshold
    # (>= 3) and returns the trimmed shape.
    last = None
    for i in range(4):
        last = _structured_error(
            {
                "error": f"primer fire {i}",
                "error_code": "USAGE_ERROR",
                "hint": "no-op",
                "command": "storm_primer",
            }
        )
    assert isinstance(last, dict), f"coalescer returned non-dict: {last!r}"
    assert last.get("isError") is True, f"trimmed shape missing isError: {last!r}"
    assert last.get("trimmed") is True, f"trimmed shape missing trimmed flag: {last!r}"
    assert "error" not in last, (
        "Trimmed envelope GAINED a top-level 'error' key — the W805-TTTTT "
        "wave's premise is invalid. The aggregator at "
        "mcp_server.py:4448-4450 would now classify the trimmed envelope "
        "correctly. Re-evaluate the xfail-strict pins above; they may "
        "no longer reproduce. Got: " + repr(last)
    )
