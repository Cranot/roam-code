"""Regression tests from real-world Roam feedback sessions."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from conftest import invoke_cli


def test_dynamic_import_then_member_is_a_consumer(project_factory, cli_runner, monkeypatch):
    proj = project_factory(
        {
            "src/utils/documents.ts": (
                "export function downloadKiniseisDocumentStatusPdf(id: string) {\n  return `/docs/${id}.pdf`\n}\n"
            ),
            "src/views/KiniseisView.ts": (
                "export function handleDownload(id: string) {\n"
                "  return import('@/utils/documents')\n"
                "    .then(m => m.downloadKiniseisDocumentStatusPdf(id))\n"
                "}\n"
            ),
        }
    )
    monkeypatch.chdir(proj)

    result = invoke_cli(cli_runner, ["uses", "downloadKiniseisDocumentStatusPdf"], cwd=proj, json_mode=True)
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert data["summary"]["production_consumers"] >= 1


def test_dynamic_await_import_member_is_a_consumer(project_factory, cli_runner, monkeypatch):
    proj = project_factory(
        {
            "src/utils/documents.ts": (
                "export function downloadKiniseisDocumentStatusPdf(id: string) {\n  return `/docs/${id}.pdf`\n}\n"
            ),
            "src/views/KiniseisView.ts": (
                "export async function handleDownload(id: string) {\n"
                "  const mod = await import('@/utils/documents')\n"
                "  return mod.downloadKiniseisDocumentStatusPdf(id)\n"
                "}\n"
            ),
        }
    )
    monkeypatch.chdir(proj)

    result = invoke_cli(cli_runner, ["uses", "downloadKiniseisDocumentStatusPdf"], cwd=proj, json_mode=True)
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert data["summary"]["production_consumers"] >= 1


def test_dead_reports_test_only_consumers_separately(project_factory, cli_runner, monkeypatch):
    proj = project_factory(
        {
            "src/utils/case.ts": (
                "export function isSnakeCase(value: string) {\n  return /^[a-z]+(_[a-z]+)*$/.test(value)\n}\n"
            ),
            "tests/case.spec.ts": (
                "import { isSnakeCase } from '../src/utils/case'\n"
                "test('snake case', () => {\n"
                "  expect(isSnakeCase('hello_world')).toBe(true)\n"
                "})\n"
            ),
        }
    )
    monkeypatch.chdir(proj)

    result = invoke_cli(cli_runner, ["--detail", "dead"], cwd=proj, json_mode=True)
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    items = data.get("high_confidence", []) + data.get("low_confidence", [])
    target = next(item for item in items if item["name"] == "isSnakeCase")
    assert target["tested"] is True
    assert target["test_consumers"] >= 1
    assert target["production_consumers"] == 0
    assert target["action"] == "REVIEW"


def test_uses_splits_test_consumers_from_production(project_factory, cli_runner, monkeypatch):
    proj = project_factory(
        {
            "src/utils/case.ts": "export function isCamelCase(value: string) { return /^[a-z][a-zA-Z]*$/.test(value) }\n",
            "tests/case.spec.ts": (
                "import { isCamelCase } from '../src/utils/case'\n"
                "test('camel case', () => expect(isCamelCase('helloWorld')).toBe(true))\n"
            ),
        }
    )
    monkeypatch.chdir(proj)

    result = invoke_cli(cli_runner, ["uses", "isCamelCase"], cwd=proj, json_mode=True)
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert data["summary"]["production_consumers"] == 0
    assert data["summary"]["test_consumers"] >= 1
    assert data["summary"]["tested"] is True


def test_global_compact_option_is_accepted_after_command(cli_runner, indexed_project, monkeypatch):
    from roam.cli import cli

    monkeypatch.chdir(indexed_project)
    old_cwd = os.getcwd()
    try:
        os.chdir(indexed_project)
        result = cli_runner.invoke(cli, ["health", "--compact"], catch_exceptions=False)
    finally:
        os.chdir(old_cwd)

    assert result.exit_code == 0, result.output
