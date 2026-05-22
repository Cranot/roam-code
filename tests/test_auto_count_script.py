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

Parallel safety
---------------
The three modifying tests (``test_script_exits_nonzero_on_drift``,
``test_script_idempotent``, plus the steady-state apply called by
``test_apply_preserves_non_marker_content`` / ``test_card_updates_preserve_array_formatting``
/ ``test_card_bundled_and_public_stay_byte_identical``) used to invoke
the script against the REAL repo root. Under ``pytest -n auto`` they
raced each other and any other test that read README.md / CLAUDE.md /
llms-install.md / the MCP cards — including
``test_readme_recipe_count_matches_registry``, which failed during the
v13.5 hardening when a sibling auto-count test had momentarily written
intermediate (drift-injected) bytes that the recipe-count test then read
in its window.

The fix: every modifying test now operates on a ``tmp_path`` COPY of the
count-bearing files (README/CLAUDE/llms-install/AGENTS + both MCP cards
+ the two ``.well-known`` mirrors + ``pyproject.toml`` +
``src/roam/cli.py`` for ``_CATEGORIES`` + ``src/roam/mcp_server.py`` for
``_CORE_TOOLS`` + ``tests/test_mcp_server_card_hash.py`` for the SHA-256
pin). The script is invoked with ``--root <tmp_path>`` so all reads and
writes are confined to the copy. The REAL working tree is never touched
by these tests, so they can run in parallel without contaminating each
other or any other test that reads the canonical files.

The non-modifying tests (``test_script_check_passes_at_head``,
``test_existing_compat_sweep_still_passes``,
``test_script_has_check_and_apply_flags``) still target the real repo
because they assert about steady-state truth that the running source
tree owns.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

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


# Files the build script reads (truth inputs) + files it writes (count-bearing
# output). The modifying tests copy each present file under ``ROOT`` into a
# ``tmp_path`` shadow, preserving the relative path under the configured
# ``--root``. Missing-on-disk files (e.g. CLAUDE.md on public clones) are
# silently skipped — the script already handles the file-missing case.
_SHADOW_RELATIVE_PATHS: tuple[str, ...] = (
    # Truth inputs read by the script.
    "pyproject.toml",
    "src/roam/cli.py",
    "src/roam/mcp_server.py",
    # Markdown targets the script may rewrite.
    "README.md",
    "CLAUDE.md",
    "llms-install.md",
    "AGENTS.md",
    # MCP card pair + the two extra .well-known mirrors.
    "src/roam/mcp-server-card.json",
    "templates/distribution/landing-page/.well-known/mcp-server-card.json",
    "templates/distribution/landing-page/.well-known/mcp/server-card.json",
    "templates/distribution/landing-page/.well-known/mcp-server-card",
    # SHA-256 pin file that ``--apply`` auto-rotates.
    "tests/test_mcp_server_card_hash.py",
)


def _make_shadow_root(tmp_path: Path) -> Path:
    """Copy the count-bearing files into ``tmp_path``; return the shadow root.

    The script's ``--root`` flag (added alongside this refactor) points all
    its reads and writes at this shadow tree, so modifying tests never touch
    the real repo and can run in parallel without contaminating each other
    or any test that reads the canonical README/CLAUDE/llms-install/cards.
    """
    shadow = tmp_path / "shadow"
    shadow.mkdir()
    for rel in _SHADOW_RELATIVE_PATHS:
        src = ROOT / rel
        if not src.exists():
            continue
        dst = shadow / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
    return shadow


def _run(args: list[str]) -> subprocess.CompletedProcess[str]:
    """Run the script in-process via subprocess, capture rc + output.

    Without ``--root``, the script defaults to its own ancestor (the real
    repo root). Used by the read-only / steady-state tests.
    """
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        check=False,
    )


def _run_in(shadow: Path, args: list[str]) -> subprocess.CompletedProcess[str]:
    """Run the script against ``shadow`` via ``--root <shadow>``.

    All file reads/writes are confined to the shadow tree. This is the
    parallel-safe entry point used by every modifying test in this module.
    """
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args, "--root", str(shadow)],
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


