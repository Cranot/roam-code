"""W759 — envelope-slot lowercase severity contract for ``cmd_preflight``.

Why this regression test exists
--------------------------------

W547 established the canonical lowercase severity vocabulary
(``critical`` / ``error`` / ``warning`` / ``info``) in
:mod:`roam.output._severity`. W762 added the AST drift-guard that
catches ``{"severity": "CRITICAL|HIGH|MEDIUM|LOW|WARNING|INFO|ERROR"}``
literal shapes in command envelopes. The original W762 inventory
flagged four cmd_preflight sites (L199 / L318 / L340 / L887 of the
pre-cleanup tree).

W759 closes the remaining cmd_preflight envelope-slot site: the
``_run_check`` substrate-default for ``affected_tests`` whose
fall-through ``"severity"`` value was the only UPPER-case literal left
inside a ``{"severity": ...}`` dict. The W762 detector flagged it at
line 1073 of the current tree. After W759 the W762 drift-guard's
``_PRE_W762_PENDING`` allowlist holds zero cmd_preflight entries.

W847 scope carve-out
--------------------

Every UPPER-case spelling that survives in ``cmd_preflight.py`` is
INTERNAL VOCABULARY for the agent-facing risk-tier display contract:

* ``_blast_severity`` / ``_test_severity`` / ``_complexity_severity``
  / ``_coupling_severity`` / ``_convention_severity`` /
  ``_fitness_severity`` helper returns (``"CRITICAL"`` / ``"HIGH"``
  / ``"MEDIUM"`` / ``"LOW"`` / ``"WARNING"`` / ``"OK"``).
* ``_SEVERITY_ORDER`` UPPER aliases (kept so the helper-return
  lookups stay byte-identical).
* ``_overall_risk`` return values (``"CRITICAL"`` / ``"HIGH"`` /
  ``"MEDIUM"`` / ``"LOW"``).
* ``_risk_driver`` ``sev.upper()`` precondition.
* Kind tags (``"DIRECT"`` / ``"TRANSITIVE"`` / ``"COLOCATED"``).
* Verdict-text comparison branches.

These are all flagged by the source comment block at the top of the
Risk-level helpers section ("Do NOT lowercase the helper-return /
rank-table / verdict-comparison sites").

This test pins the W759 contract:

1. The W762 AST drift-guard surfaces zero cmd_preflight violations.
2. The ``_run_check`` substrate defaults that flow into envelope
   ``severity`` slots are lowercase (``"warning"`` / ``"low"``) — not
   UPPER (``"WARNING"`` / ``"LOW"``).
3. The W847 internal-vocab helpers stay UPPER (the carve-out is
   intentional, not a missed cleanup).
4. ``_overall_risk`` is case-insensitive across both lowercase
   envelope-slot values and UPPER internal-vocab values — the W1088
   contract guarantees the W759 lowercase defaults don't silently
   default to rank 0.
"""

from __future__ import annotations

import ast
from pathlib import Path

from tests._helpers.repo_root import repo_root

_CMD_PATH = repo_root() / "src" / "roam" / "commands" / "cmd_preflight.py"
_UPPER_SEVERITY_LITERALS: frozenset[str] = frozenset({"CRITICAL", "HIGH", "MEDIUM", "LOW", "WARNING", "INFO", "ERROR"})


# ---------------------------------------------------------------------------
# Envelope-slot lowercase pin
# ---------------------------------------------------------------------------


def _envelope_severity_hits(path: Path) -> list[tuple[int, str]]:
    """Return ``(lineno, value)`` for every UPPER literal that sits as
    the VALUE of a literal ``"severity"`` key in *path*.

    Mirrors the W762 drift-guard classifier exactly so this test is a
    direct contract pin on the same surface.
    """
    text = path.read_text(encoding="utf-8", errors="replace")
    tree = ast.parse(text, filename=str(path))
    hits: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Dict):
            continue
        for k, v in zip(node.keys, node.values):
            if not isinstance(k, ast.Constant) or k.value != "severity":
                continue
            for sub in ast.walk(v):
                if not isinstance(sub, ast.Constant):
                    continue
                if not isinstance(sub.value, str):
                    continue
                if sub.value in _UPPER_SEVERITY_LITERALS:
                    hits.append((sub.lineno, sub.value))
    return hits


def test_cmd_preflight_envelope_severity_slots_all_lowercase() -> None:
    """No ``{"severity": "<UPPER>"}`` dict literal may remain in
    ``cmd_preflight.py``.

    Pre-W759 the W762 drift-guard flagged four sites (envelope dicts
    on the gate-decision / no-target / clean-pass / fitness paths).
    W759 closed the final remaining offender — a ``_run_check``
    substrate-default fall-through that surfaced as ``"WARNING"``
    inside the ``affected_tests`` floor dict.
    """
    hits = _envelope_severity_hits(_CMD_PATH)
    assert not hits, (
        "W759: cmd_preflight has UPPER-case severity literals inside "
        "summary-envelope `severity` slots. The canonical W547 "
        "vocabulary is lowercase (critical / error / warning / info). "
        "Offending sites (line, value):\n  " + "\n  ".join(f"L{ln}: {v!r}" for ln, v in hits)
    )


