"""Tests for the W153 migration: critique detector emits to the central
findings registry.

The critique detector is the SEVENTH migration onto the A4 findings
table (after W95 clones, W99 dead, W102 complexity, W109 smells, W115
bus-factor, W134 pr-risk). Like pr-risk, critique is INVOCATION-SCOPED:
each run produces findings tied to a specific diff (read from stdin /
``--input`` / ``--batch``) at the moment of invocation.

critique is the FIRST detector to claim the ``patch.*`` namespace and
to introduce ``subject_kind="diff_region"`` to the registry vocabulary.

The detector emits up to three kinds of findings per invocation:

* ``patch.clone_not_edited`` (when a changed symbol has clone siblings
  outside the diff) — ``static_analysis`` (reads from the deterministic
  persisted ``clone_pairs`` table).
* ``patch.high_blast`` (when a changed symbol has caller count above the
  ``--high-callers`` threshold) — ``structural`` (raw graph edge count).
* ``patch.intent_mismatch`` (when the PR title's verb-set doesn't line
  up with the diff's additions/deletions shape) — ``heuristic``
  (NLP-style title parse).

Every row carries ``evidence_json.diff_sha`` so consumers can group
findings by PR / commit / branch — and tell stale (since-merged) rows
apart from fresh ones.
"""

from __future__ import annotations

import json
import os
import sqlite3
import sys
import textwrap
from pathlib import Path

from click.testing import CliRunner

sys.path.insert(0, str(Path(__file__).parent))

from roam.cli import cli
from roam.commands.cmd_critique import (  # noqa: E402
    _CHECK_TO_KIND,
    _CRITIQUE_KIND_TO_CONFIDENCE,
    CRITIQUE_DETECTOR_VERSION,
    _critique_finding_id,
    _diff_sha,
    _emit_critique_findings,
)
from roam.db.connection import open_db  # noqa: E402
from tests._findings_helpers import assert_detector_visible_in_findings_count  # noqa: E402

# ---------------------------------------------------------------------------
# Unit tests on the deterministic helpers (no DB / no CLI invocation)
# ---------------------------------------------------------------------------


def test_diff_sha_is_deterministic():
    """Same inputs produce the same diff_sha; different inputs produce different shas."""
    a = _diff_sha(
        label="stdin",
        intent_text="fix login bug",
        file_paths=["src/a.py", "src/b.py"],
    )
    b = _diff_sha(
        label="stdin",
        intent_text="fix login bug",
        file_paths=["src/a.py", "src/b.py"],
    )
    assert a == b
    # Different file set -> different sha.
    c = _diff_sha(
        label="stdin",
        intent_text="fix login bug",
        file_paths=["src/a.py"],
    )
    assert c != a
    # Different intent -> different sha.
    d = _diff_sha(
        label="stdin",
        intent_text="add new feature",
        file_paths=["src/a.py", "src/b.py"],
    )
    assert d != a
    # Different input source label -> different sha.
    e = _diff_sha(
        label="input:my.patch",
        intent_text="fix login bug",
        file_paths=["src/a.py", "src/b.py"],
    )
    assert e != a


def test_diff_sha_is_order_independent():
    """File-path order doesn't change the diff_sha (sort happens inside)."""
    a = _diff_sha(
        label="stdin",
        intent_text=None,
        file_paths=["src/a.py", "src/b.py", "src/c.py"],
    )
    b = _diff_sha(
        label="stdin",
        intent_text=None,
        file_paths=["src/c.py", "src/a.py", "src/b.py"],
    )
    assert a == b


def test_diff_sha_matches_pr_risk_shape():
    """Confirm _diff_sha mirrors the W134 pr-risk _diff_id template.

    Both compute sha1(label + range/intent + staged + sorted-file-paths)[:12].
    critique substitutes ``intent_text`` for ``commit_range`` (the
    semantic disambiguator for the same file set under a different PR)
    and pins ``staged=0`` (critique doesn't read from the staging area).
    """
    import hashlib

    label = "stdin"
    intent = "fix"
    files = ["src/x.py", "src/y.py"]
    expected_raw = f"{label}|range={intent or ''}|staged=0|files={','.join(sorted(files))}"
    expected = hashlib.sha1(expected_raw.encode("utf-8")).hexdigest()[:12]
    assert _diff_sha(label=label, intent_text=intent, file_paths=files) == expected


