"""Tests for the W115 migration: bus-factor detector emits to the central
findings registry.

The bus-factor detector is the fourth migration onto the A4 findings
table (after W95 clones, W99 dead, W102 complexity). It continues to
render its own JSON / text envelopes (authoritative output surface) and
ALSO, when ``--persist`` is set, emits one row per concentrated or
stale-ownership risk into ``findings``.

The detector is heuristic by nature — author-count rollups and
inactivity proxies are fuzzy signals. Both sub-kinds
(``author-concentration``, ``stale-ownership``) therefore land at the
``heuristic`` confidence tier.
"""

from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import textwrap
from datetime import datetime, timedelta

import pytest
from click.testing import CliRunner

from roam.cli import cli
from tests._findings_helpers import assert_detector_visible_in_findings_count
from roam.commands.cmd_bus_factor import (
    BUS_FACTOR_DETECTOR_VERSION,
    _bus_factor_finding_id,
)
from roam.db.connection import open_db


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _git_commit_as(path, author_name, author_email, message):
    """Make a git commit with an explicit author identity."""
    subprocess.run(["git", "add", "."], cwd=str(path), capture_output=True)
    env = {
        **os.environ,
        "GIT_AUTHOR_NAME": author_name,
        "GIT_AUTHOR_EMAIL": author_email,
        "GIT_COMMITTER_NAME": author_name,
        "GIT_COMMITTER_EMAIL": author_email,
    }
    subprocess.run(
        ["git", "commit", "--allow-empty", "-m", message],
        cwd=str(path),
        capture_output=True,
        env=env,
    )


def _single_owner_project(tmp_path):
    """Project with one directory dominated by a single author.

    ``Alice`` owns 100% of ``src/core/`` — guarantees a ``concentrated``
    flag (>70% share) so ``--persist`` has a non-empty row set to emit.
    """
    proj = tmp_path / "bus_proj"
    proj.mkdir()
    (proj / ".gitignore").write_text(".roam/\n", encoding="utf-8")

    core = proj / "src" / "core"
    core.mkdir(parents=True)
    (core / "engine.py").write_text(
        textwrap.dedent(
            """
            def run_engine(data):
                return [d for d in data if d]
            """
        ).strip(),
        encoding="utf-8",
    )

    # Init repo with neutral identity, then have Alice make all the
    # subsequent commits so the bus-factor analysis sees a
    # 100%-Alice-owned directory.
    subprocess.run(["git", "init"], cwd=str(proj), capture_output=True)
    subprocess.run(
        ["git", "config", "user.email", "init@init.test"],
        cwd=str(proj),
        capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Init"],
        cwd=str(proj),
        capture_output=True,
    )

    for i in range(4):
        (core / "engine.py").write_text(
            textwrap.dedent(
                f"""
                def run_engine(data):
                    # Alice revision {i}
                    return [d for d in data if d is not None]
                """
            ).strip(),
            encoding="utf-8",
        )
        _git_commit_as(
            proj, "Alice", "alice@example.com", f"engine: Alice revision {i}"
        )

    return proj


def _stale_owner_project(tmp_path):
    """Project where Alice is the sole owner but her last commit is old.

    We can't backdate ``git commit`` without elaborate machinery, so the
    fixture uses ``--date`` to push the author timestamp well past the
    default ``--stale-months 6`` threshold. That triggers
    ``stale_primary=True`` and exercises the ``stale-ownership`` kind.
    """
    proj = tmp_path / "stale_proj"
    proj.mkdir()
    (proj / ".gitignore").write_text(".roam/\n", encoding="utf-8")

    legacy = proj / "src" / "legacy"
    legacy.mkdir(parents=True)
    (legacy / "old.py").write_text(
        textwrap.dedent(
            """
            def legacy_helper(x):
                return x
            """
        ).strip(),
        encoding="utf-8",
    )

    subprocess.run(["git", "init"], cwd=str(proj), capture_output=True)
    subprocess.run(
        ["git", "config", "user.email", "init@init.test"],
        cwd=str(proj),
        capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Init"],
        cwd=str(proj),
        capture_output=True,
    )

    # Single Alice commit dated 2 years ago — well past stale-months=6.
    # Two-year offset from now keeps the "stale" semantic relative; was a
    # 2024-01-01 hardcode pre-W1002.
    old_date = (datetime.now() - timedelta(days=730)).strftime("%Y-%m-%dT%H:%M:%S")
    subprocess.run(
        ["git", "add", "."],
        cwd=str(proj),
        capture_output=True,
    )
    env = {
        **os.environ,
        "GIT_AUTHOR_NAME": "Alice",
        "GIT_AUTHOR_EMAIL": "alice@example.com",
        "GIT_COMMITTER_NAME": "Alice",
        "GIT_COMMITTER_EMAIL": "alice@example.com",
        "GIT_AUTHOR_DATE": old_date,
        "GIT_COMMITTER_DATE": old_date,
    }
    subprocess.run(
        ["git", "commit", "-m", "legacy: initial Alice commit"],
        cwd=str(proj),
        capture_output=True,
        env=env,
    )

    return proj


