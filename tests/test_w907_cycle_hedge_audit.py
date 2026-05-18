"""W907-extension cycle-hedge pins (2026-05-18).

Each test asserts that the import edge a defensive docstring claims to be
avoiding does NOT actually close a cycle. They are marked
``xfail(strict=True)`` so a future fix-wave that hoists the lazy import to
top-level will flip them to PASS and force a clear ``strict-xfail-passed``
signal — the canonical W907 sealing flow.

When you fix one of these (i.e., promote the lazy import to a top-level
import and update the docstring), DELETE the ``@pytest.mark.xfail`` line
on the corresponding test. The test should then pass and stays as a
regression guard against the cargo-cult false hedge re-appearing.

Cross-ref: CLAUDE.md "Verify the cycle before hedging" rule (W907 family),
the W880 ``evidence/change_evidence._parse_iso`` seal, and the W904
``index/django_post`` seal — both of which followed the same audit shape.
"""

from __future__ import annotations

import ast
import pathlib

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src" / "roam"

_W907_REASON = (
    "W907 cargo-cult cycle hedge — the file claims a cycle that doesn't "
    "exist. Pin documents the false claim until the lazy import is hoisted "
    "to top-level (which will flip this test to PASS / strict-xfail-passed)."
)


def _top_level_roam_imports(rel_path: str) -> set[str]:
    """Return the set of ``roam.*`` modules imported at module top level."""
    path = SRC_ROOT.parent.parent / rel_path
    tree = ast.parse(path.read_text(encoding="utf-8"))
    out: set[str] = set()
    for stmt in tree.body:
        if isinstance(stmt, ast.ImportFrom) and stmt.module and stmt.module.startswith("roam"):
            out.add(stmt.module)
        elif isinstance(stmt, ast.Import):
            for alias in stmt.names:
                if alias.name.startswith("roam"):
                    out.add(alias.name)
    return out


# ---------------------------------------------------------------------------
# Pin 1 — src/roam/output/sarif.py:3415
#
# Claim (verbatim from sarif.py): "Lazy import: avoids a top-of-module cycle
# between roam.output.sarif and roam.commands.cmd_auth_gaps."
#
# Reality: ``cmd_auth_gaps``'s top-level roam imports are exactly:
#   roam.capability, roam.commands.resolve, roam.db.connection,
#   roam.output._severity, roam.output.confidence, roam.output.formatter
# None of these reach back to ``roam.output.sarif``. No cycle exists.
# ---------------------------------------------------------------------------


def test_no_cycle_between_sarif_and_cmd_auth_gaps() -> None:
    """W907-fix-B regression guard (xfail flipped 2026-05-18).

    The lazy import at sarif.py:3415 was hoisted to module top
    (``from roam.commands.cmd_auth_gaps import _auth_gap_confidence_tier,
    _auth_gap_finding_kind``). This test now passes plainly and remains as
    a regression guard against EITHER (a) ``cmd_auth_gaps.py`` growing a
    top-level ``roam.output.sarif`` import (which WOULD close the cycle in
    the dangerous direction), OR (b) ``output/sarif.py`` losing its new
    top-level ``roam.commands.cmd_auth_gaps`` import (the hoist getting
    reverted).
    """
    cmd_imports = _top_level_roam_imports("src/roam/commands/cmd_auth_gaps.py")
    sarif_imports = _top_level_roam_imports("src/roam/output/sarif.py")

    # Post-hoist: sarif imports cmd_auth_gaps at top-level (verified-no-cycle).
    # The forbidden direction is cmd_auth_gaps -> sarif at top-level, which
    # WOULD close a cycle. Guard against that regression.
    assert "roam.output.sarif" not in cmd_imports, (
        "Regression: cmd_auth_gaps.py now imports roam.output.sarif at "
        "top-level — this WOULD close the cycle that sarif.py already "
        "opens in the safe direction. Move the sarif import back inside "
        "a function body OR remove the cmd_auth_gaps top-level import in "
        "src/roam/output/sarif.py."
    )
    assert "roam.commands.cmd_auth_gaps" in sarif_imports, (
        "Regression: output/sarif.py no longer imports "
        "roam.commands.cmd_auth_gaps at top-level. The W907-fix-B hoist "
        "was reverted; restore `from roam.commands.cmd_auth_gaps import "
        "_auth_gap_confidence_tier, _auth_gap_finding_kind` at module "
        "top of src/roam/output/sarif.py."
    )


# ---------------------------------------------------------------------------
# Pin 2 — src/roam/commands/cmd_pr_bundle.py:1564
#
# Claim (verbatim): "Local import avoids hard module-load cycle:
# permits.store is a W198 substrate module, cmd_pr_bundle is its primary
# consumer."
#
# Reality: ``permits/store.py``'s top-level roam imports are exactly:
#   roam.atomic_io, roam.output.formatter
# Neither reaches back to ``roam.commands.cmd_pr_bundle``. No cycle exists.
# ---------------------------------------------------------------------------


