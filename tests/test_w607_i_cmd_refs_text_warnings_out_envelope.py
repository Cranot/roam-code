"""W607-I — ``cmd_refs_text`` threads ``warnings_out`` onto its envelope.

Ninth-in-batch W607 consumer-layer arc. Seals the lexical-text-audit
subprocess triplet (cmd_grep W607-G + cmd_history_grep W607-H +
cmd_refs_text W607-I) after the lexical trio (W607-A/E/F) and the
dogfood/findings consumer extensions (W607-B/C/D).

W978 first-hypothesis check (pre-fix audit)
-------------------------------------------

Before writing this file, audited ``cmd_refs_text.py`` + the read-only
helper module ``roam.commands.grep_helpers`` head-to-tail. Per
CLAUDE.md: "string audit with verdict (SAFE-TO-REMOVE / REVIEW /
LOAD-BEARING). Groups refs by surface (code/test/docs/config/dead) and
annotates reachability." Engine substrate is identical to cmd_grep's
(``detect_engine`` + ``run_search`` + ``indexed_file_scan``). Silent
fallback locations:

* ``detect_engine()`` silently returns ``"fallback"`` when
  ROAM_GREP_ENGINE pins an absent binary → user pin is dropped on the
  floor and auto-fan-out is silently chosen instead.
* ``run_search()`` / ``_run_and_parse()`` (in ``grep_helpers.py``)
  silently swallow ``FileNotFoundError`` + ``subprocess.TimeoutExpired``
  on the subprocess call → returns ``[]`` (looks like a no-match) while
  the subprocess never actually ran.
* Engine fallback re-labeling to ``indexed_scan`` happens silently when
  the auto fan-out fires.
* ``build_reachable_set`` returns None on unresolved entry — already
  loud via SystemExit + Pattern-1D state/resolution disclosure, but the
  reachability-degrade lineage is NOT separately surfaced via
  ``warnings_out`` (W607-I adds that complementary disclosure axis).

cmd_refs_text does NOT call any W605-plumbed substrate (search_fts /
fts5_available / tfidf_populated / onnx_populated / search_stored). Its
substrate is the same SUBPROCESS axis as cmd_grep — distinct from
cmd_history_grep's git-pickaxe axis and from the DB-shape lexical-search
trio (search / complete / search_semantic).

Marker family is ``refs_text_*`` — NOT ``grep_*`` (W607-G subprocess
fan-out for cmd_grep), NOT ``history_*`` (W607-H git-pickaxe for
cmd_history_grep), NOT ``search_*`` (W607-E lexical substring), NOT
``complete_*`` (W607-F lexical prefix), NOT ``semantic_*``
(W605/W607-A FTS5-BM25 substrate). The marker-prefix discipline test
pins this closed-enum distinction.

W805-W parity
-------------

W805-W already pins 3 strict-xfail Pattern-2 disclosure gaps on the
empty-corpus path (silent SAFE-TO-REMOVE verdict + missing ``state`` +
``partial_success=false`` on a corpus that couldn't be scanned).
W607-I is COMPLEMENTARY: it adds the subprocess-degrade disclosure
axis BUT does NOT fix the W805-W empty-corpus state disclosure (those
are separate Pattern-2 contracts — state-on-empty-corpus vs
subprocess-degrade-on-engine). The W805-W xfail-strict tests MUST
remain xfailed after W607-I lands.

W907 verify-cycle check
-----------------------

No "duplicated to avoid cycle" docstrings were added. The
``warnings_out: list[str] = []`` local is a plain accumulator (mirrors
W607-G's cmd_grep idiom exactly — same shared substrate, same pattern).
The shared ``grep_helpers`` module was intentionally NOT modified — the
threading lives at the cmd_refs_text boundary so the helper stays
callable from cmd_grep / cmd_history_grep / cmd_delete_check.

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
# Fixture — small indexed project so refs-text has a real corpus.
# ---------------------------------------------------------------------------


@pytest.fixture
def refs_project(tmp_path):
    """Indexed corpus with a reachable code reference to DATABASE_URL.

    Used as the populated-corpus baseline for the W607-I subprocess-axis
    tests. Distinct from the W805-W empty-corpus fixture (this corpus
    DOES contain the target string; the W607-I axis is "what happens
    when the engine subprocess fails / pin is unhonored / fanout fires"
    rather than "what happens when the string is genuinely absent").
    """
    proj = tmp_path / "refs_project"
    proj.mkdir()
    (proj / ".gitignore").write_text(".roam/\n")
    src = proj / "src"
    src.mkdir()
    (src / "core.py").write_text(
        "DATABASE_URL = 'postgresql://localhost'\n"
        "\n"
        "def get_db():\n"
        "    return DATABASE_URL\n"
        "\n"
        "def caller_fn():\n"
        "    return get_db()\n"
    )
    git_init(proj)
    out, rc = index_in_process(proj)
    assert rc == 0, f"index failed:\n{out}"
    return proj


# ---------------------------------------------------------------------------
# (1) Happy path — engine present, matches found → no warnings_out
# ---------------------------------------------------------------------------


def test_clean_happy_path(refs_project, monkeypatch):
    """Clean refs-text on a populated corpus → envelope has no warnings_out.

    Hash-stable: an empty bucket must produce a byte-identical envelope on
    the success path. The empty-bucket-no-keys discipline ensures
    consumers can't accidentally read a stale or always-present
    warnings_out field.
    """
    monkeypatch.chdir(refs_project)
    # Force auto so we don't trip the pin-missing path even on hosts that
    # happen to have neither rg nor git.
    monkeypatch.setenv("ROAM_GREP_ENGINE", "auto")
    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["--json", "refs-text", "DATABASE_URL"],
        catch_exceptions=False,
    )
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert data["command"] == "refs-text"
    # Real reference existed → at least one result with non-zero total.
    assert data["results"], f"expected >=1 result; got {data['results']!r}"
    assert data["results"][0]["total"] >= 1, data["results"]

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
        f"clean refs-text must NOT surface top-level warnings_out; got {data.get('warnings_out')!r}"
    )
    assert "warnings_out" not in data["summary"], (
        f"clean refs-text must NOT populate summary.warnings_out; got {data['summary'].get('warnings_out')!r}"
    )


# ---------------------------------------------------------------------------
# (2) Engine-failure outer-guard marker fires on synthetic exception
# ---------------------------------------------------------------------------


def test_engine_failure_marker(refs_project, monkeypatch):
    """If ``run_search`` raises (outside the inner FNF/Timeout swallow),
    the W607-I outer-guard surfaces a ``refs_text_<engine>_failed:`` marker.

    ``_run_and_parse`` silently swallows FileNotFoundError and
    TimeoutExpired — but other exceptions (e.g. PermissionError on
    Windows when the binary path is masked, or arbitrary OSError on weird
    filesystems) propagate. The W607-I outer-guard catches THOSE and
    threads the marker.
    """
    from roam.commands import cmd_refs_text

    def _boom_run_search(**kw):
        raise PermissionError("synthetic-permission-error from W607-I test")

    monkeypatch.setattr(cmd_refs_text, "run_search", _boom_run_search)
    monkeypatch.setenv("ROAM_GREP_ENGINE", "auto")
    monkeypatch.chdir(refs_project)

    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["--json", "refs-text", "DATABASE_URL"],
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
        m.startswith("refs_text_ripgrep_failed:")
        or m.startswith("refs_text_git_grep_failed:")
        or m.startswith("refs_text_engine_failed:")
        for m in top_wo
    ), f"expected refs_text_<engine>_failed: marker for run_search PermissionError; got {top_wo!r}"
    assert any("PermissionError" in m for m in top_wo), top_wo
    assert any("synthetic-permission-error from W607-I test" in m for m in top_wo), top_wo


# ---------------------------------------------------------------------------
# (3) Engine-fallback disclosed — cmd_refs_text honors ROAM_GREP_ENGINE pin
# ---------------------------------------------------------------------------


def test_engine_fallback_disclosed(refs_project, monkeypatch):
    """Pin ROAM_GREP_ENGINE=ripgrep but mask 'rg' → pin-missing marker fires.

    Pattern-2 contract: when a user pins an engine via env var and the
    binary is absent, ``detect_engine`` silently returns ``"fallback"``
    and the auto-fan-out picks a different engine (or the indexed scan).
    That's exactly the silent-fallback shape — disclose the unhonored
    pin via the ``refs_text_engine_pin_missing:`` marker.
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
    monkeypatch.chdir(refs_project)

    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["--json", "refs-text", "DATABASE_URL"],
        catch_exceptions=False,
    )
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)

    top_wo = data.get("warnings_out") or []
    assert top_wo, (
        f"pinned ROAM_GREP_ENGINE=ripgrep with rg absent MUST emit "
        f"top-level warnings_out; got data keys = {sorted(data.keys())!r}"
    )
    assert any(m.startswith("refs_text_engine_pin_missing:ripgrep") for m in top_wo), (
        f"expected ``refs_text_engine_pin_missing:ripgrep`` marker; got {top_wo!r}"
    )


