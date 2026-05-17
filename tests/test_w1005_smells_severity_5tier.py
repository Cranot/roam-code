"""W1005 — cmd_smells --min-severity widened from 3-tier to W547 canonical 5-tier.

Pattern 3a (vocabulary divergence). Pre-W1005, ``roam smells --min-severity``
accepted only 3 tokens (``critical`` / ``warning`` / ``info``) while the
canonical roam severity rank table at :mod:`roam.output._severity` accepts
the full W547 5-tier vocabulary (``critical`` / ``error`` / ``high`` /
``warning`` / ``medium`` / ``low`` / ``info``). Same concept, divergent
names — agents that read the canonical severity_rank() docstring then tried
``roam smells --min-severity high`` got a parse error from click.Choice.

W1005 widens the click.Choice to the full 7-token canonical vocabulary while
preserving the polarity contract (higher = worse via ``severity_rank()``).
Detectors still EMIT only 3 of those tokens; the WIDER filter input vocabulary
is the contract change.

What this test pins
-------------------

* Each canonical 7-token value parses (no click usage error).
* Polarity contract: ``--min-severity critical`` keeps only critical
  findings; ``--min-severity high`` (rank 4) drops critical-only-emitter
  findings cleanly; ``--min-severity info`` (rank 0, floor) keeps
  everything.
* Unknown tokens still hard-fail at parse time (the W996 fixed-enum
  semantic for ``--min-severity`` is preserved — only the SET widened).
* The local ``_VALID_SEVERITIES`` frozenset (the EMIT vocab) stays
  unchanged at 3 tiers — the widening is on the INPUT only.
"""

from __future__ import annotations

import json
import os
import sqlite3
import subprocess
from pathlib import Path

import pytest
from click.testing import CliRunner

from roam.cli import cli
from roam.commands.cmd_smells import _VALID_SEVERITIES
from roam.output._severity import severity_rank

# ---------------------------------------------------------------------------
# Tiny fixture: synthetic brain-method (severity=critical) so the polarity
# contract has at least one finding to filter through.
# ---------------------------------------------------------------------------


def _git_init(path: Path) -> None:
    subprocess.run(["git", "init"], cwd=path, capture_output=True)
    subprocess.run(["git", "config", "user.email", "t@t.com"], cwd=path, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=path, capture_output=True)
    (path / "dummy.py").write_text("# dummy\n")
    subprocess.run(["git", "add", "."], cwd=path, capture_output=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=path, capture_output=True)


