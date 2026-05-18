"""Hash-pin ``mcp-server-card.json`` so unintended drift fails CI.

The MCP server card describes the tool surface — name, description,
capabilities, presets — that agents read to decide what's available.
Audit R17: a tampered card could shape agent behaviour without the
maintainer noticing. Pin its SHA-256 here; if the card legitimately
changes, the contributor updates the constant in the same PR. CI
catches anyone editing the card without acknowledging the security
review surface.

When updating the card:
  1. Edit the JSON (or run ``python dev/build_readme_counts.py --apply``).
  2. ``--apply`` auto-rotates ``_EXPECTED_CARD_SHA256`` below since W844 —
     no manual digest paste needed in the common case.
  3. If you DELIBERATELY want the pin to stay (e.g. you're debugging a
     card edit you don't want the substrate to chase), run
     ``--apply --no-rotate-card-hash`` and update the pin by hand using
     this test's failure message as the source of truth.
  4. Note in the PR description what changed and why.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

from tests._helpers.repo_root import repo_root

# SHA-256 of the canonical mcp-server-card.json bytes. Auto-rotated by
# ``dev/build_readme_counts.py --apply`` since W844 (closes the W563 gap
# the W789/W794/W1307/W1308 manual bumps used to fill). The W844 substrate
# computes this digest on LF-normalized bytes so CI Linux + local Windows
# agree.
# W793: renamed ``display_name`` → ``title`` per SEP-2127 readiness.
# v13.1 (2026-05-15): version bump 13.0 → 13.1.
# v13.2 (2026-05-16, W1307+W1308): version bump 13.1 → 13.2; LF
# normalization fixed CRLF/LF hash divergence between Windows + CI Linux.
# W794 [landed 2026-05-16]: added SEP-2127-ready icons[] field (favicon.svg +
# og.png pointing at deployed assets on roam-code.com). All 3
# .well-known card path variants stay byte-identical per the W792
# invariant.
_EXPECTED_CARD_SHA256 = "3ef07cd2954c87128a1ebd64b58fbefaaf0e4b77f40fce985af9bebb715e9ddf"


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
