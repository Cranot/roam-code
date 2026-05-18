"""W1038 + W1030 integration smoke -- ``extract_typed`` composes cleanly
with ``return_status=True``.

W1038 shipped ``extract_typed`` (the "load -> check type ->
warn-or-default" extractor) and W1030 ships ``return_status=True`` (the
empty-file disambiguation). Their composition is the canonical recipe a
new Pattern-2 callsite should follow::

    parsed, status = load_yaml_with_warnings(
        path, config_label="cfg", warnings_out=warnings,
        return_status=True,
    )
    if status == "empty_file":
        warnings.append("cfg: file is empty; using defaults.")
    if not isinstance(parsed, dict):
        return DEFAULTS
    rules = extract_typed(
        parsed, "rules", list, [],
        warnings_out=warnings,
        context=f"cfg: {path!r}",
        expected_shape="a list of rule mappings",
    )

These two tests are a smoke layer over the recipe -- they prove
W1038's extract_typed still works against a value returned through the
W1030 status-aware exit path.

Background: tests/test_extract_typed.py already covers extract_typed in
isolation (28 tests). This file covers the cross-helper composition,
which lives at the boundary between the two phases.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from roam.commands._yaml_loader import (
    LOAD_STATUSES,
    extract_typed,
    load_yaml_with_warnings,
)


def _write(tmp_path: Path, name: str, content: str) -> Path:
    p = tmp_path / name
    p.write_text(content, encoding="utf-8")
    return p


# ---------------------------------------------------------------------------
# Composition smoke -- ok path
# ---------------------------------------------------------------------------


def test_return_status_ok_feeds_extract_typed_cleanly(tmp_path: Path) -> None:
    """Helper recipe: load with status -> extract_typed pulls a list key."""
    path = _write(
        tmp_path,
        "rules.yml",
        "rules:\n  - name: r1\n    enabled: true\n  - name: r2\n",
    )
    warnings: list[str] = []
    parsed, status = load_yaml_with_warnings(
        path,
        config_label="cfg",
        warnings_out=warnings,
        return_status=True,
    )
    assert status == "ok"
    assert status in LOAD_STATUSES
    assert isinstance(parsed, dict)

    rules = extract_typed(
        parsed,
        "rules",
        list,
        [],
        warnings_out=warnings,
        context=f"cfg: {str(path)!r}",
        expected_shape="a list of rule mappings",
    )
    assert isinstance(rules, list)
    assert len(rules) == 2
    assert rules[0]["name"] == "r1"
    # No warnings from the happy path.
    assert warnings == []


# ---------------------------------------------------------------------------
# Composition smoke -- empty_file path (W1030 + W1038 cooperate)
# ---------------------------------------------------------------------------


def test_empty_file_status_routes_through_extract_typed_default(tmp_path: Path) -> None:
    """An empty-file load returns ``{}``; ``extract_typed`` then falls
    back to the default without raising. The two helpers MUST cooperate:
    W1030 surfaces the status, W1038 returns the safe default.
    """
    path = _write(tmp_path, "empty.yml", "")
    warnings: list[str] = []
    parsed, status = load_yaml_with_warnings(
        path,
        config_label="cfg",
        warnings_out=warnings,
        return_status=True,
    )
    assert status == "empty_file"
    # Empty-file path emits NO warning (the file is a valid empty state).
    assert warnings == []
    # parsed is the empty container -- extract_typed on it returns default
    # (key absent, not a type mismatch, so still no warning).
    rules = extract_typed(
        parsed,
        "rules",
        list,
        ["default-marker"],
        warnings_out=warnings,
        context=f"cfg: {str(path)!r}",
        expected_shape="a list of rule mappings",
    )
    assert rules == ["default-marker"]
    # Key-absent path: extract_typed sees value == default, isinstance(default, list)
    # passes, no warning appended. The caller decides whether absence is
    # itself a warning condition.
    assert warnings == []


# ---------------------------------------------------------------------------
# Migrated caller smoke -- cmd_health._load_gate_config still works
# ---------------------------------------------------------------------------


def test_migrated_caller_cmd_budget_load_budgets(tmp_path: Path) -> None:
    """One of the W1038 migrated callsites (cmd_budget._load_budgets)
    composes ``load_yaml_with_warnings`` + ``extract_typed``. This is a
    smoke test that the migrated callsite still works after the W1030
    refactor -- the legacy ``return_status=False`` path is unchanged.

    Contract: given a YAML file with a ``budgets:`` list, the loader
    returns the parsed entries. Empty files fall through to ``[]`` with
    no warnings (the file is a valid empty state).
    """
    from roam.commands.cmd_budget import _load_budgets

    # Happy path: valid YAML with a budgets block.
    path = _write(
        tmp_path,
        "budget.yaml",
        "budgets:\n  - name: cycles-cap\n    metric: cycles\n    max_increase: 5\n",
    )
    warnings: list[str] = []
    result = _load_budgets(path, warnings_out=warnings)
    assert isinstance(result, list)
    assert len(result) == 1
    assert result[0]["name"] == "cycles-cap"
    assert result[0]["metric"] == "cycles"
    assert warnings == []

    # Empty file path: ``[]`` returned untouched. W1030-followup-A:
    # opt-in load_status surfacing — _load_budgets now consumes
    # ``load_yaml_with_warnings(return_status=True)`` and short-circuits
    # the ``empty_file`` / ``empty_yaml`` states BEFORE the "no
    # `budgets:` key" warning fires, so the zero-byte stub disambiguates
    # from "well-formed dict missing the key". The pre-W1030-followup-A
    # contract pinned a single "no budgets key" warning here; the
    # post-migration contract is that an empty stub emits NO warning
    # (the absence is a valid empty state). Callers that need to know
    # the file existed-but-empty consume the status via
    # _load_budgets_with_status instead.
    empty = _write(tmp_path, "budget-empty.yaml", "")
    warnings_empty: list[str] = []
    result_empty: list[Any] = _load_budgets(empty, warnings_out=warnings_empty)
    assert result_empty == []
    # W1030-followup-A: empty file is now a clean (zero-warning) path.
    assert warnings_empty == [], (
        f"W1030-followup-A: empty_file must short-circuit before the "
        f"'no `budgets:` key' warning fires, got: {warnings_empty!r}"
    )

    # Missing file path: ``[]`` returned untouched (helper short-circuits).
    missing_result = _load_budgets(tmp_path / "absent.yaml", warnings_out=[])
    assert missing_result == []

    # None path: pre-existing short-circuit (callsite-level), still ``[]``.
    none_result = _load_budgets(None, warnings_out=[])
    assert none_result == []