def _run_bus_factor_persist(proj):
    """Index the project and run ``bus-factor --persist``.

    Returns the CliRunner result so tests can assert on the exit code if
    they care about the persist path itself.
    """
    runner = CliRunner()
    old_cwd = os.getcwd()
    try:
        os.chdir(str(proj))
        index_result = runner.invoke(cli, ["index"])
        assert index_result.exit_code == 0, index_result.output
        result = runner.invoke(cli, ["bus-factor", "--persist", "--force-team-mode"])
        assert result.exit_code == 0, result.output
        return result
    finally:
        os.chdir(old_cwd)


# ---------------------------------------------------------------------------
# Core migration assertions
# ---------------------------------------------------------------------------


def test_bus_factor_emits_to_findings_registry(tmp_path):
    """Running bus-factor --persist on a single-owner project populates findings."""
    proj = _single_owner_project(tmp_path)
    old_cwd = os.getcwd()
    try:
        os.chdir(str(proj))
        _run_bus_factor_persist(proj)

        with open_db(readonly=True) as conn:
            rows = conn.execute(
                "SELECT finding_id_str, claim, source_detector, source_version, "
                "       subject_kind, confidence "
                "FROM findings WHERE source_detector = 'bus-factor'"
            ).fetchall()
        assert len(rows) >= 1, "expected at least one bus-factor finding row"
        for r in rows:
            assert r["source_detector"] == "bus-factor"
            assert r["source_version"] == BUS_FACTOR_DETECTOR_VERSION
            # Bus-factor risks attach to directories, not symbols.
            assert r["subject_kind"] == "directory"
            # Bus-factor is fundamentally a heuristic detector — both
            # sub-kinds carry the same confidence tier.
            assert r["confidence"] == "heuristic"
            assert r["finding_id_str"].startswith("bus-factor:")
    finally:
        os.chdir(old_cwd)


def test_bus_factor_finding_id_is_deterministic():
    """_bus_factor_finding_id returns the same id for the same (directory, kind)."""
    a = _bus_factor_finding_id("src/core/", "author-concentration")
    b = _bus_factor_finding_id("src/core/", "author-concentration")
    assert a == b
    assert a.startswith("bus-factor:author-concentration:")
    # Different directory -> different id.
    assert (
        _bus_factor_finding_id("src/other/", "author-concentration") != a
    )
    # Different kind -> different id (same directory still gets two rows
    # when it surfaces under both kinds).
    assert _bus_factor_finding_id("src/core/", "stale-ownership") != a


def test_bus_factor_rerun_upserts_not_duplicates(tmp_path):
    """Re-running bus-factor --persist produces the same finding_id_str set."""
    proj = _single_owner_project(tmp_path)
    old_cwd = os.getcwd()
    try:
        os.chdir(str(proj))
        _run_bus_factor_persist(proj)

        with open_db(readonly=True) as conn:
            first_ids = {
                r[0]
                for r in conn.execute(
                    "SELECT finding_id_str FROM findings "
                    "WHERE source_detector = 'bus-factor'"
                ).fetchall()
            }
            first_count = conn.execute(
                "SELECT COUNT(*) FROM findings WHERE source_detector = 'bus-factor'"
            ).fetchone()[0]
        assert first_count == len(first_ids), (
            "duplicate finding_id_str rows on first run"
        )
        assert first_count >= 1

        # Second run — same fixture, same code, same hash inputs.
        runner = CliRunner()
        result = runner.invoke(cli, ["bus-factor", "--persist", "--force-team-mode"])
        assert result.exit_code == 0, result.output

        with open_db(readonly=True) as conn:
            second_ids = {
                r[0]
                for r in conn.execute(
                    "SELECT finding_id_str FROM findings "
                    "WHERE source_detector = 'bus-factor'"
                ).fetchall()
            }
            second_count = conn.execute(
                "SELECT COUNT(*) FROM findings WHERE source_detector = 'bus-factor'"
            ).fetchone()[0]
        assert second_count == first_count, "row count drifted across runs"
        assert second_ids == first_ids, "finding_id_str set changed across runs"
    finally:
        os.chdir(old_cwd)


