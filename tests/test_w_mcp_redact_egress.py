"""MCP-P0.1 — egress secret redaction on the MCP receipt boundary.

Per ``dev/BACKLOG.md`` MCP-P0.1: ``redact_secrets_in_string`` ships in
``src/roam/security/redact.py`` with 7 patterns (GitHub PAT, fine-grained
PAT, sk-prefix, AWS AKIA, Bearer, PEM, JWT) but historically was never
wired into the MCP egress path. This test pins the fix:

1. A sensitive tool that returns a string containing a secret-shaped
   token must NOT leak the verbatim secret to the MCP client.
2. The receipt's ``redactions`` tuple must contain ``"secret"``.
3. The receipt's ``output_hash`` must hash the REDACTED output
   (consistent with what the client saw) — NOT the raw output.
4. Per-pattern hit counts must ride in ``extra["redaction_details"]``
   so audit consumers can see which patterns fired without losing the
   closed-enum invariant on ``redactions``.

Mirrors the harness pattern in ``tests/test_mcp_receipt_emitter.py``.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Helpers (lifted/aligned with test_mcp_receipt_emitter.py)
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


@pytest.fixture
def isolated_repo(tmp_path, monkeypatch):
    """Create a tmp git-repo-shaped dir and clear inherited env vars."""
    (tmp_path / ".git").mkdir()
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("ROAM_RUN_ID", raising=False)
    monkeypatch.delenv("ROAM_AGENT_ID", raising=False)
    monkeypatch.delenv("ROAM_MCP_CLIENT_ID", raising=False)
    return tmp_path


def _register_sensitive_returning(monkeypatch, name: str, return_value):
    """Register a synthetic sensitive @_tool that returns ``return_value``."""
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
        return return_value

    return m._wrap_with_receipt(name, _inner)


# ---------------------------------------------------------------------------
# 1. Egress redaction at the client boundary
# ---------------------------------------------------------------------------


# A deterministic sk-prefix token shaped like the SECRET_PATTERNS regex.
_SECRET_TOKEN = "sk-test-1234567890abcdef1234567890"
_REDACTED_PLACEHOLDER = "[REDACTED]"


def test_egress_redaction_strips_secret_from_client_visible_output(isolated_repo, monkeypatch) -> None:
    """A sensitive tool returning a secret in its envelope must NOT leak
    the verbatim secret to the MCP client. The redacted form is what
    crosses the boundary."""
    raw_output = {
        "command": "stub_leaky",
        "summary": {"verdict": f"emitted with token {_SECRET_TOKEN}"},
        "_meta": {"cli_exit_code": 0},  # ride-through invariant — int must survive
    }
    wrapped = _register_sensitive_returning(monkeypatch, "stub_leaky", raw_output)
    result = wrapped(symbol="useThemeClasses")

    # The verbatim secret must NOT appear anywhere in the client-visible
    # output (recursive scrub covers nested dicts).
    flat = json.dumps(result)
    assert _SECRET_TOKEN not in flat, f"secret leaked verbatim into MCP output: {flat!r}"
    assert _REDACTED_PLACEHOLDER in flat, f"expected [REDACTED] placeholder, got {flat!r}"

    # Wrapper-bridge passthrough invariant: _meta.cli_exit_code is an int
    # and must survive the recursive walk unchanged.
    assert result["_meta"]["cli_exit_code"] == 0


def test_receipt_redactions_field_records_secret_reason(isolated_repo, monkeypatch) -> None:
    """The receipt's ``redactions`` tuple includes the closed-enum
    ``"secret"`` reason whenever a secret pattern fired."""
    raw_output = {
        "command": "stub_with_secret",
        "summary": {"verdict": f"verbatim {_SECRET_TOKEN}"},
    }
    wrapped = _register_sensitive_returning(monkeypatch, "stub_with_secret", raw_output)
    wrapped()

    receipts = _read_receipts(isolated_repo / ".roam" / "mcp_receipts")
    assert len(receipts) == 1
    r = receipts[0]
    assert "secret" in r["redactions"], f"expected 'secret' in redactions, got {r['redactions']!r}"


def test_receipt_output_hash_matches_redacted_output_not_raw(isolated_repo, monkeypatch) -> None:
    """Receipt's ``output_hash`` is sha256(redacted) — NOT sha256(raw).

    This is the security invariant: the hash must be consistent with what
    the client actually received, so an auditor reconstructing the call
    can't be misled by a hash that fingerprints the verbatim secret
    bytes the client never saw.
    """
    raw_output = {
        "command": "stub_hash_check",
        "summary": {"verdict": f"with {_SECRET_TOKEN}"},
    }
    expected_redacted = {
        "command": "stub_hash_check",
        "summary": {"verdict": f"with {_REDACTED_PLACEHOLDER}"},
    }

    wrapped = _register_sensitive_returning(monkeypatch, "stub_hash_check", raw_output)
    wrapped()

    receipts = _read_receipts(isolated_repo / ".roam" / "mcp_receipts")
    assert len(receipts) == 1
    r = receipts[0]

    canonical_raw = json.dumps(raw_output, sort_keys=True, separators=(",", ":")).encode("utf-8")
    canonical_redacted = json.dumps(expected_redacted, sort_keys=True, separators=(",", ":")).encode("utf-8")
    raw_hash = hashlib.sha256(canonical_raw).hexdigest()
    redacted_hash = hashlib.sha256(canonical_redacted).hexdigest()

    assert r["output_hash"] == redacted_hash, (
        f"output_hash must hash REDACTED output, got {r['output_hash']!r} vs expected {redacted_hash!r}"
    )
    assert r["output_hash"] != raw_hash, (
        "output_hash must NOT hash the raw (verbatim-secret) bytes — that would let the audit-trail "
        "fingerprint a value the client never saw"
    )


def test_redaction_details_carries_per_pattern_hit_count(isolated_repo, monkeypatch) -> None:
    """``extra["redaction_details"]`` records which pattern fired and how
    many hits. This is the audit-detail tier above the closed-enum
    ``redactions`` tuple."""
    raw_output = {
        "command": "stub_details",
        "summary": {"verdict": f"two hits: {_SECRET_TOKEN} and AKIAIOSFODNN7EXAMPLE"},
    }
    wrapped = _register_sensitive_returning(monkeypatch, "stub_details", raw_output)
    wrapped()

    receipts = _read_receipts(isolated_repo / ".roam" / "mcp_receipts")
    assert len(receipts) == 1
    details = receipts[0]["extra"].get("redaction_details") or {}
    # Both the sk_prefix and aws_access_key patterns should have fired.
    assert "sk_prefix" in details, f"expected sk_prefix in details, got {details!r}"
    assert "aws_access_key" in details, f"expected aws_access_key in details, got {details!r}"
    assert details["sk_prefix"] >= 1
    assert details["aws_access_key"] >= 1


def test_clean_output_emits_no_redaction_marker(isolated_repo, monkeypatch) -> None:
    """When the output contains no secrets, ``redactions`` stays empty
    and the receipt looks identical to the pre-W195 shape."""
    clean = {"command": "stub_clean", "summary": {"verdict": "no secrets here"}}
    wrapped = _register_sensitive_returning(monkeypatch, "stub_clean", clean)
    wrapped()

    receipts = _read_receipts(isolated_repo / ".roam" / "mcp_receipts")
    assert len(receipts) == 1
    r = receipts[0]
    assert r["redactions"] == [] or r["redactions"] == (), (
        f"clean output must produce empty redactions, got {r['redactions']!r}"
    )
    assert "redaction_details" not in (r.get("extra") or {})
