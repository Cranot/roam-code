"""W1471 — a high-water mark that cannot be read is not a mark of zero.

W1460 closed the FILE-level route to a laundered ratchet ceiling: a genuinely
absent baseline is a first generation, and anything present-but-unreadable is
a refusal that leaves the file alone. It left the FIELD-level route open.

``_carried_mark`` validated the recorded mark with a bare
``isinstance(mark, int)`` and fell back to the live violation count on every
way of failing it. So a ratchet file that parsed perfectly well — but whose
``_high_water_mark`` was missing, or spelled ``"68"`` / ``68.0`` / ``null`` —
silently reset the ceiling to today's count and exited 0.

Two things make that worse than the zero-byte file W1460 closed:

* the zero-byte file REFUSED. This one SUCCEEDED, with no diagnostic.
* the *rationale* was carried forward intact, so the regenerated file paired
  today's small count with the prose written to justify a much larger mark.
  The diff reads as a reviewed ratchet. The ceiling is simply gone.

The contract pinned here: only a genuinely absent FILE is a first
generation. A file that is present must carry a non-negative integer mark
AND a non-empty rationale, or ``--baseline`` refuses and writes nothing.

Negative controls (the half that makes the rest mean something)
---------------------------------------------------------------
A refusal that fires on everything is not a guard, it is a broken command.
:func:`test_a_wellformed_ratchet_still_regenerates` and
:func:`test_an_absent_ratchet_is_still_a_first_generation` pin the two states
that MUST still succeed, and
:func:`test_the_live_repo_ratchet_regenerates_byte_identically` pins that the
rule and the tree it ships with agree — the tightened rule and the artifact
satisfying it land together, or the rule lands last.

``bool`` gets its own case because ``isinstance(True, int)`` is true in
Python: a mark of ``True`` compares as 1, pinning the ratchet at a single
entry via a typo that every ``isinstance`` check in the file would wave
through.
"""

from __future__ import annotations

import json
import pathlib
import shutil
import subprocess
import sys

import pytest

# W572/W588 — ask git for the canonical toplevel rather than walking
# parents[], which lands on the worktree root under nested dispatch and would
# point this suite at a scanner that is not the one under test.
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from _helpers.repo_root import repo_root  # noqa: E402

REPO_ROOT = repo_root()
SCANNER = REPO_ROOT / "scripts" / "scan_disclosure_asymmetry.py"
FIXTURE_SRC = REPO_ROOT / "tests" / "fixtures" / "scanner_positive_controls" / "disclosure"

sys.path.insert(0, str(REPO_ROOT / "scripts"))
import scan_disclosure_asymmetry as disclosure  # noqa: E402

#: A mark that is not the live violation count of the planted corpus, so
#: "carried forward" and "recomputed from today's tree" can never be
#: mistaken for one another.
SEEDED_MARK = 99
SEEDED_NOTE = "Raised to 99 on 2026-08-04 because <a real reason lives here>. W1471 fixture rationale."

#: An entry the planted corpus does NOT reproduce. Its disappearance proves
#: the regeneration actually ran; without it, "the mark survived" is also
#: satisfied by a scanner that never opened the file at all.
STALE_ENTRY: dict[str, object] = {
    "command": "a_command_that_no_longer_exists",
    "token": "warnings_out",
    "observed_by": ["json"],
    "blind": ["text"],
    "module": "cmd_deleted_last_year.py",
    "reason": "json-only-warnings-bucket",
}


def _plant_repo(tmp_path: pathlib.Path) -> tuple[pathlib.Path, pathlib.Path]:
    """A self-contained repo the scanner can regenerate inside.

    ``_repo_root()`` is ``parents[1]`` of the scanner file, so copying the
    script into ``<tmp>/scripts/`` makes ``<tmp>`` the root and keeps the real
    repository — which other agents are editing — entirely out of it.
    """
    repo = tmp_path / "repo"
    (repo / "scripts").mkdir(parents=True)
    shutil.copy2(SCANNER, repo / "scripts" / "scan_disclosure_asymmetry.py")

    fixture = repo / "tests" / "fixtures" / "scanner_positive_controls" / "disclosure"
    fixture.mkdir(parents=True)
    for src in sorted(FIXTURE_SRC.glob("*.py")):
        shutil.copy2(src, fixture / src.name)

    (repo / "tests" / "data").mkdir(parents=True)
    return repo, fixture


def _ratchet(repo: pathlib.Path) -> pathlib.Path:
    return repo / "tests" / "data" / "disclosure_asymmetry_baseline.json"


def _seed(repo: pathlib.Path, payload: dict) -> pathlib.Path:
    target = _ratchet(repo)
    target.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return target