def test_bus_factor_finding_evidence_carries_directory_and_authors(tmp_path):
    """The finding's evidence JSON carries directory, share, and author rollup."""
    proj = _single_owner_project(tmp_path)
    old_cwd = os.getcwd()
    try:
        os.chdir(str(proj))
        _run_bus_factor_persist(proj)

        with open_db(readonly=True) as conn:
            row = conn.execute(
                "SELECT evidence_json, subject_id, claim FROM findings "
                "WHERE source_detector = 'bus-factor' "
                "ORDER BY id ASC LIMIT 1"
            ).fetchone()
        assert row is not None
        evidence = json.loads(row["evidence_json"])
        # Required structural keys for cross-detector consumers.
        for k in (
            "directory",
            "bus_factor",
            "entropy",
            "primary_author",
            "primary_share",
            "primary_share_pct",
            "concentrated",
            "stale_primary",
            "staleness_factor",
            "top_authors",
        ):
            assert k in evidence, f"evidence missing key {k}"
        # Directory is the subject — subject_id stays NULL since
        # directories aren't ``symbols.id`` rows.
        assert row["subject_id"] is None
        # Alice should own the only-author directory.
        assert evidence["primary_author"] == "Alice"
        assert evidence["concentrated"] is True
        # The claim must name a recognisable bus-factor risk.
        assert "bus-factor" in (row["claim"] or "").lower() or (
            "stale ownership" in (row["claim"] or "").lower()
        )
    finally:
        os.chdir(old_cwd)


@pytest.mark.git_history
def test_bus_factor_stale_kind_emitted(tmp_path):
    """A stale primary author triggers the ``stale-ownership`` sub-kind.

    The fixture commit is dated 2 years ago to push the author timestamp
    past the default ``--stale-months 6`` threshold. The W405 shallow-
    history default (``_DEFAULT_SINCE = "365d"`` in ``git_stats.py``) would
    drop that commit on first index — capturing zero git history and
    producing zero bus-factor findings. The ``git_history`` mark (registered
    in ``pyproject.toml``, applied by the autouse fixture in
    ``tests/conftest.py``) sets ``ROAM_GIT_SINCE=0`` so the detector sees
    the stale commit and emits. W984.
    """
    proj = _stale_owner_project(tmp_path)
    old_cwd = os.getcwd()
    try:
        os.chdir(str(proj))
        _run_bus_factor_persist(proj)

        with open_db(readonly=True) as conn:
            rows = conn.execute(
                "SELECT finding_id_str FROM findings "
                "WHERE source_detector = 'bus-factor'"
            ).fetchall()
        kinds = {row[0].split(":")[1] for row in rows}
        # The legacy directory is single-owner AND stale, so BOTH kinds
        # should fire. The author-concentration kind is the floor; the
        # interesting assertion is that stale-ownership ALSO emits.
        assert "stale-ownership" in kinds, (
            f"expected stale-ownership kind, got kinds={kinds}"
        )
    finally:
        os.chdir(old_cwd)


# ---------------------------------------------------------------------------
# Visibility through the read-side CLI (`roam findings`)
# ---------------------------------------------------------------------------


def test_bus_factor_findings_visible_via_cmd_findings_list(tmp_path):
    """`roam findings list --detector bus-factor` returns rows after migration."""
    proj = _single_owner_project(tmp_path)
    old_cwd = os.getcwd()
    try:
        os.chdir(str(proj))
        _run_bus_factor_persist(proj)

        runner = CliRunner()
        result = runner.invoke(
            cli, ["--json", "findings", "list", "--detector", "bus-factor"]
        )
        assert result.exit_code == 0, result.output
        envelope = json.loads(result.output)
        assert envelope["command"] == "findings-list"
        assert envelope["summary"]["state"] == "populated"
        assert envelope["summary"]["total_findings"] >= 1
        assert "bus-factor" in envelope["summary"]["detectors"]
        assert all(
            r["source_detector"] == "bus-factor" for r in envelope["findings"]
        )
    finally:
        os.chdir(old_cwd)


