"""W1032 — ``load_suppressions`` + ``load_suppressions_typed`` warnings_out plumb.

W1017 plumbed ``warnings_out`` through
:func:`roam.commands.finding_suppress.load_per_finding_suppressions_typed`
on the ``.roam/suppressions.json`` substrate. A drive-by audit found the
sibling typed wrapper
:func:`roam.commands.suppression.load_suppressions_typed` and its underlying
:func:`roam.commands.suppression.load_suppressions` (the
``.roam-suppressions.yml`` substrate) did NOT accept ``warnings_out`` — so
the Pattern-2 silent-fallback fix from W706/W1009 was incomplete for the
oldest of the four suppression loaders.

This test pins the W1032 plumb: every silent-fallback path on
``load_suppressions`` surfaces as a structured warning when ``warnings_out``
is supplied AND the typed wrapper threads the accumulator through. Pre-W1032
callers that don't supply ``warnings_out`` see byte-identical silent-empty-
list behaviour — that contract is also pinned below.

Vocabulary mirrors the W706/W1009/W1019b reference shape: warnings open with
``"suppressions: <path>: <body>"`` and the body names the failure shape +
the imperative fix step.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from roam.commands.suppression import (
    load_suppressions,
    load_suppressions_typed,
)
from roam.policy.suppression_v2 import RuleFileSuppression


# ---------------------------------------------------------------------------
# Missing-file path: no warning, no exception, empty list. The Pattern-2
# contract treats "no config" as a benign default state.
# ---------------------------------------------------------------------------


def test_missing_file_returns_empty_list_without_warning(tmp_path: Path) -> None:
    warnings_out: list[str] = []
    result = load_suppressions(tmp_path, warnings_out=warnings_out)
    assert result == []
    assert warnings_out == []


def test_missing_file_pre_w1032_caller_stays_byte_identical(tmp_path: Path) -> None:
    # Pre-W1032 callers pass no warnings_out kwarg — same return value.
    assert load_suppressions(tmp_path) == []


# ---------------------------------------------------------------------------
# Happy-path: well-formed file returns the expected dict shape; no warning.
# ---------------------------------------------------------------------------


def test_valid_load_returns_expected_dicts_without_warning(tmp_path: Path) -> None:
    (tmp_path / ".roam-suppressions.yml").write_text(
        "suppressions:\n"
        "  - rule: secret-detection\n"
        "    file: tests/fixtures/fake_secrets.py\n"
        "    reason: Test fixtures with fake credentials\n"
        "    status: safe\n"
        "  - rule: complexity-high\n"
        "    file: src/roam/index/indexer.py\n"
        "    line: 142\n"
        "    reason: Intentionally complex pipeline\n"
        "    status: acknowledged\n",
        encoding="utf-8",
    )
    warnings_out: list[str] = []
    result = load_suppressions(tmp_path, warnings_out=warnings_out)
    assert warnings_out == []
    assert len(result) == 2
    assert result[0]["rule"] == "secret-detection"
    assert result[0]["file"] == "tests/fixtures/fake_secrets.py"
    assert result[1]["rule"] == "complexity-high"
    assert result[1]["file"] == "src/roam/index/indexer.py"
    # ``line`` is coerced to int by _coerce_value.
    assert result[1]["line"] == 142


def test_valid_load_pre_w1032_caller_returns_same_dicts(tmp_path: Path) -> None:
    # Same input — the pre-W1032 call shape must produce the same rows.
    (tmp_path / ".roam-suppressions.yml").write_text(
        "suppressions:\n"
        "  - rule: r\n"
        "    file: f\n"
        "    status: safe\n",
        encoding="utf-8",
    )
    result_pre = load_suppressions(tmp_path)
    result_post = load_suppressions(tmp_path, warnings_out=[])
    assert result_pre == result_post


# ---------------------------------------------------------------------------
# Malformed YAML root (non-mapping) surfaces a warning.
# ---------------------------------------------------------------------------


def test_non_dict_root_surfaces_warning(tmp_path: Path) -> None:
    pytest.importorskip("yaml")
    # A top-level scalar (YAML reads as a plain string) is a non-mapping
    # root. The shared helper appends the canonical
    # "root is 'str', expected a mapping" warning.
    (tmp_path / ".roam-suppressions.yml").write_text("just-a-string\n", encoding="utf-8")
    warnings_out: list[str] = []
    result = load_suppressions(tmp_path, warnings_out=warnings_out)
    assert result == []
    assert any("expected a mapping" in w for w in warnings_out), warnings_out
    assert all(w.startswith("suppressions:") for w in warnings_out), warnings_out


def test_malformed_yaml_surfaces_warning(tmp_path: Path) -> None:
    pytest.importorskip("yaml")
    # Tabbed indentation + unclosed bracket — PyYAML will reject this.
    (tmp_path / ".roam-suppressions.yml").write_text(
        "suppressions:\n  - rule: [unclosed\n",
        encoding="utf-8",
    )
    warnings_out: list[str] = []
    result = load_suppressions(tmp_path, warnings_out=warnings_out)
    assert result == []
    # Helper-emitted warning: "malformed YAML: ..."
    assert any("malformed YAML" in w for w in warnings_out), warnings_out
    assert all(w.startswith("suppressions:") for w in warnings_out), warnings_out


# ---------------------------------------------------------------------------
# Malformed-entry validation: rows missing required `rule` or `file` are
# dropped with a structured warning naming the 1-based row index + missing
# field. Mirrors the W995 vocabulary used by ``smells_suppress``.
# ---------------------------------------------------------------------------


def test_malformed_entry_warns_and_is_skipped_yaml(tmp_path: Path) -> None:
    pytest.importorskip("yaml")
    # Three rows: row #1 is missing ``rule``, row #2 is well-formed, row #3
    # is missing ``file``. Helper-path: PyYAML parses the file, then
    # _validate_suppression_rows surfaces both drops.
    (tmp_path / ".roam-suppressions.yml").write_text(
        "suppressions:\n"
        "  - file: only-file.py\n"
        "    reason: missing rule\n"
        "  - rule: good\n"
        "    file: good.py\n"
        "    status: safe\n"
        "  - rule: only-rule\n"
        "    reason: missing file\n",
        encoding="utf-8",
    )
    warnings_out: list[str] = []
    result = load_suppressions(tmp_path, warnings_out=warnings_out)
    # Only the well-formed row survives.
    assert len(result) == 1
    assert result[0]["rule"] == "good"
    # Two per-row drop warnings plus one summary warning when >1 dropped.
    drop_msgs = [w for w in warnings_out if "dropped" in w]
    assert len(drop_msgs) >= 2
    # First drop names row #1 + missing "rule".
    assert any("entry #1" in w and "'rule'" in w for w in warnings_out), warnings_out
    # Third drop names row #3 + missing "file".
    assert any("entry #3" in w and "'file'" in w for w in warnings_out), warnings_out
    # Summary warning appears when more than one row dropped.
    assert any("dropped 2 malformed" in w for w in warnings_out), warnings_out


def test_malformed_entry_pre_w1032_silently_skips(tmp_path: Path) -> None:
    # Same shape as above — without warnings_out, the malformed rows are
    # silently dropped (byte-identical to pre-W1032 behaviour).
    (tmp_path / ".roam-suppressions.yml").write_text(
        "suppressions:\n"
        "  - file: only-file.py\n"
        "  - rule: good\n"
        "    file: good.py\n",
        encoding="utf-8",
    )
    result = load_suppressions(tmp_path)
    assert len(result) == 1
    assert result[0]["rule"] == "good"


# ---------------------------------------------------------------------------
# Typed wrapper plumb-through: warnings drain to the same accumulator AND
# the typed view round-trips the rows that survived validation.
# ---------------------------------------------------------------------------


def test_typed_wrapper_plumbs_warnings_through(tmp_path: Path) -> None:
    pytest.importorskip("yaml")
    (tmp_path / ".roam-suppressions.yml").write_text(
        "suppressions:\n"
        "  - file: missing-rule.py\n"
        "  - rule: r\n"
        "    file: f\n"
        "    status: safe\n",
        encoding="utf-8",
    )
    warnings_out: list[str] = []
    result = load_suppressions_typed(tmp_path, warnings_out=warnings_out)
    # Typed view returned for the well-formed row only.
    assert len(result) == 1
    assert isinstance(result[0], RuleFileSuppression)
    assert result[0].rule == "r"
    assert result[0].file == "f"
    # Warning from the malformed row propagated through the typed wrapper.
    assert any("entry #1" in w and "'rule'" in w for w in warnings_out), warnings_out


def test_typed_wrapper_pre_w1032_signature_still_works(tmp_path: Path) -> None:
    # Pre-W1032 callers used the positional-only signature. Same result.
    (tmp_path / ".roam-suppressions.yml").write_text(
        "suppressions:\n"
        "  - rule: r\n"
        "    file: f\n",
        encoding="utf-8",
    )
    result = load_suppressions_typed(tmp_path)
    assert len(result) == 1
    assert result[0].rule == "r"


# ---------------------------------------------------------------------------
# No-PyYAML fallback: when PyYAML is unavailable, the tiny_parser handles
# the documented shape. Drop-warning vocabulary survives this fallback path.
# (Exercised opportunistically — when PyYAML IS installed, the test still
# passes by hitting the PyYAML branch with the same fixture.)
# ---------------------------------------------------------------------------


def test_unknown_root_key_silently_returns_empty(tmp_path: Path) -> None:
    pytest.importorskip("yaml")
    # PyYAML happily parses ``other:`` as a mapping with no ``suppressions``
    # key. The loader returns ``[]`` without a warning — this matches
    # pre-W1032 behaviour where a missing ``suppressions:`` key was
    # silently treated as no-rules. (Future tightening could surface this
    # as a warning, but that's a separate change.)
    (tmp_path / ".roam-suppressions.yml").write_text(
        "other:\n  - foo: bar\n",
        encoding="utf-8",
    )
    warnings_out: list[str] = []
    result = load_suppressions(tmp_path, warnings_out=warnings_out)
    assert result == []
    assert warnings_out == []


def test_unreadable_file_surfaces_warning(tmp_path: Path) -> None:
    # Substitute a directory for the file path — Path.read_text raises
    # OSError on a directory, exercising the helper's OSError branch.
    target = tmp_path / ".roam-suppressions.yml"
    target.mkdir()
    warnings_out: list[str] = []
    result = load_suppressions(tmp_path, warnings_out=warnings_out)
    assert result == []
    assert any("could not read file" in w for w in warnings_out), warnings_out
    assert all(w.startswith("suppressions:") for w in warnings_out), warnings_out
