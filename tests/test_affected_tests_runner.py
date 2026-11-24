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
    ("scripts", "config", "contents", "runner", "command"),
    [
        (
            {"test": "vitest"},
            "vitest.config.ts",
            "import { configDefaults, defineConfig } from 'vitest/config';\n"
            "export default defineConfig({test: {exclude: [...configDefaults.exclude, 'e2e/**']}});",
            "vitest",
            "npx vitest run storage.test.ts",
        ),
        (
            {"test:unit": "vitest", "test:e2e": "playwright test"},
            None,
            None,
            "vitest",
            "npx vitest run storage.test.ts",
        ),
        (
            {},
            "jest.config.js",
            "module.exports = {testMatch: ['**/__tests__/**/*.?([mc])[jt]s?(x)', "
            "'**/?(*.)+(spec|test).?([mc])[jt]s?(x)']};",
            "jest",
            "npx jest --runTestsByPath storage.test.ts",
        ),
    ],
    ids=["vitest-default-excludes", "unit-and-e2e-scripts", "jest-default-testmatch"],
)
def test_preflight_suggests_runner_for_common_js_layouts(
    mixed_tests, cli_runner, scripts, config, contents, runner, command
):
    from roam.output.test_commands import affected_test_commands

    (mixed_tests / "package.json").write_text(json.dumps({"scripts": scripts}), encoding="utf-8")
    if config:
        (mixed_tests / config).write_text(contents, encoding="utf-8")

    result = invoke_cli(cli_runner, ["preflight", "useStorage"])
    assert result.exit_code == 0, result.output
    assert f"Suggested tests: {command}" in result.output

    if runner == "jest":
        files = ["__tests__/nested/helper.cts", "spec.test.mjs", "bare.test.jsx", "src/helper.ts"]
        commands, unknown = affected_test_commands(mixed_tests, files)
        assert commands[0]["files"] == files[:-1]
        assert unknown == files[-1:]
    else:
        path = "e2e/storage.test.ts"
        commands, unknown = affected_test_commands(mixed_tests, [path])
        if config:
            assert (commands, unknown) == ([], [path])
        else:
            assert commands[0]["runner"] == "playwright"
            assert unknown == []


def test_preflight_keeps_legacy_command_for_non_pytest_runner(mixed_tests, cli_runner):
    result = invoke_cli(cli_runner, ["--json", "preflight", "useStorage"])
    assert result.exit_code == 0, result.output
    tests = json.loads(result.stdout)["tests"]
    assert tests["pytest_command"] == "npx vitest run storage.test.ts"
    assert tests["test_commands"][0]["command"] == tests["pytest_command"]