# ---------------------------------------------------------------------------
# (4) Reachability-degrade disclosure — unresolved --reachable-from entry
# ---------------------------------------------------------------------------


def test_reachability_degrade_disclosed(refs_project, monkeypatch):
    """When ``--reachable-from`` names a symbol absent from the index,
    surface the degrade reason via ``warnings_out``.

    Pre-W607-I behavior: cmd_refs_text already emits a Pattern-1D
    structured envelope on this path (``state: "unresolved_entry"`` +
    ``resolution: "unresolved"`` + non-zero exit). W607-I ADDS the
    complementary ``warnings_out`` axis so a consumer scanning the
    bucket can detect the degrade lineage independently of the state
    field. Pattern-2 disclosure axis — the underlying ``--reachable-from``
    feature was honored, just the entry-resolution subprocess that
    implements it failed to find the seed.
    """
    monkeypatch.setenv("ROAM_GREP_ENGINE", "auto")
    monkeypatch.chdir(refs_project)
    runner = CliRunner()
    result = runner.invoke(
        cli,
        [
            "--json",
            "refs-text",
            "DATABASE_URL",
            "--reachable-from",
            "this_entry_does_not_exist_xyzzy",
        ],
        catch_exceptions=False,
    )
    # cmd_refs_text raises SystemExit(1) on the unresolved-entry path.
    assert result.exit_code == 1, result.output
    data = json.loads(result.output)

    top_wo = data.get("warnings_out") or []
    assert top_wo, (
        f"unresolved --reachable-from MUST surface top-level warnings_out; got data keys = {sorted(data.keys())!r}"
    )
    degraded = [m for m in top_wo if m.startswith("refs_text_reachability_degraded:")]
    assert degraded, f"expected ``refs_text_reachability_degraded:`` marker; got {top_wo!r}"
    assert any("unresolved_entry" in m for m in degraded), degraded
    assert any("this_entry_does_not_exist_xyzzy" in m for m in degraded), degraded


