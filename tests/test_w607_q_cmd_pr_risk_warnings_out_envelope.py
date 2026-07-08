"""W607-Q -- ``cmd_pr_risk`` threads ``warnings_out`` onto its envelope.

Seventeenth-in-batch W607 consumer-layer arc. DB-shape continuation after
W607-K (cmd_describe flagship aggregator), W607-L (cmd_minimap DB-shape
aggregator), W607-M (cmd_health CI-gate flagship), W607-N (cmd_doctor
environment aggregator), W607-O (cmd_dashboard unified status surface),
and W607-P (cmd_audit one-shot architecture audit). cmd_pr_risk is the
**PR-time risk aggregator** that composes ~9 substrate helpers
(``get_changed_files`` / ``resolve_changed_to_db`` / ``_detect_author`` /
``build_symbol_graph`` / ``_compute_surprise`` / ``detect_layers`` /
``_author_familiarity`` / ``_minor_contributor_risk`` /
``_emit_pr_risk_findings``) into a single structured-JSON envelope.

W978 first-hypothesis check (pre-fix audit)
-------------------------------------------

cmd_pr_risk's substrate-call sites are direct helper invocations
(``get_changed_files(...)`` etc.) -- NOT a uniform ``_capture`` boundary
like cmd_audit. Each helper has its own internal try/except returning a
safe floor (``[]`` for changed files on subprocess error; ``(0, 0)`` for
diff stats; ``None`` for missing git config). But a helper itself can
still raise BEFORE reaching that floor (e.g., a downstream refactor
changes the SQL shape, or networkx blows up during build_symbol_graph,
or a third-party patch surfaces an unexpected raise). The outer call
sites in pr_risk() previously had no guards, so the envelope crashed
whole. W607-Q wraps each substrate boundary with
``_run_check(phase, fn, *args)`` so the raise becomes a
``pr_risk_<phase>_failed:<exc_class>:<detail>`` marker via
``warnings_out`` and the envelope still emits the remaining sections
cleanly.

Marker family is ``pr_risk_*`` -- NOT ``audit_*`` (W607-P), NOT
``dashboard_*`` (W607-O), NOT ``doctor_*`` (W607-N), NOT ``health_*``
(W607-M), NOT ``describe_*`` (W607-K), NOT ``minimap_*`` (W607-L). The
marker-prefix discipline test pins this closed-enum distinction.

W805-EEEE intersection
----------------------

``get_changed_files`` is the shared helper the W805-EEEE pin documented
as silently returning ``[]`` on subprocess failure
(FileNotFoundError/TimeoutExpired). W607-Q is ADDITIVE -- for those
canonical raise types the helper still floors to ``[]`` per the pin;
for ANY OTHER raise that escapes the helper's own try/except, W607-Q
surfaces ``pr_risk_get_changed_files_failed:<exc_class>:<detail>`` via
warnings_out and the envelope still emits cleanly. The shared helper's
silent-floor contract is preserved verbatim.

W907 verify-cycle check
-----------------------

No "duplicated to avoid cycle" docstrings added. Both networkx and the
graph builders are already deferred-imported inline at their call site
(cost-deferred lazy import, not a cycle hedge); no remediation needed.

Pattern 2 / W989 interplay
--------------------------

cmd_pr_risk already shipped a W989 ``_warnings_out`` accumulator (the
Pattern-2 canonical-level fallback list). W607-Q adds a SEPARATE
``_w607q_warnings_out`` accumulator for substrate-CALL markers. At
envelope time the two lists are concatenated into a single combined
``warnings_out`` field; consumers demux by marker shape (W989 has the
``"Config field 'level' value ..."`` prefix; W607-Q has the
three-segment ``pr_risk_<phase>_failed:<exc_class>:<detail>`` shape).

LAW 4 note: warning markers are diagnostic strings, NOT
``agent_contract.facts`` content, and therefore not subject to the
concrete-noun-terminal lint.
"""

from __future__ import annotations

import ast
import json as _json
import os
import sys
from pathlib import Path

import pytest
from click.testing import CliRunner

sys.path.insert(0, str(Path(__file__).parent))
from conftest import git_init, index_in_process  # noqa: E402

