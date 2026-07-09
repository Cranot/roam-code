"""Regression tests for externally consumed package exports in ``roam dead``."""

from __future__ import annotations

import json

from tests.conftest import invoke_cli


def _dead_results(data: dict) -> dict[str, dict]:
    results = {}
    for bucket in ("high_confidence", "low_confidence"):
        for finding in data.get(bucket, []):
            value = finding.get("value", finding)
            results[value["name"]] = value
    return results


def test_public_reexport_is_review_but_internal_helper_stays_safe(project_factory, cli_runner):
    project = project_factory(
        {
            "src/acme/__init__.py": ('from .api import public_api\n\n__all__ = ["public_api"]\n'),
            "src/acme/api.py": (
                'def public_api():\n    return "public"\n\ndef internal_helper():\n    return "internal"\n'
            ),
        }
    )

    result = invoke_cli(
        cli_runner,
        ["--detail", "dead", "--all"],
        cwd=project,
        json_mode=True,
    )
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    findings = _dead_results(data)

    assert "public_api" in findings, json.dumps(data, indent=2)
    assert findings["public_api"]["action"] == "REVIEW"
    assert "external-facing" in findings["public_api"]["reason"]
    assert findings["internal_helper"]["action"] == "SAFE"


def test_dunder_all_and_declared_entries_are_external_facing(project_factory, cli_runner):
    project = project_factory(
        {
            "pyproject.toml": (
                '[project]\nname = "acme"\nversion = "1.0.0"\n\n[project.scripts]\nacme = "acme.cli:main"\n'
            ),
            "src/acme/api.py": ('__all__ = ["documented_api"]\n\ndef documented_api():\n    return "public"\n'),
            "src/acme/cli.py": "def main():\n    return 0\n",
        }
    )

    result = invoke_cli(
        cli_runner,
        ["--detail", "dead", "--all"],
        cwd=project,
        json_mode=True,
    )
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    findings = _dead_results(data)

    assert "documented_api" in findings, data
    assert findings["documented_api"]["action"] == "REVIEW"
    assert findings["main"]["action"] == "REVIEW"
    assert all("external-facing" in findings[name]["reason"] for name in ("documented_api", "main"))


def test_package_json_entry_export_is_external_facing(project_factory, cli_runner):
    project = project_factory(
        {
            "package.json": json.dumps({"name": "acme", "exports": "./src/index.ts"}),
            "src/index.ts": ("export function publicApi() { return 1; }\nfunction internalHelper() { return 2; }\n"),
        }
    )

    result = invoke_cli(
        cli_runner,
        ["--detail", "dead", "--all"],
        cwd=project,
        json_mode=True,
    )
    assert result.exit_code == 0, result.output
    findings = _dead_results(json.loads(result.output))

    assert findings["publicApi"]["action"] == "REVIEW"
    assert "external-facing" in findings["publicApi"]["reason"]
    assert "internalHelper" not in findings