def test_critique_finding_id_format():
    """_critique_finding_id always begins with ``critique:patch.<kind>:<diff_sha>``."""
    fid = _critique_finding_id("patch.high_blast", "abc123", "src/x.py:10-20")
    assert fid.startswith("critique:patch.high_blast:abc123:")
    # Deterministic — same inputs produce the same id.
    fid2 = _critique_finding_id("patch.high_blast", "abc123", "src/x.py:10-20")
    assert fid == fid2
    # Different kind -> different id.
    other = _critique_finding_id("patch.clone_not_edited", "abc123", "src/x.py:10-20")
    assert other != fid
    # Different diff_sha -> different id.
    other2 = _critique_finding_id("patch.high_blast", "def456", "src/x.py:10-20")
    assert other2 != fid
    # Different region -> different id.
    other3 = _critique_finding_id("patch.high_blast", "abc123", "src/y.py:1-2")
    assert other3 != fid


def test_confidence_tier_table_covers_emitted_kinds():
    """Every kind the emit helper writes has a confidence tier."""
    expected_kinds = {
        "patch.clone_not_edited",
        "patch.high_blast",
        "patch.intent_mismatch",
    }
    assert expected_kinds <= set(_CRITIQUE_KIND_TO_CONFIDENCE.keys())
    # clone_pairs table is deterministic AST/structural -> static_analysis.
    assert _CRITIQUE_KIND_TO_CONFIDENCE["patch.clone_not_edited"] == "static_analysis"
    # Caller count is a raw graph edge count -> structural.
    assert _CRITIQUE_KIND_TO_CONFIDENCE["patch.high_blast"] == "structural"
    # NLP-style title parse against the diff shape -> heuristic.
    assert _CRITIQUE_KIND_TO_CONFIDENCE["patch.intent_mismatch"] == "heuristic"


def test_check_to_kind_routing_is_complete():
    """Every check label that aggregator can produce has a routed kind.

    ``roam.critique.checks`` emits three Finding.check labels:
    ``clones-not-edited`` / ``impact`` / ``intent``. Each must route to a
    registry kind so persist doesn't silently drop one.
    """
    assert _CHECK_TO_KIND["clones-not-edited"] == "patch.clone_not_edited"
    assert _CHECK_TO_KIND["impact"] == "patch.high_blast"
    assert _CHECK_TO_KIND["intent"] == "patch.intent_mismatch"
    # The mapped kinds must be tier-mapped — otherwise emit would KeyError.
    for kind in _CHECK_TO_KIND.values():
        assert kind in _CRITIQUE_KIND_TO_CONFIDENCE


# ---------------------------------------------------------------------------
# Direct unit tests on _emit_critique_findings (no CLI / no indexer)
# ---------------------------------------------------------------------------


# Synthetic findings dicts (the aggregator's output shape) — used to
# drive the emit helper directly so we can assert on the registry rows
# without crafting a diff + indexed fixture for every assertion.
_SYNTH_IMPACT_FINDING = {
    "check": "impact",
    "severity": "medium",
    "title": "handleSave has 12 direct callers",
    "detail": "Changing handleSave ripples through 12 call sites.",
    "evidence": {
        "symbol_id": 42,
        "callers": 12,
        "file": "src/app.py",
        "line": 100,
        "max_caller_runtime_score": 0.1,
    },
}

_SYNTH_CLONE_FINDING = {
    "check": "clones-not-edited",
    "severity": "high",
    "title": "process_orders has 1 clone sibling that may need the same change",
    "detail": "Unedited clone siblings:\n  src/b.py:1 (handle_invoices, sim=0.85)",
    "evidence": {
        "changed_symbol": {
            "id": 7,
            "name": "process_orders",
            "file": "src/a.py",
        },
        "siblings": [
            {
                "sibling_qname": "src/b.py:handle_invoices",
                "sibling_file": "src/b.py",
                "sibling_line": 1,
                "sibling_func": "handle_invoices",
                "similarity": 0.85,
            }
        ],
    },
}

_SYNTH_INTENT_FINDING = {
    "check": "intent",
    "severity": "medium",
    "title": "PR title says 'remove' but the diff has no deletions",
    "detail": "Either the intent is overstated or the change is purely additive.",
    "evidence": {
        "intent_label": "remove",
        "symbols_touched": 1,
        "additions": 5,
        "deletions": 0,
        "files": 1,
    },
}