# ---------------------------------------------------------------------------
# (5) No-match clean → byte-identical envelope (hash stability)
# ---------------------------------------------------------------------------


def test_no_match_byte_identical(refs_project, monkeypatch):
    """Clean envelope with no matches must NOT carry warnings_out keys.

    Empty-bucket discipline: the W607-I plumbing must NOT leak the empty
    bucket onto a no-match envelope. The pre-W607-I envelope shape is
    preserved byte-for-byte when no markers fired. (Note: the W805-W
    strict-xfail set pins the OTHER axis — that the no-match path needs
    ``state``/``partial_success`` disclosure for the empty-corpus class.
    W607-I does NOT fix W805-W; it adds the subprocess-degrade axis only.)
    """
    import shutil as _sh

    monkeypatch.setenv("ROAM_GREP_ENGINE", "auto")
    monkeypatch.chdir(refs_project)
    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["--json", "refs-text", "this_pattern_will_never_match_anything_xyzzy"],
        catch_exceptions=False,
    )
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)

    # If the host has no rg AND no git on PATH, the fan-out marker WILL
    # fire — sidestep that env-dependence (the test is about the no-match
    # clean path on a properly-equipped host).
    rg_present = bool(_sh.which("rg"))
    git_present = bool(_sh.which("git"))
    if not rg_present and not git_present:
        pytest.skip(
            "host has neither 'rg' nor 'git' on PATH; fan-out fallback marker is the CORRECT signal in that environment"
        )

    assert "warnings_out" not in data, (
        f"clean no-match envelope must omit top-level warnings_out; "
        f"got data['warnings_out']={data.get('warnings_out')!r}"
    )
    assert "warnings_out" not in data["summary"], (
        f"clean no-match envelope must omit summary.warnings_out; got summary={data['summary']!r}"
    )


# ---------------------------------------------------------------------------
# (6) Three-segment marker shape — prefix:exc_or_reason:detail
# ---------------------------------------------------------------------------


