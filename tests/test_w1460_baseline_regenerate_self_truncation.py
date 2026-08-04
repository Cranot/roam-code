"""W1460 — the documented way to regenerate a ratchet must regenerate the ratchet.

THE DEFECT
----------
``scripts/scan_disclosure_asymmetry.py --baseline`` is the documented
remediation for the W1331 ratchet, named in the module docstring, in the
``--baseline`` help, and inside the emitted file itself as ``_regenerate``.
Measured at ``4a358387``, run verbatim, it:

* exits **0**,
* prints the ratchet to **stdout**,
* and leaves ``tests/data/disclosure_asymmetry_baseline.json`` **byte-identical**.

A remediation that reports success and changes nothing is worse than one that
fails, because the operator moves on. The only way to make it do what it says
is to add the obvious redirect --- and that is the trap:

    python scripts/scan_disclosure_asymmetry.py --baseline > tests/data/disclosure_asymmetry_baseline.json

The shell opens and TRUNCATES the redirect target before the process starts.
``_carried_mark`` then reads its own high-water mark out of a zero-byte file,
``json.loads("")`` raises, and at ``4a358387`` the scanner refuses (exit 2) ---
leaving the ratchet destroyed on disk, with mark, rationale, entries and
reason-code table gone. Deleting the "corrupt" file and re-running fails
identically, because the redirect recreates it empty every time. The command
can never succeed.

WHAT IS AND IS NOT NEW HERE
---------------------------
The SILENT reset --- mark quietly replaced by today's count, rationale
replaced by the ``_UNEXPLAINED_MARK`` placeholder --- was fixed earlier, at
``c17d4325``, by making the unreadable-ratchet case propagate instead of
falling back. That fix converted silent data loss into LOUD data loss; it did
not remove the trap. The tests below are therefore honest about which arm is
a falsifier and which is a regression pin:

* ``test_documented_regenerate_command_rewrites_the_ratchet`` and
  ``test_regenerate_preserves_the_mark_and_its_rationale`` FAIL against
  ``4a358387`` --- the no-op is the defect being fixed.
* ``test_legacy_redirect_form_never_writes_a_reset_mark`` PASSES against
  ``4a358387``. It pins ``c17d4325``'s refusal so a future rewrite of the
  emit path cannot re-open the silent reset. It is labelled, not claimed.

Nothing inside the process can undo a truncation the shell performed before
``exec``, which is why the fix removes the redirect from the contract rather
than defending against it: ``--baseline`` writes the file itself and puts
nothing on stdout. A ``git``-history fallback for the mark was considered and
rejected --- it would silently RAISE a mark that had been legitimately lowered
in an uncommitted edit, which is the laundering ``_carried_mark`` exists to
prevent, running the other way.

EVERY ARM HAS A NEGATIVE CONTROL
--------------------------------
"the mark survived" is satisfied by a scanner that does nothing at all ---
which is precisely the pre-fix behaviour --- so survival is never asserted
alone. It is always asserted beside proof that the run DID the work: a stale
entry planted in the seeded ratchet must be gone, and the emitted violation
set must equal the live scan. ``test_the_mark_is_read_not_invented`` runs the
same path over two different seeded marks and requires the output to track the
seed, so a hardcoded constant fails. ``test_an_absent_ratchet_is_a_first
_generation`` requires the refusal NOT to be blanket.
"""

from __future__ import annotations

import json
import pathlib
import shutil
import subprocess
import sys

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
SCANNER = REPO_ROOT / "scripts" / "scan_disclosure_asymmetry.py"
FIXTURE_SRC = REPO_ROOT / "tests" / "fixtures" / "scanner_positive_controls" / "disclosure"

sys.path.insert(0, str(REPO_ROOT / "scripts"))
import scan_disclosure_asymmetry as disclosure  # noqa: E402

#: The single sanctioned place the trap command may still appear in prose.
_HISTORICAL_MARKER = "W1460-HISTORICAL"