_SYNTH_DIFF_DATA = {
    "diff_sha": "synth0000abcd",
    "label": "stdin",
    "intent_text": "remove the dead code",
    "file_list": ["src/a.py"],
}


def test_emit_helper_writes_all_three_kinds(indexed_project):
    """Above thresholds, _emit_critique_findings writes one row per kind."""
    with open_db(readonly=False) as conn:
        conn.execute("DELETE FROM findings WHERE source_detector = 'critique'")
        conn.commit()

        written = _emit_critique_findings(
            conn,
            [_SYNTH_CLONE_FINDING, _SYNTH_IMPACT_FINDING, _SYNTH_INTENT_FINDING],
            _SYNTH_DIFF_DATA,
            CRITIQUE_DETECTOR_VERSION,
        )
        conn.commit()

    assert written == 3

    with open_db(readonly=True) as conn:
        rows = conn.execute(
            "SELECT finding_id_str, confidence, subject_kind, subject_id, "
            "       source_detector, source_version "
            "FROM findings WHERE source_detector = 'critique'"
        ).fetchall()
    kinds = {row[0].split(":")[1] for row in rows}
    assert kinds == {
        "patch.clone_not_edited",
        "patch.high_blast",
        "patch.intent_mismatch",
    }
    # Confidence-tier assignment on the persisted rows.
    by_kind = {row[0].split(":")[1]: row[1] for row in rows}
    assert by_kind["patch.clone_not_edited"] == "static_analysis"
    assert by_kind["patch.high_blast"] == "structural"
    assert by_kind["patch.intent_mismatch"] == "heuristic"
    # Subject vocabulary — every row uses diff_region with NULL id.
    for row in rows:
        assert row["subject_kind"] == "diff_region"
        assert row["subject_id"] is None
        assert row["source_detector"] == "critique"
        assert row["source_version"] == CRITIQUE_DETECTOR_VERSION
        assert row["finding_id_str"].startswith("critique:")


def test_emit_helper_anchors_evidence_json(indexed_project):
    """evidence_json carries diff_sha, file_list, qualified_name, affected_symbol_id."""
    with open_db(readonly=False) as conn:
        conn.execute("DELETE FROM findings WHERE source_detector = 'critique'")
        conn.commit()

        _emit_critique_findings(
            conn,
            [_SYNTH_IMPACT_FINDING],
            _SYNTH_DIFF_DATA,
            CRITIQUE_DETECTOR_VERSION,
        )
        conn.commit()

    with open_db(readonly=True) as conn:
        row = conn.execute(
            "SELECT evidence_json, claim FROM findings "
            "WHERE source_detector = 'critique' "
            "  AND finding_id_str LIKE 'critique:patch.high_blast:%' "
            "LIMIT 1"
        ).fetchone()
    assert row is not None
    evidence = json.loads(row["evidence_json"])

    # Audit-trail keys — every kind shares this base envelope.
    for key in (
        "diff_sha",
        "label",
        "intent_text",
        "file_list",
        "changed_files_count",
        "created_at_epoch",
        "qualified_name",
        "severity",
        "check",
        "title",
    ):
        assert key in evidence, f"evidence missing key {key}"

    # The diff_region qualified name follows "<file>:<line_start>-<line_end>".
    assert evidence["qualified_name"] == "src/app.py:100-100"
    # The impact finding carried an explicit symbol_id, which the emit
    # helper exposes as affected_symbol_id for drill-down queries.
    assert evidence["affected_symbol_id"] == 42
    # diff_sha round-trip.
    assert evidence["diff_sha"] == _SYNTH_DIFF_DATA["diff_sha"]


def test_emit_helper_handles_empty_findings(indexed_project):
    """An empty findings list is a no-op — zero rows written, no crash."""
    with open_db(readonly=False) as conn:
        conn.execute("DELETE FROM findings WHERE source_detector = 'critique'")
        conn.commit()

        written = _emit_critique_findings(
            conn,
            [],
            _SYNTH_DIFF_DATA,
            CRITIQUE_DETECTOR_VERSION,
        )
        conn.commit()

    assert written == 0

    with open_db(readonly=True) as conn:
        count = conn.execute("SELECT COUNT(*) FROM findings WHERE source_detector = 'critique'").fetchone()[0]
    assert count == 0