def test_bus_factor_findings_visible_via_cmd_findings_count(tmp_path):
    """`roam findings count` includes a non-zero entry for bus-factor."""
    proj = _single_owner_project(tmp_path)
    old_cwd = os.getcwd()
    try:
        os.chdir(str(proj))
        _run_bus_factor_persist(proj)
    finally:
        os.chdir(old_cwd)
    assert_detector_visible_in_findings_count(proj, "bus-factor")


# ---------------------------------------------------------------------------
# Defensive paths
# ---------------------------------------------------------------------------


def test_no_persist_does_not_emit_findings(tmp_path):
    """Without --persist, the standard read path stays side-effect-free.

    The registry mirror lives inside the ``--persist`` branch — running
    ``roam bus-factor`` without the flag must not write to ``findings``.
    """
    proj = _single_owner_project(tmp_path)
    old_cwd = os.getcwd()
    try:
        os.chdir(str(proj))
        runner = CliRunner()
        assert runner.invoke(cli, ["index"]).exit_code == 0
        # No --persist.
        assert runner.invoke(cli, ["bus-factor", "--force-team-mode"]).exit_code == 0

        with open_db(readonly=True) as conn:
            try:
                count = conn.execute(
                    "SELECT COUNT(*) FROM findings "
                    "WHERE source_detector = 'bus-factor'"
                ).fetchone()[0]
            except sqlite3.OperationalError:
                # findings table may not be present on every test env's
                # schema flavour — that's still a "no findings emitted"
                # outcome from this command path.
                count = 0
        assert count == 0, "non-persist bus-factor still wrote to findings"
    finally:
        os.chdir(old_cwd)


# ---------------------------------------------------------------------------
# W164: solo-author collapse — one summary row, not N per-directory rows.
# ---------------------------------------------------------------------------


def _multi_directory_solo_owner_project(tmp_path):
    """Solo-author project spread across MANY directories.

    The W149 dogfood audit found that on a true solo-author repo
    (Cranot owned 100% of commits) we were emitting 65 per-directory
    rows that all said "single author owns this directory". The
    collapse fix turns those into ONE repo-level summary. To exercise
    the fix we need a fixture with several distinct directories, all
    owned by the same author. Five directories is enough to
    distinguish "1 finding" from "5 findings" without slowing the
    test suite.
    """
    proj = tmp_path / "solo_multi"
    proj.mkdir()
    (proj / ".gitignore").write_text(".roam/\n", encoding="utf-8")

    subprocess.run(["git", "init"], cwd=str(proj), capture_output=True)
    subprocess.run(
        ["git", "config", "user.email", "alice@example.com"],
        cwd=str(proj),
        capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Alice"],
        cwd=str(proj),
        capture_output=True,
    )

    # Five directories, two files each — enough surface for
    # ``_analyse_bus_factor`` to roll up multiple per-directory rows.
    dirs = ["src/alpha", "src/beta", "src/gamma", "src/delta", "src/epsilon"]
    for d in dirs:
        dp = proj / d
        dp.mkdir(parents=True)
        for fname in ("one.py", "two.py"):
            (dp / fname).write_text(
                f"def {d.split('/')[-1]}_{fname[:-3]}():\n    return 1\n",
                encoding="utf-8",
            )

    # All commits by Alice — the shape detector's
    # ``_SINGLE_AUTHOR_THRESHOLD`` (>= 80%) trips at 100% Alice.
    for i in range(3):
        for d in dirs:
            (proj / d / "one.py").write_text(
                f"def {d.split('/')[-1]}_one():\n    return {i}\n",
                encoding="utf-8",
            )
        _git_commit_as(
            proj, "Alice", "alice@example.com", f"alice revision {i}"
        )

    return proj


