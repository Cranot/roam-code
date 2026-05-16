"""Tests for ``dev/build_readme_counts.py`` — the count-drift killer.

The script auto-substitutes command/MCP-tool counts into README.md,
CLAUDE.md, llms-install.md, and mcp-server-card.json between explicit
auto-count markers. These tests verify:

1. ``--check`` exits 0 when docs already match truth.
2. ``--check`` exits non-zero on drift; ``--apply`` fixes it.
3. The script is idempotent (running it twice produces no change).
4. The existing compat-sweep test still passes after a script run.
5. Marker rewrites preserve non-marker content byte-for-byte.
6. JSON updates preserve the file's whitespace style (no reformatting).
"""

from __future__ import annotations

import json
import subprocess
import sys

from tests._helpers.repo_root import repo_root

# Resolve via git's canonical toplevel so nested-worktree dispatch
# (``.claude/worktrees/.../.claude/worktrees/...``) still finds the
# project root that owns ``CLAUDE.md`` / ``README.md`` (W572).
ROOT = repo_root()
SRC = ROOT / "src"
SCRIPT = ROOT / "dev" / "build_readme_counts.py"
README = ROOT / "README.md"
CLAUDE = ROOT / "CLAUDE.md"
LLMS = ROOT / "llms-install.md"
PUBLIC_CARD = ROOT / "templates" / "distribution" / "landing-page" / ".well-known" / "mcp-server-card.json"
BUNDLED_CARD = ROOT / "src" / "roam" / "mcp-server-card.json"


if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))


def _run(args: list[str]) -> subprocess.CompletedProcess[str]:
    """Run the script in-process via subprocess, capture rc + output."""
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        check=False,
    )


# ---------------------------------------------------------------------------
# 1. Script runs cleanly at HEAD state (counts already match)
# ---------------------------------------------------------------------------


def test_script_check_passes_at_head():
    """``--check`` exits 0 when every count site agrees with truth.

    This is the post-Wave-21 steady state. If a sibling agent in this wave
    forgot to re-run the script, this test will fail with rc=1.
    """
    proc = _run(["--check"])
    assert proc.returncode == 0, f"--check failed (rc={proc.returncode}); stdout={proc.stdout!r} stderr={proc.stderr!r}"


# ---------------------------------------------------------------------------
# 2. Drift detection + auto-fix
# ---------------------------------------------------------------------------


def test_script_exits_nonzero_on_drift(tmp_path, monkeypatch):
    """Inject a wrong count into README; --check must exit non-zero, --apply must fix."""
    # Compute the truth-aligned count substring from the source of truth instead
    # of hard-coding it — otherwise this test breaks every time someone adds a
    # command (the literal here used to be "233 commands and 149 MCP tools" and
    # silently rotted to a no-op replace once the count drifted to 234).
    from roam.surface_counts import cli_surface_counts, mcp_surface_counts

    cli = cli_surface_counts()
    mcp = mcp_surface_counts()
    correct_cmd_count = int(cli["command_names"])
    correct_mcp_count = int(mcp["registered_tools"])
    # Mirror the ``readme-headline-prose`` template in ``dev/build_readme_counts.py``:
    # ``f"... through {c.command_names} commands and {c.mcp_full} MCP tools ..."``.
    truth_string = f"{correct_cmd_count} commands and {correct_mcp_count} MCP tools"
    # Wrong-by-one is enough to trigger drift detection, and is guaranteed not to
    # collide with the truth string (so the replace below cannot become a no-op).
    drift_string = f"{correct_cmd_count - 1} commands and {correct_mcp_count} MCP tools"
    assert drift_string != truth_string, "wrong-by-one drift string collided with truth"

    backup = README.read_text(encoding="utf-8")
    assert truth_string in backup, (
        f"test precondition failed: truth-aligned substring {truth_string!r} "
        f"not present in README — run dev/build_readme_counts.py --apply first"
    )
    try:
        # Inject drift: replace the truth-count with a wrong-by-one count inside
        # the auto-count block. Use a marker-protected line so the change is reversible.
        bad = backup.replace(truth_string, drift_string, 1)
        assert bad != backup, "test setup failed — drift injection didn't change file"
        README.write_text(bad, encoding="utf-8")

        # --check must now exit 1.
        proc = _run(["--check"])
        assert proc.returncode == 1, (
            f"--check should exit 1 on drift; got rc={proc.returncode} stdout={proc.stdout!r} stderr={proc.stderr!r}"
        )
        assert "DRIFT" in proc.stderr, f"stderr should mention DRIFT: {proc.stderr!r}"

        # --apply must fix it.
        proc = _run(["--apply"])
        assert proc.returncode == 0
        assert README.read_text(encoding="utf-8") == backup, "--apply should restore README to the truth-aligned state"
    finally:
        # Always restore.
        README.write_text(backup, encoding="utf-8")


