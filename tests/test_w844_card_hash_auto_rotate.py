"""W844 — regression tests for the card-hash auto-rotate substrate.

The W563 mechanism was supposed to bump ``_EXPECTED_CARD_SHA256`` in
``tests/test_mcp_server_card_hash.py`` automatically whenever any of the 4
``mcp-server-card.json`` files changes. In practice waves W789, W794, W1307,
and W1308 all had to hand-re-compute + paste the new SHA256 — the substrate
existed in name but not in wiring. W844 closes the loop by extending
``dev/build_readme_counts.py --apply`` to:

  1. Sync the 2 extra ``.well-known`` mirror paths (SEP-1649 + SEP-2127) to
     the canonical bytes — closes the W1308 manual-sync gap as a drive-by.
  2. Recompute the SHA-256 of the canonical card (LF-bytes) and rewrite the
     ``_EXPECTED_CARD_SHA256`` pin in place — closes the W563 gap.

These tests pin both behaviours so a future refactor cannot silently
re-introduce the manual-bump treadmill. Every mutation is confined to a
``tmp_path`` shadow passed through the script's ``--root`` option; the real
release files remain read-only under pytest-xdist.
"""

from __future__ import annotations

import hashlib
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from tests._helpers.repo_root import repo_root

ROOT = repo_root()
SCRIPT = ROOT / "dev" / "build_readme_counts.py"

_SHADOW_RELATIVE_PATHS = (
    "pyproject.toml",
    "src/roam/cli.py",
    "src/roam/mcp_server.py",
    "README.md",
    "CLAUDE.md",
    "llms-install.md",
    "AGENTS.md",
    "src/roam/mcp-server-card.json",
    "templates/distribution/landing-page/.well-known/mcp-server-card.json",
    "templates/distribution/landing-page/.well-known/mcp/server-card.json",
    "templates/distribution/landing-page/.well-known/mcp-server-card",
    "tests/test_mcp_server_card_hash.py",
)


def _card_paths(root: Path) -> tuple[Path, Path, Path, Path, Path]:
    well_known = root / "templates" / "distribution" / "landing-page" / ".well-known"
    return (
        root / "src" / "roam" / "mcp-server-card.json",
        well_known / "mcp-server-card.json",
        well_known / "mcp" / "server-card.json",
        well_known / "mcp-server-card",
        root / "tests" / "test_mcp_server_card_hash.py",
    )


@pytest.fixture
def shadow_root(tmp_path: Path) -> Path:
    """Copy every count-script input/output into an isolated root."""
    shadow = tmp_path / "shadow"
    shadow.mkdir()
    for relative in _SHADOW_RELATIVE_PATHS:
        source = ROOT / relative
        if not source.exists():
            continue
        destination = shadow / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
    return shadow


def _lf_sha256(path: Path) -> str:
    """Hash on LF-normalized bytes — matches the runtime pin discipline (W1308)."""
    return hashlib.sha256(path.read_bytes().replace(b"\r\n", b"\n")).hexdigest()


def _read_pin(pin_file: Path) -> str:
    text = pin_file.read_text(encoding="utf-8")
    # Grab the digest between quotes on the _EXPECTED_CARD_SHA256 line.
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("_EXPECTED_CARD_SHA256"):
            _, _, rhs = stripped.partition("=")
            return rhs.strip().strip('"')
    raise AssertionError("_EXPECTED_CARD_SHA256 not found in pin file")


def _run_apply(shadow: Path, *extra: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--apply", *extra, "--root", str(shadow)],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        check=False,
    )


