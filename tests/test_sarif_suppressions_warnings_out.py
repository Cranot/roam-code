"""W1042: SARIF suppressions loaders surface silent-fallback paths via warnings_out.

Mirrors the W1032 (suppression.load_suppressions / load_suppressions_typed)
and W1017 (finding_suppress.load_per_finding_suppressions_typed) shapes.

The two SARIF-side loaders are:

* ``roam.output.sarif._load_suppressions``  — dict-shaped (legacy).
* ``roam.output.sarif._load_suppressions_typed`` — typed counterpart that
  projects onto :class:`FindingIdSuppression` (canonical W691 shape only).

Both accept a ``warnings_out: list[str] | None`` kwarg. Pre-W1042 callers
that don't supply the accumulator retain byte-identical silent-empty-list
behaviour so the SARIF output hash stays stable on the happy path.
"""

from __future__ import annotations

import json

from roam.output.sarif import _load_suppressions, _load_suppressions_typed


def test_missing_file_emits_no_warning(tmp_path, monkeypatch) -> None:
    """Absent ``.roam/suppressions.json`` is the default state — no warning."""
    monkeypatch.chdir(tmp_path)
    warnings: list[str] = []
    rows = _load_suppressions(warnings_out=warnings)
    assert rows == []
    assert warnings == []

    typed = _load_suppressions_typed(warnings_out=warnings)
    assert typed == []
    assert warnings == []


def test_valid_canonical_dict_emits_no_warning(tmp_path, monkeypatch) -> None:
    """Well-formed canonical dict shape — no warning, rows project cleanly."""
    monkeypatch.chdir(tmp_path)
    suppressions_dir = tmp_path / ".roam"
    suppressions_dir.mkdir()
    (suppressions_dir / "suppressions.json").write_text(
        json.dumps(
            {
                "abc123def456abcd": {
                    "rule_id": "ROAM-DEMO-1",
                    "location": "src/x.py:10",
                    "reason": "intentional",
                    "added_at": "2026-05-15T00:00:00Z",
                    "source": "manual",
                }
            }
        ),
        encoding="utf-8",
    )

    warnings: list[str] = []
    rows = _load_suppressions(warnings_out=warnings)
    assert len(rows) == 1
    assert rows[0]["rule_id"] == "ROAM-DEMO-1"
    assert rows[0]["location"] == "src/x.py:10"
    assert warnings == []

    warnings.clear()
    typed = _load_suppressions_typed(warnings_out=warnings)
    assert len(typed) == 1
    assert typed[0].rule_id == "ROAM-DEMO-1"
    assert warnings == []


def test_malformed_json_emits_warning(tmp_path, monkeypatch) -> None:
    """Malformed JSON file surfaces an actionable warning naming the path + format."""
    monkeypatch.chdir(tmp_path)
    suppressions_dir = tmp_path / ".roam"
    suppressions_dir.mkdir()
    (suppressions_dir / "suppressions.json").write_text("{not valid json: [", encoding="utf-8")

    warnings: list[str] = []
    rows = _load_suppressions(warnings_out=warnings)
    assert rows == []
    assert len(warnings) == 1
    msg = warnings[0]
    assert "sarif-suppressions" in msg
    assert "suppressions.json" in msg
    assert "malformed JSON" in msg

    warnings.clear()
    typed = _load_suppressions_typed(warnings_out=warnings)
    assert typed == []
    assert len(warnings) == 1
    assert "malformed JSON" in warnings[0]


def test_malformed_root_type_emits_warning(tmp_path, monkeypatch) -> None:
    """Top-level scalar (string/number) — neither dict nor list — warns."""
    monkeypatch.chdir(tmp_path)
    suppressions_dir = tmp_path / ".roam"
    suppressions_dir.mkdir()
    (suppressions_dir / "suppressions.json").write_text(json.dumps("a bare string"), encoding="utf-8")

    warnings: list[str] = []
    rows = _load_suppressions(warnings_out=warnings)
    assert rows == []
    assert len(warnings) == 1
    assert "expected a mapping or a list" in warnings[0]


def test_malformed_entry_dropped_with_warning(tmp_path, monkeypatch) -> None:
    """Legacy list shape with non-dict entries — drop the entry + warn."""
    monkeypatch.chdir(tmp_path)
    suppressions_dir = tmp_path / ".roam"
    suppressions_dir.mkdir()
    # Legacy list shape: one good dict, one bogus string entry.
    (suppressions_dir / "suppressions.json").write_text(
        json.dumps(
            [
                {"rule_id": "ROAM-DEMO-1", "location": "src/x.py:10"},
                "not a dict",
            ]
        ),
        encoding="utf-8",
    )

    warnings: list[str] = []
    rows = _load_suppressions(warnings_out=warnings)
    assert len(rows) == 1
    assert rows[0]["rule_id"] == "ROAM-DEMO-1"
    assert len(warnings) == 1
    msg = warnings[0]
    assert "entry #2" in msg
    assert "str" in msg  # type.__name__ for "not a dict"
    assert "Skipping entry." in msg


def test_typed_warns_on_legacy_list_shape(tmp_path, monkeypatch) -> None:
    """Typed surface can't project legacy list shape — warns + returns empty."""
    monkeypatch.chdir(tmp_path)
    suppressions_dir = tmp_path / ".roam"
    suppressions_dir.mkdir()
    (suppressions_dir / "suppressions.json").write_text(
        json.dumps([{"rule_id": "X", "location": "src/x.py:1"}]),
        encoding="utf-8",
    )

    warnings: list[str] = []
    typed = _load_suppressions_typed(warnings_out=warnings)
    assert typed == []
    assert len(warnings) == 1
    assert "legacy top-level list" in warnings[0]
    assert "FindingIdSuppression" in warnings[0]


def test_typed_warns_on_legacy_envelope_shape(tmp_path, monkeypatch) -> None:
    """Typed surface can't project legacy envelope shape — warns + returns empty."""
    monkeypatch.chdir(tmp_path)
    suppressions_dir = tmp_path / ".roam"
    suppressions_dir.mkdir()
    (suppressions_dir / "suppressions.json").write_text(
        json.dumps({"suppressions": [{"rule_id": "X", "location": "src/x.py:1"}]}),
        encoding="utf-8",
    )

    warnings: list[str] = []
    typed = _load_suppressions_typed(warnings_out=warnings)
    assert typed == []
    assert len(warnings) == 1
    assert "envelope shape" in warnings[0]


def test_pre_w1042_caller_byte_identical_behaviour(tmp_path, monkeypatch) -> None:
    """Callers that don't supply warnings_out get the pre-W1042 silent-empty path.

    This is the byte-identical SARIF-output invariant: existing `to_sarif()`
    consumers never opted into warnings, so the SARIF document bytes must
    stay stable on malformed input.
    """
    monkeypatch.chdir(tmp_path)
    suppressions_dir = tmp_path / ".roam"
    suppressions_dir.mkdir()
    (suppressions_dir / "suppressions.json").write_text("{not valid json: [", encoding="utf-8")

    # No warnings_out kwarg — must return [] silently.
    assert _load_suppressions() == []
    assert _load_suppressions_typed() == []