# ---------------------------------------------------------------------------
# Helpers -- invoke pr-risk via the Click group (uses --json flag on group)
# ---------------------------------------------------------------------------


def _invoke_pr_risk(runner: CliRunner, cwd, json_mode: bool = True, *extra):
    """Invoke ``roam pr-risk`` through the group so ``--json`` is honoured."""
    from roam.cli import cli

    args = []
    if json_mode:
        args.append("--json")
    args.append("pr-risk")
    args.extend(extra)

    old_cwd = os.getcwd()
    try:
        os.chdir(str(cwd))
        result = runner.invoke(cli, args, catch_exceptions=False)
    finally:
        os.chdir(old_cwd)
    return result


# ---------------------------------------------------------------------------
# Fixture -- empty indexed corpus (no unstaged changes -> no-changes branch).
# We use the no-changes/empty-corpus path for the byte-identical regression
# guard (no changed_files == clean envelope without warnings_out).
# ---------------------------------------------------------------------------


@pytest.fixture
def cli_runner():
    return CliRunner()


@pytest.fixture
def pr_risk_project(tmp_path, monkeypatch):
    """Indexed corpus with NO pending diff. The no-changes branch exercises
    the clean ``get_changed_files -> [] -> no-changes envelope`` path; we
    monkeypatch ``get_changed_files`` to inject failures or to return a
    populated list per-test.
    """
    proj = tmp_path / "pr_risk_w607q_project"
    proj.mkdir()
    (proj / ".gitignore").write_text(".roam/\n")
    src = proj / "src"
    src.mkdir()
    (src / "main.py").write_text(
        "def main():\n    helper()\n    return 1\n\n"
        "def helper():\n    inner()\n    return 42\n\n"
        "def inner():\n    return 7\n",
        encoding="utf-8",
    )
    (src / "utils.py").write_text(
        'def format_name(first, last):\n    return f"{first} {last}"\n\ndef shout(msg):\n    return msg.upper()\n',
        encoding="utf-8",
    )
    git_init(proj)
    monkeypatch.chdir(proj)
    out, rc = index_in_process(proj, "--force")
    assert rc == 0, f"index failed:\n{out}"
    return proj


@pytest.fixture
def pr_risk_project_with_changes(pr_risk_project):
    """Variant of ``pr_risk_project`` that has unstaged modifications so
    the main pr-risk envelope path is exercised. We touch ``src/main.py``
    so ``git diff --name-only`` reports a change.
    """
    (pr_risk_project / "src" / "main.py").write_text(
        "def main():\n    helper()\n    return 2\n\n"  # changed return value
        "def helper():\n    inner()\n    return 42\n\n"
        "def inner():\n    return 7\n",
        encoding="utf-8",
    )
    return pr_risk_project


# ---------------------------------------------------------------------------
# (1) Happy path -- empty corpus / no-changes -> envelope omits warnings_out
# ---------------------------------------------------------------------------


def test_pr_risk_empty_corpus_envelope_byte_identical(cli_runner, pr_risk_project):
    """Clean pr-risk on no-changes corpus -> no warnings_out key.

    Hash-stable: an empty W607-Q bucket on the no-changes branch must
    produce a clean envelope WITHOUT warnings_out / partial_success keys.
    The empty-bucket-no-keys discipline ensures consumers can't
    accidentally read a stale or always-present warnings_out field on
    the no-changes path. Mirrors W607-N/O/P contract.

    Note: the MAIN-path envelope (when there ARE changes) currently
    ships an always-present ``warnings_out=[]`` field from the
    pre-existing W989 plumbing -- that pre-W607-Q shape is NOT changed
    by this wave (regression guard on the W989 surface). Only the
    no-changes and index-stale envelopes are affected by the
    empty-bucket-no-keys discipline.
    """
    result = _invoke_pr_risk(cli_runner, pr_risk_project, True)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)
    assert data["command"] == "pr-risk"
    # No-changes branch: verdict carries the canonical sentinel.
    verdict = data["summary"]["verdict"]
    assert isinstance(verdict, str) and verdict, verdict
    assert verdict == "no-changes", verdict
    # Empty-bucket discipline: NO warnings_out keys on the no-changes branch.
    assert "warnings_out" not in data, (
        f"clean no-changes pr-risk must NOT surface top-level warnings_out; got {data.get('warnings_out')!r}"
    )
    assert "warnings_out" not in data["summary"], (
        f"clean no-changes pr-risk must NOT populate summary.warnings_out; got {data['summary'].get('warnings_out')!r}"
    )
    # On the clean no-changes path partial_success must remain False
    # (auto-default from json_envelope) -- only the disclosure path flips it.
    assert data["summary"].get("partial_success") is False, (
        f"clean no-changes pr-risk summary.partial_success must remain "
        f"False on the auto-default path; got {data['summary']!r}"
    )


