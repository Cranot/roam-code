"""W805-UUUUU — error-storm coalescer trim-variant sweep across error kinds.

W805-NNNNN discovered that the error-storm coalescer at
``src/roam/mcp_server.py:3580-3627`` trims repeat ``USAGE_ERROR`` envelopes
to a bare-isError shape that omits the top-level ``error`` key. W805-TTTTT
pinned the aggregator-side leak (``_compound_envelope`` at
``mcp_server.py:4448-4450``) with a root + drift-guard + representative
end-to-end pin.

Both prior waves were ``USAGE_ERROR``-specific. THIS wave (W805-UUUUU)
probes the OTHER side of the same root: the COALESCER itself. The hypothesis
under test: does the coalescer produce ONE trim shape that ALL error kinds
collapse to, or does it produce a FAMILY of trim shapes (one per error code)
that would each defeat ``_compound_envelope`` differently?

If the coalescer has ONE trim shape, the W805-TTTTT aggregator-side fix
(widen the check to also catch ``data.get("isError") is True``) is
structurally sufficient — the wave's hypothesis is DISCONFIRMED and we
record the structural sufficiency proof.

If the coalescer has MULTIPLE distinct trim shapes (e.g., one path that
drops ``error_code`` for certain codes, or a branch where ``first_error_message``
is missing for some codes), each gets its own xfail-strict pin AND informs
the eventual aggregator-side fix's exact widening — the W805-TTTTT
single-line widening would be insufficient.

**Probe scope (3-test version per task spec time-guard).**

1. **AST enumeration + per-code parametrized trim-shape capture.** For each
   error code in ``_SEVERITY_MAP`` (the most permissive source — every code
   flowing through ``_structured_error`` consults this map for ``severity``),
   prime the coalescer to threshold and capture the trim-shape's field set
   (the topology, not the values). One parametrized test per code; pass when
   the topology matches the expected single-shape.

2. **Worst-case Pattern-2 axis: first-fire with empty/missing error string.**
   The coalescer's ``first_error_message`` capture at line 3596-3599 is gated
   on ``isinstance(msg, str) and msg``. If the first-fire envelope omits
   ``error`` OR carries an empty string, the trim shape NEVER gains a
   replacement field — neither ``error`` NOR ``first_error_message`` survives.
   This is the WORST case (full Pattern-1D / Pattern-2 silent SAFE). Pinned
   as xfail-strict — when the W805-TTTTT aggregator-side fix lands AND the
   coalescer is taught to capture even an empty-error first-fire, this pin
   flips.

3. **Family-spanning summary test (the verdict cell).** Assert that across
   all error codes in ``_SEVERITY_MAP``, the coalescer produces EXACTLY ONE
   distinct trim-shape topology. If the count is 1 → DISCONFIRMED (one shape,
   W805-TTTTT's single-line widening is sufficient). If the count is >1 →
   CONFIRMED (multi-variant family, the widening must enumerate each
   variant). The verdict is written into the test name so the wave's outcome
   is visible from the pytest report alone.

**Verdict (recorded in this docstring after probe-and-record cycle):**

DISCONFIRMED single-shape. The coalescer's trim shape at lines 3607-3626 is
a STATIC dict literal — no branching on ``error_code``. The only per-code
variations are field VALUES (``severity`` via ``_SEVERITY_MAP``, ``retryable``
via ``_RETRYABLE_CODES``, ``doc_link`` via ``_DOC_LINKS``), not field SET. The
ONLY conditional field is ``first_error_message``, gated on the first-fire
envelope having a non-empty ``error`` string. So the trim shape produces at
most TWO topologies: (A) with ``first_error_message`` and (B) without. Both
omit the top-level ``error`` key — both defeat the W805-TTTTT aggregator
check identically. The W805-TTTTT single-line widening (recognise
``data.get("isError") is True``) IS structurally sufficient.

The W805-TTTTT widening's ``err_msg`` fallback chain
(``data.get("error") or data.get("first_error_message") or "empty result"``)
already handles BOTH topologies: topology (A) hits ``first_error_message``,
topology (B) falls through to ``"empty result"``. Variant (B) is therefore
a Pattern-2 silent SAFE that ALSO leaks an opaque error string up; the
xfail-strict on test (2) below pins both axes simultaneously.

**Cross-link:** ``tests/test_w805_ttttt_octet_trimmed_iserror_axis_sweep.py``
pins the aggregator-side leak this wave's coalescer-side probe confirms is
single-shape (modulo the first_error_message presence axis).
"""

