"""W692 — Canonical Suppression dataclass + discriminated-union match keys.

Tests three concerns:

1. Each variant round-trips through ``from_dict`` -> ``to_dict`` for the
   on-disk shape it represents.
2. The shared ``_SuppressionBase.is_expired()`` semantics match the legacy
   smells_suppress behaviour (UTC date, missing-expires = never).
3. The new typed loader in ``commands.suppression`` returns
   :class:`RuleFileSuppression` instances semantically equivalent to the
   legacy ``load_suppressions`` dict shape.
"""

from __future__ import annotations

from datetime import date

from roam.commands.suppression import load_suppressions, load_suppressions_typed
from roam.policy.suppression_v2 import (
    VALID_STATUSES,
    FindingIdSuppression,
    KindSymbolSuppression,
    RuleFileSuppression,
    _coerce_date,
    _coerce_int,
    _coerce_status,
)

# ---------------------------------------------------------------------------
# RuleFileSuppression — ``.roam-suppressions.yml`` shape
# ---------------------------------------------------------------------------


def test_rule_file_suppression_round_trip():
    """Legacy dict -> dataclass -> dict preserves every field."""
    legacy = {
        "rule": "secret-detection",
        "file": "tests/fake_secrets.py",
        "line": 42,
        "reason": "Test fixture",
        "status": "safe",
        "author": "dev@example.com",
        "date": "2026-02-25",
    }
    sup = RuleFileSuppression.from_dict(legacy)
    assert sup.rule == "secret-detection"
    assert sup.file == "tests/fake_secrets.py"
    assert sup.line == 42
    assert sup.status == "safe"
    assert sup.added == date(2026, 2, 25)
    assert sup.source == "rule-file-yml"

    round_tripped = sup.to_dict()
    # Stable field order: rule, file, line, reason, status, author, date.
    assert list(round_tripped.keys()) == ["rule", "file", "line", "reason", "status", "author", "date"]
    assert round_tripped["date"] == "2026-02-25"


def test_rule_file_suppression_optional_line_omitted_in_to_dict():
    """When ``line`` is None it must NOT appear in the projected dict."""
    sup = RuleFileSuppression.from_dict({"rule": "foo", "file": "bar.py"})
    assert sup.line is None
    assert "line" not in sup.to_dict()


def test_rule_file_suppression_backslash_normalisation():
    """Windows-style paths get forward-slash-normalised on ingest."""
    sup = RuleFileSuppression.from_dict({"rule": "x", "file": r"src\foo\bar.py"})
    assert sup.file == "src/foo/bar.py"


# ---------------------------------------------------------------------------
# KindSymbolSuppression — ``.roam/smells.suppress.yml`` shape
# ---------------------------------------------------------------------------


def test_kind_symbol_suppression_round_trip():
    legacy = {
        "kind": "shotgun-surgery",
        "symbol": "roam.languages.registry.get_language_for_file",
        "reason": "Public API hub by design",
        "expires": "2026-12-01",
        "author": "Cranot",
        "added": "2026-05-14",
    }
    sup = KindSymbolSuppression.from_dict(legacy)
    assert sup.kind == "shotgun-surgery"
    assert sup.symbol == "roam.languages.registry.get_language_for_file"
    assert sup.expires == date(2026, 12, 1)
    assert sup.added == date(2026, 5, 14)
    assert sup.source == "smells-suppress-yml"

    round_tripped = sup.to_dict()
    assert round_tripped["kind"] == "shotgun-surgery"
    assert round_tripped["expires"] == "2026-12-01"


def test_kind_symbol_expiry_semantics_match_legacy():
    """Future expiry: not expired. Past expiry: expired. Missing: never."""
    past = KindSymbolSuppression.from_dict({"kind": "k", "symbol": "s", "expires": "2020-01-01"})
    future = KindSymbolSuppression.from_dict({"kind": "k", "symbol": "s", "expires": "2099-01-01"})
    none = KindSymbolSuppression.from_dict({"kind": "k", "symbol": "s"})

    fixed_today = date(2026, 5, 14)
    assert past.is_expired(today=fixed_today) is True
    assert future.is_expired(today=fixed_today) is False
    assert none.is_expired(today=fixed_today) is False
    # No-arg path uses UTC today — must not raise.
    none.is_expired()


