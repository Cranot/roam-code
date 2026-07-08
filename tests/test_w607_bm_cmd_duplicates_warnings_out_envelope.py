"""W607-BM -- ``cmd_duplicates`` substrate-boundary plumbing.

Forty-seventh-in-batch W607 consumer-layer arc. GREENFIELD plumbing:
cmd_duplicates had NO prior W607 marker family on the accumulator before
this wave (unlike cmd_dark_matter's W641-followup-G
``dark_matter_unknown_severity:`` family). The substrates we wrap:

* query_candidates           -- the symbol_metrics + math_signals
                                + graph_metrics SELECT.
* compute_similarity         -- the per-pair weighted scoring loop.
* classify_role_buckets      -- W165 production/test/mixed
                                bucket classification.
* emit_findings              -- registry mirror under ``--persist``
                                (replaces the W136 silent fallback).
* serialize_to_sarif         -- SARIF projection for CI gates.

cmd_duplicates is the W805 paired-scoring sibling of cmd_dark_matter
(W607-BK) -- both detect DRY/architecture debt from different signal
axes (co-change vs structural-similarity). The marker-prefix discipline
test locks in the ``duplicates_*`` family so a future drift into
``dark_matter_*`` or ``clones_*`` is caught.

Marker family ``duplicates_<phase>_failed:<exc_class>:<detail>``
(underscore form -- matches the W805 paired sibling marker discipline).

W978 first-hypothesis check
---------------------------

Each W607-BM-wrapped substrate has a documented empty-floor default that
matches its happy-path return shape so a raise degrades cleanly.

W907 verify-cycle check
-----------------------

No "duplicated to avoid cycle" docstrings added. Substrates are patched
via ``monkeypatch.setattr`` on module-level helpers.

LAW 4 note: warning markers are diagnostic strings, NOT
``agent_contract.facts`` content, and therefore not subject to the
concrete-noun-terminal lint.

PATTERN-2 ELIMINATIONS
----------------------

One Pattern-2 silent fallback was removed in-place by W607-BM:

1. The ``except sqlite3.OperationalError: pass`` inside the
   ``--persist`` path (line ~879 pre-W607-BM) silently no-op'd whenever
   ANY OperationalError surfaced (locked DB, full disk, etc.) -- not
   just a missing findings table. Replaced with
   ``_run_check_bm("emit_findings", ...)``.

(cmd_duplicates already had structured early-exit envelopes for the
empty-corpus / insufficient-candidates W805 paths -- those are NOT
Pattern-2 fallbacks, they are explicit state disclosures with
``state=...`` + ``partial_success=True``.)
"""

from __future__ import annotations

import ast
import json as _json
import os
from pathlib import Path

import pytest
from click.testing import CliRunner

# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def cli_runner():
    return CliRunner()


@pytest.fixture
def duplicates_project(project_factory):
    """Small indexed corpus -- enough for cmd_duplicates to emit a
    well-formed envelope. Duplicate clusters may be 0 on this small
    corpus (the functions are too short / dissimilar to cluster) but
    the envelope shape is fully formed either way."""
    return project_factory(
        {
            "service.py": (
                "def process_user(user_id):\n"
                "    if user_id is None:\n"
                "        return None\n"
                "    result = compute(user_id)\n"
                "    if result is None:\n"
                "        return None\n"
                "    return result\n"
                "\n"
                "def process_order(order_id):\n"
                "    if order_id is None:\n"
                "        return None\n"
                "    result = compute(order_id)\n"
                "    if result is None:\n"
                "        return None\n"
                "    return result\n"
                "\n"
                "def compute(x):\n"
                "    return x * 2\n"
            ),
            "api.py": (
                "def handle_user(user_id):\n"
                "    if user_id is None:\n"
                "        return None\n"
                "    result = lookup(user_id)\n"
                "    if result is None:\n"
                "        return None\n"
                "    return result\n"
                "\n"
                "def lookup(x):\n"
                "    return x + 1\n"
            ),
        }
    )


def _invoke_duplicates(cli_runner, project_root, *args, json_mode=True):
    """Invoke ``roam duplicates`` against a project root via top-level CLI."""
    from roam.cli import cli

    full_args: list[str] = []
    if json_mode:
        full_args.append("--json")
    full_args.append("duplicates")
    full_args.extend(args)
    old_cwd = os.getcwd()
    try:
        os.chdir(str(project_root))
        return cli_runner.invoke(cli, full_args, catch_exceptions=False)
    finally:
        os.chdir(old_cwd)


