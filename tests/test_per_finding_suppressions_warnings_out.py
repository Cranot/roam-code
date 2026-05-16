"""W1009 — Pattern 2 (silent fallback) tests for `_load_per_finding_suppressions`.

Direct mirror of ``test_ignore_findings_warnings_out.py`` (W706) applied
to the sibling loader for ``.roam/suppressions.json``. Drives the
``warnings_out`` accumulator plumbed in W1009 through both the loader
helper and ``annotate_with_suppression``. When no accumulator is
supplied, behaviour is byte-identical to pre-W1009 (silent empty
dict).

Cross-links:
- W706 — sibling loader (``_load_ignore_findings_file``) shipped first.
- W918 / W989 — canonical ``warnings_out`` plumb-through pattern.
- ``(internal memo)`` — the playbook.
- CLAUDE.md "Six systemic anti-patterns" / Pattern 2 "Silent fallback".
"""

from __future__ import annotations

import json as _json
from pathlib import Path

from roam.commands.finding_suppress import (
    _load_per_finding_suppressions,
    annotate_with_suppression,
)

# ---------------------------------------------------------------------------
# _load_per_finding_suppressions — direct loader behaviour
# ---------------------------------------------------------------------------


def test_load_missing_file_no_warning(tmp_path: Path) -> None:
    """Absent file is the default state — never warn (would spam every run)."""
    warnings_out: list[str] = []
    suppressions = _load_per_finding_suppressions(tmp_path / "missing.json", warnings_out=warnings_out)
    assert suppressions == {}
    assert warnings_out == []


def test_load_valid_json_no_warning(tmp_path: Path) -> None:
    """Happy path: well-formed file, no warnings emitted, all entries returned."""
    p = tmp_path / "suppressions.json"
    payload = {
        "abc123def4567890": {
            "reason": "verified false positive",
            "added_at": "2026-05-15T12:00:00Z",
        },
        "deadbeef00000000": {
            "reason": "documented exception",
        },
    }
    p.write_text(_json.dumps(payload), encoding="utf-8")
    warnings_out: list[str] = []
    suppressions = _load_per_finding_suppressions(p, warnings_out=warnings_out)
    assert warnings_out == []
    assert len(suppressions) == 2
    assert suppressions["abc123def4567890"]["reason"] == "verified false positive"
    assert suppressions["deadbeef00000000"]["reason"] == "documented exception"


def test_load_malformed_json_warns(tmp_path: Path) -> None:
    """File contains invalid JSON — caller must see the parse error."""
    p = tmp_path / "suppressions.json"
    p.write_text("{not valid json,,,\n", encoding="utf-8")
    warnings_out: list[str] = []
    suppressions = _load_per_finding_suppressions(p, warnings_out=warnings_out)
    assert suppressions == {}
    assert len(warnings_out) == 1
    msg = warnings_out[0]
    assert "suppressions.json" in msg
    assert "malformed JSON" in msg


def test_load_non_dict_root_warns(tmp_path: Path) -> None:
    """Root is a list/scalar/string — surface the shape problem."""
    p = tmp_path / "suppressions.json"
    p.write_text(_json.dumps(["not", "a", "mapping"]), encoding="utf-8")
    warnings_out: list[str] = []
    suppressions = _load_per_finding_suppressions(p, warnings_out=warnings_out)
    assert suppressions == {}
    assert len(warnings_out) == 1
    msg = warnings_out[0]
    assert "suppressions.json" in msg
    assert "expected a mapping" in msg


def test_load_non_dict_entry_warns_and_skips(tmp_path: Path) -> None:
    """An entry value that's a list/scalar surfaces the finding_id + type."""
    p = tmp_path / "suppressions.json"
    p.write_text(
        _json.dumps(
            {
                "abc123def4567890": "just-a-string",
                "deadbeef00000000": {"reason": "ok"},
            }
        ),
        encoding="utf-8",
    )
    warnings_out: list[str] = []
    suppressions = _load_per_finding_suppressions(p, warnings_out=warnings_out)
    # Only the well-formed entry survives.
    assert len(suppressions) == 1
    assert "deadbeef00000000" in suppressions
    assert len(warnings_out) == 1
    msg = warnings_out[0]
    assert "abc123def4567890" in msg
    assert "str" in msg  # type-name of the rejected value


def test_load_warnings_out_none_is_byte_identical_silent(tmp_path: Path) -> None:
    """When the caller doesn't pass an accumulator, behaviour is silent (pre-W1009)."""
    p = tmp_path / "suppressions.json"
    p.write_text("{not valid,,,\n", encoding="utf-8")
    # Should not raise, should not print, should return {}
    assert _load_per_finding_suppressions(p) == {}

    # Same byte-identical silence for non-dict root.
    p.write_text(_json.dumps(["x", "y"]), encoding="utf-8")
    assert _load_per_finding_suppressions(p) == {}

    # And for non-dict entries — well-formed survivors still returned.
    p.write_text(_json.dumps({"a": "bad", "b": {"reason": "ok"}}), encoding="utf-8")
    assert _load_per_finding_suppressions(p) == {"b": {"reason": "ok"}}


def test_load_empty_dict_no_warning(tmp_path: Path) -> None:
    """An empty `{}` is well-formed — no warning, empty dict returned."""
    p = tmp_path / "suppressions.json"
    p.write_text("{}", encoding="utf-8")
    warnings_out: list[str] = []
    suppressions = _load_per_finding_suppressions(p, warnings_out=warnings_out)
    assert suppressions == {}
    assert warnings_out == []


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