def _three_author_project(tmp_path):
    """Distributed project — three authors, roughly equal share.

    Regression fixture: when team size is NOT single-author, the
    detector must continue emitting per-directory rows (the W115
    behaviour) instead of collapsing into a summary row. Three
    distinct authors each making a similar number of commits keeps
    the top author's share well under the 80% single-author threshold.
    """
    proj = tmp_path / "team_proj"
    proj.mkdir()
    (proj / ".gitignore").write_text(".roam/\n", encoding="utf-8")

    subprocess.run(["git", "init"], cwd=str(proj), capture_output=True)
    subprocess.run(
        ["git", "config", "user.email", "init@init.test"],
        cwd=str(proj),
        capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Init"],
        cwd=str(proj),
        capture_output=True,
    )

    dirs = ["src/web", "src/api", "src/lib"]
    for d in dirs:
        dp = proj / d
        dp.mkdir(parents=True)
        (dp / "module.py").write_text(
            f"def {d.split('/')[-1]}_init():\n    return 0\n",
            encoding="utf-8",
        )

    authors = [
        ("Alice", "alice@example.com"),
        ("Bob", "bob@example.com"),
        ("Carol", "carol@example.com"),
    ]
    # Each author touches each directory the same number of times so
    # no single author dominates ownership. 3 authors x 3 dirs x 2
    # rounds = 18 commits, top author share ~33%.
    for round_i in range(2):
        for name, email in authors:
            for d in dirs:
                (proj / d / "module.py").write_text(
                    f"def {d.split('/')[-1]}_round_{round_i}_{name}():\n    return {round_i}\n",
                    encoding="utf-8",
                )
                _git_commit_as(proj, name, email, f"{name}: {d} round {round_i}")

    return proj


def _bus_factor_finding_counts(proj):
    """Helper: return {subject_kind: count} for bus-factor findings."""
    old_cwd = os.getcwd()
    try:
        os.chdir(str(proj))
        with open_db(readonly=True) as conn:
            rows = conn.execute(
                "SELECT subject_kind, COUNT(*) AS n FROM findings "
                "WHERE source_detector = 'bus-factor' "
                "GROUP BY subject_kind"
            ).fetchall()
        return {r["subject_kind"]: int(r["n"]) for r in rows}
    finally:
        os.chdir(old_cwd)


def test_solo_author_repo_emits_one_summary_row(tmp_path):
    """W164: on a solo-author repo, --persist (without --force-team-mode)
    emits ONE repo-level summary finding instead of N per-directory rows.
    """
    proj = _multi_directory_solo_owner_project(tmp_path)
    old_cwd = os.getcwd()
    try:
        os.chdir(str(proj))
        runner = CliRunner()
        assert runner.invoke(cli, ["index"]).exit_code == 0
        # NOTE: no --force-team-mode — exercise the collapse path.
        result = runner.invoke(cli, ["bus-factor", "--persist"])
        assert result.exit_code == 0, result.output

        counts = _bus_factor_finding_counts(proj)
        # Exactly one summary finding — the collapse target.
        assert counts.get("repo", 0) == 1, (
            f"expected exactly 1 repo summary finding, got counts={counts}"
        )
        # No per-directory author-concentration rows on a solo repo —
        # the whole point of the collapse. Stale-ownership rows MAY
        # be present (and remain valuable on solo repos), but this
        # fixture has no stale modules so the directory count should
        # be 0 here.
        assert counts.get("directory", 0) == 0, (
            f"expected 0 per-directory rows on solo collapse, got counts={counts}"
        )
        # Total across all subject kinds collapses to 1.
        with open_db(readonly=True) as conn:
            total = conn.execute(
                "SELECT COUNT(*) FROM findings WHERE source_detector='bus-factor'"
            ).fetchone()[0]
        assert total == 1
    finally:
        os.chdir(old_cwd)


def test_force_team_mode_overrides_solo_collapse(tmp_path):
    """W164: --force-team-mode brings back the W115 per-directory rows.

    Same fixture as the collapse test, but the explicit override must
    short-circuit the solo-author detection and emit
    author-concentration rows per directory like before. Back-compat
    guarantee for users who actually want the full ranking on a
    solo-author repo.
    """
    proj = _multi_directory_solo_owner_project(tmp_path)
    old_cwd = os.getcwd()
    try:
        os.chdir(str(proj))
        runner = CliRunner()
        assert runner.invoke(cli, ["index"]).exit_code == 0
        result = runner.invoke(
            cli, ["bus-factor", "--persist", "--force-team-mode"]
        )
        assert result.exit_code == 0, result.output

        counts = _bus_factor_finding_counts(proj)
        # No repo-level summary in force-team-mode.
        assert counts.get("repo", 0) == 0, (
            f"expected no summary row under --force-team-mode, got counts={counts}"
        )
        # Multiple per-directory rows — the fixture has 5 dirs, every
        # one owned 100% by Alice, so every one is ``concentrated``
        # and emits an author-concentration finding.
        assert counts.get("directory", 0) >= 2, (
            f"expected per-directory rows under --force-team-mode, got counts={counts}"
        )
    finally:
        os.chdir(old_cwd)