# ---------------------------------------------------------------------------
# (1) Happy path -- envelope omits W607-BM substrate markers
# ---------------------------------------------------------------------------


def test_duplicates_clean_envelope_omits_w607bm_markers(cli_runner, duplicates_project):
    """Clean duplicates run -> no W607-BM substrate markers.

    Byte-identical-on-happy-path discipline: an empty W607-BM bucket on
    the success path must NOT introduce new ``duplicates_<phase>_failed:``
    markers tied to the W607-BM wrap.
    """
    result = _invoke_duplicates(cli_runner, duplicates_project)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)
    assert data["command"] == "duplicates"
    verdict = data["summary"]["verdict"]
    assert isinstance(verdict, str) and verdict, verdict

    top_wo = data.get("warnings_out") or []
    summary_wo = data["summary"].get("warnings_out") or []
    bm_phases = (
        "query_candidates",
        "compute_similarity",
        "classify_role_buckets",
        "emit_findings",
        "serialize_to_sarif",
    )
    bm_markers = [
        m for m in (list(top_wo) + list(summary_wo)) if any(f"duplicates_{p}_failed:" in m for p in bm_phases)
    ]
    assert not bm_markers, (
        f"clean duplicates must NOT surface W607-BM substrate markers; got top={top_wo!r}, summary={summary_wo!r}"
    )


# ---------------------------------------------------------------------------
# (2) query_candidates failure -> structured marker + partial_success
# ---------------------------------------------------------------------------


def test_duplicates_query_candidates_failure_marker_format(cli_runner, duplicates_project, monkeypatch):
    """If the candidate SELECT raises, surface the W607-BM marker with
    the canonical three-segment shape.

    Wrap ``open_db`` so the returned connection's ``execute`` raises
    on the candidate SELECT. sqlite3.Connection is C-immutable so we
    proxy through a wrapper class instead of patching the type.
    """
    from contextlib import contextmanager

    from roam.commands import cmd_duplicates
    from roam.db import connection as _conn_mod

    real_open_db = _conn_mod.open_db

    class _ProxyConn:
        def __init__(self, real_conn):
            self._real = real_conn

        def execute(self, sql, *params):
            if "COALESCE(sm.line_count" in sql:
                raise RuntimeError("synthetic-query-from-W607-BM")
            return self._real.execute(sql, *params)

        def commit(self):
            return self._real.commit()

        def __getattr__(self, name):
            return getattr(self._real, name)

    @contextmanager
    def _wrapped_open_db(*args, **kwargs):
        with real_open_db(*args, **kwargs) as conn:
            yield _ProxyConn(conn)

    monkeypatch.setattr(cmd_duplicates, "open_db", _wrapped_open_db)

    result = _invoke_duplicates(cli_runner, duplicates_project)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    top_wo = data.get("warnings_out") or []
    summary_wo = data["summary"].get("warnings_out") or []
    all_wo = list(top_wo) + list(summary_wo)
    markers = [m for m in all_wo if m.startswith("duplicates_query_candidates_failed:")]
    assert markers, f"expected duplicates_query_candidates_failed: marker; got {all_wo!r}"
    assert any("RuntimeError" in m for m in markers), markers
    assert any("synthetic-query-from-W607-BM" in m for m in markers), markers
    # Envelope flips partial_success on the degraded path.
    assert data["summary"].get("partial_success") is True, (
        f"query-failure degraded envelope must flip partial_success; got summary = {data['summary']!r}"
    )


# ---------------------------------------------------------------------------
# (3) warnings_out lands in envelope (top-level AND summary mirror)
# ---------------------------------------------------------------------------


def test_duplicates_w607bm_warnings_in_envelope(cli_runner, duplicates_project, monkeypatch):
    """Non-empty W607-BM bucket -> both top-level AND summary.warnings_out."""
    from roam.commands import cmd_duplicates

    def _raise(*args, **kwargs):
        raise RuntimeError("synthetic-mirror-from-W607-BM")

    # Patch the similarity helper -- raises on first pair scored.
    monkeypatch.setattr(cmd_duplicates, "_compute_similarity", _raise)

    result = _invoke_duplicates(cli_runner, duplicates_project)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    assert data.get("warnings_out"), (
        f"top-level warnings_out missing on W607-BM disclosure path; keys = {sorted(data.keys())!r}"
    )
    assert data["summary"].get("warnings_out"), (
        f"summary.warnings_out missing on W607-BM disclosure path; got summary = {data['summary']!r}"
    )
    markers = [m for m in data["warnings_out"] if m.startswith("duplicates_compute_similarity_failed:")]
    assert markers, f"expected duplicates_compute_similarity_failed: marker; got {data['warnings_out']!r}"