def test_emit_helper_rerun_upserts_in_place(indexed_project):
    """Calling the emitter twice with the same diff_data upserts (no duplicates)."""
    with open_db(readonly=False) as conn:
        conn.execute("DELETE FROM findings WHERE source_detector = 'critique'")
        conn.commit()

        # First call.
        _emit_critique_findings(
            conn,
            [_SYNTH_IMPACT_FINDING, _SYNTH_CLONE_FINDING],
            _SYNTH_DIFF_DATA,
            CRITIQUE_DETECTOR_VERSION,
        )
        conn.commit()

        first_ids = {
            row[0]
            for row in conn.execute("SELECT finding_id_str FROM findings WHERE source_detector = 'critique'").fetchall()
        }
        first_count = len(first_ids)
        assert first_count == 2

        # Second call — same diff_data, same findings -> same ids.
        _emit_critique_findings(
            conn,
            [_SYNTH_IMPACT_FINDING, _SYNTH_CLONE_FINDING],
            _SYNTH_DIFF_DATA,
            CRITIQUE_DETECTOR_VERSION,
        )
        conn.commit()

        second_ids = {
            row[0]
            for row in conn.execute("SELECT finding_id_str FROM findings WHERE source_detector = 'critique'").fetchall()
        }
    assert second_ids == first_ids
    assert len(second_ids) == 2


def test_emit_helper_different_diff_inserts_fresh_rows(indexed_project):
    """A different diff_sha produces a NEW row set, not an upsert.

    The audit-trail design: prior PR findings stay in the registry so a
    consumer can compare critique runs across iterations of the same
    branch. Only a rerun against the *same* diff_sha upserts.
    """
    with open_db(readonly=False) as conn:
        conn.execute("DELETE FROM findings WHERE source_detector = 'critique'")
        conn.commit()

        # First diff.
        _emit_critique_findings(
            conn,
            [_SYNTH_IMPACT_FINDING],
            _SYNTH_DIFF_DATA,
            CRITIQUE_DETECTOR_VERSION,
        )
        conn.commit()

        # Second diff — different file_list, different diff_sha.
        second_diff = {
            "diff_sha": "differentsha",
            "label": "stdin",
            "intent_text": "remove the dead code",
            "file_list": ["src/different.py"],
        }
        _emit_critique_findings(
            conn,
            [_SYNTH_IMPACT_FINDING],
            second_diff,
            CRITIQUE_DETECTOR_VERSION,
        )
        conn.commit()

        rows = conn.execute(
            "SELECT finding_id_str FROM findings "
            "WHERE source_detector = 'critique' "
            "  AND finding_id_str LIKE 'critique:patch.high_blast:%' "
            "ORDER BY id ASC"
        ).fetchall()

    # The first diff's id must still be present (audit trail), and a
    # second, distinct row must have been inserted.
    ids = [r[0] for r in rows]
    assert len(set(ids)) == 2, f"expected two distinct high_blast ids across diffs, got {ids}"


def test_emit_helper_skips_unknown_check(indexed_project):
    """A finding with an unrecognised check label is skipped, not crashed.

    LAW 8 — closed enumeration over free-string composition. Adding a
    new check requires both a ``_CHECK_TO_KIND`` entry AND a confidence
    tier; until then the row is dropped rather than minting a kind on
    the fly.
    """
    unknown = {
        "check": "unmapped-future-check",
        "severity": "low",
        "title": "made-up finding",
        "detail": "",
        "evidence": {},
    }
    with open_db(readonly=False) as conn:
        conn.execute("DELETE FROM findings WHERE source_detector = 'critique'")
        conn.commit()

        written = _emit_critique_findings(
            conn,
            [unknown, _SYNTH_IMPACT_FINDING],
            _SYNTH_DIFF_DATA,
            CRITIQUE_DETECTOR_VERSION,
        )
        conn.commit()
    assert written == 1, "only the impact finding should land in findings"


# ---------------------------------------------------------------------------
# End-to-end fixtures — drive the CLI with a real diff against an indexed repo
# ---------------------------------------------------------------------------


# A minimal unified diff against the python_project fixture's
# ``src/models.py``. Adds one line inside ``User.__init__`` so the
# critique pipeline's ``find_changed_symbols`` resolves to the
# ``__init__`` symbol on the indexed project.
_TINY_DIFF = textwrap.dedent(
    """\
    --- a/src/models.py
    +++ b/src/models.py
    @@ -3,3 +3,4 @@ class User:
         def __init__(self, name, email):
             self.name = name
             self.email = email
    +        self.added = True
    """
)