def _make_db(tmp_path: Path) -> sqlite3.Connection:
    db_path = tmp_path / ".roam" / "index.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS files (
            id INTEGER PRIMARY KEY, path TEXT NOT NULL UNIQUE,
            language TEXT, file_role TEXT DEFAULT 'source',
            hash TEXT, mtime REAL, line_count INTEGER DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS symbols (
            id INTEGER PRIMARY KEY, file_id INTEGER NOT NULL,
            name TEXT NOT NULL, qualified_name TEXT, kind TEXT NOT NULL,
            signature TEXT, line_start INTEGER, line_end INTEGER,
            docstring TEXT, visibility TEXT DEFAULT 'public',
            is_exported INTEGER DEFAULT 1, parent_id INTEGER,
            default_value TEXT,
            FOREIGN KEY(file_id) REFERENCES files(id)
        );
        CREATE TABLE IF NOT EXISTS edges (
            id INTEGER PRIMARY KEY, source_id INTEGER NOT NULL,
            target_id INTEGER NOT NULL, kind TEXT NOT NULL DEFAULT 'call',
            line INTEGER, bridge TEXT, confidence REAL,
            source_file_id INTEGER
        );
        CREATE TABLE IF NOT EXISTS graph_metrics (
            symbol_id INTEGER PRIMARY KEY,
            pagerank REAL DEFAULT 0,
            in_degree INTEGER DEFAULT 0,
            out_degree INTEGER DEFAULT 0,
            betweenness REAL DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS symbol_metrics (
            symbol_id INTEGER PRIMARY KEY,
            cognitive_complexity REAL DEFAULT 0,
            nesting_depth INTEGER DEFAULT 0,
            param_count INTEGER DEFAULT 0,
            line_count INTEGER DEFAULT 0,
            return_count INTEGER DEFAULT 0,
            bool_op_count INTEGER DEFAULT 0,
            callback_depth INTEGER DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS file_stats (
            file_id INTEGER PRIMARY KEY,
            commit_count INTEGER DEFAULT 0,
            total_churn INTEGER DEFAULT 0,
            distinct_authors INTEGER DEFAULT 0,
            complexity REAL DEFAULT 0,
            health_score REAL DEFAULT NULL
        );
        CREATE TABLE IF NOT EXISTS git_cochange (
            file_id_a INTEGER NOT NULL,
            file_id_b INTEGER NOT NULL,
            cochange_count INTEGER DEFAULT 0,
            PRIMARY KEY (file_id_a, file_id_b)
        );
    """)
    return conn


@pytest.fixture()
def project_with_brain_method(tmp_path: Path) -> Path:
    """A tmp project with one synthetic brain-method finding (severity=critical).

    Mirrors :func:`tests.test_smells._populate_brain_method` — keeps this
    test file self-contained without depending on TestSmellsCLI fixtures.
    """
    _git_init(tmp_path)
    conn = _make_db(tmp_path)
    conn.execute("INSERT INTO files (id, path) VALUES (1, 'src/engine.py')")
    conn.execute(
        "INSERT INTO symbols (id, file_id, name, kind, line_start, line_end, signature) "
        "VALUES (1, 1, 'process_everything', 'function', 10, 200, '(data, config, opts)')"
    )
    conn.execute("INSERT INTO symbol_metrics (symbol_id, cognitive_complexity, nesting_depth) VALUES (1, 75, 6)")
    conn.commit()
    conn.close()
    return tmp_path


# ---------------------------------------------------------------------------
# 1. Vocabulary parse-acceptance — every canonical token must parse.
# ---------------------------------------------------------------------------


CANONICAL_5TIER_VALUES: tuple[str, ...] = (
    "critical",
    "error",
    "high",
    "warning",
    "medium",
    "low",
    "info",
)


@pytest.mark.parametrize("token", CANONICAL_5TIER_VALUES)
def test_canonical_5tier_token_accepted_by_click(token: str, project_with_brain_method: Path) -> None:
    """Every W547 canonical token parses (no click usage error, exit != 2)."""
    runner = CliRunner()
    old_cwd = os.getcwd()
    try:
        os.chdir(str(project_with_brain_method))
        result = runner.invoke(cli, ["smells", "--min-severity", token])
    finally:
        os.chdir(old_cwd)
    # click usage error = exit 2; W1005 widening means none of the 7 should
    # hit usage-error path. Exit 0 is the expected successful-parse outcome.
    assert result.exit_code == 0, (
        f"--min-severity {token!r}: expected exit 0 (canonical 5-tier accepted), "
        f"got {result.exit_code}. Output:\n{result.output}"
    )


@pytest.mark.parametrize("token", CANONICAL_5TIER_VALUES)
def test_canonical_5tier_case_insensitive(token: str, project_with_brain_method: Path) -> None:
    """Click.Choice(case_sensitive=False) — UPPER-case spellings parse too."""
    runner = CliRunner()
    old_cwd = os.getcwd()
    try:
        os.chdir(str(project_with_brain_method))
        result = runner.invoke(cli, ["smells", "--min-severity", token.upper()])
    finally:
        os.chdir(old_cwd)
    assert result.exit_code == 0, (
        f"--min-severity {token.upper()!r}: case-insensitive widening must accept, "
        f"got exit {result.exit_code}. Output:\n{result.output}"
    )


# ---------------------------------------------------------------------------
# 2. Polarity contract — severity_rank() drives the filter.
# ---------------------------------------------------------------------------


def test_min_severity_high_keeps_only_critical(project_with_brain_method: Path) -> None:
    """``--min-severity high`` (rank 4) keeps findings ranked >= 4.

    Smells emit ``critical`` (rank 5) / ``warning`` (rank 3) / ``info``
    (rank 0). With rank-4 floor, only ``critical`` survives — same set as
    ``--min-severity critical`` for this detector's emit vocabulary.
    """
    runner = CliRunner()
    old_cwd = os.getcwd()
    try:
        os.chdir(str(project_with_brain_method))
        crit = runner.invoke(cli, ["--json", "smells", "--min-severity", "critical"])
        high = runner.invoke(cli, ["--json", "smells", "--min-severity", "high"])
    finally:
        os.chdir(old_cwd)
    assert crit.exit_code == 0
    assert high.exit_code == 0
    crit_data = json.loads(crit.output)
    high_data = json.loads(high.output)
    # severity_rank("high") == severity_rank("error") == 4; smells emit no
    # error/high natively. Both filters resolve to the same critical-only set.
    assert crit_data["summary"]["total_smells"] == high_data["summary"]["total_smells"], (
        "--min-severity high should match critical-only set on emit vocab "
        "{critical, warning, info}: critical "
        f"{crit_data['summary']['total_smells']} != high "
        f"{high_data['summary']['total_smells']}"
    )


def test_min_severity_low_keeps_more_than_warning(project_with_brain_method: Path) -> None:
    """``--min-severity low`` (rank 1) is strictly less restrictive than
    ``--min-severity warning`` (rank 3). Smells with ``info`` (rank 0)
    still drop — ``low`` is rank 1, ``info`` is rank 0, ``info < low``.
    But the W547 rank table sorts low < warning, so low keeps everything
    warning would keep PLUS any rank-1/-2 findings. Smells emit none of
    those today, so the contract pins ``low_total >= warning_total``.
    """
    runner = CliRunner()
    old_cwd = os.getcwd()
    try:
        os.chdir(str(project_with_brain_method))
        warn = runner.invoke(cli, ["--json", "smells", "--min-severity", "warning"])
        low = runner.invoke(cli, ["--json", "smells", "--min-severity", "low"])
    finally:
        os.chdir(old_cwd)
    assert warn.exit_code == 0
    assert low.exit_code == 0
    warn_total = json.loads(warn.output)["summary"]["total_smells"]
    low_total = json.loads(low.output)["summary"]["total_smells"]
    assert low_total >= warn_total, (
        f"--min-severity low (rank 1) must keep at least as many findings "
        f"as --min-severity warning (rank 3); got low={low_total} warn={warn_total}"
    )


# ---------------------------------------------------------------------------
# 3. Pattern 3a anti-regression: unknown tokens still hard-fail at parse.
# ---------------------------------------------------------------------------


def test_unknown_min_severity_raises_usage_error(project_with_brain_method: Path) -> None:
    """W996 closed-enum boundary preserved — unknown tokens still exit 2.

    The widening is to the SET of accepted tokens, not a switch to
    permissive parsing. ``note`` and ``unknown`` are intentionally NOT
    in the Choice (they collapse to ``info`` / sort below ``info`` via
    severity_rank, so a user-facing filter on them would be confusing).
    """
    runner = CliRunner()
    old_cwd = os.getcwd()
    try:
        os.chdir(str(project_with_brain_method))
        result = runner.invoke(cli, ["smells", "--min-severity", "bogus"])
    finally:
        os.chdir(old_cwd)
    # click usage error = exit 2 (sometimes propagated as 1 via group dispatch;
    # accept either non-zero exit so this stays a robust hard-fail pin).
    assert result.exit_code != 0, (
        f"--min-severity bogus must hard-fail (W996 closed-enum boundary); "
        f"got exit {result.exit_code}. Output:\n{result.output}"
    )


def test_alias_note_rejected_at_parse(project_with_brain_method: Path) -> None:
    """``note`` is a canonical W547 ALIAS but NOT in the CLI Choice.

    Pins the deliberate intent documented in the click.Choice comment:
    aliases that collapse to ``info`` (``note``) or sort below ``info``
    (``unknown``) are kept OUT of the user-facing filter to avoid
    confusing UX. Validation lives in severity_rank() / normalize_severity()
    for INTERNAL callers; the CLI surface is the documented closed set.
    """
    runner = CliRunner()
    old_cwd = os.getcwd()
    try:
        os.chdir(str(project_with_brain_method))
        result = runner.invoke(cli, ["smells", "--min-severity", "note"])
    finally:
        os.chdir(old_cwd)
    assert result.exit_code != 0, (
        "--min-severity note must hard-fail at click.Choice (W1005 deliberately omits aliases that collapse to info)"
    )


# ---------------------------------------------------------------------------
# 4. Emit-vocab vs accept-vocab asymmetry — _VALID_SEVERITIES unchanged.
# ---------------------------------------------------------------------------


def test_emit_vocab_unchanged_at_3_tiers() -> None:
    """``_VALID_SEVERITIES`` (the EMIT vocab) stays {critical, warning, info}.

    The W1005 widening applies ONLY to the INPUT side (click.Choice).
    Detectors still emit a 3-tier subset, and the local frozenset that
    enumerates the EMIT vocabulary must reflect that asymmetry. If a new
    detector starts emitting ``error`` / ``high`` / ``medium`` / ``low``,
    that's a deliberate emit-vocab change and this pin will surface it.
    """
    assert _VALID_SEVERITIES == frozenset({"critical", "warning", "info"}), (
        f"_VALID_SEVERITIES drift: {sorted(_VALID_SEVERITIES)} — W1005 widened "
        f"INPUT only. Adding an emit tier requires a separate, deliberate change."
    )


def test_severity_rank_drives_polarity() -> None:
    """The rank polarity (higher = worse) is the comparator the filter uses.

    Pre-W1005 ``cmd_smells`` had a local 3-tier comparator; W564 + W1005
    consolidate on ``severity_rank()`` from ``roam.output._severity``. Pin
    the canonical ordering so a future polarity flip there surfaces here.
    """
    assert severity_rank("critical") > severity_rank("error")
    assert severity_rank("error") == severity_rank("high")  # 4 == 4
    assert severity_rank("high") > severity_rank("warning")
    assert severity_rank("warning") > severity_rank("medium")
    assert severity_rank("medium") > severity_rank("low")
    assert severity_rank("low") > severity_rank("info")
    # W1088 lookup-miss safety: unknown / None collapse to -1 (sort-below-info).
    assert severity_rank("bogus") == -1
    assert severity_rank(None) == -1
