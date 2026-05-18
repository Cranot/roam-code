"""W607-G — ``cmd_grep`` threads ``warnings_out`` onto its JSON envelope.

The W595-W606 substrate-floor Pattern-2 arc plumbed ``warnings_out``
buckets on every silent-fallback substrate reader. W607-A landed the
first consumer-layer wave on ``cmd_search_semantic``. W607-B landed
``cmd_retrieve`` (outer-guard-only). W607-C landed ``cmd_findings``.
W607-D landed ``cmd_dogfood`` (outer-guard-only). W607-E landed
``cmd_search``. W607-F sealed the lexical-search trio with
``cmd_complete`` (lexical-prefix layer). W607-G is the seventh
consumer-layer wave — and opens the SUBPROCESS-shaped axis with
``cmd_grep``.

W978 first-hypothesis check (pre-fix audit)
-------------------------------------------

Before writing this file, audited ``cmd_grep.py`` + the read-only
helper module ``roam.commands.grep_helpers`` head-to-tail. Per
CLAUDE.md: "ripgrep > git grep > fallback (pin via
`ROAM_GREP_ENGINE`)". Inventory of silent-fallback locations:

* ``detect_engine()`` silently returns ``"fallback"`` when
  ROAM_GREP_ENGINE pins an absent binary (e.g. user pinned "rg" but
  ``shutil.which("rg")`` returned None). The user's pin is dropped on
  the floor and the auto-fan-out is silently chosen instead.
* ``run_search()`` / ``_run_and_parse()`` (in ``grep_helpers.py:120``)
  silently swallow ``FileNotFoundError`` + ``subprocess.TimeoutExpired``
  on the subprocess call → returns ``[]`` (looks like a no-match)
  while the subprocess never actually ran.
* ``indexed_file_scan()`` silently ``OSError``-skips unreadable files
  in its per-file ``read_text`` loop (``grep_helpers.py:171``).

cmd_grep does NOT call any W605-plumbed substrate (search_fts /
fts5_available / tfidf_populated / onnx_populated / search_stored). Its
substrate is the SUBPROCESS axis — a fundamentally distinct failure
shape from the DB-shape lexical-search trio (search / complete /
search_semantic). Therefore the W607-G threading approach is
OUTER-GUARD at the cmd_grep boundary (the helper module is shared
with refs-text / delete-check / history-grep so MUST stay read-only
per the task contract; future W607-H peer for cmd_refs_text /
cmd_history_grep would benefit from the same boundary discipline).

Marker family is ``grep_*`` — NOT ``search_*`` (W607-E lexical-substring
layer), NOT ``complete_*`` (W607-F lexical-prefix layer), NOT
``semantic_*`` (W605/W607-A FTS5-BM25 substrate), NOT ``history_*``
(W607-H candidate for the through-history pickaxe). The marker-prefix
discipline test below pins this closed-enum distinction.

W907 verify-cycle check
-----------------------

No "duplicated to avoid cycle" docstrings were added. The
``warnings_out: list[str] = []`` local is a plain accumulator (mirrors
cmd_complete W607-F / cmd_search W607-E / cmd_dogfood W607-D /
cmd_findings W607-C / cmd_retrieve W607-B / cmd_search_semantic W607-A
disclosure idioms); no shared module was created or hoisted. The
helper module ``grep_helpers.py`` was intentionally NOT modified —
the threading lives at the cmd_grep boundary so the shared substrate
stays callable from refs-text / delete-check / history-grep.

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
from _helpers.repo_root import repo_root  # noqa: E402, F401

# ---------------------------------------------------------------------------
# Fixture: a small indexed project so grep has a real corpus.
# ---------------------------------------------------------------------------


@pytest.fixture
def grep_project(project_factory):
    return project_factory(
        {
            "auth/login.py": (
                "def authenticate_user(username, password):\n"
                "    '''Authenticate a user with credentials.'''\n"
                "    return True\n"
                "\n"
                "def authorize_user(username):\n"
                "    '''Authorize a user.'''\n"
                "    return True\n"
            ),
            "db/connection.py": (
                "class DatabaseConnection:\n"
                "    def open_database(self):\n"
                "        '''Open a database connection.'''\n"
                "        pass\n"
            ),
        }
    )


# ---------------------------------------------------------------------------
# (1) Happy path — ripgrep / git-grep present, matches found → no warnings_out
# ---------------------------------------------------------------------------


def test_clean_happy_path(grep_project, monkeypatch):
    """Clean grep on a populated corpus → envelope has no warnings_out.

    Hash-stable: an empty bucket must produce a byte-identical envelope
    on the success path. The empty-bucket-no-keys discipline ensures
    consumers can't accidentally read a stale or always-present
    warnings_out field.
    """
    monkeypatch.chdir(grep_project)
    # Force auto so we don't trip the pin-missing path even on hosts
    # that happen to have neither rg nor git (the test is about the
    # CLEAN happy path; if no engine is available, indexed_scan still
    # runs and matches still come back, but the fan-out fallback marker
    # would correctly fire — we sidestep that env-dependence here by
    # only asserting the no-warnings path on hosts where some engine
    # IS available).
    monkeypatch.setenv("ROAM_GREP_ENGINE", "auto")
    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["--json", "grep", "authenticate"],
        catch_exceptions=False,
    )
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert data["command"] == "grep"

    # If the host has no rg AND no git on PATH, the fan-out fallback
    # marker WILL fire even on the happy path — that's CORRECT
    # behaviour (the fallback IS happening silently in the substrate).
    # Skip the no-warnings assertion in that environment.
    import shutil as _sh

    rg_present = bool(_sh.which("rg"))
    git_present = bool(_sh.which("git"))
    if not rg_present and not git_present:
        pytest.skip(
            "host has neither 'rg' nor 'git' on PATH; fan-out fallback marker is the CORRECT signal in that environment"
        )

    assert "warnings_out" not in data, (
        f"clean grep must NOT surface top-level warnings_out; got {data.get('warnings_out')!r}"
    )
    assert "warnings_out" not in data["summary"], (
        f"clean grep must NOT populate summary.warnings_out; got {data['summary'].get('warnings_out')!r}"
    )


# ---------------------------------------------------------------------------
# (2) Engine fallback disclosed — ROAM_GREP_ENGINE=ripgrep but rg absent
# ---------------------------------------------------------------------------


def test_engine_fallback_disclosed(grep_project, monkeypatch):
    """Pin ROAM_GREP_ENGINE=ripgrep but mask 'rg' → pin-missing marker fires.

    Pattern-2 contract: when a user pins an engine via env var and the
    binary is absent, ``detect_engine`` silently returns ``"fallback"``
    and the auto-fan-out picks a different engine. That's exactly the
    silent-fallback shape — disclose the unhonored pin.
    """
    import shutil

    real_which = shutil.which

    def _mock_which(name, *a, **kw):
        # Mask only 'rg' — leave 'git' and others alone so the auto fan-out
        # still has a real engine to fall back to.
        if name == "rg":
            return None
        return real_which(name, *a, **kw)

    monkeypatch.setattr(shutil, "which", _mock_which)
    # Also mask in the grep_helpers module's local import binding.
    from roam.commands import grep_helpers

    monkeypatch.setattr(grep_helpers.shutil, "which", _mock_which)
    monkeypatch.setenv("ROAM_GREP_ENGINE", "ripgrep")
    monkeypatch.chdir(grep_project)

    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["--json", "grep", "authenticate"],
        catch_exceptions=False,
    )
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)

    top_wo = data.get("warnings_out") or []
    assert top_wo, (
        f"pinned ROAM_GREP_ENGINE=ripgrep with rg absent MUST emit "
        f"top-level warnings_out; got data keys = {sorted(data.keys())!r}"
    )
    assert any(m.startswith("grep_engine_pin_missing:ripgrep") for m in top_wo), (
        f"expected ``grep_engine_pin_missing:ripgrep`` marker; got {top_wo!r}"
    )


# ---------------------------------------------------------------------------
# (3) All-engines-fail pipeline — both rg and git masked → fan-out marker
# ---------------------------------------------------------------------------


def test_all_engines_fail_pipeline(grep_project, monkeypatch):
    """Mask both rg AND git on auto → fan-out fallback marker + partial_success.

    Pattern-2 contract: when ``detect_engine`` returns ``"fallback"`` (no
    rg/git on PATH) AND ``indexed_file_scan`` produces the results, the
    envelope MUST disclose the fan-out lineage — not just relabel
    ``used_engine`` to ``"indexed_scan"`` silently.
    """
    import shutil

    real_which = shutil.which

    def _mock_which(name, *a, **kw):
        if name in ("rg", "git"):
            return None
        return real_which(name, *a, **kw)

    monkeypatch.setattr(shutil, "which", _mock_which)
    from roam.commands import grep_helpers

    monkeypatch.setattr(grep_helpers.shutil, "which", _mock_which)
    monkeypatch.setenv("ROAM_GREP_ENGINE", "auto")
    monkeypatch.chdir(grep_project)

    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["--json", "grep", "authenticate"],
        catch_exceptions=False,
    )
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)

    top_wo = data.get("warnings_out") or []
    assert top_wo, f"all engines masked MUST emit top-level warnings_out; got data keys = {sorted(data.keys())!r}"
    assert any(m.startswith("grep_engine_fanout_fallback:auto") for m in top_wo), (
        f"expected ``grep_engine_fanout_fallback:auto`` marker on "
        f"auto-fan-out with neither rg nor git present; "
        f"got {top_wo!r}"
    )
    assert data["summary"].get("partial_success") is True, (
        f"non-empty warnings_out must flip summary.partial_success=True; got summary = {data['summary']!r}"
    )


# ---------------------------------------------------------------------------
# (4) No-match clean → byte-identical envelope (hash stability)
# ---------------------------------------------------------------------------


def test_no_match_byte_identical(grep_project, monkeypatch):
    """Clean envelope with no matches must NOT carry warnings_out keys.

    Empty-bucket discipline: the W607-G plumbing must NOT leak the
    empty bucket onto a no-match envelope. The pre-W607-G envelope shape
    is preserved byte-for-byte when no markers fired.
    """
    import shutil as _sh

    monkeypatch.setenv("ROAM_GREP_ENGINE", "auto")
    monkeypatch.chdir(grep_project)
    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["--json", "grep", "this_pattern_will_never_match_anything_xyzzy"],
        catch_exceptions=False,
    )
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)

    # If the host has no rg AND no git on PATH, the fan-out marker WILL
    # fire — sidestep that env-dependence (the test is about the
    # no-match clean path on a properly-equipped host).
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
# (5) Three-segment marker shape — prefix:exc_class:detail
# ---------------------------------------------------------------------------


def test_three_segment_marker_shape(grep_project, monkeypatch):
    """Marker must have three colon-separated segments.

    The marker shape MUST be ``<prefix>:<exc_class>:<detail>`` — three
    colon-separated segments — so downstream consumers can parse the
    exception class without regex gymnastics. Mirrors cmd_complete
    W607-F / cmd_search W607-E / cmd_findings W607-C / cmd_retrieve
    W607-B / cmd_dogfood W607-D outer-guard contracts.
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
    monkeypatch.chdir(grep_project)

    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["--json", "grep", "authenticate"],
        catch_exceptions=False,
    )
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)

    top_wo = data.get("warnings_out") or []
    assert top_wo, "engine-pin-missing path must emit at least one marker"

    pin_markers = [m for m in top_wo if m.startswith("grep_engine_pin_missing:")]
    assert pin_markers, f"engine-pin-missing path must emit grep_engine_pin_missing marker; got {top_wo!r}"

    marker = pin_markers[0]
    parts = marker.split(":", 2)
    assert len(parts) == 3, f"marker must have three colon-separated segments (prefix:exc_class:detail); got {marker!r}"
    assert parts[0] == "grep_engine_pin_missing", parts
    # exc_class slot carries the engine name for pin-missing markers
    # (this is the canonical disclosure shape — the "exc_class" slot
    # is semantic: it names WHAT failed, not necessarily a Python
    # exception class).
    assert parts[1] in {"ripgrep", "git"}, parts
    assert parts[2], parts