def _run_critique_persist(project, diff_text, *extra_args):
    """Invoke ``roam critique --persist`` with ``diff_text`` on stdin."""
    runner = CliRunner()
    old_cwd = os.getcwd()
    try:
        os.chdir(str(project))
        result = runner.invoke(
            cli,
            ["critique", "--persist", *extra_args],
            input=diff_text,
            catch_exceptions=False,
        )
        return result
    finally:
        os.chdir(old_cwd)


def test_critique_persist_writes_findings_to_registry(indexed_project):
    """End-to-end: piping a diff into ``critique --persist`` lands rows in findings.

    Uses ``--high-callers=1`` so even our tiny fixture surfaces an
    impact finding for the changed ``__init__``. The ``--intent`` flag
    forces a deterministic intent label so the intent check fires
    independent of git log state.
    """
    result = _run_critique_persist(
        indexed_project,
        _TINY_DIFF,
        "--high-callers",
        "1",
        "--intent",
        "remove dead code from the user model",
    )
    # Exit may be 0 or 5 (gate fail on high severity); both are valid
    # for the persist branch — we only care that the rows landed.
    assert result.exit_code in (0, 5), result.output

    with open_db(readonly=True) as conn:
        rows = conn.execute(
            "SELECT finding_id_str, subject_kind, subject_id, "
            "       source_detector, source_version, confidence "
            "FROM findings WHERE source_detector = 'critique'"
        ).fetchall()
    assert len(rows) >= 1, "expected at least one critique finding row"
    for r in rows:
        assert r["source_detector"] == "critique"
        assert r["source_version"] == CRITIQUE_DETECTOR_VERSION
        # First diff-region subject in the registry.
        assert r["subject_kind"] == "diff_region"
        assert r["subject_id"] is None
        assert r["finding_id_str"].startswith("critique:patch.")
        # Every emitted kind has a confidence tier.
        kind = r["finding_id_str"].split(":")[1]
        assert r["confidence"] == _CRITIQUE_KIND_TO_CONFIDENCE[kind]


def test_critique_persist_rerun_upserts_not_duplicates(indexed_project):
    """Same diff on a rerun produces the same id set (upsert in place)."""
    r1 = _run_critique_persist(
        indexed_project,
        _TINY_DIFF,
        "--high-callers",
        "1",
        "--intent",
        "remove dead code from the user model",
    )
    assert r1.exit_code in (0, 5), r1.output

    with open_db(readonly=True) as conn:
        first_ids = {
            row[0]
            for row in conn.execute("SELECT finding_id_str FROM findings WHERE source_detector = 'critique'").fetchall()
        }
        first_count = len(first_ids)
    assert first_count >= 1

    r2 = _run_critique_persist(
        indexed_project,
        _TINY_DIFF,
        "--high-callers",
        "1",
        "--intent",
        "remove dead code from the user model",
    )
    assert r2.exit_code in (0, 5), r2.output

    with open_db(readonly=True) as conn:
        second_ids = {
            row[0]
            for row in conn.execute("SELECT finding_id_str FROM findings WHERE source_detector = 'critique'").fetchall()
        }
        second_count = len(second_ids)
    assert second_count == first_count, "row count drifted across reruns"
    assert second_ids == first_ids, "finding_id_str set changed across reruns"


def test_critique_no_persist_does_not_emit_findings(indexed_project):
    """Without --persist, critique stays side-effect-free."""
    # First, clear any existing critique rows so the assertion is exact.
    with open_db(readonly=False) as conn:
        try:
            conn.execute("DELETE FROM findings WHERE source_detector = 'critique'")
            conn.commit()
        except sqlite3.OperationalError:
            pass

    runner = CliRunner()
    old_cwd = os.getcwd()
    try:
        os.chdir(str(indexed_project))
        result = runner.invoke(
            cli,
            ["critique", "--high-callers", "1", "--intent", "remove things"],
            input=_TINY_DIFF,
            catch_exceptions=False,
        )
    finally:
        os.chdir(old_cwd)
    assert result.exit_code in (0, 5), result.output

    with open_db(readonly=True) as conn:
        try:
            count = conn.execute("SELECT COUNT(*) FROM findings WHERE source_detector = 'critique'").fetchone()[0]
        except sqlite3.OperationalError:
            count = 0
    assert count == 0, "non-persist critique still wrote to findings"