def _wellformed(**overrides: object) -> dict:
    """A ratchet that regenerates cleanly, minus whatever a case breaks."""
    payload: dict[str, object] = {
        "_comment": "W1471 seeded ratchet",
        "_regenerate": "python scripts/scan_disclosure_asymmetry.py --baseline",
        "_high_water_mark": SEEDED_MARK,
        "_high_water_mark_note": SEEDED_NOTE,
        "_reason_codes": {"json-only-warnings-bucket": "seeded"},
        "violations": [STALE_ENTRY],
    }
    payload.update(overrides)
    return payload


def _regenerate(repo: pathlib.Path, fixture: pathlib.Path) -> subprocess.CompletedProcess[str]:
    """Run the regenerate command EXACTLY as documented — no redirect."""
    return subprocess.run(
        [sys.executable, str(repo / "scripts" / "scan_disclosure_asymmetry.py"), "--baseline", "--root", str(fixture)],
        cwd=str(repo),
        capture_output=True,
        text=True,
        check=False,
    )


# ---------------------------------------------------------------------------
# 1. The falsifier: an unusable mark must never be silently recomputed
# ---------------------------------------------------------------------------

#: Every way of writing a mark that ``isinstance(mark, int)`` alone let
#: through to the ``count`` fallback. ``True`` is the subtle one.
UNUSABLE_MARKS: list[tuple[str, dict[str, object]]] = [
    ("key absent", {}),
    ("string", {"_high_water_mark": "99"}),
    ("float", {"_high_water_mark": 99.0}),
    ("null", {"_high_water_mark": None}),
    ("bool", {"_high_water_mark": True}),
    ("negative", {"_high_water_mark": -1}),
    ("list", {"_high_water_mark": [99]}),
]


@pytest.mark.parametrize(("label", "override"), UNUSABLE_MARKS, ids=[c[0] for c in UNUSABLE_MARKS])
def test_an_unusable_mark_refuses_and_leaves_the_ratchet_alone(
    tmp_path: pathlib.Path, label: str, override: dict[str, object]
) -> None:
    """Pre-fix this SUCCEEDS with the mark reset to the live count.

    The assertions are ordered so the failure message names the defect: the
    exit code first (pre-fix: 0), then the file's own contents (pre-fix: the
    mark is gone and replaced by ``len(violations)``).
    """
    repo, fixture = _plant_repo(tmp_path)
    payload = _wellformed()
    payload.pop("_high_water_mark", None)
    payload.update(override)
    target = _seed(repo, payload)
    before = target.read_text(encoding="utf-8")

    result = _regenerate(repo, fixture)

    assert result.returncode != 0, (
        f"W1471: a ratchet whose _high_water_mark is {label} regenerated SUCCESSFULLY. "
        "A mark the scanner cannot read is not a mark of zero — it must refuse, "
        f"not recompute the ceiling from today's tree. stderr={result.stderr!r}"
    )
    assert "REFUSING" in result.stderr, f"the refusal must say so on stderr; got {result.stderr!r}"
    assert target.read_text(encoding="utf-8") == before, (
        "a refused regeneration must leave the existing ratchet byte-identical — "
        "W1460's lesson was that turning silent data loss into loud data loss "
        "moves the defect rather than removing it"
    )


def test_the_reset_mark_is_never_written_with_a_carried_rationale(tmp_path: pathlib.Path) -> None:
    """The specific shape that made this worse than a corrupt file.

    A reset mark paired with the ORIGINAL rationale produces a file that reads
    as reviewed: today's count, explained by the prose written to justify a
    much larger one. Pinned separately from the exit code because this is the
    property a human reviewing the diff would rely on.
    """
    repo, fixture = _plant_repo(tmp_path)
    payload = _wellformed()
    del payload["_high_water_mark"]
    target = _seed(repo, payload)

    _regenerate(repo, fixture)

    after = json.loads(target.read_text(encoding="utf-8"))
    live_count = len(after["violations"])
    assert not (after.get("_high_water_mark") == live_count and after.get("_high_water_mark_note") == SEEDED_NOTE), (
        "the ratchet was rewritten with the ceiling reset to today's count "
        f"({live_count}) while still carrying the rationale for {SEEDED_MARK}. "
        "That file reads as reviewed and is not."
    )