from __future__ import annotations

import inspect
import re

import pytest

# Guarded import — mirrors W805-NNNNN / W805-TTTTT.
try:
    from roam.mcp_server import (  # noqa: E402
        _ERROR_STORM_THRESHOLD,
        _SEVERITY_MAP,
        _reset_error_storm,
        _structured_error,
    )
except Exception as _exc:  # pragma: no cover - guarded environments only
    pytest.skip(
        f"roam.mcp_server import failed: {_exc!r}; W805-UUUUU coalescer "
        "probe requires the MCP server module to be importable.",
        allow_module_level=True,
    )


# ---------------------------------------------------------------------------
# Test hygiene — clean state for every test; the coalescer is stateful.
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset_storm():
    _reset_error_storm()
    yield
    _reset_error_storm()


# ---------------------------------------------------------------------------
# AST enumeration — the set of error codes the coalescer can see. Every call
# to _structured_error consults _SEVERITY_MAP for severity; codes outside the
# map fall through to default "error", but the coalescer's trim shape does
# not branch on the code regardless. The map is therefore the most
# representative enumeration of codes that flow through the trim path in
# production.
# ---------------------------------------------------------------------------


# Pin the AST enumeration so any new code added to _SEVERITY_MAP automatically
# joins the parametrized sweep (no manual list to keep in sync). Sorted for
# deterministic test-id ordering.
_ALL_CODES = sorted(_SEVERITY_MAP.keys())


# ---------------------------------------------------------------------------
# Expected trim shape (the topology). Drawn directly from the literal in
# mcp_server.py plus the conditional command and first_error_message keys.
# This is the "expected single shape" the wave's verdict claims.
# ---------------------------------------------------------------------------


# Always-present keys in the trim shape (the static dict literal).
_EXPECTED_TRIM_KEYS = frozenset(
    {
        "isError",
        "error_code",
        "severity",
        "retryable",
        "doc_link",
        "repeat_count",
        "trimmed",
        "trimmed_hint",
    }
)

# Conditional: ``command`` is present iff the input supplied command identity.
# ``first_error_message`` is present iff the first-fire envelope had a
# non-empty string in the ``error`` field.
_CONDITIONAL_TRIM_KEYS = frozenset({"command", "first_error_message"})


def _prime_to_trim(code: str, first_error_text: str | None) -> dict:
    """Fire ``_structured_error`` ``_ERROR_STORM_THRESHOLD + 1`` times with
    the same code so the FINAL call returns the trim shape.

    The first fire captures the optional ``first_error_message`` iff
    ``first_error_text`` is a non-empty string per the coalescer's gate at
    mcp_server.py:3596-3599. Subsequent fires omit the ``error`` field
    because the cache lookup uses the first fire's snapshot.
    """
    fires_needed = _ERROR_STORM_THRESHOLD + 1
    last: dict | None = None
    for i in range(fires_needed):
        env: dict = {
            "error_code": code,
            "hint": f"no-op fire {i}",
            "command": "storm_primer",
        }
        # Only attach an ``error`` string on the FIRST fire — the cache
        # snapshot at mcp_server.py:3596-3599 is captured then. The
        # ``first_error_text=None`` path means the trim shape will lack
        # ``first_error_message`` (topology B).
        if i == 0 and first_error_text:
            env["error"] = first_error_text
        last = _structured_error(env)
    assert isinstance(last, dict), f"coalescer returned non-dict: {last!r}"
    return last