def test_team_repo_still_emits_per_directory(tmp_path):
    """Regression: a multi-author repo continues to emit per-directory rows.

    The W164 collapse must only fire on solo-author repos. A distributed
    team's per-directory ownership signal is still useful — collapsing
    it would lose information.
    """
    proj = _three_author_project(tmp_path)
    old_cwd = os.getcwd()
    try:
        os.chdir(str(proj))
        runner = CliRunner()
        assert runner.invoke(cli, ["index"]).exit_code == 0
        # No --force-team-mode — the collapse path would normally fire
        # if the project tripped the solo-author heuristic. With three
        # equal contributors it does NOT, and the behaviour stays as
        # per-directory rows.
        result = runner.invoke(cli, ["bus-factor", "--persist"])
        assert result.exit_code == 0, result.output

        counts = _bus_factor_finding_counts(proj)
        # No summary finding — this isn't a solo repo.
        assert counts.get("repo", 0) == 0, (
            f"expected no repo-summary row on a team repo, got counts={counts}"
        )
    finally:
        os.chdir(old_cwd)


def test_summary_finding_evidence_carries_aggregate_counts(tmp_path):
    """The solo summary's evidence_json carries all four aggregate fields.

    Spec: ``total_directories_analyzed``, ``unique_authors_count``,
    ``dominant_author``, ``dominant_author_share``. Consumers of the
    findings registry need these to reconstruct what the per-directory
    rows would have said without re-running the detector.
    """
    proj = _multi_directory_solo_owner_project(tmp_path)
    old_cwd = os.getcwd()
    try:
        os.chdir(str(proj))
        runner = CliRunner()
        assert runner.invoke(cli, ["index"]).exit_code == 0
        assert runner.invoke(cli, ["bus-factor", "--persist"]).exit_code == 0

        with open_db(readonly=True) as conn:
            row = conn.execute(
                "SELECT finding_id_str, claim, evidence_json, subject_kind "
                "FROM findings "
                "WHERE source_detector='bus-factor' AND subject_kind='repo'"
            ).fetchone()
        assert row is not None, "no repo-summary finding emitted"
        assert row["subject_kind"] == "repo"
        # Stable id prefix — consumers can spot the summary class
        # without parsing evidence_json.
        assert row["finding_id_str"].startswith("bus-factor-summary:solo-author:")

        evidence = json.loads(row["evidence_json"])
        for k in (
            "total_directories_analyzed",
            "unique_authors_count",
            "dominant_author",
            "dominant_author_share",
        ):
            assert k in evidence, f"summary evidence missing {k!r}"

        assert evidence["total_directories_analyzed"] >= 1
        # Alice owns the only-author fixture; expect ~100% share.
        assert evidence["dominant_author"] == "Alice"
        assert evidence["dominant_author_share"] >= 0.8
        # The claim string carries the same headline numbers in a form
        # ``roam findings list`` can render directly.
        assert "Alice" in row["claim"]
    finally:
        os.chdir(old_cwd)


def test_bus_factor_persist_no_findings_table_no_crash(tmp_path):
    """``bus-factor --persist`` degrades cleanly when the findings table is absent.

    Simulates the pre-W89 schema by DROP-ing ``findings`` after init but
    before the persist call. The standard analysis path (which legacy
    consumers depend on) must keep working — the command exits 0 and
    writes no registry rows.
    """
    proj = _single_owner_project(tmp_path)
    old_cwd = os.getcwd()
    try:
        os.chdir(str(proj))
        runner = CliRunner()
        assert runner.invoke(cli, ["index"]).exit_code == 0

        with open_db(readonly=False) as conn:
            conn.execute("DROP TABLE IF EXISTS findings")
            conn.commit()

        result = runner.invoke(
            cli, ["bus-factor", "--persist", "--force-team-mode"]
        )
        # Must succeed despite the missing findings table.
        assert result.exit_code == 0, result.output
    finally:
        os.chdir(old_cwd)