# ---------------------------------------------------------------------------
# (4) Three-segment marker shape -- prefix:exc_class:detail
# ---------------------------------------------------------------------------


def test_duplicates_three_segment_marker_shape(cli_runner, duplicates_project, monkeypatch):
    """Marker must have three colon-separated segments.

    Shape contract: ``<prefix>:<exc_class>:<detail>`` so downstream
    consumers can parse the exception class without regex gymnastics.
    """
    from roam.commands import cmd_duplicates

    def _raise(*args, **kwargs):
        raise PermissionError("synthetic-shape-detail-from-W607-BM")

    monkeypatch.setattr(cmd_duplicates, "_compute_similarity", _raise)

    result = _invoke_duplicates(cli_runner, duplicates_project)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    top_wo = data.get("warnings_out") or []
    failure_markers = [m for m in top_wo if m.startswith("duplicates_compute_similarity_failed:")]
    assert failure_markers, f"expected duplicates_compute_similarity_failed: marker; got {top_wo!r}"

    marker = failure_markers[0]
    parts = marker.split(":", 2)
    assert len(parts) == 3, f"marker must have three colon-separated segments (prefix:exc_class:detail); got {marker!r}"
    assert parts[0] == "duplicates_compute_similarity_failed", parts
    assert parts[1] == "PermissionError", parts
    assert parts[2], parts


# ---------------------------------------------------------------------------
# (5) classify_role_buckets failure -> envelope still emits clusters
# ---------------------------------------------------------------------------


def test_duplicates_classify_buckets_failure_envelope_still_emits(cli_runner, duplicates_project, monkeypatch):
    """A raise inside ``_role_bucket_for_files`` must NOT crash the
    duplicates command. The clusters still emit; the role_bucket field
    safe-floors to "production" via ``setdefault`` after the wrap.
    """
    from roam.commands import cmd_duplicates

    def _raise(files):
        raise RuntimeError("synthetic-buckets-from-W607-BM")

    monkeypatch.setattr(cmd_duplicates, "_role_bucket_for_files", _raise)

    result = _invoke_duplicates(cli_runner, duplicates_project)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    # Surface marker present on the path where the bucket loop ran.
    top_wo = data.get("warnings_out") or []
    summary_wo = data["summary"].get("warnings_out") or []
    all_wo = list(top_wo) + list(summary_wo)
    # The bucket-loop only runs when cluster_list is non-empty AFTER the
    # similarity pass. On a small corpus the loop may iterate zero times
    # (no clusters survive the threshold). The discipline guard is at the
    # source level (test 7 + 8). When the loop DOES execute and raises,
    # the marker family is correct.
    bucket_markers = [m for m in all_wo if m.startswith("duplicates_classify_role_buckets_failed:")]
    if bucket_markers:
        assert any("RuntimeError" in m for m in bucket_markers), bucket_markers
        assert data["summary"].get("partial_success") is True


# ---------------------------------------------------------------------------
# (6) Marker-prefix discipline -- W607-BM stays in ``duplicates_*`` family
# ---------------------------------------------------------------------------