def test_annotate_with_suppression_surfaces_per_finding_loader_warnings(
    tmp_path: Path,
) -> None:
    """The plumb-through: malformed `.roam/suppressions.json` -> warning at the caller."""
    (tmp_path / ".roam").mkdir()
    (tmp_path / ".roam" / "suppressions.json").write_text("{not valid json,,,\n", encoding="utf-8")
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
    assert any("suppressions.json" in w for w in warnings_out)


def test_annotate_with_suppression_per_finding_happy_path_no_warnings(
    tmp_path: Path,
) -> None:
    """Well-formed suppressions file + matching finding -> no warning, suppression applied."""
    # Compute the finding_id the loader expects so the match succeeds.
    from roam.commands.finding_suppress import finding_id

    fid = finding_id("io-in-loop", "src/foo.py:10", "fn")
    (tmp_path / ".roam").mkdir()
    (tmp_path / ".roam" / "suppressions.json").write_text(_json.dumps({fid: {"reason": "verified"}}), encoding="utf-8")
    warnings_out: list[str] = []
    out, count = annotate_with_suppression(
        [_make_finding("io-in-loop", "src/foo.py:10")],
        command="math",
        project_root=tmp_path,
        warnings_out=warnings_out,
    )
    assert count == 1
    assert warnings_out == []
    assert out[0]["suppressed"]["source"] == "suppressions.json"
    assert out[0]["suppressed"]["reason"] == "verified"


def test_annotate_with_suppression_both_loaders_warn_together(tmp_path: Path) -> None:
    """Both files malformed -> both warnings drain into the same accumulator."""
    (tmp_path / ".roamignore-findings").write_text("- not a mapping\n", encoding="utf-8")
    (tmp_path / ".roam").mkdir()
    (tmp_path / ".roam" / "suppressions.json").write_text("{not valid,,,\n", encoding="utf-8")
    warnings_out: list[str] = []
    out, count = annotate_with_suppression(
        [_make_finding("io-in-loop", "src/foo.py:10")],
        command="math",
        project_root=tmp_path,
        warnings_out=warnings_out,
    )
    assert count == 0
    assert out[0].get("suppressed") is None
    # One warning from each loader.
    assert any(".roamignore-findings" in w for w in warnings_out)
    assert any("suppressions.json" in w for w in warnings_out)


# ---------------------------------------------------------------------------
# load_per_finding_suppressions_typed — W1017 warnings_out plumb-through
# ---------------------------------------------------------------------------


def test_typed_wrapper_warnings_out_none_is_byte_identical_silent(
    tmp_path: Path,
) -> None:
    """W1017: typed wrapper without warnings_out stays silent (pre-W1017 behaviour).

    Mirrors ``test_load_warnings_out_none_is_byte_identical_silent`` for the
    typed surface — well-formed survivors come through, malformed input
    silently collapses to an empty list, nothing prints/raises.
    """
    from roam.commands.finding_suppress import load_per_finding_suppressions_typed

    p = tmp_path / "suppressions.json"
    # Malformed JSON -> silent empty list.
    p.write_text("{not valid json,,,\n", encoding="utf-8")
    assert load_per_finding_suppressions_typed(p) == []

    # Non-dict root -> silent empty list.
    p.write_text(_json.dumps(["x", "y"]), encoding="utf-8")
    assert load_per_finding_suppressions_typed(p) == []

    # Mixed-good-and-bad entries -> well-formed survivor returned silently.
    p.write_text(
        _json.dumps({"abc123def4567890": "bad", "deadbeef00000000": {"reason": "ok"}}),
        encoding="utf-8",
    )
    typed = load_per_finding_suppressions_typed(p)
    assert len(typed) == 1
    assert typed[0].finding_id == "deadbeef00000000"


def test_typed_wrapper_warnings_out_collects_on_malformed_input(
    tmp_path: Path,
) -> None:
    """W1017: typed wrapper threads warnings_out to the dict-keyed loader.

    Every silent-fallback path that the dict-keyed loader surfaces (W1009)
    must reach a typed-surface caller through the same accumulator —
    otherwise future migrators to the typed API lose the warnings the
    legacy API gained in W1009.
    """
    from roam.commands.finding_suppress import load_per_finding_suppressions_typed

    p = tmp_path / "suppressions.json"

    # Malformed JSON -> warning emitted, empty list returned.
    p.write_text("{not valid json,,,\n", encoding="utf-8")
    warnings_out: list[str] = []
    typed = load_per_finding_suppressions_typed(p, warnings_out=warnings_out)
    assert typed == []
    assert len(warnings_out) == 1
    assert "suppressions.json" in warnings_out[0]
    assert "malformed JSON" in warnings_out[0]

    # Non-dict entry -> warning emitted, well-formed survivor returned.
    p.write_text(
        _json.dumps({"abc123def4567890": "bad", "deadbeef00000000": {"reason": "ok"}}),
        encoding="utf-8",
    )
    warnings_out = []
    typed = load_per_finding_suppressions_typed(p, warnings_out=warnings_out)
    assert len(typed) == 1
    assert typed[0].finding_id == "deadbeef00000000"
    assert len(warnings_out) == 1
    assert "abc123def4567890" in warnings_out[0]
    assert "str" in warnings_out[0]
