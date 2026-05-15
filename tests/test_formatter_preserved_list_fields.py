"""W1006: extend ``strip_list_payloads`` preservation to ``errors`` + ``redactions``.

W1000 sealed the Pattern 2 silent-fallback hole for ``warnings_out``;
W1006 extends the closed allow-set to two additional disclosure-shaped
fields with the same obligation:

* ``errors`` — emitted at top-level by multiple commands
  (``batch-search``, ``cga-verify``, ``plugins``, ``rules-validate``,
  ``ws``). The universal disclosure idiom for "the producer hit
  recoverable failures the consumer needs to know about".
* ``redactions`` — emitted at top-level by ``pr-bundle`` and
  ``evidence-doctor``. The producer comments explicitly mark this
  field as "Pattern 2 — explicit absence"; without it the agent
  cannot tell a clean evidence packet from a redaction-heavy one.

This test fixture mirrors the W1000 shape (one fixture per preserved
field, three round-trip shapes per field: short, long, empty) and
locks the W1006 fix in:

* short lists (≤10) survive intact, no truncated sibling;
* long lists (>10) keep their first 10 entries and grow a sibling
  ``<field>_truncated`` int naming how many were dropped;
* empty lists survive as ``[]``, no truncated sibling;
* the ``summary.truncated`` advisory still fires correctly — flipped
  when EITHER a non-preserved list was dropped OR a preserved list
  was capped, but NOT when a preserved list round-trips intact.
"""

from __future__ import annotations

import pytest

from roam.output.formatter import json_envelope, strip_list_payloads


def _envelope(**extra) -> dict:
    return json_envelope("test", summary={"verdict": "ok"}, **extra)


# --------------------------------------------------------------------- errors


class TestErrorsPreserved:
    def test_small_errors_survives_strip(self):
        env = _envelope(errors=["boom", "kaboom", "fizzle"])
        result = strip_list_payloads(env)
        assert result.get("errors") == ["boom", "kaboom", "fizzle"]
        assert result["summary"]["detail_available"] is True
        # Round-trip intact — not a truncation event.
        assert result["summary"].get("truncated") is not True
        assert "errors_truncated" not in result

    def test_empty_errors_survives_as_empty_list(self):
        env = _envelope(errors=[])
        result = strip_list_payloads(env)
        # Pattern 2 discipline: the key stays present, empty list
        # communicates "no errors".
        assert result.get("errors") == []
        assert result["summary"].get("truncated") is not True

    def test_long_errors_truncates_with_counter(self):
        errors = [f"err-{i}" for i in range(15)]
        env = _envelope(errors=errors)
        result = strip_list_payloads(env)
        kept = result.get("errors")
        assert isinstance(kept, list)
        assert len(kept) == 10
        assert kept == [f"err-{i}" for i in range(10)]
        assert result.get("errors_truncated") == 5
        # Truncating a preserved list IS a truncation event.
        assert result["summary"].get("truncated") is True


# ----------------------------------------------------------------- redactions


class TestRedactionsPreserved:
    def test_small_redactions_survives_strip(self):
        env = _envelope(redactions=["secret", "pii"])
        result = strip_list_payloads(env)
        assert result.get("redactions") == ["secret", "pii"]
        assert result["summary"]["detail_available"] is True
        assert result["summary"].get("truncated") is not True
        assert "redactions_truncated" not in result

    def test_empty_redactions_survives_as_empty_list(self):
        env = _envelope(redactions=[])
        result = strip_list_payloads(env)
        # Pattern 2 discipline: an empty redactions list is
        # informative — it asserts "no axes were masked".
        assert result.get("redactions") == []
        assert result["summary"].get("truncated") is not True

    def test_long_redactions_truncates_with_counter(self):
        redactions = [f"reason-{i}" for i in range(13)]
        env = _envelope(redactions=redactions)
        result = strip_list_payloads(env)
        kept = result.get("redactions")
        assert isinstance(kept, list)
        assert len(kept) == 10
        assert kept == [f"reason-{i}" for i in range(10)]
        assert result.get("redactions_truncated") == 3
        assert result["summary"].get("truncated") is True


# ---------------------------------------------- cross-cutting truncated flag


class TestTruncatedFlagSemantics:
    """``summary.truncated`` correctness across the expanded set."""

    def test_preserved_only_no_truncation_does_not_flip_truncated(self):
        """All preserved lists round-trip — no elision happened."""
        env = _envelope(
            warnings_out=["w"],
            errors=["e"],
            redactions=["r"],
        )
        result = strip_list_payloads(env)
        assert result.get("warnings_out") == ["w"]
        assert result.get("errors") == ["e"]
        assert result.get("redactions") == ["r"]
        assert result["summary"]["detail_available"] is True
        assert result["summary"].get("truncated") is not True

    def test_preserved_list_capped_flips_truncated(self):
        """Capping any preserved list IS a truncation event."""
        env = _envelope(errors=[f"e{i}" for i in range(12)])
        result = strip_list_payloads(env)
        assert result.get("errors_truncated") == 2
        assert result["summary"].get("truncated") is True

    def test_non_preserved_list_drop_flips_truncated(self):
        """Dropping a non-preserved list still flips truncated."""
        env = _envelope(
            errors=["e"],
            other_findings=[{"a": 1}],
        )
        result = strip_list_payloads(env)
        # errors preserved.
        assert result.get("errors") == ["e"]
        # other_findings dropped.
        assert "other_findings" not in result
        # truncated flipped because a real list was dropped.
        assert result["summary"].get("truncated") is True

    def test_non_preserved_list_with_empty_redactions(self):
        """Empty preserved list + non-empty dropped list still flips."""
        env = _envelope(
            redactions=[],
            other_findings=[{"a": 1}, {"a": 2}],
        )
        result = strip_list_payloads(env)
        assert result.get("redactions") == []
        assert "other_findings" not in result
        assert result["summary"].get("truncated") is True


# ---------------------------------------------- preserved-set membership lock


def test_w1006_preserved_set_membership():
    """Lock the W1006 set — the three Pattern 2 disclosure lists must
    all be preserved. If this test fails because the set grew, that's a
    design change — re-run the W1006 audit (does the new field qualify
    as a Pattern 2 disclosure list at envelope top-level?) before
    relaxing the lock.
    """
    from roam.output.formatter import _ALWAYS_PRESERVED_LIST_FIELDS

    assert "warnings_out" in _ALWAYS_PRESERVED_LIST_FIELDS
    assert "errors" in _ALWAYS_PRESERVED_LIST_FIELDS
    assert "redactions" in _ALWAYS_PRESERVED_LIST_FIELDS


if __name__ == "__main__":  # pragma: no cover
    pytest.main([__file__, "-x", "-v"])