# ---------------------------------------------------------------------------
# (6) Marker prefix discipline — ``grep_*`` not ``search_*`` etc.
# ---------------------------------------------------------------------------


def test_marker_prefix_grep_not_search_or_history(grep_project, monkeypatch):
    """Every surfaced marker uses the canonical ``grep_*`` prefix family.

    cmd_grep is the SUBPROCESS axis — distinct from:

    * cmd_search           → ``search_*`` (lexical substring)
    * cmd_complete         → ``complete_*`` (lexical prefix)
    * cmd_search_semantic  → ``semantic_*`` (FTS5-BM25 W605 substrate)
    * cmd_history_grep     → ``history_*`` (through-history pickaxe, W607-H)

    Hard guard against accidental marker-prefix drift in this consumer
    (e.g., a future contributor mis-routing a marker into the
    ``search_*`` or ``history_*`` family). Closes the closed-enum
    discipline at the cmd_grep boundary.
    """
    import shutil

    real_which = shutil.which

    def _mock_which(name, *a, **kw):
        if name in ("rg", "git"):
            return None
        return real_which(name, *a, **kw)

    monkeypatch.setattr(shutil, "which", _mock_which)
    from roam.commands import grep_helpers

    monkeypatch.setattr(grep_helpers.shutil, "which", _mock_which)
    monkeypatch.setenv("ROAM_GREP_ENGINE", "auto")
    monkeypatch.chdir(grep_project)

    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["--json", "grep", "authenticate"],
        catch_exceptions=False,
    )
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    top_wo = data.get("warnings_out") or []
    assert top_wo, "expected non-empty warnings_out for prefix-consistency check"
    for marker in top_wo:
        assert marker.startswith("grep_"), (
            f"every surfaced marker must use the W607-G ``grep_*`` prefix "
            f"family (cmd_grep subprocess-axis scope); got {marker!r}"
        )
        # Hard distinction from the sibling lexical / semantic / history layers.
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
        assert not marker.startswith("history_"), (
            f"marker leaked into ``history_*`` family (cmd_history_grep W607-H scope); got {marker!r}"
        )


