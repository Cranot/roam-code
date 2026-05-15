"""W1000: ``strip_list_payloads`` must preserve ``warnings_out``.

The default-detail-off envelope serializer historically stripped every
list-valued top-level field. W994 + W995 added a ``warnings_out`` list
to commands like ``smells`` so that silent-fallback states (malformed
suppression YAML, expired/missing fields) become explicit on the
envelope. ``strip_list_payloads`` then dropped that list before it
reached the caller, re-introducing Pattern 2 (silent fallback).

This test fixture proves the regression and locks in the W1000 fix:

* small ``warnings_out`` lists round-trip untouched (no info loss);
* large ``warnings_out`` lists keep their first 10 entries and grow a
  sibling ``warnings_out_truncated`` int naming how many were dropped;
* the ``summary.truncated`` advisory still fires (consumers that watch
  the summary still learn that something was elided);
* the ``warnings_out`` preservation does NOT leak into the general
  list-stripping behaviour (other lists keep getting dropped).
"""

from __future__ import annotations

from roam.output.formatter import json_envelope, strip_list_payloads


def _envelope(**extra) -> dict:
    return json_envelope("test", summary={"verdict": "ok"}, **extra)


class TestWarningsOutPreserved:
    def test_small_warnings_out_survives_strip(self):
        env = _envelope(warnings_out=["w1", "w2", "w3", "w4"])
        result = strip_list_payloads(env)
        assert result.get("warnings_out") == ["w1", "w2", "w3", "w4"]
        # The summary still gets the progressive-disclosure flag.
        assert result["summary"]["detail_available"] is True

    def test_empty_warnings_out_survives_as_empty_list(self):
        env = _envelope(warnings_out=[])
        result = strip_list_payloads(env)
        # Pattern 2 discipline: consumers can rely on the key being
        # present, empty list communicates "no warnings".
        assert result.get("warnings_out") == []

    def test_long_warnings_out_truncates_with_counter(self):
        warnings = [f"warn-{i}" for i in range(15)]
        env = _envelope(warnings_out=warnings)
        result = strip_list_payloads(env)
        kept = result.get("warnings_out")
        assert isinstance(kept, list)
        assert len(kept) == 10
        assert kept == [f"warn-{i}" for i in range(10)]
        assert result.get("warnings_out_truncated") == 5

    def test_other_lists_still_dropped(self):
        env = _envelope(
            warnings_out=["w1"],
            other_findings=[{"a": 1}, {"a": 2}],
        )
        result = strip_list_payloads(env)
        # warnings_out preserved
        assert result.get("warnings_out") == ["w1"]
        # other lists still dropped (general progressive-disclosure
        # behaviour unchanged).
        assert "other_findings" not in result
        # summary still flips truncated because a real list was dropped.
        assert result["summary"].get("truncated") is True

    def test_warnings_out_only_does_not_flip_truncated(self):
        """Preserving ``warnings_out`` is NOT a truncation event.

        When the only list-valued field is ``warnings_out`` and it
        round-trips intact, the summary should NOT learn
        ``truncated: true`` — nothing was actually elided.
        """
        env = _envelope(warnings_out=["w1", "w2"])
        result = strip_list_payloads(env)
        assert result.get("warnings_out") == ["w1", "w2"]
        # detail_available is always present, truncated is not.
        assert result["summary"]["detail_available"] is True
        assert result["summary"].get("truncated") is not True

    def test_long_warnings_out_flips_truncated(self):
        """Truncating ``warnings_out`` itself is a truncation event."""
        env = _envelope(warnings_out=[f"w{i}" for i in range(12)])
        result = strip_list_payloads(env)
        assert result.get("warnings_out_truncated") == 2
        assert result["summary"].get("truncated") is True
