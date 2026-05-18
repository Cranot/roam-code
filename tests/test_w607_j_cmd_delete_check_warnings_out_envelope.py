"""W607-J — ``cmd_delete_check`` threads ``warnings_out`` onto its envelope.

Tenth-in-batch W607 consumer-layer arc. Seals the grep_helpers consumer
QUARTET — cmd_grep (W607-G) + cmd_history_grep (W607-H) +
cmd_refs_text (W607-I) + cmd_delete_check (W607-J) — after the lexical
trio (W607-A/E/F) and the dogfood/findings consumer extensions
(W607-B/C/D).

W978 first-hypothesis check (pre-fix audit)
-------------------------------------------

Before writing this file, audited ``cmd_delete_check.py`` + the
read-only helper module ``roam.commands.grep_helpers`` head-to-tail.
Per CLAUDE.md: "gates the diff on surviving references; exits 5 on
BREAK-RISK with --ci". Engine substrate is identical to
cmd_grep/cmd_refs_text (``detect_engine`` + ``run_search`` +
``indexed_file_scan``). Silent fallback locations:

* ``detect_engine()`` silently returns ``"fallback"`` when
  ROAM_GREP_ENGINE pins an absent binary → user pin is dropped on the
  floor and auto-fan-out is silently chosen instead.
* ``run_search()`` / ``_run_and_parse()`` (in ``grep_helpers.py``)
  silently swallow ``FileNotFoundError`` + ``subprocess.TimeoutExpired``
  on the subprocess call → returns ``[]`` (looks like a no-survivor)
  while the subprocess never actually ran.
* Engine fallback re-labeling to ``indexed_scan`` happens silently when
  the auto fan-out fires.
* ``_git_diff`` (the diff-source subprocess for
  --source=working/staged/pr/head) collapses git-missing, git-timeout,
  and git-error into the same ``(_, error_kind)`` shape — already
  surfaces via ``git_error`` field; W607-J adds the ``warnings_out``
  mirror so a consumer can detect the degrade lineage independently.
* ``build_reachable_set`` returns None on unresolved entry — already
  loud via SystemExit + Pattern-1D state/resolution disclosure, but the
  reachability-degrade lineage is NOT separately surfaced via
  ``warnings_out`` (W607-J adds that complementary disclosure axis).

cmd_delete_check has TWO distinct subprocess axes: the engine fan-out
(shared with cmd_grep/cmd_refs_text) AND the git diff-source subprocess
(`_git_diff` for --source). The latter is shape-shared with cmd_pr_diff
/ cmd_diff but distinct from the cmd_history_grep pickaxe axis.

Marker family is ``delete_check_*`` — NOT ``grep_*`` (W607-G), NOT
``history_*`` (W607-H), NOT ``refs_text_*`` (W607-I), NOT ``search_*``
(W607-E), NOT ``complete_*`` (W607-F), NOT ``semantic_*``
(W605/W607-A). The marker-prefix discipline test pins this closed-enum
distinction.

W805-Z parity
-------------

W805-Z already pins 5 strict-xfail Pattern-2 disclosure gaps on the
empty-corpus / zero-survivors path (CRITICAL agent-safety: silent SAFE
verdict + missing ``state`` + ``partial_success=false`` + exit 0 under
--ci on unscannable corpus). W607-J is COMPLEMENTARY: it adds the
subprocess-degrade disclosure axis BUT does NOT fix the W805-Z
empty-corpus state disclosure (those are separate Pattern-2 contracts
— state-on-zero-survivors vs subprocess-degrade-on-engine). The
W805-Z xfail-strict tests MUST remain xfailed after W607-J lands.

W907 verify-cycle check
-----------------------

No "duplicated to avoid cycle" docstrings were added. The
``warnings_out: list[str] = []`` local is a plain accumulator (mirrors
W607-G's cmd_grep / W607-I's cmd_refs_text idiom exactly — same
shared substrate, same pattern). The shared ``grep_helpers`` module
was intentionally NOT modified — the threading lives at the
cmd_delete_check boundary so the helper stays callable from the
sibling consumers.

LAW 4 note: warning markers are diagnostic strings, NOT
``agent_contract.facts`` content, and therefore not subject to the
concrete-noun-terminal lint.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from click.testing import CliRunner

from roam.cli import cli

sys.path.insert(0, str(Path(__file__).parent))
from conftest import git_init, index_in_process  # noqa: E402

# ---------------------------------------------------------------------------
# Fixture — small indexed project with a real BREAK-RISK deletion.
# ---------------------------------------------------------------------------


@pytest.fixture
def delete_check_project(tmp_path):
    """Indexed corpus with foo defined in foo.py + bar.py still calling it.

    Used as the populated-corpus baseline for the W607-J subprocess-axis
    tests. Deleting foo's definition in the working tree produces a
    genuine BREAK-RISK signal — distinct from the W805-Z empty-corpus
    fixture (this corpus DOES have surviving references; the W607-J
    axis is "what happens when the engine subprocess fails / pin is
    unhonored / fanout fires" rather than "what happens when the
    corpus is empty").
    """
    proj = tmp_path / "delete_check_project"
    proj.mkdir()
    (proj / ".gitignore").write_text(".roam/\n")
    src = proj / "src"
    src.mkdir()
    (src / "foo.py").write_text("def foo():\n    return 1\n")
    (src / "bar.py").write_text("from src.foo import foo\n\ndef bar():\n    return foo()\n")
    git_init(proj)
    out, rc = index_in_process(proj)
    assert rc == 0, f"index failed:\n{out}"
    # Remove foo's def in the working tree (creates a real BREAK-RISK).
    (src / "foo.py").write_text("# foo removed\n")
    return proj


# ---------------------------------------------------------------------------
# (1) Happy path — engine present, deletion gated → no warnings_out
# ---------------------------------------------------------------------------


def test_clean_happy_path(delete_check_project, monkeypatch):
    """Clean delete-check on populated corpus → envelope has no warnings_out.

    Hash-stable: an empty bucket must produce a byte-identical envelope on
    the success path. The empty-bucket-no-keys discipline ensures
    consumers can't accidentally read a stale or always-present
    warnings_out field.
    """
    monkeypatch.chdir(delete_check_project)
    monkeypatch.setenv("ROAM_GREP_ENGINE", "auto")
    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["--json", "delete-check"],
        catch_exceptions=False,
    )
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert data["command"] == "delete-check"
    # Real deletion existed → at least one deletion record.
    assert data.get("deletions"), f"expected >=1 deletion; got {data!r}"

    # If the host has no rg AND no git on PATH, the fan-out fallback
    # marker WILL fire even on the happy path — sidestep that env
    # dependence (the test is about the clean happy path on a properly
    # equipped host).
    import shutil as _sh

    rg_present = bool(_sh.which("rg"))
    git_present = bool(_sh.which("git"))
    if not rg_present and not git_present:
        pytest.skip(
            "host has neither 'rg' nor 'git' on PATH; fan-out fallback marker is the CORRECT signal in that environment"
        )

    assert "warnings_out" not in data, (
        f"clean delete-check must NOT surface top-level warnings_out; got {data.get('warnings_out')!r}"
    )
    assert "warnings_out" not in data["summary"], (
        f"clean delete-check must NOT populate summary.warnings_out; got {data['summary'].get('warnings_out')!r}"
    )


# ---------------------------------------------------------------------------
# (2) Engine-failure outer-guard marker fires on synthetic exception
# ---------------------------------------------------------------------------


def test_engine_failure_marker(delete_check_project, monkeypatch):
    """If ``run_search`` raises (outside the inner FNF/Timeout swallow),
    the W607-J outer-guard surfaces a ``delete_check_<engine>_failed:`` marker.

    ``_run_and_parse`` silently swallows FileNotFoundError and
    TimeoutExpired — but other exceptions (e.g. PermissionError on
    Windows when the binary path is masked, or arbitrary OSError on weird
    filesystems) propagate. The W607-J outer-guard catches THOSE and
    threads the marker.
    """
    from roam.commands import cmd_delete_check

    def _boom_run_search(**kw):
        raise PermissionError("synthetic-permission-error from W607-J test")

    monkeypatch.setattr(cmd_delete_check, "run_search", _boom_run_search)
    monkeypatch.setenv("ROAM_GREP_ENGINE", "auto")
    monkeypatch.chdir(delete_check_project)

    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["--json", "delete-check"],
        catch_exceptions=False,
    )
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)

    top_wo = data.get("warnings_out") or []
    assert top_wo, (
        f"run_search PermissionError must surface top-level warnings_out; got data keys = {sorted(data.keys())!r}"
    )
    # The marker family depends on which engine ``detect_engine`` picked.
    assert any(
        m.startswith("delete_check_ripgrep_failed:")
        or m.startswith("delete_check_git_grep_failed:")
        or m.startswith("delete_check_engine_failed:")
        for m in top_wo
    ), f"expected delete_check_<engine>_failed: marker for run_search PermissionError; got {top_wo!r}"
    assert any("PermissionError" in m for m in top_wo), top_wo
    assert any("synthetic-permission-error from W607-J test" in m for m in top_wo), top_wo


# ---------------------------------------------------------------------------
# (3) Engine-fallback disclosed — cmd_delete_check honors ROAM_GREP_ENGINE pin
# ---------------------------------------------------------------------------


def test_engine_fallback_disclosed(delete_check_project, monkeypatch):
    """Pin ROAM_GREP_ENGINE=ripgrep but mask 'rg' → pin-missing marker fires.

    Pattern-2 contract: when a user pins an engine via env var and the
    binary is absent, ``detect_engine`` silently returns ``"fallback"``
    and the auto-fan-out picks a different engine (or the indexed scan).
    That's exactly the silent-fallback shape — disclose the unhonored
    pin via the ``delete_check_engine_pin_missing:`` marker.
    """
    import shutil

    real_which = shutil.which

    def _mock_which(name, *a, **kw):
        if name == "rg":
            return None
        return real_which(name, *a, **kw)

    monkeypatch.setattr(shutil, "which", _mock_which)
    from roam.commands import grep_helpers

    monkeypatch.setattr(grep_helpers.shutil, "which", _mock_which)
    monkeypatch.setenv("ROAM_GREP_ENGINE", "ripgrep")
    monkeypatch.chdir(delete_check_project)

    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["--json", "delete-check"],
        catch_exceptions=False,
    )
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)

    top_wo = data.get("warnings_out") or []
    assert top_wo, (
        f"pinned ROAM_GREP_ENGINE=ripgrep with rg absent MUST emit "
        f"top-level warnings_out; got data keys = {sorted(data.keys())!r}"
    )
    assert any(m.startswith("delete_check_engine_pin_missing:ripgrep") for m in top_wo), (
        f"expected ``delete_check_engine_pin_missing:ripgrep`` marker; got {top_wo!r}"
    )


# ---------------------------------------------------------------------------
# (4) Diff-source subprocess failure — _git_diff failure surfaces marker
# ---------------------------------------------------------------------------


def test_diff_source_subprocess_failure(delete_check_project, monkeypatch):
    """When ``_git_diff`` returns an error sentinel (git missing / timeout /
    error), surface the degrade reason via ``warnings_out``.

    Pre-W607-J behavior: cmd_delete_check already emits a structured
    envelope on this path (``git_error`` field + ``partial_success=True``).
    W607-J ADDS the complementary ``warnings_out`` axis so a consumer
    scanning the bucket can detect the diff-source degrade lineage
    independently of the existing ``git_error`` field. Pattern-2
    disclosure axis — the underlying --source flag was honored, just
    the git subprocess that implements it failed.
    """
    from roam.commands import cmd_delete_check

    def _fake_git_diff(*a, **kw):
        return "", cmd_delete_check._GIT_MISSING

    monkeypatch.setattr(cmd_delete_check, "_git_diff", _fake_git_diff)
    monkeypatch.setenv("ROAM_GREP_ENGINE", "auto")
    monkeypatch.chdir(delete_check_project)

    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["--json", "delete-check"],
        catch_exceptions=False,
    )
    # cmd_delete_check returns 0 on git error WITHOUT --ci.
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)

    top_wo = data.get("warnings_out") or []
    assert top_wo, f"_git_diff failure MUST surface top-level warnings_out; got data keys = {sorted(data.keys())!r}"
    degraded = [m for m in top_wo if m.startswith("delete_check_git_diff_failed:")]
    assert degraded, f"expected ``delete_check_git_diff_failed:`` marker; got {top_wo!r}"
    assert any("git_not_available" in m for m in degraded), degraded


# ---------------------------------------------------------------------------
# (5) No-match clean → byte-identical envelope (hash stability)
# ---------------------------------------------------------------------------


def test_no_match_byte_identical(delete_check_project, monkeypatch):
    """Clean envelope must NOT carry warnings_out keys when no markers fire.

    Empty-bucket discipline: the W607-J plumbing must NOT leak the empty
    bucket onto a clean envelope. The pre-W607-J envelope shape is
    preserved byte-for-byte when no markers fired. (Note: the W805-Z
    strict-xfail set pins the OTHER axis — that the empty-corpus /
    zero-survivors path needs ``state``/``partial_success`` disclosure.
    W607-J does NOT fix W805-Z; it adds the subprocess-degrade axis only.)
    """
    import shutil as _sh

    monkeypatch.setenv("ROAM_GREP_ENGINE", "auto")
    monkeypatch.chdir(delete_check_project)
    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["--json", "delete-check"],
        catch_exceptions=False,
    )
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)

    # If the host has no rg AND no git on PATH, the fan-out marker WILL
    # fire — sidestep that env-dependence (the test is about the clean
    # path on a properly-equipped host).
    rg_present = bool(_sh.which("rg"))
    git_present = bool(_sh.which("git"))
    if not rg_present and not git_present:
        pytest.skip(
            "host has neither 'rg' nor 'git' on PATH; fan-out fallback marker is the CORRECT signal in that environment"
        )

    assert "warnings_out" not in data, (
        f"clean envelope must omit top-level warnings_out; got data['warnings_out']={data.get('warnings_out')!r}"
    )
    assert "warnings_out" not in data["summary"], (
        f"clean envelope must omit summary.warnings_out; got summary={data['summary']!r}"
    )


# ---------------------------------------------------------------------------
# (6) Three-segment marker shape — prefix:exc_or_reason:detail
# ---------------------------------------------------------------------------


def test_three_segment_marker_shape(delete_check_project, monkeypatch):
    """Marker must have three colon-separated segments.

    Shape contract: ``<prefix>:<exc_class_or_reason>:<detail>`` so
    downstream consumers can parse the exception class / reason without
    regex gymnastics. Mirrors W607-G cmd_grep / W607-H cmd_history_grep /
    W607-I cmd_refs_text / W607-F cmd_complete / W607-E cmd_search /
    W607-A cmd_search_semantic / W607-B cmd_retrieve / W607-C
    cmd_findings / W607-D cmd_dogfood contracts.
    """
    from roam.commands import cmd_delete_check

    def _boom_run_search(**kw):
        raise PermissionError("synthetic-shape-detail-from-W607-J")

    monkeypatch.setattr(cmd_delete_check, "run_search", _boom_run_search)
    monkeypatch.setenv("ROAM_GREP_ENGINE", "auto")
    monkeypatch.chdir(delete_check_project)

    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["--json", "delete-check"],
        catch_exceptions=False,
    )
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)

    top_wo = data.get("warnings_out") or []
    assert top_wo, "run_search outer-guard must emit a marker"
    failure_markers = [
        m
        for m in top_wo
        if m.startswith("delete_check_ripgrep_failed:")
        or m.startswith("delete_check_git_grep_failed:")
        or m.startswith("delete_check_engine_failed:")
    ]
    assert failure_markers, f"expected delete_check_<engine>_failed: marker; got {top_wo!r}"

    marker = failure_markers[0]
    parts = marker.split(":", 2)
    assert len(parts) == 3, f"marker must have three colon-separated segments (prefix:exc_class:detail); got {marker!r}"
    assert parts[0] in {
        "delete_check_ripgrep_failed",
        "delete_check_git_grep_failed",
        "delete_check_engine_failed",
    }, parts
    assert parts[1] == "PermissionError", parts
    assert parts[2], parts


# ---------------------------------------------------------------------------
# (7) Marker-prefix discipline — ``delete_check_*`` not ``refs_text_*`` /
#     ``grep_*`` / ``history_*``
# ---------------------------------------------------------------------------


def test_marker_prefix_delete_check_not_refs_text_or_grep(delete_check_project, monkeypatch):
    """Every surfaced marker uses the canonical ``delete_check_*`` prefix family.

    cmd_delete_check is the DIFF-GATE-WITH-CI-EXIT-5 axis — distinct from:

    * cmd_grep             → ``grep_*`` (W607-G ripgrep/git-grep fan-out)
    * cmd_history_grep     → ``history_*`` (W607-H through-history pickaxe)
    * cmd_refs_text        → ``refs_text_*`` (W607-I string-audit-with-verdict)
    * cmd_search           → ``search_*`` (W607-E lexical substring)
    * cmd_complete         → ``complete_*`` (W607-F lexical prefix)
    * cmd_search_semantic  → ``semantic_*`` (W607-A / W605 FTS5 substrate)

    Hard guard against accidental marker-prefix drift in this consumer
    (e.g., a future contributor mis-routing a marker into the
    ``refs_text_*`` or ``grep_*`` family because cmd_delete_check reuses
    ``grep_helpers``). Closes the closed-enum discipline at the
    cmd_delete_check boundary.
    """
    from roam.commands import cmd_delete_check

    def _boom_run_search(**kw):
        raise PermissionError("synthetic-prefix-discipline-from-W607-J")

    monkeypatch.setattr(cmd_delete_check, "run_search", _boom_run_search)
    monkeypatch.setenv("ROAM_GREP_ENGINE", "auto")
    monkeypatch.chdir(delete_check_project)

    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["--json", "delete-check"],
        catch_exceptions=False,
    )
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    top_wo = data.get("warnings_out") or []
    assert top_wo, "expected non-empty warnings_out for prefix-consistency check"
    for marker in top_wo:
        assert marker.startswith("delete_check_"), (
            f"every surfaced marker must use the W607-J ``delete_check_*`` "
            f"prefix family (cmd_delete_check subprocess-axis scope); "
            f"got {marker!r}"
        )
        # Hard distinction from sibling subprocess layers.
        assert not marker.startswith("grep_"), (
            f"marker leaked into ``grep_*`` family (cmd_grep W607-G scope); got {marker!r}"
        )
        assert not marker.startswith("history_"), (
            f"marker leaked into ``history_*`` family (cmd_history_grep W607-H scope); got {marker!r}"
        )
        assert not marker.startswith("refs_text_"), (
            f"marker leaked into ``refs_text_*`` family (cmd_refs_text W607-I scope); got {marker!r}"
        )
        assert not marker.startswith("search_"), (
            f"marker leaked into ``search_*`` family (cmd_search W607-E scope); got {marker!r}"
        )
        assert not marker.startswith("complete_"), (
            f"marker leaked into ``complete_*`` family (cmd_complete W607-F scope); got {marker!r}"
        )
        assert not marker.startswith("semantic_"), (
            f"marker leaked into ``semantic_*`` family "
            f"(cmd_search_semantic W607-A / W605 substrate scope); "
            f"got {marker!r}"
        )


# ---------------------------------------------------------------------------
# (8) partial_success flips on subprocess failure
# ---------------------------------------------------------------------------


def test_partial_success_flip_on_subprocess_failure(delete_check_project, monkeypatch):
    """Any non-empty warnings_out → summary.partial_success = True.

    Pattern-2 contract: the agent MUST be able to distinguish "clean
    delete-check" from "delete-check ran with subprocess degradation"
    via summary.partial_success alone, independent of the existing
    ``git_error`` / ``state`` / ``resolution`` Pattern-1D fields.
    """
    from roam.commands import cmd_delete_check

    def _boom_run_search(**kw):
        raise PermissionError("synthetic-partial-success-from-W607-J")

    monkeypatch.setattr(cmd_delete_check, "run_search", _boom_run_search)
    monkeypatch.setenv("ROAM_GREP_ENGINE", "auto")
    monkeypatch.chdir(delete_check_project)

    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["--json", "delete-check"],
        catch_exceptions=False,
    )
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert data["summary"].get("partial_success") is True, (
        f"non-empty warnings_out must flip summary.partial_success=True; got summary = {data['summary']!r}"
    )


# ---------------------------------------------------------------------------
# (9) summary.warnings_out mirror — top-level AND summary populated
# ---------------------------------------------------------------------------


def test_summary_warnings_out_mirror(delete_check_project, monkeypatch):
    """Non-empty bucket → both top-level AND summary.warnings_out populated.

    Top-level is needed because the preserved-list field
    (``_ALWAYS_PRESERVED_LIST_FIELDS`` in formatter.py) survives
    ``strip_list_payloads`` in default-detail mode. summary mirror gives
    consumers reading only the summary block visibility too. Mirror
    parity with W607-A/B/C/D/E/F/G/H/I consumers.
    """
    from roam.commands import cmd_delete_check

    def _boom_run_search(**kw):
        raise PermissionError("synthetic-mirror-from-W607-J")

    monkeypatch.setattr(cmd_delete_check, "run_search", _boom_run_search)
    monkeypatch.setenv("ROAM_GREP_ENGINE", "auto")
    monkeypatch.chdir(delete_check_project)

    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["--json", "delete-check"],
        catch_exceptions=False,
    )
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)

    assert data.get("warnings_out"), (
        f"top-level warnings_out missing on disclosure path; keys = {sorted(data.keys())!r}"
    )
    assert data["summary"].get("warnings_out"), (
        f"summary.warnings_out missing on disclosure path; got summary = {data['summary']!r}"
    )
    # Top-level and summary content must be equal.
    assert sorted(data["warnings_out"]) == sorted(data["summary"]["warnings_out"]), (
        f"top-level vs summary.warnings_out must be equal; "
        f"top={data['warnings_out']!r} summary={data['summary']['warnings_out']!r}"
    )


# ---------------------------------------------------------------------------
# (10) Top-level mirror explicitly checked (W607-A..I discipline parity)
# ---------------------------------------------------------------------------


def test_top_level_warnings_out_mirror(delete_check_project, monkeypatch):
    """Top-level ``warnings_out`` must be present alongside summary mirror.

    The preserved-list-field discipline at ``_ALWAYS_PRESERVED_LIST_FIELDS``
    requires the top-level mirror so the field survives detail-mode
    list-payload stripping. W607-A through W607-I pinned the same
    discipline; W607-J extends it to cmd_delete_check, sealing the
    grep_helpers consumer quartet.
    """
    from roam.commands import cmd_delete_check

    def _boom_run_search(**kw):
        raise PermissionError("synthetic-top-level-from-W607-J")

    monkeypatch.setattr(cmd_delete_check, "run_search", _boom_run_search)
    monkeypatch.setenv("ROAM_GREP_ENGINE", "auto")
    monkeypatch.chdir(delete_check_project)

    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["--json", "delete-check"],
        catch_exceptions=False,
    )
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)

    top_wo = data.get("warnings_out")
    assert isinstance(top_wo, list) and top_wo, (
        f"top-level warnings_out must be a non-empty list on disclosure path; got {top_wo!r}"
    )


# ---------------------------------------------------------------------------
# (11) W805-Z parity — strict-xfail Pattern-2 disclosure tests must
#      remain xfailed (W607-J does NOT fix the empty-corpus state gap).
# ---------------------------------------------------------------------------


def test_w805_z_xfail_still_strict():
    """W805-Z strict-xfail Pattern-2 disclosure must remain xfailed.

    W805-Z pins 5 strict-xfail tests on the empty-corpus / zero-survivors
    path (silent SAFE verdict + missing ``state`` + ``partial_success=false``
    + silent SAFE on unscannable corpus + exit 0 under --ci). W607-J adds
    a COMPLEMENTARY disclosure axis (subprocess-degrade via
    ``warnings_out``), but does NOT address the empty-corpus Pattern-2
    contract (state-on-empty-corpus is a separate fix). The W805-Z tests
    must stay xfailed after W607-J lands — a drive-by graduation of ANY
    of those five to PASS would mean W607-J accidentally fixed something
    it wasn't scoped to fix.

    Verify the xfail-strict markers are still present in the W805-Z
    test source. Source-text scan beats invoking pytest-on-pytest;
    if the strict markers were removed, this assertion catches it.
    """
    here = Path(__file__).parent
    w805_z = here / "test_w805_z_cmd_delete_check_empty_corpus.py"
    assert w805_z.exists(), f"W805-Z test file missing at {w805_z}"
    src = w805_z.read_text(encoding="utf-8")
    # Count strict-xfail markers — must remain at 5 (the original pin set).
    strict_count = src.count("strict=True")
    assert strict_count == 5, (
        f"W805-Z strict-xfail marker count drift: expected 5, got "
        f"{strict_count}. W607-J must NOT graduate any W805-Z bug; the "
        f"empty-corpus state disclosure is a separate Pattern-2 contract "
        f"orthogonal to the W607-J subprocess-degrade axis."
    )
    # Names of the 5 xfail-strict tests — pin so a future rename without
    # graduation doesn't slip past.
    for test_name in (
        "test_empty_diff_explicit_state",
        "test_zero_survivors_explicit_state",
        "test_zero_survivors_partial_success_set",
        "test_no_silent_safe_on_empty",
        "test_no_silent_no_break_risk_on_empty_ci",
    ):
        assert test_name in src, (
            f"W805-Z xfail-strict test {test_name!r} was renamed or "
            f"removed without graduation. W607-J scope is the subprocess-"
            f"degrade axis only — empty-corpus state disclosure is a "
            f"separate Pattern-2 fix."
        )
