"""W228 — false-positive feedback loop tests.

Covers the ``roam.evidence.feedback`` module:

* Decision-enum validation (closed enumeration).
* Atomic-write persistence to ``.roam/feedback/``.
* Best-effort behaviour on read-only / unwritable target dirs.
* Filter-by-finding-id-str on load.
* Skip-and-warn on malformed JSON.
* Dismissal-reason aggregation primitive (the rules-pack improvement
  data hook).
* Canonical-JSON round-trip stability.
* Free-text truncation at 500 chars.
* Path-traversal safety on ``finding_id_str`` → filename.

All tests use ``tmp_path`` — no global state, no network, no real
``.roam/`` writes.
"""

from __future__ import annotations

import json
import warnings
from pathlib import Path

import pytest

from roam.evidence.feedback import (
    DEFAULT_FEEDBACK_DIR,
    FEEDBACK_DECISIONS,
    FREE_TEXT_MAX_CHARS,
    FindingFeedback,
    aggregate_dismissal_reasons,
    load_feedback,
    persist_feedback,
)


# ---------------------------------------------------------------------------
# Construction-time validation
# ---------------------------------------------------------------------------


def test_feedback_validates_decision() -> None:
    """Bad decision raises ValueError; closed enumeration is enforced."""
    # Happy path on every documented decision.
    for decision in FEEDBACK_DECISIONS:
        fb = FindingFeedback(
            finding_id_str="clones:sym:abc",
            decision=decision,
            rationale="generic-tag",
            reviewer="human:alice@example.com",
            timestamp="2026-05-14T12:00:00Z",
        )
        assert fb.decision == decision

    # Bad decision rejected.
    with pytest.raises(ValueError, match="unknown decision"):
        FindingFeedback(
            finding_id_str="clones:sym:abc",
            decision="not_a_real_decision",
            rationale="x",
            reviewer="human:alice@example.com",
            timestamp="2026-05-14T12:00:00Z",
        )

    # Empty rationale rejected (we need SOMETHING to group by).
    with pytest.raises(ValueError, match="rationale"):
        FindingFeedback(
            finding_id_str="clones:sym:abc",
            decision="accepted_real",
            rationale="",
            reviewer="human:alice@example.com",
            timestamp="2026-05-14T12:00:00Z",
        )

    # Empty reviewer rejected (no anonymous feedback in the loop).
    with pytest.raises(ValueError, match="reviewer"):
        FindingFeedback(
            finding_id_str="clones:sym:abc",
            decision="accepted_real",
            rationale="real-bug",
            reviewer="",
            timestamp="2026-05-14T12:00:00Z",
        )

    # Empty finding_id_str rejected (no orphan feedback).
    with pytest.raises(ValueError, match="finding_id_str"):
        FindingFeedback(
            finding_id_str="",
            decision="accepted_real",
            rationale="real-bug",
            reviewer="human:alice@example.com",
            timestamp="2026-05-14T12:00:00Z",
        )

    # Unparseable timestamp rejected.
    with pytest.raises(ValueError, match="ISO-8601"):
        FindingFeedback(
            finding_id_str="clones:sym:abc",
            decision="accepted_real",
            rationale="real-bug",
            reviewer="human:alice@example.com",
            timestamp="not-a-timestamp",
        )


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------


def test_persist_feedback_atomic_write(tmp_path: Path) -> None:
    """File appears in the target dir and contains the canonical payload."""
    fb = FindingFeedback(
        finding_id_str="clones:sym:abc123",
        decision="dismissed_false_positive",
        rationale="visitor-pattern",
        reviewer="human:alice@example.com",
        timestamp="2026-05-14T12:00:00Z",
        free_text="The visitor classes legitimately share a method shape.",
    )

    feedback_dir = tmp_path / "feedback"
    path_str = persist_feedback(fb, feedback_dir=feedback_dir)

    # Persist returned a real path (not the empty-string sentinel).
    assert path_str
    written = Path(path_str)
    assert written.exists()
    assert written.parent.resolve() == feedback_dir.resolve()
    assert written.suffix == ".json"

    payload = json.loads(written.read_text(encoding="utf-8"))
    assert payload["finding_id_str"] == "clones:sym:abc123"
    assert payload["decision"] == "dismissed_false_positive"
    assert payload["rationale"] == "visitor-pattern"
    assert payload["reviewer"] == "human:alice@example.com"
    assert payload["timestamp"] == "2026-05-14T12:00:00Z"
    assert "visitor classes legitimately" in payload["free_text"]