def test_w762_pending_allowlist_holds_no_cmd_preflight_entries() -> None:
    """The W762 drift-guard's ``_PRE_W762_PENDING`` allowlist must hold
    zero ``cmd_preflight.py`` entries.

    Pre-W759 the allowlist grandfathered four cmd_preflight sites
    (L199 / L318 / L340 / L887 of the pre-cleanup tree). W759 closed
    every one of them — any remaining entry means a cmd_preflight site
    was silently re-introduced after the W759 cleanup and is being
    shielded by the allowlist.
    """
    from tests.test_w762_severity_upper_drift import _PRE_W762_PENDING

    cmd_preflight_entries = [entry for entry in _PRE_W762_PENDING if entry.startswith("cmd_preflight.py:")]
    assert not cmd_preflight_entries, (
        "W759: cmd_preflight entries must be ZERO in _PRE_W762_PENDING. "
        f"Found: {cmd_preflight_entries}. The W759 cleanup wave closed "
        "every cmd_preflight envelope-slot UPPER literal; if the W762 "
        "drift-guard fires on a new one, the fix is to lowercase the "
        "literal, not to re-add it to the allowlist."
    )


# ---------------------------------------------------------------------------
# W847 internal-vocabulary preservation pin
# ---------------------------------------------------------------------------


def test_blast_severity_helper_returns_upper_per_w847() -> None:
    """``_blast_severity`` returns UPPER per the W847 internal-vocab
    carve-out.

    The helper feeds the agent-facing risk-tier display and the
    ``_overall_risk`` UPPER rank table. Lowercasing the helper would
    degrade the display contract without any W547 / SARIF benefit (it
    never flows into a SARIF level-map directly).
    """
    from roam.commands.cmd_preflight import _blast_severity

    assert _blast_severity(60, 20) == "CRITICAL"
    assert _blast_severity(25, 9) == "HIGH"
    assert _blast_severity(6, 4) == "MEDIUM"
    assert _blast_severity(0, 0) == "LOW"


def test_test_severity_helper_returns_upper_per_w847() -> None:
    """``_test_severity`` returns UPPER per the W847 internal-vocab
    carve-out.

    The helper's ``"WARNING"`` return (no affected tests found) is the
    upstream UPPER value that gets surfaced into the envelope ``severity``
    slot. The W847 carve-out keeps the helper UPPER; the W759 fix
    targets the ``_run_check`` substrate-default fall-through (which
    surfaces UPPER literally in a dict-literal sitting under the same
    ``"severity"`` key — W762's exact detection shape).
    """
    from roam.commands.cmd_preflight import _test_severity

    assert _test_severity(0, 0, 0) == "WARNING"
    assert _test_severity(1, 0, 0) == "OK"
    assert _test_severity(0, 5, 0) == "OK"


def test_overall_risk_is_case_insensitive_w1088_contract() -> None:
    """``_overall_risk`` must accept BOTH lowercase envelope-slot values
    AND UPPER internal-vocab values.

    W1088 added ``.lower()`` normalization at the ``_SEVERITY_ORDER``
    lookup site so the W759 envelope-slot lowercase values
    (``"warning"`` / ``"low"``) and the W847 helper-return UPPER values
    (``"WARNING"`` / ``"LOW"``) resolve to the same rank. Without this
    contract, lowercasing the W759 defaults would silently drop them
    to rank 0 and the overall-risk rollup would mis-classify every
    degraded substrate.
    """
    from roam.commands.cmd_preflight import _overall_risk

    # Pure UPPER (legacy / helper-return shape).
    assert _overall_risk("CRITICAL", "LOW", "OK", "OK", "OK", "OK") == "CRITICAL"
    assert _overall_risk("HIGH", "OK", "OK", "OK", "OK", "OK") == "HIGH"
    assert _overall_risk("WARNING", "OK", "OK", "OK", "OK", "OK") == "MEDIUM"

    # Pure lowercase (W759 envelope-slot shape).
    assert _overall_risk("critical", "low", "ok", "ok", "ok", "ok") == "CRITICAL"
    assert _overall_risk("warning", "low", "ok", "ok", "ok", "ok") == "MEDIUM"
    assert _overall_risk("low", "ok", "ok", "ok", "ok", "ok") == "LOW"

    # Mixed (envelope-slot + helper-return co-mingled in the live
    # envelope assembly; the W759 fix means substrate-default falls
    # through as lowercase while ``_run_check`` happy-path stays UPPER).
    assert _overall_risk("warning", "HIGH", "ok", "ok", "ok", "ok") == "HIGH"
    assert _overall_risk("low", "WARNING", "ok", "ok", "ok", "ok") == "MEDIUM"


# ---------------------------------------------------------------------------
# Canonical contract drift-guard (mirrors W761 / W762 sister tests)
# ---------------------------------------------------------------------------


def test_w547_canonical_vocabulary_stays_lowercase() -> None:
    """Pin the canonical W547 vocabulary the W759 migration targets.

    Mirrors the equivalent assertions in
    ``tests/test_w761_invariants_bus_factor_complexity_lowercase.py``
    and ``tests/test_w762_severity_upper_drift.py``. If W547 ever
    drifts to a different vocabulary, the W759 envelope-slot pin
    needs to be re-cut against the new contract.
    """
    from roam.output._severity import SEVERITY_LEVELS

    assert SEVERITY_LEVELS == frozenset({"critical", "error", "warning", "info"}), (
        "W759: canonical W547 vocabulary drifted — re-cut the W759 envelope-slot pin against the new contract."
    )
