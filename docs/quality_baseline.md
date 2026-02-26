# Quality Baseline

This baseline turns the identified weaknesses into repeatable checks.

On Windows shells where `make` is unavailable, run the equivalent `python` / `pytest` commands directly.
For a single Windows command, run: `powershell -ExecutionPolicy Bypass -File dev/quality_baseline.ps1`

## 1) Working tree hygiene
- Focused view (agent coding paths): `python dev/repo_hygiene.py --focus-path src --focus-path tests --focus-path dev --focus-path pyproject.toml --focus-path Makefile`
- Strict focused thresholds: `python dev/repo_hygiene.py --focus-path src --focus-path tests --focus-path dev --focus-path pyproject.toml --focus-path Makefile --threshold-scope focus --max-untracked 50 --max-unstaged 30 --max-staged 30 --max-conflicts 0`
- Initialize global debt baseline (run once, or after intentional bulk cleanup/addition): `make hygiene-debt-init`
- Enforce no new global debt vs baseline: `make hygiene-debt`
- Baseline file: `reports/hygiene_debt_baseline.json`

## 2) Command robustness
- `python dev/command_audit.py --output reports/command_audit_latest.md --max-output-lines 25`
- Produces an agent-oriented report with:
- execution failures vs diagnostic findings
- command durations
- focused/global hygiene split
- prioritized next actions

## 3) Test feedback speed
- Fast loop (high-signal, low-latency): `make test-smoke`
- Broader local check: `make test-core`
- Full suite: `make test`

## 4) Dependency consistency
- Default (project-relevant conflicts only): `python dev/env_doctor.py --no-require-venv`
- Strict global: `python dev/env_doctor.py --require-venv --strict-global`

## 5) TODO/FIXME/HACK governance
- `python dev/todo_guard.py`
- Required format: `# TODO(owner,YYYY-MM-DD): description`

## 6) Logging consistency
- Ruff now enables `T20` to detect `print` usage in runtime code.
- Test and dev script folders are exempted to keep fixture/script output flexible.

## One-command baseline
- `make quality-baseline`
- Strict gate (includes venv requirement + broader tests): `make quality-strict`
- Windows script: `powershell -ExecutionPolicy Bypass -File dev/quality_baseline.ps1`
- Windows strict script: `powershell -ExecutionPolicy Bypass -File dev/quality_baseline.ps1 -Strict`
- Windows baseline bootstrap: `powershell -ExecutionPolicy Bypass -File dev/quality_baseline.ps1 -InitDebtBaseline`
