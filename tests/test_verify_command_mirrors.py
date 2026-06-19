"""Regression tests for verify duplicate-name command mirror exemptions."""

from __future__ import annotations


def test_api_command_mirror_is_not_duplicate():
    """Python API command shims may mirror Click command names."""
    from roam.commands.cmd_verify import _duplicate_indexes, _exact_duplicate_for_symbol

    existing = {
        "id": 1,
        "name": "index",
        "kind": "function",
        "signature": None,
        "line_start": 41,
        "file_path": "src/roam/commands/cmd_index.py",
        "file_role": "source",
    }
    new_sym = {
        "id": 2,
        "name": "index",
        "kind": "method",
        "signature": None,
        "line_start": 196,
        "file_path": "src/roam/api.py",
        "file_role": "source",
    }
    existing_by_name, _ = _duplicate_indexes([existing])

    assert _exact_duplicate_for_symbol(new_sym, existing_by_name, {2}, "source") is None
