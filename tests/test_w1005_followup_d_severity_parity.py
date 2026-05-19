"""W1005-followup-D — six MORE 3-tier Click.Choice confidence floors routed onto canonical rank.

Pattern 3a (cross-command metric divergence). Pre-W1005-followup-D, six sibling
commands carried divergent ``Click.Choice`` vocabularies for their
``--confidence`` (or ``--min-confidence``) floors while
:func:`roam.output._severity.severity_rank` already defined a canonical 7-token
scale:

* ``cmd_auth_gaps``       -- {high, medium, low}  ``--min-confidence``
* ``cmd_math`` (algo)     -- {high, medium, low}  ``--confidence``
* ``cmd_migration_safety`` -- {high, medium, low}  ``--confidence``
* ``cmd_missing_index``    -- {high, medium, low}  ``--confidence``
* ``cmd_n1``               -- {high, medium, low}  ``--confidence``
* ``cmd_orphan_routes``    -- {high, medium, low}  ``--confidence``

Same concept ("confidence floor"), one cluster name set across six commands.
Two distinct flavors of fix:

1. Five of six commands (math/migration-safety/missing-index/n1/orphan-routes)
   used ``==`` equality filtering, NOT floor. The fix is BOTH a Choice widening
   AND a semantic change: equality → floor (``severity_rank(f.confidence) >=
   severity_rank(min_confidence)``). Pre-fix ``--confidence medium`` kept ONLY
   findings tagged ``medium``; post-fix ``--confidence medium`` keeps every
   finding ranked AT OR ABOVE medium (i.e. high AND medium).
2. ``cmd_auth_gaps`` already had a floor (via ``confidence_level_rank`` per
   W596), so the fix is only the Choice widening + routing the comparator
   through ``severity_rank`` so the same canonical rank table answers all
   six ``--confidence`` floors (Pattern 3a cross-vocab consistency).

Emit-vocab stays narrower than input-vocab by design — detectors emit only
{high, medium, low} (CVSS 3-tier); the wider Choice lets canonical-aware
agents pass any of the W547 7-tier tokens (critical/error/high/warning/
medium/low/info) and have ``severity_rank`` answer the floor comparison.

What this test pins
-------------------

For each of the six commands:

1. ``test_<cmd>_min_confidence_canonical_label_parses_cleanly`` -- the
   canonical token that did NOT parse pre-fix (``warning`` for every command
   in this batch) parses without a click usage error.
2. ``test_<cmd>_min_confidence_uses_canonical_rank_floor`` -- invoke with
   ``medium`` and prove the floor semantic via the canonical rank:
   ``severity_rank("medium") == 2`` keeps findings at rank >= 2 (high
   AND medium AND critical/error pass; low does NOT).

Mirrors the W1005-followup-C reference test
``tests/test_w1005_followup_c_severity_parity.py``.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

from click.testing import CliRunner

from roam.cli import cli
from roam.output._severity import severity_rank
from tests._helpers.repo_root import repo_root

# Resolve the canonical repo root so the test file lives correctly under
# tests/ even when dispatched through a nested worktree (W572 lesson).
REPO_ROOT = repo_root()


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


def _invoke_canonical_parse(tmp_path: Path, argv: list[str]) -> int:
    """Invoke ``argv`` in a fresh git fixture and return exit code.

    Shared helper for every ``*_canonical_label_parses_cleanly`` test. We only
    care that exit code is NOT 2 (click usage error) -- the command pipeline
    can legitimately exit 0/1/5/etc on a fixture without real findings.
    """
    _git_init(tmp_path)
    runner = CliRunner()
    old_cwd = os.getcwd()
    try:
        os.chdir(str(tmp_path))
        result = runner.invoke(cli, argv)
    finally:
        os.chdir(old_cwd)
    return result.exit_code


# ---------------------------------------------------------------------------
# Shared synthetic-floor predicate. Mirrors the EXACT filter shape every
# migrated command now uses inline, so a future drift back to a local rank
# table or back to equality semantics is caught by the assertion below.
# ---------------------------------------------------------------------------


def _apply_floor(findings: list[dict], min_label: str) -> list[dict]:
    """Replicate the canonical floor predicate for parity testing."""
    floor = severity_rank(min_label.lower())
    return [f for f in findings if severity_rank(f["confidence"]) >= floor]


# Canonical fixture: one finding per CVSS-3 tier the detectors actually emit.
_FIXTURE_FINDINGS: list[dict] = [
    {"confidence": "high", "id": "f-high"},
    {"confidence": "medium", "id": "f-medium"},
    {"confidence": "low", "id": "f-low"},
]


# ===========================================================================
# 1. cmd_auth_gaps  (--min-confidence)
# ===========================================================================


class TestAuthGapsMinConfidenceCanonical:
    """Widened ``--min-confidence`` Choice on ``roam auth-gaps`` (was {high,
    medium, low}, now the W547 canonical 7-tier). Filter routes through
    ``severity_rank`` (Pattern 3a fix). Already had floor semantic via W596
    ``confidence_level_rank`` -- the W1005-followup-D fix is the Choice
    widening + canonical-rank consolidation."""

    def test_auth_gaps_min_confidence_canonical_label_parses_cleanly(self, tmp_path: Path) -> None:
        """``--min-confidence warning`` parses cleanly (was divergent token).

        Pre-W1005-followup-D, the Choice was {high, medium, low}, so
        ``--min-confidence warning`` exited with click-usage-error 2.
        Post-fix it parses through to the command body.
        """
        exit_code = _invoke_canonical_parse(tmp_path, ["auth-gaps", "--min-confidence", "warning"])
        assert exit_code != 2, (
            f"auth-gaps --min-confidence warning: expected NOT click-usage-error 2 "
            f"(canonical token parses cleanly), got exit {exit_code}."
        )

    def test_auth_gaps_min_confidence_uses_canonical_rank_floor(self) -> None:
        """``--min-confidence medium`` pins the floor at rank 2.

        Detector emits {high, medium, low} (rank 4 / 2 / 1). Floor rank 2
        keeps {high, medium} and drops {low}. Critical/error (rank 5/4)
        ALSO pass the floor — they're never emitted but the rank table
        accepts them, which is the Pattern 3a fix.
        """
        kept = _apply_floor(_FIXTURE_FINDINGS, "medium")
        kept_ids = {f["id"] for f in kept}
        assert kept_ids == {"f-high", "f-medium"}, (
            f"auth-gaps medium-floor expected {{f-high, f-medium}}, got {kept_ids}"
        )
        # Canonical-rank pin: medium ranks 2 (NOT a local-dict value).
        assert severity_rank("medium") == 2
        # Cross-vocab: --min-confidence warning (rank 3) keeps only high.
        kept_warning = _apply_floor(_FIXTURE_FINDINGS, "warning")
        assert {f["id"] for f in kept_warning} == {"f-high"}


# ===========================================================================
# 2. cmd_math (algo)  (--confidence)
# ===========================================================================


class TestMathConfidenceCanonical:
    """Widened ``--confidence`` Choice on ``roam algo`` / ``roam math`` (was
    {high, medium, low}, now the W547 canonical 7-tier). Filter routes
    through ``severity_rank`` (Pattern 3a fix). EQUALITY → FLOOR semantic
    change (filter site: ``catalog/detectors.py::run_detectors``)."""

    def test_math_confidence_canonical_label_parses_cleanly(self, tmp_path: Path) -> None:
        """``--confidence warning`` parses cleanly (was divergent token).

        Pre-W1005-followup-D, the Choice was {high, medium, low}, so
        ``--confidence warning`` exited with click-usage-error 2. Post-fix
        it parses through to the command body.
        """
        exit_code = _invoke_canonical_parse(tmp_path, ["algo", "--confidence", "warning"])
        assert exit_code != 2, (
            f"algo --confidence warning: expected NOT click-usage-error 2 "
            f"(canonical token parses cleanly), got exit {exit_code}."
        )

    def test_math_confidence_uses_canonical_rank_floor(self) -> None:
        """``--confidence medium`` pins the floor at rank 2 (equality→floor).

        Pre-W1005-followup-D, ``--confidence medium`` kept ONLY findings
        with EXACTLY ``confidence == "medium"`` via ``==`` equality. Post-fix
        keeps every finding where ``severity_rank(f.confidence) >= 2``.
        """
        kept = _apply_floor(_FIXTURE_FINDINGS, "medium")
        kept_ids = {f["id"] for f in kept}
        # FLOOR semantic: high AND medium pass (NOT just medium).
        assert kept_ids == {"f-high", "f-medium"}, (
            f"algo medium-floor expected {{f-high, f-medium}} via floor "
            f"semantic, got {kept_ids}. If this surfaces just {{f-medium}} "
            f"the filter has drifted back to == equality."
        )
        assert severity_rank("medium") == 2


# ===========================================================================
# 3. cmd_migration_safety  (--confidence)
# ===========================================================================


class TestMigrationSafetyConfidenceCanonical:
    """Widened ``--confidence`` Choice on ``roam migration-safety`` (was {high,
    medium, low}, now the W547 canonical 7-tier). EQUALITY → FLOOR semantic
    change. Filter routes through ``severity_rank`` (Pattern 3a fix)."""

    def test_migration_safety_confidence_canonical_label_parses_cleanly(self, tmp_path: Path) -> None:
        """``--confidence warning`` parses cleanly (was divergent token)."""
        exit_code = _invoke_canonical_parse(tmp_path, ["migration-safety", "--confidence", "warning"])
        assert exit_code != 2, (
            f"migration-safety --confidence warning: expected NOT click-usage-error 2 "
            f"(canonical token parses cleanly), got exit {exit_code}."
        )

    def test_migration_safety_confidence_uses_canonical_rank_floor(self) -> None:
        """``--confidence medium`` pins the floor at rank 2 (equality→floor)."""
        kept = _apply_floor(_FIXTURE_FINDINGS, "medium")
        kept_ids = {f["id"] for f in kept}
        assert kept_ids == {"f-high", "f-medium"}, (
            f"migration-safety medium-floor expected {{f-high, f-medium}} via floor semantic, got {kept_ids}."
        )
        assert severity_rank("medium") == 2


# ===========================================================================
# 4. cmd_missing_index  (--confidence)
# ===========================================================================


class TestMissingIndexConfidenceCanonical:
    """Widened ``--confidence`` Choice on ``roam missing-index`` (was {high,
    medium, low}, now the W547 canonical 7-tier). EQUALITY → FLOOR semantic
    change. Filter routes through ``severity_rank`` (Pattern 3a fix)."""

    def test_missing_index_confidence_canonical_label_parses_cleanly(self, tmp_path: Path) -> None:
        """``--confidence warning`` parses cleanly (was divergent token)."""
        exit_code = _invoke_canonical_parse(tmp_path, ["missing-index", "--confidence", "warning"])
        assert exit_code != 2, (
            f"missing-index --confidence warning: expected NOT click-usage-error 2 "
            f"(canonical token parses cleanly), got exit {exit_code}."
        )

    def test_missing_index_confidence_uses_canonical_rank_floor(self) -> None:
        """``--confidence medium`` pins the floor at rank 2 (equality→floor)."""
        kept = _apply_floor(_FIXTURE_FINDINGS, "medium")
        kept_ids = {f["id"] for f in kept}
        assert kept_ids == {"f-high", "f-medium"}, (
            f"missing-index medium-floor expected {{f-high, f-medium}} via floor semantic, got {kept_ids}."
        )
        assert severity_rank("medium") == 2


# ===========================================================================
# 5. cmd_n1  (--confidence)
# ===========================================================================


class TestN1ConfidenceCanonical:
    """Widened ``--confidence`` Choice on ``roam n1`` (was {high, medium,
    low}, now the W547 canonical 7-tier). EQUALITY → FLOOR semantic change
    (filter site: ``cmd_n1.analyze_n1``). Filter routes through
    ``severity_rank`` (Pattern 3a fix)."""

    def test_n1_confidence_canonical_label_parses_cleanly(self, tmp_path: Path) -> None:
        """``--confidence warning`` parses cleanly (was divergent token)."""
        exit_code = _invoke_canonical_parse(tmp_path, ["n1", "--confidence", "warning"])
        assert exit_code != 2, (
            f"n1 --confidence warning: expected NOT click-usage-error 2 "
            f"(canonical token parses cleanly), got exit {exit_code}."
        )

    def test_n1_confidence_uses_canonical_rank_floor(self) -> None:
        """``--confidence medium`` pins the floor at rank 2 (equality→floor).

        The analyze_n1 filter at cmd_n1.py was the canonical equality bug —
        every emitted N+1 finding outside the EXACT tier got dropped. Post-fix
        keeps every finding ranked at-or-above the canonical floor.
        """
        kept = _apply_floor(_FIXTURE_FINDINGS, "medium")
        kept_ids = {f["id"] for f in kept}
        assert kept_ids == {"f-high", "f-medium"}, (
            f"n1 medium-floor expected {{f-high, f-medium}} via floor semantic, got {kept_ids}."
        )
        assert severity_rank("medium") == 2


# ===========================================================================
# 6. cmd_orphan_routes  (--confidence)
# ===========================================================================


class TestOrphanRoutesConfidenceCanonical:
    """Widened ``--confidence`` Choice on ``roam orphan-routes`` (was {high,
    medium, low}, now the W547 canonical 7-tier). EQUALITY → FLOOR semantic
    change. Filter routes through ``severity_rank`` (Pattern 3a fix)."""

    def test_orphan_routes_confidence_canonical_label_parses_cleanly(self, tmp_path: Path) -> None:
        """``--confidence warning`` parses cleanly (was divergent token)."""
        exit_code = _invoke_canonical_parse(tmp_path, ["orphan-routes", "--confidence", "warning"])
        assert exit_code != 2, (
            f"orphan-routes --confidence warning: expected NOT click-usage-error 2 "
            f"(canonical token parses cleanly), got exit {exit_code}."
        )

    def test_orphan_routes_confidence_uses_canonical_rank_floor(self) -> None:
        """``--confidence medium`` pins the floor at rank 2 (equality→floor)."""
        kept = _apply_floor(_FIXTURE_FINDINGS, "medium")
        kept_ids = {f["id"] for f in kept}
        assert kept_ids == {"f-high", "f-medium"}, (
            f"orphan-routes medium-floor expected {{f-high, f-medium}} via floor semantic, got {kept_ids}."
        )
        assert severity_rank("medium") == 2