#: A mark that is not the live violation count, so "carried" and "recomputed"
#: can never be confused for one another.
SEEDED_MARK = 99
SEEDED_NOTE = "Raised to 99 on 2026-08-04 because <a real reason lives here>. W1460 fixture."

#: An entry that the live tree does NOT produce. Its disappearance is the
#: proof that the regeneration actually ran; without it, "the mark survived"
#: is satisfied by a scanner that never opened the file.
STALE_ENTRY: dict[str, object] = {
    "command": "a_command_that_no_longer_exists",
    "token": "warnings_out",
    "observed_by": ["json"],
    "blind": ["text"],
    "module": "cmd_deleted_last_year.py",
    "reason": "json-only-warnings-bucket",
}


def _plant_repo(tmp_path: pathlib.Path, scanner_source: str) -> tuple[pathlib.Path, pathlib.Path]:
    """A self-contained repo the scanner can regenerate inside.

    ``_repo_root()`` is ``parents[1]`` of the scanner file, so copying the
    script into ``<tmp>/scripts/`` makes ``<tmp>`` the root and keeps the real
    repository --- which other agents are editing --- entirely out of it.
    """
    repo = tmp_path / "repo"
    (repo / "scripts").mkdir(parents=True)
    (repo / "scripts" / "scan_disclosure_asymmetry.py").write_text(scanner_source, encoding="utf-8")

    fixture = repo / "tests" / "fixtures" / "scanner_positive_controls" / "disclosure"
    fixture.mkdir(parents=True)
    for src in sorted(FIXTURE_SRC.glob("*.py")):
        shutil.copy2(src, fixture / src.name)

    (repo / "tests" / "data").mkdir(parents=True)
    return repo, fixture