def test_w844_card_edit_triggers_pin_rotation(shadow_root: Path) -> None:
    """Editing the canonical card + running --apply rewrites the SHA-256 pin.

    Simulates the W1307 / W1308 / W794 manual-bump scenario: a wave changes
    a card-content field, then --apply recomputes the digest + rewrites the
    pin without human intervention.
    """
    bundled_card, public_card, _, _, pin_file = _card_paths(shadow_root)
    original_pin = _read_pin(pin_file)
    original_bytes = bundled_card.read_bytes()

    # Inject a benign change into a non-count-bearing field. ``card_url`` is
    # a string near the top of the card and isn't touched by
    # ``_update_mcp_card_text`` regexes, so the edit survives --apply.
    mutated = original_bytes.replace(
        b'"card_url"',
        b'"card_url"  ',
        1,
    )
    assert mutated != original_bytes, "test setup: card edit produced no byte change"
    bundled_card.write_bytes(mutated)
    # Keep the well-known canonical in lock-step so the W792 invariant
    # holds at the point --apply reads from PUBLIC_CARD (the canonical
    # source for the well-known mirrors).
    public_card.write_bytes(mutated)

    expected_digest = _lf_sha256(public_card)
    assert expected_digest != original_pin, "test setup: mutated card hashes to the original pin"

    result = _run_apply(shadow_root)
    assert result.returncode == 0, f"--apply failed: {result.stderr}"

    new_pin = _read_pin(pin_file)
    # The pin must now match the digest of the post-_apply canonical card.
    # _apply_mcp_card may further mutate count-bearing fields, so recompute.
    post_apply_digest = _lf_sha256(public_card)
    assert new_pin == post_apply_digest, (
        f"pin not rotated to match post-apply card bytes\n  pin:        {new_pin}\n  card hash:  {post_apply_digest}"
    )


def test_w844_no_rotate_card_hash_opt_out(shadow_root: Path) -> None:
    """``--no-rotate-card-hash`` preserves the pin even when the card changes."""
    bundled_card, public_card, _, _, pin_file = _card_paths(shadow_root)
    original_pin = _read_pin(pin_file)

    mutated = bundled_card.read_bytes().replace(
        b'"card_url"',
        b'"card_url"  ',
        1,
    )
    bundled_card.write_bytes(mutated)
    public_card.write_bytes(mutated)

    result = _run_apply(shadow_root, "--no-rotate-card-hash")
    assert result.returncode == 0, f"--apply failed: {result.stderr}"

    assert _read_pin(pin_file) == original_pin, "--no-rotate-card-hash should leave _EXPECTED_CARD_SHA256 untouched"


def test_w844_well_known_mirrors_synced(shadow_root: Path) -> None:
    """``--apply`` syncs SEP-1649 + SEP-2127 mirrors to the canonical bytes.

    Closes the W1308 manual-sync gap: prior --apply runs only updated the
    flat .json mirror, leaving the nested + no-suffix mirrors stale for the
    W792 byte-identity guard to catch later. The auto-rotate substrate now
    keeps all 3 mirrors in lock-step.
    """
    _, public_card, sep_1649, sep_2127, _ = _card_paths(shadow_root)
    # Corrupt the two non-canonical mirrors so the script has work to do.
    sep_1649.write_bytes(b'{"intentionally": "stale"}\n')
    sep_2127.write_bytes(b'{"intentionally": "stale"}\n')

    result = _run_apply(shadow_root)
    assert result.returncode == 0, f"--apply failed: {result.stderr}"

    canonical = public_card.read_bytes()
    assert sep_1649.read_bytes() == canonical, "SEP-1649 mirror not re-synced"
    assert sep_2127.read_bytes() == canonical, "SEP-2127 mirror not re-synced"


def test_w844_apply_is_idempotent_with_rotation(shadow_root: Path) -> None:
    """Two consecutive ``--apply`` runs produce no further changes.

    Idempotency proof for the new substrate: rotation must converge in one
    step. If the rewritten pin file is itself counted as a drift on the
    second pass, the substrate would oscillate. (It doesn't — the pin file
    is not in MARKDOWN_TARGETS, and the digest is stable once written.)
    """
    # First pass — may or may not change anything (steady-state usually no-op).
    paths = _card_paths(shadow_root)
    _run_apply(shadow_root)
    # Snapshot bytes after first pass.
    after_first = {path: path.read_bytes() for path in paths}
    # Second pass — must be a no-op.
    _run_apply(shadow_root)
    for path, content in after_first.items():
        assert path.read_bytes() == content, (
            f"non-idempotent: {path.relative_to(shadow_root).as_posix()} changed on second --apply"
        )
