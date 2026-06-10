"""Tests for guard_errors module (structured error envelopes)."""

from __future__ import annotations

import json

from roam.guard_errors import (
    GUARD_ERROR_CODES,
    exit_code_for_guard_error,
    guard_error_envelope,
    make_guard_error,
)


def test_make_guard_error_returns_full_shape():
    err = make_guard_error("no_bundle_found", "no bundle on disk")
    assert err["code"] == "no_bundle_found"
    assert err["detail"] == "no bundle on disk"
    # Always-present keys (Pattern 2 — never missing keys).
    assert err["fix"] is None
    assert err["context"] is None


def test_make_guard_error_with_fix_and_context():
    err = make_guard_error(
        "rule_pack_invalid",
        "regex unclosed",
        fix="check the YAML",
        context={"path": "bad.yml", "line": 5},
    )
    assert err["fix"] == "check the YAML"
    assert err["context"]["path"] == "bad.yml"


def test_make_guard_error_unknown_code_softfails():
    """Unknown codes don't raise — they emit unexpected_error."""
    err = make_guard_error("not_a_real_code", "x")
    assert err["code"] == "unexpected_error"
    assert "not_a_real_code" in err["detail"]


def test_exit_code_for_guard_error_known_codes():
    assert exit_code_for_guard_error("no_bundle_found") == 2
    assert exit_code_for_guard_error("compose_failed") == 5
    assert exit_code_for_guard_error("schema_validation_failed") == 5
    assert exit_code_for_guard_error("unexpected_error") == 1


def test_exit_code_for_guard_error_unknown_code():
    assert exit_code_for_guard_error("totally_made_up") == 1


def test_guard_error_envelope_has_uniform_shape():
    env = guard_error_envelope(
        "test-cmd",
        "no_bundle_found",
        "could not find bundle",
        fix="run roam pr-bundle init",
    )
    assert env["command"] == "test-cmd"
    assert env["summary"]["error_code"] == "no_bundle_found"
    assert env["summary"]["partial_success"] is True
    # Verdict in summary mirrors the error code for at-a-glance scanning.
    assert env["summary"]["verdict"] == "no_bundle_found"
    # The structured error object is the canonical surface.
    assert env["error"]["code"] == "no_bundle_found"
    assert env["error"]["fix"] == "run roam pr-bundle init"
    # Agent contract surfaces the detail + fix as facts.
    facts = env["agent_contract"]["facts"]
    assert any("could not find bundle" in f for f in facts)
    assert any("fix:" in f for f in facts)
    # Risks list includes the structured error object.
    assert env["agent_contract"]["risks"] == [env["error"]]


def test_guard_error_envelope_with_summary_extras():
    """summary_extras merge into the envelope's summary block."""
    env = guard_error_envelope(
        "test-cmd",
        "rule_pack_invalid",
        "bad yaml",
        summary_extras={"rule_pack_path": "bad.yml"},
    )
    assert env["summary"]["rule_pack_path"] == "bad.yml"


def test_guard_error_envelope_serializes_to_json():
    """The envelope must be JSON-serializable end-to-end."""
    env = guard_error_envelope("test-cmd", "no_bundle_found", "x", fix="y")
    text = json.dumps(env)  # no TypeError
    parsed = json.loads(text)
    assert parsed["summary"]["error_code"] == "no_bundle_found"


def test_all_error_codes_in_enum_have_exit_codes():
    """Lint: every code in the enum has an exit code mapping."""
    from roam.guard_errors import GUARD_ERROR_EXIT_CODES

    missing = GUARD_ERROR_CODES - set(GUARD_ERROR_EXIT_CODES.keys())
    assert missing == set(), f"error codes missing exit mapping: {missing}"


def test_make_guard_error_with_none_fields_keeps_keys():
    """Pattern 2 — explicit None over missing keys."""
    err = make_guard_error("no_bundle_found", "x")
    # All four keys present, two are None.
    assert set(err.keys()) == {"code", "detail", "fix", "context"}