def _seed_ratchet(repo: pathlib.Path, *, mark: int = SEEDED_MARK, note: str = SEEDED_NOTE) -> pathlib.Path:
    target = repo / "tests" / "data" / "disclosure_asymmetry_baseline.json"
    target.write_text(
        json.dumps(
            {
                "_comment": "W1460 seeded ratchet",
                "_regenerate": "python scripts/scan_disclosure_asymmetry.py --baseline",
                "_high_water_mark": mark,
                "_high_water_mark_note": note,
                "_reason_codes": {"json-only-warnings-bucket": "seeded"},
                "violations": [STALE_ENTRY],
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return target


def _run_documented(repo: pathlib.Path, fixture: pathlib.Path) -> subprocess.CompletedProcess[str]:
    """Run the regenerate command EXACTLY as documented --- no redirect."""
    return subprocess.run(
        [sys.executable, str(repo / "scripts" / "scan_disclosure_asymmetry.py"), "--baseline", "--root", str(fixture)],
        cwd=str(repo),
        capture_output=True,
        text=True,
        check=False,
    )


def _run_legacy_redirect(repo: pathlib.Path, fixture: pathlib.Path, target: pathlib.Path) -> int:
    """Run the trap form: stdout redirected onto the ratchet the scan reads."""
    with target.open("w", encoding="utf-8") as sink:  # exactly what ``>`` does, and when
        return subprocess.run(
            [
                sys.executable,
                str(repo / "scripts" / "scan_disclosure_asymmetry.py"),
                "--baseline",
                "--root",
                str(fixture),
            ],
            cwd=str(repo),
            stdout=sink,
            stderr=subprocess.DEVNULL,
            text=True,
            check=False,
        ).returncode


def _prefix_source() -> str:
    return subprocess.run(
        ["git", "show", "HEAD:scripts/scan_disclosure_asymmetry.py"],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        check=True,
    ).stdout


# ---------------------------------------------------------------------------
# 1. The falsifier: the documented command must DO something
# ---------------------------------------------------------------------------


def test_documented_regenerate_command_rewrites_the_ratchet(tmp_path: pathlib.Path) -> None:
    """Run it verbatim; the file on disk must change to match the live scan.

    Pre-fix this fails on the first assertion: the command exits 0, prints the
    ratchet to stdout, and the stale entry is still sitting in the file.
    """
    repo, fixture = _plant_repo(tmp_path, SCANNER.read_text(encoding="utf-8"))
    target = _seed_ratchet(repo)
    before = target.read_bytes()

    result = _run_documented(repo, fixture)
    assert result.returncode == disclosure.EXIT_OK, f"stderr:\n{result.stderr}"

    after = target.read_bytes()
    assert after != before, (
        "the documented regenerate command left the ratchet byte-identical. "
        "A remediation that reports success and changes nothing is the defect."
    )

    payload = json.loads(after)
    entries = payload["violations"]
    assert STALE_ENTRY not in entries, "a stale entry survived regeneration — the file was not actually rebuilt"
    assert entries, "the planted sentinel asymmetry is missing — the fixture stopped exercising the scan"


def test_regenerate_preserves_the_mark_and_its_rationale(tmp_path: pathlib.Path) -> None:
    """The brief's assertion, but only on a run that provably did the work.

    Asserting mark-survival alone would pass against pre-fix code, which
    survives the mark by never touching the file. The rebuild proof comes
    first, in the same test, so the pair cannot be satisfied by inaction.
    """
    repo, fixture = _plant_repo(tmp_path, SCANNER.read_text(encoding="utf-8"))
    target = _seed_ratchet(repo)

    result = _run_documented(repo, fixture)
    assert result.returncode == disclosure.EXIT_OK, f"stderr:\n{result.stderr}"
    payload = json.loads(target.read_text(encoding="utf-8"))

    # Proof the run did the work (negative control for the two below).
    assert STALE_ENTRY not in payload["violations"]

    assert payload["_high_water_mark"] == SEEDED_MARK, (
        f"the high-water mark was not carried forward: {payload['_high_water_mark']} != {SEEDED_MARK}. "
        "A ratchet that loses its own ceiling on regeneration quietly gets easier."
    )
    assert payload["_high_water_mark_note"] == SEEDED_NOTE, (
        "the rationale was replaced. A number in a ratchet file with no reason beside it "
        "is indistinguishable from someone raising it to make CI green."
    )
    assert payload["_high_water_mark_note"] != disclosure._UNEXPLAINED_MARK, (
        "the mark carries the first-generation placeholder — the unexplained-mark path fired "
        "on a regeneration over an EXISTING ratchet"
    )


def test_baseline_writes_nothing_to_stdout(tmp_path: pathlib.Path) -> None:
    """The property that removes the redirect from the contract.

    While ``--baseline`` prints the artifact, ``> the_baseline_file`` remains
    the obvious thing to type, and typing it destroys the file. Empty stdout
    is what makes the trap unavailable rather than merely undocumented.
    """
    repo, fixture = _plant_repo(tmp_path, SCANNER.read_text(encoding="utf-8"))
    _seed_ratchet(repo)

    result = _run_documented(repo, fixture)

    assert result.returncode == disclosure.EXIT_OK, f"stderr:\n{result.stderr}"
    assert result.stdout == "", (
        f"--baseline still emits an artifact to stdout, so the redirect stays tempting: {result.stdout[:200]!r}"
    )
    assert result.stderr.strip(), "a regeneration that writes a file must say so somewhere"


def test_no_documented_form_of_the_command_carries_a_redirect() -> None:
    """The trap must not be re-documented anywhere the operator reads.

    Three places named the command at ``4a358387``: the USAGE block, the
    ``--baseline`` help, and ``_regenerate`` inside the emitted file. A
    redirect in any of them re-arms it.

    The docstring's account of the historical trap has to quote the bad
    command, so it carries an explicit ``W1460-HISTORICAL`` marker. That
    marker is the ONLY exemption, and it is checked to still exist below ---
    an exemption nobody can see is how a lint stops linting.
    """
    source = SCANNER.read_text(encoding="utf-8")
    assert source.count(_HISTORICAL_MARKER) == 1, (
        f"expected exactly one {_HISTORICAL_MARKER} exemption; a second one means the trap "
        "was re-documented under cover of the marker"
    )
    for line in source.splitlines():
        if "scan_disclosure_asymmetry.py --baseline" not in line:
            continue
        if _HISTORICAL_MARKER in line:
            continue
        assert ">" not in line.split("--baseline", 1)[1], (
            f"a documented form of the regenerate command still carries a shell redirect: {line.strip()!r}"
        )


# ---------------------------------------------------------------------------
# 2. Negative controls
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("seeded", [7, 42, SEEDED_MARK])
def test_the_mark_is_read_not_invented(tmp_path: pathlib.Path, seeded: int) -> None:
    """Carrying must track the recorded value, not a constant and not the count.

    A "fix" that hardcodes the mark, or that always echoes the live violation
    count, satisfies a single-value assertion. Three seeds do not.
    """
    repo, fixture = _plant_repo(tmp_path, SCANNER.read_text(encoding="utf-8"))
    target = _seed_ratchet(repo, mark=seeded, note=f"seeded {seeded}")

    result = _run_documented(repo, fixture)
    assert result.returncode == disclosure.EXIT_OK, f"stderr:\n{result.stderr}"

    payload = json.loads(target.read_text(encoding="utf-8"))
    # Rebuild proof first: without it this whole test passes against pre-fix
    # code, which preserves every seeded mark by never opening the file.
    assert STALE_ENTRY not in payload["violations"]
    assert payload["_high_water_mark"] == seeded
    assert payload["_high_water_mark_note"] == f"seeded {seeded}"
    assert payload["_high_water_mark"] != len(payload["violations"]), (
        "the seeded marks are chosen to differ from the live count; if they stop differing "
        "this test can no longer tell carrying from recomputing"
    )


def test_an_absent_ratchet_is_still_a_first_generation(tmp_path: pathlib.Path) -> None:
    """The refusal must not be blanket.

    A guard that refuses everything passes every "it did not lose the mark"
    assertion. Bootstrapping from no file at all must still work, and must
    say --- via the placeholder --- that the mark has no rationale yet.
    """
    repo, fixture = _plant_repo(tmp_path, SCANNER.read_text(encoding="utf-8"))
    target = repo / "tests" / "data" / "disclosure_asymmetry_baseline.json"
    assert not target.exists()

    result = _run_documented(repo, fixture)

    assert result.returncode == disclosure.EXIT_OK, f"stderr:\n{result.stderr}"
    payload = json.loads(target.read_text(encoding="utf-8"))
    assert payload["_high_water_mark"] == len(payload["violations"])
    assert payload["_high_water_mark_note"] == disclosure._UNEXPLAINED_MARK


def test_an_unreadable_ratchet_refuses_and_writes_nothing(tmp_path: pathlib.Path) -> None:
    """Present-but-corrupt is not a first generation, and must not be rewritten.

    The refusal has to leave the file alone: overwriting it would destroy the
    only copy of a mark the operator still has to recover by hand.
    """
    repo, fixture = _plant_repo(tmp_path, SCANNER.read_text(encoding="utf-8"))
    target = repo / "tests" / "data" / "disclosure_asymmetry_baseline.json"
    target.write_text("{ this is not json", encoding="utf-8")
    before = target.read_bytes()

    result = _run_documented(repo, fixture)

    assert result.returncode == disclosure.EXIT_UNANALYZABLE, f"stderr:\n{result.stderr}"
    assert target.read_bytes() == before, "a refused regeneration overwrote the ratchet anyway"
    assert "git checkout" in result.stderr, (
        "the refusal must name the recovery; the operator got here by following the docs"
    )


def test_no_temp_file_is_left_behind(tmp_path: pathlib.Path) -> None:
    """The atomic write must not litter the data directory.

    A scanner that writes nothing also leaves no temp files, so the rebuild
    proof is asserted here too — otherwise this passes pre-fix.
    """
    repo, fixture = _plant_repo(tmp_path, SCANNER.read_text(encoding="utf-8"))
    target = _seed_ratchet(repo)

    assert _run_documented(repo, fixture).returncode == disclosure.EXIT_OK
    assert STALE_ENTRY not in json.loads(target.read_text(encoding="utf-8"))["violations"]

    leftovers = sorted(p.name for p in (repo / "tests" / "data").iterdir() if p.name.endswith(".tmp"))
    assert leftovers == [], f"the atomic write left temp files behind: {leftovers}"


def test_the_ratchet_is_written_with_lf_endings(tmp_path: pathlib.Path) -> None:
    """Owning the write means owning the line endings.

    The shell redirect this replaces inherited whatever the shell did; a
    text-mode ``write_text`` on Windows emits CRLF and rewrites every line of
    a committed file on each regeneration. Caught by measurement: the first
    build of the fix produced 62 CRLF lines and 0 LF, turning a four-entry
    ratchet into a whole-file diff.
    """
    repo, fixture = _plant_repo(tmp_path, SCANNER.read_text(encoding="utf-8"))
    target = _seed_ratchet(repo)

    assert _run_documented(repo, fixture).returncode == disclosure.EXIT_OK

    raw = target.read_bytes()
    assert STALE_ENTRY not in json.loads(raw)["violations"]
    assert b"\r\n" not in raw, "the regenerated ratchet carries CRLF; every line would churn in the diff"
    assert raw.endswith(b"}\n"), "the ratchet must end with exactly one newline, as the previous emitter did"


# ---------------------------------------------------------------------------
# 3. Regression pin (NOT a falsifier — passes pre-fix, and says so)
# ---------------------------------------------------------------------------


def test_legacy_redirect_form_never_writes_a_reset_mark(tmp_path: pathlib.Path) -> None:
    """Muscle memory still destroys the file; it must never LAUNDER the mark.

    This arm passes against ``4a358387`` as well: ``c17d4325`` already made
    the unreadable-ratchet case propagate instead of falling back to today's
    count. It is pinned here because the emit path is being rewritten around
    it, and the failure it guards --- a ratchet whose ceiling silently becomes
    the current count, rationale replaced by the placeholder --- is invisible
    in a diff and permanent once committed.

    What it does NOT claim: that the redirect is safe. The shell truncates
    before ``exec``; the file is destroyed either way. Only the LAUNDERING is
    excluded.
    """
    repo, fixture = _plant_repo(tmp_path, SCANNER.read_text(encoding="utf-8"))
    target = _seed_ratchet(repo)

    returncode = _run_legacy_redirect(repo, fixture, target)

    assert returncode != disclosure.EXIT_OK, "the trap form must never report success"
    raw = target.read_text(encoding="utf-8")
    if raw.strip():
        payload = json.loads(raw)
        assert payload["_high_water_mark"] == SEEDED_MARK
        assert payload["_high_water_mark_note"] != disclosure._UNEXPLAINED_MARK


def test_prefix_scanner_is_a_no_op_under_the_documented_command(tmp_path: pathlib.Path) -> None:
    """The falsifier for this whole file: pre-fix code leaves the ratchet alone.

    If this ever stops failing to rewrite --- i.e. if HEAD already carries the
    fix --- the arm is history and skips, rather than silently asserting
    nothing.
    """
    source = _prefix_source()
    if "def emit_baseline(" in source:
        pytest.skip("HEAD already carries the W1460 fix; the pre-fix arm is history")

    repo, fixture = _plant_repo(tmp_path, source)
    target = _seed_ratchet(repo)
    before = target.read_bytes()

    result = _run_documented(repo, fixture)

    assert result.returncode == 0, "pre-fix, the documented command reports success"
    assert target.read_bytes() == before, (
        "the pre-fix corpus must show the ratchet UNCHANGED, or it is not exercising the defect"
    )
    assert result.stdout.strip().startswith("{"), (
        "pre-fix, the artifact went to stdout — that is what invited the redirect"
    )
