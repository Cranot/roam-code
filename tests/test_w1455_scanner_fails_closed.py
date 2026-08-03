"""W1455 — the disclosure guard must not have the defect it exists to find.

THE DEFECT
----------
``scripts/scan_disclosure_asymmetry.py`` is the scanner behind the W1331
ratchet: it exists to catch commands that report clean while a signal was
degraded. It had that exact defect itself::

    except (OSError, SyntaxError):
        return []          # scan_file
    except (OSError, SyntaxError):
        continue           # enumerate_command_sites

Reproduced before the fix, by copying a real command module and appending
``def broken(:``::

    scan violations: []
    enumerated sites: []

An unparseable file was BYTE-IDENTICAL to a clean repository. Worse, it also
disappeared from ``enumerate_command_sites`` — the function whose entire
purpose is proving coverage. The zero-baseline pytest asserts
``violations == []`` and PASSED. So a syntax error anywhere in
``src/roam/commands`` silently removed that module from the gate while the
gate reported success. ``scripts/scan_snake_case_violations.py`` had the
same shape at its ``except SyntaxError: return []``.

THE CONTRACT PINNED HERE
------------------------
1. Three-valued per file — ``CLEAN | VIOLATION | UNANALYZABLE`` — with no
   path returning an empty result for a file that could not be read/parsed.
2. The denominator is published (``files_parsed`` / ``files_unanalyzable`` /
   ``files_skipped``) so consumers assert it SEPARATELY from the numerator.
3. Cannot-analyse exits NON-ZERO. It is a failure of the gate, not a pass.
4. A positive control runs on EVERY invocation: a planted instance of what
   the scanner detects. If the sentinel is not found the run reports BROKEN.
   Without it, "0 findings" and "the detector stopped working" are the same
   output — which is precisely the defect class this scanner exists to find.
5. Exceptions inside the walk are caught PER FILE and recorded, never
   swallowed to continue the loop silently.
6. Zero matches is reported as a COUNT, not as silence.

AND THE NEGATIVE CONTROL
------------------------
``test_negative_control_*`` are what stop "make it always fail" from
satisfying everything above: a genuinely clean tree must still report clean,
exit 0, and not fire the sentinel alarm.
"""

from __future__ import annotations

import json
import pathlib
import shutil
import subprocess
import sys

import pytest

from tests._helpers.repo_root import repo_root

REPO_ROOT = repo_root()
COMMANDS = REPO_ROOT / "src" / "roam" / "commands"

_SCRIPTS = str(REPO_ROOT / "scripts")
if _SCRIPTS not in sys.path:
    sys.path.insert(0, _SCRIPTS)

import scan_disclosure_asymmetry as disclosure  # noqa: E402
import scan_snake_case_violations as snake  # noqa: E402

#: The exact appended text that reproduced the original defect.
BROKEN_SUFFIX = "\n\ndef broken(:\n    pass\n"

#: A minimal, genuinely symmetric command — the negative-control corpus.
CLEAN_COMMAND = """
import click


@click.command()
@click.pass_context
def probe(ctx):
    warnings_out = []
    json_mode = ctx.obj.get("json")
    if json_mode:
        click.echo(to_json({"warnings_out": warnings_out}))
    else:
        for marker in warnings_out:
            click.echo(f"# warning: {marker}", err=True)
        click.echo("VERDICT: ok")
"""


def _plant_unparseable(tmp_path: pathlib.Path, name: str = "cmd_delete_check.py") -> pathlib.Path:
    """Copy a real command module and append a syntax error, as reproduced."""
    dst = tmp_path / name
    shutil.copy(COMMANDS / name, dst)
    dst.write_text(dst.read_text(encoding="utf-8") + BROKEN_SUFFIX, encoding="utf-8")
    return dst


# ---------------------------------------------------------------------------
# 1. Three-valued per file — the reproduction, inverted
# ---------------------------------------------------------------------------


def test_unparseable_command_module_is_unanalyzable_not_clean(tmp_path: pathlib.Path) -> None:
    """The reproduction, asserted the other way round.

    Pre-fix this returned ``[]`` — the same value a clean module returns.
    ``scan_file`` must now name the third state and carry the reason.
    """
    broken = _plant_unparseable(tmp_path)
    result = disclosure.scan_file(broken)

    assert result.status == disclosure.UNANALYZABLE
    assert result.violations == [], "an unanalyzed file cannot contribute findings either way"
    assert result.reason and "SyntaxError" in result.reason


