"""W706 — Pattern 2 (silent fallback) tests for `_load_ignore_findings_file`.

Drives the `warnings_out` accumulator plumbed in W706 through both the
loader helper and ``annotate_with_suppression``. Mirrors the W918 /
W989 disciplines: every silent-fallback path surfaces a structured
warning when an accumulator is supplied; when no accumulator is
supplied, behaviour is byte-identical to pre-W706 (silent empty
list).

Cross-links:
- W918 / W989 — canonical ``warnings_out`` plumb-through pattern.
- ``(internal memo)`` — the playbook.
- CLAUDE.md "Six systemic anti-patterns" / Pattern 2 "Silent fallback".
"""

from __future__ import annotations

import json as _json
from pathlib import Path

import pytest

from roam.commands.finding_suppress import (
    _load_ignore_findings_file,
    annotate_with_suppression,
)


# ---------------------------------------------------------------------------
# _load_ignore_findings_file — direct loader behaviour
# ---------------------------------------------------------------------------


def test_load_missing_file_no_warning(tmp_path: Path) -> None:
    """Absent file is the default state — never warn (would spam every run)."""
    warnings_out: list[str] = []
    rules = _load_ignore_findings_file(tmp_path / "missing.yml", warnings_out=warnings_out)
    assert rules == []
    assert warnings_out == []


def test_load_valid_yaml_no_warning(tmp_path: Path) -> None:
    """Happy path: well-formed file, no warnings emitted, all rules returned."""
    body = (
        "rules:\n"
        "  - task_id: io-in-loop\n"
        '    path_glob: "src/composables/**/*.ts"\n'
        '    reason: "cache reads"\n'
        "  - task_id: branching-recursion\n"
        '    path_glob: "src/utils/object-diff.ts"\n'
    )
    p = tmp_path / ".roamignore-findings"
    p.write_text(body, encoding="utf-8")
    warnings_out: list[str] = []
    rules = _load_ignore_findings_file(p, warnings_out=warnings_out)
    assert warnings_out == []
    assert len(rules) == 2
    assert rules[0]["task_id"] == "io-in-loop"
    assert rules[1]["task_id"] == "branching-recursion"


def test_load_malformed_yaml_root_warns(tmp_path: Path) -> None:
    """Root is a scalar / list / string — caller must see the shape problem."""
    p = tmp_path / ".roamignore-findings"
    # JSON-encodes as a list at the root — valid YAML, wrong shape.
    p.write_text("- just\n- a\n- list\n", encoding="utf-8")
    warnings_out: list[str] = []
    rules = _load_ignore_findings_file(p, warnings_out=warnings_out)
    assert rules == []
    assert len(warnings_out) == 1
    msg = warnings_out[0]
    assert ".roamignore-findings" in msg
    assert "expected a mapping" in msg or "expected a list" in msg


def test_load_missing_rules_key_warns(tmp_path: Path) -> None:
    """Top-level dict without `rules:` — surface the missing key, not empty silence."""
    p = tmp_path / ".roamignore-findings"
    p.write_text('version: "1"\nnotes: "no rules here"\n', encoding="utf-8")
    warnings_out: list[str] = []
    rules = _load_ignore_findings_file(p, warnings_out=warnings_out)
    assert rules == []
    assert len(warnings_out) == 1
    assert "no `rules:` key" in warnings_out[0]


def test_load_rules_not_a_list_warns(tmp_path: Path) -> None:
    """`rules:` set to a scalar or mapping — surface the type mismatch."""
    p = tmp_path / ".roamignore-findings"
    p.write_text("rules: not-a-list\n", encoding="utf-8")
    warnings_out: list[str] = []
    rules = _load_ignore_findings_file(p, warnings_out=warnings_out)
    assert rules == []
    assert len(warnings_out) == 1
    assert "expected a list" in warnings_out[0]