# ---------------------------------------------------------------------------
# (2) Each substrate failure marker fires when that helper raises
# ---------------------------------------------------------------------------


def _patch_helper(monkeypatch, attr_name: str, exc):
    """Patch ``cmd_pr_risk.<attr_name>`` to raise ``exc`` unconditionally."""
    from roam.commands import cmd_pr_risk

    def _raise(*args, **kwargs):
        raise exc

    monkeypatch.setattr(cmd_pr_risk, attr_name, _raise)


def test_pr_risk_get_changed_files_failure_marker_format(cli_runner, pr_risk_project, monkeypatch):
    """If ``get_changed_files`` raises, surface ``pr_risk_get_changed_files_failed:``.

    W805-EEEE intersection: the helper's own try/except handles
    FileNotFoundError + TimeoutExpired (silent floor preserved). W607-Q
    surfaces OTHER raise classes -- this test injects a RuntimeError that
    escapes the helper's own guards.
    """
    _patch_helper(
        monkeypatch,
        "get_changed_files",
        RuntimeError("synthetic-get-changed-files-from-W607-Q"),
    )

    result = _invoke_pr_risk(cli_runner, pr_risk_project, True)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    top_wo = data.get("warnings_out") or data["summary"].get("warnings_out") or []
    assert top_wo, f"get_changed_files RuntimeError must surface warnings_out; got data keys = {sorted(data.keys())!r}"
    markers = [m for m in top_wo if m.startswith("pr_risk_get_changed_files_failed:")]
    assert markers, f"expected ``pr_risk_get_changed_files_failed:`` marker; got {top_wo!r}"
    assert any("RuntimeError" in m for m in markers), markers
    assert any("synthetic-get-changed-files-from-W607-Q" in m for m in markers), markers


def test_pr_risk_resolve_changed_to_db_failure_marker_format(cli_runner, pr_risk_project_with_changes, monkeypatch):
    """If ``resolve_changed_to_db`` raises, surface ``pr_risk_resolve_changed_to_db_failed:``."""
    _patch_helper(
        monkeypatch,
        "resolve_changed_to_db",
        PermissionError("synthetic-resolve-from-W607-Q"),
    )

    result = _invoke_pr_risk(cli_runner, pr_risk_project_with_changes, True)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    top_wo = data.get("warnings_out") or []
    assert top_wo, (
        f"resolve_changed_to_db PermissionError must surface top-level "
        f"warnings_out; got data keys = {sorted(data.keys())!r}"
    )
    markers = [m for m in top_wo if m.startswith("pr_risk_resolve_changed_to_db_failed:")]
    assert markers, f"expected ``pr_risk_resolve_changed_to_db_failed:`` marker; got {top_wo!r}"
    assert any("PermissionError" in m for m in markers), markers


def test_pr_risk_detect_author_failure_marker_format(cli_runner, pr_risk_project_with_changes, monkeypatch):
    """If ``_detect_author`` raises, surface ``pr_risk_detect_author_failed:``."""
    _patch_helper(
        monkeypatch,
        "_detect_author",
        RuntimeError("synthetic-detect-author-from-W607-Q"),
    )

    result = _invoke_pr_risk(cli_runner, pr_risk_project_with_changes, True)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    top_wo = data.get("warnings_out") or []
    markers = [m for m in top_wo if m.startswith("pr_risk_detect_author_failed:")]
    assert markers, f"expected ``pr_risk_detect_author_failed:`` marker; got {top_wo!r}"