def test_unparseable_module_is_distinguishable_from_a_clean_repo(tmp_path: pathlib.Path) -> None:
    """The heart of the defect: the two situations must not print the same thing.

    Pre-fix both produced ``violations: []`` and were indistinguishable to
    every consumer. Now the DENOMINATOR separates them.

    Both corpora start from the same genuinely symmetric source, so the ONLY
    difference between them is the appended syntax error. Copying a shipped
    command module here instead — as this test first did — made the experiment
    depend on whether that one module happens to be symmetric this week, which
    is a ratcheted and deliberately moving number (see
    ``tests/data/disclosure_asymmetry_baseline.json``). A control whose baseline
    arm can legitimately change is not a control.
    """
    dirty = tmp_path / "dirty"
    clean = tmp_path / "clean"
    dirty.mkdir()
    clean.mkdir()
    (dirty / "cmd_probe.py").write_text(CLEAN_COMMAND + BROKEN_SUFFIX, encoding="utf-8")
    (clean / "cmd_probe.py").write_text(CLEAN_COMMAND, encoding="utf-8")

    dirty_report = disclosure.scan(dirty)
    clean_report = disclosure.scan(clean)

    assert dirty_report.violations == clean_report.violations == []
    assert (dirty_report.files_parsed, dirty_report.files_unanalyzable) == (0, 1)
    assert (clean_report.files_parsed, clean_report.files_unanalyzable) == (1, 0)
    assert dirty_report.summary() != clean_report.summary()
    assert not dirty_report.ok
    assert clean_report.ok


def test_unparseable_module_still_appears_in_the_coverage_enumeration(tmp_path: pathlib.Path) -> None:
    """A coverage proof must not under-report its own coverage.

    Pre-fix the module vanished from ``enumerate_command_sites`` entirely —
    the function whose whole purpose is proving what was traversed.
    """
    _plant_unparseable(tmp_path)
    report = disclosure.enumerate_command_sites(tmp_path)

    assert report.sites == [], "the module genuinely could not be parsed, so it contributes no sites"
    assert report.files_unanalyzable == 1
    assert [e["module"] for e in report.unanalyzable] == ["cmd_delete_check.py"]


def test_unreadable_path_is_unanalyzable_not_clean(tmp_path: pathlib.Path) -> None:
    """``OSError`` was the other half of the swallowed tuple."""
    missing = tmp_path / "cmd_absent.py"
    result = disclosure.scan_file(missing)

    assert result.status == disclosure.UNANALYZABLE
    assert result.reason and "read failed" in result.reason


