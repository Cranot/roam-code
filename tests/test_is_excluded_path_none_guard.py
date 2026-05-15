"""W1029: None-guard tests for ``is_excluded_path``.

Pins the contract that ``is_excluded_path`` accepts ``str | None`` so
callers (cmd_complexity, cmd_fan, ...) can pass raw ``row["file_path"]``
without the cargo-cult ``or ""`` defensive wrapper.

Background: W1013/W1014 added the same None-guard pattern to
``is_test_file`` + ``is_low_risk_file`` in ``changed_files``. This sweep
(W1029) extends the discipline to ``is_excluded_path``, ``tokenize``,
and ``_camel_split``. See the "Verify the cycle before hedging" /
W907 anti-pattern rule in CLAUDE.md.
"""

from __future__ import annotations

from roam.output.file_role_hints import is_excluded_path


def test_is_excluded_path_none_returns_false():
    """``None`` input returns False — a path we can't classify can't be excluded."""
    assert is_excluded_path(None) is False


def test_is_excluded_path_empty_string_returns_false():
    """Empty string input returns False (same semantics as None)."""
    assert is_excluded_path("") is False


def test_is_excluded_path_normal_behavior_preserved():
    """Normal-path behaviour is unchanged by the W1029 signature widening."""
    # node_modules is in the default-excluded set (tooling dir).
    assert is_excluded_path("node_modules/foo/bar.js") is True
    # A regular source path is not excluded.
    assert is_excluded_path("src/roam/cli.py") is False


def test_is_excluded_path_extra_dirs_with_none():
    """Passing ``extra_dirs`` alongside a None path still returns False."""
    assert is_excluded_path(None, extra_dirs=frozenset({"custom"})) is False