# ---------------------------------------------------------------------------
# FindingIdSuppression — ``.roam/suppressions.json`` shape
# ---------------------------------------------------------------------------


def test_finding_id_suppression_round_trip_with_sarif_projection():
    """Entries with rule_id + location project cleanly back."""
    fid = "abc123def4567890"
    entry = {
        "reason": "verified manually",
        "added_at": "2026-05-14T00:00:00.000000Z",
        "source": "from-finding",  # legacy, ignored on the dataclass
        "rule_id": "algo/io-in-loop",
        "location": "src/foo.py:42",
        "task_id": "io-in-loop",
        "symbol_name": "MyClass.list",
    }
    sup = FindingIdSuppression.from_dict(fid, entry)
    assert sup.finding_id == fid
    assert sup.rule_id == "algo/io-in-loop"
    assert sup.location == "src/foo.py:42"
    assert sup.source == "suppressions-json"

    out = sup.to_dict()
    assert out["rule_id"] == "algo/io-in-loop"
    assert out["location"] == "src/foo.py:42"
    assert out["added_at"] == "2026-05-14"  # date-only round-trip


def test_finding_id_suppression_hash_only_entry():
    """Hash-only entries (no rule_id/location) still construct cleanly."""
    sup = FindingIdSuppression.from_dict("deadbeef", {"reason": "by hash only"})
    assert sup.finding_id == "deadbeef"
    assert sup.rule_id is None
    assert sup.location is None
    assert sup.reason == "by hash only"

    out = sup.to_dict()
    assert "rule_id" not in out
    assert "location" not in out


# ---------------------------------------------------------------------------
# commands.suppression typed-loader wiring
# ---------------------------------------------------------------------------


def test_load_suppressions_typed_matches_legacy_loader(tmp_path):
    """The new typed loader returns the same rows as the legacy dict loader."""
    yml = tmp_path / ".roam-suppressions.yml"
    yml.write_text(
        "suppressions:\n  - rule: secret-detection\n    file: tests/fake.py\n    reason: fixture\n    status: safe\n",
        encoding="utf-8",
    )

    legacy_rows = load_suppressions(tmp_path)
    typed_rows = load_suppressions_typed(tmp_path)

    assert len(legacy_rows) == len(typed_rows) == 1
    assert isinstance(typed_rows[0], RuleFileSuppression)
    assert typed_rows[0].rule == legacy_rows[0]["rule"]
    assert typed_rows[0].file == legacy_rows[0]["file"]
    assert typed_rows[0].status == "safe"


# ---------------------------------------------------------------------------
# Coercion helpers — defensive tolerance
# ---------------------------------------------------------------------------


def test_coerce_helpers_tolerate_malformed_input():
    assert _coerce_int(None) is None
    assert _coerce_int("") is None
    assert _coerce_int("not a number") is None
    assert _coerce_int("42") == 42
    assert _coerce_int(42) == 42
    assert _coerce_int(True) is None  # bool is not a "real" int we accept

    assert _coerce_date(None) is None
    assert _coerce_date("") is None
    assert _coerce_date("garbage") is None
    assert _coerce_date("2026-05-14") == date(2026, 5, 14)
    assert _coerce_date("2026-05-14T12:34:56Z") == date(2026, 5, 14)
    assert _coerce_date(date(2026, 5, 14)) == date(2026, 5, 14)

    assert _coerce_status(None) is None
    assert _coerce_status("bogus") is None
    assert _coerce_status("safe") == "safe"
    assert all(_coerce_status(s) == s for s in VALID_STATUSES)