def test_pr_risk_compute_surprise_failure_marker_format(cli_runner, pr_risk_project_with_changes, monkeypatch):
    """If ``_compute_surprise`` raises, surface ``pr_risk_compute_surprise_failed:``."""
    _patch_helper(
        monkeypatch,
        "_compute_surprise",
        RuntimeError("synthetic-compute-surprise-from-W607-Q"),
    )

    result = _invoke_pr_risk(cli_runner, pr_risk_project_with_changes, True)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    top_wo = data.get("warnings_out") or []
    markers = [m for m in top_wo if m.startswith("pr_risk_compute_surprise_failed:")]
    assert markers, f"expected ``pr_risk_compute_surprise_failed:`` marker; got {top_wo!r}"


def test_pr_risk_author_familiarity_failure_marker_format(cli_runner, pr_risk_project_with_changes, monkeypatch):
    """If ``_author_familiarity`` raises, surface ``pr_risk_author_familiarity_failed:``."""
    _patch_helper(
        monkeypatch,
        "_author_familiarity",
        RuntimeError("synthetic-author-familiarity-from-W607-Q"),
    )

    result = _invoke_pr_risk(cli_runner, pr_risk_project_with_changes, True)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    top_wo = data.get("warnings_out") or []
    markers = [m for m in top_wo if m.startswith("pr_risk_author_familiarity_failed:")]
    assert markers, f"expected ``pr_risk_author_familiarity_failed:`` marker; got {top_wo!r}"


def test_pr_risk_minor_contributor_risk_failure_marker_format(cli_runner, pr_risk_project_with_changes, monkeypatch):
    """If ``_minor_contributor_risk`` raises, surface ``pr_risk_minor_contributor_risk_failed:``."""
    _patch_helper(
        monkeypatch,
        "_minor_contributor_risk",
        RuntimeError("synthetic-minor-from-W607-Q"),
    )

    result = _invoke_pr_risk(cli_runner, pr_risk_project_with_changes, True)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    top_wo = data.get("warnings_out") or []
    markers = [m for m in top_wo if m.startswith("pr_risk_minor_contributor_risk_failed:")]
    assert markers, f"expected ``pr_risk_minor_contributor_risk_failed:`` marker; got {top_wo!r}"


# ---------------------------------------------------------------------------
# (3) warnings_out lands in envelope (top-level AND summary mirror)
# ---------------------------------------------------------------------------


def test_pr_risk_warnings_out_in_envelope(cli_runner, pr_risk_project_with_changes, monkeypatch):
    """Non-empty bucket -> both top-level AND summary.warnings_out populated.

    Top-level is needed because the preserved-list field
    (``_ALWAYS_PRESERVED_LIST_FIELDS`` in formatter.py) survives
    ``strip_list_payloads`` in default-detail mode. Summary mirror
    gives consumers reading only the summary block visibility too.
    Mirror parity with W607-A..P consumers.
    """
    _patch_helper(
        monkeypatch,
        "resolve_changed_to_db",
        RuntimeError("synthetic-mirror-from-W607-Q"),
    )

    result = _invoke_pr_risk(cli_runner, pr_risk_project_with_changes, True)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    assert data.get("warnings_out"), (
        f"top-level warnings_out missing on disclosure path; keys = {sorted(data.keys())!r}"
    )
    assert data["summary"].get("warnings_out"), (
        f"summary.warnings_out missing on disclosure path; got summary = {data['summary']!r}"
    )


# ---------------------------------------------------------------------------
# (4) partial_success flips when ANY pr_risk helper raises
# ---------------------------------------------------------------------------


def test_partial_success_set_when_pr_risk_helper_raises(cli_runner, pr_risk_project_with_changes, monkeypatch):
    """Any non-empty warnings_out -> summary.partial_success = True.

    Pattern-2 contract: the agent MUST be able to distinguish "clean
    pr-risk" from "pr-risk ran with substrate degradation" via
    summary.partial_success alone, independent of the verdict text.
    cmd_pr_risk previously only flipped partial_success on the W989
    canonical-level fallback axis -- the W607-Q wave extends the flip
    to ANY substrate-CALL raise.
    """
    # ``build_symbol_graph`` is imported lazily inside the pr_risk()
    # body via ``from roam.graph.builder import build_symbol_graph``,
    # so we patch at the source module rather than at cmd_pr_risk's
    # namespace (where the symbol won't appear until first call).
    from roam.graph import builder as _gb

    def _raise(*a, **kw):
        raise RuntimeError("synthetic-partial-success-from-W607-Q")

    monkeypatch.setattr(_gb, "build_symbol_graph", _raise)

    result = _invoke_pr_risk(cli_runner, pr_risk_project_with_changes, True)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)
    assert data["summary"].get("partial_success") is True, (
        f"non-empty warnings_out must flip summary.partial_success=True; got summary = {data['summary']!r}"
    )


