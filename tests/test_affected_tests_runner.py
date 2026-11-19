"""Affected-test suggestions use repository evidence and real test cases."""

import json
import os

import pytest

from tests.conftest import git_init, index_in_process, invoke_cli


@pytest.fixture
def mixed_tests(tmp_path, monkeypatch):
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", os.devnull)
    monkeypatch.setenv("GIT_CONFIG_COUNT", "0")
    for path, source in {
        "storage.ts": "export function useStorage() {}\n",
        "storage.test.ts": (
            "import { useStorage } from './storage'\nconst KEY = 'key'\nit('string', () => {\n  useStorage()\n})\n"
        ),
        "main.py": "def transform(): pass\n",
        "test_main.py": ("from main import transform\ndef test_transform():\n    transform()\n"),
        "package.json": json.dumps(
            {
                "scripts": {"test": "nr test:unit", "test:unit": "vitest"},
                "devDependencies": {"vitest": "^3", "playwright": "^1"},
            }
        ),
        "pytest.ini": "[pytest]\n",
    }.items():
        (tmp_path / path).write_text(source, encoding="utf-8")
    git_init(tmp_path)
    output, status = index_in_process(tmp_path)
    assert status == 0, output
    monkeypatch.chdir(tmp_path)
    return tmp_path


def test_typescript_uses_vitest_and_python_uses_pytest(mixed_tests, cli_runner):
    js = invoke_cli(cli_runner, ["affected-tests", "useStorage"])
    py = invoke_cli(cli_runner, ["affected-tests", "transform"])
    assert js.exit_code == py.exit_code == 0, (js.output, py.output)
    assert "test_main.py::test_transform" in py.output
    assert "Run: pytest test_main.py" in py.output
    assert "Run: npx vitest run storage.test.ts" in js.output


def test_typescript_constant_is_not_a_named_test(mixed_tests, cli_runner):
    result = invoke_cli(cli_runner, ["--json", "affected-tests", "useStorage"])
    assert result.exit_code == 0, result.output
    tests = json.loads(result.output)["tests"]
    assert [(test["file"], test["symbol"], test["kind"]) for test in tests] == [
        ("storage.test.ts", None, "DIRECT"),
    ]


def test_unconfigured_typescript_does_not_guess_pytest(mixed_tests, cli_runner):
    (mixed_tests / "package.json").write_text('{"scripts": {"test": "echo jest"}}', encoding="utf-8")
    result = invoke_cli(cli_runner, ["affected-tests", "useStorage", "--command"])
    assert result.exit_code == 0, result.output
    assert result.output.strip() == "# Unknown test runner for: storage.test.ts"


def test_preflight_suggests_vitest_for_typescript(mixed_tests, cli_runner):
    result = invoke_cli(cli_runner, ["preflight", "useStorage"])
    assert result.exit_code == 0, result.output
    assert "Suggested tests: npx vitest run storage.test.ts" in result.output


@pytest.mark.parametrize(
    ("package", "expected"),
    [
        (
            {"scripts": {"test": "cross-env NODE_OPTIONS='--max-old-space-size=4096' npx jest"}},
            "npx jest --runTestsByPath storage.test.ts",
        ),
        ({"devDependencies": {"mocha": "^11"}}, "npx mocha storage.test.ts"),
        ({"scripts": {"test:e2e": "echo ready && playwright test"}}, r"npx playwright test 'storage\.test\.ts$'"),
        ({"scripts": {"test": 'echo ";" jest'}}, "# Unknown test runner for: storage.test.ts"),
        ({"scripts": {"test": "echo ready # ; jest"}}, "# Unknown test runner for: storage.test.ts"),
        ({"scripts": {"test": 'echo " && jest'}}, "# Unknown test runner for: storage.test.ts"),
        ({"scripts": {"test": "vitest && jest"}}, "# Unknown test runner for: storage.test.ts"),
    ],
)
def test_script_and_dependency_evidence(mixed_tests, cli_runner, package, expected):
    (mixed_tests / "package.json").write_text(json.dumps(package), encoding="utf-8")
    result = invoke_cli(cli_runner, ["affected-tests", "useStorage", "--command"])
    assert result.exit_code == 0, result.output
    assert result.output.strip() == expected


def test_diff_suggests_the_repository_runner(mixed_tests, cli_runner):
    with (mixed_tests / "storage.ts").open("a", encoding="utf-8") as source:
        source.write("export const changed = 1\n")
    text = invoke_cli(cli_runner, ["diff", "--tests"])
    assert text.exit_code == 0, text.output
    assert "npx vitest run storage.test.ts" in text.output
    assert "pytest storage.test.ts" not in text.output
    data = json.loads(invoke_cli(cli_runner, ["--json", "diff", "--tests"]).output)
    tests = data["affected_tests"]
    assert [entry["runner"] for entry in tests["test_commands"]] == ["vitest"]
    assert tests["pytest_command"] == ""
    assert tests["unknown_runner_files"] == []


def test_plan_suggests_the_repository_runner(mixed_tests, cli_runner):
    text = invoke_cli(cli_runner, ["plan", "--symbol", "useStorage"])
    assert text.exit_code == 0, text.output
    assert "npx vitest run storage.test.ts" in text.output
    assert "pytest storage.test.ts" not in text.output
    data = json.loads(invoke_cli(cli_runner, ["--json", "plan", "--symbol", "useStorage"]).output)
    tests = data["tests"]
    assert [entry["runner"] for entry in tests["test_commands"]] == ["vitest"]
    assert tests["pytest_command"] == ""
    python = json.loads(invoke_cli(cli_runner, ["--json", "plan", "--symbol", "transform"]).output)
    assert python["tests"]["pytest_command"] == "pytest test_main.py"