def test_script_exits_nonzero_on_drift(tmp_path):
    """Inject a wrong count into README; --check must exit non-zero, --apply must fix.

    Operates on a ``tmp_path`` shadow of the count-bearing files (see module
    docstring) so the real README is never touched and the test is safe
    under ``pytest -n auto``.
    """
    # W844-drive-by: the old ``readme-headline-prose`` block emitted
    # ``f"... {command_names} commands and {mcp_full} MCP tools ..."`` and was
    # dropped from the script in v13.2 (see the v13.2 comment on
    # ``_readme_blocks`` in ``dev/build_readme_counts.py``). The README hero
    # now leads with the positioning core (credential-free + zero-egress +
    # tamper-evident evidence) — the old "N commands and M MCP tools" literal
    # is no longer present anywhere in README.md, so the prior precondition
    # assertion failed on clean main.
    #
    # The script DOES still manage a command-count substring inside the
    # ``readme-canonical-mention`` marker block. Mirroring that template:
    # ``f"...is **{command_names} commands ({canonical_commands} canonical "
    # ``f"+ {alias_names} aliases) organised into..."``
    # so we target the ``N commands (M canonical`` substring instead — it's
    # uniquely-bound inside an auto-count block, a wrong-by-one in N breaks
    # script drift detection cleanly, and the script's own ``--apply`` fixes it.
    from roam.surface_counts import cli_surface_counts

    cli = cli_surface_counts()
    correct_cmd_count = int(cli["command_names"])
    correct_canonical = int(cli["canonical_commands"])
    truth_string = f"{correct_cmd_count} commands ({correct_canonical} canonical"
    # Wrong-by-one is enough to trigger drift detection, and is guaranteed not to
    # collide with the truth string (so the replace below cannot become a no-op).
    drift_string = f"{correct_cmd_count - 1} commands ({correct_canonical} canonical"
    assert drift_string != truth_string, "wrong-by-one drift string collided with truth"

    shadow = _make_shadow_root(tmp_path)
    shadow_readme = shadow / "README.md"
    backup = shadow_readme.read_text(encoding="utf-8")
    assert truth_string in backup, (
        f"test precondition failed: truth-aligned substring {truth_string!r} "
        f"not present in README — run dev/build_readme_counts.py --apply first"
    )
    # Inject drift: replace the truth-count with a wrong-by-one count inside
    # the auto-count block. The shadow tree is disposable (tmp_path),
    # so the try/finally restore the old test needed is no longer required.
    bad = backup.replace(truth_string, drift_string, 1)
    assert bad != backup, "test setup failed — drift injection didn't change file"
    shadow_readme.write_text(bad, encoding="utf-8")

    # --check must now exit 1.
    proc = _run_in(shadow, ["--check"])
    assert proc.returncode == 1, (
        f"--check should exit 1 on drift; got rc={proc.returncode} stdout={proc.stdout!r} stderr={proc.stderr!r}"
    )
    assert "DRIFT" in proc.stderr, f"stderr should mention DRIFT: {proc.stderr!r}"

    # --apply must fix it.
    proc = _run_in(shadow, ["--apply"])
    assert proc.returncode == 0
    assert shadow_readme.read_text(encoding="utf-8") == backup, (
        "--apply should restore README to the truth-aligned state"
    )


def test_script_idempotent(tmp_path):
    """Running --apply twice produces identical bytes — no oscillation.

    Operates on a ``tmp_path`` shadow of the count-bearing files (see module
    docstring) so the real working tree is never touched.
    """
    shadow = _make_shadow_root(tmp_path)
    shadow_files = tuple(
        shadow / rel
        for rel in (
            "README.md",
            "CLAUDE.md",
            "llms-install.md",
            "templates/distribution/landing-page/.well-known/mcp-server-card.json",
            "src/roam/mcp-server-card.json",
        )
    )
    snapshots_before = {p: p.read_text(encoding="utf-8") for p in shadow_files if p.exists()}
    proc1 = _run_in(shadow, ["--apply"])
    assert proc1.returncode == 0
    snapshots_after1 = {p: p.read_text(encoding="utf-8") for p in snapshots_before}
    proc2 = _run_in(shadow, ["--apply"])
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


def test_apply_preserves_non_marker_content(tmp_path):
    """--apply at steady state must not touch a single byte of non-marker content.

    Records the file bytes outside the auto-count blocks, runs --apply on a
    ``tmp_path`` shadow of the real files, re-records, asserts equality.
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
    shadow = _make_shadow_root(tmp_path)
    targets = tuple(shadow / rel for rel in ("README.md", "CLAUDE.md", "llms-install.md") if (shadow / rel).exists())
    before = {p: _strip_blocks(p.read_text(encoding="utf-8")) for p in targets}
    proc = _run_in(shadow, ["--apply"])
    assert proc.returncode == 0
    after = {p: _strip_blocks(p.read_text(encoding="utf-8")) for p in targets}
    for p in before:
        assert before[p] == after[p], (
            f"non-marker content of {p.name} changed across --apply — script is touching bytes it shouldn't"
        )


# ---------------------------------------------------------------------------
# 5. JSON updates preserve whitespace style
# ---------------------------------------------------------------------------


def test_card_updates_preserve_array_formatting(tmp_path):
    """The cards have inline arrays like ``["stdio", "sse", "streamable-http"]``.
    The script must not reflow them. Validates by checking the inline form
    is still inline after a no-op --apply against a ``tmp_path`` shadow.
    """
    shadow = _make_shadow_root(tmp_path)
    shadow_public_card = shadow / "templates" / "distribution" / "landing-page" / ".well-known" / "mcp-server-card.json"
    raw = shadow_public_card.read_text(encoding="utf-8")
    assert '"supported": ["stdio"' in raw or '"supported":["stdio"' in raw, (
        "test precondition failed: inline ``supported`` array missing"
    )
    proc = _run_in(shadow, ["--apply"])
    assert proc.returncode == 0
    raw_after = shadow_public_card.read_text(encoding="utf-8")
    assert raw_after == raw, "card was reformatted by no-op --apply"
    # Also re-parses as valid JSON.
    json.loads(raw_after)


def test_card_bundled_and_public_stay_byte_identical(tmp_path):
    """test_bundled_card_matches_public_card requires both copies to be
    byte-identical. The script writes to both — confirm they agree
    after --apply against a ``tmp_path`` shadow.
    """
    shadow = _make_shadow_root(tmp_path)
    shadow_public_card = shadow / "templates" / "distribution" / "landing-page" / ".well-known" / "mcp-server-card.json"
    shadow_bundled_card = shadow / "src" / "roam" / "mcp-server-card.json"
    proc = _run_in(shadow, ["--apply"])
    assert proc.returncode == 0
    a = shadow_public_card.read_text(encoding="utf-8")
    b = shadow_bundled_card.read_text(encoding="utf-8")
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
