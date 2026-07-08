"""W169 reconcile-survival invariant: docs/COMMANDS.md (the full command index)
must exist AND stay in sync with the live `roam surface`.

The command index was dropped once in a history reconcile with nothing to catch
it. This test fails if the doc goes missing OR drifts from the command surface,
so a future drop/rename cannot pass CI silently. Regenerate on failure:
    python scripts/build_commands_doc.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import build_commands_doc as bcd  # noqa: E402


def test_commands_doc_exists():
    assert bcd.DOC.exists(), (
        "docs/COMMANDS.md is MISSING (W169 reconcile-survival invariant). "
        "Regenerate: python scripts/build_commands_doc.py"
    )


def test_commands_doc_in_sync_with_surface():
    surface = bcd._surface()
    want = bcd.render(surface)
    have = bcd.DOC.read_text(encoding="utf-8")
    assert have == want, (
        "docs/COMMANDS.md is OUT OF SYNC with `roam surface` "
        f"({surface.get('command_count')} commands). "
        "Regenerate: python scripts/build_commands_doc.py"
    )


def test_every_command_is_documented():
    """Belt-and-suspenders: every live command name appears in the index."""
    surface = bcd._surface()
    doc = bcd.DOC.read_text(encoding="utf-8")
    missing = [c["name"] for c in surface.get("commands", []) if f"`{c['name']}`" not in doc]
    assert not missing, f"commands missing from docs/COMMANDS.md: {missing[:20]}"