def test_no_cycle_between_pr_bundle_and_permits_store() -> None:
    """W907-fix-C regression guard (xfail flipped 2026-05-18).

    The lazy import at cmd_pr_bundle.py:1564 was hoisted to module top
    (``from roam.permits.store import load_permits_from_disk``). This
    test now passes plainly and remains as a regression guard: it fires
    if EITHER (a) permits/store.py grows a top-level
    ``roam.commands.cmd_pr_bundle`` import (cycle-closing direction),
    OR (b) cmd_pr_bundle.py loses the new top-level
    ``roam.permits.store`` import (hoist reverted).
    """
    store_imports = _top_level_roam_imports("src/roam/permits/store.py")
    bundle_imports = _top_level_roam_imports("src/roam/commands/cmd_pr_bundle.py")

    # Post-hoist: cmd_pr_bundle imports permits.store at top-level
    # (verified-no-cycle). The forbidden direction is permits.store ->
    # cmd_pr_bundle at top-level, which WOULD close the cycle. Guard
    # against that regression.
    assert "roam.commands.cmd_pr_bundle" not in store_imports, (
        "Regression: permits/store.py now imports cmd_pr_bundle at "
        "top-level — this WOULD close the cycle that cmd_pr_bundle "
        "already opens in the safe direction. Move the cmd_pr_bundle "
        "import back inside a function body OR remove the permits.store "
        "top-level import in cmd_pr_bundle.py."
    )
    assert "roam.permits.store" in bundle_imports, (
        "Regression: cmd_pr_bundle.py no longer imports "
        "roam.permits.store at top-level. The W907-fix-C hoist was "
        "reverted; restore `from roam.permits.store import "
        "load_permits_from_disk` at module top of "
        "src/roam/commands/cmd_pr_bundle.py."
    )


# ---------------------------------------------------------------------------
# Pin 3 — src/roam/mcp_server.py:6295
#
# Claim (verbatim): "local import to avoid module-load cycle"
# (importing _COMMANDS from roam.cli inside _verify_compound_registry)
#
# Reality: ``roam/cli.py`` has ZERO top-level roam imports (it is the
# LazyGroup root). No top-level edge from cli to mcp_server exists, so
# importing _COMMANDS at the top of mcp_server.py cannot close a cycle.
# ---------------------------------------------------------------------------


def test_no_cycle_between_mcp_server_and_cli() -> None:
    """W907-fix-A regression guard (xfail flipped 2026-05-18).

    The lazy import at mcp_server.py:6295 was hoisted to module top
    (``from roam.cli import _COMMANDS``). This test now passes plainly
    and remains as a regression guard: if any future edit re-introduces
    a top-level edge in the OTHER direction (cli.py importing
    mcp_server), the assertion fires.
    """
    cli_imports = _top_level_roam_imports("src/roam/cli.py")
    mcp_imports = _top_level_roam_imports("src/roam/mcp_server.py")

    # Post-hoist: mcp_server imports cli at top-level (verified-no-cycle).
    # The forbidden direction is cli -> mcp_server at top-level, which
    # WOULD close a cycle. Guard against that regression.
    assert "roam.mcp_server" not in cli_imports, (
        "Regression: cli.py now imports mcp_server at top-level — this "
        "WOULD close the cycle that mcp_server.py:30 already opens in "
        "the safe direction. Move the cli.py import back inside a "
        "function body OR remove the mcp_server top-level import in "
        "mcp_server.py."
    )
    assert "roam.cli" in mcp_imports, (
        "Regression: mcp_server.py no longer imports roam.cli at "
        "top-level. The W907-fix-A hoist was reverted; restore "
        "`from roam.cli import _COMMANDS` at module top of "
        "src/roam/mcp_server.py."
    )


# ---------------------------------------------------------------------------
# Floor invariant — verifies the audit's prerequisites still hold. If this
# fails, the per-pin xfail tests need re-triage (the codebase has moved on
# and the alleged-cycle baseline shifted).
# ---------------------------------------------------------------------------


def test_w907_audit_baseline_unchanged() -> None:
    """The three target files still have no top-level edge to their alleged
    cycle counterpart. If this regresses, re-run the W907-extension audit
    before trusting the xfail tests above."""
    assert "roam.output.sarif" not in _top_level_roam_imports("src/roam/commands/cmd_auth_gaps.py")
    assert "roam.commands.cmd_pr_bundle" not in _top_level_roam_imports("src/roam/permits/store.py")
    cli_imports = _top_level_roam_imports("src/roam/cli.py")
    assert "roam.mcp_server" not in cli_imports
    # roam.cli has effectively no top-level roam imports — pin that floor.
    assert len(cli_imports) == 0, f"roam.cli top-level roam imports drifted: {cli_imports}"
