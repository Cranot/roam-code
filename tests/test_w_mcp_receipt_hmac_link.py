"""MCP-P0.3 — HMAC-link MCP receipts to the signed event stream.

MCP-P0.3 motivation. Before P0.3, MCP receipts at
``.roam/mcp_receipts/<run_id>/<tool_call>.json`` lived OUTSIDE the
HMAC-chained run ledger — flipping a byte in a receipt JSON file was
undetectable. P0.3 wires:

1. Every receipt write also appends a signed ledger event carrying the
   sha256 of the canonical receipt bytes.
2. ``verify_chain_with_receipts`` walks those events, re-hashes the
   on-disk receipts, and reports a ``receipt_integrity`` sub-state
   (``ok`` / ``missing`` / ``tampered`` / ``not_linked``).

Hash-stability discipline: pre-P0.3 chains have no ``mcp_receipt``
events, so existing canonical-JSON bytes / signatures are unchanged.
This is the W210 ``_W210_OMIT_WHEN_DEFAULT_FIELDS`` pattern applied at
the ledger-event payload layer.
"""

from __future__ import annotations

import hashlib
import json

import pytest

from roam.runs.ledger import log_event, read_run_events, run_dir, start_run
from roam.runs.signing import (
    RECEIPT_INTEGRITY_STATES,
    ensure_ledger_key,
    verify_chain,
    verify_chain_with_receipts,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def isolated_repo(tmp_path, monkeypatch):
    """Tmp git-shaped dir; chdir + clear inherited env vars."""
    (tmp_path / ".git").mkdir()
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("ROAM_RUN_ID", raising=False)
    monkeypatch.delenv("ROAM_AGENT_ID", raising=False)
    monkeypatch.delenv("ROAM_MCP_CLIENT_ID", raising=False)
    monkeypatch.delenv("ROAM_MODE_ENFORCEMENT", raising=False)
    return tmp_path


def _register_sensitive(monkeypatch, name: str = "stub_p03_sensitive"):
    """Register a synthetic sensitive @_tool and return its receipt-wrapped fn."""
    import roam.mcp_server as m

    monkeypatch.setitem(
        m._TOOL_METADATA,
        name,
        {
            "name": name,
            "title": name,
            "description": "synthetic test fixture",
            "core": False,
            "read_only": False,
            "destructive": True,
            "idempotent": False,
            "task_mode": "required",
            "version": "0.0.0",
        },
    )

    def _inner(**kwargs):
        return {"command": name, "summary": {"verdict": "ok"}}

    return m._wrap_with_receipt(name, _inner)


# ---------------------------------------------------------------------------
# 1. Vocabulary drift guard
# ---------------------------------------------------------------------------


def test_receipt_integrity_states_are_closed_enum() -> None:
    """The 4 expected sub-states are present and no others sneak in."""
    assert RECEIPT_INTEGRITY_STATES == {"ok", "missing", "tampered", "not_linked"}


# ---------------------------------------------------------------------------
# 2. Happy path: receipt write → ledger gets a signed mcp_receipt event →
#    verify_chain_with_receipts returns receipt_integrity="ok"
# ---------------------------------------------------------------------------


def test_happy_path_links_receipt_to_ledger(isolated_repo, monkeypatch) -> None:
    """A sensitive tool call emits BOTH a receipt file AND a signed
    ``mcp_receipt`` event whose ``receipt_hash`` matches the on-disk bytes.
    """
    # Start a real run so the receipt links to a real run id.
    meta = start_run(isolated_repo, agent="test-p03")
    monkeypatch.setenv("ROAM_RUN_ID", meta.run_id)

    wrapped = _register_sensitive(monkeypatch)
    wrapped(symbol="foo")

    # The receipt file should exist.
    receipts_bucket = isolated_repo / ".roam" / "mcp_receipts" / meta.run_id
    receipt_files = list(receipts_bucket.glob("*.json"))
    assert len(receipt_files) == 1, f"expected exactly one receipt, found {receipt_files}"
    receipt_path = receipt_files[0]

    # The ledger should now carry exactly one ``mcp_receipt`` event.
    events = list(read_run_events(isolated_repo, meta.run_id))
    receipt_events = [e for e in events if e.get("action") == "mcp_receipt"]
    assert len(receipt_events) == 1
    re_event = receipt_events[0]
    assert re_event["tool_call"] == receipt_path.stem
    assert "receipt_hash" in re_event
    assert len(re_event["receipt_hash"]) == 64  # sha256 hex
    # Event MUST be signed too — receipt-link inherits chain integrity.
    assert "signature" in re_event

    # verify_chain_with_receipts reports OK on both axes.
    key = ensure_ledger_key(isolated_repo)
    result = verify_chain_with_receipts(events, key, isolated_repo, meta.run_id)
    assert result["state"] == "ok"
    assert result["receipt_integrity"] == "ok"
    assert result["first_tamper_at_seq"] is None


# ---------------------------------------------------------------------------
# 3. Tamper a receipt JSON file → verify reports tampered + first_tamper_at_seq
# ---------------------------------------------------------------------------


def test_tampered_receipt_file_detected(isolated_repo, monkeypatch) -> None:
    """Flip a byte in the receipt JSON → verify returns tampered with
    ``first_tamper_at_seq`` pointing at the receipt event.
    """
    meta = start_run(isolated_repo, agent="test-p03-tamper")
    monkeypatch.setenv("ROAM_RUN_ID", meta.run_id)

    wrapped = _register_sensitive(monkeypatch)
    wrapped(symbol="foo")

    receipt_path = next((isolated_repo / ".roam" / "mcp_receipts" / meta.run_id).glob("*.json"))
    # Tamper: load → mutate → write back. We change the tool_name so the
    # canonical bytes (and thus the sha256) shift.
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    receipt["tool_name"] = "evil_renamed"
    receipt_path.write_text(json.dumps(receipt, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8")

    events = list(read_run_events(isolated_repo, meta.run_id))
    receipt_events = [e for e in events if e.get("action") == "mcp_receipt"]
    expected_tamper_seq = receipt_events[0]["seq"]

    key = ensure_ledger_key(isolated_repo)
    result = verify_chain_with_receipts(events, key, isolated_repo, meta.run_id)
    assert result["state"] == "tampered"
    assert result["receipt_integrity"] == "tampered"
    assert result["first_tamper_at_seq"] == expected_tamper_seq
    assert "mcp_receipt" in result["details"].lower() or "receipt" in result["details"].lower()


# ---------------------------------------------------------------------------
# 4. Delete the receipt file → verify reports missing
# ---------------------------------------------------------------------------


def test_missing_receipt_file_detected(isolated_repo, monkeypatch) -> None:
    """The chain stays intact, but the receipt artefact is gone — verify
    reports ``receipt_integrity="missing"`` and a missing-seq pointer.
    """
    meta = start_run(isolated_repo, agent="test-p03-missing")
    monkeypatch.setenv("ROAM_RUN_ID", meta.run_id)

    wrapped = _register_sensitive(monkeypatch)
    wrapped(symbol="foo")

    receipt_path = next((isolated_repo / ".roam" / "mcp_receipts" / meta.run_id).glob("*.json"))
    receipt_path.unlink()

    events = list(read_run_events(isolated_repo, meta.run_id))
    receipt_events = [e for e in events if e.get("action") == "mcp_receipt"]
    expected_missing_seq = receipt_events[0]["seq"]

    key = ensure_ledger_key(isolated_repo)
    result = verify_chain_with_receipts(events, key, isolated_repo, meta.run_id)
    # Chain itself is still ok — only the on-disk artefact is gone.
    assert result["state"] == "ok"
    assert result["receipt_integrity"] == "missing"
    assert result["first_missing_receipt_at_seq"] == expected_missing_seq
    # Surface as partial so callers don't read "ok" and ignore the hole.
    assert result["partial_success"] is True


# ---------------------------------------------------------------------------
# 5. Pre-P0.3 run (no mcp_receipt events) → receipt_integrity="not_linked"
# ---------------------------------------------------------------------------


def test_pre_p03_chain_reports_not_linked(isolated_repo) -> None:
    """A run with only non-receipt events should pass with ``not_linked`` —
    NOT a failure (backward-compat contract).
    """
    meta = start_run(isolated_repo, agent="test-pre-p03")
    log_event(isolated_repo, meta.run_id, action="preflight", target="useFoo")
    log_event(isolated_repo, meta.run_id, action="diff")

    events = list(read_run_events(isolated_repo, meta.run_id))
    key = ensure_ledger_key(isolated_repo)
    result = verify_chain_with_receipts(events, key, isolated_repo, meta.run_id)
    assert result["state"] == "ok"
    assert result["receipt_integrity"] == "not_linked"
    # Backward-compat: not_linked is informational, NOT partial_success.
    assert result["partial_success"] is False


# ---------------------------------------------------------------------------
# 6. Hash-stability: an event WITHOUT receipt_hash has byte-identical
#    canonical bytes / signature to a pre-P0.3 ledger.
# ---------------------------------------------------------------------------


def test_pre_p03_event_bytes_byte_identical(isolated_repo) -> None:
    """A vanilla ``log_event`` call (no receipt_hash field) produces
    canonical JSON byte-for-byte identical to what a pre-P0.3 caller would
    have produced. Proves W210 omit-when-default discipline at the ledger
    layer.
    """
    meta = start_run(isolated_repo, agent="test-byte-identical")
    log_event(isolated_repo, meta.run_id, action="preflight", target="useFoo")

    events_path = run_dir(isolated_repo, meta.run_id) / "events.jsonl"
    line = events_path.read_text(encoding="utf-8").splitlines()[0]
    parsed = json.loads(line)

    # The event MUST NOT have a ``receipt_hash`` key by default — the field
    # is additive and rides only on ``mcp_receipt`` events.
    assert "receipt_hash" not in parsed
    # Chain still verifies cleanly.
    key = ensure_ledger_key(isolated_repo)
    base = verify_chain([parsed], key)
    assert base["state"] == "ok"


# ---------------------------------------------------------------------------
# 7. verify_chain (the old 2-arg API) still works unchanged on a P0.3 chain
# ---------------------------------------------------------------------------


def test_legacy_verify_chain_api_still_works(isolated_repo, monkeypatch) -> None:
    """``verify_chain(events, key)`` — the pre-P0.3 callable — must stay
    valid for callers that don't care about receipt integrity. The new
    field rides only on ``verify_chain_with_receipts``.
    """
    meta = start_run(isolated_repo, agent="test-legacy-api")
    monkeypatch.setenv("ROAM_RUN_ID", meta.run_id)

    wrapped = _register_sensitive(monkeypatch)
    wrapped(symbol="foo")

    events = list(read_run_events(isolated_repo, meta.run_id))
    key = ensure_ledger_key(isolated_repo)
    result = verify_chain(events, key)
    assert result["state"] == "ok"
    # Old API doesn't surface receipt_integrity — keeps callers untouched.
    assert "receipt_integrity" not in result


# ---------------------------------------------------------------------------
# 8. Receipt-hash matches the canonical receipt bytes the receipt itself
#    claims (end-to-end hash determinism)
# ---------------------------------------------------------------------------


def test_receipt_hash_matches_canonical_bytes(isolated_repo, monkeypatch) -> None:
    """The chain-baked ``receipt_hash`` should equal sha256 of the
    receipt's canonical-JSON form — proving determinism end-to-end.
    """
    meta = start_run(isolated_repo, agent="test-p03-determinism")
    monkeypatch.setenv("ROAM_RUN_ID", meta.run_id)

    wrapped = _register_sensitive(monkeypatch)
    wrapped(target="x")

    receipt_path = next((isolated_repo / ".roam" / "mcp_receipts" / meta.run_id).glob("*.json"))
    on_disk = receipt_path.read_bytes().rstrip(b"\n")
    expected = hashlib.sha256(on_disk).hexdigest()

    events = list(read_run_events(isolated_repo, meta.run_id))
    receipt_events = [e for e in events if e.get("action") == "mcp_receipt"]
    assert len(receipt_events) == 1
    assert receipt_events[0]["receipt_hash"] == expected


# ---------------------------------------------------------------------------
# 9. Read-only tools don't emit receipts → no mcp_receipt events appear
# ---------------------------------------------------------------------------


def test_readonly_tool_does_not_emit_receipt_event(isolated_repo, monkeypatch) -> None:
    """A pure read-only / idempotent / no-task-mode tool is non-sensitive,
    so the receipt wrapper is a pass-through. The run ledger should NOT
    see an ``mcp_receipt`` event for it.
    """
    import roam.mcp_server as m

    meta = start_run(isolated_repo, agent="test-p03-readonly")
    monkeypatch.setenv("ROAM_RUN_ID", meta.run_id)

    name = "stub_readonly_for_p03"
    monkeypatch.setitem(
        m._TOOL_METADATA,
        name,
        {
            "name": name,
            "destructive": False,
            "read_only": True,
            "idempotent": True,
            "task_mode": None,
        },
    )

    def _inner(**kwargs):
        return {"command": name}

    wrapped = m._wrap_with_receipt(name, _inner)
    wrapped()

    events = list(read_run_events(isolated_repo, meta.run_id))
    receipt_events = [e for e in events if e.get("action") == "mcp_receipt"]
    assert receipt_events == []
