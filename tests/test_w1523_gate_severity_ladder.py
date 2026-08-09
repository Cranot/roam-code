"""W1523 -- a gate rule declared `severity: critical` was silently demoted to `warning`.

The defect. ``load_gates_config`` compared the user's declared severity
against a LOCAL two-element frozenset, ``{"error", "warning"}``. ``critical``
-- the HIGHEST tier on this repo's canonical ladder, strictly above ``error``
(``severity_rank``: critical 5, error 4, high 4, warning 3) -- is not in that
set, so it fell through the membership test and was rewritten to ``warning``:
the LEAST severe bucket, the one that never gates. Measured on a scratch repo
whose ``.roam-gates.yml`` declared ``severity: critical``::

    roam coverage-gaps --config .roam-gates.yml --ci   -> rc 0
    (same file, only critical -> error)                -> rc 5

The user's most severe word disarmed the gate it was written to arm, and the
envelope then published ``"0 blocking gate violations with 2 advisory
warnings"`` and ``errors: 0``. Nothing on any channel said a severity had been
rewritten -- the three ``log_swallowed`` lineage signals in that function cover
pyyaml-missing / file-read / yaml-parse only.

The shape of the fix. The vocabulary is no longer local. It is
``roam.output._severity.severity_rank``, the repo's single source of truth for
severity ORDER, which ``cmd_adversarial`` already accepts in full. A rule
blocks iff its declared label ranks at or above ``error``.

W531 IS PRESERVED, and that is the load-bearing half. A label that is not on
the ladder at all (``catastrophic``, ``blocker``, a typo, a non-string) still
falls back to ``warning``: an unknown label must NEVER promote a finding into
a CI-failing rank. The change there is disclosure, not escalation -- the run
now carries ``state: "severity_unrecognised"`` and ``partial_success: true``,
the same idiom the ``no_gates`` / ``no_entries`` branches use, plus a fourth
lineage signal beside the existing three.
"""

from __future__ import annotations

import pytest

from roam.commands.gate_presets import coerce_gate_severity, load_gates_config
from roam.output._severity import severity_rank
from tests.conftest import git_init, index_in_process, invoke_cli, parse_json_output


def _write_config(proj, severity: str) -> str:
    cfg = proj / ".roam-gates.yml"
    cfg.write_text(
        "rules:\n"
        "  - name: payments-must-be-tested\n"
        "    description: Money-handling modules must have 3+ tests\n"
        '    include: ["src/**/models*.py", "src/**/service*.py"]\n'
        '    exclude: ["tests/**"]\n'
        "    min_tests: 3\n"
        f"    severity: {severity}\n",
        encoding="utf-8",
    )
    return str(cfg)


@pytest.fixture
def gates_project(tmp_path):
    """Project with two money-handling modules and no tests for either."""
    proj = tmp_path / "w1523_proj"
    proj.mkdir()
    (proj / ".gitignore").write_text(".roam/\n")
    src = proj / "src" / "app"
    src.mkdir(parents=True)
    (src / "models.py").write_text(
        'class Payment:\n    """Money."""\n\n    def __init__(self, amount):\n        self.amount = amount\n'
    )
    (src / "service.py").write_text(
        "from src.app.models import Payment\n\n\ndef charge(amount):\n    return Payment(amount)\n"
    )
    tests_dir = proj / "tests"
    tests_dir.mkdir()
    (tests_dir / "test_smoke.py").write_text("def test_smoke():\n    assert True\n")
    git_init(proj)
    index_in_process(proj)
    return proj


# ---------------------------------------------------------------------------
# The ladder itself -- unit level, no index required
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("label", ["critical", "error", "high", "CRITICAL", "  Error  "])
def test_at_or_above_error_blocks(label):
    """Every ladder label ranking >= error coerces to the blocking bucket."""
    bucket, unrecognised = coerce_gate_severity(label)
    assert bucket == "error"
    assert unrecognised is False


@pytest.mark.parametrize("label", ["warning", "medium", "low", "info"])
def test_below_error_stays_advisory(label):
    """Ladder labels below error stay advisory -- no escalation anywhere."""
    bucket, unrecognised = coerce_gate_severity(label)
    assert bucket == "warning"
    assert unrecognised is False


@pytest.mark.parametrize("label", ["catastrophic", "blocker", "erorr", "", None, 3, ["error"]])
def test_unknown_labels_never_promote(label):
    """W531: an unrecognised label must NOT reach a CI-failing rank.

    This is the must-not-fire boundary of the whole fix. `catastrophic` and
    `blocker` READ as more severe than `error` in English; both rank -1 on the
    ladder, so both stay advisory. Promoting on 'sounds severe' would turn a
    typo into a red build.
    """
    bucket, unrecognised = coerce_gate_severity(label)
    assert bucket == "warning"
    assert unrecognised is True


def test_blocking_threshold_is_derived_not_literal():
    """The cut is `>= rank(error)`, read from the ladder, not a hardcoded int."""
    assert severity_rank("critical") > severity_rank("error")
    assert severity_rank("high") == severity_rank("error")
    assert severity_rank("warning") < severity_rank("error")
    assert severity_rank("catastrophic") == -1


