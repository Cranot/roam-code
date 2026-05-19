"""W1005-followup-F -- cmd_api_changes SemVer severity widened with canonical alias map.

Pattern 3a (cross-command metric divergence). Pre-W1005-followup-F,
``roam api-changes --severity`` accepted ONLY the SemVer 3-tier
``{breaking, warning, info}`` -- semantically a contract-change tier,
NOT a CVSS-style finding severity. An agent fluent in the W547 canonical
vocab (``critical / error / high / warning / medium / low / info``) who
typed ``--severity high`` (because that's what ``roam smells``,
``roam alerts``, etc. accept post-W1005) hit a click usage error 2.

Path A (the chosen fix). Widen Click.Choice to accept BOTH vocabs;
project canonical tokens onto the SemVer 3-tier via
:data:`_CANONICAL_TO_SEMVER` before the existing ``_SEVERITY_ORDER``
floor comparator runs. The EMIT vocab stays SemVer (every
``change["severity"]`` in the JSON envelope is one of
``breaking``/``warning``/``info``) so downstream consumers are
unchanged. The INPUT vocab is the union.

Projection (one-way):
    critical / error / high -> breaking
    warning                 -> warning  (identity)
    medium                  -> warning  (no slot between warning/info)
    info / low / note       -> info

What this test pins
-------------------

1. SemVer label parses (back-compat) -- existing behaviour unchanged.
2. Canonical label parses (Pattern 3a fix) -- ``--severity high`` no
   longer trips click usage error 2.
3. Projection semantics + floor comparator agree with the alias map --
   ``--severity high`` keeps SemVer-breaking-tier changes only;
   ``--severity medium`` keeps breaking + warning (projects to
   ``warning``); ``--severity low`` keeps all three (projects to
   ``info``).
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

from click.testing import CliRunner

from roam.cli import cli
from roam.commands.cmd_api_changes import (
    _CANONICAL_TO_SEMVER,
    _SEVERITY_ORDER,
    _project_severity_input,
)
from tests._helpers.repo_root import repo_root

# Resolve the canonical repo root so the test file lives correctly under
# tests/ even when dispatched through a nested worktree (W572 lesson).
REPO_ROOT = repo_root()


# ---------------------------------------------------------------------------
# Tiny git fixture helper -- enough to satisfy ``ensure_index()``'s preflight
# without requiring a full corpus. The parity contract under test is the
# Click.Choice parse boundary + the projection map's floor semantic, NOT the
# downstream API-diff pipeline.
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


def _invoke_parse(tmp_path: Path, argv: list[str]) -> int:
    """Invoke ``argv`` in a fresh git fixture and return exit code.

    We only care that exit code is NOT 2 (click usage error) -- the
    command pipeline can legitimately exit 0/1 on a fixture without any
    actual API changes against HEAD.
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
# Synthetic fixture: one change per SemVer emit tier. Mirrors the EMIT
# shape the detector produces so the floor predicate can be exercised
# without invoking the full diff pipeline.
# ---------------------------------------------------------------------------


_FIXTURE_CHANGES: list[dict] = [
    {"severity": "breaking", "id": "c-breaking"},
    {"severity": "warning", "id": "c-warning"},
    {"severity": "info", "id": "c-info"},
]


def _apply_floor(changes: list[dict], min_label: str) -> list[dict]:
    """Replicate the in-command filter predicate via the projection map.

    Pre-W1005-followup-F shape (unchanged on the floor side):
        min_severity = _SEVERITY_ORDER[_project_severity_input(min_label)]
        kept = [c for c in changes if _SEVERITY_ORDER[c["severity"]] <= min_severity]

    SemVer polarity is LOWER numeric = WORSE (breaking=0, warning=1,
    info=2), so the comparator is ``<= min_severity`` (NOT ``>=`` like
    severity_rank()). This is the existing api-changes contract; the
    widening only widens INPUT parsing.
    """
    min_severity = _SEVERITY_ORDER[_project_severity_input(min_label)]
    return [c for c in changes if _SEVERITY_ORDER[c["severity"]] <= min_severity]