def test_script_idempotent(tmp_path):
    """Running --apply twice produces identical bytes — no oscillation."""
    snapshots_before = {
        p: p.read_text(encoding="utf-8") for p in (README, CLAUDE, LLMS, PUBLIC_CARD, BUNDLED_CARD) if p.exists()
    }
    proc1 = _run(["--apply"])
    assert proc1.returncode == 0
    snapshots_after1 = {p: p.read_text(encoding="utf-8") for p in snapshots_before}
    proc2 = _run(["--apply"])
    assert proc2.returncode == 0
    snapshots_after2 = {p: p.read_text(encoding="utf-8") for p in snapshots_before}
    assert snapshots_after1 == snapshots_after2, "script is not idempotent — second --apply changed bytes"
    # Also: at HEAD steady-state, the first --apply should also not change.
    assert snapshots_after1 == snapshots_before, (
        "first --apply at HEAD changed bytes — drift exists without --check noticing"
    )


# ---------------------------------------------------------------------------
# 3. Compat-sweep still passes
# ---------------------------------------------------------------------------


def test_existing_compat_sweep_still_passes():
    """Re-run the surface-count snapshot pin to confirm the script's
    output satisfies the existing drift test.

    Defence in depth: ``test_compat_sweep.py::test_surface_command_count_matches_actual``
    is the post-hoc detector. This test confirms the auto-generator emits
    documentation that the detector accepts.
    """
    from tests.test_compat_sweep import (  # type: ignore[import-not-found]
        test_mcp_tool_count_matches_actual,
        test_surface_command_count_matches_actual,
    )

    test_surface_command_count_matches_actual()
    test_mcp_tool_count_matches_actual()


# ---------------------------------------------------------------------------
# 4. Marker discipline — non-marker content is preserved byte-for-byte
# ---------------------------------------------------------------------------


def test_apply_preserves_non_marker_content():
    """--apply at steady state must not touch a single byte of non-marker content.

    Records the file bytes outside the auto-count blocks, runs --apply,
    re-records, asserts equality.
    """
    import re

    def _strip_blocks(text: str) -> str:
        # Match any of our auto-count blocks (BEGIN...END pairs).
        pat = re.compile(
            r"<!-- BEGIN auto-count:[^>]+ -->.*?<!-- END auto-count:[^>]+ -->",
            flags=re.DOTALL,
        )
        return pat.sub("__BLOCK__", text)

    # CLAUDE.md is intentionally untracked on public clones (89a338d9); skip
    # when absent. README + LLMS are always present.
    targets = tuple(p for p in (README, CLAUDE, LLMS) if p.exists())
    before = {p: _strip_blocks(p.read_text(encoding="utf-8")) for p in targets}
    proc = _run(["--apply"])
    assert proc.returncode == 0
    after = {p: _strip_blocks(p.read_text(encoding="utf-8")) for p in targets}
    for p in before:
        assert before[p] == after[p], (
            f"non-marker content of {p.name} changed across --apply — script is touching bytes it shouldn't"
        )


# ---------------------------------------------------------------------------
# 5. JSON updates preserve whitespace style
# ---------------------------------------------------------------------------


def test_card_updates_preserve_array_formatting():
    """The cards have inline arrays like ``["stdio", "sse", "streamable-http"]``.
    The script must not reflow them. Validates by checking the inline form
    is still inline after a no-op --apply.
    """
    raw = PUBLIC_CARD.read_text(encoding="utf-8")
    assert '"supported": ["stdio"' in raw or '"supported":["stdio"' in raw, (
        "test precondition failed: inline ``supported`` array missing"
    )
    proc = _run(["--apply"])
    assert proc.returncode == 0
    raw_after = PUBLIC_CARD.read_text(encoding="utf-8")
    assert raw_after == raw, "card was reformatted by no-op --apply"
    # Also re-parses as valid JSON.
    json.loads(raw_after)


def test_card_bundled_and_public_stay_byte_identical():
    """test_bundled_card_matches_public_card requires both copies to be
    byte-identical. The script writes to both — confirm they agree
    after --apply.
    """
    proc = _run(["--apply"])
    assert proc.returncode == 0
    a = PUBLIC_CARD.read_text(encoding="utf-8")
    b = BUNDLED_CARD.read_text(encoding="utf-8")
    assert a == b, (
        "public and bundled mcp-server-card.json must be byte-identical; script wrote different bytes to each"
    )


# ---------------------------------------------------------------------------
# 6. Help / no-op modes
# ---------------------------------------------------------------------------


def test_script_has_check_and_apply_flags():
    """``--help`` exposes the two main flags."""
    proc = _run(["--help"])
    assert proc.returncode == 0
    assert "--check" in proc.stdout
    assert "--apply" in proc.stdout
