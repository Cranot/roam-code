"""W761 — envelope-slot lowercase severity contract for
``cmd_invariants``, ``cmd_bus_factor``, and ``cmd_complexity``.

Why this regression test exists
--------------------------------

W547 established the canonical lowercase severity vocabulary
(``critical`` / ``error`` / ``warning`` / ``info``) in
:mod:`roam.output._severity`. W649 migrated ``cmd_alerts`` fully onto
that vocabulary. W762 added a narrow AST drift-guard that catches
``{"severity": "CRITICAL"}`` literal shapes in command envelopes
(helper-indirected sites like ``{"severity": _severity(...)}`` slip
through the lint by design — those cleanups are coordinated per-command
via W759 / W760 / W761).

W847 clarified the scope: the canonical W547 ``severity`` envelope slot
is the migration target, while neighbouring fields (``risk_level``,
``risk``, ``knowledge_risk``, ``stability``, ``run_state``) are INTERNAL
VOCABULARY for the agent-facing risk-tier display contract and may
retain UPPER-case spellings. The W847 carve-out is documented inline
in the source files.

This test pins THREE behaviours:

1. ``cmd_complexity``'s per-symbol envelope payload emits the canonical
   lowercase severity vocabulary (``critical`` / ``high`` / ``medium``
   / ``low``). Pre-W761 the ``_severity()`` helper returned UPPER-case
   strings which flowed into ``symbols[i].value.severity`` via a
   helper-indirected dict assignment — the W762 lint didn't fire (the
   value node was a ``Call``, not a ``Constant``), so the W547 contract
   leaked silently.
2. ``cmd_invariants`` summary envelope has NO ``severity`` slot (its
   rollup is on ``high_risk_count`` / ``symbols_analyzed`` / the
   per-symbol ``risk_level`` internal-vocabulary field).
3. ``cmd_bus_factor`` summary envelope has NO ``severity`` slot (its
   rollup is on ``high_risk`` / ``directories_analyzed`` / ``run_state``).

Together these pin the W761 invariant: every envelope-slot ``severity``
value across the three commands is lowercase, and any UPPER-case
spelling that remains is on an explicitly-internal-vocabulary field
guarded by a ``# W761/W847 retained UPPER-case for internal vocabulary``
comment in the source.

Internal-vocabulary annotation discipline
------------------------------------------

A separate static check pins that the retained UPPER-case sites in
``cmd_invariants`` / ``cmd_bus_factor`` carry the W761/W847 marker
comment. This is the loud-fallback rule (CLAUDE.md "Make fallback
chains loud"): an UPPER-case literal that survived W761 must be
ANNOTATED so the next reader sees it was deliberately retained, not
forgotten.
"""

from __future__ import annotations

import json

from click.testing import CliRunner

from roam.cli import cli
from tests._helpers.repo_root import repo_root

# ---------------------------------------------------------------------------
# Envelope-slot lowercase pins
# ---------------------------------------------------------------------------


def _invoke_json(args: list[str]) -> dict:
    runner = CliRunner()
    result = runner.invoke(cli, ["--json", *args])
    assert result.exit_code in (0, 5, 6), (
        f"roam --json {' '.join(args)} exited unexpectedly: exit={result.exit_code}\nstdout={result.output[:500]}"
    )
    return json.loads(result.output)


_CANONICAL_LOWERCASE: frozenset[str] = frozenset({"critical", "high", "medium", "low", "info", "warning", "error"})


def test_complexity_envelope_severity_slot_is_lowercase() -> None:
    """``cmd_complexity`` per-symbol ``value.severity`` must be lowercase.

    The complexity envelope wraps each ranked symbol in ``{value,
    confidence, reason}``; the ``value.severity`` field is the W547
    canonical envelope slot. Pre-W761 it carried ``"CRITICAL"`` /
    ``"HIGH"`` etc. via the ``_severity()`` helper; W761 lowercased
    the helper so the envelope slot now matches the canonical W547
    vocabulary.
    """
    data = _invoke_json(["complexity", "--top", "5"])
    assert data["command"] == "complexity"
    symbols = data.get("symbols") or []
    assert symbols, "complexity must produce at least one ranked symbol on roam-code"
    seen_severities: set[str] = set()
    for entry in symbols:
        value = entry.get("value") or {}
        sev = value.get("severity")
        if sev is not None:
            seen_severities.add(sev)
    assert seen_severities, "complexity envelope must populate value.severity"
    for sev in seen_severities:
        assert sev == sev.lower(), (
            f"W761: cmd_complexity value.severity must be lowercase; got {sev!r}. "
            f"Run roam complexity --json and inspect symbols[].value.severity."
        )
        assert sev in _CANONICAL_LOWERCASE, (
            f"W761: cmd_complexity value.severity must be in the canonical "
            f"W547 vocabulary; got {sev!r}; expected one of "
            f"{sorted(_CANONICAL_LOWERCASE)}."
        )


def test_invariants_envelope_has_no_severity_slot() -> None:
    """``cmd_invariants`` summary envelope must NOT carry a ``severity``
    field.

    The invariants rollup is on ``high_risk_count`` /
    ``symbols_analyzed`` / ``total_invariants``; per-symbol fields use
    ``risk_level`` (W847 canonical rollup, internal vocabulary) and
    per-invariant fields use ``stability`` (internal vocabulary). A
    ``severity`` field would be a regression — it would introduce a
    W547 envelope slot that doesn't currently exist, and a stale
    UPPER-case helper would silently flow through.
    """
    data = _invoke_json(["invariants", "--breaking-risk", "--top", "3"])
    assert data["command"] == "invariants"
    summary = data.get("summary") or {}
    assert "severity" not in summary, (
        "W761: cmd_invariants summary must not carry a 'severity' slot. "
        f"Got summary keys: {sorted(summary)}. The W547 rollup belongs on "
        "high_risk_count / symbols_analyzed; per-symbol UPPER-case lives on "
        "risk_level (internal vocabulary per W847)."
    )