# ---------------------------------------------------------------------------
# (5) Three-segment marker shape -- prefix:exc_class:detail
# ---------------------------------------------------------------------------


def test_three_segment_marker_shape(cli_runner, pr_risk_project_with_changes, monkeypatch):
    """Marker must have three colon-separated segments.

    Shape contract: ``<prefix>:<exc_class>:<detail>`` so downstream
    consumers can parse the exception class without regex gymnastics.
    Mirrors W607-A..P contracts.
    """
    _patch_helper(
        monkeypatch,
        "resolve_changed_to_db",
        PermissionError("synthetic-shape-detail-from-W607-Q"),
    )

    result = _invoke_pr_risk(cli_runner, pr_risk_project_with_changes, True)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    top_wo = data.get("warnings_out") or []
    assert top_wo, "resolve_changed_to_db guard must emit a marker"
    failure_markers = [m for m in top_wo if m.startswith("pr_risk_resolve_changed_to_db_failed:")]
    assert failure_markers, f"expected pr_risk_resolve_changed_to_db_failed: marker; got {top_wo!r}"

    marker = failure_markers[0]
    parts = marker.split(":", 2)
    assert len(parts) == 3, f"marker must have three colon-separated segments (prefix:exc_class:detail); got {marker!r}"
    assert parts[0] == "pr_risk_resolve_changed_to_db_failed", parts
    assert parts[1] == "PermissionError", parts
    assert parts[2], parts


# ---------------------------------------------------------------------------
# (6) Marker-prefix discipline -- ``pr_risk_*`` not audit/dashboard/etc.
# ---------------------------------------------------------------------------


def test_marker_prefix_pr_risk_not_audit_or_dashboard(cli_runner, pr_risk_project_with_changes, monkeypatch):
    """Every surfaced marker uses the canonical ``pr_risk_*`` prefix.

    cmd_pr_risk is the PR-TIME-RISK-AGGREGATOR axis -- distinct from:

    * cmd_audit            -> ``audit_*`` (W607-P one-shot architecture audit)
    * cmd_dashboard        -> ``dashboard_*`` (W607-O unified status)
    * cmd_doctor           -> ``doctor_*`` (W607-N environment aggregator)
    * cmd_health           -> ``health_*`` (W607-M CI-gate flagship)
    * cmd_describe         -> ``describe_*`` (W607-K flagship aggregator)
    * cmd_minimap          -> ``minimap_*`` (W607-L DB-shape aggregator)
    * cmd_grep             -> ``grep_*`` (W607-G ripgrep/git-grep fan-out)
    * cmd_history_grep     -> ``history_*`` (W607-H pickaxe)
    * cmd_refs_text        -> ``refs_text_*`` (W607-I string-audit)
    * cmd_delete_check     -> ``delete_check_*`` (W607-J diff-gate)
    * cmd_search           -> ``search_*`` (W607-E lexical)
    * cmd_complete         -> ``complete_*`` (W607-F prefix)
    * cmd_search_semantic  -> ``semantic_*`` (W607-A FTS5)
    * cmd_findings         -> ``findings_query_*`` (W607-C registry)
    * cmd_dogfood          -> ``dogfood_*`` (W607-D corpus loader)
    * cmd_retrieve         -> ``retrieve_*`` (W607-B pipeline)

    Hard guard against accidental marker-prefix drift.
    """
    _patch_helper(
        monkeypatch,
        "resolve_changed_to_db",
        PermissionError("synthetic-prefix-discipline-from-W607-Q"),
    )

    result = _invoke_pr_risk(cli_runner, pr_risk_project_with_changes, True)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)
    top_wo = data.get("warnings_out") or []
    assert top_wo, "expected non-empty warnings_out for prefix-consistency check"
    for marker in top_wo:
        # Skip W989 canonical-level warnings (they don't use the W607-Q
        # marker prefix -- they have the "Config field 'level' value..."
        # canonical prefix). The combined warnings_out carries both
        # families; demux by marker shape.
        if marker.startswith("Config field "):
            continue
        assert marker.startswith("pr_risk_"), (
            f"every surfaced W607-Q marker must use the ``pr_risk_*`` "
            f"prefix family (cmd_pr_risk PR-time risk aggregator scope); "
            f"got {marker!r}"
        )
        # Hard distinction from sibling W607-* layers.
        for forbidden_prefix, sibling in (
            ("audit_", "cmd_audit W607-P"),
            ("dashboard_", "cmd_dashboard W607-O"),
            ("doctor_", "cmd_doctor W607-N"),
            ("health_", "cmd_health W607-M"),
            ("describe_", "cmd_describe W607-K"),
            ("minimap_", "cmd_minimap W607-L"),
            ("grep_", "cmd_grep W607-G"),
            ("history_", "cmd_history_grep W607-H"),
            ("refs_text_", "cmd_refs_text W607-I"),
            ("delete_check_", "cmd_delete_check W607-J"),
            ("search_", "cmd_search W607-E"),
            ("complete_", "cmd_complete W607-F"),
            ("semantic_", "cmd_search_semantic W607-A"),
            ("findings_query_", "cmd_findings W607-C"),
            ("dogfood_", "cmd_dogfood W607-D"),
            ("retrieve_", "cmd_retrieve W607-B"),
        ):
            assert not marker.startswith(forbidden_prefix), (
                f"marker leaked into ``{forbidden_prefix}*`` family ({sibling} scope); got {marker!r}"
            )