# ---------------------------------------------------------------------------
# (1) PARAMETRIZED PER-CODE TRIM-SHAPE CAPTURE
# For every code in _SEVERITY_MAP, drive it through the coalescer and assert
# the field set matches the expected topology. If ANY code produces a
# different field set, the wave's "single-shape" hypothesis is broken and
# this test fails — directly informing the W805-TTTTT widening shape.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("code", _ALL_CODES)
def test_per_code_trim_shape_topology_matches_expected(code: str):
    """Per-code: trim shape's KEY SET (topology) is identical regardless of
    code. Only the VALUES of severity/retryable/doc_link vary.

    Passes today: confirms the coalescer's static-literal trim shape
    produces the same topology for every code in _SEVERITY_MAP.

    Fails if: a new branch is added to the coalescer that conditionally
    drops or adds keys per code — at which point the W805-TTTTT widening
    would need to enumerate the new variants.
    """
    trim = _prime_to_trim(code, first_error_text="orig msg for " + code)
    keys = frozenset(trim.keys())

    # Required-always set (the static literal).
    missing = _EXPECTED_TRIM_KEYS - keys
    assert not missing, (
        f"Trim shape for code={code!r} is MISSING expected always-present "
        f"keys {sorted(missing)!r}. trim={trim!r}. If the coalescer was "
        "changed to conditionally drop keys, the W805-TTTTT widening's "
        "fallback chain must be updated."
    )

    # Conditional set: with first_error_text supplied, first_error_message
    # MUST be present per the gate at mcp_server.py:3596-3599.
    assert "first_error_message" in keys, (
        f"Trim shape for code={code!r} lacks first_error_message despite "
        "a non-empty first-fire error string. This breaks the W805-TTTTT "
        "aggregator widening's err_msg fallback chain. trim=" + repr(trim)
    )

    # Allowed-extra check: any key beyond expected always-present + conditional
    # indicates a NEW variant the W805-TTTTT widening doesn't anticipate.
    allowed = _EXPECTED_TRIM_KEYS | _CONDITIONAL_TRIM_KEYS
    unexpected = keys - allowed
    assert not unexpected, (
        f"Trim shape for code={code!r} carries UNEXPECTED keys "
        f"{sorted(unexpected)!r}. New trim-shape variant detected — "
        "re-evaluate the W805-TTTTT widening to confirm it still covers "
        "every variant. trim=" + repr(trim)
    )

    # Critical anti-leak invariant: the trim shape must NEVER carry a
    # top-level 'error' key — that's the W805-NNNNN root finding. If this
    # ever appears, the W805-TTTTT widening becomes redundant (the narrow
    # ``"error" in data`` check would suffice) and the wave's premise is
    # invalid.
    assert "error" not in keys, (
        f"Trim shape for code={code!r} GAINED a top-level 'error' key. "
        "This invalidates the W805-NNNNN / W805-TTTTT premise — the "
        "aggregator's narrow 'error in data' check would catch it. "
        "Re-evaluate both prior waves' pins. trim=" + repr(trim)
    )

    # And the error_code field survives — this is the recoverable signal
    # that lets the W805-TTTTT widening route the trimmed envelope to the
    # error branch even without a top-level ``error`` field.
    assert trim.get("error_code") == code, (
        f"Trim shape for code={code!r} lost or corrupted error_code: "
        f"got {trim.get('error_code')!r}. The W805-TTTTT widening uses "
        "this as the alternative routing signal. trim=" + repr(trim)
    )


# ---------------------------------------------------------------------------
# (2) WORST-CASE PATTERN-2 AXIS — first-fire with no error string.
# When the first fire of an error_code lacks the ``error`` key (or carries
# an empty string), the coalescer's first_error_message cache stays empty
# (mcp_server.py:3596-3599). The resulting trim shape carries NEITHER
# a top-level ``error`` key NOR a first_error_message — the aggregator
# even with the W805-TTTTT widening will route this to the error branch
# but the err_msg fallback chain hits ``"empty result"``, surfacing an
# opaque error string to the agent.
#
# This is the second axis on the same root: even after the W805-TTTTT
# fix lands, the agent will see ``error: "empty result"`` for any storm
# whose first fire happened to lack a user-facing message. xfail-strict so
# this pin flips when EITHER the coalescer is taught to synthesize a
# default first message OR the aggregator's err_msg chain learns to
# extract from the trimmed envelope's other fields (e.g. ``trimmed_hint``).
# ---------------------------------------------------------------------------