def test_critique_persist_no_findings_table_no_crash(indexed_project):
    """``critique --persist`` degrades cleanly when the findings table is absent.

    Simulates the pre-W89 schema by DROP-ing ``findings`` after init but
    before the persist call. The standard analysis path (which the
    JSON / text envelope still has to produce) must keep working — the
    command exits without crashing.
    """
    # Drop the findings table to simulate pre-W89 schema.
    with open_db(readonly=False) as conn:
        conn.execute("DROP TABLE IF EXISTS findings")
        conn.commit()

    result = _run_critique_persist(
        indexed_project,
        _TINY_DIFF,
        "--high-callers",
        "1",
        "--intent",
        "remove things",
    )
    # Must succeed (or hit the gate exit) despite the missing findings table.
    assert result.exit_code in (0, 5), result.output


# ---------------------------------------------------------------------------
# Diff-sha differentiation (W134 audit-trail invariant)
# ---------------------------------------------------------------------------


def test_critique_different_diff_gets_fresh_finding_id(indexed_project):
    """Changing the diff's file set writes a NEW row, not an upsert.

    The audit-trail design: prior diff's critique rows stay in the
    registry so a consumer can compare critique runs across iterations
    of the same branch. Only a rerun against the *same* diff (same file
    set + same intent text + same input source) upserts.
    """
    # First diff: touch models.py only.
    r1 = _run_critique_persist(
        indexed_project,
        _TINY_DIFF,
        "--high-callers",
        "1",
        "--intent",
        "remove dead code from the user model",
    )
    assert r1.exit_code in (0, 5), r1.output

    with open_db(readonly=True) as conn:
        first_count = conn.execute("SELECT COUNT(*) FROM findings WHERE source_detector = 'critique'").fetchone()[0]
    assert first_count >= 1

    # Second diff: touch a different file (utils.py instead).
    second_diff = textwrap.dedent(
        """\
        --- a/src/utils.py
        +++ b/src/utils.py
        @@ -1,3 +1,4 @@ def format_name(first, last):
         def format_name(first, last):
             \"\"\"Format a full name.\"\"\"
             return f"{first} {last}"
        +    # new comment
        """
    )

    r2 = _run_critique_persist(
        indexed_project,
        second_diff,
        "--high-callers",
        "1",
        "--intent",
        "remove dead code from utils",
    )
    assert r2.exit_code in (0, 5), r2.output

    with open_db(readonly=True) as conn:
        second_count = conn.execute("SELECT COUNT(*) FROM findings WHERE source_detector = 'critique'").fetchone()[0]
        # Distinct diff_sha values across all rows -> at least 2.
        rows = conn.execute("SELECT evidence_json FROM findings WHERE source_detector = 'critique'").fetchall()
    assert second_count > first_count, "expected the second diff to insert fresh rows on top of the first"
    diff_shas = {json.loads(r[0]).get("diff_sha") for r in rows}
    assert len(diff_shas) >= 2, f"expected at least two distinct diff_sha values, got {diff_shas}"


# ---------------------------------------------------------------------------
# Visibility through the read-side CLI (`roam findings`)
# ---------------------------------------------------------------------------


def test_critique_findings_visible_via_cmd_findings_list(indexed_project):
    """`roam findings list --detector critique` returns rows after migration."""
    r = _run_critique_persist(
        indexed_project,
        _TINY_DIFF,
        "--high-callers",
        "1",
        "--intent",
        "remove things",
    )
    assert r.exit_code in (0, 5), r.output

    runner = CliRunner()
    old_cwd = os.getcwd()
    try:
        os.chdir(str(indexed_project))
        result = runner.invoke(cli, ["--json", "findings", "list", "--detector", "critique"])
    finally:
        os.chdir(old_cwd)
    assert result.exit_code == 0, result.output
    envelope = json.loads(result.output)
    assert envelope["command"] == "findings-list"
    assert envelope["summary"]["state"] == "populated"
    assert envelope["summary"]["total_findings"] >= 1
    assert "critique" in envelope["summary"]["detectors"]
    assert all(r["source_detector"] == "critique" for r in envelope["findings"])


def test_critique_findings_visible_via_cmd_findings_count(indexed_project):
    """`roam findings count` includes a non-zero entry for critique."""
    r = _run_critique_persist(
        indexed_project,
        _TINY_DIFF,
        "--high-callers",
        "1",
        "--intent",
        "remove things",
    )
    assert r.exit_code in (0, 5), r.output
    assert_detector_visible_in_findings_count(indexed_project, "critique")
