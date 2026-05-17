"""W1100: surface malformed ``agent_contract`` shape as Pattern-2 partial_success.

W1007 added ``agent_contract`` to ``_ALWAYS_PRESERVED_LIST_FIELDS`` so the
malformed list shape stays visible at envelope top-level. But preservation
alone is insufficient: a consumer can still read the envelope's
``summary.partial_success: false`` (or absent) and treat the call as a
clean success despite the schema violation.

W1100 closes the loop: when ``strip_list_payloads`` observes
``agent_contract`` as a list (any length, including empty), it lifts a
structured signal into ``summary``:

* ``summary.partial_success: true`` — override any pre-existing ``false``
  because schema violation is non-recoverable signal.
* ``summary.schema_violations: ["agent_contract_shape"]`` — closed-string
  disclosure list extending (never replacing) any caller-supplied list.

The W1007 invariant (the malformed list itself is preserved at envelope
top-level) MUST still hold — W1100 only adds the summary signal.
"""

from __future__ import annotations

import pytest

from roam.output.formatter import strip_list_payloads


def _raw_envelope(**extra) -> dict:
    """Build a raw envelope bypassing ``json_envelope`` so malformed
    shapes the canonical constructor would reject stay reachable.
    """
    env = {
        "command": "test",
        "schema": "roam-envelope-v1",
        "schema_version": "1.1.0",
        "summary": {"verdict": "ok"},
    }
    env.update(extra)
    return env


class TestSchemaViolationLiftsPartialSuccess:
    def test_empty_agent_contract_list_lifts_partial_success(self):
        """``agent_contract: []`` → ``summary.partial_success: true`` +
        ``summary.schema_violations: ["agent_contract_shape"]``. The
        malformed list itself is preserved (W1007 invariant).
        """
        env = _raw_envelope(agent_contract=[])
        result = strip_list_payloads(env)
        # W1007 invariant: list preserved at top-level.
        assert result.get("agent_contract") == []
        # W1100: schema violation lifted into summary.
        assert result["summary"]["partial_success"] is True
        assert result["summary"]["schema_violations"] == ["agent_contract_shape"]

    def test_nonempty_agent_contract_list_lifts_partial_success(self):
        """A non-empty list ``["fact"]`` is just as malformed as an empty
        one — the canonical shape is a DICT. Same disclosure pair fires.
        """
        env = _raw_envelope(agent_contract=["fact"])
        result = strip_list_payloads(env)
        assert result.get("agent_contract") == ["fact"]
        assert result["summary"]["partial_success"] is True
        assert result["summary"]["schema_violations"] == ["agent_contract_shape"]

    def test_canonical_dict_does_not_flip_partial_success(self):
        """Canonical ``agent_contract: {"facts": [...]}`` is well-formed —
        no partial_success flip, no schema_violations injection.
        """
        contract = {
            "facts": ["12 cycles"],
            "risks": [],
            "next_commands": ["roam preflight"],
            "confidence": 0.8,
        }
        env = _raw_envelope(agent_contract=contract)
        result = strip_list_payloads(env)
        assert result.get("agent_contract") == contract
        # No schema violation injection.
        assert "partial_success" not in result["summary"]
        assert "schema_violations" not in result["summary"]

    def test_caller_partial_success_false_overridden_by_violation(self):
        """A caller pre-set ``partial_success: false`` MUST be overridden
        to ``true`` when ``agent_contract`` is malformed — schema
        violation is non-recoverable and wins over the stale flag.
        """
        env = _raw_envelope(agent_contract=[])
        env["summary"]["partial_success"] = False
        result = strip_list_payloads(env)
        assert result["summary"]["partial_success"] is True
        assert result["summary"]["schema_violations"] == ["agent_contract_shape"]

    def test_existing_schema_violations_list_extended_not_replaced(self):
        """A caller-supplied ``schema_violations`` list (for an orthogonal
        violation) MUST be extended, not replaced. Both violations stay
        visible. Caller's ``partial_success: true`` stays true (idempotent).
        """
        env = _raw_envelope(agent_contract=[])
        env["summary"]["partial_success"] = True
        env["summary"]["schema_violations"] = ["other_violation_kind"]
        result = strip_list_payloads(env)
        assert result["summary"]["partial_success"] is True
        # Both kinds present; existing entry kept, new one appended.
        assert result["summary"]["schema_violations"] == [
            "other_violation_kind",
            "agent_contract_shape",
        ]

    def test_no_agent_contract_key_no_violation_injected(self):
        """Envelopes that omit ``agent_contract`` entirely produce no
        schema_violations injection (Pattern 2: absent ≠ malformed).
        """
        env = _raw_envelope()
        result = strip_list_payloads(env)
        assert "schema_violations" not in result["summary"]
        # partial_success not auto-injected when no violation present.
        assert "partial_success" not in result["summary"]


if __name__ == "__main__":  # pragma: no cover
    pytest.main([__file__, "-x", "-v"])