def test_a_present_mark_with_no_rationale_refuses(tmp_path: pathlib.Path) -> None:
    """A number carried forward with no reason beside it is the banned state.

    ``_UNEXPLAINED_MARK`` clears ``test_a_raised_high_water_mark_says_why``'s
    length floor, so silently substituting it would launder a dropped
    justification straight past the guard written to catch it.
    """
    repo, fixture = _plant_repo(tmp_path)
    for missing_note in ({}, {"_high_water_mark_note": ""}, {"_high_water_mark_note": "   "}):
        payload = _wellformed()
        del payload["_high_water_mark_note"]
        payload.update(missing_note)
        target = _seed(repo, payload)
        before = target.read_text(encoding="utf-8")

        result = _regenerate(repo, fixture)

        assert result.returncode != 0, (
            f"a mark of {SEEDED_MARK} with note={missing_note!r} regenerated successfully; "
            "the mark and its rationale are refused together"
        )
        assert target.read_text(encoding="utf-8") == before


def test_a_non_object_ratchet_refuses_instead_of_crashing(tmp_path: pathlib.Path) -> None:
    """A JSON array parses fine, then ``.get`` raises AttributeError.

    That escapes ``emit_baseline``'s ``except (OSError, ValueError)`` and
    reaches the user as a traceback rather than a refusal.
    """
    repo, fixture = _plant_repo(tmp_path)
    target = _ratchet(repo)
    target.write_text("[]\n", encoding="utf-8")

    result = _regenerate(repo, fixture)

    assert result.returncode != 0
    assert "REFUSING" in result.stderr, (
        f"a non-object ratchet must be refused, not crash the scanner; stderr={result.stderr!r}"
    )
    assert "Traceback" not in result.stderr, f"the scanner crashed instead of refusing: {result.stderr!r}"


# ---------------------------------------------------------------------------
# 2. Negative controls — the states that MUST still succeed
# ---------------------------------------------------------------------------


def test_a_wellformed_ratchet_still_regenerates(tmp_path: pathlib.Path) -> None:
    """The guard must not fire on a good file.

    Also pins that the regeneration really ran: the stale entry the planted
    corpus cannot reproduce is gone, and the mark is carried rather than
    recomputed.
    """
    repo, fixture = _plant_repo(tmp_path)
    target = _seed(repo, _wellformed())

    result = _regenerate(repo, fixture)

    assert result.returncode == 0, f"a well-formed ratchet must regenerate; stderr={result.stderr!r}"
    after = json.loads(target.read_text(encoding="utf-8"))
    assert after["_high_water_mark"] == SEEDED_MARK
    assert after["_high_water_mark_note"] == SEEDED_NOTE
    assert STALE_ENTRY not in after["violations"], "the regeneration did not actually run"
    assert after["_high_water_mark"] != len(after["violations"]), (
        "the mark must be READ from the file, not recomputed from the tree"
    )


def test_an_absent_ratchet_is_still_a_first_generation(tmp_path: pathlib.Path) -> None:
    """The refusal must not be blanket: no file at all is still generation one."""
    repo, fixture = _plant_repo(tmp_path)
    assert not _ratchet(repo).exists()

    result = _regenerate(repo, fixture)

    assert result.returncode == 0, f"an absent ratchet is a first generation; stderr={result.stderr!r}"
    after = json.loads(_ratchet(repo).read_text(encoding="utf-8"))
    assert after["_high_water_mark"] == len(after["violations"])
    assert after["_high_water_mark_note"] == disclosure._UNEXPLAINED_MARK


def test_carried_mark_returns_the_recorded_pair_for_a_good_file(tmp_path: pathlib.Path) -> None:
    """Unit-level control on the helper itself, independent of the CLI."""
    repo, _fixture = _plant_repo(tmp_path)
    _seed(repo, _wellformed())

    mark, note = disclosure._carried_mark(count=3, root=repo)

    assert (mark, note) == (SEEDED_MARK, SEEDED_NOTE)


# ---------------------------------------------------------------------------
# 3. The rule and the tree it ships with must agree
# ---------------------------------------------------------------------------


def test_the_live_repo_ratchet_satisfies_the_tightened_rule() -> None:
    """The committed ratchet must pass the rule landing in the same commit.

    A stricter rule committed without the artifact satisfying it is how main
    went red earlier in this campaign. This is the cheap standing check that
    the two never separate again.
    """
    recorded = json.loads(disclosure.baseline_path().read_text(encoding="utf-8"))
    mark = recorded.get("_high_water_mark")
    note = recorded.get("_high_water_mark_note")

    assert isinstance(mark, int) and not isinstance(mark, bool) and mark >= 0, (
        f"the committed ratchet carries an unusable _high_water_mark: {mark!r}"
    )
    assert isinstance(note, str) and note.strip(), f"the committed ratchet carries no rationale: {note!r}"
    assert note != disclosure._UNEXPLAINED_MARK, (
        "the committed ratchet carries the first-generation placeholder rather than "
        "a real rationale. The placeholder is long enough to clear "
        "test_a_raised_high_water_mark_says_why's length floor, so it is exactly "
        "the state that guard cannot see."
    )