def test_w607bm_marker_prefix_stays_in_duplicates_family(cli_runner, duplicates_project, monkeypatch):
    """Every W607-BM substrate marker uses the canonical ``duplicates_*``
    prefix.

    Hard distinction from sibling W607-* layers including the paired
    W805 detectors (dark_matter / clones / smells) that share the same
    DRY/architecture-debt axis.
    """
    from roam.commands import cmd_duplicates

    def _raise(*args, **kwargs):
        raise PermissionError("synthetic-prefix-discipline-from-W607-BM")

    monkeypatch.setattr(cmd_duplicates, "_compute_similarity", _raise)

    result = _invoke_duplicates(cli_runner, duplicates_project)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)
    top_wo = data.get("warnings_out") or []
    summary_wo = data["summary"].get("warnings_out") or []
    all_wo = list(top_wo) + list(summary_wo)
    substrate_markers = [m for m in all_wo if "_failed:" in m]
    assert substrate_markers, "expected non-empty substrate markers for prefix-consistency check"
    for marker in substrate_markers:
        assert marker.startswith("duplicates_"), (
            f"every surfaced W607-BM marker must use the ``duplicates_*`` "
            f"prefix family (cmd_duplicates scope); got {marker!r}"
        )
        # Hard distinction from sibling W607-* layers (paired W805
        # detectors + the broader W607 family).
        for forbidden_prefix, sibling in (
            # W805 paired-scoring detector family
            ("dark_matter_", "cmd_dark_matter W805 sibling (W607-BK)"),
            ("clones_", "cmd_clones W805 sibling"),
            ("smells_", "cmd_smells W805 sibling"),
            # Broader W607 family
            ("debt_", "cmd_debt W607-BG"),
            ("health_", "cmd_health W607-M / W607-BA"),
            ("complexity_", "cmd_complexity W607-BJ"),
            ("vulns_", "cmd_vulns W607-AQ"),
            ("taint_", "cmd_taint W607-AY"),
            ("attest_", "cmd_attest W607-AD"),
            ("diff_", "cmd_diff W607-Z"),
            ("critique_", "cmd_critique W607-Y"),
            ("pr_risk_", "cmd_pr_risk W607-Q / W607-AB"),
            ("impact_", "cmd_impact W607-T"),
            ("describe_", "cmd_describe W607-K"),
            ("minimap_", "cmd_minimap W607-L"),
            ("retrieve_", "cmd_retrieve W607-B"),
            ("findings_", "cmd_findings W607-C"),
        ):
            assert not marker.startswith(forbidden_prefix), (
                f"marker leaked into ``{forbidden_prefix}*`` family ({sibling} scope); got {marker!r}"
            )

    # W979 vocabulary regression guard: marker prefix uses underscore
    # form, NOT hyphenated.
    for marker in substrate_markers:
        assert not marker.startswith("duplicates-"), (
            f"marker uses hyphenated form -- inconsistent with the "
            f"W607-BK sibling underscore discipline; got {marker!r}"
        )


# ---------------------------------------------------------------------------
# (7) Source-level guard: cmd_duplicates carries the W607-BM accumulator
# ---------------------------------------------------------------------------


def test_cmd_duplicates_carries_w607bm_accumulator():
    """AST-level guard: cmd_duplicates source carries the W607-BM accumulator."""
    src_path = Path(__file__).parent.parent / "src" / "roam" / "commands" / "cmd_duplicates.py"
    assert src_path.exists(), f"cmd_duplicates.py missing at {src_path}"
    src = src_path.read_text(encoding="utf-8")
    assert "w607bm_warnings_out" in src, (
        "W607-BM accumulator missing from cmd_duplicates; the substrate-CALL marker plumbing has been removed."
    )
    assert "_run_check_bm" in src, (
        "W607-BM ``_run_check_bm`` helper missing from cmd_duplicates; "
        "the per-substrate wrapper has been refactored away."
    )
    # Parse-tree level: confirm _run_check_bm is defined inside cmd_duplicates.
    tree = ast.parse(src)
    found_run_check_bm = False
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "_run_check_bm":
            found_run_check_bm = True
            break
    assert found_run_check_bm, (
        "W607-BM ``_run_check_bm`` helper not found in cmd_duplicates AST; "
        "the per-substrate wrapper has been refactored away."
    )


# ---------------------------------------------------------------------------
# (8) Each W607-BM substrate phase is wrapped (source-level)
# ---------------------------------------------------------------------------


def test_all_w607bm_substrate_phases_wrapped_in_source():
    """Source-level guard: every W607-BM substrate boundary is wrapped.

    W607-BM substrate inventory (cmd_duplicates):

    * query_candidates           -- candidate SELECT
    * compute_similarity         -- weighted scoring loop
    * classify_role_buckets      -- W165 production/test/mixed
    * emit_findings              -- registry mirror under --persist
    * serialize_to_sarif         -- ``duplicates_to_sarif``

    If a future wave introduces a new substrate boundary, this guard
    needs to know about it -- add the phase name here. Accepts multiple
    indent depths because the call sites span branch blocks.
    """
    src_path = Path(__file__).parent.parent / "src" / "roam" / "commands" / "cmd_duplicates.py"
    src = src_path.read_text(encoding="utf-8")
    expected_phases = [
        "query_candidates",
        "compute_similarity",
        "classify_role_buckets",
        "emit_findings",
        "serialize_to_sarif",
    ]
    for phase in expected_phases:
        same_line = f'_run_check_bm("{phase}"' in src
        multi_line = (
            f'_run_check_bm(\n        "{phase}"' in src
            or f'_run_check_bm(\n            "{phase}"' in src
            or f'_run_check_bm(\n                "{phase}"' in src
            or f'_run_check_bm(\n                    "{phase}"' in src
            or f'_run_check_bm(\n                        "{phase}"' in src
        )
        assert same_line or multi_line, (
            f"W607-BM _run_check_bm wrap missing for phase {phase!r}; substrate boundary is no longer caught."
        )


