"""W738 (W692 Phase C-1c) — wire-format byte-equivalence guards.

Two guard goals here:

1. **cmd_triage JSON envelope wire format must not drift.** cmd_triage.py
   serialises raw ``load_suppressions()`` dicts directly into the envelope
   (lines 74 / 219 / 293). W738 BAILED on migrating those three sites to
   ``load_suppressions_typed()`` because three malformed-input edge cases
   silently change the wire bytes (backslash paths get normalised,
   unparseable dates get dropped, invalid statuses get dropped). This guard
   pins the legacy behavior so any future migration attempt must add a
   compatibility shim instead of breaking the bytes.

2. **save_suppression() round-trips correctly.** suppression.py:272 DID
   migrate to ``load_suppressions_typed()`` for the in-memory dedup check.
   The on-disk YAML serialiser still consumes dicts via ``to_dict()``
   projection. This guard exercises the round-trip on a well-formed file
   and on the dedup-no-op path.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from roam.commands.suppression import (
    load_suppressions,
    load_suppressions_typed,
    save_suppression,
)
from roam.output.formatter import json_envelope, to_json


# ---------------------------------------------------------------------------
# Guard 1: cmd_triage wire-format invariants (legacy dict path stays stable)
# ---------------------------------------------------------------------------


def _envelope_for(suppressions: list[dict]) -> str:
    return to_json(
        json_envelope(
            "triage-list",
            summary={"verdict": f"{len(suppressions)} suppression(s)", "total": len(suppressions)},
            budget=0,
            suppressions=suppressions,
        )
    )


def test_triage_list_envelope_byte_stable_well_formed(tmp_path: Path) -> None:
    """A well-formed .roam-suppressions.yml yields byte-identical envelopes
    via the legacy load_suppressions() path AND the typed projection — this
    is the case W738 migration would have been safe for.
    """
    (tmp_path / ".roam-suppressions.yml").write_text(
        "# Test fixture\n"
        "suppressions:\n"
        "  - rule: secret-detection\n"
        "    file: tests/fake.py\n"
        "    reason: fake credentials\n"
        "    status: safe\n"
        "    author: dev@example.com\n"
        "    date: 2026-02-25\n"
        "  - rule: complexity-high\n"
        "    file: src/x.py\n"
        "    line: 142\n"
        "    reason: complex pipeline\n"
        "    status: acknowledged\n",
        encoding="utf-8",
    )
    legacy = load_suppressions(tmp_path)
    typed_projected = [s.to_dict() for s in load_suppressions_typed(tmp_path)]
    assert _envelope_for(legacy) == _envelope_for(typed_projected)


def test_triage_list_envelope_diverges_on_backslash_path(tmp_path: Path) -> None:
    """BAIL guard: backslash paths in the YAML are preserved verbatim by the
    legacy parser but normalised to forward slashes by RuleFileSuppression.
    Migrating cmd_triage to the typed loader WOULD change the wire bytes for
    any pre-existing file containing a Windows-style path. Pinned.
    """
    (tmp_path / ".roam-suppressions.yml").write_text(
        "suppressions:\n"
        "  - rule: r\n"
        "    file: src\\x.py\n"
        "    status: safe\n"
        "    reason: ok\n",
        encoding="utf-8",
    )
    legacy = load_suppressions(tmp_path)
    typed_projected = [s.to_dict() for s in load_suppressions_typed(tmp_path)]
    # Confirm the divergence exists (so we know the BAIL was warranted)
    assert legacy != typed_projected
    # And confirm the legacy path keeps the raw backslash on the wire
    assert legacy[0]["file"] == "src\\x.py"
    assert typed_projected[0]["file"] == "src/x.py"


def test_triage_list_envelope_diverges_on_malformed_date(tmp_path: Path) -> None:
    """BAIL guard: malformed date strings (e.g. ``2026/05/14``) are carried
    through verbatim by the legacy parser but dropped by the typed loader
    (``_coerce_date`` returns None). Pinned.
    """
    (tmp_path / ".roam-suppressions.yml").write_text(
        "suppressions:\n"
        "  - rule: r\n"
        "    file: f.py\n"
        "    date: 2026/05/14\n"
        "    reason: ok\n"
        "    status: safe\n",
        encoding="utf-8",
    )
    legacy = load_suppressions(tmp_path)
    typed_projected = [s.to_dict() for s in load_suppressions_typed(tmp_path)]
    assert legacy != typed_projected
    assert legacy[0].get("date") == "2026/05/14"
    assert "date" not in typed_projected[0]


def test_triage_list_envelope_diverges_on_invalid_status(tmp_path: Path) -> None:
    """BAIL guard: status values outside VALID_STATUSES are carried verbatim
    by the legacy parser but dropped by the typed loader. Pinned.
    """
    (tmp_path / ".roam-suppressions.yml").write_text(
        "suppressions:\n"
        "  - rule: r\n"
        "    file: f.py\n"
        "    status: notvalid\n"
        "    reason: ok\n",
        encoding="utf-8",
    )
    legacy = load_suppressions(tmp_path)
    typed_projected = [s.to_dict() for s in load_suppressions_typed(tmp_path)]
    assert legacy != typed_projected
    assert legacy[0].get("status") == "notvalid"
    assert "status" not in typed_projected[0]


# ---------------------------------------------------------------------------
# Guard 2: save_suppression round-trip (typed dedup + dict serialise)
# ---------------------------------------------------------------------------


def test_save_suppression_appends_new_entry(tmp_path: Path) -> None:
    save_suppression(
        tmp_path,
        rule="hardcoded-secret",
        file="main.py",
        reason="test data",
        status="safe",
    )
    rows = load_suppressions(tmp_path)
    assert len(rows) == 1
    assert rows[0]["rule"] == "hardcoded-secret"
    assert rows[0]["file"] == "main.py"
    assert rows[0]["status"] == "safe"
    assert rows[0]["reason"] == "test data"
    assert "date" in rows[0]


def test_save_suppression_dedup_skips_existing(tmp_path: Path) -> None:
    """The typed dedup check must recognise an already-present (rule, file, line)
    triple and return without rewriting the file.
    """
    save_suppression(tmp_path, rule="r1", file="f.py", reason="r", status="safe", line=10)
    first_bytes = (tmp_path / ".roam-suppressions.yml").read_bytes()
    # Same triple — should be a no-op
    save_suppression(tmp_path, rule="r1", file="f.py", reason="different", status="acknowledged", line=10)
    second_bytes = (tmp_path / ".roam-suppressions.yml").read_bytes()
    assert first_bytes == second_bytes


def test_save_suppression_normalises_backslash_paths(tmp_path: Path) -> None:
    """Both legacy and typed paths normalise the new entry's file field —
    this is on the write side, where normalisation has always happened.
    """
    save_suppression(tmp_path, rule="r1", file="src\\x.py", reason="r", status="safe")
    rows = load_suppressions(tmp_path)
    assert rows[0]["file"] == "src/x.py"


def test_save_suppression_dedup_after_typed_normalises_path(tmp_path: Path) -> None:
    """If a pre-existing entry has a backslash path, the typed loader
    normalises it before dedup. The second save with a forward-slash equivalent
    must be detected as a duplicate.
    """
    # Plant a row with a backslash path the parser carries through.
    (tmp_path / ".roam-suppressions.yml").write_text(
        "suppressions:\n"
        "  - rule: r1\n"
        "    file: src\\x.py\n"
        "    status: safe\n"
        "    reason: r\n",
        encoding="utf-8",
    )
    # The new save uses the forward-slash form — typed dedup must catch it.
    save_suppression(tmp_path, rule="r1", file="src/x.py", reason="newer", status="acknowledged")
    rows = load_suppressions(tmp_path)
    # Either one row (dedup hit) — exactly the desired behavior.
    assert len(rows) == 1