# ---------------------------------------------------------------------------
# (7) partial_success flips when any marker present
# ---------------------------------------------------------------------------


def test_partial_success_flip_on_engine_failure(grep_project, monkeypatch):
    """Any non-empty warnings_out → summary.partial_success = True.

    Pattern-2 contract: the agent MUST be able to distinguish "clean
    grep" from "grep ran with substrate degradation" via
    summary.partial_success alone.
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
    monkeypatch.chdir(grep_project)

    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["--json", "grep", "authenticate"],
        catch_exceptions=False,
    )
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert data["summary"].get("partial_success") is True, (
        f"non-empty warnings_out must flip summary.partial_success=True; got summary = {data['summary']!r}"
    )


# ---------------------------------------------------------------------------
# (8) summary.warnings_out is populated alongside top-level on disclosure
# ---------------------------------------------------------------------------


def test_summary_warnings_out_mirror(grep_project, monkeypatch):
    """Non-empty bucket → both top-level AND summary.warnings_out populated.

    Top-level is needed because the preserved-list field
    (``_ALWAYS_PRESERVED_LIST_FIELDS`` in formatter.py) survives
    ``strip_list_payloads`` in default-detail mode. summary mirror gives
    consumers reading only the summary block visibility too.
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
    monkeypatch.chdir(grep_project)

    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["--json", "grep", "authenticate"],
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
    # Mirror — top-level and summary content must be equal.
    assert sorted(data["warnings_out"]) == sorted(data["summary"]["warnings_out"]), (
        f"top-level vs summary.warnings_out must be equal; "
        f"top={data['warnings_out']!r} summary={data['summary']['warnings_out']!r}"
    )


