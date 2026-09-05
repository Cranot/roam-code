.PHONY: install dev test test-smoke test-core lint format hygiene hygiene-strict hygiene-debt hygiene-debt-init todo-guard doctor-env doctor-env-strict command-audit quality-baseline quality-strict build publish clean

install:
	pip install -e .

dev:
	pip install -e ".[dev]"

test:
	pytest tests/

test-smoke:
	pytest -q tests/test_exit_codes.py tests/test_health_gate.py tests/test_surface_counts.py tests/test_competitor_site_data.py --maxfail=1

test-core:
	pytest -q tests/test_basic.py tests/test_exit_codes.py tests/test_health_gate.py tests/test_runtime.py tests/test_rules.py tests/test_surface_counts.py tests/test_competitor_site_data.py --maxfail=3

lint:
	ruff check --no-cache src/ tests/

format:
	ruff format --no-cache src/ tests/

hygiene:
	python dev/repo_hygiene.py --focus-path src --focus-path tests --focus-path dev --focus-path pyproject.toml --focus-path Makefile

hygiene-strict:
	python dev/repo_hygiene.py --focus-path src --focus-path tests --focus-path dev --focus-path pyproject.toml --focus-path Makefile --threshold-scope focus --max-untracked 50 --max-unstaged 30 --max-staged 30 --max-conflicts 0

hygiene-debt-init:
	python dev/repo_hygiene.py --debt-baseline reports/hygiene_debt_baseline.json --write-debt-baseline --show 0

hygiene-debt:
	python dev/repo_hygiene.py --debt-baseline reports/hygiene_debt_baseline.json --require-debt-baseline --max-new-untracked 0 --max-new-unstaged 0 --max-new-staged 0 --max-conflicts 0 --show 0

todo-guard:
	python dev/todo_guard.py

doctor-env:
	python dev/env_doctor.py --no-require-venv

doctor-env-strict:
	python dev/env_doctor.py --require-venv

command-audit:
	python dev/command_audit.py --output reports/command_audit_latest.md --max-output-lines 25

quality-baseline: hygiene-strict hygiene-debt todo-guard doctor-env lint test-smoke

quality-strict: hygiene-strict hygiene-debt todo-guard doctor-env-strict lint test-core

build: clean
	python -m build

verify-build: build
	# PEP 621 / PEP 639 metadata sanity. Twine catches missing classifiers,
	# malformed long-description, and license-expression issues that would
	# otherwise show up as "no license" on the published PyPI page.
	twine check --strict dist/*
	# Confirm License-Expression is actually in the wheel METADATA. Pre-12.50
	# wheels shipped without it because the build ran on setuptools < 77.
	@unzip -p dist/roam_code-*.whl '*/METADATA' 2>/dev/null \
		| grep -q '^License-Expression: Apache-2.0$$' \
		|| (echo "ERROR: License-Expression missing from wheel METADATA — needs setuptools >= 77" && exit 1)

# Production publish path. Recommended path is GitHub Actions / OIDC
# (publish.yml triggered by a v* tag); this target is the local
# fallback / dry-run. Refuses to upload without quality-strict + a
# build that passes twine + license-metadata checks.
publish: quality-strict verify-build
	twine upload dist/*

# roam-code.com goes out by hand on purpose (see CONTRIBUTING.md "Deploys"):
# a human says when the site ships. These targets keep the step from being
# lost: site-check names the drift the moment the live site falls behind the
# declared version (measured 2026-08: a release sat undeployed for a month
# because the command lived only in a doc), site-deploy is the one command.
site-check:
	@declared=$$(python -c "import tomllib;print(tomllib.load(open('pyproject.toml','rb'))['project']['version'])"); \
	live=$$(curl -sL https://roam-code.com/changelog | grep -o '[0-9]\+\.[0-9]\+\.[0-9]\+' | head -1); \
	echo "declared=$$declared live=$$live"; \
	[ "$$declared" = "$$live" ] || { echo "DRIFT: live roam-code.com is behind -- run 'make site-deploy'"; exit 1; }

site-deploy:
	@set -eu; \
	site_status=$$(git status --porcelain=v1 --untracked-files=all); \
	[ -z "$$site_status" ] || { echo "Commit or preserve working changes before deploying the production site."; exit 1; }; \
	site_sha=$$(git rev-parse --verify HEAD); \
	npx wrangler pages deploy templates/distribution/landing-page --project-name roam-code --branch main --commit-dirty=false --commit-hash="$$site_sha"

clean:
	rm -rf build/ dist/ *.egg-info src/*.egg-info