def test_load_missing_required_field_warns_and_skips(tmp_path: Path) -> None:
    """Entry without `task_id` AND without `path_glob` would match every finding silently.

    Surface the over-broad entry as a structured warning AND skip it so
    suppression of unrelated findings can't happen by accident.
    """
    p = tmp_path / ".roamignore-findings"
    p.write_text(
        "rules:\n"
        '  - reason: "no task_id, no path_glob — matches everything"\n'
        "  - task_id: io-in-loop\n"
        '    path_glob: "src/*.py"\n',
        encoding="utf-8",
    )
    warnings_out: list[str] = []
    rules = _load_ignore_findings_file(p, warnings_out=warnings_out)
    # The over-broad entry is dropped; only the well-formed one survives.
    assert len(rules) == 1
    assert rules[0]["task_id"] == "io-in-loop"
    assert len(warnings_out) == 1
    assert "neither" in warnings_out[0]
    assert "task_id" in warnings_out[0]


def test_load_non_dict_entry_warns_and_skips(tmp_path: Path) -> None:
    """An entry that's a list or scalar (typo) surfaces the index + type."""
    p = tmp_path / ".roamignore-findings"
    p.write_text(
        "rules:\n"
        "  - just-a-string\n"
        "  - task_id: io-in-loop\n"
        '    path_glob: "src/*.py"\n',
        encoding="utf-8",
    )
    warnings_out: list[str] = []
    rules = _load_ignore_findings_file(p, warnings_out=warnings_out)
    assert len(rules) == 1
    assert rules[0]["task_id"] == "io-in-loop"
    assert len(warnings_out) == 1
    assert "rules[0]" in warnings_out[0]


def test_load_warnings_out_none_is_byte_identical_silent(tmp_path: Path) -> None:
    """When the caller doesn't pass an accumulator, behaviour is silent (pre-W706)."""
    p = tmp_path / ".roamignore-findings"
    p.write_text("- not a mapping\n", encoding="utf-8")
    # Should not raise, should not print, should return []
    rules = _load_ignore_findings_file(p)
    assert rules == []


def test_load_no_pyyaml_json_path_works(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """If PyYAML import fails, JSON-shaped file still loads cleanly with no warnings."""
    import builtins

    real_import = builtins.__import__

    def _fake_import(name, *args, **kwargs):
        if name == "yaml":
            raise ImportError("simulated PyYAML absence")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", _fake_import)
    p = tmp_path / ".roamignore-findings"
    p.write_text(
        _json.dumps({
            "rules": [
                {"task_id": "io-in-loop", "path_glob": "src/*.py"},
            ]
        }),
        encoding="utf-8",
    )
    warnings_out: list[str] = []
    rules = _load_ignore_findings_file(p, warnings_out=warnings_out)
    assert warnings_out == []
    assert len(rules) == 1
    assert rules[0]["task_id"] == "io-in-loop"


# ---------------------------------------------------------------------------
# annotate_with_suppression — warnings_out plumbs through to caller
# ---------------------------------------------------------------------------


def _make_finding(task_id: str, location: str, name: str = "fn") -> dict:
    return {
        "task_id": task_id,
        "location": location,
        "symbol_name": name,
        "confidence": "high",
    }


def test_annotate_with_suppression_surfaces_loader_warnings(tmp_path: Path) -> None:
    """The plumb-through: malformed `.roamignore-findings` -> warning at the caller."""
    (tmp_path / ".roamignore-findings").write_text("- not a mapping\n", encoding="utf-8")
    warnings_out: list[str] = []
    findings = [_make_finding("io-in-loop", "src/foo.py:10")]
    out, count = annotate_with_suppression(
        findings,
        command="math",
        project_root=tmp_path,
        warnings_out=warnings_out,
    )
    assert count == 0
    assert out[0].get("suppressed") is None
    assert len(warnings_out) >= 1
    assert any(".roamignore-findings" in w for w in warnings_out)


def test_annotate_with_suppression_happy_path_no_warnings(tmp_path: Path) -> None:
    """Well-formed file + happy match -> empty accumulator, suppression applied."""
    (tmp_path / ".roamignore-findings").write_text(
        "rules:\n"
        "  - task_id: io-in-loop\n"
        '    path_glob: "src/foo.py"\n'
        '    reason: "verified"\n',
        encoding="utf-8",
    )
    warnings_out: list[str] = []
    out, count = annotate_with_suppression(
        [_make_finding("io-in-loop", "src/foo.py:10")],
        command="math",
        project_root=tmp_path,
        warnings_out=warnings_out,
    )
    assert count == 1
    assert warnings_out == []
    assert out[0]["suppressed"]["source"] == ".roamignore-findings"
