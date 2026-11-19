"""Build affected-test commands using runner evidence for each test file."""

from __future__ import annotations

import json
import re
import shlex
import tomllib
from configparser import ConfigParser
from configparser import Error as IniError
from pathlib import Path, PurePosixPath

from roam.index.parser import detect_language

_JS_COMMANDS = {
    "vitest": "npx vitest run",
    "jest": "npx jest --runTestsByPath",
    "mocha": "npx mocha",
    "playwright": "npx playwright test",
}
_JS_LANGUAGES = frozenset({"javascript", "typescript", "jsx", "tsx", "vue", "svelte"})
_MANIFESTS = {
    "python": ("pytest.ini", "pyproject.toml"),
    "go": ("go.mod",),
    "rust": ("Cargo.toml",),
    "ruby": ("Gemfile",),
    "js": ("package.json",),
}
_PYTEST_DEPENDENCY = re.compile(r"pytest(?=\s|[\[<>=!~;@]|$)", re.IGNORECASE)


# Keep quoted/escaped separators inside shell words so an argument cannot
# masquerade as a command. Shell scripts are examined as text, never run.
_SHELL_TOKEN = re.compile(
    r"""[ \t\r]+|\#[^\n]*|(?P<separator>&&|\|\||[;|&\n])|"""
    r"""(?P<word>(?:[^\s;|&()'"\\`]|\\[\s\S]|"(?:[^"\\]|\\[\s\S])*"|'[^']*')+)"""
)


def _script_commands(script):
    """Yield shell words grouped by unquoted command separators."""
    command = []
    pos = 0
    for match in _SHELL_TOKEN.finditer(script):
        if match.start() != pos:
            raise ValueError("unsupported shell syntax in package.json")
        pos = match.end()
        if match["word"]:
            command.extend(shlex.split(match["word"]))
        elif match["separator"]:
            yield command
            command = []
    if pos != len(script):
        raise ValueError("unsupported shell syntax in package.json")
    yield command


def _script_runners(script):
    if not isinstance(script, str):
        return set()
    runners = set()
    for command in _script_commands(script):
        while command and (re.match(r"[a-zA-Z_][a-zA-Z0-9_]*=", command[0]) or command[0] in {"env", "cross-env"}):
            command.pop(0)
        if command and command[0] in {"npx", "pnpm", "npm", "yarn", "bun", "bunx"}:
            launcher = command.pop(0)
            if (launcher == "npm" and command[:1] != ["exec"]) or (
                launcher in {"pnpm", "yarn", "bun"} and command[:1] == ["run"]
            ):
                command = []
            if command and command[0] in {"exec", "dlx", "x"}:
                command.pop(0)
            while command and command[0].startswith("-"):
                command.pop(0)
        if command and not {"--help", "--version"}.intersection(command[1:]):
            runner = PurePosixPath(command[0]).name
            if runner in _JS_COMMANDS and (runner != "playwright" or command[1:2] == ["test"]):
                runners.add(runner)
    return runners


def _js_runner(content):
    package = json.loads(content)
    if not isinstance(package, dict):
        raise ValueError("package.json must be an object")
    scripts = package.get("scripts", {})
    dependencies = package.get("devDependencies", {})
    if not isinstance(scripts, dict) or not isinstance(dependencies, dict):
        raise ValueError("invalid package.json runner metadata")

    test_scripts = {name: value for name, value in scripts.items() if name == "test" or name.startswith("test:")}
    if any(not isinstance(value, str) for value in test_scripts.values()):
        raise ValueError("test scripts must be strings")
    runners = _script_runners(test_scripts.get("test"))
    if not runners:
        for script in test_scripts.values():
            runners.update(_script_runners(script))
    if not runners:
        runners = {name for name in _JS_COMMANDS if isinstance(dependencies.get(name), str)}
        if isinstance(dependencies.get("@playwright/test"), str):
            runners.add("playwright")
    if len(runners) > 1:
        raise ValueError("conflicting JavaScript test runners")
    return next(iter(runners), None)


def _python_runner(content):
    project = tomllib.loads(content)
    if isinstance(project.get("tool", {}).get("pytest"), dict):
        return "pytest"
    metadata = project.get("project", {})
    groups = [
        metadata.get("dependencies", []),
        *metadata.get("optional-dependencies", {}).values(),
        *project.get("dependency-groups", {}).values(),
    ]
    if any(
        isinstance(dep, str) and _PYTEST_DEPENDENCY.match(dep.strip())
        for group in groups
        if isinstance(group, list)
        for dep in group
    ):
        return "pytest"
    return None


