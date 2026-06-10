"""Smoke + envelope-shape tests for roam_full_coupling.

The tool bundles ``roam_coupling`` (temporal pairs) + ``roam_deps``
(structural imports/importers) + ``roam_file_info`` (top symbols)
into one envelope keyed on ``file_path``. LAW 5 ("≤3 concrete steps")
motivates the bundle: agents that would otherwise chain three sequential
calls can issue one.
"""

from __future__ import annotations

from roam.mcp_server import roam_full_coupling


def test_full_coupling_returns_required_envelope_keys() -> None:
    """Envelope shape must include the four data sections + a verdict."""
    result = roam_full_coupling(file_path="src/roam/cli.py", top_n=3, root=".")

    assert isinstance(result, dict)
    for key in ("command", "file", "coupling", "deps", "top_symbols", "summary"):
        assert key in result, f"missing envelope key {key!r}: got {sorted(result)}"
    assert result["command"] == "roam_full_coupling"
    assert result["file"] == "src/roam/cli.py"


def test_full_coupling_verdict_is_concrete_noun_anchored() -> None:
    """The verdict ends on a concrete-noun anchor (LAW 4)."""
    result = roam_full_coupling(file_path="src/roam/cli.py", top_n=3, root=".")
    verdict = result["summary"]["verdict"]
    assert verdict, "verdict must be non-empty"
    assert verdict.endswith(("src/roam/cli.py", "for src/roam/cli.py")), (
        f"verdict should terminate on the file anchor: {verdict!r}"
    )


def test_full_coupling_pairs_filtered_to_target_file() -> None:
    """Returned pairs touch the requested file_path."""
    target = "src/roam/cli.py"
    result = roam_full_coupling(file_path=target, top_n=5, root=".")
    pairs = result["coupling"]["pairs"]
    assert isinstance(pairs, list)
    for pair in pairs:
        assert isinstance(pair, dict)
        assert target in (pair.get("file_a"), pair.get("file_b")), f"pair does not touch {target!r}: {pair}"


def test_full_coupling_top_n_respected() -> None:
    """top_n caps both the pairs and top_symbols slices."""
    result = roam_full_coupling(file_path="src/roam/cli.py", top_n=2, root=".")
    assert len(result["coupling"]["pairs"]) <= 2
    assert len(result["top_symbols"]) <= 2


def test_full_coupling_handles_unknown_file_gracefully() -> None:
    """Unknown path should not crash — pairs/top_symbols may be empty."""
    result = roam_full_coupling(file_path="this/file/does/not/exist.py", top_n=3, root=".")
    assert result["coupling"]["pairs"] == []
    assert isinstance(result.get("top_symbols"), list)
    assert "verdict" in result["summary"]
