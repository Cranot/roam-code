"""W596 — ``read_run_meta`` (+ bonus ``read_run_events``) plumbs ``warnings_out``.

W448 added ``warnings_out`` to ``read_lease``. W589/W592 closed the lease
sibling cluster. W593/W595 closed the permits cluster. W596 closes the
runs-ledger reader: ``read_run_meta`` previously swallowed
``(OSError, json.JSONDecodeError)`` + ``TypeError`` (from the
:class:`RunMeta` constructor) with a bare ``return None`` and converted
"meta.json not on disk" / "meta.json unreadable" / "malformed JSON" /
"top-level not a dict" / "dataclass kwargs reject schema" into one
indistinguishable None.

The W596-bonus plumb covers ``read_run_events`` (sibling read function
in the same file with the SAME silent-swallow shape — per-line
``JSONDecodeError`` + missing-file silent-empty).

Marker shape mirrors W595's ``read_permit`` 5-marker shape with a
``run_meta_`` / ``run_event_`` prefix so a caller threading the same
bucket through multiple substrate read sites sees a uniform marker
vocabulary.

``read_run_meta`` closed-enum kinds:

  * ``run_meta_not_found:<run_id>/meta.json``
  * ``run_meta_read_failed:<run_id>/meta.json:<exc_class>:<detail>``
  * ``run_meta_corrupt:<run_id>/meta.json:JSONDecodeError``
  * ``run_meta_corrupt:<run_id>/meta.json:NotAJsonObject``
  * ``run_meta_corrupt:<run_id>/meta.json:SchemaInvalid``

``read_run_events`` (bonus) closed-enum kinds:

  * ``run_events_not_found:<run_id>/events.jsonl``
  * ``run_events_read_failed:<run_id>/events.jsonl:<exc_class>:<detail>``
  * ``run_event_corrupt:<run_id>/events.jsonl:<seq>:JSONDecodeError``
  * ``run_event_corrupt:<run_id>/events.jsonl:<seq>:NotAJsonObject``

The ``None`` (meta) / empty-stream (events) returns are PRESERVED — the
existing caller contracts are unchanged. ``warnings_out=None`` (default)
preserves the pre-W596 silent-drop behaviour.

LAW 4 note: warning kinds are NOT ``agent_contract.facts`` strings and
therefore not subject to the concrete-noun-terminal lint. They are
internal diagnostic markers (same discipline as W589 / W592 / W593 /
W595).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
from conftest import git_init  # noqa: E402

from roam.runs.ledger import (  # noqa: E402
    EVENTS_FILE,
    META_FILE,
    log_event,
    read_run_events,
    read_run_meta,
    run_dir,
    start_run,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def runs_project(tmp_path: Path) -> Path:
    """A minimal git-initialised project mirroring ``test_runs_ledger``."""
    proj = tmp_path / "w596_runproj"
    proj.mkdir()
    (proj / ".gitignore").write_text(".roam/\n")
    (proj / "app.py").write_text("def main():\n    return 0\n")
    git_init(proj)
    return proj


# ===========================================================================
# (1) read_run_meta — happy path: clean read on existing run emits no warning
# ===========================================================================


def test_read_clean_emits_no_warning(runs_project: Path) -> None:
    """A normal read on a clean run appends nothing to ``warnings_out``.

    Sanity check that the W596 plumbing only fires on degenerate paths.
    """
    meta = start_run(runs_project, agent="w596-clean")

    warnings: list[str] = []
    loaded = read_run_meta(runs_project, meta.run_id, warnings_out=warnings)

    assert loaded is not None, "clean read must return a RunMeta"
    assert loaded.run_id == meta.run_id
    assert loaded.agent == "w596-clean"
    assert warnings == [], f"clean read_run_meta must NOT emit warnings; got {warnings!r}"


# ===========================================================================
# (2) read_run_meta — missing meta.json emits ``run_meta_not_found:`` marker
# ===========================================================================


def test_read_missing_run_meta_emits_not_found_marker(runs_project: Path) -> None:
    """Read on a non-existent run_id emits ``run_meta_not_found:<run>/meta.json``.

    Marker shape mirrors W595's ``permit_not_found:`` — the missing-file
    path is an operational anomaly worth disclosing on a ``runs show``
    lookup (caller typo / wrong repo root / partially-written run dir).
    """
    warnings: list[str] = []
    result = read_run_meta(
        runs_project,
        "run_20990101_deadbe",
        warnings_out=warnings,
    )

    assert result is None, "missing run must still return None (existing contract)"
    assert len(warnings) == 1, f"expected exactly one warning on missing run; got {len(warnings)}: {warnings!r}"
    msg = warnings[0]
    assert msg.startswith("run_meta_not_found:"), msg
    assert f"run_20990101_deadbe/{META_FILE}" in msg, msg


# ===========================================================================
# (3) read_run_meta — corrupt JSON emits ``run_meta_corrupt:...:JSONDecodeError``
# ===========================================================================


def test_read_corrupt_json_emits_corrupt_marker(runs_project: Path) -> None:
    """Malformed JSON emits ``run_meta_corrupt:<run>/meta.json:JSONDecodeError``.

    Marker prefix mirrors W595's ``permit_corrupt:`` shape so a caller
    grepping substrate warnings sees one uniform vocabulary.
    """
    meta = start_run(runs_project, agent="w596-corrupt")
    meta_path = run_dir(runs_project, meta.run_id) / META_FILE
    meta_path.write_text("{not valid json", encoding="utf-8")

    warnings: list[str] = []
    result = read_run_meta(runs_project, meta.run_id, warnings_out=warnings)

    assert result is None, "corrupt meta.json must return None (existing contract)"
    assert len(warnings) == 1, f"expected one corrupt-meta warning; got {len(warnings)}: {warnings!r}"
    msg = warnings[0]
    assert msg.startswith("run_meta_corrupt:"), msg
    assert f"{meta.run_id}/{META_FILE}" in msg, msg
    assert "JSONDecodeError" in msg, msg


# ===========================================================================
# (4) read_run_meta — OSError on read emits ``run_meta_read_failed:`` marker
# ===========================================================================


def test_read_other_oserror_emits_read_failed_marker(runs_project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A non-decode OSError on read_text emits ``run_meta_read_failed:<file>:<exc>:<detail>``.

    Monkeypatches ``Path.read_text`` to raise ``PermissionError`` for the
    specific meta.json path. The file EXISTS on disk (so we get past the
    ``not path.exists()`` short-circuit) but read fails.
    """
    meta = start_run(runs_project, agent="w596-eacces")
    meta_path = (run_dir(runs_project, meta.run_id) / META_FILE).resolve()
    assert meta_path.exists(), "fixture must produce a meta.json file"

    original_read_text = Path.read_text

    def _raising_read_text(self, *args, **kwargs):
        if self.resolve() == meta_path:
            raise PermissionError("synthetic-EACCES from W596 test")
        return original_read_text(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", _raising_read_text)

    warnings: list[str] = []
    result = read_run_meta(runs_project, meta.run_id, warnings_out=warnings)

    assert result is None, "read_text failure must preserve None return; got non-None"
    assert len(warnings) == 1, f"expected one run_meta_read_failed warning; got {len(warnings)}: {warnings!r}"
    msg = warnings[0]
    assert msg.startswith("run_meta_read_failed:"), msg
    assert f"{meta.run_id}/{META_FILE}" in msg, msg
    assert "PermissionError" in msg, msg
    assert "synthetic-EACCES from W596 test" in msg, msg


# ===========================================================================
# (5) read_run_meta — non-dict top-level emits ``...:NotAJsonObject``
# ===========================================================================


def test_read_non_dict_top_level_emits_corrupt_marker(runs_project: Path) -> None:
    """Top-level JSON array emits ``run_meta_corrupt:<file>:NotAJsonObject``.

    Mirrors W595's fourth corrupt sub-case (top-level value is valid
    JSON but not a dict). Distinct structured marker so an operator can
    grep the bucket.
    """
    meta = start_run(runs_project, agent="w596-array")
    meta_path = run_dir(runs_project, meta.run_id) / META_FILE
    meta_path.write_text("[1, 2, 3]", encoding="utf-8")

    warnings: list[str] = []
    result = read_run_meta(runs_project, meta.run_id, warnings_out=warnings)

    assert result is None
    assert len(warnings) == 1, warnings
    msg = warnings[0]
    assert msg.startswith("run_meta_corrupt:"), msg
    assert f"{meta.run_id}/{META_FILE}" in msg, msg
    assert "NotAJsonObject" in msg, msg


# ===========================================================================
# (6) read_run_meta — schema-invalid dict emits ``...:SchemaInvalid``
# ===========================================================================


def test_read_schema_invalid_emits_corrupt_marker(runs_project: Path) -> None:
    """A dict with unknown kwargs that trips ``RunMeta(**kwargs)`` TypeError
    emits ``run_meta_corrupt:<file>:SchemaInvalid``.

    The ``RunMeta`` constructor accepts the ``known`` set via ``**kwargs``
    and rejects unknown keyword args. The current code-path filters by
    ``known`` keys BEFORE constructing the dataclass, so a non-dict in
    a known slot won't currently trip TypeError naturally. To exercise
    the SchemaInvalid path deterministically, monkeypatch the
    :class:`RunMeta` constructor to raise ``TypeError`` for one call.
    """
    # Build a meta.json missing the required ``run_id`` / ``agent`` /
    # ``started_at`` positional fields — RunMeta is a dataclass with
    # those three fields required, so the kwargs-only build will raise
    # TypeError ("missing 3 required positional arguments").
    meta = start_run(runs_project, agent="w596-schemainvalid")
    meta_path = run_dir(runs_project, meta.run_id) / META_FILE
    meta_path.write_text(
        json.dumps(
            {
                # NOTE: required ``run_id`` / ``agent`` / ``started_at``
                # deliberately MISSING. ``ended_at`` alone in the known
                # set means kwargs = {"ended_at": ...} and the
                # RunMeta dataclass __init__ raises TypeError on
                # missing required positional args.
                "ended_at": "2099-01-01T00:00:00Z",
            }
        ),
        encoding="utf-8",
    )

    warnings: list[str] = []
    result = read_run_meta(runs_project, meta.run_id, warnings_out=warnings)

    assert result is None
    assert len(warnings) == 1, warnings
    msg = warnings[0]
    assert msg.startswith("run_meta_corrupt:"), msg
    assert f"{meta.run_id}/{META_FILE}" in msg, msg
    assert "SchemaInvalid" in msg, msg


# ===========================================================================
# (7) read_run_meta — default ``warnings_out=None`` preserves silent behaviour
# ===========================================================================


def test_read_default_none_no_crash(runs_project: Path) -> None:
    """Default ``warnings_out=None`` returns None cleanly, no crash, no warnings.

    Existing callers (cmd_runs / cmd_replay / cmd_next / cmd_agent_score /
    attest.vsa / evidence.config_hashes_producer / evidence.collector)
    call ``read_run_meta(root, run_id)`` with no kwargs — they must NOT
    regress on any failure mode covered by the W596 plumb.
    """
    # (a) Missing run -- the most common silent-None path.
    result = read_run_meta(runs_project, "run_20990101_deadbe")
    assert result is None

    # (b) Corrupt JSON -- the second silent-None path.
    meta = start_run(runs_project, agent="w596-default")
    meta_path = run_dir(runs_project, meta.run_id) / META_FILE
    meta_path.write_text("{not valid json", encoding="utf-8")
    result = read_run_meta(runs_project, meta.run_id)
    assert result is None

    # (c) Schema-invalid -- the third silent-None path.
    meta2 = start_run(runs_project, agent="w596-default-2")
    meta2_path = run_dir(runs_project, meta2.run_id) / META_FILE
    meta2_path.write_text(json.dumps({"ended_at": "x"}), encoding="utf-8")
    result = read_run_meta(runs_project, meta2.run_id)
    assert result is None

    # (d) Happy path with default-None still returns the record.
    meta3 = start_run(runs_project, agent="w596-default-happy")
    loaded = read_run_meta(runs_project, meta3.run_id)
    assert loaded is not None
    assert loaded.run_id == meta3.run_id


# ===========================================================================
# (8) read_run_events bonus — happy path emits no warning
# ===========================================================================


def test_read_events_clean_emits_no_warning(runs_project: Path) -> None:
    """Clean read on a run with valid events emits no warning."""
    meta = start_run(runs_project, agent="w596-events-clean")
    log_event(runs_project, meta.run_id, action="preflight", verdict="SAFE")
    log_event(runs_project, meta.run_id, action="impact", verdict="OK")

    warnings: list[str] = []
    events = list(read_run_events(runs_project, meta.run_id, warnings_out=warnings))

    assert len(events) == 2
    assert events[0]["action"] == "preflight"
    assert events[1]["action"] == "impact"
    assert warnings == [], f"clean read_run_events must NOT emit warnings; got {warnings!r}"


# ===========================================================================
# (9) read_run_events bonus — missing events.jsonl emits ``run_events_not_found:``
# ===========================================================================


def test_read_events_missing_file_emits_not_found_marker(runs_project: Path) -> None:
    """Read on a non-existent events.jsonl emits ``run_events_not_found:``.

    Construct a synthetic run_id whose directory does not exist — the
    iterator yields nothing and emits one marker.
    """
    warnings: list[str] = []
    events = list(read_run_events(runs_project, "run_20990101_missing", warnings_out=warnings))

    assert events == [], "missing events file must yield nothing"
    assert len(warnings) == 1, warnings
    msg = warnings[0]
    assert msg.startswith("run_events_not_found:"), msg
    assert f"run_20990101_missing/{EVENTS_FILE}" in msg, msg


# ===========================================================================
# (10) read_run_events bonus — corrupt line emits ``run_event_corrupt:<seq>:JSONDecodeError``
# ===========================================================================


def test_read_events_corrupt_line_emits_corrupt_marker(runs_project: Path) -> None:
    """One malformed JSON line emits a per-line corrupt marker.

    Iteration continues past the corrupt line — the substrate keeps
    streaming so a single mangled write never blocks the rest of the
    chain.
    """
    meta = start_run(runs_project, agent="w596-events-corrupt")
    log_event(runs_project, meta.run_id, action="preflight")
    # Append a deliberately-malformed line manually.
    events_path = run_dir(runs_project, meta.run_id) / EVENTS_FILE
    with events_path.open("a", encoding="utf-8") as fh:
        fh.write("{not valid json\n")
    log_event(runs_project, meta.run_id, action="impact")

    warnings: list[str] = []
    events = list(read_run_events(runs_project, meta.run_id, warnings_out=warnings))

    # The two valid events stream through.
    actions = [e.get("action") for e in events]
    assert "preflight" in actions
    assert "impact" in actions

    # One corrupt-line marker emitted.
    assert len(warnings) == 1, warnings
    msg = warnings[0]
    assert msg.startswith("run_event_corrupt:"), msg
    assert f"{meta.run_id}/{EVENTS_FILE}" in msg, msg
    assert "JSONDecodeError" in msg, msg
    # The corrupt line was line 2 (1=preflight, 2=corrupt, 3=impact).
    assert ":2:" in msg, msg


# ===========================================================================
# (11) read_run_events bonus — non-dict line emits ``...:NotAJsonObject``
# ===========================================================================


def test_read_events_non_dict_line_emits_corrupt_marker(runs_project: Path) -> None:
    """A line that parses as JSON but isn't a dict emits the NotAJsonObject marker."""
    meta = start_run(runs_project, agent="w596-events-notdict")
    log_event(runs_project, meta.run_id, action="preflight")
    events_path = run_dir(runs_project, meta.run_id) / EVENTS_FILE
    with events_path.open("a", encoding="utf-8") as fh:
        fh.write("[1, 2, 3]\n")

    warnings: list[str] = []
    events = list(read_run_events(runs_project, meta.run_id, warnings_out=warnings))

    assert len(events) == 1  # only the preflight event
    assert len(warnings) == 1, warnings
    msg = warnings[0]
    assert msg.startswith("run_event_corrupt:"), msg
    assert "NotAJsonObject" in msg, msg


# ===========================================================================
# (12) read_run_events bonus — default ``warnings_out=None`` preserves behaviour
# ===========================================================================


def test_read_events_default_none_no_crash(runs_project: Path) -> None:
    """Default ``warnings_out=None`` preserves the pre-W596 silent behaviour.

    Existing callers (cmd_runs / cmd_replay / cmd_next / cmd_agent_score /
    evidence.collector) call ``read_run_events(root, run_id)`` with no
    kwargs — they must NOT regress on any of the failure modes.
    """
    # (a) Missing events file — yield nothing, no crash.
    events = list(read_run_events(runs_project, "run_20990101_missing"))
    assert events == []

    # (b) Corrupt line — keep streaming.
    meta = start_run(runs_project, agent="w596-events-default")
    log_event(runs_project, meta.run_id, action="ok")
    events_path = run_dir(runs_project, meta.run_id) / EVENTS_FILE
    with events_path.open("a", encoding="utf-8") as fh:
        fh.write("{not valid json\n")
    events = list(read_run_events(runs_project, meta.run_id))
    assert len(events) == 1
    assert events[0]["action"] == "ok"
