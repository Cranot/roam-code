"""Regression: god-component classification must be symbol-KIND aware.

Dogfood FP (2026-05-20): `EvidenceArtifact.path` (kind=`prop`, degree=2408)
and `WebhookBridge.path` (kind=`prop`, degree=323) were classified
`actionable` + CRITICAL by `god_components()` — but a data attribute with high
reference fan-in is NOT a refactorable god *component*: there is no logic to
decompose. The classification loop previously consulted only the file path
(`_is_utility_path`), never the symbol kind.

These tests exercise the classification loop directly via a fake connection
that returns crafted `TOP_BY_DEGREE` rows, so the regression is pinned on the
classification logic itself rather than on the indexer organically producing a
high-degree `prop` symbol.
"""

from __future__ import annotations

from roam.quality.god_components import _DATA_ATTRIBUTE_KINDS, god_components


class _FakeCursor:
    def __init__(self, rows):
        self._rows = rows

    def fetchall(self):
        return self._rows


class _FakeConn:
    """Minimal stand-in: only ``execute(...).fetchall()`` is exercised."""

    def __init__(self, rows):
        self._rows = rows

    def execute(self, _sql, _params=None):
        return _FakeCursor(self._rows)


def _row(name, kind, in_deg, out_deg, file_path):
    # sqlite3.Row supports __getitem__ by column name; a dict matches that.
    return {
        "name": name,
        "kind": kind,
        "in_degree": in_deg,
        "out_degree": out_deg,
        "file_path": file_path,
    }


def test_dataclass_field_not_actionable_critical():
    """A high fan-in `prop` (dataclass field) must NOT be an actionable god component."""
    rows = [
        _row("path", "prop", 2400, 8, "src/roam/evidence/artifact.py"),
        _row("WebhookBridge.path", "prop", 320, 3, "src/roam/bridges/webhook.py"),
    ]
    summary = god_components(_FakeConn(rows), include_items=True)

    assert summary.actionable == 0, "data-attribute fields must not be actionable"
    assert summary.critical == 0, "data-attribute fields must not be CRITICAL"
    for item in summary.items:
        assert item["category"] == "data_attribute"
        assert item["severity"] == "INFO"


def test_function_god_component_still_actionable_critical():
    """A genuine high-degree function (logic) must still be actionable + CRITICAL."""
    rows = [
        _row("orchestrate", "function", 200, 40, "src/roam/commands/cmd_orchestrate.py"),
    ]
    summary = god_components(_FakeConn(rows), include_items=True)

    assert summary.actionable == 1
    assert summary.critical == 1
    assert summary.items[0]["category"] == "actionable"
    assert summary.items[0]["severity"] == "CRITICAL"


def test_all_data_attribute_kinds_are_guarded():
    """Every kind in the data-attribute set lands in the non-actionable band."""
    rows = [_row(f"attr_{k}", k, 100, 5, "src/roam/model.py") for k in sorted(_DATA_ATTRIBUTE_KINDS)]
    summary = god_components(_FakeConn(rows), include_items=True)

    assert summary.actionable == 0
    assert summary.critical == 0
    assert summary.total == len(_DATA_ATTRIBUTE_KINDS)
    assert all(it["category"] == "data_attribute" for it in summary.items)