# ---------------------------------------------------------------------------
# (9) Top-level mirror explicitly checked (W607-A/B/C/D/E/F discipline parity)
# ---------------------------------------------------------------------------


def test_top_level_warnings_out_mirror(grep_project, monkeypatch):
    """Top-level ``warnings_out`` must be present alongside summary mirror.

    The preserved-list-field discipline at ``_ALWAYS_PRESERVED_LIST_FIELDS``
    requires the top-level mirror so the field survives detail-mode
    list-payload stripping. cmd_search_semantic W607-A + cmd_retrieve
    W607-B + cmd_findings W607-C + cmd_dogfood W607-D + cmd_search
    W607-E + cmd_complete W607-F pinned the same discipline; W607-G
    extends it to cmd_grep.
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
    monkeypatch.chdir(grep_project)

    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["--json", "grep", "authenticate"],
        catch_exceptions=False,
    )
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)

    top_wo = data.get("warnings_out")
    assert isinstance(top_wo, list) and top_wo, (
        f"top-level warnings_out must be a non-empty list on disclosure path; got {top_wo!r}"
    )


# ---------------------------------------------------------------------------
# (10) Outer-guard for run_search — TimeoutExpired surfaces as marker
# ---------------------------------------------------------------------------


def test_run_search_exception_outer_guarded(grep_project, monkeypatch):
    """If ``run_search`` raises (despite the inner swallow), outer-guard fires.

    ``_run_and_parse`` swallows ``FileNotFoundError`` and
    ``TimeoutExpired`` silently — but other exceptions (e.g.
    PermissionError on Windows when the binary path is masked) propagate.
    The W607-G outer-guard catches THOSE and threads the marker.
    """
    from roam.commands import cmd_grep

    def _boom_run_search(**kw):
        raise PermissionError("synthetic-permission-error from W607-G test")

    monkeypatch.setattr(cmd_grep, "run_search", _boom_run_search)
    monkeypatch.setenv("ROAM_GREP_ENGINE", "auto")
    monkeypatch.chdir(grep_project)

    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["--json", "grep", "authenticate"],
        catch_exceptions=False,
    )
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)

    top_wo = data.get("warnings_out") or []
    assert top_wo, (
        f"run_search PermissionError must surface top-level warnings_out; got data keys = {sorted(data.keys())!r}"
    )
    # The marker name depends on which engine ``detect_engine`` picked
    # — assert the marker starts with the right family and carries the
    # exc class + synthetic detail.
    assert any(
        m.startswith("grep_ripgrep_failed:")
        or m.startswith("grep_git_grep_failed:")
        or m.startswith("grep_engine_failed:")
        for m in top_wo
    ), f"expected grep_<engine>_failed: marker for run_search PermissionError; got {top_wo!r}"
    assert any("PermissionError" in m for m in top_wo), top_wo
    assert any("synthetic-permission-error from W607-G test" in m for m in top_wo), top_wo