# ===========================================================================
# 1. SemVer label parses cleanly (back-compat unchanged)
# ===========================================================================


class TestSemVerLabelsParse:
    """Back-compat: the three SemVer tokens still parse without usage error."""

    def test_breaking_label_parses_cleanly(self, tmp_path: Path) -> None:
        """``--severity breaking`` parses cleanly (back-compat unchanged)."""
        exit_code = _invoke_parse(tmp_path, ["api-changes", "--base", "HEAD", "--severity", "breaking"])
        assert exit_code != 2, (
            f"api-changes --severity breaking: expected NOT click-usage-error 2 "
            f"(SemVer token unchanged), got exit {exit_code}."
        )

    def test_warning_label_parses_cleanly(self, tmp_path: Path) -> None:
        """``--severity warning`` parses cleanly (back-compat unchanged)."""
        exit_code = _invoke_parse(tmp_path, ["api-changes", "--base", "HEAD", "--severity", "warning"])
        assert exit_code != 2

    def test_info_label_parses_cleanly(self, tmp_path: Path) -> None:
        """``--severity info`` parses cleanly (back-compat unchanged)."""
        exit_code = _invoke_parse(tmp_path, ["api-changes", "--base", "HEAD", "--severity", "info"])
        assert exit_code != 2


# ===========================================================================
# 2. Canonical W547 labels parse cleanly (Pattern 3a fix)
# ===========================================================================


class TestCanonicalLabelsParse:
    """Pattern 3a fix: W547 canonical tokens parse without usage error."""

    def test_high_label_parses_cleanly(self, tmp_path: Path) -> None:
        """``--severity high`` parses cleanly (pre-fix tripped usage error 2).

        ``high`` is the W547 token that maps onto SemVer ``breaking``
        in the projection. Pre-W1005-followup-F this exited 2 (click
        usage error).
        """
        exit_code = _invoke_parse(tmp_path, ["api-changes", "--base", "HEAD", "--severity", "high"])
        assert exit_code != 2, (
            f"api-changes --severity high: expected NOT click-usage-error 2 "
            f"(canonical token parses via W547 alias), got exit {exit_code}."
        )

    def test_critical_label_parses_cleanly(self, tmp_path: Path) -> None:
        """``--severity critical`` parses cleanly (canonical alias)."""
        exit_code = _invoke_parse(tmp_path, ["api-changes", "--base", "HEAD", "--severity", "critical"])
        assert exit_code != 2

    def test_medium_label_parses_cleanly(self, tmp_path: Path) -> None:
        """``--severity medium`` parses cleanly (canonical alias)."""
        exit_code = _invoke_parse(tmp_path, ["api-changes", "--base", "HEAD", "--severity", "medium"])
        assert exit_code != 2

    def test_low_label_parses_cleanly(self, tmp_path: Path) -> None:
        """``--severity low`` parses cleanly (canonical alias)."""
        exit_code = _invoke_parse(tmp_path, ["api-changes", "--base", "HEAD", "--severity", "low"])
        assert exit_code != 2

    def test_canonical_tokens_all_in_choice(self) -> None:
        """Every key of _CANONICAL_TO_SEMVER also lives in the Click.Choice.

        Drift guard: if a contributor adds a canonical token to the
        alias map but forgets the Click.Choice widening, ``--severity
        <new>`` would still trip usage error 2. This pin catches that.
        """
        from roam.commands.cmd_api_changes import api_changes

        # Find the --severity option's Choice values.
        severity_opt = next(p for p in api_changes.params if p.name == "severity")
        choice_values = set(severity_opt.type.choices)
        # Every projected (canonical) token must be a valid Choice value.
        for canonical_token in _CANONICAL_TO_SEMVER:
            assert canonical_token in choice_values, (
                f"_CANONICAL_TO_SEMVER includes {canonical_token!r} but the "
                f"--severity Click.Choice does not -- widening drifted out of "
                f"sync with the alias map. Choice: {sorted(choice_values)}."
            )