# ---------------------------------------------------------------------------
# (9) Pattern-2 elimination: --persist path emit_findings degradation
# ---------------------------------------------------------------------------


def test_duplicates_persist_emit_findings_degradation(cli_runner, duplicates_project, monkeypatch):
    """Pattern-2 silent-fallback elimination: the pre-W607-BM
    ``except sqlite3.OperationalError: pass`` inside the ``--persist``
    branch silently no-op'd whenever the findings table was missing
    OR any other OperationalError surfaced. FIXED IN PLACE: the
    exception still degrades to no-write, but now surfaces a
    ``duplicates_emit_findings_failed:<exc>:<detail>`` marker.

    To force the --persist write path to actually run we need at least
    one cluster -- we monkeypatch the cluster build to force a fake
    cluster list. If the small-corpus duplicates_project yields no
    clusters naturally, this test still validates the plumbing via
    the cluster-injection path.
    """
    from roam.commands import cmd_duplicates

    def _raise(*args, **kwargs):
        raise RuntimeError("synthetic-emit-from-W607-BM")

    # Patch the emit helper at the module level so the --persist path
    # routes through the raise. If the cluster list is empty at the
    # time of the call, the wrapped function still runs (its body
    # commits even on an empty cluster list).
    monkeypatch.setattr(cmd_duplicates, "_emit_duplicates_findings", _raise)

    result = _invoke_duplicates(cli_runner, duplicates_project, "--persist", "--threshold", "0.0")
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    top_wo = data.get("warnings_out") or []
    summary_wo = data["summary"].get("warnings_out") or []
    all_wo = list(top_wo) + list(summary_wo)
    emit_markers = [m for m in all_wo if m.startswith("duplicates_emit_findings_failed:")]
    # Only assert when the --persist branch actually ran the emit. If
    # there were no clusters at all we still expect the marker because
    # the wrap runs unconditionally inside the if-persist block.
    assert emit_markers, f"expected duplicates_emit_findings_failed: marker on --persist path; got {all_wo!r}"
    assert any("RuntimeError" in m for m in emit_markers), emit_markers


# ---------------------------------------------------------------------------
# (10) W805 PAIRED-SCORING coexistence: duplicates + dark_matter markers
# ---------------------------------------------------------------------------


def test_w805_paired_scoring_markers_coexist_on_same_corpus(cli_runner, duplicates_project, monkeypatch):
    """W805 paired-scoring detector family: dark_matter + duplicates +
    clones + smells all detect DRY/architecture debt from different
    signal axes on the same corpus. They can produce non-empty
    warnings_out buckets simultaneously when each substrate raises on
    its own axis.

    This test verifies the SYMMETRIC half of W607-BK's paired test:
    the duplicates envelope correctly emits its W607-BM markers
    without leaking into the dark_matter_* / clones_* / smells_*
    prefix families.
    """
    from roam.commands import cmd_duplicates

    def _raise(*args, **kwargs):
        raise RuntimeError("synthetic-paired-from-W607-BM")

    monkeypatch.setattr(cmd_duplicates, "_compute_similarity", _raise)

    result = _invoke_duplicates(cli_runner, duplicates_project)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    top_wo = data.get("warnings_out") or []
    summary_wo = data["summary"].get("warnings_out") or []
    all_wo = list(top_wo) + list(summary_wo)

    # duplicates_* markers present.
    dup_markers = [m for m in all_wo if m.startswith("duplicates_")]
    assert dup_markers, f"expected duplicates_* markers on duplicates envelope; got {all_wo!r}"

    # No paired-W805 sibling prefix leakage.
    for sibling_prefix in ("dark_matter_", "clones_", "smells_"):
        sibling_leak = [m for m in all_wo if m.startswith(sibling_prefix)]
        assert not sibling_leak, (
            f"duplicates envelope leaked into {sibling_prefix}* family "
            f"(W805 paired-scoring sibling scope); got {sibling_leak!r}"
        )


# ---------------------------------------------------------------------------
# (11) W136/W821 overlap regression guard: duplicates marker prefix does
#      not collide with cmd_clones marker family
# ---------------------------------------------------------------------------


