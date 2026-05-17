"""W1030 -- tests for the ``return_status`` empty-file disambiguation kwarg.

Phase 2 of the Pattern-2 YAML loader hardening (W1016 memo, follow-up to
W1018). The bare ``load_yaml_with_warnings`` return shape conflated
several distinct on-disk states behind a single empty-container ``{}``:

* file does not exist
* file exists but is zero-byte / whitespace-only
* file has content but the parser returned ``None`` (comments-only YAML)
* file has malformed content
* file parses but root type is wrong
* schema validator rejected the content

W1030 ships a ``return_status=True`` opt-in kwarg that surfaces a closed
enum (:data:`LOAD_STATUSES`) so callers can act on the actual on-disk
state. ``return_status=False`` (default) keeps the legacy single-value
shape -- every pre-W1030 callsite stays byte-identical.

Coverage taxonomy:

1. ``status == "missing"`` -- file does not exist; value is ``None``.
2. ``status == "empty_file"`` -- zero-byte file; value is empty container.
3. ``status == "empty_file"`` -- whitespace-only file; same outcome.
4. ``status == "empty_yaml"`` -- comments-only YAML; value is empty container.
5. ``status == "parse_error"`` -- malformed YAML; value is empty container + warning.
6. ``status == "ok"`` -- valid YAML; value is the parsed dict.
7. ``status == "wrong_root_type"`` -- list root, dict expected.
8. ``status == "schema_invalid"`` -- validator returned non-empty list.
9. Legacy contract: ``return_status=False`` (default) returns the bare
   value -- backward compat with W1018/W1019 callsites.
10. ``LOAD_STATUSES`` is a closed-enum tuple; every status returned by
    the helper is a member of that tuple.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from roam.commands._yaml_loader import (
    LOAD_STATUSES,
    load_yaml_with_warnings,
)


def _write(tmp_path: Path, name: str, content: str) -> Path:
    p = tmp_path / name
    p.write_text(content, encoding="utf-8")
    return p


# ---------------------------------------------------------------------------
# 1. missing
# ---------------------------------------------------------------------------


def test_status_missing_returns_none(tmp_path: Path) -> None:
    warnings: list[str] = []
    value, status = load_yaml_with_warnings(
        tmp_path / "nope.yml",
        config_label="cfg",
        warnings_out=warnings,
        return_status=True,
    )
    assert value is None
    assert status == "missing"
    assert warnings == []


# ---------------------------------------------------------------------------
# 2 + 3. empty_file (zero-byte and whitespace-only)
# ---------------------------------------------------------------------------


def test_status_empty_file_zero_byte(tmp_path: Path) -> None:
    path = _write(tmp_path, "empty.yml", "")
    warnings: list[str] = []
    value, status = load_yaml_with_warnings(
        path,
        config_label="cfg",
        warnings_out=warnings,
        return_status=True,
    )
    assert value == {}
    assert status == "empty_file"
    # An empty file is a valid on-disk state; no warning emitted.
    assert warnings == []


def test_status_empty_file_whitespace_only(tmp_path: Path) -> None:
    # A file that is bytes-non-empty but yields no useful content after
    # strip is still 'empty_file' from the helper's perspective -- the
    # documented shape is absent before the parser even runs.
    path = _write(tmp_path, "ws.yml", "   \n\t\n  \n")
    warnings: list[str] = []
    value, status = load_yaml_with_warnings(
        path,
        config_label="cfg",
        warnings_out=warnings,
        return_status=True,
    )
    assert value == {}
    assert status == "empty_file"
    assert warnings == []


# ---------------------------------------------------------------------------
# 4. empty_yaml (file has content, parser returns None)
# ---------------------------------------------------------------------------


def test_status_empty_yaml_comments_only(tmp_path: Path) -> None:
    # Comments-only YAML: PyYAML returns None on a stream that has
    # bytes-but-no-documented-entries. Distinct from empty_file because
    # the file has substantive bytes (the user wrote comments and means
    # something by it); the documented shape is intentionally absent.
    path = _write(tmp_path, "comments.yml", "# header comment\n# nothing here yet\n")
    warnings: list[str] = []
    value, status = load_yaml_with_warnings(
        path,
        config_label="cfg",
        warnings_out=warnings,
        return_status=True,
    )
    assert value == {}
    assert status == "empty_yaml"
    # Same as empty_file: no warning, the file is a valid empty state.
    assert warnings == []


# ---------------------------------------------------------------------------
# 5. parse_error
# ---------------------------------------------------------------------------


def test_status_parse_error_warns(tmp_path: Path) -> None:
    path = _write(tmp_path, "broken.yml", "rules: [unterminated\n")
    warnings: list[str] = []
    value, status = load_yaml_with_warnings(
        path,
        config_label="cfg",
        warnings_out=warnings,
        return_status=True,
    )
    assert value == {}
    assert status == "parse_error"
    assert len(warnings) == 1
    assert "malformed YAML" in warnings[0]


# ---------------------------------------------------------------------------
# 6. ok
# ---------------------------------------------------------------------------


def test_status_ok_returns_parsed(tmp_path: Path) -> None:
    path = _write(tmp_path, "ok.yml", "rules:\n  - task_id: io-in-loop\n")
    warnings: list[str] = []
    value, status = load_yaml_with_warnings(
        path,
        config_label="cfg",
        warnings_out=warnings,
        return_status=True,
    )
    assert isinstance(value, dict)
    assert value["rules"][0]["task_id"] == "io-in-loop"
    assert status == "ok"
    assert warnings == []


# ---------------------------------------------------------------------------
# 7. wrong_root_type
# ---------------------------------------------------------------------------


def test_status_wrong_root_type_dict_expected(tmp_path: Path) -> None:
    path = _write(tmp_path, "list.yml", "- a\n- b\n")
    warnings: list[str] = []
    value, status = load_yaml_with_warnings(
        path,
        config_label="cfg",
        warnings_out=warnings,
        return_status=True,
    )
    assert value == {}
    assert status == "wrong_root_type"
    assert any("expected a mapping" in w for w in warnings)


# ---------------------------------------------------------------------------
# 8. schema_invalid
# ---------------------------------------------------------------------------


def test_status_schema_invalid_warns(tmp_path: Path) -> None:
    path = _write(tmp_path, "bad-schema.yml", "rules:\n  - foo: bar\n")

    def _validator(data: Any) -> list[str]:
        return ["cfg: 'bad-schema.yml': rules[0] missing required field `task_id`."]

    warnings: list[str] = []
    value, status = load_yaml_with_warnings(
        path,
        schema_validator=_validator,
        config_label="cfg",
        warnings_out=warnings,
        return_status=True,
    )
    assert value == {}
    assert status == "schema_invalid"
    assert len(warnings) == 1
    assert "missing required field" in warnings[0]


# ---------------------------------------------------------------------------
# 9. Legacy contract: return_status=False (default) is byte-identical
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "name, content, expected",
    [
        ("ok.yml", "k: v\n", {"k": "v"}),
        ("empty.yml", "", {}),
        ("comments.yml", "# only\n", {}),
        ("broken.yml", "rules: [unterminated\n", {}),
    ],
)
def test_return_status_default_false_is_legacy_shape(
    tmp_path: Path,
    name: str,
    content: str,
    expected: Any,
) -> None:
    """Pre-W1030 callers MUST keep working -- value is returned bare,
    not as a (value, status) tuple. The W1018 + W1019 + W1036 + W1051 +
    W1052 callsites all rely on this shape; W1030 is strictly additive.
    """
    path = _write(tmp_path, name, content)
    result = load_yaml_with_warnings(
        path,
        config_label="cfg",
        warnings_out=[],
        # return_status omitted -- default False
    )
    # Result is the bare value, never a tuple.
    assert not isinstance(result, tuple)
    assert result == expected


def test_return_status_false_missing_file_returns_bare_none(tmp_path: Path) -> None:
    """Missing-file path returns ``None`` bare, not ``(None, "missing")``."""
    result = load_yaml_with_warnings(
        tmp_path / "missing.yml",
        config_label="cfg",
        warnings_out=[],
    )
    assert result is None


# ---------------------------------------------------------------------------
# 10. LOAD_STATUSES closed-enum membership invariant
# ---------------------------------------------------------------------------


def test_every_returned_status_is_in_closed_enum(tmp_path: Path) -> None:
    """Status strings must be drawn from :data:`LOAD_STATUSES`. Drift here
    would mean a new status got introduced silently -- breaks callers
    doing ``if status == "ok":`` style switches.
    """
    cases: list[tuple[str, str, dict[str, Any]]] = [
        ("missing", "absent", {"_path_override": tmp_path / "missing.yml"}),
        ("empty_file", "", {}),
        ("empty_yaml", "# comments only\n", {}),
        ("parse_error", "rules: [unterminated\n", {}),
        ("ok", "k: v\n", {}),
        ("wrong_root_type", "- a\n- b\n", {}),
    ]
    for expected_status, content, opts in cases:
        path = opts.get("_path_override") or _write(tmp_path, f"{expected_status}.yml", content)
        _value, status = load_yaml_with_warnings(
            path,
            config_label="cfg",
            warnings_out=[],
            return_status=True,
        )
        assert status in LOAD_STATUSES, f"{status!r} not in LOAD_STATUSES"
        assert status == expected_status