def test_frozen_dataclasses_are_hashable():
    """frozen=True means the dataclasses can land in sets/dict keys."""
    a = RuleFileSuppression.from_dict({"rule": "r", "file": "f"})
    b = RuleFileSuppression.from_dict({"rule": "r", "file": "f"})
    c = RuleFileSuppression.from_dict({"rule": "r", "file": "other"})
    assert a == b
    assert {a, b, c} == {a, c}


# ---------------------------------------------------------------------------
# W744 — sarif_status / policy_status split
# ---------------------------------------------------------------------------


def test_w744_new_status_fields_carry_through_from_dict():
    """sarif_status + policy_status round-trip through from_dict/to_dict."""
    from roam.policy.suppression_v2 import (
        VALID_POLICY_STATUSES,
        VALID_SARIF_STATUSES,
    )

    entry = {
        "rule": "secret-detection",
        "file": "tests/fake.py",
        "sarif_status": "suppressed",
        "policy_status": "accepted_with_caveats",
    }
    sup = RuleFileSuppression.from_dict(entry)
    assert sup.sarif_status == "suppressed"
    assert sup.policy_status == "accepted_with_caveats"
    # Legacy field stays None when not supplied.
    assert sup.status is None

    out = sup.to_dict()
    assert out["sarif_status"] == "suppressed"
    assert out["policy_status"] == "accepted_with_caveats"
    assert "status" not in out  # legacy stays absent
    assert "suppressed" in VALID_SARIF_STATUSES
    assert "accepted_with_caveats" in VALID_POLICY_STATUSES


def test_w744_unknown_values_coerce_to_none():
    """Out-of-enum values for the new fields drop to None (closed enum)."""
    sup = RuleFileSuppression.from_dict(
        {
            "rule": "r",
            "file": "f",
            "sarif_status": "bogus",
            "policy_status": "alsobogus",
        }
    )
    assert sup.sarif_status is None
    assert sup.policy_status is None


def test_w744_legacy_status_only_emits_deprecation_warning():
    """Legacy ``status`` field without new fields = deprecation warning."""
    from roam.policy.suppression_v2 import LEGACY_STATUS_DEPRECATION_HINT

    warnings_out: list[str] = []
    sup = RuleFileSuppression.from_dict(
        {"rule": "r", "file": "f.py", "status": "safe"},
        warnings_out=warnings_out,
    )
    # Back-compat: legacy value still lands on the legacy field.
    assert sup.status == "safe"
    assert sup.sarif_status is None
    assert sup.policy_status is None
    # Warning carries the migration hint.
    assert len(warnings_out) == 1
    assert LEGACY_STATUS_DEPRECATION_HINT in warnings_out[0]
    assert "sarif_status" in warnings_out[0]
    assert "policy_status" in warnings_out[0]


def test_w744_no_warning_when_new_field_present_alongside_legacy():
    """When sarif_status or policy_status accompany legacy status, no warning."""
    warnings_out: list[str] = []
    sup = RuleFileSuppression.from_dict(
        {
            "rule": "r",
            "file": "f.py",
            "status": "safe",  # legacy
            "sarif_status": "suppressed",  # new
        },
        warnings_out=warnings_out,
    )
    assert sup.status == "safe"
    assert sup.sarif_status == "suppressed"
    assert warnings_out == []


def test_w744_legacy_only_no_warnings_out_stays_silent():
    """Pre-W744 callers (no warnings_out) get byte-identical silent behaviour."""
    sup = RuleFileSuppression.from_dict({"rule": "r", "file": "f.py", "status": "safe"})
    assert sup.status == "safe"
    # No warnings_out supplied → no crash, no warning, byte-identical legacy path.