def test_three_segment_marker_shape(refs_project, monkeypatch):
    """Marker must have three colon-separated segments.

    Shape contract: ``<prefix>:<exc_class_or_reason>:<detail>`` so
    downstream consumers can parse the exception class / reason without
    regex gymnastics. Mirrors W607-G cmd_grep / W607-H cmd_history_grep /
    W607-F cmd_complete / W607-E cmd_search / W607-A cmd_search_semantic /
    W607-B cmd_retrieve / W607-C cmd_findings / W607-D cmd_dogfood
    contracts.
    """
    from roam.commands import cmd_refs_text

    def _boom_run_search(**kw):
        raise PermissionError("synthetic-shape-detail-from-W607-I")

    monkeypatch.setattr(cmd_refs_text, "run_search", _boom_run_search)
    monkeypatch.setenv("ROAM_GREP_ENGINE", "auto")
    monkeypatch.chdir(refs_project)

    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["--json", "refs-text", "DATABASE_URL"],
        catch_exceptions=False,
    )
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)

    top_wo = data.get("warnings_out") or []
    assert top_wo, "run_search outer-guard must emit a marker"
    failure_markers = [
        m
        for m in top_wo
        if m.startswith("refs_text_ripgrep_failed:")
        or m.startswith("refs_text_git_grep_failed:")
        or m.startswith("refs_text_engine_failed:")
    ]
    assert failure_markers, f"expected refs_text_<engine>_failed: marker; got {top_wo!r}"

    marker = failure_markers[0]
    parts = marker.split(":", 2)
    assert len(parts) == 3, f"marker must have three colon-separated segments (prefix:exc_class:detail); got {marker!r}"
    assert parts[0] in {
        "refs_text_ripgrep_failed",
        "refs_text_git_grep_failed",
        "refs_text_engine_failed",
    }, parts
    assert parts[1] == "PermissionError", parts
    assert parts[2], parts


# ---------------------------------------------------------------------------
# (7) Marker-prefix discipline — ``refs_text_*`` not ``grep_*`` / ``history_*``
# ---------------------------------------------------------------------------


def test_marker_prefix_refs_text_not_grep_or_history(refs_project, monkeypatch):
    """Every surfaced marker uses the canonical ``refs_text_*`` prefix family.

    cmd_refs_text is the STRING-AUDIT-WITH-VERDICT axis (closed-enum
    SAFE-TO-REMOVE / REVIEW / LOAD-BEARING) — distinct from:

    * cmd_grep             → ``grep_*`` (W607-G ripgrep/git-grep fan-out)
    * cmd_history_grep     → ``history_*`` (W607-H through-history pickaxe)
    * cmd_search           → ``search_*`` (W607-E lexical substring)
    * cmd_complete         → ``complete_*`` (W607-F lexical prefix)
    * cmd_search_semantic  → ``semantic_*`` (W607-A / W605 FTS5 substrate)

    Hard guard against accidental marker-prefix drift in this consumer
    (e.g., a future contributor mis-routing a marker into the ``grep_*``
    family because cmd_refs_text reuses ``grep_helpers``). Closes the
    closed-enum discipline at the cmd_refs_text boundary.
    """
    from roam.commands import cmd_refs_text

    def _boom_run_search(**kw):
        raise PermissionError("synthetic-prefix-discipline-from-W607-I")

    monkeypatch.setattr(cmd_refs_text, "run_search", _boom_run_search)
    monkeypatch.setenv("ROAM_GREP_ENGINE", "auto")
    monkeypatch.chdir(refs_project)

    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["--json", "refs-text", "DATABASE_URL"],
        catch_exceptions=False,
    )
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    top_wo = data.get("warnings_out") or []
    assert top_wo, "expected non-empty warnings_out for prefix-consistency check"
    for marker in top_wo:
        assert marker.startswith("refs_text_"), (
            f"every surfaced marker must use the W607-I ``refs_text_*`` "
            f"prefix family (cmd_refs_text subprocess-axis scope); "
            f"got {marker!r}"
        )
        # Hard distinction from sibling subprocess layers.
        assert not marker.startswith("grep_"), (
            f"marker leaked into ``grep_*`` family (cmd_grep W607-G scope); got {marker!r}"
        )
        assert not marker.startswith("history_"), (
            f"marker leaked into ``history_*`` family (cmd_history_grep W607-H scope); got {marker!r}"
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


def test_partial_success_flip_on_subprocess_failure(refs_project, monkeypatch):
    """Any non-empty warnings_out → summary.partial_success = True.

    Pattern-2 contract: the agent MUST be able to distinguish "clean
    refs-text" from "refs-text ran with subprocess degradation" via
    summary.partial_success alone, independent of the existing
    ``state``/``resolution`` Pattern-1D fields.
    """
    from roam.commands import cmd_refs_text

    def _boom_run_search(**kw):
        raise PermissionError("synthetic-partial-success-from-W607-I")

    monkeypatch.setattr(cmd_refs_text, "run_search", _boom_run_search)
    monkeypatch.setenv("ROAM_GREP_ENGINE", "auto")
    monkeypatch.chdir(refs_project)

    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["--json", "refs-text", "DATABASE_URL"],
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


def test_summary_warnings_out_mirror(refs_project, monkeypatch):
    """Non-empty bucket → both top-level AND summary.warnings_out populated.

    Top-level is needed because the preserved-list field
    (``_ALWAYS_PRESERVED_LIST_FIELDS`` in formatter.py) survives
    ``strip_list_payloads`` in default-detail mode. summary mirror gives
    consumers reading only the summary block visibility too. Mirror
    parity with W607-A/B/C/D/E/F/G/H consumers.
    """
    from roam.commands import cmd_refs_text

    def _boom_run_search(**kw):
        raise PermissionError("synthetic-mirror-from-W607-I")

    monkeypatch.setattr(cmd_refs_text, "run_search", _boom_run_search)
    monkeypatch.setenv("ROAM_GREP_ENGINE", "auto")
    monkeypatch.chdir(refs_project)

    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["--json", "refs-text", "DATABASE_URL"],
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
# (10) Top-level mirror explicitly checked (W607-A..H discipline parity)
# ---------------------------------------------------------------------------


def test_top_level_warnings_out_mirror(refs_project, monkeypatch):
    """Top-level ``warnings_out`` must be present alongside summary mirror.

    The preserved-list-field discipline at ``_ALWAYS_PRESERVED_LIST_FIELDS``
    requires the top-level mirror so the field survives detail-mode
    list-payload stripping. W607-A through W607-H pinned the same
    discipline; W607-I extends it to cmd_refs_text.
    """
    from roam.commands import cmd_refs_text

    def _boom_run_search(**kw):
        raise PermissionError("synthetic-top-level-from-W607-I")

    monkeypatch.setattr(cmd_refs_text, "run_search", _boom_run_search)
    monkeypatch.setenv("ROAM_GREP_ENGINE", "auto")
    monkeypatch.chdir(refs_project)

    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["--json", "refs-text", "DATABASE_URL"],
        catch_exceptions=False,
    )
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)

    top_wo = data.get("warnings_out")
    assert isinstance(top_wo, list) and top_wo, (
        f"top-level warnings_out must be a non-empty list on disclosure path; got {top_wo!r}"
    )


