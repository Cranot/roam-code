"""LAW 4 stragglers (W21.7) — anchor-string regression tests for the
four commands that survived W17.3 but still leaked awkward auto-derived
facts in specific edge cases.

Each test pins the fix-strategy at the source level (so a regression
that re-introduces the old field name / drops the explicit facts is
caught at PR time) and, where practical, also exercises the envelope
construction to lock the resulting ``agent_contract.facts`` shape.

Scope:

- ``bus-factor`` — ``directory_count`` → ``directories_analyzed``
  so the humanizer produces ``"N directories analyzed"`` instead of
  ``"directory count N"``.
- ``auth-gaps`` — zero-severity counts are suppressed from the facts
  list (no more ``"0 high findings"`` / ``"0 medium findings"`` /
  ``"0 low findings"`` noise).
- ``hotspots`` — when ``total == 0`` the verdict + an explicit
  "no hotspots above threshold" anchor replace the ``"0 total findings"``
  noise.
- ``capabilities`` — explicit facts pin the verdict + AI-safe-share
  string so the auto-derive doesn't bolt on a redundant ``"count N"``
  fact alongside the concrete verdict.
"""

from __future__ import annotations

import sys

from tests._helpers.repo_root import repo_root

REPO_ROOT = repo_root()
_SRC_DIR = REPO_ROOT / "src"
if _SRC_DIR.is_dir() and str(_SRC_DIR) not in sys.path:
    sys.path.insert(0, str(_SRC_DIR))


# ---------------------------------------------------------------------------
# A — bus-factor: directory_count → directories_analyzed
# ---------------------------------------------------------------------------


def test_bus_factor_renamed_field_produces_clean_fact():
    """``directory_count`` renamed to ``directories_analyzed`` so the
    LAW 4 humanizer produces ``"N directories analyzed"`` instead of
    the awkward ``"directory count N"``.

    Verifies both the source-level rename and the auto-derived fact
    string produced by the formatter for the new key.
    """
    src = (REPO_ROOT / "src" / "roam" / "commands" / "cmd_bus_factor.py").read_text(encoding="utf-8")
    # The new key MUST appear at least twice (no-data envelope + main envelope):
    assert src.count('"directories_analyzed"') >= 2, "Both bus-factor envelopes must use the renamed field"
    # The old key MUST be gone from the envelope-assembly code:
    assert '"directory_count":' not in src, "Old ``directory_count`` field name must be removed from envelope assembly"

    # And the formatter humanizer must render the new key cleanly.
    from roam.output.formatter import json_envelope

    env = json_envelope(
        "bus-factor",
        summary={
            "verdict": "bus factor 1 (min), 2 high-risk, top risk: src/",
            "directories_analyzed": 65,
            "high_risk": 2,
        },
    )
    facts = env["agent_contract"]["facts"]
    # Clean concrete-noun anchor — "65 directories analyzed", NOT
    # "directory count 65" and NOT "65 directories analyzed findings".
    assert any("65 directories analyzed" == f for f in facts), facts
    joined = " | ".join(facts)
    assert "directory count" not in joined, facts
    assert "directories analyzed findings" not in joined, facts


# ---------------------------------------------------------------------------
# B — auth-gaps: zero-severity rows suppressed
# ---------------------------------------------------------------------------


def test_auth_gaps_zero_count_suppressed():
    """When ``auth-gaps`` finds zero high/medium/low gaps, the explicit
    facts list must omit those zero buckets entirely. Pre-W21.7 the
    auto-derive emitted three lines of ``"0 X findings"`` noise.
    """
    src = (REPO_ROOT / "src" / "roam" / "commands" / "cmd_auth_gaps.py").read_text(encoding="utf-8")
    # The fix uses an explicit-facts loop that only appends non-zero
    # severities. The construct should be present in source so future
    # edits can't silently revert to auto-derive.
    assert "explicit_facts" in src, "auth-gaps must build an explicit_facts list for the agent_contract"
    # W607-ED refactored agent_contract into envelope_kwargs dict; accept
    # BOTH the legacy kwarg-call form AND the dict-literal form so the
    # invariant ("facts must be pinned, not auto-derived") survives the
    # additive aggregation-phase plumbing.
    assert 'agent_contract={"facts": explicit_facts}' in src or '"agent_contract": {"facts": explicit_facts}' in src, (
        "auth-gaps must pin the explicit facts onto agent_contract "
        "(either as a kwarg to json_envelope() OR as a key in the "
        "envelope_kwargs dict passed to json_envelope())"
    )
    # The zero-skip guard:
    assert "if n > 0" in src, "auth-gaps must skip zero-severity rows from the facts list"

    # Verify the resulting envelope shape with a synthesized summary —
    # mirrors what the command emits when total == 0.
    from roam.output.formatter import json_envelope

    explicit_facts = ["0 auth gap(s) found"]
    # The command would not append any per-severity facts because all are 0.
    env = json_envelope(
        "auth-gaps",
        summary={
            "verdict": "0 auth gap(s) found",
            "total": 0,
            "high": 0,
            "medium": 0,
            "low": 0,
            "route_gaps": 0,
            "controller_gaps": 0,
        },
        agent_contract={"facts": explicit_facts},
    )
    facts = env["agent_contract"]["facts"]
    joined = " | ".join(facts)
    # Zero-severity noise MUST NOT appear:
    assert "0 high" not in joined, facts
    assert "0 medium" not in joined, facts
    assert "0 low" not in joined, facts
    # The verdict survives as the only fact:
    assert any("0 auth gap(s) found" in f for f in facts), facts


