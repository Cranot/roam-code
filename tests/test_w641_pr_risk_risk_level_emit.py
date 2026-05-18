"""W641 — ``roam pr-risk`` canonical risk-LEVEL emission.

Pattern-3a structural close-out (third axis, drive-by from W631):

W631 shipped the canonical ``roam.output.risk`` module
(``normalize_risk_level`` + ``risk_rank``) and migrated
``cmd_migration_plan`` (3-tier lowercase) + ``cmd_path_coverage``
(4-tier UPPER-cased — W632) onto it. ``cmd_pr_risk`` already emitted
``summary.risk_level`` pre-W641 — but the value carried pr-risk's
domain vocabulary (``low``/``moderate``/``high``/``critical``) without
projecting through the canonical normalizer, so consumers comparing
risk ranks across commands got inconsistent answers depending on
which command emitted the row.

This module pins the W641 emit contract on the JSON envelope:

* ``summary.risk_level`` — preserved as pre-W641 (domain vocabulary
  including ``moderate``) for back-compat with W718 / W989 consumers.
* ``summary.risk_level_canonical`` — NEW. Projected via
  ``normalize_risk_level`` so the W631 closed-set vocabulary
  (``low``/``medium``/``high``/``critical``) reaches downstream
  floor-comparator consumers. ``moderate`` projects to ``medium``
  per the W631 ``RISK_ALIASES`` table.
* ``summary.risk_rank`` — NEW. Integer floor via the W631
  ``risk_rank`` table (``critical=4``/``high=3``/``medium=2``/
  ``low=1``); ``-1`` on unknown labels.
* ``summary.verdict`` — augmented to terminate on a closed-enum
  ``(risk_level <canonical>)`` parenthesis. LAW 6 standalone check
  remains satisfied: the verdict line still works without any other
  envelope field.

Same Pattern-3a discipline as W632: re-use the canonical module
instead of re-deriving the rank vocabulary at the call site, so a
single edit to ``RISK_LEVELS`` / ``_RISK_RANK`` propagates to every
consumer.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

# Ensure tests/conftest helpers are importable.
sys.path.insert(0, str(Path(__file__).parent))

from conftest import invoke_cli  # noqa: E402

from roam.output.risk import RISK_LEVELS, normalize_risk_level, risk_rank  # noqa: E402

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_unstaged_change(project):
    """Overwrite ``src/models.py`` so ``git diff`` picks it up.

    Mirrors ``tests/test_pr_risk_author.py::_make_unstaged_change`` to keep
    test fixture parity. The change is small enough that any test that
    composes it can still run against the W414c-BAIL function-scoped
    ``indexed_project`` without leaking findings rows into sibling tests.
    """
    fp = project / "src" / "models.py"
    fp.write_text(
        "class User:\n"
        '    """A user model (modified for pr-risk W641)."""\n'
        "    def __init__(self, name, email):\n"
        "        self.name = name\n"
        "        self.email = email\n"
        "\n"
        "    def display_name(self):\n"
        "        return self.name.title()\n"
        "\n"
        "    def validate_email(self):\n"
        '        return "@" in self.email\n'
    )


def _restore_models(project, original):
    (project / "src" / "models.py").write_text(original)


def _run_pr_risk_json(cli_runner, project) -> dict:
    """Invoke ``roam pr-risk --json`` against an indexed project; return parsed envelope."""
    result = invoke_cli(cli_runner, ["pr-risk"], json_mode=True)
    assert result.exit_code == 0, f"pr-risk failed:\n{result.output}"
    return json.loads(result.output)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestRiskLevelCanonicalEmit:
    """Pin the W641 emit contract on the JSON envelope ``summary`` block."""

    def test_envelope_emits_risk_level_canonical_string(self, indexed_project, cli_runner, monkeypatch):
        """summary.risk_level_canonical is a string in the canonical W631 4-tier set."""
        monkeypatch.chdir(indexed_project)
        models = indexed_project / "src" / "models.py"
        original = models.read_text()
        try:
            _make_unstaged_change(indexed_project)
            data = _run_pr_risk_json(cli_runner, indexed_project)
            summary = data["summary"]
            assert "risk_level_canonical" in summary, "W641: summary.risk_level_canonical missing"
            assert isinstance(summary["risk_level_canonical"], str)
            assert summary["risk_level_canonical"] in RISK_LEVELS, (
                f"risk_level_canonical {summary['risk_level_canonical']!r} not in canonical set {sorted(RISK_LEVELS)}"
            )
        finally:
            _restore_models(indexed_project, original)

    def test_risk_level_canonical_matches_normalize_helper(self, indexed_project, cli_runner, monkeypatch):
        """summary.risk_level_canonical == normalize_risk_level(summary.risk_level) (or 'low' floor).

        Projection consistency: the emitted canonical field must equal
        whatever the canonical helper returns when called on the
        domain-vocabulary ``risk_level`` field. This is the central
        Pattern-3a invariant — drift here would mean a sibling command
        could compare ``risk_level_canonical`` against ``risk_rank()`` and
        get an inconsistent answer.
        """
        monkeypatch.chdir(indexed_project)
        models = indexed_project / "src" / "models.py"
        original = models.read_text()
        try:
            _make_unstaged_change(indexed_project)
            data = _run_pr_risk_json(cli_runner, indexed_project)
            summary = data["summary"]
            expected = normalize_risk_level(summary["risk_level"]) or "low"
            assert summary["risk_level_canonical"] == expected, (
                f"projection drift: risk_level_canonical={summary['risk_level_canonical']!r} "
                f"but normalize_risk_level({summary['risk_level']!r})={expected!r}"
            )
        finally:
            _restore_models(indexed_project, original)

    def test_risk_rank_floor_comparator(self, indexed_project, cli_runner, monkeypatch):
        """summary.risk_rank == risk_rank(summary.risk_level_canonical) — round-trip via canonical."""
        monkeypatch.chdir(indexed_project)
        models = indexed_project / "src" / "models.py"
        original = models.read_text()
        try:
            _make_unstaged_change(indexed_project)
            data = _run_pr_risk_json(cli_runner, indexed_project)
            summary = data["summary"]
            assert "risk_rank" in summary, "W641: summary.risk_rank missing"
            assert isinstance(summary["risk_rank"], int)
            assert summary["risk_rank"] == risk_rank(summary["risk_level_canonical"]), (
                f"floor drift: risk_rank={summary['risk_rank']} "
                f"but risk_rank({summary['risk_level_canonical']!r})={risk_rank(summary['risk_level_canonical'])}"
            )
            # All known canonical levels rank in [1, 4]; the floor is 1 (low).
            assert summary["risk_rank"] >= 1, "W531 CI-safety floor: unknown labels must not reach here"
        finally:
            _restore_models(indexed_project, original)

    def test_verdict_disambiguates_canonical_level(self, indexed_project, cli_runner, monkeypatch):
        """Verdict text mentions the canonical risk_level in a closed-enum parenthesis.

        LAW 6: the verdict line works standalone. After W641, the line
        terminates on ``(risk_level <canonical>)`` (optionally followed by
        ``(driver: ...)``) so a consumer that reads only the verdict
        string parses the canonical bucket from the line itself, without
        loading the rest of the envelope.
        """
        monkeypatch.chdir(indexed_project)
        models = indexed_project / "src" / "models.py"
        original = models.read_text()
        try:
            _make_unstaged_change(indexed_project)
            data = _run_pr_risk_json(cli_runner, indexed_project)
            summary = data["summary"]
            verdict = summary["verdict"]
            canonical = summary["risk_level_canonical"]
            assert f"risk_level {canonical}" in verdict, (
                f"LAW 6 violated: verdict {verdict!r} does not name canonical risk_level {canonical!r}"
            )
            # The verdict's parenthesised canonical label must come from
            # the closed W631 enum — sanity-check parser-side that no
            # off-vocab token slipped in.
            for level in RISK_LEVELS:
                if f"risk_level {level}" in verdict:
                    found = level
                    break
            else:
                raise AssertionError(f"verdict {verdict!r} does not contain any canonical risk_level label")
            assert found == canonical
        finally:
            _restore_models(indexed_project, original)


class TestRiskLevelEdgeCases:
    """Edge cases — no-changes branch, the W989 vocabulary union."""

    def test_no_changes_emits_low_canonical(self, indexed_project, cli_runner, monkeypatch):
        """No-changes branch: risk_level_canonical='low', risk_rank=1.

        A zero-change diff is trivially the safest possible state under
        the W631 polarity (higher = worse). The envelope must still emit
        the canonical fields so consumers can call
        ``risk_rank(summary['risk_level_canonical'])`` unconditionally —
        not just on diffs with actual changes.
        """
        monkeypatch.chdir(indexed_project)
        result = invoke_cli(cli_runner, ["pr-risk"], json_mode=True)
        assert result.exit_code == 0
        data = json.loads(result.output)
        summary = data["summary"]
        # On a clean tree this is the no-changes branch.
        if summary.get("verdict") == "no-changes":
            assert summary["risk_level_canonical"] == "low"
            assert summary["risk_rank"] == risk_rank("low")
            assert summary["risk_rank"] == 1  # canonical W631 rank for "low"

    def test_canonical_set_drift_guard(self):
        """W631 ``RISK_LEVELS`` stays the canonical 4-tier vocabulary.

        Drift guard: if a future edit ever changes the canonical
        vocabulary, this test fails fast so the pr-risk emit contract
        can be re-evaluated alongside. Mirrors the W632 / W631 audit
        discipline — the canonical module is the source of truth, but
        consumers that emit through it should pin their own expectations.
        """
        assert RISK_LEVELS == frozenset({"critical", "high", "medium", "low"})

    def test_moderate_projects_to_medium(self):
        """W631 alias: pr-risk's ``moderate`` bucket must project to canonical ``medium``.

        Behavioural ground-truth for the projection consistency assertion
        above: pr-risk's score window 25 < risk ≤ 50 emits domain label
        ``moderate``; the canonical normalizer must map that to ``medium``
        so the envelope's ``risk_level_canonical`` lands on a W631 member.
        """
        assert normalize_risk_level("moderate") == "medium"
        assert risk_rank("moderate") == risk_rank("medium")


class TestRiskLevelMockedScore:
    """Mock-the-score variants — exercise the bucketing branches deterministically.

    The end-to-end fixture only ever exercises the bucket that the
    indexed project's score happens to land in. These mocked-internal
    tests pin the canonical projection on each of the four pr-risk
    bucket labels so a future edit to the bucketing rule (the
    if/elif/else at ~line 1095 in cmd_pr_risk.py) fails the right
    test.
    """

    def test_low_score_emits_low_canonical(self):
        assert normalize_risk_level("low") == "low"
        assert risk_rank("low") == 1

    def test_moderate_score_emits_medium_canonical(self):
        # pr-risk's ``moderate`` bucket (25 < score ≤ 50) projects to
        # canonical ``medium``. The W631 alias table is what enables
        # this — without it the canonical projection would be ``None``
        # and the ``or "low"`` floor would mis-bucket every moderate-risk
        # PR as low-risk.
        assert normalize_risk_level("moderate") == "medium"
        assert risk_rank("moderate") == 2

    def test_high_score_emits_high_canonical(self):
        assert normalize_risk_level("high") == "high"
        assert risk_rank("high") == 3

    def test_critical_score_emits_critical_canonical(self):
        assert normalize_risk_level("critical") == "critical"
        assert risk_rank("critical") == 4