def test_w744_warning_propagates_through_finding_id_loader():
    """FindingIdSuppression.from_dict accepts warnings_out per the W744 spec."""
    from roam.policy.suppression_v2 import LEGACY_STATUS_DEPRECATION_HINT

    warnings_out: list[str] = []
    sup = FindingIdSuppression.from_dict(
        "deadbeef",
        {"reason": "x", "status": "acknowledged"},
        warnings_out=warnings_out,
    )
    assert sup.status == "acknowledged"
    assert len(warnings_out) == 1
    assert LEGACY_STATUS_DEPRECATION_HINT in warnings_out[0]


def test_w744_warning_propagates_through_kind_symbol_loader():
    """KindSymbolSuppression.from_dict accepts warnings_out per the W744 spec."""
    from roam.policy.suppression_v2 import LEGACY_STATUS_DEPRECATION_HINT

    warnings_out: list[str] = []
    sup = KindSymbolSuppression.from_dict(
        {"kind": "shotgun-surgery", "symbol": "s", "status": "wont-fix"},
        warnings_out=warnings_out,
    )
    assert sup.status == "wont-fix"
    assert len(warnings_out) == 1
    assert LEGACY_STATUS_DEPRECATION_HINT in warnings_out[0]


def test_w744_sarif_emission_consumes_sarif_status_over_legacy():
    """to_sarif() / SARIF applier consumes sarif_status, not legacy status."""
    from roam.output.sarif import _apply_suppressions_typed

    suppressions = [
        FindingIdSuppression.from_dict(
            "deadbeef0001",
            {
                "rule_id": "r1",
                "location": "src/x.py:10",
                "sarif_status": "notSuppressed",  # new field wins
                "status": "safe",  # legacy is shadowed
                "reason": "audited",
            },
        )
    ]
    results = [
        {
            "ruleId": "r1",
            "locations": [
                {
                    "physicalLocation": {
                        "artifactLocation": {"uri": "src/x.py"},
                        "region": {"startLine": 10},
                    }
                }
            ],
        }
    ]
    out = _apply_suppressions_typed(results, suppressions)
    # sarif_status="notSuppressed" beats legacy status="safe"
    assert out[0]["suppressions"][0]["status"] == "notSuppressed"


def test_w744_sarif_emission_falls_back_to_legacy_status_when_sarif_status_unset():
    """W736 byte-identity: when sarif_status is unset, legacy status passes through."""
    from roam.output.sarif import _apply_suppressions_typed

    suppressions = [
        FindingIdSuppression.from_dict(
            "deadbeef0002",
            {
                "rule_id": "r2",
                "location": "src/y.py:20",
                "status": "safe",  # legacy only — no sarif_status
                "reason": "audited",
            },
        )
    ]
    results = [
        {
            "ruleId": "r2",
            "locations": [
                {
                    "physicalLocation": {
                        "artifactLocation": {"uri": "src/y.py"},
                        "region": {"startLine": 20},
                    }
                }
            ],
        }
    ]
    out = _apply_suppressions_typed(results, suppressions)
    # Legacy "safe" passes through unchanged — W736 byte-identity preserved.
    assert out[0]["suppressions"][0]["status"] == "safe"


def test_w744_policy_status_not_consumed_by_sarif_emission():
    """policy_status is NOT emitted to SARIF (orthogonality contract)."""
    from roam.output.sarif import _apply_suppressions_typed

    suppressions = [
        FindingIdSuppression.from_dict(
            "deadbeef0003",
            {
                "rule_id": "r3",
                "location": "src/z.py:30",
                "policy_status": "dismissed",  # policy concern, not SARIF
                "reason": "rejected by reviewer",
            },
        )
    ]
    results = [
        {
            "ruleId": "r3",
            "locations": [
                {
                    "physicalLocation": {
                        "artifactLocation": {"uri": "src/z.py"},
                        "region": {"startLine": 30},
                    }
                }
            ],
        }
    ]
    out = _apply_suppressions_typed(results, suppressions)
    # policy_status="dismissed" must NOT leak into SARIF status.
    assert out[0]["suppressions"][0]["status"] == "accepted"  # fallback default
    assert out[0]["suppressions"][0]["status"] != "dismissed"
