"""W1005-followup-B — cmd_adversarial --severity widened from CVSS-only 4-tier
to W547 canonical 7-token vocab.

Pattern 3a (vocabulary divergence). Pre-W1005-followup-B,
``roam adversarial --severity`` accepted only 4 CVSS-style tokens
(``low`` / ``medium`` / ``high`` / ``critical``) while the canonical roam
severity rank table at :mod:`roam.output._severity` accepts the full W547
7-token vocabulary (``critical`` / ``error`` / ``high`` / ``warning`` /
``medium`` / ``low`` / ``info``). Same concept, divergent names — agents
that read the canonical ``severity_rank()`` docstring then tried
``roam adversarial --severity warning`` got a parse error from click.Choice.

This widens the click.Choice to the full 7-token canonical vocabulary
while preserving the polarity contract (higher = worse via
``severity_rank()``). Detectors still EMIT only UPPER 4-tier
{CRITICAL, HIGH, WARNING, INFO}; the WIDER filter input vocabulary is
the contract change.

What this test pins
-------------------

* Each canonical 7-token value parses (no click usage error).
* Legacy CVSS-only 4-tier values (``low``/``medium``/``high``/``critical``)
  still parse byte-identically — INPUT widening is purely additive.
* Polarity contract: ``severity_rank()`` from
  ``roam.output._severity`` drives the filter; the canonical ordering
  (critical > error == high > warning > medium > low > info) is preserved.
* Unknown tokens still hard-fail at parse time (the W996 fixed-enum
  semantic is preserved — only the SET widened).
* The local ``_MIN_SEVERITY`` table covers the full 7-token canonical
  so click parse and runtime rank lookups stay aligned.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest
from click.testing import CliRunner

sys.path.insert(0, str(Path(__file__).parent))
from conftest import git_init, index_in_process  # noqa: E402

from roam.commands.cmd_adversarial import _MIN_SEVERITY, adversarial  # noqa: E402
from roam.output._severity import severity_rank  # noqa: E402

# ---------------------------------------------------------------------------
# Fixture: minimal indexed project so adversarial doesn't bail early on
# "index not built". No changes required — click.Choice parsing happens
# before the no-changes branch.
# ---------------------------------------------------------------------------


@pytest.fixture
def indexed_proj(tmp_path: Path) -> Path:
    """A fully indexed project with no uncommitted changes.

    Mirrors :func:`tests.test_adversarial.indexed_project_no_changes` —
    enough to clear ``ensure_index()`` so the click.Choice parse path
    can be exercised through to the no-changes verdict.
    """
    proj = tmp_path / "adv_severity"
    proj.mkdir()
    (proj / ".gitignore").write_text(".roam/\n")
    (proj / "app.py").write_text("def main():\n    return 0\n")
    git_init(proj)
    index_in_process(proj)
    return proj


def _invoke(proj: Path, args: list[str]) -> object:
    """Invoke adversarial via the command callable (mirrors test_adversarial.run_adversarial)."""
    runner = CliRunner()
    old_cwd = os.getcwd()
    try:
        os.chdir(str(proj))
        return runner.invoke(adversarial, args, obj={"json": False}, catch_exceptions=False)
    finally:
        os.chdir(old_cwd)


# ---------------------------------------------------------------------------
# 1. Vocabulary parse-acceptance — every W547 canonical token must parse.
# ---------------------------------------------------------------------------


CANONICAL_7TOKEN_VALUES: tuple[str, ...] = (
    "critical",
    "error",
    "high",
    "warning",
    "medium",
    "low",
    "info",
)


@pytest.mark.parametrize("token", CANONICAL_7TOKEN_VALUES)
def test_canonical_7token_accepted_by_click(token: str, indexed_proj: Path) -> None:
    """Every W547 canonical token parses (no click usage error, exit != 2)."""
    result = _invoke(indexed_proj, ["--severity", token])
    # click usage error = exit 2; W1005-followup-B widening means none of
    # the 7 should hit usage-error path. Exit 0 is the expected
    # successful-parse outcome (no changes -> graceful return).
    assert result.exit_code == 0, (
        f"--severity {token!r}: expected exit 0 (canonical 7-token accepted), "
        f"got {result.exit_code}. Output:\n{result.output}"
    )


@pytest.mark.parametrize("token", CANONICAL_7TOKEN_VALUES)
def test_canonical_7token_case_insensitive(token: str, indexed_proj: Path) -> None:
    """click.Choice(case_sensitive=False) — UPPER-case spellings parse too."""
    result = _invoke(indexed_proj, ["--severity", token.upper()])
    assert result.exit_code == 0, (
        f"--severity {token.upper()!r}: case-insensitive widening must accept, "
        f"got exit {result.exit_code}. Output:\n{result.output}"
    )


# ---------------------------------------------------------------------------
# 2. Legacy CVSS-only 4-tier values still accepted byte-identically.
# ---------------------------------------------------------------------------


LEGACY_4TIER_VALUES: tuple[str, ...] = ("low", "medium", "high", "critical")


@pytest.mark.parametrize("token", LEGACY_4TIER_VALUES)
def test_legacy_4tier_still_accepted(token: str, indexed_proj: Path) -> None:
    """W1005-followup-B is purely additive — pre-widening callers must keep working.

    Anchors the back-compat contract: any script / CI job / docs example
    that already uses ``--severity {low,medium,high,critical}`` must
    continue to parse and exit cleanly after the widening.
    """
    result = _invoke(indexed_proj, ["--severity", token])
    assert result.exit_code == 0, (
        f"--severity {token!r} (legacy CVSS 4-tier) must still parse after "
        f"W1005-followup-B widening; got exit {result.exit_code}. "
        f"Output:\n{result.output}"
    )


# ---------------------------------------------------------------------------
# 3. Polarity contract — severity_rank() drives the filter.
# ---------------------------------------------------------------------------


def test_severity_rank_drives_polarity() -> None:
    """Pin the canonical ordering (higher = worse) so a future polarity flip
    in ``roam.output._severity`` surfaces here.

    The W547 rank table is the comparator the ``--severity`` filter uses
    at line ~664 in cmd_adversarial.py (``severity_rank(c["severity"]) >=
    min_sev``). Detectors emit UPPER 4-tier {CRITICAL, HIGH, WARNING,
    INFO}; ``severity_rank()`` is case-insensitive so the same comparator
    works for emitted UPPER labels and lowercase CLI input.
    """
    assert severity_rank("critical") > severity_rank("error")
    assert severity_rank("error") == severity_rank("high")  # rank 4 == 4
    assert severity_rank("high") > severity_rank("warning")
    assert severity_rank("warning") > severity_rank("medium")
    assert severity_rank("medium") > severity_rank("low")
    assert severity_rank("low") > severity_rank("info")
    # Case-insensitivity contract — adversarial emits UPPER labels and
    # the comparator must normalize them to the same rank.
    assert severity_rank("CRITICAL") == severity_rank("critical")
    assert severity_rank("HIGH") == severity_rank("high")
    assert severity_rank("WARNING") == severity_rank("warning")
    assert severity_rank("INFO") == severity_rank("info")


def test_min_severity_table_covers_full_canonical() -> None:
    """The ``_MIN_SEVERITY`` translation dict mirrors the click.Choice.

    After widening, the click.Choice accepts 7 tokens; the
    ``_MIN_SEVERITY`` table must cover all 7 so the runtime lookup at
    line ~664 never falls through to the ``severity_rank("low")``
    default for a value the parser would have accepted.
    """
    expected = set(CANONICAL_7TOKEN_VALUES)
    actual = set(_MIN_SEVERITY.keys())
    assert actual == expected, (
        f"_MIN_SEVERITY drift vs click.Choice: missing={expected - actual} "
        f"extra={actual - expected}. Both surfaces must align after "
        f"W1005-followup-B widening."
    )
    # Each entry must resolve to the canonical rank — no stale local rank table.
    for label, rank in _MIN_SEVERITY.items():
        assert rank == severity_rank(label), (
            f"_MIN_SEVERITY[{label!r}]={rank} drifts from "
            f"severity_rank({label!r})={severity_rank(label)} — table "
            f"must source from canonical helper."
        )


# ---------------------------------------------------------------------------
# 4. Pattern 3a anti-regression: unknown tokens still hard-fail at parse.
# ---------------------------------------------------------------------------


def test_unknown_severity_raises_usage_error(indexed_proj: Path) -> None:
    """W996 closed-enum boundary preserved — unknown tokens still hard-fail.

    The widening is to the SET of accepted tokens, not a switch to
    permissive parsing. ``note`` and ``unknown`` are intentionally NOT
    in the Choice (they collapse to ``info`` / sort below ``info`` via
    severity_rank, so a user-facing filter on them would be confusing).
    """
    result = _invoke(indexed_proj, ["--severity", "bogus"])
    # click usage error = exit 2 (sometimes propagated as 1 via group
    # dispatch; accept any non-zero exit so this stays a robust hard-fail pin).
    assert result.exit_code != 0, (
        f"--severity bogus must hard-fail (W996 closed-enum boundary); "
        f"got exit {result.exit_code}. Output:\n{result.output}"
    )


def test_alias_note_rejected_at_parse(indexed_proj: Path) -> None:
    """``note`` is a canonical W547 ALIAS but NOT in the CLI Choice.

    Pins the deliberate intent documented in the click.Choice comment:
    aliases that collapse to ``info`` (``note``) or sort below ``info``
    (``unknown``) are kept OUT of the user-facing filter to avoid
    confusing UX. Validation lives in severity_rank() /
    normalize_severity() for INTERNAL callers; the CLI surface is the
    documented closed set.
    """
    result = _invoke(indexed_proj, ["--severity", "note"])
    assert result.exit_code != 0, (
        "--severity note must hard-fail at click.Choice (W1005-followup-B "
        "deliberately omits aliases that collapse to info)"
    )
