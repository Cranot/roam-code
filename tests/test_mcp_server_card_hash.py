"""Hash-pin ``mcp-server-card.json`` so unintended drift fails CI.

The MCP server card describes the tool surface — name, description,
capabilities, presets — that agents read to decide what's available.
Audit R17: a tampered card could shape agent behaviour without the
maintainer noticing. Pin its SHA-256 here; if the card legitimately
changes, the contributor updates the constant in the same PR. CI
catches anyone editing the card without acknowledging the security
review surface.

When updating the card:
  1. Edit the JSON.
  2. Re-run this test; it'll fail and print the new digest.
  3. Paste the new digest into ``_EXPECTED_CARD_SHA256`` below.
  4. Note in the PR description what changed and why.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

from tests._helpers.repo_root import repo_root

# SHA-256 of the canonical mcp-server-card.json bytes. Update via the
# protocol in this file's docstring whenever the card changes.
# W793: bumped after renaming ``display_name`` → ``title`` per SEP-2127
# readiness. SEP-2127-ready, byte-stable change; no other card content
# moved. W563/W789 auto-rotate is broken per W844 finding — bumped manually.
# v13.1 (2026-05-15): bumped after version bump 13.0 → 13.1 (card body
# only changed the ``"version"`` field; W554 audit-report YAML bundle
# unchanged; all other card content stable).
# v13.2 (2026-05-16, W1307+W1308): bumped after version bump 13.1 → 13.2.
# Card body changed only in the "version" field; auto-derived counts
# (238 commands / 224 MCP tools / 57 core preset) unchanged. The hash
# is computed on the LF-line-ending bytes (canonical git storage) so
# the pin matches the CI Linux digest, not the Windows-CRLF digest.
_EXPECTED_CARD_SHA256 = "bef2dc3e8d4618e5a105621430d32bb93361e3e8d0c4f5f0f6fb7dfd50fe3ca8"


def _card_path() -> Path:
    return repo_root() / "src" / "roam" / "mcp-server-card.json"


def test_mcp_server_card_hash_pinned():
    """The card's SHA-256 must match ``_EXPECTED_CARD_SHA256``.

    A mismatch means either:
      (a) Someone legitimately edited the card and forgot to bump the
          digest — fix per the protocol in this file's docstring.
      (b) Something altered the card unexpectedly — investigate before
          accepting the change.

    Either path forces a deliberate review of the agent-surface contract.
    """
    path = _card_path()
    assert path.exists(), f"card file missing at {path}"
    actual = hashlib.sha256(path.read_bytes()).hexdigest()
    assert actual == _EXPECTED_CARD_SHA256, (
        f"\nmcp-server-card.json digest drift detected.\n"
        f"  expected: {_EXPECTED_CARD_SHA256}\n"
        f"  actual:   {actual}\n\n"
        f"If the card change is intentional, update _EXPECTED_CARD_SHA256 "
        f"in tests/test_mcp_server_card_hash.py and document the change "
        f"in the PR description. The card describes the agent-facing "
        f"tool surface — a tampered card is a real attack vector."
    )