# ---------------------------------------------------------------------------
# (7) Sibling parity -- W607-P cmd_audit surface unchanged
# ---------------------------------------------------------------------------


def test_w607_p_cmd_audit_xfails_unaffected():
    """Sibling parity guard: W607-P cmd_audit source surface unchanged.

    W607-Q lands only in cmd_pr_risk. The W607-P cmd_audit surface
    (per-helper ``_run_check`` wrapper + ``_w607p_warnings_out``
    accumulator + ``audit_*`` marker emission) MUST stay identical. If
    a future refactor wave touches cmd_audit while editing pr_risk, the
    canonical anchors below catch the drift before sibling tests fail
    downstream.
    """
    src_path = Path(__file__).parent.parent / "src" / "roam" / "commands" / "cmd_audit.py"
    assert src_path.exists(), f"cmd_audit.py missing at {src_path}"
    src = src_path.read_text(encoding="utf-8")
    assert "w607p_warnings_out" in src, (
        "W607-P accumulator removed from cmd_audit; W607-Q must not regress the sibling instrumentation."
    )
    assert "audit_" in src, (
        "W607-P marker prefix removed from cmd_audit; W607-Q must not regress the sibling marker family."
    )


# ---------------------------------------------------------------------------
# (8) W805-EEEE intersection -- get_changed_files failure surfaces via warnings_out
# ---------------------------------------------------------------------------


def test_get_changed_files_failure_surfaces_via_warnings_out(cli_runner, pr_risk_project, monkeypatch):
    """W805-EEEE intersection: an UNEXPECTED raise from ``get_changed_files``
    surfaces via warnings_out, vs. silent-floor to ``[]``.

    The W805-EEEE pin documents that ``get_changed_files`` returns ``[]``
    silently on FileNotFoundError / TimeoutExpired (the canonical
    subprocess-failure axes). W607-Q is ADDITIVE -- for those canonical
    raise types the silent floor is preserved (W805-EEEE pin honored);
    for ANY OTHER raise that escapes the helper's own try/except,
    W607-Q surfaces the marker via warnings_out so the agent can see
    the substrate degradation.

    This test injects an OSError (which is NOT in the helper's own
    except list -- only FileNotFoundError + TimeoutExpired are caught
    by the helper) to verify the W607-Q wrapper catches the escape.
    """
    _patch_helper(
        monkeypatch,
        "get_changed_files",
        OSError("synthetic-W805EEEE-intersection-from-W607-Q"),
    )

    result = _invoke_pr_risk(cli_runner, pr_risk_project, True)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    # Marker surfaces on the no-changes envelope (the OSError makes
    # _run_check return its default of [], which triggers the
    # no-changes branch; the wrapper still surfaces the marker there).
    top_wo = data.get("warnings_out") or data["summary"].get("warnings_out") or []
    assert top_wo, (
        f"OSError escape from get_changed_files must surface via "
        f"warnings_out (W805-EEEE intersection); got data = {data!r}"
    )
    markers = [m for m in top_wo if m.startswith("pr_risk_get_changed_files_failed:")]
    assert markers, f"expected ``pr_risk_get_changed_files_failed:`` marker; got {top_wo!r}"
    assert any("OSError" in m for m in markers), markers
    # partial_success must flip on the no-changes envelope too.
    assert data["summary"].get("partial_success") is True, (
        f"OSError escape must flip summary.partial_success=True on the "
        f"no-changes branch; got summary = {data['summary']!r}"
    )