def test_auth_gaps_nonzero_severity_still_surfaces():
    """When auth-gaps DOES find non-zero severities, those facts must
    still be emitted (we only suppress the zero rows)."""
    from roam.output.formatter import json_envelope

    explicit_facts = [
        "3 auth gap(s) found",
        "2 high-severity auth gaps",
        "1 medium-severity auth gaps",
    ]
    env = json_envelope(
        "auth-gaps",
        summary={
            "verdict": "3 auth gap(s) found",
            "total": 3,
            "high": 2,
            "medium": 1,
            "low": 0,
            "route_gaps": 1,
            "controller_gaps": 2,
        },
        agent_contract={"facts": explicit_facts},
    )
    facts = env["agent_contract"]["facts"]
    joined = " | ".join(facts)
    assert "2 high-severity auth gaps" in joined, facts
    assert "1 medium-severity auth gaps" in joined, facts
    # Low (which is 0) still suppressed:
    assert "0 low" not in joined, facts


# ---------------------------------------------------------------------------
# C — hotspots: explicit-facts when total == 0
# ---------------------------------------------------------------------------


def test_hotspots_zero_total_explicit_fact():
    """When ``hotspots --security`` reports zero hotspots, an explicit
    ``"no security hotspots above threshold"`` fact replaces the
    auto-derived ``"0 total findings"`` noise.
    """
    src = (REPO_ROOT / "src" / "roam" / "commands" / "cmd_hotspots.py").read_text(encoding="utf-8")
    # Source-level: the explicit-facts branch must exist.
    assert "no security hotspots above threshold" in src, (
        "hotspots must pin an explicit healthy-codebase fact when total == 0"
    )
    assert "if total == 0" in src, "hotspots must explicitly key off the zero-total case"

    # Envelope construction — synthesise the zero-hotspots payload and
    # verify the facts list contains the explicit anchor.
    from roam.output.formatter import json_envelope

    verdict = "No security hotspots detected"
    env = json_envelope(
        "hotspots",
        summary={
            "verdict": verdict,
            "mode": "security",
            "total": 0,
            "reachable": 0,
            "critical": 0,
            "high": 0,
            "medium": 0,
        },
        mode="security",
        signals={"entrypoints": 0, "files_scanned": 0},
        hotspots=[],
        agent_contract={
            "facts": [
                verdict,
                "no security hotspots above threshold; codebase is healthy",
            ],
        },
    )
    facts = env["agent_contract"]["facts"]
    joined = " | ".join(facts)
    assert "no security hotspots above threshold" in joined, facts
    # And the auto-derive noise MUST NOT have leaked back in:
    assert "0 total findings" not in joined, facts
    assert "0 critical findings" not in joined, facts


# ---------------------------------------------------------------------------
# D — capabilities: no redundant "count N" fact
# ---------------------------------------------------------------------------


def test_capabilities_no_redundant_count_fact():
    """``capabilities`` emits explicit ``agent_contract.facts`` so the
    redundant ``"count N"`` auto-derived fact (the verdict already
    names the count) is suppressed.
    """
    src = (REPO_ROOT / "src" / "roam" / "commands" / "cmd_capabilities.py").read_text(encoding="utf-8")
    # The explicit-facts construct must be present.
    assert "explicit_facts" in src, "capabilities must build an explicit_facts list"
    assert "agent_contract={" in src, "capabilities must pin agent_contract on the envelope"

    # Envelope construction — emulate the command's verdict + explicit
    # facts and verify the facts list does NOT include "count 10".
    from roam.output.formatter import json_envelope

    verdict = "10 registered capabilities (7 AI-safe)"
    explicit_facts = [
        verdict,
        "7 of 10 AI-safe capabilities",
    ]
    env = json_envelope(
        "capabilities",
        summary={
            "verdict": verdict,
            "count": 10,
            "category_filter": None,
            "ai_safe_only": False,
        },
        agent_contract={"facts": explicit_facts},
        capabilities=[],
    )
    facts = env["agent_contract"]["facts"]
    joined = " | ".join(facts)
    # The verdict survives, the explicit AI-safe share survives ...
    assert "10 registered capabilities" in joined, facts
    assert "7 of 10 AI-safe capabilities" in joined, facts
    # ... but the redundant auto-derived "count 10" must NOT be present.
    assert "count 10" not in joined, facts
