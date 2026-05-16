"""Tests for the global ``roam --ci`` flag (W21.6).

The ``--ci`` flag is a semantic "I'm running in CI" lever: it flips the
defaults of per-command flags so a single invocation picks the
machine-friendly + gate-failing variant of each subcommand.

Coverage:
  1. Flag is recognized (appears in --help)
  2. --ci implies --leaks-only for over-fetch
  3. --ci implies --strict for pr-bundle emit
  4. --ci implies --strict for pr-bundle validate
  5. Explicit per-command flag overrides --ci's implication
  6. --ci passthrough to a non-CI-aware command works fine
  7. ROAM_CI=1 environment variable has the same effect as --ci

Per LAW 11 (user intent > inference), explicit subcommand flags ALWAYS
win over --ci's implications.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest
from click.testing import CliRunner

sys.path.insert(0, str(Path(__file__).parent))
from conftest import git_init, index_in_process, parse_json_output  # noqa: E402

# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def cli_runner():
    return CliRunner()


def _invoke(cli_runner, args, **kw):
    """Invoke the roam CLI directly with `catch_exceptions=False`."""
    from roam.cli import cli

    return cli_runner.invoke(cli, args, catch_exceptions=False, **kw)


# ---------------------------------------------------------------------------
# Over-fetch fixture: a Laravel-shaped project with all 3 endpoint states
#  (BARE, GUARDED_RELATION, UNGUARDED_RELATION) so --leaks-only's filter
#  has something to suppress.
# ---------------------------------------------------------------------------


def _seed_wide_employee_model(proj: Path) -> None:
    models = proj / "app" / "Models"
    models.mkdir(parents=True, exist_ok=True)
    (models / "Employee.php").write_text(
        "<?php\nnamespace App\\Models;\n\n"
        "use Illuminate\\Database\\Eloquent\\Model;\n\n"
        "class Employee extends Model {\n"
        "    protected $fillable = [\n"
        "        'first_name', 'last_name', 'email', 'phone', 'address',\n"
        "        'city', 'state', 'zip', 'country', 'date_of_birth',\n"
        "        'social_security', 'national_id', 'tax_id', 'bank_account',\n"
        "        'salary', 'bonus', 'department_id', 'manager_id', 'role',\n"
        "        'hire_date', 'termination_date', 'status', 'photo_url',\n"
        "    ];\n"
        "}\n"
    )


def _write_controller(proj: Path, name: str, body: str) -> None:
    controllers = proj / "app" / "Http" / "Controllers"
    controllers.mkdir(parents=True, exist_ok=True)
    (controllers / f"{name}.php").write_text(
        "<?php\nnamespace App\\Http\\Controllers;\n\n"
        "use App\\Models\\Employee;\n\n"
        f"class {name} extends Controller {{\n"
        "    public function index() {\n"
        f"        {body}\n"
        "    }\n"
        "}\n"
    )


@pytest.fixture
def three_state_project(tmp_path):
    """Project with one BARE, one GUARDED_RELATION, and one UNGUARDED_RELATION
    endpoint — enough for --leaks-only to filter the GUARDED row out."""
    proj = tmp_path / "three_state"
    proj.mkdir()
    (proj / ".gitignore").write_text(".roam/\n")
    _seed_wide_employee_model(proj)
    _write_controller(
        proj,
        "RawListController",  # BARE
        "return Employee::query()->paginate(20);",
    )
    _write_controller(
        proj,
        "GuardedController",  # GUARDED_RELATION
        "return Employee::with('manager:id,name')->select(['id'])->paginate(20);",
    )
    _write_controller(
        proj,
        "WorkCardController",  # UNGUARDED_RELATION
        "return Employee::with('manager')->select(['id'])->paginate(20);",
    )
    git_init(proj)
    index_in_process(proj)
    return proj


# ---------------------------------------------------------------------------
# pr-bundle fixture
# ---------------------------------------------------------------------------


@pytest.fixture
def bundle_project(tmp_path, monkeypatch):
    proj = tmp_path / "bundle_repo"
    proj.mkdir()
    (proj / ".gitignore").write_text(".roam/\n")
    (proj / "main.py").write_text("def hello():\n    return 'hi'\n")
    git_init(proj)
    subprocess.run(["git", "checkout", "-B", "test-branch"], cwd=proj, capture_output=True)
    monkeypatch.chdir(proj)
    return proj


# ---------------------------------------------------------------------------
# 1. --ci flag is recognized
# ---------------------------------------------------------------------------


def test_ci_mode_flag_recognized(cli_runner):
    """`roam --help` lists --ci and `roam --ci --help` runs cleanly."""
    result = _invoke(cli_runner, ["--help"])
    assert result.exit_code == 0, result.output
    assert "--ci" in result.output, "`--ci` flag missing from `roam --help`"

    # And `--ci --help` itself runs without complaint.
    result2 = _invoke(cli_runner, ["--ci", "--help"])
    assert result2.exit_code == 0, result2.output


# ---------------------------------------------------------------------------
# 2. --ci implies --leaks-only for over-fetch
# ---------------------------------------------------------------------------


def test_ci_implies_leaks_only_for_over_fetch(cli_runner, three_state_project, monkeypatch):
    """With --ci, over-fetch filters GUARDED_RELATION from findings list."""
    monkeypatch.chdir(three_state_project)
    result = _invoke(cli_runner, ["--ci", "--json", "over-fetch"])
    assert result.exit_code == 0, result.output

    raw = getattr(result, "stdout", None) or result.output
    data = json.loads(raw)
    # leaks_only should be recorded as True in summary
    assert data["summary"]["leaks_only"] is True, (
        f"`--ci` should imply leaks_only=True, got {data['summary'].get('leaks_only')!r}"
    )
    # And no GUARDED_RELATION should appear in the findings list
    eps = data.get("endpoint_findings", [])
    guarded = [e for e in eps if e.get("state") == "GUARDED_RELATION"]
    assert guarded == [], f"GUARDED_RELATION must be suppressed under --ci, found: {guarded}"
    # But summary counts STILL reflect the full classification
    assert data["summary"]["guarded_relation_count"] == 1


# ---------------------------------------------------------------------------
# 3. --ci implies --strict for pr-bundle emit
# ---------------------------------------------------------------------------


def test_ci_implies_strict_for_pr_bundle_emit(cli_runner, bundle_project):
    """Incomplete bundle + --ci pr-bundle emit exits 5 (CI gating)."""
    _invoke(cli_runner, ["pr-bundle", "init", "--intent", "Add retry"])
    # Intent only — no affected, no context-cmd, no verdict → incomplete.
    result = _invoke(
        cli_runner,
        ["--ci", "--json", "pr-bundle", "emit", "--no-auto-collect"],
    )
    assert result.exit_code == 5, (
        f"expected exit 5 under --ci on incomplete bundle, got {result.exit_code}: {result.output}"
    )
    # Envelope still echoed before the non-zero exit.
    raw = getattr(result, "stdout", None) or result.output
    data = json.loads(raw)
    assert data["summary"]["state"] == "incomplete"


# ---------------------------------------------------------------------------
# 4. --ci implies --strict for pr-bundle validate
# ---------------------------------------------------------------------------


def test_ci_implies_strict_for_pr_bundle_validate(cli_runner, bundle_project):
    """Incomplete bundle + --ci pr-bundle validate exits 5."""
    _invoke(cli_runner, ["pr-bundle", "init", "--intent", ""])
    result = _invoke(cli_runner, ["--ci", "--json", "pr-bundle", "validate"])
    assert result.exit_code == 5, (
        f"expected exit 5 under --ci on incomplete bundle, got {result.exit_code}: {result.output}"
    )


# ---------------------------------------------------------------------------
# 5. Explicit flag overrides --ci's implied default (LAW 11)
# ---------------------------------------------------------------------------


def test_explicit_no_leaks_only_overrides_ci(cli_runner, three_state_project, monkeypatch):
    """`--ci over-fetch --no-leaks-only` shows GUARDED_RELATION (explicit wins)."""
    monkeypatch.chdir(three_state_project)
    result = _invoke(cli_runner, ["--ci", "--json", "over-fetch", "--no-leaks-only"])
    assert result.exit_code == 0, result.output
    raw = getattr(result, "stdout", None) or result.output
    data = json.loads(raw)
    # Explicit --no-leaks-only must override --ci's implied True
    assert data["summary"]["leaks_only"] is False, (
        f"explicit --no-leaks-only must beat --ci, got leaks_only={data['summary'].get('leaks_only')!r}"
    )
    # And GUARDED_RELATION should be back in the findings list
    eps = data.get("endpoint_findings", [])
    guarded = [e for e in eps if e.get("state") == "GUARDED_RELATION"]
    assert guarded, "GUARDED_RELATION should appear when --no-leaks-only overrides --ci"


def test_explicit_no_strict_overrides_ci_for_pr_bundle_validate(cli_runner, bundle_project):
    """`--ci pr-bundle validate --no-strict` exits 0 on incomplete (explicit wins)."""
    _invoke(cli_runner, ["pr-bundle", "init", "--intent", ""])
    result = _invoke(
        cli_runner,
        ["--ci", "--json", "pr-bundle", "validate", "--no-strict"],
    )
    assert result.exit_code == 0, f"explicit --no-strict must beat --ci, got exit {result.exit_code}: {result.output}"


# ---------------------------------------------------------------------------
# 6. --ci passthrough to a non-CI-aware command
# ---------------------------------------------------------------------------


def test_ci_passthrough_to_non_ci_command(cli_runner, three_state_project, monkeypatch):
    """`--ci health` runs fine; no behavior change for non-CI-aware commands."""
    monkeypatch.chdir(three_state_project)
    result = _invoke(cli_runner, ["--ci", "--json", "health"])
    assert result.exit_code in (0, 5), f"`--ci health` should exit cleanly, got {result.exit_code}: {result.output}"
    # Health envelope is well-formed
    data = parse_json_output(result, command="health")
    assert "summary" in data
    assert "verdict" in data["summary"]


# ---------------------------------------------------------------------------
# 7. ROAM_CI=1 environment variable has the same effect as --ci
# ---------------------------------------------------------------------------


def test_roam_ci_env_var_implies_ci_mode(cli_runner, bundle_project, monkeypatch):
    """Setting ROAM_CI=1 in the environment behaves like passing --ci."""
    _invoke(cli_runner, ["pr-bundle", "init", "--intent", ""])
    monkeypatch.setenv("ROAM_CI", "1")
    # No explicit --ci flag — but ROAM_CI=1 should still trigger --strict.
    result = _invoke(cli_runner, ["--json", "pr-bundle", "validate"])
    assert result.exit_code == 5, (
        f"ROAM_CI=1 should imply --strict like --ci does, got exit {result.exit_code}: {result.output}"
    )


# ---------------------------------------------------------------------------
# 8. --ci implies --strict-resolved on pr-bundle emit/validate (W22.3)
# ---------------------------------------------------------------------------
#
# W22.1 made --strict-resolved an additive gate on top of --strict that
# also fails when unresolved ("ghost") symbols are present in the bundle.
# W22.2 made --ci imply --strict. W22.3 closes the gap: --ci ALSO implies
# --strict-resolved, so a CI run gates on completeness AND on the absence
# of ghost-symbol entries. LAW 11: explicit --no-strict-resolved wins.


def test_ci_implies_strict_resolved_for_pr_bundle_emit(cli_runner, bundle_project):
    """`--ci pr-bundle emit` records strict_resolved=True in the envelope."""
    _invoke(
        cli_runner,
        ["pr-bundle", "init", "--intent", "Add retry"],
    )
    result = _invoke(
        cli_runner,
        ["--ci", "--json", "pr-bundle", "emit", "--no-auto-collect"],
    )
    # Bundle is incomplete -> --strict fires exit 5 (already covered by
    # test 3). The new assertion is that strict_resolved is also recorded.
    assert result.exit_code == 5, (
        f"expected exit 5 under --ci on incomplete bundle, got {result.exit_code}: {result.output}"
    )
    raw = getattr(result, "stdout", None) or result.output
    data = json.loads(raw)
    assert data["summary"]["strict_resolved"] is True, (
        f"--ci should imply strict_resolved=True, got {data['summary'].get('strict_resolved')!r}"
    )


def test_ci_implies_strict_resolved_for_pr_bundle_validate(cli_runner, bundle_project):
    """`--ci pr-bundle validate` records strict_resolved=True in the envelope."""
    _invoke(cli_runner, ["pr-bundle", "init", "--intent", ""])
    result = _invoke(cli_runner, ["--ci", "--json", "pr-bundle", "validate"])
    assert result.exit_code == 5, (
        f"expected exit 5 under --ci on incomplete bundle, got {result.exit_code}: {result.output}"
    )
    raw = getattr(result, "stdout", None) or result.output
    data = json.loads(raw)
    assert data["summary"]["strict_resolved"] is True, (
        f"--ci should imply strict_resolved=True for validate, got {data['summary'].get('strict_resolved')!r}"
    )


def test_explicit_no_strict_resolved_overrides_ci_for_pr_bundle_emit(cli_runner, bundle_project):
    """`--ci pr-bundle emit --no-strict-resolved` records strict_resolved=False.

    Explicit user intent (LAW 11) MUST beat the --ci inference. The
    --strict gate may still fire on incomplete bundles, but
    strict_resolved itself must surface as False in the envelope.
    """
    _invoke(
        cli_runner,
        ["pr-bundle", "init", "--intent", "Add retry"],
    )
    result = _invoke(
        cli_runner,
        [
            "--ci",
            "--json",
            "pr-bundle",
            "emit",
            "--no-auto-collect",
            "--no-strict-resolved",
        ],
    )
    raw = getattr(result, "stdout", None) or result.output
    data = json.loads(raw)
    assert data["summary"]["strict_resolved"] is False, (
        f"explicit --no-strict-resolved must beat --ci, got {data['summary'].get('strict_resolved')!r}"
    )