def test_persist_feedback_does_not_break_on_readonly_dir(tmp_path: Path) -> None:
    """Best-effort write: failures are swallowed, caller is not crashed.

    The receipt discipline (W196): improvement data should NEVER
    obstruct the review workflow.
    """
    fb = FindingFeedback(
        finding_id_str="clones:sym:def456",
        decision="accepted_real",
        rationale="real-bug",
        reviewer="human:bob@example.com",
        timestamp="2026-05-14T13:00:00Z",
    )

    # Strategy: point at a path where the *parent of the parent* is a
    # regular file. ``Path.mkdir(parents=True)`` will raise NotADirectoryError
    # — atomic_write_text catches all OSError subclasses.
    blocker = tmp_path / "blocker_file"
    blocker.write_text("not a directory")
    poisoned_target = blocker / "feedback"  # parent is a file → unwritable

    # Must NOT raise.
    result = persist_feedback(fb, feedback_dir=poisoned_target)

    # Empty-string sentinel signals "not persisted".
    assert result == ""


def test_persist_feedback_handles_permission_error_on_windows(tmp_path: Path, monkeypatch) -> None:
    """Best-effort: a PermissionError from atomic_write_json is swallowed."""
    fb = FindingFeedback(
        finding_id_str="clones:sym:ghi789",
        decision="deferred",
        rationale="follow-up",
        reviewer="human:carol@example.com",
        timestamp="2026-05-14T14:00:00Z",
    )

    def _raise_permission(*_a, **_kw):
        raise PermissionError("simulated readonly volume")

    monkeypatch.setattr(
        "roam.evidence.feedback.atomic_write_json", _raise_permission
    )

    result = persist_feedback(fb, feedback_dir=tmp_path / "fb")
    assert result == ""


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------


def test_load_feedback_filters_by_finding_id_str(tmp_path: Path) -> None:
    """Filter argument restricts results to the matching finding."""
    fb_dir = tmp_path / "feedback"

    fb1 = FindingFeedback(
        finding_id_str="clones:sym:aaa",
        decision="dismissed_false_positive",
        rationale="visitor-pattern",
        reviewer="human:alice@example.com",
        timestamp="2026-05-14T12:00:00Z",
    )
    fb2 = FindingFeedback(
        finding_id_str="clones:sym:bbb",
        decision="accepted_real",
        rationale="real-bug",
        reviewer="human:alice@example.com",
        timestamp="2026-05-14T12:30:00Z",
    )
    fb3 = FindingFeedback(
        finding_id_str="clones:sym:aaa",
        decision="dismissed_by_design",
        rationale="intentional-copy",
        reviewer="human:bob@example.com",
        timestamp="2026-05-14T13:00:00Z",
    )

    for fb in (fb1, fb2, fb3):
        assert persist_feedback(fb, feedback_dir=fb_dir)

    # Unfiltered: 3 records.
    all_records = load_feedback(feedback_dir=fb_dir)
    assert len(all_records) == 3

    # Filtered: 2 records on the AAA finding.
    aaa_records = load_feedback("clones:sym:aaa", feedback_dir=fb_dir)
    assert len(aaa_records) == 2
    assert all(r.finding_id_str == "clones:sym:aaa" for r in aaa_records)

    # Filtered for an unknown finding: empty tuple, not crash.
    nada = load_feedback("nonexistent:sym:zzz", feedback_dir=fb_dir)
    assert nada == ()

    # Missing feedback dir entirely: empty tuple.
    missing = load_feedback(feedback_dir=tmp_path / "absent")
    assert missing == ()


def test_load_feedback_skips_malformed_json_with_warning(tmp_path: Path) -> None:
    """Malformed JSON files are skipped and a UserWarning is emitted.

    Captures the warning explicitly so we both observe it AND let the
    rest of the load proceed.
    """
    fb_dir = tmp_path / "feedback"
    fb_dir.mkdir()

    # Drop in a malformed JSON file directly.
    (fb_dir / "broken__2026-05-14T12_00_00Z.json").write_text(
        "{not valid json", encoding="utf-8"
    )
    # Drop in a non-object JSON file (valid JSON, wrong shape).
    (fb_dir / "wrongshape__2026-05-14T12_00_00Z.json").write_text(
        "[1, 2, 3]", encoding="utf-8"
    )
    # Drop in a valid-object JSON file missing required keys.
    (fb_dir / "incomplete__2026-05-14T12_00_00Z.json").write_text(
        json.dumps({"finding_id_str": "x"}), encoding="utf-8"
    )

    # Drop in a real valid record so we can confirm load continues
    # past the malformed entries.
    good = FindingFeedback(
        finding_id_str="clones:sym:aaa",
        decision="accepted_real",
        rationale="real-bug",
        reviewer="human:alice@example.com",
        timestamp="2026-05-14T12:00:00Z",
    )
    persist_feedback(good, feedback_dir=fb_dir)

    with warnings.catch_warnings(record=True) as captured:
        warnings.simplefilter("always")
        records = load_feedback(feedback_dir=fb_dir)

    # The valid record loaded; malformed entries were skipped.
    assert len(records) == 1
    assert records[0].finding_id_str == "clones:sym:aaa"

    # At least one warning per malformed file.
    user_warnings = [w for w in captured if issubclass(w.category, UserWarning)]
    assert len(user_warnings) >= 3
    messages = " ".join(str(w.message) for w in user_warnings)
    assert "skipping" in messages.lower()