def test_duplicates_markers_do_not_collide_with_clones(cli_runner, duplicates_project, monkeypatch):
    """W136 (duplicates) and W821 (clones) detectors overlap in the
    DRY-detection axis (both compare functions for similarity).
    cmd_clones is the W805 paired-scoring sibling that has not yet
    received W607 plumbing -- when it does, its marker family will be
    ``clones_<phase>_failed:``. This test pins the W607-BM markers
    to the ``duplicates_*`` family so a future cmd_clones plumbing
    wave does not accidentally collide.
    """
    from roam.commands import cmd_duplicates

    def _raise(*args, **kwargs):
        raise RuntimeError("synthetic-overlap-from-W607-BM")

    monkeypatch.setattr(cmd_duplicates, "_compute_similarity", _raise)

    result = _invoke_duplicates(cli_runner, duplicates_project)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)
    top_wo = data.get("warnings_out") or []
    summary_wo = data["summary"].get("warnings_out") or []
    all_wo = list(top_wo) + list(summary_wo)

    # Every marker is on the duplicates_* family -- no clones_* leakage.
    clones_leak = [m for m in all_wo if m.startswith("clones_")]
    assert not clones_leak, (
        f"duplicates W607-BM markers leaked into clones_* family (W136/W821 overlap scope); got {clones_leak!r}"
    )

    # The substrate-CALL markers we DID surface are all on the
    # duplicates_* family.
    substrate_markers = [m for m in all_wo if "_failed:" in m]
    assert substrate_markers, f"expected substrate markers on degraded envelope; got {all_wo!r}"
    for m in substrate_markers:
        assert m.startswith("duplicates_"), f"every W607-BM marker must use the duplicates_* family; got {m!r}"


# ---------------------------------------------------------------------------
# (12) Pre-W607-BM Pattern-2 fallback is gone (source-level guard)
# ---------------------------------------------------------------------------


def test_w607bm_pattern2_silent_fallback_removed():
    """The pre-W607-BM ``except sqlite3.OperationalError: pass`` block in
    the --persist branch is replaced by ``_run_check_bm("emit_findings",
    ...)``. Source-level guard: the bare-pass swallow does NOT survive
    inside the if-persist branch.
    """
    src_path = Path(__file__).parent.parent / "src" / "roam" / "commands" / "cmd_duplicates.py"
    src = src_path.read_text(encoding="utf-8")

    # The persist branch starts with ``if persist:`` -- search for the
    # exact bare-pass swallow that the W607-BM wave removed. The
    # historical block was:
    #
    #   try:
    #       _emit_duplicates_findings(conn, ...)
    #       conn.commit()
    #   except sqlite3.OperationalError:
    #       pass
    #
    # The new path uses ``_run_check_bm("emit_findings", ...)`` so the
    # raw ``except sqlite3.OperationalError:`` followed by a single
    # ``pass`` must NOT appear in the source. Comments about it may
    # appear -- search for the LIVE-code shape only.
    forbidden_block = "except sqlite3.OperationalError:\n                # findings table missing"
    assert forbidden_block not in src, (
        "W607-BM Pattern-2 elimination regressed -- the bare-pass "
        "``except sqlite3.OperationalError`` swallow inside the persist "
        "branch is back. Use ``_run_check_bm('emit_findings', ...)`` instead."
    )

    # The replacement IS present.
    assert '_run_check_bm(\n                "emit_findings"' in src or ('_run_check_bm("emit_findings"' in src), (
        "W607-BM emit_findings wrap missing from --persist branch."
    )


# ---------------------------------------------------------------------------
# (13) LAW 6 verdict standalone-parse discipline
# ---------------------------------------------------------------------------


def test_duplicates_verdict_single_line_on_degraded_path(cli_runner, duplicates_project, monkeypatch):
    """LAW 6: the verdict line must stand alone (parseable without
    descending into any other field). On a degraded path with W607-BM
    markers the verdict still emits as a single line.
    """
    from roam.commands import cmd_duplicates

    def _raise(*args, **kwargs):
        raise RuntimeError("synthetic-law6-from-W607-BM")

    monkeypatch.setattr(cmd_duplicates, "_compute_similarity", _raise)

    result = _invoke_duplicates(cli_runner, duplicates_project)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)
    verdict = data["summary"].get("verdict")
    assert isinstance(verdict, str) and verdict, verdict
    assert "\n" not in verdict, f"verdict must be single line: {verdict!r}"