def test_attest_reports_signed_command_and_runner_metadata(mixed_tests, cli_runner):
    from hashlib import sha256

    source = mixed_tests / "main.py"
    source.write_text("def transform(): return 1\n", encoding="utf-8")
    result = invoke_cli(cli_runner, ["--json", "attest", "--sign"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    evidence = payload["evidence"]
    tests = evidence["tests"]
    assert tests["command"] == "pytest test_main.py"
    assert tests["test_run"]["test_commands"][0]["command"] == tests["command"]
    assert (
        payload["attestation"]["content_hash"]
        == "sha256:" + sha256(json.dumps(evidence, sort_keys=True, default=str).encode()).hexdigest()
    )

    result = invoke_cli(cli_runner, ["attest", "--format", "markdown"])
    assert result.exit_code == 0, result.output
    assert "- `pytest test_main.py`" in result.stdout
    assert "test_commands" not in result.stdout


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
    # Contract change on Windows: command mode used to print the bare command.
    # A POSIX shell comment now identifies the required shell and keeps the command copyable.
    if os.name == "nt" and not expected.startswith("#"):
        expected = "# Run in a POSIX shell\n" + expected
    assert "\n".join(result.output.splitlines()) == expected


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


@pytest.mark.parametrize(
    ("manifest", "metadata", "files", "runner", "command"),
    [
        (
            "go.mod",
            "module example.test/repo",
            ["app_test.go", "sub/api_test.go", "sub/db_test.go"],
            "go",
            "go test . ./sub",
        ),
        ("Cargo.toml", '[package]\nname = "app"\nversion = "0.1.0"', ["tests/app.rs"], "cargo", "cargo test"),
        ("Gemfile", 'gem "rspec"', ["spec/app_spec.rb"], "rspec", "bundle exec rspec spec/app_spec.rb"),
        (
            "web/package.json",
            '{"scripts":{"test":"vitest"}}',
            ["web/a b.test.ts"],
            "vitest",
            "(cd ./web && npx vitest run 'a b.test.ts')",
        ),
        (
            "pyproject.toml",
            '[project.optional-dependencies]\ntest = ["pytest>=8"]',
            ["tests/test_app.py"],
            "pytest",
            "pytest tests/test_app.py",
        ),
    ],
)
def test_runner_command_controls(tmp_path, manifest, metadata, files, runner, command):
    from roam.output.test_commands import affected_test_commands

    config = tmp_path / manifest
    config.parent.mkdir(parents=True, exist_ok=True)
    config.write_text(metadata, encoding="utf-8")
    commands, unknown = affected_test_commands(tmp_path, files)
    assert unknown == []
    assert commands == [{"runner": runner, "command": command, "shell": "posix", "files": files}]


@pytest.mark.parametrize(
    ("evidence", "contents"),
    [
        ("setup.cfg", "[tool:pytest]\n"),
        ("tox.ini", "[pytest]\n"),
        ("tests/conftest.py", ""),
        ("requirements-dev.txt", "# development\nPyTest >= 8\n"),
        ("requirements/test.txt", "pytest[extra]>=8\n"),
        ("requirements-dev.in", "pytest>=8\n"),
        ("pyproject.toml", '[tool.poetry.group.dev.dependencies]\npytest = "^8"\n'),
        ("pyproject.toml", '[tool.poetry.dev-dependencies]\npytest = "^8"\n'),
        ("pyproject.toml", '[tool.hatch.envs.test]\ndependencies = ["pytest>=8"]\n'),
    ],
)
def test_pytest_evidence_from_other_configurations(tmp_path, evidence, contents):
    from roam.output.test_commands import test_run_fields

    config = tmp_path / evidence
    config.parent.mkdir(parents=True, exist_ok=True)
    config.write_text(contents, encoding="utf-8")
    fields = test_run_fields(tmp_path, ["tests/test_app.py"])
    assert fields["pytest_command"] == "pytest tests/test_app.py"
    assert fields["unknown_runner_files"] == []


@pytest.mark.parametrize("manifest", ["gemfile", "gemspec", "standalone-gemspec"])
def test_rspec_rails_and_gemspec_dependencies(tmp_path, manifest):
    from roam.output.test_commands import affected_test_commands

    if manifest == "gemfile":
        (tmp_path / "Gemfile").write_text('gem "rspec-rails"\n', encoding="utf-8")
    else:
        if manifest == "gemspec":
            (tmp_path / "Gemfile").write_text("gemspec\n", encoding="utf-8")
        (tmp_path / "app.gemspec").write_text(
            'Gem::Specification.new do |spec|\n  spec.add_development_dependency "rspec", "~> 3"\nend\n',
            encoding="utf-8",
        )
    commands, unknown = affected_test_commands(tmp_path, ["spec/app_spec.rb"])
    assert unknown == []
    prefix = "rspec" if manifest == "standalone-gemspec" else "bundle exec rspec"
    assert commands[0]["runner"] == "rspec"
    assert commands[0]["command"] == f"{prefix} spec/app_spec.rb"


@pytest.mark.parametrize("evidence", ["script-name", "e2e-script", "playwright-config", "script-glob"])
def test_javascript_runner_follows_each_file(tmp_path, evidence):
    from roam.output.test_commands import affected_test_commands

    scripts = {"test": "vitest"}
    if evidence in {"script-name", "e2e-script"}:
        scripts["test:e2e" if evidence == "script-name" else "e2e"] = "playwright test"
    elif evidence == "playwright-config":
        (tmp_path / "playwright.config.ts").write_text(
            'export default defineConfig({testDir: "./e2e"});', encoding="utf-8"
        )
    else:
        scripts["test:browser"] = 'mocha "e2e/**/*.spec.ts"'
    (tmp_path / "package.json").write_text(json.dumps({"scripts": scripts}), encoding="utf-8")

    files = ["src/unit.test.ts", "e2e/login.spec.ts"]
    commands, unknown = affected_test_commands(tmp_path, files)
    assert unknown == []
    assert [(entry["runner"], entry["files"]) for entry in commands] == [
        ("vitest", files[:1]),
        ("mocha" if evidence == "script-glob" else "playwright", files[1:]),
    ]


@pytest.mark.parametrize("script", ["karma start", "react-scripts test", "node --test"])
def test_explicit_unknown_runner_supersedes_dev_dependencies(tmp_path, script):
    from roam.output.test_commands import affected_test_commands

    package = {"scripts": {"test": script}, "devDependencies": {"mocha": "^11"}}
    (tmp_path / "package.json").write_text(json.dumps(package), encoding="utf-8")
    assert affected_test_commands(tmp_path, ["app.test.js"]) == ([], ["app.test.js"])


@pytest.mark.parametrize(
    ("runner", "argument"),
    [("jest", "./-a b.test.ts"), ("playwright test", r"(^|/)-a\ b\.test\.ts$")],
)
def test_suggested_commands_declare_shell_and_protect_leading_dash(tmp_path, runner, argument):
    import shlex

    from roam.output.test_commands import affected_test_commands

    (tmp_path / "package.json").write_text(json.dumps({"scripts": {"test": runner}}), encoding="utf-8")
    commands, unknown = affected_test_commands(tmp_path, ["-a b.test.ts"])
    assert unknown == []
    assert shlex.split(commands[0]["command"])[-1] == argument
    assert commands[0]["shell"] == "posix"


@pytest.mark.parametrize("args", [["diff", "--tests"], ["plan", "--symbol", "useStorage"]])
def test_unknown_runner_is_separate_from_run_commands(mixed_tests, cli_runner, args):
    (mixed_tests / "package.json").write_text('{"scripts":{"test":"karma start"}}', encoding="utf-8")
    with (mixed_tests / "storage.ts").open("a", encoding="utf-8") as source:
        source.write("export const changed = 1\n")
    result = invoke_cli(cli_runner, args)
    assert result.exit_code == 0, result.output
    assert "Unknown test runner for: storage.test.ts" in result.output
    assert "Run: Unknown test runner" not in result.output
    if args[0] == "plan":
        assert "   Runner detection: Unknown test runner" in result.output


def test_compile_probe_preserves_non_pytest_commands(mixed_tests, cli_runner, monkeypatch):
    from roam.plan import compiler

    result = invoke_cli(cli_runner, ["--json", "affected-tests", "useStorage"])
    assert result.exit_code == 0, result.output
    affected = json.loads(result.output)
    monkeypatch.setattr(compiler, "_run_roam", lambda *args, **kwargs: affected)
    probe = compiler._probe_test_impact_for_task("which tests cover `useStorage`?", [], str(mixed_tests))
    assert probe["test_impact"]["test_commands"] == affected["test_commands"]
    assert probe["test_impact"]["test_commands"][0]["runner"] == "vitest"
    assert probe["test_impact"]["pytest_command"] == ""
    assert "`test_commands`" in probe["test_impact_definition"]


def test_windows_text_identifies_posix_test_commands(mixed_tests, cli_runner, monkeypatch):
    from roam.output import test_commands

    monkeypatch.setattr(test_commands, "_WINDOWS", True, raising=False)
    with (mixed_tests / "storage.ts").open("a", encoding="utf-8") as source:
        source.write("export const changed = 1\n")
    for args in (
        ["affected-tests", "useStorage"],
        ["preflight", "useStorage"],
        ["plan", "--symbol", "useStorage"],
        ["diff", "--tests"],
    ):
        text = invoke_cli(cli_runner, args)
        assert text.exit_code == 0, text.output
        assert "npx vitest run storage.test.ts (POSIX shell)" in text.output
    result = invoke_cli(cli_runner, ["affected-tests", "useStorage", "--command"])
    assert result.output.splitlines() == ["# Run in a POSIX shell", "npx vitest run storage.test.ts"]


def test_compile_refreshes_runner_after_manifest_edit(mixed_tests):
    from roam.plan import compiler

    manifest = mixed_tests / "package.json"
    task = "which tests cover `useStorage`?"
    for runner in ("jest", "vitest"):
        manifest.write_text(json.dumps({"scripts": {"test": runner}}), encoding="utf-8")
        probe = compiler._probe_test_impact_for_task(task, [], str(mixed_tests))
        assert probe["test_impact"]["test_commands"][0]["runner"] == runner


def test_dynamic_auxiliary_script_does_not_hide_unit_runner(tmp_path):
    from roam.output.test_commands import affected_test_commands

    package = {"scripts": {"test": "vitest", "test:e2e": "runner=$(select_runner)"}}
    (tmp_path / "package.json").write_text(json.dumps(package), encoding="utf-8")
    unit, e2e = "src/unit.test.ts", "e2e/login.spec.ts"
    commands, unknown = affected_test_commands(tmp_path, [unit, e2e])
    assert [entry["command"] for entry in commands] == ["npx vitest run src/unit.test.ts"]
    assert unknown == [e2e]


def test_preflight_budget_keeps_commands_when_affected_paths_are_large():
    from roam.output.formatter import budget_truncate_json

    files = [f"tests/test_preflight_scenario_{index}.py" for index in range(500)]
    commands = [{"runner": "pytest", "command": f"pytest {files[0]}", "shell": "posix", "files": files[:1]}]
    envelope = {
        "command": "preflight",
        "summary": {"verdict": "Review affected tests"},
        "tests": {"total": len(files), "test_files": files, "test_commands": commands},
    }

    limited = budget_truncate_json(envelope, 500)
    assert limited["tests"]["test_commands"] == commands
    assert limited["tests"]["total"] == len(files)
    emitted = len(limited["tests"]["test_files"])
    assert 0 < emitted < len(files)
    assert limited["tests"]["test_files"] == files[:emitted]
    assert limited["summary"]["emitted_counts"]["tests.test_files"] == emitted
    assert limited["summary"]["partial_success"] is True
    assert budget_truncate_json(envelope, 0)["tests"]["test_files"] == files
