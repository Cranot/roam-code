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


# ----------------------------------------------------- W1007 agent_contract


class TestAgentContractPreserved:
    """W1007: ``agent_contract`` joins the preserved set defensively.

    The canonical shape of ``agent_contract`` is a dict (per
    ``_derive_agent_contract`` — ``{facts, risks, next_commands,
    confidence}``). Strip only fires on list-valued top-level fields,
    so the canonical dict already passes through the ``else`` branch
    untouched.

    The W1007 hole was the malformed ``agent_contract: []`` shape: if a
    producer ever emits the empty-list mistake, the strip would silently
    drop it — making the schema mistake invisible to the agent and
    forever-debugged. Adding ``agent_contract`` to the preserved set
    keeps the malformed disclosure visible so consumers (and the
    per-emitter sweep) can detect and react.

    These tests construct raw envelope dicts (NOT via ``json_envelope``,
    which auto-injects the canonical dict shape) so the empty-list
    pathological case is reachable.
    """

    def _raw_envelope(self, **extra) -> dict:
        """Build a raw envelope dict bypassing ``json_envelope`` so we
        can construct shapes the producer should never emit but might.
        """
        env = {
            "command": "test",
            "schema": "roam-envelope-v1",
            "schema_version": "1.1.0",
            "summary": {"verdict": "ok"},
        }
        env.update(extra)
        return env

    def test_empty_agent_contract_list_survives_strip(self):
        """An ``agent_contract: []`` (producer mistake) is preserved, not
        silently dropped. Consumers can then detect the wrong shape and
        report the producer bug instead of guessing why no contract
        showed up.
        """
        env = self._raw_envelope(agent_contract=[])
        result = strip_list_payloads(env)
        assert result.get("agent_contract") == []
        # Empty preserved list is NOT a truncation event.
        assert result["summary"].get("truncated") is not True

    def test_canonical_agent_contract_dict_passes_through(self):
        """The canonical dict-shaped ``agent_contract`` still passes
        through unchanged — non-list values fall into the ``else``
        branch and the preserved-set membership is a no-op for them.
        """
        contract = {
            "facts": ["12 cycles"],
            "risks": [],
            "next_commands": ["roam preflight"],
            "confidence": 0.8,
        }
        env = self._raw_envelope(agent_contract=contract)
        result = strip_list_payloads(env)
        assert result.get("agent_contract") == contract

    def test_no_agent_contract_key_stays_absent(self):
        """Envelopes that omit ``agent_contract`` entirely do NOT have
        the key auto-injected by the strip helper. Pattern 2 discipline:
        absent and intentionally-empty are distinct states.
        """
        env = self._raw_envelope()
        # Construct without agent_contract key, sanity-check shape.
        assert "agent_contract" not in env
        result = strip_list_payloads(env)
        assert "agent_contract" not in result


def test_w1007_agent_contract_in_preserved_set():
    """Lock W1007: ``agent_contract`` must stay in the preserved set so
    the malformed empty-list shape stays visible at envelope top-level.
    """
    from roam.output.formatter import _ALWAYS_PRESERVED_LIST_FIELDS

    assert "agent_contract" in _ALWAYS_PRESERVED_LIST_FIELDS


# --------------------------------------------------------------- W1008 list_counts


