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
    # NOTE (2026-08, task #141): this SAFE rides the low bucket — the
    # indexer records no file_edges row for the barrel's relative import
    # in this src-layout fixture, so api.py counts as not-imported and
    # internal_helper classifies SAFE 90 (a band the THEME 6 measurement
    # did not indict). The file-imported SAFE 80 branch itself was
    # demoted to REVIEW; see test_dead_file_imported_demotion.py.
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


# ---------------------------------------------------------------------------
# W-PSU -- unreadable public-surface evidence must resolve to UNKNOWN, never to
# "not public". A barrel or manifest that does not parse produces exactly the
# same "no re-export reason" as one that genuinely re-exports nothing; without
# a guard the SAFE bucket publishes a "graph proof" over evidence never read.
# ---------------------------------------------------------------------------


def _warnings(data: dict) -> list[str]:
    return list(data.get("warnings_out") or data.get("summary", {}).get("warnings_out") or [])


def test_unparseable_barrel_downgrades_safe_to_review_and_discloses(project_factory, cli_runner):
    project = project_factory(
        {
            "src/acme/__init__.py": (
                'from .api import public_api\n\n__all__ = ["public_api"]\n\ndef broken(:\n    pass\n'
            ),
            "src/acme/api.py": 'def public_api():\n    return "public"\n',
        }
    )

    result = invoke_cli(cli_runner, ["--detail", "dead", "--all"], cwd=project, json_mode=True)
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    findings = _dead_results(data)

    assert "public_api" in findings, json.dumps(data, indent=2)
    # Pre-fix this was SAFE/high: the re-export was unread, not absent.
    assert findings["public_api"]["action"] == "REVIEW", json.dumps(data, indent=2)
    assert "UNKNOWN" in findings["public_api"]["reason"]
    assert data["summary"]["safe"] == 0
    assert data["summary"]["partial_success"] is True
    assert any("dead_public_surface_unreadable" in w for w in _warnings(data)), _warnings(data)


def test_unparseable_pyproject_downgrades_declared_entry_point(project_factory, cli_runner):
    project = project_factory(
        {
            "pyproject.toml": (
                '[project]\nname = "acme"\nversion = "1.0.0"\n\n'
                '[project.scripts]\nacme = "acme.cli:main"\nthis is not toml ===\n'
            ),
            "src/acme/cli.py": "def main():\n    return 0\n",
        }
    )

    result = invoke_cli(cli_runner, ["--detail", "dead", "--all"], cwd=project, json_mode=True)
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    findings = _dead_results(data)

    assert "main" in findings, json.dumps(data, indent=2)
    assert findings["main"]["action"] == "REVIEW", json.dumps(data, indent=2)
    assert data["summary"]["partial_success"] is True
    assert any("pyproject.toml" in w for w in _warnings(data)), _warnings(data)


def test_unreadable_barrel_radius_stops_at_its_own_package(project_factory, cli_runner):
    """Negative control: the guard must not blanket-downgrade everything.

    A fix that simply routes every candidate to REVIEW (or that flips
    ``partial_success`` on every run) fails this test. Only ``alpha``'s subtree
    is unknowable; ``beta``'s internal helper still has readable evidence and
    must stay SAFE.
    """
    project = project_factory(
        {
            "src/alpha/__init__.py": "from .api import alpha_api\n\ndef broken(:\n    pass\n",
            "src/alpha/api.py": "def alpha_api():\n    return 1\n",
            "src/beta/__init__.py": "",
            "src/beta/api.py": "def beta_internal():\n    return 2\n",
        }
    )

    result = invoke_cli(cli_runner, ["--detail", "dead", "--all"], cwd=project, json_mode=True)
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    findings = _dead_results(data)

    assert findings["alpha_api"]["action"] == "REVIEW", json.dumps(data, indent=2)
    assert findings["beta_internal"]["action"] == "SAFE", json.dumps(data, indent=2)
    assert data["summary"]["safe"] >= 1


def test_readable_evidence_keeps_a_clean_partial_success(project_factory, cli_runner):
    """Negative control: healthy input must not acquire the disclosure."""
    project = project_factory(
        {
            "src/acme/__init__.py": ('from .api import public_api\n\n__all__ = ["public_api"]\n'),
            "src/acme/api.py": ("def public_api():\n    return 1\n\n\ndef internal_helper():\n    return 2\n"),
        }
    )

    result = invoke_cli(cli_runner, ["--detail", "dead", "--all"], cwd=project, json_mode=True)
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)

    assert not [w for w in _warnings(data) if "public_surface_unreadable" in w]
    assert data["summary"]["partial_success"] is False
    assert _dead_results(data)["internal_helper"]["action"] == "SAFE"
