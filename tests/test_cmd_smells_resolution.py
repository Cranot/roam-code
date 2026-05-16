"""W1245 batch-1 — premise BAIL for ``cmd_smells``.

The W1245 batch-1 task selected five resolver-using commands for
Pattern-2 variant-D resolution disclosure. The premise check at
implementation time found ``cmd_smells`` does NOT call
``find_symbol(conn, name)``. The command's only ``commands.``-namespace
imports are ``ensure_index`` from ``roam.commands.resolve`` and
``smells_suppress`` helpers — no resolver API call.

The W1233 audit listed ``cmd_smells`` among resolver-using commands;
re-inspection at batch-1 time established that smells is a codebase-
wide structural detector — it scans every symbol / file in the index
for cognitive-complexity, parallel-hierarchy, data-clump, feature-envy,
god-class and ~24 other anti-pattern shapes — and never takes a
single-symbol target argument from the caller.

A whole-codebase detector that never resolves a symbol from a
user-supplied name has nothing to disclose under variant-D, which
guards the success verdict on degraded RESOLVER output specifically.

This file exists so the batch-1 test sweep (``pytest -k resolution``)
still finds a test stub at the documented path and the BAIL is
discoverable by future audits. The single test is skipped with a
clear reason so a future implementor who adds resolver-mode to
``smells`` (e.g. ``smells --symbol X`` for per-symbol detection) is
forced to revisit this BAIL.
"""

from __future__ import annotations

import pytest


@pytest.mark.skip(
    reason=(
        "W1245 batch-1 BAIL: cmd_smells does not call find_symbol(); it is a "
        "whole-codebase structural detector with no per-symbol input. No "
        "resolver disclosure to apply. Re-evaluate if smells gains a "
        "per-symbol mode."
    )
)
def test_cmd_smells_resolution_disclosure_bail() -> None:
    """Placeholder for the W1245 BAIL — see module docstring for rationale."""
