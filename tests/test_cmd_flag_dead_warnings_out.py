"""W1010 - Pattern 2 (silent fallback) tests for ``_load_known_stale``.

Drives the ``warnings_out`` accumulator plumbed in W1010 through the
known-stale loader on :mod:`cmd_flag_dead`. Mirrors W706 / W1019c /
W1019d disciplines: every silent-fallback path surfaces a structured
warning when an accumulator is supplied; when no accumulator is
supplied, behaviour is byte-identical to pre-W1010 (silent empty set).

The schema is plain text (one flag name per line, ``#`` comments) - NOT
YAML - so this loader does NOT migrate to
``roam.commands._yaml_loader.load_yaml_with_warnings``. The only
documented failure modes are OSError on open and UnicodeDecodeError
during iteration; the "non-mapping root" / "non-dict entries" branches
do not apply to a line-oriented file.

Cross-links:
- W706 - canonical ``warnings_out`` plumb-through.
- W1019c / W1019d - YAML-config callsite migrations.
- ``(internal memo)`` - survey + rationale.
- CLAUDE.md "Six systemic anti-patterns" / Pattern 2 "Silent fallback".
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from roam.commands.cmd_flag_dead import _load_known_stale

# ---------------------------------------------------------------------------
# _load_known_stale - direct loader behaviour
# ---------------------------------------------------------------------------


def test_load_missing_file_warns(tmp_path: Path) -> None:
    """Missing file is an OSError - the open() call raises FileNotFoundError.

    Unlike the YAML loader (which treats absence as "default state"),
    the known-stale loader's caller already guards on ``config_path`` via
    Click's ``type=click.Path(exists=True)`` - so reaching the loader
    with a missing file is a programmer error worth surfacing.
    """
    warnings_out: list[str] = []
    stale = _load_known_stale(str(tmp_path / "missing.txt"), warnings_out=warnings_out)
    assert stale == set()
    assert len(warnings_out) == 1
    msg = warnings_out[0]
    assert "known-stale" in msg
    assert "could not read file" in msg


def test_load_valid_file_no_warning(tmp_path: Path) -> None:
    """Happy path: well-formed file, no warnings, all flag names returned."""
    body = (
        "# Known-stale feature flags\n"
        "old-checkout-redesign\n"
        "abandoned-experiment\n"
        "\n"
        "# Another comment\n"
        "deprecated-toggle\n"
    )
    p = tmp_path / "stale-flags.txt"
    p.write_text(body, encoding="utf-8")
    warnings_out: list[str] = []
    stale = _load_known_stale(str(p), warnings_out=warnings_out)
    assert warnings_out == []
    assert stale == {"old-checkout-redesign", "abandoned-experiment", "deprecated-toggle"}


def test_load_unreadable_file_warns(tmp_path: Path) -> None:
    """File whose open() fails (path is a directory, not a file) - caller
    must see the structured failure, not a silent empty set."""
    # A directory path triggers OSError (IsADirectoryError on POSIX,
    # PermissionError on Windows) when passed to ``open(..., "r")``.
    dir_path = tmp_path / "subdir"
    dir_path.mkdir()
    warnings_out: list[str] = []
    stale = _load_known_stale(str(dir_path), warnings_out=warnings_out)
    assert stale == set()
    assert len(warnings_out) == 1
    msg = warnings_out[0]
    assert "known-stale" in msg
    assert "could not read file" in msg
    # Path is repr()-quoted in the warning - on Windows that doubles
    # backslashes, so compare on the leaf name rather than the raw str.
    assert "subdir" in msg


@pytest.mark.skipif(
    sys.platform == "win32",
    reason="Windows file-handle semantics make a forced UnicodeDecodeError "
    "during iteration unreliable; the OSError path covers the equivalent "
    "Pattern-2 branch.",
)
def test_load_non_utf8_file_warns(tmp_path: Path) -> None:
    """Non-UTF8 bytes during line iteration raise UnicodeDecodeError;
    the loader must surface that as a structured warning, not silently
    return the partial set already accumulated."""
    p = tmp_path / "stale-flags.txt"
    # Write bytes that are valid in latin-1 but invalid as UTF-8 mid-stream:
    # 0xff is never a valid leading byte in UTF-8.
    p.write_bytes(b"valid-flag\n\xff\xfe-invalid-utf8\n")
    warnings_out: list[str] = []
    stale = _load_known_stale(str(p), warnings_out=warnings_out)
    # On decode failure we return an empty set (consistent with the
    # OSError branch); the accumulator carries the actionable warning.
    assert stale == set()
    assert len(warnings_out) == 1
    msg = warnings_out[0]
    assert "known-stale" in msg
    assert "UTF-8" in msg


def test_load_warnings_out_none_is_byte_identical_silent(tmp_path: Path) -> None:
    """When the caller doesn't pass an accumulator, behaviour is silent
    (pre-W1010 byte-identical) on every failure path."""
    # Missing file - no raise, no print, just empty set.
    stale = _load_known_stale(str(tmp_path / "missing.txt"))
    assert stale == set()

    # Directory path (OSError) - same byte-identical silence.
    dir_path = tmp_path / "subdir"
    dir_path.mkdir()
    stale = _load_known_stale(str(dir_path))
    assert stale == set()


def test_load_warnings_out_none_byte_identical_happy_path(tmp_path: Path) -> None:
    """Happy path with no accumulator returns the exact same set as with one."""
    body = "flag-a\n# comment\nflag-b\n"
    p = tmp_path / "stale-flags.txt"
    p.write_text(body, encoding="utf-8")

    silent = _load_known_stale(str(p))
    warnings_out: list[str] = []
    instrumented = _load_known_stale(str(p), warnings_out=warnings_out)
    assert silent == instrumented == {"flag-a", "flag-b"}
    assert warnings_out == []