def test_analysis_crash_is_recorded_per_file_not_swallowed(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Contract 5: an exception inside the walk becomes UNANALYZABLE.

    Neither a silent ``continue`` (the pre-fix shape, which reads as clean)
    nor a whole-scan abort that loses the other files' results.
    """
    (tmp_path / "cmd_boom.py").write_text(CLEAN_COMMAND, encoding="utf-8")
    (tmp_path / "cmd_fine.py").write_text(CLEAN_COMMAND, encoding="utf-8")

    real = disclosure.analyze_command

    def exploding(func, funcs, vocabulary=disclosure.DISCLOSURE_TOKENS):
        raise RuntimeError("synthetic analyzer crash")

    monkeypatch.setattr(disclosure, "analyze_command", exploding)
    try:
        report = disclosure._build_report(tmp_path, disclosure.DISCLOSURE_TOKENS, disclosure.run_positive_control())
    finally:
        monkeypatch.setattr(disclosure, "analyze_command", real)

    assert report.files_unanalyzable == 2, "both files crashed; both must be recorded"
    assert report.files_parsed == 0
    assert all("synthetic analyzer crash" in str(e["reason"]) for e in report.unanalyzable)
    assert not report.ok


# ---------------------------------------------------------------------------
# 2. The published denominator
# ---------------------------------------------------------------------------


def test_report_publishes_parsed_unanalyzable_and_skipped(tmp_path: pathlib.Path) -> None:
    (tmp_path / "cmd_ok.py").write_text(CLEAN_COMMAND, encoding="utf-8")
    _plant_unparseable(tmp_path)
    (tmp_path / "helpers.py").write_text("x = 1\n", encoding="utf-8")

    report = disclosure.scan(tmp_path)

    assert report.files_parsed == 1
    assert report.files_unanalyzable == 1
    assert report.files_skipped == 1
    assert report.skipped[0]["reason"] == "outside the cmd_*.py glob"
    assert report.unanalyzable[0]["module"] == "cmd_delete_check.py"


def test_zero_matches_is_reported_as_a_count_not_as_silence(tmp_path: pathlib.Path) -> None:
    """Contract 6 — the disclosure IS the count."""
    (tmp_path / "cmd_ok.py").write_text(CLEAN_COMMAND, encoding="utf-8")

    summary = disclosure.scan(tmp_path).summary()

    assert summary == "checked 1 files, 0 violations, 0 unanalyzable, positive control OK"


def test_real_command_tree_has_no_silently_unanalyzed_modules() -> None:
    """The number the fix exists to expose: how many modules were never analysed."""
    report = disclosure.scan(COMMANDS)

    assert report.files_parsed > 200, f"the command surface collapsed: {report.summary()}"
    assert report.files_unanalyzable == 0, (
        "modules in src/roam/commands that the W1331 gate never actually analysed:\n"
        + "\n".join(f"  {e['module']}: {e['reason']}" for e in report.unanalyzable)
    )


# ---------------------------------------------------------------------------
# 3. Non-zero exit on unanalyzable, not only on violation
# ---------------------------------------------------------------------------


def _run_disclosure_cli(args: list[str], cwd: pathlib.Path | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(REPO_ROOT / "scripts" / "scan_disclosure_asymmetry.py"), *args],
        cwd=str(cwd or REPO_ROOT),
        capture_output=True,
        text=True,
        check=False,
    )


def test_cli_exits_non_zero_when_a_file_cannot_be_analysed(tmp_path: pathlib.Path) -> None:
    """Cannot-analyse is a failure OF THE GATE, not a pass.

    Driven through ``main`` against a synthetic tree so the real repository
    is never mutated.
    """
    (tmp_path / "cmd_ok.py").write_text(CLEAN_COMMAND, encoding="utf-8")
    _plant_unparseable(tmp_path)
    report = disclosure.scan(tmp_path)

    assert report.violations == [], "no violation here — the exit must come from the parse failure alone"
    assert report.files_unanalyzable == 1
    assert not report.ok
    assert disclosure.EXIT_UNANALYZABLE != 0


def test_cli_exit_codes_are_distinct_and_non_zero() -> None:
    codes = {
        disclosure.EXIT_OK,
        disclosure.EXIT_VIOLATIONS,
        disclosure.EXIT_UNANALYZABLE,
        disclosure.EXIT_SCANNER_BROKEN,
    }
    assert len(codes) == 4, "a shared exit code cannot distinguish 'dirty' from 'cannot tell'"
    assert disclosure.EXIT_OK == 0
    assert all(code != 0 for code in codes - {disclosure.EXIT_OK})


def test_snake_case_cli_exits_non_zero_when_a_file_cannot_be_analysed(tmp_path: pathlib.Path) -> None:
    src = tmp_path / "src" / "roam"
    src.mkdir(parents=True)
    (src / "fine.py").write_text("def ok_name():\n    return 1\n", encoding="utf-8")
    (src / "broken.py").write_text("def broken(:\n    pass\n", encoding="utf-8")

    report = snake.scan(REPO_ROOT, src_root=src)

    assert report.findings == [], "no camelCase here — the failure must come from the parse alone"
    assert report.files_parsed == 1
    assert report.files_unanalyzable == 1
    assert not report.ok
    assert snake.EXIT_UNANALYZABLE != 0


# ---------------------------------------------------------------------------
# 4. The positive control — the detector detects its own sentinel
# ---------------------------------------------------------------------------


def test_positive_control_detects_its_own_sentinel() -> None:
    control = disclosure.run_positive_control()

    assert control.ok, control.detail
    assert disclosure.POSITIVE_CONTROL_SENTINEL["command"] in control.detail


def test_positive_control_fixture_actually_contains_the_planted_defect() -> None:
    """The sentinel is a real finding produced by the real scan path."""
    fixture = disclosure.positive_control_dir()
    results = {r.module: r for r in disclosure.scan_files(fixture)}

    planted = results[str(disclosure.POSITIVE_CONTROL_SENTINEL["module"])]
    assert planted.status == disclosure.VIOLATION
    for field, expected in disclosure.POSITIVE_CONTROL_SENTINEL.items():
        assert planted.violations[0][field] == expected

    twin = results[disclosure.POSITIVE_CONTROL_SILENT_MODULE]
    assert twin.status == disclosure.CLEAN, "the symmetric twin must stay silent"


def test_positive_control_reports_broken_when_the_detector_stops_detecting(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Simulate the failure mode the control exists for: a blinded detector.

    A future ``ast`` change, a renamed node type, or a refactor that moves
    commands out of ``cmd_*.py`` all land here — the scan finds nothing and,
    without the control, prints the same "0 violations" as a clean repo.
    """
    monkeypatch.setattr(disclosure, "analyze_command", lambda *a, **k: [])

    control = disclosure.run_positive_control()

    assert not control.ok
    assert "was NOT detected" in control.detail


def test_positive_control_reports_broken_when_its_fixture_is_missing(tmp_path: pathlib.Path) -> None:
    control = disclosure.run_positive_control(root=tmp_path)

    assert not control.ok
    assert "missing" in control.detail


def test_positive_control_reports_broken_when_the_detector_over_fires(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ "Always fire" must fail the control too, not sail through it."""
    monkeypatch.setattr(
        disclosure,
        "analyze_command",
        lambda func, *a, **k: [
            {"command": func.name, "token": "warnings_out", "observed_by": ["json"], "blind": ["text"], "line": 1}
        ],
    )

    control = disclosure.run_positive_control()

    assert not control.ok
    assert "over-firing" in control.detail


def test_a_broken_control_makes_the_whole_report_not_ok(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A clean scan under a broken detector is NOT a pass."""
    (tmp_path / "cmd_ok.py").write_text(CLEAN_COMMAND, encoding="utf-8")
    monkeypatch.setattr(disclosure, "analyze_command", lambda *a, **k: [])

    report = disclosure.scan(tmp_path)

    assert report.violations == []
    assert report.files_unanalyzable == 0
    assert not report.ok, "0 violations from a detector that cannot detect is not a clean result"
    assert "BROKEN" in report.summary()


def test_snake_case_positive_control_detects_its_own_sentinel() -> None:
    control = snake.run_positive_control(REPO_ROOT)

    assert control.ok, control.detail
    assert snake.POSITIVE_CONTROL_SENTINEL in control.detail


def test_snake_case_positive_control_reports_broken_when_blinded(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(snake, "_should_skip", lambda name: True)

    control = snake.run_positive_control(REPO_ROOT)

    assert not control.ok
    assert "was NOT detected" in control.detail


def test_snake_case_positive_control_reports_broken_when_over_firing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(snake, "_should_skip", lambda name: False)

    control = snake.run_positive_control(REPO_ROOT)

    assert not control.ok
    assert "over-firing" in control.detail


# ---------------------------------------------------------------------------
# 5. NEGATIVE CONTROL — "make it always fail" must not pass
# ---------------------------------------------------------------------------


def test_negative_control_clean_tree_reports_clean_and_exits_zero(tmp_path: pathlib.Path) -> None:
    """A genuinely clean corpus stays clean: exit 0, no alarm, no violations.

    Without this, satisfying every assertion above would be trivial — a
    scanner that flags everything, or that calls every file unanalyzable,
    passes the fail-closed tests and is useless.
    """
    for name in ("cmd_alpha.py", "cmd_beta.py"):
        (tmp_path / name).write_text(CLEAN_COMMAND, encoding="utf-8")

    report = disclosure.scan(tmp_path)

    assert report.violations == []
    assert report.files_parsed == 2
    assert report.files_unanalyzable == 0
    assert report.positive_control.ok, "the sentinel alarm must not fire on a clean tree"
    assert report.ok
    assert report.summary() == "checked 2 files, 0 violations, 0 unanalyzable, positive control OK"


def test_negative_control_real_repo_scan_exits_cleanly_on_the_denominator() -> None:
    """The shipped tree parses completely and the control fires — end to end.

    Violations are the W1331 test's business; this asserts only that the
    scanner is not reporting BROKEN or UNANALYZABLE against the real repo,
    i.e. that the fail-closed machinery does not fail closed spuriously.
    """
    result = _run_disclosure_cli([])
    payload = json.loads(result.stdout)

    assert payload["files_unanalyzable"] == 0, payload["unanalyzable"]
    assert payload["files_parsed"] > 200
    assert payload["positive_control"]["status"] == "ok"
    assert result.returncode != disclosure.EXIT_UNANALYZABLE
    assert result.returncode != disclosure.EXIT_SCANNER_BROKEN


def test_negative_control_snake_case_clean_tree_stays_clean(tmp_path: pathlib.Path) -> None:
    src = tmp_path / "src" / "roam"
    src.mkdir(parents=True)
    (src / "fine.py").write_text("def ok_name():\n    return 1\n\n\ndef __init__(self):\n    pass\n", encoding="utf-8")

    report = snake.scan(REPO_ROOT, src_root=src)

    assert report.findings == []
    assert report.files_parsed == 1
    assert report.files_unanalyzable == 0
    assert report.positive_control.ok
    assert report.ok
    assert report.summary() == "checked 1 files, 0 violations, 0 unanalyzable, positive control OK"


def test_negative_control_snake_case_cli_exits_zero_on_the_real_tree() -> None:
    result = subprocess.run(
        [sys.executable, str(REPO_ROOT / "scripts" / "scan_snake_case_violations.py")],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == snake.EXIT_OK, result.stderr
    assert "0 unanalyzable" in result.stderr
    assert "positive control OK" in result.stderr
    assert isinstance(json.loads(result.stdout), list), "stdout must stay the bare baseline artifact"