@pytest.mark.xfail(
    strict=True,
    reason=(
        "W805-UUUUU Pattern-2 worst-case axis. When the first-fire "
        "envelope lacks a non-empty 'error' string, the coalescer's "
        "first_error_message cache stays empty (mcp_server.py:3596-3599). "
        "The trim shape then carries NO actionable human text — the "
        "W805-TTTTT err_msg fallback chain hits 'empty result', "
        "surfacing opaque text to the agent. Fix-forward (separate wave): "
        "either teach the coalescer to synthesize a default first message "
        "from hint/error_code, or teach the aggregator's err_msg fallback "
        "to extract from trimmed_hint/doc_link/error_code. This pin "
        "flips when either lands."
    ),
)
def test_worst_case_first_fire_without_error_string_leaves_trim_without_human_text():
    """Probe the Pattern-2 worst-case: first fire has no ``error`` key.
    Assert that BOTH ``error`` AND ``first_error_message`` are absent from
    the resulting trim shape — full silent-SAFE for human-readable text.

    Today: PASSES the negative assertions (both keys absent), so we then
    assert the POSITIVE recovery — that the trim shape carries SOME human-
    readable replacement field. It does not. This is what the xfail-strict
    pins.
    """
    trim = _prime_to_trim("USAGE_ERROR", first_error_text=None)

    # The leak: both 'error' and 'first_error_message' absent.
    assert "error" not in trim, f"Unexpected error key: {trim!r}"
    assert "first_error_message" not in trim, f"Unexpected first_error_message key: {trim!r}"

    # The xfail pin: assert recovery is present — TODAY this fails because
    # the trim shape carries no human-readable replacement field. The
    # ``trimmed_hint`` field is a META message (about the storm itself),
    # not about the underlying error. ``doc_link`` is a URL, not text.
    # ``error_code`` is a symbol, not a sentence.
    #
    # When a fix lands (coalescer synthesizes a default first message OR
    # aggregator's err_msg fallback learns to extract a human-readable
    # string from another field), THIS assertion will pass and the
    # xfail-strict marker will fail-on-pass — flipping the pin.
    has_human_text = bool(trim.get("error") or trim.get("first_error_message"))
    assert has_human_text, (
        "Trim shape carries NO human-readable error text — Pattern-2 "
        "silent SAFE for the actionable message even after the "
        "W805-TTTTT aggregator widening lands. trim=" + repr(trim)
    )


# ---------------------------------------------------------------------------
# (3) FAMILY-SPANNING SUMMARY — distinct trim-shape topology count.
# The verdict cell. Count distinct (frozenset of keys) topologies across
# every code in _SEVERITY_MAP. If the count is 1 → DISCONFIRMED single-
# shape (W805-TTTTT widening sufficient). If >1 → CONFIRMED multi-variant
# (W805-TTTTT widening needs per-variant enumeration).
# ---------------------------------------------------------------------------


