param(
    [switch]$Strict,
    [switch]$InitDebtBaseline
)

$ErrorActionPreference = "Stop"

$focusArgs = @(
    "--focus-path", "src",
    "--focus-path", "tests",
    "--focus-path", "dev",
    "--focus-path", "pyproject.toml",
    "--focus-path", "Makefile",
    "--threshold-scope", "focus",
    "--max-untracked", "50",
    "--max-unstaged", "30",
    "--max-staged", "30",
    "--max-conflicts", "0"
)
$debtBaselinePath = "reports/hygiene_debt_baseline.json"
$debtArgs = @(
    "--debt-baseline", $debtBaselinePath,
    "--require-debt-baseline",
    "--max-new-untracked", "0",
    "--max-new-unstaged", "0",
    "--max-new-staged", "0",
    "--max-conflicts", "0",
    "--show", "0"
)

function Assert-Step {
    param(
        [string]$Label
    )
    if ($LASTEXITCODE -ne 0) {
        throw "Step failed: $Label (exit code $LASTEXITCODE)"
    }
}

python dev/repo_hygiene.py @focusArgs
Assert-Step "repo_hygiene"

if ($InitDebtBaseline) {
    python dev/repo_hygiene.py --debt-baseline $debtBaselinePath --write-debt-baseline --show 0
    Assert-Step "repo_hygiene_debt_init"
}

python dev/repo_hygiene.py @debtArgs
Assert-Step "repo_hygiene_debt"

python dev/todo_guard.py
Assert-Step "todo_guard"

if ($Strict) {
    python dev/env_doctor.py --require-venv --strict-global
    Assert-Step "env_doctor_strict"
} else {
    python dev/env_doctor.py --no-require-venv
    Assert-Step "env_doctor"
}

ruff check --no-cache src tests --output-format concise
Assert-Step "ruff_check"

if ($Strict) {
    pytest -q tests/test_basic.py tests/test_exit_codes.py tests/test_health_gate.py tests/test_runtime.py tests/test_rules.py tests/test_surface_counts.py tests/test_competitor_site_data.py --maxfail=3
    Assert-Step "pytest_core"
} else {
    pytest -q tests/test_exit_codes.py tests/test_health_gate.py tests/test_surface_counts.py tests/test_competitor_site_data.py --maxfail=1
    Assert-Step "pytest_smoke"
}

python dev/command_audit.py --output reports/command_audit_latest.md --max-output-lines 20 --fail-on-error --fail-on-finding
Assert-Step "command_audit"
