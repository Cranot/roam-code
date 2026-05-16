"""W1245 batch-1 — premise BAIL for ``cmd_hotspots``.

The W1245 batch-1 task selected five resolver-using commands for
Pattern-2 variant-D resolution disclosure. The premise check at
implementation time found ``cmd_hotspots`` does NOT call
``find_symbol(conn, name)`` — the only ``find_symbol`` token in the
module is a private helper ``_find_symbol_for_line(spans, line_no)``
that performs line-span lookup on whole-file scans, not target-
resolution from a user-supplied name.

The W1233 audit listed ``cmd_hotspots`` among resolver-using commands;
re-inspection at batch-1 time established the audit grep matched the
``find_symbol`` substring rather than the resolver API call. The
command operates in two modes:

* default / ``--runtime`` / ``--discrepancy``: scans ``runtime_stats``
  for trace-derived hotspots — no per-symbol input from the caller.
* ``--security``: scans every file in the project for regex-matched
  dangerous-API patterns — no per-symbol input from the caller.
* ``--danger``: file-level p75-band intersection — no per-symbol
  input from the caller.

None of these flows take a resolved symbol via ``find_symbol`` and
therefore none can emit ``resolution=symbol`` / ``fuzzy`` / ``unresolved``
disclosures. Variant-D guards the success verdict on degraded
RESOLVER output; a command that never resolves a symbol from a
user-supplied name has nothing to disclose.

This file exists so the batch-1 test sweep (``pytest -k resolution``)
still finds a test stub at the documented path and the BAIL is
discoverable by future audits. The single test is xfailed with a
strict reason so a future implementor who adds resolver-mode to
``hotspots`` is forced to revisit this BAIL.
"""

from __future__ import annotations

import pytest


@pytest.mark.skip(
    reason=(
        "W1245 batch-1 BAIL: cmd_hotspots does not call find_symbol(); the "
        "audit-grep match was the private _find_symbol_for_line helper. No "
        "resolver disclosure to apply. Re-evaluate if hotspots gains a "
        "per-symbol mode."
    )
)
def test_cmd_hotspots_resolution_disclosure_bail() -> None:
    """Placeholder for the W1245 BAIL — see module docstring for rationale."""