# ---------------------------------------------------------------------------
# Aggregation primitive (the rules-pack improvement-data hook)
# ---------------------------------------------------------------------------


def test_aggregate_dismissal_reasons_counts_correctly(tmp_path: Path) -> None:
    """Dismissal rationales are tallied per-detector; accepts are ignored."""
    fb_dir = tmp_path / "feedback"

    # 10 entries — mix of dismissals and one accept, mix of two detectors.
    records = [
        # 6 visitor-pattern dismissals on the smells detector
        ("smells:visitor:1", "dismissed_false_positive", "visitor-pattern"),
        ("smells:visitor:2", "dismissed_false_positive", "visitor-pattern"),
        ("smells:visitor:3", "dismissed_by_design",      "visitor-pattern"),
        ("smells:visitor:4", "dismissed_false_positive", "visitor-pattern"),
        ("smells:visitor:5", "dismissed_test_fixture",   "visitor-pattern"),
        ("smells:visitor:6", "dismissed_by_design",      "visitor-pattern"),
        # 2 third-party-code dismissals on smells
        ("smells:thirdparty:1", "dismissed_by_design", "third-party-code"),
        ("smells:thirdparty:2", "dismissed_by_design", "third-party-code"),
        # 1 accept on smells (must NOT count)
        ("smells:realbug:1", "accepted_real", "real-bug"),
        # 1 dismissal on a DIFFERENT detector (must NOT bleed in)
        ("clones:c:1", "dismissed_false_positive", "test-fixture"),
    ]
    for i, (fid, decision, rationale) in enumerate(records):
        fb = FindingFeedback(
            finding_id_str=fid,
            decision=decision,
            rationale=rationale,
            reviewer="human:alice@example.com",
            timestamp=f"2026-05-14T12:{i:02d}:00Z",
        )
        persist_feedback(fb, feedback_dir=fb_dir)

    counts = aggregate_dismissal_reasons(detector="smells", feedback_dir=fb_dir)

    # Only smells dismissals (visitor-pattern × 6, third-party-code × 2).
    assert counts == {"visitor-pattern": 6, "third-party-code": 2}

    # Insertion order = descending count (top rationale first).
    assert list(counts.keys())[0] == "visitor-pattern"

    # Clones detector seen separately.
    clones_counts = aggregate_dismissal_reasons(
        detector="clones", feedback_dir=fb_dir
    )
    assert clones_counts == {"test-fixture": 1}

    # Detector with no dismissals returns empty.
    empty_counts = aggregate_dismissal_reasons(
        detector="nonexistent", feedback_dir=fb_dir
    )
    assert empty_counts == {}

    # Bad input rejected.
    with pytest.raises(ValueError):
        aggregate_dismissal_reasons(detector="", feedback_dir=fb_dir)


# ---------------------------------------------------------------------------
# Round-trip + truncation + path safety
# ---------------------------------------------------------------------------


def test_feedback_round_trips_canonical_json(tmp_path: Path) -> None:
    """persist → load reconstructs the exact same dataclass."""
    fb = FindingFeedback(
        finding_id_str="taint:src.foo.bar:9abc",
        decision="dismissed_test_fixture",
        rationale="test-fixture",
        reviewer="ci_runner:github.com/owner/repo/runs/123",
        timestamp="2026-05-14T15:30:45+00:00",
        free_text="Sanitiser is a test double; real path covered elsewhere.",
        extra={"pr_number": 42, "audit_row_id": "ar_001"},
    )

    fb_dir = tmp_path / "feedback"
    persist_feedback(fb, feedback_dir=fb_dir)

    loaded = load_feedback(feedback_dir=fb_dir)
    assert len(loaded) == 1
    got = loaded[0]

    assert got.finding_id_str == fb.finding_id_str
    assert got.decision == fb.decision
    assert got.rationale == fb.rationale
    assert got.reviewer == fb.reviewer
    assert got.timestamp == fb.timestamp
    assert got.free_text == fb.free_text
    assert dict(got.extra) == dict(fb.extra)

    # Canonical-JSON is stable across constructions with re-ordered
    # keyword arguments.
    fb_reordered = FindingFeedback(
        extra={"audit_row_id": "ar_001", "pr_number": 42},
        timestamp="2026-05-14T15:30:45+00:00",
        decision="dismissed_test_fixture",
        rationale="test-fixture",
        free_text="Sanitiser is a test double; real path covered elsewhere.",
        reviewer="ci_runner:github.com/owner/repo/runs/123",
        finding_id_str="taint:src.foo.bar:9abc",
    )
    assert fb.to_canonical_json() == fb_reordered.to_canonical_json()


