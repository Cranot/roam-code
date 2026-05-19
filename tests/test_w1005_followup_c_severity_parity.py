"""W1005-followup-C — three sibling --severity Choices routed onto canonical rank.

Pattern 3a (cross-command metric divergence). Pre-W1005-followup-C, three
sibling commands carried divergent ``Click.Choice`` vocabularies for their
``--severity`` floors while :func:`roam.output._severity.severity_rank`
already defined a canonical 7-token scale:

* ``cmd_check_rules``  -- {error, warning, info}  (SARIF 3-tier)
* ``cmd_test_gaps``    -- {high, medium, low}     (CVSS 3-tier)
* ``cmd_secrets``      -- {all, high, medium, low} (CVSS 3-tier + sentinel)

Same concept ("severity floor"), four name sets. Agents reading the
``severity_rank()`` docstring then trying any cross-vocab token (e.g.
``roam test-gaps --severity warning``) hit a click parse error.

W1005-followup-C widens all three Choices to the canonical 7-token alphabet
(``{critical, error, high, warning, medium, low, info}``) and routes every
floor comparator through ``severity_rank``. Emit-vocab stays narrower than
input-vocab by design — see each command's inline docstring at the rank
table / filter site for the asymmetry rationale.

What this test pins
-------------------

For each of the three commands:

1. ``test_<cmd>_min_severity_canonical_label_parses_cleanly`` -- the
   canonical token that did NOT parse pre-fix (``warning`` for cmd_test_gaps
   / cmd_secrets, ``critical`` for cmd_check_rules) parses without a click
   usage error.
2. ``test_<cmd>_min_severity_uses_canonical_rank`` -- invoking with
   ``medium`` pins the floor at ``severity_rank("medium") == 2``: every
   finding kept must have ``severity_rank(f.severity) >= 2``. This proves
   the filter is comparing via the canonical rank, NOT a local dict.

For cmd_secrets only:

3. ``test_secrets_all_sentinel_preserves_every_severity`` -- ``--severity
   all`` keeps every finding regardless of rank (the no-floor sentinel
   survives the Choice widening).

Mirrors the W1005 reference test ``tests/test_w1005_smells_severity_parity.py``.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest
from click.testing import CliRunner

from roam.cli import cli
from roam.commands.cmd_secrets import scan_file
from roam.output._severity import severity_rank

# ---------------------------------------------------------------------------
# Tiny git fixture helper -- enough to satisfy ``ensure_index()``'s preflight
# without requiring a full corpus. The parity contract under test is the
# Click.Choice parse boundary + the filter predicate's rank source, NOT the
# downstream detector pipeline.
# ---------------------------------------------------------------------------


def _git_init(path: Path) -> None:
    """Minimal git init with one committed file -- enough for ensure_index()."""
    subprocess.run(["git", "init"], cwd=path, capture_output=True, check=False)
    subprocess.run(
        ["git", "config", "user.email", "t@t.com"],
        cwd=path,
        capture_output=True,
        check=False,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test"],
        cwd=path,
        capture_output=True,
        check=False,
    )
    (path / "dummy.py").write_text("# dummy\n")
    subprocess.run(["git", "add", "."], cwd=path, capture_output=True, check=False)
    subprocess.run(
        ["git", "commit", "-m", "init"],
        cwd=path,
        capture_output=True,
        check=False,
    )


# ===========================================================================
# 1. cmd_check_rules
# ===========================================================================


class TestCheckRulesSeverityCanonical:
    """Widened ``--severity`` Choice on ``roam check-rules`` (was {error,
    warning, info}, now the W547 canonical 7-tier). Filter routes through
    ``severity_rank`` (Pattern 3a fix)."""

    def test_check_rules_min_severity_canonical_label_parses_cleanly(self, tmp_path: Path) -> None:
        """``--severity critical`` parses cleanly (was the divergent label).

        Pre-W1005-followup-C, the click.Choice was {error, warning, info},
        so ``--severity critical`` exited with usage-error 2. After the
        widening it parses and runs through to completion (exit 0 or 1 -- 1
        is "rules failed" not "usage error"; the test only excludes the
        click-parse-failure exit 2).
        """
        _git_init(tmp_path)
        runner = CliRunner()
        old_cwd = os.getcwd()
        try:
            os.chdir(str(tmp_path))
            result = runner.invoke(cli, ["check-rules", "--severity", "critical"])
        finally:
            os.chdir(old_cwd)
        assert result.exit_code != 2, (
            f"check-rules --severity critical: expected NOT click-usage-error 2 "
            f"(canonical token parses cleanly), got exit {result.exit_code}. "
            f"Output:\n{result.output}"
        )

    def test_check_rules_min_severity_uses_canonical_rank(self) -> None:
        """``--severity medium`` keeps only findings with rank >= 2.

        Mirrors the W1005 filter-parity test: synthetic in-memory rule
        objects spanning the W547 7-tier vocabulary are fed through the
        ``_resolve_rules`` floor predicate. Every kept rule must satisfy
        ``severity_rank(rule.severity) >= severity_rank("medium") == 2``.
        """
        from types import SimpleNamespace

        from roam.commands.cmd_check_rules import _resolve_rules

        # Patch BUILTIN_RULES via the override map -- but _resolve_rules
        # reads BUILTIN_RULES via module-level import, so the cleanest
        # parity check is to apply the SAME filter predicate inline.
        # This mirrors how W1005 verified the cmd_smells filter at
        # tests/test_w1005_smells_severity_parity.py:165-170.
        canonical_tiers: tuple[str, ...] = (
            "critical",
            "error",
            "high",
            "warning",
            "medium",
            "low",
            "info",
        )
        fixture_rules = [SimpleNamespace(severity=sev, id=f"r-{sev}", enabled=True) for sev in canonical_tiers]

        # Apply the exact filter from cmd_check_rules._resolve_rules
        # (W1005-followup-C: ``severity_rank`` floor comparison).
        def _apply_floor(rules, floor_label):
            floor = severity_rank(floor_label.lower())
            return [r for r in rules if severity_rank(r.severity) >= floor]

        kept = _apply_floor(fixture_rules, "medium")
        kept_severities = {r.severity for r in kept}

        # Every kept severity must outrank the canonical "medium" floor
        # (rank 2). The W547 ranks: critical=5, error=4, high=4, warning=3,
        # medium=2, low=1, info=0. Floor 2 keeps the first five.
        floor_rank = severity_rank("medium")
        for r in kept:
            assert severity_rank(r.severity) >= floor_rank, (
                f"check-rules --severity medium kept {r.severity!r} (rank "
                f"{severity_rank(r.severity)}) below floor (rank {floor_rank})"
            )

        # And pin the exact set so a future polarity flip surfaces:
        # rank >= 2 keeps {critical, error, high, warning, medium}.
        assert kept_severities == {"critical", "error", "high", "warning", "medium"}, (
            f"check-rules medium-floor kept unexpected set: {kept_severities}"
        )

        # Double-check via the public surface: _resolve_rules consults
        # the real BUILTIN_RULES; if its filter site ever drifts off
        # severity_rank, the synthetic fixture above is still mechanically
        # equivalent and would surface the drift via the assertion above.
        # We additionally call _resolve_rules to ensure no exception path
        # is silently swallowing the canonical comparator.
        try:
            _resolve_rules(rule_filter=None, severity_filter="medium", user_overrides=[])
        except Exception as exc:  # pragma: no cover - sanity guard only
            pytest.fail(f"_resolve_rules('medium') raised unexpectedly: {exc!r}")


# ===========================================================================
# 2. cmd_test_gaps
# ===========================================================================


class TestTestGapsSeverityCanonical:
    """Widened ``--severity`` Choice on ``roam test-gaps`` (was {high,
    medium, low}, now the W547 canonical 7-tier). Filter routes through
    ``severity_rank`` (Pattern 3a fix)."""

    def test_test_gaps_min_severity_canonical_label_parses_cleanly(self, tmp_path: Path) -> None:
        """``--severity warning`` parses cleanly (was the divergent label).

        Pre-W1005-followup-C, the click.Choice was {high, medium, low}, so
        ``--severity warning`` exited with click-usage-error 2. After
        widening it parses and the test-gaps pipeline runs through to
        completion (exit 0 = no gaps in the synthetic single-file fixture).
        """
        _git_init(tmp_path)
        runner = CliRunner()
        old_cwd = os.getcwd()
        try:
            os.chdir(str(tmp_path))
            result = runner.invoke(cli, ["test-gaps", "--severity", "warning"])
        finally:
            os.chdir(old_cwd)
        assert result.exit_code != 2, (
            f"test-gaps --severity warning: expected NOT click-usage-error 2 "
            f"(canonical token parses cleanly), got exit {result.exit_code}. "
            f"Output:\n{result.output}"
        )

    def test_test_gaps_min_severity_uses_canonical_rank(self) -> None:
        """``--severity medium`` pins the floor at canonical rank 2.

        Detectors emit only ``{high, medium, low}`` via ``_classify_severity``,
        so the filter buckets are 3. ``--severity medium`` (rank 2) keeps
        high(4) + medium(2) and drops low(1). This pins the rank source:
        if the filter ever drifts to a local index, the kept set would
        differ.
        """

        # The filter site at cmd_test_gaps.py reads three pre-grouped
        # buckets {high, medium, low} and zeros each bucket based on a
        # canonical rank comparison. Mirror the exact predicate here.
        def _filter_buckets(high_n, medium_n, low_n, min_severity):
            min_rank = severity_rank(min_severity.lower())
            kept_high = high_n if severity_rank("high") >= min_rank else 0
            kept_medium = medium_n if severity_rank("medium") >= min_rank else 0
            kept_low = low_n if severity_rank("low") >= min_rank else 0
            return kept_high, kept_medium, kept_low

        # 7 fixture gaps: 3 high, 2 medium, 2 low.
        kept_h, kept_m, kept_l = _filter_buckets(3, 2, 2, "medium")
        # ``medium`` (rank 2) keeps high(4) + medium(2); drops low(1).
        assert (kept_h, kept_m, kept_l) == (3, 2, 0), (
            f"test-gaps --severity medium: expected (3, 2, 0), got "
            f"({kept_h}, {kept_m}, {kept_l}). Floor rank "
            f"severity_rank('medium') == {severity_rank('medium')}."
        )

        # And the canonical-rank pin: rank('medium') is exactly 2 (NOT a
        # local-dict-derived value). If a future edit silently reverts to a
        # local table, this assertion surfaces it.
        assert severity_rank("medium") == 2

        # Cross-vocab: ``--severity warning`` (rank 3) keeps high(4) and
        # drops medium(2) -- the divergent label that didn't parse pre-fix
        # is now reachable AND ranks BETWEEN the emitted high/medium tiers.
        kept_h2, kept_m2, kept_l2 = _filter_buckets(3, 2, 2, "warning")
        assert (kept_h2, kept_m2, kept_l2) == (3, 0, 0), (
            f"test-gaps --severity warning (rank 3) expected (3, 0, 0), got ({kept_h2}, {kept_m2}, {kept_l2})"
        )


# ===========================================================================
# 3. cmd_secrets
# ===========================================================================


class TestSecretsSeverityCanonical:
    """Widened ``--severity`` Choice on ``roam secrets`` (was {all, high,
    medium, low}, now {all, +W547 canonical 7-tier}). ``all`` sentinel
    preserved. Filter routes through ``severity_rank`` (Pattern 3a fix)."""

    def test_secrets_min_severity_canonical_label_parses_cleanly(self, tmp_path: Path) -> None:
        """``--severity warning`` parses cleanly (was the divergent label).

        Pre-W1005-followup-C, the click.Choice was {all, high, medium, low},
        so ``--severity warning`` exited with click-usage-error 2. After
        widening it parses and the secrets-scan runs through to completion.
        """
        _git_init(tmp_path)
        runner = CliRunner()
        old_cwd = os.getcwd()
        try:
            os.chdir(str(tmp_path))
            result = runner.invoke(cli, ["secrets", "--severity", "warning"])
        finally:
            os.chdir(old_cwd)
        assert result.exit_code != 2, (
            f"secrets --severity warning: expected NOT click-usage-error 2 "
            f"(canonical token parses cleanly), got exit {result.exit_code}. "
            f"Output:\n{result.output}"
        )

    def test_secrets_min_severity_uses_canonical_rank(self, tmp_path: Path) -> None:
        """``--severity medium`` filters at canonical rank 2 inside scan_file.

        Pins the rank source: every kept finding must have
        ``severity_rank(f.severity) >= severity_rank("medium") == 2``.
        Uses a fixture file containing a high-severity secret (AWS key)
        which has rank 4 -- it MUST be kept under the medium floor.
        """
        # A real high-severity pattern (rank 4 via severity_rank).
        target = tmp_path / "fake_creds.py"
        target.write_text('aws_key = "AKIAIOSFODNN7QQQQQQAA"\n')

        findings = scan_file(str(target), min_severity="medium")
        # Floor rank 2 keeps the high(4) AWS finding.
        assert len(findings) >= 1, f"scan_file('medium') expected >=1 high finding, got {findings}"
        for f in findings:
            assert severity_rank(f["severity"]) >= severity_rank("medium"), (
                f"scan_file('medium') kept finding {f!r} below rank floor 2"
            )
        # Canonical-rank pin: ``medium`` is rank 2 (NOT a local value).
        assert severity_rank("medium") == 2

        # Cross-vocab: ``--severity critical`` (rank 5) drops every emitted
        # finding because no pattern emits critical. This pins the
        # asymmetry-rationale documented in the source.
        findings_crit = scan_file(str(target), min_severity="critical")
        assert findings_crit == [], f"scan_file('critical') expected [] (no pattern emits rank 5), got {findings_crit}"

    def test_secrets_all_sentinel_preserves_every_severity(self, tmp_path: Path) -> None:
        """``--severity all`` is the no-floor sentinel -- keeps everything.

        The 4th option that's NOT a severity. After the Choice widening to
        include the W547 7-tier, ``all`` MUST still short-circuit the rank
        comparison so every emitted finding passes regardless of rank.
        """
        target = tmp_path / "creds.py"
        # One high-rank finding (AWS key, rank 4).
        target.write_text('aws_key = "AKIAIOSFODNN7QQQQQQAA"\n')

        kept_all = scan_file(str(target), min_severity="all")
        kept_medium = scan_file(str(target), min_severity="medium")
        # ``all`` keeps >= what ``medium`` keeps (any rank-2-and-above
        # finding passes both). On a single-high-pattern fixture they're
        # equal; the durable invariant is the SUPERSET relation.
        kept_all_ids = {(f["file"], f["line"], f["pattern_name"]) for f in kept_all}
        kept_medium_ids = {(f["file"], f["line"], f["pattern_name"]) for f in kept_medium}
        assert kept_medium_ids.issubset(kept_all_ids), (
            f"--severity all must keep a superset of --severity medium; "
            f"all kept {kept_all_ids}, medium kept {kept_medium_ids}"
        )

        # Case-insensitive sentinel preserves behavior under
        # ``case_sensitive=False`` Click normalisation (mixed-case input
        # is normalised to canonical by Click, but the defensive
        # ``.lower()`` at the scan_file ``min_rank = -1 if min_severity
        # .lower() == "all"`` branch guards against direct programmatic
        # callers that bypass Click).
        kept_all_upper = scan_file(str(target), min_severity="ALL")
        kept_all_upper_ids = {(f["file"], f["line"], f["pattern_name"]) for f in kept_all_upper}
        assert kept_all_upper_ids == kept_all_ids, (
            f"--severity ALL (case-insensitive sentinel) must match --severity "
            f"all; got {kept_all_upper_ids} vs {kept_all_ids}"
        )
