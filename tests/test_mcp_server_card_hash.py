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

# SHA-256 of the canonical mcp-server-card.json bytes. Update via the
# protocol in this file's docstring whenever the card changes.
_EXPECTED_CARD_SHA256 = "b917ddd39ed7ea60831eac3e946cdb1fa28594b2e78f13eaf12bcefac22477b2"


def _card_path() -> Path:
    return Path(__file__).resolve().parents[1] / "src" / "roam" / "mcp-server-card.json"


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