def test_family_spanning_distinct_trim_topology_count_is_one():
    """Verdict cell: across all error codes the coalescer can see, count
    the number of distinct trim-shape topologies (key-sets, not values).
    Assert exactly one — proving the single-shape hypothesis and showing
    that the W805-TTTTT aggregator-side widening is structurally
    sufficient (DISCONFIRMED multi-variant hypothesis).

    Holds first_error_text constant (non-empty) across the sweep so the
    conditional ``first_error_message`` key is always present — isolating
    the per-code variation to its proper axis.
    """
    topologies: dict[frozenset, list[str]] = {}
    for code in _ALL_CODES:
        _reset_error_storm()  # isolate each per-code probe
        trim = _prime_to_trim(code, first_error_text=f"orig msg for {code}")
        topo = frozenset(trim.keys())
        topologies.setdefault(topo, []).append(code)

    distinct = len(topologies)
    # Build a diagnostic that names each topology and its codes — when this
    # test ever fails (i.e., a future change introduces a per-code branch
    # in the trim shape), the failure message names exactly which codes
    # diverged and how.
    diag_lines = []
    for topo, codes in topologies.items():
        diag_lines.append(f"  topology(keys={sorted(topo)!r}) → codes={codes!r}")
    diag = "\n".join(diag_lines)

    assert distinct == 1, (
        f"W805-UUUUU CONFIRMED multi-variant: coalescer produces {distinct} "
        f"distinct trim-shape topologies across {len(_ALL_CODES)} error codes. "
        f"The W805-TTTTT single-line widening is INSUFFICIENT — each variant "
        f"must be enumerated. Topologies follow:\n{diag}"
    )

    # Recorded verdict: DISCONFIRMED. The single topology MUST be the
    # expected_always | conditional set (we held first_error_text non-empty
    # so the conditional key is present).
    only_topo = next(iter(topologies.keys()))
    expected_topo = _EXPECTED_TRIM_KEYS | _CONDITIONAL_TRIM_KEYS
    assert only_topo == expected_topo, (
        f"Single trim-shape topology is not the expected set. "
        f"got={sorted(only_topo)!r} expected={sorted(expected_topo)!r}. "
        "The W805-TTTTT widening's fallback chain may need to learn new "
        "fields."
    )


# ---------------------------------------------------------------------------
# Drift guard — the coalescer trim shape is a static dict literal at
# mcp_server.py:3607-3619. If anyone refactors it to branch on error_code
# (e.g., ``if code == "USAGE_ERROR": trimmed["extra"] = ...``), the wave's
# verdict (single-shape) is no longer guaranteed and the per-code parametrized
# test (1) above starts catching variants. This guard makes the structural
# claim mechanical: assert the trim dict is a STATIC dict literal with no
# per-code if-branches between it and the return statement.
# ---------------------------------------------------------------------------


def test_drift_guard_coalescer_trim_dict_has_no_per_code_branches():
    """FAMILY CLOSER guard: the coalescer's trim shape is built from a
    STATIC dict literal. If a future commit adds per-code branching
    between the ``trimmed = {`` literal and the ``return trimmed`` line,
    this guard fires — at which point the wave's single-shape verdict
    needs re-evaluation.

    The check is narrow on purpose: it pins the structural property
    (static literal, no code-keyed branching) without coupling to
    formatting. The trim block ends at the ``return trimmed`` line.
    """
    src = inspect.getsource(_structured_error)
    # Isolate the trim block: from 'trimmed = {' to the matching 'return
    # trimmed'. This is a contiguous span in the current source per
    # mcp_server.py:3607-3626.
    m_start = re.search(r"trimmed\s*=\s*\{", src)
    m_end = re.search(r"return\s+trimmed\b", src)
    assert m_start and m_end and m_start.start() < m_end.start(), (
        "Could not locate the trim block in _structured_error source. "
        "The coalescer may have been refactored — re-evaluate W805-UUUUU."
    )
    block = src[m_start.start() : m_end.end()]
    # The only conditional inside the block today is the first_error_message
    # capture: ``if first_msg:``. ANY other ``if`` that mentions ``code`` or
    # an error-code string literal would be a per-code branch.
    #
    # Scan for ``if`` lines that reference ``code`` (the local variable that
    # holds the error_code in _structured_error). Allow the existing
    # ``if first_msg:`` (no ``code`` reference).
    bad_lines: list[str] = []
    for line in block.splitlines():
        stripped = line.strip()
        if stripped.startswith("if ") and "code" in stripped:
            bad_lines.append(stripped)
    assert not bad_lines, (
        "Coalescer trim block contains per-code conditional branching — "
        "the W805-UUUUU single-shape verdict no longer holds. New variants "
        "may have been introduced. Offending lines:\n  " + "\n  ".join(bad_lines)
    )
