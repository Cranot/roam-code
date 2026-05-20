"""End-to-end composition test for the MCP runtime security pipeline.

Today's individual layer tests pin each mechanism in isolation:

* P0.1 - egress secret redaction          (``test_w_mcp_redact_egress.py``)
* P0.2 - 4-mode policy enforcement        (``test_w_mcp_mode_enforcement.py``)
* P0.3 - HMAC-link receipts to ledger     (``test_w_mcp_receipt_hmac_link.py``)
* P2.2 - McpDecisionReceipt JSON Schema   (``test_mcp_receipt_json_schema.py``)

This file pins the COMPOSITION: a single tool call must fire all four
mechanisms in sequence and produce evidence that each one ran. The four
layers maps to the Pattern-1 canonical failure-envelope discipline and
supports evidence for the eight-question agentic-assurance crosswalk
(see ``CLAUDE.md`` "Evidence compiler layer").

The closed-enum vocabulary is imported by reference from
:mod:`roam.evidence._vocabulary` and
:mod:`roam.evidence.mcp_receipt`; literal enum strings are never
hard-coded at the assertion site. Secret-shaped fixtures use the same
synthetic tokens already in ``tests/test_redact.py`` so the leak-gate
``tests/test_no_internal_language.py`` stays green.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from roam.evidence._vocabulary import REDACTION_REASONS
from roam.evidence.mcp_receipt import _POLICY_DECISIONS
from roam.evidence.mcp_receipt_schema import mcp_receipt_json_schema
from roam.runs.ledger import read_run_events, start_run
from roam.runs.signing import (
    RECEIPT_INTEGRITY_STATES,
    ensure_ledger_key,
    verify_chain_with_receipts,
)
from tests._helpers.repo_root import repo_root  # noqa: F401 — imported for parity with sister tests

# ---------------------------------------------------------------------------
# Closed-enum sanity (imported by reference, never hard-coded)
# ---------------------------------------------------------------------------

#: Subset of :data:`_POLICY_DECISIONS` we expect to observe in this file.
#: The membership check guards against any silent vocabulary drift between
#: the receipt dataclass + schema layer and the assertions here.
_EXPECTED_DECISIONS = {"allow", "deny"}
assert _EXPECTED_DECISIONS <= set(_POLICY_DECISIONS), (
    "policy-decision vocabulary drift: composition test assumes 'allow' and 'deny' are members of _POLICY_DECISIONS"
)
assert "secret" in REDACTION_REASONS, (
    "redaction-reason vocabulary drift: composition test assumes 'secret' is a member of REDACTION_REASONS"
)
assert {"ok", "tampered"} <= RECEIPT_INTEGRITY_STATES, (
    "receipt-integrity vocabulary drift: composition test assumes 'ok' and "
    "'tampered' are members of RECEIPT_INTEGRITY_STATES"
)


@pytest.fixture(autouse=True)
def _reset_mcp_module_state():
    """Reset the module-level error-storm counter before AND after each test.

    ``_structured_error`` keeps a process-wide ``_ERROR_STORM_STATE`` that
    trims envelope fields (including ``summary``) after the 3rd consecutive
    same-code error. Several MODE_BLOCKED denials in this file — and in
    sibling files like ``test_w_mcp_mode_enforcement.py`` / ``test_w_mcp_shadow_mode.py``
    — share the ``MODE_BLOCKED`` code. Under ``pytest -n auto`` an xdist
    worker that already saw ≥3 ``MODE_BLOCKED`` errors trims this test's
    deny envelope, dropping the ``summary`` key and breaking the
    ``result["summary"]["state"] == "mode_blocked"`` assertion (the
    3.10/3.11/3.13-only CI flake). Standard pattern across the suite — see
    ``tests/test_w_mcp_shadow_mode.py`` and ``tests/test_mcp_server.py``.
    """
    from roam.mcp_server import _reset_error_storm

    _reset_error_storm()
    yield
    _reset_error_storm()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _read_receipts(receipts_root: Path) -> list[dict]:
    """Walk every bucket under ``mcp_receipts/`` and load JSON receipts."""
    if not receipts_root.exists():
        return []
    receipts: list[dict] = []
    for sub in receipts_root.iterdir():
        if sub.is_dir():
            for f in sub.glob("*.json"):
                receipts.append(json.loads(f.read_text(encoding="utf-8")))
    return receipts


def _register_destructive_tool(monkeypatch, name: str, *, return_value, backing_cli: str | None = None):
    """Register a synthetic destructive @_tool wired to a real backing CLI.

    Mirrors the helper in ``test_w_mcp_mode_enforcement.py`` so the
    composition test exercises the same wrapper layer (and same gate)
    the individual-layer tests pin.
    """
    import roam.mcp_server as m

    monkeypatch.setitem(
        m._TOOL_METADATA,
        name,
        {
            "name": name,
            "title": name,
            "description": "synthetic destructive composition fixture",
            "core": False,
            "read_only": False,
            "destructive": True,
            "idempotent": False,
            "task_mode": "required",
            "version": "0.0.0",
        },
    )
    if backing_cli is not None:
        monkeypatch.setitem(m._MCP_TO_CLI_RENAME_ALIAS, name, backing_cli)

    call_count = {"n": 0}

    def _inner(**kwargs):
        call_count["n"] += 1
        return return_value

    wrapped = m._wrap_with_receipt(name, _inner)
    return wrapped, call_count


@pytest.fixture
def isolated_repo(tmp_path, monkeypatch):
    """Tmp git-shaped dir; clear inherited env vars so each scenario starts clean.

    Hermetic by design: every scenario chdir's into a fresh ``tmp_path``,
    so the ``.roam/`` state from one test never leaks into another.
    """
    (tmp_path / ".git").mkdir()
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("ROAM_RUN_ID", raising=False)
    monkeypatch.delenv("ROAM_AGENT_ID", raising=False)
    monkeypatch.delenv("ROAM_MCP_CLIENT_ID", raising=False)
    monkeypatch.delenv("ROAM_AGENT_MODE", raising=False)
    monkeypatch.delenv("ROAM_MODE_ENFORCEMENT", raising=False)
    monkeypatch.delenv("ROAM_MODE_DRY_RUN", raising=False)
    return tmp_path


# Synthetic secret-shaped fixture. Same shape as the ``sk_prefix`` regex
# in ``src/roam/security/redact.py`` SECRET_PATTERNS — pattern-catalogue
# language, not a real credential.
_SECRET_TOKEN_FIXTURE = "sk-test-1234567890abcdef1234567890"
_REDACTED_PLACEHOLDER = "[REDACTED]"


# ---------------------------------------------------------------------------
# Scenario 1: Happy path — allow + receipt + chain link + schema
# ---------------------------------------------------------------------------


def test_happy_path_allow_receipt_chain_schema(isolated_repo, monkeypatch) -> None:
    """A non-secret tool call under an allowed mode fires every layer.

    Maps to all four P0/P2 mechanisms:

    * P0.1 — egress walk runs (nothing to redact, ``redactions`` stays empty).
    * P0.2 — gate resolves ``policy_decision == "allow"``.
    * P0.3 — ledger gets one ``mcp_receipt`` event whose ``receipt_hash``
      matches the on-disk receipt bytes; ``verify_chain_with_receipts``
      reports ``state=ok`` + ``receipt_integrity=ok``.
    * P2.2 — the on-disk receipt validates against
      :func:`mcp_receipt_json_schema`.
    """
    jsonschema = pytest.importorskip("jsonschema")

    # Migration mode + enforcement so the destructive tool is on the allow
    # path (mutate sits at the migration tier).
    monkeypatch.setenv("ROAM_AGENT_MODE", "migration")
    monkeypatch.setenv("ROAM_MODE_ENFORCEMENT", "1")

    meta = start_run(isolated_repo, agent="composition-test-happy")
    monkeypatch.setenv("ROAM_RUN_ID", meta.run_id)

    raw_output = {"command": "stub_happy", "summary": {"verdict": "ok"}}
    wrapped, call_count = _register_destructive_tool(
        monkeypatch, "roam_pipeline_happy", return_value=raw_output, backing_cli="mutate"
    )

    result = wrapped(symbol="useThemeClasses")
    assert call_count["n"] == 1, "happy-path tool must run under migration mode"
    assert result.get("error_code") != "MODE_BLOCKED"

    # P0.2 — receipt records the allow decision.
    receipts_bucket = isolated_repo / ".roam" / "mcp_receipts" / meta.run_id
    receipt_files = list(receipts_bucket.glob("*.json"))
    assert len(receipt_files) == 1, f"expected one receipt, found {receipt_files}"
    receipt_path = receipt_files[0]
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    assert receipt["policy_decision"] == "allow"
    assert receipt["policy_decision"] in _POLICY_DECISIONS

    # P0.1 — no secret in raw output, so redactions stays empty.
    assert receipt.get("redactions") in ([], ()), (
        f"clean output must yield empty redactions, got {receipt.get('redactions')!r}"
    )

    # P2.2 — receipt validates against the schema.
    jsonschema.validate(instance=receipt, schema=mcp_receipt_json_schema())

    # P0.3 — ledger linkage holds end-to-end.
    events = list(read_run_events(isolated_repo, meta.run_id))
    receipt_events = [e for e in events if e.get("action") == "mcp_receipt"]
    assert len(receipt_events) == 1
    on_disk = receipt_path.read_bytes().rstrip(b"\n")
    expected_hash = hashlib.sha256(on_disk).hexdigest()
    assert receipt_events[0]["receipt_hash"] == expected_hash

    key = ensure_ledger_key(isolated_repo)
    verify_result = verify_chain_with_receipts(events, key, isolated_repo, meta.run_id)
    assert verify_result["state"] == "ok"
    assert verify_result["receipt_integrity"] == "ok"
    assert verify_result["first_tamper_at_seq"] is None


# ---------------------------------------------------------------------------
# Scenario 2: Deny path — receipt records the deny without invoking the tool
# ---------------------------------------------------------------------------


def test_deny_path_blocks_tool_and_records_receipt(isolated_repo, monkeypatch) -> None:
    """Under ``read_only`` + enforcement, a destructive tool is blocked.

    Side-effect invariant: the underlying tool function is never invoked.
    The receipt records ``policy_decision == "deny"`` and the ledger +
    receipt-hash linkage still holds — P0.3 fires on the deny path too,
    because the wrapper writes the receipt inside the ``finally`` of the
    ``_mcp_receipt_for`` context manager regardless of whether the gate
    short-circuited the tool body.
    """
    jsonschema = pytest.importorskip("jsonschema")

    monkeypatch.setenv("ROAM_AGENT_MODE", "read_only")
    monkeypatch.setenv("ROAM_MODE_ENFORCEMENT", "1")

    meta = start_run(isolated_repo, agent="composition-test-deny")
    monkeypatch.setenv("ROAM_RUN_ID", meta.run_id)

    raw = {"command": "stub_deny", "summary": {"verdict": "should not run"}}
    wrapped, call_count = _register_destructive_tool(
        monkeypatch, "roam_pipeline_deny", return_value=raw, backing_cli="mutate"
    )

    result = wrapped(symbol="foo")

    # Side-effect invariant: tool body did NOT run.
    assert call_count["n"] == 0, "destructive tool ran despite mode block"

    # Pattern-1 MODE_BLOCKED envelope shape.
    assert result.get("isError") is True
    assert result.get("error_code") == "MODE_BLOCKED"
    assert result["summary"]["state"] == "mode_blocked"
    assert result["next_command"].startswith("roam mode ")

    # Receipt records the deny decision (P0.2).
    receipts = _read_receipts(isolated_repo / ".roam" / "mcp_receipts")
    assert len(receipts) == 1
    r = receipts[0]
    assert r["policy_decision"] == "deny"
    assert r["policy_decision"] in _POLICY_DECISIONS
    assert r["required_mode"] == "migration"

    # P2.2 — receipt is schema-valid even on the deny path.
    jsonschema.validate(instance=r, schema=mcp_receipt_json_schema())

    # P0.3 — ledger still links to the on-disk receipt.
    events = list(read_run_events(isolated_repo, meta.run_id))
    receipt_events = [e for e in events if e.get("action") == "mcp_receipt"]
    assert len(receipt_events) == 1
    key = ensure_ledger_key(isolated_repo)
    verify_result = verify_chain_with_receipts(events, key, isolated_repo, meta.run_id)
    assert verify_result["state"] == "ok"
    assert verify_result["receipt_integrity"] == "ok"


# ---------------------------------------------------------------------------
# Scenario 3: Redaction path — secret in output scrubbed before receipt write
# ---------------------------------------------------------------------------


def test_redaction_path_scrubs_secret_before_receipt_write(isolated_repo, monkeypatch) -> None:
    """A tool returning a secret-shaped string must have it scrubbed.

    Maps the P0.1 invariants onto the composition path:

    * The client-visible result contains the ``[REDACTED]`` placeholder
      and never the verbatim secret.
    * ``receipt["redactions"]`` is the closed-enum tuple ``("secret",)``.
    * ``receipt["output_hash"]`` is the sha256 of the canonical-JSON of
      the REDACTED output — never the raw bytes.
    * ``extra["redaction_details"]`` lists at least one pattern from the
      catalogue (here: ``sk_prefix``).
    * P0.3 chain linkage still holds — the receipt-hash baked into the
      ledger event is the sha256 of the canonical receipt bytes (which
      already reflect redaction).
    """
    jsonschema = pytest.importorskip("jsonschema")

    monkeypatch.setenv("ROAM_AGENT_MODE", "migration")
    monkeypatch.setenv("ROAM_MODE_ENFORCEMENT", "1")

    meta = start_run(isolated_repo, agent="composition-test-redact")
    monkeypatch.setenv("ROAM_RUN_ID", meta.run_id)

    raw = {
        "command": "stub_redact",
        "summary": {"verdict": f"token {_SECRET_TOKEN_FIXTURE}"},
    }
    wrapped, call_count = _register_destructive_tool(
        monkeypatch, "roam_pipeline_redact", return_value=raw, backing_cli="mutate"
    )

    result = wrapped()
    assert call_count["n"] == 1

    # P0.1 — egress scrub fired on the client-visible bytes.
    flat = json.dumps(result)
    assert _SECRET_TOKEN_FIXTURE not in flat
    assert _REDACTED_PLACEHOLDER in flat

    # Receipt closed-enum redactions value.
    receipts = _read_receipts(isolated_repo / ".roam" / "mcp_receipts")
    assert len(receipts) == 1
    r = receipts[0]
    # JSON deserializes tuples as lists — accept either shape.
    assert tuple(r["redactions"]) == ("secret",)
    assert "secret" in REDACTION_REASONS  # reference, not literal

    # output_hash fingerprints the REDACTED output, never the raw bytes.
    expected_redacted = {
        "command": "stub_redact",
        "summary": {"verdict": f"token {_REDACTED_PLACEHOLDER}"},
    }
    canonical_redacted = json.dumps(expected_redacted, sort_keys=True, separators=(",", ":")).encode("utf-8")
    canonical_raw = json.dumps(raw, sort_keys=True, separators=(",", ":")).encode("utf-8")
    assert r["output_hash"] == hashlib.sha256(canonical_redacted).hexdigest()
    assert r["output_hash"] != hashlib.sha256(canonical_raw).hexdigest()

    # Per-pattern hit count rides in extra (P0.1 audit-detail tier).
    details = (r.get("extra") or {}).get("redaction_details") or {}
    assert "sk_prefix" in details, f"expected sk_prefix in details, got {details!r}"

    # P2.2 — redaction-path receipt is schema-valid.
    jsonschema.validate(instance=r, schema=mcp_receipt_json_schema())

    # P0.3 — chain still verifies; the receipt-hash matches the post-
    # redaction canonical bytes.
    events = list(read_run_events(isolated_repo, meta.run_id))
    key = ensure_ledger_key(isolated_repo)
    verify_result = verify_chain_with_receipts(events, key, isolated_repo, meta.run_id)
    assert verify_result["state"] == "ok"
    assert verify_result["receipt_integrity"] == "ok"


# ---------------------------------------------------------------------------
# Scenario 4: Tampered receipt — schema still valid, chain detects tamper
# ---------------------------------------------------------------------------


def test_tampered_receipt_schema_valid_chain_detects(isolated_repo, monkeypatch) -> None:
    """Schema validation is structural; tamper detection is cryptographic.

    Run a happy-path call, then mutate one field in the on-disk receipt
    JSON. The two axes diverge:

    * P2.2 — the mutated JSON is still syntactically valid against the
      schema (the changed field is a known property, the new value is in
      the property's domain).
    * P0.3 — :func:`verify_chain_with_receipts` recomputes the sha256
      from disk, compares to the chain-baked ``receipt_hash``, and
      reports ``state == "tampered"`` + ``receipt_integrity == "tampered"``
      with ``first_tamper_at_seq`` pointing at the right event.

    The two checks supports evidence for the "shape AND executability"
    two-instrument rule: a schema-valid envelope is not proof of
    integrity; the chain hash is.
    """
    jsonschema = pytest.importorskip("jsonschema")

    monkeypatch.setenv("ROAM_AGENT_MODE", "migration")
    monkeypatch.setenv("ROAM_MODE_ENFORCEMENT", "1")

    meta = start_run(isolated_repo, agent="composition-test-tamper")
    monkeypatch.setenv("ROAM_RUN_ID", meta.run_id)

    raw_output = {"command": "stub_tamper", "summary": {"verdict": "ok"}}
    wrapped, _call_count = _register_destructive_tool(
        monkeypatch, "roam_pipeline_tamper", return_value=raw_output, backing_cli="mutate"
    )
    wrapped(symbol="foo")

    receipt_path = next((isolated_repo / ".roam" / "mcp_receipts" / meta.run_id).glob("*.json"))
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))

    # Flip a known string field to another string. The result stays
    # schema-valid (tool_name is just a string) but the sha256 shifts.
    original_tool_name = receipt["tool_name"]
    receipt["tool_name"] = original_tool_name + "_TAMPERED"
    tampered_canonical = json.dumps(receipt, sort_keys=True, separators=(",", ":"))
    receipt_path.write_text(tampered_canonical + "\n", encoding="utf-8")

    # P2.2 — schema validation still passes (structural, not semantic).
    jsonschema.validate(instance=receipt, schema=mcp_receipt_json_schema())

    # P0.3 — chain walks the on-disk receipt, recomputes sha256, and
    # detects the mismatch.
    events = list(read_run_events(isolated_repo, meta.run_id))
    receipt_events = [e for e in events if e.get("action") == "mcp_receipt"]
    expected_tamper_seq = receipt_events[0]["seq"]

    key = ensure_ledger_key(isolated_repo)
    verify_result = verify_chain_with_receipts(events, key, isolated_repo, meta.run_id)
    assert verify_result["state"] == "tampered"
    assert verify_result["receipt_integrity"] == "tampered"
    assert verify_result["first_tamper_at_seq"] == expected_tamper_seq

    # Cross-check: the chain-baked hash is the sha256 of the ORIGINAL
    # canonical bytes (the writer's pre-tamper output), not of the
    # tampered bytes the file now holds. This pins the hash-stability
    # invariant: the chain commitment is immutable once written.
    tampered_hash = hashlib.sha256(tampered_canonical.encode("utf-8")).hexdigest()
    assert receipt_events[0]["receipt_hash"] != tampered_hash, (
        "chain hash must remain pinned to the pre-tamper bytes — otherwise the integrity check would never fire"
    )