# ===========================================================================
# 3. Floor semantic + projection agree (cross-tier comparison)
# ===========================================================================


class TestProjectedFloorSemantic:
    """Canonical floor labels filter the SemVer emit changes correctly."""

    def test_high_keeps_breaking_only(self) -> None:
        """``--severity high`` projects to ``breaking``; keeps breaking only.

        ``_CANONICAL_TO_SEMVER["high"] == "breaking"``, ``_SEVERITY_ORDER
        ["breaking"] == 0``. The filter keeps changes with rank <= 0,
        i.e. only ``severity == "breaking"`` (warning=1, info=2 drop out).
        """
        kept = _apply_floor(_FIXTURE_CHANGES, "high")
        kept_ids = {c["id"] for c in kept}
        assert kept_ids == {"c-breaking"}, (
            f"--severity high (projects to breaking) expected {{c-breaking}}, "
            f"got {kept_ids}. If this surfaces more tiers the projection "
            f"map drifted off-spec."
        )

    def test_medium_keeps_breaking_and_warning(self) -> None:
        """``--severity medium`` projects to ``warning``; keeps breaking + warning.

        ``_CANONICAL_TO_SEMVER["medium"] == "warning"``, ``_SEVERITY_ORDER
        ["warning"] == 1``. The filter keeps changes with rank <= 1
        (breaking=0 + warning=1 pass; info=2 drops).
        """
        kept = _apply_floor(_FIXTURE_CHANGES, "medium")
        kept_ids = {c["id"] for c in kept}
        assert kept_ids == {"c-breaking", "c-warning"}, (
            f"--severity medium (projects to warning) expected {{c-breaking, c-warning}}, got {kept_ids}."
        )

    def test_low_keeps_all_three_tiers(self) -> None:
        """``--severity low`` projects to ``info``; keeps every tier.

        ``_CANONICAL_TO_SEMVER["low"] == "info"``, ``_SEVERITY_ORDER
        ["info"] == 2``. The filter keeps changes with rank <= 2
        (every SemVer tier passes).
        """
        kept = _apply_floor(_FIXTURE_CHANGES, "low")
        kept_ids = {c["id"] for c in kept}
        assert kept_ids == {"c-breaking", "c-warning", "c-info"}, (
            f"--severity low (projects to info) expected all three tiers, got {kept_ids}."
        )

    def test_breaking_label_keeps_breaking_only(self) -> None:
        """Back-compat: ``--severity breaking`` (SemVer) keeps breaking only."""
        kept = _apply_floor(_FIXTURE_CHANGES, "breaking")
        kept_ids = {c["id"] for c in kept}
        assert kept_ids == {"c-breaking"}

    def test_case_insensitive_canonical_label(self) -> None:
        """``--severity HIGH`` projects to ``breaking`` (case-insensitive)."""
        kept = _apply_floor(_FIXTURE_CHANGES, "HIGH")
        kept_ids = {c["id"] for c in kept}
        assert kept_ids == {"c-breaking"}

    def test_projection_map_covers_every_canonical_token(self) -> None:
        """_CANONICAL_TO_SEMVER projects every value into the SemVer 3-tier.

        Polarity-correctness guard: a future contributor extending the
        canonical vocab must also map the new token onto one of the
        SemVer 3-tier slots, NOT introduce a fourth slot to
        _SEVERITY_ORDER (which would silently break the polarity
        contract).
        """
        for canonical, semver in _CANONICAL_TO_SEMVER.items():
            assert semver in _SEVERITY_ORDER, (
                f"_CANONICAL_TO_SEMVER[{canonical!r}] -> {semver!r} is NOT a "
                f"valid SemVer slot ({sorted(_SEVERITY_ORDER)})."
            )
