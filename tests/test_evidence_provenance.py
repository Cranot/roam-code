"""W282 - evidence-provenance vocabulary + helper tests.

Covers the small vocabulary + helper wave per the W282 directive:

* ``PROVENANCE_SOURCES`` is a 10-element frozenset with the exact
  literals listed in the directive (drift guard).
* :func:`provenance_label` returns the bare source literal when no
  detail is provided.
* :func:`provenance_label` returns the compact ``"<source>(<detail>)"``
  form when a detail is provided.
* :func:`provenance_label` raises ``ValueError`` on unknown sources
  (silently accepting an unknown source would defeat the closed-
  enumeration discipline).
* :func:`provenance_label` is deterministic: same input -> same output,
  no clock / UUID / env dependence.

This wave is vocabulary + helper only. NO producer wires this in yet;
broader producer wiring is a separate later wave (W289+).
"""

from __future__ import annotations

import pytest

from roam.evidence import PROVENANCE_SOURCES, provenance_label


# ---------------------------------------------------------------------------
# Vocabulary drift guard
# ---------------------------------------------------------------------------


def test_provenance_sources_drift_guard() -> None:
    """``PROVENANCE_SOURCES`` is the 10-element frozenset per the W282 directive.

    The exact literal set is fixed by the directive; future waves can
    extend it with a deliberate count bump and a corresponding
    drift-guard update. The frozenset must be immutable.
    """
    assert isinstance(PROVENANCE_SOURCES, frozenset)
    assert len(PROVENANCE_SOURCES) == 10
    assert PROVENANCE_SOURCES == frozenset({
        "ci_env_var",
        "git_config",
        "run_ledger",
        "cli_flag",
        "env_var",
        "producer_envelope",
        "audit_trail",
        "mcp_receipt",
        "inferred",
        "unknown",
    })
    # Drift guard: frozenset is immutable.
    with pytest.raises(AttributeError):
        PROVENANCE_SOURCES.add("rogue_source")  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# provenance_label helper
# ---------------------------------------------------------------------------


def test_provenance_label_returns_source_when_no_detail() -> None:
    """Bare-source form: ``provenance_label("git_config") == "git_config"``."""
    assert provenance_label("git_config") == "git_config"
    assert provenance_label("ci_env_var") == "ci_env_var"
    assert provenance_label("run_ledger") == "run_ledger"
    assert provenance_label("cli_flag") == "cli_flag"
    assert provenance_label("env_var") == "env_var"
    assert provenance_label("producer_envelope") == "producer_envelope"
    assert provenance_label("audit_trail") == "audit_trail"
    assert provenance_label("mcp_receipt") == "mcp_receipt"
    assert provenance_label("inferred") == "inferred"
    assert provenance_label("unknown") == "unknown"


def test_provenance_label_returns_compact_form_with_detail() -> None:
    """Compact form: ``"<source>(<detail>)"`` when detail is provided."""
    assert (
        provenance_label("git_config", detail="user.email")
        == "git_config(user.email)"
    )
    assert (
        provenance_label("ci_env_var", detail="GITHUB_ACTOR")
        == "ci_env_var(GITHUB_ACTOR)"
    )
    assert (
        provenance_label("env_var", detail="ROAM_AGENT_ID")
        == "env_var(ROAM_AGENT_ID)"
    )
    assert (
        provenance_label("cli_flag", detail="--agent")
        == "cli_flag(--agent)"
    )


def test_provenance_label_raises_on_unknown_source() -> None:
    """Unknown sources raise ValueError naming PROVENANCE_SOURCES.

    The closed-enumeration discipline requires construction-time
    validation; silently accepting an unknown source would defeat the
    purpose of having a closed set.
    """
    with pytest.raises(ValueError, match="PROVENANCE_SOURCES"):
        provenance_label("totally-fake")
    with pytest.raises(ValueError, match="PROVENANCE_SOURCES"):
        provenance_label("totally-fake", detail="ignored")
    # Adjacent vocabulary literals must NOT leak through as valid
    # provenance sources just because they're spelled similarly to other
    # closed enumerations.
    with pytest.raises(ValueError, match="PROVENANCE_SOURCES"):
        provenance_label("direct")  # CLAIM_CONFIDENCES literal
    with pytest.raises(ValueError, match="PROVENANCE_SOURCES"):
        provenance_label("verified_ci")  # ACTOR_TRUST_TIERS literal


def test_provenance_label_is_deterministic() -> None:
    """Same input -> same output, repeatedly. No hidden state."""
    # Repeated calls produce byte-identical output.
    a1 = provenance_label("git_config")
    a2 = provenance_label("git_config")
    assert a1 == a2
    # With detail too.
    b1 = provenance_label("git_config", detail="user.email")
    b2 = provenance_label("git_config", detail="user.email")
    assert b1 == b2
    # And across all 10 source literals.
    expected = {
        "ci_env_var": "ci_env_var",
        "git_config": "git_config",
        "run_ledger": "run_ledger",
        "cli_flag": "cli_flag",
        "env_var": "env_var",
        "producer_envelope": "producer_envelope",
        "audit_trail": "audit_trail",
        "mcp_receipt": "mcp_receipt",
        "inferred": "inferred",
        "unknown": "unknown",
    }
    for source, label in expected.items():
        assert provenance_label(source) == label
        # Call twice to defend against any accidental memoisation /
        # state pollution.
        assert provenance_label(source) == label