def _configured_runner(base, language):
    """Return the runner or raise when local evidence cannot be interpreted."""
    for name in _MANIFESTS.get(language, ()):
        try:
            content = (base / name).read_text(encoding="utf-8")
        except FileNotFoundError:
            continue
        if name == "package.json":
            return _js_runner(content)
        if name == "pyproject.toml":
            return _python_runner(content)
        if name == "Cargo.toml":
            cargo = tomllib.loads(content)
            if not isinstance(cargo.get("package"), dict) and not isinstance(cargo.get("workspace"), dict):
                raise ValueError("Cargo manifest has no package or workspace")
            return "cargo"
        if name == "go.mod":
            if not re.search(r"^[ \t]*module[ \t]+(?!//)\S", content, re.MULTILINE):
                raise ValueError("go.mod has no module directive")
            return "go"
        if name == "Gemfile":
            return "rspec" if re.search(r"""^\s*gem\s+["']rspec(?:-core)?["']""", content, re.MULTILINE) else None
        # An empty pytest.ini is valid and takes precedence over pyproject.toml.
        config = ConfigParser()
        config.read_string(content)
        return "pytest"
    return None


def affected_test_commands(root: Path, test_files: list[str]) -> tuple[list[dict], list[str]]:
    """Return commands with their test files, plus paths with unknown runners."""
    cache = {}
    groups = {}
    unknown = []
    for path in test_files:
        runner = None
        file = PurePosixPath(path)
        if not file.name or file.is_absolute() or ".." in file.parts:
            unknown.append(path)
            continue
        language = detect_language(path)
        if language in _JS_LANGUAGES:
            language = "js"
        for directory in file.parents:
            key = (directory, language)
            if key not in cache:
                try:
                    cache[key] = _configured_runner(root / directory, language)
                except (OSError, ValueError, AttributeError, IniError):
                    cache[key] = False  # Unusable local evidence precludes an ancestor fallback.
            runner = cache[key]
            if runner is False:
                break
            if runner:
                groups.setdefault((directory, runner), []).append(path)
                break
        if not runner:
            unknown.append(path)

    commands = []
    for (directory, runner), paths in groups.items():
        relative = [PurePosixPath(path).relative_to(directory).as_posix() for path in paths]
        args = [shlex.quote(f"./{path}" if path.startswith("-") else path) for path in relative]
        if runner == "go":
            parents = (PurePosixPath(path).parent.as_posix() for path in relative)
            packages = dict.fromkeys("." if parent == "." else f"./{parent}" for parent in parents)
            command = "go test " + " ".join(map(shlex.quote, packages))
        elif runner == "cargo":
            command = "cargo test"
        elif runner == "rspec":
            command = " ".join(["bundle exec rspec", *args])
        elif runner == "playwright":
            command = _JS_COMMANDS[runner] + " " + " ".join(shlex.quote(re.escape(path) + "$") for path in relative)
        else:
            command = " ".join([_JS_COMMANDS.get(runner, runner), *args])
        if directory != PurePosixPath("."):
            command = f"(cd {shlex.quote('./' + directory.as_posix())} && {command})"
        commands.append({"runner": runner, "command": command, "files": paths})
    return commands, unknown


def test_run_fields(root: Path, test_files: list[str]) -> dict:
    """Return the JSON fields every command that suggests test runs shares.

    ``pytest_command`` holds only commands for files detected as pytest, as in
    ``roam affected-tests``; other runners are in ``test_commands``.
    """
    commands, unknown = affected_test_commands(root, test_files)
    return {
        "test_commands": commands,
        "pytest_command": "\n".join(entry["command"] for entry in commands if entry["runner"] == "pytest"),
        "unknown_runner_files": unknown,
    }


def test_run_lines(fields: dict) -> list[str]:
    """Return the text lines that tell a reader how to run the tests."""
    lines = [entry["command"] for entry in fields.get("test_commands", [])]
    if fields.get("unknown_runner_files"):
        lines.append(unknown_runner_message(fields["unknown_runner_files"]))
    return lines


def unknown_runner_message(paths: list[str]) -> str:
    """Render paths safely for command-mode comments and text output."""
    escaped = (path.replace("\n", "\\n").replace("\r", "\\r") for path in paths)
    return "Unknown test runner for: " + ", ".join(escaped)