def test_free_text_truncated_at_500_chars() -> None:
    """``free_text`` longer than the cap is silently truncated."""
    long_text = "x" * 1000
    fb = FindingFeedback(
        finding_id_str="clones:sym:aaa",
        decision="accepted_real",
        rationale="real-bug",
        reviewer="human:alice@example.com",
        timestamp="2026-05-14T12:00:00Z",
        free_text=long_text,
    )
    assert fb.free_text is not None
    assert len(fb.free_text) == FREE_TEXT_MAX_CHARS
    assert fb.free_text == "x" * FREE_TEXT_MAX_CHARS

    # Exactly-at-cap is preserved untouched.
    exact = "y" * FREE_TEXT_MAX_CHARS
    fb_exact = FindingFeedback(
        finding_id_str="clones:sym:bbb",
        decision="accepted_real",
        rationale="real-bug",
        reviewer="human:alice@example.com",
        timestamp="2026-05-14T12:00:00Z",
        free_text=exact,
    )
    assert fb_exact.free_text == exact

    # None is preserved as None.
    fb_none = FindingFeedback(
        finding_id_str="clones:sym:ccc",
        decision="accepted_real",
        rationale="real-bug",
        reviewer="human:alice@example.com",
        timestamp="2026-05-14T12:00:00Z",
    )
    assert fb_none.free_text is None


def test_path_traversal_in_finding_id_str_is_sanitized(tmp_path: Path) -> None:
    """A traversal-shaped ``finding_id_str`` writes a safe sibling file.

    The opinionated sanitisation rule:

    1. Any char outside ``[A-Za-z0-9._-]`` becomes ``_``. This collapses
       ``/``, ``\\``, ``:``, NUL, and every separator onto one sentinel.
    2. Leading dots are stripped so ``..`` can never escape upward.
    3. The full unsanitised ``finding_id_str`` is preserved verbatim
       INSIDE the JSON payload — the filename is an index hint, not the
       authoritative id.
    """
    fb_dir = tmp_path / "feedback"

    fb = FindingFeedback(
        finding_id_str="../../etc/passwd",
        decision="dismissed_false_positive",
        rationale="path-traversal-attempt",
        reviewer="human:alice@example.com",
        timestamp="2026-05-14T12:00:00Z",
    )
    written_path = persist_feedback(fb, feedback_dir=fb_dir)
    assert written_path  # write succeeded

    written = Path(written_path)

    # The written file lives directly inside fb_dir — no traversal.
    assert written.parent.resolve() == fb_dir.resolve()
    assert written.exists()

    # Filename is sanitised: no path separators, no leading dot, no colon.
    name = written.name
    assert "/" not in name
    assert "\\" not in name
    assert ":" not in name
    assert not name.startswith(".")
    assert ".." not in name

    # The original unsanitised id survives in the JSON payload — the
    # filename is just an index; the authoritative id is on disk
    # verbatim in the body.
    payload = json.loads(written.read_text(encoding="utf-8"))
    assert payload["finding_id_str"] == "../../etc/passwd"

    # And it round-trips through load_feedback with the filter argument.
    loaded = load_feedback("../../etc/passwd", feedback_dir=fb_dir)
    assert len(loaded) == 1
    assert loaded[0].finding_id_str == "../../etc/passwd"

    # NUL byte / Windows-illegal chars also collapse to underscore.
    fb_nul = FindingFeedback(
        finding_id_str="weird\x00name|with*chars",
        decision="dismissed_false_positive",
        rationale="weird-id",
        reviewer="human:alice@example.com",
        timestamp="2026-05-14T13:00:00Z",
    )
    p2 = persist_feedback(fb_nul, feedback_dir=fb_dir)
    assert p2
    assert Path(p2).exists()


# ---------------------------------------------------------------------------
# Default location
# ---------------------------------------------------------------------------


def test_default_feedback_dir_constant_is_repo_local() -> None:
    """Default location is repo-local under ``.roam/`` (no global state)."""
    assert DEFAULT_FEEDBACK_DIR == ".roam/feedback"