class TestListCountsSurfaced:
    """W1008: ``strip_list_payloads`` now surfaces ``list_counts`` at
    envelope top-level for non-preserved lists it dropped. Agents read
    this to decide whether to re-request with ``--detail`` (e.g.
    "findings was stripped but had 12 entries — worth fetching").

    The helper already COMPUTED ``list_counts`` (drove the
    ``summary.truncated`` flag) but never emitted it. W1008 closes that
    drive-by gap from W1007.
    """

    def test_dropped_list_count_surfaced_at_top_level(self):
        """A 12-entry non-preserved list gets dropped, but the count
        survives in the top-level ``list_counts`` dict.
        """
        findings = [{"id": i} for i in range(12)]
        env = _envelope(findings=findings)
        result = strip_list_payloads(env)
        # The list itself is dropped (non-preserved).
        assert "findings" not in result
        # But the count is surfaced.
        assert result.get("list_counts") == {"findings": 12}
        # Sanity: truncation flag still flips.
        assert result["summary"].get("truncated") is True

    def test_multiple_dropped_lists_all_surfaced(self):
        """Multiple non-preserved lists each get a ``list_counts`` entry."""
        env = _envelope(
            findings=[{"id": i} for i in range(8)],
            hotspots=[{"id": i} for i in range(3)],
        )
        result = strip_list_payloads(env)
        assert "findings" not in result
        assert "hotspots" not in result
        assert result.get("list_counts") == {"findings": 8, "hotspots": 3}

    def test_no_dropped_lists_emits_empty_list_counts(self):
        """W1101: When no non-preserved lists are present, the helper
        still emits ``list_counts: {}`` for symmetry with the W1006
        redactions[] precedent. An empty dict tells the consumer
        "strip_list_payloads ran and dropped nothing" vs an absent key
        which would be indistinguishable from "envelope wasn't processed".
        """
        env = _envelope(verdict_extra="scalar-only")
        result = strip_list_payloads(env)
        assert result.get("list_counts") == {}

    def test_preserved_lists_not_in_list_counts(self):
        """Preserved lists (``warnings_out``, ``errors``, ``redactions``,
        ``agent_contract``) are NOT counted in ``list_counts`` -- they
        aren't stripped, so there's nothing to count. Only the
        non-preserved ``findings`` list shows up.
        """
        env = _envelope(
            warnings_out=["w1", "w2"],
            errors=["e1"],
            redactions=["r1", "r2", "r3"],
            findings=[{"id": i} for i in range(4)],
        )
        result = strip_list_payloads(env)
        # Preserved lists survive.
        assert result.get("warnings_out") == ["w1", "w2"]
        assert result.get("errors") == ["e1"]
        assert result.get("redactions") == ["r1", "r2", "r3"]
        # Non-preserved counted.
        assert result.get("list_counts") == {"findings": 4}

    def test_empty_dropped_list_still_counted_as_zero(self):
        """Empty non-preserved lists ARE counted (as 0). The disclosure
        is "this field was a list, here's its size" -- 0 is informative
        too. The truncated flag does NOT flip on zero-only drops since
        ``has_non_empty_lists`` requires c > 0.
        """
        env = _envelope(findings=[])
        result = strip_list_payloads(env)
        assert "findings" not in result
        assert result.get("list_counts") == {"findings": 0}
        # No non-empty drops -> truncated stays unset.
        assert result["summary"].get("truncated") is not True


# ----------------------------------------- W1028 deferred-candidate drift-guard


def test_w1028_deferred_candidates_not_silently_added():
    """W1028: lock the W1006 / W1028 audit verdict into a drift-guard.

    The W1006 audit named 4 candidate fields for ``_ALWAYS_PRESERVED_LIST_FIELDS``
    expansion. W1028 re-ran the audit (2026-05-16) and confirmed each remains
    DEFER:

    * ``dropped_keys`` — no producer in source.
    * ``dropped_reasons`` — only nested under ``summary`` (already preserved).
    * ``stale_reasons`` — no top-level emitter calls ``strip_list_payloads``.
    * ``enum_violations`` / ``trust_warnings`` / ``bundle_warnings`` — none
      of their producer commands call ``strip_list_payloads``;
      ``bundle_warnings`` is aliased into ``warnings_out`` (already preserved).

    If this test fails because a candidate joined the set, that's the
    intended path — but the editor must (a) update the inline comment in
    ``src/roam/output/formatter.py`` naming the new producer + consumer,
    (b) add a preservation test mirroring the W1006 / W1007 fixtures, and
    (c) remove the candidate from the assertion below. The size check
    keeps the set from quietly growing past the audited boundary.
    """
    from roam.output.formatter import _ALWAYS_PRESERVED_LIST_FIELDS

    # The canonical preserved set is the W1000 + W1006 + W1007 closure.
    # Re-audit (W1028, W1029+, ...) before changing this count.
    assert len(_ALWAYS_PRESERVED_LIST_FIELDS) == 4, (
        "Preserved-list set size changed without an audit update. "
        "Re-run the W1006 / W1028 audit pattern (Pattern-2 disclosure list "
        "at envelope top-level + consumer that calls strip_list_payloads) "
        "and document the new field in formatter.py's inline comment."
    )
    assert _ALWAYS_PRESERVED_LIST_FIELDS == frozenset({"warnings_out", "errors", "redactions", "agent_contract"})

    # Deferred candidates from W1006 — pin them as deliberately absent so
    # a future editor noticing the comment cannot silently graduate one
    # without ripping out this guard.
    deferred = (
        "dropped_keys",
        "dropped_reasons",
        "stale_reasons",
        "enum_violations",
        "trust_warnings",
        "bundle_warnings",
    )
    for name in deferred:
        assert name not in _ALWAYS_PRESERVED_LIST_FIELDS, (
            f"{name!r} graduated from the W1006 / W1028 deferred-candidate "
            f"watch-list without updating the inline comment OR removing "
            f"this drift-guard. Re-audit the producer/consumer pair and "
            f"either (a) keep it deferred and revert, or (b) add a "
            f"preservation test mirroring TestErrorsPreserved + update "
            f"both this assertion and the inline comment."
        )


if __name__ == "__main__":  # pragma: no cover
    pytest.main([__file__, "-x", "-v"])
