"""Tests for py-except-pass narrow-exception suppression.

When ``except`` names only narrow OS / parse / optional-import errors
that are legitimately swallowed (file went away, optional package
missing), the detector should NOT fire. Only ``Exception`` /
``BaseException`` / bare ``except`` and custom error classes count.
"""

from __future__ import annotations

import pytest

from roam.catalog.python_idioms import _except_clause_is_narrow


@pytest.mark.parametrize(
    "clause,expected",
    [
        # Bare except — never narrow (catches BaseException too)
        ("", False),
        ("   ", False),
        # Single narrow types
        (" OSError", True),
        (" FileNotFoundError", True),
        (" UnicodeDecodeError", True),
        (" ImportError", True),
        (" ModuleNotFoundError", True),
        (" KeyError", True),
        (" AttributeError", True),
        # Tuple of narrow types
        (" (OSError, UnicodeDecodeError)", True),
        (" (FileNotFoundError, PermissionError)", True),
        (" (ImportError, ModuleNotFoundError)", True),
        # ``as exc`` doesn't change narrowness
        (" OSError as exc", True),
        (" (OSError, IOError) as exc", True),
        # Broad / generic — always fires
        (" Exception", False),
        (" BaseException", False),
        (" Exception as exc", False),
        # Custom exception classes — fires (we can't tell if narrow)
        (" CustomError", False),
        (" MyServiceError", False),
        # Mixed: narrow + broad — fires (the broad one wins)
        (" (OSError, Exception)", False),
        # qualified names still match
        (" subprocess.TimeoutExpired", True),
    ],
)
def test_except_clause_is_narrow(clause, expected):
    assert _except_clause_is_narrow(clause) is expected