# ---------------------------------------------------------------------------
# (11) W805-W parity — strict-xfail Pattern-2 disclosure tests must
#      remain xfailed (W607-I does NOT fix the empty-corpus state gap).
# ---------------------------------------------------------------------------


def test_w805_w_xfail_still_strict():
    """W805-W strict-xfail Pattern-2 disclosure must remain xfailed.

    W805-W pins 3 strict-xfail tests on the empty-corpus path (silent
    SAFE-TO-REMOVE verdict + missing ``state`` + ``partial_success=false``
    + silent SAFE-TO-REMOVE on unscannable corpus). W607-I adds a
    COMPLEMENTARY disclosure axis (subprocess-degrade via
    ``warnings_out``), but does NOT address the empty-corpus Pattern-2
    contract (state-on-empty-corpus is a separate fix). The W805-W
    tests must stay xfailed after W607-I lands — a drive-by graduation
    of ANY of those three to PASS would mean W607-I accidentally fixed
    something it wasn't scoped to fix.

    Verify the xfail-strict markers are still present in the W805-W
    test source. Source-text scan beats invoking pytest-on-pytest;
    if the strict markers were removed, this assertion catches it.
    """
    here = Path(__file__).parent
    w805_w = here / "test_w805_w_cmd_refs_text_empty_corpus.py"
    assert w805_w.exists(), f"W805-W test file missing at {w805_w}"
    src = w805_w.read_text(encoding="utf-8")
    # Count strict-xfail markers — must remain at 3 (the original pin set).
    strict_count = src.count("strict=True")
    assert strict_count == 3, (
        f"W805-W strict-xfail marker count drift: expected 3, got "
        f"{strict_count}. W607-I must NOT graduate any W805-W bug; the "
        f"empty-corpus state disclosure is a separate Pattern-2 contract "
        f"orthogonal to the W607-I subprocess-degrade axis."
    )
    # Names of the 3 xfail-strict tests — pin so a future rename without
    # graduation doesn't slip past.
    for test_name in (
        "test_empty_corpus_explicit_state",
        "test_empty_corpus_partial_success_set",
        "test_no_silent_safe_to_remove_on_empty",
    ):
        assert test_name in src, (
            f"W805-W xfail-strict test {test_name!r} was renamed or "
            f"removed without graduation. W607-I scope is the subprocess-"
            f"degrade axis only — empty-corpus state disclosure is a "
            f"separate Pattern-2 fix."
        )