# ---------------------------------------------------------------------------
# (9) Multiple substrates can fail simultaneously -- all markers surface
# ---------------------------------------------------------------------------


def test_multiple_substrates_failing_emit_separate_markers(cli_runner, pr_risk_project_with_changes, monkeypatch):
    """Two simultaneous substrate raises -> two markers, both surfaced.

    Aggregator scope: the pr-risk value proposition is composing multiple
    substrates. The W607-Q guard must NOT short-circuit on the first
    raise -- each subsequent substrate still runs and emits its own
    marker on failure. Consumers see the full degradation lineage.
    """
    from roam.commands import cmd_pr_risk

    def _raise_compute(*a, **kw):
        raise RuntimeError("synthetic-multi-compute-from-W607-Q")

    def _raise_familiarity(*a, **kw):
        raise PermissionError("synthetic-multi-familiarity-from-W607-Q")

    monkeypatch.setattr(cmd_pr_risk, "_compute_surprise", _raise_compute)
    monkeypatch.setattr(cmd_pr_risk, "_author_familiarity", _raise_familiarity)

    result = _invoke_pr_risk(cli_runner, pr_risk_project_with_changes, True)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    top_wo = data.get("warnings_out") or []
    compute_markers = [m for m in top_wo if m.startswith("pr_risk_compute_surprise_failed:")]
    fam_markers = [m for m in top_wo if m.startswith("pr_risk_author_familiarity_failed:")]
    assert compute_markers, f"expected pr_risk_compute_surprise_failed: marker; got {top_wo!r}"
    assert fam_markers, f"expected pr_risk_author_familiarity_failed: marker; got {top_wo!r}"
    # partial_success still flips with multiple markers.
    assert data["summary"].get("partial_success") is True, data["summary"]


# ---------------------------------------------------------------------------
# (10) Source-level guard: cmd_pr_risk uses the canonical W607-Q accumulator
# ---------------------------------------------------------------------------


def test_cmd_pr_risk_carries_w607q_accumulator():
    """AST-level guard: cmd_pr_risk source carries the W607-Q accumulator.

    Pins the canonical anchors so a future refactor that removes the
    instrumentation (e.g., switches to a single try/except wrapping the
    whole command body) fails this guard rather than silently regressing
    every other test on dynamic envelope shape.
    """
    src_path = Path(__file__).parent.parent / "src" / "roam" / "commands" / "cmd_pr_risk.py"
    assert src_path.exists(), f"cmd_pr_risk.py missing at {src_path}"
    src = src_path.read_text(encoding="utf-8")
    assert "w607q_warnings_out" in src, (
        "W607-Q accumulator missing from cmd_pr_risk; the substrate-CALL marker plumbing has been removed."
    )
    assert "pr_risk_" in src, (
        'W607-Q marker prefix missing from cmd_pr_risk; check the `f"pr_risk_{phase}_failed:..."` line in _run_check.'
    )
    # Parse-tree level: confirm _run_check is defined inside pr_risk().
    tree = ast.parse(src)
    found_run_check = False
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "_run_check":
            found_run_check = True
            break
    assert found_run_check, (
        "W607-Q ``_run_check`` helper not found in cmd_pr_risk AST; the per-substrate wrapper has been refactored away."
    )
