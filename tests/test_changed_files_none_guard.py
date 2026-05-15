"""None-guard hygiene for ``changed_files.is_test_file`` / ``is_low_risk_file``.

W1013 + W1014: the two helpers used to crash on ``None`` input, forcing
3 callers (laws/miner, cmd_fn_coupling, cmd_risk) to write cargo-cult
``... or ""`` fallbacks. The functions now guard themselves and mirror
the canonical shape at ``src/roam/catalog/_shared.py:193-194``.
"""

from __future__ import annotations

from roam.commands.changed_files import is_low_risk_file, is_test_file


class TestIsTestFileNoneGuard:
    def test_none_returns_false(self) -> None:
        assert is_test_file(None) is False

    def test_empty_string_returns_false(self) -> None:
        assert is_test_file("") is False

    def test_recognises_pytest_layout(self) -> None:
        assert is_test_file("tests/test_foo.py") is True

    def test_recognises_non_test_source(self) -> None:
        assert is_test_file("src/roam/cli.py") is False


class TestIsLowRiskFileNoneGuard:
    def test_none_returns_false(self) -> None:
        assert is_low_risk_file(None) is False

    def test_empty_string_returns_false(self) -> None:
        assert is_low_risk_file("") is False