def test_bus_factor_envelope_has_no_severity_slot() -> None:
    """``cmd_bus_factor`` summary envelope must NOT carry a ``severity``
    field.

    The bus-factor rollup is on ``directories_analyzed`` / ``high_risk``
    / ``run_state``; per-directory fields use ``risk`` / ``knowledge_risk``
    (internal vocabulary per W847). A ``severity`` field would
    introduce a W547 envelope slot that doesn't currently exist.
    """
    data = _invoke_json(["bus-factor"])
    assert data["command"] == "bus-factor"
    summary = data.get("summary") or {}
    assert "severity" not in summary, (
        "W761: cmd_bus_factor summary must not carry a 'severity' slot. "
        f"Got summary keys: {sorted(summary)}. The W547 rollup belongs on "
        "directories_analyzed / high_risk / run_state; per-directory "
        "UPPER-case lives on risk / knowledge_risk (internal vocabulary per W847)."
    )


# ---------------------------------------------------------------------------
# Internal-vocabulary annotation discipline (CLAUDE.md loud-fallback rule)
# ---------------------------------------------------------------------------


def _read_source(rel_path: str) -> str:
    return (repo_root() / "src" / "roam" / "commands" / rel_path).read_text(encoding="utf-8", errors="replace")


def test_invariants_internal_upper_carries_w761_w847_marker() -> None:
    """Every retained UPPER-case severity literal in ``cmd_invariants``
    must carry a ``# W761/W847 retained UPPER-case for internal vocabulary``
    marker.

    Loud-fallback discipline: an UPPER-case literal that survived W761
    must be ANNOTATED so the next reader sees it was deliberately
    retained as internal vocabulary, not forgotten by a stealth-skip.
    """
    src = _read_source("cmd_invariants.py")
    # The five retained UPPER sites: stability HIGH/MEDIUM/LOW ternary,
    # stability HIGH-or-MEDIUM ternary, stability HIGH-literal,
    # stability MEDIUM-literal, risk_level CRITICAL/HIGH/MEDIUM/LOW
    # ternary, and the aggregate-summary "risk_level in (...)" tuple.
    marker = "W761/W847"
    occurrences = src.count(marker)
    assert occurrences >= 5, (
        f"W761: cmd_invariants must annotate every retained UPPER-case "
        f"site with a 'W761/W847' marker comment; found {occurrences}. "
        f"Loud-fallback discipline (CLAUDE.md) — a UPPER literal that "
        f"survived W761 must be ANNOTATED so the next reader knows it's "
        f"internal vocabulary, not a missed cleanup."
    )


def test_bus_factor_internal_upper_carries_w761_w847_marker() -> None:
    """Every retained UPPER-case severity literal in ``cmd_bus_factor``
    must carry a ``# W761/W847 retained UPPER-case for internal vocabulary``
    marker.
    """
    src = _read_source("cmd_bus_factor.py")
    # Retained UPPER sites: _knowledge_risk_label() (CRITICAL/HIGH/
    # MEDIUM/LOW), _risk_label() (HIGH/MEDIUM/LOW), aggregate_risk_counts
    # comparison tuple, _score_classify_run() (HEALTHY/WARN/CRITICAL/
    # DEGRADED), and the "_state_label = 'CRITICAL'" branch.
    marker = "W761/W847"
    occurrences = src.count(marker)
    assert occurrences >= 4, (
        f"W761: cmd_bus_factor must annotate every retained UPPER-case "
        f"site with a 'W761/W847' marker comment; found {occurrences}."
    )


# ---------------------------------------------------------------------------
# Helper-level lowercase pin (cmd_complexity._severity)
# ---------------------------------------------------------------------------


def test_complexity_severity_helper_returns_lowercase() -> None:
    """``cmd_complexity._severity()`` returns the canonical W547
    lowercase vocabulary.

    Pre-W761 this helper returned UPPER-case ("CRITICAL" / "HIGH" /
    "MEDIUM" / "LOW") which then flowed into the per-symbol envelope
    ``value.severity`` slot via two builder sites (the SARIF symbol
    list and the ranked-envelope symbol list). W761 lowercased the
    helper so the envelope slot inherits the canonical contract
    without per-site case-fold logic.
    """
    from roam.commands.cmd_complexity import _severity

    assert _severity(30.0) == "critical"
    assert _severity(20.0) == "high"
    assert _severity(10.0) == "medium"
    assert _severity(3.0) == "low"
    # Floor + boundary checks.
    assert _severity(25.0) == "critical"
    assert _severity(15.0) == "high"
    assert _severity(8.0) == "medium"
    assert _severity(0.0) == "low"


def test_w547_canonical_vocabulary_stays_lowercase() -> None:
    """Pin the canonical W547 vocabulary the W761 migration targets.

    Mirrors the W762 ``test_w547_canonical_vocabulary_stays_stable``
    assertion. If W547 ever drifts to a different vocabulary, the W761
    test set needs to be re-cut against the new contract.
    """
    from roam.output._severity import SEVERITY_LEVELS

    assert SEVERITY_LEVELS == frozenset({"critical", "error", "warning", "info"}), (
        "W761: canonical W547 vocabulary drifted — re-cut the W761 envelope-slot pins against the new contract."
    )