def test_declared_label_is_preserved_through_the_loader(tmp_path):
    """A coercion must be legible: the authored word survives on the rule."""
    cfg = tmp_path / "g.yml"
    cfg.write_text("rules:\n  - name: x\n    severity: critical\n", encoding="utf-8")
    (rule,) = load_gates_config(str(cfg))
    assert rule.severity == "error"
    assert rule.severity_declared == "critical"
    assert rule.severity_unrecognised is False


def test_w531_loader_fallback_still_warning(tmp_path):
    """The original W531 pin, restated at the loader: catastrophic -> warning."""
    cfg = tmp_path / "g.yml"
    cfg.write_text("rules:\n  - name: x\n    severity: catastrophic\n", encoding="utf-8")
    (rule,) = load_gates_config(str(cfg))
    assert rule.severity == "warning"
    assert rule.severity_declared == "catastrophic"
    assert rule.severity_unrecognised is True


def test_w706_broken_config_still_returns_empty(tmp_path):
    """The coercion must not introduce a raise into any fallback path."""
    bad = tmp_path / "bad.yml"
    bad.write_text("rules:\n  - name: x\n  bad-indent-here\n", encoding="utf-8")
    assert load_gates_config(str(bad)) == []
    assert load_gates_config(str(tmp_path / "nope.yml")) == []


# ---------------------------------------------------------------------------
# MUST FIRE -- the gate the user armed
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("label", ["critical", "error", "high"])
def test_ci_gate_refuses_on_blocking_severity(cli_runner, gates_project, label):
    """`--ci` refuses for every label the user could reasonably mean by "block"."""
    cfg = _write_config(gates_project, label)
    result = invoke_cli(cli_runner, ["coverage-gaps", "--config", cfg, "--ci"], cwd=gates_project)
    assert result.exit_code == 5, result.output


def test_critical_counts_as_an_error_in_the_envelope(cli_runner, gates_project):
    """The published counts must agree with the gate's own decision."""
    cfg = _write_config(gates_project, "critical")
    result = invoke_cli(cli_runner, ["coverage-gaps", "--config", cfg, "--json"], cwd=gates_project)
    data = parse_json_output(result)
    summary = data["summary"]
    assert summary["errors"] == 2
    assert summary["warnings"] == 0
    assert "blocking" in summary["verdict"]
    assert not summary["verdict"].startswith("0 blocking")
    violation = data["gate_violations"][0]
    assert violation["severity"] == "error"
    assert violation["severity_declared"] == "critical"


# ---------------------------------------------------------------------------
# MUST NOT FIRE -- proving this is a disclosure fix, not an outage
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("label", ["warning", "medium", "low", "info"])
def test_ci_gate_stays_green_on_advisory_severity(cli_runner, gates_project, label):
    """Advisory tiers never gate, in any mode. Unchanged by this commit."""
    cfg = _write_config(gates_project, label)
    result = invoke_cli(cli_runner, ["coverage-gaps", "--config", cfg, "--ci"], cwd=gates_project)
    assert result.exit_code == 0, result.output


@pytest.mark.parametrize("label", ["catastrophic", "blocker"])
def test_ci_gate_stays_green_on_unrecognised_severity(cli_runner, gates_project, label):
    """The W531 boundary, end to end: a word off the ladder does not gate."""
    cfg = _write_config(gates_project, label)
    result = invoke_cli(cli_runner, ["coverage-gaps", "--config", cfg, "--ci"], cwd=gates_project)
    assert result.exit_code == 0, result.output


def test_default_mode_never_gates_even_on_critical(cli_runner, gates_project):
    """Without `--ci` the command reports; it does not gate. The opt-in holds."""
    cfg = _write_config(gates_project, "critical")
    result = invoke_cli(cli_runner, ["coverage-gaps", "--config", cfg], cwd=gates_project)
    assert result.exit_code == 0, result.output


# ---------------------------------------------------------------------------
# Disclosure -- the fallback is now audible on both channels
# ---------------------------------------------------------------------------


def test_unrecognised_severity_is_disclosed_in_json(cli_runner, gates_project):
    cfg = _write_config(gates_project, "catastrophic")
    result = invoke_cli(cli_runner, ["coverage-gaps", "--config", cfg, "--json"], cwd=gates_project)
    summary = parse_json_output(result)["summary"]
    assert summary["partial_success"] is True
    assert summary["state"] == "severity_unrecognised"
    assert summary["severity_unrecognised_rules"] == ["payments-must-be-tested"]


def test_unrecognised_severity_is_disclosed_in_text(cli_runner, gates_project):
    cfg = _write_config(gates_project, "catastrophic")
    result = invoke_cli(cli_runner, ["coverage-gaps", "--config", cfg], cwd=gates_project)
    assert "unrecognised severity" in result.output
    assert "payments-must-be-tested" in result.output


@pytest.mark.parametrize("label", ["critical", "error", "warning", "info"])
def test_recognised_severity_never_claims_partial(cli_runner, gates_project, label):
    """A label roam DID understand must not degrade the run's own status."""
    cfg = _write_config(gates_project, label)
    result = invoke_cli(cli_runner, ["coverage-gaps", "--config", cfg, "--json"], cwd=gates_project)
    summary = parse_json_output(result)["summary"]
    assert summary["partial_success"] is False
    assert "state" not in summary
