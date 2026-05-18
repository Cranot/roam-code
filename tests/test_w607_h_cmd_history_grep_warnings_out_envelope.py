"""W607-H — ``cmd_history_grep`` threads ``warnings_out`` onto its envelope.

Eighth-in-batch W607 consumer-layer arc. Closes the subprocess pair
(cmd_grep W607-G + cmd_history_grep W607-H) after the lexical trio
(W607-A/E/F). The W595-W606 substrate-floor Pattern-2 arc plumbed
``warnings_out`` buckets on every silent-fallback substrate reader;
W607-A landed the first consumer-layer wave on cmd_search_semantic.
W607-B/C/D/E/F/G extended through retrieve / findings / dogfood /
search / complete / grep. W607-H is the eighth consumer-layer wave and
the second subprocess-axis consumer.

W978 first-hypothesis check (pre-fix audit)
-------------------------------------------

Before writing this file, audited ``cmd_history_grep.py`` head-to-tail.
Per CLAUDE.md: "git pickaxe (-S/-G) with author/date and
introduced/removed annotation". Two distinct git subprocess sites:

* ``_git_pickaxe`` (lines 75-92 pre-W607-H) — wraps ``git log -S/-G``.
  Already CP45/CP46 fail-loud per the ``_GIT_*`` sentinels: catches
  ``FileNotFoundError`` / ``TimeoutExpired`` / rc != 0 and surfaces
  the kind via ``git_errors{}`` on the envelope. Loud and correct.

* ``_diff_polarity`` (lines 112-159 pre-W607-H) — wraps ``git show``
  for the ``--polarity`` annotation. **SILENT fallback**: catches
  ``FileNotFoundError`` / ``TimeoutExpired`` / rc != 0 and collapses
  all three into a single ``return None``, indistinguishable from a
  successful diff that found no +/- match. The envelope has no
  field where an agent can detect "polarity subprocess broke".

W607-H additions (complementary to existing git_errors, NOT a
replacement):

1. Outer-guard around the ``_git_pickaxe`` per-pattern call → catches
   exceptions OUTSIDE the inner FNF/TimeoutExpired/rc-mapped axis
   (e.g. PermissionError on Windows, OSError on weird filesystems).
   Marker family: ``history_pickaxe_failed:<exc>:<detail>``.

2. Outer-guard around the ``_diff_polarity`` per-commit call → same
   shape but for ``git show``. Marker family:
   ``history_polarity_failed:<exc>:<detail>``.

3. Inner-disclosure for the previously-silent polarity-degrade path
   (FNF / TimeoutExpired / rc!=0 on ``git show``) — closes the W805-DD
   shape-parity gap where ``--polarity`` was requested but produced no
   observable signal. Marker family:
   ``history_polarity_degraded:<reason>:sha=<short>``.

cmd_history_grep does NOT call any W605-plumbed substrate (search_fts
/ fts5_available / tfidf_populated / onnx_populated / search_stored).
Its substrate is the GIT subprocess axis — distinct shape from
cmd_grep's ripgrep/git-grep fan-out (W607-G) and from the lexical
DB-shape trio (W607-A/E/F).

Marker family is ``history_*`` — NOT ``grep_*`` (W607-G subprocess
fan-out), NOT ``search_*`` (W607-E lexical substring), NOT
``complete_*`` (W607-F lexical prefix), NOT ``semantic_*`` (W605/W607-A
FTS5-BM25 substrate). The marker-prefix discipline test pins this
closed-enum distinction.

W805-DD parity
--------------

W805-DD already pins 4 strict-xfail Pattern-2 disclosure gaps on the
empty-corpus path (silent ``0 commit(s) across 0/N pattern(s)`` verdict
+ missing ``state`` + ``partial_success=false`` + silent ``--polarity``
on zero commits). W607-H is COMPLEMENTARY: it adds the
subprocess-degrade disclosure axis BUT does NOT fix the W805-DD
empty-corpus state disclosure (those are separate Pattern-2 contracts
— state-on-empty-history vs subprocess-degrade-on-polarity). The
W805-DD xfail-strict tests MUST remain xfailed after W607-H lands.

W907 verify-cycle check
-----------------------

No "duplicated to avoid cycle" docstrings were added. The
``warnings_out: list[str] = []`` local is a plain accumulator (mirrors
W607-G's cmd_grep idiom); the ``_diff_polarity`` signature changed
from ``str | None`` to ``tuple[str | None, str | None]`` to carry the
degrade reason, but that's a contract widening for the SINGLE caller
inside the cmd_history_grep module — no cross-module hoisting was
needed.

LAW 4 note: warning markers are diagnostic strings, NOT
``agent_contract.facts`` content, and therefore not subject to the
concrete-noun-terminal lint.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest
from click.testing import CliRunner

from roam.cli import cli

sys.path.insert(0, str(Path(__file__).parent))
from conftest import git_init, index_in_process  # noqa: E402

# ---------------------------------------------------------------------------
# Fixtures — corpora that exercise the W607-H subprocess axes.
# ---------------------------------------------------------------------------


@pytest.fixture
def history_project(tmp_path):
    """Repo with a real introduced string + --polarity-annotatable diff.

    Init commit introduces DATABASE_URL; pickaxe -S DATABASE_URL finds
    1 commit; ``--polarity`` annotates it as 'introduced'. Clean
    happy-path for W607-H — no warnings_out should fire.
    """
    proj = tmp_path / "history_project"
    proj.mkdir()
    (proj / ".gitignore").write_text(".roam/\n")
    src = proj / "src"
    src.mkdir()
    (src / "core.py").write_text("DATABASE_URL = 'postgresql://localhost'\n\ndef get_db():\n    return DATABASE_URL\n")
    git_init(proj)
    out, rc = index_in_process(proj)
    assert rc == 0, f"index failed:\n{out}"
    return proj


# ---------------------------------------------------------------------------
# (1) Happy path — git present, matches found, polarity clean → no warnings_out
# ---------------------------------------------------------------------------


def test_clean_happy_path(history_project, monkeypatch):
    """Clean history-grep on populated corpus → no warnings_out keys.

    Hash-stable: empty bucket must produce a byte-identical envelope on
    the success path. Empty-bucket-no-keys discipline prevents
    consumers from reading a stale always-present warnings_out field.
    """
    monkeypatch.chdir(history_project)
    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["--json", "history-grep", "DATABASE_URL"],
        catch_exceptions=False,
    )
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert data["command"] == "history-grep"
    # Real commit existed → pickaxe returned >= 1 row.
    assert data["results"][0]["commits"], f"expected >=1 commit; got {data['results']!r}"

    assert "warnings_out" not in data, (
        f"clean history-grep must NOT surface top-level warnings_out; got {data.get('warnings_out')!r}"
    )
    assert "warnings_out" not in data["summary"], (
        f"clean history-grep must NOT populate summary.warnings_out; got {data['summary'].get('warnings_out')!r}"
    )


# ---------------------------------------------------------------------------
# (2) Subprocess timeout marker — outer-guard fires on synthetic exception
# ---------------------------------------------------------------------------


def test_subprocess_timeout_marker(history_project, monkeypatch):
    """If ``_git_pickaxe`` raises (outside the inner FNF/timeout/rc map),
    the outer-guard surfaces a ``history_pickaxe_failed:`` marker.

    COMPLEMENTARY to the existing CP45/CP46 ``git_errors`` field —
    ``git_errors`` carries the per-pattern inner sentinel (git_not_available
    / git_timeout / git_error); ``warnings_out`` catches anything OUTSIDE
    those (e.g. PermissionError on Windows when the binary path is masked,
    or arbitrary OSError on weird filesystems). The two channels co-exist;
    a single failure can populate both.
    """
    from roam.commands import cmd_history_grep

    def _boom(*a, **kw):
        raise PermissionError("synthetic-permission-error from W607-H test")

    monkeypatch.setattr(cmd_history_grep, "_git_pickaxe", _boom)
    monkeypatch.chdir(history_project)

    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["--json", "history-grep", "DATABASE_URL"],
        catch_exceptions=False,
    )
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    top_wo = data.get("warnings_out") or []
    assert top_wo, (
        f"_git_pickaxe PermissionError must surface top-level warnings_out; got data keys = {sorted(data.keys())!r}"
    )
    assert any(m.startswith("history_pickaxe_failed:") for m in top_wo), top_wo
    assert any("PermissionError" in m for m in top_wo), top_wo
    assert any("synthetic-permission-error from W607-H test" in m for m in top_wo), top_wo


# ---------------------------------------------------------------------------
# (3) Polarity-degraded disclosure — W805-DD silent --polarity gap
# ---------------------------------------------------------------------------


def test_polarity_degraded_disclosure(history_project, monkeypatch):
    """When ``_diff_polarity`` subprocess fails, surface the degrade reason.

    Pre-W607-H ``_diff_polarity`` collapsed FileNotFoundError /
    TimeoutExpired / rc != 0 into a silent ``return None``, identical
    to "no +/- match". W607-H widens the return to a tuple carrying
    the degrade reason; the caller threads that into ``warnings_out``
    via the ``history_polarity_degraded:`` marker family. This is a
    Pattern-2 disclosure axis — the underlying ``--polarity`` feature
    flag was honored, just the subprocess that implements it broke.
    """
    from roam.commands import cmd_history_grep

    # Force the `git show` invocation inside _diff_polarity to timeout.
    real_run = subprocess.run

    def _selective_timeout(cmd, *args, **kwargs):
        # _diff_polarity invokes ['git', 'show', '--unified=0', ...]
        if isinstance(cmd, list) and len(cmd) >= 2 and cmd[0] == "git" and cmd[1] == "show":
            raise subprocess.TimeoutExpired(cmd, kwargs.get("timeout", 20))
        return real_run(cmd, *args, **kwargs)

    monkeypatch.setattr(cmd_history_grep.subprocess, "run", _selective_timeout)
    monkeypatch.chdir(history_project)

    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["--json", "history-grep", "--polarity", "DATABASE_URL"],
        catch_exceptions=False,
    )
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    top_wo = data.get("warnings_out") or []
    assert top_wo, (
        f"--polarity with git show timeout MUST surface top-level warnings_out; got data keys = {sorted(data.keys())!r}"
    )
    degraded = [m for m in top_wo if m.startswith("history_polarity_degraded:")]
    assert degraded, f"expected ``history_polarity_degraded:`` marker; got {top_wo!r}"
    # Reason segment carries the canonical sentinel.
    assert any("polarity_git_timeout" in m for m in degraded), degraded


# ---------------------------------------------------------------------------
# (4) No-match clean → byte-identical envelope (hash stability)
# ---------------------------------------------------------------------------


def test_no_match_byte_identical(history_project, monkeypatch):
    """Clean no-match envelope must NOT carry warnings_out keys.

    Empty-bucket discipline: the W607-H plumbing must NOT leak an
    empty bucket onto a clean no-match envelope. The pre-W607-H
    shape is preserved byte-for-byte when no markers fired. (Note:
    the W805-DD strict-xfail set pins the OTHER axis — that the
    no-match path needs ``state``/``partial_success`` for the
    empty-history class. W607-H does NOT fix W805-DD; it adds the
    subprocess-degrade axis only.)
    """
    monkeypatch.chdir(history_project)
    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["--json", "history-grep", "this_pattern_will_never_match_xyzzy"],
        catch_exceptions=False,
    )
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert "warnings_out" not in data, (
        f"clean no-match envelope must omit top-level warnings_out; "
        f"got data['warnings_out']={data.get('warnings_out')!r}"
    )
    assert "warnings_out" not in data["summary"], (
        f"clean no-match envelope must omit summary.warnings_out; got summary={data['summary']!r}"
    )


# ---------------------------------------------------------------------------
# (5) Three-segment marker shape — prefix:exc_or_reason:detail
# ---------------------------------------------------------------------------


def test_three_segment_marker_shape(history_project, monkeypatch):
    """Marker must have three colon-separated segments.

    Shape contract: ``<prefix>:<exc_or_reason>:<detail>`` so downstream
    consumers can parse the exception class / reason without regex
    gymnastics. Mirrors W607-G cmd_grep / W607-F cmd_complete /
    W607-E cmd_search / W607-A cmd_search_semantic / W607-B
    cmd_retrieve / W607-C cmd_findings / W607-D cmd_dogfood contracts.
    """
    from roam.commands import cmd_history_grep

    def _boom(*a, **kw):
        raise PermissionError("synthetic-shape-detail-from-W607-H")

    monkeypatch.setattr(cmd_history_grep, "_git_pickaxe", _boom)
    monkeypatch.chdir(history_project)

    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["--json", "history-grep", "DATABASE_URL"],
        catch_exceptions=False,
    )
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    top_wo = data.get("warnings_out") or []
    assert top_wo, "pickaxe outer-guard must emit a marker"
    failure_markers = [m for m in top_wo if m.startswith("history_pickaxe_failed:")]
    assert failure_markers, f"expected ``history_pickaxe_failed:`` marker; got {top_wo!r}"

    marker = failure_markers[0]
    parts = marker.split(":", 2)
    assert len(parts) == 3, f"marker must have three colon-separated segments (prefix:exc_class:detail); got {marker!r}"
    assert parts[0] == "history_pickaxe_failed", parts
    assert parts[1] == "PermissionError", parts
    assert parts[2], parts


# ---------------------------------------------------------------------------
# (6) Marker prefix discipline — ``history_*`` not ``grep_*`` etc.
# ---------------------------------------------------------------------------


def test_marker_prefix_history_not_grep_or_search(history_project, monkeypatch):
    """Every surfaced marker uses the canonical ``history_*`` prefix.

    cmd_history_grep is the GIT-SUBPROCESS / through-history axis —
    distinct from:

    * cmd_grep             → ``grep_*`` (W607-G ripgrep/git-grep fan-out)
    * cmd_search           → ``search_*`` (W607-E lexical substring)
    * cmd_complete         → ``complete_*`` (W607-F lexical prefix)
    * cmd_search_semantic  → ``semantic_*`` (W607-A / W605 FTS5 substrate)

    Hard guard against accidental marker-prefix drift in this consumer
    (e.g., a future contributor mis-routing a marker into the
    ``grep_*`` family because of the shared "grep" word in the command
    name). Closes the closed-enum discipline at the cmd_history_grep
    boundary.
    """
    from roam.commands import cmd_history_grep

    def _boom(*a, **kw):
        raise PermissionError("synthetic-prefix-discipline-from-W607-H")

    monkeypatch.setattr(cmd_history_grep, "_git_pickaxe", _boom)
    monkeypatch.chdir(history_project)

    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["--json", "history-grep", "DATABASE_URL"],
        catch_exceptions=False,
    )
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    top_wo = data.get("warnings_out") or []
    assert top_wo, "expected non-empty warnings_out for prefix-consistency check"
    for marker in top_wo:
        assert marker.startswith("history_"), (
            f"every surfaced marker must use the W607-H ``history_*`` prefix "
            f"family (cmd_history_grep subprocess-axis scope); got {marker!r}"
        )
        # Hard distinction from sibling layers.
        assert not marker.startswith("grep_"), (
            f"marker leaked into ``grep_*`` family (cmd_grep W607-G scope); got {marker!r}"
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
# (7) partial_success flips on subprocess failure
# ---------------------------------------------------------------------------


def test_partial_success_flip_on_subprocess_failure(history_project, monkeypatch):
    """Any non-empty warnings_out → summary.partial_success = True.

    Pattern-2 contract: the agent MUST be able to distinguish "clean
    history-grep" from "history-grep ran with subprocess degradation"
    via summary.partial_success alone, independent of the existing
    ``git_errors`` field (which is per-pattern inner-sentinel; this
    is the outer-guard / polarity-degrade lineage).
    """
    from roam.commands import cmd_history_grep

    def _boom(*a, **kw):
        raise PermissionError("synthetic-partial-success-from-W607-H")

    monkeypatch.setattr(cmd_history_grep, "_git_pickaxe", _boom)
    monkeypatch.chdir(history_project)

    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["--json", "history-grep", "DATABASE_URL"],
        catch_exceptions=False,
    )
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert data["summary"].get("partial_success") is True, (
        f"non-empty warnings_out must flip summary.partial_success=True; got summary = {data['summary']!r}"
    )


# ---------------------------------------------------------------------------
# (8) summary.warnings_out mirror — top-level AND summary populated
# ---------------------------------------------------------------------------


def test_summary_warnings_out_mirror(history_project, monkeypatch):
    """Non-empty bucket → both top-level AND summary.warnings_out populated.

    Top-level is needed because the preserved-list field
    (``_ALWAYS_PRESERVED_LIST_FIELDS`` in formatter.py) survives
    ``strip_list_payloads`` in default-detail mode. summary mirror
    gives consumers reading only the summary block visibility too.
    Mirror parity with W607-A/B/C/D/E/F/G consumers.
    """
    from roam.commands import cmd_history_grep

    def _boom(*a, **kw):
        raise PermissionError("synthetic-mirror-from-W607-H")

    monkeypatch.setattr(cmd_history_grep, "_git_pickaxe", _boom)
    monkeypatch.chdir(history_project)

    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["--json", "history-grep", "DATABASE_URL"],
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
# (9) Top-level mirror explicitly checked (W607-A..G discipline parity)
# ---------------------------------------------------------------------------


def test_top_level_warnings_out_mirror(history_project, monkeypatch):
    """Top-level ``warnings_out`` must be present alongside summary mirror.

    The preserved-list-field discipline at
    ``_ALWAYS_PRESERVED_LIST_FIELDS`` requires the top-level mirror so
    the field survives detail-mode list-payload stripping. W607-A
    through W607-G pinned the same discipline; W607-H extends it to
    cmd_history_grep.
    """
    from roam.commands import cmd_history_grep

    def _boom(*a, **kw):
        raise PermissionError("synthetic-top-level-from-W607-H")

    monkeypatch.setattr(cmd_history_grep, "_git_pickaxe", _boom)
    monkeypatch.chdir(history_project)

    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["--json", "history-grep", "DATABASE_URL"],
        catch_exceptions=False,
    )
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)

    top_wo = data.get("warnings_out")
    assert isinstance(top_wo, list) and top_wo, (
        f"top-level warnings_out must be a non-empty list on disclosure path; got {top_wo!r}"
    )


# ---------------------------------------------------------------------------
# (10) W805-DD parity — strict-xfail Pattern-2 disclosure tests must
#      remain xfailed (W607-H does NOT fix the empty-corpus state gap).
# ---------------------------------------------------------------------------


def test_w805_dd_xfail_still_strict():
    """W805-DD strict-xfail Pattern-2 disclosure must remain xfailed.

    W805-DD pins 4 strict-xfail tests on the empty-corpus path
    (silent zero-commits verdict + missing ``state`` +
    ``partial_success=false`` + silent ``--polarity`` on zero commits).
    W607-H adds a COMPLEMENTARY disclosure axis (subprocess-degrade
    via ``warnings_out``), but does NOT address the empty-corpus
    Pattern-2 contract (state-on-empty-history is a separate fix).
    The W805-DD tests must stay xfailed after W607-H lands — a
    drive-by graduation of ANY of those four to PASS would mean
    W607-H accidentally fixed something it wasn't scoped to fix.

    Verify the xfail-strict markers are still present in the W805-DD
    test source. Source-text scan beats invoking pytest-on-pytest;
    if the strict markers were removed, this assertion catches it.
    """
    here = Path(__file__).parent
    w805_dd = here / "test_w805_dd_cmd_history_grep_empty_corpus.py"
    assert w805_dd.exists(), f"W805-DD test file missing at {w805_dd}"
    src = w805_dd.read_text(encoding="utf-8")
    # Count strict-xfail markers — must remain at 4 (the original pin set).
    strict_count = src.count("strict=True")
    assert strict_count == 4, (
        f"W805-DD strict-xfail marker count drift: expected 4, got "
        f"{strict_count}. W607-H must NOT graduate any W805-DD bug; the "
        f"empty-corpus state disclosure is a separate Pattern-2 contract "
        f"orthogonal to the W607-H subprocess-degrade axis."
    )
    # Names of the 4 xfail-strict tests — pin so a future rename without
    # graduation doesn't slip past.
    for test_name in (
        "test_empty_corpus_state_explicit",
        "test_empty_corpus_partial_success_set",
        "test_no_silent_no_matches_on_empty",
        "test_polarity_disclosure_on_empty",
    ):
        assert test_name in src, (
            f"W805-DD xfail-strict test {test_name!r} was renamed or "
            f"removed without graduation. W607-H scope is the subprocess-"
            f"degrade axis only — empty-corpus state disclosure is a "
            f"separate Pattern-2 fix."
        )
